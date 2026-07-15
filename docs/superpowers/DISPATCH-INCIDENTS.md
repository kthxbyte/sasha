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

### 2026-07-15 — Task 8.1 (retry 2) — `< /dev/null` resolved it, confirmed root cause

- **Resolution:** re-ran the identical dispatch with `< /dev/null` added
  to the `opencode run` invocation. It streamed immediately — read the
  issue file and `repl.py`, ran the contract suite, edited `write_line`,
  reran the full suite (169 passed), committed `250e8b5` with the correct
  trailers — all within under a minute, no `timeout` needed.
- **Root cause confirmed:** `opencode run` blocks indefinitely on an
  unclosed stdin in this sandboxed shell when none is supplied. CLAUDE.md's
  Dispatch Command template needs `< /dev/null` added permanently — every
  prior successful dispatch in this project's history (Tasks 1.1 through
  7.1) was launched by whatever shell/session ran it at the time; this is
  the first time the exact invocation environment reproduced the hang
  twice in a row, so the fix belongs in the standing command, not as a
  one-off workaround.
