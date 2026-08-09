"""Contract tests — Task 1.1: package skeleton + config module.

Written by Tier 1 at spec time; immutable to the implementer.
Spec: docs/superpowers/specs/task-1.1-config.md (design §5.7, §8.1).
"""

import dataclasses
import os
import pathlib
import subprocess
import sys
import tomllib

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]


@pytest.fixture
def isolated_env(monkeypatch, tmp_path):
    """No real user config, no ambient API keys."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("SASHA_TEST_KEY", raising=False)
    return tmp_path


# --- AC1: built-in defaults ------------------------------------------------

def test_ac1_defaults_when_no_config_file(isolated_env):
    from sasha.config import load

    cfg = load(None)
    # Default profile changed to "llm7" by Task 15.1 (ISSUE-026) — a free,
    # keyless, streaming-capable profile so Sasha works with zero setup.
    # "deepseek" remains a valid, fully-specified profile, just no longer
    # the default. See docs/superpowers/specs/2026-07-17-llm-setup-design.md.
    assert cfg.default_profile == "llm7"
    assert set(cfg.profiles) >= {"deepseek", "local"}

    ds = cfg.profiles["deepseek"]
    assert ds.name == "deepseek"
    assert ds.base_url == "https://api.deepseek.com/v1"
    assert ds.model == "deepseek-chat"
    assert ds.api_key == ""  # DEEPSEEK_API_KEY deleted by fixture

    lo = cfg.profiles["local"]
    assert lo.name == "local"
    assert lo.base_url == "http://192.168.8.253:11441/v1"
    assert lo.model == "local"
    assert lo.api_key == ""


def test_ac1_default_limits(isolated_env):
    from sasha.config import load

    lim = load(None).limits
    assert lim.history_tokens == 12000
    assert lim.stderr_capture_bytes == 8192
    assert lim.max_tool_turns == 10
    assert lim.mute_threshold == 3


def test_ac1_profile_dataclass_frozen(isolated_env):
    from sasha.config import load

    ds = load(None).profiles["deepseek"]
    with pytest.raises(dataclasses.FrozenInstanceError):
        ds.model = "other"


# --- AC2: TOML overrides merge into defaults --------------------------------

CFG_OVERRIDE = """
default_profile = "local"
unknown_top_level = 1

[profiles.deepseek]
model = "deepseek-reasoner"
unknown_key = "ignored"

[profiles.extra]
base_url = "http://example.test/v1"
model = "extra-model"

