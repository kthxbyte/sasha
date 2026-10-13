"""The task must survive history trimming (ISSUE-077).

`build_messages` trims history from the front to fit `history_tokens` (12000 by
default). In a long tool run the user's request is the OLDEST message there is,
so it was the first thing discarded: verified before fixing, with a 2000-token
budget and 40 tool turns the task was gone and the only user message left was
`[context:cwd]`. The model then kept working with no record of what it had been
asked to do -- which is also the problem a "todo list" tool is usually reached
for, and fixing it directly costs the model nothing.
"""

from sasha.llm import ChatResponse
from sasha.session import Session


def _tool_turns(session, count=40, chars=40):
    """A long agent run: assistant tool calls, each with a fat tool result."""
    for i in range(count):
        session.history.append({
            "role": "assistant", "content": "",
            "tool_calls": [{"id": f"c{i}", "type": "function",
                            "function": {"name": "read_file",
                                         "arguments": '{"path": "x"}'}}]})
        session.history.append({
            "role": "tool", "tool_call_id": f"c{i}",
            "content": ("line of file contents " * chars) + str(i)})


TASK = "Refactor the executor so the terminal state is restored on error."


def test_the_task_survives_a_long_tool_run():
    s = Session(history_token_budget=2000)
    s.add_user(TASK)
    _tool_turns(s)

    msgs = s.build_messages("/tmp")

    assert any(TASK in m.get("content", "") for m in msgs)


def test_the_task_is_pinned_directly_after_the_system_prompt():
    s = Session(history_token_budget=2000)
    s.add_user(TASK)
    _tool_turns(s)

    msgs = s.build_messages("/tmp")

    assert msgs[0]["role"] == "system"
    assert msgs[1]["role"] == "user"
    assert TASK in msgs[1]["content"]


def test_trimming_still_actually_trims():
    """Pinning the task must not defeat the budget."""
    s = Session(history_token_budget=2000)
    s.add_user(TASK)
    _tool_turns(s)

    msgs = s.build_messages("/tmp")

    assert len(msgs) < 43                      # 1 + 1 + 41-ish retained


def test_a_single_user_message_is_not_duplicated():
    s = Session()
    s.add_user(TASK)

    msgs = s.build_messages("/tmp")

    users = [m for m in msgs if m["role"] == "user"]
    assert sum(1 for m in users if TASK in m.get("content", "")) == 1


def test_the_pin_follows_the_newest_question_in_a_repl_session():
    """Otherwise an hour-old request would be pinned forever."""
    s = Session(history_token_budget=1500)
    s.add_user("Q1: the old question, no longer relevant at all.")
    s.add_assistant(ChatResponse(text="answer one", tool_calls=[]))
    s.add_user("Q2: the question actually being worked on now.")
    s.add_assistant(ChatResponse(text="answer two", tool_calls=[]))
    _tool_turns(s)

    msgs = s.build_messages("/tmp")
    joined = " ".join(m.get("content", "") for m in msgs)

    assert "Q2: the question actually being worked on now." in joined
    assert "Q1: the old question, no longer relevant at all." not in joined


def test_a_context_marker_is_never_mistaken_for_the_task():
    s = Session(history_token_budget=800)
    s.add_user(TASK)
    _tool_turns(s, count=30)

    msgs = s.build_messages("/tmp")

    # the pinned message is the task, not a [context:...] injection
    assert not msgs[1]["content"].startswith("[context:")
    assert TASK in msgs[1]["content"]


def test_no_task_and_no_notes_still_builds_a_valid_prompt():
    s = Session(history_token_budget=800)
    _tool_turns(s, count=30)

    msgs = s.build_messages("/tmp")

    assert msgs[0]["role"] == "system"


def test_notes_are_still_cleared_after_building():
    from sasha.session import NoteCommand

    s = Session()
    s.add_user(TASK)
    s.notes.append(NoteCommand(line="ls", exit_code=0, stderr_text=""))
    s.build_messages("/tmp")

    assert s.notes == []
