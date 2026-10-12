"""Supplementary tests (not contract) — crash safety net.

Step 1: an exception escaping the executor's relay loop must still restore the
terminal and reap the child. Before this change that cleanup sat *after* the
try/finally in straight-line order, so any exception skipped it — leaving the
terminal in raw mode, the child unreaped, and the PTY fds leaked.

Steps 2-3: an internal error inside the REPL must repair the terminal, write a
crash bundle, and keep the session alive — but stop catching once the same
failure keeps recurring, so a systematic fault can't become an infinite loop.
"""

from __future__ import annotations

import dataclasses
import os
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "contract"))

from pty_harness import spawn  # noqa: E402

from sasha import config as config_mod  # noqa: E402
from sasha import guard  # noqa: E402


def _run_in_pty_with_relay_failure():
    """Fork a controlling-terminal PTY, set the terminal raw from inside the
    child command, then make the parent's stderr relay raise. Reports what the
    terminal and fd table looked like afterwards."""
    import termios

    from sasha.executor import Executor

    def open_fd_count() -> int:
        return len(os.listdir("/proc/self/fd"))

    before_attrs = termios.tcgetattr(0)
    before_fds = open_fd_count()

    executor = Executor(os.getcwd(), dict(os.environ))
    real_write = os.write

    def exploding_write(fd, data):
        if fd == 2 and b"EXPLODE_ME" in data:
            raise RuntimeError("injected relay failure")
        return real_write(fd, data)

    raised = None
    os.write = exploding_write
    try:
        # `stty raw -echo` puts the real terminal into raw mode from inside the
        # child, so restoration is observable; the second echo trips the relay.
        executor.run("stty raw -echo; echo EXPLODE_ME >&2")
    except BaseException as exc:  # noqa: BLE001 - reported below
        raised = exc
    finally:
        os.write = real_write

    after_attrs = termios.tcgetattr(0)

    return {
        "raised": type(raised).__name__ if raised is not None else None,
        "message": str(raised) if raised is not None else None,
        # cooked mode restored == ICANON and ECHO both back on
        "canonical_restored": bool(after_attrs[3] & termios.ICANON),
        "echo_restored": bool(after_attrs[3] & termios.ECHO),
        "attrs_equal": after_attrs == before_attrs,
        "fd_delta": open_fd_count() - before_fds,
        "last_result": executor.last_result is None,
    }


@pytest.fixture(scope="module")
def relay_failure_outcome():
    harness = spawn(_run_in_pty_with_relay_failure)
    try:
        return harness.result(timeout=30)
    finally:
        harness.close()


def test_terminal_is_restored_when_the_relay_raises(relay_failure_outcome):
    r = relay_failure_outcome
    assert getattr(r, "error", None) is None, r
    # the injected failure really did propagate (not silently swallowed)
    assert r.raised == "RuntimeError"
    assert "injected relay failure" in r.message
    # ...and the terminal the child left in raw mode was handed back cooked
    assert r.canonical_restored is True
    assert r.echo_restored is True
    assert r.attrs_equal is True


def test_no_fd_leak_when_the_relay_raises(relay_failure_outcome):
    r = relay_failure_outcome
    assert getattr(r, "error", None) is None, r
    assert r.fd_delta == 0, f"leaked {r.fd_delta} fds"


def test_failed_run_records_no_result(relay_failure_outcome):
    """A command that blew up mid-relay must not be reported as having run."""
    r = relay_failure_outcome
    assert getattr(r, "error", None) is None, r
    assert r.last_result is True


# --------------------------------------------------------------------------
# guard: signatures, describing, circuit breaker, bundles
# --------------------------------------------------------------------------

def _raise_nested():
    def inner():
        raise ValueError("deepest thing")

    inner()


def test_signature_points_at_the_deepest_frame():
    try:
        _raise_nested()
    except ValueError as exc:
        kind, filename, lineno = guard.signature(exc)
    assert kind == "ValueError"
    assert filename.endswith("test_crash_safety_net.py")
    assert lineno > 0
    # the deepest frame is `inner`, not this test function
    assert lineno != _raise_nested.__code__.co_firstlineno


def test_two_raises_from_the_same_site_share_a_signature():
    sigs = []
    for _ in range(2):
        try:
            _raise_nested()
        except ValueError as exc:
            sigs.append(guard.signature(exc))
    assert sigs[0] == sigs[1]


