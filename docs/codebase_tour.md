# Oxpecker Guard codebase tour (v0)

A guided walkthrough of the v0 codebase. Reads as a tour rather than reference documentation — sections build on each other.

---

## Section 1: Project metadata and tooling

Files that aren't application code but make the project work.

**`pyproject.toml`** — The single source of truth for project metadata. Since 2021 this has been the canonical Python project file (replacing the old `setup.py` + `requirements.txt` + `MANIFEST.in` + various other files). Contains:

- The project's name, version, description, author, license
- Runtime dependencies (`pydantic`, `httpx`, `pyyaml`, plus the conditional `typing-extensions` for Python <3.11)
- Dev dependencies (`pytest`, `pytest-asyncio`, `ruff`, `mypy`, `types-pyyaml`)
- Configuration for the dev tools — `[tool.ruff]` controls linting, `[tool.mypy]` controls type checking, `[tool.pytest.ini_options]` controls test discovery
- Build system info (we use `setuptools`, the standard Python build backend)

When you ran `pip install -e ".[dev]"`, pip read this file to know what to install.

**`Makefile`** — The task runner shortcut. Bundles common command sequences as named targets (`make check`, `make test`, `make lint`, etc.) so you don't have to type each tool's command separately.

**`.gitignore`** — Tells git which files to ignore. Standard Python entries (`__pycache__/`, `*.pyc`, `*.egg-info/`, `.venv/`) plus repo-specific entries (`runs/` for audit logs, `*.audit.jsonl`, `benchmarks/results/*.json`). Build artifacts, virtual environments, and runtime outputs shouldn't be in version control.

**`.github/workflows/ci.yml`** — The GitHub Actions CI workflow. When you push to main or open a PR, GitHub spins up a fresh Ubuntu container, installs Python (currently testing both 3.10 and 3.12), installs OPG, runs ruff/mypy/pytest, and reports pass/fail.

Also at project level:

**`README.md`** — The trimmed public README, v3.
**`CHANGELOG.md`** — Version history.
**`benchmarks/reference_machine.md`** — Placeholder for the eventual reference machine spec (the desktop with the RTX 4060 Ti).

---

## Aside: pydantic

Pydantic shows up in nearly every file from section 2 onward. A library for **runtime data validation using Python type annotations**. You write a class like this:

```python
from pydantic import BaseModel

class User(BaseModel):
    name: str
    age: int
    email: str | None = None
```

…and pydantic gives you:

**1. Automatic validation on construction.**

```python
User(name="Alice", age=30)              # OK
User(name="Alice", age="not a number")  # ValidationError raised
User(name="Alice")                      # ValidationError — age is required
```

When you instantiate a pydantic model, pydantic checks every field against its declared type.

**2. Type coercion when reasonable.**

```python
User(name="Alice", age="30")  # OK — pydantic converts "30" → 30
```

If a string can be parsed into the declared type cleanly, pydantic does it. Useful when loading from JSON or YAML.

**3. Serialization to and from JSON.**

```python
user = User(name="Alice", age=30)
json_str = user.model_dump_json()
restored = User.model_validate_json(json_str)
```

This is what `RunState.model_dump_json()` and `Checkpoint.model_validate(data)` use under the hood.

**4. Nested validation.** Recursive validation of nested models and collections. Errors come with paths like `user.age: Input should be a valid integer`.

**5. Strict mode.** In OPG every model uses `model_config = ConfigDict(extra="forbid")`. Unknown fields raise rather than being silently ignored.

We use pydantic v2 throughout. v1 had different method names (`parse_obj()`, `dict()`, `json()`, inner `class Config`); v2 is faster and has better type inference.

The mental model: "dataclasses with validation and serialization built in." If you've used `@dataclass`, pydantic models look very similar — same field-with-type syntax — but they validate inputs and serialize cleanly.

---

## Section 2: Core types

Two files: `opg/core/state.py` and `opg/core/audit.py`.

### `opg/core/state.py`

The data shapes that the orchestrator passes around during a run. Five pydantic models in dependency order:

**`Role`** — A type alias:

```python
Role = Literal["system", "user", "assistant", "tool"]
```

**`ToolCall`** — A request from the model to invoke a tool:

```python
class ToolCall(BaseModel):
    id: str
    name: str
    arguments: dict[str, Any]
```

**`ToolResult`** — The result returned to the model after a tool was dispatched:

```python
class ToolResult(BaseModel):
    tool_call_id: str
    content: str
    is_error: bool = False
```

**`Message`** — The unit of conversation history:

```python
class Message(BaseModel):
    role: Role
    content: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    tool_result: ToolResult | None = None
```

For most messages, `content` carries the text. For assistant messages that requested tools, `tool_calls` is set. For tool-role messages returning a result, `tool_result` is set.

**`Counters`** — Deterministic budget tracking:

```python
class Counters(BaseModel):
    iterations: int = 0
    model_calls: int = 0
    tool_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
```

The runner updates these at well-defined points; guards read them.

**`RunState`** — The mutable object threaded through every node:

```python
class RunState(BaseModel):
    run_id: UUID = Field(default_factory=uuid4)
    started_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    user_id: str | None = None
    messages: list[Message] = Field(default_factory=list)
    counters: Counters = Field(default_factory=Counters)
    scratch: dict[str, Any] = Field(default_factory=dict)
```

Each run has a unique UUID, a timestamp, optionally a user identity, the conversation history, the budget counters, and a generic scratch dict for demo-specific values. Demos use `state.scratch["my_key"] = ...` to carry values across nodes without polluting the core API.

### `opg/core/audit.py`

The audit log. Three things in this file:

**`AUDIT_SCHEMA_VERSION = 1`** — A constant. Every event written includes this version.

**`EventType`** — A `Literal` type with all valid event types: `run_start`, `run_end`, `node_enter`, `node_exit`, `slot_enter`, `slot_exit`, `guard_pass`, `guard_reject`, `model_call_start`, `model_call_end`, `tool_dispatch_start`, `tool_dispatch_end`, `checkpoint_save`, `checkpoint_resume`, `error`.

**`AuditEvent`** — The structured record of a single event:

```python
class AuditEvent(BaseModel):
    schema_version: int = AUDIT_SCHEMA_VERSION
    event_id: UUID = Field(default_factory=uuid4)
    run_id: UUID
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    event_type: EventType
    payload: dict[str, Any] = Field(default_factory=dict)
```

The `payload` is intentionally loose — different event types carry different data. The runner is the source of truth for what each event's payload looks like.

**`AuditLog`** — The append-only writer, used as a context manager:

```python
with AuditLog.open(run_id, dir=Path("runs")) as log:
    log.emit("run_start", {"user_id": "alice"})
    log.emit("node_enter", {"node": "call_model"})
```

One file per run, named `<run_id>.jsonl`. Each line is a single JSON object — that's the JSON Lines format. Why not a single JSON array? If the process crashes mid-run, the file is still valid up to the crash point. Each line is independently parseable. The `emit()` method flushes after every write, so a crashed process leaves a readable log up to the crash.

**`read_log(path) -> list[AuditEvent]`** — Helper that parses a JSON Lines file back into a list of `AuditEvent` objects.

### Why this split

`RunState` mutates throughout a run — messages added, counters increment. The audit log is append-only — events go in, never out. Keeping them separate means the data flow is obvious: state is what *is*, audit log is what *happened*.

Reproducibility: given the audit log alone, you can reconstruct the run. Given the final state alone, you can't. The audit log is the canonical record; the state is just the working snapshot. This split is what lets MBT-10 (auditable orchestrator) work.

---

## Section 3: The graph abstraction

One file: `opg/core/graph.py`. About 290 lines, the largest file in the project.

### Layer 1 — Handler protocols

Three `Protocol` types describing the *shape* of callables the graph uses:

```python
class NodeHandler(Protocol):
    async def __call__(self, state: RunState) -> str | None: ...

class EdgePredicate(Protocol):
    def __call__(self, state: RunState) -> bool: ...

class GuardFn(Protocol):
    def __call__(self, state: RunState) -> GuardVerdict: ...
```

Protocols are Python's structural typing — any object that matches the signature counts, regardless of inheritance. So a function, a lambda, or a class with `__call__` all qualify as long as the signature matches.

Critically: handlers are async, predicates and guards are sync. Handlers may call the model or dispatch tools (network I/O). Predicates and guards must be deterministic and fast — making them async would invite the temptation to await an LLM, which would violate the deterministic-guard contract.

### Layer 2 — Guard verdicts

Two frozen dataclasses representing what a guard can return:

```python
@dataclass(frozen=True, slots=True)
class GuardPass:
    guard_name: str
    detail: str = ""

@dataclass(frozen=True, slots=True)
class GuardReject:
    guard_name: str
    reason: str

GuardVerdict = GuardPass | GuardReject
```

Dataclasses (not pydantic) — these are transient values that exist only between a guard returning and the runner consuming. The runner pattern-matches on the type of the verdict.

### Layer 3 — Slots, edges, nodes

The actual graph primitives:

```python
@dataclass(frozen=True, slots=True)
class GuardSlot:
    node_name: str
    position: SlotPosition  # Literal["before", "after"]
    guards: tuple[GuardFn, ...] = ()

@dataclass(frozen=True, slots=True)
class Edge:
    source: str
    target: str
    predicate: EdgePredicate | None = None
    label: str = ""

@dataclass(frozen=True, slots=True)
class Node:
    name: str
    handler: NodeHandler
    kind: str = "generic"
```

A **slot** is addressed by `(node_name, position)`. Each node has up to two slots — "before" runs before the handler, "after" runs after. A slot may bind zero or more guards.

An **edge** is directed from `source` to `target`. If `predicate` is None, the edge is unconditional. The `label` is for human-readable audit events; it never affects routing.

A **node** has a name, a handler, and a `kind` (a descriptive label like "model_call" emitted in audit events). The runner doesn't privilege any kind value.

### Layer 4 — Graph and GraphBuilder

The `Graph` class is the validated, immutable result you hand to the runner:

```python
@dataclass(frozen=True, slots=True)
class Graph:
    entry: str
    nodes: dict[str, Node]
    edges: dict[str, tuple[Edge, ...]]
    slots: dict[tuple[str, SlotPosition], GuardSlot]
    terminals: frozenset[str]
```

`GraphBuilder` is the demo-facing API — a fluent builder:

```python
graph = (
    GraphBuilder(entry="receive")
    .node("receive", handler=receive_request)
    .node("call_model", handler=call_model, kind="model_call")
    .node("done", handler=finalize)
    .edge("receive", "call_model")
    .edge("call_model", "done")
    .terminal("done")
    .guard_after("call_model", schema_validate)
    .build()
)
```

`build()` validates everything, in this order:

1. **Entry node exists.**
2. **Edge sources and targets exist.**
3. **Terminals are declared as nodes.**
4. **Terminals have no outgoing edges.**
5. **Non-terminals have outgoing edges.**
6. **At most one unconditional edge per source.**
7. **Slots reference declared nodes.**

When validation passes, the builder freezes its state into a `Graph`. After this, the `Graph` is immutable — the runner walks it but cannot modify it.

### Non-obvious design choices

**Frozen dataclasses for the graph, pydantic for the state.** Different needs. The graph is immutable once built; frozen dataclasses fit perfectly with no validation overhead at runtime. The state is mutable and crosses serialization boundaries; pydantic's validation and JSON support are what we need.

**Async handler, sync predicate/guard.** Asymmetry encoded in the type system. The sync constraint on guards is *enforcement*: if a guard wanted to be async, the natural reason would be "I need to call something" — the only thing it might call is the model, which would violate determinism.

**Builder instead of direct `Graph` construction.** Validation has to happen somewhere. Putting it in the constructor would make `Graph` complicated. The builder also makes demo code more readable.

**Slots addressed by `(node, position)` instead of per-edge.** Edge-keyed slots would mean a node with three outgoing edges has three "after" slots. More flexible but harder to reason about. Node-keyed slots match how operators actually think — a guard checks an operation, not a transition.

**Guards stored as a tuple, not a list.** Immutability after build.

---

## Section 4: The runner

One file: `opg/core/orchestrator.py`. About 230 lines. Takes a built `Graph`, an initial `RunState`, an `OperatorConfig`, and an `AuditLog`, walks the graph to completion (or rejection, cap-exceeded, or error).

### Outcome types

Four frozen dataclasses representing the four ways a run can end:

```python
@dataclass(frozen=True, slots=True)
class CompletedOutcome:
    final_node: str
    state: RunState

@dataclass(frozen=True, slots=True)
class RejectedOutcome:
    guard_name: str
    reason: str
    rejected_at_node: str
    rejected_at_position: SlotPosition
    state: RunState

@dataclass(frozen=True, slots=True)
class CapExceededOutcome:
    cap_name: str
    state: RunState

@dataclass(frozen=True, slots=True)
class ErrorOutcome:
    error_type: str
    message: str
    node: str
    state: RunState

Outcome = CompletedOutcome | RejectedOutcome | CapExceededOutcome | ErrorOutcome
```

