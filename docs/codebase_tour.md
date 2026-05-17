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

**`README.md`** — The public README.
**`CHANGELOG.md`** — Version history.
**`benchmarks/reference_machine.md`** — Placeholder for the eventual reference machine spec.

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
```

Each run has a unique UUID, a timestamp, optionally a user identity, the conversation history, and the budget counters. The `messages` list is both the model's working context and an auditable artifact — a forensic reader comparing message snapshots across audit events can detect whether a handler modified or filtered the conversation history between two transitions.

### `opg/core/audit.py`

The audit log. Three things in this file:

**`AUDIT_SCHEMA_VERSION = 1`** — A constant. Every event written includes this version.

**`EventType`** — A `Literal` type enumerating all valid event types:

```
run_start, run_end
node_enter, node_exit
slot_enter, slot_exit
guard_pass, guard_reject
gate_enter                  # gate node reached; run pausing
gate_signal                 # resume() called with valid signal
checkpoint_save             # checkpoint written to disk
checkpoint_resume           # checkpoint consumed and run continuing
checkpoint_abandoned        # checkpoint sealed without resumption
model_call_start, model_call_end
tool_dispatch_start, tool_dispatch_end
error
```

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

### Two auditable artifacts, not one

`RunState` mutates throughout a run — messages added, counters increment. The audit log is append-only — events go in, never out. Both are auditable artifacts, and they're complementary in what they reveal:

- The **audit log** is the witness account. It records every orchestration event — gate enters, guard passes, node visits — with a timestamp and structured payload. It is the source of truth for what the orchestrator did and when.
- The **`messages` list** is the model's working context. It's visible in `RunState` and can be snapshotted in audit payloads. A handler that modifies or filters the messages list between two audit events makes that modification detectable to a forensic reader comparing the before-and-after snapshots.

Reading them together gives the complete picture. The audit log can't tell you what the model was shown; messages can. The messages list can't tell you what the orchestrator decided; the audit log can.

The audit log is not the authentication mechanism. The orchestrator does not consult the log to validate signals or decide whether an authorization is legitimate. The log records that authorizations happened — what was asked (`gate_enter`), what was signaled (`gate_signal`), and metadata about the source. Authentication itself happens in the authenticator before the signal reaches `resume()`; the audit log captures the outcome.

---

## Section 3: The graph abstraction

One file: `opg/core/graph.py`.

### Layer 1 — Handler and guard protocols

Two `Protocol` types describing the shape of callables the graph uses:

```python
class NodeHandler(Protocol):
    async def __call__(self, state: RunState) -> str | None: ...

class GuardFn(Protocol):
    def __call__(self, state: RunState) -> GuardVerdict: ...
```

Protocols are Python's structural typing — any object that matches the signature counts, regardless of inheritance. A function, a lambda, or a class with `__call__` all qualify as long as the signature matches.

Critically: handlers are async, guards are sync. Handlers may call the model or dispatch tools (network I/O). Guards must be deterministic and fast — making them async would invite the temptation to await an LLM, which would violate the deterministic-guard contract. The sync constraint is enforcement via the type system.

A handler returning a `str` names the next node to visit (the "explicit-next" pattern). Returning `None` means: follow the single outgoing edge. Returning `None` when there are multiple outgoing edges is a runner-detected error.

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

The graph primitives:

```python
@dataclass(frozen=True, slots=True)
class GuardSlot:
    node_name: str
    guards: tuple[GuardFn, ...] = ()

@dataclass(frozen=True, slots=True)
class Edge:
    source: str
    target: str
    label: str = ""

@dataclass(frozen=True, slots=True)
class Node:
    name: str
    handler: NodeHandler
    kind: str = "generic"
