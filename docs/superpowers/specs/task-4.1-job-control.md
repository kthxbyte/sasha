# Task 4.1: Job control handoff (tcsetpgrp, Ctrl+Z termination, TUI passthrough)

## Authoritative contract
`docs/superpowers/specs/2026-07-14-sasha-v2-job-control-design.md` (all
sections) is the complete contract for this task. It amends
`docs/superpowers/specs/2026-07-14-sasha-design.md` §9.1/§9.3. This task
fixes ISSUE-015 (a foreground command like `yes` cannot be interrupted) and
ISSUE-016 (TUI programs like `vi`/`mc` get stopped before they can render)
by completing the process-group isolation `Executor.run()` already started
but never finished.

## What already exists
- `src/sasha/executor.py` (Tasks 1.3/1.4) — the relay loop (stderr always
  PTY-teed, stdout opt-in via `capture_stdout`), the cwd/env side-channel
  parsing, the `SIGWINCH` window-size propagation, `check_syntax()`. **Bind
  to all of this — do not rewrite the relay/capture/side-channel/winsize
  logic.** Only the sections named in the fix sketch below change.
- `src/sasha/repl.py` (Task 3.1/3.4) — `Repl.run_once`'s "otherwise" branch
  (`result = self._executor.run(stripped)` through the `decide(...)` call).
- `tests/contract/test_task_1_3_executor_core.py`,
  `tests/contract/test_task_1_4_executor_pty.py` — MUST stay green
  unmodified. They run under pytest's own (non-tty) stdin, so they already
  exercise the non-interactive fallback path this task adds; nothing in them
  should change behavior.
- `tests/contract/pty_harness.py` — new PTY test harness (already committed
  with this spec). Read it to understand how `test_task_4_1_job_control.py`
  drives real terminal signals.

## What's new
1. `Executor.run()` gains a job-control handoff: when Sasha's own stdin is a
   real controlling terminal, hand the terminal to the foreground child's
   process group before waiting, detect a stop (not just an exit) without
   blocking forever, terminate (never leave stopped) a Ctrl+Z'd child, and
   restore both the terminal and Sasha's own termios settings afterward.
2. `ExecResult` gains one field: `ctrl_z_terminated: bool = False`.
3. `Repl.run_once` gains one new branch that prints a dedicated message and
   skips the trigger engine when `ctrl_z_terminated` is set.

## Fix sketch (authoritative)

Replace `Executor.run()` in `src/sasha/executor.py` in its entirety with the
following. Everything else in the file (`ExecResult` gets the one new field
below, `_sanitize`, `_on_sigwinch`, `_parse_export_p`, `check_syntax`,
`__init__`, the `cwd` property) is unchanged.

```python
@dataclasses.dataclass(frozen=True)
class ExecResult:
    exit_code: int
    signal: int | None
    stderr_text: str
    stdout_text: str
    cwd_after: str
    duration_ms: int
    ctrl_z_terminated: bool = False
```

```python
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

        return ExecResult(
            exit_code=exit_code,
            signal=signal_num,
            stderr_text=stderr_text,
            stdout_text=stdout_text,
            cwd_after=self._cwd,
            duration_ms=duration_ms,
            ctrl_z_terminated=ctrl_z_terminated,
        )
```

Note: the previous `signal.signal(signal.SIGINT, signal.SIG_IGN)` bracket is
**kept**, not removed (the approved design's §4.1 said it becomes
unnecessary — true once the handoff succeeds, but it is cheap,
belt-and-suspenders for the `tcsetpgrp` `OSError`-fallback branch, and
keeping it minimizes the diff against working code). No other behavior
changes from the design doc.

In `src/sasha/repl.py`, in `Repl.run_once`'s "otherwise" branch, insert a new
check immediately after the existing `self._session.note_command(stripped,
result)` line and before the existing `decision = decide(stripped, result,
self._mutes)` line:

```python
        result = self._executor.run(stripped)
        self._session.note_command(stripped, result)

        if result.ctrl_z_terminated:
            self._out("[sasha] Ctrl+Z not supported — command terminated")
            return True

        decision = decide(stripped, result, self._mutes)
```

Everything else in `repl.py` is unchanged.

## Constraints
- Modify `src/sasha/executor.py` and `src/sasha/repl.py` only (plus optional
  supplementary tests under `tests/impl/`).
- Do not touch: `scripts/`, `docs/superpowers/`, `CLAUDE.md`, `AGENTS.md`,
  `PLAN.md`, `ISSUE-TRACKING.md`, `README.md`, `.gitignore`,
  `pyproject.toml`, `src/sasha/__init__.py`, `src/sasha/config.py`,
  `src/sasha/mutes.py`, `src/sasha/triggers.py`, `tests/contract/`.
- **Executor routing (per CLAUDE.md's standing rule): this is a surgical
  edit to code that already works, not a greenfield module. Dispatch to the
  default model (deepseek-v4-pro) — do not launch under a local-model
  `SASHA_DISPATCH_MODEL` override.**

## Acceptance criteria
(encoded 1:1 in `tests/contract/test_task_4_1_job_control.py`)
1. A foreground command's SIGINT (Ctrl+C, delivered by the kernel's terminal
   driver, not by a directly-targeted signal) reaches the child, not Sasha;
   `ExecResult.signal == SIGINT`, no lingering process —
   `test_ac1_sigint_reaches_child_via_terminal`.
2. Ctrl+Z (SUSP) never leaves a stopped job: the child is terminated, fully
   reaped, `ExecResult.ctrl_z_terminated == True`, no lingering process —
   `test_ac2_ctrl_z_terminates_and_reaps`.
3. The non-interactive (piped stdin) path is unaffected: the existing Task
   1.3/1.4 contract test files stay green unmodified — no new test, verified
   by running them as part of this task's own gate.
4. Sasha's own terminal settings are restored after `run()` even when the
   child was forcibly killed mid-raw-mode —
   `test_ac4_termios_restored_after_ctrl_z_kill`.
5. `Repl.run_once` prints the dedicated Ctrl+Z message and does not invoke
   the trigger engine when `ctrl_z_terminated` is set —
   `test_ac5_repl_prints_ctrl_z_message_and_skips_triggers`.

## Standing instructions
- Your goal: make `tests/contract/test_task_4_1_job_control.py` pass with
  `tests/contract/test_task_1_3_executor_core.py`,
  `tests/contract/test_task_1_4_executor_pty.py`, and
  `tests/contract/test_task_3_1_repl.py` still green:
  `PYTHONPATH=src python3 -m pytest tests/contract/test_task_1_3_executor_core.py tests/contract/test_task_1_4_executor_pty.py tests/contract/test_task_3_1_repl.py tests/contract/test_task_4_1_job_control.py`.
- NEVER modify files under `tests/contract/` (this includes `pty_harness.py`)
  — the gate rejects the commit if you do. If a contract test seems wrong or
  unimplementable, stop and flag it; do not commit.
- The fix sketch above is authoritative — implement it as given. If you
  believe a deviation is necessary, flag it in the commit message via
  `Deferred:` rather than silently diverging; a deviation that still passes
  the gate will be reviewed at the phase boundary regardless.
- Deliver exactly ONE new commit on top of the HEAD you started from; never
  amend, rebase, or reset; leave the working tree clean.
- Commit message per AGENTS.md, with trailers:
  `Done: Task 4.1 — <one line>`, `Fixed: ISSUE-015, ISSUE-016`,
  `Next: Task 4.2 — Phase 4 review (Tier 1, with Salvador)`, and the
  Co-Authored-By signature line from your dispatch prompt.
