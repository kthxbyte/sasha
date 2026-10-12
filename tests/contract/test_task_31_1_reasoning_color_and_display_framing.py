"""Contract tests — Task 31.1: reasoning-stage coloring + tool-turn display
framing (ISSUE-049).

Written by Tier 1 at spec time; immutable to the implementer.
Spec: docs/superpowers/specs/task-31.1-reasoning-color-and-display-framing.md
Design: docs/superpowers/specs/2026-07-25-issue-049-reasoning-color-and-display-framing-design.md
"""

from __future__ import annotations

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from sse_stub import StubServer  # noqa: E402


# =============================================================================
# AC1: ChatClient.chat() separates reasoning_content from content
# =============================================================================

def test_ac1a_on_reasoning_and_on_text_receive_separate_streams():
    from sasha.config import Profile
    from sasha.llm import ChatClient

    with StubServer([{"kind": "text", "text": "the answer",
                       "reasoning": "thinking about it"}]) as stub:
        client = ChatClient(Profile(name="stub", base_url=stub.base_url,
                                    model="m", api_key=""), timeout_s=10.0)
        text_chunks = []
        reasoning_chunks = []
        response = client.chat([{"role": "user", "content": "hi"}], [],
                                on_text=text_chunks.append,
                                on_reasoning=reasoning_chunks.append)

    assert "".join(reasoning_chunks) == "thinking about it"
    assert "".join(text_chunks) == "the answer"
    assert response.text == "the answer"
    assert "thinking" not in response.text


def test_ac1b_on_reasoning_defaults_to_noop_for_backward_compat():
    from sasha.config import Profile
    from sasha.llm import ChatClient

    with StubServer([{"kind": "text", "text": "hello",
                       "reasoning": "thinking"}]) as stub:
        client = ChatClient(Profile(name="stub", base_url=stub.base_url,
                                    model="m", api_key=""), timeout_s=10.0)
        # Omitting on_reasoning entirely must not raise, matching every
        # pre-existing call site of ChatClient.chat().
        response = client.chat([{"role": "user", "content": "hi"}], [],
                                on_text=lambda c: None)

    assert response.text == "hello"


# =============================================================================
# AC2/AC3: TerminalSink chunk-run-kind switching + write_line color param
# =============================================================================

def _make_sink(monkeypatch, isatty=True, no_color=None, **kwargs):
    from sasha.repl import TerminalSink

    monkeypatch.setattr(sys.stdout, "isatty", lambda: isatty)
    if no_color is None:
        monkeypatch.delenv("NO_COLOR", raising=False)
    else:
        monkeypatch.setenv("NO_COLOR", no_color)
    return TerminalSink(**kwargs)


def test_ac2a_reasoning_chunk_uses_reasoning_color(monkeypatch, capsys):
    from sasha.repl import _RESET

    sink = _make_sink(monkeypatch, reasoning_color="\x1b[2;37m")
    sink.write_reasoning_chunk("thinking")
    assert capsys.readouterr().out == "\x1b[2;37mthinking"


def test_ac2b_same_kind_continues_without_reopening_color(monkeypatch, capsys):
    sink = _make_sink(monkeypatch, reasoning_color="\x1b[2;37m")
    sink.write_reasoning_chunk("foo")
    sink.write_reasoning_chunk("bar")
    assert capsys.readouterr().out == "\x1b[2;37mfoobar"


def test_ac2c_kind_switch_mid_stream_inserts_reset_and_newline(monkeypatch, capsys):
    from sasha.repl import _LLM_COLOR, _RESET

    sink = _make_sink(monkeypatch, reasoning_color="\x1b[2;37m")
    sink.write_reasoning_chunk("thinking")   # no trailing newline
    sink.write_chunk("answer")               # kind changes reasoning -> llm
    assert (capsys.readouterr().out
            == f"\x1b[2;37mthinking{_RESET}\n{_LLM_COLOR}answer")


