# Task 26.1: Cursor-based activity indicator (ISSUE-045)

## Authoritative contract
`docs/superpowers/specs/2026-07-21-issue-045-cursor-activity-indicator-design.md`
(all sections) is the complete contract for this task. This task builds
and wires the new indicator; removing the Phase 19 busy-star mechanism
is Task 26.2, a separate task — **do not touch `prompt.py`,
`DEFAULT_TEMPLATE`, `_background_tasks`, `_begin_background_task`, or
`_end_background_task` in this task.**

## What already exists
- `src/sasha/agent.py` (Task 16.2/21.1/22.1/25.1) — `run_consult`'s
  dispatch loop. **Bind to the existing confirm/auto-approve/execute
  branching, `tool_log`/`safe_commands` calls, and repetition-guard logic
  — do not restructure any of it.** Only the exact insertions in the fix
  sketch below are added.
- `src/sasha/llm.py` — `ChatClient.chat(messages, tools, on_text)`. **Do
  not modify.** `on_text` fires only when a stream chunk carries
  `delta.content` — confirmed by reading this file directly; it never
  fires for a turn that's pure tool-calls with no preceding text. This
  is exactly why two independent stop triggers are needed (fix sketch
  below).
- `src/sasha/repl.py` — `Repl.__init__`, `_consult()`, the raw-command
  path (Task 25.1's block ending in `self._session.note_command(...)`).
  **Bind to all of this — only the exact insertions below are added.**
- `src/sasha/config.py` (Task 1.1/9.1/21.1/22.2) — `Config`,
  `_build_config`, `_DEFAULT_CONFIG_TOML`. Follow the exact
  default-and-fallback pattern already established for `[tool_log]`.
- `tests/contract/test_task_2_3_agent.py`,
  `tests/contract/test_task_16_2_agent_registry.py`,
  `tests/contract/test_task_22_1_tool_call_logging.py`,
  `tests/contract/test_task_22_2_tool_call_log_compaction.py` — MUST
  stay green unmodified. None of them pass `on_phase` to `run_consult`,
  so its default no-op keeps their behavior byte-identical.

## What's new
1. **`src/sasha/activity_indicator.py`** (new file) — the
   `ActivityIndicator` class.
2. **`src/sasha/agent.py`** — `run_consult` gains one new optional
   parameter, `on_phase`, called at the phase-transition points described
   in the fix sketch.
3. **`src/sasha/repl.py`** — owns one `ActivityIndicator` instance; wires
   it into `_consult()` and the raw-command path.
4. **`src/sasha/config.py`** — new `[activity_indicator]` section and
   four new `Config` fields.

## Fix sketch (authoritative)

### `src/sasha/activity_indicator.py` (new file)

```python
from __future__ import annotations

import sys
import threading

_HIDE = "\x1b[?25l"
_SHOW = "\x1b[?25h"
_RESET_COLOR = "\x1b]112\x07"


class ActivityIndicator:
    """Drives cursor visibility + OSC 12 color from a background thread
    to signal Sasha is busy, since native terminal cursor blink is
    neither rate-controllable nor reliably toggleable (confirmed by
    hand against a real terminal before this was designed — see
    docs/superpowers/specs/2026-07-21-issue-045-cursor-activity-indicator-design.md).
    """

    def __init__(self, colors: dict[str, str], interval_s: float = 0.05,
                 enabled: bool = True) -> None:
        self._colors = colors
        self._interval_s = interval_s
        self._enabled = enabled
        self._thread: threading.Thread | None = None
        self._stop_event: threading.Event | None = None

    def start(self, phase: str) -> None:
        if not self._enabled:
            return
        color = self._colors.get(phase)
        if color is None:
            return
        self.stop()
        self._stop_event = threading.Event()
        self._thread = threading.Thread(
            target=self._run, args=(color, self._stop_event), daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._thread is None:
            return
        self._stop_event.set()
        self._thread.join(timeout=1)
        self._thread = None
        self._stop_event = None

    def _run(self, color: str, stop_event: threading.Event) -> None:
        self._emit(f"\x1b]12;{color}\x07")
        visible = True
        try:
            while not stop_event.is_set():
                self._emit(_SHOW if visible else _HIDE)
                visible = not visible
                stop_event.wait(self._interval_s)
        finally:
            self._emit(_SHOW)
            self._emit(_RESET_COLOR)

    @staticmethod
    def _emit(seq: str) -> None:
        sys.stdout.write(seq)
        sys.stdout.flush()
```

### `src/sasha/agent.py` — exact changes

Add `on_phase: Callable[[str | None], None] = lambda phase: None` as the
last parameter of `run_consult`. Add a local closure right after the
`previous_key`/`tool_turns` init, and use it at both `client.chat(...)`
call sites:

```python
    def _consult_llm(messages, tools):
        on_phase("thinking")
        stopped = False

        def _on_text(chunk):
            nonlocal stopped
            if not stopped:
                on_phase(None)
                stopped = True
            out(chunk)

        response = client.chat(messages, tools=tools, on_text=_on_text)
        on_phase(None)
        return response
```
Replace `response = client.chat(messages, tools=schema, on_text=out)`
with `response = _consult_llm(messages, schema)`. Replace
`final_response = client.chat(final_messages, tools=[], on_text=out)`
with `final_response = _consult_llm(final_messages, [])`.

Wrap both `tool.handler(final_arguments, executor)` call sites (the
confirmed-`yes`/`edit` branch's `if executed:` block, and the
auto-approved `else:` branch) with `on_phase("tool")` immediately before
and `on_phase(None)` immediately after — nothing else in either branch
changes. The declined-verdict branches (`no`/`edit`-as-reply/`reply`)
are untouched — `tool.handler` is never called there, so no `on_phase`
call is added.

### `src/sasha/repl.py` — exact changes

Add `from sasha.activity_indicator import ActivityIndicator` to imports.
In `Repl.__init__`, after `self._current_profile = ...`:
```python
        self._activity = ActivityIndicator(
            colors={"thinking": config.activity_indicator_thinking_color,
                    "tool": config.activity_indicator_tool_color},
            interval_s=config.activity_indicator_interval_s,
            enabled=config.activity_indicator_enabled and sink.color_enabled,
        )
```
Add a small adapter method:
```python
    def _on_activity_phase(self, phase):
        if phase is None:
            self._activity.stop()
        else:
            self._activity.start(phase)
```
In `_consult()`, add `on_phase=self._on_activity_phase` to the
`run_consult(...)` call's keyword arguments, and add
`self._activity.stop()` as the first line of the existing `finally:`
block (before `self._sink.ensure_newline()`).

In the raw-command path, immediately before
`result = self._executor.run(stripped)`, add `self._activity.start("tool")`;
immediately after, add `self._activity.stop()`.

### `src/sasha/config.py` — exact changes

1. Add four fields to `Config`, after `tool_log_max_size_mb`:
```python
    activity_indicator_enabled: bool = True
    activity_indicator_interval_s: float = 0.05
    activity_indicator_thinking_color: str = "#C084FC"
    activity_indicator_tool_color: str = "#FFD700"
```
2. In `_build_config`, after the `tool_log_max_size_mb` block:
```python
    activity_indicator_enabled = True
    activity_indicator_interval_s = 0.05
    activity_indicator_thinking_color = "#C084FC"
    activity_indicator_tool_color = "#FFD700"
    activity_table = raw.get("activity_indicator", {})
    if isinstance(activity_table, dict):
        raw_enabled = activity_table.get("enabled")
        if isinstance(raw_enabled, bool):
            activity_indicator_enabled = raw_enabled
        raw_interval = activity_table.get("interval_s")
        if (isinstance(raw_interval, (int, float))
                and not isinstance(raw_interval, bool)
                and raw_interval > 0):
            activity_indicator_interval_s = float(raw_interval)
        raw_thinking = activity_table.get("thinking_color")
        if isinstance(raw_thinking, str) and raw_thinking:
            activity_indicator_thinking_color = raw_thinking
        raw_tool = activity_table.get("tool_color")
        if isinstance(raw_tool, str) and raw_tool:
            activity_indicator_tool_color = raw_tool
```
3. Add the four fields to the `return Config(...)` call, after
   `tool_log_max_size_mb=tool_log_max_size_mb,`.
4. Append to `_DEFAULT_CONFIG_TOML` (after the `[tool_log]` table):
```toml

[activity_indicator]
enabled = true
interval_s = 0.05
thinking_color = "#C084FC"
tool_color = "#FFD700"
```

## Constraints
- Modify `src/sasha/agent.py`, `src/sasha/repl.py`, `src/sasha/config.py`.
  Add `src/sasha/activity_indicator.py`.
- Do not touch `src/sasha/prompt.py`, `src/sasha/tools.py`,
  `src/sasha/safe_commands.py`, `src/sasha/tool_log.py`,
  `src/sasha/executor.py`, `src/sasha/__main__.py` — nothing in this
  task's contract exercises them.
- Do not remove or modify `_background_tasks`,
  `_begin_background_task`, `_end_background_task`, the `\S` prompt
  token, or `DEFAULT_TEMPLATE` — that's Task 26.2, not this one.
- Do not add per-tool-type colors, a "done" flash, or an indicator around
  the confirmation-prompt wait — all deliberately out of scope (design
  doc §2).
- **Executor routing (per CLAUDE.md's standing rule): this task edits
  existing control flow in `agent.py`/`repl.py`/`config.py` that already
  works (surgical), even though `activity_indicator.py` is new. Dispatch
  to the default model (deepseek-v4-pro) — do not launch under a
  local-model `SASHA_DISPATCH_MODEL` override.**

## Acceptance criteria
(encoded 1:1 in `tests/contract/test_task_26_1_activity_indicator.py`)
1. `ActivityIndicator.start(phase)` for a known phase emits the OSC 12
   color-set sequence and visibility-toggle sequences; `stop()` ends the
   background thread and emits show-cursor + OSC 112 reset —
   `test_ac1_start_stop_emits_expected_sequences`.
2. `start(phase)` for a phase not present in `colors` is a no-op — no
   thread spawned, nothing written —
   `test_ac2_unknown_phase_is_noop`.
3. `enabled=False` makes every `start()` call a no-op regardless of
   phase — `test_ac3_disabled_is_always_noop`.
4. `stop()` without a prior `start()`, and calling `stop()` twice in a
   row, are both safe no-ops — `test_ac4_stop_is_idempotent`.
5. Calling `start()` again without an intervening `stop()` cleanly
   replaces the running thread — exactly one thread alive afterward, no
   leaked thread still writing — `test_ac5_restart_replaces_running_thread`.
6. A text-bearing `run_consult` turn calls `on_phase("thinking")` then
   `on_phase(None)`, the latter triggered no later than the first
   streamed chunk — `test_ac6_thinking_phase_stops_on_first_text`.
7. A text-free (pure tool-call) turn still calls `on_phase("thinking")`
   then `on_phase(None)` — via the post-`chat()`-return path, since
   `on_text` never fires for it — `test_ac7_thinking_phase_stops_after_chat_returns_when_no_text`.
8. An executed tool call (both the confirmed-`yes` and auto-approved
   paths) wraps `tool.handler(...)` with `on_phase("tool")`/`on_phase(None)` —
   `test_ac8_tool_phase_wraps_handler_execution`.
9. A declined tool call (verdict `"no"`) never triggers `on_phase("tool")` —
   `test_ac9_declined_tool_never_triggers_tool_phase`.
10. The final stop-message consult (repetition-guard/turn-limit path)
    also gets `on_phase("thinking")`/`on_phase(None)` wrapping —
    `test_ac10_final_stop_message_consult_also_wrapped`.
11. `run_consult`'s existing behavior with no `on_phase` argument is
    unchanged — no new test; the full existing agent-dispatch suites
    stay green, unmodified, as part of this task's own gate.
12. `repl.py`'s raw-command path calls `activity.start("tool")` before
    `Executor.run()` and `activity.stop()` after —
    `test_ac12_raw_command_wrapped_with_tool_phase`.
13. `_consult()`'s `finally` block stops the activity indicator even
    when `ChatError` is raised mid-turn —
    `test_ac13_consult_stops_activity_on_chat_error`.
14. `Repl` constructs its `ActivityIndicator` with `enabled` equal to
    `config.activity_indicator_enabled and sink.color_enabled` — tested
    with both combinations —
    `test_ac14_repl_combines_config_and_color_enabled_gates`.
15. `[activity_indicator]` config parses with the same
    default-and-fallback pattern as `[tool_log]`, including rejecting a
    non-positive `interval_s` and a non-bool `enabled` —
    `test_ac15_config_default_and_fallback`.

## Standing instructions
- Your goal: make
  `tests/contract/test_task_26_1_activity_indicator.py` pass, with the
  full existing suite green: `bash scripts/run-tests.sh`.
- NEVER modify files under `tests/contract/` — the gate rejects the
  commit if you do. If a contract test seems wrong or unimplementable,
  stop and flag it; do not commit.
- Deliver exactly ONE new commit on top of the HEAD you started from;
  never amend, rebase, or reset; leave the working tree clean.
- Commit message per AGENTS.md, with trailers: `Done: Task 26.1 — <one
  line>`, `Next: Task 26.2 — Remove the Phase 19 busy-star mechanism`,
  and the Co-Authored-By signature line from your dispatch prompt.
