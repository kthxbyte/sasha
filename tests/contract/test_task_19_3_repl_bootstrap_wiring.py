"""Contract tests — Task 19.3: repl.py stage 1/2 bootstrap wiring into
_check_place_shift.

Written by Tier 1 at spec time; immutable to the implementer.
Spec: docs/superpowers/specs/task-19.3-repl-bootstrap-wiring.md
Design: docs/superpowers/specs/2026-07-18-memory-bootstrap-design.md
"""

import json
import pathlib
import subprocess
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from sse_stub import StubServer, refused_base_url  # noqa: E402


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


class SpyMutes:
    def is_muted(self, command, exit_code):
        return False

    def record_ignored(self, command, exit_code):
        return False

    def record_acted(self, command, exit_code):
        pass

    def mute(self, command, exit_code=None):
        pass

    def unmute(self, command, exit_code=None):
        pass


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


def _repl(cwd, stub_url=None):
    from sasha.config import Config, Limits, Profile
    from sasha.llm import ChatClient
    from sasha.repl import Repl
    from sasha.session import Session

    profile = Profile(name="stubprof", base_url=stub_url or "http://unused",
                       model="stub-model", api_key="")
    config = Config(default_profile="stubprof",
                     profiles={"stubprof": profile}, limits=Limits())
    executor = FakeExecutor(cwd)
    sink = SpySink()
    repl = Repl(config, executor, SpyMutes(), Session(),
                lambda p: ChatClient(p, timeout_s=10.0), None,
                lambda prompt: "", sink)
    return repl, executor, sink


def _join_if_spawned(repl, timeout=15):
    if repl._last_bootstrap_thread is not None:
        repl._last_bootstrap_thread.join(timeout=timeout)


def _semantic_entries(repo):
    path = repo / ".sasha" / "memory" / "semantic.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines()]


# --- AC1: first-time bootstrap of a repo with real commits --------------------

