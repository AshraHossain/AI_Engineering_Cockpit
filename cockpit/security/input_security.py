"""Prompt-injection pattern detection and basic input validation.

Real, working Tier 1 logic built on the standard library ``re`` module only
(no ML classifier — this is a fast, deterministic first line of defense,
not a substitute for a dedicated moderation model). Gated by
``feature_flags.is_enabled("security")``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from cockpit.config.feature_flags import is_enabled
from cockpit.utils.logging_config import get_logger

_logger = get_logger(__name__)

# Common prompt-injection phrasings. Case-insensitive, matched against the
# raw input text. Each pattern is intentionally narrow to keep false
# positives low; expand this list as new attack phrasings are observed.
_INJECTION_PATTERNS: dict[str, re.Pattern[str]] = {
    "ignore_instructions": re.compile(
        r"\bignore\s+(all\s+|any\s+)?(previous|prior|above|earlier)\s+instructions\b",
        re.IGNORECASE,
    ),
    "disregard_instructions": re.compile(
        r"\bdisregard\s+(all\s+|any\s+)?(previous|prior|above|earlier)\s+(instructions|rules|prompt)\b",
        re.IGNORECASE,
    ),
    "override_system_prompt": re.compile(
        r"\b(override|bypass|ignore)\s+(the\s+)?(system\s+prompt|system\s+message|guardrails?)\b",
        re.IGNORECASE,
    ),
    "reveal_system_prompt": re.compile(
        r"\b(reveal|show|print|output|repeat)\s+(me\s+)?(your\s+|the\s+)?(system\s+prompt|instructions|hidden\s+prompt)\b",
        re.IGNORECASE,
    ),
    "role_reassignment": re.compile(
        r"\byou\s+are\s+now\s+(a|an|in)\b|\bact\s+as\s+(if\s+you\s+(are|were)\s+)?(a|an)\b|\bpretend\s+(to\s+be|you\s+are)\b",
        re.IGNORECASE,
    ),
    "dan_style_jailbreak": re.compile(
        r"\b(dan\s+mode|developer\s+mode|jailbreak|no\s+restrictions\s+mode)\b",
        re.IGNORECASE,
    ),
    "fake_conversation_boundary": re.compile(
        r"\[?\s*(system|assistant)\s*\]?\s*:\s*",
        re.IGNORECASE,
    ),
    "instruction_smuggling_markup": re.compile(
        r"<\s*(system|instructions?|admin)\s*>",
        re.IGNORECASE,
    ),
}

MAX_REASONABLE_INPUT_LENGTH = 50_000


@dataclass(frozen=True)
class InjectionScanResult:
    """Result of scanning text for prompt-injection patterns.

    Attributes:
        is_suspicious: True if one or more injection patterns matched.
        matched_patterns: Names of the patterns that matched (see
            ``_INJECTION_PATTERNS`` keys).
        text_length: Length of the scanned text, for logging context.
    """

    is_suspicious: bool
    matched_patterns: list[str] = field(default_factory=list)
    text_length: int = 0


def scan_for_prompt_injection(text: str) -> InjectionScanResult:
    """Scan text for known prompt-injection phrasings.

    This is a heuristic, regex-based scan intended as a fast pre-filter.
    It will not catch novel or obfuscated injection attempts and should be
    layered with other defenses (allow-listing tools, output validation,
    least-privilege system design).

    Args:
        text: The untrusted input text to scan (e.g. user message content).

    Returns:
        An :class:`InjectionScanResult` describing whether the text looks
        suspicious and which patterns fired.

    Raises:
        TypeError: If ``text`` is not a string.
    """
    if not isinstance(text, str):
        raise TypeError(f"text must be a str, got {type(text).__name__}.")

    matched = [name for name, pattern in _INJECTION_PATTERNS.items() if pattern.search(text)]
    result = InjectionScanResult(
        is_suspicious=bool(matched),
        matched_patterns=matched,
        text_length=len(text),
    )
    if result.is_suspicious:
        _logger.warning(
            "Prompt-injection patterns detected: %s (input length=%d)",
            ", ".join(matched),
            result.text_length,
        )
    return result


def validate_input_length(text: str, max_length: int = MAX_REASONABLE_INPUT_LENGTH) -> bool:
    """Check that input text does not exceed a maximum length.

    Guards against resource-exhaustion or context-stuffing attacks via
    oversized inputs.

    Args:
        text: The input text to check.
        max_length: Maximum allowed character length.

    Returns:
        True if ``text`` is within the allowed length.

    Raises:
        TypeError: If ``text`` is not a string.
        ValueError: If ``max_length`` is not positive.
    """
    if not isinstance(text, str):
        raise TypeError(f"text must be a str, got {type(text).__name__}.")
    if max_length <= 0:
        raise ValueError("max_length must be positive.")
    return len(text) <= max_length


def contains_control_characters(text: str) -> bool:
    """Detect non-printable control characters that could smuggle payloads.

    Excludes common whitespace (``\\n``, ``\\r``, ``\\t``) which are
    expected in normal text.

    Args:
        text: The input text to inspect.

    Returns:
        True if any disallowed control character is present.

    Raises:
        TypeError: If ``text`` is not a string.
    """
    if not isinstance(text, str):
        raise TypeError(f"text must be a str, got {type(text).__name__}.")
    allowed_whitespace = {"\n", "\r", "\t"}
    return any((ord(ch) < 0x20 and ch not in allowed_whitespace) for ch in text)


def validate_input(text: str, max_length: int = MAX_REASONABLE_INPUT_LENGTH) -> list[str]:
    """Run all Tier 1 input checks and collect human-readable problems.

    Combines length validation, control-character detection, and
    prompt-injection scanning into a single entry point suitable for a
    request-handling pipeline.

    Args:
        text: The untrusted input text to validate.
        max_length: Maximum allowed character length, forwarded to
            :func:`validate_input_length`.

    Returns:
        A list of problem descriptions. Empty if the input passed all
        checks. Callers decide whether to reject, flag, or log-and-allow
        based on this list.

    Raises:
        TypeError: If ``text`` is not a string.
    """
    if not is_enabled("security"):
        _logger.debug("Security framework disabled; skipping input validation.")
        return []

    problems: list[str] = []

    if not validate_input_length(text, max_length=max_length):
        problems.append(f"Input exceeds maximum length of {max_length} characters.")

    if contains_control_characters(text):
        problems.append("Input contains disallowed control characters.")

    scan = scan_for_prompt_injection(text)
    if scan.is_suspicious:
        problems.append(
            "Input matches possible prompt-injection pattern(s): "
            + ", ".join(scan.matched_patterns)
        )

    return problems
