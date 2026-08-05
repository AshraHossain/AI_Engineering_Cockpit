"""Prompt-injection pattern detection and basic input validation.

Deterministic, standard-library-only defense. There is no ML classifier
here — this is a fast first-layer filter, not a substitute for a
moderation model, and not a security boundary on its own.

The scan works in three layers, because matching literal text alone is
trivially defeated:

1. **Raw** — the input exactly as received.
2. **Normalized** — Unicode-folded, with zero-width and combining
   characters stripped and letter-spacing collapsed. Defeats obfuscation
   that preserves meaning for the model's tokenizer while destroying the
   surface form a regex matches (``i g n o r e``, ``ig\\u200bnore``).
3. **Decoded** — base64 / hex / rot13 candidates are decoded and rescanned,
   since an encoded instruction is still an instruction to the model.

Known limitation: attacks whose payload is *assembled across turns* or
from fragments the model concatenates itself cannot be caught here. That
needs conversation-level state, not a per-message filter. See
``docs/SECURITY_COVERAGE.md`` for measured coverage.

Gated by ``feature_flags.is_enabled("security")``.
"""

from __future__ import annotations

import base64
import binascii
import codecs
import re
import unicodedata
from dataclasses import dataclass, field

from cockpit.config.feature_flags import is_enabled
from cockpit.utils.logging_config import get_logger

_logger = get_logger(__name__)

