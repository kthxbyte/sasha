# Tool-call logging — Design (ISSUE-042)

**Status:** Draft for review
**Date:** 2026-07-21

## 1. Mission

Sasha keeps a full, durable record of every tool call it proposes —
`run_command`, `write_file`, `read_file`, `list_dir`, `search_files`,
`remember` — so Salvador can study his own usage patterns later: which
tools/commands he actually reaches for, how often he declines or edits,
where the confirmation gate earns its keep versus where it's just
friction, and (when something looks odd in retrospect) a forensic trail
of exactly what ran and what came back. This is a general-purpose record,
not tuned to one narrow analysis — capture everything reasonably
capturable now, figure out the specific study later.

## 2. Scope

### In scope (v1):
- One global, append-only JSONL log of every tool call the LLM proposes,
  regardless of verdict (executed, declined, edited, replied-to).
- Full verbatim call + result capture, with one privacy carve-out: results
  are withheld (not stored) for `run_command` calls that touch a path
  matching the ISSUE-041 sensitive-path denylist.
- Size-bounded via background compaction (stripping oldest results first)
  plus a manual `sasha compact-log` command.

### Out of scope / non-goals:
- **Per-project scoping.** Unlike `.sasha/memory/`, this is one global log
  under `state_dir`, not scattered per git repo. Each entry carries its own
  `cwd` for later filtering/grouping.
- **Truncating large individual fields.** Salvador explicitly wants full
  verbatim capture (a 40,000-character `read_file` result or a large
  `stdout` capture is stored whole); size is managed at the file level via
  compaction, not per-field truncation.
- **Sensitive-path withholding for `write_file`/`read_file`/`list_dir`/
  `search_files`.** Scoped to `run_command` only for v1 (§4.1 explains
  why) — an explicit, deliberate narrowing, not an oversight.
- **A global enable/disable toggle.** Not requested; if wanted later, it's
  a small, separate addition to `[tool_log]`.
- **Analysis tooling** (querying/aggregating the log). This spec only
  covers writing and bounding it; how Salvador studies it later (jq,
  a notebook, a future sasha command) is out of scope.

## 3. Key contracts & assumptions

1. **Every proposed tool call gets exactly one log entry**, whatever its
   outcome — executed, declined ("no"), edited, or replied-to. The entry's
   `verdict`/`executed`/`result_omitted_reason` fields disambiguate which
   happened.
2. **The log must never break the interactive loop.** A write failure
   (disk full, permission error) is caught and the entry is dropped
   silently (with a one-time-per-process warning) — logging is a study
   aid, not a load-bearing system.
3. **Fail closed on privacy, not on completeness.** The one place this
   design deliberately omits data (sensitive-path `run_command` results)
   is a narrow, explicit carve-out — everything else is verbatim, matching
   Salvador's call that completeness matters more than caution here.
4. **Compaction only ever removes `result` fields from old entries, never
   whole entries.** Call metadata (tool, arguments, verdict, timestamp,
   cwd) is permanent; only the (potentially large) result payload is
   reclaimable, and only from entries old enough to no longer be "recent."

## 4. Architecture

### 4.1 Where it lives, and the one hook point

New module `src/sasha/tool_log.py`, stateless functions in the same style
as `memory.py`. Writes to a single global file:
`state_dir / "tool_log.jsonl"` (i.e. `~/.local/state/sasha/tool_log.jsonl`
under the existing `XDG_STATE_HOME` convention `__main__.py` already
computes for `mutes.json`/readline history).

It hooks into exactly one place: `agent.py`'s `run_consult` dispatch loop,
once per tool call, right after `result_str` is finalized for whichever
branch ran (confirmed-yes, edit, declined, reply, or auto-approved) and
before `session.add_tool_result(...)`. One call site covers every tool
and every verdict — nothing tool-specific to wire per tool.

**Sensitive-path withholding is scoped to `run_command` only.** A new
exported helper, `safe_commands.touches_sensitive_path(command: str,
executor) -> bool`, factored out of the existing classifier's path-match
step (§4.2 of `2026-07-20-issue-041-safe-command-auto-approval-design.md`
step 6) without changing that classifier's existing behavior at all — it
is a pure extraction, and Task 21.1's contract suite (already green) pins
`requires_confirmation`'s behavior byte-for-byte, so this refactor must
not alter it. `write_file`/`read_file`/`list_dir`/`search_files` do NOT
get this treatment: the three read-side tools are already cwd-sandboxed
(`_safe_path()`) so they can't reach `~/.ssh` unless Salvador's cwd is
already inside it; `write_file`'s risk lives in the write action itself
(already confirmation-gated), not in its result-log line (`"wrote N bytes
to <path>"`, which never contains file content).

