# Task 30.1: Drop the synthetic continuation-turn cwd message (ISSUE-047 fix)

## Authoritative contract
`docs/superpowers/specs/2026-07-23-issue-047-drop-synthetic-continuation-turn-design.md`
is the complete contract: `Session.build_messages()` (`src/sasha/session.py`)
currently synthesizes a bare `{"role": "user", "content": "<cwd>"}` message
whenever it's called with no pending real user message in history — this
happens on every tool-turn-loop continuation round in `agent.py`'s
`run_consult()`. This task removes that synthetic message; the messages
array simply ends on the actual last history entry (the tool result) in
that case.

## What already exists
- `src/sasha/session.py`'s `build_messages()` — the trimming loop above the
  final-assembly section (the `while get_current_total_estimate() >
  self.history_token_budget and history_to_trim:` loop) and everything
  before it. **Bind to this — do not change trimming behavior.**
- `src/sasha/agent.py`'s `run_consult()` — calls `session.build_messages(
  executor.cwd)` at the top of its `while True:` loop and again at line
  136 for the forced final answer. **Do not touch `agent.py` at all** —
  this task's fix is entirely inside `build_messages()`; the call sites
  don't need to change or know anything changed.
- The "pending user message" branch of `build_messages()` (currently the
  `if history_to_trim and history_to_trim[-1].get("role") == "user":`
  branch) — **unchanged, bind to it.** cwd/notes continue to attach to a
  real pending user message exactly as today.

## What's new
Nothing new — one function's `else` branch changes, plus the final
message-array assembly line.

## Fix sketch (authoritative)

In `src/sasha/session.py`, `build_messages()` currently reads (approximately):

```python
if history_to_trim and history_to_trim[-1].get("role") == "user":
    last_user_msg = history_to_trim.pop()
    base_text = last_user_msg.get("content", "")
    final_user_msg = {
        "role": "user",
        "content": (base_text + "\n" + context_block) if base_text else context_block
    }
else:
    base_text = ""
    final_user_msg = {"role": "user", "content": context_block}
```
... (trimming loop, unchanged) ...
```python
messages = [self.system_prompt_dict()] + history_to_trim + [final_user_msg]
self.notes = []
return messages
```

Change the `else` branch and the final assembly to:

```python
if history_to_trim and history_to_trim[-1].get("role") == "user":
    last_user_msg = history_to_trim.pop()
    base_text = last_user_msg.get("content", "")
    final_user_msg = {
        "role": "user",
        "content": (base_text + "\n" + context_block) if base_text else context_block
    }
else:
    base_text = ""
    final_user_msg = ({"role": "user", "content": context_block}
                       if self.notes else None)
