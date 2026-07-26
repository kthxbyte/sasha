"""Contract tests — Task 32.1: general [context:KEY] injection syntax,
applied to cwd (ISSUE-050).

Written by Tier 1 at spec time; immutable to the implementer.
Spec: docs/superpowers/specs/task-32.1-context-injection-cwd.md
Design: docs/superpowers/specs/2026-07-26-issue-050-context-injection-syntax-design.md
"""

from __future__ import annotations


def _result(exit_code, stderr=""):
    from sasha.executor import ExecResult

    return ExecResult(exit_code=exit_code, signal=None, stderr_text=stderr,
                      stdout_text="", cwd_after="/", duration_ms=1)


def _tool_response(call_id, command="ls"):
    from sasha.llm import ChatResponse, ToolCall

    return ChatResponse(text="", tool_calls=[ToolCall(
        id=call_id, name="run_command",
        arguments={"command": command, "description": "desc"})])


# --- AC1: boot-time awareness — first call always tells cwd -----------------

def test_ac1_first_ever_call_inserts_cwd_marker():
    from sasha.session import Session

    s = Session()
    s.add_user("hola")
    msgs = s.build_messages("/home/salvador")

    assert msgs[-2]["role"] == "user"
    assert msgs[-2]["content"] == "[context:cwd] /home/salvador"
    assert msgs[-1]["content"] == "hola"


# --- AC2: unchanged cwd never duplicates the marker --------------------------

def test_ac2_same_cwd_twice_no_duplicate_marker():
    from sasha.session import Session

    s = Session()
    s.add_user("hola")
    s.build_messages("/x")
    s.add_user("again")
    msgs = s.build_messages("/x")

    markers = [m for m in msgs if m.get("content", "").startswith("[context:cwd]")]
    assert len(markers) == 1
    assert markers[0]["content"] == "[context:cwd] /x"


# --- AC3: a genuine cwd change inserts a new marker -------------------------

def test_ac3_cwd_change_inserts_new_marker():
    from sasha.session import Session

    s = Session()
    s.add_user("hola")
    s.build_messages("/a")
    s.add_user("now in b")
    msgs = s.build_messages("/b")

    markers = [m["content"] for m in msgs if m.get("content", "").startswith("[context:cwd]")]
    assert markers == ["[context:cwd] /a", "[context:cwd] /b"]
    assert msgs[-1]["content"] == "now in b"


# --- AC4: marker lands before the pending user message, never merged -------

def test_ac4_marker_precedes_pending_user_message_not_merged():
    from sasha.session import Session

    s = Session()
    s.add_user("a real question")
    msgs = s.build_messages("/regression-check-dir")

    assert msgs[-1]["role"] == "user"
    assert msgs[-1]["content"] == "a real question"
    assert msgs[-2]["content"] == "[context:cwd] /regression-check-dir"


# --- AC5: no pending user message — marker becomes the last entry ----------

def test_ac5_marker_alone_is_last_entry_with_no_pending_user_or_notes():
    from sasha.session import Session

    s = Session()
    s.add_user("do something")
    s.build_messages("/a")
    s.add_assistant(_tool_response("call_1"))
    s.add_tool_result("call_1", "exit code: 0")

    msgs = s.build_messages("/b")

    assert msgs[-1]["role"] == "user"
    assert msgs[-1]["content"] == "[context:cwd] /b"


# --- AC6: marker and notes stay as two separate messages, never folded -----

def test_ac6_marker_and_notes_together_stay_separate_messages():
    from sasha.session import Session

    s = Session()
    s.add_user("do something")
    s.build_messages("/a")
    s.add_assistant(_tool_response("call_1"))
    s.add_tool_result("call_1", "exit code: 0")
    s.note_command("failed-cmd", _result(1, "boom"))

    msgs = s.build_messages("/b")

    assert msgs[-2]["content"] == "[context:cwd] /b"
    assert msgs[-1]["content"] == "failed-cmd (1)\nboom"


def test_ac6b_stale_marker_and_new_notes_stay_separate_when_cwd_unchanged():
    from sasha.session import Session

    s = Session()
    s.build_messages("/z")  # boot marker, empty history, no pending user
    s.note_command("failed-cmd", _result(127, "nf"))

    msgs = s.build_messages("/z")  # cwd unchanged; the boot marker is stale

    assert msgs[-2]["content"] == "[context:cwd] /z"
    assert msgs[-1]["content"] == "failed-cmd (127)\nnf"


# --- AC7: no pending user, no notes, cwd unchanged — array still ends on
# the actual last history entry (Task 30.1's behavior preserved) -----------

def test_ac7_no_marker_no_notes_ends_on_last_real_history_entry():
    from sasha.session import Session

    s = Session()
    s.add_user("hi")
    s.build_messages("/z")
    s.add_assistant(_tool_response("call_1"))
    s.add_tool_result("call_1", "ok")

    msgs = s.build_messages("/z")

    assert msgs[-1]["role"] == "tool"
    assert msgs[-1]["tool_call_id"] == "call_1"


# --- AC8: cwd is never glued into the notes-only context block anymore -----

def test_ac8_cwd_absent_from_notes_block_content():
    from sasha.session import Session

    s = Session()
    s.note_command("ls -la", _result(0))
    s.add_user("why?")
    msgs = s.build_messages("/distinctive-marker-dir")

    assert "/distinctive-marker-dir" not in msgs[-1]["content"]
    assert msgs[-2]["content"] == "[context:cwd] /distinctive-marker-dir"


# --- AC9: system prompt carries the anchor sentence, stable across cwd -----

def test_ac9_system_prompt_has_stable_anchor_sentence():
    from sasha.session import Session

    s = Session()
    s.add_user("hi")
    p1 = s.build_messages("/a")[0]["content"]
    s.add_user("again")
    p2 = s.build_messages("/b")[0]["content"]

    assert '"[context:KEY] VALUE"' in p1
    assert "not typed by the user" in p1
    assert p1 == p2  # changing cwd must never perturb the system prompt
