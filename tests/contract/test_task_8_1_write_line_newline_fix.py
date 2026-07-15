"""Contract tests — Task 8.1: write_line() closes a dangling unterminated
line before printing (ISSUE-024).

Written by Tier 1 at spec time; immutable to the implementer.
Spec: docs/superpowers/specs/task-8.1-write-line-newline-fix.md
Issue: issues/ISSUE-024-write-line-missing-ensure-newline.md

Two existing contract tests pinned the pre-fix byte sequence and are
amended alongside this file (not duplicated here):
test_task_6_1_output_sink.py::test_ac3_write_line_after_chunk_still_tracked
test_task_6_2_colorization_prompt.py::test_ac1_write_line_closes_open_chunk_run
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


# --- AC1: missing newline inserted before write_line's own text, plain -----

def test_ac1_write_line_inserts_missing_newline_plain(monkeypatch, capsys):
    sink = _make_sink(monkeypatch, isatty=False)
    sink.write_chunk("partial")
    sink.write_line("done")
    assert capsys.readouterr().out == "partial\ndone\n"


# --- AC2: no double newline when the preceding write already ended in \n --

def test_ac2_write_line_no_double_newline_when_chunk_ended_in_newline(
        monkeypatch, capsys):
    sink = _make_sink(monkeypatch, isatty=False)
    sink.write_chunk("partial\n")
    sink.write_line("done")
    assert capsys.readouterr().out == "partial\ndone\n"


# --- AC3: colored — separating \n lands after RESET, never itself colored -

def test_ac3_write_line_inserts_missing_newline_colored(monkeypatch, capsys):
    from sasha.repl import _LLM_COLOR, _RESET, _SASHA_COLOR

    sink = _make_sink(monkeypatch, isatty=True, no_color=None)
    sink.write_chunk("partial")
    sink.write_line("done")
    assert (capsys.readouterr().out
            == f"{_LLM_COLOR}partial{_RESET}\n{_SASHA_COLOR}done{_RESET}\n")


# --- AC4: no leading newline on the very first write ------------------------

def test_ac4_write_line_first_call_no_leading_newline(monkeypatch, capsys):
    sink = _make_sink(monkeypatch, isatty=False)
    sink.write_line("first")
    assert capsys.readouterr().out == "first\n"


# --- AC5: consecutive write_line calls get exactly one \n each -------------

def test_ac5_consecutive_write_line_calls_no_extra_blank_lines(
        monkeypatch, capsys):
    sink = _make_sink(monkeypatch, isatty=False)
    sink.write_line("a")
    sink.write_line("b")
    assert capsys.readouterr().out == "a\nb\n"


# --- AC6: ensure_newline remains a no-op right after write_line ------------

def test_ac6_ensure_newline_still_noop_after_write_line(monkeypatch, capsys):
    sink = _make_sink(monkeypatch, isatty=False)
    sink.write_chunk("partial")
    sink.write_line("done")
    sink.ensure_newline()
    assert capsys.readouterr().out == "partial\ndone\n"