### 4.2 Entry schema (JSONL, one JSON object per line)

```json
{
  "ts": "2026-07-21T14:32:10.481Z",
  "session_id": "b3f1c9a2-4e7d-4a1b-9c3e-2f8a6d0b5e11",
  "cwd": "/home/salvador/project",
  "tool": "run_command",
  "call_id": "call_1",
  "arguments": {"command": "ls -la /var", "description": "look around"},
  "executed_command": null,
  "verdict": "yes",
  "executed": true,
  "result": "exit code: 0\nstdout:\n...\nstderr:\n",
  "result_omitted_reason": null
}
```

Field notes:
- `ts` — UTC, ISO-8601 with milliseconds.
- `session_id` — one UUID4 generated at sasha process startup (in
  `__main__.py`, alongside where `Executor`/`Session` are constructed),
  included in every entry from that run so entries can be grouped by "one
  sasha invocation" without relying on timestamp proximity.
- `verdict` — one of `"yes"`, `"no"`, `"edit"`, `"reply"`,
  `"auto-approved"` (mirrors the verdict vocabulary `agent.py` already
  uses, plus the auto-approved case from Task 21.1).
- `executed_command` — non-null only when `verdict == "edit"` (the one
  case where what ran differs from what the LLM proposed); otherwise
  `null`, to avoid duplicating `arguments.command`.
- `executed` — `true` iff `tool.handler` actually ran.
- `result` / `result_omitted_reason` — exactly one of four states:
  - executed normally: `result` has the text, reason `null`.
  - declined / replied / edited-away (nothing ran): `result: null`,
    reason `"declined"`.
  - executed but withheld (`run_command` touched a sensitive path):
    `result: null`, reason `"sensitive_path"`.
  - stripped later by compaction: `result: null`, reason `"compacted"`
    (stamped in-place over whatever the original reason was, since once
    compacted the original executed/withheld distinction no longer
    matters for space purposes — the call metadata alone still tells you
    it ran).

### 4.3 Compaction

**Config** — new `[tool_log]` section in `config.toml`, self-installing
the same way `[safe_commands]` did in Task 21.1:
```toml
[tool_log]
max_size_mb = 100
```

**Trigger** — after every `log_call()` append, a cheap
`os.path.getsize()` check against `max_size_mb * 1_000_000`. If over,
compaction runs on a background thread (reusing the Phase 19 memory-
bootstrap background-task pattern, so the interactive prompt stays
responsive — same mechanism as the busy-star indicator). A
`threading.Lock` ensures the automatic trigger and a manual
`sasha compact-log` invocation can never run concurrently.

