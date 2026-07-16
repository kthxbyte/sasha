# Dispatch Incidents

A durable log of anomalous Tier 3 (`opencode run`) dispatches — hangs,
silent failures, provider errors — kept separate from `metrics/dispatches.csv`
(gitignored, per-run timing ledger) because these are qualitative,
design-relevant observations meant to inform `run-phase.sh` and the
dispatch protocol over time, not raw telemetry.

Log an entry here whenever a dispatch needs manual intervention (killed,
force-restarted, silently stuck) that a future Tier 1 session — or a
future improvement to the dispatch tooling — should know about.

## Entry format

```markdown
### YYYY-MM-DD — Task X.Y — <one-line symptom>

- **Model / command:** exact model + how it was launched
- **Symptom:** what was observed (timing, log evidence)
- **Diagnosis:** root cause if known, or "undetermined"
- **Resolution:** what was done (killed + retried, etc.)
- **Follow-up:** anything worth fixing in the harness/protocol
```

---

### 2026-07-15 — Task 8.1 — opencode hung at startup, never reached first model call

- **Model / command:** `deepseek/deepseek-v4-pro`, launched via
  `opencode run --model "deepseek/deepseek-v4-pro" "<task spec>..."` per
  CLAUDE.md's dispatch command, run in the background (Bash
  `run_in_background`).
- **Symptom:** Process (PID 12661) ran for 2.5 hours wall-clock but
  accumulated only ~4 minutes of CPU time (`ps -o etime,time`:
  `02:30:32` elapsed vs `00:04:04` CPU). `/proc/<pid>/wchan` showed
  `ep_poll` (idle epoll wait), `/proc/<pid>/fd` showed no live TCP socket
  to any provider — only a local unix socket pair to its own wrapper
  shell. `~/.local/share/opencode/log/opencode.log` showed this run
  (`run=fae4d99c`) completed bootstrap (`creating instance`,
  `bootstrapping`, `loading path` x5, `init`) and then logged **nothing
  else** except three hourly `cleanup prune=7.days` housekeeping ticks —
  no `stream`, `process`, or `loop` entries, meaning it never made (or
  never logged making) a first call to the model. The dispatch's stdout
  capture file was completely empty the entire time.
- **Diagnosis (revised after a second, reproducible occurrence — see
  below):** initially undetermined. Ruled out: db corruption (`PRAGMA
  integrity_check` → `ok`), stale db lock (`fuser` on `opencode.db*` found
  nothing), disk space (11G free), deepseek API/network/auth (direct
  `curl` to `https://api.deepseek.com/chat/completions` with the stored
  key from `auth.json` returned HTTP 200 in 1.7s), prompt size (Task 8.1's
  spec, 6913 bytes, is barely larger than Task 7.1's 6822-byte spec, which
  dispatched successfully). Leading hypothesis: `opencode run` is launched
  with no explicit stdin redirection in CLAUDE.md's dispatch command; if
  it probes stdin (TTY check / "read piped context" pattern common in
  CLIs), it can block forever waiting for EOF in this sandboxed shell,
  which never delivers one. This would explain "clean bootstrap, then
  nothing" with zero CPU churn and zero network activity — the process is
  parked on a read, not doing work.
- **Resolution:** killed the `opencode run` process and its wrapper shell
  (Bash `kill`). First retry (identical command, no stdin change) hung
  identically — same bootstrap-then-silence signature, killed after 590s
  by a `timeout` wrapper this time instead of a manual kill. Second retry
  adds `< /dev/null` to the dispatch command to close stdin explicitly.
- **Follow-up:** if `< /dev/null` resolves it, CLAUDE.md's Dispatch
  Command template should gain it permanently — every future `opencode
  run` invocation should redirect stdin from `/dev/null` by default, not
  just when a hang is suspected. Independent of that fix,
  `run-phase.sh`'s dispatch step should still have a wall-clock timeout
  (kill + escalate if `opencode.log` shows no `stream`/`process` line
  within N minutes) rather than waiting indefinitely — this incident was
  only caught twice because Salvador noticed the wait felt too long; a
  fully unattended loop run would have stalled with no automatic
  recovery.

