"""Node handlers for Example 6.

The call_model handler summarizes a legal filing. The user message contains
the full filing text. The after-slot name-list guard checks the model's
summary before it reaches the caller.
"""

from __future__ import annotations

from typing import Any

from opg.core.model_client import ModelClient, ToolSpec
from opg.core.state import Message, RunState

_SYSTEM_PROMPT = (
    "You are a legal-research assistant. When given a legal filing, produce a "
    "concise summary of the key facts, procedural history, and parties involved. "
    "Aim for 200-300 words. Plain prose; no bullet points."
)


async def receive_request(state: RunState) -> None:
    """Entry node. The filing text is already in state.messages as the user turn."""


async def done(state: RunState) -> None:
    """Terminal: name-list guard passed and the summary is safe to surface."""


def make_call_model_handler(
    client: ModelClient,
    tools: list[ToolSpec] | None = None,
    temperature: float | None = None,
) -> Any:
    """Return a NodeHandler that calls the model and appends its text response."""

    async def _call_model(state: RunState) -> None:
        response = await client.chat(state.messages, tools=tools, temperature=temperature)
        state.counters.model_calls += 1
        state.counters.input_tokens += response.input_tokens
        state.counters.output_tokens += response.output_tokens
        state.append_message(Message(role="assistant", content=response.text))

    return _call_model


def make_model_stub(text: str) -> Any:
    """Return a NodeHandler stub that injects text as an assistant message.

    Use in tests to exercise the name-list guard with specific output:

        stub = make_model_stub("The plaintiff John Doe filed a complaint...")
        graph = build_graph(make_model_stub(...), protected_names=[...])
    """

    async def _stub(state: RunState) -> None:
        state.append_message(Message(role="assistant", content=text))
        state.counters.model_calls += 1

    return _stub
