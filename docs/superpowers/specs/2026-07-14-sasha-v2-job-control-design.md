# Sasha v2 — Job Control Design (ISSUE-015 / ISSUE-016)

**Status:** Draft for review
**Date:** 2026-07-14
**Amends:** `docs/superpowers/specs/2026-07-14-sasha-design.md` §4, §5.1, §9.1, §9.3, §12

## 1. Mission

Fix `Executor.run()` so a foreground child actually receives the terminal's
job-control signals. v1 spawns the child into its own process group but never
hands it the controlling terminal (`tcsetpgrp`), so Ctrl+C and Ctrl+Z are
delivered to Sasha's own (ignoring) process instead of the child — this is
ISSUE-015 (an infinite command like `yes` cannot be interrupted) and the
primary cause of ISSUE-016 (a TUI program like `vi` gets `SIGTTOU`/`SIGTTIN`
stopped before it can draw, because it is a background process group relative
to the terminal). This design completes the process-group isolation v1
already started.

## 2. Scope

### In scope:

- Proper `tcsetpgrp` handoff of the controlling terminal to a foreground
  child's process group, and back to Sasha when the child is done.
- Detecting a *stopped* (not just exited) child without blocking forever.
- A defined, deterministic policy for Ctrl+Z, since v1 has no `fg`/`bg`/`jobs`
  to resume a stopped job.
- A terminal-state safety net so a forcibly-killed raw-mode program can never
  leave Sasha's own prompt broken.
- Graceful fallback when Sasha has no controlling terminal at all (piped
  stdin — test harnesses, CI, non-interactive invocation).

### Out of scope / non-goals:

- Real job control: no `fg`, `bg`, `jobs`, no resuming a stopped process.
  (Unchanged from v1 §2 non-goals.)
- Guaranteeing every ncurses/TUI program renders correctly. The handoff fixes
  the mechanism that stops them from rendering at all; actual visual
  correctness of third-party programs is not something Sasha can guarantee
  and is accepted as a known residual gap (§7).
- Background jobs (`cmd &`) started directly at the Sasha prompt — v1 already
  only supports one foreground child per line; that is unchanged.
- Any change to `capture_stdout` semantics, ANSI sanitization, or the
  existing stderr/stdout PTY tee relay — those are correct as specified in
  design §9.1 and untouched here.

## 3. Key contracts & assumptions

1. **Job-control failure must never break command execution.** If
   `tcsetpgrp` or any step of the handoff raises `OSError` (no controlling
   terminal despite `isatty(0)`, sandboxed environment, etc.), `run()` logs
   nothing user-visible and falls back to v1's behavior for that single call
   (no handoff, child still runs to completion, output still relays
   correctly) — mirrors design contract 3's "LLM failing never breaks the
   shell," applied to job control.
2. **The handoff is attempted only when Sasha itself has a controlling
   terminal:** guarded by `os.isatty(0)` at the top of `run()`. Piped/redirected
   stdin (contract tests without a PTY harness, CI) skips the whole mechanism.
3. **A stopped child is always resolved to termination**, never left running
   in the background — no orphaned stopped processes (§4.3).
4. **Sasha's own terminal settings are restored after every `run()` call**,
   regardless of how the child ended (exit, signal, or Sasha-initiated
   termination).

## 4. Mechanism

### 4.1 Handoff sequence

Extends design §9.1 step 2 (spawn) and step 5 (reap). All new steps are
skipped entirely when `os.isatty(0)` is `False` at the start of `run()`.

1. Before spawn: `sasha_pgid = os.getpgrp()`; `saved_termios =
   termios.tcgetattr(0)`; install `SIGTTOU` handler `SIG_IGN` (saving the old
   one) — needed because the *restoring* `tcsetpgrp` call in step 4 happens
   while Sasha is not yet foreground again; also perform §4.2 step 1 (self-pipe
   + `SIGCHLD` handler setup) here, in the same "before spawn" window.
