"""Loop guard, per-version run windows, alert rules and their delivery.

Two different time scales:

* :class:`LoopGuard` works *inside* one run. It sees each turn's tool calls
  before they execute and stops a run that is going in circles.
* :class:`AlertMonitor` works *across* runs. It evaluates fixed rules over the
  last :data:`WINDOW_SIZE` runs of each version and reports only transitions,
  so a breached rule fires once rather than on every run.

Windows are keyed by version, not by canary group, so after a promotion the
new version's numbers never mix with the old version's.
"""

from __future__ import annotations

import json
import logging
from collections import Counter, defaultdict, deque
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

from cockpit.monitoring.performance_metrics import percentile

if TYPE_CHECKING:
    from agent import RunRecord

_logger = logging.getLogger(__name__)

REPEAT_LIMIT: Final = 3
PER_TOOL_LIMIT: Final = 5
WINDOW_SIZE: Final = 20


@dataclass
class LoopGuard:
    """Stops a run that repeats itself. Create a fresh one per run.

    Attributes:
        repeat_limit: Identical calls (same tool, same input) that trip it.
        per_tool_limit: Calls to one tool, whatever the input, that trip it.
    """

    repeat_limit: int = REPEAT_LIMIT
    per_tool_limit: int = PER_TOOL_LIMIT
    _identical: Counter[tuple[str, str]] = field(default_factory=Counter)
    _per_tool: Counter[str] = field(default_factory=Counter)

    def check(self, calls: Iterable[tuple[str, Mapping[str, Any]]]) -> str | None:
        """Count one turn's tool calls and report the first limit reached.

        Args:
            calls: ``(tool_name, input)`` pairs from one assistant turn, in order.

        Returns:
            ``"repeat:<tool>"`` or ``"no_progress:<tool>"`` when a limit is
            reached, otherwise None.
        """
        for name, tool_input in calls:
            key = (name, json.dumps(tool_input, sort_keys=True))
            self._identical[key] += 1
            self._per_tool[name] += 1
            if self._identical[key] >= self.repeat_limit:
                return f"repeat:{name}"
            if self._per_tool[name] >= self.per_tool_limit:
                return f"no_progress:{name}"
        return None


@dataclass(frozen=True)
class WindowStats:
    """Summary of one version's most recent runs.

    Attributes:
        runs: Runs in the window.
        loops: Runs that ended with outcome ``loop``.
        failure_rate: Share of runs whose outcome is not ``ok``.
        tool_error_rate: Tool errors divided by the tool calls the service
            answered, i.e. excluding service errors (0.0 with none).
        p95_s: 95th-percentile run duration, in seconds.
        mean_cost: Mean cost per run, in US dollars.
        service_error_rate: Service errors divided by all tool calls (0.0
            with no calls).
    """

    runs: int
    loops: int
    failure_rate: float
    tool_error_rate: float
    p95_s: float
    mean_cost: float
    service_error_rate: float = 0.0

    @classmethod
    def of(cls, records: Sequence[RunRecord]) -> WindowStats:
        """Summarize a sequence of run records.

        Args:
            records: The runs to summarize, in any order.

        Returns:
            The summary; all zeros for an empty sequence.
        """
        runs = len(records)
        if runs == 0:
            return cls(0, 0, 0.0, 0.0, 0.0, 0.0)
        tool_calls = sum(r.tool_calls for r in records)
        service_errors = sum(r.service_errors for r in records)
        # A call the service failed says nothing about the model's input.
        answered = tool_calls - service_errors
        return cls(
            runs=runs,
            loops=sum(r.outcome == "loop" for r in records),
            failure_rate=sum(r.outcome != "ok" for r in records) / runs,
            tool_error_rate=(sum(r.tool_errors for r in records) / answered) if answered else 0.0,
            p95_s=percentile([r.duration_s for r in records], 95),
            mean_cost=sum(r.cost_usd for r in records) / runs,
            service_error_rate=(service_errors / tool_calls) if tool_calls else 0.0,
        )


class RunWindow:
    """Keeps the last :data:`WINDOW_SIZE` run records for each version."""

    def __init__(self, size: int = WINDOW_SIZE) -> None:
        """Create an empty window.

        Args:
            size: Runs kept per version.
        """
        self._runs: defaultdict[str, deque[RunRecord]] = defaultdict(lambda: deque(maxlen=size))

    def add(self, record: RunRecord) -> WindowStats:
        """Append a record to its version's window.

        Args:
            record: The finished run.

        Returns:
            The version's stats including this run.
        """
        self._runs[record.version].append(record)
        return self.stats(record.version)

    def stats(self, version: str) -> WindowStats:
        """Summarize a version's window.

        Args:
            version: Version identifier.

        Returns:
            Its stats; all zeros if it has no runs yet.
        """
        return WindowStats.of(list(self._runs[version]))


