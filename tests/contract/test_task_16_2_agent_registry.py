"""Contract tests — Task 16.2: agent.py registry-driven dispatch +
generalized repetition guard.

Written by Tier 1 at spec time; immutable to the implementer.
Spec: docs/superpowers/specs/task-16.2-agent-dispatch.md
Design: docs/superpowers/specs/2026-07-17-multi-tool-registry-design.md

These tests exercise behavior specific to the registry rewrite. Behavior
carried over unchanged from Task 2.3/3.0 (run_command's own confirm/execute
path, ChatError propagation, the turn-limit path) stays pinned in
test_task_2_3_agent.py and test_task_3_0_agent_hardening.py, amended in
this same spec commit only where the vocabulary changed (see those files'
inline comments) — not duplicated here.
"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from sse_stub import StubServer  # noqa: E402

# Amended by Tier 1 for Task 58.1: this file only ever exercises the
# repetition stop (never turn-limit), so its one STOP_LINE constant
# becomes REPEATED_STOP_LINE. See
# docs/superpowers/specs/task-58.1-split-stop-messages.md.
REPEATED_STOP_LINE = "[sasha] stopping: repeated identical action"


class SpyExecutor:
    def __init__(self, cwd):
        self._cwd = cwd
        self.calls = []

    @property
    def cwd(self):
        return self._cwd

    def run(self, command, capture_stdout=False):
        raise AssertionError("executor.run must not be called for non-run_command tools")


class ScriptedConfirmer:
    def __init__(self, verdicts):
        self.verdicts = list(verdicts)
        self.asked = []

    def ask(self, summary, description):
        self.asked.append((summary, description))
        return self.verdicts.pop(0)


class RefusingConfirmer:
    """Spy that asserts it is never called (for auto-approved tools)."""

    def __init__(self):
        self.asked = []

    def ask(self, summary, description):
        self.asked.append((summary, description))
        raise AssertionError("confirmer.ask must not be called for auto-approved tools")


def _tc(name, arguments, call_id="call_1"):
    return {"kind": "tool_call", "id": call_id, "name": name,
            "arguments": arguments, "fragments": 2}


def _consult(stub_url, confirmer, executor=None, seed="help me", max_tool_turns=10):
    from sasha.agent import run_consult
    from sasha.config import Profile
    from sasha.llm import ChatClient
    from sasha.session import Session

    session = Session()
    session.add_user(seed)
    client = ChatClient(Profile(name="stub", base_url=stub_url,
                                model="stub-model", api_key=""), timeout_s=10.0)
    if executor is None:
        executor = SpyExecutor("/spy")
    out_chunks = []
    run_consult(session, client, executor, confirmer,
                out=out_chunks.append, max_tool_turns=max_tool_turns)
    return session, executor, confirmer, "".join(out_chunks)


def _tool_message_contents(stub, request_index):
    msgs = stub.requests[request_index]["body"]["messages"]
    return [m["content"] for m in msgs if m.get("role") == "tool"]


# --- AC: schema list includes all five tools ---------------------------------

def test_ac_schema_sent_includes_all_five_tools():
    # Amended by Tier 1 for Task 17.2 (project memory): a sixth tool,
    # "remember", is added to the registry. See
    # docs/superpowers/specs/task-17.2-remember-tool.md.
    # Amended by Tier 1 for Task 57.1: a seventh tool, "question", is
    # added. See docs/superpowers/specs/task-57.1-question-core.md.
    with StubServer([{"kind": "text", "text": "hi"}]) as stub:
        _consult(stub.base_url, ScriptedConfirmer([]))
        sent_tools = stub.requests[0]["body"]["tools"]
    names = {t["function"]["name"] for t in sent_tools}
    assert names == {"run_command", "write_file", "read_file", "list_dir",
                      "search_files", "remember", "question"}


# --- AC: dispatch by name for a new tool (auto-approved) ---------------------

def test_ac_read_file_dispatches_to_real_handler(tmp_path):
    (tmp_path / "f.txt").write_text("hello from disk")
    executor = SpyExecutor(str(tmp_path))
    confirmer = RefusingConfirmer()
    with StubServer([_tc("read_file", {"path": "f.txt"}),
                     {"kind": "text", "text": "done"}]) as stub:
        _consult(stub.base_url, confirmer, executor=executor)
        tool_contents = _tool_message_contents(stub, 1)
    assert confirmer.asked == []
    assert tool_contents == ["hello from disk"]


def test_ac_list_dir_dispatches_to_real_handler(tmp_path):
    (tmp_path / "only.txt").write_text("")
    executor = SpyExecutor(str(tmp_path))
    confirmer = RefusingConfirmer()
    with StubServer([_tc("list_dir", {}),
                     {"kind": "text", "text": "done"}]) as stub:
        _consult(stub.base_url, confirmer, executor=executor)
        tool_contents = _tool_message_contents(stub, 1)
    assert confirmer.asked == []
    assert tool_contents == ["only.txt"]


def test_ac_search_files_dispatches_to_real_handler(tmp_path):
    (tmp_path / "f.txt").write_text("needle here\n")
    executor = SpyExecutor(str(tmp_path))
    confirmer = RefusingConfirmer()
    with StubServer([_tc("search_files", {"pattern": "needle"}),
                     {"kind": "text", "text": "done"}]) as stub:
        _consult(stub.base_url, confirmer, executor=executor)
        tool_contents = _tool_message_contents(stub, 1)
    assert confirmer.asked == []
    assert tool_contents == ["f.txt:1: needle here"]


# --- AC: unknown tool name still produces "Unknown tool: ..." ----------------

def test_ac_unknown_tool_unchanged():
    confirmer = ScriptedConfirmer([])
    with StubServer([_tc("totally_bogus_tool", {}),
                     {"kind": "text", "text": "ok"}]) as stub:
        _consult(stub.base_url, confirmer)
        tool_contents = _tool_message_contents(stub, 1)
    assert confirmer.asked == []
    assert tool_contents == ["Unknown tool: totally_bogus_tool"]


# --- AC: write_file confirmation-gated verdicts -------------------------------

def test_ac_write_file_no_does_not_write(tmp_path):
    executor = SpyExecutor(str(tmp_path))
    confirmer = ScriptedConfirmer([("no", "")])
    with StubServer([_tc("write_file", {"path": "f.txt", "content": "x",
                                        "description": "d"}),
                     {"kind": "text", "text": "ok"}]) as stub:
        _consult(stub.base_url, confirmer, executor=executor)
        tool_contents = _tool_message_contents(stub, 1)
    assert not (tmp_path / "f.txt").exists()
    assert tool_contents == ["User declined this action."]


def test_ac_write_file_edit_treated_as_reply(tmp_path):
    executor = SpyExecutor(str(tmp_path))
    confirmer = ScriptedConfirmer([("edit", "please don't")])
    with StubServer([_tc("write_file", {"path": "f.txt", "content": "x",
                                        "description": "d"}),
                     {"kind": "text", "text": "ok"}]) as stub:
        _consult(stub.base_url, confirmer, executor=executor)
        tool_contents = _tool_message_contents(stub, 1)
    assert not (tmp_path / "f.txt").exists()
    assert tool_contents == ["User did not run this and replied: please don't"]


def test_ac_write_file_reply_does_not_write(tmp_path):
    executor = SpyExecutor(str(tmp_path))
    confirmer = ScriptedConfirmer([("reply", "use a different name")])
    with StubServer([_tc("write_file", {"path": "f.txt", "content": "x",
                                        "description": "d"}),
                     {"kind": "text", "text": "ok"}]) as stub:
        _consult(stub.base_url, confirmer, executor=executor)
        tool_contents = _tool_message_contents(stub, 1)
    assert not (tmp_path / "f.txt").exists()
    assert tool_contents == [
        "User did not run this and replied: use a different name"]


def test_ac_write_file_yes_writes_and_confirmer_sees_summary(tmp_path):
    executor = SpyExecutor(str(tmp_path))
    confirmer = ScriptedConfirmer([("yes", "")])
    with StubServer([_tc("write_file", {"path": "f.txt", "content": "hello",
                                        "description": "greeting"}),
                     {"kind": "text", "text": "ok"}]) as stub:
        _consult(stub.base_url, confirmer, executor=executor)
        tool_contents = _tool_message_contents(stub, 1)
    assert (tmp_path / "f.txt").read_text() == "hello"
    assert tool_contents == ["wrote 5 bytes to f.txt"]
    summary, description = confirmer.asked[0]
    assert "f.txt" in summary
    assert "5" in summary
    assert description == "greeting"


# --- AC: repetition guard widened to (tool_name, key) -------------------------

def test_ac_repetition_guard_fires_across_write_file_calls_ignoring_description(tmp_path):
    executor = SpyExecutor(str(tmp_path))
    confirmer = ScriptedConfirmer([("yes", ""), ("yes", "")])
    with StubServer([
            _tc("write_file", {"path": "f.txt", "content": "same",
                                "description": "first description"}, "call_a"),
            _tc("write_file", {"path": "f.txt", "content": "same",
                                "description": "second description"}, "call_b"),
            {"kind": "text", "text": "final answer"},
    ]) as stub:
        _, executor, _, out = _consult(stub.base_url, confirmer, executor=executor)
        assert len(stub.requests) == 3
        last_body = stub.requests[2]["body"]
    assert not last_body.get("tools")
    assert REPEATED_STOP_LINE in out
    assert "final answer" in out


def test_ac_repetition_guard_does_not_fire_on_different_content(tmp_path):
    executor = SpyExecutor(str(tmp_path))
    confirmer = ScriptedConfirmer([("yes", ""), ("yes", "")])
    with StubServer([
            _tc("write_file", {"path": "f.txt", "content": "first",
                                "description": "d"}, "call_a"),
            _tc("write_file", {"path": "f.txt", "content": "second",
                                "description": "d"}, "call_b"),
            {"kind": "text", "text": "final answer"},
    ]) as stub:
        _, executor, _, out = _consult(stub.base_url, confirmer, executor=executor,
                                        max_tool_turns=10)
        assert len(stub.requests) == 3
        last_body = stub.requests[2]["body"]
    assert last_body.get("tools")  # loop did NOT stop; third round is a real turn
    assert REPEATED_STOP_LINE not in out
    assert (tmp_path / "f.txt").read_text() == "second"


def test_ac_repetition_guard_fires_on_identical_auto_approved_calls(tmp_path):
    (tmp_path / "f.txt").write_text("content")
    executor = SpyExecutor(str(tmp_path))
    confirmer = RefusingConfirmer()
    with StubServer([
            _tc("read_file", {"path": "f.txt"}, "call_a"),
            _tc("read_file", {"path": "f.txt"}, "call_b"),
            {"kind": "text", "text": "final answer"},
    ]) as stub:
        _, executor, _, out = _consult(stub.base_url, confirmer, executor=executor)
        assert len(stub.requests) == 3
        last_body = stub.requests[2]["body"]
    assert not last_body.get("tools")
    assert REPEATED_STOP_LINE in out
    assert "final answer" in out
