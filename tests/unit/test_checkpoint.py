"""Checkpoint save / load round trip."""

from __future__ import annotations

from pathlib import Path

from opg.core.checkpoint import Checkpoint, CheckpointStore
from opg.core.state import Message, RunState


def test_checkpoint_roundtrip(tmp_path: Path) -> None:
    store = CheckpointStore.at(tmp_path)
    state = RunState(user_id="alice")
    state.append_message(Message(role="user", content="hi"))
    state.counters.iterations = 4

    cp = Checkpoint(run_id=state.run_id, paused_at_node="approval", state=state, note="ready")
    path = store.save(cp)
    assert path.exists()

    restored = store.load(cp.checkpoint_id)
    assert restored.run_id == state.run_id
    assert restored.paused_at_node == "approval"
    assert restored.state.counters.iterations == 4
    assert restored.state.messages[0].content == "hi"
