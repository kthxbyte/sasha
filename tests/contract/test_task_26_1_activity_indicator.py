"""Contract tests — Task 26.1: cursor-based activity indicator (ISSUE-045).

Written by Tier 1 at spec time; immutable to the implementer.
Spec: docs/superpowers/specs/task-26.1-activity-indicator.md
Design: docs/superpowers/specs/2026-07-21-issue-045-cursor-activity-indicator-design.md
"""

from __future__ import annotations

import pathlib
import sys
import threading
import time

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from sse_stub import StubServer  # noqa: E402


# --- AC1-AC5: ActivityIndicator, pure unit tests ----------------------------

def test_ac1_start_stop_emits_expected_sequences(capsys):
    from sasha.activity_indicator import ActivityIndicator

    ind = ActivityIndicator(colors={"thinking": "#C084FC"},
                             interval_s=0.01, enabled=True)
    ind.start("thinking")
    time.sleep(0.05)
    ind.stop()

    out = capsys.readouterr().out
    assert "\x1b]12;#C084FC\x07" in out
    assert "\x1b[?25l" in out
    assert "\x1b[?25h" in out
    assert "\x1b]112\x07" in out


def test_ac2_unknown_phase_is_noop(capsys):
    from sasha.activity_indicator import ActivityIndicator

    ind = ActivityIndicator(colors={"thinking": "#C084FC"},
                             interval_s=0.01, enabled=True)
    ind.start("not_a_real_phase")
    time.sleep(0.05)
    ind.stop()

    assert capsys.readouterr().out == ""


def test_ac3_disabled_is_always_noop(capsys):
    from sasha.activity_indicator import ActivityIndicator

    ind = ActivityIndicator(colors={"thinking": "#C084FC"},
                             interval_s=0.01, enabled=False)
    ind.start("thinking")
    time.sleep(0.05)
    ind.stop()

    assert capsys.readouterr().out == ""


def test_ac4_stop_is_idempotent():
    from sasha.activity_indicator import ActivityIndicator

    ind = ActivityIndicator(colors={"thinking": "#C084FC"},
                             interval_s=0.01, enabled=True)
    ind.stop()  # no prior start -- must not raise
    ind.start("thinking")
    ind.stop()
    ind.stop()  # second stop -- must not raise


def test_ac5_restart_replaces_running_thread():
    from sasha.activity_indicator import ActivityIndicator

    ind = ActivityIndicator(colors={"thinking": "#C084FC", "tool": "#FFD700"},
                             interval_s=0.01, enabled=True)
    before = threading.active_count()
    ind.start("thinking")
    time.sleep(0.02)
    mid = threading.active_count()
    ind.start("tool")  # no intervening stop()
    time.sleep(0.02)
    after_restart = threading.active_count()
    ind.stop()
    time.sleep(0.02)
    final = threading.active_count()

    assert mid == before + 1
    assert after_restart == before + 1
    assert final == before


# --- AC6-AC11: agent.py run_consult phase sequencing ------------------------

class SpyExecutor:
    def __init__(self, cwd):
        from sasha.executor import ExecResult

        self.cwd = cwd
        self.sensitive_path_patterns = ()
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


def _consult(stub_url, confirmer, executor, monkeypatch, max_tool_turns=10):
    from sasha import agent as agent_module
    from sasha.config import Profile
    from sasha.llm import ChatClient
    from sasha.session import Session

    monkeypatch.setattr(agent_module.tool_log, "log_call", lambda **kw: None)
    monkeypatch.setattr(agent_module.tool_log, "check_and_maybe_compact_async",
                         lambda out: None)

    session = Session()
    session.add_user("help me")
    client = ChatClient(Profile(name="stub", base_url=stub_url,
                                model="stub-model", api_key=""), timeout_s=10.0)
    phase_calls = []
    text_calls = []
    agent_module.run_consult(
        session, client, executor, confirmer, out=text_calls.append,
        max_tool_turns=max_tool_turns, on_phase=phase_calls.append)
    return phase_calls, text_calls


def test_ac6_thinking_phase_stops_on_first_text(monkeypatch):
    executor = SpyExecutor("/spy")
    confirmer = ScriptedConfirmer([])
    with StubServer([{"kind": "text", "text": "hello there"}]) as stub:
        phase_calls, text_calls = _consult(stub.base_url, confirmer, executor, monkeypatch)

    assert phase_calls[0] == "thinking"
    assert None in phase_calls
    stop_index = phase_calls.index(None)
    assert text_calls  # some text streamed
    # the stop must have registered before/at the point text was flowing
    assert phase_calls[:stop_index + 1] == ["thinking", None]


