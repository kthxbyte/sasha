from __future__ import annotations

import dataclasses
import fcntl
import os
import shlex
import signal
import subprocess
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


class Executor:
    def __init__(self, cwd: str, env: dict[str, str]) -> None:
        self._cwd = os.path.realpath(cwd)
        self._env = dict(env)

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

        start = time.monotonic_ns()

        pid = os.fork()
        if pid == 0:
            try:
                os.setpgid(0, 0)
            except OSError:
                pass
            try:
                os.close(read_fd)
                os.chdir(self._cwd)
                os.execve(
                    "/bin/bash",
                    ["/bin/bash", "-c", wrapped],
                    child_env,
                )
            except Exception:
                os._exit(127)

        os.close(write_fd)

        try:
            os.setpgid(pid, 0)
        except OSError:
            pass

        old_sigint = signal.signal(signal.SIGINT, signal.SIG_IGN)
        try:
            _, status = os.waitpid(pid, 0)
        finally:
            signal.signal(signal.SIGINT, old_sigint)

        end = time.monotonic_ns()
        duration_ms = (end - start) // 1_000_000

        data = b""
        while True:
            try:
                chunk = os.read(read_fd, 4096)
                if not chunk:
                    break
                data += chunk
            except OSError:
                break
        os.close(read_fd)

        exit_code_raw = os.waitstatus_to_exitcode(status)
        if exit_code_raw >= 0:
            exit_code = exit_code_raw
            signal_num = None
        else:
            signal_num = -exit_code_raw
            exit_code = 128 + signal_num

        if data:
            nul_idx = data.find(b"\0")
            if nul_idx >= 0:
                final_cwd_raw = data[:nul_idx].decode().strip()
                if final_cwd_raw:
                    self._cwd = os.path.realpath(final_cwd_raw)
                env_data = data[nul_idx + 1:].decode()
                self._env = self._parse_export_p(env_data)

        return ExecResult(
            exit_code=exit_code,
            signal=signal_num,
            stderr_text="",
            stdout_text="",
            cwd_after=self._cwd,
            duration_ms=duration_ms,
        )

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
