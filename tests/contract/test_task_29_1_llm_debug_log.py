"""Contract tests — Task 29.1: LLM request debug log (ISSUE-047 tooling).

Written by Tier 1 at spec time; immutable to the implementer.
Spec: docs/superpowers/specs/task-29.1-llm-debug-log.md
Design: docs/superpowers/specs/2026-07-22-issue-047-llm-debug-log-design.md
"""

from __future__ import annotations

import json
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from sse_stub import StubServer  # noqa: E402


# --- AC1-AC4: llm_debug module, pure unit tests -----------------------------

def test_ac1_disabled_writes_nothing():
    from sasha import llm_debug

    llm_debug.configure(enabled=False)
    llm_debug.log_request(profile="deepseek", model="deepseek-chat",
                           messages=[{"role": "user", "content": "hi"}],
                           tools=[])
    assert not llm_debug._log_path().exists()


def test_ac2_enabled_appends_one_line_with_correct_schema():
    from sasha import llm_debug

    llm_debug.configure(enabled=True)
    try:
        messages = [{"role": "system", "content": "sys"},
                    {"role": "user", "content": "hi"}]
        llm_debug.log_request(profile="deepseek", model="deepseek-chat",
                               messages=messages, tools=[])
        path = llm_debug._log_path()
        lines = path.read_text().splitlines()
        assert len(lines) == 1
        entry = json.loads(lines[0])
        assert entry["profile"] == "deepseek"
        assert entry["model"] == "deepseek-chat"
        assert entry["messages"] == messages
        assert entry["tools"] == []
        assert "ts" in entry and "session_id" in entry
    finally:
        llm_debug.configure(enabled=False)


def test_ac3_tools_field_is_empty_list_not_omitted_when_no_tools():
    from sasha import llm_debug

    llm_debug.configure(enabled=True)
    try:
        llm_debug.log_request(profile="p", model="m",
                               messages=[{"role": "user", "content": "x"}],
                               tools=[])
        entry = json.loads(llm_debug._log_path().read_text().splitlines()[-1])
        assert entry["tools"] == []
    finally:
        llm_debug.configure(enabled=False)


def test_ac4_write_failure_warns_once_and_never_raises(monkeypatch, capsys):
    from sasha import llm_debug

    llm_debug.configure(enabled=True)
    try:
        def _bad_log_path():
            return pathlib.Path("/nonexistent-root-only-dir/llm_debug.jsonl")

        monkeypatch.setattr(llm_debug, "_log_path", _bad_log_path)

        llm_debug.log_request(profile="p", model="m", messages=[], tools=[])
        llm_debug.log_request(profile="p", model="m", messages=[], tools=[])

        stderr = capsys.readouterr().err
        warning_lines = [line for line in stderr.splitlines() if line]
        assert len(warning_lines) == 1
    finally:
        llm_debug.configure(enabled=False)
        llm_debug._warned = False


# --- AC5: config default + fallback ------------------------------------------

@pytest.fixture
def isolated_env(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    return tmp_path


def test_ac5_config_default_and_fallback(isolated_env, tmp_path):
    from sasha.config import load

    config = load()
    assert config.llm_debug_enabled is False
    written = (isolated_env / "xdg-config" / "sasha" / "config.toml").read_text()
    assert "[llm_debug]" in written

    p = tmp_path / "override.toml"
    p.write_text('default_profile = "llm7"\n[profiles.llm7]\n'
                 'base_url = "https://api.llm7.io/v1"\nmodel = "gpt-oss:20b"\n'
                 '[llm_debug]\nenabled = true\n')
    overridden = load(p)
    assert overridden.llm_debug_enabled is True

    p2 = tmp_path / "bad_types.toml"
    p2.write_text('default_profile = "llm7"\n[profiles.llm7]\n'
                  'base_url = "https://api.llm7.io/v1"\nmodel = "gpt-oss:20b"\n'
                  '[llm_debug]\nenabled = "yes"\n')
    bad = load(p2)
    assert bad.llm_debug_enabled is False


# --- AC6: end-to-end via ChatClient.chat() -----------------------------------

def test_ac6_chat_client_logs_the_exact_outbound_request():
    from sasha import llm_debug
    from sasha.config import Profile
    from sasha.llm import ChatClient

    llm_debug.configure(enabled=True)
    try:
        with StubServer([{"kind": "text", "text": "hi"}]) as stub:
            profile = Profile(name="stubprof", base_url=stub.base_url,
                               model="stub-model", api_key="")
            client = ChatClient(profile, timeout_s=10.0)
            messages = [{"role": "system", "content": "sys"},
                        {"role": "user", "content": "hello"}]
            client.chat(messages, [], lambda chunk: None)

            entry = json.loads(llm_debug._log_path().read_text().splitlines()[-1])
            assert entry["profile"] == "stubprof"
            assert entry["model"] == "stub-model"
            assert entry["messages"] == messages
            assert entry["tools"] == []
            assert stub.requests[-1]["body"]["messages"] == messages
    finally:
        llm_debug.configure(enabled=False)
