# Stop synthesizing a bare cwd-only user turn on tool-turn continuations (ISSUE-047 fix)

**Status:** Draft for review
**Date:** 2026-07-23

## 1. Mission

Fix the confirmed root cause behind a real repeated-tool-call incident:
`Session.build_messages()` synthesizes a brand-new, bare `{"role": "user",
"content": "<cwd>"}` message on every tool-turn continuation round — even
though nothing new happened — because `agent.py`'s `run_consult()` calls
`build_messages(cwd)` fresh at the top of every loop iteration, not just
once per genuine new user input. This produces a non-standard conversation
shape (a synthetic user turn interposed between a tool result and the next
assistant turn) that a real captured interaction shows a local model
misreading as an implicit nudge to act again, triggering the repetition
guard. This task removes that synthetic turn.

## 2. Scope

### In scope:
- `Session.build_messages()` (`src/sasha/session.py`): when there is no
  pending real user message in history (the tool-turn-continuation case),
  stop synthesizing a context-only user message — end the messages array
  on the actual last history entry instead.

### Out of scope / non-goals:
- **cwd changing mid-tool-turn-loop.** `Executor.cwd` can change during a
  `run_command` tool call that runs `cd`. Under this fix, the model won't
  be told about that change until the next genuine user turn — Salvador's
  call, accepted as a rare edge case rather than solved now (tool output
  like `ls` listings and file paths usually makes location obvious
  anyway). Not addressed by this task.
- **The genuine-new-user-turn path.** cwd/notes continue to attach to a
  real pending user message exactly as today — that branch of
  `build_messages()` is unchanged.
- **`agent.py`'s loop structure, the repetition guard, or the
  turn-limit/stop logic.** This task changes what `build_messages()`
  returns, not when or how often it's called, nor how the stop condition
  is detected.
- **The two other ISSUE-047 candidate-cause investigations** (full system
  prompt resent unchanged every turn — confirmed via `llm_debug.jsonl` but
  not being fixed here; that's unavoidable without a caching layer, which
  is a separate, larger piece of work not requested).

## 3. Key contracts & assumptions

1. **Root cause, confirmed by log.** `llm_debug.jsonl` (Task 29.1) captured
   a real session where every tool-turn continuation's outbound request
   ended in a message whose entire content was the literal string
   `/home/salvador` — no notes, no other content, because
   `Session.notes` is already empty by the second round (cleared after
   first use, ISSUE-031's "consume once" pattern). Immediately after one
   such bare turn, the model called `list_dir("/home/salvador")` a second
   time with identical arguments, tripping the repetition guard.
2. **The only structural condition that matters:** whether
   `history_to_trim[-1]`'s role is `"user"` at the time `build_messages()`
   is called. When it is (a real pending user message), cwd/notes attach
   to that real message — this is the common "??  <question>" path and is
   unaffected. When it isn't (this only happens mid tool-turn-loop, after
   `add_assistant()`/`add_tool_result()` were just called), the current
   code fabricates a new user turn purely to carry cwd — this is the
   behavior being removed.
3. **Standard tool-calling shape has no synthetic user turn.** The
   OpenAI-style conversation shape for a multi-round tool call is
   `system, ..., assistant(tool_calls), tool(result), tool(result),
   assistant(next-response)` — no user-role message is required or
   expected between a tool result and the next assistant turn. Ending
   `build_messages()` on the last tool-result entry (when there's no
   pending user message) matches this shape exactly.
4. **No amendment to any existing contract test.** Verified by reading
   (not just grepping) every contract test that calls `build_messages()`
   more than once or inspects multi-round message shapes
   (`test_task_2_2_session.py`, `test_task_14_1_consume_notes.py`,
   `test_task_25_1_raw_command_activity.py`, `test_task_2_3_agent.py`):
   every one of them calls `add_user()` immediately before each
   `build_messages()` call, so none currently exercise the branch being
   changed, and none assert on `msgs[-1]`'s role/content for a
   continuation round.

## 4. The change

`src/sasha/session.py`, `build_messages()`, current shape:
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

...

