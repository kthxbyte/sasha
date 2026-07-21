"""Contract tests — Task 21.1: safe read-only command auto-approval.

Written by Tier 1 at spec time; immutable to the implementer.
Spec: docs/superpowers/specs/task-21.1-safe-command-auto-approval.md
Design: docs/superpowers/specs/2026-07-20-issue-041-safe-command-auto-approval-design.md

Pure string/token classifier logic (AC1-AC10) plus two integration checks
(AC11 dispatch, AC12 config) — no PTY or real-terminal machinery needed.
"""

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from sse_stub import StubServer  # noqa: E402

_DEFAULT_SENSITIVE_PATHS = (
    "~/.ssh/**", "/etc/passwd", "/etc/shadow*", "/etc/sudoers*",
    "~/.aws/**", "~/.gnupg/**", "~/.kube/**", "~/.config/gcloud/**",
    "~/.docker/config.json", "~/.netrc", "~/.pgpass",
    "~/.git-credentials", "~/.npmrc", "**/.env", "**/.env.*",
)


class FakeExecutor:
    """Duck-typed executor exposing exactly what the classifier reads."""

    def __init__(self, cwd, sensitive_path_patterns=_DEFAULT_SENSITIVE_PATHS):
        self.cwd = cwd
        self.sensitive_path_patterns = sensitive_path_patterns


def _classify(command, cwd="/home/salvador/project",
              sensitive_path_patterns=_DEFAULT_SENSITIVE_PATHS):
    from sasha.safe_commands import requires_confirmation

    executor = FakeExecutor(cwd, sensitive_path_patterns)
    return requires_confirmation({"command": command, "description": "d"}, executor)


# --- AC1: the four real transcript commands auto-approve -------------------

@pytest.mark.parametrize("command", [
    "pwd && ls FILE",
    "head -50 file.txt",
    "ls -la dir && echo --- && pwd",
    "wc -l *.txt",
])
def test_ac1_real_transcript_commands_auto_approve(command):
    assert _classify(command) is False


# --- AC2: chains joined by ; | || (not just &&) also auto-approve ----------

@pytest.mark.parametrize("command", [
    "ls; pwd",
    "cat f.txt | grep needle",
    "ls || echo none",
    "find . -name '*.py' | sort | uniq",
])
def test_ac2_chained_allowlisted_verbs_auto_approve(command):
    assert _classify(command) is False


# --- AC3: output redirection requires confirmation --------------------------

@pytest.mark.parametrize("command", [
    "ls > out.txt",
    "wc -l f.txt >> log",
    "echo hi > /tmp/x",
])
def test_ac3_redirection_requires_confirmation(command):
    assert _classify(command) is True


# --- AC4: command substitution requires confirmation ------------------------

@pytest.mark.parametrize("command", [
    "echo `id`",
    "echo $(whoami)",
    "cat $(find . -name secret)",
])
def test_ac4_command_substitution_requires_confirmation(command):
    assert _classify(command) is True


# --- AC5: unparseable quoting requires confirmation -------------------------

@pytest.mark.parametrize("command", [
    'echo "unterminated',
    "cat 'unterminated",
])
def test_ac5_unparseable_quoting_requires_confirmation(command):
    assert _classify(command) is True


# --- AC6: backgrounding requires confirmation --------------------------------

@pytest.mark.parametrize("command", [
    "ls &",
    "wc -l f.txt &",
])
def test_ac6_backgrounding_requires_confirmation(command):
    assert _classify(command) is True


# --- AC7: unrecognized leading token requires confirmation ------------------

@pytest.mark.parametrize("command", [
    "curl http://example.com",
    "ls && rm -rf /",
    "xargs rm",
    "FOO=bar ls",
    "/bin/ls",
    "ls && curl http://example.com",
])
def test_ac7_unrecognized_verb_requires_confirmation(command):
    assert _classify(command) is True


# --- AC8: unexpanded shell variable requires confirmation -------------------

@pytest.mark.parametrize("command", [
    "cat $HOME/file.txt",
    "echo $UNSET",
    "ls $SOME_DIR",
])
def test_ac8_unexpanded_variable_requires_confirmation(command):
    assert _classify(command) is True


# --- AC9: denylisted sensitive path requires confirmation -------------------

