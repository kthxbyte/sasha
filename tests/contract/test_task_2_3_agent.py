"""Contract tests — Task 2.3: agent loop with confirmation gate.

Written by Tier 1 at spec time; immutable to the implementer.
Spec: docs/superpowers/specs/task-2.3-agent.md (design §5.5, §7.2, §7.4, §10).
Driven by the SSE stub, a scripted confirmer, and a spy executor.
"""

import json
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from sse_stub import StubServer, refused_base_url  # noqa: E402

STOP_LINE = "[sasha] stopping: repeated identical command / tool-turn limit reached"


class SpyExecutor:
    """Duck-typed executor: records calls, returns a scripted result."""

    def __init__(self):
        from sasha.executor import ExecResult

        self.calls = []
        self._result = ExecResult(exit_code=3, signal=None,
                                  stderr_text="ERR-TAIL", stdout_text="OUT-TAIL",
                                  cwd_after="/spy", duration_ms=1)

    @property
    def cwd(self):
        return "/spy"

    def run(self, command, capture_stdout=False):
        self.calls.append((command, capture_stdout))
        return self._result


class ScriptedConfirmer:
    def __init__(self, verdicts):
        self.verdicts = list(verdicts)
        self.asked = []

    def ask(self, command, description):
        self.asked.append((command, description))
        return self.verdicts.pop(0)


def _tc(command, call_id="call_1"):
    return {"kind": "tool_call", "id": call_id, "name": "run_command",
            "arguments": {"command": command, "description": "a step"},
            "fragments": 2}


def _consult(stub_url, script_verdicts, seed="help me", max_tool_turns=10):
    from sasha.agent import run_consult
    from sasha.config import Profile
    from sasha.llm import ChatClient
    from sasha.session import Session

    session = Session()
    session.add_user(seed)
    client = ChatClient(Profile(name="stub", base_url=stub_url,
                                model="stub-model", api_key=""), timeout_s=10.0)
    executor = SpyExecutor()
    confirmer = ScriptedConfirmer(script_verdicts)
    out_chunks = []
    run_consult(session, client, executor, confirmer,
                out=out_chunks.append, max_tool_turns=max_tool_turns)
    return session, executor, confirmer, "".join(out_chunks)


def _tool_message_contents(stub, request_index):
    msgs = stub.requests[request_index]["body"]["messages"]
    return [m["content"] for m in msgs if m.get("role") == "tool"]


# --- AC1: text only --------------------------------------------------------

def test_ac1_text_only_runs_nothing():
    with StubServer([{"kind": "text", "text": "just an answer"}]) as stub:
        session, executor, confirmer, out = _consult(stub.base_url, [])
    assert "just an answer" in out
    assert executor.calls == []
    assert confirmer.asked == []
    assert len(stub.requests) == 1
    # stored in session: a later build includes the assistant text
    assert "just an answer" in json.dumps(session.build_messages("/spy"))


# --- AC2: yes ------------------------------------------------------------------

def test_ac2_yes_executes_and_reports():
    with StubServer([_tc("ls -la /var"), {"kind": "text", "text": "done"}]) as stub:
        _, executor, confirmer, _ = _consult(stub.base_url, [("yes", "")])
        tool_contents = _tool_message_contents(stub, 1)
    assert executor.calls == [("ls -la /var", True)]
    assert confirmer.asked[0][0] == "ls -la /var"
    assert len(tool_contents) == 1
    assert "3" in tool_contents[0]           # exit code
    assert "OUT-TAIL" in tool_contents[0]
    assert "ERR-TAIL" in tool_contents[0]


# --- AC3: edit --------------------------------------------------------------------

def test_ac3_edit_runs_edited():
    with StubServer([_tc("rm -rf /"), {"kind": "text", "text": "ok"}]) as stub:
        _, executor, _, _ = _consult(stub.base_url, [("edit", "echo safer")])
    assert executor.calls == [("echo safer", True)]


# --- AC4: no ------------------------------------------------------------------------

def test_ac4_no_executes_nothing():
    with StubServer([_tc("shutdown now"), {"kind": "text", "text": "ok"}]) as stub:
        _, executor, _, _ = _consult(stub.base_url, [("no", "")])
        tool_contents = _tool_message_contents(stub, 1)
    assert executor.calls == []
    assert tool_contents == ["User declined to run this command."]


# --- AC5: reply -----------------------------------------------------------------------

def test_ac5_reply_never_executes():
    with StubServer([_tc("apt install foo"), {"kind": "text", "text": "ok"}]) as stub:
        _, executor, _, _ = _consult(
            stub.base_url, [("reply", "use ripgrep instead")])
        tool_contents = _tool_message_contents(stub, 1)
    assert executor.calls == []
    assert tool_contents == [
        "User did not run the command and replied: use ripgrep instead"]


# --- AC6: identical-command loop detection ------------------------------------------------

def test_ac6_identical_command_stops_loop():
    with StubServer([_tc("ls", "call_a"), _tc("ls", "call_b"),
                     {"kind": "text", "text": "final answer"}]) as stub:
        _, executor, _, out = _consult(
            stub.base_url, [("yes", ""), ("yes", "")])
        assert len(stub.requests) == 3
        last_body = stub.requests[2]["body"]
    assert executor.calls == [("ls", True), ("ls", True)]
    assert not last_body.get("tools")        # final round is tools-less
    assert STOP_LINE in out
    assert "final answer" in out


# --- AC7: turn limit -------------------------------------------------------------------------

def test_ac7_turn_limit_stops_loop():
    with StubServer([_tc("cmd-a", "call_a"), _tc("cmd-b", "call_b"),
                     {"kind": "text", "text": "wrap-up"}]) as stub:
        _, executor, _, out = _consult(
            stub.base_url, [("yes", ""), ("yes", "")], max_tool_turns=2)
        assert len(stub.requests) == 3
        last_body = stub.requests[2]["body"]
    assert executor.calls == [("cmd-a", True), ("cmd-b", True)]
    assert not last_body.get("tools")
    assert STOP_LINE in out
    assert "wrap-up" in out


# --- AC8: exact tool schema -------------------------------------------------------------------

def test_ac8_tool_schema_exact():
    from sasha.agent import RUN_COMMAND_TOOL

    assert RUN_COMMAND_TOOL == {
        "type": "function",
        "function": {
            "name": "run_command",
            "description": (
                "Propose one bash command to run in the user's shell. "
                "The user sees the command and your description and must "
                "approve it before it runs."),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string",
                                "description": "exact bash command"},
                    "description": {
                        "type": "string",
                        "description": "one line: what this does and why"},
                },
                "required": ["command", "description"],
            },
        },
    }


# --- AC9: ChatError propagates ----------------------------------------------------------------

def test_ac9_chaterror_propagates():
    from sasha.llm import ChatError

    with pytest.raises(ChatError):
        _consult(refused_base_url(), [])
