# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- v0 foundations: orchestrator core (`opg/core/`) implementing the abstract
  graph-with-guard-slots model from the level-set doc.
  - `state.py`: `RunState`, `Message`, `ToolCall`, `ToolResult`, `Counters`
  - `audit.py`: JSON Lines audit log writer with versioned schema
  - `graph.py`: `Node`, `Edge`, `GuardSlot`, `Graph`, `GraphBuilder` with
    declarative validation
  - `config.py`: `OperatorConfig`, `ModelConfig`, `LimitsConfig`, YAML loader
  - `model_client.py`: OpenAI-compatible HTTP wrapper for local/remote model
    servers (no live tests yet — wrapper only)
  - `checkpoint.py`: durable JSON checkpoint store for HITL pauses
  - `orchestrator.py`: `GraphRunner` with `Outcome` union types
- Two foundational guards: `iteration_cap_guard`, `tool_call_cap_guard`
- 44 tests across unit and integration tiers; full coverage of graph
  configuration and runtime flow per v0 requirements
- GitHub Actions CI: ruff lint + format check, mypy, pytest
- Tooling: `pyproject.toml` (Python 3.12, pydantic v2, httpx, pyyaml),
  `Makefile`, `.gitignore`

### Notes

- No deterministic-guard demos in v0. Demos begin in v0.1 with examples 1, 4a, 6.
- Live-model tests deferred until first demo and reference machine setup.
