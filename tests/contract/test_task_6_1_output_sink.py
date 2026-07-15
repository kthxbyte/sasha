"""Contract tests — Task 6.1: Sink protocol + TerminalSink (structural).

Written by Tier 1 at spec time; immutable to the implementer.
Spec: docs/superpowers/specs/task-6.1-output-sink.md
Design: docs/superpowers/specs/2026-07-15-sasha-v2-output-sink-design.md §4, §5.1.

Task 6.1 delivers the Sink protocol, TerminalSink's write_line/write_chunk/
ensure_newline mechanics, and the color_enabled flag's correct computation —
all producing PLAIN, uncolored text. Actual color wrapping is Task 6.2; do
not expect ANSI codes here.

Repl/TerminalConfirmer routing through the sink (rather than a bare out()
callable) is exercised by the amended existing files: test_task_3_1_repl.py,
test_task_3_4_repl_streaming.py, test_task_4_1_job_control.py,
test_task_5_1_drop_127_preamble.py, test_task_5_2_thinking_indicator.py —
not duplicated here.
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


# --- AC1: write_line always ends the line -----------------------------------

def test_ac1_write_line_adds_newline(monkeypatch, capsys):
    sink = _make_sink(monkeypatch, isatty=False)
    sink.write_line("hello")
    assert capsys.readouterr().out == "hello\n"


# --- AC2: write_chunk never adds anything, no-ops on empty text -------------

def test_ac2_write_chunk_no_added_newline(monkeypatch, capsys):
    sink = _make_sink(monkeypatch, isatty=False)
    sink.write_chunk("hel")
    sink.write_chunk("lo")
    assert capsys.readouterr().out == "hello"


def test_ac2_write_chunk_empty_is_noop(monkeypatch, capsys):
    sink = _make_sink(monkeypatch, isatty=False)
    sink.write_chunk("")
    assert capsys.readouterr().out == ""


# --- AC3: ensure_newline is conditional --------------------------------------

def test_ac3_ensure_newline_adds_when_missing(monkeypatch, capsys):
    sink = _make_sink(monkeypatch, isatty=False)
    sink.write_chunk("no newline here")
    sink.ensure_newline()
    assert capsys.readouterr().out == "no newline here\n"


def test_ac3_ensure_newline_noop_when_present(monkeypatch, capsys):
    sink = _make_sink(monkeypatch, isatty=False)
    sink.write_line("already a line")
    sink.ensure_newline()
    assert capsys.readouterr().out == "already a line\n"


def test_ac3_ensure_newline_noop_before_any_write(monkeypatch, capsys):
    sink = _make_sink(monkeypatch, isatty=False)
    sink.ensure_newline()
    assert capsys.readouterr().out == ""


def test_ac3_write_line_after_chunk_still_tracked(monkeypatch, capsys):
    # write_line always ends True regardless of what preceded it.
    sink = _make_sink(monkeypatch, isatty=False)
    sink.write_chunk("partial")
    sink.write_line("complete")
    sink.ensure_newline()
    assert capsys.readouterr().out == "partialcomplete\n"


# --- AC4: color_enabled reflects isatty() and NO_COLOR -----------------------

def test_ac4_color_enabled_when_tty_and_no_color_unset(monkeypatch, capsys):
    sink = _make_sink(monkeypatch, isatty=True, no_color=None)
    capsys.readouterr()
    assert sink.color_enabled is True


def test_ac4_color_disabled_when_not_a_tty(monkeypatch, capsys):
    sink = _make_sink(monkeypatch, isatty=False, no_color=None)
    capsys.readouterr()
    assert sink.color_enabled is False


def test_ac4_color_disabled_when_no_color_set(monkeypatch, capsys):
    sink = _make_sink(monkeypatch, isatty=True, no_color="1")
    capsys.readouterr()
    assert sink.color_enabled is False


# --- AC5: Task 6.1 output is plain — no ANSI wrapping yet (that's 6.2) ------

def test_ac5_write_line_unwrapped_even_when_color_enabled(monkeypatch, capsys):
    sink = _make_sink(monkeypatch, isatty=True, no_color=None)
    assert sink.color_enabled is True
    sink.write_line("[sasha] hello")
    assert capsys.readouterr().out == "[sasha] hello\n"
