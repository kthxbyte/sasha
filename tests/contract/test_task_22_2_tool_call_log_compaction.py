"""Contract tests — Task 22.2: tool-call logging compaction, config,
and the manual 'sasha compact-log' command.

Written by Tier 1 at spec time; immutable to the implementer.
Spec: docs/superpowers/specs/task-22.2-tool-call-log-compaction.md
Design: docs/superpowers/specs/2026-07-21-tool-call-logging-design.md

State-dir isolation (XDG_STATE_HOME) comes from the autouse fixture in
tests/contract/conftest.py (Task 22.1).
"""

from __future__ import annotations

import json
import pathlib
import sys
import threading
import time

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from sse_stub import StubServer  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_tool_log_module_state():
    import sasha.tool_log as tool_log_module

    tool_log_module._max_size_mb = 100
    yield
    tool_log_module._max_size_mb = 100


def _log_path(tmp_path) -> pathlib.Path:
    return tmp_path / "xdg-state" / "sasha" / "tool_log.jsonl"


def _write_raw_entries(tmp_path, entries: list[dict]) -> None:
    path = _log_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(e) + "\n" for e in entries))


def _make_entry(call_id, result_size=0, result=None):
    return {
        "ts": "2026-07-21T00:00:00.000Z", "session_id": "s1", "cwd": "/spy",
        "tool": "run_command", "call_id": call_id,
        "arguments": {"command": "ls"}, "executed_command": None,
        "verdict": "yes", "executed": True,
        "result": result if result is not None else ("x" * result_size),
        "result_omitted_reason": None,
    }


def _read_entries(tmp_path) -> list[dict]:
    path = _log_path(tmp_path)
    return [json.loads(line) for line in path.read_text().splitlines() if line]


# --- AC1: oldest-first stripping down to ~50% target ------------------------

def test_ac1_compact_strips_oldest_first_to_target(tmp_path):
    from sasha.tool_log import compact

    entries = [_make_entry(f"c{i}", result_size=2000) for i in range(10)]
    _write_raw_entries(tmp_path, entries)
    before_size = _log_path(tmp_path).stat().st_size

    # max_size_mb tiny enough (in bytes-equivalent) that the *current*
    # file is already "over" it, forcing compaction to run.
    max_size_mb = before_size / 1_000_000 * 0.5
    result = compact(max_size_mb=max_size_mb)

    assert result.before_bytes == before_size
    assert result.after_bytes < before_size

    entries_after = _read_entries(tmp_path)
    assert len(entries_after) == 10  # no whole entries dropped
    # Oldest entries (front of the list) are the ones stripped first.
    stripped = [e["call_id"] for e in entries_after if e["result"] is None]
    kept = [e["call_id"] for e in entries_after if e["result"] is not None]
    assert stripped == [f"c{i}" for i in range(len(stripped))]
    assert kept == [f"c{i}" for i in range(len(stripped), 10)]
    for e in entries_after:
        if e["result"] is None:
            assert e["result_omitted_reason"] == "compacted"


# --- AC2: atomic — a failed write leaves the original untouched -------------

def test_ac2_compact_atomic_rename_leaves_original_untouched_on_failure(tmp_path, monkeypatch):
    from sasha.tool_log import compact
    import sasha.tool_log as tool_log_module

    entries = [_make_entry(f"c{i}", result_size=2000) for i in range(5)]
    _write_raw_entries(tmp_path, entries)
    original_text = _log_path(tmp_path).read_text()

    monkeypatch.setattr(tool_log_module.os, "replace",
                         lambda *a, **k: (_ for _ in ()).throw(OSError("boom")))

    result = compact(max_size_mb=0.0001)

    assert _log_path(tmp_path).read_text() == original_text
    assert result.after_bytes == result.before_bytes


# --- AC3: a corrupt trailing line is skipped, not fatal ---------------------

def test_ac3_compact_skips_corrupt_lines(tmp_path):
    from sasha.tool_log import compact

    entries = [_make_entry(f"c{i}", result_size=2000) for i in range(5)]
    path = _log_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "".join(json.dumps(e) + "\n" for e in entries)
    text += '{"call_id": "truncated", "resu'  # corrupt trailing line
    path.write_text(text)

    result = compact(max_size_mb=0.0001)  # must not raise

    entries_after = _read_entries(tmp_path)
    assert len(entries_after) == 5
    assert result.before_bytes > 0


