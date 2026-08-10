"""Load security events into the shape the Tier 3 detectors expect.

Two sources, one output type:

* **Replay** -- :func:`load_events` reads a JSON Lines stream off disk. This
  is what you have during an investigation: somebody exported yesterday's
  gateway log and wants to know what a detector would have said about it.
* **Live** -- :func:`events_from_audit_log` reads a running
  :class:`~cockpit.security.audit_logging.AuditLog`, and
  :func:`events_from_chain_file` reads one back from its JSON Lines sink.
  Both verify the hash chain first and refuse to analyze a broken one: a
  detector run over records an attacker was able to edit produces findings
  that say whatever the attacker wanted them to say.

:class:`StreamEvent` structurally satisfies
:class:`cockpit.security.threat_detection.SecurityEvent`, so nothing here
needs to subclass or register anything. That protocol is the entire coupling
between this project and the detectors.

Malformed input raises :class:`EventFormatError` naming the file and the line
number. A stack trace pointing at ``json.loads`` tells the operator nothing;
"events.jsonl line 88 is missing 'actor_id'" tells them where to look.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cockpit.security.audit_logging import (
    AuditEvent,
    AuditLog,
    AuditRecord,
    load_chain,
    verify_chain,
)

# The bundled stream lives beside this package, not in the CWD, so
# `--dry-run` works from any directory.
DEFAULT_EVENTS_PATH = Path(__file__).resolve().parents[1] / "data" / "events.jsonl"

# Fields every line must carry. `metadata` is optional -- plenty of real
# event sources emit nothing but the five-tuple.
REQUIRED_FIELDS: tuple[str, ...] = ("timestamp", "actor_id", "action", "resource", "outcome")

# What the bundled stream is *supposed* to contain. Documented here rather
# than only in the tests because it is the fixture's contract: if a detector
# change starts flagging an actor on this list, that is a false positive and
# the test suite should say so in those words.
DEMO_BENIGN_ACTORS: tuple[str, ...] = ("alice@corp", "bob@corp", "svc-batch")
DEMO_SUSPECT_ACTORS: tuple[str, ...] = (
    "carol@corp",
    "dana@corp",
    "mallory@corp",
    "svc-scraper",
)


class EventFormatError(ValueError):
    """Raised when an event stream cannot be parsed into events."""


class ChainIntegrityError(RuntimeError):
    """Raised when an audit hash chain fails verification before analysis."""


@dataclass(frozen=True)
class StreamEvent:
    """One security event, in the five fields the detectors read.

    Attributes:
        timestamp: When the event occurred, normalized to aware UTC.
        actor_id: Identifier of the principal responsible.
        action: The action attempted, e.g. "login" or "export".
        resource: Identifier of the affected resource.
        outcome: Result of the action, e.g. "success" or "denied".
        metadata: Optional structured context carried through from the
            source. The detectors ignore it; the report prints it, because
            an analyst reading evidence usually wants the source address.
    """

    timestamp: datetime
    actor_id: str
    action: str
    resource: str
    outcome: str
    metadata: Mapping[str, Any] = field(default_factory=dict)


def _to_utc(moment: datetime) -> datetime:
    """Normalize a datetime to an aware UTC datetime.

    Naive values are read as UTC rather than local time, matching how
    :mod:`cockpit.security.threat_detection` and
    :mod:`cockpit.security.audit_logging` treat them. Reading them as local
    time would silently shift events in and out of detection windows
    depending on which host ran the monitor.

    Args:
        moment: The datetime to normalize.

    Returns:
        The equivalent timezone-aware UTC datetime.
    """
    if moment.tzinfo is None:
        return moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC)


def parse_timestamp(raw: str) -> datetime:
    """Parse an ISO-8601 timestamp into an aware UTC datetime.

    Args:
        raw: An ISO-8601 string. A trailing "Z" is accepted.

    Returns:
        The parsed instant in UTC.

    Raises:
        ValueError: If ``raw`` is not a valid ISO-8601 datetime.
    """
    return _to_utc(datetime.fromisoformat(raw.strip()))


def parse_event(payload: Any, *, source: str, line_number: int) -> StreamEvent:
    """Build one :class:`StreamEvent` from a decoded JSON object.

    Args:
        payload: The decoded JSON value for one line.
        source: Name of the stream, used in error messages.
        line_number: 1-based line number, used in error messages.

    Returns:
        The parsed event.

    Raises:
        EventFormatError: If the payload is not an object, is missing a
            required field, or has an unparseable timestamp.
    """
    if not isinstance(payload, dict):
        raise EventFormatError(
            f"{source} line {line_number}: expected a JSON object, got {type(payload).__name__}."
        )
    missing = [name for name in REQUIRED_FIELDS if name not in payload]
    if missing:
        raise EventFormatError(
            f"{source} line {line_number}: missing required field(s) {', '.join(missing)}."
        )
    try:
        when = parse_timestamp(str(payload["timestamp"]))
    except ValueError as exc:
        raise EventFormatError(
            f"{source} line {line_number}: timestamp {payload['timestamp']!r} "
            "is not a valid ISO-8601 datetime."
        ) from exc

    metadata = payload.get("metadata") or {}
    if not isinstance(metadata, dict):
        raise EventFormatError(
            f"{source} line {line_number}: 'metadata' must be an object, "
            f"got {type(metadata).__name__}."
        )

    return StreamEvent(
        timestamp=when,
        actor_id=str(payload["actor_id"]),
        action=str(payload["action"]),
        resource=str(payload["resource"]),
        outcome=str(payload["outcome"]),
        metadata=dict(metadata),
    )


def parse_stream(lines: Iterable[str], *, source: str = "<stream>") -> list[StreamEvent]:
    """Parse JSON Lines text into events, oldest first.

    Args:
        lines: The raw lines. Blank lines are skipped.
        source: Name of the stream, used in error messages.

    Returns:
        The parsed events sorted by timestamp.

    Raises:
        EventFormatError: If any non-blank line is not a well-formed event.
    """
    events: list[StreamEvent] = []
    for line_number, raw in enumerate(lines, start=1):
        line = raw.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            raise EventFormatError(
                f"{source} line {line_number}: not valid JSON ({exc.msg})."
            ) from exc
        events.append(parse_event(payload, source=source, line_number=line_number))
    events.sort(key=lambda event: event.timestamp)
    return events


def load_events(
    path: Path | str = DEFAULT_EVENTS_PATH,
    *,
    since: datetime | None = None,
    until: datetime | None = None,
) -> list[StreamEvent]:
    """Replay a saved JSON Lines event stream from disk.

    Args:
        path: The ``.jsonl`` file to read.
        since: Drop events strictly before this instant, if given.
        until: Drop events strictly after this instant, if given.

    Returns:
        The events, oldest first.

    Raises:
        FileNotFoundError: If ``path`` does not exist.
        EventFormatError: If any line is not a well-formed event.
    """
    file_path = Path(path)
    if not file_path.exists():
        raise FileNotFoundError(f"Event stream not found: {file_path}")
    text = file_path.read_text(encoding="utf-8")
    events = parse_stream(text.splitlines(), source=file_path.name)
    return filter_window(events, since=since, until=until)


def filter_window(
    events: Sequence[StreamEvent],
    *,
    since: datetime | None = None,
    until: datetime | None = None,
) -> list[StreamEvent]:
    """Trim a stream to an inclusive time range.

    This trims the stream the detectors see, which also trims the history
    :func:`~cockpit.security.threat_detection.detect_anomalous_rate` builds
    its per-actor baseline from. That is deliberate -- it models a retention
    bound, not a display filter -- but it means a narrow ``since`` can make
    the rate detector go quiet for want of a baseline.

    Args:
        events: Events to trim.
        since: Lower bound, inclusive. None for unbounded.
        until: Upper bound, inclusive. None for unbounded.

    Returns:
        The surviving events, oldest first.
    """
    lower = _to_utc(since) if since is not None else None
    upper = _to_utc(until) if until is not None else None
    kept = [
        event
        for event in events
        if (lower is None or event.timestamp >= lower)
        and (upper is None or event.timestamp <= upper)
    ]
    kept.sort(key=lambda event: event.timestamp)
    return kept


def event_from_audit_event(event: AuditEvent) -> StreamEvent:
    """Adapt one :class:`~cockpit.security.audit_logging.AuditEvent`.

    Args:
        event: The audit event to adapt.

    Returns:
        The equivalent :class:`StreamEvent`.
    """
    return StreamEvent(
        timestamp=_to_utc(event.timestamp),
        actor_id=event.actor_id,
        action=event.action,
        resource=event.resource,
        outcome=event.outcome,
        metadata=dict(event.metadata),
    )


def events_from_audit_log(
    log: AuditLog,
    *,
    since: datetime | None = None,
    until: datetime | None = None,
    require_intact_chain: bool = True,
) -> list[StreamEvent]:
    """Read a live audit log, verifying its hash chain first.

    Args:
        log: The audit log to read.
        since: Drop events strictly before this instant, if given.
        until: Drop events strictly after this instant, if given.
        require_intact_chain: Refuse to return events when verification
            fails. Set False only to triage a chain you already know is
            broken, and treat the findings as untrusted.

    Returns:
        The recorded events, oldest first.

    Raises:
        ChainIntegrityError: If verification fails and
            ``require_intact_chain`` is set.
    """
    verification = log.verify()
    if require_intact_chain and not verification.is_valid:
        raise ChainIntegrityError(
            "Refusing to analyze a tampered audit chain: "
            f"{verification.reason} (checked {verification.records_checked} record(s))."
        )
    events = [event_from_audit_event(record.event) for record in log.records]
    return filter_window(events, since=since, until=until)


def events_from_chain_file(
    path: Path | str,
    *,
    since: datetime | None = None,
    until: datetime | None = None,
    require_intact_chain: bool = True,
) -> list[StreamEvent]:
    """Read an audit chain back from its JSON Lines sink and verify it.

    Args:
        path: File written by an :class:`AuditLog` with a ``sink_path``.
        since: Drop events strictly before this instant, if given.
        until: Drop events strictly after this instant, if given.
        require_intact_chain: Refuse to return events when verification
            fails.

    Returns:
        The recorded events, oldest first. A missing file yields an empty
        list, matching :func:`~cockpit.security.audit_logging.load_chain`.

    Raises:
        ChainIntegrityError: If verification fails and
            ``require_intact_chain`` is set.
        EventFormatError: If a line is not a serialized audit record.
    """
    file_path = Path(path)
    try:
        records: list[AuditRecord] = load_chain(file_path)
    except (json.JSONDecodeError, KeyError, ValueError) as exc:
        raise EventFormatError(f"{file_path.name} is not a serialized audit chain: {exc}") from exc

    verification = verify_chain(records)
    if require_intact_chain and not verification.is_valid:
        raise ChainIntegrityError(
            f"Refusing to analyze a tampered audit chain in {file_path.name}: "
            f"{verification.reason}"
        )
    events = [event_from_audit_event(record.event) for record in records]
    return filter_window(events, since=since, until=until)


def latest_timestamp(events: Sequence[StreamEvent]) -> datetime | None:
    """Return the newest timestamp in a stream.

    Replaying a saved stream against the wall clock finds nothing, because
    every event is already outside every window. Anchoring the evaluation
    time to the stream's own newest event is what makes a replay reproduce
    what the monitor would have said at the time.

    Args:
        events: The stream to inspect.

    Returns:
        The newest timestamp, or None if the stream is empty.
    """
    if not events:
        return None
    return max(event.timestamp for event in events)


def distinct_actors(events: Sequence[StreamEvent]) -> tuple[str, ...]:
    """List the actors appearing in a stream, sorted.

    Args:
        events: The stream to inspect.

    Returns:
        The distinct actor ids in sorted order.
    """
    return tuple(sorted({event.actor_id for event in events}))


__all__ = [
    "DEFAULT_EVENTS_PATH",
    "DEMO_BENIGN_ACTORS",
    "DEMO_SUSPECT_ACTORS",
    "REQUIRED_FIELDS",
    "ChainIntegrityError",
    "EventFormatError",
    "StreamEvent",
    "distinct_actors",
    "event_from_audit_event",
    "events_from_audit_log",
    "events_from_chain_file",
    "filter_window",
    "latest_timestamp",
    "load_events",
    "parse_event",
    "parse_stream",
    "parse_timestamp",
]
