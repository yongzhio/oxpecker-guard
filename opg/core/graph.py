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
  * Edges may be conditional (a deterministic predicate over RunState).
  * Guards are also plain callables — see opg/core/guards.py for the protocol.
  * The graph is a frozen, validated data structure once built; the demo
    constructs it via GraphBuilder, then hands it to the runner.

Nothing in this module privileges any node type. "Model call" or "tool dispatch"
nodes are demo concerns — they are ordinary Nodes with handlers that happen
to call the model client or dispatch a tool.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

from opg.core.state import RunState

# ---------------------------------------------------------------------------
# Handler protocols
# ---------------------------------------------------------------------------


class NodeHandler(Protocol):
    """A node's work function. Mutates RunState in place; may return a value
    that conditional edges can inspect via `state.scratch[<key>]`.

    Returning a string is a convention: the handler may return an explicit
    next-node name to override edge resolution. This is the escape hatch for
    nodes that need direct routing (e.g. a decision node returning the chosen
    branch by name). Returning None means: use edges to resolve next.
    """

    async def __call__(self, state: RunState) -> str | None: ...


class EdgePredicate(Protocol):
    """A deterministic predicate over RunState. Used by conditional edges.
    Must be a pure function of the state — same inputs, same output."""

    def __call__(self, state: RunState) -> bool: ...


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

    Slots are addressed by (node_name, position). An empty slot (no guards)
    is a pass-through. Guards bound to a slot run in declaration order; the
    first rejection halts the slot's evaluation.
    """

    node_name: str
    position: SlotPosition
    guards: tuple[GuardFn, ...] = ()


@dataclass(frozen=True, slots=True)
class Edge:
    """A directed transition from one node to another, optionally conditional.

    If `predicate` is None the edge is unconditional. If multiple unconditional
    edges leave a node, the graph is invalid. If multiple conditional edges
    leave a node, they're evaluated in declaration order; the first whose
    predicate returns True is taken.
    """

    source: str
    target: str
    predicate: EdgePredicate | None = None
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
    """

    entry: str
    """Name of the entry node — where execution begins."""

    nodes: dict[str, Node]
    edges: dict[str, tuple[Edge, ...]]
    """Edges keyed by source node name."""

    slots: dict[tuple[str, SlotPosition], GuardSlot]
    """Slots keyed by (node_name, position). Missing key = empty slot."""

    terminals: frozenset[str]
    """Names of terminal nodes (no outgoing edges expected)."""


# ---------------------------------------------------------------------------
# Builder — the demo-facing API
# ---------------------------------------------------------------------------


class GraphBuilder:
    """Fluent builder for Graphs.

    A demo describes its graph declaratively:

        builder = GraphBuilder(entry="receive")
        builder.node("receive", handler=receive_request)
        builder.node("call_model", handler=call_model, kind="model_call")
        builder.node("done", handler=finalize, kind="terminal")
        builder.edge("receive", "call_model")
        builder.edge("call_model", "done")
        builder.terminal("done")
        builder.guard_after("call_model", schema_validate)
        graph = builder.build()

    Validation runs in build():
      * entry node exists
      * every edge's source and target exist
      * each terminal node has no outgoing edges
      * each non-terminal node has at least one outgoing edge
      * unconditional-edge uniqueness per source
    """

    def __init__(self, entry: str) -> None:
        self._entry = entry
        self._nodes: dict[str, Node] = {}
        self._edges: dict[str, list[Edge]] = {}
        self._terminals: set[str] = set()
        self._slots: dict[tuple[str, SlotPosition], list[GuardFn]] = {}

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
        predicate: EdgePredicate | None = None,
        label: str = "",
    ) -> GraphBuilder:
        self._edges.setdefault(source, []).append(
            Edge(source=source, target=target, predicate=predicate, label=label)
        )
        return self

    def terminal(self, name: str) -> GraphBuilder:
        self._terminals.add(name)
        return self

    def guard_before(self, node_name: str, *guards: GuardFn) -> GraphBuilder:
        self._slots.setdefault((node_name, "before"), []).extend(guards)
        return self

    def guard_after(self, node_name: str, *guards: GuardFn) -> GraphBuilder:
        self._slots.setdefault((node_name, "after"), []).extend(guards)
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

        # Validation: terminals are declared nodes
        for t in self._terminals:
            if t not in self._nodes:
                raise ValueError(f"terminal {t!r} not declared as a node")

        # Validation: terminals have no outgoing edges
        for t in self._terminals:
            if self._edges.get(t):
                raise ValueError(f"terminal {t!r} has outgoing edges")

        # Validation: non-terminals have outgoing edges
        for name in self._nodes:
            if name in self._terminals:
                continue
            if not self._edges.get(name):
                raise ValueError(f"non-terminal node {name!r} has no outgoing edges")

        # Validation: at most one unconditional edge per source
        for src, src_edges in self._edges.items():
            unconditional = [e for e in src_edges if e.predicate is None]
            if len(unconditional) > 1:
                raise ValueError(f"node {src!r} has multiple unconditional outgoing edges")

        # Validation: slots reference declared nodes
        for (node_name, _pos), _ in self._slots.items():
            if node_name not in self._nodes:
                raise ValueError(f"slot references undeclared node {node_name!r}")

        # Freeze
        slots: dict[tuple[str, SlotPosition], GuardSlot] = {
            key: GuardSlot(node_name=key[0], position=key[1], guards=tuple(guards))
            for key, guards in self._slots.items()
        }
        edges: dict[str, tuple[Edge, ...]] = {src: tuple(es) for src, es in self._edges.items()}
        return Graph(
            entry=self._entry,
            nodes=dict(self._nodes),
            edges=edges,
            slots=slots,
            terminals=frozenset(self._terminals),
        )