messages = [self.system_prompt_dict()] + history_to_trim + [final_user_msg]
self.notes = []
return messages
```

New shape: the `if` branch is unchanged; the `else` branch only fabricates
`final_user_msg` when there's still something that needs to reach the
model out-of-band (`self.notes` non-empty), and the final assembly only
appends a trailing message when one was actually built:
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

...

trailing = [final_user_msg] if final_user_msg is not None else []
messages = [self.system_prompt_dict()] + history_to_trim + trailing
self.notes = []
return messages
```
The token-trimming loop above this (which estimates `base_text` alongside
`history_to_trim`) is unaffected — `base_text = ""` already contributes 0
to the estimate in the no-pending-user case, same as before.

**Why the `else` branch isn't simply `None` unconditionally:** `repl.py`'s
exit-127 `AUTO_CONSULT` path (raw command exits 127 → auto-consult without
the user typing `??`) calls `self._consult()` with **no** `auto_prompt`
argument, so `Session.add_user()` is never invoked for that flow — there is
no pending user message in history at all for that turn. The failed
command's context (command line, exit code, stderr) reaches the LLM
*entirely* through `self.notes` (populated by `note_command()` just before
`_consult()` is called), folded into this exact `else` branch. An
unconditional `None` silently drops that context and breaks
`test_task_5_1_drop_127_preamble.py::test_ac1_no_verbose_preamble_sent_to_llm`
(caught during scratch-worktree full-suite verification, not by the new
task's own tests — none of the new tests populate `self.notes`, so they
can't distinguish the two versions of the fix). The corrected condition
keeps this legitimate case working: build a context-only trailing message
only when `self.notes` actually has something to say; otherwise, drop it.

## 5. Decisions log

- **Decision:** Drop the synthetic continuation-turn entirely rather than
  making it conditional on cwd having changed. **Rationale:** simplest
  correct fix matching the standard tool-calling shape; a
  changed-vs-unchanged cwd comparison would need to track "last cwd sent,"
  adding state for a case not currently observed to matter.
- **Decision:** Accept the cwd-mid-loop information gap as out of scope.
  **Rationale:** Salvador's call — rare in practice, tool output usually
  reveals location anyway; can be revisited as a follow-up if it ever
  actually causes confusion.
- **Decision:** No change to `agent.py`. **Rationale:** the loop's
  `build_messages(cwd)` call site doesn't need to know or care whether a
  trailing message gets attached — that's entirely `build_messages()`'s
  internal decision, keeping the fix isolated to one function.
- **Decision:** The `else` branch is conditional on `self.notes`, not an
  unconditional `None`. **Rationale:** discovered during full-suite
  scratch-worktree verification of the fix (not by the task's own new
  tests) that `repl.py`'s exit-127 `AUTO_CONSULT` path relies on this exact
  branch to deliver a failed command's context via `self.notes` — it never
  calls `add_user()`. An unconditional `None` regressed
  `test_task_5_1_drop_127_preamble.py::test_ac1_no_verbose_preamble_sent_to_llm`.
  See §4 for the mechanism. This is why the task's own contract tests
  (which never populate `self.notes`) can't tell the two versions apart —
  the full suite is what caught it, reinforcing why `verify.sh`/the task
  spec require the whole suite green, not just the new tests.

## 6. Testing strategy

New contract tests (in `test_task_2_2_session.py`'s style, likely a new
file `test_task_30_1_no_synthetic_continuation_turn.py`) pin the new
behavior directly:
1. `add_user(...)` → `add_assistant(<tool_calls response>)` →
   `add_tool_result(...)` → `build_messages(cwd)`: the returned messages
   array's last entry has `role == "tool"` (not a synthesized user
   message), and no message in the array is a bare cwd-only user turn.
2. Regression guard: the existing "if" branch (genuine pending user
   message) still attaches cwd/notes exactly as before — re-assert the
   existing `test_ac2_context_block_in_final_user_message`-style behavior
   continues to hold (not a new test — the existing test in
   `test_task_2_2_session.py` already covers this and needs no change,
   just confirmed still green).
3. An `agent.py`-level integration test (via the `sse_stub` harness):
   drive a two-tool-call round trip and assert the *second* request sent
   to the stub server has no trailing bare-cwd user message — the last
   message in that request's body is the tool result from the first
   call's execution.

No amendments to any existing contract test file are needed (see §3,
contract #4).
