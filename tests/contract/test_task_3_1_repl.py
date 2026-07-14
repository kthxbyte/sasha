"""Contract tests — Task 3.1: REPL dispatch.

Written by Tier 1 at spec time; immutable to the implementer.
Spec: docs/superpowers/specs/task-3.1-repl.md (design §9.5, §6, §7.4, §10).
Everything injected: spy executor, spy mutes, real Session, real ChatClient
against the SSE stub.
"""

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from sse_stub import StubServer, refused_base_url  # noqa: E402


class SpyExecutor:
    def __init__(self):
        from sasha.executor import ExecResult

        self.calls = []
        self.syntax_error = None      # set to a string to fail the pre-check
        self.next_exit = 0
        self._mk = lambda code: ExecResult(
            exit_code=code, signal=None, stderr_text="spy-stderr",
            stdout_text="", cwd_after="/spy", duration_ms=1)

    @property
    def cwd(self):
        return "/spy"

    def check_syntax(self, line):
        return self.syntax_error

    def run(self, line, capture_stdout=False):
        self.calls.append(line)
        return self._mk(self.next_exit)


class SpyMutes:
    def __init__(self):
        self.log = []
        self.ignored_returns = False   # what record_ignored returns

    def is_muted(self, command, exit_code):
        return False

    def record_ignored(self, command, exit_code):
        self.log.append(("ignored", command, exit_code))
        return self.ignored_returns

    def record_acted(self, command, exit_code):
        self.log.append(("acted", command, exit_code))

    def mute(self, command, exit_code=None):
        self.log.append(("mute", command, exit_code))

    def unmute(self, command, exit_code=None):
        self.log.append(("unmute", command, exit_code))


class ScriptedConfirmer:
    def ask(self, command, description):
        return ("no", "")


def _repl(stub_url, script=None):
    from sasha.config import Config, Limits, Profile
    from sasha.llm import ChatClient
    from sasha.repl import Repl
    from sasha.session import Session

    profiles = {
        "stubprof": Profile(name="stubprof", base_url=stub_url,
                            model="stub-model", api_key=""),
        "other": Profile(name="other", base_url=stub_url,
                         model="other-model", api_key=""),
    }
    config = Config(default_profile="stubprof", profiles=profiles,
                    limits=Limits())
    executor = SpyExecutor()
    mutes = SpyMutes()
    out_chunks = []
    factory_calls = []

    def client_factory(profile):
        factory_calls.append(profile.name)
        return ChatClient(profile, timeout_s=10.0)

    repl = Repl(config, executor, mutes, Session(), client_factory,
                ScriptedConfirmer(), lambda prompt: "", out_chunks.append)
    return repl, executor, mutes, out_chunks, factory_calls


def _out_text(chunks):
    return "\n".join(chunks)


# --- AC1: explicit ask ------------------------------------------------------

def test_ac1_explicit_ask():
    with StubServer([{"kind": "text", "text": "an answer"}]) as stub:
        repl, executor, mutes, out, _ = _repl(stub.base_url)
        assert repl.run_once("?? why is the sky blue") is True
        messages = stub.requests[0]["body"]["messages"]
    assert executor.calls == []
    assert "why is the sky blue" in messages[-1]["content"]
    assert "an answer" in _out_text(out)


# --- AC2: empty line ------------------------------------------------------------

def test_ac2_empty_accepts_pending_offer():
    with StubServer([{"kind": "text", "text": "here is why"}]) as stub:
        repl, executor, mutes, out, _ = _repl(stub.base_url)
        executor.next_exit = 3
        repl.run_once("badcmd --flag")          # OFFER
        assert len(stub.requests) == 0          # zero tokens so far
        repl.run_once("")                       # accept
        assert len(stub.requests) == 1
    assert ("acted", "badcmd", 3) in mutes.log
    assert "here is why" in _out_text(out)


def test_ac2_empty_without_offer_is_noop():
    with StubServer([]) as stub:
        repl, executor, mutes, out, _ = _repl(stub.base_url)
        assert repl.run_once("") is True
        assert stub.requests == []
    assert executor.calls == []
    assert mutes.log == []


# --- AC3: ignoring an offer ---------------------------------------------------------

def test_ac3_ignored_offer_then_processes():
    with StubServer([]) as stub:
        repl, executor, mutes, out, _ = _repl(stub.base_url)
        executor.next_exit = 3
        repl.run_once("badcmd --flag")
        executor.next_exit = 0
        repl.run_once("echo hello")
        assert stub.requests == []
    assert ("ignored", "badcmd", 3) in mutes.log
    assert executor.calls == ["badcmd --flag", "echo hello"]


# --- AC4: syntax pre-check (T2) ---------------------------------------------------------

def test_ac4_syntax_error_never_executes():
    with StubServer([{"kind": "text", "text": "close your quote"}]) as stub:
        repl, executor, mutes, out, _ = _repl(stub.base_url)
        executor.syntax_error = "unexpected EOF while looking for `\"'"
        repl.run_once('echo "unclosed')
        messages = stub.requests[0]["body"]["messages"]
    assert executor.calls == []                       # never executed
    assert "unexpected EOF" in messages[-2]["content"] \
        or "unexpected EOF" in messages[-1]["content"]
    assert "close your quote" in _out_text(out)


