"""Contract tests — Task 19.1: memory.py bounded_history, root_docs,
encode_bootstrap, write lock.

Written by Tier 1 at spec time; immutable to the implementer.
Spec: docs/superpowers/specs/task-19.1-memory-bootstrap-core.md
Design: docs/superpowers/specs/2026-07-18-memory-bootstrap-design.md
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


def _commit(repo, filename, content, message):
    (repo / filename).write_text(content)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)


# --- AC1-4: bounded_history --------------------------------------------------

def test_ac1_bounded_history_newest_first_multiline_roundtrip(tmp_path):
    from sasha.memory import bounded_history
    repo = tmp_path / "repo"
    _init_repo(repo)
    _commit(repo, "a.txt", "1", "first commit")
    _commit(repo, "b.txt", "2", "second commit\n\nwith a body paragraph")
    _commit(repo, "c.txt", "3", "third commit")
    result = bounded_history(repo)
    assert len(result) == 3
    hashes = [h for h, _ in result]
    messages = [m for _, m in result]
    assert messages[0] == "third commit"
    assert "second commit" in messages[1]
    assert "with a body paragraph" in messages[1]
    assert messages[2] == "first commit"
    assert len(set(hashes)) == 3
    assert all(len(h) == 40 for h in hashes)


def test_ac2_bounded_history_keeps_first_even_over_budget(tmp_path):
    from sasha.memory import bounded_history
    repo = tmp_path / "repo"
    _init_repo(repo)
    _commit(repo, "a.txt", "1", "x" * 5000)
    result = bounded_history(repo, max_chars=10)
    assert len(result) == 1
    assert "x" * 5000 in result[0][1]


def test_ac3_bounded_history_stops_at_budget(tmp_path):
    from sasha.memory import bounded_history
    repo = tmp_path / "repo"
    _init_repo(repo)
    for i in range(20):
        _commit(repo, f"f{i}.txt", str(i), f"commit number {i} " + ("x" * 50))
    result = bounded_history(repo, max_chars=300)
    assert 1 <= len(result) < 20
    messages = [m for _, m in result]
    assert any("commit number 19" in m for m in messages)  # newest kept
    assert not any("commit number 0 " in m for m in messages)  # oldest dropped


def test_ac4_bounded_history_no_commits_returns_empty(tmp_path):
    from sasha.memory import bounded_history
    repo = tmp_path / "repo"
    _init_repo(repo)
    result = bounded_history(repo)
    assert result == []
    assert not (repo / ".sasha").exists()


# --- AC5-8: root_docs ---------------------------------------------------------

def test_ac5_root_docs_case_insensitive_extensions_non_recursive(tmp_path):
    from sasha.memory import root_docs
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "README.MD").write_text("readme content")
    (repo / "notes.TXT").write_text("notes content")
    (repo / "page.htm").write_text("<p>html</p>")
    (repo / "ignored.py").write_text("print('nope')")
    sub = repo / "docs"
    sub.mkdir()
    (sub / "nested.md").write_text("should not be found")
    result = root_docs(repo)
    names = {name for name, _ in result}
    assert names == {"README.MD", "notes.TXT", "page.htm"}


def test_ac6_root_docs_sorted_newest_mtime_first(tmp_path):
    import os
    import time
    from sasha.memory import root_docs
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "old.md").write_text("old")
    time.sleep(0.05)
    (repo / "new.md").write_text("new")
    now = time.time()
    os.utime(repo / "old.md", (now - 100, now - 100))
    os.utime(repo / "new.md", (now, now))
    result = root_docs(repo)
    assert [name for name, _ in result] == ["new.md", "old.md"]


def test_ac7_root_docs_keeps_first_then_caps(tmp_path):
    import os
    import time
    from sasha.memory import root_docs
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "big.md").write_text("x" * 5000)
    (repo / "small.md").write_text("y" * 100)
    now = time.time()
    os.utime(repo / "big.md", (now, now))
    os.utime(repo / "small.md", (now - 100, now - 100))
    result = root_docs(repo, max_chars=10)
    assert len(result) == 1
    assert result[0][0] == "big.md"


def test_ac8_root_docs_no_matches_returns_empty(tmp_path):
    from sasha.memory import root_docs
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "ignored.py").write_text("nope")
    assert root_docs(repo) == []


# --- AC9: encode_bootstrap ------------------------------------------------------

def test_ac9_encode_bootstrap_appends_entry(tmp_path):
    from sasha.memory import encode_bootstrap
    repo = tmp_path / "repo"
    _init_repo(repo)
    just_created = encode_bootstrap(repo, "Sasha is a bash-wrapping REPL")
    assert just_created is True
    lines = (repo / ".sasha" / "memory" / "semantic.jsonl").read_text().splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["kind"] == "bootstrap"
    assert entry["text"] == "Sasha is a bash-wrapping REPL"
    assert entry["pointer"] is None
    assert "timestamp" in entry


def test_ac9_encode_bootstrap_multiple_appends_in_order(tmp_path):
    from sasha.memory import encode_bootstrap
    repo = tmp_path / "repo"
    _init_repo(repo)
    encode_bootstrap(repo, "first fact")
    just_created = encode_bootstrap(repo, "second fact")
    assert just_created is False
    lines = (repo / ".sasha" / "memory" / "semantic.jsonl").read_text().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["text"] == "first fact"
    assert json.loads(lines[1])["text"] == "second fact"


# --- AC10: same-process concurrent writers --------------------------------------

def test_ac10_concurrent_bootstrap_and_commit_writers_produce_no_torn_lines(tmp_path):
    from sasha.memory import encode_bootstrap, encode_commit, load_memory_block
    repo = tmp_path / "repo"
    _init_repo(repo)

    def _bootstrap_writer(n):
        for i in range(15):
            encode_bootstrap(repo, f"bootstrap thread {n} fact {i}")

    def _commit_writer(n):
        for i in range(15):
            encode_commit(repo, f"commit thread {n} message {i}", f"h{n}-{i}")

    threads = ([threading.Thread(target=_bootstrap_writer, args=(n,))
                for n in range(2)]
               + [threading.Thread(target=_commit_writer, args=(n,))
                  for n in range(2)])
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    semantic = repo / ".sasha" / "memory" / "semantic.jsonl"
    lines = semantic.read_text().splitlines()
    assert len(lines) == 60
    for line in lines:
        json.loads(line)  # every single line must be independently parseable
    _, count = load_memory_block(repo, max_chars=1_000_000)
    assert count == 60
