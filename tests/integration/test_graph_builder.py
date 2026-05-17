"""GraphBuilder: declarative graph construction and validation.

Comprehensive coverage per v0 instructions: how graphs are configured is
load-bearing for the orchestrator's reproducibility claims.
"""

from __future__ import annotations

import logging

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
    """A two-node graph with one edge builds successfully."""
    g = (
        GraphBuilder(entry="a")
        .node("a", handler=noop)
        .node("b", handler=noop)
        .edge("a", "b")
        .build()
    )
    assert g.entry == "a"
    assert set(g.nodes) == {"a", "b"}
    assert "a" in g.edges
    assert g.edges["a"][0].target == "b"


def test_build_graph_with_kind_metadata() -> None:
    """Node kind is preserved through the build."""
    g = (
        GraphBuilder(entry="start")
        .node("start", handler=noop, kind="entrypoint")
        .node("end", handler=noop, kind="terminal")
        .edge("start", "end")
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
        .guard_before("a", passing_guard)
        .guard_after("a", passing_guard, passing_guard)
        .build()
    )
    assert g.before_slots["a"].guards == (passing_guard,)
    assert g.after_slots["a"].guards == (passing_guard, passing_guard)
    # slots not declared remain absent (= empty/pass-through)
    assert "b" not in g.before_slots


def test_build_graph_with_multiple_outgoing_edges() -> None:
    """A node may have multiple outgoing edges; handler must return explicit_next."""
    g = (
        GraphBuilder(entry="decision")
        .node("decision", handler=noop)
        .node("left", handler=noop)
        .node("right", handler=noop)
        .edge("decision", "left")
        .edge("decision", "right")
        .build()
    )
    assert len(g.edges["decision"]) == 2
    targets = {e.target for e in g.edges["decision"]}
    assert targets == {"left", "right"}


def test_sink_node_logs_warning(caplog: pytest.LogCaptureFixture) -> None:
    """A node with no outgoing edges logs a warning at build time (not an error)."""
    with caplog.at_level(logging.WARNING, logger="opg.core.graph"):
        GraphBuilder(entry="a").node("a", handler=noop).node("b", handler=noop).edge(
            "a", "b"
        ).build()
    assert any("no outgoing edges" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# Validation failures
# ---------------------------------------------------------------------------


def test_entry_must_be_declared() -> None:
    with pytest.raises(ValueError, match="entry node"):
        GraphBuilder(entry="missing").build()


def test_edge_source_must_be_declared() -> None:
    builder = GraphBuilder(entry="a").node("a", handler=noop)
    builder.edge("ghost", "a")
    with pytest.raises(ValueError, match="edge source"):
        builder.build()


def test_edge_target_must_be_declared() -> None:
    builder = GraphBuilder(entry="a").node("a", handler=noop).edge("a", "ghost")
    with pytest.raises(ValueError, match="edge target"):
        builder.build()


def test_node_redeclaration_rejected() -> None:
    builder = GraphBuilder(entry="a").node("a", handler=noop)
    with pytest.raises(ValueError, match="already declared"):
        builder.node("a", handler=noop)


def test_duplicate_edge_rejected() -> None:
    """Two edges with the same source and target are rejected."""
    builder = (
        GraphBuilder(entry="a")
        .node("a", handler=noop)
        .node("b", handler=noop)
        .edge("a", "b")
        .edge("a", "b")  # duplicate
    )
    with pytest.raises(ValueError, match="duplicate edge"):
        builder.build()


def test_slot_must_reference_declared_node() -> None:
    builder = GraphBuilder(entry="a").node("a", handler=noop).guard_before("ghost", passing_guard)
    with pytest.raises(ValueError, match="undeclared node 'ghost'"):
        builder.build()