```

A **slot** is addressed by node name alone; its position (before or after the handler) is implicit in which collection it lives in — `Graph.before_slots` or `Graph.after_slots`. An empty slot (no guards) is a pass-through.

An **edge** is unconditional. It has a source, a target, and an optional human-readable label. There are no predicates on edges; branching happens in handlers via explicit-next.

A **node** has a name, an async handler, and a `kind` (a descriptive label like `"model_call"` emitted in audit events). The runner doesn't privilege any kind value.

### Layer 4 — Gate nodes

```python
class GateNode(ABC):
    def __init__(
        self,
        name: str,
        signals: tuple[str, ...],
        routing: dict[str, str],
    ) -> None: ...

    @abstractmethod
    def elicit_signal(self, state: RunState) -> str: ...
```

`GateNode` is an abstract base class, not a frozen dataclass. Demos subclass it and implement `elicit_signal()` to fit their deployment's signal-delivery mechanism.

Three properties at construction time:
- `name` — unique node identifier, addressable by edges.
- `signals` — the finite, named signal vocabulary declared at build time. Every value must have a routing entry.
- `routing` — maps each signal value to the name of the next node.

`elicit_signal()` is not called by the runner. When the runner reaches a gate node it pauses with `PausedOutcome`; it's the *caller's* responsibility to invoke `gate.elicit_signal()` (or any other delivery mechanism) and pass the result to `runner.resume()`.

Why abstract class and not frozen dataclass? Gate nodes need subclass implementations — the runner calls no handler on them, but the deployment layer calls `elicit_signal()`. Subclassing ABC gives demos a clean extension point. Why abstract and not just a base class with a default? Making `elicit_signal()` abstract forces the demo author to make a deliberate choice about the signal-delivery mechanism.

### Layer 5 — Graph and GraphBuilder

The `Graph` class is the validated, immutable result you hand to the runner:

```python
@dataclass(frozen=True, slots=True)
class Graph:
    entry: str
    nodes: dict[str, Node]
    edges: dict[str, tuple[Edge, ...]]
    before_slots: dict[str, GuardSlot]
    after_slots: dict[str, GuardSlot]
    gate_nodes: dict[str, GateNode]
```

Two slot collections keyed by node name, one per position. A gate node that has a guard in `before_slots` is rejected at build time (bypassing a gate's required pause is forbidden). Gate nodes have a separate namespace from regular nodes — both are validated for naming conflicts.

`Graph.compute_hash()` returns a SHA-256 over the graph's structural content (node names, kinds, edges, guard identities, gate node names, signals, and routing). Cosmetic fields (edge labels) are excluded. The hash is stored in a checkpoint at pause time; `resume()` rejects the checkpoint if the hash no longer matches the current graph.

`GraphBuilder` is the demo-facing API — a fluent builder:

```python
gate = ApprovalGate(
    name="approval_gate",
    signals=("approved", "rejected"),
    routing={"approved": "dispatch", "rejected": "refuse"},
)

