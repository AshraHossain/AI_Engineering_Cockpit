"""An offline stand-in for ``google.genai.Client``.

The agent never constructs its own client, so anything with the right shape
can be injected. This module supplies that shape twice over:

* the small ``Fake*`` records reproduce the parts of the SDK response tree
  the agent actually reads -- ``response.candidates[0].content.parts[i]
  .function_call`` (with ``.name`` and ``.args``), ``response.text``, and
  ``response.usage_metadata.*_token_count``;
* :class:`ScriptedModelClient` replays a list of :class:`ScriptedTurn` s, and
  in automatic mode *emulates* the SDK's automatic function calling by
  actually invoking the callables it was handed in ``config.tools``.

That emulation is what makes ``--dry-run`` a faithful demo rather than a
stub: the tools genuinely execute, so the trace, the latency samples, and
the PII screening all exercise the same code paths a live run would.

Nothing here touches the network.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

# --------------------------------------------------------------------------
# Response tree
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class FakeFunctionCall:
    """The SDK's ``function_call`` payload.

    Attributes:
        name: Name of the function the model wants invoked.
        args: Arguments the model supplied.
    """

    name: str
    args: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class FakePart:
    """One part of a candidate's content.

    Attributes:
        text: Text content, when this part is prose.
        function_call: The requested call, when this part is a tool request.
    """

    text: str | None = None
    function_call: FakeFunctionCall | None = None


@dataclass(frozen=True)
class FakeContent:
    """A single conversation turn.

    Attributes:
        role: ``"model"`` or ``"user"``.
        parts: The parts making up this turn.
    """

    role: str
    parts: tuple[FakePart, ...] = ()


@dataclass(frozen=True)
class FakeCandidate:
    """One generation candidate.

    Attributes:
        content: The candidate's content.
    """

    content: FakeContent


@dataclass(frozen=True)
class FakeUsageMetadata:
    """Token accounting for one response.

    Attributes:
        prompt_token_count: Prompt tokens billed.
        candidates_token_count: Completion tokens billed.
    """

    prompt_token_count: int = 0
    candidates_token_count: int = 0

    @property
    def total_token_count(self) -> int:
        """Combined prompt and completion tokens.

        Returns:
            The sum of both counts.
        """
        return self.prompt_token_count + self.candidates_token_count


@dataclass(frozen=True)
class FakeResponse:
    """The object ``generate_content`` returns.

    Attributes:
        text: Final text, or ``None`` when the turn is only tool requests.
        candidates: Generation candidates.
        usage_metadata: Token accounting for this call.
    """

    text: str | None = None
    candidates: tuple[FakeCandidate, ...] = ()
    usage_metadata: FakeUsageMetadata = field(default_factory=FakeUsageMetadata)


# --------------------------------------------------------------------------
# Script
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ScriptedCall:
    """A tool call the scripted model will ask for.

    Attributes:
        name: Tool name to request.
        args: Arguments to request it with.
    """

    name: str
    args: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ScriptedTurn:
    """One scripted model turn.

    In manual mode a turn with ``calls`` produces function-call parts and no
    text. In automatic mode the same turn produces the tool executions *and*
    the final ``text``, mirroring how the real SDK collapses the whole tool
    round trip into one ``generate_content`` return.

    Attributes:
        calls: Tool calls this turn requests.
        text: Final text for this turn, if any.
        prompt_tokens: Prompt tokens to report for this call.
        candidates_tokens: Completion tokens to report for this call.
    """

    calls: tuple[ScriptedCall, ...] = ()
    text: str | None = None
    prompt_tokens: int = 0
    candidates_tokens: int = 0


@dataclass(frozen=True)
class RecordedCall:
    """One ``generate_content`` invocation, captured for assertions.

    Attributes:
        model: Model id the agent asked for.
        contents: The transcript the agent sent.
        config: The ``GenerateContentConfig`` the agent built.
    """

    model: str
    contents: tuple[Any, ...]
    config: Any


def _automatic_function_calling_disabled(config: Any) -> bool:
    """Read the AFC disable switch off a config object.

    Args:
        config: The ``GenerateContentConfig`` (or ``None``).

    Returns:
        True if automatic function calling was explicitly disabled.
    """
    afc = getattr(config, "automatic_function_calling", None)
    return bool(getattr(afc, "disable", False))


def _callables_from_config(config: Any) -> dict[str, Callable[..., Any]]:
    """Collect the plain callables the agent registered as tools.

    Args:
        config: The ``GenerateContentConfig`` (or ``None``).

    Returns:
        Mapping of function name to callable. Empty when the config carries
        declaration objects rather than raw callables.
    """
    tools = getattr(config, "tools", None) or []
    return {
        name: tool for tool in tools if callable(tool) and (name := getattr(tool, "__name__", ""))
    }


class _ScriptedModels:
    """The ``client.models`` namespace of :class:`ScriptedModelClient`."""

    def __init__(self, owner: ScriptedModelClient) -> None:
        """Bind this service to its owning client.

        Args:
            owner: The client whose script and call log this service drives.
        """
        self._owner = owner

    def generate_content(self, *, model: str, contents: Any, config: Any = None) -> FakeResponse:
        """Replay the next scripted turn.

        Args:
            model: Model id (recorded, otherwise ignored).
            contents: The transcript (recorded, otherwise ignored).
            config: The generation config. Its ``tools`` and
                ``automatic_function_calling`` fields decide whether tool
                callables are executed here.

        Returns:
            The :class:`FakeResponse` for this turn.

        Raises:
            IndexError: If the script is exhausted and ``repeat_last`` is off.
        """
        return self._owner.next_response(model=model, contents=contents, config=config)


@dataclass
class ScriptedModelClient:
    """A ``genai.Client``-shaped fake that replays a fixed script.

    Attributes:
        turns: The scripted turns, consumed in order.
        repeat_last: When True, calls past the end of the script replay the
            final turn forever -- which is how the iteration-cap guard rail
            gets exercised.
        calls: Every invocation this client received, in order.
        models: The ``client.models`` namespace the agent calls through.
    """

    turns: Sequence[ScriptedTurn]
    repeat_last: bool = False
    calls: list[RecordedCall] = field(default_factory=list)
    models: _ScriptedModels = field(init=False, repr=False, compare=False)
    cursor: int = field(init=False, default=0, repr=False, compare=False)

    def __post_init__(self) -> None:
        """Attach the ``models`` namespace."""
        self.models = _ScriptedModels(self)

    def _next_turn(self) -> ScriptedTurn:
        """Advance the script cursor by one.

        Returns:
            The turn to replay.

        Raises:
            IndexError: If the script is exhausted and ``repeat_last`` is off.
        """
        if self.cursor < len(self.turns):
            turn = self.turns[self.cursor]
            self.cursor += 1
            return turn
        if self.repeat_last and self.turns:
            return self.turns[-1]
        raise IndexError("ScriptedModelClient ran out of scripted turns.")

    def next_response(self, *, model: str, contents: Any, config: Any) -> FakeResponse:
        """Build the response for the next scripted turn.

        Args:
            model: Model id the agent asked for.
            contents: The transcript the agent sent.
            config: The generation config the agent built.

        Returns:
            The :class:`FakeResponse` for this turn.
        """
        self.calls.append(RecordedCall(model=model, contents=tuple(contents), config=config))
        turn = self._next_turn()
        usage = FakeUsageMetadata(
            prompt_token_count=turn.prompt_tokens,
            candidates_token_count=turn.candidates_tokens,
        )

        if not turn.calls:
            return FakeResponse(
                text=turn.text,
                candidates=(FakeCandidate(FakeContent(role="model", parts=())),),
                usage_metadata=usage,
            )

        if _automatic_function_calling_disabled(config):
            parts = tuple(
                FakePart(function_call=FakeFunctionCall(name=call.name, args=dict(call.args)))
                for call in turn.calls
            )
            return FakeResponse(
                text=None,
                candidates=(FakeCandidate(FakeContent(role="model", parts=parts)),),
                usage_metadata=usage,
            )

        # Automatic mode: do what the SDK would do -- run the functions.
        available = _callables_from_config(config)
        for call in turn.calls:
            function = available.get(call.name)
            if function is not None:
                function(**dict(call.args))
        return FakeResponse(
            text=turn.text,
            candidates=(FakeCandidate(FakeContent(role="model", parts=())),),
            usage_metadata=usage,
        )


# --------------------------------------------------------------------------
# The canned --dry-run scenario
# --------------------------------------------------------------------------

DEMO_QUESTION = "What is 17 * 23, and how many days are there between 2026-01-01 and 2026-08-03?"

_DEMO_CALLS = (
    ScriptedCall(name="calculate", args={"expression": "17 * 23"}),
    ScriptedCall(name="days_between", args={"start_date": "2026-01-01", "end_date": "2026-08-03"}),
)

_DEMO_ANSWER = (
    "17 * 23 is 391, and there are 214 days between 2026-01-01 (Thursday) "
    "and 2026-08-03 (Monday)."
)


def build_demo_client(*, manual: bool) -> ScriptedModelClient:
    """Build the canned offline client used by ``--dry-run``.

    Args:
        manual: True for the manual-mode script (a tool-call turn followed
            by an answer turn), False for the automatic-mode script (a
            single turn that both runs the tools and answers).

    Returns:
        A :class:`ScriptedModelClient` primed for the demo question.
    """
    if manual:
        return ScriptedModelClient(
            turns=(
                ScriptedTurn(calls=_DEMO_CALLS, prompt_tokens=180, candidates_tokens=24),
                ScriptedTurn(text=_DEMO_ANSWER, prompt_tokens=260, candidates_tokens=38),
            )
        )
    return ScriptedModelClient(
        turns=(
            ScriptedTurn(
                calls=_DEMO_CALLS,
                text=_DEMO_ANSWER,
                prompt_tokens=440,
                candidates_tokens=38,
            ),
        )
    )
