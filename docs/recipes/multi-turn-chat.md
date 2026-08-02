# Recipe: Multi-Turn Chat

How to maintain conversation history across turns so the model has context
from earlier in the exchange, without letting the history grow unbounded.

## The pattern

```python
"""Minimal multi-turn chat history management."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

MAX_HISTORY_TURNS = 20  # keep the last N (role, content) pairs


@dataclass
class ChatSession:
    """In-memory conversation history for a single chat session.

    Attributes:
        system_prompt: Instruction prepended to every request.
        history: Ordered list of (role, content) tuples, oldest first.
    """

    system_prompt: str
    history: list[tuple[str, str]] = field(default_factory=list)

    def add_turn(self, role: str, content: str) -> None:
        """Append a turn and trim history to MAX_HISTORY_TURNS.

        Args:
            role: Either "user" or "assistant".
            content: The message text.

        Raises:
            ValueError: If role is not "user" or "assistant".
        """
        if role not in ("user", "assistant"):
            raise ValueError(f"Unknown role: {role!r}")

        self.history.append((role, content))
        if len(self.history) > MAX_HISTORY_TURNS:
            dropped = len(self.history) - MAX_HISTORY_TURNS
            self.history = self.history[dropped:]
            logger.info("Trimmed %d oldest turn(s) from chat history", dropped)

    def to_messages(self) -> list[dict[str, str]]:
        """Render history as a provider-agnostic messages list.

        Returns:
            A list of {"role": ..., "content": ...} dicts, system prompt first.
        """
        messages = [{"role": "system", "content": self.system_prompt}]
        messages.extend({"role": role, "content": content} for role, content in self.history)
        return messages


def chat_turn(session: ChatSession, user_input: str) -> str:
    """Send one user message and return the assistant's reply.

    Swap the placeholder call for a real client (Gemini/OpenAI/Anthropic).
    """
    session.add_turn("user", user_input)
    messages = session.to_messages()
    logger.info("Sending %d messages to model", len(messages))

    # Placeholder — replace with e.g.:
    #   response = client.chat.completions.create(model=..., messages=messages)
    #   reply = response.choices[0].message.content
    reply = "(model response would go here)"

    session.add_turn("assistant", reply)
    return reply
```

## Why this shape

- **Bounded history.** `MAX_HISTORY_TURNS` prevents the context (and your
  bill) from growing without limit across a long conversation. Tune this per
  provider's context window and your latency/cost budget.
- **Provider-agnostic message format.** `to_messages()` returns the
  `{"role", "content"}` shape that all three supported providers accept with
  little or no translation, so the session object isn't tied to one SDK.
- **Explicit role validation** catches bugs early (e.g. accidentally logging
  a `"system"` turn into history) rather than silently corrupting the
  conversation.

## Next steps

- For very long conversations, summarize dropped turns into a running
  summary instead of discarding them outright.
- See `projects/03-multi-model-orchestrator` for routing the same
  `ChatSession` history through different providers.
