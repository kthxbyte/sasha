# Task 31.1: Reasoning-stage coloring + tool-turn display framing (ISSUE-049)

## Authoritative contract
`docs/superpowers/specs/2026-07-25-issue-049-reasoning-color-and-display-framing-design.md`
is the complete contract: stream a model's `reasoning_content` SSE field
with its own color, and fix a real display bug where tool-turn rounds'
streamed text glues together with no separator and system messages
("ran (auto-approved): ...", the tool_log compaction notice, "stopping: ...")
land unframed in the raw text stream.

## What already exists
- `src/sasha/llm.py`'s `ChatClient.chat()` — currently only reads
  `delta.get("content")` from SSE chunks via a required `on_text` callback.
  **Bind to the existing streaming loop and delta-parsing structure — do
  not restructure it.**
- `src/sasha/agent.py`'s `run_consult()` — the `while True:` tool-turn
  loop, `_consult_llm`'s inner closure, the `current_key`/`previous_key`
  repetition-guard logic, the three raw `out(...)` calls for system
  messages. **Bind to the loop structure and repetition-guard comparison
  logic — only the message-channel and message-content for the three
  system messages changes.**
- `src/sasha/repl.py`'s `TerminalSink` — `write_line`, `write_chunk`,
  `_close_chunk_run`, `ensure_newline`, the five existing `_XXX_COLOR`
  constants (all bold SGR, from Task 28.1). `Repl._on_activity_phase` and
  `Repl._consult`'s `run_consult(...)` call site.
- `src/sasha/config.py`'s `sasha_color` field — added just before this
  task, same mechanism (`prompt.COLOR_TOKENS` vocabulary, validated in
  `_build_config`, resolved in `__main__.py`) this task's `reasoning_color`
  field reuses exactly.
- Four existing immutable contract tests pin the exact literal string
  `"[sasha] stopping: repeated identical action / tool-turn limit reached"`
  as a required substring of the text collected from `run_consult`'s `out`
  parameter, in *both* the genuine-repetition case and the plain
  turn-limit case: `test_task_3_0_agent_hardening.py::test_ac1_unknown_tool_turns_hit_limit`,
  `test_task_2_3_agent.py::test_ac6_identical_command_stops_loop` and
  `::test_ac7_turn_limit_stops_loop`,
  `test_task_16_2_agent_registry.py::test_ac_repetition_guard_fires_across_write_file_calls_ignoring_description`
  and `::test_ac_repetition_guard_fires_on_identical_auto_approved_calls`.
  **Do not reword, reorder, or remove this base sentence in any case —
  only append to it, and only in the genuine-repetition case.**
- One existing immutable contract test pins the auto-approved announcement
  to the `out` channel specifically:
  `test_task_21_1_safe_command_auto_approval.py::test_ac11_dispatch_auto_approves_safe_and_announces`
  asserts `"(auto-approved)" in out` and `"ls -la" in out`.
- One existing immutable contract test pins `check_and_maybe_compact_async`'s
  call *count*, not the identity of the callable passed to it:
  `test_task_22_2_tool_call_log_compaction.py::test_ac10_dispatch_calls_check_and_maybe_compact`
  asserts `len(calls) == 1` only.
- Several existing immutable contract tests define their own minimal fake
  Sink test doubles that implement only `write_line`/`write_chunk`/
  `ensure_newline` — **not** the new `write_reasoning_chunk` method, and
  their `write_line` fakes take one positional argument, not a `color=`
  keyword: `test_task_3_1_repl.py`, `test_task_3_4_repl_streaming.py`,
  `test_task_5_1_drop_127_preamble.py`, `test_task_5_2_thinking_indicator.py`,
  `test_task_17_3_repl_memory.py`, `test_task_26_1_activity_indicator.py`.
  `Repl._consult()`'s wiring must tolerate these (see the fix sketch below —
  this is not optional hardening, it's required for the gate to pass).
