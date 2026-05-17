"""Gate node integration tests.

Covers pause, resume, checkpoint state machine, audit events, routing, and
the iteration-counter invariant (gate nodes do not tick iterations — decision 14).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from opg.core.audit import AuditLog, read_log
from opg.core.checkpoint import CheckpointStore
from opg.core.config import OperatorConfig
from opg.core.graph import GateNode, GraphBuilder, GuardPass, GuardReject, GuardVerdict
from opg.core.orchestrator import (
    CheckpointAbandonedError,
    CheckpointConsumedError,
    CompletedOutcome,
    GraphRunner,
    GraphVersionMismatchError,
    PausedOutcome,
)
from opg.core.state import RunState

# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


class SimpleGate(GateNode):
    """Concrete GateNode for tests. The runner never calls elicit_signal()."""

    def elicit_signal(self, state: RunState) -> str:
        raise NotImplementedError


async def noop(_state: RunState) -> None:
    return None


def passing_guard(_state: RunState) -> GuardVerdict:
    return GuardPass(guard_name="passing")


def rejecting_guard(_state: RunState) -> GuardVerdict:
    return GuardReject(guard_name="rejecting", reason="blocked")


def _gate() -> SimpleGate:
    return SimpleGate(
        name="review_gate",
        signals=("approved", "rejected"),
        routing={"approved": "done", "rejected": "refuse"},
    )


def _build_graph(gate: GateNode):
    """work → review_gate; gate routes to done or refuse."""
    return (
        GraphBuilder(entry="work")
        .node("work", handler=noop)
        .node("done", handler=noop)
        .node("refuse", handler=noop)
        .gate_node(gate)
        .edge("work", "review_gate")
        .build()
    )


def _make_runner(graph, audit: AuditLog, store: CheckpointStore) -> GraphRunner:
    return GraphRunner(graph=graph, config=OperatorConfig(), audit=audit, checkpoint_store=store)


# ---------------------------------------------------------------------------
# Pause behaviour
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_pauses_at_gate_node(tmp_path: Path) -> None:
    """A run that reaches a gate node returns PausedOutcome with the right gate name."""
    gate = _gate()
    graph = _build_graph(gate)
    state = RunState()
    store = CheckpointStore.at(tmp_path / "cp")
    with AuditLog.open(state.run_id, dir=tmp_path) as audit:
        outcome = await _make_runner(graph, audit, store).run(state)

    assert isinstance(outcome, PausedOutcome)
    assert outcome.gate_name == "review_gate"
    assert outcome.signals == ("approved", "rejected")


@pytest.mark.asyncio
async def test_checkpoint_status_is_pending_after_pause(tmp_path: Path) -> None:
    """The checkpoint saved at gate-pause has status 'pending'."""
    gate = _gate()
    graph = _build_graph(gate)
    state = RunState()
    store = CheckpointStore.at(tmp_path / "cp")
    with AuditLog.open(state.run_id, dir=tmp_path) as audit:
        outcome = await _make_runner(graph, audit, store).run(state)

    assert isinstance(outcome, PausedOutcome)
    cp = store.load(outcome.checkpoint_id)
    assert cp.status == "pending"
    assert cp.consumed_at is None
    assert cp.abandoned_at is None


@pytest.mark.asyncio
async def test_gate_node_does_not_increment_iteration_counter(tmp_path: Path) -> None:
    """Gate detection runs before the cap/increment block — gate visit costs 0 iterations."""
    gate = _gate()
    graph = _build_graph(gate)
    state = RunState()
    store = CheckpointStore.at(tmp_path / "cp")
    with AuditLog.open(state.run_id, dir=tmp_path) as audit:
        outcome = await _make_runner(graph, audit, store).run(state)

    assert isinstance(outcome, PausedOutcome)
    # Only "work" incremented the counter; "review_gate" did not
    assert outcome.state.counters.iterations == 1


# ---------------------------------------------------------------------------
# Resume — happy paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resume_approved_signal_routes_to_done(tmp_path: Path) -> None:
    """Resuming with 'approved' continues the run and routes to the 'done' node."""
    gate = _gate()
    graph = _build_graph(gate)
    state = RunState()
    store = CheckpointStore.at(tmp_path / "cp")
    with AuditLog.open(state.run_id, dir=tmp_path) as audit:
        runner = _make_runner(graph, audit, store)
        paused = await runner.run(state)
        assert isinstance(paused, PausedOutcome)

        outcome = await runner.resume(paused.checkpoint_id, "approved")

    assert isinstance(outcome, CompletedOutcome)
    assert outcome.final_node == "done"


@pytest.mark.asyncio
async def test_resume_rejected_signal_routes_to_refuse(tmp_path: Path) -> None:
    """Resuming with 'rejected' routes to the 'refuse' node."""
    gate = _gate()
    graph = _build_graph(gate)
    state = RunState()
    store = CheckpointStore.at(tmp_path / "cp")
    with AuditLog.open(state.run_id, dir=tmp_path) as audit:
        runner = _make_runner(graph, audit, store)
        paused = await runner.run(state)
        assert isinstance(paused, PausedOutcome)

        outcome = await runner.resume(paused.checkpoint_id, "rejected")

    assert isinstance(outcome, CompletedOutcome)
    assert outcome.final_node == "refuse"


@pytest.mark.asyncio
async def test_checkpoint_status_is_consumed_after_resume(tmp_path: Path) -> None:
    """A checkpoint is marked consumed (not deleted) after a successful resume."""
    gate = _gate()
    graph = _build_graph(gate)
    state = RunState()
    store = CheckpointStore.at(tmp_path / "cp")
    with AuditLog.open(state.run_id, dir=tmp_path) as audit:
        runner = _make_runner(graph, audit, store)
        paused = await runner.run(state)
        assert isinstance(paused, PausedOutcome)
        await runner.resume(paused.checkpoint_id, "approved")

    cp = store.load(paused.checkpoint_id)
    assert cp.status == "consumed"
    assert cp.consumed_at is not None


@pytest.mark.asyncio
async def test_after_slot_on_post_gate_node_runs_on_resume(tmp_path: Path) -> None:
    """Guards bound to nodes after the gate execute normally during the resumed run."""
    visited: list[str] = []

    async def record_done(state: RunState) -> None:
        visited.append("done")

    def record_guard(state: RunState) -> GuardVerdict:
        visited.append("guard")
        return GuardPass(guard_name="after_guard")

    gate = _gate()
    graph = (
        GraphBuilder(entry="work")
        .node("work", handler=noop)
        .node("done", handler=record_done)
        .node("refuse", handler=noop)
        .gate_node(gate)
        .edge("work", "review_gate")
        .guard_after("done", record_guard)
        .build()
    )
    state = RunState()
    store = CheckpointStore.at(tmp_path / "cp")
    with AuditLog.open(state.run_id, dir=tmp_path) as audit:
        runner = _make_runner(graph, audit, store)
        paused = await runner.run(state)
        assert isinstance(paused, PausedOutcome)
        outcome = await runner.resume(paused.checkpoint_id, "approved")

    assert isinstance(outcome, CompletedOutcome)
    assert visited == ["done", "guard"]


# ---------------------------------------------------------------------------
# Resume — error paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resume_invalid_signal_raises_value_error(tmp_path: Path) -> None:
    """Delivering a signal not in the gate's enumeration raises ValueError."""
    gate = _gate()
    graph = _build_graph(gate)
    state = RunState()
    store = CheckpointStore.at(tmp_path / "cp")
    with AuditLog.open(state.run_id, dir=tmp_path) as audit:
        runner = _make_runner(graph, audit, store)
        paused = await runner.run(state)
        assert isinstance(paused, PausedOutcome)

        with pytest.raises(ValueError, match="not in gate"):
            await runner.resume(paused.checkpoint_id, "maybe")


