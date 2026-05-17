"""Graph types — the abstract model from §8.1 of the level-set doc.

A demo's execution is described by a Graph: a set of named Nodes connected
by Edges. Between every transition (and at graph entry/exit) there is a
GuardSlot that may be empty (pass-through) or bind one or more guards in
sequence. When a guard rejects, control transfers to a refusal terminal.

This module contains only the *types* — the runner that walks them lives
in opg/core/orchestrator.py.

Design notes:
  * Nodes have names (string IDs) so the graph can be inspected and audited.
  * Node handlers are plain async callables; the runner awaits them.
  * Edges are unconditional. Branching is expressed by handlers returning an
    explicit next-node name (the explicit_next pattern). A node with multiple
    outgoing edges must have a handler that returns explicit_next; returning
    None from such a node is a runner-detected error.
  * Guards are also plain callables — see opg/core/guards.py for the protocol.
  * The graph is a frozen, validated data structure once built; the demo
    constructs it via GraphBuilder, then hands it to the runner.

Nothing in this module privileges any node type. "Model call" or "tool dispatch"
nodes are demo concerns — they are ordinary Nodes with handlers that happen
to call the model client or dispatch a tool.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal, Protocol

from opg.core.state import RunState

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Handler protocols
# ---------------------------------------------------------------------------


class NodeHandler(Protocol):
    """A node's work function. Mutates RunState in place.

    Returning a string routes to the named node next (explicit_next pattern);
    use this for decision nodes that need direct routing. Returning None means:
    follow the single outgoing edge (error if there are multiple outgoing edges).
    """

    async def __call__(self, state: RunState) -> str | None: ...


# Guard verdicts — see also opg/core/guards.py for the GuardFn protocol


@dataclass(frozen=True, slots=True)
class GuardPass:
    """The guard allows the run to continue."""

    guard_name: str
    detail: str = ""


@dataclass(frozen=True, slots=True)
class GuardReject:
    """The guard rejects. The runner halts the run and emits a refusal."""

    guard_name: str
    reason: str
    """Human-readable rejection reason. Goes into the audit log."""


GuardVerdict = GuardPass | GuardReject


class GuardFn(Protocol):
    """A deterministic guard. Pure function of RunState plus its own config.

    Guards live in opg/guards/ and are registered with the orchestrator.
    A guard MUST NOT call an LLM. A guard MUST NOT have side effects on
    external systems. Reading state is fine; mutating state is forbidden.
    """

    def __call__(self, state: RunState) -> GuardVerdict: ...


# ---------------------------------------------------------------------------
# Slots, edges, nodes
# ---------------------------------------------------------------------------

SlotPosition = Literal["before", "after"]


@dataclass(frozen=True, slots=True)
class GuardSlot:
    """A configurable slot at which guards may be bound.

    Position (before/after) is implicit in which graph collection the slot
    lives in — `Graph.before_slots` or `Graph.after_slots`. An empty slot
    (no guards) is a pass-through. Guards run in declaration order; the
    first rejection halts the slot's evaluation.
    """

    node_name: str
    guards: tuple[GuardFn, ...] = ()


@dataclass(frozen=True, slots=True)
class Edge:
    """A directed transition from one node to another.

    Edges are unconditional. A node with multiple outgoing edges requires its
    handler to return an explicit next-node name; the runner raises if the
    handler returns None from a multi-edge node.
    """

    source: str
    target: str
    label: str = ""
    """Optional human-readable label used in audit events and diagrams."""


@dataclass(frozen=True, slots=True)
class Node:
    """A unit of work in the graph.

    `kind` is a short string the runner emits in audit events; it lets readers
    of an audit trail tell node types apart at a glance. The runner does not
    privilege any kind value — they're descriptive labels.
    """

    name: str
    handler: NodeHandler
    kind: str = "generic"


# ---------------------------------------------------------------------------
# Graph: the validated, immutable structure
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Graph:
    """A validated graph ready for execution.

    Construct via GraphBuilder; do not instantiate directly outside that path
    (validation happens in the builder).

    Termination is detected dynamically: the run completes when control reaches
    a node with no outgoing edges whose handler returns None.
    """

    entry: str
    """Name of the entry node — where execution begins."""

    nodes: dict[str, Node]
    edges: dict[str, tuple[Edge, ...]]
    """Edges keyed by source node name."""

    before_slots: dict[str, GuardSlot]
    """Guards that run before the node's handler. Keyed by node_name."""

    after_slots: dict[str, GuardSlot]
    """Guards that run after the node's handler. Keyed by node_name."""


