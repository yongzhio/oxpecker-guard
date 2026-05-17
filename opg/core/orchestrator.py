"""Graph runner — walks a Graph, evaluates GuardSlots, emits audit events.

This is the core of the orchestrator: it takes a validated Graph, an initial
RunState, an OperatorConfig, and an AuditLog, and runs the demo to completion
(or to a guard rejection, or to an iteration cap, or to an error, or to a gate
node pause).

What it does NOT do:
  * It does not call the model. Nodes that call the model do so via their
    handler using a ModelClient passed in via closure.
  * It does not dispatch tools. Same pattern.
  * It does not interpret guard semantics. A guard is a callable that returns
    a verdict; the runner just runs it and acts on the verdict.
  * It does not elicit gate signals. When a gate node is reached the runner
    saves a checkpoint and returns PausedOutcome. The caller obtains the signal
    (via elicit_signal, UI, webhook, etc.) and calls runner.resume().

Per the level-set doc, the runner is held constant across demos (MBT-11).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from opg.core.audit import AuditLog
from opg.core.checkpoint import (
    Checkpoint,
    CheckpointAbandonedError,
    CheckpointConsumedError,
    CheckpointStore,
)
from opg.core.config import OperatorConfig
from opg.core.graph import Graph, GuardPass, SlotPosition
from opg.core.state import RunState

# Re-exported for backward compatibility — callers that import these from
# opg.core.orchestrator continue to work after the classes moved to checkpoint.
__all__ = [
    "CheckpointAbandonedError",
    "CheckpointConsumedError",
    "GraphVersionMismatchError",
]

# ---------------------------------------------------------------------------
# Outcome types
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CompletedOutcome:
    """The graph reached a sink node normally."""

    final_node: str
    state: RunState


@dataclass(frozen=True, slots=True)
class RejectedOutcome:
    """A guard rejected. The run halted at a slot."""

    guard_name: str
    reason: str
    rejected_at_node: str
    rejected_at_position: SlotPosition
    state: RunState


@dataclass(frozen=True, slots=True)
class CapExceededOutcome:
    """A hard cap (max iterations, etc.) terminated the run."""

    cap_name: str
    state: RunState


@dataclass(frozen=True, slots=True)
class ErrorOutcome:
    """An exception escaped a node handler. The run halted."""

    error_type: str
    message: str
    node: str
    state: RunState


@dataclass(frozen=True, slots=True)
class PausedOutcome:
    """The graph reached a gate node and paused pending an external signal.

    The caller should obtain the signal (via the gate's elicit_signal(), a
    UI callback, a webhook, etc.) and call runner.resume(checkpoint_id, signal).
    """

    checkpoint_id: UUID
    gate_name: str
    signals: tuple[str, ...]
    state: RunState


Outcome = CompletedOutcome | RejectedOutcome | CapExceededOutcome | ErrorOutcome | PausedOutcome


# ---------------------------------------------------------------------------
# Exceptions raised by resume() and abandon_checkpoint()
# ---------------------------------------------------------------------------


class GraphVersionMismatchError(Exception):
    """Raised when the graph's current hash differs from the checkpoint's recorded hash.

    Resuming against a structurally different graph is refused (decision 18).
    """

    def __init__(self, checkpoint_id: UUID, saved: str, current: str) -> None:
        self.checkpoint_id = checkpoint_id
        self.saved_hash = saved
        self.current_hash = current
        super().__init__(
            f"graph hash mismatch for checkpoint {checkpoint_id}: "
            f"saved={saved[:8]!r}, current={current[:8]!r}"
        )


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


class GraphRunner:
    """Executes a Graph against a RunState.

    Construct with the immutable inputs (graph, config, audit log, optional
    checkpoint store) and call run() with the seed state. Returns an Outcome.

    checkpoint_store is required if the graph contains gate nodes. A runner
    without a store will raise RuntimeError when it reaches a gate node.
    """

    def __init__(
        self,
        graph: Graph,
        config: OperatorConfig,
        audit: AuditLog,
        checkpoint_store: CheckpointStore | None = None,
    ) -> None:
        self._graph = graph
        self._config = config
        self._audit = audit
        self._checkpoint_store = checkpoint_store

    async def run(self, state: RunState) -> Outcome:
        self._audit.emit(
            "run_start",
            {
                "run_id": str(state.run_id),
                "user_id": state.user_id,
                "entry_node": self._graph.entry,
            },
        )
        return await self._run_from(state, self._graph.entry)

    async def resume(
        self,
        checkpoint_id: UUID,
        signal: str,
        metadata: dict[str, Any] | None = None,
    ) -> Outcome:
        """Resume a paused run by delivering a signal to the gate checkpoint.

        Validates that the checkpoint is pending, that the graph hash matches,
        and that the signal is in the gate's declared enumeration. Marks the
        checkpoint consumed before continuing the run (single-use guarantee).
        """
        if self._checkpoint_store is None:
            raise RuntimeError("GraphRunner has no checkpoint_store configured")

        checkpoint = self._checkpoint_store.load(checkpoint_id)

        if checkpoint.status == "consumed":
            raise CheckpointConsumedError(checkpoint_id)
        if checkpoint.status == "abandoned":
            raise CheckpointAbandonedError(checkpoint_id)

        current_hash = self._graph.compute_hash()
        if checkpoint.graph_hash != current_hash:
            raise GraphVersionMismatchError(checkpoint_id, checkpoint.graph_hash, current_hash)

        gate = self._graph.gate_nodes[checkpoint.paused_at_node]
        if signal not in gate.signals:
            raise ValueError(
                f"signal {signal!r} not in gate {checkpoint.paused_at_node!r} "
                f"declared signals {gate.signals!r}"
            )

        self._checkpoint_store.seal_consumed(checkpoint_id)

        self._audit.emit(
            "gate_signal",
            {
                "gate": checkpoint.paused_at_node,
                "signal": signal,
                "checkpoint_id": str(checkpoint_id),
                "metadata": metadata or {},
            },
        )
        self._audit.emit(
            "checkpoint_resume",
            {"checkpoint_id": str(checkpoint_id), "signal": signal},
        )

        # Evaluate the gate's after slot for post-signal work (decision 4).
        rejection = self._run_slot(checkpoint.state, checkpoint.paused_at_node, "after")
        if rejection is not None:
            return rejection

        next_node = gate.routing[signal]
        return await self._run_from(checkpoint.state, next_node)

    def abandon_checkpoint(
        self,
        checkpoint_id: UUID,
        reason: str,
        abandoned_by: str | None = None,
    ) -> None:
        """Seal a pending checkpoint without resuming it.

        Idempotency: raises if the checkpoint is already consumed or abandoned.
        The checkpoint is preserved on disk (audit-preserved, decision 17).
        """
        if self._checkpoint_store is None:
            raise RuntimeError("GraphRunner has no checkpoint_store configured")

        abandoned = self._checkpoint_store.seal_abandoned(checkpoint_id, reason)

        self._audit.emit(
            "checkpoint_abandoned",
            {
                "checkpoint_id": str(checkpoint_id),
                "reason": reason,
                "abandoned_at": abandoned.abandoned_at.isoformat() if abandoned.abandoned_at else None,
                "abandoned_by": abandoned_by,
            },
        )

    # ------------------------------------------------------------------
    # Internal run loop
    # ------------------------------------------------------------------

    async def _run_from(self, state: RunState, start_node: str) -> Outcome:
        """Core execution loop starting at start_node."""
        current = start_node
        while True:
            # Gate check first — gates do not consume an iteration tick (decision 14)
            if current in self._graph.gate_nodes:
                return await self._handle_gate(state, current)

            # Cap check: max iterations
            if state.counters.iterations >= self._config.limits.max_iterations:
                self._audit.emit(
                    "run_end",
                    {"reason": "cap_exceeded", "cap": "max_iterations"},
                )
                return CapExceededOutcome(cap_name="max_iterations", state=state)
            state.counters.iterations += 1

            # Evaluate the "before" slot
            rejection = self._run_slot(state, current, "before")
            if rejection is not None:
                return rejection

            # Run the node
            node = self._graph.nodes[current]
            self._audit.emit("node_enter", {"node": current, "kind": node.kind})
            try:
                explicit_next = await node.handler(state)
            except Exception as exc:
                self._audit.emit(
                    "error",
                    {
                        "node": current,
                        "error_type": type(exc).__name__,
                        "message": str(exc),
                    },
                )
                self._audit.emit("run_end", {"reason": "error"})
                return ErrorOutcome(
                    error_type=type(exc).__name__,
                    message=str(exc),
                    node=current,
                    state=state,
                )
            self._audit.emit("node_exit", {"node": current, "kind": node.kind})

            # Evaluate the "after" slot
            rejection = self._run_slot(state, current, "after")
            if rejection is not None:
                return rejection

            # Resolve next node (None means sink — run completes here)
            try:
                next_node = self._resolve_next(state, current, explicit_next)
            except RuntimeError as exc:
                self._audit.emit(
                    "error",
                    {
                        "node": current,
                        "error_type": type(exc).__name__,
                        "message": str(exc),
                    },
                )
                self._audit.emit("run_end", {"reason": "error"})
                return ErrorOutcome(
                    error_type=type(exc).__name__,
                    message=str(exc),
                    node=current,
                    state=state,
                )

            if next_node is None:
                self._audit.emit(
                    "run_end",
                    {"reason": "completed", "final_node": current},
                )
                return CompletedOutcome(final_node=current, state=state)

            current = next_node

    async def _handle_gate(self, state: RunState, gate_name: str) -> PausedOutcome:
        """Save a checkpoint and return PausedOutcome when a gate node is reached."""
        if self._checkpoint_store is None:
            raise RuntimeError(
                f"gate node {gate_name!r} reached but GraphRunner has no checkpoint_store"
            )

        gate = self._graph.gate_nodes[gate_name]

        # Create the checkpoint first so its ID is available for the gate_enter payload.
        checkpoint = Checkpoint(
            run_id=state.run_id,
            paused_at_node=gate_name,
            gate_signals=gate.signals,
            graph_hash=self._graph.compute_hash(),
            state=state,
        )
        self._audit.emit(
            "gate_enter",
            {
                "gate": gate_name,
                "signals": list(gate.signals),
                "checkpoint_id": str(checkpoint.checkpoint_id),
            },
        )
        self._checkpoint_store.save(checkpoint)
        self._audit.emit(
            "checkpoint_save",
            {
                "checkpoint_id": str(checkpoint.checkpoint_id),
                "paused_at_node": gate_name,
            },
        )

        return PausedOutcome(
            checkpoint_id=checkpoint.checkpoint_id,
            gate_name=gate_name,
            signals=gate.signals,
            state=state,
        )

    # ------------------------------------------------------------------
    # Slot evaluation
    # ------------------------------------------------------------------

    def _run_slot(
        self,
        state: RunState,
        node_name: str,
        position: SlotPosition,
    ) -> RejectedOutcome | None:
        slot_dict = self._graph.before_slots if position == "before" else self._graph.after_slots
        slot = slot_dict.get(node_name)
        if slot is None or not slot.guards:
            return None  # empty slot — pass-through

        self._audit.emit(
            "slot_enter",
            {"node": node_name, "position": position, "guard_count": len(slot.guards)},
        )
        for guard in slot.guards:
            verdict = guard(state)
            if isinstance(verdict, GuardPass):
                self._audit.emit(
                    "guard_pass",
                    {
                        "node": node_name,
                        "position": position,
                        "guard": verdict.guard_name,
                        "detail": verdict.detail,
                    },
                )
                continue
            # GuardReject
            self._audit.emit(
                "guard_reject",
                {
                    "node": node_name,
                    "position": position,
                    "guard": verdict.guard_name,
                    "reason": verdict.reason,
                },
            )
            self._audit.emit(
                "run_end",
                {
                    "reason": "guard_reject",
                    "guard": verdict.guard_name,
                    "node": node_name,
                    "position": position,
                },
            )
            return RejectedOutcome(
                guard_name=verdict.guard_name,
                reason=verdict.reason,
                rejected_at_node=node_name,
                rejected_at_position=position,
                state=state,
            )

        self._audit.emit("slot_exit", {"node": node_name, "position": position})
        return None

    # ------------------------------------------------------------------
    # Edge resolution
    # ------------------------------------------------------------------

    def _resolve_next(
        self,
        state: RunState,
        current: str,
        explicit_next: str | None,
    ) -> str | None:
        """Pick the next node. Returns None when the node is a sink (no outgoing edges)."""
        if explicit_next is not None:
            all_nodes = set(self._graph.nodes) | set(self._graph.gate_nodes)
            if explicit_next not in all_nodes:
                raise RuntimeError(
                    f"node {current!r} returned explicit next {explicit_next!r} "
                    "which is not a declared node"
                )
            return explicit_next

        edges = self._graph.edges.get(current, ())
        if not edges:
            return None  # sink node — run completes here

        if len(edges) > 1:
            raise RuntimeError(
                f"node {current!r} has multiple outgoing edges and handler returned None; "
                "handler must return an explicit next-node name"
            )

        return edges[0].target