@pytest.mark.asyncio
async def test_resume_consumed_checkpoint_raises(tmp_path: Path) -> None:
    """A second resume on the same checkpoint raises CheckpointConsumedError."""
    gate = _gate()
    graph = _build_graph(gate)
    state = RunState()
    store = CheckpointStore.at(tmp_path / "cp")
    with AuditLog.open(state.run_id, dir=tmp_path) as audit:
        runner = _make_runner(graph, audit, store)
        paused = await runner.run(state)
        assert isinstance(paused, PausedOutcome)

        await runner.resume(paused.checkpoint_id, "approved")

        with pytest.raises(CheckpointConsumedError):
            await runner.resume(paused.checkpoint_id, "approved")


@pytest.mark.asyncio
async def test_resume_abandoned_checkpoint_raises(tmp_path: Path) -> None:
    """Resuming an abandoned checkpoint raises CheckpointAbandonedError."""
    gate = _gate()
    graph = _build_graph(gate)
    state = RunState()
    store = CheckpointStore.at(tmp_path / "cp")
    with AuditLog.open(state.run_id, dir=tmp_path) as audit:
        runner = _make_runner(graph, audit, store)
        paused = await runner.run(state)
        assert isinstance(paused, PausedOutcome)

        runner.abandon_checkpoint(paused.checkpoint_id, reason="reviewer unavailable")

        with pytest.raises(CheckpointAbandonedError):
            await runner.resume(paused.checkpoint_id, "approved")


