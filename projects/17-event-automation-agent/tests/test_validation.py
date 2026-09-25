"""Behavioural tests for payload validation limits."""

from __future__ import annotations

import pytest

from integrations import (
    MAX_ARRAY_LENGTH,
    MAX_NESTING_DEPTH,
    MAX_PAYLOAD_BYTES,
    MAX_STRING_LENGTH,
    PayloadValidationError,
    validate_payload,
)


def test_small_payload_passes():
    validate_payload({"observable": "C:\\malware.exe", "confidence": 0.98})


def test_empty_payload_passes():
    validate_payload({})


def test_oversized_payload_rejected():
    with pytest.raises(PayloadValidationError):
        validate_payload({"blob": "x" * (MAX_PAYLOAD_BYTES + 1)})


def test_string_within_limit_passes():
    validate_payload({"note": "x" * MAX_STRING_LENGTH})


def test_string_over_limit_rejected():
    with pytest.raises(PayloadValidationError):
        validate_payload({"note": "x" * (MAX_STRING_LENGTH + 1)})


def test_array_within_limit_passes():
    validate_payload({"items": list(range(MAX_ARRAY_LENGTH))})


def test_array_over_limit_rejected():
    with pytest.raises(PayloadValidationError):
        validate_payload({"items": list(range(MAX_ARRAY_LENGTH + 1))})


def test_nesting_within_limit_passes():
    value = "leaf"
    for _ in range(MAX_NESTING_DEPTH - 1):
        value = {"nested": value}
    validate_payload({"top": value})


def test_nesting_past_limit_rejected():
    value = "leaf"
    for _ in range(MAX_NESTING_DEPTH + 5):
        value = {"nested": value}
    with pytest.raises(PayloadValidationError):
        validate_payload({"top": value})


def test_oversized_string_inside_nested_structure_rejected():
    with pytest.raises(PayloadValidationError):
        validate_payload({"level1": {"level2": ["x" * (MAX_STRING_LENGTH + 1)]}})
