"""Contract tests — Task 7.1: per-line colors for the confirmation gate.

Written by Tier 1 at spec time; immutable to the implementer.
Fixes ISSUE-023. Spec: docs/superpowers/specs/task-7.1-confirmation-colors.md.
"""

import re
import sys

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


class _CaptureSink:
    """Fake sink exposing a controllable color_enabled flag."""

    def __init__(self, color_enabled):
        self.color_enabled = color_enabled
        self.lines = []

    def write_line(self, text):
        self.lines.append(text)

    def write_chunk(self, text):
        pass

    def ensure_newline(self):
        pass


def _confirmer(color_enabled, response="y"):
    from sasha.repl import TerminalConfirmer

    sink = _CaptureSink(color_enabled)
    conf = TerminalConfirmer(lambda p: response, sink)
    return conf, sink


# --- AC1: plain rendering is byte-identical to the pre-Task-7.1 output -----

def test_ac1_plain_rendering_unchanged_when_color_disabled():
    conf, sink = _confirmer(color_enabled=False)
    conf.ask("ls -la", "list the files")
    assert sink.lines[0] == (
        "[sasha] wants to run:  ls -la\n"
        "        └ list the files\n"
        "        [y]es / [n]o / [e]dit — or type a reply")


def test_ac1_plain_missing_description_placeholder_unchanged():
    conf, sink = _confirmer(color_enabled=False, response="n")
    conf.ask("ls", "")
    assert "└ (no description provided)" in sink.lines[0]
    assert "\x1b[" not in sink.lines[0]


# --- AC2: command line — yellow -----------------------------------------

def test_ac2_command_line_colored_yellow():
    from sasha.repl import _COMMAND_COLOR, _RESET

    conf, sink = _confirmer(color_enabled=True)
    conf.ask("ls -la", "list the files")
    first_line = sink.lines[0].split("\n")[0]
    assert first_line == f"{_COMMAND_COLOR}[sasha] wants to run:  ls -la{_RESET}"


# --- AC3: description line — dim/faint, including the placeholder ---------

def test_ac3_description_line_dim():
    from sasha.repl import _DIM, _RESET

    conf, sink = _confirmer(color_enabled=True)
    conf.ask("ls -la", "list the files")
    second_line = sink.lines[0].split("\n")[1]
    assert second_line == f"{_DIM}        └ list the files{_RESET}"


def test_ac3_missing_description_placeholder_dim():
    from sasha.repl import _DIM, _RESET

    conf, sink = _confirmer(color_enabled=True, response="n")
    conf.ask("ls", "")
    second_line = sink.lines[0].split("\n")[1]
    assert second_line == f"{_DIM}        └ (no description provided){_RESET}"


# --- AC4: decision prompt line reuses the existing _PROMPT_COLOR ----------

def test_ac4_prompt_line_reuses_prompt_color():
    from sasha.repl import _PROMPT_COLOR, _RESET

    conf, sink = _confirmer(color_enabled=True)
    conf.ask("ls -la", "list the files")
    third_line = sink.lines[0].split("\n")[2]
    assert third_line == (
        f"{_PROMPT_COLOR}        [y]es / [n]o / [e]dit — or type a reply{_RESET}")


# --- AC5: colorization adds no/removes no visible text, only escape codes -

def test_ac5_colored_text_matches_plain_text_when_ansi_stripped():
    from sasha.repl import TerminalConfirmer

    plain = _CaptureSink(color_enabled=False)
    TerminalConfirmer(lambda p: "y", plain).ask("ls -la", "list the files")

    colored = _CaptureSink(color_enabled=True)
    TerminalConfirmer(lambda p: "y", colored).ask("ls -la", "list the files")

    assert _ANSI_RE.sub("", colored.lines[0]) == plain.lines[0]


# --- AC6: composes safely through the real TerminalSink's own outer wrap --

def test_ac6_composes_with_terminalsink_outer_wrap(monkeypatch, capsys):
    from sasha.repl import (TerminalConfirmer, TerminalSink, _COMMAND_COLOR,
                            _DIM, _PROMPT_COLOR, _RESET, _SASHA_COLOR)

    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    monkeypatch.delenv("NO_COLOR", raising=False)
    sink = TerminalSink()
    conf = TerminalConfirmer(lambda p: "y", sink)
    conf.ask("ls -la", "list the files")
    out = capsys.readouterr().out

    assert f"{_COMMAND_COLOR}[sasha] wants to run:  ls -la{_RESET}" in out
    assert f"{_DIM}        └ list the files{_RESET}" in out
    assert (f"{_PROMPT_COLOR}        [y]es / [n]o / [e]dit — or type a "
            f"reply{_RESET}") in out
    # TerminalSink's own write_line wrap (untouched) still opens with cyan.
    assert out.startswith(_SASHA_COLOR)
