"""Contract tests — Task 12.1: readline delims + Executor.cwd threading
for path completion (ISSUE-029).

Written by Tier 1 at spec time; immutable to the implementer.
Spec: docs/superpowers/specs/task-12.1-completion-path-fixes.md
"""

import io
import pathlib

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
_DEFAULT_DELIMS = " \t\n`~!@#$%^&*()-=+[{]}\\|;:'\",<>/?"


# --- AC1: complete_via_bash respects cwd ------------------------------------

def test_ac1_cwd_changes_where_bash_looks(tmp_path):
    from sasha.completion import complete_via_bash

    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()

    assert complete_via_bash(["cat", "PLAN"], 1, cwd=str(empty_dir)) == []
    assert "PLAN.md" in complete_via_bash(["cat", "PLAN"], 1, cwd=str(REPO))


def test_ac1_default_cwd_none_preserves_prior_behavior():
    from sasha.completion import complete_via_bash

    # Called with no cwd override, behaves as Task 11.2 left it (relative
    # to the real process cwd, whatever that is for this test run).
    result = complete_via_bash(["git", "chec"], 1)
    assert "checkout" in result


# --- AC2: complete_filenames respects cwd -----------------------------------

def test_ac2_relative_prefix_resolves_against_cwd(tmp_path):
    from sasha.completion import complete_filenames

    (tmp_path / "foo.txt").write_text("x")
    (tmp_path / "foobar").mkdir()

    result = complete_filenames("foo", cwd=str(tmp_path))
    assert result == ["foo.txt", "foobar/"]


def test_ac2_absolute_prefix_unaffected_by_cwd(tmp_path):
    from sasha.completion import complete_filenames

    real_dir = tmp_path / "real"
    real_dir.mkdir()
    (real_dir / "target.txt").write_text("x")
    decoy_cwd = tmp_path / "decoy"
    decoy_cwd.mkdir()

    result = complete_filenames(str(real_dir / "target"), cwd=str(decoy_cwd))
    assert result == [str(real_dir / "target.txt")]


def test_ac2_cwd_none_behaves_like_task_11_2(tmp_path, monkeypatch):
    from sasha.completion import complete_filenames

    (tmp_path / "alpha.txt").write_text("x")
    monkeypatch.chdir(tmp_path)

    result = complete_filenames("alpha")
    assert result == ["alpha.txt"]


# --- AC3: readline delims narrowed during __main__ setup ---------------------

def test_ac3_main_narrows_completer_delims(monkeypatch, tmp_path):
    import readline

    from sasha.__main__ import main

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    readline.set_completer_delims(_DEFAULT_DELIMS)

    main([])

    assert "/" not in readline.get_completer_delims()


# --- AC4: words_and_cword composes correctly with narrowed delims -----------

def test_ac4_absolute_path_reconstructed_as_one_word():
    from sasha.completion import words_and_cword

    line = "cat /home/salvador/Sa"
    text = "/home/salvador/Sa"
    begidx = line.index(text)

    words, cword = words_and_cword(line, begidx, text)
    assert words == ["cat", "/home/salvador/Sa"]
    assert cword == 1


def test_ac4_relative_path_with_slash_reconstructed_as_one_word():
    from sasha.completion import words_and_cword

    line = "cat src/sa"
    text = "src/sa"
    begidx = line.index(text)

    words, cword = words_and_cword(line, begidx, text)
    assert words == ["cat", "src/sa"]
    assert cword == 1
