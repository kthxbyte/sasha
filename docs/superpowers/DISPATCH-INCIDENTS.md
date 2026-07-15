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
- **Diagnosis:** undetermined — bootstrap completed cleanly (config
  loaded, LSPs/formatters resolved) but the agent loop never started. Not
  a slow provider response (no outbound network connection existed to be
  slow) and not an infinite retry loop (no repeated log entries, no CPU
  churn). Left the process in a state indistinguishable from "waiting on
  something that will never arrive."
- **Resolution:** killed the `opencode run` process and its wrapper shell
  (Bash `kill`), then re-dispatched the identical command against the
  same base commit.
- **Follow-up:** `run-phase.sh`'s dispatch step should have a wall-clock
  timeout (e.g. kill + escalate if `opencode.log` shows no `stream`/
  `process` line within N minutes of the wrapper starting) rather than
  waiting indefinitely on a foreground-equivalent background task. This
  incident was only caught because Salvador noticed the wait felt too
  long — a fully unattended loop run would have stalled here with no
  automatic recovery.