**Algorithm**:
1. Read all JSONL lines from the log file. Skip (don't crash on) any line
   that fails to parse as JSON — tolerates a truncated trailing line from
   a crash mid-append.
2. Walk entries oldest → newest. For each entry that still has a non-null
   `result`, replace it with `null` and set
   `result_omitted_reason: "compacted"`, accumulating the running
   estimated serialized size as you go.
3. Stop once the running total drops to **50% of `max_size_mb`** — a
   hysteresis buffer so compacting down to exactly the limit doesn't
   immediately re-trigger on the very next logged call.
4. Serialize all entries (compacted prefix + untouched suffix) to a temp
   file in the same directory, then atomically `os.rename()` over the
   original. A crash mid-compaction leaves the original file untouched —
   never a corrupted log.
5. **If stripping every entry's result still leaves the file over
   `max_size_mb`** (the metadata alone exceeds the limit — a pathological
   case), stop and surface, on the next sasha startup or right after the
   failed attempt:
   `"[sasha] tool_log.jsonl is <N> MB even with all results stripped —
   delete it or archive it (e.g. mv ~/.local/state/sasha/tool_log.jsonl
   ~/tool_log-archive-<date>.jsonl) to reclaim space."`

**Manual command** — `sasha compact-log`, a new REPL built-in dispatched
the same way `sasha memory`/`sasha forget` already are in `repl.py`. Runs
the identical compaction routine on demand regardless of current size,
and reports the before/after file size.

## 5. Interface changes summary

- New module `src/sasha/tool_log.py`: `log_call(...)`, `compact(...)`.
- `src/sasha/safe_commands.py`: new exported `touches_sensitive_path(command,
  executor) -> bool`, extracted from the existing classifier's step 6
  with no behavior change to `requires_confirmation`.
- `src/sasha/agent.py`: one new call to `tool_log.log_call(...)` per tool
  call in `run_consult`, after `result_str` is finalized.
- `src/sasha/__main__.py`: generate `session_id` (UUID4) at startup, pass
  it plus the resolved `state_dir` path and `config.tool_log_max_size_mb`
  to wherever `log_call` needs them.
- `src/sasha/config.py`: new `Config.tool_log_max_size_mb: int = 100`
  field, `[tool_log]` TOML section, same default-and-fallback pattern as
  `[safe_commands]`.
- `src/sasha/repl.py`: new `sasha compact-log` built-in.

No changes to `Tool`, `ToolResult`, `ExecResult`, or any existing tool's
schema/behavior.

## 6. Error handling

- Log write failure (disk full, permission error): caught, entry dropped,
  one-time-per-process warning — never blocks or crashes the agent loop.
- Corrupt/truncated trailing line in the log file: skipped during
  compaction, not fatal.
- Log file created with `chmod 600` — full command outputs can be
  sensitive even outside the denylist (e.g. a `git log` whose commit
  messages contain a token), so it shouldn't be world/group-readable by
  default.
- Compaction failures (can't write the temp file, can't rename): caught,
  logged as a warning, original file left untouched.

## 7. Decisions log

- **Decision:** Single global log (not per-project like project memory).
  **Rationale:** Salvador's call — study is cross-project by nature; each
  entry's `cwd` field gives project-level grouping without scattering
  the data across every repo.
- **Decision:** Full verbatim call+result capture, no per-field
  truncation.
  **Rationale:** Salvador's explicit call, prioritizing complete
  reconstruction over log-file economy; size is managed at the file level
  (compaction) instead.
- **Decision:** Withhold (don't store) `result` for `run_command` calls
  touching a sensitive-path, even though everything else is verbatim.
  **Rationale:** Raised by Tier 1 as a direct tension with "verbatim,
  forever" — approving a sensitive-path read (e.g. `cat ~/.ssh/id_rsa`)
  would otherwise write the secret's actual contents into a persistent,
  ever-growing plaintext file, a strictly bigger blast radius than the
  original command. Salvador agreed to the carve-out over the objection.
- **Decision:** Compaction runs on a background thread, not synchronously.
  **Rationale:** Raised by Tier 1 — a full-file rewrite on a 100 MB log is
  a multi-second operation; blocking the interactive prompt for that,
  right when the threshold is crossed, is bad UX for a feature whose only
  job is passive record-keeping. Salvador agreed.
- **Decision:** Compaction target is 50% of `max_size_mb`, not the limit
  itself.
  **Rationale:** Tier 1's call (not separately re-litigated) — pure
  hysteresis to avoid re-compacting on every single call once the log
  sits near the threshold.
- **Decision:** Both automatic (size-triggered) and manual
  (`sasha compact-log`) compaction triggers.
  **Rationale:** Salvador's call — automatic for the common case, manual
  for whenever he wants to shrink the file proactively (e.g. before
  archiving) without waiting for the threshold.
- **Decision:** Sensitive-path withholding scoped to `run_command` only,
  not `write_file`/`read_file`/`list_dir`/`search_files`.
  **Rationale:** Salvador's call after Tier 1 raised widening it — the
  three read-side tools are already cwd-sandboxed (can't reach denylisted
  paths under normal use), and `write_file`'s danger is the write action
  itself, not its result-log line.

## 8. Testing strategy

All pure logic, no PTY harness needed:
- `log_call()` produces correctly-shaped entries for every
  verdict × executed combination (yes/no/edit/reply/auto-approved),
  including the `executed_command` only-on-edit rule.
- `touches_sensitive_path()` extraction is extracted such that
  `requires_confirmation`'s existing behavior is unchanged — the full
  Task 21.1 contract suite (already green) must stay green unmodified as
  part of this task's own gate, proving the refactor is behavior-neutral.
- Compaction: given a synthetic oversized log, oldest-first stripping
  stops at ~50%, uses atomic temp+rename, tolerates a corrupt trailing
  line, and warns correctly when stripping everything still isn't enough.
- Config: `[tool_log].max_size_mb` parses with the same default-and-
  fallback pattern already proven for `[safe_commands]`.
- Dispatch integration: `run_consult` calls `log_call` exactly once per
  tool call, for every branch, verified with a spy in place of the real
  `tool_log` module.
- `sasha compact-log` triggers the same compaction path on demand and
  reports before/after size.
