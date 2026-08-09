"""Contract tests — Task 60.1: bash-completion default-delegation
fallback (ISSUE-058, root-caused).

Written by Tier 1 at spec time; immutable to the implementer.
Spec: docs/superpowers/specs/task-60.1-bash-completion-default-fallback.md

AC1/AC4 drive the real bash-completion installation on this system (same
approach as Task 11.2's own tests) since the point is this works against
the real ecosystem. AC2/AC3 mock the subprocess boundary directly so the
sentinel-parsing contract is pinned regardless of which bash-completion
version is installed -- the whole reason ISSUE-058 sat undiagnosed is
that this behavior is version-sensitive.
"""

import pathlib

REPO = pathlib.Path(__file__).resolve().parents[2]


class _FakeCompletedProcess:
    def __init__(self, stdout):
        self.stdout = stdout


# --- AC1: real bash-completion catch-all fallback ----------------------------

def test_ac1_nano_gets_filename_completion_via_real_bash_completion():
    from sasha.completion import complete_via_bash

    result = complete_via_bash(["nano", "PLAN"], 1, cwd=str(REPO))
    assert "PLAN.md" in result


# --- AC2: sentinel from bash triggers the filename fallback (isolated) -------

def test_ac2_default_sentinel_triggers_filename_fallback(monkeypatch, tmp_path):
    from sasha import completion

    (tmp_path / "report.txt").write_text("x")

    def _fake_run(*args, **kwargs):
        return _FakeCompletedProcess(completion._DEFAULT_COMPLETION_SENTINEL + "\n")

    monkeypatch.setattr(completion.subprocess, "run", _fake_run)
    result = completion.complete_via_bash(["nano", "rep"], 1, cwd=str(tmp_path))
    assert result == ["report.txt"]


# --- AC3: no sentinel, no completions -> stays empty (no blanket fallback) ---

def test_ac3_no_sentinel_no_fallback_stays_empty(monkeypatch, tmp_path):
    from sasha import completion

    (tmp_path / "report.txt").write_text("x")

    def _fake_run(*args, **kwargs):
        return _FakeCompletedProcess("")

    monkeypatch.setattr(completion.subprocess, "run", _fake_run)
    result = completion.complete_via_bash(["git", "checkout", "zzz"], 2, cwd=str(tmp_path))
    assert result == []


# --- AC4: real completers (git) are unaffected, sentinel never leaks --------

def test_ac4_real_git_completer_unaffected_and_sentinel_never_leaks():
    from sasha.completion import complete_via_bash, _DEFAULT_COMPLETION_SENTINEL

    result = complete_via_bash(["git", "chec"], 1)
    assert "checkout" in result
    assert _DEFAULT_COMPLETION_SENTINEL not in result
