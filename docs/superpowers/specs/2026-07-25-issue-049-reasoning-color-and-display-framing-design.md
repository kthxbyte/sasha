# Reasoning-stage coloring + tool-turn display framing (ISSUE-049)

**Status:** Draft for review
**Date:** 2026-07-25

## 1. Mission

Sasha's terminal output has two related problems, both found while investigating
a real "[sasha] stopping: repeated identical action / tool-turn limit reached"
incident. First, some local models (confirmed live: `unsloth/GLM-4.7-Flash-REAP-23B-A3B-GGUF:UD-Q4_K_XL`
via Salvador's llama.cpp-compatible server) stream a separate `reasoning_content`
field ahead of their real answer, and Sasha currently has no way to show it —
it's silently dropped. Second, `agent.py`'s tool-turn loop streams every round's
visible text through one raw, unframed callback with no separator between
rounds, and status/warning messages ("ran (auto-approved): ...", the tool_log
compaction notice, "stopping: ...") share that same raw channel instead of
being line-framed — confirmed via a real garbled paste showing three copies of
repeated text glued together with the stopping message spliced mid-line. This
task fixes both: reasoning text gets its own color, and every message gets a
clean line.

## 2. Scope

### In scope:
- `llm.py`: stream the `reasoning_content` SSE delta field via a new
  `on_reasoning` callback, structurally separate from `content`/`on_text`.
- `agent.py`: thread `on_reasoning` through; add `on_status`/`on_warning`
  callback parameters (both optional, falling back to the existing `out`
  parameter when not supplied) for system messages; call `ensure_newline()`
  at every phase transition to separate tool-turn rounds; enrich the
  "stopping: ..." message with which action repeated, when that's the actual
  cause.
- `repl.py`/`TerminalSink`: generalize the single chunk-run tracking to a
  chunk-run *kind* (llm / reasoning), so switching kinds inserts a line
  break; add `write_reasoning_chunk`; add an optional `color` param to
  `write_line`; add a fixed bold-red-blink `_WARNING_COLOR` for the stopping
  message; wire real `on_status`/`on_warning`/`on_reasoning` callbacks in
  `_consult`.
- `config.py`: new `reasoning_color` key (config.toml, `prompt.COLOR_TOKENS`
  vocabulary, same mechanism as `sasha_color`).

### Out of scope / non-goals:
- Inline `<think>...</think>` tag parsing — this server splits it into a
  structured field already; tag-parsing is a different mechanism for a
  different class of server, not needed here, not designed here.
- Any change to the repetition-guard *logic* itself (when it fires) —
  only the message it prints when it does.
- Making `_WARNING_COLOR` configurable — fixed bold-red-blink, per decision.
- Retrying, deduplicating, or otherwise suppressing an underlying model's
  own repeated tool calls — that's model behavior, not something Sasha's
  display layer can or should mask.

## 3. Key contracts & assumptions

1. **`reasoning_content` arrives as a structurally separate SSE delta field**,
   confirmed via a live `curl` against Salvador's actual local profile
   (`unsloth/GLM-4.7-Flash-REAP-23B-A3B-GGUF:UD-Q4_K_XL`, llama.cpp-compatible
   server): every chunk's `delta` has either `reasoning_content` or `content`,
   never inline tags mixing the two in one field.
2. **Reasoning text is never stored in session history.** It's ephemeral,
   display-only — `ChatResponse.text` and `Session.add_assistant()` are
   unaffected; only `content` deltas accumulate into `full_text`/`ChatResponse.text`.
3. **Four existing contract tests pin the exact literal string**
   `"[sasha] stopping: repeated identical action / tool-turn limit reached"`
   as a required substring of `out`'s collected text, in both the
   genuine-repetition case and the plain turn-limit case:
   `test_task_3_0_agent_hardening.py::test_ac1_unknown_tool_turns_hit_limit`,
   `test_task_2_3_agent.py::test_ac6_identical_command_stops_loop` and
   `::test_ac7_turn_limit_stops_loop`,
   `test_task_16_2_agent_registry.py::test_ac_repetition_guard_fires_across_write_file_calls_ignoring_description`
   and `::test_ac_repetition_guard_fires_on_identical_auto_approved_calls`.
   The base sentence must never be reworded, reordered, or removed — only
   appended to. `test_task_3_3_e2e.py::test_ac6_readme_documents_vocabulary`
   also checks README documents the phrase "stopping: repeated identical
   action" — unaffected by an append-only change.
4. **One existing contract test pins the auto-approved announcement to the
   `out` channel specifically**:
   `test_task_21_1_safe_command_auto_approval.py::test_ac11_dispatch_auto_approves_safe_and_announces`
   asserts `"(auto-approved)" in out` and `"ls -la" in out` where `out` is
   the joined text collected from the *same* `out` parameter `run_consult`
   already takes — not a new parameter. This is why `on_status`/`on_warning`
   must default to `None` and fall back to reusing `out`, rather than
   replacing it outright.
5. **One existing contract test pins `check_and_maybe_compact_async`'s call
   count, not the identity of the callable passed to it**
   (`test_task_22_2_tool_call_log_compaction.py::test_ac10_dispatch_calls_check_and_maybe_compact`
   asserts `len(calls) == 1` only) — safe to pass `status` (which may be
   `on_status` or the `out` fallback) instead of `out` directly.
6. **`TerminalSink.__init__`'s existing `sasha_color` parameter and
   `write_line`'s existing single-argument call sites are untouched** — both
   changes here (a new `reasoning_color` constructor param, an optional
   `color` param on `write_line`) are strictly additive with defaults
   matching current behavior.

## 4. The change

### 4.1 `llm.py` — stream `reasoning_content`

`ChatClient.chat()` gains `on_reasoning: Callable[[str], None] = lambda chunk: None`.
In the existing delta-parsing loop, alongside the `content` check:

```python
if "reasoning_content" in delta and delta["reasoning_content"]:
    on_reasoning(delta["reasoning_content"])
```

Not accumulated anywhere, not stored on `ChatResponse` — purely streamed
through for display, per contract #2.

### 4.2 `agent.py` — plumbing + round separation + message enrichment

`run_consult()`'s signature gains:

```python
def run_consult(
    session, client, executor, confirmer,
    out: Callable[[str], None],
    on_reasoning: Callable[[str], None] = lambda chunk: None,
    on_status: Callable[[str], None] | None = None,
    on_warning: Callable[[str], None] | None = None,
    max_tool_turns: int = 10,
    on_phase: Callable[[str | None], None] = lambda phase: None,
) -> None:
    status = on_status if on_status is not None else out
    warn = on_warning if on_warning is not None else out
    ...
```

`_consult_llm`'s inner `_on_text`/new `_on_reasoning` both stop the
"thinking" phase on first arrival of *either* kind (shared `stopped` flag,
same rule as today, now covering both streams):

```python
def _on_reasoning(chunk):
    nonlocal stopped
    if not stopped:
        on_phase(None)
        stopped = True
    on_reasoning(chunk)

response = client.chat(messages, tools=tools, on_text=_on_text,
                        on_reasoning=_on_reasoning)
```

Replace the three raw `out(...)` calls for system messages:
- Line ~93 (`"[sasha] ran (auto-approved): ..."`) → `status(...)`.
- Line ~124 (`tool_log.check_and_maybe_compact_async(out)`) → `check_and_maybe_compact_async(status)`.
- Line ~133 (`"[sasha] stopping: ..."`) → `warn(...)`, with the enrichment below.

Track the repeated action alongside `current_key` (same loop, no new
iteration). `current_key: tuple | None = None` is already declared once per
round, at the top of the `while True:` body (before the `for call in
response.tool_calls:` loop) — add `current_action: tuple | None = None`
right next to it, same reset-per-round lifetime, then set both together:

```python
if executed:
    current_key = (call.name, tool.repetition_key(final_arguments))
    current_action = (call.name, final_arguments)
```

At the stop-check site:

```python
if ((current_key is not None and current_key == previous_key)
        or tool_turns >= max_tool_turns):
    message = ("[sasha] stopping: repeated identical action / "
               "tool-turn limit reached")
    if current_key is not None and current_key == previous_key:
        name, args = current_action
        desc = f"{name}({args})"
        if len(desc) > 200:
            desc = desc[:200] + "…"
        message += f" — {desc}"
    warn(message)
    session.add_user("Answer in text now; no further commands will be run.")
    ...
```

Round separation: in `repl.py` (not `agent.py` — see 4.3), `on_phase` already
fires at exactly the right moments; no `agent.py` change needed for this part
beyond what's already there.

### 4.3 `repl.py` — `TerminalSink` generalization + wiring

Replace `_chunk_run_open: bool` with `_chunk_run_kind: str | None`
(`None` / `"llm"` / `"reasoning"`). Shared helper:

```python
def _write_colored_chunk(self, text: str, kind: str, color: str) -> None:
    if not text:
        return
    if self.color_enabled:
        if self._chunk_run_kind is not None and self._chunk_run_kind != kind:
            sys.stdout.write(_RESET)
            if not self._ends_in_newline:
                sys.stdout.write("\n")
                self._ends_in_newline = True
            self._chunk_run_kind = None
        if self._chunk_run_kind != kind:
            sys.stdout.write(color)
            self._chunk_run_kind = kind
    sys.stdout.write(text)
    sys.stdout.flush()
    self._ends_in_newline = text.endswith("\n")

def write_chunk(self, text: str) -> None:
    self._write_colored_chunk(text, "llm", _LLM_COLOR)

def write_reasoning_chunk(self, text: str) -> None:
    self._write_colored_chunk(text, "reasoning", self._reasoning_color)
```

`_close_chunk_run` (used by `write_line`/`ensure_newline`) generalizes the
same way — resets `_chunk_run_kind` to `None`, writing `_RESET` first if a
run was open. This is a strict superset of today's behavior: a single
continuous run of one kind never triggers the "kind changed" branch, so it
behaves identically to today's `write_chunk`.

`write_line` gains an optional `color: str | None = None` parameter
(defaults to `self._sasha_color`, unaffected for every existing call site):

```python
def write_line(self, text: str, color: str | None = None) -> None:
    self._close_chunk_run()
    if not self._ends_in_newline:
        sys.stdout.write("\n")
    print(self._wrap(text, color if color is not None else self._sasha_color))
    self._ends_in_newline = True
```

New constant: `_WARNING_COLOR = "\x1b[1;5;31m"` (bold, blink, red) — fixed,
not configurable.

`TerminalSink.__init__` gains `reasoning_color: str = _REASONING_COLOR`
(new constant `_REASONING_COLOR = "\x1b[2;37m"`, i.e. dim white, matching
the default before config resolution), stored as `self._reasoning_color`.

`_on_activity_phase` calls `ensure_newline()` before starting the indicator,
for every non-`None` phase — except the very first phase transition of a
consult, which is always redundant (`_consult()` already wrote a fresh
"[sasha] thinking…" line immediately before it) and, per the decisions log
below, exposes a mismatch in several existing tests' fake sink mocks:

```python
def _on_activity_phase(self, phase):
    if phase is None:
        self._activity.stop()
    else:
        if self._phase_needs_newline:
            self._sink.ensure_newline()
        self._phase_needs_newline = True
        self._activity.start(phase)
```

`_consult()` resets the flag at the top, before calling `run_consult`:

```python
def _consult(self, auto_prompt=None):
    if auto_prompt:
        self._session.add_user(auto_prompt)
    self._phase_needs_newline = False
    self._sink.write_line("[sasha] thinking…")
    ...
```

`_consult`'s `run_consult(...)` call adds, guarding against fake Sink test
doubles that predate `write_reasoning_chunk`/`write_line`'s `color` param
(per the decisions log below):

```python
on_reasoning = getattr(self._sink, "write_reasoning_chunk", self._sink.write_chunk)

def _on_warning(text):
    try:
        self._sink.write_line(text, color=_WARNING_COLOR)
    except TypeError:
        self._sink.write_line(text)

run_consult(self._session, client, self._executor, self._confirmer,
            self._sink.write_chunk,
            on_reasoning=on_reasoning,
            on_status=self._sink.write_line,
            on_warning=_on_warning,
            max_tool_turns=self._config.limits.max_tool_turns,
            on_phase=self._on_activity_phase)
```

### 4.4 `config.py` — `reasoning_color`

Identical pattern to `sasha_color` (§ already shipped, Task from the
`sasha_color` cosmetic change): new `Config.reasoning_color: str = "dim-white"`
field, validated against `prompt.COLOR_TOKENS` in `_build_config`, resolved
in `__main__.py` via `prompt_mod.COLOR_TOKENS[config.reasoning_color]` and
passed to `TerminalSink(..., reasoning_color=...)`.

## 5. Decisions log

- **Decision:** `on_status`/`on_warning` default to `None` and fall back to
  the existing `out` parameter, rather than being required or replacing
  `out` outright. **Rationale:** contract #4 — an existing immutable test
  asserts the auto-approved announcement lands in `out` specifically; a
  fallback preserves that without requiring any contract-test amendment.
- **Decision:** the stopping message's base sentence is never reworded —
  only appended to, and only when the cause is genuinely a repeated action
  (not a bare turn-limit). **Rationale:** contract #3 — four existing tests
  pin the literal sentence in both cases; appending preserves all of them
  while still answering the real request (know which action repeated).
- **Decision:** reasoning text is streamed live but never stored in
  `ChatResponse`/session history. **Rationale:** it's a display-only
  ephemeral trace; sending it back to the model on the next turn isn't
  requested and isn't how these reasoning fields are meant to be used.
- **Decision:** `_WARNING_COLOR` is fixed (bold red blink), not configurable.
  **Rationale:** Salvador's call — a single warning-only style doesn't need
  a config surface; simpler than extending `COLOR_TOKENS` with blink-color
  combinations for one message.
- **Decision:** `ensure_newline()` fires on *every* non-`None` phase
  (`"thinking"` and `"tool"`), not just `"thinking"`. **Rationale:** both are
  phase-transition points where prior output could be left unterminated;
  consistent handling is simpler than special-casing one phase.
- **Decision:** the reasoning/answer color-run-kind mechanism generalizes
  `write_chunk`'s existing single-boolean tracking rather than adding a
  parallel, separate tracking scheme. **Rationale:** keeps one code path
  responsible for "is a colored run open and of what kind," avoiding two
  independently-maintained newline/reset mechanisms that could drift apart.
- **Decision (found during scratch-worktree verification):** `repl.py`'s
  `_consult()` wiring must not access `self._sink.write_reasoning_chunk`
  unconditionally — several existing immutable contract tests
  (`test_task_3_1_repl.py`, `test_task_3_4_repl_streaming.py`,
  `test_task_5_1_drop_127_preamble.py`, `test_task_5_2_thinking_indicator.py`,
  `test_task_17_3_repl_memory.py`, `test_task_26_1_activity_indicator.py`)
  define their own minimal fake Sink classes that only implement
  `write_line`/`write_chunk`/`ensure_newline`, not the new method. Since
  Python evaluates keyword-argument expressions eagerly, a bare
  `self._sink.write_reasoning_chunk` reference crashed all of them with
  `AttributeError` before `run_consult` even started — caught by running
  the *full* suite in a scratch worktree, not by this task's own new
  tests (none of which use an old-style fake sink). **Fix:**
  `on_reasoning = getattr(self._sink, "write_reasoning_chunk",
  self._sink.write_chunk)` — old sinks get reasoning text routed through
  their existing `write_chunk` (no special color, but no crash); the real
  `TerminalSink` always has the method, so production behavior is
  unaffected. The same reasoning applies to `on_warning`'s `color=` kwarg
  on `write_line` — wrapped in a `try/except TypeError` fallback to a
  plain `write_line(text)` call for the same class of old fake sinks.
- **Decision (found during the same verification pass):** `_on_activity_phase`
  must not call `ensure_newline()` on the *first* phase transition of a
  consult. `test_task_3_4_repl_streaming.py::test_ac1_chunks_stream_through`
  failed with a spurious leading `"\n"` before the streamed answer —
  traced to that test's fake `ensure_newline()` mock, which (unlike the
  real `TerminalSink`) doesn't know that a preceding `write_line()` call
  already guarantees a fresh line, since its fake `write_line` doesn't
  store an implicit trailing newline the way real `print()` does. Rather
  than treat this as unfixable, the underlying call really is redundant
  in production too: `_consult()` already writes "[sasha] thinking…" via
  `write_line()` immediately before the first phase transition, so a
  fresh line is already guaranteed there regardless. **Fix:** a per-consult
  `self._phase_needs_newline` flag, reset to `False` at the top of
  `_consult()`, set to `True` after the first phase transition — so
  `ensure_newline()` is skipped exactly once per consult (the genuinely
  redundant case) and still fires for every later phase transition
  (tool phases, later tool-turn rounds), preserving the round-separator
  fix's actual purpose.

