"""Operator configuration — pydantic models for deployment-level policy.

The operator config is one of two configuration inputs (the other is the demo's
graph spec, expressed in code). It carries deployment-level policy: rate limits,
allowlists, model choice, content thresholds, and so on.

Per the level-set doc: operator config is consumed by the orchestrator and by
guards. The model never sees it.

This v0 schema is intentionally small. Demos and guards extend it via the
typed `extras` field rather than by adding fields here, so the core stays
stable as new demos add new policy needs.

TOML parsing is the responsibility of each demo's own config loader
(examples/*/config.py), not the orchestrator core. The core exposes only
the typed pydantic models; callers construct them from already-parsed data.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ModelConfig(BaseModel):
    """How and where to call the model."""

    model_config = ConfigDict(extra="forbid")

    base_url: str = "http://localhost:1234/v1"
    """OpenAI-compatible endpoint. LM Studio's default is :1234, Ollama's :11434."""

    model_name: str = "qwen2.5-coder:32b"
    """Model identifier as the server expects it. Documented as a default,
    not a guarantee — the level-set doc defers the version pin to v0 build."""

    temperature: float = 0.0
    """Default to 0 for reproducibility. Demos can override."""

    timeout_seconds: float = 120.0
    api_key: str | None = None
    """Optional. Most local servers don't require one."""


class LimitsConfig(BaseModel):
    """Hard caps the orchestrator enforces."""

    model_config = ConfigDict(extra="forbid")

    max_iterations: int = 20
    """Maximum graph node visits per run before forced termination."""

    max_model_calls: int = 10
    max_tool_calls: int = 20
    max_input_tokens: int = 100_000
    max_output_tokens: int = 10_000


class OperatorConfig(BaseModel):
    """Full operator configuration for a run.

    Demos and guards may carry their own typed config in `extras` keyed by
    a stable string. The core does not interpret extras.
    """

    model_config = ConfigDict(extra="forbid")

    model: ModelConfig = Field(default_factory=ModelConfig)
    limits: LimitsConfig = Field(default_factory=LimitsConfig)
    extras: dict[str, Any] = Field(default_factory=dict)
