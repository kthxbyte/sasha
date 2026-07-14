"""Contract tests — Task 1.3: executor core (subprocess, exit codes, side channel).

Written by Tier 1 at spec time; immutable to the implementer.
Spec: docs/superpowers/specs/task-1.3-executor-core.md (design §5.1, §9.3).
Runs real /bin/bash; a 30 s alarm guards against executor hangs.
"""

import dataclasses
import os
import shlex
import signal

import pytest


@pytest.fixture(autouse=True)
def _hang_guard():
    def boom(signum, frame):
        raise TimeoutError("contract test timed out (executor hang?)")

    old = signal.signal(signal.SIGALRM, boom)
    signal.alarm(30)
    yield
    signal.alarm(0)
    signal.signal(signal.SIGALRM, old)


def _executor(tmp_path):
    from sasha.executor import Executor

    return Executor(cwd=str(tmp_path), env=dict(os.environ))


# --- AC1: exit codes and result shape ------------------------------------------

def test_ac1_exit_codes(tmp_path):
    ex = _executor(tmp_path)
    assert ex.run("true").exit_code == 0
    assert ex.run("exit 7").exit_code == 7
    assert ex.run("definitely-not-a-command-xyz").exit_code == 127


def test_ac1_result_shape(tmp_path):
    ex = _executor(tmp_path)
    r = ex.run("true")
    assert r.signal is None
    assert isinstance(r.stderr_text, str)
    assert isinstance(r.stdout_text, str)
    assert isinstance(r.cwd_after, str) and os.path.isabs(r.cwd_after)
    assert isinstance(r.duration_ms, int) and r.duration_ms >= 0


def test_ac1_result_frozen(tmp_path):
    r = _executor(tmp_path).run("true")
    with pytest.raises(dataclasses.FrozenInstanceError):
        r.exit_code = 1


def test_ac1_duration_reflects_wall_clock(tmp_path):
    r = _executor(tmp_path).run("sleep 0.2")
    assert r.duration_ms >= 150


# --- AC2: signal death -----------------------------------------------------------

def test_ac2_signal_death_reports_signal_and_128n(tmp_path):
    r = _executor(tmp_path).run("kill -9 $$")
    assert r.signal == 9
    assert r.exit_code == 137


# --- AC3: cd persists -------------------------------------------------------------

def test_ac3_cd_persists_across_runs(tmp_path):
    ex = _executor(tmp_path)
    sub = tmp_path / "subdir"
    sub.mkdir()
    real_sub = os.path.realpath(sub)

    r = ex.run(f"cd {shlex.quote(str(sub))}")
    assert r.exit_code == 0
    assert os.path.realpath(ex.cwd) == real_sub
    assert os.path.realpath(r.cwd_after) == real_sub

    # the NEXT line starts in the new cwd (filesystem side effect, no stdout)
    ex.run("touch marker-file")
    assert (sub / "marker-file").exists()


# --- AC4: env persists --------------------------------------------------------------

def test_ac4_export_visible_in_next_line(tmp_path):
    ex = _executor(tmp_path)
    assert ex.run("export SASHA_T13=hello").exit_code == 0
    assert ex.run('test "$SASHA_T13" = hello').exit_code == 0


def test_ac4_unset_removes_variable(tmp_path):
    ex = _executor(tmp_path)
    ex.run("export SASHA_T13=hello")
    assert ex.run("unset SASHA_T13").exit_code == 0
    assert ex.run('test -z "${SASHA_T13+x}"').exit_code == 0


def test_ac4_env_persists_across_failing_line(tmp_path):
    ex = _executor(tmp_path)
    r = ex.run("export SASHA_T13B=kept; exit 3")
    assert r.exit_code == 3
    assert ex.run('test "$SASHA_T13B" = kept').exit_code == 0


# --- AC5: unreported death keeps state ------------------------------------------------

def test_ac5_sigkill_preserves_cwd_and_env(tmp_path):
    ex = _executor(tmp_path)
    ex.run("export SASHA_T13C=before")
    cwd_before = ex.cwd

    r = ex.run("cd /; kill -9 $$")
    assert r.signal == 9
    assert ex.cwd == cwd_before
    assert os.path.realpath(r.cwd_after) == os.path.realpath(cwd_before)
    assert ex.run('test "$SASHA_T13C" = before').exit_code == 0


# --- AC6: syntax pre-check ---------------------------------------------------------------

def test_ac6_check_syntax_reports_error_text(tmp_path):
    from sasha.executor import Executor

    err = Executor.check_syntax('echo "unclosed')
    assert isinstance(err, str) and err.strip()


def test_ac6_check_syntax_accepts_valid_line():
    from sasha.executor import Executor

    assert Executor.check_syntax("echo ok") is None


def test_ac6_check_syntax_executes_nothing(tmp_path):
    from sasha.executor import Executor

    probe = tmp_path / "must-not-exist"
    assert Executor.check_syntax(f"touch {shlex.quote(str(probe))}") is None
    assert not probe.exists()


# --- AC7: own process group -----------------------------------------------------------------

def test_ac7_child_in_own_process_group(tmp_path):
    ex = _executor(tmp_path)
    out = tmp_path / "pg.txt"
    r = ex.run(f"ps -o pgid= -p $$ > {shlex.quote(str(out))}; echo $$ >> {shlex.quote(str(out))}")
    assert r.exit_code == 0
    pgid_line, pid_line = out.read_text().split()
    assert int(pgid_line) != os.getpgrp()          # not Sasha's group
    assert int(pgid_line) == int(pid_line)         # the child leads its own group
