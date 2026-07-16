# Task 14.1: build_messages() clears notes/last_stderr after use

## Authoritative contract
`issues/ISSUE-031-stale-notes-resurface.md` is the complete design record
for this task — read it first. Summary: `Session.notes`/`last_stderr`
are unconditionally re-included in the context block on every
`build_messages()` call, with nothing ever clearing them — so a note
from a command run several turns ago keeps getting freshly re-appended
to every later consult (auto-triggered or user-initiated `??`) until 10
more commands push it out of the FIFO. This task makes a note/stderr
surface exactly once: the very next context block built after it's
recorded, not indefinitely.

## What already exists
- `src/sasha/session.py` `Session.build_messages(cwd)` (the method this
  task modifies — only its final step, after `context_block` and the
  returned message list are already constructed). `note_command`,
  `add_user`, `add_assistant`, `add_tool_result`, `_estimate_tokens`,
  `system_prompt_dict`, and `_build_system_prompt` are unchanged.
- `Session.notes` (a `list[NoteCommand]`, populated by `note_command`,
  capped at 10 entries FIFO) and `Session.last_stderr` (a `str`,
  overwritten by every `note_command` call) — the two pieces of state
  this task clears after they've been read.
- `src/sasha/agent.py`'s `run_consult` calls `build_messages()` possibly
  more than once within a single consult (its `while True:` tool-calling
  loop, plus once more in the "stopping" branch) — not modified by this
  task; the fix lives entirely inside `build_messages()`, so every
  caller benefits automatically regardless of how many times per
  consult it's called.

## What's new
`build_messages()` gains one new final step: after `context_block` and
the return value are computed (but using their already-computed values —
this does not change what the CURRENT call returns), reset:
```python
self.notes = []
self.last_stderr = ""
```
before `return`. The context block built by *this* call still contains
whatever notes/stderr were present when the method was entered — only
the *next* call (with no intervening `note_command`) sees them cleared.

## Constraints
- Modify `src/sasha/session.py`'s `build_messages` method only — add the
  two reset lines as the last thing before the `return` statement. No
  other method changes, no new methods, no new parameters.
- Do not touch: `scripts/`, `docs/superpowers/`, `CLAUDE.md`, `AGENTS.md`,
  `PLAN.md`, `ISSUE-TRACKING.md`, `README.md`, `.gitignore`,
  `pyproject.toml`, `src/sasha/__init__.py`, `src/sasha/config.py`,
  `src/sasha/mutes.py`, `src/sasha/triggers.py`, `src/sasha/agent.py`,
  `src/sasha/llm.py`, `src/sasha/executor.py`, `src/sasha/repl.py`,
  `src/sasha/prompt.py`, `src/sasha/completion.py`,
  `src/sasha/personality.py`, `tests/contract/`.
- The method's return value for the call that consumes the notes/stderr
  must be byte-identical to today (i.e. this is purely a post-return-
  value side effect, not a change to what gets sent to the LLM on the
  call where the notes were present).
- No new dependencies.
- Greenfield-vs-surgical routing: a two-line, precise addition to one
  existing method in code that already works. Dispatch to the default
  model (deepseek-v4-pro); do not use a `SASHA_DISPATCH_MODEL`
  local-model override.

## Acceptance criteria
(encoded in `tests/contract/test_task_14_1_consume_notes.py`)
1. After a `build_messages()` call that included one or more notes
   (via `note_command`), a second `build_messages()` call with no
   intervening `note_command` produces a context block that does **not**
   contain any of the previously-shown notes' command lines or exit
   codes — `test_ac1_*`.
2. Same for `last_stderr`: a second call's context block does not
   contain the previously-shown stderr text — `test_ac2_*`.
3. The **first** `build_messages()` call after notes/stderr are recorded
   still includes them correctly — byte-for-byte the same behavior as
   today (regression guard, matching `test_task_2_2_session.py`'s
   existing `test_ac2_context_block_in_final_user_message`, which this
   task's contract tests do not duplicate but must not break) —
   `test_ac3_*`.
4. If multiple notes accumulate via several `note_command` calls before
   any `build_messages()` call happens, they are all shown together on
   the first call and all cleared together afterward (no partial
   clearing) — `test_ac4_*`.
5. A new `note_command()` call after a clear correctly repopulates
   `notes`/`last_stderr`, and the next `build_messages()` call shows
   *only* the new note, not a stale mix — `test_ac5_*`.
6. Every existing contract test in `test_task_2_2_session.py`,
   `test_task_2_3_agent.py`, and any other file constructing `Session`
   or calling `build_messages`/`note_command` continues to pass
   unmodified — confirmed by the full suite, not a new test in this
   file.

## Standing instructions
- Your goal: make the full contract suite pass:
  `PYTHONPATH=src python3 -m pytest tests/contract/`.
- NEVER modify files under `tests/contract/` — the gate rejects the
  commit if you do. No existing contract test should need to change for
  this task (verified at spec time: neither of `test_task_2_2_session.py`'s
  `build_messages()`-calling tests calls it twice with notes present in
  between).
- Deliver exactly ONE new commit on top of the HEAD you started from;
  never amend, rebase, or reset; leave the working tree clean.
- Commit message per AGENTS.md, with trailers:
  `Done: Task 14.1 — <one line>`, `Fixed: ISSUE-031`,
  `Next: Task 14.2 — Phase 14 review (Tier 1, with Salvador)`, and the
  Co-Authored-By signature line from your dispatch prompt.
