"""Tamper-evident audit logging built on a SHA-256 hash chain.

The requirement this module answers is usually written as a "tamper-proof
audit trail". Nothing that lives in the same process as the code it audits
is tamper-proof: anything able to append to the chain is also able to
rewrite it end to end and recompute every hash. What *is* achievable in
process is **tamper evidence** -- each entry commits to the hash of the one
before it, so editing, reordering, or deleting any past entry invalidates
every link from that point on and :func:`verify_chain` says exactly where.

Making that evidence hard to forge needs something the attacker does not
control: shipping entries to append-only remote storage, periodically
publishing the head hash somewhere external, or signing entries with a key
held off the box. Those are deployment concerns and deliberately out of
scope here; this module provides the primitive they would build on.

Layering, mirroring :mod:`cockpit.monitoring.cost_tracking`:

* :func:`canonical_payload` / :func:`compute_entry_hash` / :func:`verify_chain`
  are pure functions with no state and no feature-flag check, so integrity
  can always be verified even with the security framework switched off.
* :class:`AuditLog` owns a chain and is thread-safe.
* Module-level :func:`record_event` / :func:`query_events` /
  :func:`verify_log_integrity` delegate to a shared default log, and only
  :func:`record_event` -- the top-level recording entry point -- is gated on
  ``feature_flags.is_enabled("security")``.

Event detail is passed through :mod:`cockpit.security.output_security` before
it is stored. An audit log that faithfully captures a leaked password or a
customer's SSN is a liability, not a control.
"""

from __future__ import annotations

import hashlib
import json
import threading
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cockpit.config.feature_flags import is_enabled
from cockpit.security.output_security import mask_pii, scan_for_pii
from cockpit.utils.logging_config import get_logger

_logger = get_logger(__name__)

# The `previous_hash` of the first entry in a chain. A fixed, well-known
# value rather than an empty string so that "no predecessor" is itself a
# committed-to fact and a chain cannot be silently beheaded.
GENESIS_HASH = "0" * 64

# Fields on an event that are identifying by design and must survive into
# the record. Masking these would defeat the point of the log: "who did
# what to which resource" is the answer an audit is for, even when the
# actor id happens to look like an email address.
_UNMASKED_FIELDS = ("actor_id", "action", "resource", "outcome")


def _to_utc(moment: datetime) -> datetime:
    """Normalize a datetime to an aware UTC datetime.

    A naive datetime is *assumed* to be UTC rather than local time.
    Interpreting it as local time would make the same logical event hash
    differently on two machines, which would break verification for reasons
    that have nothing to do with tampering.

    Args:
        moment: The datetime to normalize.

    Returns:
        The equivalent timezone-aware datetime in UTC.
    """
    if moment.tzinfo is None:
        return moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC)


