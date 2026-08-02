"""Regex-based PII detection and masking for model output.

Real, working Tier 1 logic built on the standard library ``re`` module
only. Detects email addresses, phone numbers, US SSNs, credit card
numbers, and IPv4 addresses, and can mask them in place. Gated by
``feature_flags.is_enabled("security")``.

These patterns are heuristic (format-based, not checksum/Luhn-validated)
and intended as a fast pre-filter before output is returned to a user or
logged, not a certified PII-detection engine.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from cockpit.config.feature_flags import is_enabled
from cockpit.utils.logging_config import get_logger

_logger = get_logger(__name__)

_PII_PATTERNS: dict[str, re.Pattern[str]] = {
    "email": re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
    "phone": re.compile(r"\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]\d{3}[-.\s]\d{4}\b"),
    "ssn": re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    "credit_card": re.compile(r"\b(?:\d[ -]?){13,16}\b"),
    "ip_address": re.compile(
        r"\b(?:(?:25[0-5]|2[0-4]\d|[01]?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|[01]?\d?\d)\b"
    ),
}

_MASK_TOKEN = "[REDACTED:{category}]"  # noqa: S105 -- redaction format string, not a credential


@dataclass(frozen=True)
class PiiFinding:
    """A single detected PII occurrence.

    Attributes:
        category: PII type, e.g. "email", "phone", "ssn", "credit_card",
            "ip_address".
        matched_text: The raw substring that matched.
        start: Start offset of the match in the original text.
        end: End offset (exclusive) of the match in the original text.
    """

    category: str
    matched_text: str
    start: int
    end: int


@dataclass(frozen=True)
class PiiScanResult:
    """Result of scanning text for PII.

    Attributes:
        has_pii: True if any PII was detected.
        findings: All individual :class:`PiiFinding` matches, in the order
            they appear in the text.
    """

    has_pii: bool
    findings: list[PiiFinding] = field(default_factory=list)


def _is_probable_credit_card(digits_only: str) -> bool:
    """Validate a digit string against the Luhn checksum.

    Args:
        digits_only: A string of digits with separators already stripped.

    Returns:
        True if the digits pass the Luhn check and are a plausible card
        length (13-19 digits).
    """
    if not (13 <= len(digits_only) <= 19):
        return False
    total = 0
    reverse_digits = digits_only[::-1]
    for index, char in enumerate(reverse_digits):
        digit = int(char)
        if index % 2 == 1:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return total % 10 == 0


def scan_for_pii(text: str) -> PiiScanResult:
    """Scan text for personally identifiable information.

    Args:
        text: The text to scan (e.g. model output before returning to a
            user, or before logging).

    Returns:
        A :class:`PiiScanResult` listing every detected PII span.

    Raises:
        TypeError: If ``text`` is not a string.
    """
    if not isinstance(text, str):
        raise TypeError(f"text must be a str, got {type(text).__name__}.")

    findings: list[PiiFinding] = []
    for category, pattern in _PII_PATTERNS.items():
        for match in pattern.finditer(text):
            if category == "credit_card":
                digits_only = re.sub(r"[ -]", "", match.group(0))
                if not _is_probable_credit_card(digits_only):
                    continue
            findings.append(
                PiiFinding(
                    category=category,
                    matched_text=match.group(0),
                    start=match.start(),
                    end=match.end(),
                )
            )

    findings.sort(key=lambda f: f.start)
    result = PiiScanResult(has_pii=bool(findings), findings=findings)
    if result.has_pii:
        categories = sorted({f.category for f in findings})
        _logger.warning(
            "PII detected in output: categories=%s, count=%d", categories, len(findings)
        )
    return result


def mask_pii(text: str, mask_token: str = _MASK_TOKEN) -> str:
    """Replace all detected PII occurrences in text with a redaction token.

    Args:
        text: The text to mask.
        mask_token: Format string with a ``{category}`` placeholder used
            for each redaction (e.g. ``"[REDACTED:email]"``).

    Returns:
        A copy of ``text`` with every detected PII span replaced by the
        formatted mask token. Text with no PII is returned unchanged.

    Raises:
        TypeError: If ``text`` is not a string.
    """
    scan = scan_for_pii(text)
    if not scan.has_pii:
        return text

    masked_parts: list[str] = []
    cursor = 0
    for finding in scan.findings:
        if finding.start < cursor:
            # Overlapping match (e.g. credit card digits inside a longer
            # number already consumed) — skip to avoid corrupting output.
            continue
        masked_parts.append(text[cursor : finding.start])
        masked_parts.append(mask_token.format(category=finding.category))
        cursor = finding.end
    masked_parts.append(text[cursor:])
    return "".join(masked_parts)


def sanitize_output(text: str) -> str:
    """Convenience entry point: mask PII in output if security is enabled.

    Args:
        text: The model output to sanitize before returning or logging.

    Returns:
        The masked text if the security framework is enabled and PII was
        found; otherwise the original text unchanged.

    Raises:
        TypeError: If ``text`` is not a string.
    """
    if not is_enabled("security"):
        _logger.debug("Security framework disabled; skipping output sanitization.")
        return text
    return mask_pii(text)
