"""Audit log — JSON Lines writer for run events.

Every state transition, every guard invocation, every model call, every tool
dispatch emits an event. The log is the substrate for MBT-3 (metrics) and
MBT-10 (auditability): a run can be reconstructed from the log alone.

Format: JSON Lines. One file per run, named `<run_id>.jsonl`. Each line is a
single self-contained JSON object with a `schema_version`, `event_type`, and
`timestamp`. Schema is versioned so the log format can evolve without breaking
older logs.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field
from typing_extensions import Self

AUDIT_SCHEMA_VERSION = 1

EventType = Literal[
    "run_start",
    "run_end",
    "node_enter",
    "node_exit",
    "slot_enter",
    "slot_exit",
    "guard_pass",
    "guard_reject",
    "model_call_start",
    "model_call_end",
    "tool_dispatch_start",
    "tool_dispatch_end",
    "checkpoint_save",
    "checkpoint_resume",
    "error",
]


class AuditEvent(BaseModel):
    """A single audit event.

    `payload` is event-type-specific structured data. Keeping it loosely typed
    here keeps the audit module independent of which event types exist; the
    consumer (a metrics script, a debugger UI) interprets the payload.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: int = AUDIT_SCHEMA_VERSION
    event_id: UUID = Field(default_factory=uuid4)
    run_id: UUID
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    event_type: EventType
    payload: dict[str, Any] = Field(default_factory=dict)


class AuditLog:
    """Append-only writer for audit events.

    Usage:
        with AuditLog.open(run_id, dir=Path("runs")) as log:
            log.emit("node_enter", {"node": "model_call"})

    The writer flushes after every event so a crashed process still leaves a
    readable log up to the crash point. Performance is fine for the repo's
    target volumes (tens of events per run, hundreds of runs per benchmark).
    """

    def __init__(self, run_id: UUID, path: Path) -> None:
        self.run_id = run_id
        self.path = path
        self._fh: Any = None  # opened in __enter__

    @classmethod
    def open(cls, run_id: UUID, dir: Path) -> Self:
        """Open a log file at `dir/<run_id>.jsonl`. Creates `dir` if missing."""
        dir.mkdir(parents=True, exist_ok=True)
        path = dir / f"{run_id}.jsonl"
        return cls(run_id=run_id, path=path)

    def __enter__(self) -> Self:
        self._fh = self.path.open("a", encoding="utf-8")
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        if self._fh is not None:
            self._fh.close()
            self._fh = None

    def emit(self, event_type: EventType, payload: Mapping[str, Any] | None = None) -> AuditEvent:
        """Write a single event to the log. Returns the event for caller use."""
        if self._fh is None:
            raise RuntimeError("AuditLog used outside of a context manager")
        event = AuditEvent(
            run_id=self.run_id,
            event_type=event_type,
            payload=dict(payload or {}),
        )
        # mode='json' so datetimes/UUIDs serialize cleanly
        self._fh.write(event.model_dump_json() + "\n")
        self._fh.flush()
        return event


def read_log(path: Path) -> list[AuditEvent]:
    """Read all events from a log file. Used by metrics scripts and tests."""
    events: list[AuditEvent] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            data = json.loads(line)
            events.append(AuditEvent.model_validate(data))
    return events