def test_ac2d_kind_switch_already_at_line_start_adds_no_extra_newline(monkeypatch, capsys):
    from sasha.repl import _LLM_COLOR, _RESET

    sink = _make_sink(monkeypatch, reasoning_color="\x1b[2;37m")
    sink.write_reasoning_chunk("thinking\n")  # already ends in a newline
    sink.write_chunk("answer")
    assert (capsys.readouterr().out
            == f"\x1b[2;37mthinking\n{_RESET}{_LLM_COLOR}answer")


def test_ac2e_llm_to_reasoning_switch_also_inserts_break(monkeypatch, capsys):
    from sasha.repl import _LLM_COLOR, _RESET

    sink = _make_sink(monkeypatch, reasoning_color="\x1b[2;37m")
    sink.write_chunk("answer")
    sink.write_reasoning_chunk("more thinking")
    assert (capsys.readouterr().out
            == f"{_LLM_COLOR}answer{_RESET}\n\x1b[2;37mmore thinking")


def test_ac2f_no_color_when_disabled(monkeypatch, capsys):
    sink = _make_sink(monkeypatch, isatty=False, reasoning_color="\x1b[2;37m")
    sink.write_reasoning_chunk("thinking")
    sink.write_chunk("answer")
    assert capsys.readouterr().out == "thinkinganswer"


def test_ac3a_write_line_default_color_unchanged(monkeypatch, capsys):
    from sasha.repl import _RESET, _SASHA_COLOR

    sink = _make_sink(monkeypatch)
    sink.write_line("[sasha] hi")
    assert capsys.readouterr().out == f"{_SASHA_COLOR}[sasha] hi{_RESET}\n"


def test_ac3b_write_line_custom_color(monkeypatch, capsys):
    from sasha.repl import _RESET

    sink = _make_sink(monkeypatch)
    sink.write_line("WARNING", color="\x1b[1;5;31m")
    assert capsys.readouterr().out == f"\x1b[1;5;31mWARNING{_RESET}\n"


def test_ac3c_default_reasoning_color_constant():
    from sasha.repl import _REASONING_COLOR
    assert _REASONING_COLOR == "\x1b[2;37m"


def test_ac3d_warning_color_constant():
    from sasha.repl import _WARNING_COLOR
    assert _WARNING_COLOR == "\x1b[1;5;31m"


# =============================================================================
# AC4: round separator — ensure_newline() between tool-turn rounds
# =============================================================================

class _FakeExecResult:
    def __init__(self, cwd):
        self.exit_code = 0
        self.signal = None
        self.stderr_text = ""
        self.stdout_text = ""
        self.cwd_after = cwd
        self.duration_ms = 1


class _FakeExecutor:
    def __init__(self, cwd):
        self._cwd = cwd
        self.sensitive_path_patterns = ()

    @property
    def cwd(self):
        return self._cwd

    def check_syntax(self, line):
        return None

    def run(self, line, capture_stdout=False):
        return _FakeExecResult(self._cwd)


class _RefusingReplConfirmer:
    def ask(self, command, description):
        return ("no", "")


class _RecordingSink:
    def __init__(self, color_enabled=True):
        self.color_enabled = color_enabled
        self.calls = []

    def write_line(self, text, color=None):
        self.calls.append(("write_line", text))

    def write_chunk(self, text):
        self.calls.append(("write_chunk", text))

    def write_reasoning_chunk(self, text):
        self.calls.append(("write_reasoning_chunk", text))

    def ensure_newline(self):
        self.calls.append(("ensure_newline",))


def _make_repl(cwd="/tmp"):
    from sasha.config import Config, Limits, Profile
    from sasha.llm import ChatClient
    from sasha.repl import Repl
    from sasha.session import Session

    profile = Profile(name="stubprof", base_url="http://unused",
                       model="stub-model", api_key="")
    config = Config(default_profile="stubprof",
                     profiles={"stubprof": profile}, limits=Limits(),
                     activity_indicator_enabled=False)
    executor = _FakeExecutor(cwd)
    sink = _RecordingSink()
    repl = Repl(config, executor, Session(),
                lambda p: ChatClient(p, timeout_s=10.0), _RefusingReplConfirmer(),
                lambda prompt: "", sink)
    repl._current_profile = profile
    return repl, sink


