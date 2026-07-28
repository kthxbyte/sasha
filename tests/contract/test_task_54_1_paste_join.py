"""Contract tests — Task 54.1: paste_join.py + __main__.py wiring
(ISSUE-054).

Written by Tier 1 at spec time; immutable to the implementer.
Spec: docs/superpowers/specs/task-54.1-paste-join.md
Design: docs/superpowers/specs/2026-07-27-issue-054-paste-join-design.md
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys
import time

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from pty_harness import spawn  # noqa: E402

REPO = pathlib.Path(__file__).resolve().parents[2]


# --- AC1-6: pure read_joined_input logic (fakes, no I/O) -------------------

def test_ac1_single_line_no_pending_is_unchanged():
    from sasha import paste_join

    calls = []

    def read_line(prompt):
        calls.append(prompt)
        return "hello"

    def has_pending():
        return False

    result = paste_join.read_joined_input("PROMPT> ", read_line, has_pending)
    assert result == "hello"
    assert calls == ["PROMPT> "]


def test_ac2_multiple_lines_joined_in_order():
    from sasha import paste_join

    lines = iter(["line1", "line2", "line3"])
    pending_values = iter([True, True, False])

    def read_line(prompt):
        return next(lines)

    def has_pending():
        return next(pending_values)

    result = paste_join.read_joined_input("PROMPT> ", read_line, has_pending)
    assert result == "line1\nline2\nline3"


def test_ac3_continuation_reads_use_empty_prompt():
    from sasha import paste_join

    prompts_seen = []
    lines = iter(["a", "b", "c"])
    pending_values = iter([True, True, False])

    def read_line(prompt):
        prompts_seen.append(prompt)
        return next(lines)

    def has_pending():
        return next(pending_values)

    paste_join.read_joined_input("ORIGINAL> ", read_line, has_pending)
    assert prompts_seen == ["ORIGINAL> ", "", ""]


def test_ac4_first_read_eof_propagates():
    from sasha import paste_join

    def read_line(prompt):
        raise EOFError()

    def has_pending():
        return False

    with pytest.raises(EOFError):
        paste_join.read_joined_input("PROMPT> ", read_line, has_pending)


def test_ac5_mid_burst_eof_returns_partial_join():
    from sasha import paste_join

    calls = [0]

    def read_line(prompt):
        calls[0] += 1
        if calls[0] == 1:
            return "first"
        raise EOFError()

    def has_pending():
        return True

    result = paste_join.read_joined_input("PROMPT> ", read_line, has_pending)
    assert result == "first"


def test_ac6_max_burst_lines_caps_pathological_pending():
    from sasha import paste_join

    calls = [0]

    def read_line(prompt):
        calls[0] += 1
        return f"line{calls[0]}"

    def has_pending():
        return True

    result = paste_join.read_joined_input("PROMPT> ", read_line, has_pending)
    assert calls[0] == paste_join.MAX_BURST_LINES
    assert result.count("\n") == paste_join.MAX_BURST_LINES - 1


# --- AC7-8: real end-to-end via a real PTY (select-based has_pending) -----

def test_ac7_real_typing_is_never_joined():
    def target():
        import select
        import sys as _sys
        from sasha import paste_join

        # pty_harness dup2's the real fds but a forked child still
        # inherits pytest's own captured stdin/stdout Python objects;
        # restore the originals (thin fd-number wrappers, unaffected by
        # pytest's capture monkeypatching) so input() actually reads from
        # and prompts on the new pty slave.
        _sys.stdin = _sys.__stdin__
        _sys.stdout = _sys.__stdout__

        def has_pending():
            r, _, _ = select.select([_sys.stdin], [], [], 0.01)
            return bool(r)

        results = []
        while len(results) < 3:
            try:
                line = paste_join.read_joined_input("P> ", input, has_pending)
            except EOFError:
                break
            results.append(line)
            print(f"GOT:{line!r}", flush=True)
        return {"results": results}

    h = spawn(target)
    try:
        h.read_until(b"P> ", timeout=5)
        h.write(b"first\n")
        time.sleep(0.3)
        h.write(b"second\n")
        time.sleep(0.3)
        h.write(b"third\n")
        result = h.result(timeout=5)
        assert result.results == ["first", "second", "third"]
    finally:
        h.close()


def test_ac8_real_pasted_burst_is_joined():
    def target():
        import select
        import sys as _sys
        from sasha import paste_join

        _sys.stdin = _sys.__stdin__
        _sys.stdout = _sys.__stdout__

        def has_pending():
            r, _, _ = select.select([_sys.stdin], [], [], 0.01)
            return bool(r)

        results = []
        while len(results) < 1:
            try:
                line = paste_join.read_joined_input("P> ", input, has_pending)
            except EOFError:
                break
            results.append(line)
            print(f"GOT:{line!r}", flush=True)
        return {"results": results}

    h = spawn(target)
    try:
        h.read_until(b"P> ", timeout=5)
        h.write(b"one\ntwo\nthree\n")
        result = h.result(timeout=5)
        assert result.results == ["one\ntwo\nthree"]
    finally:
        h.close()


# --- AC9-10: real end-to-end against the actual `python -m sasha` ---------

@pytest.fixture
def sasha_env(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    env = dict(os.environ)
    env["HOME"] = str(home)
    env["XDG_CONFIG_HOME"] = str(tmp_path / "xdg-config")
    env["XDG_STATE_HOME"] = str(tmp_path / "xdg-state")
    env.pop("DEEPSEEK_API_KEY", None)
    src = str(REPO / "src")
    env["PYTHONPATH"] = src + (
        os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    return env


def test_ac9_real_sasha_joins_multiline_paste_into_one_command(tmp_path, sasha_env):
    cwd = str(tmp_path)

    def target():
        p = subprocess.Popen(
            [sys.executable, "-m", "sasha"],
            stdin=0, stdout=1, stderr=2, env=sasha_env, cwd=cwd)
        p.wait()
        return {"exit_code": p.returncode}

    h = spawn(target)
    try:
        h.read_until(b"$ ", timeout=15)
        # x=<marker> / echo $x pasted as ONE burst. The marker is echoed
        # once regardless (tty echo of the typed/pasted input itself), so
        # a plain "does the marker appear" check would pass vacuously even
        # when broken. Only a *correctly joined* paste (both lines run in
        # the SAME bash invocation) makes `echo $x` print the marker a
        # SECOND time; a split-into-two-commands paste gives the second
        # command's fresh Executor.run() subprocess an empty $x, so the
        # marker appears only once (the input echo).
        marker = b"PASTEJOINMARKER7f3a"
        h.write(b"x=" + marker + b"\necho $x\n")
        out = h.read_until(b"$ ", timeout=10)
        assert out.count(marker) >= 2, out
        h.write(b"exit\n")
    finally:
        h.close()


def test_ac10_piped_stdin_unaffected(sasha_env, tmp_path):
    r = subprocess.run(
        [sys.executable, "-m", "sasha"],
        input="echo one\necho two\nexit\n",
        capture_output=True, text=True, env=sasha_env, cwd=str(tmp_path),
        timeout=30)
    assert "one" in r.stdout
    assert "two" in r.stdout


# --- AC11: TerminalConfirmer wired to the same joined-input function ------

def test_ac11_confirmer_uses_the_same_joined_input_function(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg-state"))
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setattr(sys, "argv", ["sasha"])
    monkeypatch.setattr(sys, "stdin", __import__("io").StringIO(""))

    import sasha.__main__ as main_mod

    recorded = {}

    class SpyConfirmer:
        def __init__(self, input_fn, sink):
            recorded["input_fn"] = input_fn

    monkeypatch.setattr(main_mod, "TerminalConfirmer", SpyConfirmer)

    main_mod.main()

    assert recorded["input_fn"] is main_mod._joined_input
