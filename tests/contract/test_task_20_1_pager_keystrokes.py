"""Contract tests — Task 20.1: pager keystroke fix (ISSUE-040).

Written by Tier 1 at spec time; immutable to the implementer.
Spec: docs/superpowers/specs/task-20.1-pager-keystrokes.md
Design: docs/superpowers/specs/2026-07-20-issue-040-pager-keystrokes-design.md

SIGTTIN/SIGTTOU disposition and stop-cause classification need a real
controlling terminal — see pty_harness.py (unmodified, reused from Task
4.1). A 30 s alarm guards the whole test against a hung handoff.
"""

from __future__ import annotations

import dataclasses
import os
import pathlib
import signal
import sys

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


# --- AC1: SIGTTIN is ignored (SIG_IGN) in the child tree -------------------

def test_ac1_sigttin_ignored_in_child(tmp_path):
    cwd = str(tmp_path)

    def target():
        from sasha.executor import Executor
        ex = Executor(cwd=cwd, env=dict(os.environ))
        r = ex.run(
            "python3 -c \"import signal; "
            "print(signal.getsignal(signal.SIGTTIN) is signal.SIG_IGN)\"",
            capture_stdout=True,
        )
        return dataclasses.asdict(r)

    h = spawn(target)
    try:
        result = h.result(timeout=10)
    finally:
        h.close()

    assert result.stdout_text.strip() == "True"


# --- AC2: a protective stop (SIGTTIN) never kills the job ------------------

def test_ac2_sigttin_stop_does_not_kill_job(tmp_path):
    cwd = str(tmp_path)

    def target():
        from sasha.executor import Executor
        ex = Executor(cwd=cwd, env=dict(os.environ))
        # Independent of AC1's fix: this resets the wrapped bash's OWN
        # SIGTTIN disposition back to default and signals itself directly
        # (bash builtins — trap/kill — run in the directly-forked process
        # itself, no further fork), so this test exercises the stop-
        # classification path on its own, simulating a stop from a cause
        # the AC1 ignore doesn't cover.
        r = ex.run(
            "trap - TTIN; kill -TTIN $$; echo resumed",
            capture_stdout=True,
        )
        return dataclasses.asdict(r)

    h = spawn(target)
    try:
        result = h.result(timeout=10)
    finally:
        h.close()

    assert result.ctrl_z_terminated is False
    assert "resumed" in result.stdout_text


# --- AC3: real Ctrl+Z (SIGTSTP) still kills the job ------------------------
# No new test: verified by the existing Task 4.1 contract test
# test_ac2_ctrl_z_terminates_and_reaps in test_task_4_1_job_control.py,
# which must stay green unmodified as part of this task's gate.
