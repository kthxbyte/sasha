"""Contract tests — Task 3.3: end-to-end smoke + README.

Written by Tier 1 at spec time; immutable to the implementer.
Spec: docs/superpowers/specs/task-3.3-e2e.md.
Drives a real `python -m sasha` under a PTY against the SSE stub.
"""

import json
import os
import pathlib
import pty
import select
import signal
import subprocess
import sys
import time

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from sse_stub import StubServer, refused_base_url  # noqa: E402

REPO = pathlib.Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _hang_guard():
    def boom(signum, frame):
        raise TimeoutError("e2e contract test timed out")

    old = signal.signal(signal.SIGALRM, boom)
    signal.alarm(120)
    yield
    signal.alarm(0)
    signal.signal(signal.SIGALRM, old)


class SashaProc:
    def __init__(self, tmp_path, base_url):
        home = tmp_path / "home"
        home.mkdir(exist_ok=True)
        cfg = tmp_path / "config.toml"
        cfg.write_text(
            'default_profile = "local"\n'
            '[profiles.local]\n'
            f'base_url = "{base_url}"\n'
            'model = "stub"\n'
            'api_key_env = ""\n')
        env = dict(os.environ)
        env["HOME"] = str(home)
        env["XDG_CONFIG_HOME"] = str(tmp_path / "xdg-config")
        env["XDG_STATE_HOME"] = str(tmp_path / "xdg-state")
        env.pop("DEEPSEEK_API_KEY", None)
        src = str(REPO / "src")
        env["PYTHONPATH"] = src + (
            os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")

        self.master, slave = pty.openpty()
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "sasha", "--config", str(cfg)],
            stdin=slave, stdout=slave, stderr=slave,
            env=env, cwd=str(home), close_fds=True)
        os.close(slave)
        self.home = home
        self.state_dir = tmp_path / "xdg-state" / "sasha"
        self.transcript = b""
        # Amended by Tier 1 for Task 54.1 (ISSUE-054): read_until() used
        # to match anywhere in the whole cumulative transcript, so a call
        # could be satisfied by a marker that had already appeared on an
        # earlier round -- always loose, but harmless before this task.
        # paste_join's burst-join now means back-to-back send() calls
        # with no realistic gap can legitimately be treated as one pasted
        # block (correct behavior for a real terminal paste); this test's
        # own rapid send()/drain_prompt() loop was relying on that
        # looseness to avoid ever truly waiting for the new round-trip to
        # finish, so two of its own commands could land close enough
        # together to be joined. Tracking how much of the transcript has
        # already been matched forces every read_until()/drain_prompt()
        # call to wait for a genuinely NEW occurrence, which fixes the
        # race, but proved insufficient alone against back-to-back send()
        # calls with literally zero gap -- see send()'s own note below.
        self._consumed_upto = 0

    def send(self, line):
        # Amended by Tier 1 for Task 54.1 (ISSUE-054): a real human never
        # sends a second command with zero gap after the first -- burst-
        # join is only meant to catch a genuine paste (all bytes arriving
        # near-instantly with no interleaved keystrokes at all). This
        # harness's own back-to-back send() calls have no such gap, so a
        # brief sleep here restores that realistic timing rather than
        # accidentally looking like one continuous paste to the new
        # burst-detection heuristic.
        os.write(self.master, (line + "\r").encode())
        time.sleep(0.05)

    def read_until(self, needle, timeout=25):
        deadline = time.monotonic() + timeout
        needle_b = needle.encode()
        while time.monotonic() < deadline:
            idx = self.transcript.find(needle_b, self._consumed_upto)
            if idx != -1:
                self._consumed_upto = idx + len(needle_b)
                return self.transcript.decode("utf-8", errors="replace")
            r, _, _ = select.select([self.master], [], [], 0.25)
            if r:
                try:
                    chunk = os.read(self.master, 4096)
                except OSError:
                    break
                if not chunk:
                    break
                self.transcript += chunk
        raise AssertionError(
            f"did not see {needle!r} in transcript:\n"
            + self.transcript.decode("utf-8", errors="replace"))

    def drain_prompt(self):
        # Amended by Tier 1 for Task 26.2 (ISSUE-045): the busy-star (✦)
        # is removed from DEFAULT_TEMPLATE; "$ " (the prompt's own
        # trailing marker) is the replacement "a prompt was drawn"
        # signal. Amended again by Tier 1 for Task 54.1 (ISSUE-054):
        # read_until()'s _consumed_upto tracking (see __init__) now
        # guarantees this waits for a genuinely NEW prompt each call.
        return self.read_until("$ ")

    def close(self):
        try:
            self.send("exit")
            self.proc.wait(timeout=10)
        except Exception:
            self.proc.kill()
            self.proc.wait(timeout=10)
        os.close(self.master)