def test_ac7_thinking_phase_stops_after_chat_returns_when_no_text(monkeypatch):
    executor = SpyExecutor("/spy")
    confirmer = ScriptedConfirmer([("yes", "")])
    with StubServer([_tc("run_command", {"command": "ls", "description": "d"}, "call_1"),
                     {"kind": "text", "text": "done"}]) as stub:
        phase_calls, text_calls = _consult(stub.base_url, confirmer, executor, monkeypatch)

    # First turn (pure tool-call, no text) must still bracket with thinking/None
    assert phase_calls[0] == "thinking"
    assert phase_calls[1] is None


def test_ac8_tool_phase_wraps_handler_execution_confirmed(monkeypatch):
    executor = SpyExecutor("/spy")
    confirmer = ScriptedConfirmer([("yes", "")])
    with StubServer([_tc("run_command", {"command": "ls", "description": "d"}, "call_1"),
                     {"kind": "text", "text": "done"}]) as stub:
        phase_calls, _ = _consult(stub.base_url, confirmer, executor, monkeypatch)

    assert "tool" in phase_calls
    tool_index = phase_calls.index("tool")
    assert phase_calls[tool_index + 1] is None


def test_ac8_tool_phase_wraps_handler_execution_auto_approved(monkeypatch):
    executor = SpyExecutor("/spy")
    confirmer = ScriptedConfirmer([])
    with StubServer([_tc("run_command", {"command": "ls -la", "description": "d"}, "call_1"),
                     {"kind": "text", "text": "done"}]) as stub:
        phase_calls, _ = _consult(stub.base_url, confirmer, executor, monkeypatch)

    assert "tool" in phase_calls


def test_ac9_declined_tool_never_triggers_tool_phase(monkeypatch):
    executor = SpyExecutor("/spy")
    confirmer = ScriptedConfirmer([("no", "")])
    with StubServer([_tc("run_command", {"command": "rm -rf /", "description": "d"}, "call_1"),
                     {"kind": "text", "text": "ok"}]) as stub:
        phase_calls, _ = _consult(stub.base_url, confirmer, executor, monkeypatch)

    assert "tool" not in phase_calls


def test_ac10_final_stop_message_consult_also_wrapped(monkeypatch):
    executor = SpyExecutor("/spy")
    confirmer = ScriptedConfirmer([("yes", ""), ("yes", "")])
    with StubServer([
            _tc("run_command", {"command": "ls", "description": "d"}, "call_a"),
            _tc("run_command", {"command": "ls", "description": "d"}, "call_b"),
            {"kind": "text", "text": "final answer"},
    ]) as stub:
        phase_calls, text_calls = _consult(stub.base_url, confirmer, executor,
                                            monkeypatch, max_tool_turns=10)

    # repetition guard fires on the second identical "ls" call -> triggers
    # the final stop-message consult, which must also be thinking-wrapped.
    assert phase_calls.count("thinking") >= 2
    assert "final answer" in "".join(text_calls)


# --- AC12-AC14: repl.py wiring ------------------------------------------------

class FakeExecutor:
    def __init__(self, cwd):
        from sasha.executor import ExecResult

        self._cwd = cwd
        self.sensitive_path_patterns = ()
        self._ExecResult = ExecResult

    @property
    def cwd(self):
        return self._cwd

    def check_syntax(self, line):
        return None

    def run(self, line, capture_stdout=False):
        return self._ExecResult(exit_code=0, signal=None, stderr_text="",
                                stdout_text="", cwd_after=self._cwd, duration_ms=1)


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


class ScriptedReplConfirmer:
    def ask(self, command, description):
        return ("no", "")


class SpySink:
    def __init__(self, color_enabled=True):
        self.color_enabled = color_enabled
        self.lines = []

    def write_line(self, text):
        self.lines.append(text)

    def write_chunk(self, text):
        pass

    def ensure_newline(self):
        pass


class SpyActivity:
    def __init__(self):
        self.calls = []

    def start(self, phase):
        self.calls.append(("start", phase))

    def stop(self):
        self.calls.append(("stop", None))