### 2026-07-15 — Task 8.1 (retry 1) — identical hang, confirms reproducibility

- **Model / command:** same as above, re-dispatched from base
  `84f0869` after killing the first hang and gitignoring an unrelated
  untracked file (`proposal-personality.md`) that was dirtying the tree.
  This time wrapped in `timeout 590` so it could not hang indefinitely.
- **Symptom:** `opencode.log` run `71500ac1`: bootstrap completed
  (`creating instance` through `init`) at 18:57:00 UTC, one
  `cleanup prune=7.days` tick at 18:58:01, then silence until `timeout`
  sent SIGTERM at ~590s (exit code 143). Output file contained only
  `Terminated`. Working tree stayed clean — nothing was ever touched.
- **Diagnosis:** identical signature to the first hang, on a fresh
  process with a verified-healthy db/network/API underneath. Confirms
  this is reproducible, not a one-off fluke — strengthens the stdin
  hypothesis above (or points to some other deterministic startup bug in
  this opencode installation/version, triggered every time regardless of
  prompt content).
- **Resolution:** proceeding to retry #2 with `< /dev/null` appended to
  the dispatch command.

### 2026-07-15 — Task 8.1 (retry 2) — `< /dev/null` coincided with success; NOT confirmed as the cause

- **Resolution:** re-ran the identical dispatch with `< /dev/null` added
  to the `opencode run` invocation. It streamed immediately — read the
  issue file and `repl.py`, ran the contract suite, edited `write_line`,
  reran the full suite (169 passed), committed `250e8b5` with the correct
  trailers — all within under a minute, no `timeout` needed.
- **Original conclusion (later walked back):** this entry originally
  claimed "root cause confirmed: opencode run blocks indefinitely on an
  unclosed stdin." That was too strong a claim from a single before/after
  data point — correlation, not a verified mechanism. See the replication
  study below, which retracts it.

### 2026-07-15 — Replication study: could not reproduce the hang; cause remains unconfirmed

Prompted by Salvador asking whether the hangs might be explained by his
local LLM (llamacpp, LAN endpoint `192.168.8.253:11434`) being down. Ruled
that out first: neither hung run's log (`fae4d99c`, `71500ac1`) contains a
single `stream`/`process`/`"llm runtime selected"` line — both hangs
happened *before* opencode ever selected or contacted any provider, local
or remote, so the local LLM's state is irrelevant to this specific failure.

Then attempted to reproduce the hang directly, varying each variable
suspected of causing it, all via `strace -f -tt` for direct syscall
evidence rather than inference from `/proc`:

1. No stdin redirection, small prompt, foreground — completed in seconds.
2. No stdin redirection, ~7KB padded prompt (matching real task-spec
   size), foreground — completed in seconds.
