"""Contract tests — Task 6.2: colorization + user@host prompt (ISSUE-019).

Written by Tier 1 at spec time; immutable to the implementer.
Spec: docs/superpowers/specs/task-6.2-colorization-prompt.md
Design: docs/superpowers/specs/2026-07-15-sasha-v2-output-sink-design.md
§4, §5.2, §5.3.
"""

import sys


def _make_sink(monkeypatch, isatty=True, no_color=None):
    from sasha.repl import TerminalSink

    monkeypatch.setattr(sys.stdout, "isatty", lambda: isatty)
    if no_color is None:
        monkeypatch.delenv("NO_COLOR", raising=False)
    else:
        monkeypatch.setenv("NO_COLOR", no_color)
    return TerminalSink()


# --- AC1: write_line colored when enabled, plain when not -------------------
# write_line also closes any chunk run left open by a preceding write_chunk
# streak, so switching from streamed LLM text to a [sasha] line never leaves
# a dangling, unterminated color code. Task 8.1 (ISSUE-024) additionally
# makes write_line insert the missing separating newline in that case.

def test_ac1_write_line_colored_when_enabled(monkeypatch, capsys):
    from sasha.repl import _RESET, _SASHA_COLOR

    sink = _make_sink(monkeypatch, isatty=True, no_color=None)
    sink.write_line("[sasha] hi")
    assert capsys.readouterr().out == f"{_SASHA_COLOR}[sasha] hi{_RESET}\n"


def test_ac1_write_line_plain_when_disabled(monkeypatch, capsys):
    sink = _make_sink(monkeypatch, isatty=False)
    sink.write_line("[sasha] hi")
    assert capsys.readouterr().out == "[sasha] hi\n"


def test_ac1_write_line_closes_open_chunk_run(monkeypatch, capsys):
    from sasha.repl import _LLM_COLOR, _RESET, _SASHA_COLOR

    sink = _make_sink(monkeypatch, isatty=True, no_color=None)
    sink.write_chunk("partial")
    sink.write_line("done")
    assert (capsys.readouterr().out
            == f"{_LLM_COLOR}partial{_RESET}\n{_SASHA_COLOR}done{_RESET}\n")


# --- AC2: write_chunk opens one color run per contiguous streak, not one ----
# per call — a chunk boundary must never split the visible text with an
# interleaved RESET+COLOR pair, or any consumer doing substring matching on
# the raw byte stream (including a real terminal's own text search, or the
# test_task_3_3_e2e.py PTY harness) breaks on text that happens to straddle
# two network chunks.

def test_ac2_write_chunk_opens_color_run_when_enabled(monkeypatch, capsys):
    from sasha.repl import _LLM_COLOR

    sink = _make_sink(monkeypatch, isatty=True, no_color=None)
    sink.write_chunk("partial")
    # No RESET yet — the run stays open until write_line/ensure_newline.
    assert capsys.readouterr().out == f"{_LLM_COLOR}partial"


def test_ac2_write_chunk_run_stays_byte_contiguous(monkeypatch, capsys):
    from sasha.repl import _LLM_COLOR

    sink = _make_sink(monkeypatch, isatty=True, no_color=None)
    sink.write_chunk("ab")
    sink.write_chunk("cd")
    # Exactly one color code for the whole run — "abcd" is never split.
    assert capsys.readouterr().out == f"{_LLM_COLOR}abcd"


def test_ac2_write_chunk_plain_when_disabled(monkeypatch, capsys):
    sink = _make_sink(monkeypatch, isatty=False)
    sink.write_chunk("partial")
    assert capsys.readouterr().out == "partial"


def test_ac2_write_chunk_empty_still_noop_when_colored(monkeypatch, capsys):
    sink = _make_sink(monkeypatch, isatty=True, no_color=None)
    sink.write_chunk("")
    assert capsys.readouterr().out == ""


# --- AC3: ensure_newline closes any open chunk run; its own newline is -----
# never itself colored.

def test_ac3_ensure_newline_uncolored(monkeypatch, capsys):
    from sasha.repl import _LLM_COLOR

    sink = _make_sink(monkeypatch, isatty=True, no_color=None)
    sink.write_chunk("text")
    sink.ensure_newline()
    out = capsys.readouterr().out
    assert out.endswith("\n")
    assert out.count(_LLM_COLOR) == 1   # only from write_chunk, not the newline


def test_ac3_ensure_newline_closes_run_before_newline(monkeypatch, capsys):
    from sasha.repl import _LLM_COLOR, _RESET

    sink = _make_sink(monkeypatch, isatty=True, no_color=None)
    sink.write_chunk("text")
    sink.ensure_newline()
    assert capsys.readouterr().out == f"{_LLM_COLOR}text{_RESET}\n"


