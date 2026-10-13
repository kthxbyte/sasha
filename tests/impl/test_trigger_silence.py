"""Silence after ordinary commands — the trigger policy (ISSUE-078).

Salvador reported that Sasha "keeps calling the llm after executing normal
commands like git log", which breaks the ordinary rhythm of using a shell.

The cause was the trigger rule, not `git log`: an earlier change consulted on
*every* non-zero exit, on the reasoning that a failed command is exactly when an
explanation is worth having. Measured against the real executor, that fires for
commands that are working perfectly:

    git log -1 --oneline              exit 0    (fine)
    grep zzz README.md                exit 1    no match — NORMAL
    diff a b                          exit 1    files differ — NORMAL
    git diff --quiet HEAD~5           exit 1    changes present — NORMAL
    git log  (repo with no commits)   exit 128  NORMAL
    GIT_PAGER=... git log             exit 141  SIGPIPE while paging — NORMAL

None of those needs explaining, and a terminal command that returns a status is
not a failure. The policy now consults only when the shell could not do what was
asked, or when a program genuinely crashed.
"""

from sasha.executor import ExecResult
from sasha.triggers import Decision, decide


def _r(code, sig=None):
    return ExecResult(exit_code=code, signal=sig, stderr_text="",
                      stdout_text="", cwd_after="/tmp", duration_ms=1)


# ==========================================================================
# The commands that caused the report must be silent
# ==========================================================================

def test_a_successful_command_is_silent():
    assert decide(_r(0)) is Decision.NOTHING


def test_grep_with_no_match_is_silent():
    """`grep` exits 1 when nothing matched. That is an answer, not a failure."""
    assert decide(_r(1)) is Decision.NOTHING


def test_diff_reporting_differences_is_silent():
    assert decide(_r(1)) is Decision.NOTHING


def test_a_command_killed_by_sigpipe_while_paging_is_silent():
    """`git log` into a pager the user quits early reports 128+13 = 141 once bash
    has translated it, so it arrives as an ordinary exit code."""
    assert decide(_r(141)) is Decision.NOTHING


def test_git_log_in_a_repo_with_no_commits_is_silent():
    """`git log` exits 128 there, which is normal for a fresh repo."""
    assert decide(_r(128)) is Decision.NOTHING


def test_a_missing_path_is_silent():
    assert decide(_r(2)) is Decision.NOTHING


# ==========================================================================
# ...but the shell failing to run the line at all still consults
# ==========================================================================

def test_command_not_found_still_consults():
    """Also how a line of plain English fails, which is the feature."""
    assert decide(_r(127)) is Decision.AUTO_CONSULT


def test_not_executable_still_consults():
    assert decide(_r(126)) is Decision.AUTO_CONSULT


def test_a_crash_signal_still_consults():
    """A program that segfaults is worth explaining even though bash reports it
    as an ordinary 128+N status."""
    for name, code in (("SIGSEGV", 139), ("SIGABRT", 134), ("SIGBUS", 135),
                       ("SIGILL", 132), ("SIGFPE", 136)):
        assert decide(_r(code)) is Decision.AUTO_CONSULT, name


def test_stopping_signals_do_not_consult():
    """Ctrl+C and SIGTERM mean the user (or the system) stopped it."""
    assert decide(_r(130)) is Decision.NOTHING      # SIGINT
    assert decide(_r(143)) is Decision.NOTHING      # SIGTERM


def test_a_reported_signal_never_consults():
    assert decide(_r(130, sig=2)) is Decision.NOTHING
    assert decide(_r(141, sig=13)) is Decision.NOTHING
    assert decide(_r(139, sig=11)) is Decision.NOTHING


# ==========================================================================
# End to end through the REPL: the reported commands make no LLM call
# ==========================================================================

def test_run_once_makes_no_llm_call_for_the_reported_commands(tmp_path):
    import os

    os.environ.setdefault("XDG_STATE_HOME", str(tmp_path / "state"))
    os.environ.setdefault("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    os.chdir(tmp_path)
    os.system("git init -q . && echo hi > f.txt && "
              "git -c user.email=t@e.test -c user.name=T add -A && "
              "git -c user.email=t@e.test -c user.name=T commit -q -m first "
              "2>/dev/null")

    from sasha.config import Config, Limits, Profile
    from sasha.executor import Executor
    from sasha.llm import ChatResponse
    from sasha.repl import Repl
    from sasha.session import Session

    class RecordingClient:
        def __init__(self):
            self.calls = 0

        def chat(self, messages, tools, on_text, on_reasoning=lambda c: None,
                 on_warning=None):
            self.calls += 1
            return ChatResponse(text="(stub)", tool_calls=[])

    client = RecordingClient()

    class Sink:
        color_enabled = False

        def write_line(self, text, color=None):
            pass

        def write_chunk(self, text):
            pass

        def ensure_newline(self):
            pass

    config = Config(default_profile="p",
                    profiles={"p": Profile("p", "http://x/v1", "m", "", False)},
                    limits=Limits())
    repl = Repl(config, Executor(str(tmp_path), {}), Session(),
                lambda p: client, None, lambda prompt: "", Sink())

    for line in ("git log -1 --oneline", "git status", "grep zzz f.txt",
                 "diff f.txt f.txt"):
        before = client.calls
        repl.run_once(line)
        assert client.calls == before, f"{line!r} made an LLM call"

    # ...while a line that names no command still does consult
    before = client.calls
    repl.run_once("this_is_not_a_command_xyz")
    assert client.calls == before + 1
