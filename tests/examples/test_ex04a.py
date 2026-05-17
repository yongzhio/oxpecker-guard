"""Example 4a integration tests: tool allowlist and blast-radius gate.

Exercises all paths through the Example 4a graph:
  - Low-risk allowlisted tool → direct dispatch, no gate
  - High-risk allowlisted tool → gate pauses; approved → dispatch
  - High-risk allowlisted tool → gate pauses; rejected → refuse
  - Tool not on allowlist → allowlist guard rejects
  - Audit event sequence for a full high-risk approved flow
"""

from __future__ import annotations

from pathlib import Path

import pytest

from examples.ex04a_tool_allowlist.graph import (
    HIGH_BLAST_RADIUS_TOOLS,
    build_graph,
    make_model_stub,
)
from opg.core.audit import AuditLog, read_log
from opg.core.checkpoint import CheckpointStore
from opg.core.config import OperatorConfig
from opg.core.orchestrator import (
    CompletedOutcome,
    GraphRunner,
    PausedOutcome,
    RejectedOutcome,
)
from opg.core.state import Message, RunState

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_runner(graph, audit: AuditLog, store: CheckpointStore) -> GraphRunner:
    return GraphRunner(graph=graph, config=OperatorConfig(), audit=audit, checkpoint_store=store)


def _seed_state() -> RunState:
    state = RunState(user_id="alice")
    state.append_message(Message(role="user", content="Please read /etc/hosts"))
    return state


# ---------------------------------------------------------------------------
# Layer 4: allowlist guard
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tool_not_on_allowlist_is_rejected(tmp_path: Path) -> None:
    """A tool call for a name not on the allowlist halts the run before dispatch."""
    graph = build_graph(call_model_handler=make_model_stub("exec_arbitrary_code"))
    state = _seed_state()
    store = CheckpointStore.at(tmp_path / "cp")
    with AuditLog.open(state.run_id, dir=tmp_path) as audit:
        outcome = await _make_runner(graph, audit, store).run(state)

    assert isinstance(outcome, RejectedOutcome)
    assert outcome.guard_name == "tool_allowlist"
    assert "exec_arbitrary_code" in outcome.reason
    assert outcome.rejected_at_node == "call_model"
    assert outcome.rejected_at_position == "after"


# ---------------------------------------------------------------------------
# Layer 5: blast-radius routing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_low_risk_tool_dispatches_without_gate(tmp_path: Path) -> None:
    """An allowlisted low-risk tool completes without pausing at the gate."""
    low_risk = next(t for t in ("read_file", "list_directory") if t not in HIGH_BLAST_RADIUS_TOOLS)
    graph = build_graph(call_model_handler=make_model_stub(low_risk))
    state = _seed_state()
    store = CheckpointStore.at(tmp_path / "cp")
    with AuditLog.open(state.run_id, dir=tmp_path) as audit:
        outcome = await _make_runner(graph, audit, store).run(state)

    assert isinstance(outcome, CompletedOutcome)
    assert outcome.final_node == "done"
    # Tool was dispatched: a tool-role message was appended
    tool_msgs = [m for m in outcome.state.messages if m.role == "tool"]
    assert len(tool_msgs) == 1
    assert low_risk in (tool_msgs[0].tool_result.content if tool_msgs[0].tool_result else "")


@pytest.mark.asyncio
async def test_high_risk_tool_pauses_at_gate(tmp_path: Path) -> None:
    """An allowlisted high-risk tool causes the run to pause at the approval gate."""
    graph = build_graph(call_model_handler=make_model_stub("delete_file"))
    state = _seed_state()
    store = CheckpointStore.at(tmp_path / "cp")
    with AuditLog.open(state.run_id, dir=tmp_path) as audit:
        outcome = await _make_runner(graph, audit, store).run(state)

    assert isinstance(outcome, PausedOutcome)
    assert outcome.gate_name == "approval_gate"
    assert "approved" in outcome.signals
    assert "rejected" in outcome.signals


# ---------------------------------------------------------------------------
# Layer 6: HITL gate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_approval_routes_to_dispatch_approved(tmp_path: Path) -> None:
    """Resuming with 'approved' dispatches the high-risk tool and completes normally."""
    graph = build_graph(call_model_handler=make_model_stub("send_email"))
    state = _seed_state()
    store = CheckpointStore.at(tmp_path / "cp")
    with AuditLog.open(state.run_id, dir=tmp_path) as audit:
        runner = _make_runner(graph, audit, store)
        paused = await runner.run(state)
        assert isinstance(paused, PausedOutcome)

        outcome = await runner.resume(paused.checkpoint_id, "approved")

    assert isinstance(outcome, CompletedOutcome)
    assert outcome.final_node == "done"
    tool_msgs = [m for m in outcome.state.messages if m.role == "tool"]
    assert any("[approved]" in (m.tool_result.content if m.tool_result else "") for m in tool_msgs)


@pytest.mark.asyncio
async def test_rejection_routes_to_refuse_node(tmp_path: Path) -> None:
    """Resuming with 'rejected' routes to the refuse terminal."""
    graph = build_graph(call_model_handler=make_model_stub("write_file"))
    state = _seed_state()
    store = CheckpointStore.at(tmp_path / "cp")
    with AuditLog.open(state.run_id, dir=tmp_path) as audit:
        runner = _make_runner(graph, audit, store)
        paused = await runner.run(state)
        assert isinstance(paused, PausedOutcome)

        outcome = await runner.resume(paused.checkpoint_id, "rejected")

    assert isinstance(outcome, CompletedOutcome)
    assert outcome.final_node == "refuse"
    # No tool dispatch happened
    tool_msgs = [m for m in outcome.state.messages if m.role == "tool"]
    assert len(tool_msgs) == 0


# ---------------------------------------------------------------------------
# Audit trace
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_full_high_risk_approved_audit_trace(tmp_path: Path) -> None:
    """High-risk approved flow: verify the audit log contains all expected events."""
    graph = build_graph(call_model_handler=make_model_stub("delete_file"))
    state = _seed_state()
    store = CheckpointStore.at(tmp_path / "cp")
    with AuditLog.open(state.run_id, dir=tmp_path) as audit:
        runner = _make_runner(graph, audit, store)
        paused = await runner.run(state)
        assert isinstance(paused, PausedOutcome)
        await runner.resume(paused.checkpoint_id, "approved", metadata={"reviewer": "ops-team"})

    events = read_log(tmp_path / f"{state.run_id}.jsonl")
    types = [e.event_type for e in events]

    assert types[0] == "run_start"
    assert types[-1] == "run_end"
    assert "guard_pass" in types  # allowlist guard passed
    assert "gate_enter" in types  # hit the approval gate
    assert "checkpoint_save" in types  # checkpoint written
    assert "gate_signal" in types  # resume with signal
    assert "checkpoint_resume" in types  # checkpoint consumed

    # guard_pass should come before gate_enter
    assert types.index("guard_pass") < types.index("gate_enter")

    # gate_signal payload carries the reviewer metadata
    signal_event = next(e for e in events if e.event_type == "gate_signal")
    assert signal_event.payload["signal"] == "approved"
    assert signal_event.payload["metadata"]["reviewer"] == "ops-team"
