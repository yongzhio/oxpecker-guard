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

---

## Prerequisites

- Python 3.10 or 3.11+
- [Ollama](https://ollama.com) running locally with a model pulled:
  ```
  ollama pull qwen3.5:9b
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

To see a rejection, run the demo with the sample filing — the model is likely
to reproduce the protected name. When it does, the run is rejected and the
summary is suppressed:

```
Result:       REJECTED by guard 'name_list_filter'
Reason:       output contains protected name 'John Doe'
```

To see a passing run, provide a filing with no mention of any listed name, or
provide an empty names file.

To inspect the raw model output after a rejection, read the audit log:

```bash
cat runs/ex06/audit/<run_id>.jsonl | python3 -m json.tool --no-ensure-ascii
```

The `node_exit` event for `call_model` carries the model's full response in
the run state, so a human reviewer can see what was suppressed.

---

## Python version note

Runs on Python 3.10 and later. Uses `tomli` backport on 3.10; stdlib `tomllib`
on 3.11+.
