# Task 27.1: Remove ActivityIndicator wrap around the raw-typed command path (ISSUE-048)

## Authoritative contract
`docs/superpowers/specs/2026-07-21-issue-048-activity-indicator-raw-command-scope-design.md`
is the complete contract for this task: the cursor-based `ActivityIndicator`
(Task 26.1/26.2, ISSUE-045) currently wraps the raw-typed shell command path
in `src/sasha/repl.py` with `start("tool")`/`stop()`. That path hands the
child process real foreground terminal control (`Executor.run()`'s
`tcsetpgrp` handoff, Task 4.1), so the indicator's cursor-visibility/OSC 12
toggling visibly corrupts full-screen apps run that way (`nano`, `mc`,
`git log`'s pager). This task removes that one wrap only.

## What already exists
- `src/sasha/activity_indicator.py`, `agent.py`'s `on_phase` hook,
  `repl.py`'s `Repl._consult()` (LLM-thinking + agent-driven-tool-call
  wiring) — **do not touch any of this.** Design doc contract #2 explains
  why the agent-driven tool-call path doesn't have this bug (it calls
  `Executor.run()` with `capture_stdout=True`, so stdout is a pipe, not a
  tty): unaffected, unchanged.
- `src/sasha/repl.py`'s raw-command branch (the "otherwise, run it as a
  shell command" case, currently around line 463) — the `Executor.run()`
  call itself, and everything below it (`result_text` construction,
  `safe_commands.touches_sensitive_path` check, `tool_log.log_call(...)`)
  stay exactly as they are. **Bind to these — do not modify them.**

## What's new
Nothing new — this task only deletes two lines.

## Fix sketch (authoritative)

In `src/sasha/repl.py`, the raw-command branch currently reads:

```python
        self._activity.start("tool")
        result = self._executor.run(stripped)
        self._activity.stop()
```

Change it to:

```python
        result = self._executor.run(stripped)
```

That's the entire change. Nothing else in the file moves or changes.

## Pre-existing contract test — already amended by Tier 1, in the same
## commit as this spec (not the implementer's job)

`tests/contract/test_task_26_1_activity_indicator.py`'s
`test_ac12_raw_command_wrapped_with_tool_phase` pinned the exact behavior
being removed (asserted `("start", "tool")` and `("stop", None)` both
appear in a spy's call list after `repl.run_once("echo hi")`). It has been
renamed to `test_ac12_raw_command_not_wrapped_with_activity_indicator` and
inverted to assert `spy.calls == []` after the same call. No other
contract test references the raw-command path's activity-indicator
wrapping — `test_task_6_2_colorization_prompt.py` and
`test_task_9_1_configurable_prompt.py` each carry one stale comment
mentioning Task 26.1's indicator, unrelated to this behavior, untouched.

Verified locally in a scratch worktree together with the `repl.py` change
above: full suite green, 570/570 (unchanged count — this is a rename +
inversion of one existing test, not an add/delete).

## Constraints
- Modify only `src/sasha/repl.py`. The `tests/contract/` amendment above is
  already committed — **do not touch anything under `tests/contract/` at
  all.**
- Do not touch `src/sasha/activity_indicator.py`, `src/sasha/agent.py`,
  `src/sasha/config.py`, or `Repl._consult()` — none of them are part of
  this task's contract.
- Do not add any interactive/full-screen-command detection, denylist, or
  TTY-mode heuristic — the design doc's decision log is explicit: the wrap
  is removed unconditionally for the whole raw-command path, no
  conditional logic.
- **Executor routing (per CLAUDE.md's standing rule): this is a surgical
  removal from code that already works. Dispatch to the default model
  (deepseek-v4-pro) — do not launch under a local-model
  `SASHA_DISPATCH_MODEL` override.**

## Acceptance criteria
(no new contract-test file — purely subtractive, so the acceptance
criterion is the already-amended test above passing against your
`repl.py` change)
1. The full existing suite is green: 570/570 —
   `bash scripts/run-tests.sh`.
2. `src/sasha/repl.py`'s raw-command branch calls `self._executor.run(stripped)`
   with no `self._activity.start(...)`/`self._activity.stop()` calls
   immediately before/after it.
3. `Repl._consult()` and its `on_phase`-driven activity-indicator wiring
   are byte-for-byte unchanged from before this task.

## Standing instructions
- Your goal: the full existing suite green (570/570) after your one-line
  change: `bash scripts/run-tests.sh`.
- NEVER modify files under `tests/contract/` — the gate rejects the commit
  if you do. The amendment this task needed there is already committed as
  part of this spec. If something still seems wrong once you've made the
  `repl.py` change, stop and flag it; do not commit.
- Deliver exactly ONE new commit on top of the HEAD you started from;
  never amend, rebase, or reset; leave the working tree clean.
- Commit message per AGENTS.md, with trailers: `Done: Task 27.1 — <one
  line>`, `Fixed: ISSUE-048`,
  `Next: Task 27.2 — Phase 27 review (Tier 1, with Salvador)`, and the
  Co-Authored-By signature line from your dispatch prompt.
