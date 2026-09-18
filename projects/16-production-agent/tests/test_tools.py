"""The simulated backend: fixed data, strict input, ToolError on bad input."""

from __future__ import annotations

import json

import pytest
from anthropic.lib.tools import ToolError

from tools import ORDERS, SAMPLE_QUESTIONS, lookup_order, refund_policy, track_shipment


def test_lookup_order_returns_the_order_as_json() -> None:
    assert json.loads(lookup_order("ORD-10042")) == {
        "order_id": "ORD-10042",
        "item": "Trail running shoes",
        "status": "shipped",
        "tracking_id": "TRK-5501",
        "category": "footwear",
    }


@pytest.mark.parametrize("order_id", ["10042", "ord-10042", "A-10042"])
def test_lookup_order_rejects_ids_not_in_the_ord_form(order_id: str) -> None:
    with pytest.raises(ToolError, match="expected the form ORD-12345"):
        lookup_order(order_id)


def test_lookup_order_rejects_unknown_orders() -> None:
    with pytest.raises(ToolError, match="No order with ID ORD-99999"):
        lookup_order("ORD-99999")


def test_the_stuck_shipment_never_gets_a_new_scan() -> None:
    assert json.loads(track_shipment("TRK-5503"))["status"] == "in transit"


def test_track_shipment_rejects_unknown_ids() -> None:
    with pytest.raises(ToolError, match="No shipment"):
        track_shipment("TRK-0000")


def test_every_order_category_has_a_refund_policy() -> None:
    for order in ORDERS.values():
        assert json.loads(refund_policy(str(order["category"])))["policy"]


def test_refund_policy_rejects_unknown_categories() -> None:
    with pytest.raises(ToolError, match="No refund policy"):
        refund_policy("groceries")


def test_every_sample_question_names_a_known_order() -> None:
    for question in SAMPLE_QUESTIONS:
        assert any(order_id in question for order_id in ORDERS), question
