"""The tools: sample data by default, a support API when configured, ToolError on bad input."""

from __future__ import annotations

import json
from collections.abc import Callable

import httpx2
import pytest
from anthropic.lib.tools import ToolError

import tools
from tools import (
    ORDERS,
    SAMPLE_QUESTIONS,
    SupportAPI,
    lookup_order,
    refund_policy,
    track_shipment,
    use_support_api,
)


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


# --------------------------------------------------------------------------- support API


def _api(handler: Callable[[httpx2.Request], httpx2.Response]) -> list[httpx2.Request]:
    """Point the tools at a fake support API; returns the requests it receives."""
    seen: list[httpx2.Request] = []

    def record(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return handler(request)

    use_support_api(
        SupportAPI(
            "https://support.example.test/v1",
            token="t0ken",  # pragma: allowlist secret
            transport=httpx2.MockTransport(record),
        )
    )
    return seen


@pytest.fixture(autouse=True)
def _no_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tools, "BACKOFF_S", 0)


def test_the_api_answer_reaches_the_model_minus_unknown_fields() -> None:
    body = {
        "item": "Tent",
        "status": "shipped",
        "tracking_id": "TRK-1",
        "category": "camping",
        "customer_email": "jane@example.com",
    }
    seen = _api(lambda request: httpx2.Response(200, json=body))
    assert json.loads(lookup_order("ORD-777")) == {
        "order_id": "ORD-777",
        "item": "Tent",
        "status": "shipped",
        "tracking_id": "TRK-1",
        "category": "camping",
    }
    [request] = seen
    assert str(request.url) == "https://support.example.test/v1/orders/ORD-777"
    assert request.headers["Authorization"] == "Bearer t0ken"


@pytest.mark.parametrize(
    ("call", "path"),
    [
        (lambda: track_shipment("TRK-9"), "/v1/shipments/TRK-9"),
        (lambda: refund_policy("camping"), "/v1/refund-policies/camping"),
    ],
    ids=["shipments", "refund-policies"],
)
def test_each_tool_reads_its_own_resource(call: Callable[[], str], path: str) -> None:
    seen = _api(
        lambda request: httpx2.Response(
            200, json={"status": "in transit", "last_scan": "today", "policy": "30 days"}
        )
    )
    call()
    assert seen[0].url.path == path


def test_a_404_reads_as_an_unknown_id() -> None:
    _api(lambda request: httpx2.Response(404))
    with pytest.raises(ToolError, match="No order with ID ORD-404"):
        lookup_order("ORD-404")


def test_the_id_from_the_model_stays_one_path_segment() -> None:
    seen = _api(lambda request: httpx2.Response(404))
    with pytest.raises(ToolError):
        lookup_order("ORD-../../admin?x=1")
    assert seen[0].url.raw_path == b"/v1/orders/ORD-..%2F..%2Fadmin%3Fx%3D1"


def test_a_transient_failure_is_retried() -> None:
    replies = iter([httpx2.Response(503), httpx2.Response(200, json={"policy": "30 days"})])
    seen = _api(lambda request: next(replies))
    assert json.loads(refund_policy("camping"))["policy"] == "30 days"
    assert len(seen) == 2


def test_a_service_that_stays_down_is_reported_as_unavailable() -> None:
    def timeout(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectTimeout("timed out", request=request)

    seen = _api(timeout)
    with pytest.raises(
        ToolError, match=r"orders service is unavailable right now \(ConnectTimeout\)"
    ):
        lookup_order("ORD-1")
    assert len(seen) == tools.ATTEMPTS


def test_a_rejected_request_is_not_retried() -> None:
    seen = _api(lambda request: httpx2.Response(401))
    with pytest.raises(ToolError, match=r"rejected the request \(HTTP 401\)"):
        lookup_order("ORD-1")
    assert len(seen) == 1


@pytest.mark.parametrize(
    "response",
    [
        httpx2.Response(200, text="<html>maintenance</html>"),
        httpx2.Response(200, json=["not", "an", "object"]),
        httpx2.Response(200, json={"status": "shipped"}),
        httpx2.Response(200, json={"status": 3, "last_scan": "today"}),
    ],
    ids=["not-json", "not-an-object", "missing-field", "wrong-type"],
)
def test_an_off_contract_answer_never_reaches_the_model(response: httpx2.Response) -> None:
    _api(lambda request: response)
    with pytest.raises(ToolError, match="unexpected response"):
        track_shipment("TRK-1")


def test_the_sample_data_is_back_once_the_api_is_cleared() -> None:
    _api(lambda request: httpx2.Response(404))
    use_support_api(None)
    assert json.loads(track_shipment("TRK-5503"))["status"] == "in transit"