def test_describe_is_one_line_with_location():
    try:
        _raise_nested()
    except ValueError as exc:
        text = guard.describe(exc)
    assert "ValueError" in text
    assert "deepest thing" in text
    assert "test_crash_safety_net.py" in text
    assert "\n" not in text


def test_circuit_breaker_trips_at_the_limit():
    breaker = guard.CircuitBreaker(limit=3)
    sig = ("RuntimeError", "/x/y.py", 10)
    assert breaker.record(sig) is False
    assert breaker.record(sig) is False
    assert breaker.record(sig) is True
    assert breaker.count(sig) == 3


def test_circuit_breaker_tracks_signatures_independently():
    breaker = guard.CircuitBreaker(limit=2)
    a = ("ValueError", "/a.py", 1)
    b = ("TypeError", "/b.py", 2)
    assert breaker.record(a) is False
    assert breaker.record(b) is False
    assert breaker.record(a) is True
    assert breaker.count(b) == 1


def test_write_crash_bundle_records_context_and_traceback(tmp_path):
    try:
        _raise_nested()
    except ValueError as exc:
        path = guard.write_crash_bundle(
            tmp_path, exc, context={"cwd": "/somewhere", "profile": "local"})

    assert path is not None and path.exists()
    assert path.parent.name == "crashes"
    text = path.read_text()
    assert "ValueError" in text
    assert "deepest thing" in text
    assert "cwd: /somewhere" in text
    assert "profile: local" in text
    assert "# traceback" in text
    # a machine-readable twin alongside it
    assert path.with_suffix(".json").exists()


def test_write_crash_bundle_without_a_state_dir_is_a_no_op():
    try:
        _raise_nested()
    except ValueError as exc:
        assert guard.write_crash_bundle(None, exc, context={}) is None


def test_write_crash_bundle_never_raises_on_an_unwritable_dir(tmp_path):
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("i am a file")
    try:
        _raise_nested()
    except ValueError as exc:
        assert guard.write_crash_bundle(blocker, exc, context={}) is None


def test_git_state_is_best_effort(tmp_path):
    # not a git repo: must return an empty/partial dict, never raise
    assert isinstance(guard.git_state(tmp_path), dict)


# --------------------------------------------------------------------------
# TerminalSink: repairing a half-rendered screen
# --------------------------------------------------------------------------

def _sink_for_repair(monkeypatch, no_color=False):
    from sasha.repl import TerminalSink

    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    if no_color:
        monkeypatch.setenv("NO_COLOR", "1")
    else:
        monkeypatch.delenv("NO_COLOR", raising=False)
    return TerminalSink(reasoning_mode="compact")


def test_repair_clears_an_open_chunk_run_and_ends_the_line(monkeypatch, capsys):
    sink = _sink_for_repair(monkeypatch)
    sink.write_chunk("half an answer")        # no trailing newline
    assert sink._chunk_run_kind == "llm"

    capsys.readouterr()
    sink.repair_terminal_state()
    out = capsys.readouterr().out

    assert "\n" in out
    assert sink._ends_in_newline is True
    assert sink._chunk_run_kind is None


def test_repair_settles_a_half_drawn_reasoning_preview(monkeypatch, capsys):
    sink = _sink_for_repair(monkeypatch)
    sink.write_reasoning_chunk("completed first line\nbody of paragraph")
    assert sink._preview_shown is True

    capsys.readouterr()
    sink.repair_terminal_state()

    assert sink._preview_shown is False
    assert sink._reasoning_buf == ""
    assert sink._ends_in_newline is True


def test_repair_is_idempotent_and_silent_when_nothing_is_wrong(monkeypatch, capsys):
    sink = _sink_for_repair(monkeypatch)
    sink.repair_terminal_state()
    sink.repair_terminal_state()
    assert sink._ends_in_newline is True


# --------------------------------------------------------------------------
# the net in Repl.loop()
# --------------------------------------------------------------------------