# ---------------------------------------------------------------------------
# Builder — the demo-facing API
# ---------------------------------------------------------------------------


class GraphBuilder:
    """Fluent builder for Graphs.

    A demo describes its graph declaratively:

        builder = GraphBuilder(entry="receive")
        builder.node("receive", handler=receive_request)
        builder.node("call_model", handler=call_model, kind="model_call")
        builder.node("done", handler=finalize)
        builder.edge("receive", "call_model")
        builder.edge("call_model", "done")
        builder.guard_after("call_model", schema_validate)
        graph = builder.build()

    Termination is detected dynamically at runtime: the run completes when it
    reaches a node with no outgoing edges whose handler returns None. Nodes
    with no outgoing edges emit a build-time warning, not an error.

    Validation runs in build():
      * entry node exists
      * every edge's source and target exist
      * no duplicate edges (same source and target)
      * slots reference declared nodes
    """

    def __init__(self, entry: str) -> None:
        self._entry = entry
        self._nodes: dict[str, Node] = {}
        self._edges: dict[str, list[Edge]] = {}
        self._before_slots: dict[str, list[GuardFn]] = {}
        self._after_slots: dict[str, list[GuardFn]] = {}

    def node(
        self,
        name: str,
        handler: NodeHandler,
        kind: str = "generic",
    ) -> GraphBuilder:
        if name in self._nodes:
            raise ValueError(f"node {name!r} already declared")
        self._nodes[name] = Node(name=name, handler=handler, kind=kind)
        return self

    def edge(
        self,
        source: str,
        target: str,
        label: str = "",
    ) -> GraphBuilder:
        self._edges.setdefault(source, []).append(Edge(source=source, target=target, label=label))
        return self

    def guard_before(self, node_name: str, *guards: GuardFn) -> GraphBuilder:
        self._before_slots.setdefault(node_name, []).extend(guards)
        return self

    def guard_after(self, node_name: str, *guards: GuardFn) -> GraphBuilder:
        self._after_slots.setdefault(node_name, []).extend(guards)
        return self

    def build(self) -> Graph:
        # Validation: entry exists
        if self._entry not in self._nodes:
            raise ValueError(f"entry node {self._entry!r} not declared")

        # Validation: edges reference declared nodes
        for src, src_edges in self._edges.items():
            if src not in self._nodes:
                raise ValueError(f"edge source {src!r} not declared")
            for e in src_edges:
                if e.target not in self._nodes:
                    raise ValueError(f"edge target {e.target!r} not declared (from {src!r})")

        # Validation: at most one edge per source-target pair
        for src, src_edges in self._edges.items():
            targets = [e.target for e in src_edges]
            seen: set[str] = set()
            for t in targets:
                if t in seen:
                    raise ValueError(f"duplicate edge from {src!r} to {t!r}")
                seen.add(t)

        # Validation: slots reference declared nodes
        for node_name in (*self._before_slots, *self._after_slots):
            if node_name not in self._nodes:
                raise ValueError(f"slot references undeclared node {node_name!r}")

        # Integrity notices: nodes with no outgoing edges are potential sinks
        for name in self._nodes:
            if not self._edges.get(name):
                _log.warning("node %r has no outgoing edges; run will complete there", name)

        # Freeze
        before_slots = {
            name: GuardSlot(node_name=name, guards=tuple(guards))
            for name, guards in self._before_slots.items()
        }
        after_slots = {
            name: GuardSlot(node_name=name, guards=tuple(guards))
            for name, guards in self._after_slots.items()
        }
        edges: dict[str, tuple[Edge, ...]] = {src: tuple(es) for src, es in self._edges.items()}
        return Graph(
            entry=self._entry,
            nodes=dict(self._nodes),
            edges=edges,
            before_slots=before_slots,
            after_slots=after_slots,
        )
