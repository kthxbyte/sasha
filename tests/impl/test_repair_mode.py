"""Supplementary tests (not contract) — `sasha repair` and the repair profile.

Repair is the one job worth pointing at a frontier model even when daily
driving is local, so the profile it uses is a separate, explicit setting
(`repair_profile`, overridable with `--repair-profile`) rather than a profile
that happens to be named "repair" — a profile is an endpoint, not a purpose,
and the same endpoint stays usable interactively.
"""

from __future__ import annotations

import argparse
import dataclasses
import os
import pathlib
import sys
import tomllib

import pytest

from sasha import config as config_mod
from sasha import guard
from sasha import __main__ as main_mod
from sasha.session import Session


def _isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))


# --------------------------------------------------------------------------
# config: repair_profile
# --------------------------------------------------------------------------

def test_repair_profile_defaults_to_empty(monkeypatch, tmp_path):
    _isolated(monkeypatch, tmp_path)
    cfg = config_mod.load(None)
    assert cfg.repair_profile == ""


def test_repair_profile_is_written_to_the_self_installed_config(monkeypatch, tmp_path):
    _isolated(monkeypatch, tmp_path)
    cfg = config_mod.load(None)
    assert "repair_profile" in cfg.path.read_text()


def test_repair_profile_accepts_a_known_profile(monkeypatch, tmp_path):
    _isolated(monkeypatch, tmp_path)
    p = tmp_path / "config.toml"
    p.write_text('repair_profile = "deepseek"\n')
    assert config_mod.load(p).repair_profile == "deepseek"


def test_repair_profile_rejects_an_unknown_name(monkeypatch, tmp_path):
    _isolated(monkeypatch, tmp_path)
    p = tmp_path / "config.toml"
    p.write_text('repair_profile = "gpt-9"\n')
    with pytest.raises(config_mod.ConfigError) as exc:
        config_mod.load(p)
    assert "repair_profile" in str(exc.value)
    assert "gpt-9" in str(exc.value)


def test_repair_profile_may_name_a_user_defined_profile(monkeypatch, tmp_path):
    _isolated(monkeypatch, tmp_path)
    p = tmp_path / "config.toml"
    p.write_text("""\
repair_profile = "big"
[profiles.big]
base_url = "https://example.test/v1"
model = "frontier-1"
api_key = "k"
""")
    assert config_mod.load(p).repair_profile == "big"


# --------------------------------------------------------------------------
# session: repair framing + crash report in context
# --------------------------------------------------------------------------

def test_repair_context_reaches_the_system_prompt():
    session = Session(12000)
    assert "own source code" not in session.system_prompt

    session.set_repair_context(main_mod._REPAIR_BRIEF, "RuntimeError: boom")

    prompt = session.system_prompt
    assert "own source code" in prompt
    assert "tests/contract/" in prompt          # the immutable-oracle rule
    assert "RuntimeError: boom" in prompt       # the crash report itself
    # and the ordinary functional instructions are still there
    assert "interactive shell wrapper" in prompt


def test_repair_context_without_a_crash_report():
    session = Session(12000)
    session.set_repair_context("be careful", "")
    prompt = session.system_prompt
    assert "be careful" in prompt
    assert "crash report" not in prompt


# --------------------------------------------------------------------------
# checkout + bundle discovery
# --------------------------------------------------------------------------

def test_checkout_root_is_detected_for_an_editable_install():
    root = main_mod._sasha_checkout_root()
    assert root is not None
    assert (root / "pyproject.toml").is_file()
    assert (root / "src" / "sasha").is_dir()


def test_newest_crash_bundle_picks_the_latest(tmp_path):
    crashes = tmp_path / "crashes"
    crashes.mkdir()
    (crashes / "20260101T000000Z-1.txt").write_text("old")
    (crashes / "20260202T000000Z-2.txt").write_text("new")
    (crashes / "20260202T000000Z-2.json").write_text("{}")

    assert main_mod._newest_crash_bundle(tmp_path).name == \
        "20260202T000000Z-2.txt"


