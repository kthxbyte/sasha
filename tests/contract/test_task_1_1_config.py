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
    # Amended by Tier 1 (2026-10-12): shipped default port corrected
    # 11441 -> 11434 to match the llama.cpp server in use.
    assert lo.base_url == "http://192.168.8.253:11434/v1"
    assert lo.model == "local"
    assert lo.api_key == ""


def test_ac1_default_limits(isolated_env):
    from sasha.config import load

    lim = load(None).limits
    assert lim.history_tokens == 12000
    assert lim.stderr_capture_bytes == 8192
    # Amended by Tier 1 (2026-10-12, ISSUE-073): 10 -> 50. Ten was too few to
    # finish real work (read files, search, edit, run tests, fix, re-run easily
    # passes 20 actions), so it interrupted tasks instead of protecting them.
    # The loop it was meant to stop is now caught by the repetition guard in
    # llm.py, which watches the streamed text rather than counting turns.
    # Amended by Tier 1 (2026-10-12, ISSUE-080): 50 -> 0, meaning NO LIMIT.
    # Salvador's verdict from use is that it never served the purpose it was
    # added for: the runaway it aimed at was *reasoning* (no tool turns at all,
    # now bounded by max_think_seconds), while the ceiling interrupted legitimate
    # long work. A repeated-action guard covers the tool-call loop. The ceiling
    # remains available as an opt-in.
    assert lim.max_tool_turns == 0
    # Amended by Tier 1 (2026-10-12): `mute_threshold` was removed with the
    # trigger-economy inversion (ISSUE-067) — there are no offers to mute.
    assert not hasattr(lim, "mute_threshold")


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
# Amended by Tier 1 (2026-10-12): version bumped to 0.6.0 for a session of
# local-model hardening and simplification (request gate, crash safety net,
# `sasha repair`, compact reasoning display, single-sourced version, and the
# deletion of the offer/mute economy and of directory recap).
# Amended by Tier 1 (2026-10-12): version bumped to 0.7.0, which is the first
# TAGGED release of the session -- 0.6.0 was a marker commit and was tagged
# retroactively. Since 0.6.0: string/bytes hardening, the edit_file tool, the
# task-survives-trimming fix, runaway-response bounds, and the deletion of
# memory-bootstrap stage 2.

def test_ac5_version_flag_prints_version_and_exits_zero():
    r = subprocess.run(
        [sys.executable, "-m", "sasha", "--version"],
        capture_output=True, text=True, env=_subproc_env(), timeout=30,
    )
    assert r.returncode == 0
    assert r.stdout.strip() == "sasha 0.7.0"


def test_ac5_pyproject_declares_packaging():
    data = tomllib.loads((REPO / "pyproject.toml").read_text())
    proj = data["project"]
    assert proj["name"] == "sasha"
    # Amended by Tier 1 (2026-10-12): the version was a literal here AND in
    # __main__.py AND in sasha/__init__.py -- three copies free to drift, with
    # `sasha --version` printing the hardcoded one. pyproject now declares
    # `dynamic = ["version"]` sourced from sasha.__version__, so this asserts
    # the single-source declaration rather than a duplicated string. The
    # user-visible `sasha --version` output is still pinned by
    # test_ac5_version_is_reported above.
    assert proj["dynamic"] == ["version"]
    assert "version" not in proj
    assert (data["tool"]["setuptools"]["dynamic"]["version"]["attr"]
            == "sasha.__version__")
    assert "3.11" in proj["requires-python"]
    assert not proj.get("dependencies")  # stdlib-only runtime
    assert "sasha" in proj["scripts"]
    assert "sasha.__main__" in proj["scripts"]["sasha"]


def test_ac5_version_has_one_source_of_truth():
    """`--version` must be DERIVED from `__version__`, not a second literal.

    Amended by Tier 1 (2026-10-12): this test previously asserted the literal
    "0.5.0" twice, which duplicated test_ac5_version_flag_prints_version_and_exits_zero
    and would have needed editing on every bump for no extra coverage. It now
    pins the invariant that actually matters and is bump-proof: whatever
    `__version__` says is exactly what `--version` prints. The concrete number
    is still pinned, deliberately, by the two subprocess tests above and in
    test_task_3_2_entry.py.
    """
    import io
    import contextlib

    import sasha
    from sasha.__main__ import main

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        assert main(["--version"]) == 0
    assert buf.getvalue().strip() == f"sasha {sasha.__version__}"


def test_ac5_package_version():
    import sasha

    assert sasha.__version__ == "0.7.0"
