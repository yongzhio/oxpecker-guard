# Example 4a — Tool Allowlist with Human-in-the-Loop Gate

Demonstrates three deterministic containment layers applied to model-proposed tool calls:

| Layer | Mechanism | Failure mode prevented |
|---|---|---|
| 4 | Tool allowlist guard | Model proposes a tool outside the operator-approved set |
| 5 | Blast-radius router | High-risk tools bypass direct dispatch without review |
| 6 | HITL approval gate | High-risk tool executes without operator acknowledgement |

The guard and router are pure Python; neither calls the model. The gate pauses execution and writes a checkpoint to disk. The operator delivers a signal (`approved` or `rejected`) before the run continues.

See `example_session.txt` for captured output from real runs of this demo.

---

## Prerequisites

- Python 3.10 or 3.11+
- [Ollama](https://ollama.com) running locally with at least one model pulled
- The repo installed in editable mode: `pip install -e ".[dev]"` from the repo root

Pull a compatible model (the default config uses `qwen3.5:9b`):

```
ollama pull qwen3.5:9b
```

---

## Quick start

Run from the repo root:

```bash
python -m examples.ex04a_tool_allowlist.run_demo "list the files in /tmp"
```

With a custom config:

```bash
python -m examples.ex04a_tool_allowlist.run_demo "send a status report" \
    --config examples/ex04a_tool_allowlist/config.toml
```

---

## Configuration

Edit `examples/ex04a_tool_allowlist/config.toml` to change model, limits, or the checkpoint directory:

```toml
[model]
base_url = "http://localhost:11434/v1"   # Ollama default
model_name = "qwen3.5:9b"
temperature = 0.1
timeout_seconds = 120.0

[limits]
max_iterations = 20
max_model_calls = 6
max_tool_calls = 4

[storage]
checkpoint_dir = "runs/ex04a"
```

For LM Studio change `base_url` to `http://localhost:1234/v1`. For remote servers set `api_key` under `[model]`.

---

## Example prompts

**Low-risk path (no gate pause):**

```
python -m examples.ex04a_tool_allowlist.run_demo "read the file /etc/hostname"
```

The model calls `read_file`. The allowlist guard passes. The blast-radius router sends it to `dispatch_direct`. The run completes without a gate pause.

**High-risk path (gate pause):**

```
python -m examples.ex04a_tool_allowlist.run_demo "write a config file to /tmp/test.cfg"
```

The model calls `write_file`. The allowlist guard passes. The router classifies it as high blast-radius and routes to `approval_gate`. The run pauses:

```
--- Paused at gate 'approval_gate' (checkpoint <uuid>) ---

Tool call: 'write_file' — high blast-radius.
Valid signals: approved, rejected
Signal? [approved/rejected]:
```

Type `approved` to dispatch the tool, or `rejected` to refuse and end the run.

**Guard rejection (tool not on allowlist):**

```
python -m examples.ex04a_tool_allowlist.run_demo "run a shell command: ls -la"
```

If the model proposes `run_shell` (not on the allowlist), the guard rejects and the run halts immediately with a `RejectedOutcome`. No gate pause occurs.

---

## What happens at a gate pause

1. The runner saves a checkpoint to `runs/ex04a/<checkpoint_id>.json` before returning `PausedOutcome`.
2. The demo's pause/resume loop calls `ApprovalGate.elicit_signal()`, which reads from stdin.
3. The operator types `approved` or `rejected`.
4. The runner resumes from the checkpoint, marks it consumed, and continues the graph.

The checkpoint is a single-use token. Attempting to resume the same checkpoint twice raises `CheckpointConsumedError`.

In production, replace `elicit_signal()` with a Slack notification, a web-UI callback, or an MFA webhook. The signal delivery mechanism is the only part that changes; the graph topology, guard, and runner are unchanged.

---

## Audit log

Each run writes a JSON Lines audit log to `runs/ex04a/audit/<run_id>.jsonl`. Every node visit, guard verdict, gate pause, and signal is recorded. To inspect a run:

```bash
cat runs/ex04a/audit/<run_id>.jsonl | python3 -m json.tool --no-ensure-ascii
```

---

## Python version note

This example runs on Python 3.10 and later. On 3.10 it uses the `tomli` backport to read `config.toml`; on 3.11+ it uses the stdlib `tomllib`. No other version-specific code is present.

---

## Model-behaviour caveats

- **Model may not call a tool.** If the model returns a text response instead of a tool call, the graph reaches `classify_blast_radius` with no tool in state, routes to `dispatch_direct`, and completes normally. This is expected — not all prompts result in a tool call.
- **Model may call a disallowed tool.** The guard rejects it deterministically. The model's preference is irrelevant.
- **Model may not follow the one-tool-at-a-time instruction.** The client takes only the first tool call from the response (`_last_tool_call`). Additional calls in the same response are ignored in this demo.
- **Token / iteration limits.** If the model loops without reaching a terminal node, `max_iterations` or `max_model_calls` will terminate the run with `CapExceededOutcome`.
