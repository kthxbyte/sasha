# Foreground-group keystroke relay helper — Design (ISSUE-040, third attempt)

**Status:** Draft for review
**Date:** 2026-07-21
**Amends:** `docs/superpowers/specs/2026-07-14-sasha-v2-job-control-design.md` §4.2
**Supersedes:** `docs/superpowers/specs/2026-07-20-issue-040-stderr-pty-keystroke-relay-design.md`
(that design's mission, scope, and trigger-detection mechanism are still
correct and are reused verbatim here — only *who does the relaying*
changes, because that design's central assumption, that Sasha's own
process could read fd 0 after handing off foreground status, was
disproven during implementation: POSIX/Linux flatly does not allow a
background process group to read its controlling terminal, confirmed
empirically by `EIO` on every attempt.)

## 1. Mission

Fix pagers and other fd-2-for-terminal-control programs (confirmed:
`less`, invoked directly or via `git log`) that never receive keystrokes
when run inside Sasha. Same root cause as before: `Executor.run()`
unconditionally wraps a child's fd 2 (and fd 1, when capturing stdout) in
a synthetic PTY so output can be captured for LLM context, but nothing
ever fed real keystrokes into that PTY, so a program that raw-modes it
for input blocks forever.

**What's new in this attempt**: the relay runs from a small helper
process that shares the target command's foreground process group,
instead of from Sasha's own (now-background) process — verified viable
with a minimal, isolated repro before this design was written (see §7),
following the same "test the assumption directly, don't just reason
about it" discipline that found the previous blocking issue in the first
place.

## 2. Scope

### In scope:
- Everything in the superseded design's scope (§2): relay real keyboard
  input from fd 0 into whichever capture PTY (`err_master` always,
  `out_master` when `capture_stdout=True`) a child has put into raw mode;
  mirror that PTY's exact termios onto fd 0 at the trigger moment.
- The new mechanics needed to run that relay from a process that isn't
  Sasha itself: fd-passing to the child environment, a standalone helper
  script, its lifecycle (launch, signal survival, cleanup).

### Out of scope / non-goals:
- Everything the superseded design already scoped out (§2): wrapping fd
  0 in its own synthetic PTY, changing `Executor.run()`'s signature or
  `ExecResult`, perfect fidelity for programs that open a fresh
  `/dev/tty` and ignore fd 0/1/2 entirely.
- A contract test that exercises Ctrl+C/Ctrl+Z arriving *during* an
  active relay — accepted as a manual verification point (§6), not
  automated, since driving that precise interleaving through the test
  harness reliably is disproportionately hard versus its marginal value.

## 3. Key contracts & assumptions

1. **A process in the foreground process group can read fd 0 even
   though Sasha's own (background) process cannot.** This is the
   load-bearing assumption of the whole design — verified directly
   (§7), not assumed. It is not a Sasha-specific workaround; it is how
   POSIX job control is supposed to work (only the *group* matters, not
   which specific process within it issues the read).
2. **The relay writes into the master side, not a duplicate of the
   slave side.** A PTY is directional per side: bytes written to the
   master arrive as input on the slave; bytes written to the slave loop
   out to whoever reads the master (Sasha's own output-capture loop).
   Getting this backwards was an actual bug caught during this design's
   own verification repro (§7) — worth stating explicitly since it's an
   easy mistake to make again.
3. **fd 0 is never read by anyone until a capture PTY has gone raw** —
   unchanged from the superseded design. This is what keeps the fix from
   regressing Task 4.1's already-working fd-0-direct-reader case (`vi`,
   etc.): the helper never competes for fd 0 unless a program has
   positively signaled "I'm treating a *different* fd as my terminal."
4. **Cleanup is Sasha/bash's responsibility, not the helper's own
   detection.** The helper holding open its own copies of the master
   fds means it can never reliably notice "the command is done" by
   watching those fds go bad — it would be watching for a condition its
   own existence prevents. The bash wrapper's `EXIT` trap explicitly
   killing the helper is therefore the primary mechanism, not a
   convenience.