# Prompt-injection phrasings, grouped by technique family. Case-insensitive.
# Each pattern is kept narrow to hold false positives down; the breadth comes
# from scanning several *surfaces* of the same input (see module docstring),
# not from loosening individual regexes.
_INJECTION_PATTERNS: dict[str, re.Pattern[str]] = {
    "ignore_instructions": re.compile(
        r"\bignore\s+(all\s+|any\s+)?(previous|prior|above|earlier)\s+instructions\b",
        re.IGNORECASE,
    ),
    "disregard_instructions": re.compile(
        r"\bdisregard\s+(all\s+|any\s+)?(previous|prior|above|earlier)\s+"
        r"(instructions|rules|prompt)\b",
        re.IGNORECASE,
    ),
    "override_system_prompt": re.compile(
        r"\b(override|bypass|ignore)\s+(the\s+)?(system\s+prompt|system\s+message|guardrails?)\b",
        re.IGNORECASE,
    ),
    "reveal_system_prompt": re.compile(
        # Any verb that produces the prompt's contents is an exfiltration
        # request -- "summarize"/"outline" are as effective as "reveal", and
        # tend to be less guarded against.
        #
        # The object must clearly be *the model's own* configuration. Bare
        # "the instructions" is ordinary English ("describe the instructions
        # for assembling this desk") and matching it caused false positives.
        r"\b(?:reveal|show|print|output|repeat|summari[sz]e|describe|outline|list|explain|"
        r"restate|paraphrase)\s+(?:me\s+)?"
        r"(?:(?:your|the)\s+)?(?:system|initial|hidden|original)\s+"
        r"(?:prompt|instructions?|message)\b"
        r"|\b(?:reveal|show|print|output|repeat|summari[sz]e|describe|outline|list|explain)\s+"
        r"(?:me\s+)?your\s+(?:instructions?|prompt|rules|directives?)\b",
        re.IGNORECASE,
    ),
    "role_reassignment": re.compile(
        r"\byou\s+are\s+now\s+(a|an|in)\b|\bact\s+as\s+(if\s+you\s+(are|were)\s+)?(a|an)\b"
        r"|\bpretend\s+(to\s+be|you\s+are)\b",
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
    # --- Exfiltration: treat the system prompt as ordinary context to echo ---
    "context_echo_request": re.compile(
        r"\b(repeat|echo|print|output|show|display|reproduce)\b[^.?!]{0,40}?"
        r"\b(everything|all\s+of\s+it|the\s+text|the\s+content|the\s+words|what)\b"
        r"[^.?!]{0,20}?\b(above|before|preceding|prior|earlier|so\s+far)\b",
        re.IGNORECASE,
    ),
    "instruction_interrogation": re.compile(
        # Anchored on "your" so questions about instructions in general
        # ("what are the instructions for this form?") are not swept up.
        r"\b(what|which|describe|tell\s+me)\b[^.?!]{0,40}?\byour\s+"
        r"(original|initial|system|underlying|hidden|real)?\s*"
        r"(instructions?|rules?|directives?|guidelines?|prompt|configuration)\b",
        re.IGNORECASE,
    ),
    "diagnostic_mode_pretext": re.compile(
        r"\b(debug|diagnostic|maintenance|verbose|admin)\s+mode\b"
        r"|\b(dump|disclose|expose)\s+(your\s+)?"
        r"(config|configuration|context|system\s+message|prompt)\b",
        re.IGNORECASE,
    ),
    "verbatim_preservation_request": re.compile(
        r"\b(summari[sz]e|paraphrase|rewrite|restate)\b[^.?!]{0,60}?"
        r"\b(verbatim|word[-\s]for[-\s]word|exactly|preserving|character[-\s]for[-\s]character)\b",
        re.IGNORECASE,
    ),
    # --- Attacks on the model's willingness to decline, not on its rules ---
    "refusal_suppression": re.compile(
        r"\b(do\s*not|don'?t|never|under\s+no\s+circumstances)\b\s+"
        r"(ever\s+)?(refuse|decline|apologi[sz]e|reject|say\s+(that\s+)?you\s+(can'?t|cannot))\b"
        r"|\bfailure\s+is\s+not\s+an\s+option\b"
        r"|\byou\s+(must|have\s+to|are\s+required\s+to)\s+(answer|comply|respond|obey)\b"
        # Coercion by consequence rather than by command: declining is framed
        # as an incident, an error, or something that gets reported. Scoped to
        # the assistant's own refusal, so prose about refusals in the world
        # ("a refusal to pay is recorded on your credit report") stays clean.
        r"|\b(your\s+refusal|any\s+refusal|if\s+you\s+refuse|should\s+you\s+refuse|"
        r"refusing\s+to\s+(comply|answer|respond))\b"
        r"[^.?!]{0,60}?\b(logged|recorded|reported|escalat\w+|penal\w+|"
        r"violation|failure|incident)\b",
        re.IGNORECASE,
    ),
    # --- Claimed out-of-band authority rather than an explicit rule override ---
    "fake_authority_claim": re.compile(
        r"\b(developer|admin|administrator|operator|maintainer)\s+"
        r"(update|override|notice|directive|access|privileges?)\b"
        # Same claim with the words the other way round: "update from the
        # developer", "notice from your operator".
        r"|\b(update|notice|directive|message|instruction)\s+from\s+"
        r"(the\s+|your\s+|our\s+)?(developer|admin|administrator|operator|engineering)\b"
        r"|\bas\s+(your|the)\s+(developer|administrator|creator|operator)\b"
        r"|\bi\s+am\s+(your|the)\s+(developer|administrator|creator|operator)\b",
        re.IGNORECASE,
    ),
}

MAX_REASONABLE_INPUT_LENGTH = 50_000

# Characters that are invisible to a reader but split keywords for a regex,
# while the model's tokenizer sees straight through them.
#
# Written as escapes on purpose. Literal invisible characters in source are
# unreviewable (you cannot see what the class contains) and they break tools
# that render this file to a non-UTF-8 console -- bandit's text report on a
# cp1252 Windows terminal crashes outright on a raw U+200B.
#   U+00AD soft hyphen | U+200B-U+200F zero-width & directional marks
#   U+2060-U+2064 word joiner & invisible operators | U+FEFF BOM / ZWNBSP
_INVISIBLE_ORDS = (
    0x00AD,  # soft hyphen
    *range(0x200B, 0x2010),  # zero-width space/joiners, directional marks
    *range(0x2060, 0x2065),  # word joiner, invisible operators
    0xFEFF,  # BOM / zero-width no-break space
)
_INVISIBLE_CHARS = re.compile("[" + "".join(map(chr, _INVISIBLE_ORDS)) + "]")

# A *span* of letter-spaced text: "I g n o r e   a l l   p r e v i o u s".
# Matching the whole span rather than word-by-word matters, because attackers
# space out a full sentence and the short words in it ("all") are too short to
# recognise in isolation. Within a span, a single space separates letters of
# one word and a wider gap separates words. Requires five consecutive single
# letters, which ordinary prose effectively never produces.
_LETTER_SPACED_SPAN = re.compile(r"(?:[A-Za-z]\s+){4,}[A-Za-z]\b")
_WORD_GAP_SENTINEL = "\x00"

_BASE64_CANDIDATE = re.compile(r"[A-Za-z0-9+/]{20,}={0,2}")
_HEX_CANDIDATE = re.compile(r"(?:[0-9a-fA-F]{2}){16,}")

# Bound the decode work so a large hostile input cannot turn the filter into
# a CPU sink. ponytail: fixed caps, revisit only if real traffic needs more.
_MAX_DECODE_INPUT = 20_000
_MAX_DECODE_CANDIDATES = 8


def _collapse_letter_spacing(text: str) -> str:
    """Rejoin letter-spaced spans, preserving the words inside them.

    ``"I g n o r e   a l l"`` becomes ``"Ignore all"``: within a spaced-out
    span the single spaces are inside words and the wider gaps are between
    them, so they must be collapsed differently.
    """

    def _collapse(match: re.Match[str]) -> str:
        span = match.group(0)
        # Mark the wide gaps first, then delete the intra-word single spaces,
        # then restore the marks as real word separators.
        span = re.sub(r"(?<=[A-Za-z])\s{2,}(?=[A-Za-z])", _WORD_GAP_SENTINEL, span)
        span = re.sub(r"(?<=[A-Za-z])\s(?=[A-Za-z])", "", span)
        return span.replace(_WORD_GAP_SENTINEL, " ")

    return _LETTER_SPACED_SPAN.sub(_collapse, text)


def normalize_for_detection(text: str) -> str:
    """Fold a string to the form an attacker cannot cheaply vary.

    Applies NFKC normalization, strips invisible and combining characters,
    collapses letter-spacing, and collapses runs of whitespace. The result
    is only for matching — never store it or send it onward as if it were
    the user's actual input.

    Args:
        text: The raw input text.

    Returns:
        The normalized text.

    Raises:
        TypeError: If ``text`` is not a string.
    """
    if not isinstance(text, str):
        raise TypeError(f"text must be a str, got {type(text).__name__}.")

    # NFKD first, not NFKC: decomposition splits precomposed characters into
    # base + combining mark, so the mark-stripping below can actually reach
    # them. Composing first would fuse "i" + U+0301 into "i" and the accent
    # would survive -- which is exactly the evasion this is meant to close.
    folded = unicodedata.normalize("NFKD", text)
    folded = _INVISIBLE_CHARS.sub("", folded)
    # Drop combining marks: both Zalgo-style stacking and ordinary accents
    # used as homoglyphs ("ignore" -> "ignore").
    folded = "".join(ch for ch in folded if unicodedata.category(ch) != "Mn")
    # Recompose and fold compatibility forms (fullwidth, ligatures, etc.).
    folded = unicodedata.normalize("NFKC", folded)
    folded = _collapse_letter_spacing(folded)
    return re.sub(r"\s+", " ", folded).strip()


def _decode_base64_candidates(text: str) -> list[str]:
    """Decode base64-looking runs to UTF-8 text, skipping anything that isn't."""
    decoded: list[str] = []
    for match in _BASE64_CANDIDATE.finditer(text):
        if len(decoded) >= _MAX_DECODE_CANDIDATES:
            break
        blob = match.group(0)
        # b64decode needs a length that is a multiple of 4; pad rather than
        # discard, since attackers strip padding precisely to dodge filters.
        padded = blob + "=" * (-len(blob) % 4)
        try:
            raw = base64.b64decode(padded, validate=True)
            candidate = raw.decode("utf-8")
        except (binascii.Error, UnicodeDecodeError, ValueError):
            continue
        if candidate.isprintable() or " " in candidate:
            decoded.append(candidate)
    return decoded


def _decode_hex_candidates(text: str) -> list[str]:
    """Decode long hex runs to UTF-8 text, skipping anything that isn't."""
    decoded: list[str] = []
    for match in _HEX_CANDIDATE.finditer(text):
        if len(decoded) >= _MAX_DECODE_CANDIDATES:
            break
        try:
            candidate = bytes.fromhex(match.group(0)).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            continue
        if candidate.isprintable() or " " in candidate:
            decoded.append(candidate)
    return decoded


def decoded_variants(text: str) -> list[tuple[str, str]]:
    """Produce decoded readings of text that may hide an instruction.

    Encoding an instruction does not stop it being an instruction — the
    model will happily decode base64 or rot13 on request. This surfaces
    those readings so the same pattern set can be applied to them.

    Args:
        text: The raw input text.

    Returns:
        A list of ``(method, decoded_text)`` pairs. Empty when nothing
        decodes cleanly, which is the common case for ordinary input.

    Raises:
        TypeError: If ``text`` is not a string.
    """
    if not isinstance(text, str):
        raise TypeError(f"text must be a str, got {type(text).__name__}.")

    if not text or len(text) > _MAX_DECODE_INPUT:
        return []

    variants: list[tuple[str, str]] = []
    variants.extend(("base64", value) for value in _decode_base64_candidates(text))
    variants.extend(("hex", value) for value in _decode_hex_candidates(text))
    # rot13 is cheap and self-inverse, so always try the whole string.
    rotated = codecs.encode(text, "rot_13")
    if rotated != text:
        variants.append(("rot13", rotated))
    return variants


@dataclass(frozen=True)
class InjectionScanResult:
    """Result of scanning text for prompt-injection patterns.

    Attributes:
        is_suspicious: True if one or more injection patterns matched.
        matched_patterns: Names of the patterns that matched (see
            ``_INJECTION_PATTERNS`` keys).
        text_length: Length of the scanned text, for logging context.
        matched_surfaces: Which readings of the input triggered a match —
            ``"raw"``, ``"normalized"``, or ``"decoded:<method>"``. A hit on
            anything other than ``"raw"`` means the input was obfuscated,
            which is itself worth alerting on.
    """

    is_suspicious: bool
    matched_patterns: list[str] = field(default_factory=list)
    text_length: int = 0
    matched_surfaces: list[str] = field(default_factory=list)


def scan_for_prompt_injection(text: str) -> InjectionScanResult:
    """Scan text, and obfuscation-resistant readings of it, for injection.

    Heuristic and deterministic. It will not catch a genuinely novel
    phrasing, and it cannot catch a payload assembled across several turns
    — layer it with tool allow-listing, output validation, and
    least-privilege design rather than relying on it alone.

    Args:
        text: The untrusted input text to scan (e.g. user message content).

    Returns:
        An :class:`InjectionScanResult` describing whether the text looks
        suspicious, which patterns fired, and on which reading of the input.

    Raises:
        TypeError: If ``text`` is not a string.
    """
    if not isinstance(text, str):
        raise TypeError(f"text must be a str, got {type(text).__name__}.")

    surfaces: list[tuple[str, str]] = [("raw", text)]

    normalized = normalize_for_detection(text)
    if normalized != text:
        surfaces.append(("normalized", normalized))

    for method, decoded in decoded_variants(text):
        surfaces.append((f"decoded:{method}", decoded))
        # An attacker can obfuscate *and* encode; normalize decoded text too.
        decoded_normalized = normalize_for_detection(decoded)
        if decoded_normalized != decoded:
            surfaces.append((f"decoded:{method}+normalized", decoded_normalized))

    matched: list[str] = []
    matched_surfaces: list[str] = []
    for surface_name, candidate in surfaces:
        for name, pattern in _INJECTION_PATTERNS.items():
            if pattern.search(candidate):
                if name not in matched:
                    matched.append(name)
                if surface_name not in matched_surfaces:
                    matched_surfaces.append(surface_name)

    result = InjectionScanResult(
        is_suspicious=bool(matched),
        matched_patterns=matched,
        text_length=len(text),
        matched_surfaces=matched_surfaces,
    )
    if result.is_suspicious:
        _logger.warning(
            "Prompt-injection patterns detected: %s (surfaces=%s, input length=%d)",
            ", ".join(matched),
            ", ".join(matched_surfaces),
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


def contains_invisible_characters(text: str) -> bool:
    """Detect zero-width or soft-hyphen characters used to break keywords.

    These are invisible to a human reader and to most logs, but they split
    words for a regex while leaving the meaning intact for a tokenizer —
    so their presence in user input is suspicious on its own.

    Args:
        text: The input text to inspect.

    Returns:
        True if any invisible formatting character is present.

    Raises:
        TypeError: If ``text`` is not a string.
    """
    if not isinstance(text, str):
        raise TypeError(f"text must be a str, got {type(text).__name__}.")
    return _INVISIBLE_CHARS.search(text) is not None


def validate_input(text: str, max_length: int = MAX_REASONABLE_INPUT_LENGTH) -> list[str]:
    """Run all input checks and collect human-readable problems.

    Combines length validation, control- and invisible-character detection,
    and prompt-injection scanning into a single entry point suitable for a
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

    if contains_invisible_characters(text):
        problems.append("Input contains zero-width or invisible formatting characters.")

    scan = scan_for_prompt_injection(text)
    if scan.is_suspicious:
        detail = ", ".join(scan.matched_patterns)
        problems.append(f"Input matches possible prompt-injection pattern(s): {detail}")
        if any(surface != "raw" for surface in scan.matched_surfaces):
            surfaces = ", ".join(s for s in scan.matched_surfaces if s != "raw")
            problems.append(f"Injection was only visible after de-obfuscation ({surfaces}).")

    return problems