def _sanitize_detail(value: Any) -> Any:
    """Recursively mask PII in a structured detail value.

    Walks dicts, lists, and tuples so PII nested inside event metadata is
    reached, and rebuilds every container. Rebuilding matters for more than
    masking: the caller keeps no reference into what the chain hashed, so a
    later mutation of their own dict cannot silently invalidate an entry
    and raise a false tamper alarm.

    Args:
        value: Any JSON-shaped detail value.

    Returns:
        A copy of ``value`` with detected PII replaced by redaction tokens.
    """
    if isinstance(value, str):
        return mask_pii(value)
    if isinstance(value, dict):
        # Keys are masked too -- a dict keyed by customer email leaks just
        # as much as one that stores it in the value.
        return {mask_pii(str(key)): _sanitize_detail(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_sanitize_detail(item) for item in value]
    return value


def _detail_has_pii(value: Any) -> bool:
    """Report whether any string inside a structured detail value holds PII.

    Args:
        value: Any JSON-shaped detail value.

    Returns:
        True if :func:`~cockpit.security.output_security.scan_for_pii` fires
        on any string found in the structure, including dict keys.
    """
    if isinstance(value, str):
        return scan_for_pii(value).has_pii
    if isinstance(value, dict):
        return any(
            scan_for_pii(str(key)).has_pii or _detail_has_pii(item) for key, item in value.items()
        )
    if isinstance(value, list | tuple):
        return any(_detail_has_pii(item) for item in value)
    return False


@dataclass(frozen=True)
class AuditEvent:
    """A single immutable audit log entry.

    Attributes:
        event_id: Unique identifier for this event.
        timestamp: When the event occurred (UTC).
        actor_id: Identifier of the principal that triggered the event.
        action: The action performed (e.g. "read", "delete", "login").
        resource: Identifier of the affected resource.
        outcome: Result of the action (e.g. "success", "denied", "error").
        metadata: Additional structured context about the event. Sanitized
            through :mod:`cockpit.security.output_security` before storage,
            so it is safe to hand a raw request body here.
    """

    event_id: str
    timestamp: datetime
    actor_id: str
    action: str
    resource: str
    outcome: str
    metadata: dict[str, Any]


@dataclass(frozen=True)
class AuditRecord:
    """One link in the hash chain: an event plus its integrity fields.

    Attributes:
        sequence: Zero-based position of this record in its chain.
        event: The audit event this record commits to.
        previous_hash: ``entry_hash`` of the preceding record, or
            :data:`GENESIS_HASH` for the first record.
        entry_hash: SHA-256 over the canonical serialization of
            ``sequence``, ``previous_hash``, and ``event``.
    """

    sequence: int
    event: AuditEvent
    previous_hash: str
    entry_hash: str


@dataclass(frozen=True)
class ChainVerification:
    """Outcome of verifying a hash chain.

    A bare boolean is not enough to act on. Someone woken by a tamper alert
    needs to know *which* record broke and *why* before they can tell an
    edited record apart from a deleted one or a truncated file.

    Attributes:
        is_valid: True if every link verified.
        records_checked: How many records were inspected before stopping.
            On a valid chain this is the full length.
        broken_index: Position in the supplied sequence of the first record
            that failed, or None if the chain is intact.
        broken_sequence: The failing record's own ``sequence`` value, which
            differs from ``broken_index`` when records were removed.
        reason: Human-readable description of the first failure, or None.
    """

    is_valid: bool
    records_checked: int
    broken_index: int | None = None
    broken_sequence: int | None = None
    reason: str | None = None


def canonical_payload(event: AuditEvent, sequence: int, previous_hash: str) -> str:
    """Serialize a record's hashable content to a canonical JSON string.

    Canonicalization is what makes the chain verifiable at all. The same
    logical event must produce byte-identical output every time, so:

    * ``sort_keys=True`` -- Python preserves dict insertion order, so
      ``{"a": 1, "b": 2}`` and ``{"b": 2, "a": 1}`` are equal dicts that
      would otherwise serialize to different bytes and hash differently.
      A hash that depends on how the caller happened to build their
      metadata dict would make verification randomly flaky, and every
      flaky integrity alarm trains someone to ignore the real one.
    * ``separators=(",", ":")`` -- no incidental whitespace.
    * ``ensure_ascii=True`` -- non-ASCII is escaped, so the payload's bytes
      do not depend on the encoding of whatever wrote or read the file.
    * ``default=str`` -- a value the caller passed that JSON cannot encode
      still hashes deterministically instead of raising mid-append.

    Args:
        event: The event to serialize.
        sequence: The record's position in the chain.
        previous_hash: The preceding record's ``entry_hash``.

    Returns:
        The canonical JSON string that :func:`compute_entry_hash` digests.
    """
    payload: dict[str, Any] = {
        "sequence": sequence,
        "previous_hash": previous_hash,
        "event_id": event.event_id,
        "timestamp": _to_utc(event.timestamp).isoformat(),
        "actor_id": event.actor_id,
        "action": event.action,
        "resource": event.resource,
        "outcome": event.outcome,
        "metadata": event.metadata,
    }
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=str,
    )