@pytest.fixture
def sasha_factory(tmp_path):
    procs = []

    def factory(base_url):
        p = SashaProc(tmp_path, base_url)
        procs.append(p)
        p.drain_prompt()
        return p

    yield factory
    for p in procs:
        p.close()


def _mute_pairs(proc):
    path = proc.state_dir / "mutes.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text()).get("pairs", {})


# --- AC1 ------------------------------------------------------------------

def test_ac1_natural_language_line(sasha_factory):
    with StubServer([{"kind": "text", "text": "550*11 = 6050"}]) as stub:
        p = sasha_factory(stub.base_url)
        p.send("550*11")
        p.read_until("6050")
        p.drain_prompt()


# --- AC2 --------------------------------------------------------------------

def test_ac2_failure_consults_immediately(sasha_factory):
    # Amended by Tier 1 (2026-10-12): the trigger-economy inversion (ISSUE-067)
    # removed the OFFER state, so a failing command consults with no extra
    # keystroke and with nothing recorded against the command.
    with StubServer([{"kind": "text", "text": "the hint you need"}]) as stub:
        p = sasha_factory(stub.base_url)
        p.send("bash -c 'exit 3'")
        p.read_until("the hint you need")        # one command, no Enter needed
        p.drain_prompt()
        assert _mute_pairs(p) == {}              # no mute bookkeeping any more


# --- AC3 ----------------------------------------------------------------------

def test_ac3_repeated_failures_always_consult_and_never_mute(sasha_factory):
    # Amended by Tier 1 (2026-10-12): ISSUE-067 — there is no ignore streak, so
    # there is nothing to mute. Four identical failures must consult four times.
    with StubServer([{"kind": "text", "text": "explained"}] * 4) as stub:
        p = sasha_factory(stub.base_url)
        for _ in range(4):
            p.send("bash -c 'exit 3'")
            p.read_until("explained")
            p.drain_prompt()
        assert len(stub.requests) == 4
        text = p.transcript.decode("utf-8", errors="replace")
        assert "press Enter to ask" not in text
        assert "muting offers for" not in text
        assert _mute_pairs(p) == {}


# --- AC4 ------------------------------------------------------------------------

def test_ac4_confirmation_gate_end_to_end(sasha_factory, tmp_path):
    marker = tmp_path / "must-not-exist"
    with StubServer([
        {"kind": "tool_call", "id": "call_1", "name": "run_command",
         "arguments": {"command": f"touch {marker}",
                       "description": "create a marker"}},
        {"kind": "text", "text": "understood, not running it"},
    ]) as stub:
        p = sasha_factory(stub.base_url)
        p.send("?? make the marker")
        p.read_until("[sasha] wants to run:")
        p.read_until("[y]es / [n]o / [e]dit — or type a reply")
        p.send("n")
        p.read_until("understood, not running it")
    assert not marker.exists()


# --- AC5 --------------------------------------------------------------------------

def test_ac5_llm_down_shell_survives(sasha_factory):
    p = sasha_factory(refused_base_url())
    p.send("?? anyone home")
    p.read_until("unreachable")
    p.read_until("'sasha profile <name>' to switch")
    p.send("echo alive-and-well")
    p.read_until("alive-and-well")


# --- AC6 -----------------------------------------------------------------------------

def test_ac6_readme_documents_vocabulary():
    readme = (REPO / "README.md").read_text()
    for needle in [
        # Amended by Tier 1 (2026-10-12): "press Enter to ask" and "muting
        # offers for" were removed from the vocabulary by ISSUE-067 and are
        # asserted absent below.
        "unreachable:",
        # Amended by Tier 1 for Task 16.2 (multi-tool registry): stop-loop
        # wording changes from "command" to "action" once the guard covers
        # every tool, not just run_command.
        "stopping: repeated identical action",
        "[sasha] profile:",
        "sasha: config error:",
        "deepseek",
        "local",
        "pip install",
    ]:
        assert needle in readme, f"README missing: {needle!r}"
    for gone in ["press Enter to ask", "muting offers for", "sasha mute"]:
        assert gone not in readme, f"README still documents removed: {gone!r}"
