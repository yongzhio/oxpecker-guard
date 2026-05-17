"""Orchestrator runtime flow tests.

Comprehensive coverage of how the GraphRunner walks a Graph: node execution,
guard slots, explicit-next routing, terminals, error propagation, hard caps,
and audit-log emission.

Per v0 instructions, this is where test coverage matters most.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from opg.core.audit import AuditLog, read_log
from opg.core.config import LimitsConfig, OperatorConfig
from opg.core.graph import (
    GraphBuilder,
    GuardPass,
    GuardReject,
    GuardVerdict,
)
from opg.core.orchestrator import (
    CapExceededOutcome,
    CompletedOutcome,
    ErrorOutcome,
    GraphRunner,
    RejectedOutcome,
)
from opg.core.state import RunState

# ---------------------------------------------------------------------------
# Helpers — minimal handlers and guards
# ---------------------------------------------------------------------------


async def noop_handler(_state: RunState) -> None:
    return None


def make_recording_handler(name: str, log: list[str]):
    async def _handler(_state: RunState) -> None:
        log.append(name)
        return None

    return _handler


def make_passing_guard(name: str):
    def _guard(_state: RunState) -> GuardVerdict:
        return GuardPass(guard_name=name)

    return _guard


def make_rejecting_guard(name: str, reason: str = "blocked"):
    def _guard(_state: RunState) -> GuardVerdict:
        return GuardReject(guard_name=name, reason=reason)

    return _guard


def make_runner(graph, audit, *, max_iterations: int = 20) -> GraphRunner:
    cfg = OperatorConfig(limits=LimitsConfig(max_iterations=max_iterations))
    return GraphRunner(graph=graph, config=cfg, audit=audit)


# ---------------------------------------------------------------------------
# Linear flow
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_linear_two_node_run_completes(tmp_path: Path) -> None:
    """Simplest happy path: a → b (sink). All nodes execute, run completes."""
    visited: list[str] = []
    g = (
        GraphBuilder(entry="a")
        .node("a", handler=make_recording_handler("a", visited))
        .node("b", handler=make_recording_handler("b", visited))
        .edge("a", "b")
        .build()
    )
    state = RunState()
    with AuditLog.open(state.run_id, dir=tmp_path) as audit:
        outcome = await make_runner(g, audit).run(state)

    assert isinstance(outcome, CompletedOutcome)
    assert outcome.final_node == "b"
    assert visited == ["a", "b"]


# ---------------------------------------------------------------------------
# Guard slots
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_passing_guard_in_before_slot_does_not_block(tmp_path: Path) -> None:
    visited: list[str] = []
    g = (
        GraphBuilder(entry="a")
        .node("a", handler=make_recording_handler("a", visited))
        .guard_before("a", make_passing_guard("ok"))
        .build()
    )
    state = RunState()
    with AuditLog.open(state.run_id, dir=tmp_path) as audit:
        outcome = await make_runner(g, audit).run(state)

    assert isinstance(outcome, CompletedOutcome)
    assert visited == ["a"]


@pytest.mark.asyncio
async def test_rejecting_guard_in_before_slot_halts_before_node(tmp_path: Path) -> None:
    """A 'before' rejection means the node handler never runs."""
    visited: list[str] = []
    g = (
        GraphBuilder(entry="a")
        .node("a", handler=make_recording_handler("a", visited))
        .guard_before("a", make_rejecting_guard("blocker", "no go"))
        .build()
    )
    state = RunState()
    with AuditLog.open(state.run_id, dir=tmp_path) as audit:
        outcome = await make_runner(g, audit).run(state)

    assert isinstance(outcome, RejectedOutcome)
    assert outcome.guard_name == "blocker"
    assert outcome.reason == "no go"
    assert outcome.rejected_at_node == "a"
    assert outcome.rejected_at_position == "before"
    assert visited == []  # handler never ran


@pytest.mark.asyncio
async def test_rejecting_guard_in_after_slot_halts_after_node(tmp_path: Path) -> None:
    """An 'after' rejection means the node handler did run, but the next node won't."""
    visited: list[str] = []
    g = (
        GraphBuilder(entry="a")
        .node("a", handler=make_recording_handler("a", visited))
        .node("b", handler=make_recording_handler("b", visited))
        .edge("a", "b")
        .guard_after("a", make_rejecting_guard("blocker"))
        .build()
    )
    state = RunState()
    with AuditLog.open(state.run_id, dir=tmp_path) as audit:
        outcome = await make_runner(g, audit).run(state)

    assert isinstance(outcome, RejectedOutcome)
    assert outcome.rejected_at_position == "after"
    assert visited == ["a"]  # 'a' ran, 'b' did not


@pytest.mark.asyncio
async def test_multiple_guards_in_one_slot_run_in_order(tmp_path: Path) -> None:
    """Guards in a slot run in declaration order; first rejection wins."""
    g = (
        GraphBuilder(entry="a")
        .node("a", handler=noop_handler)
        .guard_before(
            "a",
            make_passing_guard("first"),
            make_rejecting_guard("second"),
            make_rejecting_guard("third"),  # never reached
        )
        .build()
    )
    state = RunState()
    with AuditLog.open(state.run_id, dir=tmp_path) as audit:
        outcome = await make_runner(g, audit).run(state)

    assert isinstance(outcome, RejectedOutcome)
    assert outcome.guard_name == "second"


