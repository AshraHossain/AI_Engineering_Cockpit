"""Unit tests for cockpit/red_teaming/*.

Everything here runs against in-memory fake targets -- no network, no LLM
SDK, no credentials. The fakes are the point: the harness takes its target
as an injected ``str -> str`` callable precisely so its behavior can be
pinned down deterministically.

The ``red_teaming`` feature flag is forced explicitly in both directions
rather than relying on whatever ``FRAMEWORKS_ENABLED`` currently says, so
these tests stay correct whichever way the flag is set in the repo.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator

import pytest

from cockpit.red_teaming import adversarial_tests, edge_case_tests, prompt_injection
from cockpit.red_teaming.adversarial_tests import (
    AdversarialCase,
    AttackOutcome,
    OutcomeStatus,
    generate_adversarial_cases,
    run_adversarial_suite,
    run_campaign,
    run_payload,
    summarize_outcomes,
)
from cockpit.red_teaming.edge_case_tests import (
    EdgeCase,
    EdgeCaseCategory,
    RobustnessStatus,
    generate_edge_cases,
    run_edge_case,
    run_edge_case_campaign,
    run_edge_case_suite,
)
from cockpit.red_teaming.prompt_injection import (
    COMPLIANCE_MARKER,
    INJECTION_CORPUS,
    AttackCategory,
    InjectionPayload,
    Severity,
    SuccessIndicator,
    build_canary_probe,
    canary_leaked,
    evaluate_injection_resistance,
    generate_canary_token,
    generate_injection_attempts,
    get_corpus,
    judge_injection_success,
    looks_like_refusal,
    payloads_evading_defense,
    scan_corpus_against_defense,
)

# --------------------------------------------------------------------------
# Fake targets
# --------------------------------------------------------------------------


def always_refuses(prompt: str) -> str:
    """A target that declines everything.

    Args:
        prompt: Ignored.

    Returns:
        A fixed refusal string.
    """
    return "I'm sorry, I cannot help with that request."


def always_complies(prompt: str) -> str:
    """A target with no defenses at all.

    Args:
        prompt: Ignored.

    Returns:
        A response containing the compliance marker.
    """
    return COMPLIANCE_MARKER


def always_raises(prompt: str) -> str:
    """A target that is broken or unreachable.

    Args:
        prompt: Ignored.

    Returns:
        Never returns.

    Raises:
        RuntimeError: Always.
    """
    raise RuntimeError("upstream provider exploded")


def echoes(prompt: str) -> str:
    """A target that echoes whatever it is given.

    Args:
        prompt: The input to echo.

    Returns:
        The prompt wrapped in a short acknowledgement.
    """
    return f"You said: {prompt}"


@pytest.fixture
def red_teaming_enabled(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Force the ``red_teaming`` feature flag on for one test.

    Yields:
        None.
    """
    for module in (adversarial_tests, edge_case_tests, prompt_injection):
        monkeypatch.setattr(module, "is_enabled", lambda _framework: True)
    yield


