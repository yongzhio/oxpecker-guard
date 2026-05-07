"""GraphBuilder: declarative graph construction and validation.

Comprehensive coverage per v0 instructions: how graphs are configured is
load-bearing for the orchestrator's reproducibility claims.
"""

from __future__ import annotations

import pytest

from opg.core.graph import GraphBuilder, GuardPass
from opg.core.state import RunState

# ---------------------------------------------------------------------------
# Helpers — minimal handlers and guards for exercising the builder
# ---------------------------------------------------------------------------


async def noop(_state: RunState) -> None:
    return None


def passing_guard(_state: RunState) -> GuardPass:
    return GuardPass(guard_name="passing")


# ---------------------------------------------------------------------------
# Happy-path construction
# ---------------------------------------------------------------------------


def test_build_minimal_graph() -> None:
    """A two-node graph with one edge and one terminal builds successfully."""
    g = (
        GraphBuilder(entry="a")
        .node("a", handler=noop)
        .node("b", handler=noop)
        .edge("a", "b")
        .terminal("b")
        .build()
    )
    assert g.entry == "a"
    assert set(g.nodes) == {"a", "b"}
    assert g.terminals == frozenset({"b"})
    assert "a" in g.edges
    assert g.edges["a"][0].target == "b"


def test_build_graph_with_kind_metadata() -> None:
    """Node kind is preserved through the build."""
    g = (
        GraphBuilder(entry="start")
        .node("start", handler=noop, kind="entrypoint")
        .node("end", handler=noop, kind="terminal")
        .edge("start", "end")
        .terminal("end")
        .build()
    )
    assert g.nodes["start"].kind == "entrypoint"
    assert g.nodes["end"].kind == "terminal"


def test_build_graph_with_guard_slots() -> None:
    """Guards bound to slots are preserved as immutable tuples."""
    g = (
        GraphBuilder(entry="a")
        .node("a", handler=noop)
        .node("b", handler=noop)
        .edge("a", "b")
        .terminal("b")
        .guard_before("a", passing_guard)
        .guard_after("a", passing_guard, passing_guard)
        .build()
    )
    assert g.slots[("a", "before")].guards == (passing_guard,)
    assert g.slots[("a", "after")].guards == (passing_guard, passing_guard)
    # slots not declared remain absent (= empty/pass-through)
    assert ("b", "before") not in g.slots


def test_build_graph_with_conditional_edge() -> None:
    def branch_left(state: RunState) -> bool:
        return state.scratch.get("go_left", False)

    g = (
        GraphBuilder(entry="decision")
        .node("decision", handler=noop)
        .node("left", handler=noop)
        .node("right", handler=noop)
        .edge("decision", "left", predicate=branch_left)
        .edge("decision", "right")  # unconditional fallthrough
        .terminal("left")
        .terminal("right")
        .build()
    )
    assert len(g.edges["decision"]) == 2
    assert g.edges["decision"][0].predicate is branch_left
    assert g.edges["decision"][1].predicate is None


# ---------------------------------------------------------------------------
# Validation failures
# ---------------------------------------------------------------------------


def test_entry_must_be_declared() -> None:
    with pytest.raises(ValueError, match="entry node"):
        GraphBuilder(entry="missing").build()


def test_edge_source_must_be_declared() -> None:
    builder = GraphBuilder(entry="a").node("a", handler=noop)
    builder.edge("ghost", "a")
    builder.terminal("a")
    with pytest.raises(ValueError, match="edge source"):
        builder.build()


def test_edge_target_must_be_declared() -> None:
    builder = GraphBuilder(entry="a").node("a", handler=noop).edge("a", "ghost").terminal("a")
    with pytest.raises(ValueError, match="edge target"):
        builder.build()


def test_terminal_must_be_declared() -> None:
    builder = GraphBuilder(entry="a").node("a", handler=noop).terminal("ghost")
    with pytest.raises(ValueError, match="terminal 'ghost' not declared"):
        builder.build()


def test_terminal_cannot_have_outgoing_edges() -> None:
    builder = (
        GraphBuilder(entry="a")
        .node("a", handler=noop)
        .node("b", handler=noop)
        .edge("a", "b")
        .edge("b", "a")  # b is supposed to be terminal but has an outgoing edge
        .terminal("b")
    )
    with pytest.raises(ValueError, match="terminal 'b' has outgoing edges"):
        builder.build()


def test_non_terminal_must_have_outgoing_edges() -> None:
    """A node that is not a terminal and has no outgoing edges is a dead-end
    and should be rejected at build time."""
    builder = (
        GraphBuilder(entry="a")
        .node("a", handler=noop)
        .node("b", handler=noop)  # b is reached but is neither terminal nor has outgoing edges
        .edge("a", "b")
    )
    with pytest.raises(ValueError, match="non-terminal node 'b'"):
        builder.build()


def test_node_redeclaration_rejected() -> None:
    builder = GraphBuilder(entry="a").node("a", handler=noop)
    with pytest.raises(ValueError, match="already declared"):
        builder.node("a", handler=noop)


def test_multiple_unconditional_edges_rejected() -> None:
    builder = (
        GraphBuilder(entry="a")
        .node("a", handler=noop)
        .node("b", handler=noop)
        .node("c", handler=noop)
        .edge("a", "b")
        .edge("a", "c")  # both unconditional from 'a'
        .terminal("b")
        .terminal("c")
    )
    with pytest.raises(ValueError, match="multiple unconditional outgoing edges"):
        builder.build()


def test_slot_must_reference_declared_node() -> None:
    builder = (
        GraphBuilder(entry="a")
        .node("a", handler=noop)
        .terminal("a")
        .guard_before("ghost", passing_guard)
    )
    with pytest.raises(ValueError, match="undeclared node 'ghost'"):
        builder.build()
