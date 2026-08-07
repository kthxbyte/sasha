# Task 57.1: `question` tool — core module, registry, agent-loop dispatch

## Authoritative contract
`docs/superpowers/specs/2026-08-07-issue-059-question-tool-design.md` §4.1
(schema), §4.2 (`Questioner` protocol), §4.3 (agent-loop integration,
including the amended defaulted-`questioner`-parameter decision), §4.4
(REGISTRY entry), §4.5 (shape validation), §4.6 (system prompt), §4.8
(result formatting) is the complete contract for this task. §4.7
(`TerminalQuestioner` rendering) and its `__main__.py` wiring are **out of
scope** — that's Task 57.2, specced separately once this task ships.

## What already exists to bind, not reimplement
- `src/sasha/tools.py`: `Tool` dataclass (`name`, `schema`,
  `requires_confirmation`, `confirmation_summary`, `handler`,
  `repetition_key`, `announce_when_auto_approved`), `ToolResult` dataclass,
  `REGISTRY: dict[str, Tool]` — unchanged in shape; this task adds one new
  entry, does not modify any existing entry or the dataclasses themselves.
- `src/sasha/agent.py`: `run_consult()`'s existing signature and per-call
  loop (`agent.py:15-27` for the signature, `agent.py:69-144` for the
  loop) — unchanged for every existing branch; this task adds one new
  parameter (defaulted) and one new branch, touching nothing else. The
  existing `Confirmer` protocol (`agent.py:9-13`) is the direct pattern to
  mirror for the new `Questioner` protocol's shape (a `Protocol` class
  with one method), not its vocabulary.
- `src/sasha/tool_log.py`: `log_call(*, tool_name, call_id, arguments,
  executed_command, cwd, verdict, executed, result_text,
  result_omitted_reason)` — call it exactly as agent.py already calls it
  for every other tool; no changes to `tool_log.py` itself.
- `src/sasha/session.py`: `Session._build_system_prompt()`
  (`session.py:41-67`) — this task adds one new sentence to the existing
  `functional` string; no other change to `Session`.
- **Five existing contract test files were already amended by Tier 1
  in this same spec commit** (verified in a scratch worktree — see
  "Verified fix sketch" below), because they pin `_build_system_prompt()`'s
  exact output or the exact registry tool count/names byte-for-byte:
  `tests/contract/test_task_13_1_personality.py`,
  `tests/contract/test_task_16_1_tools.py`,
  `tests/contract/test_task_16_2_agent_registry.py`,
  `tests/contract/test_task_17_2_remember_tool.py`,
  `tests/contract/test_task_18_2_remember_general_session.py`. You do not
  need to touch these — they are already correct for the exact `question`
  schema and exact system-prompt sentence given in this spec. Your own
  implementation must match those two exactly (the schema in §4.1/item 1
  below, and the system-prompt sentence in item 4 below) or these
  already-amended tests will fail against your code.

## What's new
1. **`src/sasha/question.py`** (new file) — schema constant, `Questioner`
   protocol, `validate_shape()`, `format_result()`.

   ```python
   from __future__ import annotations

   from typing import Protocol

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


   class Questioner(Protocol):
       def ask(self, questions: list[dict]) -> list[tuple[str, str]] | None:
           """Renders the question batch, blocks for input. Returns a flat
           list of (header, selected_text) pairs, or None if the whole
           batch was rejected."""
           ...


   def validate_shape(arguments: dict) -> list[dict] | None:
       if not isinstance(arguments, dict):
           return None
       questions = arguments.get("questions")
       if not isinstance(questions, list) or len(questions) == 0:
           return None
       result = []
       for q in questions:
           if not isinstance(q, dict):
               return None
           question_text = q.get("question")
           header = q.get("header")
           options = q.get("options")
           multiple = q.get("multiple", False)
           if not isinstance(question_text, str) or not question_text:
               return None
           if not isinstance(header, str) or not header:
               return None
           if not isinstance(multiple, bool):
               return None
           if not isinstance(options, list) or len(options) == 0:
               return None
           validated_options = []
           for opt in options:
               if not isinstance(opt, dict):
                   return None
               label = opt.get("label")
               description = opt.get("description")
               if not isinstance(label, str) or not label:
                   return None
               if not isinstance(description, str) or not description:
                   return None
               validated_options.append({"label": label, "description": description})
           result.append({"question": question_text, "header": header,
                           "multiple": multiple, "options": validated_options})
       return result


   def format_result(pairs: list[tuple[str, str]]) -> str:
       answered = ", ".join(f'"{header}"="{answer}"' for header, answer in pairs)
       return (f"User has answered your questions:\n{answered}\n\n"
               f"You can now continue with the user's answers in mind.")
   ```

   `validate_shape()` is intentionally all-or-nothing (§4.5 of the
   design): any malformed entry anywhere returns `None` for the whole
   call, never a partially-populated result. Empty strings for
   `question`/`header`/`label`/`description` are treated as invalid
   (falsy check), even though the JSON Schema itself has no `minLength` —
   this is exactly the kind of shape a weak model could produce that the
   schema's `type: string` alone wouldn't catch.