```
... (trimming loop, unchanged — `base_text` still contributes to the
token estimate exactly as before; it's `""` in the no-pending-user case,
same as today) ...
```python
trailing = [final_user_msg] if final_user_msg is not None else []
messages = [self.system_prompt_dict()] + history_to_trim + trailing
self.notes = []
return messages
```

**Why `else` isn't unconditionally `None`:** `repl.py`'s exit-127
`AUTO_CONSULT` path calls `self._consult()` with no `auto_prompt`, so
`add_user()` is never called for that flow — the failed command's context
reaches the LLM entirely through `self.notes` (populated by
`note_command()`) folded into this exact branch. An unconditional `None`
silently drops that context and regresses
`test_task_5_1_drop_127_preamble.py::test_ac1_no_verbose_preamble_sent_to_llm`
— caught via full-suite scratch-worktree verification, not by this task's
own new tests (none of which populate `self.notes`, so they can't
distinguish the two versions of the fix — exactly why AC6 below requires
the full suite, not just the new tests). See the design doc's §4 and
decisions log for the full mechanism.

That's the entire change: one variable becomes conditionally `None`
instead of an unconditional dict in one branch, and the final
list-concatenation accounts for that.

## Pre-existing contract tests — no amendment needed

Verified by reading (not just grepping) every contract test that calls
`build_messages()` more than once or inspects multi-round message shapes:
`test_task_2_2_session.py`, `test_task_14_1_consume_notes.py`,
`test_task_25_1_raw_command_activity.py`, `test_task_2_3_agent.py`. Every
one of them calls `add_user()` immediately before each `build_messages()`
call, so every one of them exercises only the unchanged "pending user
message" branch — none currently assert on `msgs[-1]`'s role/content for a
continuation round (the branch being changed). All pass unchanged.

New contract test file,
`tests/contract/test_task_30_1_no_synthetic_continuation_turn.py`, is
already committed in the same commit as this spec, RED against base.

Verified locally in a scratch worktree together with the `session.py`
change above: full suite green, 587/587 (582 existing + this task's 5 new
tests).

## Constraints
- Modify only `src/sasha/session.py`, and only inside `build_messages()`.
- Do not touch `src/sasha/agent.py` — its call sites need no change.
- Do not touch the trimming loop's logic (only the `else` branch's
  `final_user_msg` assignment and the final list-assembly line change).
- Do not add any handling for cwd changing mid-tool-turn-loop (e.g. via a
  `cd` inside `run_command`) — explicitly out of scope per the design
  doc's decision log; the model simply won't see a cwd change until the
  next genuine user turn, and that's expected, not a bug to guard against.
- Do not touch `tests/contract/` at all — the new contract test file is
  already committed as part of this spec.
- **Executor routing (per CLAUDE.md's standing rule): this is a surgical,
  precisely-specified edit to code that already works. Dispatch to the
  default model (deepseek-v4-pro) — do not launch under a local-model
  `SASHA_DISPATCH_MODEL` override.**

## Acceptance criteria
(encoded 1:1 in `tests/contract/test_task_30_1_no_synthetic_continuation_turn.py`)
1. `test_ac1_no_trailing_message_after_tool_result_with_no_pending_user` —
   after `add_user`+`add_assistant(tool_calls)`+`add_tool_result`,
   `build_messages(cwd)`'s last message has `role == "tool"`, matching the
   tool call's `tool_call_id`.
2. `test_ac2_no_bare_cwd_only_message_anywhere_in_array` — no message in
   the returned array has `content` equal to exactly the bare cwd string.
3. `test_ac3_multiple_continuation_rounds_never_synthesize_a_turn` — two
   consecutive tool-turn rounds (each `build_messages()` call following
   `add_assistant`+`add_tool_result`, no intervening `add_user`) both end
   on the correct tool-result message, never a synthetic user turn.
4. `test_ac4_pending_user_message_still_gets_context_block` — regression
   guard: a genuine pending user message still gets cwd + its own text
   attached exactly as before.
5. `test_ac5_second_round_trip_request_has_no_synthetic_user_turn` — a
   real two-tool-call `run_consult()` round trip (via the `sse_stub`
   harness): the second outbound request's `messages[-1]["role"]` is
   `"tool"`, not a synthetic user message.
6. The full existing suite is green: 587/587 —
   `bash scripts/run-tests.sh`.

## Standing instructions
- Your goal: make the contract tests in
  `tests/contract/test_task_30_1_no_synthetic_continuation_turn.py` pass,
  and keep the full suite green (587/587):
  `bash scripts/run-tests.sh`.
- NEVER modify files under `tests/contract/` — the gate rejects the commit
  if you do.
- Deliver exactly ONE new commit on top of the HEAD you started from;
  never amend, rebase, or reset; leave the working tree clean.
- Commit message per AGENTS.md, with trailers: `Done: Task 30.1 — <one
  line>`, `Fixed: ISSUE-047` (this fixes the confirmed `cwd` cause; the
  full-system-prompt-resent-every-turn cause remains a known, accepted,
  unaddressed characteristic — do not word this trailer as if that part
  is also fixed),
  `Next: Task 30.2 — Phase 30 review (Tier 1, with Salvador)`, and the
  Co-Authored-By signature line from your dispatch prompt.
