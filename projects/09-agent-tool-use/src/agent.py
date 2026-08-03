"""The tool-use agent loop, in both automatic and manual modes.

Two ways to let a model call your functions, and the difference matters:

**Automatic** -- hand the ``google-genai`` SDK the plain Python callables. It
reflects over each signature and docstring to build the schema, executes any
function the model asks for, feeds the result back, and returns only the
final natural-language answer. One line of setup, zero control.

**Manual** -- disable automatic function calling, receive the proposed
``function_call`` part yourself, decide whether to run it, execute it, and
append a ``function_response`` to the transcript for the next turn. More
code, but there is now a point in the loop where an authorization check,
an audit log, or a human approval can live.

The model client is always **injected**, never constructed here. That is
what lets the whole loop -- including the multi-turn manual round trip -- be
tested against a scripted fake with no API key and no network.

Guard rails implemented below:

* every proposed tool name is resolved through :class:`~tools.ToolRegistry`,
  which doubles as the authorization allowlist; an unregistered name is
  refused and the refusal is reported back to the model;
* the manual loop is bounded by ``max_iterations`` so a model that keeps
  requesting tools cannot spend unbounded time or money, and the automatic
  mode gets the same bound via ``maximum_remote_calls``;
* the user's question is screened with
  :func:`~cockpit.security.input_security.validate_input` before it reaches
  the model;
* every tool result is screened for leaked personal data before it is fed
  back into the conversation.
"""

from __future__ import annotations

import functools
import inspect
import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Final, Protocol

from cockpit.evaluation.safety_evaluation import detect_refusal
from cockpit.monitoring.performance_metrics import CallOutcome
from cockpit.security.input_security import validate_input

from instrumented import AgentInstrumentation, InstrumentationReport, screen_tool_output
from tools import ToolError, ToolRegistry, ToolSpec, build_registry

logger = logging.getLogger(__name__)

DEFAULT_MODEL: Final[str] = "gemini-2.5-flash"
DEFAULT_MAX_ITERATIONS: Final[int] = 4

ITERATION_CAP_MESSAGE: Final[str] = (
    "Stopped after the maximum number of tool-calling rounds without a final answer."
)
EMPTY_RESPONSE_MESSAGE: Final[str] = "The model returned no text."


# --------------------------------------------------------------------------
# Errors
# --------------------------------------------------------------------------


class AgentError(RuntimeError):
    """Base class for failures that abort an agent run."""


class UnsafeInputError(AgentError):
    """Raised when the user's question fails the cockpit input checks."""


class ModelCallError(AgentError):
    """Raised when the injected client's ``generate_content`` call fails."""


# --------------------------------------------------------------------------
# The injected client's shape
# --------------------------------------------------------------------------


class ModelsService(Protocol):
    """The one SDK method this agent uses.

    ``google.genai.Client.models`` satisfies this structurally, and so does
    the scripted fake in :mod:`fake_client`.
    """

    def generate_content(self, *, model: str, contents: Any, config: Any) -> Any: ...


class ModelClient(Protocol):
    """Structural interface for the injected model client.

    Attributes:
        models: The service exposing ``generate_content``.
    """

    models: ModelsService


# --------------------------------------------------------------------------
# Result types
# --------------------------------------------------------------------------


class ToolCallMode(StrEnum):
    """Which function-calling strategy a run uses.

    Attributes:
        AUTOMATIC: The SDK executes tools itself and returns final text.
        MANUAL: This module inspects, authorizes, and executes each call.
    """

    AUTOMATIC = "automatic"
    MANUAL = "manual"


class StopReason(StrEnum):
    """Why the agent loop stopped.

    Attributes:
        COMPLETED: The model produced a final text answer.
        ITERATION_CAP: The round-trip budget ran out first.
        EMPTY_RESPONSE: The model stopped calling tools but returned no text.
    """

    COMPLETED = "completed"
    ITERATION_CAP = "iteration_cap"
    EMPTY_RESPONSE = "empty_response"


