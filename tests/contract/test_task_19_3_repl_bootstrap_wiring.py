"""Contract tests — Task 19.3: repl.py memory-bootstrap wiring.

Written by Tier 1 at spec time; immutable to the implementer.
Spec: docs/superpowers/specs/task-19.3-repl-bootstrap-wiring.md
Design: docs/superpowers/specs/2026-07-18-memory-bootstrap-design.md

AMENDED BY TIER 1 (2026-10-12) for the audit's §4.3: the LLM half of the
memory bootstrap is GONE. Stage 2 asked the model to summarise the repository's
history and root docs, wrote its answer into semantic memory as
`kind="bootstrap"` entries, and injected those into every later prompt as fact.
A 26B's summary of git history is not authoritative, a hallucination there is
durable and indistinguishable from something the user said, and ISSUE-039
recorded that there was no refresh path short of `sasha forget`.

What remains is stage 1 only: a free, mechanical replay of recent commit
messages into project memory, regenerated from git whenever memory is absent.
So this file now pins the replay, the "untouched" case, and the ABSENCE of
everything that was removed — `.bootstrap_pending`, `kind="bootstrap"`
entries, the background thread, the prompt builder and the response parser —
so a regression that reintroduces them fails loudly rather than silently
passing.
"""

import json
import pathlib
import subprocess
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from sse_stub import StubServer, refused_base_url  # noqa: E402,F401


def _git(repo, *args):
    subprocess.run(["git", *args], cwd=str(repo), check=True,
                    capture_output=True)


def _init_repo(repo):
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")


def _commit(repo, message):
    (repo / "f.txt").write_text(message)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)


class FakeExecutor:
    def __init__(self, cwd):
        from sasha.executor import ExecResult
        self._cwd = cwd
        self._last_command = None
        self._last_result = None
        self.calls = []
        self.next_exit = 0
        self._ExecResult = ExecResult

    @property
    def cwd(self):
        return self._cwd

    sensitive_path_patterns = ()

    @property
    def last_command(self):
        return self._last_command

    @property
    def last_result(self):
        return self._last_result

    def check_syntax(self, line):
        return None

    def run(self, line, capture_stdout=False):
        self.calls.append(line)
        result = self._ExecResult(
            exit_code=self.next_exit, signal=None, stderr_text="",
            stdout_text="", cwd_after=self._cwd, duration_ms=1)
        self._last_command = line
        self._last_result = result
        return result


class SpySink:
    color_enabled = False

    def __init__(self):
        self.lines = []

    def write_line(self, text):
        self.lines.append(text)

    def write_chunk(self, text):
        if text:
            self.lines.append(text)

    def ensure_newline(self):
        pass


def _repl(cwd):
    from sasha.config import Config, Limits, Profile
    from sasha.repl import Repl
    from sasha.session import Session

    profile = Profile(name="stubprof", base_url="http://unused",
                      model="stub-model", api_key="")
    config = Config(default_profile="stubprof",
                    profiles={"stubprof": profile}, limits=Limits())
    executor = FakeExecutor(cwd)
    sink = SpySink()
    repl = Repl(config, executor, Session(),
                lambda p: None, None, lambda prompt: "", sink)
    return repl, executor, sink


def _semantic_entries(repo):
    path = repo / ".sasha" / "memory" / "semantic.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines()]


# --- AC1: first-time replay of a repo with real commits -----------------------

def test_ac1_first_replay_writes_commits_oldest_to_newest(tmp_path):
    repo = tmp_path / "proj"
    _init_repo(repo)
    _commit(repo, "first commit")
    _commit(repo, "second commit")
    _commit(repo, "third commit")

    repl, executor, sink = _repl(repo)
    repl._check_place_shift()

    entries = _semantic_entries(repo)
    assert [e["text"] for e in entries] == [
        "first commit", "second commit", "third commit"]
    assert all(e["kind"] == "commit" for e in entries)
    assert all(e["pointer"] for e in entries)


def test_ac1_replay_line_printed_exactly_once(tmp_path):
    repo = tmp_path / "proj"
    _init_repo(repo)
    _commit(repo, "only commit")

    repl, executor, sink = _repl(repo)
    repl._check_place_shift()
    repl._check_place_shift()

    replayed = [l for l in sink.lines if "replayed" in l]
    assert len(replayed) == 1
    assert "proj" in replayed[0]
    assert "1 recent commit" in replayed[0]
    # the tally is honest about how many commits were replayed
    assert "1 recent commit(s)" in replayed[0]


