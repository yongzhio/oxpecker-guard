"""Iteration cap guard.

Rejects if `state.counters.iterations` exceeds a configured threshold. Useful
as an `after`-slot guard on loop nodes to enforce hard termination separately
from the orchestrator's own max_iterations cap (which is more of a safety net).

This is a small but real guard: a deterministic predicate over state, with
explicit pass/reject verdicts and a stable name.
"""

from __future__ import annotations

from opg.core.graph import GuardFn, GuardPass, GuardReject, GuardVerdict
from opg.core.state import RunState


def iteration_cap_guard(max_iterations: int, name: str = "iteration_cap") -> GuardFn:
    """Return a guard that rejects when iterations exceed `max_iterations`."""
    if max_iterations < 1:
        raise ValueError("max_iterations must be at least 1")

    def _check(state: RunState) -> GuardVerdict:
        if state.counters.iterations > max_iterations:
            return GuardReject(
                guard_name=name,
                reason=(f"iterations {state.counters.iterations} exceeds cap {max_iterations}"),
            )
        return GuardPass(guard_name=name)

    return _check
