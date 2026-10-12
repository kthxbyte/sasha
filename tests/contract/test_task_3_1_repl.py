"""Contract tests — Task 3.1: REPL dispatch.

Written by Tier 1 at spec time; immutable to the implementer.
Spec: docs/superpowers/specs/task-3.1-repl.md (design §9.5, §6, §7.4, §10).
Everything injected: spy executor, real Session, real ChatClient
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
    """Sink test double (Task 6.1) — no coloring, raw text captured in a
    shared list; both write_line and write_chunk append to it so existing
    substring/exact-element assertions written against the old bare-callable
    out() keep working unchanged."""

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


def _repl(stub_url, script=None):
    from sasha.config import Config, Limits, Profile
    from sasha.llm import ChatClient
    from sasha.repl import Repl
    from sasha.session import Session

    # "other" carries a non-empty api_key so `sasha profile other` (AC7)
    # switches immediately rather than triggering Task 15.2's masked
    # key-entry prompt (ISSUE-026) — that prompt path is a new, separate
    # contract covered by tests/contract/test_task_15_2_llm_setup_repl.py.
    profiles = {
        "stubprof": Profile(name="stubprof", base_url=stub_url,
                            model="stub-model", api_key=""),
        "other": Profile(name="other", base_url=stub_url,
                         model="other-model", api_key="other-key"),
    }
    config = Config(default_profile="stubprof", profiles=profiles,
                    limits=Limits(),
                    # Amended by Tier 1 (2026-10-12), fixing ISSUE-057: the
                    # passive directory-recap cascade is enabled by default and
                    # spawns a BACKGROUND thread that makes its own LLM request.
                    # For this helper's fake cwd ("/spy") decide() legitimately
                    # returns ESCALATE, so whether that request lands before or
                    # after a test's `len(stub.requests) == N` assertion is pure
                    # thread-scheduling luck. The recap is irrelevant to
                    # everything these tests assert, so switch it off here
                    # rather than making every request-count assertion racy.
                    directory_recap_enabled=False)
    executor = SpyExecutor()
    out_chunks = []
    factory_calls = []

    def client_factory(profile):
        factory_calls.append(profile.name)
        return ChatClient(profile, timeout_s=10.0)

    repl = Repl(config, executor, Session(), client_factory,
                ScriptedConfirmer(), lambda prompt: "", SpySink(out_chunks))
    return repl, executor, out_chunks, factory_calls


def _out_text(chunks):
    # Concatenate without separators: consult text arrives as STREAMED
    # chunks (Task 3.4), and a "\n" join would split words across chunk
    # boundaries. (Tier 1 amendment for ISSUE-013 — the original "\n".join
    # forced implementations to buffer.)
    return "".join(chunks)


# --- AC1: explicit ask ------------------------------------------------------

def test_ac1_explicit_ask():
    with StubServer([{"kind": "text", "text": "an answer"}]) as stub:
        repl, executor, out, _ = _repl(stub.base_url)
        assert repl.run_once("?? why is the sky blue") is True
        messages = stub.requests[0]["body"]["messages"]
    assert executor.calls == []
    assert "why is the sky blue" in messages[-1]["content"]
    assert "an answer" in _out_text(out)


# --- AC2: empty line ------------------------------------------------------------

def test_ac2_failure_consults_immediately_without_an_offer():
    # Amended by Tier 1 (2026-10-12): the trigger-economy inversion (ISSUE-067)
    # removed the OFFER state. A failing command consults at once; there is no
    # pending offer for a following empty line to accept.
    with StubServer([{"kind": "text", "text": "here is why"}]) as stub:
        repl, executor, out, _ = _repl(stub.base_url)
        executor.next_exit = 3
        repl.run_once("badcmd --flag")
        assert len(stub.requests) == 1          # consulted straight away
        repl.run_once("")                       # empty line is inert
        assert len(stub.requests) == 1
    assert "here is why" in _out_text(out)
    assert "press Enter to ask" not in _out_text(out)


def test_ac2_empty_is_noop():
    with StubServer([]) as stub:
        repl, executor, out, _ = _repl(stub.base_url)
        assert repl.run_once("") is True
        assert stub.requests == []
    assert executor.calls == []


# --- AC3: ignoring an offer ---------------------------------------------------------

def test_ac3_failure_consults_and_the_next_command_still_runs():
    with StubServer([{"kind": "text", "text": "explained"}]) as stub:
        repl, executor, out, _ = _repl(stub.base_url)
        executor.next_exit = 3
        repl.run_once("badcmd --flag")          # consults
        executor.next_exit = 0
        repl.run_once("echo hello")             # success: no consult
        assert len(stub.requests) == 1
    assert executor.calls == ["badcmd --flag", "echo hello"]


# --- AC4: syntax pre-check (T2) ---------------------------------------------------------

def test_ac4_syntax_error_never_executes():
    with StubServer([{"kind": "text", "text": "close your quote"}]) as stub:
        repl, executor, out, _ = _repl(stub.base_url)
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
        repl, executor, out, _ = _repl(stub.base_url)
        executor.next_exit = 127
        repl.run_once("550*11")
        assert len(stub.requests) == 1
    assert executor.calls == ["550*11"]
    assert "not a command" in _out_text(out)
    assert "press Enter to ask" not in _out_text(out)   # consult, not offer


def test_ac5_success_is_silent():
    with StubServer([]) as stub:
        repl, executor, out, _ = _repl(stub.base_url)
        repl.run_once("true")
        assert stub.requests == []
    assert out == []


# --- AC6: §10 vocabulary, byte-exact ------------------------------------------------------------

def test_ac6_no_offer_line_and_no_mute_notice():
    # Amended by Tier 1 (2026-10-12): ISSUE-067 removed both the offer line and
    # the auto-mute notice from the user-visible vocabulary.
    #
    # The stub is deliberately given an EMPTY script, so each consult fails
    # immediately with a fast 500 instead of streaming a response. Five
    # streamed responses made this test sensitive to load: a client timeout
    # mid-stream reset the connection and failed the stub. An empty script
    # still proves what matters -- one consult was ATTEMPTED per failure --
    # and does it deterministically.
    with StubServer([]) as stub:
        repl, executor, out, _ = _repl(stub.base_url)
        executor.next_exit = 3
        for n in range(5):                      # five identical failures
            repl.run_once("badcmd")
        assert len(stub.requests) == 5          # every one attempted a consult
    text = _out_text(out)
    assert "press Enter to ask" not in text
    assert "muting offers for" not in text
    # each failed consult reported itself, rather than being silently dropped
    assert text.count("unreachable:") == 5


# --- AC7: builtins ---------------------------------------------------------------------------------

def test_ac7_mute_unmute_are_no_longer_builtins():
    # Amended by Tier 1 (2026-10-12): ISSUE-067 removed them; they must fall
    # through to "unknown command" rather than silently doing nothing.
    with StubServer([]) as stub:
        repl, executor, out, _ = _repl(stub.base_url)
        assert repl.run_once("sasha mute grep") is True
        assert repl.run_once("sasha unmute grep") is True
        assert stub.requests == []
    assert sum("unknown command" in c for c in out) == 2
    assert executor.calls == []


def test_ac7_profile_builtin():
    with StubServer([{"kind": "text", "text": "hi"}]) as stub:
        repl, executor, out, factory_calls = _repl(stub.base_url)
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
        repl, executor, out, _ = _repl(stub.base_url)
        assert repl.run_once("sasha help") is True
        # Amended by Tier 1 (2026-10-12): mute/unmute dropped from the help line.
        assert any(("profile" in c and "??" in c) for c in out)
        assert repl.run_once("exit") is False
        assert repl.run_once("quit") is False
        assert executor.calls == []


# --- AC8: ChatError containment -----------------------------------------------------------------------

def test_ac8_chaterror_keeps_repl_alive():
    repl, executor, out, _ = _repl(refused_base_url())
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
    conf = TerminalConfirmer(lambda prompt: queue.pop(0), SpySink(out_chunks))
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