3. Same as #2, run via background execution (matching how both real hangs
   were launched) — completed in seconds (high CPU throughout, unlike the
   real hangs' near-zero CPU).
4. No stdin redirection, small prompt, foreground, run *immediately after
   SIGTERM+SIGKILL-ing a mid-stream victim opencode process* — the closest
   analogue to what actually happened before the second real hang
   (kill the first hang, immediately re-dispatch) — completed in seconds.

5 for 5 clean runs, zero reproductions, including the scenario closest to
the real sequence of events. The rapid cascades of `+++ exited with 0 +++`
lines visible in the strace logs during shutdown are normal multi-threaded
teardown (bun's worker threads/processes being reaped in quick
succession), not a loop or hang.

- **Conclusion:** the `< /dev/null` fix from retry 2 is **not confirmed**
  as the actual mechanism — it may have simply coincided with whatever
  transient condition caused the two real hangs clearing on its own.
  Checked for an environmental cause instead: no OOM events in `dmesg`, no
  relevant `journalctl` warnings, no swap usage, 29G+ free memory, no
  stale lock/pid/socket files under `~/.local/share/opencode/` or
  `~/.config/opencode/`, and `opencode.db` integrity was already verified
  `ok` in the first incident. No explanation found.
- **Standing status:** cause remains **undetermined**. `< /dev/null`
  stays in CLAUDE.md's Dispatch Command template — it's a harmless,
  reasonable default for a non-interactive dispatch regardless of whether
  it was the real fix — but it should not be trusted as a guaranteed
  prevention. The wall-clock-timeout follow-up (kill + escalate if
  `opencode.log` shows no `stream` line within N minutes of dispatch) is
  now the more important mitigation, since this incident is unreproducible
  on demand and could recur in an unattended `run-phase.sh` loop with no
  one watching the clock.

### 2026-07-15 — Built the wall-clock-timeout follow-up: `scripts/dispatch-watchdog.sh`

The follow-up from the replication study above is now implemented, wired
into `run-phase.sh` in place of the bare `opencode run` call. Two modes:

- **Default** (`WATCHDOG_FULL_TRACE=0`): if `opencode.log` shows no
  `stream` line for the current run within `WATCHDOG_TIMEOUT_S` (default
  180s), captures `/proc/<pid>/status`, `wchan`, `fd`, `cmdline` — plain
  reads, zero overhead on every normal dispatch — then kills the process.
- **Opt-in** (`WATCHDOG_FULL_TRACE=1`): additionally wraps the dispatch in
  `strace -f -tt` from launch, so a live syscall trace exists if a hang is
  detected. Discarded immediately on a normal exit.

**Important discovery made while building this:** the originally-planned
design was "launch normally, decide later, `strace -p <pid>` attach only
if a hang looks likely" — attach on demand, zero cost on the happy path.
That design does not work on this machine. This system has Yama
`ptrace_scope=1` (`/proc/sys/kernel/yama/ptrace_scope` → `1`), which
restricts `ptrace` attach to a process's own descendants. Tested the exact
pattern directly: a shell launches a background child, then that same
shell later runs `strace -f -tt -p <child_pid>` — fails with
`ptrace(PTRACE_SEIZE, ...): Operation not permitted`, because at
attach-time `strace` is a *sibling* of the target (both children of the
same shell), not its ancestor, and Yama's restricted mode requires the
tracer to be a genuine ancestor. This is why `WATCHDOG_FULL_TRACE` traces
from launch instead (`strace` execs `opencode` directly, making it the
real parent — no ptrace-permission issue) rather than attaching
after-the-fact. `sudo` (raising to `ptrace_scope=0`, or `CAP_SYS_PTRACE`)
would remove this restriction but wasn't used — this account has no
passwordless sudo, and weakening system-wide ptrace protection isn't
warranted just for this.

**Sizing data, measured directly** (informed the cheap-by-default design):
a full `strace -f` trace of a trivial single-turn dispatch (no tool calls)
was 20.2MB; a light dispatch (2 file reads + 1 grep) was 36.1MB. A real
task dispatch (edits + two full pytest runs, which `-f` recurses into,
capturing the whole Python/pytest import storm) would likely be
100–300+MB. All of that volume comes from *successful* runs — a genuine
hang goes silent right after `init`, so the actually-diagnostic slice of
any trace is small (its length is bounded by how much a bootstrap phase
does, not by how long the hang lasts). This is the core reasoning for
attach-only/trace-from-launch being reserved for suspected hangs, not run
on every dispatch by default.

**Storage:** `/dev/shm/sasha-dispatch-traces/` — a subdirectory of the
system's existing `/dev/shm` tmpfs (20G total, confirmed already mounted,
no `sudo`/`fstab` needed) rather than a dedicated size-capped mount.
Traded a hard kernel-enforced quota for zero setup cost; 20G of headroom
against a KB-few-MB typical incident (or ~300MB worst case, discarded
immediately) makes the quota's protection margin comfortable either way.

**Validated** (4 scenarios, using a copy of the real script with the
`opencode run` line substituted for a plain `sleep`, pointed at a scratch
log file so the real `opencode.log` and dispatch history stayed untouched):
default-mode happy path (exit 0, zero artifacts), default-mode hang path
(exit 2, correct `/proc` snapshots matching the sleeping process's actual
`hrtimer_nanosleep` block), full-trace-mode hang path (exit 2, real strace
evidence showing the exact blocked syscall — `clock_nanosleep(...)` — up
to the SIGTERM that killed it), full-trace-mode happy path (exit 0, trace
file discarded, `/dev/shm` usage back to baseline). All four passed.
