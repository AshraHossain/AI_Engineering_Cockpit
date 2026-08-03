"""Monitoring framework: cost tracking, performance metrics, dashboards.

Three modules, layered so the cheap parts stay usable everywhere:

* :mod:`cockpit.monitoring.cost_tracking` — token-usage and spend accounting.
  :func:`~cockpit.monitoring.cost_tracking.estimate_cost` prices a call from
  its token counts; :class:`~cockpit.monitoring.cost_tracking.CostTracker`
  accumulates and rolls up what was actually spent.
* :mod:`cockpit.monitoring.performance_metrics` — latency and throughput.
  :class:`~cockpit.monitoring.performance_metrics.PerformanceTracker` records
  per-call timings (``with tracker.measure("op"): ...``) and summarizes them
  into p50/p95/p99 latencies plus an error rate.
* :mod:`cockpit.monitoring.dashboard` — a read-only rollup that composes the
  other two into a :class:`~cockpit.monitoring.dashboard.DashboardSnapshot`
  and renders it as plain text.

The ``monitoring`` feature flag (see :mod:`cockpit.config.feature_flags`) is
enforced only at the module-level recording entry points — ``record_cost``
and ``record_latency`` no-op when it is off. Pure computation (cost
estimation, percentiles, summaries, rendering) is never gated, so callers can
price or analyze a call without turning the framework on globally, and tests
can exercise the math without touching flags.

Trackers are thread-safe: model calls are routinely fanned out across a
thread pool.
"""
