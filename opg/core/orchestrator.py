"""Graph runner — walks a Graph, evaluates GuardSlots, emits audit events.

This is the core of the orchestrator: it takes a validated Graph, an initial
RunState, an OperatorConfig, and an AuditLog, and runs the demo to completion
(or to a guard rejection, or to an iteration cap, or to an error).

What it does NOT do:
  * It does not call the model. Nodes that call the model do so via their
    handler, using a ModelClient passed in via state.scratch or closure.
  * It does not dispatch tools. Same pattern.
  * It does not interpret guard semantics. A guard is a callable that returns
    a verdict; the runner just runs it and acts on the verdict.

Per the level-set doc, the runner is held constant across demos (MBT-11).
"""

from __future__ import annotations

from dataclasses import dataclass

from opg.core.audit import AuditLog
from opg.core.config import OperatorConfig
from opg.core.graph import Graph, GuardPass, SlotPosition
from opg.core.state import RunState

# ---------------------------------------------------------------------------
# Outcome types
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CompletedOutcome:
    """The graph reached a terminal node normally."""

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


Outcome = CompletedOutcome | RejectedOutcome | CapExceededOutcome | ErrorOutcome


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


class GraphRunner:
    """Executes a Graph against a RunState.

    Construct with the immutable inputs (graph, config, audit log) and call
    `run()` with the seed state. Returns an Outcome.
    """

    def __init__(
        self,
        graph: Graph,
        config: OperatorConfig,
        audit: AuditLog,
    ) -> None:
        self._graph = graph
        self._config = config
        self._audit = audit

    async def run(self, state: RunState) -> Outcome:
        self._audit.emit(
            "run_start",
            {
                "run_id": str(state.run_id),
                "user_id": state.user_id,
                "entry_node": self._graph.entry,
            },
        )

        current = self._graph.entry
        try:
            while True:
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

                # Resolve next node (None means no outgoing edges — run completes here)
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
        finally:
            # Outcomes already emitted run_end; this is a safety net for
            # paths that escape without one (none currently, but defensive).
            pass

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
            if explicit_next not in self._graph.nodes:
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
