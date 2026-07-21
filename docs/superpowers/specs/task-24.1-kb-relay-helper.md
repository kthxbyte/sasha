# Task 24.1: Foreground-group keystroke relay helper (ISSUE-040)

## Authoritative contract
`docs/superpowers/specs/2026-07-21-issue-040-foreground-relay-helper-design.md`
(all sections) is the complete contract for this task. This task fixes
ISSUE-040 (pagers and other fd-2-for-terminal-control programs never
receive real keystrokes when run inside sasha) by relaying real keyboard
input from fd 0 into whichever capture PTY a child has put into raw mode,
via a small helper process that shares the child's foreground process
group — the only way to satisfy both "child must be foreground for
correct signal delivery" and "something must read fd 0."

## What already exists
- `src/sasha/executor.py` (Task 1.3/4.1/20.1/21.1) — the full job-control
  handoff (`tcsetpgrp`, `SIGCHLD` self-pipe, stderr-always/stdout-opt-in
  PTY tee, cwd/env side-channel via `SASHA_STATE_FD`, `SIGWINCH`
  propagation, SIGTTIN/SIGTTOU ignore + stop-cause classification).
  **Bind to all of this — do not rewrite the relay/capture/side-channel/
  winsize/handoff-sequencing logic.** Only the lines named in the fix
  sketch below change.
- `tests/contract/test_task_4_1_job_control.py`,
  `tests/contract/test_task_1_3_executor_core.py`,
  `tests/contract/test_task_1_4_executor_pty.py`,
  `tests/contract/test_task_20_1_pager_keystrokes.py` — MUST stay green
  unmodified.
- `tests/contract/pty_harness.py` — reused unmodified, same as every
  prior attempt on this issue.

## What's new
1. **`src/sasha/_kb_relay_helper.py`** (new file) — the standalone relay
   helper script.
2. **`src/sasha/executor.py`** — `err_master`/`out_master` survive the
   child's `execve` (interactive only); the wrapped bash script launches
   the helper and arranges its cleanup.

## Fix sketch (authoritative — verified directly)

This has been verified directly and extensively (Tier 1 built an
isolated minimal repro of the core assumption *before* writing the
design doc, then implemented this exact fix in a scratch git worktree
and ran the full contract suite — 564/564 tests green, including all 3
new ACs — plus a real end-to-end manual test against the actual `less`
program with real arrow-key and `q` keystrokes sent through a pty,
confirming `less` exits cleanly on `q`) before being committed as
authoritative. **Two real bugs were caught and fixed during that
verification, both worth flagging explicitly so they aren't
reintroduced:**

1. **`termios.error` is not a subclass of `OSError`** in Python — an
   `except OSError:` around a `termios.tcsetattr`/`tcgetattr` call does
   NOT catch it. (This exact gotcha was already documented once before,
   during the previous — abandoned — relay design's implementation
   attempt; it recurred here because it's a genuinely easy mistake to
   make, not because the earlier note was overlooked.) The fix sketch
   below catches `(OSError, termios.error)` everywhere a termios call
   might fail.
2. **bash redirects a backgrounded job's stdin to `/dev/null`** when job
   control isn't active in the script (always true for
   `Executor.run()`'s non-interactive wrapper), *unless* an explicit
   redirect is already given for that job. Without an explicit `<&0`,
   the relay helper's fd 0 would silently become `/dev/null` instead of
   the real terminal, and every termios/read call on it would fail with
   `ENOTTY` ("Inappropriate ioctl for device") — this is exactly what
   surfaced during verification, traced back to this bash behavior, not
   a mystery left unresolved.

**New file — `src/sasha/_kb_relay_helper.py`:**

