"""Example 1: Schema validation with three layered after-slot guards.

Threat model: the model returns a structured recommendation that is
structurally or semantically wrong, or names a product that does not
exist in the operator's catalog.

Three deterministic layers — applied in order in the 'after' slot of
call_model; first rejection halts the run:

  Layer 1  schema_validation  — output is valid JSON with required fields
  Layer 2  semantic_constraints — values are in bounded domains
  Layer 3  grounding           — product_id exists in the operator catalog

Graph topology:

  receive_request → call_model → done
                        │
                        └─[after slot: schema_validation,
                                       semantic_constraints,
                                       grounding]

Usage with a real model client:

    async with ModelClient(cfg) as client:
        handler = make_call_model_handler(client)
        graph = build_graph(call_model_handler=handler)

Usage in tests (inject a specific JSON payload):

    stub = make_model_stub('{"product_id": "SKU-1001", ...}')
    graph = build_graph(call_model_handler=stub)
"""

from __future__ import annotations

from typing import Any

from examples.ex01_schema_validation.guards import (
    grounding_guard,
    schema_validation_guard,
    semantic_constraints_guard,
)
from examples.ex01_schema_validation.handlers import done, make_model_stub, receive_request
from opg.core.graph import Graph, GraphBuilder

__all__ = ["build_graph", "make_model_stub"]


def build_graph(call_model_handler: Any) -> Graph:
    """Assemble the Example 1 graph.

    call_model_handler is a NodeHandler that appends the model's text response
    to state.messages. In tests, pass make_model_stub(); in production, pass a
    handler built with make_call_model_handler().
    """
    return (
        GraphBuilder(entry="receive_request")
        .node("receive_request", handler=receive_request)
        .node("call_model", handler=call_model_handler, kind="model_call")
        .node("done", handler=done)
        .edge("receive_request", "call_model")
        .edge("call_model", "done")
        .guard_after(
            "call_model",
            schema_validation_guard(),
            semantic_constraints_guard(),
            grounding_guard(),
        )
        .build()
    )
