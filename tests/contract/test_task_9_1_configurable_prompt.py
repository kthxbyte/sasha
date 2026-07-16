"""Contract tests — Task 9.1: configurable prompt (ISSUE-022, Tier 1).

Written by Tier 1 at spec time; immutable to the implementer.
Spec: docs/superpowers/specs/task-9.1-configurable-prompt.md
Design: docs/superpowers/specs/2026-07-16-configurable-prompt-design.md
"""

import pytest


# --- AC1: positional escapes -------------------------------------------------

def test_ac1_user_escape():
    from sasha.prompt import render

    out = render(r"\u", user="alice", host="devbox", cwd="/home/alice",
                 home="/home/alice", color_enabled=False)
    assert out == "alice"


def test_ac1_host_escape():
    from sasha.prompt import render

    out = render(r"\h", user="alice", host="devbox", cwd="/home/alice",
                 home="/home/alice", color_enabled=False)
    assert out == "devbox"


def test_ac1_w_abbreviates_home_subdir():
    from sasha.prompt import render

    out = render(r"\w", user="alice", host="devbox",
                 cwd="/home/alice/project", home="/home/alice",
                 color_enabled=False)
    assert out == "~/project"


def test_ac1_w_home_itself_is_tilde():
    from sasha.prompt import render

    out = render(r"\w", user="alice", host="devbox", cwd="/home/alice",
                 home="/home/alice", color_enabled=False)
    assert out == "~"


def test_ac1_w_outside_home_is_full_path():
    from sasha.prompt import render

    out = render(r"\w", user="alice", host="devbox", cwd="/etc",
                 home="/home/alice", color_enabled=False)
    assert out == "/etc"


def test_ac1_capital_w_basename_subdir():
    from sasha.prompt import render

    out = render(r"\W", user="alice", host="devbox",
                 cwd="/home/alice/project", home="/home/alice",
                 color_enabled=False)
    assert out == "project"


def test_ac1_capital_w_home_itself_is_tilde():
    from sasha.prompt import render

    out = render(r"\W", user="alice", host="devbox", cwd="/home/alice",
                 home="/home/alice", color_enabled=False)
    assert out == "~"


def test_ac1_capital_w_root_is_slash():
    from sasha.prompt import render

    out = render(r"\W", user="alice", host="devbox", cwd="/",
                 home="/home/alice", color_enabled=False)
    assert out == "/"


def test_ac1_capital_w_outside_home_is_basename():
    from sasha.prompt import render

    out = render(r"\W", user="alice", host="devbox", cwd="/etc",
                 home="/home/alice", color_enabled=False)
    assert out == "etc"


def test_ac1_combined_template():
    from sasha.prompt import render

    out = render(r"\u@\h:\w", user="alice", host="devbox",
                 cwd="/home/alice/project", home="/home/alice",
                 color_enabled=False)
    assert out == "alice@devbox:~/project"


# --- AC2: color tokens --------------------------------------------------

def test_ac2_color_tokens_table_exact():
    from sasha.prompt import COLOR_TOKENS

    expected_base = {
        "black": "30", "red": "31", "green": "32", "yellow": "33",
        "blue": "34", "magenta": "35", "cyan": "36", "white": "37",
    }
    expected = {"reset": "\x1b[0m"}
    for name, code in expected_base.items():
        expected[name] = f"\x1b[{code}m"
        expected[f"bold-{name}"] = f"\x1b[1;{code}m"
        expected[f"dim-{name}"] = f"\x1b[2;{code}m"

    assert COLOR_TOKENS == expected
    assert len(COLOR_TOKENS) == 25


@pytest.mark.parametrize("token,code", [
    ("black", "30"), ("red", "31"), ("green", "32"), ("yellow", "33"),
    ("blue", "34"), ("magenta", "35"), ("cyan", "36"), ("white", "37"),
])
def test_ac2_render_plain_color_token(token, code):
    from sasha.prompt import render

    out = render(f"\\[{token}\\]", user="a", host="h", cwd="/", home="/x",
                 color_enabled=True)
    assert out == f"\001\x1b[{code}m\002"


@pytest.mark.parametrize("token,code", [
    ("black", "30"), ("red", "31"), ("green", "32"), ("yellow", "33"),
    ("blue", "34"), ("magenta", "35"), ("cyan", "36"), ("white", "37"),
])
def test_ac2_render_bold_color_token(token, code):
    from sasha.prompt import render

    out = render(f"\\[bold-{token}\\]", user="a", host="h", cwd="/",
                 home="/x", color_enabled=True)
    assert out == f"\001\x1b[1;{code}m\002"


