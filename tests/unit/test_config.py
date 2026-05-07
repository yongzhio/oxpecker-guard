"""Operator config: defaults and YAML loading."""

from __future__ import annotations

from pathlib import Path

from opg.core.config import OperatorConfig, load_config


def test_config_defaults_construct() -> None:
    cfg = OperatorConfig()
    assert cfg.model.temperature == 0.0
    assert cfg.limits.max_iterations == 20


def test_load_config_from_yaml(tmp_path: Path) -> None:
    p = tmp_path / "operator_config.yaml"
    p.write_text(
        """
model:
  base_url: "http://desktop:1234/v1"
  model_name: "qwen2.5-coder:32b"
  temperature: 0.2
limits:
  max_iterations: 50
extras:
  demo_specific:
    foo: "bar"
""",
        encoding="utf-8",
    )
    cfg = load_config(p)
    assert cfg.model.base_url == "http://desktop:1234/v1"
    assert cfg.model.temperature == 0.2
    assert cfg.limits.max_iterations == 50
    assert cfg.extras["demo_specific"]["foo"] == "bar"
