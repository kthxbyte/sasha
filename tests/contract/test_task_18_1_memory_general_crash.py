"""Contract tests — Task 18.1: memory.py's private _at layer,
general_* functions, and the crash-signal encode pair.

Written by Tier 1 at spec time; immutable to the implementer.
Spec: docs/superpowers/specs/task-18.1-memory-general-crash.md
Design: docs/superpowers/specs/2026-07-17-general-memory-design.md

Phase 17's existing tests/contract/test_task_17_1_memory.py is NOT
modified by this task and is re-run unmodified as part of the full
suite — its continued green-ness is the proof that the refactor into a
private _at layer changed no public behavior. This file covers only
what's new: general_memory_root(), the general_* functions, and the
crash pair.
"""

import json
import subprocess

import pytest


def _git(repo, *args):
    subprocess.run(["git", *args], cwd=str(repo), check=True,
                    capture_output=True)


def _init_repo(repo):
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")


# --- AC: general_memory_root() ----------------------------------------------

def test_ac_general_memory_root_respects_xdg(monkeypatch, tmp_path):
    from sasha.memory import general_memory_root
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    assert general_memory_root() == tmp_path / "xdg" / "sasha" / "memory"


def test_ac_general_memory_root_falls_back_to_home(monkeypatch, tmp_path):
    from sasha.memory import general_memory_root
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    assert general_memory_root() == tmp_path / ".config" / "sasha" / "memory"


# --- AC: encode_general_remember --------------------------------------------

def test_ac_encode_general_remember_no_gitignore_written(monkeypatch, tmp_path):
    from sasha.memory import encode_general_remember
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    encode_general_remember("a general fact", None, None)
    general_dir = tmp_path / "xdg" / "sasha" / "memory"
    assert (general_dir / "semantic.jsonl").exists()
    assert not (general_dir.parent / ".gitignore").exists()


def test_ac_encode_general_remember_entry_shape(monkeypatch, tmp_path):
    from sasha.memory import encode_general_remember
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    encode_general_remember("prefers concise commit messages", None, None)
    path = tmp_path / "xdg" / "sasha" / "memory" / "semantic.jsonl"
    entry = json.loads(path.read_text().splitlines()[0])
    assert entry["kind"] == "remember"
    assert entry["text"] == "prefers concise commit messages"
    assert entry["pointer"] is None


def test_ac_encode_general_remember_episodic_pointer_relative_to_general_root(
        monkeypatch, tmp_path):
    from sasha.executor import ExecResult
    from sasha.memory import encode_general_remember
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    result = ExecResult(exit_code=1, signal=None, stderr_text="err",
                         stdout_text="out", cwd_after="/x", duration_ms=1)
    encode_general_remember("noted", "some-command", result)
    general_dir = tmp_path / "xdg" / "sasha" / "memory"
    entry = json.loads((general_dir / "semantic.jsonl").read_text().splitlines()[0])
    assert entry["pointer"].startswith("episodic/")
    assert (general_dir / entry["pointer"]).read_text().count("some-command") == 1


# --- AC: load_general_memory_block ------------------------------------------

def test_ac_load_general_memory_block_empty(monkeypatch, tmp_path):
    from sasha.memory import load_general_memory_block
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    text, count = load_general_memory_block()
    assert text == ""
    assert count == 0


def test_ac_load_general_memory_block_after_writes(monkeypatch, tmp_path):
    from sasha.memory import encode_general_remember, load_general_memory_block
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    encode_general_remember("fact one", None, None)
    encode_general_remember("fact two", None, None)
    text, count = load_general_memory_block()
    assert count == 2
    assert "fact one" in text
    assert "fact two" in text


# --- AC: is_crash_signal -----------------------------------------------------

def test_ac_is_crash_signal_true_for_real_crashes():
    import signal as signal_module
    from sasha.memory import is_crash_signal
    for sig in (signal_module.SIGILL, signal_module.SIGABRT,
                signal_module.SIGFPE, signal_module.SIGSEGV,
                signal_module.SIGBUS):
        assert is_crash_signal(sig) is True


