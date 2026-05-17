"""Three deterministic guards for Example 1.

Each guard inspects the last assistant message's content. They are
composed in declaration order: schema check first, semantic check next,
grounding check last. A rejection from any guard halts the run; later
guards do not execute.
"""

from __future__ import annotations

import json
import re
from typing import Any

from examples.ex01_schema_validation.catalog import PRODUCT_CATALOG, VALID_CATEGORIES
from opg.core.graph import GuardFn, GuardPass, GuardReject, GuardVerdict
from opg.core.state import Message, RunState

_SKU_PATTERN = re.compile(r"^SKU-\d{4}$")

_REQUIRED_TYPES: dict[str, type | tuple[type, ...]] = {
    "product_id": str,
    "name": str,
    "category": str,
    "price_usd": (int, float),
    "in_stock": bool,
}


# ---------------------------------------------------------------------------
# Layer 1: JSON schema check
# ---------------------------------------------------------------------------


def schema_validation_guard(name: str = "schema_validation") -> GuardFn:
    """Reject if the last assistant message is not valid JSON matching the
    product-recommendation schema.

    Required fields: product_id (str), name (str), category (str),
    price_usd (number), in_stock (bool).
    """

    def _check(state: RunState) -> GuardVerdict:
        msg = _last_assistant_message(state)
        if msg is None or not msg.content:
            return GuardReject(guard_name=name, reason="no assistant content to validate")
        try:
            obj = json.loads(msg.content)
        except json.JSONDecodeError as e:
            return GuardReject(guard_name=name, reason=f"output is not valid JSON: {e.msg}")
        if not isinstance(obj, dict):
            return GuardReject(
                guard_name=name,
                reason=f"output is not a JSON object (got {type(obj).__name__})",
            )
        for field, expected in _REQUIRED_TYPES.items():
            if field not in obj:
                return GuardReject(guard_name=name, reason=f"missing required field {field!r}")
            if not isinstance(obj[field], expected):
                return GuardReject(guard_name=name, reason=f"field {field!r} has wrong type")
        # bool is a subclass of int in Python — explicitly reject booleans for price_usd.
        if isinstance(obj["price_usd"], bool):
            return GuardReject(
                guard_name=name, reason="field 'price_usd' has wrong type (got bool)"
            )
        return GuardPass(guard_name=name, detail="JSON structure valid")

    return _check


# ---------------------------------------------------------------------------
# Layer 2: Semantic constraints
# ---------------------------------------------------------------------------


def semantic_constraints_guard(name: str = "semantic_constraints") -> GuardFn:
    """Reject if values are outside their bounded domains.

    Pre-condition: schema_validation_guard already passed (so structure is valid).
    Checks:
      - price_usd > 0 and < 100000
      - category is in VALID_CATEGORIES
      - product_id matches the pattern 'SKU-' + 4 digits (operator convention)
    """

    def _check(state: RunState) -> GuardVerdict:
        msg = _last_assistant_message(state)
        assert msg is not None and msg.content  # guaranteed by prior guard
        obj: dict[str, Any] = json.loads(msg.content)
        if not 0 < obj["price_usd"] < 100_000:
            return GuardReject(
                guard_name=name,
                reason=f"price_usd {obj['price_usd']} out of range (0, 100000)",
            )
        if obj["category"] not in VALID_CATEGORIES:
            return GuardReject(
                guard_name=name,
                reason=f"category {obj['category']!r} not in valid set",
            )
        if not _SKU_PATTERN.match(obj["product_id"]):
            return GuardReject(
                guard_name=name,
                reason=f"product_id {obj['product_id']!r} does not match operator SKU pattern",
            )
        return GuardPass(guard_name=name, detail="semantic constraints satisfied")

    return _check


# ---------------------------------------------------------------------------
# Layer 3: Grounding check
# ---------------------------------------------------------------------------


def grounding_guard(
    catalog: dict[str, dict[str, object]] = PRODUCT_CATALOG,
    name: str = "grounding",
) -> GuardFn:
    """Reject if product_id is not in the operator catalog.

    Pre-condition: prior guards passed.
    Also cross-checks name and category against the catalog entry to catch
    cases where the model reused a real SKU but invented different details.
    """

    def _check(state: RunState) -> GuardVerdict:
        msg = _last_assistant_message(state)
        assert msg is not None and msg.content
        obj: dict[str, Any] = json.loads(msg.content)
        pid = obj["product_id"]
        if pid not in catalog:
            return GuardReject(
                guard_name=name,
                reason=f"product_id {pid!r} not in operator catalog (model hallucination)",
            )
        entry = catalog[pid]
        if obj["name"] != entry["name"]:
            return GuardReject(
                guard_name=name,
                reason=(
                    f"product_id {pid!r} has name {entry['name']!r} in catalog, not {obj['name']!r}"
                ),
            )
        if obj["category"] != entry["category"]:
            return GuardReject(
                guard_name=name,
                reason=(
                    f"product_id {pid!r} has category {entry['category']!r} in catalog, "
                    f"not {obj['category']!r}"
                ),
            )
        return GuardPass(guard_name=name, detail=f"product {pid!r} grounded in catalog")

    return _check


# ---------------------------------------------------------------------------
# Internal helper
# ---------------------------------------------------------------------------


def _last_assistant_message(state: RunState) -> Message | None:
    for m in reversed(state.messages):
        if m.role == "assistant":
            return m
    return None
