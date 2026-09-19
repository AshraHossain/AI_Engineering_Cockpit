"""Order-support backend: three read-only tools over sample data or your support API.

By default the tools read fixed, in-memory data, so every run behaves the same
way. Two entries matter to the dry-run scenarios: shipment ``TRK-5503`` never
gets a new scan (the canary loops on it), and ``lookup_order`` accepts only the
``ORD-12345`` form (the promoted canary passes bare legacy numbers through and
fails).

:func:`use_support_api` sends lookups to a real HTTP service instead; live mode
does this when ``SUPPORT_API_URL`` is set. The service must answer::

    GET {base}/orders/{order_id}          -> {"item", "status", "tracking_id", "category"}
    GET {base}/shipments/{tracking_id}    -> {"status", "last_scan"}
    GET {base}/refund-policies/{category} -> {"policy"}

with 404 for an unknown ID. Only those fields reach the model. Timeouts,
connection errors, 429 and 5xx are retried; a lookup that still fails, or that
the service rejects or answers off-contract, raises :class:`ServiceError`.

Tools raise the SDK's ``ToolError`` on bad input. The Tool Runner turns that
into an ``is_error`` tool result the model can recover from.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Final
from urllib.parse import quote

import httpx2
from anthropic.lib.tools import ToolError

_logger = logging.getLogger(__name__)


class ServiceError(ToolError):
    """The support service failed, not the call: down, rejecting us, or off-contract.

    The Tool Runner reports it to the model like any ``ToolError``. The run
    loop counts it apart from bad input, so an outage is not blamed on the
    model version that happened to be serving.
    """


ORDERS: Final[dict[str, dict[str, str | None]]] = {
    "ORD-10042": {
        "item": "Trail running shoes",
        "status": "shipped",
        "tracking_id": "TRK-5501",
        "category": "footwear",
    },
    "ORD-10043": {
        "item": "Rain jacket",
        "status": "processing",
        "tracking_id": None,
        "category": "apparel",
    },
    "ORD-10044": {
        "item": "Headlamp",
        "status": "shipped",
        "tracking_id": "TRK-5503",
        "category": "electronics",
    },
}

SHIPMENTS: Final[dict[str, dict[str, str]]] = {
    "TRK-5501": {"status": "delivered", "last_scan": "2026-09-14, Denver CO"},
    "TRK-5503": {"status": "in transit", "last_scan": "no update since 2026-09-10"},
}

REFUND_POLICIES: Final[dict[str, str]] = {
    "footwear": "Unworn footwear can be returned within 30 days for a full refund.",
    "apparel": "Apparel can be returned within 60 days, tags attached.",
    "electronics": "Electronics can be returned within 14 days if unopened.",
}

SAMPLE_QUESTIONS: Final[tuple[str, ...]] = (
    "Where is my order ORD-10042?",
    "Has order ORD-10043 shipped yet?",
    "Where is my order ORD-10044? It seems stuck.",
    "Can I return the shoes from order ORD-10042?",
    "What's the return policy for order ORD-10044?",
)


TIMEOUT_S: Final = 5.0
ATTEMPTS: Final = 3
BACKOFF_S: Final = 0.25
"""Wait before the second attempt; doubled before each one after."""
_RETRY_STATUSES: Final = frozenset({429, 500, 502, 503, 504})
_FIELDS: Final[dict[str, tuple[str, ...]]] = {
    "orders": ("item", "status", "tracking_id", "category"),
    "shipments": ("status", "last_scan"),
    "refund-policies": ("policy",),
}
_SAMPLE_DATA: Final[dict[str, dict[str, dict[str, str | None]]]] = {
    "orders": ORDERS,
    "shipments": SHIPMENTS,
    "refund-policies": {name: {"policy": text} for name, text in REFUND_POLICIES.items()},
}


@dataclass(frozen=True)
class SupportAPI:
    """An HTTP service that answers the three lookups (contract in the module docstring).

    Attributes:
        base_url: Root the resource paths are appended to, e.g.
            ``https://support.example.com/v1``.
        token: Sent as ``Authorization: Bearer <token>`` when set.
        transport: httpx2 transport override; tests pass an ``httpx2.MockTransport``.
    """

    base_url: str
    token: str | None = None
    transport: httpx2.BaseTransport | None = None


_support_api: SupportAPI | None = None


def use_support_api(api: SupportAPI | None) -> None:
    """Send tool lookups to ``api``, or back to the built-in sample data with None.

    Args:
        api: The service to use, or None for the sample data.
    """
    global _support_api
    _support_api = api


def _lookup(resource: str, key: str) -> dict[str, Any] | None:
    if _support_api is None:
        return _SAMPLE_DATA[resource].get(key)
    return _fetch(_support_api, resource, key)


def _fetch(api: SupportAPI, resource: str, key: str) -> dict[str, Any] | None:
    headers = {"Accept": "application/json"}
    if api.token:
        headers["Authorization"] = f"Bearer {api.token}"
    # The key comes from the model: encode it so it stays one path segment.
    path = f"/{resource}/{quote(key, safe='')}"
    problem = ""
    # ponytail: one client per lookup, so no connection reuse. A run makes a
    # handful of tool calls; share a client if tool latency starts to matter.
    with httpx2.Client(
        base_url=api.base_url, headers=headers, timeout=TIMEOUT_S, transport=api.transport
    ) as client:
        for attempt in range(ATTEMPTS):
            if attempt:
                time.sleep(BACKOFF_S * 2 ** (attempt - 1))
            try:
                response = client.get(path)
            except httpx2.TransportError as exc:
                problem = type(exc).__name__
                continue
            if response.status_code == 404:
                return None
            if response.status_code in _RETRY_STATUSES:
                problem = f"HTTP {response.status_code}"
                continue
            if response.is_error:
                _logger.error(
                    "%s service rejected %s: HTTP %d", resource, path, response.status_code
                )
                raise ServiceError(
                    f"The {resource} service rejected the request (HTTP {response.status_code})."
                )
            return _contract_fields(resource, response)
    _logger.warning("%s lookup %s failed after %d attempts: %s", resource, path, ATTEMPTS, problem)
    raise ServiceError(f"The {resource} service is unavailable right now ({problem}).")


def _contract_fields(resource: str, response: httpx2.Response) -> dict[str, Any]:
    fields = _FIELDS[resource]
    try:
        body = response.json()
    except ValueError:
        body = None
    if not isinstance(body, dict) or not all(
        field in body and (body[field] is None or isinstance(body[field], str)) for field in fields
    ):
        # Log the shape only: a support API's payload can carry customer data.
        shape = sorted(body) if isinstance(body, dict) else type(body).__name__
        _logger.error("%s service returned an unexpected body: %s", resource, shape)
        raise ServiceError(f"The {resource} service returned an unexpected response.")
    return {field: body[field] for field in fields}


def lookup_order(order_id: str) -> str:
    """Look up an order's item, status, tracking ID and product category.

    Args:
        order_id: Order ID in the form ORD-12345.
    """
    if not order_id.startswith("ORD-"):
        raise ToolError(f"Invalid order ID {order_id!r}: expected the form ORD-12345.")
    order = _lookup("orders", order_id)
    if order is None:
        raise ToolError(f"No order with ID {order_id}.")
    return json.dumps({"order_id": order_id, **order})


def track_shipment(tracking_id: str) -> str:
    """Get the latest carrier scan for a shipment.

    Args:
        tracking_id: Tracking ID from the order, in the form TRK-1234.
    """
    shipment = _lookup("shipments", tracking_id)
    if shipment is None:
        raise ToolError(f"No shipment with tracking ID {tracking_id}.")
    return json.dumps({"tracking_id": tracking_id, **shipment})


def refund_policy(category: str) -> str:
    """Get the return and refund policy for a product category.

    Args:
        category: Product category from the order, e.g. footwear.
    """
    found = _lookup("refund-policies", category)
    if found is None:
        raise ToolError(f"No refund policy for category {category!r}.")
    return json.dumps({"category": category, "policy": found["policy"]})


TOOL_FUNCTIONS: Final[tuple[Callable[[str], str], ...]] = (
    lookup_order,
    track_shipment,
    refund_policy,
)