def test_ac4_two_tool_turn_rounds_are_newline_separated(monkeypatch):
    from sasha import agent as agent_module

    monkeypatch.setattr(agent_module.tool_log, "log_call", lambda **kw: None)
    monkeypatch.setattr(agent_module.tool_log, "check_and_maybe_compact_async",
                         lambda out: None)

    repl, sink = _make_repl()

    with StubServer([
        {"kind": "tool_call", "id": "call_1", "name": "list_dir",
         "arguments": {"path": "/a"}, "fragments": 2, "text": "AAA"},
        {"kind": "tool_call", "id": "call_2", "name": "list_dir",
         "arguments": {"path": "/b"}, "fragments": 2, "text": "BBB"},
        {"kind": "text", "text": "CCC"},
    ]) as stub:
        from sasha.config import Profile
        from sasha.llm import ChatClient

        repl._client_factory = lambda p: ChatClient(
            Profile(name="stub", base_url=stub.base_url,
                    model="stub-model", api_key=""), timeout_s=10.0)
        repl._consult(auto_prompt="hola")

    calls = sink.calls
    idx_a = calls.index(("write_chunk", "AAA"))
    idx_b = calls.index(("write_chunk", "BBB"))
    idx_c = calls.index(("write_chunk", "CCC"))
    assert idx_a < idx_b < idx_c
    assert ("ensure_newline",) in calls[idx_a + 1:idx_b]
    assert ("ensure_newline",) in calls[idx_b + 1:idx_c]


# =============================================================================
# AC5: on_status/on_warning fall back to `out` when not supplied
# =============================================================================

class _SpyExecutor:
    def __init__(self, cwd, sensitive_path_patterns=()):
        from sasha.executor import ExecResult

        self.cwd = cwd
        self.sensitive_path_patterns = sensitive_path_patterns
        self.calls = []
        self._result = ExecResult(exit_code=0, signal=None,
                                  stderr_text="", stdout_text="OUT",
                                  cwd_after=cwd, duration_ms=1)

    def run(self, command, capture_stdout=False):
        self.calls.append((command, capture_stdout))
        return self._result


class _RefusingConfirmer:
    def ask(self, summary, description):
        raise AssertionError("confirmer.ask must not be called for auto-approved commands")


def _tc_run_command(command, call_id="call_1"):
    return {"kind": "tool_call", "id": call_id, "name": "run_command",
            "arguments": {"command": command, "description": "a step"},
            "fragments": 2}


def test_ac5a_status_falls_back_to_out_when_not_supplied(monkeypatch):
    from sasha.agent import run_consult
    from sasha.config import Profile
    from sasha.llm import ChatClient
    from sasha.session import Session

    executor = _SpyExecutor("/spy")
    confirmer = _RefusingConfirmer()
    with StubServer([_tc_run_command("ls -la"), {"kind": "text", "text": "done"}]) as stub:
        session = Session()
        session.add_user("help me")
        client = ChatClient(Profile(name="stub", base_url=stub.base_url,
                                    model="stub-model", api_key=""), timeout_s=10.0)
        out_chunks = []
        run_consult(session, client, executor, confirmer, out=out_chunks.append)

    out = "".join(out_chunks)
    assert "(auto-approved)" in out
    assert "ls -la" in out


def test_ac5b_status_used_instead_of_out_when_supplied(monkeypatch):
    from sasha.agent import run_consult
    from sasha.config import Profile
    from sasha.llm import ChatClient
    from sasha.session import Session

    executor = _SpyExecutor("/spy")
    confirmer = _RefusingConfirmer()
    with StubServer([_tc_run_command("ls -la"), {"kind": "text", "text": "done"}]) as stub:
        session = Session()
        session.add_user("help me")
        client = ChatClient(Profile(name="stub", base_url=stub.base_url,
                                    model="stub-model", api_key=""), timeout_s=10.0)
        out_chunks = []
        status_chunks = []
        run_consult(session, client, executor, confirmer, out=out_chunks.append,
                    on_status=status_chunks.append)

    out = "".join(out_chunks)
    status = "".join(status_chunks)
    assert "(auto-approved)" in status
    assert "(auto-approved)" not in out