graph = (
    GraphBuilder(entry="receive")
    .node("receive", handler=receive_request)
    .node("call_model", handler=call_model, kind="model_call")
    .node("dispatch", handler=dispatch_tool)
    .node("refuse", handler=refuse)
    .gate_node(gate)
    .edge("receive", "call_model")
    .edge("call_model", "approval_gate")
    .guard_after("call_model", allowlist_guard(ALLOWED_TOOLS))
    .build()
)
```

`build()` validates everything before freezing the graph:

1. **Entry node exists** (in either regular or gate namespace).
2. **Gate names don't conflict with regular node names.**
3. **Every gate signal has a routing target; every routing target is a declared node.**
4. **No outgoing edges from gate nodes** — the routing map IS the gate's outgoing connections.
5. **Edge sources and targets exist.**
6. **No duplicate edges** (same source and target).
7. **No `before` slot on gate nodes** — bypassing a gate is forbidden.
8. **Slots reference declared nodes.**

When validation passes, the builder freezes its state into a `Graph`. After this, the `Graph` is immutable.

### Non-obvious design choices

**Frozen dataclasses for the graph, pydantic for the state.** Different needs. The graph is immutable once built; frozen dataclasses fit perfectly with no validation overhead at runtime. The state is mutable and crosses serialization boundaries; pydantic's validation and JSON support are what we need.

**Async handler, sync guard.** Asymmetry encoded in the type system. The sync constraint on guards is enforcement: if a guard wanted to be async, the only thing it might plausibly await is an LLM, which would violate determinism.

**Explicit-next routing, no edge predicates.** Dropping edge predicates means branching decisions live explicitly in handler code, not in predicate closures separate from the handler. The diff between "this demo branches left" and "this demo branches right" is in the handler body — a single, readable change.

**Position implicit in slot collection.** Two dicts (`before_slots`, `after_slots`) instead of one dict keyed by `(node, position)`. Operators reason about positions as "before the handler" and "after the handler" — two named collections match that mental model more naturally than a composite key. It also makes the no-before-slot-on-gates rule easy to enforce: just check `before_slots`.

**Builder instead of direct `Graph` construction.** Validation has to happen somewhere. Putting it in the constructor would make `Graph` complicated and mix construction with validation. The builder also makes demo code more readable.

---

## Section 4: The runner

One file: `opg/core/orchestrator.py`. Takes a built `Graph`, an initial `RunState`, an `OperatorConfig`, an `AuditLog`, and an optional `CheckpointStore`, walks the graph to completion (or rejection, cap-exceeded, error, or pause).

### Outcome types

Five frozen dataclasses representing the five ways a run can end:

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

@dataclass(frozen=True, slots=True)
class PausedOutcome:
    checkpoint_id: UUID
    gate_name: str
    signals: tuple[str, ...]
    state: RunState

Outcome = CompletedOutcome | RejectedOutcome | CapExceededOutcome | ErrorOutcome | PausedOutcome
```

The contract: `GraphRunner.run()` always returns an `Outcome`. It never raises. Callers don't need defensive try/except, and the audit log captures every termination uniformly.

`PausedOutcome` is not a terminal state — the run continues when `runner.resume()` is called. The other four are terminal.

### Exception types

Three exceptions used by `resume()` and `abandon_checkpoint()`:

```python
class CheckpointConsumedError(Exception): ...
class CheckpointAbandonedError(Exception): ...
class GraphVersionMismatchError(Exception): ...
```

These are raised by resume/abandon when the checkpoint's state or graph hash makes continuation illegitimate. They are not caught by the runner — they propagate to the caller, which decides how to handle them.

### `GraphRunner`

```python
class GraphRunner:
    def __init__(
        self,
        graph: Graph,
        config: OperatorConfig,
        audit: AuditLog,
        checkpoint_store: CheckpointStore | None = None,
    ) -> None: ...

    async def run(self, state: RunState) -> Outcome: ...
    async def resume(self, checkpoint_id: UUID, signal: str, metadata: dict | None = None) -> Outcome: ...
    def abandon_checkpoint(self, checkpoint_id: UUID, reason: str, abandoned_by: str | None = None) -> None: ...
```

The runner doesn't own state. It takes a `RunState` parameter to `run()` and threads it through. Tests construct a runner and pass different seed states. `checkpoint_store` is required when the graph contains gate nodes; a runner without a store raises `RuntimeError` if it reaches a gate.

`run()` emits `run_start` and delegates to `_run_from(state, entry_node)`. `resume()` emits `gate_signal` and `checkpoint_resume` then delegates to `_run_from(state, next_node)`. This means `run_end` is always emitted from the same code path — it doesn't matter whether the run completed directly or after one or more resumes.

### The main loop (`_run_from`)

Each iteration represents one node visit. Conceptually:

