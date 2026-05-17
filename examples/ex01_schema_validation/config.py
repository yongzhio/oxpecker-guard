"""TOML config loader for Example 1.

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

DEFAULT_CONFIG_PATH = Path(__file__).parent / "config.toml"


class ModelSection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_url: str = "http://localhost:11434/v1"
    model_name: str = "qwen3.5:9b"
    temperature: float = 0.0
    timeout_seconds: float = 120.0
    api_key: str | None = None


class LimitsSection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_iterations: int = 10
    max_model_calls: int = 3


class StorageSection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    checkpoint_dir: str = Field(default="runs/ex01")


class Ex01Config(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: ModelSection = Field(default_factory=ModelSection)
    limits: LimitsSection = Field(default_factory=LimitsSection)
    storage: StorageSection = Field(default_factory=StorageSection)


def load_ex01_config(path: Path | None = None) -> Ex01Config:
    """Load and validate Example 1 config from a TOML file."""
    resolved = path or DEFAULT_CONFIG_PATH
    with resolved.open("rb") as fh:
        data = tomllib.load(fh)
    return Ex01Config.model_validate(data)
