"""Checkpoint store — durable RunState snapshots for HITL pauses.

When a graph reaches a human-approval node, the orchestrator serializes the
current RunState to disk and exits. A separate process (the human reviewer's
UI, or a CLI command) reviews the state, optionally edits it, and calls
`resume()` to continue.

v0 implementation: JSON serialization, one file per checkpoint, named by
checkpoint_id. SQLite-backed event log is on the deferred list and can replace
this without changing the API.
"""

from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field
from typing_extensions import Self

from opg.core.state import RunState

CHECKPOINT_SCHEMA_VERSION = 1


class Checkpoint(BaseModel):
    """A durable snapshot of a paused run."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = CHECKPOINT_SCHEMA_VERSION
    checkpoint_id: UUID = Field(default_factory=uuid4)
    run_id: UUID
    paused_at_node: str
    """Name of the node that paused execution."""

    state: RunState
    note: str = ""
    """Human-readable reason for the pause; surfaced to the reviewer."""


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
            data = json.load(fh)
        return Checkpoint.model_validate(data)