```python
async def _run_from(self, state, start_node) -> Outcome:
    current = start_node
    while True:
        # Gate check — FIRST, before cap/increment
        if current in self._graph.gate_nodes:
            return await self._handle_gate(state, current)

        # Cap check
        if state.counters.iterations >= self._config.limits.max_iterations:
            return CapExceededOutcome(cap_name="max_iterations", state=state)
        state.counters.iterations += 1

        # Before-slot
        rejection = self._run_slot(state, current, "before")
        if rejection is not None:
            return rejection

        # Node handler
        explicit_next = await node.handler(state)  # exceptions → ErrorOutcome

        # After-slot
        rejection = self._run_slot(state, current, "after")
        if rejection is not None:
            return rejection

        # Resolve next (None = sink node, run completes)
        next_node = self._resolve_next(state, current, explicit_next)
        if next_node is None:
            return CompletedOutcome(final_node=current, state=state)

        current = next_node
```

Order matters:

1. **Gate check first.** Gate nodes do not consume an iteration tick — they're pause points, not agent activity. Checking before the cap/increment ensures reaching a gate is cost-free.
2. **Cap check before increment.** Even if every node and slot is empty, the cap prevents infinite loops.
3. **Before-slot before handler.** A guard rejection means the handler doesn't execute.
4. **Handler exceptions caught.** Any exception becomes `ErrorOutcome`.
5. **After-slot before resolution.** Even on the last node, "after" guards still fire — useful for validating final output.
6. **Sink detection.** A node with no outgoing edges whose handler returns `None` is the completion signal. No `terminals` declaration is needed; completion is detected dynamically.

### `_handle_gate`

```python
async def _handle_gate(self, state, gate_name) -> PausedOutcome:
    gate = self._graph.gate_nodes[gate_name]
    self._audit.emit("gate_enter", {"gate": gate_name, "signals": list(gate.signals)})
    checkpoint = Checkpoint(
        run_id=state.run_id,
        paused_at_node=gate_name,
        gate_signals=gate.signals,
        graph_hash=self._graph.compute_hash(),
        state=state,
    )
    self._checkpoint_store.save(checkpoint)
    self._audit.emit("checkpoint_save", {"checkpoint_id": str(checkpoint.checkpoint_id), ...})
    return PausedOutcome(checkpoint_id=checkpoint.checkpoint_id, gate_name=gate_name, ...)
```

No `run_end` is emitted here — the run is pausing, not ending. `run_end` is emitted only when `_run_from` terminates with a terminal outcome.

### `resume`

```python
async def resume(self, checkpoint_id, signal, metadata=None) -> Outcome:
    checkpoint = self._checkpoint_store.load(checkpoint_id)

    if checkpoint.status == "consumed":   raise CheckpointConsumedError(checkpoint_id)
    if checkpoint.status == "abandoned":  raise CheckpointAbandonedError(checkpoint_id)

    if checkpoint.graph_hash != self._graph.compute_hash():
        raise GraphVersionMismatchError(...)

    gate = self._graph.gate_nodes[checkpoint.paused_at_node]
    if signal not in gate.signals:
        raise ValueError(f"signal {signal!r} not in {gate.signals!r}")

    # Mark consumed before continuing — single-use guarantee
    self._checkpoint_store.save(checkpoint.model_copy(update={"status": "consumed", ...}))
    self._audit.emit("gate_signal", {"signal": signal, "metadata": metadata or {}, ...})
    self._audit.emit("checkpoint_resume", {...})

    return await self._run_from(checkpoint.state, gate.routing[signal])
```

Three validations before continuing: status is pending, graph hash matches, signal is in the enumeration. The checkpoint is marked consumed *before* `_run_from` is called — if the resumed run crashes, the checkpoint is already consumed, preventing a second resume attempt against corrupted state.

### `_run_slot`

