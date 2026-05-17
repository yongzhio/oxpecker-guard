"""Example 6: Name-list filter on model output.

Threat model: legal-research agent summarising a filing that contains
a protected minor's name. The model may reproduce the name verbatim in
its summary even when the system prompt does not instruct it to avoid
doing so. The deterministic guard catches every literal match against the
operator-supplied name list before the output leaves the system.

Graph topology:

  receive_request → call_model → done
                        │
                        └─[after slot: name_list_filter]

The protected names list is passed at graph-build time and captured in the
guard closure. Updating the list requires rebuilding the graph.

Usage with a real model client:

    names = load_protected_names(path)
    async with ModelClient(cfg) as client:
        handler = make_call_model_handler(client)
        graph = build_graph(call_model_handler=handler, protected_names=names)

Usage in tests:

    stub = make_model_stub("The plaintiff filed a complaint.")
    graph = build_graph(stub, protected_names=["John Doe", "J Doe"])
"""

from __future__ import annotations

from typing import Any

from examples.ex06_name_protection.guards import name_list_guard
from examples.ex06_name_protection.handlers import done, make_model_stub, receive_request
from opg.core.graph import Graph, GraphBuilder

__all__ = ["build_graph", "make_model_stub"]


def build_graph(call_model_handler: Any, protected_names: list[str]) -> Graph:
    """Assemble the Example 6 graph.

    call_model_handler is a NodeHandler that appends the model's summary to
    state.messages. protected_names is the operator-supplied list of name
    variants that must not appear in the output.
    """
    return (
        GraphBuilder(entry="receive_request")
        .node("receive_request", handler=receive_request)
        .node("call_model", handler=call_model_handler, kind="model_call")
        .node("done", handler=done)
        .edge("receive_request", "call_model")
        .edge("call_model", "done")
        .guard_after("call_model", name_list_guard(protected_names))
        .build()
    )