def test_ac_is_crash_signal_false_for_sigkill_and_none():
    import signal as signal_module
    from sasha.memory import is_crash_signal
    assert is_crash_signal(signal_module.SIGKILL) is False
    assert is_crash_signal(None) is False
    assert is_crash_signal(signal_module.SIGTERM) is False


# --- AC: encode_crash (project-scoped) ---------------------------------------

def test_ac_encode_crash_writes_semantic_and_episodic(tmp_path):
    import signal as signal_module
    from sasha.executor import ExecResult
    from sasha.memory import encode_crash
    repo = tmp_path / "repo"
    _init_repo(repo)
    result = ExecResult(exit_code=139, signal=signal_module.SIGSEGV,
                         stderr_text="Segmentation fault", stdout_text="",
                         cwd_after=str(repo), duration_ms=12)
    encode_crash(repo, "pytest -k boom", result)
    semantic = repo / ".sasha" / "memory" / "semantic.jsonl"
    entry = json.loads(semantic.read_text().splitlines()[0])
    assert entry["kind"] == "crash"
    assert entry["text"] == "pytest -k boom crashed with SIGSEGV (exit 139)"
    assert entry["pointer"] == ".sasha/memory/episodic/" + entry["pointer"].split("/")[-1]
    log = (repo / entry["pointer"]).read_text()
    assert "pytest -k boom" in log
    assert "Segmentation fault" in log


def test_ac_encode_crash_pointer_relative_to_repo_root_matches_remember(tmp_path):
    # Same convention as encode_remember's existing (Phase 17) pointers —
    # relative to the repo root, not the memory root.
    import signal as signal_module
    from sasha.executor import ExecResult
    from sasha.memory import encode_crash, encode_remember
    repo = tmp_path / "repo"
    _init_repo(repo)
    result = ExecResult(exit_code=139, signal=signal_module.SIGSEGV,
                         stderr_text="", stdout_text="", cwd_after=str(repo),
                         duration_ms=1)
    encode_remember(repo, "note", "cmd", result)
    encode_crash(repo, "cmd", result)
    semantic = repo / ".sasha" / "memory" / "semantic.jsonl"
    entries = [json.loads(l) for l in semantic.read_text().splitlines()]
    assert entries[0]["pointer"].startswith(".sasha/memory/episodic/")
    assert entries[1]["pointer"].startswith(".sasha/memory/episodic/")


# --- AC: encode_general_crash -------------------------------------------------

def test_ac_encode_general_crash_writes_to_general_root(monkeypatch, tmp_path):
    import signal as signal_module
    from sasha.executor import ExecResult
    from sasha.memory import encode_general_crash
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    result = ExecResult(exit_code=134, signal=signal_module.SIGABRT,
                         stderr_text="abort", stdout_text="", cwd_after="/x",
                         duration_ms=3)
    encode_general_crash("some-tool", result)
    general_dir = tmp_path / "xdg" / "sasha" / "memory"
    entry = json.loads((general_dir / "semantic.jsonl").read_text().splitlines()[0])
    assert entry["kind"] == "crash"
    assert entry["text"] == "some-tool crashed with SIGABRT (exit 134)"
    assert entry["pointer"].startswith("episodic/")
    assert not (general_dir.parent / ".gitignore").exists()


# --- AC: project-scoped wrappers are unmodified (spot-check; full proof is
# test_task_17_1_memory.py staying green as part of the full suite) ---------

def test_ac_existing_project_functions_still_gitignore_protected(tmp_path):
    from sasha.memory import encode_commit
    repo = tmp_path / "repo"
    _init_repo(repo)
    encode_commit(repo, "a change", "h1")
    gitignore = repo / ".sasha" / ".gitignore"
    assert gitignore.exists()
    assert gitignore.read_text() == "*\n"
