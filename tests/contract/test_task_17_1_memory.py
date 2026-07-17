"""Contract tests — Task 17.1: sasha/memory.py + Executor.last_command/last_result.

Written by Tier 1 at spec time; immutable to the implementer.
Spec: docs/superpowers/specs/task-17.1-memory-module.md
Design: docs/superpowers/specs/2026-07-17-project-memory-design.md
"""

import json
import subprocess
import threading

import pytest


def _git(repo, *args):
    subprocess.run(["git", *args], cwd=str(repo), check=True,
                    capture_output=True)


def _init_repo(repo):
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")


# --- AC: resolve_git_root ------------------------------------------------------

def test_ac_resolve_git_root_finds_repo(tmp_path):
    from sasha.memory import resolve_git_root
    repo = tmp_path / "repo"
    _init_repo(repo)
    root = resolve_git_root(str(repo))
    assert root.resolve() == repo.resolve()


def test_ac_resolve_git_root_from_subdirectory(tmp_path):
    from sasha.memory import resolve_git_root
    repo = tmp_path / "repo"
    _init_repo(repo)
    sub = repo / "a" / "b"
    sub.mkdir(parents=True)
    root = resolve_git_root(str(sub))
    assert root.resolve() == repo.resolve()


def test_ac_resolve_git_root_none_outside_repo(tmp_path):
    from sasha.memory import resolve_git_root
    plain = tmp_path / "not-a-repo"
    plain.mkdir()
    assert resolve_git_root(str(plain)) is None


# --- AC: ensure_memory_dir — lazy + self-protecting -----------------------------

def test_ac_ensure_memory_dir_creates_gitignore_before_content(tmp_path):
    from sasha.memory import ensure_memory_dir
    repo = tmp_path / "repo"
    _init_repo(repo)
    memory_dir, just_created = ensure_memory_dir(repo)
    assert just_created is True
    assert memory_dir == repo / ".sasha" / "memory"
    assert memory_dir.is_dir()
    assert (memory_dir / "episodic").is_dir()
    gitignore = repo / ".sasha" / ".gitignore"
    assert gitignore.exists()
    assert gitignore.read_text() == "*\n"


def test_ac_ensure_memory_dir_second_call_not_just_created(tmp_path):
    from sasha.memory import ensure_memory_dir
    repo = tmp_path / "repo"
    _init_repo(repo)
    ensure_memory_dir(repo)
    _, just_created = ensure_memory_dir(repo)
    assert just_created is False


def test_ac_nothing_created_until_first_write(tmp_path):
    # A repo nobody has ever encoded anything into has no .sasha/ footprint.
    repo = tmp_path / "repo"
    _init_repo(repo)
    assert not (repo / ".sasha").exists()


# --- AC: encode_commit -----------------------------------------------------------

def test_ac_encode_commit_appends_entry(tmp_path):
    from sasha.memory import encode_commit
    repo = tmp_path / "repo"
    _init_repo(repo)
    just_created = encode_commit(repo, "Track Task 16.2", "abc123")
    assert just_created is True
    lines = (repo / ".sasha" / "memory" / "semantic.jsonl").read_text().splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["kind"] == "commit"
    assert entry["text"] == "Track Task 16.2"
    assert entry["pointer"] == "abc123"
    assert "timestamp" in entry


def test_ac_encode_commit_multiple_appends(tmp_path):
    from sasha.memory import encode_commit
    repo = tmp_path / "repo"
    _init_repo(repo)
    encode_commit(repo, "first", "h1")
    just_created = encode_commit(repo, "second", "h2")
    assert just_created is False
    lines = (repo / ".sasha" / "memory" / "semantic.jsonl").read_text().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[1])["text"] == "second"


# --- AC: encode_remember ----------------------------------------------------------

def test_ac_encode_remember_without_last_result_has_null_pointer(tmp_path):
    from sasha.memory import encode_remember
    repo = tmp_path / "repo"
    _init_repo(repo)
    encode_remember(repo, "a general fact worth keeping", None, None)
    lines = (repo / ".sasha" / "memory" / "semantic.jsonl").read_text().splitlines()
    entry = json.loads(lines[0])
    assert entry["kind"] == "remember"
    assert entry["text"] == "a general fact worth keeping"
    assert entry["pointer"] is None


def test_ac_encode_remember_with_last_result_writes_episodic_log(tmp_path):
    from sasha.executor import ExecResult
    from sasha.memory import encode_remember
    repo = tmp_path / "repo"
    _init_repo(repo)
    result = ExecResult(exit_code=1, signal=None, stderr_text="boom",
                         stdout_text="partial output", cwd_after=str(repo),
                         duration_ms=340)
    encode_remember(repo, "the flaky test was a race condition",
                     "pytest -k search_files", result)
    lines = (repo / ".sasha" / "memory" / "semantic.jsonl").read_text().splitlines()
    entry = json.loads(lines[0])
    assert entry["pointer"] is not None
    log_path = repo / entry["pointer"]
    assert log_path.exists()
    content = log_path.read_text()
    assert "pytest -k search_files" in content
    assert "exit code: 1" in content
    assert "340ms" in content
    assert "partial output" in content
    assert "boom" in content


