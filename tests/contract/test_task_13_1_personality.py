"""Contract tests — Task 13.1: personality file (ISSUE-030).

Written by Tier 1 at spec time; immutable to the implementer.
Spec: docs/superpowers/specs/task-13.1-personality.md
Design: docs/superpowers/specs/2026-07-16-personality-design.md
"""

import os
import stat

import pytest

from sasha.session import FUNCTIONAL_PROMPT


# --- AC1: self-install when absent -------------------------------------

def test_ac1_creates_file_with_default_template(tmp_path):
    from sasha.personality import DEFAULT_TEMPLATE, load_personality

    path = tmp_path / "sasha" / "PERSONALITY.md"
    result = load_personality(path)

    assert path.read_text() == DEFAULT_TEMPLATE
    assert result.text == DEFAULT_TEMPLATE
    assert result.notice is not None
    assert str(path) in result.notice


def test_ac1_creates_parent_directories(tmp_path):
    from sasha.personality import load_personality

    path = tmp_path / "does" / "not" / "exist" / "PERSONALITY.md"
    load_personality(path)

    assert path.exists()


# --- AC2: existing normal file --------------------------------------------

def test_ac2_reads_existing_file_notice_none(tmp_path):
    from sasha.personality import load_personality

    path = tmp_path / "PERSONALITY.md"
    path.write_text("Be terse and a little sarcastic.")

    result = load_personality(path)

    assert result.text == "Be terse and a little sarcastic."
    assert result.notice is None


def test_ac2_does_not_overwrite_existing_file(tmp_path):
    from sasha.personality import load_personality

    path = tmp_path / "PERSONALITY.md"
    path.write_text("My custom personality")

    load_personality(path)

    assert path.read_text() == "My custom personality"


# --- AC3: unreadable file falls back gracefully, no raise -----------------

def test_ac3_unreadable_file_falls_back(tmp_path):
    from sasha.personality import DEFAULT_TEMPLATE, load_personality

    path = tmp_path / "PERSONALITY.md"
    path.write_text("secret content")
    path.chmod(0o000)

    try:
        result = load_personality(path)
    finally:
        path.chmod(0o644)  # restore so pytest can clean up tmp_path

    assert result.text == DEFAULT_TEMPLATE
    assert result.notice is not None


def test_ac3_path_is_a_directory_falls_back(tmp_path):
    from sasha.personality import DEFAULT_TEMPLATE, load_personality

    path = tmp_path / "PERSONALITY.md"
    path.mkdir()

    result = load_personality(path)

    assert result.text == DEFAULT_TEMPLATE
    assert result.notice is not None


def test_ac3_create_notice_distinct_from_read_error_notice(tmp_path):
    from sasha.personality import load_personality

    created_path = tmp_path / "fresh" / "PERSONALITY.md"
    created_result = load_personality(created_path)

    error_path = tmp_path / "PERSONALITY.md"
    error_path.mkdir()
    error_result = load_personality(error_path)

    assert created_result.notice != error_result.notice


# --- AC4: oversized file is truncated -------------------------------------

def test_ac4_truncates_to_exactly_max_chars(tmp_path):
    from sasha.personality import load_personality

    path = tmp_path / "PERSONALITY.md"
    path.write_text("x" * 9000)

    result = load_personality(path)

    assert len(result.text) == 8000
    assert result.text == "x" * 8000
    assert result.notice is not None
    assert str(path) in result.notice


def test_ac4_exactly_max_chars_not_truncated_no_notice(tmp_path):
    from sasha.personality import load_personality

    path = tmp_path / "PERSONALITY.md"
    path.write_text("y" * 8000)

    result = load_personality(path)

    assert result.text == "y" * 8000
    assert result.notice is None


# --- AC5/AC6: Session integration -----------------------------------------

# Amended by Tier 1 (2026-10-12, ISSUE-075): this file duplicated the whole
# harness instruction block inline. It now references the single source, so
# the question-tool wording lives in one place; the assertion (no personality
# means the functional block alone) is unchanged.
def test_ac5_default_personality_matches_prior_system_prompt():
    # Amended by Tier 1 for Task 57.1 (question tool): _build_system_prompt()'s
    # functional block gained a trailing question-tool-guidance paragraph.
    # See docs/superpowers/specs/task-57.1-question-core.md.
    from sasha.session import Session

    s = Session()

    assert s.system_prompt == FUNCTIONAL_PROMPT


def test_ac6_custom_personality_prepended():
    # Amended by Tier 1 for Task 57.1 (question tool): see above.
    from sasha.session import Session

    s = Session(personality="Custom text")

    assert s.system_prompt.startswith("Custom text\n\n")
    assert s.system_prompt == "Custom text\n\n" + FUNCTIONAL_PROMPT


def test_ac6_empty_personality_string_same_as_default():
    from sasha.session import Session

    default = Session()
    explicit_empty = Session(personality="")

    assert default.system_prompt == explicit_empty.system_prompt