```python
def _run_slot(self, state, node_name, position) -> RejectedOutcome | None:
    slot_dict = self._graph.before_slots if position == "before" else self._graph.after_slots
    slot = slot_dict.get(node_name)
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

```python
def _resolve_next(self, state, current, explicit_next) -> str | None:
    if explicit_next is not None:
        all_nodes = set(self._graph.nodes) | set(self._graph.gate_nodes)
        if explicit_next not in all_nodes:
            raise RuntimeError(f"node {current!r} returned explicit next {explicit_next!r} ...")
        return explicit_next

    edges = self._graph.edges.get(current, ())
    if not edges:
        return None  # sink node — run completes here

    if len(edges) > 1:
        raise RuntimeError(f"node {current!r} has multiple edges and handler returned None ...")

    return edges[0].target
```

No predicate evaluation — edges are unconditional. `explicit_next` is checked against both regular nodes and gate nodes, because a handler can explicitly route to a gate node by name.

### Audit emissions

The runner emits at every meaningful transition: `run_start`/`run_end`, `node_enter`/`node_exit` for every visit, `slot_enter`/`slot_exit` only when the slot has guards, `guard_pass`/`guard_reject` for every evaluation, `gate_enter` when a gate is reached, `gate_signal` and `checkpoint_resume` when resume is called, `checkpoint_abandoned` when a checkpoint is sealed, `error` when something fails.

The runner does **not** emit `model_call_start/end` or `tool_dispatch_start/end` — those are demo-handler concerns. The runner only knows it's running a node; it doesn't know what the node does.

### What the runner doesn't do

- **Doesn't call the model.** Handlers do, via `ModelClient` passed in via closure.
- **Doesn't dispatch tools.** Same pattern.
- **Doesn't interpret guard semantics.** A guard returns a verdict; the runner acts on the verdict.
- **Doesn't load config.** Config arrives constructed.
- **Doesn't elicit gate signals.** The runner pauses with `PausedOutcome`; the deployment layer elicits the signal and calls `resume()`.

This is MBT-11 in action: the runner is held constant across demos.

### Counter updates

The runner increments `state.counters.iterations` at the top of each regular-node visit. Gate node visits do not increment the counter (decision 14). The runner does **not** increment `model_calls` or `tool_calls` — those are demo-handler concerns. An iteration cap is enforced by the runner directly; model-call and tool-call caps are enforced via guards that read the counters.

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

The `extras` dict is the extension point for demo-specific configuration (the underage-plaintiff name list, the action-class allowlist, etc.). The core doesn't interpret extras.

**`load_config(path)`** — Loads from YAML, validates with pydantic. With `extra="forbid"` everywhere, typos in YAML produce errors immediately.

### `opg/core/checkpoint.py`

Durable serialization of `RunState` for gate-node pauses.

**`Checkpoint`**:

```python
class Checkpoint(BaseModel):
    schema_version: int = CHECKPOINT_SCHEMA_VERSION
    checkpoint_id: UUID = Field(default_factory=uuid4)
    run_id: UUID
    paused_at_node: str               # gate node name
    gate_signals: tuple[str, ...]     # gate's declared signal vocabulary at pause time
    graph_hash: str                   # structural hash; validated on resume
    status: Literal["pending", "consumed", "abandoned"] = "pending"
    consumed_at: datetime | None = None
    abandoned_at: datetime | None = None
    abandoned_reason: str | None = None
    state: RunState
    note: str = ""