[limits]
history_tokens = 5000
unknown_limit = 9
"""


def test_ac2_toml_overrides_merge_into_defaults(isolated_env, tmp_path):
    from sasha.config import load

    p = tmp_path / "config.toml"
    p.write_text(CFG_OVERRIDE)
    cfg = load(p)

    assert cfg.default_profile == "local"
    # field-level merge: model overridden, base_url kept from defaults
    assert cfg.profiles["deepseek"].model == "deepseek-reasoner"
    assert cfg.profiles["deepseek"].base_url == "https://api.deepseek.com/v1"
    # untouched default profile survives
    assert cfg.profiles["local"].model == "local"
    # new profile added
    assert cfg.profiles["extra"].base_url == "http://example.test/v1"
    assert cfg.profiles["extra"].model == "extra-model"
    # limits merge per key
    assert cfg.limits.history_tokens == 5000
    assert cfg.limits.stderr_capture_bytes == 8192


def test_ac2_default_location_is_xdg(isolated_env, tmp_path):
    from sasha.config import load

    d = tmp_path / "xdg" / "sasha"
    d.mkdir(parents=True)
    (d / "config.toml").write_text('default_profile = "local"\n')
    assert load(None).default_profile == "local"


# --- AC3: api_key resolution order ------------------------------------------

KEYS_CFG = """
[profiles.deepseek]
api_key_env = "SASHA_TEST_KEY"
api_key = "literal-fallback"
"""


def test_ac3_env_var_wins(isolated_env, tmp_path, monkeypatch):
    from sasha.config import load

    monkeypatch.setenv("SASHA_TEST_KEY", "from-env")
    p = tmp_path / "c.toml"
    p.write_text(KEYS_CFG)
    assert load(p).profiles["deepseek"].api_key == "from-env"


def test_ac3_literal_fallback_when_env_unset(isolated_env, tmp_path):
    from sasha.config import load

    p = tmp_path / "c.toml"
    p.write_text(KEYS_CFG)
    assert load(p).profiles["deepseek"].api_key == "literal-fallback"


def test_ac3_literal_fallback_when_env_empty(isolated_env, tmp_path, monkeypatch):
    from sasha.config import load

    monkeypatch.setenv("SASHA_TEST_KEY", "")
    p = tmp_path / "c.toml"
    p.write_text(KEYS_CFG)
    assert load(p).profiles["deepseek"].api_key == "literal-fallback"


def test_ac3_empty_when_neither(isolated_env, tmp_path):
    from sasha.config import load

    p = tmp_path / "c.toml"
    p.write_text(
        '[profiles.extra]\nbase_url = "http://x/v1"\nmodel = "m"\n'
    )
    assert load(p).profiles["extra"].api_key == ""


# --- AC4: ConfigError vocabulary ---------------------------------------------

def test_ac4_malformed_toml_raises_configerror(isolated_env, tmp_path):
    from sasha.config import ConfigError, load

    p = tmp_path / "bad.toml"
    p.write_text("default_profile = ???\n")
    with pytest.raises(ConfigError) as exc:
        load(p)
    assert str(exc.value).strip()


def test_ac4_missing_explicit_path_raises(isolated_env, tmp_path):
    from sasha.config import ConfigError, load

    with pytest.raises(ConfigError):
        load(tmp_path / "nope" / "config.toml")


def test_ac4_unknown_default_profile_raises(isolated_env, tmp_path):
    from sasha.config import ConfigError, load

    p = tmp_path / "c.toml"
    p.write_text('default_profile = "no-such-profile"\n')
    with pytest.raises(ConfigError):
        load(p)


# --- AC5: packaging ----------------------------------------------------------

def _subproc_env():
    env = dict(os.environ)
    src = str(REPO / "src")
    env["PYTHONPATH"] = src + (
        os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else ""
    )
    return env


# Amended by Tier 1 (2026-07-17): version bumped to 0.2.0 to mark the
# Phase 16 (multi-tool registry) milestone.
# Amended by Tier 1 (2026-07-18): version bumped to 0.3.0 to mark the
# Phase 17-19 milestone (project memory, general memory + crash trigger,
# memory bootstrap + busy-star indicator).
# Amended by Tier 1 (2026-08-08): version bumped to 0.5.0 for the v0.5.0
# tag, marking everything shipped since v0.4.0 (Phases 51-61: paste
# burst-join, directory-recap cold-start fallback, the question tool,
# split stop messages, sasha skill loading, bash-completion
# default-delegation fallback, YAML block-scalar frontmatter parsing).

def test_ac5_version_flag_prints_version_and_exits_zero():
    r = subprocess.run(
        [sys.executable, "-m", "sasha", "--version"],
        capture_output=True, text=True, env=_subproc_env(), timeout=30,
    )
    assert r.returncode == 0
    assert r.stdout.strip() == "sasha 0.5.0"


def test_ac5_pyproject_declares_packaging():
    data = tomllib.loads((REPO / "pyproject.toml").read_text())
    proj = data["project"]
    assert proj["name"] == "sasha"
    assert proj["version"] == "0.5.0"
    assert "3.11" in proj["requires-python"]
    assert not proj.get("dependencies")  # stdlib-only runtime
    assert "sasha" in proj["scripts"]
    assert "sasha.__main__" in proj["scripts"]["sasha"]


def test_ac5_package_version():
    import sasha

    assert sasha.__version__ == "0.5.0"
