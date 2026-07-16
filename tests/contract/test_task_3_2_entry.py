"""Contract tests — Task 3.2: entry point & terminal wiring.

Written by Tier 1 at spec time; immutable to the implementer.
Spec: docs/superpowers/specs/task-3.2-entry.md (design §9.5, §10, §8.3).
Subprocess-driven against the real `python -m sasha`.
"""

import os
import pathlib
import subprocess
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]


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


# --- AC1 ---------------------------------------------------------------------

def test_ac1_version(env_home):
    home, env = env_home
    r = _run(env, ["--version"])
    assert r.returncode == 0
    assert r.stdout.strip() == "sasha 0.1.0"


# --- AC2 -----------------------------------------------------------------------

def test_ac2_bad_config(env_home, tmp_path):
    home, env = env_home
    bad = tmp_path / "bad.toml"
    bad.write_text("default_profile = ???\n")
    r = _run(env, ["--config", str(bad)])
    assert r.returncode == 2
    assert r.stderr.startswith("sasha: config error: ")


# --- AC3 -------------------------------------------------------------------------

def test_ac3_profile_flag_selects_profile(env_home):
    home, env = env_home
    r = _run(env, ["--profile", "local"], stdin="sasha profile\nexit\n")
    assert r.returncode == 0
    assert "[sasha] profile: local (" in r.stdout


def test_ac3_unknown_profile_is_config_error(env_home):
    home, env = env_home
    r = _run(env, ["--profile", "does-not-exist"])
    assert r.returncode == 2
    assert r.stderr.startswith("sasha: config error: ")
    assert "does-not-exist" in r.stderr


# --- AC4 ---------------------------------------------------------------------------

def test_ac4_prompt(env_home):
    home, env = env_home
    r = _run(env, stdin="exit\n", cwd=str(home))
    assert r.returncode == 0
    assert "~✦ " in r.stdout


# --- AC5 -----------------------------------------------------------------------------

def test_ac5_history_written(env_home, tmp_path):
    home, env = env_home
    r = _run(env, stdin="true\nexit\n", cwd=str(home))
    assert r.returncode == 0
    hist = tmp_path / "xdg-state" / "sasha" / "history"
    assert hist.exists()
    assert "true" in hist.read_text()


# --- AC6 -------------------------------------------------------------------------------

def test_ac6_eof_exits_zero(env_home):
    home, env = env_home
    r = _run(env, stdin="", cwd=str(home))
    assert r.returncode == 0
