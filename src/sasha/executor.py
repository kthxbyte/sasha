from __future__ import annotations

import dataclasses
import fcntl
import os
import pty
import re
import select
import shlex
import signal
import struct
import subprocess
import termios
import time


@dataclasses.dataclass(frozen=True)
class ExecResult:
    exit_code: int
    signal: int | None
    stderr_text: str
    stdout_text: str
    cwd_after: str
    duration_ms: int


_FILTERED_ENV_KEYS = frozenset({"SASHA_STATE_FD", "SHLVL", "_", "PWD", "OLDPWD"})

_CSI_RE = re.compile(b'\x1b\\[[0-?]*[ -/]*[@-~]')
_OSC_RE = re.compile(b'\x1b\\].*?(?:\x07|\x1b\\\\)')
_ESC_SINGLE_RE = re.compile(b'\x1b.')


def _sanitize(raw: bytes) -> str:
    text = _CSI_RE.sub(b'', raw)
    text = _OSC_RE.sub(b'', text)
    text = _ESC_SINGLE_RE.sub(b'', text)
    return text.decode('utf-8', errors='replace').replace('\r', '')


class Executor:
    def __init__(self, cwd: str, env: dict[str, str], *,
                 stderr_capture_bytes: int = 8192) -> None:
        self._cwd = os.path.realpath(cwd)
        self._env = dict(env)
        self._stderr_capture_bytes = stderr_capture_bytes

    @property
    def cwd(self) -> str:
        return self._cwd

    def run(self, line: str, capture_stdout: bool = False) -> ExecResult:
        read_fd, write_fd = os.pipe()
        fcntl.fcntl(write_fd, fcntl.F_SETFD, 0)

        wrapped = (
            f"trap 'pwd -P >&{write_fd}; "
            f'printf "\\0" >&{write_fd}; '
            f"export -p >&{write_fd}' EXIT; {line}"
        )

        child_env = dict(self._env)
        child_env["SASHA_STATE_FD"] = str(write_fd)

        err_master, err_slave = pty.openpty()
        out_master = None
        out_slave = None
        if capture_stdout:
            out_master, out_slave = pty.openpty()

        try:
            if os.isatty(2):
                s = struct.pack('HHHH', 0, 0, 0, 0)
                s = fcntl.ioctl(2, termios.TIOCGWINSZ, s)
                ws_rows, ws_cols = struct.unpack('HHHH', s)[:2]
                if ws_rows <= 0 or ws_cols <= 0:
                    ws_rows, ws_cols = 24, 80
            else:
                ws_rows, ws_cols = 24, 80
        except OSError:
            ws_rows, ws_cols = 24, 80

        winsize_packed = struct.pack('HHHH', ws_rows, ws_cols, 0, 0)
        fcntl.ioctl(err_slave, termios.TIOCSWINSZ, winsize_packed)
        if out_slave is not None:
            fcntl.ioctl(out_slave, termios.TIOCSWINSZ, winsize_packed)

        self._ws_rows = ws_rows
        self._ws_cols = ws_cols
        self._ws_masters = [err_master]
        if out_master is not None:
            self._ws_masters.append(out_master)

        old_sigwinch = signal.signal(signal.SIGWINCH, self._on_sigwinch)

        start = time.monotonic_ns()

        pid = os.fork()
        if pid == 0:
            try:
                os.setpgid(0, 0)
            except OSError:
                pass
            try:
                os.close(read_fd)
                os.close(err_master)
                if out_master is not None:
                    os.close(out_master)
                os.dup2(err_slave, 2)
                os.close(err_slave)
                if capture_stdout:
                    os.dup2(out_slave, 1)
                    os.close(out_slave)
                os.chdir(self._cwd)
                os.execve(
                    "/bin/bash",
                    ["/bin/bash", "-c", wrapped],
                    child_env,
                )
            except Exception:
                os._exit(127)

        os.close(write_fd)
        os.close(err_slave)
        if out_slave is not None:
            os.close(out_slave)

        try:
            os.setpgid(pid, 0)
        except OSError:
            pass

        err_capture = b""
        out_capture = b""
        side_data = b""

        watch_fds = [read_fd, err_master]
        if out_master is not None:
            watch_fds.append(out_master)

        old_sigint = signal.signal(signal.SIGINT, signal.SIG_IGN)
        try:
            while watch_fds:
                try:
                    readable, _, _ = select.select(watch_fds, [], [])
                except (OSError, ValueError):
                    break

                for fd in readable:
                    if fd == err_master:
                        try:
                            chunk = os.read(err_master, 4096)
                            if not chunk:
                                watch_fds.remove(err_master)
                            else:
                                os.write(2, chunk)
                                err_capture += chunk
                        except OSError:
                            watch_fds.remove(err_master)
                    elif fd == out_master:
                        try:
                            chunk = os.read(out_master, 4096)
                            if not chunk:
                                watch_fds.remove(out_master)
                            else:
                                os.write(1, chunk)
                                out_capture += chunk
                        except OSError:
                            watch_fds.remove(out_master)
                    elif fd == read_fd:
                        try:
                            chunk = os.read(read_fd, 4096)
                            if not chunk:
                                watch_fds.remove(read_fd)
                            else:
                                side_data += chunk
                        except OSError:
                            watch_fds.remove(read_fd)
        finally:
            signal.signal(signal.SIGINT, old_sigint)
            signal.signal(signal.SIGWINCH, old_sigwinch)

        _, status = os.waitpid(pid, 0)

        os.close(err_master)
        if out_master is not None:
            os.close(out_master)
        os.close(read_fd)

        end = time.monotonic_ns()
        duration_ms = (end - start) // 1_000_000

        exit_code_raw = os.waitstatus_to_exitcode(status)
        if exit_code_raw >= 0:
            exit_code = exit_code_raw
            signal_num = None
        else:
            signal_num = -exit_code_raw
            exit_code = 128 + signal_num

        if side_data:
            nul_idx = side_data.find(b"\0")
            if nul_idx >= 0:
                final_cwd_raw = side_data[:nul_idx].decode().strip()
                if final_cwd_raw:
                    self._cwd = os.path.realpath(final_cwd_raw)
                env_data = side_data[nul_idx + 1:].decode()
                self._env = self._parse_export_p(env_data)

        if len(err_capture) > self._stderr_capture_bytes:
            err_capture = err_capture[-self._stderr_capture_bytes:]
        stderr_text = _sanitize(err_capture)

        stdout_text = ""
        if capture_stdout:
            if len(out_capture) > self._stderr_capture_bytes:
                out_capture = out_capture[-self._stderr_capture_bytes:]
            stdout_text = _sanitize(out_capture)

        return ExecResult(
            exit_code=exit_code,
            signal=signal_num,
            stderr_text=stderr_text,
            stdout_text=stdout_text,
            cwd_after=self._cwd,
            duration_ms=duration_ms,
        )

    def _on_sigwinch(self, signum: int, frame: object) -> None:
        if os.isatty(2):
            try:
                s = struct.pack('HHHH', 0, 0, 0, 0)
                s = fcntl.ioctl(2, termios.TIOCGWINSZ, s)
                self._ws_rows, self._ws_cols = struct.unpack('HHHH', s)[:2]
            except OSError:
                pass
        winsize = struct.pack('HHHH', self._ws_rows, self._ws_cols, 0, 0)
        for master in self._ws_masters:
            try:
                fcntl.ioctl(master, termios.TIOCSWINSZ, winsize)
            except OSError:
                pass

    def _parse_export_p(self, text: str) -> dict[str, str]:
        result: dict[str, str] = {}
        for line in text.splitlines():
            line = line.strip()
            if not line.startswith("declare -x "):
                continue
            rest = line[len("declare -x "):]
            if "=" not in rest:
                continue
            try:
                parts = shlex.split(rest)
            except ValueError:
                continue
            if not parts:
                continue
            assignment = parts[0]
            name, _, value = assignment.partition("=")
            if not name:
                continue
            result[name] = value
        for key in _FILTERED_ENV_KEYS:
            result.pop(key, None)
        return result

    @staticmethod
    def check_syntax(line: str) -> str | None:
        result = subprocess.run(
            ["/bin/bash", "-n", "-c", line],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            return None
        return result.stderr.strip()