def compute_entry_hash(event: AuditEvent, sequence: int, previous_hash: str) -> str:
    """Compute the SHA-256 entry hash for a record.

    Args:
        event: The event to hash.
        sequence: The record's position in the chain.
        previous_hash: The preceding record's ``entry_hash``.

    Returns:
        Lowercase hex SHA-256 digest of the canonical payload.
    """
    payload = canonical_payload(event, sequence, previous_hash)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def verify_chain(records: Sequence[AuditRecord]) -> ChainVerification:
    """Verify a hash chain and locate the first broken link.

    Three things are checked per record, in the order that best explains
    what happened:

    1. ``sequence`` matches the record's position -- catches a deletion.
    2. ``previous_hash`` matches the predecessor's ``entry_hash`` --
       catches reordering, splicing, or a beheaded chain.
    3. ``entry_hash`` recomputes from the record's own content -- catches
       an edited event.

    Verification stops at the first failure, which is the informative one.
    An attacker who edits a record *and* recomputes its hash moves the
    break to the following record, whose ``previous_hash`` no longer
    matches; rewriting the whole tail from that point is exactly what
    off-box anchoring of the head hash is there to prevent.

    Not flag-gated: integrity checking must work even when the security
    framework is switched off, since that is precisely when someone is
    likely to be investigating.

    Args:
        records: The chain to verify, in order. An empty chain is valid.

    Returns:
        A :class:`ChainVerification` describing the result and, on failure,
        where and why the chain broke.
    """
    expected_previous = GENESIS_HASH
    for index, record in enumerate(records):
        if record.sequence != index:
            return ChainVerification(
                is_valid=False,
                records_checked=index + 1,
                broken_index=index,
                broken_sequence=record.sequence,
                reason=(
                    f"Sequence gap: record at position {index} claims sequence "
                    f"{record.sequence}; a record was removed or reordered."
                ),
            )
        if record.previous_hash != expected_previous:
            return ChainVerification(
                is_valid=False,
                records_checked=index + 1,
                broken_index=index,
                broken_sequence=record.sequence,
                reason=(
                    f"Broken link at index {index}: previous_hash "
                    f"{record.previous_hash[:12]}... does not match the preceding "
                    f"entry hash {expected_previous[:12]}..."
                ),
            )
        recomputed = compute_entry_hash(record.event, record.sequence, record.previous_hash)
        if recomputed != record.entry_hash:
            return ChainVerification(
                is_valid=False,
                records_checked=index + 1,
                broken_index=index,
                broken_sequence=record.sequence,
                reason=(
                    f"Content modified at index {index}: recomputed hash "
                    f"{recomputed[:12]}... does not match stored entry hash "
                    f"{record.entry_hash[:12]}..."
                ),
            )
        expected_previous = record.entry_hash

    return ChainVerification(is_valid=True, records_checked=len(records))


def record_to_dict(record: AuditRecord) -> dict[str, Any]:
    """Convert a record to a JSON-serializable dict for durable storage.

    Args:
        record: The record to convert.

    Returns:
        A plain dict mirroring the record, with the timestamp as an ISO-8601
        UTC string.
    """
    return {
        "sequence": record.sequence,
        "previous_hash": record.previous_hash,
        "entry_hash": record.entry_hash,
        "event": {
            "event_id": record.event.event_id,
            "timestamp": _to_utc(record.event.timestamp).isoformat(),
            "actor_id": record.event.actor_id,
            "action": record.event.action,
            "resource": record.event.resource,
            "outcome": record.event.outcome,
            "metadata": record.event.metadata,
        },
    }


def record_from_dict(payload: dict[str, Any]) -> AuditRecord:
    """Rebuild a record from its serialized form.

    Args:
        payload: A dict produced by :func:`record_to_dict`.

    Returns:
        The reconstructed :class:`AuditRecord`.

    Raises:
        KeyError: If a required field is missing.
        ValueError: If the timestamp is not a valid ISO-8601 string.
    """
    raw_event = payload["event"]
    event = AuditEvent(
        event_id=raw_event["event_id"],
        timestamp=_to_utc(datetime.fromisoformat(raw_event["timestamp"])),
        actor_id=raw_event["actor_id"],
        action=raw_event["action"],
        resource=raw_event["resource"],
        outcome=raw_event["outcome"],
        metadata=raw_event.get("metadata") or {},
    )
    return AuditRecord(
        sequence=payload["sequence"],
        event=event,
        previous_hash=payload["previous_hash"],
        entry_hash=payload["entry_hash"],
    )


def load_chain(path: Path | str) -> list[AuditRecord]:
    """Read a JSON-lines audit chain back from disk.

    Args:
        path: File written by an :class:`AuditLog` with a ``sink_path``.

    Returns:
        The records in file order, ready to hand to :func:`verify_chain`.
        A missing file yields an empty list, since "no log yet" is not a
        corruption.

    Raises:
        json.JSONDecodeError: If a line is not valid JSON.
        KeyError: If a line is valid JSON but not a serialized record.
    """
    file_path = Path(path)
    if not file_path.exists():
        return []
    records: list[AuditRecord] = []
    with file_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            stripped = line.strip()
            if stripped:
                records.append(record_from_dict(json.loads(stripped)))
    return records


