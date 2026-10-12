"""Supplementary tests (not contract) — compact reasoning display.

The default display mode shows one line that advances as each reasoning
paragraph's first line completes (ported from DSH's ReasoningRow
`latestCompletedParagraphFirstLine`), instead of streaming every chunk. The
old behaviour is kept as `--reasoning full` / `reasoning_mode = "full"`.

TerminalSink's own default stays "full" so a bare TerminalSink() keeps the
long-standing streaming behaviour the contract suite pins; "compact" is
applied by __main__ from config.
"""

from __future__ import annotations

import dataclasses
import sys

import pytest

from sasha import config as config_mod
from sasha.repl import (
    Repl,
    TerminalSink,
    _first_line,
    _latest_completed_paragraph_first_line,
)


def _make_sink(monkeypatch, isatty=True, no_color=True, **kwargs):
    """no_color defaults True so assertions aren't tangled up in SGR codes;
    the colour path is already covered by the contract suite."""
    monkeypatch.setattr(sys.stdout, "isatty", lambda: isatty)
    if no_color:
        monkeypatch.setenv("NO_COLOR", "1")
    else:
        monkeypatch.delenv("NO_COLOR", raising=False)
    return TerminalSink(**kwargs)


# --------------------------------------------------------------------------
# the paragraph rule itself
# --------------------------------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("", ""),
    ("no newline yet", ""),                                  # in-flight: hold
    ("first line done\nbody", "first line done"),
    ("a\nbody\n\nsecond para", "a"),                         # 2nd not yet complete
    ("a\nbody\n\nsecond first\nbody", "second first"),        # advances
    ("a\n\n\n\nb\nc\n\n", "b"),
    ("   \n\n", ""),                                         # blank paragraphs skipped
])
def test_latest_completed_paragraph_first_line(text, expected):
    assert _latest_completed_paragraph_first_line(text) == expected


def test_first_line_returns_partial_when_unterminated():
    assert _first_line("one paragraph only") == "one paragraph only"
    assert _first_line("one\ntwo") == "one"


# --------------------------------------------------------------------------
# compact rendering
# --------------------------------------------------------------------------

def test_compact_holds_still_until_a_paragraph_first_line_completes(monkeypatch, capsys):
    sink = _make_sink(monkeypatch, reasoning_mode="compact")

    sink.write_reasoning_chunk("We need to answer")      # no newline yet
    assert capsys.readouterr().out == ""                  # nothing drawn

    sink.write_reasoning_chunk(" the user.\nMore detail")  # 1st line completes
    out = capsys.readouterr().out
    assert out == "[thinking] We need to answer the user."

    sink.write_reasoning_chunk("\n\nA second paragraph")   # 2nd still in-flight
    assert capsys.readouterr().out == ""                   # held, not redrawn

    sink.write_reasoning_chunk(" continues\nand body")     # 2nd line completes
    out = capsys.readouterr().out
    assert out == "\r\x1b[2K[thinking] A second paragraph continues"


def test_compact_finalises_one_line_then_streams_content(monkeypatch, capsys):
    sink = _make_sink(monkeypatch, reasoning_mode="compact")

    sink.write_reasoning_chunk("single paragraph, never terminated")
    sink.write_chunk("the answer")

    out = capsys.readouterr().out
    # exactly one dim reasoning line, then the answer on the next line
    assert out == "[thinking] single paragraph, never terminated\nthe answer"


def test_compact_emits_nothing_when_the_model_did_not_reason(monkeypatch, capsys):
    sink = _make_sink(monkeypatch, reasoning_mode="compact")

    sink.write_chunk("just an answer")

    assert capsys.readouterr().out == "just an answer"


def test_compact_off_a_tty_draws_no_escape_sequences(monkeypatch, capsys):
    sink = _make_sink(monkeypatch, isatty=False, reasoning_mode="compact")

    sink.write_reasoning_chunk("line one done\nbody")
    sink.write_reasoning_chunk("\n\nline two done\nbody")
    assert capsys.readouterr().out == ""          # nothing incremental

    sink.write_chunk("answer")
    out = capsys.readouterr().out
    assert out == "[thinking] line two done\nanswer"
    assert "\r" not in out and "\x1b[2K" not in out


def test_compact_preview_is_truncated_to_terminal_width(monkeypatch, capsys):
    sink = _make_sink(monkeypatch, reasoning_mode="compact")
    monkeypatch.setattr("shutil.get_terminal_size",
                        lambda *a, **k: __import__("os").terminal_size((40, 24)))

    sink.write_reasoning_chunk("x" * 200 + "\nbody")
    out = capsys.readouterr().out
    assert out.startswith("[thinking] ")
    assert out.endswith("\u2026")
    assert len(out) == 39          # one column spare: (cols - prefix - 1) + prefix
    assert "\n" not in out         # still a single line


def test_full_mode_still_streams_every_chunk(monkeypatch, capsys):
    """Regression guard: a bare TerminalSink() is untouched."""
    sink = _make_sink(monkeypatch, no_color=False)     # colour on, as the contract suite does
    assert sink.reasoning_mode == "full"

    sink.write_reasoning_chunk("foo")
    sink.write_reasoning_chunk("bar")
    assert capsys.readouterr().out == "\x1b[2;37mfoobar"

    sink.write_reasoning_chunk("baz")
    sink.write_chunk("answer")
    # the reasoning colour run is still open (test_ac2b semantics), so only the
    # kind switch emits the reset + newline before the answer's colour opens.
    assert capsys.readouterr().out == "baz\x1b[0m\n\x1b[1;35manswer"


