"""Edge-case and boundary-condition testing for model-facing code.

Where :mod:`cockpit.red_teaming.adversarial_tests` probes for *malicious*
failure, this module probes for *accidental* failure: inputs that are merely
nasty rather than hostile. Empty strings, 100k-character walls of text,
zero-width joiners, right-to-left overrides, mixed scripts, control bytes,
malformed JSON, and pure punctuation all routinely crash prompt-templating,
tokenization, and response-parsing code long before any attacker shows up.

Every exotic character is written as a ``\\uXXXX`` escape so this source file
stays pure ASCII and invisible characters cannot be silently mangled by an
editor, a diff viewer, or a copy-paste.

Nothing here needs a network. The system under test is injected as a
:class:`~cockpit.red_teaming.adversarial_tests.Target` callable, and only the
runners are gated behind ``feature_flags.is_enabled("red_teaming")``.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from cockpit.config.feature_flags import is_enabled
from cockpit.utils.logging_config import get_logger

_logger = get_logger(__name__)

# Sized against cockpit.security.input_security.MAX_REASONABLE_INPUT_LENGTH
# (50_000) so the suite exercises both sides of the platform's own limit.
AT_LIMIT_LENGTH = 50_000
OVERSIZED_LENGTH = 100_000

# Default wall-clock budget for a single edge-case call, in seconds. A target
# that blows through this is flagged rather than allowed to hang a suite.
DEFAULT_TIMEOUT_SECONDS = 30.0


class EdgeCaseCategory(StrEnum):
    """Family of boundary condition an edge case exercises."""

    EMPTY = "empty"
    WHITESPACE = "whitespace"
    LENGTH = "length"
    UNICODE = "unicode"
    CONTROL_CHARACTERS = "control_characters"
    MIXED_SCRIPT = "mixed_script"
    STRUCTURED_DATA = "structured_data"
    PUNCTUATION = "punctuation"
    REPETITION = "repetition"
    UNCLASSIFIED = "unclassified"


class RobustnessStatus(StrEnum):
    """How a target handled one edge-case input."""

    PASSED = "passed"
    ERRORED = "errored"
    EMPTY_RESPONSE = "empty_response"
    TIMED_OUT = "timed_out"


class Target(Protocol):
    """A system under test: anything that maps an input string to a response."""

    def __call__(self, prompt: str) -> str: ...


#: Backwards-compatible alias for the original skeleton's protocol name.
ModelCallable = Target


@dataclass(frozen=True)
class EdgeCase:
    """A single edge-case input to test.

    Attributes:
        case_id: Unique identifier for the case.
        description: Human-readable description of what is being tested
            (e.g. "empty prompt", "extremely long input", "unicode/emoji
            heavy input", "non-English input").
        input_value: The actual input to send.
        category: Boundary-condition family the case belongs to.
    """

    case_id: str
    description: str
    input_value: str
    category: EdgeCaseCategory = EdgeCaseCategory.UNCLASSIFIED


@dataclass(frozen=True)
class EdgeCaseResult:
    """Outcome of running one edge case against a target.

    Attributes:
        case: The :class:`EdgeCase` that was run.
        status: How the target handled the input.
        response: The target's response text (empty when it raised).
        elapsed_seconds: Wall-clock duration of the call.
        error: Repr of the raised exception, when ``status`` is ``ERRORED``.
    """

    case: EdgeCase
    status: RobustnessStatus
    response: str = ""
    elapsed_seconds: float = 0.0
    error: str | None = None

    @property
    def passed(self) -> bool:
        """Whether the target handled the input acceptably.

        Returns:
            True if the status is ``PASSED``.
        """
        return self.status is RobustnessStatus.PASSED


@dataclass(frozen=True)
class EdgeCaseReport:
    """Aggregate robustness result across a set of edge cases.

    Attributes:
        total: Number of cases run.
        passed: Cases the target handled cleanly.
        failed: Cases that crashed, returned nothing, or ran too long.
        pass_rate: ``passed / total``, in [0.0, 1.0]. 1.0 for an empty run.
        failures_by_category: Count of failures per edge-case category.
        results: Every per-case result, in execution order.
    """

    total: int
    passed: int
    failed: int
    pass_rate: float
    failures_by_category: dict[str, int]
    results: tuple[EdgeCaseResult, ...]


def generate_empty_cases() -> list[EdgeCase]:
    """Generate empty and whitespace-only inputs.

    Returns:
        Edge cases covering the empty string and several whitespace-only
        variants, including non-breaking and zero-width whitespace that
        ``str.strip()`` does not always remove.
    """
    return [
        EdgeCase("empty-001", "Completely empty string", "", EdgeCaseCategory.EMPTY),
        EdgeCase("empty-002", "Single space", " ", EdgeCaseCategory.WHITESPACE),
        EdgeCase("empty-003", "Tabs and newlines only", "\t\n\r\n\t", EdgeCaseCategory.WHITESPACE),
        EdgeCase(
            "empty-004",
            "Non-breaking spaces only (not stripped by naive trimming)",
            "\u00a0\u00a0\u00a0",
            EdgeCaseCategory.WHITESPACE,
        ),
        EdgeCase(
            "empty-005",
            "Zero-width space only (renders as empty but has length)",
            "\u200b\u200b",
            EdgeCaseCategory.WHITESPACE,
        ),
        EdgeCase(
            "empty-006",
            "Ideographic space only",
            "\u3000\u3000",
            EdgeCaseCategory.WHITESPACE,
        ),
    ]


def generate_length_cases() -> list[EdgeCase]:
    """Generate very short and very long inputs.

    Returns:
        Edge cases at and beyond the platform's reasonable-input limit, plus
        a single-character input and a single very long unbroken token.
    """
    return [
        EdgeCase("length-001", "Single character", "a", EdgeCaseCategory.LENGTH),
        EdgeCase(
            "length-002",
            f"Input exactly at the {AT_LIMIT_LENGTH}-character platform limit",
            "a" * AT_LIMIT_LENGTH,
            EdgeCaseCategory.LENGTH,
        ),
        EdgeCase(
            "length-003",
            f"Oversized {OVERSIZED_LENGTH}-character input (context stuffing)",
            "word " * (OVERSIZED_LENGTH // 5),
            EdgeCaseCategory.LENGTH,
        ),
        EdgeCase(
            "length-004",
            "One unbroken 10k-character token (no whitespace to split on)",
            "x" * 10_000,
            EdgeCaseCategory.LENGTH,
        ),
    ]


def generate_unicode_cases() -> list[EdgeCase]:
    """Generate unicode, emoji, RTL, and zero-width inputs.

    Returns:
        Edge cases covering emoji (including ZWJ sequences and skin-tone
        modifiers), right-to-left text, bidirectional override characters,
        combining-mark stacking, and surrogate-pair-heavy text.
    """
    return [
        EdgeCase(
            "unicode-001",
            "Emoji with ZWJ sequence and skin-tone modifier",
            "Hello \U0001f469\u200d\U0001f4bb \U0001f44d\U0001f3fd \U0001f600",
            EdgeCaseCategory.UNICODE,
        ),
        EdgeCase(
            "unicode-002",
            "Right-to-left Arabic and Hebrew text",
            "\u0645\u0631\u062d\u0628\u0627 \u05e9\u05dc\u05d5\u05dd",
            EdgeCaseCategory.UNICODE,
        ),
        EdgeCase(
            "unicode-003",
            "Bidirectional override characters (visually reorders text)",
            "safe\u202etxet desrever\u202c end",
            EdgeCaseCategory.UNICODE,
        ),
        EdgeCase(
            "unicode-004",
            "Zero-width characters embedded mid-word",
            "in\u200bvis\u200cib\u200dle \ufefftext",
            EdgeCaseCategory.UNICODE,
        ),
        EdgeCase(
            "unicode-005",
            "Combining-mark stacking (Zalgo-style)",
            "a" + "\u0301\u0302\u0303\u0304\u0305" * 20,
            EdgeCaseCategory.UNICODE,
        ),
        EdgeCase(
            "unicode-006",
            "Astral-plane characters (surrogate pairs)",
            "\U0001d400\U0001d401\U0001d402 \U0001f4a9 \U00020bb7",
            EdgeCaseCategory.UNICODE,
        ),
    ]


def generate_control_character_cases() -> list[EdgeCase]:
    """Generate inputs containing control and non-printable characters.

    Returns:
        Edge cases covering NUL bytes, ANSI escape sequences, vertical tabs
        and form feeds, and lone carriage returns.
    """
    return [
        EdgeCase(
            "control-001",
            "Embedded NUL byte",
            "before\x00after",
            EdgeCaseCategory.CONTROL_CHARACTERS,
        ),
        EdgeCase(
            "control-002",
            "ANSI escape sequence (terminal control injection)",
            "text\x1b[31mred\x1b[0m",
            EdgeCaseCategory.CONTROL_CHARACTERS,
        ),
        EdgeCase(
            "control-003",
            "Vertical tab, form feed, and backspace",
            "a\x0bb\x0cc\x08d",
            EdgeCaseCategory.CONTROL_CHARACTERS,
        ),
        EdgeCase(
            "control-004",
            "Lone carriage returns (line-ending confusion)",
            "line one\rline two\rline three",
            EdgeCaseCategory.CONTROL_CHARACTERS,
        ),
    ]


def generate_mixed_script_cases() -> list[EdgeCase]:
    """Generate inputs mixing writing systems and homoglyphs.

    Returns:
        Edge cases covering Latin/Cyrillic/Greek homoglyph mixing, CJK text
        interleaved with Latin, and full-width Latin characters.
    """
    return [
        EdgeCase(
            "script-001",
            "Cyrillic and Greek homoglyphs inside Latin words",
            "p\u0430yp\u0430l \u0430dmin \u03bfption",
            EdgeCaseCategory.MIXED_SCRIPT,
        ),
        EdgeCase(
            "script-002",
            "CJK interleaved with Latin and Devanagari",
            "\u4f60\u597d hi \u3053\u3093\u306b\u3061\u306f \u0928\u092e\u0938\u094d\u0924\u0947",
            EdgeCaseCategory.MIXED_SCRIPT,
        ),
        EdgeCase(
            "script-003",
            "Full-width Latin characters",
            "\uff28\uff45\uff4c\uff4c\uff4f \uff37\uff4f\uff52\uff4c\uff44",
            EdgeCaseCategory.MIXED_SCRIPT,
        ),
    ]


def generate_structured_data_cases() -> list[EdgeCase]:
    """Generate deeply nested and malformed structured-data inputs.

    Returns:
        Edge cases covering deep JSON nesting, unterminated JSON and code
        fences, an unclosed XML/markup tree, and a template-literal payload
        that trips naive string formatting.
    """
    depth = 200
    return [
        EdgeCase(
            "struct-001",
            f"JSON nested {depth} levels deep (recursion-limit probe)",
            '{"a":' * depth + "1" + "}" * depth,
            EdgeCaseCategory.STRUCTURED_DATA,
        ),
        EdgeCase(
            "struct-002",
            "Truncated/unterminated JSON",
            '{"user": {"name": "test", "roles": ["admin",',
            EdgeCaseCategory.STRUCTURED_DATA,
        ),
        EdgeCase(
            "struct-003",
            "Unclosed markdown code fence",
            "Here is code:\n```python\ndef f():\n    return 1\n",
            EdgeCaseCategory.STRUCTURED_DATA,
        ),
        EdgeCase(
            "struct-004",
            "Deeply unclosed XML/HTML tags",
            "<a><b><c><d><e>text",
            EdgeCaseCategory.STRUCTURED_DATA,
        ),
        EdgeCase(
            "struct-005",
            "Format-specifier and template-literal characters",
            "Total: {total} %s %d ${env} {{mustache}} {0!r}",
            EdgeCaseCategory.STRUCTURED_DATA,
        ),
    ]


def generate_punctuation_cases() -> list[EdgeCase]:
    """Generate inputs that are only punctuation or symbols.

    Returns:
        Edge cases with no alphanumeric content at all, which frequently
        produce degenerate or empty model responses.
    """
    return [
        EdgeCase(
            "punct-001",
            "Punctuation only",
            "!@#$%^&*()_+-=[]{}|;':\",./<>?",
            EdgeCaseCategory.PUNCTUATION,
        ),
        EdgeCase("punct-002", "Question marks only", "?????????", EdgeCaseCategory.PUNCTUATION),
        EdgeCase(
            "punct-003",
            "Mathematical and currency symbols only",
            "\u2211\u222b\u221e\u2260\u00b1 \u20ac\u00a5\u00a3\u20b9",
            EdgeCaseCategory.PUNCTUATION,
        ),
    ]


def generate_repetition_cases() -> list[EdgeCase]:
    """Generate extremely repetitive inputs.

    Returns:
        Edge cases covering a repeated word, a repeated single character, and
        a repeated sentence, all of which can push a model into looping or
        degenerate output.
    """
    return [
        EdgeCase(
            "repeat-001",
            "Single word repeated 5000 times",
            "spam " * 5_000,
            EdgeCaseCategory.REPETITION,
        ),
        EdgeCase(
            "repeat-002",
            "Single character repeated 20000 times",
            "!" * 20_000,
            EdgeCaseCategory.REPETITION,
        ),
        EdgeCase(
            "repeat-003",
            "Same sentence repeated 500 times",
            "Please summarize this. " * 500,
            EdgeCaseCategory.REPETITION,
        ),
    ]


def generate_edge_cases() -> list[EdgeCase]:
    """Generate the standard set of boundary-condition edge cases.

    Returns:
        A list of :class:`EdgeCase` instances covering common boundary
        conditions (empty input, max-length input, malformed encoding,
        unusual whitespace, unicode, control characters, mixed scripts,
        malformed structured data, punctuation-only, and repetition).
    """
    cases: list[EdgeCase] = []
    for generator in (
        generate_empty_cases,
        generate_length_cases,
        generate_unicode_cases,
        generate_control_character_cases,
        generate_mixed_script_cases,
        generate_structured_data_cases,
        generate_punctuation_cases,
        generate_repetition_cases,
    ):
        cases.extend(generator())
    return cases


def run_edge_case(
    case: EdgeCase, target: Target, timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
) -> EdgeCaseResult:
    """Run one edge case against a target and classify the outcome.

    Timeouts are detected by measuring elapsed wall-clock time rather than by
    interrupting the target: killing arbitrary caller code mid-flight is not
    safe, and for a robustness report "it answered, but took 90 seconds" is
    the finding worth surfacing.

    Args:
        case: The edge case to run.
        target: The system under test.
        timeout_seconds: Wall-clock budget above which the case is flagged as
            timed out.

    Returns:
        The :class:`EdgeCaseResult` for this case.

    Raises:
        ValueError: If ``timeout_seconds`` is not positive.
    """
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive.")

    started = time.perf_counter()
    try:
        # Annotated as object so the isinstance guard below is a real,
        # reachable check: an untyped caller can return anything.
        response: object = target(case.input_value)
    # The target is arbitrary caller code, so every exception type is in play.
    except Exception as exc:
        elapsed = time.perf_counter() - started
        _logger.error("Target raised on edge case %s: %r", case.case_id, exc)
        return EdgeCaseResult(
            case=case,
            status=RobustnessStatus.ERRORED,
            elapsed_seconds=elapsed,
            error=repr(exc),
        )

    elapsed = time.perf_counter() - started

    if not isinstance(response, str):
        _logger.error(
            "Target returned %s for edge case %s; expected str.",
            type(response).__name__,
            case.case_id,
        )
        return EdgeCaseResult(
            case=case,
            status=RobustnessStatus.ERRORED,
            elapsed_seconds=elapsed,
            error=f"target returned {type(response).__name__}, expected str",
        )

    if elapsed > timeout_seconds:
        _logger.warning(
            "Edge case %s exceeded the %.1fs budget (%.1fs).",
            case.case_id,
            timeout_seconds,
            elapsed,
        )
        return EdgeCaseResult(
            case=case,
            status=RobustnessStatus.TIMED_OUT,
            response=response,
            elapsed_seconds=elapsed,
        )

    if not response.strip():
        _logger.warning("Edge case %s produced an empty response.", case.case_id)
        return EdgeCaseResult(
            case=case,
            status=RobustnessStatus.EMPTY_RESPONSE,
            response=response,
            elapsed_seconds=elapsed,
        )

    return EdgeCaseResult(
        case=case,
        status=RobustnessStatus.PASSED,
        response=response,
        elapsed_seconds=elapsed,
    )


def summarize_edge_case_results(results: tuple[EdgeCaseResult, ...]) -> EdgeCaseReport:
    """Aggregate per-case results into a robustness report.

    Args:
        results: Results to aggregate, in execution order.

    Returns:
        The aggregated :class:`EdgeCaseReport`.
    """
    total = len(results)
    passed = sum(1 for r in results if r.passed)
    failures: dict[str, int] = {}
    for result in results:
        if result.passed:
            continue
        key = str(result.case.category)
        failures[key] = failures.get(key, 0) + 1

    return EdgeCaseReport(
        total=total,
        passed=passed,
        failed=total - passed,
        pass_rate=1.0 if total == 0 else passed / total,
        failures_by_category=failures,
        results=results,
    )


def run_edge_case_campaign(
    target: Target,
    cases: list[EdgeCase] | None = None,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> EdgeCaseReport:
    """Run a full edge-case robustness campaign against a target.

    Gated on ``feature_flags.is_enabled("red_teaming")``: when the framework
    is disabled this returns an empty report without contacting the target.

    Args:
        target: The system under test.
        cases: Cases to run; defaults to :func:`generate_edge_cases`.
        timeout_seconds: Per-case wall-clock budget.

    Returns:
        An :class:`EdgeCaseReport` describing which inputs the target
        mishandled.

    Raises:
        TypeError: If ``target`` is not callable.
        ValueError: If ``timeout_seconds`` is not positive.
    """
    if not is_enabled("red_teaming"):
        _logger.debug("Red-teaming framework disabled; skipping edge-case campaign.")
        return EdgeCaseReport(
            total=0,
            passed=0,
            failed=0,
            pass_rate=1.0,
            failures_by_category={},
            results=(),
        )

    if not callable(target):
        raise TypeError(f"target must be callable, got {type(target).__name__}.")
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive.")

    selected = generate_edge_cases() if cases is None else cases
    results = tuple(
        run_edge_case(case, target, timeout_seconds=timeout_seconds) for case in selected
    )
    report = summarize_edge_case_results(results)
    _logger.info(
        "Edge-case campaign complete: %d cases, %d failures, pass rate %.2f.",
        report.total,
        report.failed,
        report.pass_rate,
    )
    return report


def run_edge_case_suite(cases: list[EdgeCase], model_call: Target) -> dict[str, bool]:
    """Run edge cases against a model and record pass/fail per case.

    A case "passes" if the model handles it without raising, timing out,
    or returning an empty/degenerate response.

    Gated on ``feature_flags.is_enabled("red_teaming")``: when the framework
    is disabled this returns an empty mapping without contacting the target.

    Args:
        cases: Edge cases to execute.
        model_call: Callable that sends an input to the target model and
            returns its response text.

    Returns:
        A mapping of ``case_id`` to pass/fail boolean.
    """
    report = run_edge_case_campaign(model_call, cases=cases)
    return {result.case.case_id: result.passed for result in report.results}