@pytest.mark.asyncio
async def test_resume_with_changed_graph_raises_version_mismatch(tmp_path: Path) -> None:
    """If the graph structure changes between pause and resume, resume is refused."""
    gate_a = _gate()
    graph_a = _build_graph(gate_a)

    state = RunState()
    store = CheckpointStore.at(tmp_path / "cp")
    with AuditLog.open(state.run_id, dir=tmp_path) as audit:
        runner_a = _make_runner(graph_a, audit, store)
        paused = await runner_a.run(state)
        assert isinstance(paused, PausedOutcome)

        # Build a structurally different graph (extra node changes the hash)
        gate_b = SimpleGate(
            name="review_gate",
            signals=("approved", "rejected"),
            routing={"approved": "done", "rejected": "refuse"},
        )
        graph_b = (
            GraphBuilder(entry="work")
            .node("work", handler=noop)
            .node("extra", handler=noop)  # not in graph_a
            .node("done", handler=noop)
            .node("refuse", handler=noop)
            .gate_node(gate_b)
            .edge("work", "extra")
            .edge("extra", "review_gate")
            .build()
        )
        runner_b = _make_runner(graph_b, audit, store)

        with pytest.raises(GraphVersionMismatchError):
            await runner_b.resume(paused.checkpoint_id, "approved")


# ---------------------------------------------------------------------------
# Explicit abandonment
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_abandon_checkpoint_seals_without_resumption(tmp_path: Path) -> None:
    """abandon_checkpoint() marks the checkpoint abandoned; it cannot be resumed."""
    gate = _gate()
    graph = _build_graph(gate)
    state = RunState()
    store = CheckpointStore.at(tmp_path / "cp")
    with AuditLog.open(state.run_id, dir=tmp_path) as audit:
        runner = _make_runner(graph, audit, store)
        paused = await runner.run(state)
        assert isinstance(paused, PausedOutcome)

        runner.abandon_checkpoint(
            paused.checkpoint_id, reason="timed out", abandoned_by="scheduler"
        )

    cp = store.load(paused.checkpoint_id)
    assert cp.status == "abandoned"
    assert cp.abandoned_at is not None
    assert cp.abandoned_reason == "timed out"


@pytest.mark.asyncio
async def test_double_abandon_raises(tmp_path: Path) -> None:
    """Abandoning an already-abandoned checkpoint raises CheckpointAbandonedError."""
    gate = _gate()
    graph = _build_graph(gate)
    state = RunState()
    store = CheckpointStore.at(tmp_path / "cp")
    with AuditLog.open(state.run_id, dir=tmp_path) as audit:
        runner = _make_runner(graph, audit, store)
        paused = await runner.run(state)
        assert isinstance(paused, PausedOutcome)

        runner.abandon_checkpoint(paused.checkpoint_id, reason="first")

        with pytest.raises(CheckpointAbandonedError):
            runner.abandon_checkpoint(paused.checkpoint_id, reason="second")


# ---------------------------------------------------------------------------
# Audit events
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gate_enter_event_emitted_on_pause(tmp_path: Path) -> None:
    """A gate_enter audit event is emitted when the runner reaches a gate node."""
    gate = _gate()
    graph = _build_graph(gate)
    state = RunState()
    store = CheckpointStore.at(tmp_path / "cp")
    with AuditLog.open(state.run_id, dir=tmp_path) as audit:
        await _make_runner(graph, audit, store).run(state)

    events = read_log(tmp_path / f"{state.run_id}.jsonl")
    gate_enters = [e for e in events if e.event_type == "gate_enter"]
    assert len(gate_enters) == 1
    assert gate_enters[0].payload["gate"] == "review_gate"
    assert "approved" in gate_enters[0].payload["signals"]
    assert "rejected" in gate_enters[0].payload["signals"]


@pytest.mark.asyncio
async def test_gate_signal_event_emitted_on_resume(tmp_path: Path) -> None:
    """A gate_signal audit event is emitted when resume() is called with a valid signal."""
    gate = _gate()
    graph = _build_graph(gate)
    state = RunState()
    store = CheckpointStore.at(tmp_path / "cp")
    with AuditLog.open(state.run_id, dir=tmp_path) as audit:
        runner = _make_runner(graph, audit, store)
        paused = await runner.run(state)
        assert isinstance(paused, PausedOutcome)
        await runner.resume(paused.checkpoint_id, "approved", metadata={"reviewer": "alice"})

    events = read_log(tmp_path / f"{state.run_id}.jsonl")
    gate_signals = [e for e in events if e.event_type == "gate_signal"]
    assert len(gate_signals) == 1
    assert gate_signals[0].payload["signal"] == "approved"
    assert gate_signals[0].payload["metadata"]["reviewer"] == "alice"
