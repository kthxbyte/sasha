"""Contract tests — Task 51.1: directory_recap.py cascade + repl.py
gap-filler/dedup trigger wiring (ISSUE-051).

Written by Tier 1 at spec time; immutable to the implementer.
Spec: docs/superpowers/specs/task-51.1-directory-recap-cascade.md
Design: docs/superpowers/specs/2026-07-26-issue-051-directory-recap-design.md
"""

from __future__ import annotations

import datetime
import json
import pathlib
import subprocess
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from sse_stub import StubServer, refused_base_url  # noqa: E402


# --- shared helpers -----------------------------------------------------

def _git(repo, *args):
    subprocess.run(["git", *args], cwd=str(repo), check=True,
                    capture_output=True)


def _init_repo(repo):
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")


def _commit(repo, message):
    (repo / "f.txt").write_text(message)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)


def _ts(days_ago=0, hours_ago=0):
    when = (datetime.datetime.now(datetime.timezone.utc)
            - datetime.timedelta(days=days_ago, hours=hours_ago))
    return when.strftime("%Y-%m-%dT%H:%M:%S.") + f"{when.microsecond // 1000:03d}Z"


def _entry(cwd, ts=None, command="ls"):
    return {"ts": ts or _ts(), "session_id": "s1", "cwd": cwd, "tool": "run_command",
            "call_id": "c1", "arguments": {"command": command},
            "executed_command": command, "verdict": "direct", "executed": True,
            "result": "exit code: 0\n", "result_omitted_reason": None}


# --- AC1/AC2: umbrella shape ---------------------------------------------

def test_ac1_umbrella_non_repo_is_silent(tmp_path):
    from sasha import directory_recap

    target = tmp_path / "umbrella"
    target.mkdir()
    _init_repo(target / "child_a")
    _init_repo(target / "child_b")

    verdict, text = directory_recap.decide(target, log_entries=[])
    assert verdict == directory_recap.SILENT
    assert text is None


def test_ac2_umbrella_self_repo_escalates(tmp_path):
    from sasha import directory_recap

    target = tmp_path / "mqtt"
    _init_repo(target)
    _commit(target, "init")
    _init_repo(target / "ecom")
    _init_repo(target / "iotd")

    verdict, text = directory_recap.decide(target, log_entries=[])
    assert verdict == directory_recap.ESCALATE
    assert text is None


# --- AC3/AC4/AC5: cheap log rules -----------------------------------------

def test_ac3_no_history_is_silent(tmp_path):
    from sasha import directory_recap

    target = tmp_path / "quiet"
    _init_repo(target)
    _commit(target, "only commit")

    verdict, text = directory_recap.decide(target, log_entries=[])
    assert verdict == directory_recap.SILENT
    assert text is None


def test_ac4_high_fanout_is_silent(tmp_path):
    from sasha import directory_recap

    target = tmp_path / "scattered"
    target.mkdir()
    entries = [
        _entry(str(target / "a")),
        _entry(str(target / "b")),
        _entry(str(target / "c")),
    ]
    verdict, text = directory_recap.decide(target, log_entries=entries)
    assert verdict == directory_recap.SILENT
    assert text is None


def test_ac5_stale_activity_is_silent(tmp_path):
    from sasha import directory_recap

    target = tmp_path / "stale"
    _init_repo(target)
    _commit(target, "old work")
    entries = [_entry(str(target), ts=_ts(days_ago=20))]

    verdict, text = directory_recap.decide(target, log_entries=entries)
    assert verdict == directory_recap.SILENT
    assert text is None


# --- AC6/AC7/AC8: direct recap vs. escalate on non-umbrella paths --------

def test_ac6_coherent_repo_recaps_directly(tmp_path):
    from sasha import directory_recap

    target = tmp_path / "coherent"
    _init_repo(target)
    _commit(target, "add the widget")
    entries = [_entry(str(target), ts=_ts(hours_ago=2))]

    verdict, text = directory_recap.decide(target, log_entries=entries)
    assert verdict == directory_recap.RECAP_DIRECT
    assert "coherent" in text
    assert "add the widget" in text


def test_ac7_repo_no_commits_escalates(tmp_path):
    from sasha import directory_recap

    target = tmp_path / "empty_repo"
    _init_repo(target)  # no commits
    entries = [_entry(str(target), ts=_ts(hours_ago=1))]

    verdict, text = directory_recap.decide(target, log_entries=entries)
    assert verdict == directory_recap.ESCALATE
    assert text is None


def test_ac8_active_non_repo_escalates(tmp_path):
    from sasha import directory_recap

    target = tmp_path / "non_repo_active"
    target.mkdir()
    entries = [_entry(str(target), ts=_ts(hours_ago=1))]

    verdict, text = directory_recap.decide(target, log_entries=entries)
    assert verdict == directory_recap.ESCALATE
    assert text is None