def test_newest_crash_bundle_when_there_are_none(tmp_path):
    assert main_mod._newest_crash_bundle(tmp_path) is None
    assert main_mod._newest_crash_bundle(None) is None


# --------------------------------------------------------------------------
# the banner
# --------------------------------------------------------------------------

class _Sink:
    color_enabled = False

    def __init__(self):
        self.lines = []

    def write_line(self, text, color=None):
        self.lines.append(text)


def test_repair_banner_names_the_checkout_model_and_report(tmp_path):
    sink = _Sink()
    profile = config_mod.Profile("deepseek", "https://api.deepseek.com/v1",
                                 "deepseek-chat", "sk-x", True)

    class _Repl:
        _current_profile = profile

    report = tmp_path / "20260101T000000Z-1.txt"
    main_mod._print_repair_banner(sink, _Repl(), report, checkout=tmp_path)

    text = "\n".join(sink.lines)
    assert "repair mode" in text
    assert str(tmp_path) in text
    assert "deepseek-chat" in text
    assert str(report) in text
    assert "run-tests.sh" in text


def test_repair_banner_says_so_when_there_is_no_report(tmp_path):
    sink = _Sink()

    class _Repl:
        _current_profile = config_mod.Profile("local", "http://x/v1", "m", "", False)

    main_mod._print_repair_banner(sink, _Repl(), None, checkout=tmp_path)
    assert "no crash report found" in "\n".join(sink.lines)


# --------------------------------------------------------------------------
# end to end: `sasha repair` setup path, without touching the real repo
# --------------------------------------------------------------------------

@pytest.fixture
def restore_cwd():
    before = os.getcwd()
    try:
        yield
    finally:
        os.chdir(before)


def _fake_checkout(tmp_path: pathlib.Path) -> pathlib.Path:
    checkout = tmp_path / "fakecheckout"
    (checkout / "src" / "sasha").mkdir(parents=True)
    (checkout / "pyproject.toml").write_text("[project]\nname='sasha'\n")
    return checkout


def _repair_args(config_path):
    return argparse.Namespace(
        version=False, config=config_path, profile=None, reasoning=None,
        command="repair", bundle=None, repair_profile=None)


def _run_repair(monkeypatch, tmp_path, restore_cwd, *, repair_profile="frontier",
                bundle=None):
    _isolated(monkeypatch, tmp_path)
    checkout = _fake_checkout(tmp_path)

    cfg = tmp_path / "config.toml"
    cfg.write_text(f"""\
default_profile = "local"
repair_profile = "{repair_profile}"

[profiles.local]
base_url = "http://local.test/v1"
model = "tiny-local"
api_key = ""
api_key_required = false

[profiles.frontier]
base_url = "https://frontier.test/v1"
model = "big-model"
api_key = "sk-test"
api_key_required = true
""")
    monkeypatch.setattr(main_mod, "_sasha_checkout_root", lambda: checkout)
    monkeypatch.setattr(main_mod.Repl, "loop", lambda self: None)

    args = _repair_args(cfg)
    args.bundle = bundle
    rc = main_mod._run(args, repair_mode=True)
    return rc, checkout


def test_repair_switches_to_the_repair_profile(monkeypatch, tmp_path, restore_cwd,
                                               capsys):
    rc, checkout = _run_repair(monkeypatch, tmp_path, restore_cwd)

    assert rc == 0
    out = capsys.readouterr().out
    assert "repair mode" in out
    assert "big-model" in out            # the frontier profile, not tiny-local
    assert str(checkout) in out


def test_repair_runs_in_the_checkout(monkeypatch, tmp_path, restore_cwd):
    rc, checkout = _run_repair(monkeypatch, tmp_path, restore_cwd)
    assert rc == 0
    assert pathlib.Path(os.getcwd()).resolve() == checkout.resolve()


