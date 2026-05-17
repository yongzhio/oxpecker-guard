"""CLI entry point for Example 4a — tool allowlist with HITL gate.

Usage:
    python -m examples.ex04a_tool_allowlist.run_demo "list files in /tmp"
    python -m examples.ex04a_tool_allowlist.run_demo "send a report" --config /path/to/config.toml

When the graph pauses at the approval gate the operator is prompted on stdin
for a signal (approved/rejected). Execution continues or terminates based on
the signal. The full run is written to an audit log under the checkpoint
directory.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from examples.ex04a_tool_allowlist.config import load_ex04a_config
from examples.ex04a_tool_allowlist.graph import build_graph
from examples.ex04a_tool_allowlist.handlers import make_call_model_handler
from examples.ex04a_tool_allowlist.tools import EX04A_TOOLS
from opg.core.audit import AuditLog
from opg.core.checkpoint import CheckpointStore
from opg.core.config import LimitsConfig, ModelConfig, OperatorConfig
from opg.core.model_client import ModelClient
from opg.core.orchestrator import (
    CapExceededOutcome,
    CompletedOutcome,
    ErrorOutcome,
    GraphRunner,
    PausedOutcome,
    RejectedOutcome,
)
from opg.core.state import Message, RunState

Outcome = CompletedOutcome | RejectedOutcome | CapExceededOutcome | ErrorOutcome | PausedOutcome

_SYSTEM_PROMPT = (
    "You are a helpful assistant with access to file-system and email tools. "
    "Use the available tools to fulfil the user's request. "
    "Call one tool at a time and wait for the result before proceeding."
)


async def main(user_prompt: str, config_path: Path | None = None) -> None:
    cfg = load_ex04a_config(config_path)

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
            max_tool_calls=cfg.limits.max_tool_calls,
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
        handler = make_call_model_handler(client, EX04A_TOOLS)
        graph = build_graph(handler)

        with AuditLog.open(state.run_id, dir=audit_dir) as audit:
            runner = GraphRunner(graph, op_cfg, audit, checkpoint_store)
            outcome: Outcome = await runner.run(state)

            while isinstance(outcome, PausedOutcome):
                outcome = await _handle_gate_pause(runner, graph, outcome)

    _print_summary(outcome)


async def _handle_gate_pause(
    runner: GraphRunner,
    graph: object,
    outcome: PausedOutcome,
) -> Outcome:
    """Elicit a signal from the operator and resume the paused run."""
    gate = graph.gate_nodes[outcome.gate_name]  # type: ignore[attr-defined]
    print(f"\n--- Paused at gate '{outcome.gate_name}' (checkpoint {outcome.checkpoint_id}) ---")
    signal = gate.elicit_signal(outcome.state)
    print(f"Resuming with signal: {signal!r}\n")
    result: Outcome = await runner.resume(outcome.checkpoint_id, signal)
    return result


def _print_summary(outcome: Outcome) -> None:
    print("\n=== Run summary ===")
    if isinstance(outcome, CompletedOutcome):
        c = outcome.state.counters
        print(f"Result:       completed at '{outcome.final_node}'")
        print(f"Model calls:  {c.model_calls}")
        print(f"Tool calls:   {c.tool_calls}")
        print(f"Tokens in:    {c.input_tokens}  out: {c.output_tokens}")
    elif isinstance(outcome, RejectedOutcome):
        print(f"Result:       rejected by guard '{outcome.guard_name}'")
        print(f"At node:      {outcome.rejected_at_node} ({outcome.rejected_at_position})")
        print(f"Reason:       {outcome.reason}")
    elif isinstance(outcome, CapExceededOutcome):
        print(f"Result:       cap exceeded ({outcome.cap_name})")
    elif isinstance(outcome, ErrorOutcome):
        print(f"Result:       error in node '{outcome.node}'")
        print(f"Error:        {outcome.error_type}: {outcome.message}")
    else:
        # PausedOutcome should never reach here — the while loop above exhausts it.
        print(f"Result:       unexpected outcome type {type(outcome).__name__}")


def _parse_args() -> tuple[str, Path | None]:
    """Minimal sys.argv parsing — no third-party libraries."""
    if len(sys.argv) < 2:
        print("Usage: python -m examples.ex04a_tool_allowlist.run_demo <prompt> [--config <path>]")
        sys.exit(1)
    prompt = sys.argv[1]
    config_path: Path | None = None
    if "--config" in sys.argv:
        idx = sys.argv.index("--config")
        if idx + 1 >= len(sys.argv):
            print("--config requires a path argument")
            sys.exit(1)
        config_path = Path(sys.argv[idx + 1])
    return prompt, config_path


if __name__ == "__main__":
    user_prompt, config_path = _parse_args()
    asyncio.run(main(user_prompt, config_path))