- `tests/contract/sse_stub.py`'s `StubServer` — already extended (by Tier 1,
  at spec time, since this file is shared test infrastructure rather than
  a per-task contract test) with an optional `"reasoning"` key on the
  `"text"` and `"tool_call"` script entry kinds, streaming it as
  `reasoning_content` deltas before the existing `"text"`/tool-call
  content. This extension is part of this task's committed base — do not
  modify `tests/contract/` further.

## What's new
- `ChatClient.chat()` gains an `on_reasoning` parameter and a
  `reasoning_content` delta check.
- `run_consult()` gains `on_reasoning`, `on_status`, `on_warning` parameters.
- `TerminalSink` gains `write_reasoning_chunk`, an optional `color` param
  on `write_line`, a `reasoning_color` constructor param, and two new
  module constants (`_REASONING_COLOR`, `_WARNING_COLOR`).
- `Repl` gains a `_phase_needs_newline` instance flag.
- `Config` gains a `reasoning_color` field.

## Fix sketch (authoritative — verified locally in a scratch worktree,
full suite green, 610/610: 587 existing + 23 new)

### `src/sasha/llm.py`

```python
def chat(self, messages: list[dict], tools: list[dict],
         on_text: Callable[[str], None],
         on_reasoning: Callable[[str], None] = lambda chunk: None) -> ChatResponse:
```

Inside the delta-parsing loop, alongside the existing `content` check:

```python
    if "content" in delta and delta["content"]:
        content = delta["content"]
        full_text += content
        on_text(content)

    if "reasoning_content" in delta and delta["reasoning_content"]:
        on_reasoning(delta["reasoning_content"])
```

Not accumulated anywhere, not stored on `ChatResponse` — purely streamed
through for display.

### `src/sasha/agent.py`

Signature:

```python
def run_consult(
    session: Session,
    client: ChatClient,
    executor: Executor,
    confirmer: Confirmer,
    out: Callable[[str], None],
    on_reasoning: Callable[[str], None] = lambda chunk: None,
    on_status: Callable[[str], None] | None = None,
    on_warning: Callable[[str], None] | None = None,
    max_tool_turns: int = 10,
    on_phase: Callable[[str | None], None] = lambda phase: None,
) -> None:
    status = on_status if on_status is not None else out
    warn = on_warning if on_warning is not None else out
    previous_key: tuple | None = None
    tool_turns = 0
```

`_consult_llm`'s inner closures — both stop the "thinking" phase on first
arrival of *either* stream:

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

        def _on_reasoning(chunk):
            nonlocal stopped
            if not stopped:
                on_phase(None)
                stopped = True
            on_reasoning(chunk)

        response = client.chat(messages, tools=tools, on_text=_on_text,
                                on_reasoning=_on_reasoning)
        on_phase(None)
        return response
```

Per round, alongside `current_key: tuple | None = None` (top of the
`while True:` body, before the `for call in response.tool_calls:` loop),
add `current_action: tuple | None = None` — same reset-per-round lifetime.
Set both together, inside the existing `if executed:` block:

```python
            if executed:
                current_key = (call.name, tool.repetition_key(final_arguments))
                current_action = (call.name, final_arguments)
```

The three raw `out(...)` calls for system messages become:

```python
                verdict = "auto-approved"
                if tool.announce_when_auto_approved:
                    status(f"[sasha] ran (auto-approved): {final_arguments.get('command', '')}")
```

```python
            tool_log.check_and_maybe_compact_async(status)
```

```python
        if response.tool_calls:
            tool_turns += 1
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
                final_messages = session.build_messages(executor.cwd)
                final_response = _consult_llm(final_messages, [])
                session.add_assistant(final_response)
                return
            if current_key is not None:
                previous_key = current_key