def _crash_repl(monkeypatch, tmp_path, *, limit=3, inputs=6):
    from sasha import repl as repl_mod
    from sasha.repl import Repl, TerminalSink
    from sasha.session import Session

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    cfg = dataclasses.replace(config_mod.load(None),
                              directory_recap_enabled=False)
    monkeypatch.setattr(repl_mod.memory, "resolve_git_root", lambda cwd: None)

    class _Ex:
        cwd = str(tmp_path)

    pending = iter(["input-%d" % i for i in range(inputs)])

    def input_fn(prompt):
        try:
            return next(pending)
        except StopIteration:
            raise EOFError

    state_dir = tmp_path / "state"
    state_dir.mkdir()
    sink = TerminalSink()
    repl = Repl(cfg, _Ex(), Session(cfg.limits.history_tokens),
                client_factory=lambda p: None, confirmer=None,
                input_fn=input_fn, sink=sink, state_dir=state_dir,
                breaker=guard.CircuitBreaker(limit=limit))
    return repl, state_dir


def test_session_survives_a_one_off_internal_error(monkeypatch, tmp_path, capsys):
    repl, state_dir = _crash_repl(monkeypatch, tmp_path, limit=3, inputs=2)
    calls = {"n": 0}

    def flaky(line):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("one-off fault")
        return True

    monkeypatch.setattr(repl, "run_once", flaky)
    repl.loop()                                   # must not raise

    assert calls["n"] == 2                        # it really did continue
    out = capsys.readouterr().out
    assert "internal error" in out
    assert "still alive" in out
    assert len(list((state_dir / "crashes").glob("*.txt"))) == 1


def test_repeated_identical_failure_stops_instead_of_looping(monkeypatch, tmp_path,
                                                            capsys):
    repl, state_dir = _crash_repl(monkeypatch, tmp_path, limit=3, inputs=6)
    calls = {"n": 0}

    def always_boom(line):
        calls["n"] += 1
        raise RuntimeError("systematic fault")

    monkeypatch.setattr(repl, "run_once", always_boom)
    repl.loop()

    # Six inputs were available; the breaker stopped it at three.
    assert calls["n"] == 3
    out = capsys.readouterr().out
    assert "3 times" in out
    assert "exiting instead of looping" in out
    assert len(list((state_dir / "crashes").glob("*.txt"))) == 3


def test_bundle_path_is_reported_to_the_user(monkeypatch, tmp_path, capsys):
    repl, state_dir = _crash_repl(monkeypatch, tmp_path, limit=1, inputs=1)

    monkeypatch.setattr(repl, "run_once",
                        lambda line: (_ for _ in ()).throw(RuntimeError("x")))
    repl.loop()

    out = capsys.readouterr().out
    bundles = list((state_dir / "crashes").glob("*.txt"))
    assert len(bundles) == 1
    assert str(bundles[0]) in out


def test_eoferror_still_exits_and_is_not_treated_as_a_crash(monkeypatch, tmp_path,
                                                            capsys):
    repl, state_dir = _crash_repl(monkeypatch, tmp_path, limit=3)

    def close_stdin(line):
        raise EOFError

    monkeypatch.setattr(repl, "run_once", close_stdin)
    repl.loop()

    assert not (state_dir / "crashes").exists()
    assert "internal error" not in capsys.readouterr().out


def test_keyboardinterrupt_still_continues_and_is_not_a_crash(monkeypatch, tmp_path,
                                                              capsys):
    repl, state_dir = _crash_repl(monkeypatch, tmp_path, limit=3, inputs=2)
    calls = {"n": 0}

    def interrupt_once(line):
        calls["n"] += 1
        if calls["n"] == 1:
            raise KeyboardInterrupt
        return True

    monkeypatch.setattr(repl, "run_once", interrupt_once)
    repl.loop()

    assert calls["n"] == 2
    assert not (state_dir / "crashes").exists()
    assert "internal error" not in capsys.readouterr().out


def test_crash_context_carries_the_recent_commands(monkeypatch, tmp_path):
    from sasha.executor import ExecResult

    repl, _ = _crash_repl(monkeypatch, tmp_path)
    repl._session.note_command("ls -la", ExecResult(
        exit_code=0, signal=None, stderr_text="", stdout_text="",
        cwd_after=str(tmp_path), duration_ms=1))

    context = repl._crash_context()
    assert "ls -la" in context["recent_commands"]
    assert context["cwd"] == str(tmp_path)
    assert context["profile"]
