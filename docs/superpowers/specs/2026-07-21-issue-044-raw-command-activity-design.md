# Raw-typed command activity awareness — Design (ISSUE-044)

**Status:** Draft for review
**Date:** 2026-07-21
**Amends:** `docs/superpowers/specs/2026-07-21-tool-call-logging-design.md` (extends
`tool_log`'s scope), `Session`'s existing notes/last_stderr mechanism (ISSUE-031)

## 1. Mission

Give Sasha visibility into raw shell commands Salvador types directly at
the prompt — not just commands the LLM itself proposes via `run_command`
— covering both a durable audit trail (for troubleshooting later, the
same way `tool_log.jsonl` already serves LLM-invoked commands) and a
short recency window that actually reaches the LLM's own context (so it
knows what Salvador has been doing between questions). Investigation
first (this design's own §7): a project scan found this gap is narrower
than it first looked — `Session.notes`/`last_stderr` already capture the
last 10 raw commands as line+exit-code, cleared after every `??`, but
with no output and no persistent trace. `tool_log.py` (ISSUE-042) already
solves the persistence/compaction problem completely, just not for this
call site.

## 2. Scope

### In scope:
- One new `tool_log.log_call(...)` call site, in `repl.py`'s raw-command
  path, reusing `tool_log.py`/`safe_commands.py` entirely as-is.
- Enriching `Session`'s recency window (`NoteCommand` gains `stderr_text`;
  `Session.last_stderr` is removed, its role absorbed into each note) and
  shrinking its cap from 10 to 5, given the bigger per-entry size.

### Out of scope / non-goals:
- **Stdout capture for raw-typed commands.** Stays off, exactly as today
  — turning it on would wrap every typed command's fd 1 in a synthetic
  PTY (new overhead, new regression surface, now also watched by the
  ISSUE-040 keystroke relay), for a scope this design doesn't need.
  Persistent and live records for raw-typed commands only ever carry
  stderr.
- **A distinct "files touched" tracker.** Command lines and their stderr
  already imply file involvement (`vim foo.py`, `cat bar.txt`) to both
  the LLM and to Salvador re-reading the log later — a separate
  file-touch heuristic would solve a problem the existing text already
  solves.
- **New privacy filtering for the LLM's own context.** The recency
  window already shows raw stderr to the LLM unfiltered today (not a
  new risk introduced here); that concern belongs to the separate,
  already-deferred `local: bool` / remote-provider-privacy feature idea
  raised during the ISSUE-041 brainstorm, not this design.
- **New config, new manual command, new compaction logic.** All of that
  already exists (`[tool_log]`, `sasha compact-log`, the 50%-target
  strip-oldest-first algorithm) and is reused completely unmodified.

## 3. Key contracts & assumptions

1. **One record per raw-typed command, `verdict="direct"`.** A new
   verdict value alongside `yes`/`no`/`edit`/`reply`/`auto-approved`,
   meaning "typed directly, no LLM involved, always executed" — no
   `"declined"` case is possible for this verdict, since there's no
   confirmation gate on raw prompt input.
2. **Sensitive-path withholding applies identically**, via the existing
   `safe_commands.touches_sensitive_path(command, executor)` — the same
   function `agent.py` already calls, no new logic. This mostly matters
   as defense-in-depth given stdout isn't captured (§2's non-goal); a
   command's *stderr* could still leak fragments in edge cases.
3. **The two mechanisms serve different consumers and don't need to
   agree with each other.** `tool_log.jsonl` is the durable, compacted
   audit trail; `Session`'s recency window is what the LLM actually
   sees next turn. Nothing requires them to hold the same data or the
   same window size.

## 4. Mechanism

### 4.1 `repl.py`'s raw-command path

Immediately after the existing `result = self._executor.run(stripped)`,
before `self._session.note_command(stripped, result)`:

```python
result_text = result.stderr_text
result_omitted_reason = None
if safe_commands.touches_sensitive_path(stripped, self._executor):
    result_text = None
    result_omitted_reason = "sensitive_path"

tool_log.log_call(
    tool_name="run_command", call_id=str(uuid.uuid4()),
    arguments={"command": stripped}, executed_command=None,
    cwd=self._executor.cwd, verdict="direct", executed=True,
    result_text=result_text, result_omitted_reason=result_omitted_reason,
)
tool_log.check_and_maybe_compact_async(self._sink.write_line)
```

