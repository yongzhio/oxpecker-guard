"""Audit log: write, flush, read-back."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from opg.core.audit import AUDIT_SCHEMA_VERSION, AuditLog, read_log


def test_audit_writes_and_reads_roundtrip(tmp_path: Path) -> None:
    run_id = uuid4()
    with AuditLog.open(run_id, dir=tmp_path) as log:
        log.emit("run_start", {"foo": "bar"})
        log.emit("node_enter", {"node": "n1"})
        log.emit("run_end", {"reason": "completed"})

    events = read_log(tmp_path / f"{run_id}.jsonl")
    assert len(events) == 3
    assert [e.event_type for e in events] == ["run_start", "node_enter", "run_end"]
    assert events[0].payload == {"foo": "bar"}
    assert all(e.run_id == run_id for e in events)
    assert all(e.schema_version == AUDIT_SCHEMA_VERSION for e in events)


def test_audit_emit_outside_context_raises() -> None:
    log = AuditLog.open(uuid4(), dir=Path("/tmp/never-used"))
    # Did not enter the context manager → no file handle
    try:
        log.emit("run_start")
    except RuntimeError as exc:
        assert "context manager" in str(exc)
    else:
        raise AssertionError("expected RuntimeError")
