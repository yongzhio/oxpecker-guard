"""Checkpoint save / load round trip."""

from __future__ import annotations

from pathlib import Path

from opg.core.checkpoint import CHECKPOINT_SCHEMA_VERSION, Checkpoint, CheckpointStore
from opg.core.state import Message, RunState


def _make_checkpoint(**kwargs) -> Checkpoint:
    defaults = dict(
        run_id=RunState().run_id,
        paused_at_node="gate",
        gate_signals=("approved", "rejected"),
        graph_hash="abc123",
        state=RunState(),
    )
    defaults.update(kwargs)
    return Checkpoint(**defaults)


def test_checkpoint_roundtrip(tmp_path: Path) -> None:
    store = CheckpointStore.at(tmp_path)
    state = RunState(user_id="alice")
    state.append_message(Message(role="user", content="hi"))
    state.counters.iterations = 4

    cp = _make_checkpoint(
        run_id=state.run_id,
        paused_at_node="approval_gate",
        gate_signals=("approved", "rejected"),
        graph_hash="deadbeef",
        state=state,
        note="ready for review",
    )
    path = store.save(cp)
    assert path.exists()

    restored = store.load(cp.checkpoint_id)
    assert restored.run_id == state.run_id
    assert restored.paused_at_node == "approval_gate"
    assert restored.gate_signals == ("approved", "rejected")
    assert restored.graph_hash == "deadbeef"
    assert restored.status == "pending"
    assert restored.consumed_at is None
    assert restored.state.counters.iterations == 4
    assert restored.state.messages[0].content == "hi"
    assert restored.schema_version == CHECKPOINT_SCHEMA_VERSION


def test_checkpoint_default_status_is_pending() -> None:
    cp = _make_checkpoint()
    assert cp.status == "pending"
    assert cp.consumed_at is None
    assert cp.abandoned_at is None
    assert cp.abandoned_reason is None