@dataclass(frozen=True)
class AlertRule:
    """A threshold on one window metric.

    Attributes:
        name: Rule identifier used in events and span attributes.
        metric: Reads the value to compare from a :class:`WindowStats`.
        threshold: The rule is breached when the value is strictly above this.
        min_runs: Runs the window must hold before the rule can breach.
        blames_version: Whether a breach says the version itself is bad, so
            the rollout acts on it (abort, rollback). False for rules about
            what the agent depends on: those notify, and hold decisions until
            they resolve.
    """

    name: str
    metric: Callable[[WindowStats], float]
    threshold: float
    min_runs: int
    blames_version: bool = True

    def breached(self, stats: WindowStats) -> bool:
        """Whether the rule is breached for these stats.

        Args:
            stats: Window summary to test.

        Returns:
            True when the window is large enough and the value exceeds the threshold.
        """
        return stats.runs >= self.min_runs and self.metric(stats) > self.threshold


RULES: Final[tuple[AlertRule, ...]] = (
    AlertRule("loop_detected", lambda s: s.loops, 0, 1),
    AlertRule("failure_rate", lambda s: s.failure_rate, 0.20, 10),
    AlertRule("tool_error_rate", lambda s: s.tool_error_rate, 0.30, 10),
    AlertRule("p95_latency", lambda s: s.p95_s, 30.0, 10),
    AlertRule("cost_per_run", lambda s: s.mean_cost, 0.15, 10),
    AlertRule("service_error_rate", lambda s: s.service_error_rate, 0.20, 10, blames_version=False),
)


@dataclass(frozen=True)
class AlertEvent:
    """One alert transition.

    Attributes:
        timestamp: When it happened, in clock seconds.
        version: Version the rule was evaluated for.
        rule: Rule name.
        state: ``"fired"`` or ``"resolved"``.
        value: Metric value at the transition.
        threshold: The rule's threshold.
        request_id: Request whose run caused the transition.
    """

    timestamp: float
    version: str
    rule: str
    state: str
    value: float
    threshold: float
    request_id: str


AlertSink = Callable[[AlertEvent], None]


class AlertMonitor:
    """Evaluates rules per version and delivers only state changes."""

    def __init__(self, sinks: Sequence[AlertSink] = (), rules: Sequence[AlertRule] = RULES) -> None:
        """Create a monitor with nothing firing.

        Args:
            sinks: Called with every transition, in order.
            rules: Rules to evaluate.
        """
        self._sinks = tuple(sinks)
        self._rules = tuple(rules)
        self._blaming = frozenset(rule.name for rule in rules if rule.blames_version)
        self._firing: set[tuple[str, str]] = set()

    def observe(
        self, version: str, stats: WindowStats, *, request_id: str, timestamp: float
    ) -> list[AlertEvent]:
        """Evaluate every rule for one version and deliver any transitions.

        Args:
            version: Version the stats describe.
            stats: That version's current window stats.
            request_id: Request whose run produced these stats.
            timestamp: Current clock time.

        Returns:
            The transitions, in rule order; empty when nothing changed.
        """
        events = []
        for rule in self._rules:
            key = (version, rule.name)
            breached = rule.breached(stats)
            if breached == (key in self._firing):
                continue
            if breached:
                self._firing.add(key)
            else:
                self._firing.discard(key)
            event = AlertEvent(
                timestamp=timestamp,
                version=version,
                rule=rule.name,
                state="fired" if breached else "resolved",
                value=float(rule.metric(stats)),
                threshold=rule.threshold,
                request_id=request_id,
            )
            events.append(event)
            for sink in self._sinks:
                sink(event)
        return events

    def firing(self, version: str) -> set[str]:
        """Names of the rules currently firing for a version.

        Args:
            version: Version identifier.

        Returns:
            Rule names; empty when none are firing.
        """
        return {rule for v, rule in self._firing if v == version}

    def blaming(self, version: str) -> set[str]:
        """Names of the rules firing for a version that blame the version itself.

        Args:
            version: Version identifier.

        Returns:
            The subset of :meth:`firing` whose rules have ``blames_version``.
        """
        return self.firing(version) & self._blaming


def log_sink(event: AlertEvent) -> None:
    """Log an alert transition at WARNING.

    Args:
        event: The transition.
    """
    _logger.warning(
        "alert %s: %s for %s (value %.4g, threshold %.4g, request %s)",
        event.state,
        event.rule,
        event.version,
        event.value,
        event.threshold,
        event.request_id,
    )


def jsonl_sink(path: Path) -> AlertSink:
    """Build a sink that appends each transition to a JSON Lines file.

    Args:
        path: File to append to; its parent directory is created if missing.

    Returns:
        The sink.
    """

    def sink(event: AlertEvent) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(asdict(event)) + "\n")

    return sink
