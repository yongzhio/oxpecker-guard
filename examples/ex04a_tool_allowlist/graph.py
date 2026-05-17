"""Example 4a: Tool allowlist and blast-radius classification with HITL gate.

Threat model: model-induced misuse — the model proposes a tool call that may be
dangerous. Three layers of deterministic defence:

  Layer 4  Tool allowlist guard — only operator-approved tools may proceed.
           Fires at the 'after' slot of call_model; rejects if the tool is
           not on the allowlist. No LLM involvement.

  Layer 5  Blast-radius router — classifies the approved tool as low or high
           risk and routes accordingly. Pure routing logic, no LLM.

  Layer 6  Human-in-the-loop gate — high-risk tool calls pause execution and
           wait for an operator signal before dispatch. The gate's routing map
           is the only way out; the run cannot proceed without the signal.

Graph topology:

  receive_request → call_model ──[allowlist guard, after slot]──→ classify
    ├─(low-risk)──→ dispatch_direct → done
    └─(high-risk)─→ approval_gate ─[PausedOutcome]
                       ├─(approved)─→ dispatch_approved → done
                       └─(rejected)─→ refuse

Usage with a real model client:

    async with ModelClient(cfg) as client:
        handler = make_call_model_handler(client, EX04A_TOOLS)
        graph = build_graph(call_model_handler=handler)

Usage in tests (inject a stub that returns a specific tool call):

    stub = make_model_stub("write_file", {"path": "/etc/passwd", "content": "..."})
    graph = build_graph(call_model_handler=stub)
"""

from __future__ import annotations

from typing import Any

from examples.ex04a_tool_allowlist.handlers import (
    _last_tool_call,
    classify_blast_radius,
    dispatch_approved,
    dispatch_direct,
    done,
    make_model_stub,
    receive_request,
    refuse,
)
from examples.ex04a_tool_allowlist.tools import ALLOWED_TOOLS, HIGH_BLAST_RADIUS_TOOLS
from opg.core.graph import (
    GateNode,
    Graph,
    GraphBuilder,
    GuardFn,
    GuardPass,
    GuardReject,
    GuardVerdict,
)
from opg.core.state import RunState, ToolCall

# Re-export for test compatibility — tests import these from graph.py.
__all__ = [
    "ALLOWED_TOOLS",
    "HIGH_BLAST_RADIUS_TOOLS",
    "ApprovalGate",
    "build_graph",
    "make_model_stub",
    "tool_allowlist_guard",
]

# ---------------------------------------------------------------------------
# Guard
# ---------------------------------------------------------------------------


def tool_allowlist_guard(
    allowed_tools: frozenset[str] = ALLOWED_TOOLS,
    name: str = "tool_allowlist",
) -> GuardFn:
    """Reject any tool call whose name is not in allowed_tools.

    Reads the most recent assistant message's tool_calls field. If no tool
    call is present, the guard passes (non-tool responses are unrestricted).
    """

    def _check(state: RunState) -> GuardVerdict:
        tool = _last_tool_call(state)
        if tool is None:
            return GuardPass(guard_name=name, detail="no tool call in state")
        if tool.name not in allowed_tools:
            return GuardReject(
                guard_name=name,
                reason=f"tool {tool.name!r} is not on the operator allowlist",
            )
        return GuardPass(guard_name=name, detail=f"tool {tool.name!r} is allowed")

    return _check


# ---------------------------------------------------------------------------
# Gate node
# ---------------------------------------------------------------------------


class ApprovalGate(GateNode):
    """Human-in-the-loop gate for high-risk tool dispatch.

    The orchestrator runner does NOT call elicit_signal() during run(). When the
    gate is reached, run() returns a PausedOutcome. The deployment layer calls
    elicit_signal() (or any other signal-delivery mechanism) and then passes the
    result to runner.resume(checkpoint_id, signal).

    In this demo, elicit_signal() prompts the operator via stdin. Replace it with
    a web-UI push, a Slack notification, an MFA webhook, or any other mechanism
    that fits the deployment.
    """

    def elicit_signal(self, state: RunState) -> str:
        # The orchestrator's deterministic routing depends on this function
        # returning EXACTLY one of self.signals — no fuzzy matching, no substring
        # matching, no normalization beyond cosmetic strip/lower. Any deviation
        # here would reintroduce the parsing-drift failure mode that OPG's typed
        # signal enumeration is designed to prevent (see opg_design_v1.1.md
        # comparison section). The orchestrator validates the return value against
        # self.signals and raises on mismatch; we enforce the same discipline at
        # the elicitation layer to keep the structural property visible to readers.
        tool: ToolCall | None = _last_tool_call(state)
        tool_name = tool.name if tool is not None else "(unknown)"
        print(
            f"\nTool call: {tool_name!r} — high blast-radius.\n"
            f"Valid signals: {', '.join(self.signals)}"
        )
        while True:
            ans = input(f"Signal? [{'/'.join(self.signals)}]: ").strip().lower()
            if ans in self.signals:
                return ans
            print(f"Invalid input {ans!r}. Must be one of: {', '.join(self.signals)}.")


# ---------------------------------------------------------------------------
# Graph builder
# ---------------------------------------------------------------------------


def build_graph(call_model_handler: Any) -> Graph:
    """Assemble the Example 4a graph.

    call_model_handler is a NodeHandler that appends the model's tool-call
    response to state.messages. In tests, pass a stub (make_model_stub());
    in production, pass a handler that calls your model client.
    """
    gate = ApprovalGate(
        name="approval_gate",
        signals=("approved", "rejected"),
        routing={"approved": "dispatch_approved", "rejected": "refuse"},
    )
    return (
        GraphBuilder(entry="receive_request")
        .node("receive_request", handler=receive_request)
        .node("call_model", handler=call_model_handler, kind="model_call")
        .node("classify_blast_radius", handler=classify_blast_radius)
        .node("dispatch_direct", handler=dispatch_direct)
        .node("dispatch_approved", handler=dispatch_approved)
        .node("refuse", handler=refuse)
        .node("done", handler=done)
        .gate_node(gate)
        .edge("receive_request", "call_model")
        .edge("call_model", "classify_blast_radius")
        .edge("classify_blast_radius", "dispatch_direct")
        .edge("classify_blast_radius", "approval_gate")
        .edge("dispatch_direct", "done")
        .edge("dispatch_approved", "done")
        .guard_after("call_model", tool_allowlist_guard())
        .build()
    )
