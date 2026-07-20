# Interactive pager keystroke fix — Design (ISSUE-040)

**Status:** Draft for review
**Date:** 2026-07-20
**Amends:** `docs/superpowers/specs/2026-07-14-sasha-v2-job-control-design.md` §4.1, §4.2

## 1. Mission

Fix `Executor.run()` so a foreground interactive program that reads raw
keystrokes from the terminal (a pager like `less`, invoked directly or via
`git log`) actually receives them. Right now, arrow keys and `q` are echoed
onto the screen as literal characters and never reach the program; the only
way out is Ctrl+Z. The v2 job-control handoff (Task 4.1) ignores `SIGTTOU`
in the child tree so a background write to the terminal never stops it, but
never did the same for `SIGTTIN` — the signal generated when a background
process group tries to *read* from the controlling terminal. A silent
`SIGTTIN`-driven stop looks, from Sasha's side, identical to a real Ctrl+Z
(both are `WIFSTOPPED`), so Sasha's existing stop handler immediately kills
the job (§4.3 of the v2 design) — invisibly, since stopping a process prints
nothing. The user sees a pager that displays content but never responds to
keys, with cooked-mode terminal echo continuing the whole time because the
program was stopped before it ever got to read anything.

## 2. Scope

### In scope:

- Ignore `SIGTTIN` in the child process tree, symmetrically with the
  existing `SIGTTOU` handling, so a transient foreground/background race
  around the terminal read never silently stops an interactive child.
- Distinguish a real user-initiated stop (`SIGTSTP`/`SIGSTOP`, i.e. an
  actual Ctrl+Z) from a protective stop (`SIGTTIN`/`SIGTTOU`) in the
  existing stop-detection path, and resume the latter transparently
  instead of killing the job.

### Out of scope / non-goals:

- Changing Ctrl+Z's meaning for a real user-initiated stop — that stays
  exactly as v2 designed it (§4.3): always `SIGCONT` + `SIGTERM`, never
  left stopped, no `fg`/`bg`/`jobs`.
- Reproducing or explaining the exact originating condition that caused
  the observed `SIGTTIN` stop in the field (the precise race is not fully
  characterized — see §6). The fix removes the entire class of failure
  regardless of the exact trigger, so root-causing the trigger further is
  not required to close this out.
- Any change to the stderr/stdout PTY relay, cwd/env side-channel, or
  window-size propagation — untouched, as in v2.

## 3. Key contracts & assumptions

1. **`SIGTTIN` is ignored in the child tree for the full duration of the
   handoff**, installed and restored at exactly the same points `SIGTTOU`
   already is (before fork; restored both on normal completion and on the
   `tcsetpgrp` `OSError` degrade-to-non-interactive fallback).
2. **A stop is only ever treated as a user Ctrl+Z, and the job only ever
   killed, when the stopping signal was `SIGTSTP` or `SIGSTOP`.** A stop
   whose `WSTOPSIG` is `SIGTTIN` or `SIGTTOU` is resumed (`SIGCONT`) and the
   relay loop continues waiting — the job is never killed for this reason.
3. **This is additive to, not a replacement for, contract 1's guarantee**
   (§3 of the v2 design): job-control failures still never break command
   execution; the non-interactive path is still entirely unaffected
   (`os.isatty(0) == False` skips all of this, as before).

## 4. Mechanism

### 4.1 Ignoring SIGTTIN

Extends v2 §4.1 step 1. Where that step installs `SIGTTOU` as `SIG_IGN`
before fork (saving the old disposition), also install `SIGTTIN` as
`SIG_IGN`, saving its old disposition the same way. `SIG_IGN` survives
`execve`, so this propagates through `bash -c` into anything it forks
(`git`, `less`, ...) exactly like the existing `SIGTTOU` ignore already
does — no new propagation mechanism, just a second signal handled at the
same two existing restore points (normal end-of-`run()` cleanup, and the
`tcsetpgrp` `OSError` fallback branch in v2 §4.1 step 2).

### 4.2 Distinguishing the cause of a stop

Extends v2 §4.2 step 2. Where that step currently branches on bare
`os.WIFSTOPPED(status)`, inspect `os.WSTOPSIG(status)` as well:

- `SIGTTIN` or `SIGTTOU`: this is a protective stop, not a user action —
  `os.killpg(pid, signal.SIGCONT)` to resume the process group immediately,
  do **not** set `stopped`, do **not** end the loop; go back to waiting on
  the same `watch_fds` as before. From the relay loop's perspective nothing
  happened; the child keeps running and the select loop keeps servicing its
  stdout/stderr/side-channel fds exactly as it was.
