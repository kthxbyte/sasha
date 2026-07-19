"""Contract tests — Task 19.2: prompt.py busy-star token (\\S, blink) +
Repl background-task counter.

Written by Tier 1 at spec time; immutable to the implementer.
Spec: docs/superpowers/specs/task-19.2-busy-star-indicator.md
Design: docs/superpowers/specs/2026-07-18-memory-bootstrap-design.md
"""

import pytest


# --- AC1: COLOR_TOKENS gains "blink" ------------------------------------------

def test_ac1_blink_token_value():
    from sasha.prompt import COLOR_TOKENS
    assert COLOR_TOKENS["blink"] == "\x1b[5m"


# --- AC2-5: the \S busy-star token, in isolation -------------------------------

def test_ac2_busy_star_idle_color_enabled():
    from sasha.prompt import render
    out = render(r"\S", user="a", host="h", cwd="/", home="/x",
                 color_enabled=True, busy=False)
    assert out == "\001\x1b[33m\002✦\001\x1b[0m\002"


def test_ac3_busy_star_busy_color_enabled():
    from sasha.prompt import render
    out = render(r"\S", user="a", host="h", cwd="/", home="/x",
                 color_enabled=True, busy=True)
    assert out == "\001\x1b[5m\x1b[33m\002✦\001\x1b[0m\002"


def test_ac4_busy_star_color_disabled_busy():
    from sasha.prompt import render
    out = render(r"\S", user="a", host="h", cwd="/", home="/x",
                 color_enabled=False, busy=True)
    assert out == "✦"


def test_ac4_busy_star_color_disabled_idle():
    from sasha.prompt import render
    out = render(r"\S", user="a", host="h", cwd="/", home="/x",
                 color_enabled=False, busy=False)
    assert out == "✦"


def test_ac5_busy_defaults_to_false_when_omitted():
    from sasha.prompt import render
    out = render(r"\S", user="a", host="h", cwd="/", home="/x",
                 color_enabled=True)
    assert out == "\001\x1b[33m\002✦\001\x1b[0m\002"
    assert "\x1b[5m" not in out


# --- AC6: DEFAULT_TEMPLATE end-to-end ------------------------------------------

def test_ac6_default_template_idle():
    from sasha.prompt import DEFAULT_TEMPLATE, render
    out = render(DEFAULT_TEMPLATE, user="alice", host="devbox",
                 cwd="/home/alice/project", home="/home/alice",
                 color_enabled=True, busy=False)
    assert "\x1b[5m" not in out
    assert "✦" in out
    assert "alice@devbox" in out
    assert "~/project" in out


def test_ac6_default_template_busy():
    from sasha.prompt import DEFAULT_TEMPLATE, render
    out = render(DEFAULT_TEMPLATE, user="alice", host="devbox",
                 cwd="/home/alice/project", home="/home/alice",
                 color_enabled=True, busy=True)
    assert "\x1b[5m" in out
    assert "✦" in out
    assert "alice@devbox" in out


# --- AC7: "S" addition doesn't disturb "W" basename fallback -------------------

def test_ac7_capital_w_still_renders_basename():
    from sasha.prompt import render
    out = render(r"\W", user="a", host="h", cwd="/home/alice/project",
                 home="/home/alice", color_enabled=False)
    assert out == "project"


def test_ac7_capital_w_home_itself_still_tilde():
    from sasha.prompt import render
    out = render(r"\W", user="a", host="h", cwd="/home/alice",
                 home="/home/alice", color_enabled=False)
    assert out == "~"


# --- AC8-10: Repl background-task counter --------------------------------------

class _CwdExecutor:
    def __init__(self, cwd):
        self.cwd = cwd


def _repl(cwd="/tmp"):
    from sasha.config import Config, Limits, Profile
    from sasha.repl import Repl
    from sasha.session import Session

    profile = Profile(name="p", base_url="http://unused", model="m",
                       api_key="")
    config = Config(default_profile="p", profiles={"p": profile},
                     limits=Limits())
    return Repl(config, _CwdExecutor(cwd), None, Session(),
                lambda p: None, None, lambda prompt: "", _ColorSink())


class _ColorSink:
    color_enabled = True

    def write_line(self, text):
        pass

    def write_chunk(self, text):
        pass

    def ensure_newline(self):
        pass


def test_ac8_fresh_repl_has_zero_background_tasks():
    repl = _repl()
    assert repl._background_tasks == 0


def test_ac9_begin_and_end_track_count():
    repl = _repl()
    repl._begin_background_task()
    repl._begin_background_task()
    assert repl._background_tasks == 2
    repl._end_background_task()
    assert repl._background_tasks == 1


def test_ac10_end_never_goes_negative():
    repl = _repl()
    repl._end_background_task()
    assert repl._background_tasks == 0


# --- AC11: loop() actually reads the live counter into render() ---------------

class _CapturingInput:
    """Records the single rendered prompt it's given, then ends the loop."""

    def __init__(self):
        self.captured = None

    def __call__(self, prompt):
        self.captured = prompt
        raise EOFError


def _repl_for_loop(cwd, input_fn):
    from sasha.config import Config, Limits, Profile
    from sasha.repl import Repl
    from sasha.session import Session

    profile = Profile(name="p", base_url="http://unused", model="m",
                       api_key="")
    config = Config(default_profile="p", profiles={"p": profile},
                     limits=Limits())
    return Repl(config, _CwdExecutor(cwd), None, Session(),
                lambda p: None, None, input_fn, _ColorSink())


def test_ac11_loop_prompt_blinks_when_background_task_active(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    input_fn = _CapturingInput()
    repl = _repl_for_loop(str(plain), input_fn)
    repl._begin_background_task()
    repl.loop()
    assert "\x1b[5m" in input_fn.captured


def test_ac11_loop_prompt_static_when_idle(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    input_fn = _CapturingInput()
    repl = _repl_for_loop(str(plain), input_fn)
    repl.loop()
    assert "\x1b[5m" not in input_fn.captured