The contract: `GraphRunner.run()` always returns an `Outcome`. It never raises. This means callers don't need defensive try/except, and the audit log captures every termination uniformly.

### `GraphRunner`

```python
class GraphRunner:
    def __init__(
        self,
        graph: Graph,
        config: OperatorConfig,
        audit: AuditLog,
    ) -> None: ...

    async def run(self, state: RunState) -> Outcome: ...
```

The runner doesn't own state. It takes a `RunState` parameter to `run()` and threads it through. Tests construct a runner and pass different seed states.

### The main loop

Each iteration represents one node visit. Conceptually:

```python
while True:
    # Cap check
    if state.counters.iterations >= self._config.limits.max_iterations:
        return CapExceededOutcome(cap_name="max_iterations", state=state)
    state.counters.iterations += 1

    # Before-slot
    rejection = self._run_slot(state, current, "before")
    if rejection is not None:
        return rejection

    # Node handler
    try:
        explicit_next = await node.handler(state)
    except Exception as exc:
        # Wrap as ErrorOutcome
        ...

    # After-slot
    rejection = self._run_slot(state, current, "after")
    if rejection is not None:
        return rejection

    # Terminal check
    if current in self._graph.terminals:
        return CompletedOutcome(final_node=current, state=state)

    # Edge resolution
    try:
        current = self._resolve_next(state, current, explicit_next)
    except RuntimeError as exc:
        return ErrorOutcome(...)
```

Order matters:

1. **Cap check first.** Even if every node and slot is empty, the cap prevents infinite loops.
2. **Before-slot before handler.** A guard rejection means the handler doesn't execute.
3. **Handler exceptions caught.** Any exception becomes `ErrorOutcome`. The runner's safety net.
4. **After-slot before terminal check.** Even on the last node, "after" guards still fire — useful for validating final output.
5. **Terminal check before edge resolution.** A terminal has no outgoing edges; resolving next would fail.
6. **Edge resolution last.**

### `_run_slot`

```python
def _run_slot(self, state, node_name, position) -> RejectedOutcome | None:
    slot = self._graph.slots.get((node_name, position))
    if slot is None or not slot.guards:
        return None  # empty slot — pass-through

    self._audit.emit("slot_enter", {...})
    for guard in slot.guards:
        verdict = guard(state)
        if isinstance(verdict, GuardPass):
            self._audit.emit("guard_pass", {...})
            continue
        # GuardReject
        self._audit.emit("guard_reject", {...})
        return RejectedOutcome(...)

    self._audit.emit("slot_exit", {...})
    return None
```

First rejection wins — subsequent guards in the same slot don't run.

### `_resolve_next`

Three priority levels:

1. **Explicit next override.** If the handler returned a node name, that wins.
2. **Conditional edges in declaration order.** First predicate that returns True is taken.
3. **Unconditional fallthrough.**

If none produce a result, raise `RuntimeError`. The main loop catches that and converts it to `ErrorOutcome`.

### Audit emissions

The runner emits at every meaningful transition: `run_start`/`run_end`, `node_enter`/`node_exit` for every visit, `slot_enter`/`slot_exit` only when the slot has guards, `guard_pass`/`guard_reject` for every evaluation, `error` when something fails.

The runner does **not** emit `model_call_start/end` or `tool_dispatch_start/end` — those are demo-handler concerns. The runner only knows it's running a node; it doesn't know what the node does.

### What the runner doesn't do

- **Doesn't call the model.** Handlers do, via `ModelClient` passed in via state.scratch or closure.
- **Doesn't dispatch tools.** Same pattern.
- **Doesn't interpret guard semantics.** A guard returns a verdict; the runner acts on the verdict.
- **Doesn't load config.** Config arrives constructed.
- **Doesn't handle checkpoints.** Checkpoint save/resume happens in handlers.

This is MBT-11 in action: the runner is held constant across demos.

### Counter updates

The runner increments `state.counters.iterations` at the top of each iteration. It does **not** increment `model_calls` or `tool_calls` — those are demo-handler concerns. An iteration cap (`max_iterations`) in the operator config is enforced by the runner directly. Model-call and tool-call caps are enforced via guards that read the counters.