def test_ac1_first_bootstrap_replays_oldest_to_newest(tmp_path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    _commit(repo, "first commit")
    _commit(repo, "second commit")
    _commit(repo, "third commit")
    repl, executor, sink = _repl(str(repo))
    repl._check_place_shift()
    entries = _semantic_entries(repo)
    commit_texts = [e["text"] for e in entries if e["kind"] == "commit"]
    assert commit_texts == ["first commit", "second commit", "third commit"]
    _join_if_spawned(repl)


def test_ac1_start_line_printed_exactly_once(tmp_path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    _commit(repo, "only commit")
    repl, executor, sink = _repl(str(repo))
    repl._check_place_shift()
    start_lines = [l for l in sink.lines if "no memory yet for repo" in l
                   and "building initial context" in l]
    assert len(start_lines) == 1
    _join_if_spawned(repl)


def test_ac1_no_redundant_project_line_on_first_bootstrap(tmp_path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    _commit(repo, "only commit")
    repl, executor, sink = _repl(str(repo))
    repl._check_place_shift()
    assert not any("memories, last:" in l for l in sink.lines)
    _join_if_spawned(repl)


def test_ac1_pending_sentinel_written_synchronously(tmp_path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    _commit(repo, "only commit")
    repl, executor, sink = _repl(str(repo))
    repl._check_place_shift()
    assert (repo / ".sasha" / "memory" / ".bootstrap_pending").exists()
    _join_if_spawned(repl)


# --- AC2: genuinely empty repo is completely untouched -------------------------

def test_ac2_empty_repo_no_docs_creates_nothing(tmp_path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    repl, executor, sink = _repl(str(repo))
    repl._check_place_shift()
    assert not (repo / ".sasha").exists()
    assert not any("no memory yet" in l for l in sink.lines)
    assert repl._background_tasks == 0
    assert repl._last_bootstrap_thread is None


# --- AC3: docs without commits still spawns stage 2 ----------------------------

def test_ac3_docs_without_commits_spawns_stage2(tmp_path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "README.md").write_text("This project does X.")
    repl, executor, sink = _repl(str(repo))
    repl._check_place_shift()
    assert repl._last_bootstrap_thread is not None
    _join_if_spawned(repl)
    assert repl._background_tasks == 0


# --- AC4-5: stage 2 success ------------------------------------------------------

def test_ac4_stage2_success_writes_bootstrap_entries(tmp_path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    _commit(repo, "initial commit")
    with StubServer([{"kind": "text",
                       "text": "- fact one\n- fact two\n- fact three"}]) as stub:
        repl, executor, sink = _repl(str(repo), stub_url=stub.base_url)
        repl._check_place_shift()
        _join_if_spawned(repl)
    entries = _semantic_entries(repo)
    bootstrap_entries = [e for e in entries if e["kind"] == "bootstrap"]
    assert len(bootstrap_entries) == 3
    assert all(e["pointer"] is None for e in bootstrap_entries)
    assert {e["text"] for e in bootstrap_entries} == {
        "fact one", "fact two", "fact three"}
    assert not (repo / ".sasha" / "memory" / ".bootstrap_pending").exists()
    assert repl._background_tasks == 0


def test_ac5_finish_line_names_single_file(tmp_path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "README.md").write_text("about this project")
    with StubServer([{"kind": "text", "text": "- a fact"}]) as stub:
        repl, executor, sink = _repl(str(repo), stub_url=stub.base_url)
        repl._check_place_shift()
        _join_if_spawned(repl)
    assert any(
        l == "[sasha] finished building context: 1 entries (from README.md)"
        for l in sink.lines)


def test_ac5_finish_line_states_count_for_multiple_files(tmp_path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "README.md").write_text("about this project")
    (repo / "NOTES.txt").write_text("more notes")
    with StubServer([{"kind": "text", "text": "- a fact"}]) as stub:
        repl, executor, sink = _repl(str(repo), stub_url=stub.base_url)
        repl._check_place_shift()
        _join_if_spawned(repl)
    assert any(
        l == "[sasha] finished building context: 1 entries (from 2 files)"
        for l in sink.lines)


def test_ac5_finish_line_omits_file_mention_for_history_only(tmp_path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    _commit(repo, "initial commit")
    with StubServer([{"kind": "text", "text": "- a fact"}]) as stub:
        repl, executor, sink = _repl(str(repo), stub_url=stub.base_url)
        repl._check_place_shift()
        _join_if_spawned(repl)
    finish_lines = [l for l in sink.lines if "finished building context" in l]
    assert finish_lines == ["[sasha] finished building context: 1 entries"]


# --- AC6-7: stage 2 failure ------------------------------------------------------

def test_ac6_stage2_unreachable_writes_nothing_and_keeps_sentinel(tmp_path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    _commit(repo, "initial commit")
    repl, executor, sink = _repl(str(repo), stub_url=refused_base_url())
    repl._check_place_shift()
    _join_if_spawned(repl)
    entries = _semantic_entries(repo)
    assert not any(e["kind"] == "bootstrap" for e in entries)
    assert not any("finished building context" in l for l in sink.lines)
    assert (repo / ".sasha" / "memory" / ".bootstrap_pending").exists()
    assert repl._background_tasks == 0


def test_ac7_stage2_empty_response_writes_nothing(tmp_path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    _commit(repo, "initial commit")
    with StubServer([{"kind": "text", "text": ""}]) as stub:
        repl, executor, sink = _repl(str(repo), stub_url=stub.base_url)
        repl._check_place_shift()
        _join_if_spawned(repl)
    entries = _semantic_entries(repo)
    assert not any(e["kind"] == "bootstrap" for e in entries)
    assert not any("finished building context" in l for l in sink.lines)
    assert (repo / ".sasha" / "memory" / ".bootstrap_pending").exists()


# --- AC8: _parse_bootstrap_lines direct --------------------------------------

def test_ac8_parse_bootstrap_lines_strips_markers_and_caps_at_8(tmp_path):
    repl, executor, sink = _repl(str(tmp_path))
    text = "\n".join([
        "- dash fact",
        "* star fact",
        "• bullet fact",
        "1. numbered dot fact",
        "2) numbered paren fact",
        "",
        "plain fact no marker",
        "extra one", "extra two", "extra three",
    ])
    result = repl._parse_bootstrap_lines(text)
    assert result == [
        "dash fact", "star fact", "bullet fact", "numbered dot fact",
        "numbered paren fact", "plain fact no marker", "extra one",
        "extra two",
    ]


def test_ac8_parse_bootstrap_lines_empty_text_returns_empty(tmp_path):
    repl, executor, sink = _repl(str(tmp_path))
    assert repl._parse_bootstrap_lines("") == []
    assert repl._parse_bootstrap_lines("   \n   \n") == []


# --- AC9: _build_bootstrap_prompt direct --------------------------------------

def test_ac9_build_prompt_includes_both_sections(tmp_path):
    repl, executor, sink = _repl(str(tmp_path))
    docs = [("README.md", "unique readme content xyz")]
    history = [("h1", "unique commit message xyz")]
    prompt = repl._build_bootstrap_prompt(history, docs)
    assert "### Project documentation" in prompt
    assert "### Recent commit history" in prompt
    assert "unique readme content xyz" in prompt
    assert "unique commit message xyz" in prompt


def test_ac9_build_prompt_omits_empty_sections(tmp_path):
    repl, executor, sink = _repl(str(tmp_path))
    docs_only = repl._build_bootstrap_prompt([], [("README.md", "content")])
    assert "### Project documentation" in docs_only
    assert "### Recent commit history" not in docs_only

    history_only = repl._build_bootstrap_prompt([("h1", "msg")], [])
    assert "### Recent commit history" in history_only
    assert "### Project documentation" not in history_only


# --- AC10: retry attempts once per session -------------------------------------

def test_ac10_retry_only_attempts_once_per_session(tmp_path):
    from sasha import memory
    repo = tmp_path / "repo"
    _init_repo(repo)
    _commit(repo, "initial commit")
    memory.ensure_memory_dir(repo)
    memory.encode_commit(repo, "initial commit", "deadbeef")
    (repo / ".sasha" / "memory" / ".bootstrap_pending").write_text("")

    with StubServer([{"kind": "http_error", "status": 500},
                      {"kind": "http_error", "status": 500}]) as stub:
        repl, executor, sink = _repl(str(repo), stub_url=stub.base_url)
        repl._check_place_shift()
        _join_if_spawned(repl)

        other = tmp_path / "other"
        other.mkdir()
        executor._cwd = str(other)
        repl._check_place_shift()

        executor._cwd = str(repo)
        repl._check_place_shift()
        _join_if_spawned(repl)

    assert len(stub.requests) == 1


# --- AC11: no sentinel means never touched, regardless of attempted-set -------

def test_ac11_no_pending_sentinel_never_spawns(tmp_path):
    from sasha import memory
    repo = tmp_path / "repo"
    _init_repo(repo)
    _commit(repo, "initial commit")
    memory.ensure_memory_dir(repo)
    memory.encode_commit(repo, "initial commit", "deadbeef")
    # deliberately no .bootstrap_pending sentinel — simulates either a
    # fully-succeeded prior bootstrap or a pre-Task-19.3 (Phase 17/18)
    # directory that predates this feature entirely.
    repl, executor, sink = _repl(str(repo))
    repl._check_place_shift()
    assert repl._last_bootstrap_thread is None
    assert repl._background_tasks == 0


# --- AC12: existing creation-notice path is unaffected on a truly empty repo ---

def test_ac12_creation_notice_still_fires_via_commit_hook_on_empty_repo(tmp_path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    repl, executor, sink = _repl(str(repo))
    repl._check_place_shift()
    assert not (repo / ".sasha").exists()
    _commit(repo, "the first ever commit")
    repl.run_once('git commit -m "the first ever commit"')
    creation_notices = [l for l in sink.lines if "created .sasha/memory/" in l]
    assert len(creation_notices) == 1