def _repl(cwd, color_enabled=True, activity_enabled=True):
    from sasha.config import Config, Limits, Profile
    from sasha.llm import ChatClient
    from sasha.repl import Repl
    from sasha.session import Session

    profile = Profile(name="stubprof", base_url="http://unused",
                       model="stub-model", api_key="")
    config = Config(default_profile="stubprof",
                     profiles={"stubprof": profile}, limits=Limits(),
                     activity_indicator_enabled=activity_enabled)
    executor = FakeExecutor(cwd)
    sink = SpySink(color_enabled=color_enabled)
    repl = Repl(config, executor, SpyMutes(), Session(),
                lambda p: ChatClient(p, timeout_s=10.0), ScriptedReplConfirmer(),
                lambda prompt: "", sink)
    return repl, executor, sink


def test_ac12_raw_command_not_wrapped_with_activity_indicator():
    # ISSUE-048: raw-typed commands hand the child real foreground terminal
    # control (Executor.run()'s tcsetpgrp handoff), so the indicator's
    # cursor toggling must not run alongside full-screen apps (nano, mc,
    # git log's pager) invoked this way. Inverted from the original AC12,
    # which pinned the opposite (now-removed) behavior.
    repl, executor, sink = _repl("/tmp")
    spy = SpyActivity()
    repl._activity = spy
    repl.run_once("echo hi")

    assert spy.calls == []


def test_ac13_consult_stops_activity_on_chat_error():
    from sse_stub import refused_base_url

    repl, executor, sink = _repl("/tmp")
    spy = SpyActivity()
    repl._activity = spy
    repl._current_profile = repl._config.profiles["stubprof"]

    def _bad_client_factory(profile):
        from sasha.config import Profile
        from sasha.llm import ChatClient
        return ChatClient(Profile(name="bad", base_url=refused_base_url(),
                                  model="m", api_key=""), timeout_s=2.0)

    repl._client_factory = _bad_client_factory
    repl._consult()

    assert ("stop", None) in spy.calls


def test_ac14_repl_combines_config_and_color_enabled_gates():
    repl_both_on, _, _ = _repl("/tmp", color_enabled=True, activity_enabled=True)
    assert repl_both_on._activity._enabled is True

    repl_config_off, _, _ = _repl("/tmp", color_enabled=True, activity_enabled=False)
    assert repl_config_off._activity._enabled is False

    repl_color_off, _, _ = _repl("/tmp", color_enabled=False, activity_enabled=True)
    assert repl_color_off._activity._enabled is False


# --- AC15: config default + fallback ----------------------------------------

@pytest.fixture
def isolated_env(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    return tmp_path


def test_ac15_config_default_and_fallback(isolated_env, tmp_path):
    from sasha.config import load

    config = load()
    assert config.activity_indicator_enabled is True
    assert config.activity_indicator_interval_s == 0.05
    assert config.activity_indicator_thinking_color == "#C084FC"
    assert config.activity_indicator_tool_color == "#FFD700"
    written = (isolated_env / "xdg-config" / "sasha" / "config.toml").read_text()
    assert "[activity_indicator]" in written

    p = tmp_path / "override.toml"
    p.write_text('default_profile = "llm7"\n[profiles.llm7]\n'
                 'base_url = "https://api.llm7.io/v1"\nmodel = "gpt-oss:20b"\n'
                 '[activity_indicator]\nenabled = false\ninterval_s = 0.2\n'
                 'thinking_color = "#123456"\ntool_color = "#abcdef"\n')
    overridden = load(p)
    assert overridden.activity_indicator_enabled is False
    assert overridden.activity_indicator_interval_s == 0.2
    assert overridden.activity_indicator_thinking_color == "#123456"
    assert overridden.activity_indicator_tool_color == "#abcdef"

    p2 = tmp_path / "legacy.toml"
    p2.write_text('default_profile = "llm7"\n[profiles.llm7]\n'
                  'base_url = "https://api.llm7.io/v1"\nmodel = "gpt-oss:20b"\n')
    legacy = load(p2)
    assert legacy.activity_indicator_enabled is True
    assert legacy.activity_indicator_interval_s == 0.05

    p3 = tmp_path / "bad_types.toml"
    p3.write_text('default_profile = "llm7"\n[profiles.llm7]\n'
                  'base_url = "https://api.llm7.io/v1"\nmodel = "gpt-oss:20b"\n'
                  '[activity_indicator]\nenabled = "yes"\ninterval_s = -1\n')
    bad = load(p3)
    assert bad.activity_indicator_enabled is True
    assert bad.activity_indicator_interval_s == 0.05