def test_write_line_finalises_an_open_preview(monkeypatch, capsys):
    sink = _make_sink(monkeypatch, reasoning_mode="compact")

    sink.write_reasoning_chunk("held line\nbody")
    sink.write_line("[sasha] something")

    out = capsys.readouterr().out
    assert out == "[thinking] held line\n[sasha] something\n"


# --------------------------------------------------------------------------
# retention + `sasha reasoning` (compact must not be lossy)
# --------------------------------------------------------------------------

def test_reasoning_is_retained_even_though_only_one_line_is_shown(monkeypatch, capsys):
    sink = _make_sink(monkeypatch, reasoning_mode="compact")

    sink.write_reasoning_chunk("para one first line\npara one body\n\n")
    sink.write_reasoning_chunk("para two first line\npara two body")
    shown = capsys.readouterr().out

    assert "para one body" not in shown          # not displayed...
    assert "para one body" in sink.reasoning_text()   # ...but retained
    assert "para two body" in sink.reasoning_text()


def test_reasoning_retention_is_capped_and_flags_truncation(monkeypatch):
    from sasha.repl import _MAX_REASONING_LOG_CHARS

    sink = _make_sink(monkeypatch, reasoning_mode="compact")
    sink.write_reasoning_chunk("a" * (_MAX_REASONING_LOG_CHARS + 500))

    assert len(sink.reasoning_text()) == _MAX_REASONING_LOG_CHARS
    assert sink.reasoning_truncated is True


def test_sasha_reasoning_builtin_prints_the_retained_text(monkeypatch, capsys):
    sink = _make_sink(monkeypatch, reasoning_mode="compact")
    sink.write_reasoning_chunk("the model thought about it\nand more detail")

    class _Stub:
        _sink = sink

    Repl._handle_reasoning_builtin(_Stub())

    out = capsys.readouterr().out
    assert "the model thought about it" in out
    assert "and more detail" in out


def test_sasha_reasoning_builtin_says_so_when_empty(monkeypatch, capsys):
    sink = _make_sink(monkeypatch, reasoning_mode="compact")

    class _Stub:
        _sink = sink

    Repl._handle_reasoning_builtin(_Stub())

    assert "no reasoning recorded" in capsys.readouterr().out


def test_sasha_reasoning_builtin_notes_truncation(monkeypatch, capsys):
    from sasha.repl import _MAX_REASONING_LOG_CHARS

    sink = _make_sink(monkeypatch, reasoning_mode="compact")
    sink.write_reasoning_chunk("a" * (_MAX_REASONING_LOG_CHARS + 10))

    class _Stub:
        _sink = sink

    Repl._handle_reasoning_builtin(_Stub())

    assert "earlier reasoning dropped" in capsys.readouterr().out


# --------------------------------------------------------------------------
# config + CLI
# --------------------------------------------------------------------------

def _isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))


def test_reasoning_mode_defaults_to_compact(monkeypatch, tmp_path):
    _isolated(monkeypatch, tmp_path)
    assert config_mod.load(None).reasoning_mode == "compact"


def test_reasoning_mode_default_is_in_the_dataclass():
    assert config_mod.Config.__dataclass_fields__["reasoning_mode"].default == "compact"


def test_reasoning_mode_full_is_accepted(monkeypatch, tmp_path):
    _isolated(monkeypatch, tmp_path)
    p = tmp_path / "config.toml"
    p.write_text('reasoning_mode = "full"\n')
    assert config_mod.load(p).reasoning_mode == "full"


def test_reasoning_mode_rejects_an_unknown_value(monkeypatch, tmp_path):
    _isolated(monkeypatch, tmp_path)
    p = tmp_path / "config.toml"
    p.write_text('reasoning_mode = "loud"\n')
    with pytest.raises(config_mod.ConfigError) as exc:
        config_mod.load(p)
    assert "reasoning_mode" in str(exc.value)
    assert "compact" in str(exc.value) and "full" in str(exc.value)


def test_self_installed_config_writes_reasoning_mode(monkeypatch, tmp_path):
    _isolated(monkeypatch, tmp_path)
    cfg = config_mod.load(None)
    assert 'reasoning_mode = "compact"' in cfg.path.read_text()


def test_cli_flag_overrides_config(monkeypatch, tmp_path):
    from sasha.__main__ import _resolve_reasoning_mode

    _isolated(monkeypatch, tmp_path)
    default_cfg = config_mod.load(None)
    assert _resolve_reasoning_mode(None, default_cfg) == "compact"
    assert _resolve_reasoning_mode("full", default_cfg) == "full"

    full_cfg = dataclasses.replace(default_cfg, reasoning_mode="full")
    assert _resolve_reasoning_mode(None, full_cfg) == "full"
    assert _resolve_reasoning_mode("compact", full_cfg) == "compact"


def test_cli_rejects_an_unknown_reasoning_value():
    from sasha.__main__ import main

    with pytest.raises(SystemExit) as exc:
        main(["--reasoning", "loud"])
    assert exc.value.code == 2