---

## Section 5: Configuration and supporting modules

Three files at the runtime boundaries.

### `opg/core/config.py`

The operator config schema. Three pydantic models plus a YAML loader.

**`ModelConfig`**:

```python
class ModelConfig(BaseModel):
    base_url: str = "http://localhost:1234/v1"
    model_name: str = "qwen2.5-coder:32b"
    temperature: float = 0.0
    timeout_seconds: float = 120.0
    api_key: str | None = None
```

Defaults pointed at LM Studio (port 1234) with the qwen2.5-coder fallback model. Temperature 0 for reproducibility.

**`LimitsConfig`**:

```python
class LimitsConfig(BaseModel):
    max_iterations: int = 20
    max_model_calls: int = 10
    max_tool_calls: int = 20
    max_input_tokens: int = 100_000
    max_output_tokens: int = 10_000
```

Of these, only `max_iterations` is enforced by the runner directly. The rest are enforced by guards reading `state.counters`.

**`OperatorConfig`**:

```python
class OperatorConfig(BaseModel):
    model: ModelConfig = Field(default_factory=ModelConfig)
    limits: LimitsConfig = Field(default_factory=LimitsConfig)
    extras: dict[str, Any] = Field(default_factory=dict)
```

The `extras` dict is the extension point. A demo or guard that needs configuration the core doesn't know about (the underage-plaintiff name list, the action-class allowlist) stores it in `extras`. The core doesn't interpret extras.

**`load_config(path)`** — Loads from YAML, validates with pydantic. With `extra="forbid"` everywhere, typos in YAML produce errors immediately.

### `opg/core/checkpoint.py`

Durable serialization of `RunState` for human-in-the-loop pauses.

**`Checkpoint`**:

```python
class Checkpoint(BaseModel):
    schema_version: int = CHECKPOINT_SCHEMA_VERSION
    checkpoint_id: UUID = Field(default_factory=uuid4)
    run_id: UUID
    paused_at_node: str
    state: RunState
    note: str = ""
```

**`CheckpointStore`** — Filesystem-backed. One JSON file per checkpoint, indented for human readability. A reviewer might open one in a text editor.

The pattern: a "pause for review" node serializes state to a Checkpoint, saves it, run terminates. A separate process loads the checkpoint, optionally edits, resumes by handing state to a fresh runner. There's no in-memory mid-run pause — pauses are implemented by terminating and persisting.

In v0, the store is plumbed but no demo uses it yet. v0.1's example 4a (HITL approval) is the first.

### `opg/core/model_client.py`

The HTTP wrapper for the model server. About 130 lines.

**`ToolSpec`** — A tool description sent to the model. The `parameters` field is JSON Schema describing what arguments the tool accepts.

**`ModelResponse`** — A normalized response with text, tool_calls, token counts, finish reason.

**`ModelClient`** — Used as an async context manager:

```python
async with ModelClient(config) as client:
    resp = await client.chat(messages=[...], tools=[...])
```

Three parts inside:
- **Message serialization** to OpenAI wire format
- **Tool serialization** wrapped in OpenAI's function-calling envelope
- **Response parsing** with one piece of defensive handling — `_json_loads()` returns `{}` on parse failure rather than raising. Models occasionally emit malformed JSON for tool-call arguments; a schema-validation guard catches that downstream, but the model client itself shouldn't crash.

### Why these three are grouped

All sit at boundaries between the runner and the world:

- **Config** crosses disk → runtime
- **Checkpoint** crosses in-flight → paused
- **Model client** crosses Trust Domain 1 → Trust Domain 2

The runner doesn't know about any of them directly. Configs are passed in as constructed objects; checkpoints are saved/loaded by demo handlers; model calls happen in handlers the runner just awaits. Boundaries explicit, runner small.

---

## Section 6: The guards catalog

The directory `opg/guards/`. Three files, ~80 lines.

v0 ships only two guards. They're foundational rather than demo-specific — they enforce execution budgets, which every demo benefits from. The substantive guards (allowlist, schema_validate, name_list, action_class_classifier, etc.) ship with the demos that use them in v0.1+.

### `opg/guards/__init__.py`

```python
from opg.guards.iteration_cap import iteration_cap_guard
from opg.guards.tool_call_cap import tool_call_cap_guard

__all__ = ["iteration_cap_guard", "tool_call_cap_guard"]
```

