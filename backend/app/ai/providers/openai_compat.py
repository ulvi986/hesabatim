"""Streaming client for any OpenAI-compatible /chat/completions endpoint with tool calling
(OpenAI, Azure OpenAI, OpenRouter, Together, Groq, vLLM, Ollama's OpenAI mode, ...)."""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import httpx

from app.ai.providers.base import ProviderError, ProviderEvent, ToolCall


class OpenAICompatibleProvider:
    name = "openai_compatible"

    def __init__(self, *, base_url: str, api_key: str, model: str, timeout: float = 60.0,
                 transport: httpx.BaseTransport | None = None):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self._transport = transport

    def stream(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> Iterator[ProviderEvent]:
        # internal bookkeeping keys (e.g. "name" on tool messages) are not part of the wire format
        wire = [{k: v for k, v in m.items() if not (m["role"] == "tool" and k == "name")} for m in messages]
        body: dict[str, Any] = {"model": self.model, "messages": wire, "stream": True, "temperature": 0.2}
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        calls: dict[int, dict[str, str]] = {}
        try:
            with httpx.Client(timeout=self.timeout, transport=self._transport) as client:
                with client.stream("POST", f"{self.base_url}/chat/completions", json=body, headers=headers) as resp:
                    if resp.status_code >= 400:
                        resp.read()
                        raise ProviderError(f"LLM provider returned HTTP {resp.status_code}: {resp.text[:300]}")
                    for line in resp.iter_lines():
                        if not line or not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if data == "[DONE]":
                            break
                        try:
                            chunk = json.loads(data)
                        except json.JSONDecodeError:
                            continue
                        for choice in chunk.get("choices") or []:
                            delta = choice.get("delta") or {}
                            if delta.get("content"):
                                yield ProviderEvent(type="text", text=delta["content"])
                            for tc in delta.get("tool_calls") or []:
                                slot = calls.setdefault(tc.get("index", 0), {"id": "", "name": "", "arguments": ""})
                                if tc.get("id"):
                                    slot["id"] = tc["id"]
                                fn = tc.get("function") or {}
                                if fn.get("name"):
                                    slot["name"] += fn["name"]
                                if fn.get("arguments"):
                                    slot["arguments"] += fn["arguments"]
        except httpx.HTTPError as exc:
            raise ProviderError(f"LLM provider unreachable: {exc.__class__.__name__}") from exc
        if calls:
            yield ProviderEvent(type="tool_calls", tool_calls=[
                ToolCall(id=c["id"] or f"call_{i}", name=c["name"], arguments=c["arguments"] or "{}")
                for i, c in sorted(calls.items())
            ])
