"""Contract tests — Task 11.2: bash-completion subprocess integration
(ISSUE-028).

Written by Tier 1 at spec time; immutable to the implementer.
Spec: docs/superpowers/specs/task-11.2-completion-bash-integration.md
Design: docs/superpowers/specs/2026-07-16-tab-completion-design.md

AC1 tests drive the real bash-completion installation on this system
(confirmed present at spec time — see the design doc's §5.1) rather than
mocking it, since the whole point of this task is that it works
end-to-end against the real ecosystem.
"""

import pathlib
import subprocess

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]


# --- AC1: real bash-completion invocations ----------------------------------

def test_ac1_git_subcommand_completion():
    from sasha.completion import complete_via_bash

    result = complete_via_bash(["git", "chec"], 1)
    assert "checkout" in result


def test_ac1_git_branch_completion(monkeypatch):
    from sasha.completion import complete_via_bash

    monkeypatch.chdir(REPO)
    result = complete_via_bash(["git", "checkout", "ma"], 2)
    assert "main" in result


def test_ac1_filename_completion_via_minimal_fallback(monkeypatch):
    from sasha.completion import complete_via_bash

    monkeypatch.chdir(REPO)
    result = complete_via_bash(["cat", "PLAN"], 1)
    assert "PLAN.md" in result


# --- AC2: never raises, empty list on timeout/error --------------------------

def test_ac2_timeout_returns_empty_list(monkeypatch):
    from sasha import completion

    def _raise_timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="bash", timeout=0.5)

    monkeypatch.setattr(completion.subprocess, "run", _raise_timeout)
    assert completion.complete_via_bash(["git", "chec"], 1) == []


def test_ac2_oserror_returns_empty_list(monkeypatch):
    from sasha import completion

    def _raise_oserror(*args, **kwargs):
        raise OSError("bash not found")

    monkeypatch.setattr(completion.subprocess, "run", _raise_oserror)
    assert completion.complete_via_bash(["git", "chec"], 1) == []


# --- AC3: bash_completion_available() ---------------------------------------

def test_ac3_available_when_both_present(monkeypatch):
    from sasha import completion

    monkeypatch.setattr(completion.shutil, "which", lambda name: "/bin/bash")
    monkeypatch.setattr(completion.pathlib.Path, "exists", lambda self: True)
    assert completion.bash_completion_available() is True


def test_ac3_unavailable_when_bash_missing(monkeypatch):
    from sasha import completion

    monkeypatch.setattr(completion.shutil, "which", lambda name: None)
    monkeypatch.setattr(completion.pathlib.Path, "exists", lambda self: True)
    assert completion.bash_completion_available() is False


def test_ac3_unavailable_when_script_missing(monkeypatch):
    from sasha import completion

    monkeypatch.setattr(completion.shutil, "which", lambda name: "/bin/bash")
    monkeypatch.setattr(completion.pathlib.Path, "exists", lambda self: False)
    assert completion.bash_completion_available() is False


# --- AC4: complete_filenames() -----------------------------------------------

def test_ac4_matches_files_and_dirs(tmp_path, monkeypatch):
    from sasha.completion import complete_filenames

    (tmp_path / "foo.txt").write_text("x")
    (tmp_path / "foobar").mkdir()
    monkeypatch.chdir(tmp_path)

    result = complete_filenames("foo")
    assert result == ["foo.txt", "foobar/"]


def test_ac4_no_matches_returns_empty_list(tmp_path, monkeypatch):
    from sasha.completion import complete_filenames

    monkeypatch.chdir(tmp_path)
    assert complete_filenames("nonexistent") == []