2. Fork and `setpgid` as in v1 (unchanged). Once the parent's `os.setpgid(pid,
   0)` call (already present) returns, call `os.tcsetpgrp(0, pid)` to give the
   child the terminal. Any `OSError` here triggers the fallback in contract 1:
   catch it, tear down everything the interactive path had already set up
   (restore both `SIGTTOU` and `SIGCHLD` handlers, close the self-pipe fds,
   drop `chld_read` from the eventual `watch_fds`) and proceed exactly as v1's
   `run()` did — the relay loop still runs, just without §4.2's stop-detection,
   and termios is still restored in the `finally` (step 5) since that part is
   unconditional and harmless even when nothing changed the terminal mode.
3. Relay loop (§4.2) runs, ending in either a normal exit/signal-death or a
   detected stop (§4.3).
4. After the loop: `os.tcsetpgrp(0, sasha_pgid)` to take the terminal back;
   restore the previous `SIGTTOU` handler.
5. `finally` (always, on every exit path including the fallback of step 2):
   `termios.tcsetattr(0, termios.TCSADRAIN, saved_termios)`.

The existing `signal.signal(signal.SIGINT, signal.SIG_IGN)` bracket in v1's
`run()` is removed — it is now unnecessary. Once the handoff is correct,
Sasha structurally stops receiving the terminal's `SIGINT`/`SIGTSTP` while the
child is foreground, because it is no longer the foreground process group.

### 4.2 Detecting a stop without blocking forever

v1's relay loop only ends when every watched fd hits EOF, which happens when
the child exits and closes its fds — but a *stopped* child holds its fds open
indefinitely, so the v1 loop would never notice a Ctrl+Z and would hang.

Fix: a self-pipe fed by a `SIGCHLD` handler, added to the same `select()`
watch set used for stderr/stdout/side-channel (design §9.1 step 3):

1. Before spawn (interactive path only): `chld_read, chld_write =
   os.pipe()`; `os.set_blocking(chld_write, False)`; install a `SIGCHLD`
   handler that does a best-effort single-byte `os.write(chld_write, b"\0")`
   wrapped in `try/except OSError: pass` (async-signal-safe — no other work in
   the handler). Add `chld_read` to `watch_fds`.
2. On `chld_read` becoming readable: drain it, then
   `wpid, status = os.waitpid(pid, os.WNOHANG | os.WUNTRACED)`.
   - `wpid == 0`: spurious wake (e.g. a `SIGCONT` we sent ourselves,
     coalesced signals) — continue the loop.
   - `os.WIFSTOPPED(status)`: the child was stopped (Ctrl+Z). Break the loop
     immediately; any output not yet drained from the other fds is simply
     not read (the job is about to be terminated anyway) — proceed to §4.3.
   - Otherwise (exited or killed by signal): the child is already reaped;
     record `status`, break the loop, proceed to result-building — skip the
     `os.waitpid` call at the end of v1's `run()` (already done here).
3. If the loop instead ends normally via EOF-draining (interactive child that
   exited cleanly before the `SIGCHLD` wake was processed, or the
   non-interactive fallback path where this whole mechanism is absent):
   fall back to v1's plain blocking `os.waitpid(pid, 0)`.
4. Teardown (interactive path): restore the previous `SIGCHLD` handler, close
   `chld_read`/`chld_write`.

### 4.3 Ctrl+Z policy

Per the agreed decision (§6), a stopped child is never left running or
resumable — v1 has no `fg`/`bg` to do anything useful with it, and leaving it
stopped in the background is exactly the orphaned-process symptom ISSUE-016
already reports.

On a detected stop (§4.2 step 2): `os.killpg(pid, signal.SIGCONT)` (a stopped
process cannot act on a further signal until resumed) immediately followed by
`os.killpg(pid, signal.SIGTERM)` — targeting the whole process group so any
grandchildren die too — then a blocking `os.waitpid(pid, 0)` to collect the
real final exit status. `ExecResult.ctrl_z_terminated` is set `True`.

### 4.4 Interface change

`ExecResult` (design §5.1) gains one field, added at the end with a default
so existing positional construction is unaffected:

```python
@dataclasses.dataclass(frozen=True)
class ExecResult:
    exit_code: int
    signal: int | None
    stderr_text: str
    stdout_text: str
    cwd_after: str
    duration_ms: int
    ctrl_z_terminated: bool = False   # NEW
