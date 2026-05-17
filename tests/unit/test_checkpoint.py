"""Checkpoint save / load round trip and seal state-machine tests."""

from __future__ import annotations

from pathlib import Path

from opg.core.checkpoint import (
    CHECKPOINT_SCHEMA_VERSION,
    Checkpoint,
    CheckpointAbandonedError,
    CheckpointConsumedError,
    CheckpointStore,
)
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


# ---------------------------------------------------------------------------
# Seal state-machine tests
# ---------------------------------------------------------------------------


def test_seal_consumed_marks_pending_checkpoint_consumed(tmp_path: Path) -> None:
    store = CheckpointStore.at(tmp_path)
    cp = _make_checkpoint()
    store.save(cp)

    updated = store.seal_consumed(cp.checkpoint_id)

    assert updated.status == "consumed"
    assert updated.consumed_at is not None
    # Persisted on disk
    assert store.load(cp.checkpoint_id).status == "consumed"


def test_seal_consumed_on_consumed_raises(tmp_path: Path) -> None:
    store = CheckpointStore.at(tmp_path)
    cp = _make_checkpoint()
    store.save(cp)
    store.seal_consumed(cp.checkpoint_id)

    import pytest

    with pytest.raises(CheckpointConsumedError):
        store.seal_consumed(cp.checkpoint_id)


def test_seal_consumed_on_abandoned_raises(tmp_path: Path) -> None:
    store = CheckpointStore.at(tmp_path)
    cp = _make_checkpoint()
    store.save(cp)
    store.seal_abandoned(cp.checkpoint_id, reason="timed out")

    import pytest

    with pytest.raises(CheckpointAbandonedError):
        store.seal_consumed(cp.checkpoint_id)


def test_seal_abandoned_marks_pending_checkpoint_abandoned(tmp_path: Path) -> None:
    store = CheckpointStore.at(tmp_path)
    cp = _make_checkpoint()
    store.save(cp)

    updated = store.seal_abandoned(cp.checkpoint_id, reason="reviewer unavailable")

    assert updated.status == "abandoned"
    assert updated.abandoned_at is not None
    assert updated.abandoned_reason == "reviewer unavailable"
    # Persisted on disk
    on_disk = store.load(cp.checkpoint_id)
    assert on_disk.status == "abandoned"
    assert on_disk.abandoned_reason == "reviewer unavailable"


def test_seal_abandoned_on_consumed_raises(tmp_path: Path) -> None:
    store = CheckpointStore.at(tmp_path)
    cp = _make_checkpoint()
    store.save(cp)
    store.seal_consumed(cp.checkpoint_id)

    import pytest

    with pytest.raises(CheckpointConsumedError):
        store.seal_abandoned(cp.checkpoint_id, reason="late")


def test_seal_abandoned_on_abandoned_raises(tmp_path: Path) -> None:
    store = CheckpointStore.at(tmp_path)
    cp = _make_checkpoint()
    store.save(cp)
    store.seal_abandoned(cp.checkpoint_id, reason="first")

    import pytest

    with pytest.raises(CheckpointAbandonedError):
        store.seal_abandoned(cp.checkpoint_id, reason="second")
