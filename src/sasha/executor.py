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
    ctrl_z_terminated: bool = False


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
        self._last_command: str | None = None
        self._last_result: ExecResult | None = None

    @property
    def cwd(self) -> str:
        return self._cwd

    @property
    def last_command(self) -> str | None:
        return self._last_command

    @property
    def last_result(self) -> ExecResult | None:
        return self._last_result

    def run(self, line: str, capture_stdout: bool = False) -> ExecResult:
        interactive = os.isatty(0)
        saved_termios = termios.tcgetattr(0) if interactive else None

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

        # --- job-control setup (interactive only) --------------------------
        sasha_pgid = None
        old_sigttou = None
        old_sigttin = None
        chld_read = None
        chld_write = None
        old_sigchld = None

        def _on_sigchld(signum, frame):
            # Async-signal-safe: single non-blocking write, nothing else.
            if chld_write is not None:
                try:
                    os.write(chld_write, b"\0")
                except OSError:
                    pass

        if interactive:
            sasha_pgid = os.getpgrp()
            old_sigttou = signal.signal(signal.SIGTTOU, signal.SIG_IGN)
            old_sigttin = signal.signal(signal.SIGTTIN, signal.SIG_IGN)
            chld_read, chld_write = os.pipe()
            os.set_blocking(chld_write, False)
            old_sigchld = signal.signal(signal.SIGCHLD, _on_sigchld)

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
                if chld_read is not None:
                    os.close(chld_read)
                if chld_write is not None:
                    os.close(chld_write)
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

        if interactive:
            try:
                os.tcsetpgrp(0, pid)
            except OSError:
                # No real controlling terminal despite isatty(0) — degrade to
                # the non-interactive path for the rest of this call.
                signal.signal(signal.SIGTTOU, old_sigttou)
                signal.signal(signal.SIGTTIN, old_sigttin)
                signal.signal(signal.SIGCHLD, old_sigchld)
                os.close(chld_read)
                os.close(chld_write)
                chld_read = None
                chld_write = None
                interactive = False

        err_capture = b""
        out_capture = b""
        side_data = b""

        watch_fds = [read_fd, err_master]
        if out_master is not None:
            watch_fds.append(out_master)
        if interactive:
            watch_fds.append(chld_read)

        stopped = False
        already_reaped = False
        reaped_status = None

        old_sigint = signal.signal(signal.SIGINT, signal.SIG_IGN)
        try:
            child_done = False
            while watch_fds and not child_done:
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
                    elif fd == chld_read:
                        try:
                            os.read(chld_read, 4096)
                        except OSError:
                            pass
                        wpid, wstatus = os.waitpid(
                            pid, os.WNOHANG | os.WUNTRACED)
                        if wpid == 0:
                            continue
                        if os.WIFSTOPPED(wstatus):
                            stopsig = os.WSTOPSIG(wstatus)
                            if stopsig in (signal.SIGTTIN, signal.SIGTTOU):
                                try:
                                    os.killpg(pid, signal.SIGCONT)
                                except OSError:
                                    pass
                                continue
                            stopped = True
                        else:
                            already_reaped = True
                            reaped_status = wstatus
                        child_done = True
                        break

            # Final short, non-blocking drain for bytes already buffered
            # before the loop above ended on a chld_read wake.
            if stopped or already_reaped:
                drain_fds = [f for f in (err_master, out_master, read_fd)
                             if f is not None]
                while drain_fds:
                    ready, _, _ = select.select(drain_fds, [], [], 0)
                    if not ready:
                        break
                    for fd in ready:
                        try:
                            chunk = os.read(fd, 4096)
                        except OSError:
                            chunk = b""
                        if not chunk:
                            drain_fds.remove(fd)
                            continue
                        if fd == err_master:
                            os.write(2, chunk)
                            err_capture += chunk
                        elif fd == out_master:
                            os.write(1, chunk)
                            out_capture += chunk
                        else:
                            side_data += chunk
        finally:
            signal.signal(signal.SIGINT, old_sigint)
            signal.signal(signal.SIGWINCH, old_sigwinch)

        ctrl_z_terminated = False

        if stopped:
            try:
                os.killpg(pid, signal.SIGCONT)
            except OSError:
                pass
            try:
                os.killpg(pid, signal.SIGTERM)
            except OSError:
                pass
            status = None
            for _ in range(50):
                wpid, wstatus = os.waitpid(pid, os.WNOHANG)
                if wpid != 0:
                    status = wstatus
                    break
                time.sleep(0.02)
            if status is None:
                try:
                    os.killpg(pid, signal.SIGKILL)
                except OSError:
                    pass
                _, status = os.waitpid(pid, 0)
            ctrl_z_terminated = True
        elif already_reaped:
            status = reaped_status
        else:
            _, status = os.waitpid(pid, 0)

        if interactive:
            try:
                os.tcsetpgrp(0, sasha_pgid)
            except OSError:
                pass
            signal.signal(signal.SIGTTOU, old_sigttou)
            signal.signal(signal.SIGTTIN, old_sigttin)
            signal.signal(signal.SIGCHLD, old_sigchld)
            os.close(chld_read)
            os.close(chld_write)

        if saved_termios is not None:
            try:
                termios.tcsetattr(0, termios.TCSADRAIN, saved_termios)
            except OSError:
                pass

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

        result = ExecResult(
            exit_code=exit_code,
            signal=signal_num,
            stderr_text=stderr_text,
            stdout_text=stdout_text,
            cwd_after=self._cwd,
            duration_ms=duration_ms,
            ctrl_z_terminated=ctrl_z_terminated,
        )
        self._last_command = line
        self._last_result = result
        return result

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