### The factory pattern

```python
def iteration_cap_guard(max_iterations: int, name: str = "iteration_cap") -> GuardFn:
    if max_iterations < 1:
        raise ValueError("max_iterations must be at least 1")

    def _check(state: RunState) -> GuardVerdict:
        if state.counters.iterations > max_iterations:
            return GuardReject(
                guard_name=name,
                reason=f"iterations {state.counters.iterations} exceeds cap {max_iterations}",
            )
        return GuardPass(guard_name=name)

    return _check
```

`iteration_cap_guard` is a **factory function** — it doesn't *do* the guarding; it *returns* a guard. The demo calls it once at graph-build time:

```python
my_guard = iteration_cap_guard(max_iterations=10)
builder.guard_after("loop_node", my_guard)
```

The returned `_check` closes over `max_iterations` and `name`. When the runner later calls `_check(state)`, the configuration is baked in.

Three reasons the factory pattern is right here:

1. **Configuration capture.** A guard often needs config: a cap threshold, a name list, an allowlist, a schema. The factory takes config as arguments and returns a callable with config sealed inside.
2. **Naming.** Multiple instances of the same guard type with different configs in one graph each get distinct audit-log identities.
3. **Validation at construction.** Bad config raises at build time, where the demo author can fix it.

### Why factory and not class

A `class IterationCapGuard` with `__init__` and `__call__` would also satisfy `GuardFn`. Two reasons we went with factory functions:

1. **Less ceremony.** Function with closure vs. class boilerplate.
2. **The pattern signals "stateless function."** A class invites adding state (counters, caches, reset methods) that doesn't belong in a deterministic guard. Factory functions discourage it.

When a guard genuinely needs persistent state across calls, a class is fine. For the standard case of "config captured at construction, pure function of state at call time," factory functions are simpler.

### What guards look like in v0.1+

Preview of the first three substantive guards:

- **`schema_validate_guard(schema, target="last_message")`** — Example 1. Captures a JSON schema and the target message. Runs jsonschema validation.
- **`allowlist_guard(allowed_tools, blast_radius)`** — Example 4a. Captures the allowlist and per-tool blast-radius classification.
- **`name_list_guard(name_list_path)`** — Example 6. Loads the name list at construction, captures the compiled regex pattern. Scans the most recent assistant message.

All three follow the same factory pattern. None call an LLM.

---

## Section 7: The tests

The `tests/` directory. Two subdirectories, eight files, 44 tests total.

```
tests/
├── unit/
│   ├── test_state.py        (5 tests)
│   ├── test_audit.py        (2 tests)
│   ├── test_config.py       (2 tests)
│   ├── test_guards.py       (4 tests)
│   └── test_checkpoint.py   (1 test)
└── integration/
    ├── test_graph_builder.py    (13 tests)
    └── test_orchestrator.py     (17 tests)
```

The split: lighter touches on the foundations, comprehensive coverage on the runtime flow.

- **Unit tests** exercise one type or function in isolation
- **Integration tests** wire multiple components together

### Unit tests

14 tests. Each is a quick sanity check on the contract of one type or function.

- **`test_state.py`** — `RunState` constructs with sensible defaults, append messages works, JSON round-trip works (the contract that makes checkpoint storage possible), tool_calls work on assistant messages, `Counters` fields are independent.
- **`test_audit.py`** — Audit log round-trip (open, emit three events, read back identical). Safety check: emit on unopened log raises `RuntimeError` cleanly.
- **`test_config.py`** — Defaults construct cleanly; YAML file loads to typed object including `extras` dict.
- **`test_guards.py`** — `iteration_cap_guard` passes under the cap, rejects over with right reason, rejects construction with `max_iterations=0`. `tool_call_cap_guard` pass-and-reject.
- **`test_checkpoint.py`** — One round-trip: save state via store, load back, verify match.

### Integration tests

30 tests. The bulk of the suite.

**`test_graph_builder.py`** (13 tests) — Five happy paths covering minimal graph, kind metadata preservation, guard slots, conditional edges. Eight failure paths covering each validation rule: undeclared entry, undeclared edge source/target, undeclared terminal, terminal with outgoing edges, non-terminal without outgoing edges (dead-end check), node redeclaration, multiple unconditional edges, slot referencing undeclared node. Each failure test uses `pytest.raises(ValueError, match="...")` to pin both the error and the message.