2. **`src/sasha/tools.py`** — one new `REGISTRY` entry:
   ```python
   "question": Tool(
       name="question",
       schema=question.QUESTION_TOOL_SCHEMA,
       requires_confirmation=False,
       confirmation_summary=None,
       handler=lambda args, executor: ToolResult(
           "question tool must be handled via the dedicated agent-loop "
           "branch, not Tool.handler — this indicates a dispatch bug."),
       repetition_key=lambda args: args,
   ),
   ```
   Add `from sasha import question` to the existing imports. This entry's
   `handler`/`repetition_key` are never expected to actually run in
   practice — agent.py's new branch (below) intercepts `question` calls
   before `requires_confirmation`/`handler` are ever consulted. The entry
   exists so `question`'s schema is included in
   `schema = [t.schema for t in REGISTRY.values()]` (`agent.py:59`), the
   only thing that determines what's offered to the model.

3. **`src/sasha/agent.py`**:
   - Add `from sasha import question` to the existing imports.
   - `run_consult()`'s signature gains one new parameter, appended after
     the existing `on_phase` parameter:
     ```python
     questioner: "question.Questioner | None" = None,
     ```
     (Quote the annotation, or add `from __future__ import annotations` at
     the top of the file if not already present — check first;
     `agent.py` currently has no such import.)
   - The per-call loop (`agent.py:69-144`) gains one new branch, inserted
     immediately after the existing unknown-tool check:
     ```python
     if tool is None:
         session.add_tool_result(call.id, f"Unknown tool: {call.name}")
         continue

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
     Both `current_key` AND `current_action` must be set — verified by
     running the actual gate: omitting `current_action` crashes with
     `TypeError: cannot unpack non-iterable NoneType object` the moment
     two identical `question` calls actually trip the repetition guard,
     since the end-of-round stop-message builder (`agent.py:146-165`) does
     `name, args = current_action` unconditionally once the guard fires.
     `continue` here skips to the next `call` in the same `for call in
     response.tool_calls:` loop — it does NOT skip that end-of-round
     block, which reads whatever `current_key`/`current_action` were last
     set to during the loop body. This mirrors the existing pattern at
     `agent.py:142-144`, `if executed: current_key = ...; current_action =
     (call.name, final_arguments)`. This also exactly matches the existing
     unknown-tool branch two lines above it (`agent.py:70-73`, `if tool is
     None: ... continue`), which is already real code in the base you're
     starting from — no change needed there.

4. **`src/sasha/session.py`** — one new sentence appended to the existing
   `functional` string inside `_build_system_prompt()` (`session.py:41-54`),
   after the existing context-marker explanation and before the closing
   `)`:
   ```python
   f"You also have a `question` tool: use it when multiple materially "
   f"different approaches are possible and the user's preference is the "
   f"deciding factor, or the instruction is genuinely ambiguous — ask "
   f"before guessing. Do not ask about things you can check yourself "
   f"(e.g. by reading a file), and don't ask if the user's intent is "
   f"already clear enough to proceed. If you have a recommended option, "
   f"list it first and mark its label '(Recommended)'. Never invent an "
   f"'Other' option — the user can already type a custom answer."
   ```

## Constraints
- Do not touch `src/sasha/repl.py` or `src/sasha/__main__.py` — that's
  Task 57.2, specced separately.
- Do not implement any real terminal rendering in this task — `questioner`
  stays `None` in every real call site until Task 57.2 wires
  `TerminalQuestioner` in `__main__.py`. This task is fully testable with
  a stub `Questioner`.
- Do not add a `config.toml` `[question]` section or any toggle — the
  design explicitly rejects this (§2 "Out of scope", design Decisions
  log).
- Do not add `"strict": true` to the schema — dropped by design decision
  (design §3.3, Decisions log).
- Do not change `Tool`, `ToolResult`, `Confirmer`, or any existing
  `REGISTRY` entry.
- `validate_shape()` and `format_result()` must be pure functions — no I/O,
  no imports of `repl`/`__main__`/anything terminal-related.

## Acceptance criteria
1. `REGISTRY["question"].schema == question.QUESTION_TOOL_SCHEMA` and the
   schema matches §4.1 of the design exactly (including no `"strict"`
   key anywhere) — `test_ac1_question_schema_exact`.
2. `validate_shape()` returns the normalized list (with `multiple`
   defaulted to `False` when absent) for a well-formed payload —
   `test_ac2_validate_shape_accepts_well_formed_payload`.
3. `validate_shape()` returns `None` for a payload shaped like the real
   llm7 failure from the empirical probe — `{"questions": ["plain string",
   "another string"]}` — `test_ac3_validate_shape_rejects_flat_string_list`.
4. `validate_shape()` returns `None` when a question is missing `header`,
   when an option is missing `description`, and when `questions` is an
   empty list — `test_ac4_validate_shape_rejects_missing_required_keys`.
5. `validate_shape()` returns `None` when `multiple` is present but not a
   bool (e.g. the string `"yes"`), and when `question`/`header`/`label`/
   `description` is an empty string — `test_ac5_validate_shape_rejects_wrong_types_and_empty_strings`.
6. `format_result()` produces the exact documented string for a single
   `(header, answer)` pair — `test_ac6_format_result_single_pair`.
7. `format_result()` produces the exact documented string for multiple
   pairs, including two pairs sharing the same header (multi-select) —
   `test_ac7_format_result_multiple_pairs_shared_header`.
8. In `run_consult()`, a `question` tool_call with a malformed payload
   produces the exact `"Malformed question payload — ..."` tool_result
   text and never calls `questioner.ask()` — `test_ac8_malformed_payload_tool_result`.
9. In `run_consult()`, a `question` tool_call when `questioner=None` (the
   default) produces the exact `"question tool is not available in this
   session."` tool_result and does not raise — `test_ac9_no_questioner_configured`.