- Any other stop signal (`SIGTSTP`, `SIGSTOP`): unchanged from v2 — set
  `stopped = True`, end the loop, proceed to §4.3's termination policy.

This is defense in depth: with §4.1's `SIGTTIN` ignore in place, the kernel
should not generate a `SIGTTIN` stop for this child tree at all any more, so
this branch is not expected to be exercised in the field in the current
architecture. It stays because (a) `SIGTTOU`'s ignore was already
demonstrably insufficient on its own to prevent every background-terminal-
access stop from being misclassified as a user Ctrl+Z, and (b) it costs
nothing and correctly generalizes "only a real Ctrl+Z kills the job" beyond
what any one signal-disposition fix can guarantee for every current and
future descendant program.

## 5. Interface change

None. `Executor.run()`'s signature, `ExecResult`'s fields, and every other
module's public interface are unchanged.

## 6. What is and isn't established

Confirmed by direct testing in a real terminal session (arrow keys/`q`
echoed raw, `less`'s own `strace` showing it correctly opens `/dev/tty`,
correctly matches Sasha's controlling terminal and foreground process group
per `ps`, correctly sets raw mode via `TCSETSW` — then blocks on
`read(fd, ..., 1)` for a keystroke that never arrives, interrupted only by
the `SIGTERM` that arrives after a real Ctrl+Z, exactly matching a stop that
was never surfaced to the user):

- The read genuinely never receives the typed bytes; the terminal remains
  in cooked mode the entire time because the reading program was stopped
  before it read anything.
- `SIGTTIN` is not currently ignored anywhere in the chain, unlike
  `SIGTTOU`.

Not established with certainty: the *exact* originating condition that
causes the process group to be treated as background at read time (a
timing race around the `tcsetpgrp` handoff vs. `less`'s own startup
sequence is the leading candidate, but was not directly captured in a
trace). Per §2, this is accepted — the fix in §4 closes the entire failure
class regardless of the precise trigger, and §4.2's defensive branch means
even a stop this design didn't anticipate no longer silently kills an
interactive job.

## 7. Decisions log

- **Decision:** Ignore `SIGTTIN` symmetrically with the existing `SIGTTOU`
  handling, rather than only fixing the stop-classification (§4.2) in
  isolation.
  **Rationale:** Standard job-control-implementation practice (e.g. APUE's
  reference shell implementation) ignores both signals together in the
  controlling process for exactly this reason; `SIGTTOU`-only was an
  incomplete application of that pattern.
- **Decision:** Also add stop-cause classification (§4.2) rather than
  relying on the `SIGTTIN` ignore alone.
  **Rationale:** Salvador's call — belt-and-suspenders. The ignore should
  make this branch unreachable in the current architecture, but it's a
  correct, low-cost generalization that stops any future descendant
  program (or any other background-terminal-access edge case) from having
  its job silently killed the same way.
- **Decision:** Do not pursue further root-causing of the exact race that
  triggered the original `SIGTTIN` stop.
  **Rationale:** The fix closes the whole failure class; the marginal value
  of pinning the exact trigger further doesn't justify more investigation
  time, especially given the risk profile of experimenting with
  session/controlling-terminal manipulation (see the caution note already
  logged against ISSUE-040 in `ISSUE-TRACKING.md`'s discovery history).

## 8. Testing strategy

Extends v2 §7 (the design this amends). `SIGTTIN`/`SIGTTOU` disposition and
stop-cause behavior need a real controlling terminal to exercise — reuse
`tests/contract/pty_harness.py` as-is (no changes to the harness itself).

- **§4.1 (ignore installed):** run
  `python3 -c "import signal; print(signal.getsignal(signal.SIGTTIN) is signal.SIG_IGN)"`
  through `Executor.run(..., capture_stdout=True)` inside the harness;
  assert captured stdout is `True`.
- **§4.1 (ignore restored):** capture `signal.getsignal(signal.SIGTTIN)` in
  Sasha's own process immediately before and after a `run()` call; assert
  they're equal (mirrors the existing implicit guarantee for `SIGTTOU`).
- **§4.2 (protective stop survives):** run a command that resets its own
  `SIGTTIN` disposition to `SIG_DFL` and sends itself `SIGTTIN` directly
  (independent of §4.1 — this proves §4.2's classification on its own,
  simulating a stop from a cause the ignore doesn't cover), then continues
  and exits normally; assert `ExecResult.ctrl_z_terminated is False` and the
  command's own exit code is observed (i.e. the job was never killed).
- **Regression:** the existing `test_task_4_1_job_control.py` (in
  particular `test_ac2_ctrl_z_terminates_and_reaps`, real Ctrl+Z via SUSP)
  must stay green unmodified, proving the real-Ctrl+Z-kills-the-job path is
  untouched.
