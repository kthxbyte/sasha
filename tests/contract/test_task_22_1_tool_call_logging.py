"""Contract tests — Task 22.1: tool-call logging, core write path.

Written by Tier 1 at spec time; immutable to the implementer.
Spec: docs/superpowers/specs/task-22.1-tool-call-log-core.md
Design: docs/superpowers/specs/2026-07-21-tool-call-logging-design.md

State-dir isolation (XDG_STATE_HOME) comes from the autouse fixture in
tests/contract/conftest.py, so log_call() never touches the real
~/.local/state/sasha/ on the machine running this suite.
"""

from __future__ import annotations

import json
import os
import pathlib
import stat
import sys
import uuid

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from sse_stub import StubServer  # noqa: E402


def _log_path(tmp_path) -> pathlib.Path:
    return tmp_path / "xdg-state" / "sasha" / "tool_log.jsonl"


def _read_entries(tmp_path) -> list[dict]:
    path = _log_path(tmp_path)
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line]


# --- AC1: full schema on a normal executed call -----------------------------

def test_ac1_log_call_writes_full_schema(tmp_path):
    from sasha.tool_log import log_call

    log_call(
        tool_name="run_command", call_id="call_1",
        arguments={"command": "ls -la", "description": "look around"},
        executed_command=None, cwd="/home/salvador/project",
        verdict="yes", executed=True,
        result_text="exit code: 0\nstdout:\nok\nstderr:\n",
        result_omitted_reason=None,
    )

    entries = _read_entries(tmp_path)
    assert len(entries) == 1
    e = entries[0]
    assert e["tool"] == "run_command"
    assert e["call_id"] == "call_1"
    assert e["arguments"] == {"command": "ls -la", "description": "look around"}
    assert e["executed_command"] is None
    assert e["cwd"] == "/home/salvador/project"
    assert e["verdict"] == "yes"
    assert e["executed"] is True
    assert e["result"] == "exit code: 0\nstdout:\nok\nstderr:\n"
    assert e["result_omitted_reason"] is None
    assert "ts" in e and "T" in e["ts"]
    assert "session_id" in e
    uuid.UUID(e["session_id"])  # raises if not a valid UUID


# --- AC2: session_id stable across calls in the same process ---------------

def test_ac2_session_id_stable_across_calls(tmp_path):
    from sasha.tool_log import log_call

    for i in range(3):
        log_call(
            tool_name="read_file", call_id=f"call_{i}",
            arguments={"path": "f.txt"}, executed_command=None,
            cwd="/spy", verdict="auto-approved", executed=True,
            result_text="contents", result_omitted_reason=None,
        )

    entries = _read_entries(tmp_path)
    assert len(entries) == 3
    session_ids = {e["session_id"] for e in entries}
    assert len(session_ids) == 1


# --- AC3: log file created with owner-only permissions ----------------------

def test_ac3_log_file_created_with_owner_only_permissions(tmp_path):
    from sasha.tool_log import log_call

    log_call(
        tool_name="ls", call_id="call_1", arguments={}, executed_command=None,
        cwd="/spy", verdict="auto-approved", executed=True,
        result_text="ok", result_omitted_reason=None,
    )

    mode = stat.S_IMODE(_log_path(tmp_path).stat().st_mode)
    assert mode == 0o600


# --- AC4: write failure is caught, never raises -----------------------------

def test_ac4_write_failure_does_not_raise(tmp_path):
    from sasha.tool_log import log_call

    # Occupy the directory path with a plain file, so mkdir(parents=True)
    # for <state_dir> fails.
    state_dir = tmp_path / "xdg-state" / "sasha"
    state_dir.parent.mkdir(parents=True, exist_ok=True)
    state_dir.write_text("not a directory")

    log_call(
        tool_name="run_command", call_id="call_1",
        arguments={"command": "ls"}, executed_command=None,
        cwd="/spy", verdict="auto-approved", executed=True,
        result_text="ok", result_omitted_reason=None,
    )  # must not raise


# --- AC5: touches_sensitive_path matches and no-match -----------------------

class FakeExecutor:
    def __init__(self, cwd, sensitive_path_patterns=("~/.ssh/**", "/etc/passwd")):
        self.cwd = cwd
        self.sensitive_path_patterns = sensitive_path_patterns


def test_ac5_touches_sensitive_path_matches_and_no_match():
    from sasha.safe_commands import touches_sensitive_path

    executor = FakeExecutor("/home/salvador/project")
    assert touches_sensitive_path("cat ~/.ssh/id_rsa", executor) is True
    assert touches_sensitive_path("cat /etc/passwd", executor) is True
    assert touches_sensitive_path("ls -la /var", executor) is False
    # Not allowlisted at all, but still touches a sensitive path — this
    # function is independent of the requires_confirmation verb gate.
    # (Not "rm -rf ~/.ssh" with no trailing filename — see ISSUE-043: a
    # bare denylisted directory, with nothing after it, is a known,
    # separately-tracked gap in the underlying fnmatch pattern, inherited
    # unchanged from Task 21.1's classifier, and out of scope here.)
    assert touches_sensitive_path("rm -rf ~/.ssh/config", executor) is True


def test_ac6_touches_sensitive_path_fails_open_on_unparseable():
    from sasha.safe_commands import touches_sensitive_path

    executor = FakeExecutor("/home/salvador/project")
    assert touches_sensitive_path('cat "unterminated', executor) is False


# --- AC8/AC9/AC10: dispatch integration -------------------------------------