def test_ac_encode_remember_episodic_log_relative_pointer(tmp_path):
    from sasha.executor import ExecResult
    from sasha.memory import encode_remember
    repo = tmp_path / "repo"
    _init_repo(repo)
    result = ExecResult(exit_code=0, signal=None, stderr_text="",
                         stdout_text="ok", cwd_after=str(repo), duration_ms=1)
    encode_remember(repo, "note", "echo ok", result)
    lines = (repo / ".sasha" / "memory" / "semantic.jsonl").read_text().splitlines()
    pointer = json.loads(lines[0])["pointer"]
    assert pointer.startswith(".sasha/memory/episodic/")
    assert not pointer.startswith("/")


# --- AC: load_memory_block ---------------------------------------------------------

def test_ac_load_memory_block_missing_file(tmp_path):
    from sasha.memory import load_memory_block
    repo = tmp_path / "repo"
    _init_repo(repo)
    text, count = load_memory_block(repo)
    assert text == ""
    assert count == 0


def test_ac_load_memory_block_renders_entries_chronologically(tmp_path):
    from sasha.memory import encode_commit, load_memory_block
    repo = tmp_path / "repo"
    _init_repo(repo)
    encode_commit(repo, "first change", "h1")
    encode_commit(repo, "second change", "h2")
    text, count = load_memory_block(repo)
    assert count == 2
    lines = text.split("\n")
    assert len(lines) == 2
    assert "first change" in lines[0]
    assert "second change" in lines[1]


def test_ac_load_memory_block_skips_malformed_line(tmp_path):
    from sasha.memory import encode_commit, load_memory_block
    repo = tmp_path / "repo"
    _init_repo(repo)
    encode_commit(repo, "good entry", "h1")
    semantic = repo / ".sasha" / "memory" / "semantic.jsonl"
    with open(semantic, "a") as f:
        f.write("{not valid json\n")
    encode_commit(repo, "another good entry", "h2")
    text, count = load_memory_block(repo)
    assert count == 2
    assert "good entry" in text
    assert "another good entry" in text


def test_ac_load_memory_block_caps_by_dropping_oldest(tmp_path):
    from sasha.memory import encode_commit, load_memory_block
    repo = tmp_path / "repo"
    _init_repo(repo)
    for i in range(20):
        encode_commit(repo, f"change number {i} " + ("x" * 50), f"h{i}")
    text, count = load_memory_block(repo, max_chars=300)
    assert count == 20
    assert "change number 19" in text          # most recent always kept
    assert "change number 0 " not in text      # oldest dropped
    assert len(text) <= 300 + 100               # one entry may slightly overflow


def test_ac_load_memory_block_always_keeps_at_least_one_entry(tmp_path):
    from sasha.memory import encode_commit, load_memory_block
    repo = tmp_path / "repo"
    _init_repo(repo)
    encode_commit(repo, "x" * 5000, "h1")
    text, count = load_memory_block(repo, max_chars=10)
    assert count == 1
    assert "x" * 5000 in text


# --- AC: most_recent_gist ----------------------------------------------------------

def test_ac_most_recent_gist_missing_file(tmp_path):
    from sasha.memory import most_recent_gist
    repo = tmp_path / "repo"
    _init_repo(repo)
    assert most_recent_gist(repo) is None


def test_ac_most_recent_gist_returns_latest_text(tmp_path):
    from sasha.memory import encode_commit, most_recent_gist
    repo = tmp_path / "repo"
    _init_repo(repo)
    encode_commit(repo, "older", "h1")
    encode_commit(repo, "newest", "h2")
    assert most_recent_gist(repo) == "newest"


# --- AC: concurrent appends are safe -----------------------------------------------

def test_ac_concurrent_appends_produce_no_torn_lines(tmp_path):
    from sasha.memory import encode_remember, load_memory_block
    repo = tmp_path / "repo"
    _init_repo(repo)

    def _writer(n):
        for i in range(20):
            encode_remember(repo, f"writer {n} entry {i}", None, None)

    threads = [threading.Thread(target=_writer, args=(n,)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    semantic = repo / ".sasha" / "memory" / "semantic.jsonl"
    lines = semantic.read_text().splitlines()
    assert len(lines) == 80
    for line in lines:
        json.loads(line)  # every single line must be independently parseable
    _, count = load_memory_block(repo, max_chars=1_000_000)
    assert count == 80


# --- AC: Executor.last_command / last_result --------------------------------------

def test_ac_executor_last_result_initially_none():
    from sasha.executor import Executor
    executor = Executor(cwd="/tmp", env={})
    assert executor.last_command is None
    assert executor.last_result is None


def test_ac_executor_tracks_last_command_and_result():
    from sasha.executor import Executor
    executor = Executor(cwd="/tmp", env={})
    result = executor.run("echo hello", capture_stdout=True)
    assert executor.last_command == "echo hello"
    assert executor.last_result is result
    assert executor.last_result.exit_code == 0
