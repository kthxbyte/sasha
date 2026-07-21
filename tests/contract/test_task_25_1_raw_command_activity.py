"""Contract tests — Task 25.1: raw-typed command activity awareness
(ISSUE-044).

Written by Tier 1 at spec time; immutable to the implementer.
Spec: docs/superpowers/specs/task-25.1-raw-command-activity.md
Design: docs/superpowers/specs/2026-07-21-issue-044-raw-command-activity-design.md

State-dir isolation (XDG_STATE_HOME) comes from the autouse fixture in
tests/contract/conftest.py (Task 22.1), so tool_log.log_call() never
touches the real ~/.local/state/sasha/ on the machine running this suite.
"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))


class FakeExecutor:
    def __init__(self, cwd, sensitive_path_patterns=("~/.ssh/**", "/etc/passwd")):
        from sasha.executor import ExecResult

        self._cwd = cwd
        self.sensitive_path_patterns = sensitive_path_patterns
        self.next_exit = 0
        self.next_stderr = ""
        self._ExecResult = ExecResult

    @property
    def cwd(self):
        return self._cwd

    def check_syntax(self, line):
        return None

    def run(self, line, capture_stdout=False):
        return self._ExecResult(
            exit_code=self.next_exit, signal=None,
            stderr_text=self.next_stderr, stdout_text="",
            cwd_after=self._cwd, duration_ms=1)


class SpyMutes:
    def is_muted(self, command, exit_code):
        return False

    def record_ignored(self, command, exit_code):
        return False

    def record_acted(self, command, exit_code):
        pass

    def mute(self, command, exit_code=None):
        pass

    def unmute(self, command, exit_code=None):
        pass


class ScriptedConfirmer:
    def ask(self, command, description):
        return ("no", "")


class SpySink:
    color_enabled = False

    def __init__(self):
        self.lines = []

    def write_line(self, text):
        self.lines.append(text)

    def write_chunk(self, text):
        pass

    def ensure_newline(self):
        pass


def _repl(cwd, executor=None):
    from sasha.config import Config, Limits, Profile
    from sasha.llm import ChatClient
    from sasha.repl import Repl
    from sasha.session import Session

    profile = Profile(name="stubprof", base_url="http://unused",
                       model="stub-model", api_key="")
    config = Config(default_profile="stubprof",
                     profiles={"stubprof": profile}, limits=Limits())
    executor = executor or FakeExecutor(cwd)
    sink = SpySink()
    repl = Repl(config, executor, SpyMutes(), Session(),
                lambda p: ChatClient(p, timeout_s=10.0), ScriptedConfirmer(),
                lambda prompt: "", sink)
    return repl, executor, sink


# --- AC1: raw command logged with verdict="direct" --------------------------

def test_ac1_raw_command_logged_with_direct_verdict(tmp_path, monkeypatch):
    from sasha import repl as repl_module

    calls = []
    monkeypatch.setattr(repl_module.tool_log, "log_call",
                         lambda **kw: calls.append(kw))
    monkeypatch.setattr(repl_module.tool_log, "check_and_maybe_compact_async",
                         lambda out: None)

    executor = FakeExecutor(str(tmp_path))
    executor.next_exit = 0
    executor.next_stderr = ""
    repl, executor, sink = _repl(str(tmp_path), executor)
    repl.run_once("echo hello")

    assert len(calls) == 1
    logged = calls[0]
    assert logged["tool_name"] == "run_command"
    assert logged["verdict"] == "direct"
    assert logged["executed"] is True
    assert logged["arguments"] == {"command": "echo hello"}
    assert logged["executed_command"] is None
    assert logged["result_omitted_reason"] is None
    assert logged["result_text"] == "exit code: 0\nstderr:\n"


def test_ac1_raw_command_failure_also_logged(tmp_path, monkeypatch):
    from sasha import repl as repl_module

    calls = []
    monkeypatch.setattr(repl_module.tool_log, "log_call",
                         lambda **kw: calls.append(kw))
    monkeypatch.setattr(repl_module.tool_log, "check_and_maybe_compact_async",
                         lambda out: None)

    executor = FakeExecutor(str(tmp_path))
    executor.next_exit = 1
    executor.next_stderr = "boom: not found"
    repl, executor, sink = _repl(str(tmp_path), executor)
    repl.run_once("false-command")

    assert len(calls) == 1
    logged = calls[0]
    assert logged["verdict"] == "direct"
    assert logged["executed"] is True
    assert logged["result_text"] == "exit code: 1\nstderr:\nboom: not found"


# --- AC2: sensitive-path command withheld -----------------------------------

def test_ac2_raw_command_sensitive_path_withheld(tmp_path, monkeypatch):
    from sasha import repl as repl_module

    calls = []
    monkeypatch.setattr(repl_module.tool_log, "log_call",
                         lambda **kw: calls.append(kw))
    monkeypatch.setattr(repl_module.tool_log, "check_and_maybe_compact_async",
                         lambda out: None)

    executor = FakeExecutor(str(tmp_path))
    repl, executor, sink = _repl(str(tmp_path), executor)
    repl.run_once("cat ~/.ssh/id_rsa")

    assert len(calls) == 1
    logged = calls[0]
    assert logged["result_text"] is None
    assert logged["result_omitted_reason"] == "sensitive_path"


# --- AC3: compaction check triggered after logging --------------------------

def test_ac3_raw_command_triggers_compaction_check(tmp_path, monkeypatch):
    from sasha import repl as repl_module

    monkeypatch.setattr(repl_module.tool_log, "log_call", lambda **kw: None)
    compact_calls = []
    monkeypatch.setattr(repl_module.tool_log, "check_and_maybe_compact_async",
                         lambda out: compact_calls.append(out))

    executor = FakeExecutor(str(tmp_path))
    repl, executor, sink = _repl(str(tmp_path), executor)
    repl.run_once("echo hi")

    assert len(compact_calls) == 1


# --- AC4: notes cap at 5, each with its own stderr_text ---------------------

def _result(exit_code, stderr=""):
    from sasha.executor import ExecResult

    return ExecResult(exit_code=exit_code, signal=None, stderr_text=stderr,
                      stdout_text="", cwd_after="/", duration_ms=1)


def test_ac4_notes_cap_at_five_with_stderr_per_note():
    from sasha.session import Session

    s = Session()
    for i in range(1, 8):  # 7 commands; only the last 5 may surface
        stderr = f"stderr-for-{i}" if i in (6, 7) else ""
        s.note_command(f"probe-{i:02d}", _result(0, stderr))
    s.add_user("what happened?")

    msgs = s.build_messages("/proj")
    content = msgs[-1]["content"]
    for i in range(3, 8):
        assert f"probe-{i:02d}" in content
    assert "probe-01" not in content
    assert "probe-02" not in content
    assert "stderr-for-6" in content
    assert "stderr-for-7" in content

    # cleared after use
    s.add_user("follow-up")
    second = s.build_messages("/proj")
    assert "probe-07" not in second[-1]["content"]
    assert "stderr-for-7" not in second[-1]["content"]


# --- AC5: Session.last_stderr no longer exists ------------------------------

def test_ac5_last_stderr_attribute_removed():
    from sasha.session import Session

    s = Session()
    assert not hasattr(s, "last_stderr")
