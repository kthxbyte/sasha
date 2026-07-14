"""Contract tests — Task 1.4: executor PTY tees.

Written by Tier 1 at spec time; immutable to the implementer.
Spec: docs/superpowers/specs/task-1.4-executor-pty.md (design §9.1, §9.2).
Runs real /bin/bash; a 30 s alarm guards against relay-loop hangs.
"""

import os
import shlex
import signal

import pytest


@pytest.fixture(autouse=True)
def _hang_guard():
    def boom(signum, frame):
        raise TimeoutError("contract test timed out (PTY relay hang?)")

    old = signal.signal(signal.SIGALRM, boom)
    signal.alarm(30)
    yield
    signal.alarm(0)
    signal.signal(signal.SIGALRM, old)


def _executor(tmp_path, **kw):
    from sasha.executor import Executor

    return Executor(cwd=str(tmp_path), env=dict(os.environ), **kw)


# --- AC1: the child sees a tty ---------------------------------------------

def test_ac1_stderr_is_a_tty(tmp_path):
    assert _executor(tmp_path).run("test -t 2").exit_code == 0


def test_ac1_stdout_is_a_tty_when_captured(tmp_path):
    r = _executor(tmp_path).run("test -t 1", capture_stdout=True)
    assert r.exit_code == 0


# --- AC2: relay, not swallow -------------------------------------------------

def test_ac2_stderr_relayed_to_parent(tmp_path, capfd):
    _executor(tmp_path).run("echo sasha-relay-probe >&2")
    assert "sasha-relay-probe" in capfd.readouterr().err


# --- AC3: capture hygiene -------------------------------------------------------

def test_ac3_ansi_csi_stripped(tmp_path):
    r = _executor(tmp_path).run(r"printf '\033[31mred\033[0m plain\n' >&2")
    assert "red" in r.stderr_text
    assert "plain" in r.stderr_text
    assert "\x1b" not in r.stderr_text
    assert "[31m" not in r.stderr_text


def test_ac3_osc_stripped(tmp_path):
    r = _executor(tmp_path).run(r"printf '\033]0;win-title\007visible\n' >&2")
    assert "visible" in r.stderr_text
    assert "win-title" not in r.stderr_text
    assert "\x1b" not in r.stderr_text


def test_ac3_no_carriage_returns(tmp_path):
    r = _executor(tmp_path).run(r"printf 'line-one\r\nline-two\n' >&2")
    assert "\r" not in r.stderr_text
    assert "line-one" in r.stderr_text
    assert "line-two" in r.stderr_text


# --- AC4: tail cap ---------------------------------------------------------------

def test_ac4_tail_cap(tmp_path):
    ex = _executor(tmp_path, stderr_capture_bytes=100)
    r = ex.run("{ printf 'A%.0s' $(seq 400); printf ' ZEND'; } >&2")
    assert "ZEND" in r.stderr_text            # the tail survives
    assert len(r.stderr_text) <= 100          # hygiene only shrinks the capped bytes
    assert r.stderr_text.count("A") < 400     # the head was dropped


# --- AC5: stdout capture is opt-in -------------------------------------------------

def test_ac5_stdout_captured_when_requested(tmp_path, capfd):
    r = _executor(tmp_path).run("echo captured-out-probe", capture_stdout=True)
    assert "captured-out-probe" in r.stdout_text
    # relayed too — the bytes still reach the parent's stdout
    assert "captured-out-probe" in capfd.readouterr().out


def test_ac5_stdout_not_captured_by_default(tmp_path):
    r = _executor(tmp_path).run("echo not-captured-probe")
    assert r.stdout_text == ""


# --- AC6: window size ------------------------------------------------------------------

def _expected_winsize():
    """Mirror of the pinned rule: fd 2's size if it is a tty, else 24x80."""
    if os.isatty(2):
        import fcntl
        import struct
        import termios

        rows, cols = struct.unpack(
            "hh", fcntl.ioctl(2, termios.TIOCGWINSZ, b"\0" * 4)
        )
        if rows > 0 and cols > 0:
            return rows, cols
    return 24, 80


def test_ac6_winsize_propagated(tmp_path):
    out = tmp_path / "size.txt"
    r = _executor(tmp_path).run(f"stty size <&2 > {shlex.quote(str(out))}")
    assert r.exit_code == 0
    rows, cols = (int(x) for x in out.read_text().split())
    assert (rows, cols) == _expected_winsize()
