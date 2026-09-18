"""The two dry-run scenarios: scripted traffic, scripted models, scripted approvers.

Both models follow the same support policy, answering from the tools. Each
scenario changes one behaviour of the canary model:

* ``bad-canary``: v2 keeps re-checking a shipment whose status never
  changes, so the loop guard trips on the third identical call and the canary
  is aborted.
* ``late-regression``: v2 passes bare legacy order numbers (``10042``)
  straight to ``lookup_order`` instead of normalizing them. Legacy numbers
  only appear in traffic after promotion, so the canary looks healthy, gets
  approved and promoted, then trips ``tool_error_rate`` during the watch and
  is rolled back.

Request lists are built from :func:`canary.route`, so the ``late-regression``
traffic switches to legacy numbers exactly when the canary is promoted.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Final

from cockpit.governance.approval_workflow import ApprovalRequest, ApprovalWorkflow

from agent import CANARY, STABLE
from canary import DEFAULT_CANARY_PERCENT, READY_AFTER, WATCH_RUNS, route
from fake_model import ModelProfile, Policy, Turn, text, tool_call, tool_history

_ORDER_NUMBER = re.compile(r"\b(ORD-)?(\d{5})\b")


def support_policy(*, normalize_ids: bool = True, recheck_stuck: bool = False) -> Policy:
    """The scripted assistant's behaviour.

    Args:
        normalize_ids: Turn a bare ``10042`` into ``ORD-10042`` before lookup.
        recheck_stuck: Keep calling ``track_shipment`` while a shipment is in transit.

    Returns:
        A policy for :class:`fake_model.ModelProfile`.
    """

    def policy(messages: list[dict[str, Any]]) -> Turn:
        question = messages[0]["content"]
        history = tool_history(messages)
        if not history:
            match = _ORDER_NUMBER.search(question)
            if match is None:
                return Turn([text("Which order number is this about?")])
            order_id = (
                match.group(0) if match.group(1) or not normalize_ids else f"ORD-{match.group(2)}"
            )
            return Turn(
                [text("Let me look that up."), tool_call("lookup_order", order_id=order_id)],
                "tool_use",
            )

        name, _, result, is_error = history[-1]
        if is_error:
            return Turn([text(f"Sorry, I couldn't look that up: {result}")])
        data = json.loads(result)
        if name == "lookup_order":
            if "return" in question.lower():
                return Turn([tool_call("refund_policy", category=data["category"])], "tool_use")
            if data["tracking_id"]:
                return Turn(
                    [tool_call("track_shipment", tracking_id=data["tracking_id"])], "tool_use"
                )
            return Turn([text(f"Your {data['item']} is {data['status']} and hasn't shipped yet.")])
        if name == "track_shipment":
            if recheck_stuck and data["status"] == "in transit":
                return Turn(
                    [
                        text("Let me check that again."),
                        tool_call("track_shipment", tracking_id=data["tracking_id"]),
                    ],
                    "tool_use",
                )
            return Turn(
                [text(f"Your shipment is {data['status']} (last scan: {data['last_scan']}).")]
            )
        return Turn([text(data["policy"])])

    return policy


_MODERN: Final = (
    "Where is my order ORD-10042?",
    "Has order ORD-10043 shipped yet?",
    "Where is my order ORD-10044? It seems stuck.",
    "Can I return the shoes from order ORD-10042?",
)
_AFTER_PROMOTION: Final = (
    "Where is my order 10042?",
    "Can I return order 10044?",
    "Where is my order ORD-10042?",
)


def _request_id(n: int) -> str:
    return f"req-{n:04d}"


def _bad_canary_requests() -> list[tuple[str, str]]:
    return [(_request_id(n), _MODERN[n % len(_MODERN)]) for n in range(1, 121)]


def _late_regression_requests() -> list[tuple[str, str]]:
    requests: list[tuple[str, str]] = []
    canary_runs = 0
    n = 0
    while canary_runs < READY_AFTER:
        n += 1
        if route(_request_id(n), DEFAULT_CANARY_PERCENT):
            canary_runs += 1
        requests.append((_request_id(n), _MODERN[n % len(_MODERN)]))
    for offset in range(1, WATCH_RUNS + 11):
        requests.append((_request_id(n + offset), _AFTER_PROMOTION[offset % len(_AFTER_PROMOTION)]))
    return requests


def scripted_approvals(
    *decisions: tuple[str, bool, str]
) -> Callable[[ApprovalWorkflow, ApprovalRequest], None]:
    """An approver that records fixed decisions in order.

    Args:
        decisions: ``(approver, approved, comment)`` triples.

    Returns:
        A callable for ``RolloutController.approve``.
    """

    def approve(workflow: ApprovalWorkflow, request: ApprovalRequest) -> None:
        for principal, approved, comment in decisions:
            workflow.decide(request.request_id, approved, principal, comment=comment)

    return approve


@dataclass(frozen=True)
class Scenario:
    """A complete dry run.

    Attributes:
        name: CLI name.
        requests: ``(request_id, question)`` in order.
        profiles: Scripted behaviour per model ID.
        approve: Scripted approvers.
    """

    name: str
    requests: list[tuple[str, str]]
    profiles: dict[str, ModelProfile]
    approve: Callable[[ApprovalWorkflow, ApprovalRequest], None]


_STABLE_PROFILE: Final = ModelProfile(support_policy(), latency_s=2.0)
_APPROVERS: Final = scripted_approvals(
    ("alice", True, "canary numbers look good"),
    ("bob", True, "cheaper at the same quality"),
)

SCENARIOS: Final[dict[str, Scenario]] = {
    "bad-canary": Scenario(
        name="bad-canary",
        requests=_bad_canary_requests(),
        profiles={
            STABLE.model: _STABLE_PROFILE,
            CANARY.model: ModelProfile(support_policy(recheck_stuck=True), latency_s=1.2),
        },
        approve=_APPROVERS,
    ),
    "late-regression": Scenario(
        name="late-regression",
        requests=_late_regression_requests(),
        profiles={
            STABLE.model: _STABLE_PROFILE,
            CANARY.model: ModelProfile(support_policy(normalize_ids=False), latency_s=1.2),
        },
        approve=_APPROVERS,
    ),
}