# --- AC9: gather_context shape --------------------------------------------

def test_ac9_gather_context_shape(tmp_path):
    from sasha import directory_recap

    target = tmp_path / "mqtt"
    _init_repo(target)
    _commit(target, "bridge init")

    child_names = [f"child{i}" for i in range(7)]
    for name in sorted(child_names):
        child = target / name
        _init_repo(child)
        _commit(child, f"{name} work")

    entries = [_entry(str(target), ts=_ts(hours_ago=i), command=f"cmd{i}")
               for i in range(15)]

    ctx = directory_recap.gather_context(target, log_entries=entries)

    assert ctx["target_name"] == "mqtt"
    assert ctx["self_commit"] is not None and "bridge init" in ctx["self_commit"]
    assert len(ctx["child_commits"]) == directory_recap.MAX_CHILD_REPOS_FOR_LLM
    assert [n for n, _ in ctx["child_commits"]] == sorted(child_names)[:5]
    assert all(c is not None for _, c in ctx["child_commits"])
    assert len(ctx["recent_commands"]) == directory_recap.MAX_LOG_ENTRIES_FOR_LLM
    # most-recent-first: cmd0 (0h ago) must precede cmd14 (14h ago)
    commands = [c for c, _ in ctx["recent_commands"]]
    assert commands[0] == "cmd0"


# --- AC10/AC11: escalate_async ---------------------------------------------

def _client_factory(profile):
    from sasha.llm import ChatClient
    return ChatClient(profile, timeout_s=10.0)


def _profile(base_url):
    from sasha.config import Profile
    return Profile(name="stub", base_url=base_url, model="stub-model", api_key="")


def test_ac10_escalate_async_result_and_abstain(tmp_path):
    from sasha import directory_recap

    target = tmp_path / "mqtt"
    _init_repo(target)
    _commit(target, "bridge init")

    results = []
    with StubServer([{"kind": "text", "text": "mqtt: bridges iotd creds to an external consumer"}]) as stub:
        thread = directory_recap.escalate_async(
            target, _client_factory, _profile(stub.base_url), results.append)
        thread.join(timeout=15)

    assert len(results) == 1
    assert results[0].startswith("[sasha] ")
    assert "bridges iotd creds" in results[0]

    results2 = []
    with StubServer([{"kind": "text", "text": ""}]) as stub2:
        thread2 = directory_recap.escalate_async(
            target, _client_factory, _profile(stub2.base_url), results2.append)
        thread2.join(timeout=15)

    assert results2 == []


def test_ac11_escalate_async_swallows_chat_error(tmp_path):
    from sasha import directory_recap

    target = tmp_path / "mqtt"
    _init_repo(target)
    _commit(target, "bridge init")

    results = []
    thread = directory_recap.escalate_async(
        target, _client_factory, _profile(refused_base_url()), results.append)
    thread.join(timeout=15)

    assert results == []


# --- AC12: tool_log.log_path() --------------------------------------------

def test_ac12_tool_log_log_path_matches_internal():
    from sasha import tool_log

    assert tool_log.log_path() == tool_log._log_path()


# --- AC20: unreadable child directories don't crash the scan -------------

def test_ac20_unreadable_child_directory_does_not_crash(tmp_path):
    from sasha import directory_recap

    target = tmp_path / "target"
    target.mkdir()
    locked = target / "locked"
    locked.mkdir()
    import os
    os.chmod(locked, 0)
    try:
        verdict, text = directory_recap.decide(target, log_entries=[])
        assert verdict == directory_recap.SILENT
        assert text is None
    finally:
        os.chmod(locked, 0o755)


# --- AC13/AC14: config -----------------------------------------------------

