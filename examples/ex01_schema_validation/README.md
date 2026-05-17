# Example 1 — Schema Validation with Layered Guards

Demonstrates three deterministic guard layers applied to a model's structured output — each layer catching a failure mode the previous one cannot.

| Layer | Guard | What it catches |
|---|---|---|
| 1 | `schema_validation` | Non-JSON output; wrong types; missing required fields |
| 2 | `semantic_constraints` | Values outside bounded domains (negative price, unknown category, malformed SKU) |
| 3 | `grounding` | Product IDs that look valid but don't exist in the operator's catalog |

Guards run in this order in the `after` slot of `call_model`. The first rejection halts the run; later guards do not execute. This is the layered-guard pattern: each guard does one thing, with one honest scope.

---

## Prerequisites

- Python 3.10 or 3.11+
- [Ollama](https://ollama.com) running locally with the model tag created:
  ```
  ollama pull qwen3.5:9b
  ollama create qwen3.5:9b-65k -f examples/ex01_schema_validation/Modelfile
  ```
- Repo installed: `pip install -e ".[dev]"` from the repo root

---

## Quick start

```bash
python -m examples.ex01_schema_validation.run_demo
```

Default prompt: `"Recommend a wireless mouse for my desk setup."`

With a custom prompt:

```bash
python -m examples.ex01_schema_validation.run_demo "Recommend a standing desk accessory"
```

With a custom config:

```bash
python -m examples.ex01_schema_validation.run_demo --config examples/ex01_schema_validation/config.toml
```

---

## Example prompts and expected outcomes

**Positive — all guards pass:**

```bash
python -m examples.ex01_schema_validation.run_demo "Recommend a wireless mouse"
```

The model picks a product verbatim from the catalog. All three guards pass.
Output: `completed at 'done'`.

---

**Negative 1 — grounding guard:** model hallucinates a SKU not in the catalog.

```bash
python -m examples.ex01_schema_validation.run_demo --no-catalog "Recommend a wireless mouse"
```

Without the catalog in the prompt the model invents a product ID (e.g. `SKU-2045`).
The schema and semantic guards pass; the grounding guard rejects.
Rejection reason: `product_id 'SKU-2045' not in operator catalog (model hallucination)`.

---

**Negative 2 — semantic guard:** stub injects a payload with a negative price.

```bash
python -m examples.ex01_schema_validation.run_demo --stub '{"product_id":"SKU-1001","name":"Wireless mouse","category":"peripherals","price_usd":-10.00,"in_stock":true}'
```

The schema guard passes (valid JSON, correct fields); the semantic guard rejects.
Rejection reason: `price_usd -10.0 out of range (0, 100000)`.

---

**Negative 3 — schema guard:** stub injects plain text instead of JSON.

```bash
python -m examples.ex01_schema_validation.run_demo --stub "not json at all"
```

The schema guard rejects immediately.
Rejection reason: `output is not valid JSON: Expecting value`.

---

## What the guards catch

| Failure | Caught by | Example |
|---|---|---|
| Model returns prose | Layer 1 | "I'd recommend a mouse..." |
| Missing `product_id` field | Layer 1 | `{"name": "Mouse", ...}` |
| `price_usd` is a string | Layer 1 | `{"price_usd": "24.99"}` |
| Negative price | Layer 2 | `{"price_usd": -50, ...}` |
| Unknown category | Layer 2 | `{"category": "networking", ...}` |
| SKU format wrong | Layer 2 | `{"product_id": "ITEM-5", ...}` |
| Hallucinated product | Layer 3 | `{"product_id": "SKU-9999", ...}` |
| Real SKU, invented name | Layer 3 | SKU-1001 with name "Gaming Mouse" |

## Honest limits

The guards catch **structure**, **bounded semantics**, and **catalog grounding**. They do **not** catch:

- Recommendations that exist in the catalog but are wrong for the user's stated need
- The model recommending an out-of-stock item when in-stock items exist
- The model omitting a better option it didn't consider
- The model's reasoning being wrong while its output is technically valid

These require different mechanisms (semantic search, user preference matching, retrieval from a live catalog) that are out of scope for this demo's threat model.

---

## Reading the audit log

Each run writes a JSON Lines audit log to `runs/ex01/audit/<run_id>.jsonl`:

```bash
cat runs/ex01/audit/<run_id>.jsonl | python3 -m json.tool --no-ensure-ascii
```

A rejected run will contain a `guard_reject` event with the rejecting guard's name and reason:

```json
{"event_type": "guard_reject", "payload": {"guard": "grounding", "reason": "product_id 'SKU-9999' not in operator catalog"}}
```

---

## Context window requirements

Qwen3 models use an internal "thinking" mode that generates reasoning tokens
before the visible response. For this demo, thinking mode is disabled via the
`/no_think` directive appended to each user message — the model responds
directly with JSON, keeping latency to a few seconds rather than several minutes.

The demo uses `qwen3.5:9b-65k` (65536-token context window) to avoid KV cache
clearing failures seen with smaller values on some Ollama builds. Create the
tag once with:

```bash
ollama create qwen3.5:9b-65k -f examples/ex01_schema_validation/Modelfile
```

The KV cache for 65536 tokens uses roughly 6 GiB of VRAM on top of the ~5.6 GiB
model weights — total ~12 GiB, within a 16 GiB GPU.

**Timeout:** With thinking disabled the model responds in seconds. The default
`timeout_seconds = 600.0` provides ample headroom.

---

## Python version note

Runs on Python 3.10 and later. Uses `tomli` backport on 3.10; stdlib `tomllib` on 3.11+.
