# Task 5.1: Drop the redundant AUTO_CONSULT preamble on exit 127

## Authoritative contract
`docs/superpowers/specs/2026-07-14-sasha-design.md` §7.3 (message assembly)
and §9.5 (REPL turn) are the complete contract for the "otherwise" branch
this task touches. This task fixes **ISSUE-017**: on exit 127,
`Repl.run_once` calls `self._consult(prompt)` with a verbose,
instructional prompt the user never asked to see (`/bin/bash: line 1: hi:
command not found` followed by Sasha's own paragraph explaining what just
happened, followed by the LLM's actual answer — three layers for one input).

This is safe to drop entirely, not just shorten: `Session.build_messages`
(`src/sasha/session.py:70-116`) already synthesizes a user turn from the
context block alone when the history's last message isn't a user message —
the context block already carries the failed line and its exit code (via
`note_command`, called just before this branch runs), so the LLM sees
everything it needs without Sasha adding a second, narrated copy of it.

## What already exists
- `src/sasha/repl.py` (Task 3.1/3.4/4.1) — `Repl.run_once`'s AUTO_CONSULT
  branch (the block this task replaces) and `Repl._consult` (unmodified by
  this task — Task 5.2 touches `_consult`, not this task).
- `src/sasha/session.py` `Session.build_messages` — already handles a
  no-new-user-message consult correctly (confirmed by reading it; not
  modified by this task).
- The T2 syntax-error branch a few lines above (`syntax_error = ...`) is a
  **different** case and is explicitly NOT in scope: `check_syntax()` never
  executes the line, so nothing populates `session.notes`/`last_stderr` for
  it — its prompt is the only way the LLM learns what went wrong, and it
  must stay exactly as it is.

## What's new
In `src/sasha/repl.py`, `Repl.run_once`, replace the AUTO_CONSULT branch:

```python
        decision = decide(stripped, result, self._mutes)
        if decision == Decision.AUTO_CONSULT:
            prompt = (f"The line `{stripped}` failed with exit 127 "
                      f"(command not found, or not a command at all). "
                      f"If it was meant as a question or calculation, "
                      f"answer it; if it looks like a typo, say so "
                      f"and give the corrected command.")
            self._consult(prompt)
        elif decision == Decision.OFFER:
```

with:

```python
        decision = decide(stripped, result, self._mutes)
        if decision == Decision.AUTO_CONSULT:
            self._consult()
        elif decision == Decision.OFFER:
```

Nothing else in the file changes. (The empty-offer-accept branch at the top
of `run_once`, and the `?? ` branch, each build their own explicit prompt for
a different reason — the offer-accept one names the specific failed command
being explained on user request; the `??` one is the user's own question
verbatim. Neither is this task's ISSUE-017, and neither changes.)

## Constraints
- Modify `src/sasha/repl.py` only (plus optional supplementary tests under
  `tests/impl/`).
- Do not touch: `scripts/`, `docs/superpowers/`, `CLAUDE.md`, `AGENTS.md`,
  `PLAN.md`, `ISSUE-TRACKING.md`, `README.md`, `.gitignore`,
  `pyproject.toml`, `src/sasha/__init__.py`, `src/sasha/config.py`,
  `src/sasha/mutes.py`, `src/sasha/triggers.py`, `src/sasha/executor.py`,
  `src/sasha/session.py`, `src/sasha/agent.py`, `src/sasha/llm.py`,
  `tests/contract/`.
- Greenfield-vs-surgical routing: this is a surgical one-branch edit to code
  that already works. Dispatch to the default model (deepseek-v4-pro); do
  not use a `SASHA_DISPATCH_MODEL` local-model override.

## Acceptance criteria
(encoded 1:1 in `tests/contract/test_task_5_1_drop_127_preamble.py`)
1. On exit 127, the message sent to the LLM contains the failed line and its
   exit code (via the context block) but none of the old instructional
   preamble text — `test_ac1_no_verbose_preamble_sent_to_llm`.
2. The LLM's actual answer still reaches the user exactly as before (no
   regression to the AUTO_CONSULT trigger or streaming path) —
   `test_ac2_llm_answer_still_reaches_user`.
3. The unrelated T2 syntax-error prompt is untouched — its exact wording
   still reaches the LLM — `test_ac3_syntax_error_prompt_unchanged`.

## Standing instructions
- Your goal: make `tests/contract/test_task_5_1_drop_127_preamble.py` pass
  with `tests/contract/test_task_3_1_repl.py` and
  `tests/contract/test_task_4_1_job_control.py` still green:
  `PYTHONPATH=src python3 -m pytest tests/contract/test_task_3_1_repl.py tests/contract/test_task_4_1_job_control.py tests/contract/test_task_5_1_drop_127_preamble.py`.
- NEVER modify files under `tests/contract/` — the gate rejects the commit if
  you do.
- Deliver exactly ONE new commit on top of the HEAD you started from; never
  amend, rebase, or reset; leave the working tree clean.
- Commit message per AGENTS.md, with trailers:
  `Done: Task 5.1 — <one line>`, `Fixed: ISSUE-017`,
  `Next: Task 5.2 — Thinking indicator during LLM consult`, and the
  Co-Authored-By signature line from your dispatch prompt.
