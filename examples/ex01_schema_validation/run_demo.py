"""CLI entry point for Example 1 — schema validation demo.

Usage:
    python -m examples.ex01_schema_validation.run_demo
    python -m examples.ex01_schema_validation.run_demo "Recommend a standing desk accessory"
    python -m examples.ex01_schema_validation.run_demo --config /path/to/config.toml

    # Grounding guard demo — model hallucinates a SKU not in the catalog:
    python -m examples.ex01_schema_validation.run_demo --no-catalog "Recommend a wireless mouse"

    # Schema/semantic guard demo — inject a specific payload without a model call:
    python -m examples.ex01_schema_validation.run_demo --stub "not json at all"
    python -m examples.ex01_schema_validation.run_demo --stub '{"product_id":"SKU-1001","name":"Wireless mouse","category":"peripherals","price_usd":-10.00,"in_stock":true}'

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
from typing import Any

from examples.ex01_schema_validation.config import load_ex01_config
from examples.ex01_schema_validation.graph import build_graph
from examples.ex01_schema_validation.handlers import make_call_model_handler, make_model_stub
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
    "You are a product recommendation assistant. You have access to the following product catalog:\n"
    "SKU-1001: Wireless mouse | peripherals | $24.99 | in stock\n"
    "SKU-1002: Mechanical keyboard | peripherals | $89.50 | in stock\n"
    "SKU-1003: 27-inch monitor | displays | $329.00 | out of stock\n"
    "SKU-1004: USB-C hub | accessories | $39.95 | in stock\n"
    "SKU-1005: Laptop stand | accessories | $49.00 | in stock\n"
    "SKU-1006: Webcam 1080p | peripherals | $79.99 | in stock\n"
    "SKU-1007: Noise-cancelling headphones | audio | $199.00 | in stock\n"
    "SKU-1008: Standing desk converter | furniture | $215.00 | out of stock\n"
    "\n"
    "When the user asks for a recommendation, pick one product from the catalog above and "
    "respond with a JSON object — no other text — containing exactly these fields, "
    "copied verbatim from the catalog: "
    "product_id (string), name (string), category (string), price_usd (number), in_stock (boolean). "
    "Return only the JSON object; do not include explanations or markdown formatting."
)

_SYSTEM_PROMPT_NO_CATALOG = (
    "You are a product recommendation assistant. "
    "When the user asks for a recommendation, respond with a JSON object — no other text — "
    "containing exactly these fields: "
    "product_id (string, format: SKU-NNNN where NNNN is a four-digit number, e.g. SKU-1001), "
    "name (string), "
    "category (string, must be one of: peripherals, displays, accessories, audio, furniture), "
    "price_usd (number), in_stock (boolean). "
    "Return only the JSON object; do not include explanations or markdown formatting."
)

Outcome = CompletedOutcome | RejectedOutcome | CapExceededOutcome | ErrorOutcome


async def main(
    user_prompt: str,
    config_path: Path | None = None,
    no_catalog: bool = False,
    stub: str | None = None,
) -> None:
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

    system_prompt = _SYSTEM_PROMPT_NO_CATALOG if no_catalog else _SYSTEM_PROMPT
    state = RunState()
    state.append_message(Message(role="system", content=system_prompt))
    if stub is None:
        # /no_think disables Qwen3's internal reasoning chain, keeping latency low
        # for this structured-output task where thinking adds no value.
        state.append_message(Message(role="user", content=f"{user_prompt} /no_think"))
    else:
        state.append_message(Message(role="user", content=user_prompt))

    checkpoint_dir = Path(cfg.storage.checkpoint_dir)
    checkpoint_store = CheckpointStore.at(checkpoint_dir)
    audit_dir = checkpoint_dir / "audit"

    print(f"run_id: {state.run_id}")
    if stub is not None:
        print("mode:   stub")
        print(f"stub:   {stub!r}\n")
    else:
        print(f"model:  {cfg.model.model_name} @ {cfg.model.base_url}")
        if no_catalog:
            print("mode:   no-catalog (grounding demo)")
        print(f"prompt: {user_prompt!r}\n")

    async def _run(handler: Any) -> Outcome:
        graph = build_graph(handler)
        with AuditLog.open(state.run_id, dir=audit_dir) as audit:
            runner = GraphRunner(graph, op_cfg, audit, checkpoint_store)
            return await runner.run(state)

    if stub is not None:
        outcome: Outcome = await _run(make_model_stub(stub))
    else:
        async with ModelClient(model_cfg) as client:
            outcome = await _run(make_call_model_handler(client))

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


def _parse_args() -> tuple[str, Path | None, bool, str | None]:
    prompt = _DEFAULT_PROMPT
    config_path: Path | None = None
    no_catalog = False
    stub: str | None = None
    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] == "--config":
            if i + 1 >= len(args):
                print("--config requires a path argument")
                sys.exit(1)
            config_path = Path(args[i + 1])
            i += 2
        elif args[i] == "--no-catalog":
            no_catalog = True
            i += 1
        elif args[i] == "--stub":
            if i + 1 >= len(args):
                print("--stub requires a string argument")
                sys.exit(1)
            stub = args[i + 1]
            i += 2
        else:
            prompt = args[i]
            i += 1
    return prompt, config_path, no_catalog, stub


if __name__ == "__main__":
    user_prompt, config_path, no_catalog, stub = _parse_args()
    asyncio.run(main(user_prompt, config_path, no_catalog, stub))
