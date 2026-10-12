"""Contract tests — Task 19.1: memory.py bounded_history + the write lock.

Written by Tier 1 at spec time; immutable to the implementer.
Spec: docs/superpowers/specs/task-19.1-memory-bootstrap-core.md
Design: docs/superpowers/specs/2026-07-18-memory-bootstrap-design.md

AMENDED BY TIER 1 (2026-10-12) for the audit's §4.3: the LLM half of the
memory bootstrap was deleted, so `root_docs()` (which existed only to feed the
summariser's prompt) and `encode_bootstrap()` (which wrote `kind="bootstrap"`
entries that were then injected into every later prompt as fact) are gone with
it. The AC5-AC8 root_docs section and the AC9 encode_bootstrap section are
removed outright, replaced by an assertion that both stay removed. What remains
is `bounded_history()` — still the source of the mechanical replay — and the
same-process write lock, whose no-torn-lines guarantee is now exercised with the
writers that still exist.
"""

import json
import subprocess
import threading


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
    _commit(repo, "a.txt", "1", "first\n\nwith a body")
    _commit(repo, "b.txt", "2", "second")

    history = bounded_history(repo)
    assert [msg for _, msg in history] == ["second", "first\n\nwith a body"]
    assert all(len(h) == 40 for h, _ in history)   # full sha1


def test_ac2_bounded_history_keeps_first_even_over_budget(tmp_path):
    from sasha.memory import bounded_history
    repo = tmp_path / "repo"
    _init_repo(repo)
    _commit(repo, "a.txt", "1", "x" * 500)
    _commit(repo, "b.txt", "2", "y" * 500)

    history = bounded_history(repo, max_chars=10)
    assert len(history) == 1                       # newest always included
    assert history[0][1] == "y" * 500


def test_ac3_bounded_history_stops_at_budget(tmp_path):
    from sasha.memory import bounded_history
    repo = tmp_path / "repo"
    _init_repo(repo)
    for i in range(5):
        _commit(repo, "f%d.txt" % i, str(i), "message %d" % i)

    history = bounded_history(repo, max_chars=40)
    assert 1 <= len(history) < 5
    assert history[0][1] == "message 4"            # newest first


def test_ac4_bounded_history_no_commits_returns_empty(tmp_path):
    from sasha.memory import bounded_history
    repo = tmp_path / "repo"
    _init_repo(repo)
    assert bounded_history(repo) == []


def test_ac4_bounded_history_outside_a_repo_returns_empty(tmp_path):
    from sasha.memory import bounded_history
    plain = tmp_path / "plain"
    plain.mkdir()
    assert bounded_history(plain) == []


# --- AC5: the removed helpers stay removed ------------------------------------

def test_ac5_root_docs_and_encode_bootstrap_are_gone():
    import sasha.memory as memory

    assert not hasattr(memory, "root_docs")
    assert not hasattr(memory, "encode_bootstrap")


# --- AC6: same-process concurrent writers --------------------------------------

def test_ac6_concurrent_writers_produce_no_torn_lines(tmp_path):
    """Every line must be independently parseable, whichever thread wrote it.

    Deliberately uses only PROJECT-memory writers (repo/.sasha/memory). General
    memory resolves to the real ~/.config/sasha/memory, which these tests do not
    isolate, so involving it here would append to the developer's actual memory
    file -- a side effect a test must never have.
    """
    from sasha.memory import encode_commit, load_memory_block
    repo = tmp_path / "repo"
    _init_repo(repo)

    def _commit_writer(n):
        for i in range(15):
            encode_commit(repo, "commit thread %d message %d" % (n, i),
                          "h%d-%d" % (n, i))

    threads = [threading.Thread(target=_commit_writer, args=(n,))
               for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    semantic = repo / ".sasha" / "memory" / "semantic.jsonl"
    lines = semantic.read_text().splitlines()
    assert len(lines) == 60            # four writers, 15 each
    for line in lines:
        json.loads(line)               # no torn lines
    _, count = load_memory_block(repo, max_chars=1_000_000)
    assert count == 60
