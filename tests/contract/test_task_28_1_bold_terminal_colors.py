"""Contract tests — Task 28.1: bold TerminalSink colors (ISSUE-046).

Written by Tier 1 at spec time; immutable to the implementer.
Spec: docs/superpowers/specs/task-28.1-bold-terminal-colors.md
Design: docs/superpowers/specs/2026-07-21-issue-046-bold-terminal-colors-design.md
"""

from __future__ import annotations

from sasha.repl import (
    _COMMAND_COLOR,
    _DIM,
    _LLM_COLOR,
    _PROMPT_COLOR,
    _RESET,
    _SASHA_COLOR,
)


def test_ac1_sasha_color_is_bold_cyan():
    assert _SASHA_COLOR == "\x1b[1;36m"


def test_ac2_llm_color_is_bold_magenta():
    assert _LLM_COLOR == "\x1b[1;35m"


def test_ac3_prompt_color_is_bold_green():
    assert _PROMPT_COLOR == "\x1b[1;32m"


def test_ac4_command_color_is_bold_yellow():
    assert _COMMAND_COLOR == "\x1b[1;33m"


def test_ac5_dim_is_unchanged_and_not_bold():
    # ISSUE-046's explicit non-goal: bold (SGR 1) and faint (SGR 2) are
    # contradictory attributes, so _DIM stays exactly as it was.
    assert _DIM == "\x1b[2m"
    assert "1;" not in _DIM


def test_ac6_reset_is_unchanged():
    assert _RESET == "\x1b[0m"