```

`Executor.run()`'s signature is unchanged.

`Repl.run_once` (`src/sasha/repl.py`) gains one new branch, checked
immediately after `result = self._executor.run(stripped)` and *before* the
existing `decide(...)` call: if `result.ctrl_z_terminated`, print
`[sasha] Ctrl+Z not supported — command terminated` and return, bypassing
`triggers.decide()` entirely for that line. This is necessary because the
resulting exit code (`128 + SIGTERM`) would otherwise be misclassified by the
trigger matrix as an ordinary command failure (T3 → OFFER) rather than a
Sasha-initiated termination — `triggers.py` itself is not modified.

No other module's public interface changes.

## 5. Error vocabulary addition

| Event | Line |
|---|---|
| Ctrl+Z on a foreground command | `[sasha] Ctrl+Z not supported — command terminated` |

(Extends design §10; all other rows unchanged.)

## 6. Decisions log

- **Decision:** Complete v1's process-group isolation with a real
  `tcsetpgrp` handoff, rather than dropping the isolation and relying on
  shared-pgrp signal delivery.
  **Rationale:** The shared-pgrp alternative is less code, but it reintroduces
  a correctness gap — a backgrounded grandchild left behind by one line (e.g.
  `sleep 100 &`) would sit in Sasha's own pgrp and catch a *later* line's
  Ctrl+C. Per-line pgrp isolation, which v1 already started via `setpgid`,
  avoids this and matches how bash/zsh implement job control.
- **Decision:** A stopped (Ctrl+Z'd) foreground child is always terminated
  (`SIGCONT` + `SIGTERM`), never left stopped in the background.
  **Rationale:** v1 has no `fg`/`bg`/`jobs` to ever resume it (stated
  non-goal), and leaving it stopped is the exact orphaned-process symptom
  ISSUE-016 reports. Termination is the only option that leaves no dangling
  state.
- **Decision:** TUI program rendering (ISSUE-016) is fixed best-effort by the
  job-control handoff; visual correctness of third-party ncurses programs is
  not a v2 acceptance criterion, only the mechanism (no longer being
  `SIGTTOU`/`SIGTTIN`-stopped as a background process group).
  **Rationale:** Salvador's call — already understood as a v1 design
  limitation; the handoff removes the specific bug that made every TUI
  program fail immediately, without committing to full terminal-emulation
  fidelity for arbitrary third-party programs.
- **Decision:** Stop/exit detection uses a `SIGCHLD` self-pipe merged into the
  existing `select()` loop, not a polling timeout.
  **Rationale:** No added latency, no wasted CPU, consistent with the
  already-reactor-shaped relay loop in design §9.1.
- **Decision:** ISSUE-014 (output sink redesign) is explicitly out of scope
  for this design — different code area (`repl.py` I/O plumbing vs.
  `executor.py` process/signal handling), gets its own design pass bundled
  with the smaller ISSUE-017/018 fixes.
  **Rationale:** Salvador's call, to keep this design focused on one
  mechanism.

## 7. Testing strategy

Extends design §11. `tcsetpgrp`/`SIGTTOU`/`SIGTTIN` behavior cannot be
exercised through pipes — it requires a real controlling terminal, because
the kernel's terminal driver is what actually routes the signals being
tested. Contract tests need a new fixture, `tests/contract/pty_harness.py`
(no `conftest.py` exists yet in this repo — this is the first):

- A helper that forks a subprocess, calls `os.setsid()` and
  `fcntl.ioctl(slave_fd, termios.TIOCSCTTY)` on a freshly opened PTY slave to
  make it that subprocess's controlling terminal, then runs a given callable
  inside it (e.g. one that calls `Executor.run("yes")`), reporting results
  back to the test process over a pipe.
- The test, holding the PTY master, writes the real INTR (`\x03`) or SUSP
  (`\x1a`) control character to it — letting the kernel's line discipline
  perform the actual signal routing to whichever process group is currently
  foreground, which is what proves the handoff works (directly calling
  `os.killpg` would only prove signal delivery to a hardcoded pgid, not that
  the terminal itself is routing correctly).
- Contract tests assert: (a) a command like `yes` sends output while running,
  stops sending output within a bounded time after INTR is written, and
  `run()` returns with `signal == SIGINT`; (b) writing SUSP causes `run()` to
  return with `ctrl_z_terminated == True` and no lingering process (verified
  via `os.kill(pid, 0)` raising `ProcessLookupError` after `run()` returns);
  (c) `os.isatty(0) == False` (piped stdin) skips the mechanism entirely and
  `run()` behaves exactly as v1's contract tests already require (no
  regression — existing Task 1.3/1.4 contract tests keep passing unmodified).
- TUI visual rendering is not contract-tested (§2 non-goal) — validated
  manually if needed, not blocking.
