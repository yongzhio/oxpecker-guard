"""Run state — the mutable object the graph runner threads through nodes.

The state holds everything an executing run needs:
  * messages so far (assembled context to feed the model)
  * tool call history (what the model asked to do, what came back)
  * the run's identity (UUID, started-at, user identity if any)
  * counters used by guards (iterations, tool calls, tokens spent)
  * arbitrary scratch space the demo can use

State is mutable across the run. Snapshots are JSON-serializable for
checkpointing — see opg/core/checkpoint.py.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# Messages: the canonical chat-format unit
# ---------------------------------------------------------------------------

Role = Literal["system", "user", "assistant", "tool"]


class ToolCall(BaseModel):
    """A tool invocation requested by the model."""

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    arguments: dict[str, Any]


class ToolResult(BaseModel):
    """The result returned to the model after a tool was dispatched."""

    model_config = ConfigDict(extra="forbid")

    tool_call_id: str
    content: str
    is_error: bool = False


class Message(BaseModel):
    """A single message in the conversation history.

    `tool_calls` is set on assistant messages that requested one or more tools.
    `tool_result` is set on tool-role messages returning a result.
    `content` carries text content for any role; may be empty when tool_calls
    or tool_result is set.
    """

    model_config = ConfigDict(extra="forbid")

    role: Role
    content: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    tool_result: ToolResult | None = None


# ---------------------------------------------------------------------------
# Counters: deterministic budget enforcement
# ---------------------------------------------------------------------------


class Counters(BaseModel):
    """Counters guards consult to enforce budget caps and termination.

    Updated by the runner at well-defined points (after each model call,
    after each tool dispatch, etc.). Guards never mutate counters; they
    read them.
    """

    model_config = ConfigDict(extra="forbid")

    iterations: int = 0
    model_calls: int = 0
    tool_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0


# ---------------------------------------------------------------------------
# RunState: the object threaded through the graph
# ---------------------------------------------------------------------------


class RunState(BaseModel):
    """The mutable state object passed through every node in a run.

    Construction is intentionally minimal — most fields default to empty/zero.
    Demos populate it via the runner's seed.
    """

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=False)

    run_id: UUID = Field(default_factory=uuid4)
    started_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    user_id: str | None = None

    messages: list[Message] = Field(default_factory=list)
    counters: Counters = Field(default_factory=Counters)
    scratch: dict[str, Any] = Field(default_factory=dict)
    """Arbitrary per-run scratch space; demos may store demo-specific values here."""

    def append_message(self, message: Message) -> None:
        """Convenience for appending a message."""
        self.messages.append(message)
