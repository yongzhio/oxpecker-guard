"""Light unit tests for state types — sanity checks on key behaviour."""

from __future__ import annotations

from opg.core.state import Counters, Message, RunState, ToolCall


def test_runstate_defaults_minimal() -> None:
    """A bare RunState() should be fully populated with sensible defaults."""
    state = RunState()
    assert state.run_id is not None
    assert state.started_at is not None
    assert state.user_id is None
    assert state.messages == []
    assert state.counters.iterations == 0
    assert state.counters.tool_calls == 0
    assert state.scratch == {}


def test_runstate_append_message() -> None:
    state = RunState()
    state.append_message(Message(role="user", content="hello"))
    assert len(state.messages) == 1
    assert state.messages[0].role == "user"


def test_runstate_serializable() -> None:
    """Sanity: round-trips through JSON for checkpoint storage."""
    state = RunState(user_id="alice")
    state.append_message(Message(role="user", content="hi"))
    state.counters.iterations = 3

    dumped = state.model_dump_json()
    restored = RunState.model_validate_json(dumped)
    assert restored.user_id == "alice"
    assert restored.counters.iterations == 3
    assert restored.messages[0].content == "hi"


def test_message_with_tool_calls() -> None:
    """An assistant message can carry one or more ToolCalls."""
    msg = Message(
        role="assistant",
        tool_calls=[ToolCall(id="c1", name="search", arguments={"q": "x"})],
    )
    assert msg.tool_calls[0].name == "search"


def test_counters_independent_fields() -> None:
    c = Counters()
    c.iterations = 1
    c.tool_calls = 2
    assert c.model_calls == 0  # other fields untouched