# --- AC4: still too big even after stripping everything ---------------------

def test_ac4_compact_reports_still_too_big_when_stripping_isnt_enough(tmp_path):
    from sasha.tool_log import compact

    entries = [_make_entry(f"c{i}", result_size=50000) for i in range(20)]
    _write_raw_entries(tmp_path, entries)

    result = compact(max_size_mb=0.00001)  # impossibly small

    assert result.still_too_big is True
    entries_after = _read_entries(tmp_path)
    assert all(e["result"] is None for e in entries_after)


# --- AC5: configure() changes the threshold ---------------------------------

def test_ac5_configure_changes_threshold(tmp_path):
    from sasha.tool_log import configure, check_and_maybe_compact_async

    entries = [_make_entry(f"c{i}", result_size=2000) for i in range(5)]
    _write_raw_entries(tmp_path, entries)
    size = _log_path(tmp_path).stat().st_size

    configure(max_size_mb=size / 1_000_000 * 100)  # huge — under threshold
    assert check_and_maybe_compact_async(lambda s: None) is None

    configure(max_size_mb=size / 1_000_000 * 0.1)  # tiny — over threshold
    thread = check_and_maybe_compact_async(lambda s: None)
    assert thread is not None
    thread.join(timeout=5)


# --- AC6: no-op under threshold ----------------------------------------------

def test_ac6_check_and_maybe_compact_noop_when_under_threshold(tmp_path):
    from sasha.tool_log import configure, check_and_maybe_compact_async

    entries = [_make_entry(f"c{i}", result_size=10) for i in range(2)]
    _write_raw_entries(tmp_path, entries)
    size = _log_path(tmp_path).stat().st_size

    configure(max_size_mb=size / 1_000_000 * 1000)
    calls = []
    thread = check_and_maybe_compact_async(lambda s: calls.append(s))
    assert thread is None
    assert calls == []
    assert _read_entries(tmp_path) == entries


# --- AC7: triggers and warns when still too big ------------------------------

def test_ac7_check_and_maybe_compact_triggers_and_warns_when_still_too_big(tmp_path):
    from sasha.tool_log import configure, check_and_maybe_compact_async

    entries = [_make_entry(f"c{i}", result_size=50000) for i in range(20)]
    _write_raw_entries(tmp_path, entries)

    configure(max_size_mb=0.00001)
    calls = []
    thread = check_and_maybe_compact_async(lambda s: calls.append(s))
    assert thread is not None
    thread.join(timeout=10)

    assert len(calls) == 1
    assert "even with all results stripped" in calls[0] and "MB" in calls[0]


# --- AC8: concurrent compaction is serialized by the lock -------------------

def test_ac8_concurrent_compaction_is_serialized_by_lock(tmp_path):
    import sasha.tool_log as tool_log_module

    entries = [_make_entry(f"c{i}", result_size=100) for i in range(3)]
    _write_raw_entries(tmp_path, entries)

    acquired_by_test = tool_log_module._compaction_lock.acquire()
    assert acquired_by_test

    finished = threading.Event()

    def _run():
        tool_log_module.compact(max_size_mb=0.0001)
        finished.set()

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    time.sleep(0.2)
    assert not finished.is_set(), "compact() must block while the lock is held"

    tool_log_module._compaction_lock.release()
    finished.wait(timeout=5)
    assert finished.is_set()


# --- AC9: [tool_log].max_size_mb config parsing ------------------------------