```

State machine: `pending → consumed` (resumed) or `pending → abandoned` (explicitly sealed). Once consumed or abandoned, the checkpoint cannot be resumed — attempts raise `CheckpointConsumedError` or `CheckpointAbandonedError`. The file is never deleted; it's preserved with the terminal status for audit.

**`CheckpointStore`** — Filesystem-backed. One JSON file per checkpoint, `<checkpoint_id>.json`. A reviewer might open one in a text editor.

The pattern: when a gate node is reached, the runner serializes state to a Checkpoint via `CheckpointStore`, exits with `PausedOutcome`. The deployment layer obtains a signal and calls `runner.resume(checkpoint_id, signal)`. `resume()` loads the checkpoint, validates it, marks it consumed, and continues the run.

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

The runner doesn't know about any of them directly. Configs are passed in as constructed objects; checkpoints are managed by the runner's gate-node machinery; model calls happen in handlers the runner just awaits. Boundaries explicit, runner small.

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
2. **Naming.** Multiple instances of the same guard type with different configs each get distinct audit-log identities.
3. **Validation at construction.** Bad config raises at build time, where the demo author can fix it.

### Why factory and not class

A `class IterationCapGuard` with `__init__` and `__call__` would also satisfy `GuardFn`. Two reasons we went with factory functions:

1. **Less ceremony.** Function with closure vs. class boilerplate.
2. **The pattern signals "stateless function."** A class invites adding state (counters, caches, reset methods) that doesn't belong in a deterministic guard. Factory functions discourage it.

When a guard genuinely needs persistent state across calls, a class is fine. For the standard case of "config captured at construction, pure function of state at call time," factory functions are simpler.

### What guards look like in v0.1+

The first substantive guard ships in Example 4a (`examples/ex04a_tool_allowlist/graph.py`):

```python
def tool_allowlist_guard(allowed_tools: frozenset[str], name: str = "tool_allowlist") -> GuardFn:
    def _check(state: RunState) -> GuardVerdict:
        tool = _last_tool_call(state)
        if tool is None:
            return GuardPass(guard_name=name, detail="no tool call")
        if tool.name not in allowed_tools:
            return GuardReject(guard_name=name, reason=f"tool {tool.name!r} not on allowlist")
        return GuardPass(guard_name=name, detail=f"tool {tool.name!r} is allowed")
    return _check
```

Same factory pattern. Reads `state.messages` to find the last tool call; never calls an LLM.

---

## Section 7: The tests

The `tests/` directory. Three subdirectories, ten files.

```
tests/
├── unit/
│   ├── test_state.py           (5 tests)
│   ├── test_audit.py           (2 tests)
│   ├── test_config.py          (2 tests)
│   ├── test_guards.py          (4 tests)
│   └── test_checkpoint.py      (2 tests)
├── integration/
│   ├── test_graph_builder.py   (11 tests)
│   ├── test_orchestrator.py    (17 tests)
│   └── test_gate_node.py       (15 tests)
└── examples/
    └── test_ex04a.py           (6 tests)