@pytest.mark.parametrize("command", [
    "cat ~/.ssh/id_rsa",       # dotfile-glob category
    "cat /etc/passwd",         # exact-file category
    "cat /etc/shadow",         # wildcard-suffix category
    "find . -name .env",       # any-depth glob category (**/.env)
    "cat ~/.aws/credentials",
    "cat ~/.docker/config.json",
])
def test_ac9_denylisted_path_requires_confirmation(command):
    assert _classify(command) is True


# --- AC10: path traversal normalizing into a denylisted path ----------------

def test_ac10_path_traversal_into_denylisted_path_requires_confirmation():
    assert _classify("cat ../../etc/passwd", cwd="/a/b") is True


def test_ac10_path_traversal_into_safe_path_still_auto_approves():
    # Sanity check alongside AC10: traversal alone isn't disqualifying —
    # only traversal that resolves into a denylisted path is.
    assert _classify("cat ../sibling/file.txt", cwd="/a/b") is False


# --- AC11: dispatch integration — auto-approve vs. gate ---------------------

class SpyExecutor:
    def __init__(self, cwd, sensitive_path_patterns=_DEFAULT_SENSITIVE_PATHS):
        from sasha.executor import ExecResult

        self.cwd = cwd
        self.sensitive_path_patterns = sensitive_path_patterns
        self.calls = []
        self._result = ExecResult(exit_code=0, signal=None,
                                  stderr_text="", stdout_text="OUT",
                                  cwd_after=cwd, duration_ms=1)

    def run(self, command, capture_stdout=False):
        self.calls.append((command, capture_stdout))
        return self._result


class RefusingConfirmer:
    def __init__(self):
        self.asked = []

    def ask(self, summary, description):
        self.asked.append((summary, description))
        raise AssertionError("confirmer.ask must not be called for auto-approved commands")


def _tc(command, call_id="call_1"):
    return {"kind": "tool_call", "id": call_id, "name": "run_command",
            "arguments": {"command": command, "description": "a step"},
            "fragments": 2}


def _consult(stub_url, confirmer, executor, seed="help me"):
    from sasha.agent import run_consult
    from sasha.config import Profile
    from sasha.llm import ChatClient
    from sasha.session import Session

    session = Session()
    session.add_user(seed)
    client = ChatClient(Profile(name="stub", base_url=stub_url,
                                model="stub-model", api_key=""), timeout_s=10.0)
    out_chunks = []
    run_consult(session, client, executor, confirmer, out=out_chunks.append)
    return session, executor, confirmer, "".join(out_chunks)


def test_ac11_dispatch_auto_approves_safe_and_announces(tmp_path):
    # The unsafe-still-gated and other-tools-unaffected paths are unchanged
    # behavior, already pinned by test_task_16_2_agent_registry.py and
    # test_task_2_3_agent.py (both stay green, unmodified, as part of this
    # task's own gate) — not re-tested here to avoid a test that would pass
    # vacuously against the pre-fix baseline.
    executor = SpyExecutor(str(tmp_path))
    confirmer = RefusingConfirmer()
    with StubServer([_tc("ls -la"), {"kind": "text", "text": "done"}]) as stub:
        _, executor, confirmer, out = _consult(stub.base_url, confirmer, executor)
    assert confirmer.asked == []
    assert executor.calls == [("ls -la", True)]
    assert "(auto-approved)" in out
    assert "ls -la" in out


# --- AC12: config default + fallback ----------------------------------------

@pytest.fixture
def isolated_env(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    return tmp_path


def test_ac12_config_default_and_fallback(isolated_env, tmp_path):
    from sasha.config import load

    # Fresh self-install: config.toml is written and contains the section.
    config = load()
    assert tuple(config.sensitive_paths) == _DEFAULT_SENSITIVE_PATHS
    written = (isolated_env / "xdg" / "sasha" / "config.toml").read_text()
    assert "[safe_commands]" in written
    assert "~/.ssh/**" in written

    # Pre-existing config.toml with no [safe_commands] section at all still
    # falls back to the same default list.
    p = tmp_path / "legacy.toml"
    p.write_text('default_profile = "llm7"\n[profiles.llm7]\n'
                 'base_url = "https://api.llm7.io/v1"\nmodel = "gpt-oss:20b"\n')
    legacy_config = load(p)
    assert tuple(legacy_config.sensitive_paths) == _DEFAULT_SENSITIVE_PATHS
