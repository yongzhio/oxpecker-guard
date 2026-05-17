"""CLI entry point for Example 6 — name-list protection demo.

Usage:
    python -m examples.ex06_name_protection.run_demo
    python -m examples.ex06_name_protection.run_demo --filing /path/to/filing.txt
    python -m examples.ex06_name_protection.run_demo --names /path/to/names.txt
    python -m examples.ex06_name_protection.run_demo --config /path/to/config.toml

The filing text is loaded from config.storage.case_filing_path by default.
The protected names list is loaded from config.storage.protected_names_path.

When the guard rejects, the matched protected name is printed. When the guard
passes, the model's summary is printed so the operator can review the output.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from examples.ex06_name_protection.config import (
    load_ex06_config,
    load_protected_names,
)
from examples.ex06_name_protection.graph import build_graph
from examples.ex06_name_protection.handlers import make_call_model_handler
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

_SYSTEM_PROMPT = (
    "You are a legal-research assistant. When given a legal filing, produce a "
    "concise summary of the key facts, procedural history, and parties involved. "
    "Aim for 200-300 words. Plain prose; no bullet points."
)

Outcome = CompletedOutcome | RejectedOutcome | CapExceededOutcome | ErrorOutcome


async def main(
    config_path: Path | None = None,
    filing_path: Path | None = None,
    names_path: Path | None = None,
) -> None:
    cfg = load_ex06_config(config_path)

    resolved_filing = filing_path or Path(cfg.storage.case_filing_path)
    resolved_names = names_path or Path(cfg.storage.protected_names_path)

    filing_text = resolved_filing.read_text(encoding="utf-8")
    protected_names = load_protected_names(resolved_names)

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
    # /no_think disables Qwen3's internal reasoning chain. For this demo, the
    # guard inspects the visible output, so the reasoning chain adds latency
    # without affecting what the guard sees. This directive is Qwen3-specific;
    # other models will ignore it.
    state.append_message(Message(role="user", content=f"{filing_text}\n\n/no_think"))

    checkpoint_dir = Path(cfg.storage.checkpoint_dir)
    checkpoint_store = CheckpointStore.at(checkpoint_dir)
    audit_dir = checkpoint_dir / "audit"

    print(f"run_id:          {state.run_id}")
    print(f"model:           {cfg.model.model_name} @ {cfg.model.base_url}")
    print(f"filing:          {resolved_filing}")
    print(f"protected names: {len(protected_names)} variants from {resolved_names}\n")

    async with ModelClient(model_cfg) as client:
        handler = make_call_model_handler(client)
        graph = build_graph(handler, protected_names)

        with AuditLog.open(state.run_id, dir=audit_dir) as audit:
            runner = GraphRunner(graph, op_cfg, audit, checkpoint_store)
            outcome: Outcome = await runner.run(state)

    _print_summary(outcome)


def _print_summary(outcome: Outcome) -> None:
    print("\n=== Run summary ===")
    if isinstance(outcome, CompletedOutcome):
        c = outcome.state.counters
        assistant_msgs = [m for m in outcome.state.messages if m.role == "assistant"]
        print(f"Result:       completed at '{outcome.final_node}' — summary is clean")
        print(f"Model calls:  {c.model_calls}")
        print(f"Tokens in:    {c.input_tokens}  out: {c.output_tokens}")
        if assistant_msgs:
            print(f"\nSummary:\n{assistant_msgs[-1].content}")
    elif isinstance(outcome, RejectedOutcome):
        print(f"Result:       REJECTED by guard '{outcome.guard_name}'")
        print(f"At node:      {outcome.rejected_at_node} ({outcome.rejected_at_position})")
        print(f"Reason:       {outcome.reason}")
        print("\nThe summary was suppressed. Review the audit log for the full model output.")
    elif isinstance(outcome, CapExceededOutcome):
        print(f"Result:       cap exceeded ({outcome.cap_name})")
    elif isinstance(outcome, ErrorOutcome):
        print(f"Result:       error in node '{outcome.node}'")
        print(f"Error:        {outcome.error_type}: {outcome.message}")


def _parse_args() -> tuple[Path | None, Path | None, Path | None]:
    config_path: Path | None = None
    filing_path: Path | None = None
    names_path: Path | None = None
    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] == "--config":
            if i + 1 >= len(args):
                print("--config requires a path argument")
                sys.exit(1)
            config_path = Path(args[i + 1])
            i += 2
        elif args[i] == "--filing":
            if i + 1 >= len(args):
                print("--filing requires a path argument")
                sys.exit(1)
            filing_path = Path(args[i + 1])
            i += 2
        elif args[i] == "--names":
            if i + 1 >= len(args):
                print("--names requires a path argument")
                sys.exit(1)
            names_path = Path(args[i + 1])
            i += 2
        else:
            print(f"Unknown argument: {args[i]!r}")
            print(
                "Usage: python -m examples.ex06_name_protection.run_demo "
                "[--config PATH] [--filing PATH] [--names PATH]"
            )
            sys.exit(1)
    return config_path, filing_path, names_path


if __name__ == "__main__":
    config_path, filing_path, names_path = _parse_args()
    asyncio.run(main(config_path, filing_path, names_path))