```

### `src/sasha/repl.py`

New module constants, alongside the existing five:

```python
_REASONING_COLOR = "\x1b[2;37m"  # dim white — thinking/reasoning-stage text
_WARNING_COLOR = "\x1b[1;5;31m"  # bold, blink, red — repetition/turn-limit stop only
```

`TerminalSink`:

```python
class TerminalSink:
    def __init__(self, sasha_color: str = _SASHA_COLOR,
                 reasoning_color: str = _REASONING_COLOR) -> None:
        self.color_enabled = (sys.stdout.isatty()
                              and "NO_COLOR" not in os.environ)
        self._sasha_color = sasha_color
        self._reasoning_color = reasoning_color
        self._ends_in_newline = True
        self._chunk_run_kind = None   # None | "llm" | "reasoning" — was a bool

    def _wrap(self, text: str, color: str) -> str:
        if not self.color_enabled:
            return text
        return f"{color}{text}{_RESET}"

    def _close_chunk_run(self) -> None:
        if self._chunk_run_kind is not None:
            sys.stdout.write(_RESET)
            self._chunk_run_kind = None

    def write_line(self, text: str, color: str | None = None) -> None:
        self._close_chunk_run()
        if not self._ends_in_newline:
            sys.stdout.write("\n")
        print(self._wrap(text, color if color is not None else self._sasha_color))
        self._ends_in_newline = True

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

    def ensure_newline(self) -> None:
        self._close_chunk_run()
        if not self._ends_in_newline:
            sys.stdout.write("\n")
            self._ends_in_newline = True
        sys.stdout.flush()
```

`Repl.__init__` adds one instance attribute, alongside the existing ones:

```python
        self._phase_needs_newline = False
```

`Repl._on_activity_phase` and `Repl._consult` (note the flag reset at the
top of `_consult`, and the `getattr`/`try-except` guards around the new
callbacks — **both required**, not optional hardening; see "What already
exists" above for exactly which existing tests need them):

```python
    def _on_activity_phase(self, phase):
        if phase is None:
            self._activity.stop()
        else:
            if self._phase_needs_newline:
                self._sink.ensure_newline()
            self._phase_needs_newline = True
            self._activity.start(phase)

    def _consult(self, auto_prompt=None):
        if auto_prompt:
            self._session.add_user(auto_prompt)
        self._phase_needs_newline = False
        self._sink.write_line("[sasha] thinking…")
        try:
            client = self._client_factory(self._current_profile)
            on_reasoning = getattr(self._sink, "write_reasoning_chunk",
                                   self._sink.write_chunk)

            def _on_warning(text):
                try:
                    self._sink.write_line(text, color=_WARNING_COLOR)
                except TypeError:
                    self._sink.write_line(text)

            run_consult(self._session, client, self._executor,
                        self._confirmer, self._sink.write_chunk,
                        on_reasoning=on_reasoning,
                        on_status=self._sink.write_line,
                        on_warning=_on_warning,
                        max_tool_turns=self._config.limits.max_tool_turns,
                        on_phase=self._on_activity_phase)
        except ChatError as e:
            ...  # unchanged
        finally:
            ...  # unchanged
```

### `src/sasha/config.py`

```python
@dataclasses.dataclass(frozen=True)
class Config:
    ...
    sasha_color: str = "bold-cyan"
    reasoning_color: str = "dim-white"
    ...
```

In `_build_config`, alongside the existing `sasha_color` validation block:

```python
    reasoning_color = raw.get("reasoning_color", "dim-white")
    if not isinstance(reasoning_color, str) or reasoning_color not in prompt.COLOR_TOKENS:
        valid = ", ".join(sorted(prompt.COLOR_TOKENS))
        raise ConfigError(
            f"invalid reasoning_color '{reasoning_color}' — valid tokens: {valid}")
```

And in the final `Config(...)` construction:

```python
        sasha_color=sasha_color,
        reasoning_color=reasoning_color,
```

### `src/sasha/__main__.py`

```python
    sink = TerminalSink(sasha_color=prompt_mod.COLOR_TOKENS[config.sasha_color],
                        reasoning_color=prompt_mod.COLOR_TOKENS[config.reasoning_color])