```python
#!/usr/bin/env python3
"""Foreground-group keystroke relay helper (ISSUE-040, Task 24.1).

Invoked by Executor.run()'s wrapped bash script as:
    python3 _kb_relay_helper.py <err_master_fd> [<out_master_fd>]

Runs in the same process group as the interactive command it accompanies
(started via bash '&', which does not create a new pgrp for a
non-interactive script), so it can read fd 0 where Sasha's own
(background, post-tcsetpgrp) process cannot. See
docs/superpowers/specs/2026-07-21-issue-040-foreground-relay-helper-design.md
for the full design and rationale.
"""

import os
import select
import signal
import sys
import termios

signal.signal(signal.SIGINT, signal.SIG_IGN)
signal.signal(signal.SIGTSTP, signal.SIG_IGN)

_ORIGINAL_PPID = os.getppid()


def _is_raw(fd: int) -> bool | None:
    try:
        attrs = termios.tcgetattr(fd)
    except (OSError, termios.error):
        return None
    return not (attrs[3] & termios.ICANON)


def main() -> None:
    master_fds = [int(a) for a in sys.argv[1:]]
    triggered: set[int] = set()
    fd0_added = False

    while master_fds:
        if os.getppid() != _ORIGINAL_PPID:
            return

        for fd in list(master_fds):
            if fd in triggered:
                continue
            raw = _is_raw(fd)
            if raw is None:
                master_fds.remove(fd)
                triggered.discard(fd)
                continue
            if raw:
                triggered.add(fd)
                if not fd0_added:
                    try:
                        termios.tcsetattr(0, termios.TCSANOW,
                                           termios.tcgetattr(fd))
                    except (OSError, termios.error):
                        pass
                    fd0_added = True

        if not master_fds:
            return

        watch = [0] if fd0_added else []
        try:
            r, _, _ = select.select(watch, [], [], 0.05)
        except OSError:
            return

        if 0 in r:
            try:
                chunk = os.read(0, 4096)
            except OSError:
                return
            if not chunk:
                return
            for fd in list(triggered):
                try:
                    os.write(fd, chunk)
                except (OSError, BlockingIOError):
                    pass


if __name__ == "__main__":
    main()
```

**`src/sasha/executor.py`** — in `Executor.run()`:

1. Add `import pathlib` and `import sys` to the top-level imports
   (alongside the existing `dataclasses`/`fcntl`/`os`/`pty`/etc.).

2. Move the existing `wrapped = (...)` construction to *after* the
   `err_master, err_slave = pty.openpty()` / `out_master`/`out_slave`
   block (it now depends on those fd numbers), and insert the relay
   setup between them:

```python
        child_env = dict(self._env)
        child_env["SASHA_STATE_FD"] = str(write_fd)

        err_master, err_slave = pty.openpty()
        out_master = None
        out_slave = None
        if capture_stdout:
            out_master, out_slave = pty.openpty()

        # --- keystroke relay helper (interactive only) ----------------------
        # See docs/superpowers/specs/2026-07-21-issue-040-foreground-relay-helper-design.md.
        # err_master (and out_master) must survive the child's execve so a
        # helper sharing the child's foreground process group can relay real
        # keystrokes into whichever one a program (e.g. a pager) puts into
        # raw mode -- Sasha's own process cannot read fd 0 once it hands
        # foreground-of-terminal status to the child.
        relay_prefix = ""
        if interactive:
            fcntl.fcntl(err_master, fcntl.F_SETFD, 0)
            relay_fds = [str(err_master)]
            if out_master is not None:
                fcntl.fcntl(out_master, fcntl.F_SETFD, 0)
                relay_fds.append(str(out_master))
            helper_path = str(
                pathlib.Path(__file__).resolve().parent / "_kb_relay_helper.py")
            # bash redirects a backgrounded job's stdin to /dev/null when
            # job control isn't active (always true here, a non-interactive
            # script) unless an explicit redirect is already given -- <&0
            # overrides that so the helper keeps the real fd 0.
            relay_prefix = (
                f"{shlex.quote(sys.executable)} {shlex.quote(helper_path)} "
                f"{' '.join(relay_fds)} <&0 & __sasha_relay_pid=$!; "
            )

        wrapped = (
            relay_prefix
            + "trap '"
            + ("kill $__sasha_relay_pid 2>/dev/null; " if interactive else "")
            + f"pwd -P >&{write_fd}; "
            f'printf "\\0" >&{write_fd}; '
            f"export -p >&{write_fd}' EXIT; {line}"
        )
```

3. In the child branch (`if pid == 0:`), change the unconditional
   `os.close(err_master)` / `os.close(out_master)` to only close them
   when *not* interactive (so they survive into the `execve` when they
   need to):