@dataclass(frozen=True)
class ProposedCall:
    """A tool call the model asked for, before any authorization.

    Attributes:
        name: Tool name as the model spelled it. Not yet trusted.
        args: Arguments the model supplied, as a plain mapping.
    """

    name: str
    args: Mapping[str, Any]


@dataclass(frozen=True)
class ToolInvocation:
    """The outcome of one proposed tool call.

    A refused call is still recorded -- "the model asked for something it is
    not allowed to have" is exactly the event an audit trail needs.

    Attributes:
        name: Tool name the model proposed.
        args: Arguments the model supplied.
        result: The tool's (screened) output, or ``None`` if it did not run
            or raised.
        error: Why the call failed, or ``None`` on success.
        authorized: False when the name was not in the registry, in which
            case the tool was never executed.
        duration_seconds: Measured execution time; 0.0 for refused calls.
        redacted: True when the output was withheld by the PII screen.
    """

    name: str
    args: Mapping[str, Any]
    result: str | None = None
    error: str | None = None
    authorized: bool = True
    duration_seconds: float = 0.0
    redacted: bool = False

    @property
    def succeeded(self) -> bool:
        """Whether the tool ran and returned a result.

        Returns:
            True if there is no error.
        """
        return self.error is None

    @property
    def payload(self) -> dict[str, str]:
        """The ``function_response`` body sent back to the model.

        Returns:
            ``{"result": ...}`` on success, ``{"error": ...}`` otherwise.
        """
        if self.error is not None:
            return {"error": self.error}
        return {"result": self.result or ""}

    @property
    def response_text(self) -> str:
        """Flat string form of :attr:`payload`, for the automatic-mode return.

        Returns:
            The result text, or an ``ERROR: ...`` string on failure.
        """
        if self.error is not None:
            return f"ERROR: {self.error}"
        return self.result or ""


@dataclass(frozen=True)
class AgentResult:
    """Everything one question produced.

    Attributes:
        question: The question that was asked.
        answer: The model's final natural-language answer.
        mode: Which function-calling mode ran.
        model: Model id used.
        invocations: Every tool call attempted, in order.
        model_calls: How many round trips to the model this agent made. In
            automatic mode the SDK's own internal round trips are hidden
            behind a single call, so this is 1.
        stop_reason: Why the loop ended.
        refused: True if the final answer reads as a refusal.
        report: The cockpit cost/latency rollup for the run.
    """

    question: str
    answer: str
    mode: ToolCallMode
    model: str
    invocations: tuple[ToolInvocation, ...]
    model_calls: int
    stop_reason: StopReason
    refused: bool
    report: InstrumentationReport

    def tool_names(self) -> tuple[str, ...]:
        """List the tools that were proposed, in call order.

        Returns:
            Tool names, including refused ones.
        """
        return tuple(invocation.name for invocation in self.invocations)


# --------------------------------------------------------------------------
# SDK response/request plumbing
# --------------------------------------------------------------------------
#
# `google.genai.types` is imported lazily inside these helpers rather than at
# module scope: the SDK is only needed to *build* requests, so importing it
# late keeps the response-parsing side of this module usable against any
# duck-typed fake.


def build_config(tool_functions: Sequence[Callable[..., str]], *, manual: bool) -> Any:
    """Build the ``GenerateContentConfig`` for one mode.

    Args:
        tool_functions: Callables to expose as tools. The SDK derives each
            schema from the signature, type hints, and docstring.
        manual: True to disable automatic function calling so the caller
            sees the raw ``function_call`` part.

    Returns:
        A ``google.genai.types.GenerateContentConfig``.
    """
    from google.genai import types

    if manual:
        return types.GenerateContentConfig(
            tools=list(tool_functions),
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )
    return types.GenerateContentConfig(tools=list(tool_functions))