def test_repair_injects_the_newest_crash_report(monkeypatch, tmp_path,
                                                restore_cwd, capsys):
    _isolated(monkeypatch, tmp_path)
    crashes = tmp_path / "state" / "sasha" / "crashes"
    crashes.mkdir(parents=True)
    report = crashes / "20260101T000000Z-9.txt"
    report.write_text("# Sasha crash report\nexception: ValueError\n")

    rc, _ = _run_repair(monkeypatch, tmp_path, restore_cwd)
    assert rc == 0
    assert str(report) in capsys.readouterr().out


def test_repair_explicit_bundle_wins(monkeypatch, tmp_path, restore_cwd, capsys):
    _isolated(monkeypatch, tmp_path)
    crashes = tmp_path / "state" / "sasha" / "crashes"
    crashes.mkdir(parents=True)
    (crashes / "20260101T000000Z-9.txt").write_text("newest ignored\n")
    chosen = tmp_path / "chosen.txt"
    chosen.write_text("# Sasha crash report\nexception: KeyError\n")

    rc, _ = _run_repair(monkeypatch, tmp_path, restore_cwd, bundle=chosen)
    assert rc == 0
    assert str(chosen) in capsys.readouterr().out


def test_repair_without_a_checkout_exits_cleanly(monkeypatch, tmp_path, capsys):
    _isolated(monkeypatch, tmp_path)
    monkeypatch.setattr(main_mod, "_sasha_checkout_root", lambda: None)

    rc = main_mod._run(_repair_args(None), repair_mode=True)

    assert rc == 2
    assert "needs a source checkout" in capsys.readouterr().err


def test_repair_profile_may_point_at_any_configured_profile(monkeypatch, tmp_path,
                                                            restore_cwd, capsys):
    rc, _ = _run_repair(monkeypatch, tmp_path, restore_cwd,
                        repair_profile="local")
    assert rc == 0
    assert "tiny-local" in capsys.readouterr().out


def test_repair_warns_and_falls_back_when_no_key_is_given(monkeypatch, tmp_path,
                                                          restore_cwd, capsys):
    """The realistic case: repair_profile is a frontier endpoint with no key
    yet, and the user declines to paste one."""
    _isolated(monkeypatch, tmp_path)
    checkout = _fake_checkout(tmp_path)
    cfg = tmp_path / "config.toml"
    cfg.write_text("""\
default_profile = "local"
repair_profile = "frontier"

[profiles.local]
base_url = "http://local.test/v1"
model = "tiny-local"
api_key = ""
api_key_required = false

[profiles.frontier]
base_url = "https://frontier.test/v1"
model = "big-model"
api_key = ""
api_key_required = true
""")
    monkeypatch.setattr(main_mod, "_sasha_checkout_root", lambda: checkout)
    monkeypatch.setattr(main_mod.Repl, "loop", lambda self: None)
    monkeypatch.setattr(main_mod.getpass, "getpass", lambda prompt="": "")

    assert main_mod._run(_repair_args(cfg), repair_mode=True) == 0

    out = capsys.readouterr().out
    assert "cancelled" in out                     # _activate_profile declined
    assert "falling back to local" in out
    assert "strongly recommended" in out


# --------------------------------------------------------------------------
# CLI surface
# --------------------------------------------------------------------------

def test_cli_accepts_the_repair_command():
    from sasha.__main__ import main
    # --help is enough to prove the subcommand exists and parses; it raises
    # SystemExit(0) before any setup runs.
    with pytest.raises(SystemExit) as exc:
        main(["repair", "--help"])
    assert exc.value.code == 0


def test_cli_rejects_an_unknown_command():
    from sasha.__main__ import main
    with pytest.raises(SystemExit) as exc:
        main(["frobnicate"])
    assert exc.value.code == 2


def test_repair_inside_a_session_points_at_the_shell(monkeypatch, capsys):
    from sasha.repl import Repl

    sink = _Sink()

    class _Stub:
        _sink = sink
        _config = type("C", (), {"profiles": {}})()
        _current_profile = config_mod.Profile("p", "u", "m", "", False)

    Repl._handle_builtin(_Stub(), "sasha repair")
    assert "own session" in "\n".join(sink.lines)
