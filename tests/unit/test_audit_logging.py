"""Unit tests for cockpit/security/audit_logging.py.

Behavioral tests for the tamper-evident hash chain: a clean chain verifies,
every shape of tampering is detected at the correct index, canonical
serialization is insensitive to dict ordering, and PII never reaches
storage. Timestamps are always injected -- nothing here sleeps.
"""

from __future__ import annotations

import itertools
import json
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from cockpit.config import feature_flags
from cockpit.security import audit_logging
from cockpit.security.audit_logging import (
    GENESIS_HASH,
    AuditEvent,
    AuditLog,
    AuditRecord,
    canonical_payload,
    compute_entry_hash,
    get_default_log,
    load_chain,
    query_events,
    record_event,
    record_from_dict,
    record_to_dict,
    verify_chain,
    verify_log_integrity,
)

FIXED_TIME = datetime(2026, 8, 4, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def security_on(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Force the ``security`` feature flag on for one test.

    Yields:
        None.
    """
    monkeypatch.setitem(feature_flags.FRAMEWORKS_ENABLED, "security", True)
    yield


@pytest.fixture
def security_off(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Force the ``security`` feature flag off for one test.

    Yields:
        None.
    """
    monkeypatch.setitem(feature_flags.FRAMEWORKS_ENABLED, "security", False)
    yield


@pytest.fixture
def clean_default_log() -> Iterator[AuditLog]:
    """Reset the module-level default log before and after a test.

    Yields:
        The default :class:`AuditLog`.
    """
    log = get_default_log()
    log.reset()
    yield log
    log.reset()


def build_log(count: int = 5) -> AuditLog:
    """Build a log with ``count`` deterministic records.

    Args:
        count: How many records to append.

    Returns:
        A populated :class:`AuditLog`.
    """
    log = AuditLog()
    for index in range(count):
        log.record(
            actor_id=f"user-{index % 2}",
            action="read",
            resource=f"doc-{index}",
            outcome="success",
            metadata={"index": index},
            timestamp=FIXED_TIME + timedelta(seconds=index),
        )
    return log


class TestHashChainConstruction:
    """Tests for how the chain is built."""

    def test_first_record_links_to_genesis(self) -> None:
        log = AuditLog()
        record = log.record("alice", "login", "session", "success", timestamp=FIXED_TIME)
        assert record.sequence == 0
        assert record.previous_hash == GENESIS_HASH

    def test_each_record_links_to_its_predecessor(self) -> None:
        log = build_log(4)
        # pairwise, not zip(x, x[1:], strict=True): those slices differ in
        # length by one by construction, so strict=True raises every time.
        for previous, current in itertools.pairwise(log.records):
            assert current.previous_hash == previous.entry_hash
            assert current.sequence == previous.sequence + 1

    def test_head_hash_tracks_last_record(self) -> None:
        log = AuditLog()
        assert log.head_hash == GENESIS_HASH
        record = log.record("alice", "login", "session", "success", timestamp=FIXED_TIME)
        assert log.head_hash == record.entry_hash

    def test_entry_hash_is_a_sha256_hex_digest(self) -> None:
        log = build_log(1)
        entry_hash = log.records[0].entry_hash
        assert len(entry_hash) == 64
        assert all(char in "0123456789abcdef" for char in entry_hash)

    def test_records_are_frozen(self) -> None:
        log = build_log(1)
        with pytest.raises(FrozenInstanceError):
            log.records[0].event.actor_id = "mallory"  # type: ignore[misc]

    def test_naive_timestamps_are_stored_as_utc(self) -> None:
        log = AuditLog()
        record = log.record(
            "alice", "login", "session", "success", timestamp=datetime(2026, 8, 4, 12, 0, 0)
        )
        assert record.event.timestamp.tzinfo is not None
        assert record.event.timestamp == FIXED_TIME

    def test_caller_mutating_their_metadata_cannot_break_the_chain(self) -> None:
        """The log stores a rebuilt copy, so later caller mutation is inert."""
        payload = {"request": {"path": "/orders"}}
        log = AuditLog()
        log.record("alice", "read", "orders", "success", payload, timestamp=FIXED_TIME)

        payload["request"]["path"] = "/tampered"

        assert log.verify().is_valid is True
        assert log.records[0].event.metadata["request"]["path"] == "/orders"


class TestChainVerification:
    """Tests for verify_chain and the break location it reports."""

    def test_clean_chain_verifies(self) -> None:
        log = build_log(6)
        result = log.verify()
        assert result.is_valid is True
        assert result.broken_index is None
        assert result.reason is None
        assert result.records_checked == 6

    def test_empty_chain_verifies_trivially(self) -> None:
        result = verify_chain([])
        assert result.is_valid is True
        assert result.broken_index is None
        assert result.records_checked == 0

    def test_mutating_a_middle_entry_is_detected_at_that_index(self) -> None:
        log = build_log(5)
        tampered_event = replace(log.records[2].event, outcome="denied")
        log.records[2] = replace(log.records[2], event=tampered_event)

        result = log.verify()
        assert result.is_valid is False
        assert result.broken_index == 2
        assert result.broken_sequence == 2
        assert result.reason is not None
        assert "Content modified" in result.reason

    def test_mutating_metadata_only_is_still_detected(self) -> None:
        log = build_log(4)
        tampered_event = replace(log.records[1].event, metadata={"index": 999})
        log.records[1] = replace(log.records[1], event=tampered_event)

        result = log.verify()
        assert result.is_valid is False
        assert result.broken_index == 1

    def test_appending_after_tampering_still_reports_the_original_break(self) -> None:
        log = build_log(5)
        tampered_event = replace(log.records[1].event, actor_id="mallory")
        log.records[1] = replace(log.records[1], event=tampered_event)

        log.record("alice", "read", "doc-9", "success", timestamp=FIXED_TIME + timedelta(hours=1))
        log.record("alice", "read", "doc-10", "success", timestamp=FIXED_TIME + timedelta(hours=2))

        result = log.verify()
        assert result.is_valid is False
        assert result.broken_index == 1

    def test_deleting_a_record_is_detected_as_a_sequence_gap(self) -> None:
        log = build_log(5)
        del log.records[2]

        result = log.verify()
        assert result.is_valid is False
        assert result.broken_index == 2
        assert result.broken_sequence == 3
        assert result.reason is not None
        assert "Sequence gap" in result.reason

    def test_reordering_records_is_detected(self) -> None:
        log = build_log(5)
        log.records[1], log.records[2] = log.records[2], log.records[1]

        result = log.verify()
        assert result.is_valid is False
        assert result.broken_index == 1

    def test_recomputing_the_tampered_hash_moves_the_break_to_the_next_link(self) -> None:
        """A smarter forger who re-hashes the edited entry breaks the *next* one."""
        log = build_log(5)
        tampered_event = replace(log.records[2].event, outcome="denied")
        log.records[2] = replace(
            log.records[2],
            event=tampered_event,
            entry_hash=compute_entry_hash(tampered_event, 2, log.records[2].previous_hash),
        )

        result = log.verify()
        assert result.is_valid is False
        assert result.broken_index == 3
        assert result.reason is not None
        assert "Broken link" in result.reason

    def test_truncating_the_head_still_verifies(self) -> None:
        """Dropping trailing records leaves a valid prefix -- hence head anchoring."""
        log = build_log(5)
        del log.records[3:]
        assert log.verify().is_valid is True


class TestCanonicalSerialization:
    """Tests that hashing does not depend on incidental dict ordering."""

    def test_identical_events_hash_the_same_regardless_of_key_order(self) -> None:
        first = AuditEvent(
            event_id="fixed-id",
            timestamp=FIXED_TIME,
            actor_id="alice",
            action="read",
            resource="doc-1",
            outcome="success",
            metadata={"alpha": 1, "beta": 2, "gamma": {"x": 1, "y": 2}},
        )
        second = replace(first, metadata={"gamma": {"y": 2, "x": 1}, "beta": 2, "alpha": 1})

        # Equal dicts, different insertion order -- the exact case a
        # naive json.dumps would hash differently.
        assert first.metadata == second.metadata
        assert list(first.metadata) != list(second.metadata)
        assert canonical_payload(first, 0, GENESIS_HASH) == canonical_payload(
            second, 0, GENESIS_HASH
        )
        assert compute_entry_hash(first, 0, GENESIS_HASH) == compute_entry_hash(
            second, 0, GENESIS_HASH
        )

    def test_payload_is_pure_ascii_and_compact(self) -> None:
        event = AuditEvent(
            event_id="fixed-id",
            timestamp=FIXED_TIME,
            actor_id="alice",
            action="read",
            # Built from a code point rather than typed literally: this
            # repo's source stays pure ASCII so a cp1252 console cannot
            # break the tooling that reads it.
            resource="caf" + chr(0x00E9) + "-report",
            outcome="success",
            metadata={},
        )
        payload = canonical_payload(event, 0, GENESIS_HASH)
        assert payload.isascii()
        assert ", " not in payload

    def test_differing_content_hashes_differently(self) -> None:
        event = AuditEvent(
            event_id="fixed-id",
            timestamp=FIXED_TIME,
            actor_id="alice",
            action="read",
            resource="doc-1",
            outcome="success",
            metadata={},
        )
        assert compute_entry_hash(event, 0, GENESIS_HASH) != compute_entry_hash(
            replace(event, outcome="denied"), 0, GENESIS_HASH
        )

    def test_same_event_at_a_different_position_hashes_differently(self) -> None:
        event = AuditEvent(
            event_id="fixed-id",
            timestamp=FIXED_TIME,
            actor_id="alice",
            action="read",
            resource="doc-1",
            outcome="success",
            metadata={},
        )
        assert compute_entry_hash(event, 0, GENESIS_HASH) != compute_entry_hash(
            event, 1, GENESIS_HASH
        )

    def test_unserializable_metadata_still_hashes(self) -> None:
        event = AuditEvent(
            event_id="fixed-id",
            timestamp=FIXED_TIME,
            actor_id="alice",
            action="read",
            resource="doc-1",
            outcome="success",
            metadata={"when": FIXED_TIME},
        )
        assert len(compute_entry_hash(event, 0, GENESIS_HASH)) == 64


class TestPiiMasking:
    """Tests that secrets never reach the stored chain."""

    def test_pii_in_a_detail_field_is_masked_before_storage(self) -> None:
        log = AuditLog()
        record = log.record(
            "alice",
            "support_ticket",
            "ticket-1",
            "success",
            {"note": "contact me at alice@example.com"},
            timestamp=FIXED_TIME,
        )
        stored = record.event.metadata["note"]
        assert "alice@example.com" not in stored
        assert "[REDACTED:email]" in stored

    def test_nested_pii_is_masked(self) -> None:
        log = AuditLog()
        record = log.record(
            "alice",
            "read",
            "customer-1",
            "success",
            {"customer": {"ssn": "123-45-6789", "tags": ["ip 192.168.1.1"]}},
            timestamp=FIXED_TIME,
        )
        customer = record.event.metadata["customer"]
        assert "123-45-6789" not in customer["ssn"]
        assert "[REDACTED:ssn]" in customer["ssn"]
        assert "192.168.1.1" not in customer["tags"][0]

    def test_pii_in_a_metadata_key_is_masked(self) -> None:
        log = AuditLog()
        record = log.record(
            "alice",
            "read",
            "doc-1",
            "success",
            {"alice@example.com": "owner"},
            timestamp=FIXED_TIME,
        )
        assert "alice@example.com" not in record.event.metadata
        assert "[REDACTED:email]" in next(iter(record.event.metadata))

    def test_masked_metadata_is_what_the_chain_commits_to(self) -> None:
        log = AuditLog()
        log.record(
            "alice",
            "read",
            "doc-1",
            "success",
            {"note": "ssn 123-45-6789"},
            timestamp=FIXED_TIME,
        )
        assert log.verify().is_valid is True
        assert "123-45-6789" not in canonical_payload(log.records[0].event, 0, GENESIS_HASH)

    def test_actor_and_resource_are_not_masked(self) -> None:
        """Masking the subject of an audit record would defeat its purpose."""
        log = AuditLog()
        record = log.record(
            "alice@example.com", "login", "session", "success", timestamp=FIXED_TIME
        )
        assert record.event.actor_id == "alice@example.com"

    def test_clean_metadata_passes_through_unchanged(self) -> None:
        log = AuditLog()
        record = log.record(
            "alice", "read", "doc-1", "success", {"count": 3, "tag": "ok"}, timestamp=FIXED_TIME
        )
        assert record.event.metadata == {"count": 3, "tag": "ok"}


class TestQueries:
    """Tests for the query helpers."""

    def test_find_by_actor(self) -> None:
        log = build_log(6)
        results = log.find_by_actor("user-0")
        assert len(results) == 3
        assert {event.actor_id for event in results} == {"user-0"}

    def test_find_by_action(self) -> None:
        log = build_log(3)
        log.record("alice", "delete", "doc-0", "success", timestamp=FIXED_TIME)
        assert len(log.find_by_action("delete")) == 1
        assert len(log.find_by_action("read")) == 3

    def test_find_in_range_is_inclusive(self) -> None:
        log = build_log(5)
        results = log.find_in_range(
            FIXED_TIME + timedelta(seconds=1), FIXED_TIME + timedelta(seconds=3)
        )
        assert [event.resource for event in results] == ["doc-1", "doc-2", "doc-3"]

    def test_find_in_range_with_open_bounds(self) -> None:
        log = build_log(4)
        assert len(log.find_in_range(None, None)) == 4
        assert len(log.find_in_range(FIXED_TIME + timedelta(seconds=2), None)) == 2

    def test_query_combines_filters(self) -> None:
        log = build_log(6)
        results = log.query(actor_id="user-1", resource="doc-3")
        assert len(results) == 1
        assert results[0].resource == "doc-3"

    def test_query_returns_empty_for_unknown_actor(self) -> None:
        assert build_log(3).query(actor_id="nobody") == []


class TestDurableSink:
    """Tests for the optional file sink."""

    def test_records_round_trip_through_a_file(self, tmp_path: Path) -> None:
        sink = tmp_path / "audit" / "chain.jsonl"
        log = AuditLog(sink_path=sink)
        for index in range(3):
            log.record(
                "alice",
                "read",
                f"doc-{index}",
                "success",
                {"index": index},
                timestamp=FIXED_TIME + timedelta(seconds=index),
            )

        loaded = load_chain(sink)
        assert len(loaded) == 3
        assert verify_chain(loaded).is_valid is True
        assert [record.entry_hash for record in loaded] == [
            record.entry_hash for record in log.records
        ]

    def test_tampering_with_the_file_is_detected_on_load(self, tmp_path: Path) -> None:
        sink = tmp_path / "chain.jsonl"
        log = AuditLog(sink_path=sink)
        for index in range(4):
            log.record(
                "alice",
                "read",
                f"doc-{index}",
                "success",
                timestamp=FIXED_TIME + timedelta(seconds=index),
            )

        # Tamper by parsing and re-serializing rather than string-replacing.
        # A replace that silently fails to match tampers with nothing, and the
        # test then "passes" against an untouched chain -- which is exactly how
        # this test originally slipped through green while asserting nothing.
        lines = sink.read_text(encoding="utf-8").splitlines()
        entry = json.loads(lines[2])
        assert entry["event"]["outcome"] == "success"
        entry["event"]["outcome"] = "denied"
        lines[2] = json.dumps(entry)
        sink.write_text("\n".join(lines) + "\n", encoding="utf-8")

        assert "denied" in sink.read_text(encoding="utf-8"), "the tamper must actually land"

        result = verify_chain(load_chain(sink))
        assert result.is_valid is False
        assert result.broken_index == 2

    def test_missing_file_loads_as_empty(self, tmp_path: Path) -> None:
        assert load_chain(tmp_path / "absent.jsonl") == []

    def test_record_dict_round_trip(self) -> None:
        log = build_log(1)
        original = log.records[0]
        restored = record_from_dict(record_to_dict(original))
        assert restored == original


class TestModuleLevelApi:
    """Tests for the default-log convenience functions and the feature gate."""

    def test_record_event_appends_to_the_default_log(
        self, security_on: None, clean_default_log: AuditLog
    ) -> None:
        event = record_event("alice", "login", "session", "success", {"ip": "unknown"})
        assert event is not None
        assert event.actor_id == "alice"
        assert len(clean_default_log.records) == 1

    def test_record_event_is_a_no_op_when_security_is_disabled(
        self, security_off: None, clean_default_log: AuditLog
    ) -> None:
        assert record_event("alice", "login", "session", "success") is None
        assert clean_default_log.records == []

    def test_verify_helpers_work_with_security_disabled(
        self, security_off: None, clean_default_log: AuditLog
    ) -> None:
        """Integrity checking must not depend on the flag being on."""
        clean_default_log.record("alice", "login", "session", "success", timestamp=FIXED_TIME)
        assert verify_log_integrity() is True
        assert compute_entry_hash(clean_default_log.records[0].event, 0, GENESIS_HASH)

    def test_verify_log_integrity_reports_tampering(
        self, security_on: None, clean_default_log: AuditLog
    ) -> None:
        clean_default_log.record("alice", "login", "session", "success", timestamp=FIXED_TIME)
        tampered = replace(clean_default_log.records[0].event, outcome="denied")
        clean_default_log.records[0] = replace(clean_default_log.records[0], event=tampered)
        assert verify_log_integrity() is False

    def test_query_events_filters_the_default_log(
        self, security_on: None, clean_default_log: AuditLog
    ) -> None:
        record_event("alice", "login", "session", "success")
        record_event("bob", "login", "session", "denied")
        assert len(query_events(actor_id="alice")) == 1
        assert len(query_events()) == 2


class TestConcurrency:
    """The chain is order-dependent, so concurrent appends must serialize."""

    def test_parallel_records_produce_a_valid_chain(self) -> None:
        log = AuditLog()

        def append(index: int) -> AuditRecord:
            return log.record(
                f"user-{index}",
                "read",
                f"doc-{index}",
                "success",
                timestamp=FIXED_TIME + timedelta(seconds=index),
            )

        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(append, range(64)))

        assert len(log.records) == 64
        result = log.verify()
        assert result.is_valid is True, result.reason


def test_module_exposes_documented_public_names() -> None:
    for name in ("AuditEvent", "AuditLog", "verify_chain", "record_event", "query_events"):
        assert hasattr(audit_logging, name)