@pytest.fixture
def isolated_env(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    return tmp_path


def test_ac9_config_default_and_fallback(isolated_env, tmp_path):
    from sasha.config import load

    config = load()
    assert config.tool_log_max_size_mb == 100
    written = (isolated_env / "xdg-config" / "sasha" / "config.toml").read_text()
    assert "[tool_log]" in written
    assert "max_size_mb = 100" in written

    p = tmp_path / "override.toml"
    p.write_text('default_profile = "llm7"\n[profiles.llm7]\n'
                 'base_url = "https://api.llm7.io/v1"\nmodel = "gpt-oss:20b"\n'
                 '[tool_log]\nmax_size_mb = 250\n')
    assert load(p).tool_log_max_size_mb == 250

    p2 = tmp_path / "legacy.toml"
    p2.write_text('default_profile = "llm7"\n[profiles.llm7]\n'
                  'base_url = "https://api.llm7.io/v1"\nmodel = "gpt-oss:20b"\n')
    assert load(p2).tool_log_max_size_mb == 100

    p3 = tmp_path / "bool_value.toml"
    p3.write_text('default_profile = "llm7"\n[profiles.llm7]\n'
                  'base_url = "https://api.llm7.io/v1"\nmodel = "gpt-oss:20b"\n'
                  '[tool_log]\nmax_size_mb = true\n')
    assert load(p3).tool_log_max_size_mb == 100


# --- AC10: agent.py dispatch calls check_and_maybe_compact_async ------------

class SpyExecutor:
    def __init__(self, cwd):
        from sasha.executor import ExecResult

        self.cwd = cwd
        self.sensitive_path_patterns = ()
        self._result = ExecResult(exit_code=0, signal=None, stderr_text="",
                                  stdout_text="OUT", cwd_after=cwd, duration_ms=1)

    def run(self, command, capture_stdout=False):
        return self._result


class ScriptedConfirmer:
    def __init__(self, verdicts):
        self.verdicts = list(verdicts)

    def ask(self, summary, description):
        return self.verdicts.pop(0)


def _tc(name, arguments, call_id="call_1"):
    return {"kind": "tool_call", "id": call_id, "name": name,
            "arguments": arguments, "fragments": 2}


def test_ac10_dispatch_calls_check_and_maybe_compact(monkeypatch):
    from sasha import agent as agent_module
    from sasha.config import Profile
    from sasha.llm import ChatClient
    from sasha.session import Session

    calls = []
    monkeypatch.setattr(agent_module.tool_log, "log_call", lambda **kw: None)
    monkeypatch.setattr(agent_module.tool_log, "check_and_maybe_compact_async",
                         lambda out: calls.append(out))

    session = Session()
    session.add_user("help me")
    executor = SpyExecutor("/spy")
    confirmer = ScriptedConfirmer([])
    with StubServer([_tc("run_command", {"command": "ls -la", "description": "d"}),
                     {"kind": "text", "text": "done"}]) as stub:
        client = ChatClient(Profile(name="stub", base_url=stub.base_url,
                                    model="stub-model", api_key=""), timeout_s=10.0)
        agent_module.run_consult(session, client, executor, confirmer, out=lambda s: None)

    assert len(calls) == 1


# --- AC11/AC12: repl.py 'sasha compact-log' builtin -------------------------

class _FakeSink:
    def __init__(self):
        self.lines = []
        self.color_enabled = False

    def write_line(self, text):
        self.lines.append(text)

    def write_chunk(self, text):
        pass

    def ensure_newline(self):
        pass


class _FakeConfirmer:
    def ask(self, command, description):
        return ("no", "")


class _FakeMutes:
    def is_muted(self, command, exit_code):
        return False

    def muted_commands(self):
        return []


def _make_repl(tmp_path):
    from sasha.config import Config, Limits, Profile
    from sasha.executor import Executor
    from sasha.repl import Repl
    from sasha.session import Session

    stub_profile = Profile(name="stub", base_url="http://stub", model="stub", api_key="")
    config = Config(default_profile="stub", profiles={"stub": stub_profile},
                     limits=Limits())
    executor = Executor(str(tmp_path), {})
    session = Session()
    return Repl(config=config, executor=executor, mutes=_FakeMutes(),
                session=session, client_factory=lambda p: None,
                confirmer=_FakeConfirmer(), input_fn=lambda p: "",
                sink=_FakeSink())


def test_ac11_repl_compact_log_builtin_reports_size(tmp_path, monkeypatch):
    import sasha.tool_log as tool_log_module

    entries = [_make_entry(f"c{i}", result_size=2000) for i in range(10)]
    _write_raw_entries(tmp_path, entries)
    tool_log_module.configure(max_size_mb=_log_path(tmp_path).stat().st_size
                               / 1_000_000 * 0.5)

    repl = _make_repl(tmp_path)
    repl._handle_builtin("sasha compact-log")

    joined = "\n".join(repl._sink.lines)
    assert "compacted" in joined
    assert "MB" in joined


def test_ac12_help_mentions_compact_log(tmp_path):
    repl = _make_repl(tmp_path)
    repl._handle_builtin("sasha help")
    joined = "\n".join(repl._sink.lines)
    assert "sasha compact-log" in joined