def test_ac5c_compaction_notice_uses_status_channel(monkeypatch):
    from sasha import agent as agent_module

    calls = []
    monkeypatch.setattr(agent_module.tool_log, "log_call", lambda **kw: None)
    monkeypatch.setattr(agent_module.tool_log, "check_and_maybe_compact_async",
                         lambda cb: calls.append(cb))

    from sasha.agent import run_consult
    from sasha.config import Profile
    from sasha.llm import ChatClient
    from sasha.session import Session

    executor = _SpyExecutor("/spy")
    confirmer = _RefusingConfirmer()
    with StubServer([_tc_run_command("ls -la"), {"kind": "text", "text": "done"}]) as stub:
        session = Session()
        session.add_user("help me")
        client = ChatClient(Profile(name="stub", base_url=stub.base_url,
                                    model="stub-model", api_key=""), timeout_s=10.0)
        status_chunks = []
        run_consult(session, client, executor, confirmer, out=lambda s: None,
                    on_status=status_chunks.append)

    assert len(calls) == 1
    calls[0]("a compaction notice")
    assert "a compaction notice" in status_chunks


# =============================================================================
# AC6: stopping message enrichment
# =============================================================================

# Amended by Tier 1 for Task 58.1: the conflated single stop message split
# into two distinct messages by cause — the turn-limit variant now also
# shows the last action (when one exists) and a config hint, rather than
# staying bare. See docs/superpowers/specs/task-58.1-split-stop-messages.md.
REPEATED_STOP_LINE = "[sasha] stopping: repeated identical action"
LIMIT_STOP_LINE = "[sasha] stopping: hit the"
LIMIT_HINT = (" — raise max_tool_turns under [limits] in config.toml if "
             "this looks like real progress")


def _unknown(call_id):
    return {"kind": "tool_call", "id": call_id, "name": "delete_everything",
            "arguments": {"path": "/etc/passwd"}, "fragments": 2}


def test_ac6a_repeated_action_stop_includes_action_description(monkeypatch):
    from sasha.agent import run_consult
    from sasha.config import Profile
    from sasha.llm import ChatClient
    from sasha.session import Session

    executor = _SpyExecutor("/spy")
    confirmer = _RefusingConfirmer()
    with StubServer([
        {"kind": "tool_call", "id": "c1", "name": "list_dir",
         "arguments": {"path": "/x"}, "fragments": 2},
        {"kind": "tool_call", "id": "c2", "name": "list_dir",
         "arguments": {"path": "/x"}, "fragments": 2},
        {"kind": "text", "text": "final"},
    ]) as stub:
        session = Session()
        session.add_user("do something")
        client = ChatClient(Profile(name="stub", base_url=stub.base_url,
                                    model="stub-model", api_key=""), timeout_s=10.0)
        out_chunks = []
        run_consult(session, client, executor, confirmer, out=out_chunks.append,
                    max_tool_turns=10)

    stop_msgs = [c for c in out_chunks if c.startswith(REPEATED_STOP_LINE)]
    assert len(stop_msgs) == 1
    msg = stop_msgs[0]
    assert msg == REPEATED_STOP_LINE + " — list_dir({'path': '/x'})"


def test_ac6b_turn_limit_only_stop_has_no_action_desc_but_has_hint(monkeypatch):
    from sasha.agent import run_consult
    from sasha.config import Profile
    from sasha.llm import ChatClient
    from sasha.session import Session

    executor = _SpyExecutor("/spy")
    confirmer = _RefusingConfirmer()
    with StubServer([_unknown("call_a"), _unknown("call_b"),
                     {"kind": "text", "text": "final words"}]) as stub:
        session = Session()
        session.add_user("do something")
        client = ChatClient(Profile(name="stub", base_url=stub.base_url,
                                    model="stub-model", api_key=""), timeout_s=10.0)
        out_chunks = []
        run_consult(session, client, executor, confirmer, out=out_chunks.append,
                    max_tool_turns=2)

    stop_msgs = [c for c in out_chunks if c.startswith(LIMIT_STOP_LINE)]
    assert len(stop_msgs) == 1
    # No "last action:" fragment — the two _unknown() calls never execute,
    # so current_action stays None — but the config hint is always present.
    assert stop_msgs[0] == "[sasha] stopping: hit the 2-action limit for this turn" + LIMIT_HINT
    assert "last action:" not in stop_msgs[0]