@pytest.fixture
def red_teaming_disabled(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Force the ``red_teaming`` feature flag off for one test.

    Yields:
        None.
    """
    for module in (adversarial_tests, edge_case_tests, prompt_injection):
        monkeypatch.setattr(module, "is_enabled", lambda _framework: False)
    yield


# --------------------------------------------------------------------------
# Corpus integrity
# --------------------------------------------------------------------------


class TestCorpusIntegrity:
    """The shipped payload corpus must be non-empty and well-formed."""

    def test_corpus_is_non_empty(self) -> None:
        assert len(INJECTION_CORPUS) >= 20

    def test_payload_ids_are_unique(self) -> None:
        ids = [p.payload_id for p in INJECTION_CORPUS]
        assert len(ids) == len(set(ids))

    @pytest.mark.parametrize("payload", INJECTION_CORPUS, ids=lambda p: p.payload_id)
    def test_every_payload_is_well_formed(self, payload: InjectionPayload) -> None:
        assert payload.payload_id
        assert payload.payload.strip()
        assert payload.description.strip()
        assert isinstance(payload.category, AttackCategory)
        assert isinstance(payload.severity, Severity)

    @pytest.mark.parametrize("payload", INJECTION_CORPUS, ids=lambda p: p.payload_id)
    def test_every_payload_is_adjudicable(self, payload: InjectionPayload) -> None:
        """A payload with no success signal could never be scored."""
        indicator = payload.success_indicator
        assert indicator.expected_substrings or indicator.response_pattern or indicator.canary_leak

    def test_every_category_has_at_least_one_payload(self) -> None:
        covered = {p.category for p in INJECTION_CORPUS}
        assert covered == set(AttackCategory)

    def test_corpus_entries_are_frozen(self) -> None:
        with pytest.raises(AttributeError):
            INJECTION_CORPUS[0].payload_id = "mutated"  # type: ignore[misc]

    def test_get_corpus_filters_by_category(self) -> None:
        selected = get_corpus(categories=(AttackCategory.SYSTEM_PROMPT_LEAK,))
        assert selected
        assert all(p.category is AttackCategory.SYSTEM_PROMPT_LEAK for p in selected)

    def test_get_corpus_filters_by_min_severity(self) -> None:
        selected = get_corpus(min_severity=Severity.HIGH)
        assert selected
        assert all(p.severity in {Severity.HIGH, Severity.CRITICAL} for p in selected)

    def test_get_corpus_rejects_empty_category_tuple(self) -> None:
        with pytest.raises(ValueError):
            get_corpus(categories=())

    def test_generate_injection_attempts_cycles_the_corpus(self) -> None:
        attempts = generate_injection_attempts(count=len(INJECTION_CORPUS) + 3)
        assert len(attempts) == len(INJECTION_CORPUS) + 3
        assert attempts[0].payload == attempts[len(INJECTION_CORPUS)].payload

    def test_generate_injection_attempts_rejects_non_positive_count(self) -> None:
        with pytest.raises(ValueError):
            generate_injection_attempts(count=0)


class TestOffenseDefenseSplit:
    """The corpus is scored against the security module, not by re-detecting."""

    def test_scan_corpus_against_defense_covers_every_payload(self) -> None:
        results = scan_corpus_against_defense()
        assert set(results) == {p.payload_id for p in INJECTION_CORPUS}

    def test_defense_catches_the_canonical_override(self) -> None:
        results = scan_corpus_against_defense()
        assert results["io-001-ignore-previous"].is_suspicious is True

    def test_obfuscated_payloads_are_caught_after_deobfuscation(self) -> None:
        """Regression guard for the filter's normalize/decode layers.

        These two evaded the original literal-text regexes. They are caught
        now only because ``scan_for_prompt_injection`` folds obfuscation and
        decodes encoded text before matching -- if either layer regresses,
        this fails.
        """
        evaders = set(payloads_evading_defense())
        assert "eo-001-base64" not in evaders
        assert "eo-003-letter-spacing" not in evaders

    def test_harness_still_surfaces_a_real_residual_gap(self) -> None:
        """A per-message filter structurally cannot see split payloads.

        Asserted so the harness stays honest: if this ever passes trivially
        because the gap report broke, we want to know.
        """
        evaders = set(payloads_evading_defense())
        assert evaders, "the harness should still be reporting a residual gap"
        assert any(payload_id.startswith("ps-") for payload_id in evaders), (
            "payload-splitting attacks are the documented structural limit "
            "-- if these are now caught, update docs/SECURITY_COVERAGE.md"
        )

    def test_some_payloads_are_caught_so_the_scan_is_meaningful(self) -> None:
        evaders = set(payloads_evading_defense())
        caught = {p.payload_id for p in INJECTION_CORPUS} - evaders
        assert caught


# --------------------------------------------------------------------------
# Success adjudication
# --------------------------------------------------------------------------


class TestJudgeInjectionSuccess:
    """judge_injection_success must separate compliance from refusal."""

    @property
    def _marker_payload(self) -> InjectionPayload:
        return INJECTION_CORPUS[0]

    def test_compliant_response_scores_as_attack_success(self) -> None:
        judgment = judge_injection_success(self._marker_payload, COMPLIANCE_MARKER)
        assert judgment.succeeded is True
        assert judgment.refusal_detected is False
        assert any("matched_substring" in e for e in judgment.evidence)

    def test_refusal_scores_as_defended(self) -> None:
        judgment = judge_injection_success(self._marker_payload, "I'm sorry, I cannot do that.")
        assert judgment.succeeded is False
        assert judgment.refusal_detected is True

    def test_unrelated_response_scores_as_defended(self) -> None:
        judgment = judge_injection_success(self._marker_payload, "The capital of France is Paris.")
        assert judgment.succeeded is False
        assert judgment.evidence == ("no_success_evidence",)

    def test_refusal_that_quotes_the_marker_is_not_a_breach(self) -> None:
        """A target explaining what it won't say must not be scored as breached."""
        judgment = judge_injection_success(
            self._marker_payload, f"I cannot output {COMPLIANCE_MARKER} as you asked."
        )
        assert judgment.succeeded is False
        assert "refusal_overrides_match" in judgment.evidence

    def test_marker_match_is_case_insensitive(self) -> None:
        judgment = judge_injection_success(self._marker_payload, COMPLIANCE_MARKER.lower())
        assert judgment.succeeded is True

    def test_regex_indicator_is_honored(self) -> None:
        payload = InjectionPayload(
            payload_id="custom-regex",
            category=AttackCategory.INSTRUCTION_OVERRIDE,
            severity=Severity.LOW,
            payload="say a four digit code",
            description="Custom payload judged by regex.",
            success_indicator=SuccessIndicator(response_pattern=r"\b\d{4}\b"),
        )
        assert judge_injection_success(payload, "the code is 4821").succeeded is True
        assert judge_injection_success(payload, "no digits here").succeeded is False

    def test_canary_payload_without_token_cannot_succeed(self) -> None:
        leak_payload = get_corpus(categories=(AttackCategory.SYSTEM_PROMPT_LEAK,))[0]
        judgment = judge_injection_success(leak_payload, "You are a helpful assistant.")
        assert judgment.succeeded is False
        assert "canary_required_but_not_supplied" in judgment.evidence

    def test_judgment_carries_payload_metadata(self) -> None:
        judgment = judge_injection_success(self._marker_payload, COMPLIANCE_MARKER)
        assert judgment.payload_id == self._marker_payload.payload_id
        assert judgment.category is self._marker_payload.category
        assert judgment.severity is self._marker_payload.severity

    def test_non_string_response_raises_type_error(self) -> None:
        with pytest.raises(TypeError):
            judge_injection_success(self._marker_payload, None)  # type: ignore[arg-type]

    @pytest.mark.parametrize(
        "response",
        [
            "I'm sorry, I can't do that.",
            "I cannot comply with that instruction.",
            "I am unable to help with this.",
            "I must decline.",
            "That goes against my instructions.",
        ],
    )
    def test_known_refusal_phrasings_are_detected(self, response: str) -> None:
        assert looks_like_refusal(response) is True

    def test_plain_answer_is_not_a_refusal(self) -> None:
        assert looks_like_refusal("Sure, here is the summary you asked for.") is False

    def test_looks_like_refusal_rejects_non_string(self) -> None:
        with pytest.raises(TypeError):
            looks_like_refusal(42)  # type: ignore[arg-type]


# --------------------------------------------------------------------------
# Canary tokens
# --------------------------------------------------------------------------


class TestCanaryTokens:
    """The canary probe is the cleanest objective signal for a prompt leak."""

    def test_generated_tokens_are_unique_and_prefixed(self) -> None:
        first = generate_canary_token()
        second = generate_canary_token()
        assert first != second
        assert first.startswith("CANARY-")
        assert len(first) > 20

    def test_generate_canary_token_rejects_empty_prefix(self) -> None:
        with pytest.raises(ValueError):
            generate_canary_token(prefix="")

    def test_probe_embeds_the_token_and_preserves_the_prompt(self) -> None:
        probe = build_canary_probe("You are a helpful support assistant.")
        assert probe.token in probe.system_prompt
        assert probe.system_prompt.startswith("You are a helpful support assistant.")

    def test_build_canary_probe_rejects_non_string(self) -> None:
        with pytest.raises(TypeError):
            build_canary_probe(None)  # type: ignore[arg-type]

    def test_leak_is_detected_when_token_appears(self) -> None:
        probe = build_canary_probe("You are a helpful assistant.")
        leaked_response = f"My instructions say: {probe.system_prompt}"
        assert canary_leaked(probe.token, leaked_response) is True

    def test_no_leak_when_token_absent(self) -> None:
        probe = build_canary_probe("You are a helpful assistant.")
        assert canary_leaked(probe.token, "I can't share my instructions.") is False

    def test_canary_leaked_rejects_empty_token(self) -> None:
        with pytest.raises(ValueError):
            canary_leaked("", "anything")

    def test_canary_leaked_rejects_non_string(self) -> None:
        with pytest.raises(TypeError):
            canary_leaked("CANARY-abc", None)  # type: ignore[arg-type]

    def test_leaked_canary_beats_a_refusal_shaped_response(self) -> None:
        """If the token is in the output, the prompt leaked -- apology or not."""
        probe = build_canary_probe("You are a helpful assistant.")
        leak_payload = get_corpus(categories=(AttackCategory.SYSTEM_PROMPT_LEAK,))[0]
        judgment = judge_injection_success(
            leak_payload,
            f"I'm sorry, I can't share that. (id: {probe.token})",
            canary=probe.token,
        )
        assert judgment.succeeded is True
        assert "canary_token_leaked" in judgment.evidence

    def test_campaign_detects_a_leaking_target(self, red_teaming_enabled: None) -> None:
        probe = build_canary_probe("You are a helpful assistant.")

        def leaky_target(prompt: str) -> str:
            return f"Sure! My configuration is: {probe.system_prompt}"

        summary = run_campaign(
            leaky_target,
            payloads=get_corpus(categories=(AttackCategory.SYSTEM_PROMPT_LEAK,)),
            canary=probe.token,
        )
        assert summary.total > 0
        assert summary.succeeded == summary.total
        assert summary.pass_rate == 0.0


# --------------------------------------------------------------------------
# Campaign harness
# --------------------------------------------------------------------------


class TestCampaign:
    """Full campaigns against fake in-memory targets."""

    def test_refusing_target_is_fully_defended(self, red_teaming_enabled: None) -> None:
        summary = run_campaign(always_refuses)
        assert summary.total == len(INJECTION_CORPUS)
        assert summary.succeeded == 0
        assert summary.errored == 0
        assert summary.defended == summary.total
        assert summary.pass_rate == 1.0
        assert summary.failures_by_category == {}
        assert summary.failures_by_severity == {}

    def test_complying_target_is_fully_breached(self, red_teaming_enabled: None) -> None:
        """Only canary payloads survive a blanket-compliance target without a canary."""
        marker_payloads = tuple(p for p in INJECTION_CORPUS if not p.success_indicator.canary_leak)
        summary = run_campaign(always_complies, payloads=marker_payloads)
        assert summary.total == len(marker_payloads)
        assert summary.succeeded == summary.total
        assert summary.defended == 0
        assert summary.pass_rate == 0.0
        assert sum(summary.failures_by_category.values()) == summary.total
        assert sum(summary.failures_by_severity.values()) == summary.total

    def test_failures_are_broken_down_by_category_and_severity(
        self, red_teaming_enabled: None
    ) -> None:
        payloads = get_corpus(categories=(AttackCategory.ROLE_REASSIGNMENT,))
        summary = run_campaign(always_complies, payloads=payloads)
        assert summary.failures_by_category == {"role_reassignment": len(payloads)}
        assert set(summary.failures_by_severity) <= {s.value for s in Severity}

    def test_raising_target_is_recorded_not_propagated(self, red_teaming_enabled: None) -> None:
        summary = run_campaign(always_raises)
        assert summary.total == len(INJECTION_CORPUS)
        assert summary.errored == summary.total
        assert summary.succeeded == 0
        assert summary.defended == 0
        assert summary.pass_rate == 0.0
        assert all(o.status is OutcomeStatus.ERRORED for o in summary.outcomes)
        assert all("RuntimeError" in (o.error or "") for o in summary.outcomes)

    def test_partially_broken_target_still_completes_the_campaign(
        self, red_teaming_enabled: None
    ) -> None:
        calls = {"n": 0}

        def flaky(prompt: str) -> str:
            calls["n"] += 1
            if calls["n"] % 2 == 0:
                raise ValueError("transient failure")
            return COMPLIANCE_MARKER

        payloads = get_corpus(categories=(AttackCategory.INSTRUCTION_OVERRIDE,))
        summary = run_campaign(flaky, payloads=payloads)
        assert summary.total == len(payloads)
        assert summary.errored > 0
        assert summary.succeeded > 0
        assert summary.errored + summary.succeeded + summary.defended == summary.total

    def test_target_returning_non_string_is_an_error_outcome(
        self, red_teaming_enabled: None
    ) -> None:
        def bad_target(prompt: str) -> str:
            return None  # type: ignore[return-value]

        summary = run_campaign(bad_target, payloads=(INJECTION_CORPUS[0],))
        assert summary.errored == 1
        assert "expected str" in (summary.outcomes[0].error or "")

    def test_non_callable_target_raises_type_error(self, red_teaming_enabled: None) -> None:
        with pytest.raises(TypeError):
            run_campaign("not a callable")  # type: ignore[arg-type]

    def test_run_payload_marks_breach(self) -> None:
        outcome = run_payload(INJECTION_CORPUS[0], always_complies)
        assert outcome.status is OutcomeStatus.SUCCEEDED
        assert outcome.breached is True

    def test_run_payload_marks_defense(self) -> None:
        outcome = run_payload(INJECTION_CORPUS[0], always_refuses)
        assert outcome.status is OutcomeStatus.DEFENDED
        assert outcome.breached is False

    def test_summarize_empty_outcomes(self) -> None:
        summary = summarize_outcomes(())
        assert summary.total == 0
        assert summary.pass_rate == 1.0

    def test_summarize_counts_each_status(self) -> None:
        outcomes = (
            AttackOutcome(
                "a", AttackCategory.INSTRUCTION_OVERRIDE, Severity.HIGH, OutcomeStatus.SUCCEEDED
            ),
            AttackOutcome(
                "b", AttackCategory.INSTRUCTION_OVERRIDE, Severity.LOW, OutcomeStatus.DEFENDED
            ),
            AttackOutcome(
                "c", AttackCategory.DELIMITER_ESCAPE, Severity.HIGH, OutcomeStatus.ERRORED
            ),
            AttackOutcome(
                "d", AttackCategory.DELIMITER_ESCAPE, Severity.HIGH, OutcomeStatus.SUCCEEDED
            ),
        )
        summary = summarize_outcomes(outcomes)
        assert (summary.total, summary.succeeded, summary.defended, summary.errored) == (4, 2, 1, 1)
        assert summary.pass_rate == 0.25
        assert summary.failures_by_category == {"instruction_override": 1, "delimiter_escape": 1}
        assert summary.failures_by_severity == {"high": 2}

    def test_campaign_returns_empty_summary_when_disabled(self, red_teaming_disabled: None) -> None:
        summary = run_campaign(always_complies)
        assert summary.total == 0
        assert summary.succeeded == 0
        assert summary.pass_rate == 1.0
        assert summary.outcomes == ()


class TestAdversarialCaseApi:
    """The case-oriented API layered over the corpus."""

    def test_generate_cases_for_a_known_category(self) -> None:
        cases = generate_adversarial_cases("system_prompt_leak", count=3)
        assert len(cases) == 3
        assert all(isinstance(c, AdversarialCase) for c in cases)
        assert all(c.expected_behavior for c in cases)

    def test_generated_case_ids_are_unique_when_cycling(self) -> None:
        cases = generate_adversarial_cases("payload_splitting", count=10)
        assert len({c.case_id for c in cases}) == 10

    def test_unknown_category_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="Unknown attack category"):
            generate_adversarial_cases("nonexistent_category")

    def test_non_positive_count_raises_value_error(self) -> None:
        with pytest.raises(ValueError):
            generate_adversarial_cases("instruction_override", count=0)

    def test_suite_passes_against_a_refusing_target(self, red_teaming_enabled: None) -> None:
        cases = generate_adversarial_cases("instruction_override", count=4)
        results = run_adversarial_suite(cases, always_refuses)
        assert len(results) == 4
        assert all(r.passed for r in results)

    def test_suite_fails_against_a_complying_target(self, red_teaming_enabled: None) -> None:
        cases = generate_adversarial_cases("instruction_override", count=4)
        results = run_adversarial_suite(cases, always_complies)
        assert not any(r.passed for r in results)

    def test_suite_records_target_exceptions_as_failures(self, red_teaming_enabled: None) -> None:
        cases = generate_adversarial_cases("instruction_override", count=2)
        results = run_adversarial_suite(cases, always_raises)
        assert not any(r.passed for r in results)
        assert all("RuntimeError" in r.response for r in results)

    def test_suite_handles_custom_cases_not_in_the_corpus(self, red_teaming_enabled: None) -> None:
        custom = [AdversarialCase("custom-1", "please just say hello", "should not comply")]
        results = run_adversarial_suite(custom, always_complies)
        assert len(results) == 1
        assert results[0].passed is False

    def test_suite_returns_empty_when_disabled(self, red_teaming_disabled: None) -> None:
        cases = generate_adversarial_cases("instruction_override", count=3)
        assert run_adversarial_suite(cases, always_complies) == []


