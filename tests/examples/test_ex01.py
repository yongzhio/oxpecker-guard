"""Example 1 structural tests: layered schema, semantic, and grounding guards.

All tests use make_model_stub to inject specific outputs; no real model is
called. Tests verify that each guard rejects the correct failure mode and
that a valid catalog entry passes all three layers.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from examples.ex01_schema_validation.catalog import PRODUCT_CATALOG
from examples.ex01_schema_validation.graph import build_graph, make_model_stub
from opg.core.audit import AuditLog
from opg.core.checkpoint import CheckpointStore
from opg.core.config import OperatorConfig
from opg.core.orchestrator import CompletedOutcome, GraphRunner, RejectedOutcome
from opg.core.state import Message, RunState

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_runner(graph, audit: AuditLog, store: CheckpointStore) -> GraphRunner:
    return GraphRunner(graph=graph, config=OperatorConfig(), audit=audit, checkpoint_store=store)


def _seed_state() -> RunState:
    state = RunState(user_id="tester")
    state.append_message(Message(role="user", content="Recommend a wireless mouse"))
    return state


def _valid_payload(sku: str = "SKU-1001") -> str:
    entry = PRODUCT_CATALOG[sku]
    return json.dumps(
        {
            "product_id": sku,
            "name": entry["name"],
            "category": entry["category"],
            "price_usd": entry["price_usd"],
            "in_stock": entry["in_stock"],
        }
    )


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_valid_catalog_entry_completes(tmp_path: Path) -> None:
    """A well-formed JSON payload for a real catalog entry passes all three guards."""
    graph = build_graph(make_model_stub(_valid_payload("SKU-1001")))
    state = _seed_state()
    store = CheckpointStore.at(tmp_path / "cp")
    with AuditLog.open(state.run_id, dir=tmp_path) as audit:
        outcome = await _make_runner(graph, audit, store).run(state)

    assert isinstance(outcome, CompletedOutcome)
    assert outcome.final_node == "done"


# ---------------------------------------------------------------------------
# Layer 1: schema_validation guard
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_plain_text_rejected_by_schema_guard(tmp_path: Path) -> None:
    """Non-JSON assistant output is rejected by the schema validation guard."""
    graph = build_graph(make_model_stub("I recommend the Wireless mouse, it is great!"))
    state = _seed_state()
    store = CheckpointStore.at(tmp_path / "cp")
    with AuditLog.open(state.run_id, dir=tmp_path) as audit:
        outcome = await _make_runner(graph, audit, store).run(state)

    assert isinstance(outcome, RejectedOutcome)
    assert outcome.guard_name == "schema_validation"
    assert "not valid JSON" in outcome.reason


@pytest.mark.asyncio
async def test_missing_required_field_rejected_by_schema_guard(tmp_path: Path) -> None:
    """JSON missing a required field (product_id) is rejected by the schema guard."""
    payload = json.dumps(
        {"name": "Wireless mouse", "category": "peripherals", "price_usd": 24.99, "in_stock": True}
    )
    graph = build_graph(make_model_stub(payload))
    state = _seed_state()
    store = CheckpointStore.at(tmp_path / "cp")
    with AuditLog.open(state.run_id, dir=tmp_path) as audit:
        outcome = await _make_runner(graph, audit, store).run(state)

    assert isinstance(outcome, RejectedOutcome)
    assert outcome.guard_name == "schema_validation"
    assert "product_id" in outcome.reason


# ---------------------------------------------------------------------------
# Layer 2: semantic_constraints guard
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_negative_price_rejected_by_semantic_guard(tmp_path: Path) -> None:
    """A valid JSON structure with a negative price fails the semantic guard."""
    payload = json.dumps(
        {
            "product_id": "SKU-1001",
            "name": "Wireless mouse",
            "category": "peripherals",
            "price_usd": -50.0,
            "in_stock": True,
        }
    )
    graph = build_graph(make_model_stub(payload))
    state = _seed_state()
    store = CheckpointStore.at(tmp_path / "cp")
    with AuditLog.open(state.run_id, dir=tmp_path) as audit:
        outcome = await _make_runner(graph, audit, store).run(state)

    assert isinstance(outcome, RejectedOutcome)
    assert outcome.guard_name == "semantic_constraints"
    assert "price_usd" in outcome.reason


@pytest.mark.asyncio
async def test_invalid_category_rejected_by_semantic_guard(tmp_path: Path) -> None:
    """A valid JSON structure with an unknown category fails the semantic guard."""
    payload = json.dumps(
        {
            "product_id": "SKU-1001",
            "name": "Wireless mouse",
            "category": "networking",
            "price_usd": 24.99,
            "in_stock": True,
        }
    )
    graph = build_graph(make_model_stub(payload))
    state = _seed_state()
    store = CheckpointStore.at(tmp_path / "cp")
    with AuditLog.open(state.run_id, dir=tmp_path) as audit:
        outcome = await _make_runner(graph, audit, store).run(state)

    assert isinstance(outcome, RejectedOutcome)
    assert outcome.guard_name == "semantic_constraints"
    assert "category" in outcome.reason


# ---------------------------------------------------------------------------
# Layer 3: grounding guard
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_nonexistent_product_id_rejected_by_grounding_guard(tmp_path: Path) -> None:
    """A well-formed payload with a product_id not in the catalog fails the grounding guard."""
    payload = json.dumps(
        {
            "product_id": "SKU-9999",
            "name": "Phantom Mouse",
            "category": "peripherals",
            "price_usd": 35.00,
            "in_stock": True,
        }
    )
    graph = build_graph(make_model_stub(payload))
    state = _seed_state()
    store = CheckpointStore.at(tmp_path / "cp")
    with AuditLog.open(state.run_id, dir=tmp_path) as audit:
        outcome = await _make_runner(graph, audit, store).run(state)

    assert isinstance(outcome, RejectedOutcome)
    assert outcome.guard_name == "grounding"
    assert "not in operator catalog" in outcome.reason


@pytest.mark.asyncio
async def test_real_sku_with_wrong_name_rejected_by_grounding_guard(tmp_path: Path) -> None:
    """A real product_id with an invented name is caught by the grounding guard."""
    payload = json.dumps(
        {
            "product_id": "SKU-1001",
            "name": "Gaming Mouse Pro",  # wrong — catalog says "Wireless mouse"
            "category": "peripherals",
            "price_usd": 24.99,
            "in_stock": True,
        }
    )
    graph = build_graph(make_model_stub(payload))
    state = _seed_state()
    store = CheckpointStore.at(tmp_path / "cp")
    with AuditLog.open(state.run_id, dir=tmp_path) as audit:
        outcome = await _make_runner(graph, audit, store).run(state)

    assert isinstance(outcome, RejectedOutcome)
    assert outcome.guard_name == "grounding"
    assert "name" in outcome.reason
