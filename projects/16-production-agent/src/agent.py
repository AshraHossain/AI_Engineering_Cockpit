"""One agent run: the SDK Tool Runner loop, instrumented.

Everything a run produces is measured once, here: an ``llm.call`` span, a
latency sample and a cost entry for every model turn, and a ``tool.<name>``
span and latency sample for every tool call. The loop guard sees each turn's
tool calls *before* the runner executes them; breaking out of the loop at that
point means those tools never run.

The root ``agent.run`` span belongs to the caller (the rollout controller),
which also decides what the run means for alerts and the canary. This module
only reports what happened, as a :class:`RunRecord`.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Final

from anthropic import beta_tool

from tools import TOOL_FUNCTIONS

PROJECT: Final = "16-production-agent"
MAX_ITERATIONS: Final = 8

SYSTEM_PROMPT: Final = (
    "You are the order-support assistant for an outdoor gear shop. Answer questions "
    "about orders, shipments and returns using the tools, and keep answers short.\n\n"
    "Order IDs have the form ORD-12345. If a customer gives a bare order number such "
    "as 10042, call lookup_order with ORD-10042. If a tool returns an error, tell the "
    "customer plainly what went wrong instead of guessing."
)


@dataclass(frozen=True)
class AgentVersion:
    """Everything that defines one deployable version of the agent.

    Attributes:
        version: Registry version identifier, e.g. ``"v1"``.
        model: Claude model ID.
        system_prompt: System prompt sent on every request.
        effort: ``output_config.effort`` level.
        max_tokens: Per-response output cap.
    """

    version: str
    model: str
    system_prompt: str = SYSTEM_PROMPT
    effort: str = "medium"
    max_tokens: int = 16000

    def fingerprint(self) -> str:
        """Digest of every setting that shapes this version's requests.

        Returns:
            Hex SHA-256 over model, prompt, thinking mode, effort, output cap
            and tool schemas.
        """
        payload = {
            "model": self.model,
            "system": self.system_prompt,
            "thinking": "adaptive",
            "effort": self.effort,
            "max_tokens": self.max_tokens,
            "tools": [beta_tool(fn).to_dict() for fn in TOOL_FUNCTIONS],
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


STABLE: Final = AgentVersion("v1", "claude-opus-5")
CANARY: Final = AgentVersion("v2", "claude-sonnet-5")


@dataclass(frozen=True)
class RunRecord:
    """What one run did, as alerts and the canary judge see it.

    Attributes:
        request_id: Request identifier.
        version: Version that served it.
        group: ``"stable"`` or ``"canary"``.
        outcome: ``ok``, ``loop``, ``max_iterations``, ``refusal``,
            ``incomplete`` or ``api_error``.
        duration_s: Wall time of the whole run, in seconds.
        cost_usd: Cost of every model turn in the run.
        tool_calls: Tool calls executed.
        tool_errors: Tool calls that raised.
        input_tokens: Prompt tokens across all turns.
        output_tokens: Output tokens across all turns.
        answer: Text of the final assistant turn.
        loop_reason: Loop guard verdict when the outcome is ``loop``.
    """

    request_id: str
    version: str
    group: str
    outcome: str
    duration_s: float
    cost_usd: float
    tool_calls: int
    tool_errors: int
    input_tokens: int
    output_tokens: int
    answer: str = ""
    loop_reason: str | None = None


def outcome_for(stop_reason: str | None) -> str:
    """Map the final turn's stop reason to a run outcome.

    Args:
        stop_reason: ``stop_reason`` of the last message the runner yielded.

    Returns:
        ``ok`` for ``end_turn``; ``max_iterations`` for ``tool_use`` (the
        runner stopped with tool calls still pending); ``refusal``; otherwise
        ``incomplete``.
    """
    if stop_reason == "end_turn":
        return "ok"
    if stop_reason == "tool_use":
        return "max_iterations"
    if stop_reason == "refusal":
        return "refusal"
    return "incomplete"
