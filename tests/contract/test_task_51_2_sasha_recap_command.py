"""Contract tests — Task 51.2: sasha recap (explicit, sync, always-LLM)
+ sasha directory-recap on|off (ISSUE-051).

Written by Tier 1 at spec time; immutable to the implementer.
Spec: docs/superpowers/specs/task-51.2-sasha-recap-command.md
Design: docs/superpowers/specs/2026-07-26-issue-051-directory-recap-design.md
"""

from __future__ import annotations

import pathlib
import subprocess
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from sse_stub import StubServer, refused_base_url  # noqa: E402


def _git(repo, *args):
    subprocess.run(["git", *args], cwd=str(repo), check=True,
                    capture_output=True)


def _init_repo(repo):
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")


def _commit(repo, message):
    (repo / "f.txt").write_text(message)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)


@pytest.fixture
def isolated_tool_log_empty(monkeypatch, tmp_path):
    # Deterministic empty tool_log.jsonl for both the repl's internal
    # gather_context() call and the test's own independent call to agree.
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg-state-empty"))


# --- AC1-3: config.set_directory_recap_enabled ----------------------------

def test_ac1_patch_flips_only_the_enabled_line(tmp_path):
    from sasha import config

    p = tmp_path / "config.toml"
    p.write_text(
        '# a comment up top\n'
        'default_profile = "llm7"\n\n'
        '[profiles.llm7]\n'
        'base_url = "https://api.llm7.io/v1"\n'
        'model = "gpt-oss:20b"\n\n'
        '[directory_recap]\n'
        'enabled = true\n'
    )
    before = p.read_text()

    config.set_directory_recap_enabled(p, False)

    after = p.read_text()
    before_lines = before.splitlines(keepends=True)
    after_lines = after.splitlines(keepends=True)
    assert len(before_lines) == len(after_lines)
    diff_lines = [(b, a) for b, a in zip(before_lines, after_lines) if b != a]
    assert diff_lines == [("enabled = true\n", "enabled = false\n")]


def test_ac2_patch_inserts_missing_enabled_key(tmp_path):
    from sasha import config

    p = tmp_path / "config.toml"
    p.write_text(
        'default_profile = "llm7"\n'
        '[profiles.llm7]\n'
        'base_url = "https://api.llm7.io/v1"\n'
        'model = "gpt-oss:20b"\n\n'
        '[directory_recap]\n'
    )

    config.set_directory_recap_enabled(p, True)

    text = p.read_text()
    assert "[directory_recap]" in text
    assert "enabled = true" in text
    # still parses cleanly afterward
    loaded = config.load(p)
    assert loaded.directory_recap_enabled is True


def test_ac3_patch_raises_when_table_missing(tmp_path):
    from sasha import config

    p = tmp_path / "config.toml"
    p.write_text(
        'default_profile = "llm7"\n'
        '[profiles.llm7]\n'
        'base_url = "https://api.llm7.io/v1"\n'
        'model = "gpt-oss:20b"\n'
    )

    with pytest.raises(config.ConfigError):
        config.set_directory_recap_enabled(p, False)


# --- AC4-8: sasha recap ----------------------------------------------------

class FakeExecutor:
    def __init__(self, cwd):
        self._cwd = cwd
        self.sensitive_path_patterns = ()

    @property
    def cwd(self):
        return self._cwd


class SpyMutes:
    def is_muted(self, command, exit_code):
        return False

    def record_ignored(self, command, exit_code):
        return False

    def record_acted(self, command, exit_code):
        pass

    def mute(self, command, exit_code=None):
        pass

    def unmute(self, command, exit_code=None):
        pass


class SpySink:
    color_enabled = False

    def __init__(self):
        self.lines = []

    def write_line(self, text):
        self.lines.append(text)

    def write_async_line(self, text):
        self.lines.append(text)

    def write_chunk(self, text):
        if text:
            self.lines.append(text)

    def ensure_newline(self):
        pass


def _repl(cwd, stub_url=None, directory_recap_enabled=True, config_path=None):
    from sasha.config import Config, Limits, Profile
    from sasha.llm import ChatClient
    from sasha.repl import Repl
    from sasha.session import Session

    profile = Profile(name="stubprof", base_url=stub_url or "http://unused",
                       model="stub-model", api_key="")
    cfg = Config(default_profile="stubprof",
                 profiles={"stubprof": profile}, limits=Limits(),
                 directory_recap_enabled=directory_recap_enabled,
                 path=config_path)
    executor = FakeExecutor(cwd)
    sink = SpySink()
    repl = Repl(cfg, executor, SpyMutes(), Session(),
                lambda p: ChatClient(p, timeout_s=10.0), None,
                lambda prompt: "", sink)
    return repl, executor, sink


def test_ac4_recap_sends_gathered_context_prompt(tmp_path, isolated_tool_log_empty):
    from sasha import directory_recap

    repo = tmp_path / "myrepo"
    _init_repo(repo)
    _commit(repo, "initial work")

    expected_prompt = directory_recap.build_prompt(
        directory_recap.gather_context(repo))

    with StubServer([{"kind": "text", "text": "myrepo: a coherent little project"}]) as stub:
        repl, executor, sink = _repl(str(repo), stub_url=stub.base_url)
        repl._handle_builtin("sasha recap")

        assert len(stub.requests) == 1
        sent = stub.requests[0]["body"]["messages"]
        assert sent == [{"role": "user", "content": expected_prompt}]

    assert "myrepo: a coherent little project" in "".join(sink.lines)


