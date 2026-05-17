# Example 6 — Name-List Protection for Legal Filings

Demonstrates a deterministic name-list filter applied to a legal-research
agent's output. The agent summarises a case filing; the guard blocks the
summary from leaving the system if it contains any variant of a protected
minor's name.

---

## Threat model

A legal-research agent is given a court filing that names a minor plaintiff.
The filing contains the minor's name in many forms — full name, initials,
abbreviations. The agent's task is to produce a concise summary for a
researcher. Without a guard, the model may reproduce the protected name
verbatim, even when not explicitly instructed to avoid it.

The guard catches every literal string on the operator-supplied list. If the
model's output contains any of those strings (case-insensitive), the run is
rejected before the summary surfaces.

See `example_session.txt` for captured output from real runs of this demo.

---

## Prerequisites

- Python 3.10 or 3.11+
- [Ollama](https://ollama.com) running locally with the model tag created:
  ```
  ollama pull qwen3.5:9b
  ollama create qwen3.5:9b-65k -f examples/qwen3-9b-65k.Modelfile
  ```
- Repo installed: `pip install -e ".[dev]"` from the repo root

---

## Quick start

```bash
python -m examples.ex06_name_protection.run_demo
```

The demo loads `data/case_filing.txt` and `data/protected_names.txt` by default.

With custom paths:

```bash
python -m examples.ex06_name_protection.run_demo \
    --filing /path/to/other_filing.txt \
    --names /path/to/other_names.txt
```

With a custom config:

```bash
python -m examples.ex06_name_protection.run_demo --config /path/to/config.toml
```

---

## How the guard works

The guard holds a pre-lowercased copy of each protected name. For each model
response it checks whether any of those strings appears as a substring of the
response (case-insensitive). The first match causes a `RejectedOutcome`; the
rejection reason names the matched string.

**Match semantics:** exact case-insensitive substring. The operator enumerates
name variants in `data/protected_names.txt` — one per line. The current list
for the fictional minor "John Doe Jr." includes 25 variants such as:

```
John Doe Jr.
J. Doe
J D
JD
Johnny Doe
Joh Doe
…
```

---

## Honest limits

The guard catches **every literal string on the list, every time**. It does
**not** catch:

| Not caught | Example |
|---|---|
| Paraphrases | "the plaintiff", "the minor", "the underage party" |
| Misspellings not on the list | "Jhon Doe", "John Doe Jr" (if missing from list) |
| Indirect references | "his daughter", "the injured student" |
| Translations | equivalent names in another language |
| Descriptions | "the fourteen-year-old resident of Millhaven County" |

The correctness guarantee is exactly as wide as the list. Maintaining an
exhaustive list is the operator's responsibility. For a real deployment, the
operator would enumerate all known variants — possibly generated from a
canonical name using a normalisation tool.

Questions such as "who maintains the name list," "what audit happens before
the operator approves the list," and "what happens when the model's response
is rejected at runtime" are deployment concerns out of scope for this demo.
The demo demonstrates the **guard primitive**, not the full workflow.

---

## Verifying the guard

**Positive — real model, guard passes:**

```bash
python -m examples.ex06_name_protection.run_demo
```

The model summarises the filing. If the summary avoids every listed name
variant the guard passes and the summary is printed.
Output: `completed at 'done' — summary is clean`.

**Negative — name list filter (stub, guaranteed rejection):**

```bash
python -m examples.ex06_name_protection.run_demo \
    --stub "The plaintiff John Doe filed a complaint alleging negligence."
```

The stub injects a summary containing `John Doe`. No model call is made.
The guard rejects deterministically.
Expected output: `REJECTED by guard 'name_list_filter'` with the matched name shown.

**Negative — paraphrase only (stub, guard passes):**

```bash
python -m examples.ex06_name_protection.run_demo \
    --stub "The plaintiff filed a complaint alleging negligence."
```

The stub avoids every listed name variant. The guard passes.
Expected output: `completed at 'done' — summary is clean`.

To inspect the raw model output after a real-model rejection, read the audit log:

```bash
cat runs/ex06/audit/<run_id>.jsonl | python3 -m json.tool --no-ensure-ascii
```

The `node_exit` event for `call_model` carries the model's full response in
the run state, so a human reviewer can see what was suppressed.

---

## Context window requirements

The demo embeds a full legal filing in the user message. A typical filing
runs 1500-4000 tokens; combined with the system prompt, the model's
summary output, and any internal model framing, a single run can exceed
the default Ollama context window of 2048 tokens.

The demo uses `qwen3.5:9b-65k` (65536-token context window), created via
the shared Modelfile in `examples/qwen3-9b-65k.Modelfile`. This gives
ample headroom for any reasonable filing length.

Thinking mode is disabled via the `/no_think` directive appended to the
user message. Without this, Qwen3 generates several thousand reasoning
tokens before producing the visible summary, making each run take
minutes. With thinking disabled, summarization completes in tens of
seconds.

The KV cache for 65536 tokens uses roughly 6 GiB of VRAM on top of the
~5.6 GiB model weights — total ~12 GiB, within a 16 GiB GPU.

**Timeout:** With thinking disabled, summarization completes within a
few minutes for filings of typical length. The default
`timeout_seconds = 300.0` provides ample headroom; raise it if you
supply a very long custom filing via `--filing`, or if you switch to
a model without a fast-response mode.

---

## Python version note

Runs on Python 3.10 and later. Uses `tomli` backport on 3.10; stdlib `tomllib`
on 3.11+.
