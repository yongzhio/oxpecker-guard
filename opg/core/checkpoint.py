"""Checkpoint store — durable RunState snapshots for gate-node pauses.

When a graph reaches a gate node, the orchestrator serializes the current
RunState to disk and exits with a PausedOutcome. A caller (CLI, web handler,
MFA webhook, test harness) delivers a signal via runner.resume(), which loads
the checkpoint and continues the run.

v0 implementation: JSON serialization, one file per checkpoint, named by
checkpoint_id. SQLite-backed event log is on the deferred list and can replace
this without changing the API.

Checkpoint state model (decision 17):
  pending   — can be resumed exactly once
  consumed  — resumed; audit-preserved; no further resumption
  abandoned — explicitly sealed without resumption; audit-preserved; no resumption
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field
from typing_extensions import Self

from opg.core.state import RunState

CHECKPOINT_SCHEMA_VERSION = 2


class Checkpoint(BaseModel):
    """A durable snapshot of a run paused at a gate node."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = CHECKPOINT_SCHEMA_VERSION
    checkpoint_id: UUID = Field(default_factory=uuid4)
    run_id: UUID
    paused_at_node: str
    """Name of the gate node that paused execution."""

    gate_signals: tuple[str, ...]
    """The gate's declared signal enumeration at the time of pause."""

    graph_hash: str
    """Whole-graph structural hash recorded at pause; validated on resume."""

    status: Literal["pending", "consumed", "abandoned"] = "pending"
    consumed_at: datetime | None = None
    abandoned_at: datetime | None = None
    abandoned_reason: str | None = None

    state: RunState
    note: str = ""
    """Human-readable context for the pause; surfaced to the reviewer."""


class CheckpointStore:
    """Filesystem-backed checkpoint storage.

    One JSON file per checkpoint at `<dir>/<checkpoint_id>.json`.
    """

    def __init__(self, dir: Path) -> None:
        self.dir = dir

    @classmethod
    def at(cls, dir: Path) -> Self:
        dir.mkdir(parents=True, exist_ok=True)
        return cls(dir=dir)

    def save(self, checkpoint: Checkpoint) -> Path:
        path = self.dir / f"{checkpoint.checkpoint_id}.json"
        with path.open("w", encoding="utf-8") as fh:
            fh.write(checkpoint.model_dump_json(indent=2))
        return path

    def load(self, checkpoint_id: UUID) -> Checkpoint:
        path = self.dir / f"{checkpoint_id}.json"
        with path.open("r", encoding="utf-8") as fh:
            data: dict[str, Any] = json.load(fh)
        return Checkpoint.model_validate(data)
