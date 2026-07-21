"""Contract tests — Task 17.3: repl.py place-shift detection, commit
hook, sasha memory/forget built-ins; plus memory.py's
encode_latest_commit/forget additions.

Written by Tier 1 at spec time; immutable to the implementer.
Spec: docs/superpowers/specs/task-17.3-repl-wiring.md
Design: docs/superpowers/specs/2026-07-17-project-memory-design.md
"""

import json
import pathlib
import subprocess
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from sse_stub import StubServer  # noqa: E402


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


# --- memory.py: encode_latest_commit / forget ---------------------------------

def test_ac_encode_latest_commit_reads_head(tmp_path):
    from sasha.memory import encode_latest_commit
    repo = tmp_path / "repo"
    _init_repo(repo)
    _commit(repo, "first commit")
    just_created = encode_latest_commit(repo)
    assert just_created is True
    lines = (repo / ".sasha" / "memory" / "semantic.jsonl").read_text().splitlines()
    entry = json.loads(lines[0])
    assert entry["kind"] == "commit"
    assert entry["text"] == "first commit"
    assert entry["pointer"]        # a commit hash, non-empty
    assert len(entry["pointer"]) == 40


def test_ac_encode_latest_commit_multiline_message(tmp_path):
    from sasha.memory import encode_latest_commit
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "f.txt").write_text("x")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "title line\n\nbody paragraph")
    encode_latest_commit(repo)
    lines = (repo / ".sasha" / "memory" / "semantic.jsonl").read_text().splitlines()
    entry = json.loads(lines[0])
    assert "title line" in entry["text"]
    assert "body paragraph" in entry["text"]


def test_ac_encode_latest_commit_no_commits_yet_returns_none(tmp_path):
    from sasha.memory import encode_latest_commit
    repo = tmp_path / "repo"
    _init_repo(repo)
    result = encode_latest_commit(repo)
    assert result is None
    assert not (repo / ".sasha").exists()


def test_ac_forget_removes_memory_directory(tmp_path):
    from sasha.memory import encode_commit, forget
    repo = tmp_path / "repo"
    _init_repo(repo)
    encode_commit(repo, "a change", "h1")
    assert (repo / ".sasha" / "memory").exists()
    forget(repo)
    assert not (repo / ".sasha" / "memory").exists()


def test_ac_forget_on_repo_with_no_memory_is_a_noop(tmp_path):
    from sasha.memory import forget
    repo = tmp_path / "repo"
    _init_repo(repo)
    forget(repo)  # must not raise
    assert not (repo / ".sasha").exists()


# --- repl.py wiring --------------------------------------------------------------

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

    # Amended by Tier 1 for Task 25.1 (ISSUE-044): the raw-command path
    # now calls safe_commands.touches_sensitive_path for every command,
    # which reads this attribute.
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


class ScriptedConfirmer:
    def ask(self, command, description):
        return ("no", "")


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


def _repl(cwd, stub_url=None, input_fn=None):
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
                lambda p: ChatClient(p, timeout_s=10.0), ScriptedConfirmer(),
                input_fn or (lambda prompt: ""), sink)
    return repl, executor, sink


# --- AC: place-shift detection ----------------------------------------------------

def test_ac_loop_start_announces_existing_memory(tmp_path):
    from sasha.memory import encode_commit
    repo = tmp_path / "repo"
    _init_repo(repo)
    encode_commit(repo, "earlier work", "h1")
    repl, executor, sink = _repl(str(repo))
    repl._check_place_shift()
    assert any("project: repo" in line and "1 memories" in line
               for line in sink.lines)
    assert "earlier work" in "\n".join(sink.lines)


def test_ac_place_shift_loads_memory_into_session(tmp_path):
    from sasha.memory import encode_commit
    repo = tmp_path / "repo"
    _init_repo(repo)
    encode_commit(repo, "remembered fact", "h1")
    repl, executor, sink = _repl(str(repo))
    repl._check_place_shift()
    assert "remembered fact" in repl._session.system_prompt