def build_automatic_config(
    tool_functions: Sequence[Callable[..., str]], max_remote_calls: int
) -> Any:
    """Build the automatic-mode config with the SDK's own round-trip cap.

    Args:
        tool_functions: Callables to expose as tools.
        max_remote_calls: Maximum tool round trips the SDK may perform
            before giving up. This is the automatic-mode equivalent of the
            manual loop's ``max_iterations``.

    Returns:
        A ``google.genai.types.GenerateContentConfig`` with automatic
        function calling enabled but bounded.
    """
    from google.genai import types

    return types.GenerateContentConfig(
        tools=list(tool_functions),
        automatic_function_calling=types.AutomaticFunctionCallingConfig(
            maximum_remote_calls=max_remote_calls
        ),
    )


def build_tool_result_content(invocations: Sequence[ToolInvocation]) -> Any:
    """Build the ``function_response`` turn that answers the model's calls.

    Args:
        invocations: The executed (or refused) calls from this round, in the
            same order the model proposed them.

    Returns:
        A ``google.genai.types.Content`` carrying one function-response part
        per invocation.
    """
    from google.genai import types

    return types.Content(
        role="user",
        parts=[
            types.Part.from_function_response(name=inv.name, response=inv.payload)
            for inv in invocations
        ],
    )


def extract_function_calls(response: Any) -> list[ProposedCall]:
    """Pull every proposed function call out of a model response.

    Reads defensively with ``getattr`` so a response with no candidates, no
    content, or no parts yields an empty list instead of raising.

    Args:
        response: The object returned by ``generate_content``.

    Returns:
        The proposed calls, in the order the model emitted them.
    """
    candidates = getattr(response, "candidates", None) or []
    if not candidates:
        return []
    parts = getattr(getattr(candidates[0], "content", None), "parts", None) or []

    calls: list[ProposedCall] = []
    for part in parts:
        function_call = getattr(part, "function_call", None)
        if function_call is None:
            continue
        calls.append(
            ProposedCall(
                name=getattr(function_call, "name", "") or "",
                args=dict(getattr(function_call, "args", None) or {}),
            )
        )
    return calls


def extract_usage(response: Any) -> tuple[int, int]:
    """Read prompt/completion token counts off a response.

    Args:
        response: The object returned by ``generate_content``.

    Returns:
        An ``(input_tokens, output_tokens)`` pair; zeros when the response
        carries no usage metadata.
    """
    metadata = getattr(response, "usage_metadata", None)
    if metadata is None:
        return (0, 0)
    return (
        int(getattr(metadata, "prompt_token_count", 0) or 0),
        int(getattr(metadata, "candidates_token_count", 0) or 0),
    )


# --------------------------------------------------------------------------
# The agent
# --------------------------------------------------------------------------


