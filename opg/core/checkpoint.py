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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field
from typing_extensions import Self

from opg.core.state import RunState

CHECKPOINT_SCHEMA_VERSION = 2


# ---------------------------------------------------------------------------
# Checkpoint state-machine errors
# ---------------------------------------------------------------------------


class CheckpointConsumedError(Exception):
    """Raised when seal_consumed or seal_abandoned is attempted on a consumed checkpoint."""

    def __init__(self, checkpoint_id: UUID) -> None:
        self.checkpoint_id = checkpoint_id
        super().__init__(f"checkpoint {checkpoint_id} is already consumed")


class CheckpointAbandonedError(Exception):
    """Raised when seal_consumed or seal_abandoned is attempted on an abandoned checkpoint."""

    def __init__(self, checkpoint_id: UUID) -> None:
        self.checkpoint_id = checkpoint_id
        super().__init__(f"checkpoint {checkpoint_id} is already abandoned")


# ---------------------------------------------------------------------------
# Checkpoint model
# ---------------------------------------------------------------------------


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

    def seal_consumed(self, checkpoint_id: UUID) -> Checkpoint:
        """Transition a pending checkpoint to consumed. Returns the updated checkpoint.

        Raises CheckpointConsumedError if already consumed,
        CheckpointAbandonedError if already abandoned.
        """
        cp = self.load(checkpoint_id)
        if cp.status == "consumed":
            raise CheckpointConsumedError(checkpoint_id)
        if cp.status == "abandoned":
            raise CheckpointAbandonedError(checkpoint_id)
        updated = cp.model_copy(
            update={"status": "consumed", "consumed_at": datetime.now(timezone.utc)}
        )
        self.save(updated)
        return updated

    def seal_abandoned(self, checkpoint_id: UUID, reason: str) -> Checkpoint:
        """Transition a pending checkpoint to abandoned. Returns the updated checkpoint.

        Raises CheckpointConsumedError if already consumed,
        CheckpointAbandonedError if already abandoned.
        """
        cp = self.load(checkpoint_id)
        if cp.status == "consumed":
            raise CheckpointConsumedError(checkpoint_id)
        if cp.status == "abandoned":
            raise CheckpointAbandonedError(checkpoint_id)
        updated = cp.model_copy(
            update={
                "status": "abandoned",
                "abandoned_at": datetime.now(timezone.utc),
                "abandoned_reason": reason,
            }
        )
        self.save(updated)
        return updated
