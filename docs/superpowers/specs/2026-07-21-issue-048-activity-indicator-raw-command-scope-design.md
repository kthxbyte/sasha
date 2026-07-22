# Stop cursor activity indicator for raw-typed commands (ISSUE-048)

**Status:** Draft for review
**Date:** 2026-07-21

## 1. Mission

The cursor-based `ActivityIndicator` (Task 26.1/26.2, ISSUE-045) currently
wraps two distinct phases: LLM "thinking" and the raw-typed shell command
Salvador types directly at the prompt (tagged `"tool"`). The second wrap is
wrong — it visually corrupts full-screen/interactive terminal apps (`nano`,
`mc`, `git log`'s pager) because that code path hands the child process real
foreground terminal control, and the indicator's background-thread cursor
toggling fights with the app's own cursor management. This task removes that
one wrap; everything else about the indicator (LLM-thinking phase,
agent-driven tool-call phase) is unchanged.

## 2. Scope

### In scope:
- Remove the `ActivityIndicator` start/stop wrap around raw-typed command
  execution in `src/sasha/repl.py`.

### Out of scope / non-goals:
- No attempt to distinguish interactive/full-screen raw commands (`nano`,
  `less`) from plain long-running ones (`curl`, `sleep 30`) — Salvador's
  call: the indicator is removed unconditionally for the whole raw-command
  path, not selectively. A raw command's own output (or the prompt
  returning) is the "it's running/done" signal for this path, same as
  before Task 26.1 ever existed.
- No changes to `ActivityIndicator` itself, `agent.py`'s `on_phase` hook, or
  the LLM-thinking / agent-driven-tool-call phases — both stay wired exactly
  as Task 26.1 shipped them, in `Repl._consult()`.
- No changes to `Executor.run()`, job control, or `capture_stdout` behavior.

## 3. Key contracts & assumptions

1. **Root cause, confirmed by reading the code.** `src/sasha/repl.py`'s raw-
   command branch (the "otherwise, run it as a shell command" case) calls
   `self._executor.run(stripped)` with default `capture_stdout=False`.
   `Executor.run()` does a real `tcsetpgrp` foreground-process-group handoff
   (Task 4.1) for this path — the child gets genuine interactive terminal
   control. `ActivityIndicator`'s hide/show cursor + OSC 12 color toggling
   from a background thread visibly conflicts with apps that manage their
   own cursor (`nano`, `mc`) or invoke a pager that does (`git log` → `less`).
2. **The agent-driven tool-call path is unaffected and needs no change.**
   `tools.py`'s `_run_command_result()` calls the same `Executor.run()` but
   with `capture_stdout=True` — stdout is a pipe, not a tty, so `isatty()`
   is false for the child. Full-screen apps either can't run meaningfully
   that way or (like `git log`) auto-disable pagination. No corruption risk
   there, so the indicator stays active for agent-driven tool calls.
3. **Purely subtractive.** No new modules, no new config, no new class
   methods — three lines deleted from one function in `repl.py`.

## 4. The change

`src/sasha/repl.py`, the raw-typed-command branch: delete the
`self._activity.start("tool")` line immediately before, and the
`self._activity.stop()` line immediately after,
`result = self._executor.run(stripped)`. That call and everything below it
(exit-code/stderr capture, sensitive-path check, `tool_log.log_call(...)`)
is unchanged.

## 5. Decisions log

- **Decision:** Remove the indicator wrap unconditionally for the whole
  raw-command path, no interactive/non-interactive distinction.
  **Rationale:** Salvador's call — added complexity (a TUI-app denylist or
  a TTY-mode heuristic) isn't justified for a case he didn't ask to solve;
  matches this project's established pattern of declining cosmetic
  complexity without real day-to-day friction.
- **Decision:** Leave the LLM-thinking and agent-tool-call phases untouched.
  **Rationale:** Neither exhibits the corruption Salvador reported, and the
  root-cause analysis (contract #2 above) explains why structurally, not
  just empirically.

## 6. Testing strategy

One existing contract test currently pins the behavior being removed:
`tests/contract/test_task_26_1_activity_indicator.py::test_ac12_raw_command_wrapped_with_tool_phase`
asserts `("start", "tool")` and `("stop", None)` both appear in the spy's
call list after `repl.run_once("echo hi")`. This test is inverted (renamed
to `test_ac12_raw_command_not_wrapped_with_activity_indicator`) to assert
the spy's call list is empty after the same `run_once("echo hi")` call —
this is a Tier-1 contract-test amendment, made in the same commit as this
spec, before dispatch (per CLAUDE.md: the implementer never touches
`tests/contract/`). No other contract test references the raw-command path's
activity-indicator wrapping (`test_task_6_2_colorization_prompt.py` and
`test_task_9_1_configurable_prompt.py` each have one stale comment
mentioning Task 26.1's indicator, unrelated to this behavior, left as-is).

`test_ac13_consult_stops_activity_on_chat_error` and
`test_ac14_repl_combines_config_and_color_enabled_gates` (same file) already
exercise the `_consult()` path exclusively and need no change — they remain
the regression guard proving the LLM-thinking/tool-call wiring survives this
task untouched.
