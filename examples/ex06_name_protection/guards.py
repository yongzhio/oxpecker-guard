"""Deterministic name-list filter for Example 6.

Operator supplies a list of protected names. The guard rejects any model
output containing any of those names as a substring (case-insensitive).

The match is exact-substring against the operator-supplied list.
Paraphrases, misspellings, and references not on the list are NOT caught —
that's the honest limit. See README for details.
"""

from __future__ import annotations

from opg.core.graph import GuardFn, GuardPass, GuardReject, GuardVerdict
from opg.core.state import Message, RunState


def name_list_guard(protected_names: list[str], name: str = "name_list_filter") -> GuardFn:
    """Reject if any protected name appears in the last assistant message.

    Match is case-insensitive substring. Operator must enumerate name variants
    in protected_names (e.g., "John Doe", "John D", "J Doe", "JD", etc.).
    """
    # Pre-lowercase for O(n) matching; keep originals for readable rejection reasons.
    lookup = [(orig, orig.lower()) for orig in protected_names]

    def _check(state: RunState) -> GuardVerdict:
        msg = _last_assistant_message(state)
        if msg is None or not msg.content:
            return GuardPass(guard_name=name, detail="no assistant content to check")
        haystack = msg.content.lower()
        for original, lower in lookup:
            if lower in haystack:
                return GuardReject(
                    guard_name=name,
                    reason=f"output contains protected name {original!r}",
                )
        return GuardPass(guard_name=name, detail=f"no match against {len(lookup)} protected names")

    return _check


def _last_assistant_message(state: RunState) -> Message | None:
    for m in reversed(state.messages):
        if m.role == "assistant":
            return m
    return None