# --- AC4: prompt includes user@host and is color-wrapped with readline markers --

class _CwdExecutor:
    def __init__(self, cwd):
        self.cwd = cwd


class _EOFInput:
    """Records the prompt string it was called with, then ends the loop."""

    def __init__(self):
        self.prompts = []

    def __call__(self, prompt):
        self.prompts.append(prompt)
        raise EOFError


def _repl_for_prompt(sink, cwd="/home/testuser/project"):
    from sasha.config import Config, Limits, Profile
    from sasha.repl import Repl
    from sasha.session import Session

    profiles = {"p": Profile(name="p", base_url="http://unused", model="m", api_key="")}
    config = Config(default_profile="p", profiles=profiles, limits=Limits())
    input_fn = _EOFInput()
    repl = Repl(config, _CwdExecutor(cwd), None, Session(), lambda p: None,
                None, input_fn, sink)
    return repl, input_fn


# Amended by Tier 1 (2026-07-17): DEFAULT_TEMPLATE changed from Task 6.2's
# single-color "\[green\]\u@\h:\w✦ \[reset\]" to a bash-PS1-style, multi-
# segment "\[magenta\][sasha]\[reset\]\[yellow\]✦\[reset\] \[bold-green\]
# \u@\h\[reset\]:\[bold-blue\]\w\[reset\] $ " — a deliberate default-look
# change (Salvador's own edit), not a bug. These four tests now pin the
# new default's exact output; the single-color-wrap shape they used to
# assert no longer applies now that the prompt has four distinct color
# segments instead of one uniform wrap.

def test_ac4_prompt_includes_user_host(monkeypatch):
    import sasha.repl as repl_mod

    monkeypatch.setattr(repl_mod.getpass, "getuser", lambda: "alice")
    monkeypatch.setattr(repl_mod.socket, "gethostname", lambda: "devbox")
    sink = _make_sink(monkeypatch, isatty=False)   # plain, easier to assert
    repl, input_fn = _repl_for_prompt(sink)

    repl.loop()

    assert len(input_fn.prompts) == 1
    prompt = input_fn.prompts[0]
    assert prompt == "[sasha]✦ alice@devbox:/home/testuser/project $ "


def test_ac4_prompt_abbreviates_home(monkeypatch):
    import sasha.repl as repl_mod

    monkeypatch.setattr(repl_mod.getpass, "getuser", lambda: "alice")
    monkeypatch.setattr(repl_mod.socket, "gethostname", lambda: "devbox")
    monkeypatch.setattr(repl_mod.os.path, "expanduser",
                        lambda p: "/home/testuser" if p == "~" else p)
    sink = _make_sink(monkeypatch, isatty=False)
    repl, input_fn = _repl_for_prompt(sink, cwd="/home/testuser/project")

    repl.loop()

    assert input_fn.prompts[0] == "[sasha]✦ alice@devbox:~/project $ "


def test_ac4_prompt_colored_with_readline_markers(monkeypatch):
    import sasha.repl as repl_mod
    from sasha.prompt import COLOR_TOKENS, _RL_IGNORE_END, _RL_IGNORE_START

    monkeypatch.setattr(repl_mod.getpass, "getuser", lambda: "alice")
    monkeypatch.setattr(repl_mod.socket, "gethostname", lambda: "devbox")
    sink = _make_sink(monkeypatch, isatty=True, no_color=None)
    repl, input_fn = _repl_for_prompt(sink, cwd="/tmp")

    repl.loop()

    prompt = input_fn.prompts[0]

    def c(token):
        return f"{_RL_IGNORE_START}{COLOR_TOKENS[token]}{_RL_IGNORE_END}"

    expected = (f"{c('magenta')}[sasha]{c('reset')}"
                f"{c('yellow')}✦{c('reset')} "
                f"{c('bold-green')}alice@devbox{c('reset')}"
                f":{c('bold-blue')}/tmp{c('reset')} $ ")
    assert prompt == expected


def test_ac4_prompt_uncolored_when_color_disabled(monkeypatch):
    import sasha.repl as repl_mod
    from sasha.prompt import _RL_IGNORE_START

    monkeypatch.setattr(repl_mod.getpass, "getuser", lambda: "alice")
    monkeypatch.setattr(repl_mod.socket, "gethostname", lambda: "devbox")
    sink = _make_sink(monkeypatch, isatty=False)
    repl, input_fn = _repl_for_prompt(sink, cwd="/tmp")

    repl.loop()

    prompt = input_fn.prompts[0]
    assert _RL_IGNORE_START not in prompt
    assert "\x1b[" not in prompt
    assert prompt == "[sasha]✦ alice@devbox:/tmp $ "