@pytest.mark.parametrize("token,code", [
    ("black", "30"), ("red", "31"), ("green", "32"), ("yellow", "33"),
    ("blue", "34"), ("magenta", "35"), ("cyan", "36"), ("white", "37"),
])
def test_ac2_render_dim_color_token(token, code):
    from sasha.prompt import render

    out = render(f"\\[dim-{token}\\]", user="a", host="h", cwd="/",
                 home="/x", color_enabled=True)
    assert out == f"\001\x1b[2;{code}m\002"


def test_ac2_render_reset_token():
    from sasha.prompt import render

    out = render(r"\[reset\]", user="a", host="h", cwd="/", home="/x",
                 color_enabled=True)
    assert out == "\001\x1b[0m\002"


def test_ac2_multiple_tokens_compose():
    from sasha.prompt import render

    out = render(r"\[green\]\u\[reset\]", user="alice", host="h", cwd="/",
                 home="/x", color_enabled=True)
    assert out == "\001\x1b[32m\002alice\001\x1b[0m\002"


# --- AC3: color suppression when color_enabled=False -------------------

def test_ac3_color_token_suppressed_when_disabled():
    from sasha.prompt import render

    out = render(r"\[green\]\u\[reset\]", user="alice", host="h", cwd="/",
                 home="/x", color_enabled=False)
    assert out == "alice"


def test_ac3_positional_escapes_unaffected_by_color_disabled():
    from sasha.prompt import render

    out = render(r"\u@\h", user="alice", host="devbox", cwd="/",
                 home="/x", color_enabled=False)
    assert out == "alice@devbox"


# --- AC4: validation errors ----------------------------------------------

def test_ac4_unrecognized_escape_raises():
    from sasha.prompt import PromptError, validate_template

    with pytest.raises(PromptError) as exc:
        validate_template(r"\q")
    assert r"\q" in str(exc.value)


def test_ac4_unknown_token_raises():
    from sasha.prompt import PromptError, validate_template

    with pytest.raises(PromptError) as exc:
        validate_template(r"\[grene\]")
    assert "grene" in str(exc.value)


def test_ac4_unterminated_bracket_raises():
    from sasha.prompt import PromptError, validate_template

    with pytest.raises(PromptError):
        validate_template(r"\[green")


def test_ac4_valid_template_does_not_raise():
    from sasha.prompt import validate_template

    validate_template(r"\[bold-green\]\u@\h\[reset\]:\[blue\]\w\[reset\] $ ")


def test_ac4_default_template_is_valid():
    from sasha.prompt import DEFAULT_TEMPLATE, validate_template

    validate_template(DEFAULT_TEMPLATE)


# --- AC5: DEFAULT_TEMPLATE byte-equivalence with today's hardcoded output --

def test_ac5_default_template_matches_hardcoded_output_colored():
    from sasha.prompt import DEFAULT_TEMPLATE, render
    from sasha.repl import _PROMPT_COLOR, _RESET, _RL_IGNORE_END, _RL_IGNORE_START

    out = render(DEFAULT_TEMPLATE, user="alice", host="devbox",
                 cwd="/home/alice/project", home="/home/alice",
                 color_enabled=True)
    expected = (f"{_RL_IGNORE_START}{_PROMPT_COLOR}{_RL_IGNORE_END}"
                f"alice@devbox:~/project✦ "
                f"{_RL_IGNORE_START}{_RESET}{_RL_IGNORE_END}")
    assert out == expected


def test_ac5_default_template_matches_hardcoded_output_plain():
    from sasha.prompt import DEFAULT_TEMPLATE, render

    out = render(DEFAULT_TEMPLATE, user="alice", host="devbox",
                 cwd="/home/alice/project", home="/home/alice",
                 color_enabled=False)
    assert out == "alice@devbox:~/project✦ "


# --- AC6: config.load() integration ---------------------------------------

@pytest.fixture
def isolated_env(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    return tmp_path


def test_ac6_default_prompt_when_absent(isolated_env):
    from sasha.config import load
    from sasha.prompt import DEFAULT_TEMPLATE

    cfg = load(None)
    assert cfg.prompt == DEFAULT_TEMPLATE


def test_ac6_custom_prompt_round_trips(isolated_env, tmp_path):
    from sasha.config import load

    p = tmp_path / "config.toml"
    p.write_text('prompt = "\\\\u@\\\\h $ "\n')
    cfg = load(p)
    assert cfg.prompt == r"\u@\h $ "


def test_ac6_invalid_prompt_raises_configerror_not_prompterror(isolated_env, tmp_path):
    from sasha.config import ConfigError, load
    from sasha.prompt import PromptError

    p = tmp_path / "config.toml"
    p.write_text('prompt = "\\\\[grene\\\\]"\n')
    with pytest.raises(ConfigError) as exc:
        load(p)
    assert not isinstance(exc.value, PromptError)