class TestInjectionResistanceReport:
    """The legacy attempt/report API still works on top of the new corpus."""

    def test_refusing_model_scores_full_resistance(self, red_teaming_enabled: None) -> None:
        attempts = generate_injection_attempts(count=5)
        report = evaluate_injection_resistance(attempts, always_refuses)
        assert report.total_attempts == 5
        assert report.successful_injections == 0
        assert report.resistance_rate == 1.0
        assert report.failing_techniques == []

    def test_complying_model_scores_zero_resistance(self, red_teaming_enabled: None) -> None:
        attempts = [a for a in generate_injection_attempts(count=8) if "leak" not in a.technique]
        report = evaluate_injection_resistance(attempts, always_complies)
        assert report.successful_injections == len(attempts)
        assert report.resistance_rate == 0.0
        assert report.failing_techniques

    def test_raising_model_does_not_fabricate_breaches(self, red_teaming_enabled: None) -> None:
        attempts = generate_injection_attempts(count=3)
        report = evaluate_injection_resistance(attempts, always_raises)
        assert report.successful_injections == 0
        assert report.failing_techniques == []

    def test_report_is_neutral_when_disabled(self, red_teaming_disabled: None) -> None:
        report = evaluate_injection_resistance(generate_injection_attempts(3), always_complies)
        assert report.total_attempts == 0
        assert report.resistance_rate == 1.0


