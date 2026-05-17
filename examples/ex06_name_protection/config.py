"""TOML config loader for Example 6.

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
    model_name: str = "qwen3.5:9b-65k"
    temperature: float = 0.0
    timeout_seconds: float = 300.0
    api_key: str | None = None


class LimitsSection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_iterations: int = 10
    max_model_calls: int = 3


class StorageSection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_filing_path: str = Field(default="examples/ex06_name_protection/data/case_filing.txt")
    protected_names_path: str = Field(
        default="examples/ex06_name_protection/data/protected_names.txt"
    )
    checkpoint_dir: str = Field(default="runs/ex06")


class Ex06Config(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: ModelSection = Field(default_factory=ModelSection)
    limits: LimitsSection = Field(default_factory=LimitsSection)
    storage: StorageSection = Field(default_factory=StorageSection)


def load_ex06_config(path: Path | None = None) -> Ex06Config:
    """Load and validate Example 6 config from a TOML file."""
    resolved = path or DEFAULT_CONFIG_PATH
    with resolved.open("rb") as fh:
        data = tomllib.load(fh)
    return Ex06Config.model_validate(data)


def load_protected_names(path: Path) -> list[str]:
    """Load protected names from a text file (one name per line).

    Blank lines and lines starting with '#' are ignored.
    """
    names: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            names.append(stripped)
    return names
