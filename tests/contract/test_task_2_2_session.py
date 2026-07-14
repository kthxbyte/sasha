"""Contract tests — Task 2.2: session & context building.

Written by Tier 1 at spec time; immutable to the implementer.
Spec: docs/superpowers/specs/task-2.2-session.md (design §5.6, §7.3, §9.4).
"""

import json
import math
import platform


def _result(exit_code, stderr=""):
    from sasha.executor import ExecResult

    return ExecResult(exit_code=exit_code, signal=None, stderr_text=stderr,
                      stdout_text="", cwd_after="/", duration_ms=1)


def _tool_response(call_id, command="ls", text=""):
    from sasha.llm import ChatResponse, ToolCall

    return ChatResponse(text=text, tool_calls=[ToolCall(
        id=call_id, name="run_command",
        arguments={"command": command, "description": "desc"})])


def _text_response(text):
    from sasha.llm import ChatResponse

    return ChatResponse(text=text, tool_calls=[])


def _estimate(msg):
    total = math.ceil(len(msg.get("content") or "") / 4)
    for call in msg.get("tool_calls", []):
        total += math.ceil(len(call["function"]["arguments"]) / 4)
    return total


# --- AC1: system prompt ----------------------------------------------------

def test_ac1_system_prompt_first_and_grounded():
    from sasha.session import Session

    s = Session()
    s.add_user("hello")
    msgs = s.build_messages("/anywhere")
    assert msgs[0]["role"] == "system"
    content = msgs[0]["content"]
    assert "confirm" in content.lower()
    assert platform.platform() in content
    assert "bash" in content.lower()


# --- AC2: fresh context block -------------------------------------------------

def test_ac2_context_block_in_final_user_message():
    from sasha.session import Session

    s = Session()
    for i in range(1, 13):  # 12 commands; only the last 10 may surface
        stderr = "distinctive-boom-91" if i == 12 else ""
        code = 91 if i == 12 else 0
        s.note_command(f"probe-command-{i:02d}", _result(code, stderr))
    s.add_user("why did that fail?")

    msgs = s.build_messages("/probe/dir")
    final = msgs[-1]
    assert final["role"] == "user"
    content = final["content"]
    assert "why did that fail?" in content
    assert "/probe/dir" in content
    for i in range(3, 13):
        assert f"probe-command-{i:02d}" in content
    assert "probe-command-01" not in content
    assert "probe-command-02" not in content
    assert "91" in content                      # exit code of the trigger
    assert "distinctive-boom-91" in content     # its captured stderr


def test_ac2_stored_history_not_mutated_by_build():
    from sasha.session import Session

    s = Session()
    s.add_user("plain question")
    s.build_messages("/somewhere")
    msgs = s.build_messages("/somewhere")
    # context block must not accumulate across builds
    assert msgs[-1]["content"].count("/somewhere") == 1


# --- AC3: trimming -----------------------------------------------------------------

def test_ac3_trim_oldest_first_within_budget():
    from sasha.session import Session

    s = Session(history_token_budget=50)
    for i in range(1, 21):  # 20 messages of ~10 tokens each
        s.add_user(f"filler-{i:02d} " + "x" * 29)

    msgs = s.build_messages("/w")
    assert msgs[0]["role"] == "system"

    all_text = json.dumps(msgs)
    assert "filler-20" in all_text              # newest survives
    assert "filler-01" not in all_text          # oldest dropped
    assert "filler-05" not in all_text

    history = msgs[1:-1]  # between system prompt and context-carrying final
    assert sum(_estimate(m) for m in history) <= 50


# --- AC4: paired drops --------------------------------------------------------------

def test_ac4_no_orphaned_tool_messages():
    from sasha.session import Session

    s = Session(history_token_budget=50)
    s.add_user("old question")
    s.add_assistant(_tool_response("call_old", command="oldcmd"))
    s.add_tool_result("call_old", "old tool output")
    for i in range(1, 21):
        s.add_user(f"flood-{i:02d} " + "x" * 29)

    msgs = s.build_messages("/w")
    assert "call_old" not in json.dumps(msgs)   # pair dropped together
    # global invariant: every tool message has an earlier owning assistant
    seen_ids = set()
    for m in msgs:
        for call in m.get("tool_calls", []):
            seen_ids.add(call["id"])
        if m.get("role") == "tool":
            assert m["tool_call_id"] in seen_ids, "orphaned tool message"


# --- AC5: wire-format round trip -------------------------------------------------------

def test_ac5_wire_format_round_trip():
    from sasha.session import Session

    s = Session()
    s.add_user("do something")
    s.add_assistant(_tool_response("call_9", command="ls -la", text="Let me check."))
    s.add_tool_result("call_9", "exit code: 0")

    msgs = s.build_messages("/w")
    assistant = next(m for m in msgs if m.get("tool_calls"))
    call = assistant["tool_calls"][0]
    assert call["id"] == "call_9"
    assert call["type"] == "function"
    assert call["function"]["name"] == "run_command"
    args = call["function"]["arguments"]
    assert isinstance(args, str)                # JSON string on the wire
    assert json.loads(args) == {"command": "ls -la", "description": "desc"}

    tool = next(m for m in msgs if m.get("role") == "tool")
    assert tool["tool_call_id"] == "call_9"
    assert "exit code: 0" in tool["content"]