@pytest.mark.asyncio
async def test_empty_slot_is_pass_through(tmp_path: Path) -> None:
    """A node with no guards in either slot runs without slot events."""
    visited: list[str] = []
    g = GraphBuilder(entry="a").node("a", handler=make_recording_handler("a", visited)).build()
    state = RunState()
    with AuditLog.open(state.run_id, dir=tmp_path) as audit:
        await make_runner(g, audit).run(state)

    events = read_log(tmp_path / f"{state.run_id}.jsonl")
    types = [e.event_type for e in events]
    assert "slot_enter" not in types
    assert "slot_exit" not in types
    assert visited == ["a"]


# ---------------------------------------------------------------------------
# Explicit-next routing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_explicit_next_routes_to_chosen_branch(tmp_path: Path) -> None:
    """A decision handler returning an explicit next-node name routes there."""
    visited: list[str] = []

    async def decide(_state: RunState) -> str:
        return "left"

    g = (
        GraphBuilder(entry="decide")
        .node("decide", handler=decide)
        .node("left", handler=make_recording_handler("left", visited))
        .node("right", handler=make_recording_handler("right", visited))
        .edge("decide", "left")
        .edge("decide", "right")
        .build()
    )
    state = RunState()
    with AuditLog.open(state.run_id, dir=tmp_path) as audit:
        outcome = await make_runner(g, audit).run(state)

    assert isinstance(outcome, CompletedOutcome)
    assert outcome.final_node == "left"
    assert visited == ["left"]


@pytest.mark.asyncio
async def test_single_outgoing_edge_taken_when_handler_returns_none(tmp_path: Path) -> None:
    """A handler returning None on a single-edge node takes the one edge."""
    visited: list[str] = []

    g = (
        GraphBuilder(entry="a")
        .node("a", handler=make_recording_handler("a", visited))
        .node("b", handler=make_recording_handler("b", visited))
        .edge("a", "b")
        .build()
    )
    state = RunState()
    with AuditLog.open(state.run_id, dir=tmp_path) as audit:
        outcome = await make_runner(g, audit).run(state)

    assert isinstance(outcome, CompletedOutcome)
    assert outcome.final_node == "b"
    assert visited == ["a", "b"]


@pytest.mark.asyncio
async def test_multiple_edges_with_none_handler_returns_error_outcome(tmp_path: Path) -> None:
    """A handler returning None on a multi-edge node is a graph-design error."""
    g = (
        GraphBuilder(entry="decide")
        .node("decide", handler=noop_handler)
        .node("left", handler=noop_handler)
        .node("right", handler=noop_handler)
        .edge("decide", "left")
        .edge("decide", "right")
        .build()
    )
    state = RunState()
    with AuditLog.open(state.run_id, dir=tmp_path) as audit:
        outcome = await make_runner(g, audit).run(state)

    assert isinstance(outcome, ErrorOutcome)
    assert outcome.node == "decide"
    assert "multiple outgoing edges" in outcome.message


# ---------------------------------------------------------------------------
# Loops and cycles
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_loop_terminates_via_explicit_next(tmp_path: Path) -> None:
    """A graph with a cycle where the handler eventually routes to the exit node."""
    visit_count = {"count": 0}

    async def loop_body(_state: RunState) -> str:
        visit_count["count"] += 1
        return "end" if visit_count["count"] >= 3 else "loop"

    g = (
        GraphBuilder(entry="loop")
        .node("loop", handler=loop_body)
        .node("end", handler=noop_handler)
        .edge("loop", "end")
        .edge("loop", "loop")
        .build()
    )
    state = RunState()
    with AuditLog.open(state.run_id, dir=tmp_path) as audit:
        outcome = await make_runner(g, audit).run(state)

    assert isinstance(outcome, CompletedOutcome)
    assert outcome.final_node == "end"
    assert visit_count["count"] == 3


@pytest.mark.asyncio
async def test_iteration_cap_terminates_runaway_loop(tmp_path: Path) -> None:
    """Hard cap stops a loop that would otherwise never exit."""

    async def loop_body(_state: RunState) -> None:
        return None

    g = GraphBuilder(entry="loop").node("loop", handler=loop_body).edge("loop", "loop").build()
    state = RunState()
    with AuditLog.open(state.run_id, dir=tmp_path) as audit:
        outcome = await make_runner(g, audit, max_iterations=5).run(state)

    assert isinstance(outcome, CapExceededOutcome)
    assert outcome.cap_name == "max_iterations"
    assert state.counters.iterations == 5


