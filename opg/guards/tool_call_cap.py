"""Tool-call cap guard.

Rejects if `state.counters.tool_calls` exceeds a threshold. The pair-with
to iteration_cap_guard for budget enforcement.
"""

from __future__ import annotations

from opg.core.graph import GuardFn, GuardPass, GuardReject, GuardVerdict
from opg.core.state import RunState


def tool_call_cap_guard(max_tool_calls: int, name: str = "tool_call_cap") -> GuardFn:
    """Return a guard that rejects when tool_calls exceed `max_tool_calls`."""
    if max_tool_calls < 0:
        raise ValueError("max_tool_calls must be non-negative")

    def _check(state: RunState) -> GuardVerdict:
        if state.counters.tool_calls > max_tool_calls:
            return GuardReject(
                guard_name=name,
                reason=(f"tool_calls {state.counters.tool_calls} exceeds cap {max_tool_calls}"),
            )
        return GuardPass(guard_name=name)

    return _check
