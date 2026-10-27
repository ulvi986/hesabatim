"""Provider abstraction. Business logic only depends on this interface, so switching the
LLM vendor means adding a provider, not rewriting tools or services."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: str  # raw JSON string as produced by the model


@dataclass
class ProviderEvent:
    type: Literal["text", "tool_calls"]
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)


class ProviderError(Exception):
    pass


class LLMProvider(Protocol):
    name: str
    model: str

    def stream(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> Iterator[ProviderEvent]:
        """Yield text deltas as they arrive, then at most one tool_calls event at the end."""
        ...