@dataclass
class ToolUseAgent:
    """Runs one question to completion using local tools.

    Attributes:
        client: Injected model client. Anything exposing
            ``models.generate_content(model=, contents=, config=)`` works,
            including ``google.genai.Client`` and the scripted fake.
        registry: The tools this agent may call. Also the allowlist.
        instrumentation: Cockpit trackers. Inject one with a deterministic
            clock in tests.
        model: Model id to call.
        max_iterations: Maximum model round trips per question.
    """

    client: ModelClient
    registry: ToolRegistry = field(default_factory=build_registry)
    instrumentation: AgentInstrumentation = field(default_factory=AgentInstrumentation)
    model: str = DEFAULT_MODEL
    max_iterations: int = DEFAULT_MAX_ITERATIONS

    def __post_init__(self) -> None:
        """Validate the configured round-trip budget.

        Raises:
            ValueError: If ``max_iterations`` is not at least 1.
        """
        if self.max_iterations < 1:
            raise ValueError("max_iterations must be at least 1.")

    # -- public API --------------------------------------------------------

    def run(self, question: str, mode: ToolCallMode = ToolCallMode.MANUAL) -> AgentResult:
        """Answer a question, calling local tools as the model requests them.

        Args:
            question: The user's question.
            mode: Which function-calling strategy to use.

        Returns:
            The :class:`AgentResult` for the run.

        Raises:
            UnsafeInputError: If the question is empty or fails the cockpit
                input-security checks.
            ModelCallError: If a call to the injected client raises.
        """
        text = question.strip()
        if not text:
            raise UnsafeInputError("Question is empty.")

        problems = validate_input(text)
        if problems:
            raise UnsafeInputError("Input rejected: " + "; ".join(problems))

        if mode is ToolCallMode.AUTOMATIC:
            answer, invocations, model_calls, stop_reason = self._run_automatic(text)
        else:
            answer, invocations, model_calls, stop_reason = self._run_manual(text)

        return AgentResult(
            question=text,
            answer=answer,
            mode=mode,
            model=self.model,
            invocations=tuple(invocations),
            model_calls=model_calls,
            stop_reason=stop_reason,
            refused=detect_refusal(answer).is_refusal,
            report=self.instrumentation.report(),
        )

    # -- automatic mode ----------------------------------------------------

    def _run_automatic(self, question: str) -> tuple[str, list[ToolInvocation], int, StopReason]:
        """Let the SDK execute tools and return only the final answer.

        The callables handed to the SDK are recording wrappers, not the raw
        functions: that is the only way to get a trace out of a mode whose
        whole point is that execution happens inside the SDK.

        Args:
            question: The user's question.

        Returns:
            A ``(answer, invocations, model_calls, stop_reason)`` tuple.
        """
        invocations: list[ToolInvocation] = []
        wrapped = [self._recording_wrapper(spec, invocations) for spec in self.registry]
        config = build_automatic_config(wrapped, self.max_iterations)

        response = self._call_model([question], config)
        answer = (getattr(response, "text", None) or "").strip()
        if not answer:
            return (EMPTY_RESPONSE_MESSAGE, invocations, 1, StopReason.EMPTY_RESPONSE)
        return (answer, invocations, 1, StopReason.COMPLETED)

    def _recording_wrapper(self, spec: ToolSpec, sink: list[ToolInvocation]) -> Callable[..., str]:
        """Wrap a tool so SDK-driven execution is still measured and traced.

        ``functools.wraps`` copies ``__name__``, ``__doc__`` and
        ``__annotations__`` and sets ``__wrapped__``, so the schema the SDK
        reflects out of the wrapper is identical to the one it would build
        from the raw function.

        Args:
            spec: The tool to wrap.
            sink: List that each :class:`ToolInvocation` is appended to.

        Returns:
            A callable with the same public signature as ``spec.func``.
        """
        signature = inspect.signature(spec.func)

        @functools.wraps(spec.func)
        def wrapper(*args: Any, **kwargs: Any) -> str:
            try:
                bound = signature.bind(*args, **kwargs)
            except TypeError:
                supplied: dict[str, Any] = dict(kwargs)
            else:
                bound.apply_defaults()
                supplied = dict(bound.arguments)

            invocation = self._execute(spec, supplied)
            sink.append(invocation)
            # Returned rather than raised: an error string gives the model a
            # chance to correct itself, whereas raising here would tear down
            # the SDK's whole automatic-function-calling loop.
            return invocation.response_text

        return wrapper

    # -- manual mode -------------------------------------------------------

    def _run_manual(self, question: str) -> tuple[str, list[ToolInvocation], int, StopReason]:
        """Drive the multi-turn loop, authorizing each call before it runs.

        Args:
            question: The user's question.

        Returns:
            A ``(answer, invocations, model_calls, stop_reason)`` tuple.
        """
        config = build_config(self.registry.functions(), manual=True)
        contents: list[Any] = [question]
        invocations: list[ToolInvocation] = []
        model_calls = 0

        for _ in range(self.max_iterations):
            response = self._call_model(contents, config)
            model_calls += 1

            proposed = extract_function_calls(response)
            if not proposed:
                answer = (getattr(response, "text", None) or "").strip()
                if not answer:
                    return (
                        EMPTY_RESPONSE_MESSAGE,
                        invocations,
                        model_calls,
                        StopReason.EMPTY_RESPONSE,
                    )
                return (answer, invocations, model_calls, StopReason.COMPLETED)

            # Keep the model's own turn in the transcript; a function
            # response is only meaningful next to the call it answers.
            candidates = getattr(response, "candidates", None) or []
            model_content = getattr(candidates[0], "content", None) if candidates else None
            if model_content is not None:
                contents.append(model_content)

            round_invocations = [self._authorize_and_execute(call) for call in proposed]
            invocations.extend(round_invocations)
            contents.append(build_tool_result_content(round_invocations))

        logger.warning(
            "Hit the %d-iteration cap for question %r; stopping.", self.max_iterations, question
        )
        return (ITERATION_CAP_MESSAGE, invocations, model_calls, StopReason.ITERATION_CAP)

    def _authorize_and_execute(self, call: ProposedCall) -> ToolInvocation:
        """Resolve a proposed call against the allowlist, then run it.

        This is the seam that manual mode exists for. A name the registry
        does not know never reaches an executable.

        Args:
            call: The tool call the model proposed.

        Returns:
            The resulting :class:`ToolInvocation`, marked ``authorized=False``
            if the name was refused.
        """
        try:
            spec = self.registry.get(call.name)
        except ToolError as exc:
            logger.warning("Refused unauthorized tool call %r.", call.name)
            return ToolInvocation(
                name=call.name,
                args=dict(call.args),
                error=str(exc),
                authorized=False,
            )
        return self._execute(spec, dict(call.args))

    # -- shared execution --------------------------------------------------

    def _execute(self, spec: ToolSpec, arguments: Mapping[str, Any]) -> ToolInvocation:
        """Run one authorized tool, timed and output-screened.

        Args:
            spec: The resolved tool.
            arguments: Keyword arguments the model supplied.

        Returns:
            The :class:`ToolInvocation` describing what happened.
        """
        raw: str | None = None
        error: str | None = None

        with self.instrumentation.measure_tool_call(spec.name) as call:
            try:
                raw = spec.func(**arguments)
            except ToolError as exc:
                error = str(exc)
                call.outcome = CallOutcome.ERROR
            except TypeError as exc:
                # The model invented or omitted a parameter.
                error = f"Invalid arguments for {spec.name}: {exc}"
                call.outcome = CallOutcome.ERROR

        duration = call.duration_seconds
        if error is not None:
            return ToolInvocation(
                name=spec.name, args=dict(arguments), error=error, duration_seconds=duration
            )

        screening = screen_tool_output(raw or "")
        if not screening.safe:
            logger.warning(
                "Redacted output of tool %r (categories: %s).",
                spec.name,
                ", ".join(screening.categories),
            )
        return ToolInvocation(
            name=spec.name,
            args=dict(arguments),
            result=screening.text,
            duration_seconds=duration,
            redacted=not screening.safe,
        )

    def _call_model(self, contents: Sequence[Any], config: Any) -> Any:
        """Make one instrumented model call.

        Args:
            contents: The conversation so far, in SDK ``contents`` form.
            config: The ``GenerateContentConfig`` for this mode.

        Returns:
            The raw response object from the client.

        Raises:
            ModelCallError: If the client call raises.
        """
        input_tokens = 0
        output_tokens = 0
        with self.instrumentation.measure_model_call() as call:
            try:
                response = self.client.models.generate_content(
                    model=self.model, contents=list(contents), config=config
                )
            except Exception as exc:  # SDK raises assorted google.genai.errors types
                raise ModelCallError(f"Model call failed: {exc}") from exc
            input_tokens, output_tokens = extract_usage(response)
            call.input_tokens = input_tokens
            call.output_tokens = output_tokens

        self.instrumentation.record_model_usage(self.model, input_tokens, output_tokens)
        return response
