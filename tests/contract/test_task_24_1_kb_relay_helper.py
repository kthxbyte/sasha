"""Contract tests — Task 24.1: foreground-group keystroke relay helper
(ISSUE-040, third attempt).

Written by Tier 1 at spec time; immutable to the implementer.
Spec: docs/superpowers/specs/task-24.1-kb-relay-helper.md
Design: docs/superpowers/specs/2026-07-21-issue-040-foreground-relay-helper-design.md

Needs a real controlling terminal to exercise raw-mode negotiation at all —
see pty_harness.py (unmodified, reused from Task 4.1). A 30 s alarm guards
the whole test against a hung relay loop.

AC1/AC2 are revived, unmodified in substance, from the quarantined scratch
file at quarantine/manual-issue-040-stderr-relay-scratch/ — the *observable*
contract from Executor.run()'s caller's point of view is identical
regardless of which process does the relaying (Sasha's own, in the
abandoned design; a foreground-group helper, in this one).
"""

from __future__ import annotations

import dataclasses
import os
import pathlib
import signal
import sys
import time

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from pty_harness import spawn  # noqa: E402


@pytest.fixture(autouse=True)
def _hang_guard():
    def boom(signum, frame):
        raise TimeoutError("contract test timed out (keyboard-relay hang?)")

    old = signal.signal(signal.SIGALRM, boom)
    signal.alarm(30)
    yield
    signal.alarm(0)
    signal.signal(signal.SIGALRM, old)


# --- AC1: a program that raw-modes fd 2 (like a pager) receives real -------
# --- keystrokes typed at the real terminal --------------------------------

def test_ac1_fd2_raw_mode_receives_real_keystroke(tmp_path):
    cwd = str(tmp_path)

    def target():
        from sasha.executor import Executor
        ex = Executor(cwd=cwd, env=dict(os.environ))
        r = ex.run(
            "python3 -c \""
            "import os, sys, tty; "
            "tty.setraw(2); "
            "sys.stdout.write('READY\\n'); sys.stdout.flush(); "
            "b = os.read(2, 1); "
            "sys.stdout.write('GOT:' + repr(b)); sys.stdout.flush()"
            "\"",
            capture_stdout=True,
        )
        return dataclasses.asdict(r)

    h = spawn(target)
    try:
        h.read_until(b"READY", timeout=10)
        time.sleep(0.2)  # let the relay's poll loop notice and activate
        h.write(b"Q")
        result = h.result(timeout=10)
    finally:
        h.close()

    assert "GOT:b'Q'" in result.stdout_text


# --- AC2: a program reading fd 0 directly (mimicking a TUI's convention) --
# --- is completely unaffected — no interference from the relay mechanism --

def test_ac2_fd0_direct_reader_unaffected(tmp_path):
    cwd = str(tmp_path)

    def target():
        from sasha.executor import Executor
        ex = Executor(cwd=cwd, env=dict(os.environ))
        r = ex.run(
            "python3 -c \""
            "import os, sys, tty; "
            "tty.setraw(0); "
            "sys.stdout.write('READY\\n'); sys.stdout.flush(); "
            "b = os.read(0, 1); "
            "sys.stdout.write('GOT:' + repr(b)); sys.stdout.flush()"
            "\"",
            capture_stdout=True,
        )
        return dataclasses.asdict(r)

    h = spawn(target)
    try:
        h.read_until(b"READY", timeout=10)
        time.sleep(0.2)
        h.write(b"Q")
        result = h.result(timeout=10)
    finally:
        h.close()

    assert "GOT:b'Q'" in result.stdout_text


# --- AC3: no lingering relay-helper process after Executor.run() returns ---

def test_ac3_no_lingering_relay_helper_process(tmp_path):
    cwd = str(tmp_path)

    def target():
        from sasha.executor import Executor
        ex = Executor(cwd=cwd, env=dict(os.environ))
        r = ex.run(
            "python3 -c \""
            "import os, sys, tty; "
            "tty.setraw(2); "
            "sys.stdout.write('READY\\n'); sys.stdout.flush(); "
            "b = os.read(2, 1); "
            "sys.stdout.write('GOT:' + repr(b)); sys.stdout.flush()"
            "\"",
            capture_stdout=True,
        )
        def _scan():
            found = []
            for entry in os.listdir("/proc"):
                if not entry.isdigit():
                    continue
                try:
                    with open(f"/proc/{entry}/cmdline", "rb") as f:
                        cmdline = f.read()
                except OSError:
                    continue
                # Specific enough to not match this *test file's own* name
                # (test_task_24_1_kb_relay_helper.py, under tests/contract/)
                # -- only the real helper script, under src/sasha/.
                if b"sasha/_kb_relay_helper.py" in cmdline:
                    found.append(entry)
            return found

        lingering = _scan()
        result = dataclasses.asdict(r)
        result["lingering_helper_pids"] = lingering
        return result

    h = spawn(target)
    try:
        h.read_until(b"READY", timeout=10)
        time.sleep(0.2)
        h.write(b"Q")
        result = h.result(timeout=10)
    finally:
        h.close()

    assert "GOT:b'Q'" in result.stdout_text
    assert result.lingering_helper_pids == []


# --- AC4: non-interactive commands are unaffected --------------------------
# No new test: verified by the entire existing contract suite (561 tests as
# of this task, including test_task_1_3/1_4, test_task_4_1, and
# test_task_20_1) staying green unmodified as part of this task's own gate —
# none of them touch a capture PTY's termios, so the relay mechanism never
# activates for any of them.