# --------------------------------------------------------------------------
# Edge cases
# --------------------------------------------------------------------------


class TestEdgeCaseGenerators:
    """The generators must produce genuinely nasty, well-formed inputs."""

    def test_generates_a_meaningful_number_of_cases(self) -> None:
        cases = generate_edge_cases()
        assert len(cases) >= 25

    def test_case_ids_are_unique(self) -> None:
        cases = generate_edge_cases()
        assert len({c.case_id for c in cases}) == len(cases)

    def test_every_case_is_well_formed(self) -> None:
        for case in generate_edge_cases():
            assert case.case_id
            assert case.description
            assert isinstance(case.input_value, str)
            assert isinstance(case.category, EdgeCaseCategory)

    def test_every_generator_category_is_represented(self) -> None:
        covered = {c.category for c in generate_edge_cases()}
        expected = set(EdgeCaseCategory) - {EdgeCaseCategory.UNCLASSIFIED}
        assert covered == expected

    def test_includes_a_truly_empty_input(self) -> None:
        assert any(c.input_value == "" for c in generate_edge_cases())

    def test_includes_whitespace_only_inputs(self) -> None:
        cases = generate_edge_cases()
        whitespace = [c for c in cases if c.category is EdgeCaseCategory.WHITESPACE]
        assert len(whitespace) >= 4
        assert all(c.input_value for c in whitespace)
        assert all(not any(ch.isalnum() for ch in c.input_value) for c in whitespace)

    def test_includes_an_input_beyond_the_platform_length_limit(self) -> None:
        longest = max(len(c.input_value) for c in generate_edge_cases())
        assert longest >= 100_000

    def test_includes_zero_width_and_rtl_characters(self) -> None:
        blob = "".join(c.input_value for c in generate_edge_cases())
        assert "\u200b" in blob
        assert "\u200d" in blob
        assert "\u202e" in blob

    def test_includes_control_characters(self) -> None:
        blob = "".join(c.input_value for c in generate_edge_cases())
        assert "\x00" in blob
        assert "\x1b" in blob

    def test_includes_deeply_nested_structured_data(self) -> None:
        nested = [
            c
            for c in generate_edge_cases()
            if c.category is EdgeCaseCategory.STRUCTURED_DATA and c.input_value.count("{") > 50
        ]
        assert nested

    def test_includes_punctuation_only_input(self) -> None:
        punct = [c for c in generate_edge_cases() if c.category is EdgeCaseCategory.PUNCTUATION]
        assert punct
        assert all(not any(ch.isalnum() for ch in c.input_value) for c in punct)

    def test_includes_highly_repetitive_input(self) -> None:
        repeats = [c for c in generate_edge_cases() if c.category is EdgeCaseCategory.REPETITION]
        assert repeats
        assert any(c.input_value.count("spam") >= 1000 for c in repeats)

    def test_edge_case_defaults_to_unclassified(self) -> None:
        case = EdgeCase("ad-hoc", "manually built", "hi")
        assert case.category is EdgeCaseCategory.UNCLASSIFIED


