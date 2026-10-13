"""Contract tests — Task 5.2: "thinking" indicator during LLM consult.

Written by Tier 1 at spec time; immutable to the implementer.
Spec: docs/superpowers/specs/task-5.2-thinking-indicator.md
Design: docs/superpowers/specs/2026-07-14-sasha-design.md §7.4, §10.
"""

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from sse_stub import StubServer, refused_base_url  # noqa: E402

INDICATOR = "[sasha] thinking…"


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
    out_chunks = []

    def client_factory(profile):
        return ChatClient(profile, timeout_s=10.0)

    repl = Repl(config, executor, Session(), client_factory,
                ScriptedConfirmer(), lambda prompt: "", SpySink(out_chunks))
    return repl, executor, out_chunks


# --- AC1: indicator precedes the streamed response text --------------------

def test_ac1_indicator_precedes_response_text():
    with StubServer([{"kind": "text", "text": "an answer"}]) as stub:
        repl, executor, out = _repl(stub.base_url)
        repl.run_once("?? why is the sky blue")

    assert out[0] == INDICATOR
    assert "an answer" in "".join(out[1:])


# --- AC2: indicator precedes a ChatError, and is the only extra line -------

def test_ac2_indicator_precedes_chaterror():
    repl, executor, out = _repl(refused_base_url())
    repl.run_once("?? why is the sky blue")

    assert out[0] == INDICATOR
    assert any("unreachable" in line for line in out[1:])


# --- AC3: indicator fires on every _consult entry point ---------------------

def test_ac3_indicator_on_every_consult_path():
    # a. explicit "??"
    with StubServer([{"kind": "text", "text": "answer one"}]) as stub:
        repl, executor, out = _repl(stub.base_url)
        repl.run_once("?? question")
    assert out.count(INDICATOR) == 1

    # b. auto-consult on exit 127
    with StubServer([{"kind": "text", "text": "answer two"}]) as stub:
        repl, executor, out = _repl(stub.base_url)
        executor.next_exit = 127
        repl.run_once("550*11")
    assert out.count(INDICATOR) == 1

    # c. a CRASH signal, reported by bash as 128+N (SIGSEGV = 139). Amended by
    #    Tier 1 (2026-10-12, ISSUE-078): this slot used an ordinary non-zero exit
    #    (3), which the narrowed trigger policy deliberately no longer consults
    #    on. A crash still consults, so the third distinct consult path this test
    #    exists to cover is preserved -- and it is a better example of "worth
    #    explaining" than a plain status was.
    with StubServer([{"kind": "text", "text": "answer three"}]) as stub:
        repl, executor, out = _repl(stub.base_url)
        executor.next_exit = 139
        repl.run_once("segfault --flag")   # consults at once
    assert out.count(INDICATOR) == 1

    # d. an ordinary non-zero exit consults NOTHING (ISSUE-078) -- no indicator,
    #    because no request is made at all.
    with StubServer([]) as stub:
        repl, executor, out = _repl(stub.base_url)
        executor.next_exit = 1
        repl.run_once("grep nothing_matches here")
    assert out.count(INDICATOR) == 0
    assert stub.requests == []