@pytest.fixture
def isolated_env(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    return tmp_path


def test_ac13_config_directory_recap_enabled(isolated_env, tmp_path):
    from sasha.config import load

    default = load()
    assert default.directory_recap_enabled is True

    p = tmp_path / "off.toml"
    p.write_text('default_profile = "llm7"\n[profiles.llm7]\n'
                 'base_url = "https://api.llm7.io/v1"\nmodel = "gpt-oss:20b"\n'
                 '[directory_recap]\nenabled = false\n')
    off = load(p)
    assert off.directory_recap_enabled is False

    p2 = tmp_path / "bad.toml"
    p2.write_text('default_profile = "llm7"\n[profiles.llm7]\n'
                  'base_url = "https://api.llm7.io/v1"\nmodel = "gpt-oss:20b"\n'
                  '[directory_recap]\nenabled = "yes"\n')
    bad = load(p2)
    assert bad.directory_recap_enabled is True


def test_ac14_default_toml_has_directory_recap(isolated_env):
    from sasha.config import load

    load()
    written = (isolated_env / "xdg-config" / "sasha" / "config.toml").read_text()
    assert "[directory_recap]" in written
    assert "enabled = true" in written


# --- AC15-19: repl.py integration ------------------------------------------

class FakeExecutor:
    def __init__(self, cwd):
        self._cwd = cwd
        self.sensitive_path_patterns = ()

    @property
    def cwd(self):
        return self._cwd

    def set_cwd(self, cwd):
        self._cwd = cwd


class SpySink:
    color_enabled = False

    def __init__(self):
        self.lines = []
        self.async_lines = []

    def write_line(self, text):
        self.lines.append(text)

    def write_async_line(self, text):
        self.async_lines.append(text)

    def write_chunk(self, text):
        if text:
            self.lines.append(text)

    def ensure_newline(self):
        pass


def _repl(cwd, stub_url=None, directory_recap_enabled=True):
    from sasha.config import Config, Limits, Profile
    from sasha.llm import ChatClient
    from sasha.repl import Repl
    from sasha.session import Session

    profile = Profile(name="stubprof", base_url=stub_url or "http://unused",
                       model="stub-model", api_key="")
    config = Config(default_profile="stubprof",
                     profiles={"stubprof": profile}, limits=Limits(),
                     directory_recap_enabled=directory_recap_enabled)
    executor = FakeExecutor(cwd)
    sink = SpySink()
    repl = Repl(config, executor, Session(),
                lambda p: ChatClient(p, timeout_s=10.0), None,
                lambda prompt: "", sink)
    return repl, executor, sink


def _join_recap_thread(repl, timeout=15):
    if repl._last_recap_thread is not None:
        repl._last_recap_thread.join(timeout=timeout)


def test_ac15_check_place_shift_return_value(tmp_path):
    no_repo = tmp_path / "no_repo"
    no_repo.mkdir()
    repl, _, _ = _repl(str(no_repo))
    assert repl._check_place_shift() is False

    repo = tmp_path / "repo"
    _init_repo(repo)
    repl2, _, _ = _repl(str(repo))
    # first call: no memories yet -> bootstrap kicks in, no project line -> False
    assert repl2._check_place_shift() is False


@pytest.fixture
def isolated_tool_log(monkeypatch, tmp_path):
    state_dir = tmp_path / "xdg-state"
    monkeypatch.setenv("XDG_STATE_HOME", str(state_dir))

    def _write(cwd, count=1):
        path = state_dir / "sasha" / "tool_log.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as f:
            for i in range(count):
                f.write(json.dumps(_entry(cwd, ts=_ts(hours_ago=i))) + "\n")

    return _write


def test_ac16_gap_filler_suppresses_new_mechanism(tmp_path, isolated_tool_log):
    repo = tmp_path / "repo"
    _init_repo(repo)
    _commit(repo, "one")
    from sasha import memory
    memory.ensure_memory_dir(repo)
    memory.encode_remember(repo, "a saved note", None, None)
    isolated_tool_log(str(repo))

    repl, executor, sink = _repl(str(repo))
    before = len(sink.lines) + len(sink.async_lines)
    printed = repl._check_place_shift()
    assert printed is True
    if not printed:
        repl._check_directory_recap()
    after = len(sink.lines) + len(sink.async_lines)
    assert after == before + 1  # exactly the project-memory line, nothing more
    _join_recap_thread(repl)


def test_ac17_gap_filler_allows_new_mechanism(tmp_path, isolated_tool_log):
    repo = tmp_path / "repo"
    _init_repo(repo)
    _commit(repo, "add the gizmo")
    isolated_tool_log(str(repo))

    repl, executor, sink = _repl(str(repo))
    printed = repl._check_place_shift()
    assert printed is False
    if not printed:
        repl._check_directory_recap()
    assert any("add the gizmo" in l for l in sink.lines)
    _join_recap_thread(repl)


def test_ac18_per_session_dedup(tmp_path, isolated_tool_log):
    # Exercises directory_recap's own per-path dedup set in isolation,
    # independent of _check_place_shift's separate cwd/git-root dedup
    # (already covered by AC16/AC17's gap-filler composition tests).
    repo = tmp_path / "repo"
    _init_repo(repo)
    _commit(repo, "add the gadget")
    isolated_tool_log(str(repo))

    repl, executor, sink = _repl(str(repo))
    repl._check_directory_recap()
    first_count = len(sink.lines)
    assert any("add the gadget" in l for l in sink.lines)

    repl._check_directory_recap()  # same cwd, same session -> stays silent
    assert len(sink.lines) == first_count
    _join_recap_thread(repl)


def test_ac19_disabled_config_suppresses_everything(tmp_path, isolated_tool_log):
    repo = tmp_path / "repo"
    _init_repo(repo)
    _commit(repo, "add the sprocket")
    isolated_tool_log(str(repo))

    repl, executor, sink = _repl(str(repo), directory_recap_enabled=False)
    repl._check_directory_recap()
    assert sink.lines == []
    assert sink.async_lines == []
    assert repl._last_recap_thread is None