@dataclass
class AuditLog:
    """An in-memory, thread-safe, tamper-evident chain of audit records.

    The in-memory chain is the primary API: it needs no filesystem, so
    tests and callers that only want integrity semantics pay nothing for
    durability. Setting ``sink_path`` additionally appends each record to a
    JSON-lines file as it is written.

    Thread-safe because audit events arrive from whatever thread happened
    to serve the request, and a hash chain is order-dependent -- two
    unsynchronized appends would both read the same head hash and produce
    a chain that fails its own verification.

    Attributes:
        records: The chain, oldest first.
        sink_path: Optional file to append serialized records to.
    """

    records: list[AuditRecord] = field(default_factory=list)
    sink_path: Path | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

    @property
    def head_hash(self) -> str:
        """Hash of the most recent record, or :data:`GENESIS_HASH` if empty.

        Returns:
            The current head hash. Publishing this value somewhere the
            application cannot rewrite is what upgrades the chain from
            "detects careless edits" to "detects a determined rewrite".
        """
        with self._lock:
            return self.records[-1].entry_hash if self.records else GENESIS_HASH

    def record(
        self,
        actor_id: str,
        action: str,
        resource: str,
        outcome: str,
        metadata: dict[str, Any] | None = None,
        *,
        timestamp: datetime | None = None,
        event_id: str | None = None,
    ) -> AuditRecord:
        """Append one event to the chain.

        Args:
            actor_id: Identifier of the principal performing the action.
            action: The action performed.
            resource: Identifier of the affected resource.
            outcome: Result of the action.
            metadata: Optional structured context. Scanned and masked for
                PII before it is hashed or stored.
            timestamp: Event time; defaults to now (UTC). Injectable so
                tests do not have to freeze the clock.
            event_id: Explicit event id; defaults to a fresh UUID4 hex.

        Returns:
            The appended :class:`AuditRecord`.

        Raises:
            OSError: If ``sink_path`` is set and the append fails.
        """
        detail = metadata or {}
        if _detail_has_pii(detail):
            _logger.warning(
                "PII detected in audit metadata for action=%s resource=%s; masking before storage.",
                action,
                resource,
            )
        safe_detail = _sanitize_detail(detail)

        event = AuditEvent(
            event_id=event_id or uuid.uuid4().hex,
            timestamp=_to_utc(timestamp) if timestamp else datetime.now(UTC),
            actor_id=actor_id,
            action=action,
            resource=resource,
            outcome=outcome,
            metadata=safe_detail,
        )

        # Read the head, build the link, and append under one lock: any gap
        # between reading the head hash and appending lets a second thread
        # fork the chain.
        with self._lock:
            sequence = len(self.records)
            previous_hash = self.records[-1].entry_hash if self.records else GENESIS_HASH
            new_record = AuditRecord(
                sequence=sequence,
                event=event,
                previous_hash=previous_hash,
                entry_hash=compute_entry_hash(event, sequence, previous_hash),
            )
            self.records.append(new_record)
            if self.sink_path is not None:
                self._append_to_sink(new_record)
        return new_record

    def _append_to_sink(self, record: AuditRecord) -> None:
        """Append one serialized record to the durable sink.

        Called with the lock held so file order matches chain order.

        Args:
            record: The record to persist.

        Raises:
            OSError: If the file cannot be opened or written.
        """
        if self.sink_path is None:
            return
        self.sink_path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(record_to_dict(record), sort_keys=True, ensure_ascii=True, default=str)
        with self.sink_path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")

    def verify(self) -> ChainVerification:
        """Verify this log's chain.

        Returns:
            A :class:`ChainVerification` for the current chain.
        """
        with self._lock:
            snapshot = list(self.records)
        return verify_chain(snapshot)

    def events(self) -> list[AuditEvent]:
        """Return every recorded event, oldest first.

        Returns:
            The events in chain order.
        """
        with self._lock:
            return [record.event for record in self.records]

    def find_by_actor(self, actor_id: str) -> list[AuditEvent]:
        """Return events recorded for one actor.

        Args:
            actor_id: The actor to filter on (exact match).

        Returns:
            Matching events in chain order.
        """
        return self.query(actor_id=actor_id)

    def find_by_action(self, action: str) -> list[AuditEvent]:
        """Return events for one action.

        Args:
            action: The action to filter on (exact match).

        Returns:
            Matching events in chain order.
        """
        return self.query(action=action)

    def find_in_range(self, start: datetime | None, end: datetime | None) -> list[AuditEvent]:
        """Return events within an inclusive time range.

        Args:
            start: Lower bound, or None for unbounded.
            end: Upper bound, or None for unbounded.

        Returns:
            Matching events in chain order.
        """
        return self.query(start_time=start, end_time=end)

    def query(
        self,
        actor_id: str | None = None,
        action: str | None = None,
        resource: str | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> list[AuditEvent]:
        """Filter recorded events on any combination of fields.

        Args:
            actor_id: Restrict to this actor, if provided.
            action: Restrict to this action, if provided.
            resource: Restrict to this resource, if provided.
            start_time: Only events at or after this time, if provided.
            end_time: Only events at or before this time, if provided.

        Returns:
            Matching events in chain order (which is timestamp order for a
            log written by :meth:`record`).
        """
        lower = _to_utc(start_time) if start_time else None
        upper = _to_utc(end_time) if end_time else None

        matches: list[AuditEvent] = []
        for event in self.events():
            when = _to_utc(event.timestamp)
            if actor_id is not None and event.actor_id != actor_id:
                continue
            if action is not None and event.action != action:
                continue
            if resource is not None and event.resource != resource:
                continue
            if lower is not None and when < lower:
                continue
            if upper is not None and when > upper:
                continue
            matches.append(event)
        return matches

    def reset(self) -> None:
        """Drop every record, restarting the chain from genesis.

        Intended for tests and for a caller that has already archived the
        chain elsewhere. It is not an audit operation: discarding history
        is exactly what the chain exists to make visible.
        """
        with self._lock:
            self.records.clear()


# Shared default log backing the module-level convenience functions, the same
# way `logging` exposes a root logger.
_default_log = AuditLog()


def get_default_log() -> AuditLog:
    """Return the process-wide default :class:`AuditLog`.

    Returns:
        The log backing the module-level convenience functions. Tests
        should prefer constructing their own :class:`AuditLog` over
        mutating this one.
    """
    return _default_log


def record_event(
    actor_id: str,
    action: str,
    resource: str,
    outcome: str,
    metadata: dict[str, Any] | None = None,
) -> AuditEvent | None:
    """Append a new audit event to the default tamper-evident log.

    The single flag-gated entry point in this module, mirroring
    :func:`cockpit.security.input_security.validate_input`. Hashing and
    verification helpers stay available regardless of the flag.

    Args:
        actor_id: Identifier of the principal performing the action.
        action: The action performed.
        resource: Identifier of the affected resource.
        outcome: Result of the action.
        metadata: Optional additional structured context. Masked for PII
            before storage.

    Returns:
        The recorded :class:`AuditEvent`, including its assigned
        ``event_id`` and ``timestamp``, or None if the security framework
        is disabled (in which case nothing is recorded).
    """
    if not is_enabled("security"):
        _logger.debug("Security framework disabled; skipping audit record.")
        return None
    return _default_log.record(actor_id, action, resource, outcome, metadata).event


def query_events(
    actor_id: str | None = None,
    resource: str | None = None,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
) -> list[AuditEvent]:
    """Query events recorded on the default log.

    Args:
        actor_id: Restrict results to this actor, if provided.
        resource: Restrict results to this resource, if provided.
        start_time: Only include events at or after this time, if provided.
        end_time: Only include events at or before this time, if provided.

    Returns:
        Matching :class:`AuditEvent` records, ordered by timestamp.
    """
    return _default_log.query(
        actor_id=actor_id,
        resource=resource,
        start_time=start_time,
        end_time=end_time,
    )


def verify_log_integrity() -> bool:
    """Verify that the default audit log has not been tampered with.

    Returns:
        True if the hash chain is intact. Use :meth:`AuditLog.verify` (or
        :func:`verify_chain`) instead when you need to know *where* the
        chain broke -- a bare False is not actionable.
    """
    result = _default_log.verify()
    if not result.is_valid:
        _logger.error("Audit chain integrity check failed: %s", result.reason)
    return result.is_valid


__all__ = [
    "GENESIS_HASH",
    "AuditEvent",
    "AuditLog",
    "AuditRecord",
    "ChainVerification",
    "canonical_payload",
    "compute_entry_hash",
    "get_default_log",
    "load_chain",
    "query_events",
    "record_event",
    "record_from_dict",
    "record_to_dict",
    "verify_chain",
    "verify_log_integrity",
]
