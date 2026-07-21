# Cursor-based activity indicator (ISSUE-045)

**Status:** Draft for review
**Date:** 2026-07-21
**Amends:** removes the Phase 19 busy-star mechanism (`docs/superpowers/specs/task-19.2-busy-star-indicator.md`)
**Resolves (partially):** ISSUE-025's "thinking indicator" candidate surface —
color scheme is new/configurable here, but retrofitting *existing* prompt/sink
text colors into the same scheme stays out of scope (see §2)

## 1. Mission

Give Sasha a real, working "I'm busy" signal for the phases that actually
matter — the LLM thinking, and a tool call (or raw-typed command)
running — replacing the Phase 19 prompt busy-star, which never worked
for those cases: it only ever reflects genuine background threads still
alive when the *next* prompt is drawn (confirmed: only the memory-
bootstrap thread ever wired into it; `tool_log` compaction never did
either), and both the LLM-consult and tool-call phases run synchronously
on the main thread, so there's no concurrent redraw moment for it to
blink during. Fully validated by hand first: Salvador's terminal doesn't
honor DECSCUSR's blink-vs-steady distinction at all, but cursor
*visibility* toggling (`\e[?25l`/`\e[?25h`) and OSC 12 cursor *color*
both work reliably — confirmed live via throwaway scratch scripts before
any of this was designed, including end-to-end staged phases (thinking →
tool-call → done) at `interval_s=0.05`, which read clearly as "busy work"
without being noisy.

## 2. Scope

### In scope:
- A new `ActivityIndicator` class driving cursor visibility + OSC 12
  color from a background thread, with a simple `start(phase)`/`stop()`
  interface.
- Wiring it into `agent.py`'s `run_consult` (thinking/tool-call phases)
  and `repl.py`'s raw-command path (Task 25.1's non-`??` commands).
- A new `[activity_indicator]` config.toml section: enabled toggle,
  blink interval, per-phase colors.
- Full removal of the Phase 19 busy-star mechanism (see §6) — it's being
  replaced, not left to coexist as dead weight.

