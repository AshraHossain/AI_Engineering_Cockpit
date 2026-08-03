"""Evaluation framework: quality, safety, and cost metrics.

Real, working logic built on the standard library only, so evaluation runs
offline, deterministically, and without adding a dependency:

* :mod:`cockpit.evaluation.quality_metrics` — exact/normalized match, token
  precision/recall/F1, fuzzy similarity, required-phrase coverage,
  structured-output validity, and the :func:`~cockpit.evaluation.quality_metrics.evaluate_quality`
  aggregate.
* :mod:`cockpit.evaluation.safety_evaluation` — refusal detection, PII
  leakage (delegated to :mod:`cockpit.security.output_security`),
  injection-compliance checking, and the
  :func:`~cockpit.evaluation.safety_evaluation.evaluate_safety` aggregate.
* :mod:`cockpit.evaluation.cost_evaluation` — cost per successful answer,
  quality-per-dollar, best-value model ranking, and budget checks, priced
  via :mod:`cockpit.monitoring.cost_tracking`.

LLM-graded evaluation is supported but never hardcoded: no module here
imports a model SDK. Callers inject an object satisfying the
:class:`~cockpit.evaluation.quality_metrics.Judge` protocol; omitting it
keeps scoring purely deterministic.

The three aggregate entry points are gated by
``feature_flags.is_enabled("evaluation")`` and return a neutral, empty
result when the framework is disabled. The individual metric functions are
pure math and are always available.
"""