**`test_orchestrator.py`** (17 tests) — Seven groups:

- **Linear flow** — Two-node happy path
- **Guard slots** — Five tests covering before/after slots, multiple guards in one slot, empty slots
- **Conditional edges** — Three tests covering predicate-true, unconditional fallthrough, no-match `ErrorOutcome`
- **Loops** — Two tests: positive loop with break-out predicate, runaway loop terminated by iteration cap
- **Explicit-next override** — Two tests: handler returning node name overrides edges, returning unknown node produces `ErrorOutcome`
- **Handler exceptions** — One test: raised exception becomes `ErrorOutcome`
- **Audit log content** — Three tests including the reproducibility check (same graph + same state → same event-type sequence)

The reproducibility test operationalizes MBT-1 at the runner level. It doesn't prove demos are reproducible (model is probabilistic) but does prove the runner is.

### What the test suite doesn't cover

- **No live model tests.** Model client has no tests in v0. Will be exercised in v0.1+ via `make test-live`.
- **No concurrency tests.** Runner is async but not stressed under concurrent load.
- **Limited config edge cases.** YAML loader's adversarial paths not exercised.
- **No checkpoint integration.** `test_checkpoint.py` tests in isolation; no test runs the orchestrator + saves a checkpoint mid-run + resumes from it.

These gaps are deliberate. v0 is foundations; coverage is calibrated for what v0 ships.

### How tests are run

`make test` runs everything. CI runs the same. Full suite runs in about 0.5 seconds.

```bash
pytest tests/integration/test_orchestrator.py::test_loop_terminates_via_predicate
pytest -k "guard"
pytest -x
pytest --pdb
```

### Why this much coverage

The runner is held constant across demos (MBT-11). Every future demo's claims rest on it. Catching subtle bugs in slot evaluation or edge resolution at v0 is much cheaper than discovering them mid-demo.

The graph builder's validation rules are also load-bearing. A demo author who wires up wrong should get a clear error at build time, not a confusing runtime crash.

The integration tests collectively form the runner's behavioral spec. Reading them gives a faster understanding of what the runner does than reading the runner code, because the tests describe inputs and expected outputs in plain terms.

---

## Section 8: Documentation and diagrams

In the public repo (`oxpecker-guard/`):

- **`README.md`** — Trimmed public README, currently v3. About 430 lines. Top-of-doc AI-usage note, sharpened thesis with NVIDIA OpenShell as system-level reference, layered/trust-domain mental model, MBTs and SBTs, anticipated objections, ten planned demos with the four-question structure.
- **`CHANGELOG.md`** — Single "Unreleased / v0 foundations" entry.
- **`docs/diagrams/`** — Four SVGs:
  - `trust_domains.svg` — five trust domains with mediated relationships
  - `abstract_orchestrator_model.svg` — generic graph-with-guard-slots structure
  - `component_architecture.svg` — static component zones
  - `runtime_flow_worked_example.svg` — demo 4a's twelve-step concrete run
- **`benchmarks/reference_machine.md`** — Placeholder, gets filled in when LLM serving is provisioned.

In private notes (not in the public repo):

- **Level-set v5** — 40 pages. Full architectural content plus dismissals enumeration, decision recordkeeping, open-questions log, more detailed reasoning behind each MBT/SBT. Source of truth for design decisions.

Relationship: README is the *what*, level-set is the *why*. Diagrams cut across both. CHANGELOG bridges versions.

---

## Closing notes

The repo fits comfortably in working memory: project metadata + scaffolding, six core modules totaling ~1000 lines of Python, two foundational guards, a 700-line test suite, and the docs/diagrams. No framework dependencies, no hidden machinery, every file readable in under five minutes.

When v0.1 starts, the orchestrator core stays exactly as-is. What gets added:

- One example directory per demo: `examples/01_schema_validation/`, `examples/04a_tool_allowlist_misuse/`, `examples/06_underage_plaintiff/` — each with handler module, operator config YAML, graph spec, demo README
- Three new substantive guards: `schema_validate.py`, `allowlist.py`, `name_list.py`
- New tests including the first live-model tests gated behind `make test-live`
- Performance numbers in `benchmarks/results/` once the desktop is set up to serve the model

No restructuring, no breaking changes. The foundations are built for additive growth.
