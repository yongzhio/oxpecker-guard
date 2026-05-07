"""Model client — thin OpenAI-compatible HTTP wrapper.

The orchestrator does not call the model directly; it goes through this module.
Centralizing the HTTP boundary makes the trust-domain crossing visible: the
orchestrator's process sends bytes over HTTP, gets bytes back, and parses them
into typed structures the rest of the system can consume.

This v0 supports the chat completions endpoint with optional tool calling.
LM Studio and Ollama both serve this shape via their OpenAI-compatible APIs.

Note: the client takes a ModelConfig at construction. Per-call overrides go
through method arguments, not config mutation.
"""

from __future__ import annotations

from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field
from typing_extensions import Self

from opg.core.config import ModelConfig
from opg.core.state import Message, ToolCall


class ToolSpec(BaseModel):
    """A tool the model is told it may call. JSON-schema-shaped per OpenAI's spec."""

    model_config = ConfigDict(extra="forbid")

    name: str
    description: str
    parameters: dict[str, Any] = Field(default_factory=dict)


class ModelResponse(BaseModel):
    """A normalized response from the model server.

    Either `text` is set (the model produced a final answer) or `tool_calls`
    is non-empty (the model wants to call tools). Both may be empty in
    pathological cases (model returned nothing); callers must handle that.
    """

    model_config = ConfigDict(extra="forbid")

    text: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    finish_reason: str = ""


class ModelClient:
    """OpenAI-compatible chat completions client.

    Construct from a ModelConfig. Use as an async context manager so the
    underlying httpx.AsyncClient is closed cleanly:

        async with ModelClient(config) as client:
            resp = await client.chat(messages, tools=[...])
    """

    def __init__(self, config: ModelConfig) -> None:
        self._config = config
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> Self:
        headers = {}
        if self._config.api_key:
            headers["Authorization"] = f"Bearer {self._config.api_key}"
        self._client = httpx.AsyncClient(
            base_url=self._config.base_url,
            timeout=self._config.timeout_seconds,
            headers=headers,
        )
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def chat(
        self,
        messages: list[Message],
        tools: list[ToolSpec] | None = None,
        temperature: float | None = None,
    ) -> ModelResponse:
        """Send a chat request and return the parsed response.

        Raises httpx.HTTPStatusError on non-2xx responses. The orchestrator's
        runner catches these and writes them to the audit log as `error` events.
        """
        if self._client is None:
            raise RuntimeError("ModelClient used outside of an async context manager")

        body: dict[str, Any] = {
            "model": self._config.model_name,
            "messages": [self._serialize_message(m) for m in messages],
            "temperature": temperature if temperature is not None else self._config.temperature,
        }
        if tools:
            body["tools"] = [self._serialize_tool(t) for t in tools]

        response = await self._client.post("/chat/completions", json=body)
        response.raise_for_status()
        return self._parse_response(response.json())

    @staticmethod
    def _serialize_message(message: Message) -> dict[str, Any]:
        """Convert internal Message to the OpenAI wire format."""
        out: dict[str, Any] = {"role": message.role, "content": message.content}
        if message.tool_calls:
            out["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.name,
                        "arguments": _json_dumps(tc.arguments),
                    },
                }
                for tc in message.tool_calls
            ]
        if message.tool_result is not None:
            # OpenAI tool-result messages use role="tool" + tool_call_id at top level
            out["tool_call_id"] = message.tool_result.tool_call_id
            out["content"] = message.tool_result.content
        return out

    @staticmethod
    def _serialize_tool(tool: ToolSpec) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.parameters or {"type": "object", "properties": {}},
            },
        }

    @staticmethod
    def _parse_response(data: dict[str, Any]) -> ModelResponse:
        """Parse OpenAI-shaped response into ModelResponse."""
        choices = data.get("choices") or []
        if not choices:
            return ModelResponse()
        choice = choices[0]
        msg = choice.get("message") or {}
        text = msg.get("content") or ""
        finish_reason = choice.get("finish_reason") or ""

        tool_calls: list[ToolCall] = []
        for tc in msg.get("tool_calls") or []:
            fn = tc.get("function") or {}
            args_raw = fn.get("arguments") or "{}"
            args = _json_loads(args_raw) if isinstance(args_raw, str) else args_raw
            if not isinstance(args, dict):
                args = {}
            tool_calls.append(
                ToolCall(
                    id=tc.get("id") or "",
                    name=fn.get("name") or "",
                    arguments=args,
                )
            )

        usage = data.get("usage") or {}
        return ModelResponse(
            text=text,
            tool_calls=tool_calls,
            input_tokens=int(usage.get("prompt_tokens") or 0),
            output_tokens=int(usage.get("completion_tokens") or 0),
            finish_reason=finish_reason,
        )


def _json_dumps(value: Any) -> str:
    """Centralized JSON dump used for tool-call arguments on the wire."""
    import json

    return json.dumps(value, separators=(",", ":"))


def _json_loads(value: str) -> Any:
    """Lenient JSON load — returns {} on parse failure rather than raising.

    This matters because some models emit malformed tool-call argument JSON.
    A schema-validation guard is what catches this; the model client should
    not crash the orchestrator.
    """
    import json

    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return {}