```

## Constraints
- Do not touch `tests/contract/` — the new contract test file and the
  `sse_stub.py` extension are already committed as part of this spec.
- Do not reword, reorder, or remove the base sentence
  `"[sasha] stopping: repeated identical action / tool-turn limit reached"`
  in any code path — only append to it, and only in the genuine-repetition
  case (`current_key is not None and current_key == previous_key`).
- Do not change `check_and_maybe_compact_async`'s own signature or
  behavior — only what callable `agent.py` passes to it.
- Do not add inline `<think>...</think>` tag parsing — out of scope, see
  the design doc §2.
- Do not make `_WARNING_COLOR` configurable — fixed bold-red-blink.
- The `getattr(self._sink, "write_reasoning_chunk", self._sink.write_chunk)`
  fallback and the `try/except TypeError` fallback around `write_line`'s
  `color=` kwarg in `_on_warning` are both required — removing either
  breaks existing immutable tests (see "What already exists").
- **Executor routing (per CLAUDE.md's standing rule): this is a surgical,
  precisely-specified edit to code that already works. Dispatch to the
  default model (deepseek-v4-pro) — do not launch under a local-model
  `SASHA_DISPATCH_MODEL` override.**

## Acceptance criteria
(encoded 1:1 in `tests/contract/test_task_31_1_reasoning_color_and_display_framing.py`)
1. `test_ac1a_on_reasoning_and_on_text_receive_separate_streams` /
   `test_ac1b_on_reasoning_defaults_to_noop_for_backward_compat` —
   `ChatClient.chat()` streams `reasoning_content` and `content` through
   separate callbacks; `ChatResponse.text` contains only `content`;
   omitting `on_reasoning` doesn't raise.
2. `test_ac2a`–`test_ac2f` — `TerminalSink.write_reasoning_chunk` uses the
   reasoning color; same-kind runs don't reopen color; switching kind
   (either direction) resets + inserts a newline only if not already at
   line start; no color codes at all when `color_enabled` is `False`.
3. `test_ac3a`–`test_ac3d` — `write_line`'s default-color behavior is
   unchanged; an explicit `color=` param is honored; `_REASONING_COLOR`
   and `_WARNING_COLOR` have their exact specified values.
4. `test_ac4_two_tool_turn_rounds_are_newline_separated` — a real `Repl`
   consult with two tool-turn rounds (each producing visible text) shows
   an `ensure_newline()` call interposed between them.
5. `test_ac5a`–`test_ac5c` — `on_status`/`on_warning` fall back to `out`
   when not supplied (existing behavior, unchanged); are used instead of
   `out` when supplied; the compaction notice is routed to whichever
   callable `status` resolves to.
6. `test_ac6a`–`test_ac6c` — the stopping message gains a
   `— name(args)` suffix only when the stop is a genuine repetition,
   truncated at 200 chars + `…`; the turn-limit-only case is unchanged
   (byte-for-byte the base sentence).
7. `test_ac_run_consult_threads_on_reasoning_and_stops_phase_on_arrival` —
   `run_consult`'s `on_reasoning` parameter actually receives streamed
   reasoning text, and the "thinking" phase stops on its first arrival
   (not only on first `content`).
8. `test_ac_reasoning_color_default` / `_invalid_raises` /
   `_custom_value_accepted` — `Config.reasoning_color` defaults to
   `"dim-white"`, is validated against `prompt.COLOR_TOKENS`, and accepts
   a valid override.
9. The full existing suite is green: 610/610 —
   `bash scripts/run-tests.sh`.

## Standing instructions
- Your goal: make the contract tests in
  `tests/contract/test_task_31_1_reasoning_color_and_display_framing.py`
  pass, and keep the full suite green (610/610):
  `bash scripts/run-tests.sh`.
- NEVER modify files under `tests/contract/` — the gate rejects the commit
  if you do.
- Deliver exactly ONE new commit on top of the HEAD you started from;
  never amend, rebase, or reset; leave the working tree clean.
- Commit message per AGENTS.md, with trailers: `Done: Task 31.1 — <one
  line>`, `Fixed: ISSUE-049`,
  `Next: Task 31.2 — Phase 31 review (Tier 1, with Salvador)`, and the
  Co-Authored-By signature line from your dispatch prompt.
