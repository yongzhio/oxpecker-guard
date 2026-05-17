"""CLI entry point for Example 1 — schema validation demo.

Usage:
    python -m examples.ex01_schema_validation.run_demo
    python -m examples.ex01_schema_validation.run_demo "Recommend a standing desk accessory"
    python -m examples.ex01_schema_validation.run_demo --config /path/to/config.toml

The model is instructed to return a JSON product recommendation. Three
deterministic guards run after the model call: JSON schema check, semantic
constraints check, and catalog grounding check. Any failure produces a
RejectedOutcome; success produces a CompletedOutcome with the model's output
printed to stdout.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from examples.ex01_schema_validation.config import load_ex01_config
from examples.ex01_schema_validation.graph import build_graph
from examples.ex01_schema_validation.handlers import make_call_model_handler
from opg.core.audit import AuditLog
from opg.core.checkpoint import CheckpointStore
from opg.core.config import LimitsConfig, ModelConfig, OperatorConfig
from opg.core.model_client import ModelClient
from opg.core.orchestrator import (
    CapExceededOutcome,
    CompletedOutcome,
    ErrorOutcome,
    GraphRunner,
    RejectedOutcome,
)
from opg.core.state import Message, RunState

_DEFAULT_PROMPT = "Recommend a wireless mouse for my desk setup."

_SYSTEM_PROMPT = (
    "You are a product recommendation assistant. When the user asks for a recommendation, "
    "respond with a JSON object — no other text — containing exactly these fields: "
    "product_id (string), name (string), category (string), price_usd (number), in_stock (boolean). "
    "Return only the JSON object; do not include explanations or markdown formatting."
)

Outcome = CompletedOutcome | RejectedOutcome | CapExceededOutcome | ErrorOutcome


async def main(user_prompt: str, config_path: Path | None = None) -> None:
    cfg = load_ex01_config(config_path)

    model_cfg = ModelConfig(
        base_url=cfg.model.base_url,
        model_name=cfg.model.model_name,
        temperature=cfg.model.temperature,
        timeout_seconds=cfg.model.timeout_seconds,
        api_key=cfg.model.api_key,
    )
    op_cfg = OperatorConfig(
        model=model_cfg,
        limits=LimitsConfig(
            max_iterations=cfg.limits.max_iterations,
            max_model_calls=cfg.limits.max_model_calls,
        ),
    )

    state = RunState()
    state.append_message(Message(role="system", content=_SYSTEM_PROMPT))
    state.append_message(Message(role="user", content=user_prompt))

    checkpoint_dir = Path(cfg.storage.checkpoint_dir)
    checkpoint_store = CheckpointStore.at(checkpoint_dir)
    audit_dir = checkpoint_dir / "audit"

    print(f"run_id: {state.run_id}")
    print(f"model:  {cfg.model.model_name} @ {cfg.model.base_url}")
    print(f"prompt: {user_prompt!r}\n")

    async with ModelClient(model_cfg) as client:
        handler = make_call_model_handler(client)
        graph = build_graph(handler)

        with AuditLog.open(state.run_id, dir=audit_dir) as audit:
            runner = GraphRunner(graph, op_cfg, audit, checkpoint_store)
            outcome: Outcome = await runner.run(state)

    _print_summary(outcome)


def _print_summary(outcome: Outcome) -> None:
    print("\n=== Run summary ===")
    if isinstance(outcome, CompletedOutcome):
        c = outcome.state.counters
        assistant_msgs = [m for m in outcome.state.messages if m.role == "assistant"]
        print(f"Result:       completed at '{outcome.final_node}'")
        print(f"Model calls:  {c.model_calls}")
        print(f"Tokens in:    {c.input_tokens}  out: {c.output_tokens}")
        if assistant_msgs:
            print(f"\nModel output:\n{assistant_msgs[-1].content}")
    elif isinstance(outcome, RejectedOutcome):
        print(f"Result:       rejected by guard '{outcome.guard_name}'")
        print(f"At node:      {outcome.rejected_at_node} ({outcome.rejected_at_position})")
        print(f"Reason:       {outcome.reason}")
    elif isinstance(outcome, CapExceededOutcome):
        print(f"Result:       cap exceeded ({outcome.cap_name})")
    elif isinstance(outcome, ErrorOutcome):
        print(f"Result:       error in node '{outcome.node}'")
        print(f"Error:        {outcome.error_type}: {outcome.message}")


def _parse_args() -> tuple[str, Path | None]:
    prompt = _DEFAULT_PROMPT
    config_path: Path | None = None
    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] == "--config":
            if i + 1 >= len(args):
                print("--config requires a path argument")
                sys.exit(1)
            config_path = Path(args[i + 1])
            i += 2
        else:
            prompt = args[i]
            i += 1
    return prompt, config_path


if __name__ == "__main__":
    user_prompt, config_path = _parse_args()
    asyncio.run(main(user_prompt, config_path))