## 4. Mechanism

### 4.1 fd-passing

`err_master` (and `out_master`, if `capture_stdout=True`) get
`FD_CLOEXEC` cleared right after `pty.openpty()`, exactly like
`write_fd` already does for `SASHA_STATE_FD`. The child branch, which
today does `os.close(err_master)` (and `os.close(out_master)`) before
`execve`, stops closing them — they survive into the wrapped bash
script as inherited fd numbers.

### 4.2 The relay helper

A new standalone script, `src/sasha/_kb_relay_helper.py`, invoked as:
```
python3 <path-to-script> <err_master_fd> [<out_master_fd>]
```
(a real file, not an inline `python3 -c "..."` string — avoids fragile
nested quoting inside the already-generated bash wrapper). It:

1. Ignores `SIGINT` and `SIGTSTP` — it shares the foreground process
   group with the real command, so it receives both on Ctrl+C/Ctrl+Z,
   and must not die from them.
2. Records `os.getppid()` at startup (see §4.4).
3. Loops with a bounded `select()` timeout (50ms, matching the
   superseded design's proposal): for each master fd not yet triggered,
   `termios.tcgetattr(fd)` and check whether `ICANON` has cleared —
   readable directly off the master fd, no slave-side fd needed at all
   (termios is a property of the pty pair, not a specific side).
4. On first trigger for any master fd: `termios.tcsetattr(0, TCSANOW,
   termios.tcgetattr(<that fd>))` — mirrors its *exact* settings onto
   fd 0 (preserves `ISIG`, so Ctrl+C/Ctrl+Z keep working; also stops the
   raw escape-code double-echo that was part of the original bug
   report's visible symptom). Adds fd 0 to the watch set (once).
5. Once watching fd 0: on each readable wakeup, read available bytes
   and write them (non-blocking, dropped on `BlockingIOError`) into
   *every* currently-triggered master fd.
6. Exits when `os.getppid()` no longer matches the value recorded in
   step 2 (orphaned — see §4.4) — the only self-termination path; see
   §4.4 for why "master fds became invalid" is not a usable one.

### 4.3 Wrapped-script changes

Only when `interactive` (matches the existing job-control gating):
before the existing `trap ... EXIT; {line}`, prepend:
```
python3 <relay_helper_path> <err_master> [<out_master>] & __sasha_relay_pid=$!;
```
and prepend `kill $__sasha_relay_pid 2>/dev/null;` to the existing
`EXIT` trap body (which already writes cwd/env to `SASHA_STATE_FD`).
Backgrounding via bash `&` does not create a new process group here,
because non-interactive bash scripts have job control (monitor mode)
off by default — the helper inherits the same pgid as `{line}`, which
is exactly what makes it part of the foreground group once Sasha's
existing `tcsetpgrp` handoff runs.

### 4.4 Why cleanup needed a second look

My first pass assumed the helper could notice "the command finished" by
watching its master fds go invalid and exiting on its own — but the
helper's *own* open copies of those fds are exactly what keeps the
underlying pty from ever reaching that state, a self-referential
condition that would never actually fire. The real mechanisms are:
- **Primary**: the bash `EXIT` trap's explicit `kill`, which runs
  whenever the wrapped script exits normally (the overwhelming majority
  of cases).
- **Safety net**: parent-pid-change detection (§4.2 step 6), for the
  rare case bash itself is killed abruptly (e.g. an external `SIGKILL`
  to the whole process group) before its trap runs, orphaning the
  helper to init.

### 4.5 No interface changes

`Executor.run()`'s signature and `ExecResult` are unchanged — everything
here is internal to `run()`'s own mechanics, same as the superseded
design's §4.3.

## 5. Interaction with existing mechanisms

Identical to the superseded design's §5: Task 4.1's job-control handoff
and Task 20.1's SIGTTIN-ignore/stop-classification are both unaffected —
neither touches, nor is touched by, who's reading fd 0 at the OS level.
Sasha's own relay loop continues reading `err_master`/`out_master` for
*output* capture exactly as today; the helper's writes into those same
fds (relaying *input*) are a genuinely orthogonal direction on the same
bidirectional channel — confirmed concurrently coexisting without
interference in the verification repro (§7).

## 6. Decisions log

- **Decision:** Run the relay from a helper sharing the target's
  foreground process group, not from Sasha's own process.
  **Rationale:** The only way to satisfy both "the target must be
  foreground for correct signal delivery" and "something must read fd
  0 to source keystrokes" — Sasha itself cannot occupy both roles at
  once, but a distinct foreground-group process can.
- **Decision:** Verify the core assumption with a minimal repro before
  writing this design, not just reason about it.
  **Rationale:** The previous design's blocking flaw was itself only
  found this way, not by reasoning; the same discipline correctly
  caught a real bug in the verification repro's *own* first draft
  (writing into the slave side instead of the master), which would
  have silently produced a design built on a broken foundation.
- **Decision:** Ship the relay helper as a standalone script file, not
  an inline `python3 -c "..."` string embedded in the bash wrapper.
  **Rationale:** Avoids nested-quoting fragility; keeps the helper's
  logic independently readable and testable.
- **Decision:** Primary cleanup is bash's explicit `kill` in its `EXIT`
  trap; the helper's own "watch my master fds" idea was dropped as
  unreliable once traced through carefully (§4.4), replaced with
  parent-pid-change detection as a safety net instead.
  **Rationale:** Correctness over elegance — the more obvious-seeming
  self-detection mechanism doesn't actually work, and it's better to
  say so than to ship it and discover the gap later.
- **Decision:** No automated test for signal delivery during an active
  relay (Ctrl+C mid-pager); manual verification only.
  **Rationale:** Salvador's call, weighing automation cost against
  marginal value for a scenario that's inherently awkward to drive
  precisely through `pty_harness.py`.

## 7. Verification repro (performed before this design was written)

A standalone script (not part of the test suite — ad hoc, scratchpad
only) built the same three-role structure this design uses: a
session-leader process ("sasha") owning a real PTY as its controlling
terminal; a "wrapper" child that becomes its own process group and
receives `tcsetpgrp`; a "helper" forked from the wrapper (no `setpgid`
of its own, so it inherits the wrapper's foreground pgid); and a
"target" (the wrapper's own continuation) that raw-modes a second,
unrelated PTY's slave side (standing in for `err_slave`) and blocks
reading one byte from it, exactly as `less` does.

First attempt: helper read fd 0 successfully (`os.read(0, ...)` returned
real bytes — the core assumption held) but relayed into a *duplicate of
the slave side* instead of the master, so "target" never saw anything —
an actual bug in the repro, not a new fact about the OS, caught by
checking the full round trip rather than stopping at "helper's read
succeeded." Corrected to relay into the master side (inherited,
unclosed, exactly as §4.1 specifies); rerun three times, full round trip
succeeded every time: outer writes a keystroke into the real PTY →
helper reads it from fd 0 → helper writes it into the master → target's
raw-mode read on the slave returns the real byte.

## 8. Testing strategy

Reuses `tests/contract/pty_harness.py` (Task 4.1) unmodified, same as
every prior attempt on this issue.

- The quarantined scratch test
  (`quarantine/manual-issue-040-stderr-relay-scratch/test_task_21_1_stderr_keystroke_relay.py`)
  encodes the right two ACs for this design too — the *observable*
  contract from `Executor.run()`'s caller's point of view (a fd-2-raw-
  mode reader gets real keystrokes; a fd-0-direct reader is unaffected)
  is identical regardless of which process does the relaying. AC2
  already passes today; AC1 is the real target. Revived under this
  task's real number, not the stale one it currently references.
- New: no lingering relay-helper process survives after `Executor.run()`
  returns, once the command completes (checked via a `/proc` scan for
  the recorded helper pid).
- Regression: `test_task_4_1_job_control.py` and
  `test_task_20_1_pager_keystrokes.py` stay green, unmodified.
- Signal-survival-under-active-relay: manual verification only (§2, §6)
  — not a contract test.
