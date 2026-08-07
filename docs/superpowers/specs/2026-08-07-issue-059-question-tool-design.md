# ISSUE-059 — `question` tool: LLM-initiated structured clarifying questions

**Status:** Draft for review
**Date:** 2026-08-07

## 1. Mission

Give the LLM a way to pause and ask the user a structured, menu-driven
clarifying question — single- or multi-select, with a free-text fallback —
instead of guessing between materially different approaches or re-parsing
ambiguous free-text answers. Modeled on Salvador's proposal
(`/home/salvador/sasha-test/question_tool.md`, itself derived from
opencode's own `question` tool) and, functionally, the assistant's own
`AskUserQuestion` tool.

## 2. Scope

### In scope (v1):
- A new `question` tool: one or more questions in a single call, each with
  a header, question text, and 1+ options (label + description), optional
  `multiple` (multi-select) flag.
- A dedicated agent-loop interaction path (a new `Questioner` protocol,
  parallel to the existing `Confirmer`), since no existing sasha primitive
  renders a multi-question, multi-select menu.
- Defensive validation of the model's returned arguments against the real
  schema shape — required because real models do not reliably honor it
  (see §3, empirical findings).
- System-prompt guidance on when to use the tool.
- Ships unconditionally (no `config.toml` toggle) — matches `read_file`/
  `list_dir`/`search_files`/`remember`, which also ship with no toggle,
  since this is purely additive with no executor side effect to gate.

### Out of scope / non-goals:
- No `TIMEOUT` error state. Sasha's agent loop is synchronous and blocks
  on a real terminal; there is nothing async to time out against.
- No config.toml `[question].enabled` toggle (see above).
- No partial-batch answer state. A `question` call is answered in full or
  rejected in full — never "3 of 4 questions answered."
- No fix to ISSUE-060 (the `local` profile's broken tool-call streaming)
  or ISSUE-061 (missing `KeyboardInterrupt`/`EOFError` handling around the
  existing confirmation gate) — both found while designing this feature,
  both pre-existing and unrelated, both logged and deferred separately.
- No retroactive change to `TerminalConfirmer`'s own empty-input semantics
  — `question` defines its own (different) empty-input contract locally.

## 3. Key contracts & assumptions

1. **`question` bypasses the normal tool-dispatch machinery.** Unlike
   every other `REGISTRY` tool, it has no executor side effect to gate, so
   it skips `requires_confirmation`/`Tool.handler` entirely via a dedicated
   branch in `agent.py`'s per-call loop — see §4.3.
2. **Shape adherence is not guaranteed and must be defended against.**
   Empirical probe (2026-08-07, disposable script, driven through the real
   `ChatClient.chat()` streaming path — not one-shot completions — against
   `llm7`/gpt-oss:20b, `deepseek`/deepseek-v4-pro, and the local Ollama
   profile; `qwen` excluded, out of API credits, not a real signal):
   - The schema itself (`strict: true`, nested `additionalProperties:
     false`) was accepted at the HTTP/parse level everywhere — no
     provider rejected the request outright.
   - Shape adherence varied sharply by model. `deepseek` produced a fully
     correct nested `{question, header, multiple, options[{label,
     description}]}` payload, reassembled correctly through the real
     streaming `tool_call_fragments`/`args_str` join in `llm.py` (the
     exact mechanism `3d13b12` patched for Qwen), on a forced-use prompt.
     `llm7` (gpt-oss:20b — Salvador confirmed its tool-calling is
     "surprisingly unremarkable") flattened `questions` to a plain array
     of strings on an easy trigger, and failed outright (stream ended
     before `[DONE]`) on the harder forced-use prompt.
   - Conclusion: the handler must validate the parsed shape before
     treating it as legitimate structured data (§4.5), and a total
     stream failure needs no new handling — it's an existing `ChatError`
     that already propagates cleanly, before any tool_calls exist to
     inspect.
3. **`strict: true` is dropped from the schema**, diverging from the
   original proposal. It provided no observed benefit — `llm7` still
   produced a malformed shape with it present, unlike OpenAI's own API
   where `strict` is enforced server-side via constrained decoding. No
   existing sasha tool schema uses it; adding an unprecedented field with
   no demonstrated effect isn't worth the inconsistency.
4. **Empty input (bare Enter) aborts the whole batch**, not just the
   current question — deliberately different from `TerminalConfirmer`'s
   "empty means no" convention (`repl.py:644`), because there is no
   sensible "no" for a menu selection, only "answer" or "give up." This
   matches the original doc's stated behavior.
5. **Ctrl+C / EOF also abort the whole batch**, caught locally inside the
   new `TerminalQuestioner` rather than relying on anything upstream —
   confirmed via code reading (ISSUE-061) that nothing upstream currently
   catches these gracefully around the existing confirmation gate either.
6. **Trigger calibration is a system-prompt concern, not a schema
   concern.** The probe's `deepseek` run chose to investigate the project
   via `run_command` before asking, on a prompt that was ambiguous but
   still actionable ("go ahead") — a defensible read, not a bug. No
   schema change follows from this; only the system-prompt wording in
   §4.6 addresses it.

## 4. Architecture

### 4.1 Schema (`src/sasha/question.py`, new module)

```python
QUESTION_TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "question",
        "description": (
            "Ask the user one or more questions to gather preferences, "
            "clarify ambiguity, or make an implementation decision. Use "
            "only when multiple materially different approaches are "
            "possible and the user's preference is the deciding factor."),
        "parameters": {
            "type": "object",
            "properties": {
                "questions": {
                    "type": "array",
                    "minItems": 1,
                    "items": {
                        "type": "object",
                        "properties": {
                            "question": {"type": "string"},
                            "header": {"type": "string"},
                            "multiple": {"type": "boolean", "default": False},
                            "options": {
                                "type": "array",
                                "minItems": 1,
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "label": {"type": "string"},
                                        "description": {"type": "string"},
                                    },
                                    "required": ["label", "description"],
                                    "additionalProperties": False,
                                },
                            },
                        },
                        "required": ["question", "header", "options"],
                        "additionalProperties": False,
                    },
                }
            },
            "required": ["questions"],
            "additionalProperties": False,
        },
    },
}
```

No `"strict": true` (§3.3). `additionalProperties: false` at every object
level, matching the project's standard practice since `3d13b12`.

### 4.2 `Questioner` protocol (`src/sasha/question.py`)

```python
class Questioner(Protocol):
    def ask(self, questions: list[dict]) -> list[tuple[str, str]] | None:
        """Renders the question batch, blocks for input. Returns a flat
        list of (header, selected_text) pairs — one entry per selected
        option; multi-select produces multiple entries sharing a header.
        Returns None if the whole batch was rejected (empty input on any
        question, or Ctrl+C/EOF at any point) — no partial-answer state."""
        ...
```

Parallel to the existing `Confirmer` protocol (`agent.py:9-13`) — same
shape of concern (a blocking, renderer-owned interaction point), different
vocabulary because a menu selection isn't a yes/no/edit/reply.

### 4.3 `agent.py` integration

`run_consult()` gains a new `questioner: Questioner | None = None` keyword
parameter, alongside the existing `confirmer` — **defaulted**, matching
every other parameter added to `run_consult()` since its original Task 2.3
signature (`on_reasoning`, `on_status`, `on_warning`, `on_async_status`,
`on_phase`, `max_tool_turns` — `agent.py:21-26` — all have defaults).
This is a deliberate choice to avoid touching any of the ~9 existing
contract test files whose own `_consult()` helpers call `run_consult()`
directly (`test_task_2_3_agent.py`, `test_task_3_0_agent_hardening.py`,
`test_task_16_2_agent_registry.py`, `test_task_21_1_*`, `test_task_22_1_*`,
`test_task_22_2_*`, `test_task_26_1_*`, `test_task_30_1_*`,
`test_task_31_1_*`) — none of them exercise `question` tool calls, so none
of them need to change. Only Task 57.1's own new contract test file passes
a real (stubbed) `Questioner`.

The per-call loop (`agent.py:69-144`) gains one new branch, inserted
immediately after the existing unknown-tool check (`agent.py:70-73`),
before `requires_confirmation`/`tool.handler` are ever consulted:

```python
if call.name == "question":
    questions = question.validate_shape(call.arguments)
    if questions is None:
        result_str = ("Malformed question payload — each entry in "
                       "`questions` must be an object with question/"
                       "header/options, not a plain string. Try again.")
        verdict = "malformed"
    elif questioner is None:
        result_str = "question tool is not available in this session."
        verdict = "unavailable"
    else:
        pairs = questioner.ask(questions)
        if pairs is None:
            result_str = "REJECTED: user dismissed the question(s)"
            verdict = "rejected"
        else:
            result_str = question.format_result(pairs)
            verdict = "answered"
    session.add_tool_result(call.id, result_str)
    tool_log.log_call(
        tool_name="question", call_id=call.id, arguments=call.arguments,
        executed_command=None, cwd=executor.cwd, verdict=verdict,
        executed=True, result_text=result_str, result_omitted_reason=None)
    current_key = ("question", call.arguments)
    current_action = ("question", call.arguments)
    continue
```

`current_action` must be set alongside `current_key` — found via worktree
verification while transitioning to planning: the end-of-round
repetition-guard block
(`agent.py:146-165`) unpacks `current_action` when building its stop
message (`name, args = current_action`), and it stays `None` if this
branch only sets `current_key`, crashing with `TypeError: cannot unpack
non-iterable NoneType object` the moment two identical `question` calls
actually trip the guard. Mirrors the generic per-tool path's own `if
executed: current_key = ...; current_action = (call.name,
final_arguments)` (`agent.py:142-144`).

The `questioner is None` branch exists purely so a misconfigured or
future non-terminal caller of `run_consult()` degrades to a clear,
recoverable tool_result instead of an `AttributeError` — production
`__main__.py` always passes a real `TerminalQuestioner`, so this branch
is not expected to fire in practice.

This bypasses `requires_confirmation`/`Tool.handler` entirely (§3.1) but
still writes through `tool_log.log_call` and feeds `current_key` into the
existing repetition guard (`agent.py:146-165`) exactly like every other
tool — a `question` call repeated identically two rounds in a row still
trips the existing "stopping: repeated identical action" guard, no new
mechanism needed.

### 4.4 `tools.py` REGISTRY entry

`question` still gets a `REGISTRY` entry — this is what gets its schema
included in `schema = [t.schema for t in REGISTRY.values()]`
(`agent.py:59`), the only thing that determines what's offered to the
model. Its `handler` is a stub returning an error `ToolResult` if ever
invoked directly (it shouldn't be — §4.3's branch intercepts first); this
documents the contract rather than representing a real code path.
`requires_confirmation=False`, `confirmation_summary=None`, matching the
no-side-effect tools (`read_file`/`list_dir`/`search_files`).

### 4.5 Shape validation (`src/sasha/question.py`)

```python
def validate_shape(arguments: dict) -> list[dict] | None:
    """All-or-nothing: every entry in questions must be a dict with
    question/header/options correctly typed, every option a dict with
    label/description. Any violation anywhere -> None. Directly encodes
    the real llm7 failure mode (a flat list of plain strings) found in
    the 2026-08-07 probe."""
```

Rejecting the whole call on any malformed entry (rather than rendering the
valid ones and dropping the rest) mirrors the existing "Unknown tool: X"
precedent (`agent.py:71-72`) — a full, clear failure the model can recover
from is simpler and more honest than silent partial recovery.

### 4.6 System prompt (`session.py`)

One new sentence added to `_build_system_prompt()`'s `functional` block
(`session.py:41-54`), alongside the existing confirmation-gate/context-marker
explanation — core mechanics, not project-specific, so it belongs there
rather than in a separate `parts` block:

> "You also have a `question` tool: use it when multiple materially
> different approaches are possible and the user's preference is the
> deciding factor, or the instruction is genuinely ambiguous — ask before
> guessing. Do not ask about things you can check yourself (e.g. by
> reading a file), and don't ask if the user's intent is already clear
> enough to proceed. If you have a recommended option, list it first and
> mark its label '(Recommended)'. Never invent an 'Other' option — the
> user can already type a custom answer."

### 4.7 `TerminalQuestioner` (`src/sasha/repl.py`)

Declared next to `TerminalConfirmer` (`repl.py:625`), taking the same two
dependencies (`input_fn`, `sink`) so it inherits paste-join behavior
(`_joined_input`, wired in `__main__.py:33`) and the existing color
palette (`_COMMAND_COLOR`, `_DIM`) for free. Wired up in `__main__.py`
next to the existing `confirmer = TerminalConfirmer(_joined_input, sink)`
(`__main__.py:102`):

```python
questioner = TerminalQuestioner(_joined_input, sink)
```

Rendering (per question, sequentially):

```
Question 1/2: Cache strategy
  What approach should I take for the caching layer?

  [1] In-memory           — Fastest, but lost on restart.
  [2] File-based (Rec)    — Persists across runs with low setup cost.
  [3] Redis               — Distributed, configurable TTL.

  [C] Type your own answer

  Your choice [1-3, C]: _
```

Input handling:
- **Bare Enter** on any question → abort whole batch, `ask()` returns
  `None` (§3.4).
- **Ctrl+C / EOF** at any point → same whole-batch abort, caught locally
  (§3.5, ISSUE-061).
- **Out-of-range or unparseable input** → reprompt the same question, no
  attempt limit — unlike the y/n/e gate, there's no meaningful "reply"
  fallback for a menu; the batch always ends in a real answer or an
  explicit abort.
- **`[C]`** → a follow-up free-text prompt, formatted as `"<header>"=
  "<header>: <custom text>"` per the original proposal.
- **Multi-select** (`multiple: true`) → comma-separated numbers (e.g.
  `1,3`); each selected option becomes its own `(header, label)` pair.

### 4.8 Result formatting (`src/sasha/question.py`)

```python
def format_result(pairs: list[tuple[str, str]]) -> str:
    """'User has answered your questions:\n"<header>"="<answer>", ...\n\n
    You can now continue with the user's answers in mind.'"""
```

## 5. Testing strategy

Following the existing `TerminalConfirmer(lambda p: response, sink)` fake-
`input_fn` pattern (`tests/contract/test_task_7_1_confirmation_colors.py`):

1. Exact schema match for the `REGISTRY` entry (mirrors
   `test_ac_run_command_schema_exact`'s style).
2. `validate_shape()`: a valid nested payload passes; the llm7-shaped flat
   string-array payload, missing required keys, and wrong-typed fields all
   return `None`.
3. `format_result()`: exact string output for single-select, multi-select
   (multiple pairs sharing a header), and custom-answer formatting.
4. Agent-loop integration: a fake `Questioner` stub returning canned pairs
   (or `None`) — verifies `session.add_tool_result`'s exact text,
   `tool_log.log_call`'s verdict, and that two identical repeated
   `question` calls still trip the existing repetition guard.
5. `TerminalQuestioner` rendering: scripted fake `input_fn` sequences for
   single-select, multi-select, custom-text, empty-Enter-aborts, invalid-
   number-reprompts, and Ctrl+C/EOF-aborts.

## 6. Decisions log

- **Decision:** Drop `"strict": true` from the schema.
  **Rationale:** Empirically no protection in this multi-provider
  environment — `llm7` still produced a malformed shape with it present.
  Unprecedented in this codebase; no demonstrated benefit to justify the
  inconsistency.
- **Decision:** New `Questioner` protocol + dedicated `agent.py` branch,
  bypassing `requires_confirmation`/`Tool.handler`, rather than extending
  the shared `Tool.handler` signature to carry I/O access.
  **Rationale:** `question` has no executor side effect to gate, and
  threading a renderer reference through all 5 existing handlers (and
  their contract tests) for a capability only one tool needs would be a
  much larger blast radius for no benefit — matches how `Confirmer`
  itself is already a `run_consult()` parameter, not part of `REGISTRY`.
- **Decision:** All-or-nothing shape validation and all-or-nothing batch
  answering — no partial recovery, no partial-answer state.
  **Rationale:** Both simplify the contract significantly (one blocking
  call, one of two outcomes) and match existing failure-handling style in
  this codebase (full, clear rejection over silent partial success).
- **Decision:** No `config.toml` toggle.
  **Rationale:** Purely additive, no executor side effect, nothing to
  opt out of for safety — matches `read_file`/`list_dir`/`search_files`/
  `remember`, not `directory_recap`/`activity_indicator`/`tool_log`
  (which gate behavior with real trade-offs to opt out of).
- **Decision:** Empty input aborts the whole batch, diverging from
  `TerminalConfirmer`'s "empty means no."
  **Rationale:** There's no sensible "no" for a menu selection stage —
  only "answer" or "give up." Matches the original proposal's stated
  behavior.
- **Decision:** `questioner` is a defaulted (`None`) keyword parameter on
  `run_consult()`, not a required positional one.
  **Rationale:** Every parameter added to `run_consult()` incrementally
  since Task 2.3 has followed this pattern specifically to avoid touching
  every existing contract test file's `_consult()` helper. Discovered
  while transitioning to planning: `run_consult()` already has ~9 existing
  contract test call sites that would otherwise all need updating for a
  parameter none of them use.
- **Decision:** ISSUE-060 (local profile's broken tool-call streaming) and
  ISSUE-061 (missing Ctrl+C/EOF handling around the confirmation gate),
  both found while working this design, are logged separately and not
  fixed here.
  **Rationale:** Both are pre-existing and unrelated to `question`
  specifically — ISSUE-060 affects every tool on that profile, ISSUE-061
  affects the existing confirmation gate. `question` defines its own
  local Ctrl+C/EOF handling rather than depending on or retroactively
  fixing ISSUE-061.
