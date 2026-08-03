"""Deterministic answer-quality metrics with an optional injected LLM judge.

Every metric in this module is pure standard-library math (``re``,
``difflib``, ``json``, ``collections``), so scoring is reproducible, runs
offline, and produces identical numbers on every machine and in CI.

No LLM SDK is imported here — and none should be. When a judgement genuinely
needs a model (open-ended answers with no reference string to compare
against), the caller injects an object satisfying the :class:`Judge`
protocol. Passing ``judge=None`` (the default) keeps scoring fully
deterministic.

Only the aggregate entry point :func:`evaluate_quality` consults
``feature_flags.is_enabled("evaluation")``; the individual metric functions
are pure math and are always available.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any, Protocol, runtime_checkable

from cockpit.config.feature_flags import is_enabled
from cockpit.utils.logging_config import get_logger

_logger = get_logger(__name__)

_PUNCTUATION_RE = re.compile(r"[^\w\s]", re.UNICODE)
_WHITESPACE_RE = re.compile(r"\s+")
_SENTENCE_SPLIT_RE = re.compile(r"[.!?]+(?:\s+|$)")
_CODE_FENCE_RE = re.compile(r"^\s*```(?:json|JSON)?\s*(.*?)\s*```\s*$", re.DOTALL)

_ARTICLES: frozenset[str] = frozenset({"a", "an", "the"})

# Deliberately small: this is a relevance/factuality helper, not a linguistics
# package. Anything longer would need a real tokenizer and a real stopword
# corpus, which would mean a dependency.
_STOPWORDS: frozenset[str] = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "but",
        "by",
        "can",
        "did",
        "do",
        "does",
        "for",
        "from",
        "had",
        "has",
        "have",
        "how",
        "i",
        "if",
        "in",
        "into",
        "is",
        "it",
        "its",
        "me",
        "of",
        "on",
        "or",
        "please",
        "so",
        "than",
        "that",
        "the",
        "their",
        "them",
        "then",
        "there",
        "these",
        "they",
        "this",
        "to",
        "was",
        "we",
        "were",
        "what",
        "when",
        "where",
        "which",
        "who",
        "why",
        "will",
        "with",
        "you",
        "your",
    }
)

DEFAULT_JUDGE_CRITERIA = (
    "Rate how well the response answers the prompt: correct, complete, and "
    "directly responsive. 1.0 is a model answer, 0.0 is useless or wrong."
)

# Weight assigned to each sub-score inside :func:`evaluate_quality`. Only the
# components that actually apply to a given call participate; the weights of
# the participating components are renormalized to sum to 1.0.
DEFAULT_QUALITY_WEIGHTS: dict[str, float] = {
    "normalized_match": 0.15,
    "token_f1": 0.30,
    "fuzzy_similarity": 0.15,
    "keyword_coverage": 0.20,
    "structured_validity": 0.15,
    "judge": 0.30,
}


@runtime_checkable
class Judge(Protocol):
    """Protocol for an LLM-graded scorer injected by the caller.

    Implementations wrap whatever model client the caller already has. This
    package never constructs one, so nothing here depends on a network call
    or a vendor SDK. Test doubles just need a ``score`` method.
    """

    def score(self, prompt: str, response: str, criteria: str) -> float:
        """Grade a response against free-text criteria.

        Args:
            prompt: The prompt that elicited the response.
            response: The response text to grade.
            criteria: Natural-language description of what a good response
                looks like.

        Returns:
            A score in ``[0.0, 1.0]``, where 1.0 fully satisfies the
            criteria. Values outside the range are clamped by callers in
            this module.
        """
        ...


@dataclass(frozen=True)
class QualityScore:
    """A single quality metric result.

    Attributes:
        metric_name: Name of the metric (e.g. "relevance", "coherence").
        score: Normalized score in [0.0, 1.0].
        explanation: Human-readable rationale for the score.
    """

    metric_name: str
    score: float
    explanation: str


@dataclass(frozen=True)
class TokenOverlapScore:
    """Token-level precision/recall/F1 of a response against a reference.

    Attributes:
        precision: Fraction of response tokens that appear in the reference.
        recall: Fraction of reference tokens that appear in the response.
        f1: Harmonic mean of precision and recall.
    """

    precision: float
    recall: float
    f1: float


@dataclass(frozen=True)
class KeywordCoverage:
    """Which required phrases a response did and did not mention.

    Attributes:
        coverage: Fraction of required phrases present, in [0.0, 1.0]. An
            empty requirement list scores 1.0 (nothing was required).
        matched: Required phrases found in the response, original casing.
        missing: Required phrases absent from the response.
    """

    coverage: float
    matched: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class StructuredOutputCheck:
    """Whether a response parses as JSON and carries the expected keys.

    Attributes:
        is_valid_json: True if the response parsed as JSON.
        has_required_keys: True if every required key was present (trivially
            True when no keys were required or parsing failed is False).
        missing_keys: Required top-level keys absent from the parsed object.
        error: Parse error message, or ``None`` if parsing succeeded.
    """

    is_valid_json: bool
    has_required_keys: bool = False
    missing_keys: list[str] = field(default_factory=list)
    error: str | None = None


@dataclass(frozen=True)
class QualityReport:
    """Aggregate quality result for one response.

    Attributes:
        overall_score: Weighted mean of the applicable sub-scores, in
            [0.0, 1.0]. 0.0 when nothing could be scored.
        sub_scores: The individual component scores that fed
            ``overall_score``, keyed by the names in
            :data:`DEFAULT_QUALITY_WEIGHTS`.
        exact_match: True if the response equalled the reference byte for
            byte.
        normalized_match: True if the response equalled the reference after
            case/whitespace/punctuation normalization.
        token_overlap: Token precision/recall/F1 against the reference.
        fuzzy_similarity: ``difflib`` similarity ratio against the reference.
        keyword_coverage: Required-phrase coverage, or ``None`` if no
            phrases were required.
        structured_output: JSON validity check, or ``None`` if JSON output
            was not expected.
        judge_score: Clamped score returned by the injected judge, or
            ``None`` if no judge was supplied.
        enabled: False if the evaluation framework was disabled, in which
            case every other field holds its neutral default.
    """

    overall_score: float
    sub_scores: dict[str, float] = field(default_factory=dict)
    exact_match: bool = False
    normalized_match: bool = False
    token_overlap: TokenOverlapScore = TokenOverlapScore(0.0, 0.0, 0.0)
    fuzzy_similarity: float = 0.0
    keyword_coverage: KeywordCoverage | None = None
    structured_output: StructuredOutputCheck | None = None
    judge_score: float | None = None
    enabled: bool = True


def _require_str(value: object, name: str) -> str:
    """Validate that a value is a string.

    Args:
        value: The value to check.
        name: Parameter name, used in the error message.

    Returns:
        The value, narrowed to ``str``.

    Raises:
        TypeError: If ``value`` is not a string.
    """
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a str, got {type(value).__name__}.")
    return value


def _clamp_unit(value: float) -> float:
    """Clamp a number into the closed unit interval.

    Args:
        value: The raw value.

    Returns:
        ``value`` restricted to [0.0, 1.0].
    """
    return max(0.0, min(1.0, float(value)))


def normalize_text(text: str, *, drop_articles: bool = False) -> str:
    """Normalize text for case/whitespace/punctuation-insensitive comparison.

    Lowercases, strips punctuation, and collapses runs of whitespace to a
    single space.

    Args:
        text: The text to normalize.
        drop_articles: If True, also drop the English articles "a", "an",
            and "the" (SQuAD-style normalization).

    Returns:
        The normalized text, with no leading or trailing whitespace.

    Raises:
        TypeError: If ``text`` is not a string.
    """
    _require_str(text, "text")
    lowered = _PUNCTUATION_RE.sub(" ", text.lower())
    collapsed = _WHITESPACE_RE.sub(" ", lowered).strip()
    if not drop_articles:
        return collapsed
    return " ".join(word for word in collapsed.split(" ") if word and word not in _ARTICLES)


def tokenize(text: str, *, drop_stopwords: bool = False) -> list[str]:
    """Split text into normalized word tokens.

    Args:
        text: The text to tokenize.
        drop_stopwords: If True, remove very common English function words
            so that only content words remain.

    Returns:
        The token list, possibly empty.

    Raises:
        TypeError: If ``text`` is not a string.
    """
    tokens = [token for token in normalize_text(text).split(" ") if token]
    if drop_stopwords:
        return [token for token in tokens if token not in _STOPWORDS]
    return tokens


def exact_match(response: str, reference: str) -> bool:
    """Check whether a response is byte-for-byte identical to the reference.

    Args:
        response: The model-generated response.
        reference: The expected answer.

    Returns:
        True if the two strings are identical.

    Raises:
        TypeError: If either argument is not a string.
    """
    _require_str(response, "response")
    _require_str(reference, "reference")
    return response == reference


def normalized_match(response: str, reference: str, *, drop_articles: bool = True) -> bool:
    """Check response/reference equality ignoring case, spacing, punctuation.

    Args:
        response: The model-generated response.
        reference: The expected answer.
        drop_articles: Whether to ignore English articles as well, so that
            "the answer" matches "answer".

    Returns:
        True if the normalized forms are equal.

    Raises:
        TypeError: If either argument is not a string.
    """
    return normalize_text(response, drop_articles=drop_articles) == normalize_text(
        reference, drop_articles=drop_articles
    )


def token_overlap(response: str, reference: str) -> TokenOverlapScore:
    """Compute token-level precision, recall, and F1 against a reference.

    Uses multiset (bag-of-words) intersection, so a response that repeats a
    reference token three times gets credit only for the number of times it
    appears in the reference.

    Args:
        response: The model-generated response.
        reference: The expected answer.

    Returns:
        A :class:`TokenOverlapScore`. Two empty inputs score 1.0 across the
        board (a perfect, if vacuous, match); if exactly one side is empty
        every component is 0.0.

    Raises:
        TypeError: If either argument is not a string.
    """
    response_tokens = tokenize(response)
    reference_tokens = tokenize(reference)

    if not response_tokens and not reference_tokens:
        return TokenOverlapScore(1.0, 1.0, 1.0)
    if not response_tokens or not reference_tokens:
        return TokenOverlapScore(0.0, 0.0, 0.0)

    shared = Counter(response_tokens) & Counter(reference_tokens)
    overlap = sum(shared.values())
    if overlap == 0:
        return TokenOverlapScore(0.0, 0.0, 0.0)

    precision = overlap / len(response_tokens)
    recall = overlap / len(reference_tokens)
    f1 = (2 * precision * recall) / (precision + recall)
    return TokenOverlapScore(precision=precision, recall=recall, f1=f1)


def fuzzy_similarity(response: str, reference: str) -> float:
    """Compute a character-level similarity ratio between two texts.

    Wraps :class:`difflib.SequenceMatcher` over the normalized forms, so
    small typos and reorderings degrade the score gracefully instead of
    failing outright the way exact match does.

    Args:
        response: The model-generated response.
        reference: The expected answer.

    Returns:
        A similarity ratio in [0.0, 1.0]. Two empty strings score 1.0.

    Raises:
        TypeError: If either argument is not a string.
    """
    normalized_response = normalize_text(response)
    normalized_reference = normalize_text(reference)
    if not normalized_response and not normalized_reference:
        return 1.0
    return SequenceMatcher(None, normalized_response, normalized_reference).ratio()


def keyword_coverage(response: str, required_phrases: Sequence[str]) -> KeywordCoverage:
    """Check which required phrases a response actually mentioned.

    Matching is substring-based over normalized text, so "Model Context
    Protocol" matches "the model context protocol!" but not "context model".

    Args:
        response: The model-generated response.
        required_phrases: Phrases the response was required to mention.
            Blank entries are ignored.

    Returns:
        A :class:`KeywordCoverage` with the coverage fraction and the
        matched/missing phrase lists. An empty requirement list yields a
        coverage of 1.0.

    Raises:
        TypeError: If ``response`` is not a string, or ``required_phrases``
            is a string rather than a sequence of strings.
    """
    _require_str(response, "response")
    if isinstance(required_phrases, str):
        raise TypeError("required_phrases must be a sequence of str, not a single str.")

    phrases = [phrase for phrase in required_phrases if phrase and phrase.strip()]
    if not phrases:
        return KeywordCoverage(coverage=1.0)

    haystack = normalize_text(response)
    matched: list[str] = []
    missing: list[str] = []
    for phrase in phrases:
        needle = normalize_text(phrase)
        if needle and needle in haystack:
            matched.append(phrase)
        else:
            missing.append(phrase)

    return KeywordCoverage(
        coverage=len(matched) / len(phrases),
        matched=matched,
        missing=missing,
    )


def _strip_code_fence(text: str) -> str:
    """Remove a surrounding Markdown code fence, if present.

    Models routinely wrap JSON in ```json fences even when told not to;
    stripping them keeps the validity check about the JSON itself.

    Args:
        text: Raw response text.

    Returns:
        The fence contents if the whole text was one fenced block,
        otherwise the original text.
    """
    match = _CODE_FENCE_RE.match(text)
    return match.group(1) if match else text


def structured_output_validity(
    response: str, required_keys: Sequence[str] | None = None
) -> StructuredOutputCheck:
    """Check that a response parses as JSON and carries the required keys.

    Args:
        response: The model-generated response, optionally wrapped in a
            Markdown code fence.
        required_keys: Top-level keys the parsed JSON object must contain.
            ``None`` or empty means only parseability is checked.

    Returns:
        A :class:`StructuredOutputCheck`. Required keys can only be
        satisfied by a JSON *object*; a valid JSON array or scalar reports
        all required keys as missing.

    Raises:
        TypeError: If ``response`` is not a string.
    """
    _require_str(response, "response")
    keys = [key for key in (required_keys or []) if key]

    try:
        parsed: Any = json.loads(_strip_code_fence(response))
    except (json.JSONDecodeError, ValueError) as exc:
        return StructuredOutputCheck(
            is_valid_json=False,
            has_required_keys=False,
            missing_keys=list(keys),
            error=str(exc),
        )

    if not keys:
        return StructuredOutputCheck(is_valid_json=True, has_required_keys=True)

    present = set(parsed.keys()) if isinstance(parsed, dict) else set()
    missing = [key for key in keys if key not in present]
    return StructuredOutputCheck(
        is_valid_json=True,
        has_required_keys=not missing,
        missing_keys=missing,
    )


def grade_with_judge(judge: Judge, prompt: str, response: str, criteria: str) -> float:
    """Call an injected judge and clamp its verdict into [0.0, 1.0].

    Shared by :mod:`cockpit.evaluation.safety_evaluation` so both aggregates
    treat judge output identically. Exceptions raised by the judge itself
    (network errors, quota) propagate to the caller — a silently swallowed
    judge failure would look like a low score.

    Args:
        judge: The injected judge.
        prompt: Prompt passed through to the judge.
        response: Response passed through to the judge.
        criteria: Grading criteria passed through to the judge.

    Returns:
        The judge's score, clamped to the unit interval.

    Raises:
        TypeError: If the judge returns a non-numeric value.
    """
    raw = judge.score(prompt, response, criteria)
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise TypeError(f"Judge returned a non-numeric score: {type(raw).__name__}.")
    clamped = _clamp_unit(raw)
    if clamped != float(raw):
        _logger.warning("Judge returned out-of-range score %s; clamped to %s.", raw, clamped)
    return clamped


def score_relevance(prompt: str, response: str, judge: Judge | None = None) -> QualityScore:
    """Score how relevant a response is to its prompt.

    The deterministic signal is content-word recall: what fraction of the
    prompt's non-stopword tokens the response actually picks up. It is a
    coarse proxy, deliberately chosen over embedding similarity so the
    metric stays dependency-free and offline. When a ``judge`` is supplied
    its verdict is averaged with the heuristic rather than replacing it, so
    a judge can move the score but never fully mask the deterministic view.

    Args:
        prompt: The input prompt that elicited the response.
        response: The model-generated response to evaluate.
        judge: Optional injected LLM judge. ``None`` keeps scoring fully
            deterministic.

    Returns:
        A :class:`QualityScore` for the "relevance" metric.

    Raises:
        TypeError: If ``prompt`` or ``response`` is not a string, or the
            judge returns a non-numeric score.
    """
    prompt_tokens = tokenize(prompt, drop_stopwords=True)
    response_tokens = set(tokenize(response, drop_stopwords=True))

    if not prompt_tokens:
        heuristic = 1.0 if response_tokens else 0.0
        explanation = "Prompt had no content words; scored on response non-emptiness."
    else:
        hits = sum(1 for token in prompt_tokens if token in response_tokens)
        heuristic = hits / len(prompt_tokens)
        explanation = f"{hits}/{len(prompt_tokens)} prompt content words appear in the response."

    if judge is None:
        return QualityScore("relevance", heuristic, explanation)

    graded = grade_with_judge(judge, prompt, response, "Is the response relevant to the prompt?")
    blended = (heuristic + graded) / 2
    return QualityScore(
        "relevance",
        blended,
        f"{explanation} Judge scored {graded:.2f}; averaged with heuristic {heuristic:.2f}.",
    )


def score_coherence(response: str) -> QualityScore:
    """Score the internal logical/linguistic coherence of a response.

    Deterministic structural heuristics only — no language model. Starts at
    1.0 and deducts for the failure modes that are actually detectable from
    text shape: emptiness, apparent mid-sentence truncation, verbatim
    sentence repetition, and degenerate token loops.

    Args:
        response: The model-generated response to evaluate.

    Returns:
        A :class:`QualityScore` for the "coherence" metric.

    Raises:
        TypeError: If ``response`` is not a string.
    """
    _require_str(response, "response")
    stripped = response.strip()
    if not stripped:
        return QualityScore("coherence", 0.0, "Response is empty.")

    penalties: list[str] = []
    score = 1.0

    if not stripped.endswith((".", "!", "?", '"', ")", "`", "}", "]")):
        score -= 0.2
        penalties.append("appears truncated mid-sentence")

    sentences = [s.strip().lower() for s in _SENTENCE_SPLIT_RE.split(stripped) if s.strip()]
    if len(sentences) > 1:
        duplicate_ratio = 1 - (len(set(sentences)) / len(sentences))
        if duplicate_ratio > 0:
            score -= 0.5 * duplicate_ratio
            penalties.append(f"{duplicate_ratio:.0%} of sentences are verbatim repeats")

    tokens = tokenize(stripped)
    if len(tokens) >= 20:
        type_token_ratio = len(set(tokens)) / len(tokens)
        if type_token_ratio < 0.35:
            score -= 0.3
            penalties.append(f"degenerate vocabulary (type-token ratio {type_token_ratio:.2f})")

    explanation = "No structural coherence problems detected."
    if penalties:
        explanation = "Deducted for: " + "; ".join(penalties) + "."
    return QualityScore("coherence", _clamp_unit(score), explanation)