## 6. Testing strategy

New contract test file, `tests/contract/test_task_31_1_reasoning_color_and_display_framing.py`, covering:
1. `ChatClient.chat()` fires `on_reasoning` for `reasoning_content` deltas,
   `on_text` for `content` deltas, and `ChatResponse.text` contains only the
   `content` accumulation (reasoning never leaks into it).
2. `TerminalSink.write_reasoning_chunk` uses the configured reasoning color;
   switching from a reasoning run to an `write_chunk` run (or vice versa)
   emits a reset + newline before the new color, mid-stream.
3. `TerminalSink.write_line(text, color=...)` uses the passed color instead
   of `_sasha_color` when given; omitting `color` is unchanged from today.
4. `_on_activity_phase`-equivalent behavior: two consecutive tool-turn
   rounds' streamed text end up separated by a newline (integration test via
   `run_consult` + a fake sink recording raw writes, or a `sse_stub` round
   trip asserting the concatenated output contains an unavoidable break
   between rounds).
5. `on_status`/`on_warning` fall back to `out` when not supplied (re-run
   equivalents of `test_ac11`/`test_ac10`-style scenarios to confirm the
   fallback path specifically, without touching the original immutable
   files).
6. The stopping message gains the `— name(args)` suffix only when
   `current_key == previous_key` fires it, truncated at 200 chars; the
   plain turn-limit-only case is unchanged (byte-for-byte the existing
   `STOP_LINE`).
7. Full existing suite green — all six previously-identified tests
   (`test_task_3_0`, `test_task_2_3` ×2, `test_task_16_2` ×2, `test_task_21_1`,
   `test_task_22_2`) pass unmodified, confirming the fallback/append-only
   design actually holds in practice, not just on paper.