def test_ac_no_announcement_for_repo_with_no_memory_yet(tmp_path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    repl, executor, sink = _repl(str(repo))
    repl._check_place_shift()
    assert sink.lines == []


def test_ac_no_announcement_outside_git_repo(tmp_path):
    plain = tmp_path / "not-a-repo"
    plain.mkdir()
    repl, executor, sink = _repl(str(plain))
    repl._check_place_shift()
    assert sink.lines == []
    assert repl._session.system_prompt.count("What you remember") == 0


def test_ac_place_shift_recheck_skipped_when_cwd_unchanged(tmp_path):
    from sasha.memory import encode_commit
    repo = tmp_path / "repo"
    _init_repo(repo)
    encode_commit(repo, "fact", "h1")
    repl, executor, sink = _repl(str(repo))
    repl._check_place_shift()
    first_count = len(sink.lines)
    repl._check_place_shift()  # cwd unchanged — must not re-announce
    repl._check_place_shift()
    assert len(sink.lines) == first_count


def test_ac_moving_to_different_repo_swaps_memory(tmp_path):
    from sasha.memory import encode_commit
    repo_a = tmp_path / "repo_a"
    repo_b = tmp_path / "repo_b"
    _init_repo(repo_a)
    _init_repo(repo_b)
    encode_commit(repo_a, "fact about A", "ha")
    encode_commit(repo_b, "fact about B", "hb")
    repl, executor, sink = _repl(str(repo_a))
    repl._check_place_shift()
    assert "fact about A" in repl._session.system_prompt
    executor._cwd = str(repo_b)
    repl._check_place_shift()
    assert "fact about B" in repl._session.system_prompt
    assert "fact about A" not in repl._session.system_prompt


def test_ac_moving_out_of_repo_clears_memory(tmp_path):
    from sasha.memory import encode_commit
    repo = tmp_path / "repo"
    _init_repo(repo)
    encode_commit(repo, "fact", "h1")
    plain = tmp_path / "plain"
    plain.mkdir()
    repl, executor, sink = _repl(str(repo))
    repl._check_place_shift()
    assert "fact" in repl._session.system_prompt
    executor._cwd = str(plain)
    repl._check_place_shift()
    assert "What you remember" not in repl._session.system_prompt


# --- AC: commit hook ---------------------------------------------------------------
#
# Amended by Tier 1 for Task 19.3 (memory bootstrap): the four tests below
# each need one *real* git commit to exist so encode_latest_commit's own
# `git log -1` call (triggered by the commit hook) has something genuine
# to read — FakeExecutor.run() never touches real git state. Previously
# that commit was made before the first _check_place_shift() call; now
# that a repo with real commit history at first place-shift eagerly
# bootstraps (see docs/superpowers/specs/2026-07-18-memory-bootstrap-design.md),
# doing so would exercise bootstrap instead of the commit-hook path this
# suite exists to test. Moving each _commit(...) call to *after* the
# first _check_place_shift() keeps the repo empty (zero commits, zero
# docs) at bootstrap-decision time — bootstrap's own history/docs check
# is a no-op on an empty repo, exactly matching this repo's pre-Task-19.3
# behavior — so every assertion below is unchanged from before this task.

def test_ac_run_once_git_commit_encodes_entry(tmp_path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    repl, executor, sink = _repl(str(repo))
    repl._check_place_shift()
    _commit(repo, "the actual commit")
    repl.run_once('git commit -m "the actual commit"')
    lines = (repo / ".sasha" / "memory" / "semantic.jsonl").read_text().splitlines()
    entry = json.loads(lines[0])
    assert entry["kind"] == "commit"
    assert entry["text"] == "the actual commit"


def test_ac_commit_hook_ignores_non_commit_git_commands(tmp_path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    repl, executor, sink = _repl(str(repo))
    repl._check_place_shift()
    _commit(repo, "prior commit")
    repl.run_once("git status")
    assert not (repo / ".sasha" / "memory" / "semantic.jsonl").exists()


def test_ac_commit_hook_ignores_commit_subcommand_lookalike(tmp_path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    repl, executor, sink = _repl(str(repo))
    repl._check_place_shift()
    repl.run_once("git commit-tree -m fake")
    assert not (repo / ".sasha" / "memory" / "semantic.jsonl").exists()


def test_ac_commit_hook_skipped_on_failed_command(tmp_path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    repl, executor, sink = _repl(str(repo))
    repl._check_place_shift()
    _commit(repo, "existing")
    executor.next_exit = 1
    repl.run_once('git commit -m "should not be recorded"')
    assert not (repo / ".sasha" / "memory" / "semantic.jsonl").exists()


def test_ac_creation_notice_fires_once_on_first_commit(tmp_path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    repl, executor, sink = _repl(str(repo))
    repl._check_place_shift()
    _commit(repo, "the first ever commit")
    repl.run_once('git commit -m "the first ever commit"')
    creation_notices = [l for l in sink.lines if "created .sasha/memory/" in l]
    assert len(creation_notices) == 1
    sink.lines.clear()
    _commit(repo, "a second commit")
    repl.run_once('git commit -m "a second commit"')
    assert not any("created .sasha/memory/" in l for l in sink.lines)


# --- AC: sasha memory / sasha forget built-ins --------------------------------------

def test_ac_sasha_memory_outside_git_repo(monkeypatch, tmp_path):
    # Amended by Tier 1 for Task 18.3 (general memory): outside a repo,
    # sasha memory now shows general memory instead of a bare "not
    # inside a git repository" message — general memory is always
    # present as a fallback. See
    # docs/superpowers/specs/task-18.3-repl-general-crash-wiring.md.
    # XDG_CONFIG_HOME isolated to tmp_path so this never reads the real
    # host's ~/.config/sasha/memory/.
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    plain = tmp_path / "plain"
    plain.mkdir()
    repl, executor, sink = _repl(str(plain))
    repl._check_place_shift()
    repl.run_once("sasha memory")
    assert any("no general memory yet" in l for l in sink.lines)


def test_ac_sasha_memory_no_entries_yet(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    repo = tmp_path / "repo"
    _init_repo(repo)
    repl, executor, sink = _repl(str(repo))
    repl._check_place_shift()
    repl.run_once("sasha memory")
    assert any("no memory yet" in l for l in sink.lines)


def test_ac_sasha_memory_shows_entries(monkeypatch, tmp_path):
    from sasha.memory import encode_commit
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    repo = tmp_path / "repo"
    _init_repo(repo)
    encode_commit(repo, "a remembered thing", "h1")
    repl, executor, sink = _repl(str(repo))
    repl._check_place_shift()
    repl.run_once("sasha memory")
    text = "\n".join(sink.lines)
    assert "1 entries" in text
    assert "a remembered thing" in text


def test_ac_sasha_forget_cancelled_keeps_memory(tmp_path):
    from sasha.memory import encode_commit
    repo = tmp_path / "repo"
    _init_repo(repo)
    encode_commit(repo, "keep me", "h1")
    repl, executor, sink = _repl(str(repo), input_fn=lambda p: "no")
    repl._check_place_shift()
    repl.run_once("sasha forget")
    assert (repo / ".sasha" / "memory" / "semantic.jsonl").exists()
    assert any("cancelled" in l for l in sink.lines)


def test_ac_sasha_forget_confirmed_deletes_memory(tmp_path):
    from sasha.memory import encode_commit
    repo = tmp_path / "repo"
    _init_repo(repo)
    encode_commit(repo, "delete me", "h1")
    repl, executor, sink = _repl(str(repo), input_fn=lambda p: "yes")
    repl._check_place_shift()
    repl.run_once("sasha forget")
    assert not (repo / ".sasha" / "memory").exists()
    assert any(l == "[sasha] forgotten" for l in sink.lines)
    assert "What you remember" not in repl._session.system_prompt


def test_ac_sasha_forget_nothing_to_forget(tmp_path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    repl, executor, sink = _repl(str(repo))
    repl._check_place_shift()
    repl.run_once("sasha forget")
    assert any("nothing to forget" in l for l in sink.lines)


def test_ac_help_still_mentions_mute_profile_ask():
    repl, executor, sink = _repl("/tmp")
    repl.run_once("sasha help")
    assert any(("mute" in l and "profile" in l and "??" in l)
               for l in sink.lines)


# --- AC: remember-tool-triggered creation notice, via a real consult --------------

def test_ac_remember_via_consult_triggers_creation_notice(tmp_path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    with StubServer([
            {"kind": "tool_call", "id": "call_1", "name": "remember",
             "arguments": {"note": "learned via consult"}},
            {"kind": "text", "text": "noted"},
    ]) as stub:
        repl, executor, sink = _repl(str(repo), stub_url=stub.base_url)
        repl._check_place_shift()
        repl.run_once("?? please remember this")
    assert any("created .sasha/memory/" in l for l in sink.lines)
    lines = (repo / ".sasha" / "memory" / "semantic.jsonl").read_text().splitlines()
    assert json.loads(lines[0])["text"] == "learned via consult"