def score_factuality(response: str, reference: str) -> QualityScore:
    """Score how factually consistent a response is with a reference source.

    Uses token-level *precision* against the reference as a grounding proxy:
    what fraction of what the response says is actually attested in the
    source. Recall is deliberately not used here — a short, fully grounded
    answer should not be punished for omitting parts of the source.

    Args:
        response: The model-generated response to evaluate.
        reference: Ground-truth or retrieved source text to check against.

    Returns:
        A :class:`QualityScore` for the "factuality" metric.

    Raises:
        TypeError: If either argument is not a string.
    """
    _require_str(response, "response")
    _require_str(reference, "reference")
    if not tokenize(reference):
        return QualityScore("factuality", 0.0, "Reference text is empty; nothing to ground on.")

    overlap = token_overlap(response, reference)
    return QualityScore(
        "factuality",
        overlap.precision,
        f"{overlap.precision:.0%} of response tokens are attested in the reference.",
    )


def evaluate_quality(
    response: str,
    reference: str = "",
    *,
    prompt: str = "",
    required_phrases: Sequence[str] | None = None,
    required_json_keys: Sequence[str] | None = None,
    expect_json: bool = False,
    weights: Mapping[str, float] | None = None,
    judge: Judge | None = None,
    judge_criteria: str = DEFAULT_JUDGE_CRITERIA,
) -> QualityReport:
    """Score a response across every applicable quality dimension.

    Only the components that make sense for the call participate in the
    overall score: reference-based components need a non-empty
    ``reference``, keyword coverage needs ``required_phrases``, structured
    validity needs ``expect_json`` or ``required_json_keys``, and the judge
    component needs an injected ``judge``. Participating weights are
    renormalized so ``overall_score`` always lands in [0.0, 1.0].

    Gated by ``feature_flags.is_enabled("evaluation")``: when the framework
    is disabled this returns a neutral, empty report instead of scoring.

    Args:
        response: The model-generated response to evaluate.
        reference: Expected answer to compare against. Empty means the
            reference-based components are skipped.
        prompt: The prompt that elicited the response. Passed to the judge.
        required_phrases: Phrases the response had to mention.
        required_json_keys: Top-level keys a JSON response had to contain.
            Supplying these implies ``expect_json``.
        expect_json: Whether the response was supposed to be JSON.
        weights: Overrides for :data:`DEFAULT_QUALITY_WEIGHTS`. Unknown keys
            are ignored; missing keys fall back to the defaults.
        judge: Optional injected LLM judge for open-ended responses.
        judge_criteria: Criteria string handed to the judge.

    Returns:
        A :class:`QualityReport` with each sub-score and the weighted
        overall score.

    Raises:
        TypeError: If ``response`` or ``reference`` is not a string, or the
            judge returns a non-numeric score.
        ValueError: If the resolved weights of the participating components
            sum to zero, making the overall score undefined.
    """
    if not is_enabled("evaluation"):
        _logger.debug("Evaluation framework disabled; skipping quality evaluation.")
        return QualityReport(overall_score=0.0, enabled=False)

    _require_str(response, "response")
    _require_str(reference, "reference")

    resolved_weights = dict(DEFAULT_QUALITY_WEIGHTS)
    if weights:
        resolved_weights.update({k: float(v) for k, v in weights.items() if k in resolved_weights})

    sub_scores: dict[str, float] = {}

    is_exact = exact_match(response, reference)
    is_normalized = normalized_match(response, reference)
    overlap = token_overlap(response, reference)
    similarity = fuzzy_similarity(response, reference)

    if reference.strip():
        sub_scores["normalized_match"] = 1.0 if is_normalized else 0.0
        sub_scores["token_f1"] = overlap.f1
        sub_scores["fuzzy_similarity"] = similarity

    coverage: KeywordCoverage | None = None
    if required_phrases:
        coverage = keyword_coverage(response, required_phrases)
        sub_scores["keyword_coverage"] = coverage.coverage

    structured: StructuredOutputCheck | None = None
    if expect_json or required_json_keys:
        structured = structured_output_validity(response, required_json_keys)
        sub_scores["structured_validity"] = (
            1.0 if structured.is_valid_json and structured.has_required_keys else 0.0
        )

    graded: float | None = None
    if judge is not None:
        graded = grade_with_judge(judge, prompt, response, judge_criteria)
        sub_scores["judge"] = graded

    overall = 0.0
    if sub_scores:
        total_weight = sum(resolved_weights[name] for name in sub_scores)
        if total_weight <= 0:
            raise ValueError("Participating quality weights sum to zero; overall score undefined.")
        overall = _clamp_unit(
            sum(resolved_weights[name] * value for name, value in sub_scores.items()) / total_weight
        )
    else:
        _logger.debug("No quality components applied to this call; overall score is 0.0.")

    return QualityReport(
        overall_score=overall,
        sub_scores=sub_scores,
        exact_match=is_exact,
        normalized_match=is_normalized,
        token_overlap=overlap,
        fuzzy_similarity=similarity,
        keyword_coverage=coverage,
        structured_output=structured,
        judge_score=graded,
    )
