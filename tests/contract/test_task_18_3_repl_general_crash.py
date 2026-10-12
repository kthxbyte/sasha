"""Contract tests — Task 18.3: repl.py loads general memory at startup,
the crash-signal trigger, and sasha memory's two-tier display.

Written by Tier 1 at spec time; immutable to the implementer.
Spec: docs/superpowers/specs/task-18.3-repl-general-crash-wiring.md
Design: docs/superpowers/specs/2026-07-17-general-memory-design.md
"""

import json
import pathlib
import signal as signal_module
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from sse_stub import StubServer  # noqa: E402


def _git(repo, *args):
    subprocess.run(["git", *args], cwd=str(repo), check=True,
                    capture_output=True)


def _init_repo(repo):
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")


class FakeExecutor:
    def __init__(self, cwd):
        from sasha.executor import ExecResult
        self._cwd = cwd
        self._last_command = None
        self._last_result = None
        self.calls = []
        self.next_exit = 0
        self.next_signal = None
        self._ExecResult = ExecResult

    @property
    def cwd(self):
        return self._cwd

    # Amended by Tier 1 for Task 25.1 (ISSUE-044): the raw-command path
    # now calls safe_commands.touches_sensitive_path for every command,
    # which reads this attribute.
    sensitive_path_patterns = ()

    @property
    def last_command(self):
        return self._last_command

    @property
    def last_result(self):
        return self._last_result

    def check_syntax(self, line):
        return None

    def run(self, line, capture_stdout=False):
        self.calls.append(line)
        result = self._ExecResult(
            exit_code=self.next_exit, signal=self.next_signal,
            stderr_text="boom" if self.next_signal else "",
            stdout_text="", cwd_after=self._cwd, duration_ms=1)
        self._last_command = line
        self._last_result = result
        return result


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
        if text:
            self.lines.append(text)

    def ensure_newline(self):
        pass


def _repl(cwd, stub_url=None, input_fn=None):
    from sasha.config import Config, Limits, Profile
    from sasha.llm import ChatClient
    from sasha.repl import Repl
    from sasha.session import Session

    profile = Profile(name="stubprof", base_url=stub_url or "http://unused",
                       model="stub-model", api_key="")
    config = Config(default_profile="stubprof",
                     profiles={"stubprof": profile}, limits=Limits())
    executor = FakeExecutor(cwd)
    sink = SpySink()
    repl = Repl(config, executor, Session(),
                lambda p: ChatClient(p, timeout_s=10.0), ScriptedConfirmer(),
                input_fn or (lambda prompt: ""), sink)
    return repl, executor, sink


# --- AC: loop() loads general memory at startup -------------------------------

def test_ac_loop_loads_general_memory_into_session(monkeypatch, tmp_path):
    from sasha.memory import encode_general_remember
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    encode_general_remember("a general preference", None, None)
    plain = tmp_path / "plain"
    plain.mkdir()
    def _eof(prompt):
        raise EOFError
    repl, executor, sink = _repl(str(plain), input_fn=_eof)
    repl.loop()
    assert "a general preference" in repl._session.system_prompt


def test_ac_loop_with_no_general_memory_yet(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    plain = tmp_path / "plain"
    plain.mkdir()
    def _eof(prompt):
        raise EOFError
    repl, executor, sink = _repl(str(plain), input_fn=_eof)
    repl.loop()
    assert "What you generally remember" not in repl._session.system_prompt


# --- AC: sasha memory shows both tiers when both are present -----------------

def test_ac_sasha_memory_shows_general_and_project_together(monkeypatch, tmp_path):
    from sasha.memory import encode_commit, encode_general_remember
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    encode_general_remember("a general fact", None, None)
    repo = tmp_path / "repo"
    _init_repo(repo)
    encode_commit(repo, "a project fact", "h1")
    repl, executor, sink = _repl(str(repo))
    repl._check_place_shift()
    repl.run_once("sasha memory")
    text = "\n".join(sink.lines)
    assert "general memory: 1 entries" in text
    assert "a general fact" in text
    assert "project memory: 1 entries for repo" in text
    assert "a project fact" in text


# --- AC: crash trigger --------------------------------------------------------

def test_ac_crash_signal_encodes_project_entry(tmp_path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    repl, executor, sink = _repl(str(repo))
    repl._check_place_shift()
    executor.next_signal = signal_module.SIGSEGV
    executor.next_exit = 139
    repl.run_once("some-crashy-tool")
    semantic = repo / ".sasha" / "memory" / "semantic.jsonl"
    entry = json.loads(semantic.read_text().splitlines()[0])
    assert entry["kind"] == "crash"
    assert "SIGSEGV" in entry["text"]


def test_ac_crash_signal_outside_repo_encodes_general_entry(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    plain = tmp_path / "plain"
    plain.mkdir()
    repl, executor, sink = _repl(str(plain))
    repl._check_place_shift()
    executor.next_signal = signal_module.SIGABRT
    executor.next_exit = 134
    repl.run_once("some-crashy-tool")
    general_semantic = tmp_path / "xdg" / "sasha" / "memory" / "semantic.jsonl"
    entry = json.loads(general_semantic.read_text().splitlines()[0])
    assert entry["kind"] == "crash"
    assert "SIGABRT" in entry["text"]


def test_ac_non_crash_signal_does_not_trigger(tmp_path):
    import signal as sig
    repo = tmp_path / "repo"
    _init_repo(repo)
    repl, executor, sink = _repl(str(repo))
    repl._check_place_shift()
    executor.next_signal = sig.SIGKILL
    executor.next_exit = 137
    repl.run_once("some-killed-tool")
    assert not (repo / ".sasha" / "memory" / "semantic.jsonl").exists()


def test_ac_ordinary_failure_does_not_trigger_crash(tmp_path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    repl, executor, sink = _repl(str(repo))
    repl._check_place_shift()
    executor.next_signal = None
    executor.next_exit = 1
    repl.run_once("grep pattern nomatch")
    assert not (repo / ".sasha" / "memory" / "semantic.jsonl").exists()
