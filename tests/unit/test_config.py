"""Operator config: pydantic model defaults."""

from __future__ import annotations

from opg.core.config import OperatorConfig


def test_config_defaults_construct() -> None:
    cfg = OperatorConfig()
    assert cfg.model.temperature == 0.0
    assert cfg.limits.max_iterations == 20