class SpyExecutor:
    def __init__(self, cwd, sensitive_path_patterns=("~/.ssh/**", "/etc/passwd")):
        from sasha.executor import ExecResult

        self.cwd = cwd
        self.sensitive_path_patterns = sensitive_path_patterns
        self._result = ExecResult(exit_code=0, signal=None, stderr_text="",
                                  stdout_text="OUT", cwd_after=cwd, duration_ms=1)

    def run(self, command, capture_stdout=False):
        return self._result


class ScriptedConfirmer:
    def __init__(self, verdicts):
        self.verdicts = list(verdicts)

    def ask(self, summary, description):
        return self.verdicts.pop(0)


def _tc(name, arguments, call_id="call_1"):
    return {"kind": "tool_call", "id": call_id, "name": name,
            "arguments": arguments, "fragments": 2}


def _consult(stub_url, confirmer, executor, log_spy, monkeypatch):
    from sasha import agent as agent_module
    from sasha.config import Profile
    from sasha.llm import ChatClient
    from sasha.session import Session

    monkeypatch.setattr(agent_module.tool_log, "log_call", log_spy)

    session = Session()
    session.add_user("help me")
    client = ChatClient(Profile(name="stub", base_url=stub_url,
                                model="stub-model", api_key=""), timeout_s=10.0)
    agent_module.run_consult(session, client, executor, confirmer, out=lambda s: None)
    return session


@pytest.mark.parametrize("verdict,arguments,expect", [
    ("yes", {"command": "mv a b", "description": "d"},
     {"verdict": "yes", "executed": True, "executed_command": None,
      "result_omitted_reason": None}),
    ("no", {"command": "mv a b", "description": "d"},
     {"verdict": "no", "executed": False, "executed_command": None,
      "result_omitted_reason": "declined"}),
    ("reply", {"command": "mv a b", "description": "d"},
     {"verdict": "reply", "executed": False, "executed_command": None,
      "result_omitted_reason": "declined"}),
])
def test_ac8_dispatch_logs_each_verdict_correctly(verdict, arguments, expect, monkeypatch):
    calls = []

    def spy(**kwargs):
        calls.append(kwargs)

    confirmer_verdict = (verdict, "some reply text") if verdict == "reply" else (verdict, "")
    executor = SpyExecutor("/spy")
    confirmer = ScriptedConfirmer([confirmer_verdict])
    with StubServer([_tc("run_command", arguments),
                     {"kind": "text", "text": "done"}]) as stub:
        _consult(stub.base_url, confirmer, executor, spy, monkeypatch)

    assert len(calls) == 1
    logged = calls[0]
    assert logged["tool_name"] == "run_command"
    assert logged["verdict"] == expect["verdict"]
    assert logged["executed"] == expect["executed"]
    assert logged["executed_command"] == expect["executed_command"]
    assert logged["result_omitted_reason"] == expect["result_omitted_reason"]


def test_ac8_dispatch_logs_edit_verdict_with_executed_command(monkeypatch):
    calls = []

    def spy(**kwargs):
        calls.append(kwargs)

    executor = SpyExecutor("/spy")
    confirmer = ScriptedConfirmer([("edit", "mv c d")])
    with StubServer([_tc("run_command", {"command": "mv a b", "description": "d"}),
                     {"kind": "text", "text": "done"}]) as stub:
        _consult(stub.base_url, confirmer, executor, spy, monkeypatch)

    assert len(calls) == 1
    logged = calls[0]
    assert logged["verdict"] == "edit"
    assert logged["executed"] is True
    assert logged["executed_command"] == "mv c d"
    assert logged["arguments"] == {"command": "mv a b", "description": "d"}
    assert logged["result_omitted_reason"] is None


def test_ac8_dispatch_logs_auto_approved(monkeypatch):
    calls = []

    def spy(**kwargs):
        calls.append(kwargs)

    executor = SpyExecutor("/spy")
    confirmer = ScriptedConfirmer([])
    with StubServer([_tc("run_command", {"command": "ls -la", "description": "d"}),
                     {"kind": "text", "text": "done"}]) as stub:
        _consult(stub.base_url, confirmer, executor, spy, monkeypatch)

    assert len(calls) == 1
    logged = calls[0]
    assert logged["verdict"] == "auto-approved"
    assert logged["executed"] is True
    assert logged["result_omitted_reason"] is None
    assert logged["result_text"] == "exit code: 0\nstdout:\nOUT\nstderr:\n"


def test_ac9_dispatch_withholds_sensitive_path_result_from_log_only(monkeypatch):
    calls = []

    def spy(**kwargs):
        calls.append(kwargs)

    executor = SpyExecutor("/spy")
    confirmer = ScriptedConfirmer([("yes", "")])
    with StubServer([
            _tc("run_command", {"command": "cat ~/.ssh/id_rsa", "description": "d"}),
            {"kind": "text", "text": "done"},
    ]) as stub:
        session = _consult(stub.base_url, confirmer, executor, spy, monkeypatch)
        tool_messages = [m["content"] for m in stub.requests[1]["body"]["messages"]
                          if m.get("role") == "tool"]

    assert len(calls) == 1
    logged = calls[0]
    assert logged["result_text"] is None
    assert logged["result_omitted_reason"] == "sensitive_path"
    # The LLM still receives the real result — withholding is log-only.
    assert tool_messages == ["exit code: 0\nstdout:\nOUT\nstderr:\n"]


def test_ac10_unknown_tool_not_logged(monkeypatch):
    calls = []

    def spy(**kwargs):
        calls.append(kwargs)

    executor = SpyExecutor("/spy")
    confirmer = ScriptedConfirmer([])
    with StubServer([_tc("delete_everything", {"path": "/etc/passwd"}),
                     {"kind": "text", "text": "done"}]) as stub:
        _consult(stub.base_url, confirmer, executor, spy, monkeypatch)

    assert calls == []
