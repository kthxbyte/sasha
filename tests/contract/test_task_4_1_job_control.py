"""Contract tests — Task 4.1: job-control handoff (ISSUE-015 / ISSUE-016).

Written by Tier 1 at spec time; immutable to the implementer.
Spec: docs/superpowers/specs/task-4.1-job-control.md
Design: docs/superpowers/specs/2026-07-14-sasha-v2-job-control-design.md

tcsetpgrp/SIGTTOU/SIGTTIN behavior needs a real controlling terminal — see
pty_harness.py. A 30 s alarm guards the whole test against a hung handoff.
"""

from __future__ import annotations

import dataclasses
import os
import pathlib
import signal
import subprocess
import sys
import time
import uuid

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from pty_harness import spawn  # noqa: E402


@pytest.fixture(autouse=True)
def _hang_guard():
    def boom(signum, frame):
        raise TimeoutError("contract test timed out (job-control hang?)")

    old = signal.signal(signal.SIGALRM, boom)
    signal.alarm(30)
    yield
    signal.alarm(0)
    signal.signal(signal.SIGALRM, old)


def _marker():
    return "sasha-jc-" + uuid.uuid4().hex[:12]


def _assert_no_lingering(marker):
    time.sleep(0.1)
    leftover = subprocess.run(["pgrep", "-f", marker], capture_output=True)
    assert leftover.returncode != 0, (
        f"lingering process(es) matching {marker!r}: {leftover.stdout!r}"
    )


# --- AC1: Ctrl+C (INTR) reaches the foreground child via the terminal -----

def test_ac1_sigint_reaches_child_via_terminal(tmp_path):
    marker = _marker()
    cwd = str(tmp_path)

    def target():
        from sasha.executor import Executor
        ex = Executor(cwd=cwd, env=dict(os.environ))
        r = ex.run(f"yes {marker}")
        return dataclasses.asdict(r)

    h = spawn(target)
    try:
        h.read_until(marker.encode(), timeout=10)
        h.write(b"\x03")  # INTR
        result = h.result(timeout=10)
    finally:
        h.close()

    assert result.signal == signal.SIGINT
    assert result.exit_code == 128 + signal.SIGINT
    assert result.ctrl_z_terminated is False
    _assert_no_lingering(marker)


# --- AC2: Ctrl+Z (SUSP) terminates the job, never leaves it stopped -------

def test_ac2_ctrl_z_terminates_and_reaps(tmp_path):
    marker = _marker()
    cwd = str(tmp_path)

    def target():
        from sasha.executor import Executor
        ex = Executor(cwd=cwd, env=dict(os.environ))
        r = ex.run(f"echo {marker}; sleep 30 # {marker}")
        return dataclasses.asdict(r)

    h = spawn(target)
    try:
        h.read_until(marker.encode(), timeout=10)
        h.write(b"\x1a")  # SUSP
        result = h.result(timeout=10)
    finally:
        h.close()

    assert result.ctrl_z_terminated is True
    assert result.signal in (signal.SIGTERM, signal.SIGKILL)
    _assert_no_lingering(marker)


# --- AC3: non-interactive (piped stdin) path is unaffected -----------------
# No new test: verified by the existing Task 1.3/1.4 contract test files
# (tests/contract/test_task_1_3_executor_core.py, test_task_1_4_executor_pty.py)
# staying green unmodified — pytest's own stdin is not a controlling tty, so
# those tests already exercise the non-interactive fallback path.


# --- AC4: Sasha's own terminal settings survive a forced kill --------------

def test_ac4_termios_restored_after_ctrl_z_kill(tmp_path):
    marker = _marker()
    cwd = str(tmp_path)

    def target():
        import termios
        from sasha.executor import Executor
        baseline = termios.tcgetattr(0)
        ex = Executor(cwd=cwd, env=dict(os.environ))
        r = ex.run(f"echo {marker}; stty raw; sleep 30 # {marker}")
        after = termios.tcgetattr(0)
        data = dataclasses.asdict(r)
        data["termios_matches"] = (after == baseline)
        return data

    h = spawn(target)
    try:
        h.read_until(marker.encode(), timeout=10)
        h.write(b"\x1a")  # SUSP — child dies mid-raw-mode, never restores itself
        result = h.result(timeout=10)
    finally:
        h.close()

    assert result.ctrl_z_terminated is True
    assert result.termios_matches is True


# --- AC5: repl.py surfaces the Ctrl+Z case and bypasses the trigger engine -

class _SpyExecutor:
    def __init__(self, result):
        self._result = result
        self.ran = []

    @property
    def cwd(self):
        return "/spy"

    # Amended by Tier 1 for Task 25.1 (ISSUE-044): the raw-command path
    # now calls safe_commands.touches_sensitive_path for every command,
    # which reads this attribute.
    sensitive_path_patterns = ()

    def check_syntax(self, line):
        return None

    def run(self, line, capture_stdout=False):
        self.ran.append(line)
        return self._result


class _SpySink:
    """Sink test double (Task 6.1) — no coloring, raw text captured."""

    color_enabled = False

    def __init__(self, events):
        self._events = events

    def write_line(self, text):
        self._events.append(text)

    def write_chunk(self, text):
        if text:
            self._events.append(text)

    def ensure_newline(self):
        if self._events and not self._events[-1].endswith("\n"):
            self._events.append("\n")


def test_ac5_repl_prints_ctrl_z_message_and_skips_triggers():
    from sasha.config import Config, Limits, Profile
    from sasha.executor import ExecResult
    from sasha.repl import Repl
    from sasha.session import Session

    result = ExecResult(exit_code=143, signal=15, stderr_text="", stdout_text="",
                        cwd_after="/spy", duration_ms=1, ctrl_z_terminated=True)
    executor = _SpyExecutor(result)
    out_lines = []
    profiles = {"p": Profile(name="p", base_url="http://unused", model="m", api_key="")}
    config = Config(default_profile="p", profiles=profiles, limits=Limits())

    repl = Repl(config, executor, Session(), lambda profile: None,
                None, lambda prompt: "", _SpySink(out_lines))

    cont = repl.run_once("sleep 30")

    assert cont is True
    assert executor.ran == ["sleep 30"]
    assert out_lines == ["[sasha] Ctrl+Z not supported — command terminated"]
