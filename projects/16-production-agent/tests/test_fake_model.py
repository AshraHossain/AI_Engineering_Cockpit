"""The scripted Messages API: real SDK objects out, fake clock advanced."""

from __future__ import annotations

from typing import Any

import anthropic
import httpx2
import pytest

from fake_model import (
    DRY_RUN_EPOCH,
    FakeClock,
    ModelProfile,
    Policy,
    Turn,
    scripted_client,
    text,
    tool_call,
    tool_history,
)


def ask(policy: Policy, clock: FakeClock, messages: list[dict[str, Any]] | None = None) -> Any:
    client = scripted_client({"m": ModelProfile(policy, latency_s=2.0)}, clock)
    return client.messages.create(
        model="m", max_tokens=100, messages=messages or [{"role": "user", "content": "Hi"}]
    )


def test_the_clock_moves_only_when_advanced(clock: FakeClock) -> None:
    assert clock() == DRY_RUN_EPOCH
    clock.advance(1.5)
    assert clock() == DRY_RUN_EPOCH + 1.5


def test_a_scripted_turn_comes_back_as_a_real_message(clock: FakeClock) -> None:
    def policy(messages: list[dict[str, Any]]) -> Turn:
        return Turn(
            [text("Checking."), tool_call("lookup_order", order_id="ORD-10042")], "tool_use"
        )

    message = ask(policy, clock)
    assert message.stop_reason == "tool_use"
    assert message.content[0].text == "Checking."
    call = message.content[1]
    assert (call.name, call.input) == ("lookup_order", {"order_id": "ORD-10042"})
    assert call.id.startswith("toolu_")
    assert (message.usage.input_tokens, message.usage.output_tokens) == (1200, 300)
    assert clock() == DRY_RUN_EPOCH + 2.0


def test_prompt_tokens_grow_with_each_earlier_assistant_turn(clock: FakeClock) -> None:
    messages = [
        {"role": "user", "content": "Hi"},
        {"role": "assistant", "content": "Hello."},
        {"role": "user", "content": "Where is ORD-10042?"},
    ]
    message = ask(lambda m: Turn([text("Shipped.")]), clock, messages)
    assert message.usage.input_tokens == 1600


def test_a_policy_can_answer_with_an_http_error(clock: FakeClock) -> None:
    def overloaded(messages: list[dict[str, Any]]) -> httpx2.Response:
        return httpx2.Response(
            529, json={"type": "error", "error": {"type": "overloaded_error", "message": "busy"}}
        )

    with pytest.raises(anthropic.APIStatusError):
        ask(overloaded, clock)


def test_tool_history_pairs_each_call_with_its_result() -> None:
    messages = [
        {"role": "user", "content": "Where is 10042?"},
        {
            "role": "assistant",
            "content": [{**tool_call("lookup_order", order_id="10042"), "id": "t1"}],
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "t1",
                    "content": [{"type": "text", "text": "Invalid order ID"}],
                    "is_error": True,
                }
            ],
        },
    ]
    assert tool_history(messages) == [
        ("lookup_order", {"order_id": "10042"}, "Invalid order ID", True)
    ]