```python
                os.close(read_fd)
                if not interactive:
                    os.close(err_master)
                    if out_master is not None:
                        os.close(out_master)
                if chld_read is not None:
```
(everything else in the child branch — `os.dup2(err_slave, 2)`,
`capture_stdout` handling, `os.chdir`, `os.execve` — unchanged).

No other part of `Executor.run()` changes — `ExecResult`, the main
relay-loop `select()` body, job-control setup/teardown, `SIGWINCH`
handling, and the cwd/env side-channel parsing are all untouched.

## Constraints
- Modify `src/sasha/executor.py` only (plus the one new file). Optional
  supplementary tests may go under `tests/impl/`.
- Do not touch: `scripts/`, `docs/superpowers/`, `CLAUDE.md`, `AGENTS.md`,
  `PLAN.md`, `ISSUE-TRACKING.md`, `README.md`, `.gitignore`,
  `pyproject.toml`, `src/sasha/__init__.py`, `src/sasha/repl.py`,
  `src/sasha/config.py`, `src/sasha/mutes.py`, `src/sasha/triggers.py`,
  `src/sasha/agent.py`, `src/sasha/tools.py`, `src/sasha/safe_commands.py`,
  `src/sasha/tool_log.py`, `tests/contract/`.
- Do not add busy-star/`Repl` background-task-counter wiring for the
  relay helper — deliberately out of scope (design doc §2/§6).
- Do not add an automated test for signal delivery during an active
  relay — deliberately out of scope (design doc §2/§6); manual
  verification only.
- **Executor routing (per CLAUDE.md's standing rule): this is a
  surgical edit to code that already works (`executor.py`), even though
  `_kb_relay_helper.py` is new. Dispatch to the default model
  (deepseek-v4-pro) — do not launch under a local-model
  `SASHA_DISPATCH_MODEL` override.**

## Acceptance criteria
(encoded 1:1 in `tests/contract/test_task_24_1_kb_relay_helper.py`)
1. A program that raw-modes fd 2 (like a pager) receives a real keystroke
   typed at the real terminal — `test_ac1_fd2_raw_mode_receives_real_keystroke`.
2. A program reading fd 0 directly (mimicking a TUI's convention, e.g.
   `vi`) is completely unaffected — no interference from the relay
   mechanism — `test_ac2_fd0_direct_reader_unaffected`.
3. No relay-helper process lingers after `Executor.run()` returns —
   `test_ac3_no_lingering_relay_helper_process`.
4. Non-interactive commands are unaffected — no new test; the entire
   existing contract suite (561 tests as of this task) stays green
   unmodified as part of this task's own gate, since none of them touch
   a capture PTY's termios, so the relay mechanism never activates for
   any of them.

## Standing instructions
- Your goal: make `tests/contract/test_task_24_1_kb_relay_helper.py`
  pass, with `tests/contract/test_task_1_3_executor_core.py`,
  `tests/contract/test_task_1_4_executor_pty.py`,
  `tests/contract/test_task_4_1_job_control.py`, and
  `tests/contract/test_task_20_1_pager_keystrokes.py` still green:
  `PYTHONPATH=src python3 -m pytest tests/contract/test_task_1_3_executor_core.py tests/contract/test_task_1_4_executor_pty.py tests/contract/test_task_4_1_job_control.py tests/contract/test_task_20_1_pager_keystrokes.py tests/contract/test_task_24_1_kb_relay_helper.py`.
  Simplest to just run the full suite instead, since that's what
  `verify.sh` actually gates on: `bash scripts/run-tests.sh`.
- NEVER modify files under `tests/contract/` — the gate rejects the
  commit if you do. If a contract test seems wrong or unimplementable,
  stop and flag it; do not commit.
- The fix sketch above is authoritative and has already been verified
  to pass the full suite *and* a real manual test against `less` —
  implement it exactly as given. If you believe a deviation is
  necessary, flag it in the commit message via `Deferred:` rather than
  silently diverging.
- Deliver exactly ONE new commit on top of the HEAD you started from;
  never amend, rebase, or reset; leave the working tree clean.
- Commit message per AGENTS.md, with trailers: `Done: Task 24.1 — <one
  line>`, `Fixed: ISSUE-040`,
  `Next: Task 24.2 — Phase 24 review (Tier 1, with Salvador)`, and the
  Co-Authored-By signature line from your dispatch prompt.
