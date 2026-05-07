"""Deterministic guards.

Every guard is a pure function of RunState (and its own configuration captured
at construction time). Guards never call an LLM and never have side effects on
external systems.

This module is intentionally sparse in v0. Demos add guards as needed.
"""

from opg.guards.iteration_cap import iteration_cap_guard
from opg.guards.tool_call_cap import tool_call_cap_guard

__all__ = ["iteration_cap_guard", "tool_call_cap_guard"]
