"""A scripted Messages API behind ``httpx2.MockTransport``.

The Anthropic client is real and so is its Tool Runner; only the HTTP layer is
replaced. Each model has a :class:`ModelProfile`: a policy that reads the
conversation so far and returns the next assistant turn, plus the latency and
token counts to report. Latency advances the shared :class:`FakeClock`, so
dry-run durations, alerts and dashboards come out identical on every run.

A policy may also return an ``httpx2.Response`` directly, which is how tests
simulate API errors.
"""

from __future__ import annotations

import itertools
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Final

import anthropic
import httpx2

DRY_RUN_EPOCH: Final = 1_767_225_600.0  # 2026-01-01T00:00:00Z
TOKENS_PER_EARLIER_TURN: Final = 400


@dataclass
class FakeClock:
    """A clock that moves only when told to.

    Attributes:
        now: Current time, in epoch seconds.
    """

    now: float = DRY_RUN_EPOCH

    def __call__(self) -> float:
        """Current time, in epoch seconds."""
        return self.now

    def advance(self, seconds: float) -> None:
        """Move time forward.

        Args:
            seconds: How far to move.
        """
        self.now += seconds


@dataclass(frozen=True)
class Turn:
    """One scripted assistant turn.

    Attributes:
        content: Content blocks; ``tool_use`` blocks get their ``id`` filled in.
        stop_reason: The turn's stop reason.
    """

    content: list[dict[str, Any]]
    stop_reason: str = "end_turn"


Policy = Callable[[list[dict[str, Any]]], "Turn | httpx2.Response"]


@dataclass(frozen=True)
class ModelProfile:
    """How one scripted model behaves.

    Attributes:
        policy: Chooses the next turn from the request's messages.
        latency_s: Seconds each call takes on the fake clock.
        input_tokens: Prompt tokens reported for the first turn; each earlier
            assistant turn in the conversation adds :data:`TOKENS_PER_EARLIER_TURN`.
        output_tokens: Output tokens reported per turn.
    """

    policy: Policy
    latency_s: float = 1.0
    input_tokens: int = 1200
    output_tokens: int = 300


def text(value: str) -> dict[str, Any]:
    """A text content block."""
    return {"type": "text", "text": value}


def tool_call(name: str, **tool_input: Any) -> dict[str, Any]:
    """A ``tool_use`` content block; the transport assigns its ID."""
    return {"type": "tool_use", "name": name, "input": tool_input}


def tool_history(messages: list[dict[str, Any]]) -> list[tuple[str, dict[str, Any], str, bool]]:
    """Pair each tool call in a conversation with its result.

    Args:
        messages: The request's ``messages`` array.

    Returns:
        ``(tool_name, input, result_text, is_error)`` per completed call, oldest first.
    """
    calls: dict[str, tuple[str, dict[str, Any]]] = {}
    history = []
    for message in messages:
        if not isinstance(message["content"], list):
            continue
        for block in message["content"]:
            if block["type"] == "tool_use":
                calls[block["id"]] = (block["name"], block["input"])
            elif block["type"] == "tool_result":
                content = block.get("content", "")
                if isinstance(content, list):
                    content = "".join(part.get("text", "") for part in content)
                name, tool_input = calls[block["tool_use_id"]]
                history.append((name, tool_input, content, bool(block.get("is_error"))))
    return history


@dataclass
class _Transport:
    profiles: Mapping[str, ModelProfile]
    clock: FakeClock
    _ids: itertools.count[int] = field(default_factory=lambda: itertools.count(1))

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content)
        profile = self.profiles[body["model"]]
        self.clock.advance(profile.latency_s)
        turn = profile.policy(body["messages"])
        if isinstance(turn, httpx2.Response):
            return turn
        content = [
            (
                {**block, "id": f"toolu_{next(self._ids):05d}"}
                if block["type"] == "tool_use"
                else block
            )
            for block in turn.content
        ]
        earlier_turns = sum(m["role"] == "assistant" for m in body["messages"])
        return httpx2.Response(
            200,
            json={
                "id": f"msg_{next(self._ids):05d}",
                "type": "message",
                "role": "assistant",
                "model": body["model"],
                "content": content,
                "stop_reason": turn.stop_reason,
                "stop_sequence": None,
                "usage": {
                    "input_tokens": profile.input_tokens + TOKENS_PER_EARLIER_TURN * earlier_turns,
                    "output_tokens": profile.output_tokens,
                },
            },
        )


def scripted_client(profiles: Mapping[str, ModelProfile], clock: FakeClock) -> anthropic.Anthropic:
    """A real Anthropic client whose HTTP calls are answered by the profiles.

    Args:
        profiles: Behaviour per model ID.
        clock: Clock advanced by each call's scripted latency.

    Returns:
        The client. It never touches the network and needs no API key.
    """
    transport = httpx2.MockTransport(_Transport(profiles, clock))
    return anthropic.Anthropic(
        api_key="dry-run", max_retries=0, http_client=httpx2.Client(transport=transport)
    )
