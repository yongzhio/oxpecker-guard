"""Example 6 structural tests: name-list filter guard.

All tests use make_model_stub to inject specific outputs; no real model is
called and the actual protected_names.txt is not loaded. The guard logic is
tested against an explicit inline name list.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from examples.ex06_name_protection.graph import build_graph, make_model_stub
from opg.core.audit import AuditLog
from opg.core.checkpoint import CheckpointStore
from opg.core.config import OperatorConfig
from opg.core.orchestrator import CompletedOutcome, GraphRunner, RejectedOutcome
from opg.core.state import Message, RunState

# ---------------------------------------------------------------------------
# Test-local name list — does not load protected_names.txt
# ---------------------------------------------------------------------------

_PROTECTED = ["John Doe", "J Doe", "J D", "JD", "John D", "Doe Jr"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_runner(graph, audit: AuditLog, store: CheckpointStore) -> GraphRunner:
    return GraphRunner(graph=graph, config=OperatorConfig(), audit=audit, checkpoint_store=store)


def _seed_state() -> RunState:
    state = RunState(user_id="tester")
    state.append_message(Message(role="user", content="Summarise the filing."))
    return state


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_output_containing_john_doe_is_rejected(tmp_path: Path) -> None:
    """Model output that contains 'John Doe' is rejected by the name-list guard."""
    text = "The plaintiff John Doe filed a complaint against the school district."
    graph = build_graph(make_model_stub(text), _PROTECTED)
    state = _seed_state()
    store = CheckpointStore.at(tmp_path / "cp")
    with AuditLog.open(state.run_id, dir=tmp_path) as audit:
        outcome = await _make_runner(graph, audit, store).run(state)

    assert isinstance(outcome, RejectedOutcome)
    assert outcome.guard_name == "name_list_filter"
    assert "John Doe" in outcome.reason


@pytest.mark.asyncio
async def test_output_containing_j_d_is_rejected(tmp_path: Path) -> None:
    """Model output containing 'J D' (initials variant) is rejected."""
    text = "The complaint was filed on behalf of J D, a minor."
    graph = build_graph(make_model_stub(text), _PROTECTED)
    state = _seed_state()
    store = CheckpointStore.at(tmp_path / "cp")
    with AuditLog.open(state.run_id, dir=tmp_path) as audit:
        outcome = await _make_runner(graph, audit, store).run(state)

    assert isinstance(outcome, RejectedOutcome)
    assert outcome.guard_name == "name_list_filter"


@pytest.mark.asyncio
async def test_output_containing_jd_abbreviation_is_rejected(tmp_path: Path) -> None:
    """Model output containing 'JD' (abbreviation) is rejected."""
    text = "The minor plaintiff, referred to herein as JD, suffered injuries."
    graph = build_graph(make_model_stub(text), _PROTECTED)
    state = _seed_state()
    store = CheckpointStore.at(tmp_path / "cp")
    with AuditLog.open(state.run_id, dir=tmp_path) as audit:
        outcome = await _make_runner(graph, audit, store).run(state)

    assert isinstance(outcome, RejectedOutcome)
    assert outcome.guard_name == "name_list_filter"


@pytest.mark.asyncio
async def test_paraphrase_only_output_passes(tmp_path: Path) -> None:
    """Output using only paraphrases ('the plaintiff') passes the guard.

    This is the honest limit: paraphrases not on the list are not caught.
    """
    text = "The plaintiff filed a complaint against the school district alleging negligent supervision."
    graph = build_graph(make_model_stub(text), _PROTECTED)
    state = _seed_state()
    store = CheckpointStore.at(tmp_path / "cp")
    with AuditLog.open(state.run_id, dir=tmp_path) as audit:
        outcome = await _make_runner(graph, audit, store).run(state)

    assert isinstance(outcome, CompletedOutcome)
    assert outcome.final_node == "done"


@pytest.mark.asyncio
async def test_empty_output_passes(tmp_path: Path) -> None:
    """Empty assistant content passes the guard (no content to check)."""
    graph = build_graph(make_model_stub(""), _PROTECTED)
    state = _seed_state()
    store = CheckpointStore.at(tmp_path / "cp")
    with AuditLog.open(state.run_id, dir=tmp_path) as audit:
        outcome = await _make_runner(graph, audit, store).run(state)

    assert isinstance(outcome, CompletedOutcome)
    assert outcome.final_node == "done"


@pytest.mark.asyncio
async def test_case_insensitive_match_rejects(tmp_path: Path) -> None:
    """Match is case-insensitive: 'JOHN DOE' matches the list entry 'John Doe'."""
    text = "This complaint was submitted on behalf of JOHN DOE, a minor."
    graph = build_graph(make_model_stub(text), _PROTECTED)
    state = _seed_state()
    store = CheckpointStore.at(tmp_path / "cp")
    with AuditLog.open(state.run_id, dir=tmp_path) as audit:
        outcome = await _make_runner(graph, audit, store).run(state)

    assert isinstance(outcome, RejectedOutcome)
    assert outcome.guard_name == "name_list_filter"
