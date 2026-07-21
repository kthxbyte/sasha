# Interactive keystroke relay for capture PTYs — Design (ISSUE-040, reopened)

**Status:** Draft for review
**Date:** 2026-07-20
**Amends:** `docs/superpowers/specs/2026-07-14-sasha-v2-job-control-design.md` §4.2
**Supersedes:** `docs/superpowers/specs/2026-07-20-issue-040-pager-keystrokes-design.md` (Task 20.1's
SIGTTIN-ignore + stop-cause classification shipped and is a legitimate job-control
hardening in its own right, but is confirmed — by direct reproduction after it
landed — insufficient to fix ISSUE-040; this design targets the actual mechanism)

## 1. Mission

Fix pagers and other terminal-control programs (confirmed: `less`, invoked
directly or via `git log`) that never receive keystrokes when run inside
Sasha, forcing the user to Ctrl+Z out. Root cause, confirmed via `strace`
and `/proc/<pid>/fd` inspection: these programs follow the standard Unix
pager convention of using **stderr** (fd 2), not stdout or a fresh
`/dev/tty` open, for terminal control — both raw-mode negotiation and
keyboard reads — specifically so paged output can still be redirected while
keyboard control keeps working. Plain bash never triggers this because it
gives a command's stderr no identity other than the real terminal — fd
0/1/2 are all the same physical device, inherited straight through.
`Executor.run()` breaks that assumption: it unconditionally wraps stderr in
its own fresh synthetic PTY (`pty.openpty()` for `err_master`/`err_slave`,
every command, so stderr text can be captured for LLM context/memory) — a
real, valid tty (`isatty(2)` is true), but nothing ever feeds real
keystrokes into it. The program sets raw mode there successfully and blocks
reading it, correctly but uselessly, forever, while the real terminal (fd
0/1) is never touched and stays in cooked/echo mode the whole time — which
is also why the raw keystrokes show up echoed on screen. Ctrl+Z still works
because that's driven by the real terminal's foreground process group,
unrelated to which fd the child actually reads.

## 2. Scope

### In scope:

- Relay real keyboard input from the real terminal (fd 0) into whichever
  existing capture PTYs (stderr's, always; stdout's, when
  `capture_stdout=True`) a child program has put into raw/non-canonical
  mode — the confirmed signal that it's treating that fd as its terminal
  for input.
- Mirror that PTY's termios settings onto the real terminal at the same
  moment, so the kernel's own echo on fd 0 doesn't double up with whatever
  the relayed-to PTY does, while preserving `ISIG` so Ctrl+C/Ctrl+Z keep
  working via the real terminal exactly as Task 4.1 already established.

### Out of scope / non-goals:

- Wrapping fd 0 in a new synthetic PTY of its own. fd 0 is a bare,
  untouched passthrough of the real terminal today, which is *why*
  programs that read fd 0 directly (Task 4.1's TUI-passthrough case) already
  work — introducing a new synthetic layer there would add regression risk
  against an already-working, already-tested path to fix nothing currently
  broken.
- Any change to `Executor.run()`'s signature, `ExecResult`'s fields, or the
  cwd/env side-channel, `SIGWINCH` propagation, or Task 20.1's SIGTTIN/stop-
  classification logic — all unchanged, all still correct and worth keeping.
- Perfect fidelity for every conceivable terminal-control convention (e.g. a
  program that opens a *fresh* `/dev/tty` and ignores fd 0/1/2 entirely gets
  the real terminal already, by construction, and needs no relay at all —
  only fd-2/fd-1-preferring programs are addressed here).

## 3. Key contracts & assumptions

1. **fd 0 is never read by Sasha until a capture PTY has gone raw.** Before
   that, fd 0 remains exactly the bare passthrough it is today — no new
   reader competes with a program (e.g. `vi`) reading it directly. This is
   the mechanism that makes the fix safe to add without regressing Task
   4.1.
2. **"Gone raw" is detected by observing the PTY's own termios**, not by any
   direct notification from the child (none exists) — checked at each relay-
   loop wakeup, compared against the PTY's initial (creation-time) state.
3. **Once triggered for a given command, relay stays active** for the rest
   of that command's execution — a later transient return to cooked mode
   (e.g. a startup probe-then-set dance, observed directly in `less`) does
   not turn it back off.
4. **Relaying is non-blocking.** A write into a triggered PTY's master that
   can't be drained immediately is dropped rather than blocking Sasha's
   relay loop — mirrors the existing `chld_write` self-pipe pattern already
   in `Executor.run()`.
5. **Termios mirroring copies the triggered PTY's exact settings onto fd 0**
   (not a hardcoded "raw" template) — this is what keeps `ISIG` intact
   (Ctrl+C/Ctrl+Z unaffected) since it reflects whatever the child actually
   negotiated, rather than Sasha guessing.

## 4. Mechanism

### 4.1 Trigger detection

Extends the existing `select()`-based relay loop (v2 design §4.2). Today
that loop blocks indefinitely (`select.select(watch_fds, [], [])`); this
adds a bounded timeout (proposed: 50ms) so the loop can periodically check
for a termios change on `err_slave` (and `out_slave`, if
`capture_stdout=True`) without adding meaningful latency to the existing
output-relay path (a wakeup that finds nothing new just loops back into
`select()` — no different from today's behavior when nothing is
`readable`).

At the top of `run()`, immediately after creating each capture PTY, record
its initial `c_lflag` from `termios.tcgetattr(err_slave)` (and
`out_slave`'s, if present) — this always starts with `ICANON` set (PTYs
are created in canonical mode by default). On each timeout wakeup, for each
capture PTY not yet triggered: re-read its current `c_lflag` and check
whether the `ICANON` bit is now clear. That single condition — canonical
mode having been turned off — is the trigger; no other flag is consulted.
This matches the confirmed observation directly (`less`'s own `TCSETSW`
clears `ICANON` along with `ECHO`/`ECHOE`/`ECHOK`, while keeping `ISIG`).

### 4.2 Activating relay for a triggered PTY

On first trigger for a given PTY (`err_slave` or `out_slave`):

1. `termios.tcsetattr(0, TCSANOW, termios.tcgetattr(<triggered slave>))` —
   mirror its exact settings onto the real terminal. `ISIG` is preserved
   because it's part of whatever the child itself negotiated (confirmed by
   direct observation: `less`'s own raw-mode call keeps `ISIG` set,
   clearing only `ICANON`/`ECHO`/`ECHOE`/`ECHOK`).
