"""PTY harness for job-control contract tests (Task 4.1).

Written by Tier 1; lives under tests/contract/ and is therefore immutable to
the implementer (verify.sh rejects any commit that touches it).

tcsetpgrp/SIGTTOU/SIGTTIN semantics cannot be exercised through pipes — they
only exist because a real terminal driver is routing signals to whichever
process group currently owns the terminal. This harness forks a subprocess,
gives it a fresh PTY as its controlling terminal (os.setsid() + TIOCSCTTY),
and runs a target callable inside it. The caller (the test) holds the PTY
master and can write real control characters (INTR, SUSP) to it, so the
kernel — not the test — performs the signal routing under test.
"""

from __future__ import annotations

import fcntl
import json
import os
import pty
import select
import termios
import time


class HarnessTimeout(Exception):
    pass


class HarnessResult:
    """Thin attribute view over the JSON dict the harness child reported."""

    def __init__(self, data: dict):
        self._data = data

    def __getattr__(self, name):
        try:
            return self._data[name]
        except KeyError as e:
            raise AttributeError(name) from e

    def __repr__(self):
        return f"HarnessResult({self._data!r})"


class Harness:
    """One harness child, its controlling PTY, and its result pipe."""

    def __init__(self, master_fd: int, result_read_fd: int, pid: int):
        self.master_fd = master_fd
        self.result_read_fd = result_read_fd
        self.pid = pid
        self._closed = False

    def write(self, data: bytes) -> None:
        os.write(self.master_fd, data)

    def read_until(self, marker: bytes, timeout: float = 5.0) -> bytes:
        """Block until `marker` has appeared in the harness child's output."""
        buf = b""
        deadline = time.monotonic() + timeout
        while marker not in buf:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise HarnessTimeout(
                    f"marker {marker!r} not seen; got {buf!r}")
            r, _, _ = select.select([self.master_fd], [], [], remaining)
            if not r:
                continue
            try:
                chunk = os.read(self.master_fd, 4096)
            except OSError:
                break
            if not chunk:
                break
            buf += chunk
        return buf

    def result(self, timeout: float = 10.0) -> HarnessResult:
        """Block until the harness child reports its JSON result line."""
        buf = b""
        deadline = time.monotonic() + timeout
        while b"\n" not in buf:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise HarnessTimeout(
                    f"no result reported in {timeout}s; got {buf!r}")
            r, _, _ = select.select([self.result_read_fd], [], [], remaining)
            if not r:
                continue
            chunk = os.read(self.result_read_fd, 4096)
            if not chunk:
                break
            buf += chunk
        line = buf.split(b"\n", 1)[0]
        return HarnessResult(json.loads(line.decode()))

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for fd in (self.master_fd, self.result_read_fd):
            try:
                os.close(fd)
            except OSError:
                pass
        # Best-effort reap; the target is responsible for exiting promptly.
        for _ in range(25):
            try:
                wpid, _ = os.waitpid(self.pid, os.WNOHANG)
            except ChildProcessError:
                return
            if wpid != 0:
                return
            time.sleep(0.02)
        try:
            os.kill(self.pid, 9)
            os.waitpid(self.pid, 0)
        except (ProcessLookupError, ChildProcessError):
            pass


def spawn(target) -> Harness:
    """Fork a child with a fresh controlling-terminal PTY and run `target()`
    inside it. `target` is a zero-arg callable returning a JSON-serializable
    dict; its return value becomes the eventual HarnessResult. Any exception
    raised by `target` is captured and reported as {"error": "<message>"}.

    Returns immediately after forking — `target()` runs concurrently in the
    child. Use the returned Harness to write terminal input and then block
    on `.result()`.
    """
    master_fd, slave_fd = pty.openpty()
    result_read, result_write = os.pipe()

    pid = os.fork()
    if pid == 0:
        try:
            os.close(master_fd)
            os.close(result_read)
            os.setsid()
            fcntl.ioctl(slave_fd, termios.TIOCSCTTY, 0)
            os.dup2(slave_fd, 0)
            os.dup2(slave_fd, 1)
            os.dup2(slave_fd, 2)
            if slave_fd > 2:
                os.close(slave_fd)
            try:
                data = target()
                payload = json.dumps(data).encode()
            except Exception as e:  # noqa: BLE001 — report, don't crash silently
                payload = json.dumps({"error": str(e)}).encode()
            os.write(result_write, payload + b"\n")
        finally:
            os._exit(0)

    os.close(slave_fd)
    os.close(result_write)
    return Harness(master_fd, result_read, pid)
