"""Simulated order-support backend: three read-only tools over in-memory data.

The data is fixed so every run behaves the same way. Two entries matter to the
dry-run scenarios: shipment ``TRK-5503`` never gets a new scan (the canary
loops on it), and ``lookup_order`` accepts only the ``ORD-12345`` form (the
promoted canary passes bare legacy numbers through and fails).

Tools raise the SDK's ``ToolError`` on bad input. The Tool Runner turns that
into an ``is_error`` tool result the model can recover from.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Final

from anthropic.lib.tools import ToolError

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


def lookup_order(order_id: str) -> str:
    """Look up an order's item, status, tracking ID and product category.

    Args:
        order_id: Order ID in the form ORD-12345.
    """
    if not order_id.startswith("ORD-"):
        raise ToolError(f"Invalid order ID {order_id!r}: expected the form ORD-12345.")
    order = ORDERS.get(order_id)
    if order is None:
        raise ToolError(f"No order with ID {order_id}.")
    return json.dumps({"order_id": order_id, **order})


def track_shipment(tracking_id: str) -> str:
    """Get the latest carrier scan for a shipment.

    Args:
        tracking_id: Tracking ID from the order, in the form TRK-1234.
    """
    shipment = SHIPMENTS.get(tracking_id)
    if shipment is None:
        raise ToolError(f"No shipment with tracking ID {tracking_id}.")
    return json.dumps({"tracking_id": tracking_id, **shipment})


def refund_policy(category: str) -> str:
    """Get the return and refund policy for a product category.

    Args:
        category: Product category from the order, e.g. footwear.
    """
    policy = REFUND_POLICIES.get(category)
    if policy is None:
        raise ToolError(f"No refund policy for category {category!r}.")
    return json.dumps({"category": category, "policy": policy})


TOOL_FUNCTIONS: Final[tuple[Callable[[str], str], ...]] = (
    lookup_order,
    track_shipment,
    refund_policy,
)
