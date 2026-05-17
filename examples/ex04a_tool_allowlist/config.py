"""TOML config loader for Example 4a.

Provides a typed Ex04aConfig that wraps the values from config.toml.
Keeps the TOML schema minimal and separate from opg.core.config so
that changing the demo's config doesn't touch the core.

Python 3.10 compatibility: uses the 'tomli' backport when the stdlib
'tomllib' (3.11+) is not available.
"""

from __future__ import annotations

import sys
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib  # type: ignore[no-redef]

from pydantic import BaseModel, ConfigDict, Field

# Default config lives next to this file.
DEFAULT_CONFIG_PATH = Path(__file__).parent / "config.toml"


class ModelSection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_url: str = "http://localhost:11434/v1"
    model_name: str = "qwen3.5:9b-65k"
    temperature: float = 0.1
    timeout_seconds: float = 120.0
    api_key: str | None = None


class LimitsSection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_iterations: int = 20
    max_model_calls: int = 6
    max_tool_calls: int = 4


class StorageSection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    checkpoint_dir: str = Field(default="runs/ex04a")


class Ex04aConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: ModelSection = Field(default_factory=ModelSection)
    limits: LimitsSection = Field(default_factory=LimitsSection)
    storage: StorageSection = Field(default_factory=StorageSection)


def load_ex04a_config(path: Path | None = None) -> Ex04aConfig:
    """Load and validate Example 4a config from a TOML file.

    Falls back to config.toml next to this file when path is None.
    """
    resolved = path or DEFAULT_CONFIG_PATH
    with resolved.open("rb") as fh:
        data = tomllib.load(fh)
    return Ex04aConfig.model_validate(data)