2. Add fd 0 to `watch_fds` (once — if both PTYs later trigger, fd 0 is
   added only the first time).
3. From then on, when fd 0 is readable: read available bytes, then write
   them (non-blocking, via the triggered PTY's master —
   `err_master`/`out_master`) into every currently-triggered PTY. A `write`
   that would block (`BlockingIOError`) is simply dropped for that
   iteration — correctness for the case that matters (a child actually
   reading its triggered PTY) is unaffected, since that child is actively
   draining its own master-fed queue.

### 4.3 No new interface

`Executor.run()`'s signature and `ExecResult` are unchanged. This is
entirely internal to the relay loop's mechanics — no caller-visible change.

## 5. Interaction with existing mechanisms

- **Task 4.1's job-control handoff** (`tcsetpgrp`, stop detection): unaffected.
  fd 0's foreground process group is still the child's; this design only
  changes *who is reading* fd 0's bytes at the OS level (Sasha, once
  triggered) and does not touch process-group or signal plumbing at all.
- **Task 20.1's SIGTTIN ignore + stop classification**: unaffected and still
  correct to keep — it hardens a different, real (if not the primary)
  failure mode and remains valid defense in depth.
- **The final `termios.tcsetattr(0, saved_termios)` restore** at the end of
  `run()` (unchanged) already cleans up whatever mirroring happened mid-run,
  since `saved_termios` was captured before any of this logic runs.

## 6. Decisions log

- **Decision:** Trigger relay off the capture PTY's own termios state,
  rather than relaying unconditionally for every interactive command.
  **Rationale:** Unconditional relay would mean Sasha always reads fd 0,
  which races with any program reading fd 0 directly — exactly Task 4.1's
  already-working, already-tested TUI-passthrough case. Termios-triggered
  relay only activates for programs that have positively signaled "I'm
  treating this fd as my terminal," which by construction excludes fd-0
  readers (they never touch the capture PTY's termios at all).
- **Decision:** Do not wrap fd 0 in a new synthetic PTY.
  **Rationale:** Salvador's call. fd 0 has no observed bug today; adding a
  new synthetic layer there would only add regression surface against
  Task 4.1 to fix something that isn't broken.
- **Decision:** Mirror the triggered PTY's exact termios onto fd 0, rather
  than applying a fixed "raw mode" template.
  **Rationale:** Preserves whatever the child actually negotiated (notably
  `ISIG`) without Sasha needing to special-case which flags matter —
  correct by construction as long as the child's own negotiation is
  correct, which is exactly the behavior being restored.
- **Decision:** Accept, without further mitigation, the theoretical
  double-relay case where both `err_slave` and `out_slave` trigger for the
  same command.
  **Rationale:** Since nothing is ever written to a PTY's master before it
  triggers (i.e. before it's already raw/no-echo), there's no cooked-mode
  echo to duplicate — broadcasting the same bytes into two already-raw
  PTYs is harmless. `capture_stdout=True` is also only reachable from
  tool-invoked commands, not plain typed ones, further limiting how often
  this could even arise.

## 7. Testing strategy

Extends `tests/contract/pty_harness.py` (Task 4.1's real-controlling-
terminal harness), reused unmodified.

- **Relay activates and delivers real keystrokes:** a harness-run program
  puts `err_slave`'s (obtained the way `less` does — i.e. the fd Sasha
  exposes as the child's fd 2) termios into raw mode, then blocks reading a
  byte from it; the test writes a real keystroke to the harness's PTY
  master (simulating the physical keyboard) and asserts the program
  receives it.
- **fd-0-direct readers are unaffected:** a harness-run program reads fd 0
  directly (mimicking `vi`'s convention) without ever touching the capture
  PTY's termios; the test writes to the harness's master and asserts the
  program receives the bytes with no drops — proving the trigger never
  fires and fd 0 is untouched by Sasha for this case.
- **No regression for non-interactive commands:** a plain command (e.g.
  `echo`/`yes`) behaves identically to today — no relay ever activates,
  confirmed by absence of any fd-0 read from Sasha's side.
- **Regression:** `tests/contract/test_task_4_1_job_control.py` and
  `tests/contract/test_task_20_1_pager_keystrokes.py` must stay green
  unmodified.