# --- AC5: T1 / T6 -------------------------------------------------------------------------

def test_ac5_127_auto_consults():
    with StubServer([{"kind": "text", "text": "not a command"}]) as stub:
        repl, executor, mutes, out, _ = _repl(stub.base_url)
        executor.next_exit = 127
        repl.run_once("550*11")
        assert len(stub.requests) == 1
    assert executor.calls == ["550*11"]
    assert "not a command" in _out_text(out)
    assert "press Enter to ask" not in _out_text(out)   # consult, not offer


def test_ac5_success_is_silent():
    with StubServer([]) as stub:
        repl, executor, mutes, out, _ = _repl(stub.base_url)
        repl.run_once("true")
        assert stub.requests == []
    assert out == []


# --- AC6: §10 vocabulary, byte-exact ------------------------------------------------------------

def test_ac6_offer_line_exact():
    with StubServer([]) as stub:
        repl, executor, mutes, out, _ = _repl(stub.base_url)
        executor.next_exit = 3
        repl.run_once("badcmd")
    assert "[sasha] failed (exit 3) — press Enter to ask" in out


def test_ac6_mute_notice_exact():
    with StubServer([]) as stub:
        repl, executor, mutes, out, _ = _repl(stub.base_url)
        mutes.ignored_returns = True            # this ignore crosses the threshold
        executor.next_exit = 3
        repl.run_once("badcmd")
        repl.run_once("echo next")              # ignores the offer
    assert ("[sasha] muting offers for badcmd exit 3 — "
            "'sasha unmute badcmd' to restore") in out


# --- AC7: builtins ---------------------------------------------------------------------------------

def test_ac7_mute_unmute_builtins():
    with StubServer([]) as stub:
        repl, executor, mutes, out, _ = _repl(stub.base_url)
        assert repl.run_once("sasha mute grep") is True
        assert repl.run_once("sasha mute grep 2") is True
        assert repl.run_once("sasha unmute grep") is True
        assert stub.requests == []
    assert ("mute", "grep", None) in mutes.log
    assert ("mute", "grep", 2) in mutes.log
    assert ("unmute", "grep", None) in mutes.log
    assert executor.calls == []


def test_ac7_profile_builtin():
    with StubServer([{"kind": "text", "text": "hi"}]) as stub:
        repl, executor, mutes, out, factory_calls = _repl(stub.base_url)
        repl.run_once("sasha profile")
        assert any(c.startswith("[sasha] profile: stubprof (stub-model @ ")
                   for c in out)
        repl.run_once("sasha profile other")
        assert any(c.startswith("[sasha] profile: other (other-model @ ")
                   for c in out)
        repl.run_once("?? hello")               # consult uses switched profile
        assert factory_calls[-1] == "other"
        repl.run_once("sasha profile nope")
        assert "[sasha] unknown profile: nope" in out


def test_ac7_help_and_exit():
    with StubServer([]) as stub:
        repl, executor, mutes, out, _ = _repl(stub.base_url)
        assert repl.run_once("sasha help") is True
        assert any(("mute" in c and "profile" in c and "??" in c) for c in out)
        assert repl.run_once("exit") is False
        assert repl.run_once("quit") is False
        assert executor.calls == []


# --- AC8: ChatError containment -----------------------------------------------------------------------

def test_ac8_chaterror_keeps_repl_alive():
    repl, executor, mutes, out, _ = _repl(refused_base_url())
    assert repl.run_once("?? are you there") is True
    line = next(c for c in out if "unreachable" in c)
    assert line.startswith("[sasha] stubprof unreachable: ")
    assert line.endswith("— 'sasha profile <name>' to switch")
    repl.run_once("echo still-works")
    assert executor.calls == ["echo still-works"]


# --- AC9: TerminalConfirmer ------------------------------------------------------------------------------

def _confirmer(inputs):
    from sasha.repl import TerminalConfirmer

    queue = list(inputs)
    out_chunks = []
    conf = TerminalConfirmer(lambda prompt: queue.pop(0), out_chunks.append)
    return conf, out_chunks


def test_ac9_rendering_exact():
    conf, out = _confirmer(["y"])
    verdict = conf.ask("ls -la", "list the files")
    assert verdict == ("yes", "")
    assert out[0] == ("[sasha] wants to run:  ls -la\n"
                      "        └ list the files\n"
                      "        [y]es / [n]o / [e]dit — or type a reply")


def test_ac9_missing_description_placeholder():
    conf, out = _confirmer(["n"])
    conf.ask("ls", "")
    assert "└ (no description provided)" in out[0]


def test_ac9_input_mapping():
    assert _confirmer(["yes"])[0].ask("c", "d") == ("yes", "")
    assert _confirmer(["n"])[0].ask("c", "d") == ("no", "")
    assert _confirmer(["no"])[0].ask("c", "d") == ("no", "")
    assert _confirmer([""])[0].ask("c", "d") == ("no", "")
    assert _confirmer(["e", "edited cmd"])[0].ask("c", "d") == ("edit", "edited cmd")
    assert _confirmer(["edit", "x y z"])[0].ask("c", "d") == ("edit", "x y z")
    assert _confirmer(["use rg instead"])[0].ask("c", "d") == (
        "reply", "use rg instead")
