"""Contract tests — Task 15.1: config.py self-install, api_key_required,
set_api_key patcher (ISSUE-026).

Written by Tier 1 at spec time; immutable to the implementer.
Spec: docs/superpowers/specs/task-15.1-llm-setup-config.md
Design: docs/superpowers/specs/2026-07-17-llm-setup-design.md
"""

import tomllib

import pytest


@pytest.fixture
def isolated_env(monkeypatch, tmp_path):
    """No real user config, no ambient API keys."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    return tmp_path


# --- AC1: self-install on first run -----------------------------------------

def test_ac1_writes_default_config_toml(isolated_env):
    from sasha.config import _DEFAULT_CONFIG_TOML, _default_config_path, load

    load(None)

    path = _default_config_path()
    assert path.read_text() == _DEFAULT_CONFIG_TOML


def test_ac1_returns_llm7_default_profile(isolated_env):
    from sasha.config import _default_config_path, load

    cfg = load(None)

    assert cfg.default_profile == "llm7"
    assert cfg.path == _default_config_path()
    assert cfg.notice is not None
    assert str(cfg.path) in cfg.notice
    assert "llm7" in cfg.notice


def test_ac1_all_four_profiles_present_with_correct_fields(isolated_env):
    from sasha.config import load

    cfg = load(None)

    llm7 = cfg.profiles["llm7"]
    assert llm7.base_url == "https://api.llm7.io/v1"
    assert llm7.model == "gpt-oss:20b"
    assert llm7.api_key_required is False

    openai = cfg.profiles["openai"]
    assert openai.base_url == "https://api.openai.com/v1"
    assert openai.model == "gpt-4o-mini"
    assert openai.api_key_required is True

    deepseek = cfg.profiles["deepseek"]
    assert deepseek.base_url == "https://api.deepseek.com/v1"
    assert deepseek.model == "deepseek-chat"
    assert deepseek.api_key_required is True

    local = cfg.profiles["local"]
    assert local.base_url == "http://192.168.8.253:11441/v1"
    assert local.model == "local"
    assert local.api_key_required is False


# --- AC2: explicit non-default missing path still raises --------------------

def test_ac2_missing_explicit_path_raises_and_writes_nothing(isolated_env, tmp_path):
    from sasha.config import ConfigError, load

    target = tmp_path / "nope" / "config.toml"
    with pytest.raises(ConfigError):
        load(target)

    assert not target.exists()
    assert not target.parent.exists()


# --- AC3: existing file is never overwritten --------------------------------

def test_ac3_existing_file_not_overwritten(isolated_env):
    from sasha.config import _default_config_path, load

    path = _default_config_path()
    path.parent.mkdir(parents=True)
    path.write_text('default_profile = "local"\n[profiles.local]\n'
                     'base_url = "http://x/v1"\nmodel = "m"\n')
    original = path.read_text()

    cfg = load(None)

    assert path.read_text() == original
    assert cfg.notice is None
    assert cfg.path == path


# --- AC4: self-install write failure degrades gracefully --------------------

def test_ac4_unwritable_parent_falls_back_gracefully(isolated_env, tmp_path):
    from sasha.config import load

    xdg = tmp_path / "xdg"
    xdg.mkdir(mode=0o555)
    try:
        cfg = load(None)
    finally:
        xdg.chmod(0o755)

    assert cfg.default_profile == "llm7"
    assert set(cfg.profiles) >= {"llm7", "openai", "deepseek", "local"}
    assert cfg.path is None
    assert cfg.notice is not None


def test_ac4_failure_notice_distinct_from_success_notice(isolated_env, tmp_path):
    from sasha.config import load

    success_notice = load(None).notice

    xdg2 = tmp_path / "xdg2"
    xdg2.mkdir(mode=0o555)
    import os
    os.environ["XDG_CONFIG_HOME"] = str(xdg2)
    try:
        failure_cfg = load(None)
    finally:
        xdg2.chmod(0o755)

    assert failure_cfg.notice != success_notice


# --- AC5: api_key_required parsing -------------------------------------------

def test_ac5_explicit_false_honored(isolated_env, tmp_path):
    from sasha.config import load

    p = tmp_path / "c.toml"
    p.write_text('[profiles.openai]\napi_key_required = false\n')
    cfg = load(p)

    assert cfg.profiles["openai"].api_key_required is False


def test_ac5_explicit_true_honored(isolated_env, tmp_path):
    from sasha.config import load

    p = tmp_path / "c.toml"
    p.write_text('[profiles.llm7]\napi_key_required = true\n')
    cfg = load(p)

    assert cfg.profiles["llm7"].api_key_required is True


def test_ac5_omitted_defaults_true_for_new_profile(isolated_env, tmp_path):
    from sasha.config import load

    p = tmp_path / "c.toml"
    p.write_text('default_profile = "mine"\n[profiles.mine]\n'
                  'base_url = "http://x/v1"\nmodel = "m"\n')
    cfg = load(p)

    assert cfg.profiles["mine"].api_key_required is True


def test_ac5_non_bool_value_ignored_falls_back_to_default(isolated_env, tmp_path):
    from sasha.config import load

    p = tmp_path / "c.toml"
    p.write_text('[profiles.openai]\napi_key_required = "false"\n')
    cfg = load(p)

    assert cfg.profiles["openai"].api_key_required is True


# --- AC6a: insert api_key line when absent -----------------------------------

def test_ac6a_inserts_api_key_when_absent(tmp_path):
    from sasha.config import set_api_key

    p = tmp_path / "config.toml"
    p.write_text(
        'default_profile = "llm7"\n'
        '\n'
        '[profiles.llm7]\n'
        'base_url = "https://api.llm7.io/v1"\n'
        'model = "gpt-oss:20b"\n'
        '\n'
        '[profiles.openai]\n'
        'base_url = "https://api.openai.com/v1"\n'
        'model = "gpt-4o-mini"\n'
        'api_key_required = true\n'
    )

    set_api_key(p, "openai", "sk-abc123")

    text = p.read_text()
    parsed = tomllib.loads(text)
    assert parsed["profiles"]["openai"]["api_key"] == "sk-abc123"
    # every other line survives untouched
    assert 'base_url = "https://api.llm7.io/v1"' in text
    assert 'model = "gpt-oss:20b"' in text
    assert 'base_url = "https://api.openai.com/v1"' in text
    assert 'model = "gpt-4o-mini"' in text
    assert 'api_key_required = true' in text
    assert parsed["profiles"]["llm7"]["base_url"] == "https://api.llm7.io/v1"


# --- AC6b: replace existing api_key line only --------------------------------

def test_ac6b_replaces_existing_api_key_only(tmp_path):
    from sasha.config import set_api_key

    p = tmp_path / "config.toml"
    p.write_text(
        '[profiles.openai]\n'
        'base_url = "https://api.openai.com/v1"\n'
        'model = "gpt-4o-mini"\n'
        'api_key = "old-key"\n'
        'api_key_required = true\n'
    )

    set_api_key(p, "openai", "new-key")

    text = p.read_text()
    parsed = tomllib.loads(text)
    assert parsed["profiles"]["openai"]["api_key"] == "new-key"
    assert parsed["profiles"]["openai"]["api_key_required"] is True
    assert text.count("api_key_required = true") == 1
    assert "old-key" not in text


# --- AC6c: special characters escape/round-trip safely -----------------------

def test_ac6c_special_characters_round_trip(tmp_path):
    from sasha.config import set_api_key

    p = tmp_path / "config.toml"
    p.write_text('[profiles.openai]\nbase_url = "http://x/v1"\nmodel = "m"\n')

    tricky = 'sk-"quote"-and-\\backslash\\-end'
    set_api_key(p, "openai", tricky)

    parsed = tomllib.loads(p.read_text())
    assert parsed["profiles"]["openai"]["api_key"] == tricky


# --- AC6d: missing table raises, no modification -----------------------------

def test_ac6d_missing_table_raises_naming_profile(tmp_path):
    from sasha.config import ConfigError, set_api_key

    p = tmp_path / "config.toml"
    original = '[profiles.llm7]\nbase_url = "http://x/v1"\nmodel = "m"\n'
    p.write_text(original)

    with pytest.raises(ConfigError) as exc:
        set_api_key(p, "openai", "sk-abc")

    assert "openai" in str(exc.value)
    assert p.read_text() == original


# --- AC6e: unreadable/unwritable paths raise, no partial write ---------------

def test_ac6e_nonexistent_path_raises(tmp_path):
    from sasha.config import ConfigError, set_api_key

    p = tmp_path / "does-not-exist.toml"
    with pytest.raises(ConfigError):
        set_api_key(p, "openai", "sk-abc")


def test_ac6e_unwritable_file_raises_content_unchanged(tmp_path):
    from sasha.config import ConfigError, set_api_key

    p = tmp_path / "config.toml"
    original = '[profiles.openai]\nbase_url = "http://x/v1"\nmodel = "m"\n'
    p.write_text(original)
    p.chmod(0o444)

    try:
        with pytest.raises(ConfigError):
            set_api_key(p, "openai", "sk-abc")
    finally:
        p.chmod(0o644)

    assert p.read_text() == original
