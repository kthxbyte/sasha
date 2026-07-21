"""Contract tests — Task 5.1: drop the redundant AUTO_CONSULT preamble on
exit 127.

Written by Tier 1 at spec time; immutable to the implementer.
Spec: docs/superpowers/specs/task-5.1-drop-127-preamble.md
Design: docs/superpowers/specs/2026-07-14-sasha-design.md §7.3, §9.5.
"""

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from sse_stub import StubServer  # noqa: E402


class SpyExecutor:
    def __init__(self):
        from sasha.executor import ExecResult

        self.calls = []
        self.syntax_error = None
        self.next_exit = 0
        self._mk = lambda code: ExecResult(
            exit_code=code, signal=None, stderr_text="spy-stderr",
            stdout_text="", cwd_after="/spy", duration_ms=1)

    @property
    def cwd(self):
        return "/spy"

    # Amended by Tier 1 for Task 25.1 (ISSUE-044): the raw-command path
    # now calls safe_commands.touches_sensitive_path for every command,
    # which reads this attribute.
    sensitive_path_patterns = ()

    def check_syntax(self, line):
        return self.syntax_error

    def run(self, line, capture_stdout=False):
        self.calls.append(line)
        return self._mk(self.next_exit)


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
    """Sink test double (Task 6.1) — no coloring, raw text captured."""

    color_enabled = False

    def __init__(self, events):
        self._events = events

    def write_line(self, text):
        self._events.append(text)

    def write_chunk(self, text):
        if text:
            self._events.append(text)

    def ensure_newline(self):
        if self._events and not self._events[-1].endswith("\n"):
            self._events.append("\n")


def _repl(stub_url):
    from sasha.config import Config, Limits, Profile
    from sasha.llm import ChatClient
    from sasha.repl import Repl
    from sasha.session import Session

    profiles = {"stubprof": Profile(name="stubprof", base_url=stub_url,
                                    model="stub-model", api_key="")}
    config = Config(default_profile="stubprof", profiles=profiles,
                    limits=Limits())
    executor = SpyExecutor()
    mutes = SpyMutes()
    out_chunks = []

    def client_factory(profile):
        return ChatClient(profile, timeout_s=10.0)

    repl = Repl(config, executor, mutes, Session(), client_factory,
                ScriptedConfirmer(), lambda prompt: "", SpySink(out_chunks))
    return repl, executor, out_chunks


def _out_text(chunks):
    return "".join(chunks)


# --- AC1: no verbose preamble reaches the LLM -------------------------------

def test_ac1_no_verbose_preamble_sent_to_llm():
    with StubServer([{"kind": "text", "text": "not a command"}]) as stub:
        repl, executor, out = _repl(stub.base_url)
        executor.next_exit = 127
        repl.run_once("550*11")
        messages = stub.requests[0]["body"]["messages"]

    last = messages[-1]["content"]
    assert "550*11" in last
    assert "127" in last
    assert "command not found" not in last
    assert "If it was meant as a question" not in last
    assert "corrected command" not in last


# --- AC2: the answer still reaches the user ---------------------------------

def test_ac2_llm_answer_still_reaches_user():
    with StubServer([{"kind": "text", "text": "not a command"}]) as stub:
        repl, executor, out = _repl(stub.base_url)
        executor.next_exit = 127
        cont = repl.run_once("550*11")

    assert cont is True
    assert executor.calls == ["550*11"]
    assert "not a command" in _out_text(out)


# --- AC3: the unrelated T2 syntax-error prompt is untouched -----------------

def test_ac3_syntax_error_prompt_unchanged():
    with StubServer([{"kind": "text", "text": "close your quote"}]) as stub:
        repl, executor, out = _repl(stub.base_url)
        executor.syntax_error = "unexpected EOF while looking for `\"'"
        repl.run_once('echo "unclosed')
        messages = stub.requests[0]["body"]["messages"]

    assert executor.calls == []
    last = messages[-1]["content"]
    assert "is not valid bash" in last
    assert "unexpected EOF" in last
    assert "Explain and give a corrected line" in last