```

The split: lighter touches on the foundations, comprehensive coverage on the runtime flow.

### Unit tests

15 tests. Each is a quick sanity check on the contract of one type or function.

- **`test_state.py`** — `RunState` constructs with sensible defaults, append messages works, JSON round-trip works (the contract that makes checkpoint storage possible), tool_calls work on assistant messages, `Counters` fields are independent.
- **`test_audit.py`** — Audit log round-trip (open, emit three events, read back identical). Safety check: emit on unopened log raises `RuntimeError` cleanly.
- **`test_config.py`** — Defaults construct cleanly; YAML file loads to typed object including `extras` dict.
- **`test_guards.py`** — `iteration_cap_guard` passes under the cap, rejects over with right reason, rejects construction with `max_iterations=0`. `tool_call_cap_guard` pass-and-reject.
- **`test_checkpoint.py`** — Round-trip save + load, and default status is `pending`.

### Integration tests

43 tests across three files.

**`test_graph_builder.py`** (11 tests) — Five happy paths covering minimal graph, kind metadata, guard slots, multiple outgoing edges, and sink-node warning. Six failure paths covering each validation rule: undeclared entry, undeclared edge source/target, node redeclaration, duplicate edges, slot on undeclared node. Each failure test uses `pytest.raises(ValueError, match="...")` to pin both the error and the message.

**`test_orchestrator.py`** (17 tests) — Seven groups:

- **Linear flow** — Two-node happy path
- **Guard slots** — Five tests covering before/after slots, multiple guards in one slot, empty slots
- **Explicit-next routing** — Three tests: chosen branch, single-edge fallthrough, multi-edge-with-None error
- **Loops** — Two tests: explicit-next loop with break-out, runaway loop terminated by iteration cap
- **Handler explicit next** — Two tests: override works, unknown node produces `ErrorOutcome`
- **Handler exceptions** — One test: raised exception becomes `ErrorOutcome`
- **Audit log content** — Three tests including the reproducibility check (same graph + same state → same event-type sequence)

The reproducibility test operationalizes MBT-1 at the runner level.

**`test_gate_node.py`** (15 tests) — Four groups:

- **Pause behaviour** — Run pauses at gate, checkpoint is pending, iteration counter not incremented
- **Resume happy paths** — Approved routes correctly, rejected routes correctly, checkpoint consumed after resume, after-slot on post-gate node fires
- **Resume error paths** — Invalid signal, consumed checkpoint, abandoned checkpoint, graph version mismatch
- **Explicit abandonment** — Abandon seals the checkpoint, double-abandon raises
- **Audit events** — `gate_enter` emitted on pause, `gate_signal` emitted on resume

### Example tests

**`tests/examples/test_ex04a.py`** (6 tests) — Exercises all paths through the Example 4a graph: allowlist rejection, low-risk direct dispatch, high-risk gate pause, approved dispatch, rejected refuse, and full audit event trace for a high-risk approved flow.

### How tests are run

`make test` runs everything. Full suite runs in about 0.25 seconds.

```bash
pytest tests/integration/test_gate_node.py::test_resume_approved_signal_routes_to_done
pytest -k "guard"
pytest -x
pytest --pdb
```

### Why this much coverage

The runner is held constant across demos (MBT-11). Every future demo's claims rest on it. The gate-node machinery in particular — checkpoint state machine, graph-version pinning, single-use guarantee — has real security implications. Catching subtle bugs at v0 is much cheaper than discovering them mid-demo.

---

## Section 8: Documentation and diagrams

In the public repo (`oxpecker-guard/`):

- **`README.md`** — Public README. Top-of-doc AI-usage note, sharpened thesis with NVIDIA OpenShell as system-level reference, layered/trust-domain mental model, gate node and HITL section, comparison to LangGraph/CrewAI/AutoGen, MBTs and SBTs, anticipated objections, planned demos with the four-question structure.
- **`CHANGELOG.md`** — Single "Unreleased / v0 foundations" entry.
- **`docs/diagrams/`** — Five SVGs:
  - `trust_domains.svg` — five trust domains with mediated relationships
  - `abstract_orchestrator_model.svg` — generic graph-with-guard-slots structure
  - `component_architecture.svg` — static component zones
  - `runtime_flow_worked_example.svg` — a worked runtime example with guards at slot boundaries
  - `sequence_4a.svg` — Example 4a's pause/resume sequence: `run()` pauses at the approval gate, `resume()` completes the flow
- **`benchmarks/reference_machine.md`** — Placeholder, gets filled in when LLM serving is provisioned.

---

## Closing notes

The repo fits comfortably in working memory: project metadata + scaffolding, seven core modules totaling ~1200 lines of Python, two foundational guards, a 64-test suite covering the runner and gate-node machinery, one example, and the docs/diagrams. No framework dependencies, no hidden machinery, every file readable in under five minutes.

When v0.1 completes, the orchestrator core stays exactly as-is. What gets added:

- Example directories per demo: `examples/01_schema_validation/`, `examples/06_underage_plaintiff/` — each with handler module, operator config YAML, graph spec, example README
- New substantive guards: `schema_validate.py`, `name_list.py`
- New tests including live-model tests gated behind `make test-live`
- Performance numbers in `benchmarks/results/` once the desktop is set up to serve the model

No restructuring, no breaking changes. The foundations are built for additive growth.