def test_ac5_recap_ignores_disabled_config(tmp_path, isolated_tool_log_empty):
    repo = tmp_path / "myrepo"
    _init_repo(repo)
    _commit(repo, "work")

    with StubServer([{"kind": "text", "text": "recapped anyway"}]) as stub:
        repl, executor, sink = _repl(str(repo), stub_url=stub.base_url,
                                      directory_recap_enabled=False)
        repl._handle_builtin("sasha recap")
        assert len(stub.requests) == 1

    assert "recapped anyway" in "".join(sink.lines)


def test_ac6_recap_ignores_dedup_state(tmp_path, isolated_tool_log_empty):
    repo = tmp_path / "myrepo"
    _init_repo(repo)
    _commit(repo, "work")

    with StubServer([{"kind": "text", "text": "recapped despite dedup"}]) as stub:
        repl, executor, sink = _repl(str(repo), stub_url=stub.base_url)
        repl._recapped_dirs.add(str(repo))  # simulate already-recapped this session
        repl._handle_builtin("sasha recap")
        assert len(stub.requests) == 1
        # must not have consulted/mutated the dedup set as a side effect
        assert repl._recapped_dirs == {str(repo)}

    assert "recapped despite dedup" in "".join(sink.lines)


def test_ac7_recap_has_no_dedup_of_its_own(tmp_path, isolated_tool_log_empty):
    repo = tmp_path / "myrepo"
    _init_repo(repo)
    _commit(repo, "work")

    with StubServer([{"kind": "text", "text": "first"},
                      {"kind": "text", "text": "second"}]) as stub:
        repl, executor, sink = _repl(str(repo), stub_url=stub.base_url)
        repl._handle_builtin("sasha recap")
        repl._handle_builtin("sasha recap")
        assert len(stub.requests) == 2

    joined = "".join(sink.lines)
    assert "first" in joined
    assert "second" in joined
    assert joined.index("first") < joined.index("second")


def test_ac8_recap_handles_chat_error(tmp_path, isolated_tool_log_empty):
    repo = tmp_path / "myrepo"
    _init_repo(repo)
    _commit(repo, "work")

    repl, executor, sink = _repl(str(repo), stub_url=refused_base_url())
    repl._handle_builtin("sasha recap")

    assert any("unreachable" in l and "stubprof" in l for l in sink.lines)


# --- AC9-11: sasha directory-recap on|off ----------------------------------

def test_ac9_toggle_updates_memory_and_disk(tmp_path):
    from sasha import config as config_module

    p = tmp_path / "config.toml"
    p.write_text(
        'default_profile = "llm7"\n'
        '[profiles.llm7]\n'
        'base_url = "https://api.llm7.io/v1"\n'
        'model = "gpt-oss:20b"\n\n'
        '[directory_recap]\n'
        'enabled = true\n'
    )
    repl, executor, sink = _repl(str(tmp_path), config_path=p)

    repl._handle_builtin("sasha directory-recap off")
    assert repl._config.directory_recap_enabled is False
    assert config_module.load(p).directory_recap_enabled is False

    repl._handle_builtin("sasha directory-recap on")
    assert repl._config.directory_recap_enabled is True
    assert config_module.load(p).directory_recap_enabled is True


def test_ac10_toggle_invalid_argument_is_a_no_op(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text(
        'default_profile = "llm7"\n'
        '[profiles.llm7]\n'
        'base_url = "https://api.llm7.io/v1"\n'
        'model = "gpt-oss:20b"\n\n'
        '[directory_recap]\n'
        'enabled = true\n'
    )
    repl, executor, sink = _repl(str(tmp_path), config_path=p)

    repl._handle_builtin("sasha directory-recap maybe")
    assert repl._config.directory_recap_enabled is True
    assert any("unknown command" in l for l in sink.lines)

    repl2, executor2, sink2 = _repl(str(tmp_path), config_path=p)
    repl2._handle_builtin("sasha directory-recap")
    assert repl2._config.directory_recap_enabled is True
    assert any("unknown command" in l for l in sink2.lines)


def test_ac11_toggle_no_config_path_is_a_no_op(tmp_path):
    repl, executor, sink = _repl(str(tmp_path), config_path=None)

    repl._handle_builtin("sasha directory-recap off")

    assert repl._config.directory_recap_enabled is True
    assert any("no config file on disk" in l for l in sink.lines)


# --- AC12: help mentions both new commands ---------------------------------

def test_ac12_help_mentions_both_new_commands(tmp_path):
    repl, executor, sink = _repl(str(tmp_path))
    repl._handle_builtin("sasha help")
    joined = "\n".join(sink.lines)
    assert "sasha recap" in joined
    assert "sasha directory-recap on|off" in joined or "sasha directory-recap" in joined