No changes to `tool_log.py` itself — `log_call`, `compact`,
`check_and_maybe_compact_async` are used exactly as Task 22.1/22.2 built
them. `repl.py` gains `import uuid` and `from sasha import safe_commands`
(it already imports `tool_log`).

### 4.2 `Session`'s recency window

`NoteCommand` gains a `stderr_text: str = ""` field. `note_command`
stores it per-entry instead of Session tracking a separate
`self.last_stderr`, which is removed entirely. The cap drops from 10 to
5. `build_messages()`'s context-block construction includes each note's
`stderr_text` inline (when non-empty) instead of appending a single
separate `last_stderr` line at the end. The "cleared after every `??`"
lifecycle (ISSUE-031) is unchanged — only the content per entry is
richer.

## 5. Decisions log

- **Decision:** Reuse `tool_log`'s existing schema/compaction/withholding
  wholesale rather than building a parallel mechanism.
  **Rationale:** Salvador's call, confirmed directly — everything needed
  (permanent storage, bounded growth, sensitive-path protection) already
  exists for LLM-invoked commands; duplicating it for raw-typed ones
  would be pure waste.
- **Decision:** Stdout capture stays off for raw-typed commands.
  **Rationale:** Salvador's call, weighing new-PTY-per-typed-command
  overhead and regression risk (interactive programs behaving
  differently under a synthetic stdout PTY, now also watched by the
  ISSUE-040 relay) against completeness — not worth it for this scope.
- **Decision:** No separate "files touched" tracking mechanism.
  **Rationale:** Salvador's call — command text and output already
  carry this information implicitly; a heuristic file-touch tracker
  would be solving an already-solved problem.
- **Decision:** Remove `Session.last_stderr` outright rather than keep
  it alongside the richer per-note `stderr_text`.
  **Rationale:** Tier 1's call, not separately re-litigated — the two
  fields would otherwise overlap in purpose with no distinct reason to
  keep both; one place to look is simpler.
- **Decision:** Recency window shrinks from 10 to 5 entries.
  **Rationale:** Salvador's call — keeps per-turn context size in the
  same rough ballpark now that each entry carries real content, not
  just a line and a number.

## 6. Testing strategy

- A raw-typed command (success and failure) produces a `tool_log` entry
  with `verdict="direct"`, `executed=True`, correct `result_text`
  (stderr) and `arguments={"command": ...}`.
- A raw-typed command touching a sensitive path gets `result_text=None`,
  `result_omitted_reason="sensitive_path"` — same mechanism as
  `run_command`'s existing withholding, new call site only.
- `Session.notes` caps at 5, each entry carries its own `stderr_text`,
  `build_messages()` includes it in the context block and clears notes
  afterward — same lifecycle as today, richer content.
- Regression: existing `test_task_2_2_session.py` (or wherever
  `Session`'s current notes/last_stderr behavior is pinned — to be
  confirmed at task-spec time) and `repl.py`'s existing contract tests
  stay green, amended explicitly wherever they assert the old cap-of-10
  or reference `last_stderr` directly (same pattern as Task 21.1's
  contract-test amendments), not silently broken.

## 7. Project scan performed before this design (summary)

Read `session.py`, `triggers.py`, `mutes.py`, `repl.py`'s raw-command
path, and `memory.py`'s function list directly (not from memory of
earlier sessions) before proposing anything. Findings:
- `Session.notes`/`last_stderr` already capture the last 10 raw commands
  (line + exit code only) and the single most recent stderr, fed to the
  LLM on every `??` and cleared afterward (ISSUE-031) — a real, working
  mechanism Salvador wasn't fully aware of, closer to what he described
  wanting than the "no trace at all" hypothesis in his own framing.
- `tool_log.py` (ISSUE-042) covers the LLM-invoked half completely (full
  verbatim call+result, persisted, compacted) but has no call site for
  raw prompt-typed input at all — `agent.py`'s `log_call` invocation only
  runs inside `run_consult`'s dispatch loop.
- `memory.py` is deliberately curated (commits, explicit `remember()`,
  crash signals, bootstrap) — not a raw activity log, correctly left
  alone by this design.
- `mutes.py`/`triggers.py` only ever look at pass/fail to decide whether
  to interrupt Salvador — irrelevant to this design's goal and untouched.
