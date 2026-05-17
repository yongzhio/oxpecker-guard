"""Node handlers for Example 1.

The call_model handler asks the model for a product recommendation in JSON.
The model is instructed to return a JSON object only — no prose, no markdown.
Guards in the after-slot of call_model validate the output before the run
continues to done.
"""

from __future__ import annotations

from typing import Any

from opg.core.model_client import ModelClient, ToolSpec
from opg.core.state import Message, RunState

_SYSTEM_PROMPT = (
    "You are a product recommendation assistant. When the user asks for a recommendation, "
    "respond with a JSON object — no other text — containing exactly these fields: "
    "product_id (string), name (string), category (string), price_usd (number), in_stock (boolean). "
    "Return only the JSON object; do not include explanations or markdown formatting."
)


async def receive_request(state: RunState) -> None:
    """Entry node. The user's request is already in state.messages."""


async def done(state: RunState) -> None:
    """Terminal: guards passed and run completed normally."""


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


def make_model_stub(json_payload: str) -> Any:
    """Return a NodeHandler stub that injects json_payload as an assistant message.

    Use in tests to exercise each guard with a specific output:

        stub = make_model_stub('{"product_id": "SKU-1001", ...}')
        graph = build_graph(call_model_handler=stub)
    """

    async def _stub(state: RunState) -> None:
        state.append_message(Message(role="assistant", content=json_payload))
        state.counters.model_calls += 1

    return _stub