class TestEdgeCaseRunner:
    """The robustness runner must flag crashes, silence, and slowness."""

    def test_healthy_target_passes_everything(self, red_teaming_enabled: None) -> None:
        report = run_edge_case_campaign(echoes)
        assert report.total == len(generate_edge_cases())
        assert report.failed == 0
        assert report.pass_rate == 1.0
        assert report.failures_by_category == {}

    def test_crashing_target_fails_everything(self, red_teaming_enabled: None) -> None:
        report = run_edge_case_campaign(always_raises)
        assert report.passed == 0
        assert report.pass_rate == 0.0
        assert all(r.status is RobustnessStatus.ERRORED for r in report.results)
        assert sum(report.failures_by_category.values()) == report.total

    def test_empty_response_is_a_robustness_failure(self, red_teaming_enabled: None) -> None:
        def silent(prompt: str) -> str:
            return "   \n  "

        report = run_edge_case_campaign(silent, cases=generate_edge_cases()[:3])
        assert report.failed == 3
        assert all(r.status is RobustnessStatus.EMPTY_RESPONSE for r in report.results)

    def test_slow_target_is_flagged_as_timed_out(self) -> None:
        import time as _time

        def slow(prompt: str) -> str:
            _time.sleep(0.02)
            return "eventually"

        case = EdgeCase("slow-1", "slow target", "hi", EdgeCaseCategory.LENGTH)
        result = run_edge_case(case, slow, timeout_seconds=0.001)
        assert result.status is RobustnessStatus.TIMED_OUT
        assert result.elapsed_seconds > 0

    def test_non_string_response_is_an_error(self) -> None:
        def bad(prompt: str) -> str:
            return 123  # type: ignore[return-value]

        case = EdgeCase("bad-1", "non-string target", "hi")
        result = run_edge_case(case, bad)
        assert result.status is RobustnessStatus.ERRORED
        assert "expected str" in (result.error or "")

    def test_run_edge_case_rejects_non_positive_timeout(self) -> None:
        with pytest.raises(ValueError):
            run_edge_case(EdgeCase("t", "t", "t"), echoes, timeout_seconds=0)

    def test_campaign_rejects_non_callable_target(self, red_teaming_enabled: None) -> None:
        with pytest.raises(TypeError):
            run_edge_case_campaign("not callable")  # type: ignore[arg-type]

    def test_suite_returns_case_id_mapping(self, red_teaming_enabled: None) -> None:
        cases = generate_edge_cases()[:5]
        outcome = run_edge_case_suite(cases, echoes)
        assert set(outcome) == {c.case_id for c in cases}
        assert all(outcome.values())

    def test_suite_marks_failures_false(self, red_teaming_enabled: None) -> None:
        cases = generate_edge_cases()[:4]
        outcome = run_edge_case_suite(cases, always_raises)
        assert not any(outcome.values())

    def test_campaign_returns_empty_report_when_disabled(self, red_teaming_disabled: None) -> None:
        report = run_edge_case_campaign(always_raises)
        assert report.total == 0
        assert report.pass_rate == 1.0
        assert report.results == ()

    def test_suite_returns_empty_mapping_when_disabled(self, red_teaming_disabled: None) -> None:
        assert run_edge_case_suite(generate_edge_cases()[:3], always_raises) == {}


class TestTargetProtocol:
    """The target is injected, never imported -- any str -> str callable works."""

    @pytest.mark.parametrize(
        "target",
        [always_refuses, always_complies, echoes, lambda _prompt: "fine"],
    )
    def test_any_str_to_str_callable_is_a_valid_target(
        self, target: Callable[[str], str], red_teaming_enabled: None
    ) -> None:
        summary = run_campaign(target, payloads=(INJECTION_CORPUS[0],))
        assert summary.total == 1
        assert summary.errored == 0

    def test_no_llm_sdk_is_imported_by_the_red_teaming_package(self) -> None:
        """CI must stay offline: the harness may not pull in a provider SDK."""
        import sys

        forbidden = {"openai", "anthropic", "google.genai", "requests"}
        for module in (adversarial_tests, edge_case_tests, prompt_injection):
            imported = {
                name
                for name in vars(module)
                if isinstance(vars(module)[name], type(sys)) and name in forbidden
            }
            assert imported == set()
