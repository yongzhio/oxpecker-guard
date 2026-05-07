"""Foundational guards: pass/reject based on counters."""

from __future__ import annotations

import pytest

from opg.core.graph import GuardPass, GuardReject
from opg.core.state import RunState
from opg.guards import iteration_cap_guard, tool_call_cap_guard


def test_iteration_cap_passes_under_limit() -> None:
    guard = iteration_cap_guard(max_iterations=5)
    state = RunState()
    state.counters.iterations = 3
    verdict = guard(state)
    assert isinstance(verdict, GuardPass)
    assert verdict.guard_name == "iteration_cap"


def test_iteration_cap_rejects_over_limit() -> None:
    guard = iteration_cap_guard(max_iterations=2)
    state = RunState()
    state.counters.iterations = 3
    verdict = guard(state)
    assert isinstance(verdict, GuardReject)
    assert "iterations 3" in verdict.reason
    assert "cap 2" in verdict.reason


def test_iteration_cap_rejects_negative_threshold() -> None:
    with pytest.raises(ValueError):
        iteration_cap_guard(max_iterations=0)


def test_tool_call_cap_pass_and_reject() -> None:
    guard = tool_call_cap_guard(max_tool_calls=2)
    state = RunState()
    state.counters.tool_calls = 1
    assert isinstance(guard(state), GuardPass)
    state.counters.tool_calls = 3
    assert isinstance(guard(state), GuardReject)