def test_ac1_memory_is_regenerated_when_absent(tmp_path):
    """The replay is derived from git, so deleting memory does not lose it."""
    repo = tmp_path / "proj"
    _init_repo(repo)
    _commit(repo, "keep me")

    repl, _, _ = _repl(repo)
    repl._check_place_shift()
    assert [e["text"] for e in _semantic_entries(repo)] == ["keep me"]

    import shutil
    shutil.rmtree(repo / ".sasha")

    repl2, _, _ = _repl(repo)
    repl2._check_place_shift()
    assert [e["text"] for e in _semantic_entries(repo)] == ["keep me"]


def test_ac1_no_bootstrap_entries_and_no_pending_sentinel(tmp_path):
    """Stage 2 is gone; nothing may write `kind="bootstrap"` or a marker."""
    repo = tmp_path / "proj"
    _init_repo(repo)
    _commit(repo, "a commit")
    (repo / "README.md").write_text("# Project\n\nLots of docs.\n")

    repl, _, _ = _repl(repo)
    repl._check_place_shift()

    assert all(e["kind"] != "bootstrap" for e in _semantic_entries(repo))
    memory_dir = repo / ".sasha" / "memory"
    assert not (memory_dir / ".bootstrap_pending").exists()
    # and no thread was started at all
    assert not hasattr(repl, "_last_bootstrap_thread")
    assert not hasattr(repl, "_bootstrap_attempted")


def test_ac1_stage_two_helpers_are_gone(tmp_path):
    from sasha.repl import Repl

    for name in ("_bootstrap_repo", "_retry_bootstrap_summary",
                 "_spawn_bootstrap_summary", "_bootstrap_stage_2",
                 "_clear_bootstrap_pending", "_build_bootstrap_prompt",
                 "_parse_bootstrap_lines", "_bootstrap_finish_message"):
        assert not hasattr(Repl, name), f"{name} should have been removed"
    assert hasattr(Repl, "_replay_history")


# --- AC2: genuinely empty repo is completely untouched ------------------------

def test_ac2_empty_repo_creates_nothing(tmp_path):
    repo = tmp_path / "proj"
    _init_repo(repo)          # git repo, zero commits

    repl, _, sink = _repl(repo)
    repl._check_place_shift()

    assert not (repo / ".sasha").exists()
    assert not any("replayed" in l for l in sink.lines)


def test_ac2_repo_with_docs_but_no_commits_is_untouched(tmp_path):
    """Docs used to feed the summariser; with stage 2 gone they are not a
    reason to create memory at all."""
    repo = tmp_path / "proj"
    _init_repo(repo)
    (repo / "README.md").write_text("# Docs but no commits\n")

    repl, _, sink = _repl(repo)
    repl._check_place_shift()

    assert not (repo / ".sasha").exists()
    assert not any("replayed" in l for l in sink.lines)


# --- AC3: a second entry does not re-replay ------------------------------------

def test_ac3_existing_memory_is_not_replayed_again(tmp_path):
    repo = tmp_path / "proj"
    _init_repo(repo)
    _commit(repo, "only commit")

    repl, _, _ = _repl(repo)
    repl._check_place_shift()
    first = _semantic_entries(repo)

    repl2, _, sink2 = _repl(repo)
    repl2._check_place_shift()

    assert _semantic_entries(repo) == first        # no duplicates
    assert not any("replayed" in l for l in sink2.lines)
    # instead you get the project line, which carries the last gist
    assert any("project: proj (1 memories" in l for l in sink2.lines)


# --- AC4: the model is never consulted ----------------------------------------

def test_ac4_no_llm_call_is_ever_made(tmp_path):
    """The whole point: the replay is free. A client_factory that fails the
    test if it is even called proves it."""
    from sasha.config import Config, Limits, Profile
    from sasha.repl import Repl
    from sasha.session import Session

    repo = tmp_path / "proj"
    _init_repo(repo)
    _commit(repo, "no llm please")

    def exploding_factory(profile):
        raise AssertionError("the bootstrap must not call the model")

    config = Config(default_profile="p",
                    profiles={"p": Profile("p", "http://x/v1", "m", "", False)},
                    limits=Limits())
    repl = Repl(config, FakeExecutor(repo), Session(), exploding_factory,
                None, lambda prompt: "", SpySink())
    repl._check_place_shift()

    assert [e["text"] for e in _semantic_entries(repo)] == ["no llm please"]
