"""Node handler implementations for Example 4a.

Separating handlers from graph wiring keeps graph.py focused on topology
and allows run_demo.py to import make_call_model_handler without importing
the full graph assembly.
"""

from __future__ import annotations

from typing import Any

from examples.ex04a_tool_allowlist.tools import HIGH_BLAST_RADIUS_TOOLS
from opg.core.model_client import ModelClient, ToolSpec
from opg.core.state import Message, RunState, ToolCall, ToolResult

# ---------------------------------------------------------------------------
# Internal helper
# ---------------------------------------------------------------------------


def _last_tool_call(state: RunState) -> ToolCall | None:
    """Return the most recent tool call from an assistant message, or None."""
    for msg in reversed(state.messages):
        if msg.role == "assistant" and msg.tool_calls:
            return msg.tool_calls[0]
    return None


# ---------------------------------------------------------------------------
# Node handlers
# ---------------------------------------------------------------------------


async def receive_request(state: RunState) -> None:
    """Entry node. The user's request is already in state.messages."""


async def classify_blast_radius(state: RunState) -> str:
    """Route high-risk tools to the approval gate; low-risk tools to direct dispatch."""
    tool = _last_tool_call(state)
    if tool is not None and tool.name in HIGH_BLAST_RADIUS_TOOLS:
        return "approval_gate"
    return "dispatch_direct"


async def dispatch_direct(state: RunState) -> None:
    """Execute a low-risk tool call without human approval."""
    tool = _last_tool_call(state)
    if tool is not None:
        state.append_message(
            Message(
                role="tool",
                tool_result=ToolResult(
                    tool_call_id=tool.id,
                    content=f"[direct] {tool.name} completed",
                ),
            )
        )
        state.counters.tool_calls += 1


async def dispatch_approved(state: RunState) -> None:
    """Execute a high-risk tool call after operator approval."""
    tool = _last_tool_call(state)
    if tool is not None:
        state.append_message(
            Message(
                role="tool",
                tool_result=ToolResult(
                    tool_call_id=tool.id,
                    content=f"[approved] {tool.name} completed",
                ),
            )
        )
        state.counters.tool_calls += 1


async def refuse(state: RunState) -> None:
    """Terminal: operator rejected the tool dispatch."""


async def done(state: RunState) -> None:
    """Terminal: flow completed normally."""


# ---------------------------------------------------------------------------
# Real-model handler factory
# ---------------------------------------------------------------------------


def make_call_model_handler(
    client: ModelClient,
    tools: list[ToolSpec],
    temperature: float | None = None,
) -> Any:
    """Return a NodeHandler that calls the model via client.

    The returned handler sends the accumulated messages to the model and
    appends the response (text or tool call) back to state.messages.

        async with ModelClient(cfg) as client:
            handler = make_call_model_handler(client, EX04A_TOOLS)
            graph = build_graph(call_model_handler=handler)
    """

    async def _call_model(state: RunState) -> None:
        response = await client.chat(state.messages, tools=tools, temperature=temperature)
        state.counters.model_calls += 1
        state.counters.input_tokens += response.input_tokens
        state.counters.output_tokens += response.output_tokens

        if response.tool_calls:
            state.append_message(Message(role="assistant", tool_calls=response.tool_calls))
        else:
            state.append_message(Message(role="assistant", content=response.text))

    return _call_model


# ---------------------------------------------------------------------------
# Stub factory (for tests and offline demos)
# ---------------------------------------------------------------------------


def make_model_stub(tool_name: str, arguments: dict[str, Any] | None = None) -> Any:
    """Return a NodeHandler stub that injects a canned tool call into state.

    Use in tests and demos that don't have a real model available:

        stub = make_model_stub("read_file", {"path": "/etc/hosts"})
        graph = build_graph(call_model_handler=stub)
    """

    async def _stub(state: RunState) -> None:
        state.append_message(
            Message(
                role="assistant",
                tool_calls=[ToolCall(id="tc-stub-001", name=tool_name, arguments=arguments or {})],
            )
        )
        state.counters.model_calls += 1

    return _stub
