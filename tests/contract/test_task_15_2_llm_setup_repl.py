"""Contract tests — Task 15.2: repl.py + __main__.py masked key-entry flow
on 'sasha profile <name>' (ISSUE-026).

Written by Tier 1 at spec time; immutable to the implementer.
Spec: docs/superpowers/specs/task-15.2-llm-setup-repl.md
Design: docs/superpowers/specs/2026-07-17-llm-setup-design.md
"""

import os
import pathlib
import subprocess
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]


class _SpySink:
    color_enabled = False

    def __init__(self, events):
        self._events = events

    def write_line(self, text):
        self._events.append(text)

    def write_chunk(self, text):
        if text:
            self._events.append(text)

    def ensure_newline(self):
        pass


def _repl(profiles, default, getpass_fn, path=None, notice=None):
    from sasha.config import Config, Limits
    from sasha.repl import Repl

    events = []
    cfg = Config(default_profile=default, profiles=profiles, limits=Limits(),
                 path=path, notice=notice)
    sink = _SpySink(events)
    repl = Repl(cfg, None, None, None, None, None, lambda prompt: "", sink,
                getpass_fn=getpass_fn)
    return repl, events


def _profile(name, base_url="http://x/v1", model="m", api_key="",
             api_key_required=True):
    from sasha.config import Profile
    return Profile(name=name, base_url=base_url, model=model,
                    api_key=api_key, api_key_required=api_key_required)


# --- AC1: keyless-by-design profile switches immediately ---------------------

def test_ac1_keyless_profile_switches_immediately_empty_key():
    calls = []
    keyless = _profile("llm7", api_key="", api_key_required=False)
    repl, events = _repl({"llm7": keyless}, "llm7",
                          getpass_fn=lambda p: calls.append(p) or "SHOULD-NOT-BE-USED")

    repl.run_once("sasha profile llm7")

    assert repl._current_profile is keyless
    assert events == ["[sasha] profile: llm7 (m @ http://x/v1)"]
    assert calls == []


# --- AC2: already-keyed profile switches immediately regardless of flag ------

def test_ac2_already_keyed_profile_switches_immediately():
    calls = []
    keyed = _profile("openai", api_key="sk-existing", api_key_required=True)
    repl, events = _repl({"openai": keyed}, "openai",
                          getpass_fn=lambda p: calls.append(p) or "SHOULD-NOT-BE-USED")

    repl.run_once("sasha profile openai")

    assert repl._current_profile is keyed
    assert events == ["[sasha] profile: openai (m @ http://x/v1)"]
    assert calls == []


# --- AC3: empty-key + required, cancel on empty getpass input ----------------

def test_ac3_empty_getpass_input_cancels(monkeypatch):
    import sasha.config as config_module

    set_key_calls = []
    monkeypatch.setattr(config_module, "set_api_key",
                         lambda *a, **k: set_key_calls.append((a, k)))

    needs_key = _profile("openai", api_key="", api_key_required=True)
    repl, events = _repl({"openai": needs_key}, "openai",
                          getpass_fn=lambda p: "   ",
                          path=pathlib.Path("/fake/config.toml"))

    repl.run_once("sasha profile openai")

    assert repl._current_profile is needs_key  # unchanged
    assert any("cancelled" in e for e in events)
    assert set_key_calls == []


# --- AC4: real key entered — persisted, switched, never echoed ---------------

def test_ac4_real_key_persists_and_switches(monkeypatch):
    import sasha.config as config_module

    set_key_calls = []
    monkeypatch.setattr(
        config_module, "set_api_key",
        lambda path, name, key: set_key_calls.append((path, name, key)))

    needs_key = _profile("openai", api_key="", api_key_required=True)
    fake_path = pathlib.Path("/fake/config.toml")
    repl, events = _repl({"openai": needs_key}, "openai",
                          getpass_fn=lambda p: "sk-pasted-secret",
                          path=fake_path)

    repl.run_once("sasha profile openai")

    assert set_key_calls == [(fake_path, "openai", "sk-pasted-secret")]
    assert repl._current_profile.name == "openai"
    assert repl._current_profile.api_key == "sk-pasted-secret"
    assert repl._current_profile.base_url == needs_key.base_url
    assert repl._current_profile.model == needs_key.model
    assert "[sasha] profile: openai (m @ http://x/v1)" in events
    assert not any("sk-pasted-secret" in e for e in events)


# --- AC5: set_api_key raises ConfigError — fails closed -----------------------

def test_ac5_set_api_key_failure_fails_closed(monkeypatch):
    import sasha.config as config_module

    def _raise(*a, **k):
        raise config_module.ConfigError("boom")

    monkeypatch.setattr(config_module, "set_api_key", _raise)

    needs_key = _profile("openai", api_key="", api_key_required=True)
    repl, events = _repl({"openai": needs_key}, "openai",
                          getpass_fn=lambda p: "sk-pasted-secret",
                          path=pathlib.Path("/fake/config.toml"))

    repl.run_once("sasha profile openai")

    assert repl._current_profile is needs_key  # unchanged
    joined = " ".join(events)
    assert "boom" in joined
    assert "profile unchanged" in joined
    assert not any(e.startswith("[sasha] profile: openai") for e in events)


# --- AC6: no config file on disk (path is None) -------------------------------

def test_ac6_no_config_path_reports_and_does_not_prompt():
    calls = []
    needs_key = _profile("openai", api_key="", api_key_required=True)
    repl, events = _repl({"openai": needs_key}, "openai",
                          getpass_fn=lambda p: calls.append(p) or "sk-x",
                          path=None)

    repl.run_once("sasha profile openai")

    assert repl._current_profile is needs_key  # unchanged
    assert calls == []
    joined = " ".join(events)
    assert "no config file" in joined


# --- AC8: boot notice printed by __main__.py (subprocess, real first run) ----

@pytest.fixture
def env_home(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    env = dict(os.environ)
    env["HOME"] = str(home)
    env["XDG_CONFIG_HOME"] = str(tmp_path / "xdg-config")
    env["XDG_STATE_HOME"] = str(tmp_path / "xdg-state")
    env.pop("DEEPSEEK_API_KEY", None)
    src = str(REPO / "src")
    env["PYTHONPATH"] = src + (
        os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    return home, env


def _run(env, args=(), stdin="", cwd=None, timeout=30):
    return subprocess.run(
        [sys.executable, "-m", "sasha", *args],
        input=stdin, capture_output=True, text=True,
        env=env, cwd=cwd, timeout=timeout)


def test_ac8_first_run_prints_config_notice(env_home):
    home, env = env_home
    r = _run(env, stdin="exit\n", cwd=str(home))
    assert r.returncode == 0
    # Substring pinned to config.py's exact notice wording, not the generic
    # "[sasha] created " prefix — personality.py's own first-run notice
    # ALSO starts with "[sasha] created ", so a looser assertion here would
    # false-pass even if __main__.py never prints config.notice at all.
    assert "using the free 'llm7' tier" in r.stdout

    cfg_path = pathlib.Path(env["XDG_CONFIG_HOME"]) / "sasha" / "config.toml"
    assert cfg_path.exists()


def test_ac8_second_run_no_notice(env_home):
    home, env = env_home
    _run(env, stdin="exit\n", cwd=str(home))  # first run creates the file

    r = _run(env, stdin="exit\n", cwd=str(home))
    assert r.returncode == 0
    assert "using the free 'llm7' tier" not in r.stdout