def test_ac6c_action_description_truncated_at_200_chars(monkeypatch, tmp_path):
    from sasha.agent import run_consult
    from sasha.config import Profile
    from sasha.llm import ChatClient
    from sasha.session import Session

    long_content = "X" * 300

    class _WriteFileExecutor(_SpyExecutor):
        pass

    class _AcceptingConfirmer:
        def ask(self, command, description):
            return ("yes", "")

    executor = _WriteFileExecutor(str(tmp_path))
    confirmer = _AcceptingConfirmer()
    tc = {"kind": "tool_call", "id": "c1", "name": "write_file",
          "arguments": {"path": str(tmp_path / "f.txt"), "content": long_content,
                        "description": "d"}, "fragments": 2}
    tc2 = {"kind": "tool_call", "id": "c2", "name": "write_file",
           "arguments": {"path": str(tmp_path / "f.txt"), "content": long_content,
                        "description": "d"}, "fragments": 2}
    with StubServer([tc, tc2, {"kind": "text", "text": "final"}]) as stub:
        session = Session()
        session.add_user("do something")
        client = ChatClient(Profile(name="stub", base_url=stub.base_url,
                                    model="stub-model", api_key=""), timeout_s=10.0)
        out_chunks = []
        run_consult(session, client, executor, confirmer, out=out_chunks.append,
                    max_tool_turns=10)

    stop_msgs = [c for c in out_chunks if c.startswith(REPEATED_STOP_LINE)]
    assert len(stop_msgs) == 1
    msg = stop_msgs[0]
    prefix = REPEATED_STOP_LINE + " — "
    assert msg.startswith(prefix)
    desc = msg[len(prefix):]
    assert desc.endswith("…")
    assert len(desc) == 201  # 200 chars + the ellipsis character


# =============================================================================
# on_reasoning threads through run_consult and stops the thinking phase
# =============================================================================

def test_ac_run_consult_threads_on_reasoning_and_stops_phase_on_arrival(monkeypatch):
    from sasha import agent as agent_module
    from sasha.config import Profile
    from sasha.llm import ChatClient
    from sasha.session import Session

    monkeypatch.setattr(agent_module.tool_log, "log_call", lambda **kw: None)
    monkeypatch.setattr(agent_module.tool_log, "check_and_maybe_compact_async",
                         lambda out: None)

    executor = _SpyExecutor("/spy")
    confirmer = _RefusingConfirmer()
    with StubServer([{"kind": "text", "text": "answer",
                       "reasoning": "thinking hard"}]) as stub:
        session = Session()
        session.add_user("hi")
        client = ChatClient(Profile(name="stub", base_url=stub.base_url,
                                    model="stub-model", api_key=""), timeout_s=10.0)
        reasoning_chunks = []
        phase_calls = []
        agent_module.run_consult(session, client, executor, confirmer,
                                  out=lambda c: None,
                                  on_reasoning=reasoning_chunks.append,
                                  on_phase=phase_calls.append)

    assert "".join(reasoning_chunks) == "thinking hard"
    assert phase_calls[:2] == ["thinking", None]


# =============================================================================
# config.py: reasoning_color
# =============================================================================

def test_ac_reasoning_color_default(monkeypatch, tmp_path):
    from sasha.config import load

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    cfg = load()
    assert cfg.reasoning_color == "dim-white"


def test_ac_reasoning_color_invalid_raises(tmp_path):
    from sasha.config import ConfigError, load

    path = tmp_path / "config.toml"
    path.write_text('reasoning_color = "not-a-real-token"\n')

    with pytest.raises(ConfigError):
        load(path)


def test_ac_reasoning_color_custom_value_accepted(tmp_path):
    from sasha.config import load

    path = tmp_path / "config.toml"
    path.write_text('reasoning_color = "dim-cyan"\n')
    cfg = load(path)
    assert cfg.reasoning_color == "dim-cyan"