# ---------------------------------------------------------------------------
# Explicit next override
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handler_explicit_next_overrides_edges(tmp_path: Path) -> None:
    """A handler returning a node name takes that branch directly."""
    visited: list[str] = []

    async def decider(_state: RunState) -> str:
        visited.append("decider")
        return "right"

    g = (
        GraphBuilder(entry="decide")
        .node("decide", handler=decider)
        .node("left", handler=make_recording_handler("left", visited))
        .node("right", handler=make_recording_handler("right", visited))
        .edge("decide", "left")  # would be taken if not overridden
        .build()
    )
    state = RunState()
    with AuditLog.open(state.run_id, dir=tmp_path) as audit:
        outcome = await make_runner(g, audit).run(state)

    assert isinstance(outcome, CompletedOutcome)
    assert outcome.final_node == "right"
    assert visited == ["decider", "right"]


@pytest.mark.asyncio
async def test_handler_explicit_next_to_unknown_node_errors(tmp_path: Path) -> None:
    async def bad(_state: RunState) -> str:
        return "ghost"

    g = (
        GraphBuilder(entry="a")
        .node("a", handler=bad)
        .node("b", handler=noop_handler)
        .edge("a", "b")
        .build()
    )
    state = RunState()
    with AuditLog.open(state.run_id, dir=tmp_path) as audit:
        outcome = await make_runner(g, audit).run(state)

    assert isinstance(outcome, ErrorOutcome)
    assert "not a declared node" in outcome.message
    assert outcome.node == "a"


# ---------------------------------------------------------------------------
# Errors in handlers
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handler_exception_produces_error_outcome(tmp_path: Path) -> None:
    async def explode(_state: RunState) -> None:
        raise ValueError("boom")

    g = GraphBuilder(entry="bad").node("bad", handler=explode).build()
    state = RunState()
    with AuditLog.open(state.run_id, dir=tmp_path) as audit:
        outcome = await make_runner(g, audit).run(state)

    assert isinstance(outcome, ErrorOutcome)
    assert outcome.error_type == "ValueError"
    assert outcome.message == "boom"
    assert outcome.node == "bad"


# ---------------------------------------------------------------------------
# Audit log content
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_audit_log_records_full_run_sequence(tmp_path: Path) -> None:
    """Run a small graph and verify the audit log contains the expected events
    in the expected order."""
    g = (
        GraphBuilder(entry="a")
        .node("a", handler=noop_handler, kind="custom_kind")
        .node("b", handler=noop_handler)
        .edge("a", "b")
        .guard_before("a", make_passing_guard("g1"))
        .guard_after("b", make_passing_guard("g2"))
        .build()
    )
    state = RunState(user_id="alice")
    with AuditLog.open(state.run_id, dir=tmp_path) as audit:
        outcome = await make_runner(g, audit).run(state)
    assert isinstance(outcome, CompletedOutcome)

    events = read_log(tmp_path / f"{state.run_id}.jsonl")
    types = [e.event_type for e in events]

    # Spot checks on the sequence shape
    assert types[0] == "run_start"
    assert types[-1] == "run_end"
    # 'a' has a 'before' slot with one passing guard
    assert "slot_enter" in types
    assert "guard_pass" in types
    # 'a' (custom kind) is recorded with its kind
    a_enter = next(
        e for e in events if e.event_type == "node_enter" and e.payload.get("node") == "a"
    )
    assert a_enter.payload["kind"] == "custom_kind"


@pytest.mark.asyncio
async def test_audit_log_records_rejection_reason(tmp_path: Path) -> None:
    g = (
        GraphBuilder(entry="a")
        .node("a", handler=noop_handler)
        .guard_before("a", make_rejecting_guard("strict", "policy violation"))
        .build()
    )
    state = RunState()
    with AuditLog.open(state.run_id, dir=tmp_path) as audit:
        await make_runner(g, audit).run(state)

    events = read_log(tmp_path / f"{state.run_id}.jsonl")
    reject = next(e for e in events if e.event_type == "guard_reject")
    assert reject.payload["guard"] == "strict"
    assert reject.payload["reason"] == "policy violation"

    final = events[-1]
    assert final.event_type == "run_end"
    assert final.payload["reason"] == "guard_reject"


# ---------------------------------------------------------------------------
# Reproducibility — given the same inputs, two runs produce equivalent traces
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_runs_with_same_inputs_produce_same_event_types(tmp_path: Path) -> None:
    """Determinism check: two runs of the same graph with the same seed state
    should produce the same SEQUENCE of event types. (Run IDs and timestamps
    differ, which is correct.)"""

    def make_graph():
        return (
            GraphBuilder(entry="a")
            .node("a", handler=noop_handler)
            .node("b", handler=noop_handler)
            .edge("a", "b")
            .guard_after("a", make_passing_guard("g"))
            .build()
        )

    async def collect_event_types(graph) -> list[str]:
        state = RunState()
        path = tmp_path / f"{state.run_id}.jsonl"
        with AuditLog.open(state.run_id, dir=tmp_path) as audit:
            await make_runner(graph, audit).run(state)
        return [e.event_type for e in read_log(path)]

    types1 = await collect_event_types(make_graph())
    types2 = await collect_event_types(make_graph())
    assert types1 == types2