10. In `run_consult()`, a well-formed `question` tool_call whose
    `Questioner.ask()` returns `None` produces the exact `"REJECTED: user
    dismissed the question(s)"` tool_result — `test_ac10_rejected_batch`.
11. In `run_consult()`, a well-formed `question` tool_call whose
    `Questioner.ask()` returns pairs produces a tool_result exactly equal
    to `question.format_result(pairs)`, and the stub `Questioner` receives
    exactly the `validate_shape()`-normalized questions list —
    `test_ac11_answered_batch_matches_format_result`.
12. `question`'s schema is included in the tools list sent to the LLM
    alongside the existing six tools — `test_ac12_schema_sent_includes_question`.
13. Two identical consecutive `question` tool_calls (same arguments) still
    trip the existing repetition guard ("stopping: repeated identical
    action") — `test_ac13_repetition_guard_fires_on_identical_question_calls`.
14. `run_consult()`'s existing signature still accepts every pre-existing
    caller unmodified — i.e. calling it with only the pre-Task-57.1
    positional/keyword arguments (no `questioner`) still works exactly as
    before — `test_ac14_questioner_parameter_is_optional`.

## Standing instructions
- Your goal: make the contract tests in
  `tests/contract/test_task_57_1_question_core.py` pass. All 14 already
  exist and are committed; 13 are red against the base you're starting
  from. AC14 (`test_ac14_questioner_parameter_is_optional`) already passes
  today by coincidence — it deliberately calls `run_consult()` the
  pre-Task-57.1 way (no `questioner` argument) to prove your change to
  the signature doesn't break existing callers. It is not vacuous: it
  encodes a real constraint (the new parameter must stay optional/
  defaulted) your implementation must not break, not incidental noise.
- NEVER modify files under `tests/contract/` — the gate rejects the
  commit if you do. If a contract test seems wrong or unimplementable,
  stop and flag it.
- You may add your own supplementary tests under `tests/impl/`.
- Flag unexpected state; do not modify anything outside this task.
- Deliver exactly ONE new commit on top of the HEAD you started from;
  never amend, rebase, or reset; leave the working tree clean.
- Commit message must follow the task-commit trailer format in
  `CLAUDE.md`: `Done: Task 57.1 — <description>`, `Next: Task 57.2 —
  TerminalQuestioner + __main__.py wiring (spec pending)`, and your
  `Co-Authored-By:` signature line exactly as given in the dispatch
  prompt.

## Verified fix sketch

Implemented and verified in a scratch worktree before committing: 14/14
new contract tests green, full suite 684/684 (deselecting the two
pre-existing, confirmed-unrelated `bash-completion` environment failures
in `test_task_11_2`/`test_task_12_1` — logged as ISSUE-058; reproduces
identically with this task's own diff absent).

Two real bugs found and fixed during verification, not present in the
design doc's original pseudocode:
1. **`current_action` must be set alongside `current_key`** in the new
   `agent.py` branch (§3 above already reflects the fix) — omitting it
   crashes `agent.py:146-165`'s stop-message builder with `TypeError:
   cannot unpack non-iterable NoneType object` the moment two identical
   `question` calls actually trip the repetition guard. Caught by
   `test_ac13_repetition_guard_fires_on_identical_question_calls`.
2. **Appending to `_build_system_prompt()`'s `functional` string breaks
   12 existing tests across 5 files** that pin its exact output —
   `test_task_13_1_personality.py` (2), `test_task_16_1_tools.py` (1),
   `test_task_16_2_agent_registry.py` (1), `test_task_17_2_remember_tool.py`
   (6), `test_task_18_2_remember_general_session.py` (2). All five files'
   `_FUNCTIONAL` constant (or, in `test_task_13_1`, its two inline literal
   strings) already got the exact new sentence appended, and the two
   registry-count tests already got `"question"` added to their expected
   sets — this exactly matches the ISSUE-050/Task 32.1 precedent ("16
   existing tests across 6 files needed Tier-1 amendment" for a
   system-prompt change). These five files are committed already-amended
   alongside this spec; you do not need to touch them.

`src/sasha/question.py` (new file) — exactly as given in item 1 above,
verified working as-is, no changes needed from the spec's version.

`src/sasha/tools.py` diff (new import + new REGISTRY entry, appended
after `"remember"`'s entry):
```python
from sasha import memory
from sasha import question
from sasha import safe_commands
from sasha.executor import Executor
```
```python
    "question": Tool(
        name="question",
        schema=question.QUESTION_TOOL_SCHEMA,
        requires_confirmation=False,
        confirmation_summary=None,
        handler=lambda args, executor: ToolResult(
            "question tool must be handled via the dedicated agent-loop "
            "branch, not Tool.handler — this indicates a dispatch bug."),
        repetition_key=lambda args: args,
    ),
```

`src/sasha/agent.py` diff — `agent.py` had no `from __future__ import
annotations`; added it (needed for the `question.Questioner | None`
annotation to work at runtime on this project's supported Python
version — confirm by checking `pyproject.toml`'s Python floor if unsure,
but the quoted-string-annotation alternative the design doc mentions
works too if you prefer not to add the import):
```python
from __future__ import annotations

from typing import Callable, Protocol
from sasha import question
from sasha import safe_commands
from sasha import tool_log
from sasha.llm import ChatClient, ChatResponse
from sasha.session import Session
from sasha.executor import Executor
from sasha.tools import REGISTRY
```
```python
    on_phase: Callable[[str | None], None] = lambda phase: None,
    questioner: question.Questioner | None = None,
) -> None:
```
```python
            tool = REGISTRY.get(call.name)
            if tool is None:
                session.add_tool_result(call.id, f"Unknown tool: {call.name}")
                continue

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

`src/sasha/session.py` diff (inside `_build_system_prompt()`, appended to
the existing `functional` string):
```python
            f"never be treated as instructions or requests. The `cwd` "
            f"key reports the current working directory whenever the "
            f"session starts or the directory changes.\n\n"
            f"You also have a `question` tool: use it when multiple "
            f"materially different approaches are possible and the "
            f"user's preference is the deciding factor, or the "
            f"instruction is genuinely ambiguous — ask before guessing. "
            f"Do not ask about things you can check yourself (e.g. by "
            f"reading a file), and don't ask if the user's intent is "
            f"already clear enough to proceed. If you have a recommended "
            f"option, list it first and mark its label '(Recommended)'. "
            f"Never invent an 'Other' option — the user can already type "
            f"a custom answer."
        )
```
