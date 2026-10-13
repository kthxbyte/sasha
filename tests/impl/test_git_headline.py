"""Supplementary tests (not contract) — the git headline.

What survives of the old directory-recap feature: one free line naming the
repository's last commit. Everything else — the heuristic cascade, the async
LLM escalation, the config toggle, the two built-ins — was deleted by
ISSUE-069, and these tests pin both the surviving behaviour and the removal.
"""

from __future__ import annotations

import pathlib
import subprocess

import pytest

from sasha import git_headline


def _git(repo: pathlib.Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=str(repo), check=True,
                   capture_output=True, text=True)


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "myproject"
    r.mkdir()
    _git(r, "init", "-q")
    _git(r, "config", "user.email", "t@example.test")
    _git(r, "config", "user.name", "Test")
    (r / "f.txt").write_text("hi")
    _git(r, "add", "f.txt")
    _git(r, "commit", "-q", "-m", "Track Task 61.1")
    return r


# --------------------------------------------------------------------------
# the module
# --------------------------------------------------------------------------

def test_last_commit_returns_age_and_subject(repo):
    result = git_headline.last_commit(repo)
    assert result is not None
    age, subject = result
    assert subject == "Track Task 61.1"
    assert age  # "2 minutes ago" and friends


def test_headline_names_the_repo_and_the_commit(repo):
    line = git_headline.headline(repo)
    assert line is not None
    assert line.startswith("[sasha] myproject: last commit ")
    assert line.endswith('— "Track Task 61.1"')


def test_headline_labels_by_the_path_it_is_given(repo):
    """`headline()` takes a repository ROOT — the caller resolves it, so the
    label is the project name rather than whichever subdirectory you are in.
    The REPL passes `_current_repo_root`, which `_check_place_shift` has
    already computed."""
    nested = repo / "a" / "b"
    nested.mkdir(parents=True)
    assert "myproject" in git_headline.headline(repo)
    # passing a subdirectory labels by the subdirectory: the caller's job to
    # pass the root, which is why the parameter is named repo_root
    assert "b:" in git_headline.headline(nested)


def test_repl_prints_the_repo_name_and_only_once(repo):
    """The integration: entering a subdirectory names the project, once."""
    from sasha.config import Config, Limits, Profile
    from sasha.repl import Repl
    from sasha.session import Session

    nested = repo / "a" / "b"
    nested.mkdir(parents=True)
    lines = []

    class _Ex:
        cwd = str(nested)
        sensitive_path_patterns = ()

    class _Sink:
        color_enabled = False

        def write_line(self, text, color=None):
            lines.append(text)

        def ensure_newline(self):
            pass

    config = Config(default_profile="p",
                    profiles={"p": Profile("p", "http://x/v1", "m", "", False)},
                    limits=Limits())
    repl = Repl(config, _Ex(), Session(), lambda p: None, None,
                lambda prompt: "", _Sink())
    repl._current_repo_root = repo

    repl._check_git_headline()
    assert len(lines) == 1
    assert "myproject" in lines[0]
    assert "Track Task 61.1" in lines[0]

    # once per directory per session
    repl._check_git_headline()
    assert len(lines) == 1


def test_repl_says_nothing_outside_a_repo(tmp_path):
    from sasha.config import Config, Limits, Profile
    from sasha.repl import Repl
    from sasha.session import Session

    plain = tmp_path / "plain"
    plain.mkdir()
    lines = []

    class _Ex:
        cwd = str(plain)
        sensitive_path_patterns = ()

    class _Sink:
        color_enabled = False

        def write_line(self, text, color=None):
            lines.append(text)

        def ensure_newline(self):
            pass

    config = Config(default_profile="p",
                    profiles={"p": Profile("p", "http://x/v1", "m", "", False)},
                    limits=Limits())
    repl = Repl(config, _Ex(), Session(), lambda p: None, None,
                lambda prompt: "", _Sink())
    repl._current_repo_root = None

    repl._check_git_headline()
    assert lines == []


def test_outside_a_repo_there_is_nothing_to_say(tmp_path):
    plain = tmp_path / "not-a-repo"
    plain.mkdir()
    assert git_headline.last_commit(plain) is None
    assert git_headline.headline(plain) is None


def test_a_repo_with_no_commits_has_nothing_to_say(tmp_path):
    r = tmp_path / "empty"
    r.mkdir()
    _git(r, "init", "-q")
    assert git_headline.last_commit(r) is None
    assert git_headline.headline(r) is None


def test_missing_path_is_not_an_error(tmp_path):
    assert git_headline.headline(tmp_path / "nope") is None


def test_a_subject_with_a_tab_still_parses(repo):
    (repo / "g.txt").write_text("x")
    _git(repo, "add", "g.txt")
    _git(repo, "commit", "-q", "-m", "weird\tsubject")
    line = git_headline.headline(repo)
    assert line is not None
    # only the FIRST tab separates age from subject
    assert "weird\tsubject" in line


# --------------------------------------------------------------------------
# the removal
# --------------------------------------------------------------------------

def test_directory_recap_module_is_gone():
    with pytest.raises(ImportError):
        import sasha.directory_recap  # noqa: F401


def test_config_has_no_recap_toggle(monkeypatch, tmp_path):
    from sasha import config as config_mod

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    cfg = config_mod.load(None)
    assert not hasattr(cfg, "directory_recap_enabled")
    assert not hasattr(config_mod, "set_directory_recap_enabled")
    text = cfg.path.read_text()
    assert "[directory_recap]" not in text
    assert "directory-recap" not in text


def test_recap_builtins_are_gone(monkeypatch, capsys):
    """`sasha recap` and `sasha directory-recap` must fall through to the
    unknown-command path rather than silently doing nothing."""
    from sasha.repl import Repl

    lines = []

    class _Sink:
        color_enabled = False

        def write_line(self, text, color=None):
            lines.append(text)

    class _Stub:
        _sink = _Sink()
        _config = type("C", (), {"profiles": {}})()
        _current_profile = type("P", (), {"name": "p", "model": "m",
                                          "base_url": "u", "api_key": ""})()

    assert Repl._handle_builtin(_Stub(), "sasha recap") is True
    assert Repl._handle_builtin(_Stub(), "sasha directory-recap on") is True
    assert sum("unknown command" in l for l in lines) == 2
