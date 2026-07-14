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

    def send(self, line):
        os.write(self.master, (line + "\r").encode())

    def read_until(self, needle, timeout=25):
        deadline = time.monotonic() + timeout
        needle_b = needle.encode()
        while time.monotonic() < deadline:
            if needle_b in self.transcript:
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
        return self.read_until("✦")  # ✦

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

def test_ac2_offer_accept_no_penalty(sasha_factory):
    with StubServer([{"kind": "text", "text": "the hint you need"}]) as stub:
        p = sasha_factory(stub.base_url)
        p.send("bash -c 'exit 3'")
        p.read_until("[sasha] failed (exit 3) — press Enter to ask")
        p.send("")
        p.read_until("the hint you need")
        pairs = _mute_pairs(p)
        entry = pairs.get("bash:3")
        assert entry is None or entry["ignored_streak"] == 0


# --- AC3 ----------------------------------------------------------------------

def test_ac3_mute_after_three_ignores(sasha_factory):
    with StubServer([]) as stub:
        p = sasha_factory(stub.base_url)
        for _ in range(3):
            p.send("bash -c 'exit 3'")
            p.read_until("press Enter to ask")
            p.send("true")          # non-empty input → offer ignored
            p.drain_prompt()
        p.read_until("[sasha] muting offers for bash exit 3 — "
                     "'sasha unmute bash' to restore")
        before = len(p.transcript)
        p.send("bash -c 'exit 3'")  # fourth failure: no offer
        p.drain_prompt()
        tail = p.transcript[before:].decode("utf-8", errors="replace")
        assert "press Enter to ask" not in tail


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
        "press Enter to ask",
        "muting offers for",
        "unreachable:",
        "stopping: repeated identical command",
        "[sasha] profile:",
        "sasha: config error:",
        "deepseek",
        "local",
        "pip install",
    ]:
        assert needle in readme, f"README missing: {needle!r}"
