# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

Planned: v0.2 adds examples 4b (hostile tool server), 7 (knowledge-graph
correction), and 5 (rate limiting).

## [0.1.0] — 2026-05-17

First release with running demos. Three deterministic guard patterns exercised
end-to-end against a local Qwen3 9B model via Ollama. 92 tests across unit,
integration, and example tiers.

### Added — orchestrator extensions

- **Gate node primitive** (`opg/core/graph.py`): `GateNode` abstract interface
  with `elicit_signal(state) -> str`. Builders subclass and implement signal
  elicitation per their deployment (CLI prompt, web push, MFA, etc.). The
  orchestrator pauses on reaching a gate, persists a checkpoint, returns
  `PausedOutcome`, and resumes only when given a signal from the declared
  enumeration.
- **`PausedOutcome`** added to the outcome union. The runner exits the run loop
  cleanly on gate-pause; the caller delivers the signal out-of-band and calls
  `runner.resume(checkpoint_id, signal, metadata)` to continue.
- **Checkpoint state machine** (`opg/core/checkpoint.py`): `Checkpoint` now
  carries `status` (pending/consumed/abandoned), `consumed_at`, `abandoned_at`,
  `abandoned_reason`, `graph_hash`, and `gate_signals`. `CheckpointStore` owns
  the state transitions via `seal_consumed()` and `seal_abandoned()`.
- **Graph version pinning** (`opg/core/graph.py`): `Graph.compute_hash()`
  produces a stable SHA-256 over the graph's structural content, including
  node handler identities, guard function identities, and gate signal
  enumerations. Resume against a modified graph raises
  `GraphVersionMismatchError` — paused runs cannot continue against changed
  code.
- **New audit event types**: `gate_enter`, `gate_signal`, `checkpoint_save`,
  `checkpoint_resume`, `checkpoint_abandoned`.
- **`runner.abandon_checkpoint(id, reason, abandoned_by)`** for explicit
  sealing of a pending checkpoint without resumption.
- **Exception hierarchy** for checkpoint and resume errors:
  `CheckpointConsumedError`, `CheckpointAbandonedError`,
  `GraphVersionMismatchError` (all in `opg/core/checkpoint.py`).

### Added — examples

- **Example 1 — Schema validation with layered guards**
  (`examples/ex01_schema_validation/`). Three deterministic guards on
  `call_model`'s after-slot: schema structure → semantic constraints →
  catalog grounding. Demonstrates the layered-guard pattern with explicit
  honest limits.
- **Example 4a — Tool allowlist with HITL gate**
  (`examples/ex04a_tool_allowlist/`). Tool allowlist guard, blast-radius
  router, and an `ApprovalGate` that pauses high-blast-radius tool dispatch
  for operator approval via stdin signal.
- **Example 6 — Underage plaintiff name protection**
  (`examples/ex06_name_protection/`). Exact-substring (case-insensitive)
  name-list filter on model output. Operator-supplied protected-name list;
  exhaustive variant enumeration is the operator's responsibility.
- Each example ships with: TOML config, typed pydantic config loader,
  `run_demo.py` CLI entry point, README with example prompts and honest
  limits, captured `example_session.txt` from real Ollama runs, and a
  test suite exercising every guard path with stubbed model outputs.
- `--stub` flag in ex01 and ex06 for deterministic guard exercise without
  a live model.

### Added — documentation

- Top-level `README.md` rewritten with "Tests vs. demos" framing, definitions
  section, layers and trust domains explanation, must-be-trues, and
  comparison to existing orchestrators (LangGraph, CrewAI, AutoGen).
- `examples/README.md` with shared Modelfile setup
  (`examples/qwen3-9b-65k.Modelfile`).

### Changed — orchestrator simplifications

- Dropped `scratch` field from `RunState` — security concern (free-form dict
  in run state), no demonstrated need in v0.1 demos.
- Dropped `predicate` field from `Edge` — branching is now expressed via
  handlers returning the next node name explicitly (the "explicit-next"
  pattern) or via decision nodes whose handlers make the routing decision.
  Edges are unconditional.
- Dropped `terminals` field from `Graph` — runs end naturally at nodes with
  no outgoing edges. `GraphBuilder` warns about orphans but doesn't fail
  the build.
- Dropped `position` field from `GuardSlot` — position is implicit in which
  collection (`before_slots` or `after_slots`) the slot lives in.
- Dropped `prompt`, `timeout_seconds`, `timeout_route` from `GateNode` core
  type — became an abstract interface; elicitation details are the builder's
  responsibility.
- `make_call_model_handler` in examples now preserves both `content` and
  `tool_calls` on assistant messages, instead of dropping one when the other
  is present.

### Changed — tooling

- Config format: TOML (was YAML). Demos load `config.toml` via stdlib
  `tomllib` on Python 3.11+, or the `tomli` backport on 3.10.
- Removed `pyyaml` and `types-pyyaml` from dependencies (unused after the
  YAML → TOML migration).

## [0.0.1] — 2026-05-07

Initial release. Orchestrator core foundations only; no demos.

### Added

- v0 foundations: orchestrator core (`opg/core/`) implementing the abstract
  graph-with-guard-slots model from the level-set doc.
  - `state.py`: `RunState`, `Message`, `ToolCall`, `ToolResult`, `Counters`
  - `audit.py`: JSON Lines audit log writer with versioned schema
  - `graph.py`: `Node`, `Edge`, `GuardSlot`, `Graph`, `GraphBuilder` with
    declarative validation
  - `config.py`: `OperatorConfig`, `ModelConfig`, `LimitsConfig`
  - `model_client.py`: OpenAI-compatible HTTP wrapper for local/remote model
    servers (no live tests yet — wrapper only)
  - `checkpoint.py`: durable JSON checkpoint store for HITL pauses
  - `orchestrator.py`: `GraphRunner` with `Outcome` union types
- Two foundational guards: `iteration_cap_guard`, `tool_call_cap_guard`
- 44 tests across unit and integration tiers; full coverage of graph
  configuration and runtime flow per v0 requirements
- GitHub Actions CI: ruff lint + format check, mypy, pytest
- Tooling: `pyproject.toml` (Python 3.10+, pydantic v2, httpx), `Makefile`,
  `.gitignore`

### Notes

- No deterministic-guard demos in v0. Demos begin in v0.1 with examples 1, 4a, 6.
- Live-model tests deferred until first demo and reference machine setup.