### Out of scope / non-goals:
- **Retrofitting existing informational-text colors** (`TerminalSink`'s
  `[sasha] ...` message lines, `prompt.py`'s own color tokens) into the
  same config scheme. Salvador's call — keep this design focused on the
  new indicator; unifying everything is real, related work (this is
  exactly what ISSUE-025's still-open note anticipated) but a separate
  follow-up, not bundled here.
- **A "done" flash.** Salvador's call — clean, silent return to normal
  cursor state when a phase ends; no third color/state to design around.
- **Per-tool-type colors.** One "tool" phase color regardless of which
  tool is executing (`run_command`, `write_file`, etc.) — can be revisited
  later if wanted, not needed now.
- **Any indicator during the confirmation-prompt wait** (`confirmer.ask`).
  That's Salvador being asked something, not Sasha working — a blinking
  cursor there would misleadingly suggest activity.

## 3. Key contracts & assumptions

1. **`agent.py` stays terminal-agnostic.** `run_consult` gains exactly
   one new optional parameter, `on_phase: Callable[[str | None], None] =
   lambda phase: None` — a named-phase signal, never touching ANSI/OSC
   codes directly. That boundary already exists today (`run_consult`
   only knows about an abstract `out` callable for streamed text); this
   preserves it. Default no-op means none of `run_consult`'s existing
   callers or tests need to change.
2. **Two independent stop triggers are required for the thinking phase**,
   not one. `ChatClient.chat()`'s `on_text` callback (confirmed by
   reading `llm.py` directly) only fires when a stream chunk carries
   `delta.content` — it never fires at all for a turn that's pure
   tool-calls with no preceding text. So the indicator must stop on
   *either* the first `on_text` call *or* right after `client.chat()`
   returns, whichever comes first — stopping twice is harmless.
3. **The busy-star and this indicator are not a coexistence question** —
   the busy-star is removed outright (§6), not kept alongside.
4. **The hard gate is `color_enabled`, not a separate check.** Whenever
   `TerminalSink.color_enabled` is `False` (non-tty output, or
   `NO_COLOR` set — the same isatty+env check already gating every other
   color decision in the codebase), the indicator never fires at all —
   never emit raw escape bytes into piped/redirected output.

## 4. Mechanism

### 4.1 `ActivityIndicator` (new module, `src/sasha/activity_indicator.py`)

```python
class ActivityIndicator:
    def __init__(self, colors: dict[str, str], interval_s: float,
                 enabled: bool) -> None: ...
    def start(self, phase: str) -> None: ...   # phase: "thinking" | "tool"
    def stop(self) -> None: ...                 # idempotent
```
Internally: the same background-thread + `threading.Event` pattern
already validated by hand — toggle `\e[?25l`/`\e[?25h` at `interval_s`,
set the phase's color via OSC 12 once at `start()`, always restore
cursor-visible + reset color (`\e]112\e07`) in `stop()` and on thread
exit, matching the cleanup discipline already proven in the scratch
scripts (including the `KeyboardInterrupt` case). A no-op (`enabled=False`
or `colors` empty) skips the thread entirely.

### 4.2 `agent.py`'s `run_consult` — exact hook points

At the top of each turn in the `while True:` loop:
```python
on_phase("thinking")
stopped_on_text = False
def _on_text_wrapper(chunk):
    nonlocal stopped_on_text
    if not stopped_on_text:
        on_phase(None)
        stopped_on_text = True
    out(chunk)
response = client.chat(messages, tools=schema, on_text=_on_text_wrapper)
on_phase(None)   # idempotent -- the real stop point for text-free,
                 # pure-tool-call turns, per key contract #2
```
Per tool call, only around the branch that actually executes the
handler (`yes`/`edit`/auto-approved — never the declined/confirmation-
wait path):
```python
if executed:
    on_phase("tool")
    result = tool.handler(final_arguments, executor)
    on_phase(None)
```

### 4.3 `repl.py`

- Owns one `ActivityIndicator` instance, built from
  `config.activity_indicator_*` fields (exact field names finalized at
  task-spec time) and `self._sink.color_enabled`.
- Passes `indicator.start`/`indicator.stop` (adapted to the `on_phase`
  shape) into `run_consult`.
- Wraps the raw-command path (Task 25.1) directly:
  `indicator.start("tool")` / `self._executor.run(stripped)` /
  `indicator.stop()`.
- `_consult()`'s existing `try/except ChatError/finally` block gets
  `self._activity.stop()` added to the `finally`, as a safety net beyond
  the fine-grained pairs in §4.2 — covers `ChatError` or any unexpected
  exception mid-turn.
- The existing static `"[sasha] thinking…"` text line is unchanged — the
  cursor blink is an additional cue, not a replacement.

### 4.4 Config

New self-installing `[activity_indicator]` section, same pattern as
`[safe_commands]`/`[tool_log]`:
```toml
[activity_indicator]
enabled = true
interval_s = 0.05
thinking_color = "#C084FC"
tool_color = "#FFD700"
```
`enabled` is a dedicated on/off distinct from the hard `color_enabled`
gate (§3.4) — skippable via config even in a real, color-capable
terminal, if Salvador decides he doesn't want it. `interval_s` defaults
to the value he tuned himself by hand (0.05s, "feels like it's extremely
busy at work"). Colors are direct hex (not X11 names) for reliability
across terminals — `#C084FC` (light violet, addresses the "too dark"
feedback on plain `magenta`) and `#FFD700` (bright gold) as brighter
defaults than the prototype's plain `magenta`/`yellow`.

## 5. Interface changes summary

- New module `src/sasha/activity_indicator.py`: `ActivityIndicator`.
- `src/sasha/agent.py`: `run_consult` gains `on_phase` (optional,
  defaults to no-op — no existing caller needs to change).
- `src/sasha/repl.py`: owns an `ActivityIndicator`; wires it into
  `_consult()` and the raw-command path.
- `src/sasha/config.py`: new `Config` fields for `[activity_indicator]`,
  same default-and-fallback pattern as `[tool_log]`.

No changes to `ExecResult`, `ToolResult`, `ChatResponse`, or any other
tool's schema.

## 6. Removing the Phase 19 busy-star mechanism

Confirmed via direct grep before writing this section: the
`_background_tasks` counter is incremented/decremented *only* around the
memory-bootstrap-summary thread (`_spawn_bootstrap_summary`/
`_bootstrap_stage_2`) — `tool_log`'s compaction thread (Task 22.2) never
wired into it either, so the busy-star's real-world scope was even
narrower than "background threads in general."

Removed entirely, not kept alongside the new indicator:
- `prompt.py`: `_busy_star()`, the `"S"` entry in `_POSITIONAL`, `render()`'s
  `busy: bool = False` parameter. `DEFAULT_TEMPLATE` drops `\S ` — the
  star disappears from the prompt rather than staying as inert
  decoration (Salvador's call: a character with zero remaining function
  would just be confusing).
- `repl.py`: `self._background_tasks`, `_begin_background_task()`,
  `_end_background_task()`, and their call sites around the bootstrap
  thread spawn; the `busy=self._background_tasks > 0` argument to
  `prompt_mod.render(...)`.

**Decision:** a pre-existing `config.toml` with a custom `prompt` field
that still contains `\S` will fail `validate_template()` on next start
(unrecognized escape). Accepted — Salvador's call, consistent with the
project's no-backwards-compat-shims default and its early-alpha status.

**Pre-existing contract tests affected** (found via grep now; the
authoritative list gets confirmed by running the full suite in a scratch
worktree at task-spec time — grep alone already proved incomplete once
this session, during Task 25.1):
- `tests/contract/test_task_19_2_busy_star_indicator.py` — deleted
  outright; it tests a mechanism that no longer exists.
- `tests/contract/test_task_19_3_repl_bootstrap_wiring.py` — five
  `assert repl._background_tasks == 0` assertions need removing.
- `tests/contract/test_task_6_2_colorization_prompt.py` — hardcoded
  `"[sasha]✦ ..."` prompt-string assertions need updating to the
  star-free form.
- `tests/contract/test_task_9_1_configurable_prompt.py` — renders and
  asserts on `DEFAULT_TEMPLATE`'s exact content.

## 7. Testing strategy

A real blinking cursor isn't practically assertable in a unit test, so
testing focuses on the observable contract, same style as this session's
other `agent.py`/`repl.py` work:
- `run_consult` calls `on_phase` with `"thinking"`/`None`/`"tool"`/`None`
  at the right points, for both text-bearing and text-free (pure-tool-
  call) turns — verified with a spy in place of a real `on_phase`.
- `repl.py`'s raw-command path calls `indicator.start("tool")`/`stop()`
  around `Executor.run()`.
- `_consult()`'s `finally` block stops the indicator even when
  `ChatError` is raised mid-turn.
- `color_enabled=False` fully suppresses `ActivityIndicator` — no escape
  bytes emitted, thread never starts.
- Regression: the four files in §6 amended/deleted as described; the
  full existing suite stays green otherwise.
