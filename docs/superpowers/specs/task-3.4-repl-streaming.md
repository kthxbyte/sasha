# Task 3.4: REPL streaming passthrough

## Authoritative contract
`docs/superpowers/specs/2026-07-14-sasha-design.md` §5.4 ("every text delta is
passed to `on_text` as it arrives (the REPL prints it)"). Fixes ISSUE-013.

## What already exists
- `src/sasha/repl.py` (Task 3.1) — the ONLY file this task modifies.
- All contract suites, including the amended
  `tests/contract/test_task_3_1_repl.py` (Tier 1 fixed its `_out_text`
  helper: chunks are now joined WITHOUT separators) and the new
  `tests/contract/test_task_3_4_repl_streaming.py`.

## What's new / the defect
`Repl._consult` currently accumulates every streamed chunk into a local
buffer and emits ONE `out()` call after `run_consult` returns. Consequences:
no live streaming, and confirmation prompts (which go to `out` directly,
during the loop) render BEFORE the assistant text that preceded them.

## Fix sketch (authoritative)
In `Repl._consult`, delete the buffer entirely and pass the REPL's own `out`
straight through:

```python
run_consult(self._session, client, self._executor,
            self._confirmer, self._out,
            max_tool_turns=self._config.limits.max_tool_turns)
```

`buf` / `out_acc` / the final flush all go away. Nothing else changes.

## Constraints
- Modify `src/sasha/repl.py` only (plus optional tests under `tests/impl/`).
- Do not touch: `scripts/`, `docs/superpowers/`, `CLAUDE.md`, `AGENTS.md`,
  `PLAN.md`, `ISSUE-TRACKING.md`, `README.md`, `.gitignore`,
  `pyproject.toml`, any other `src/sasha/*.py`, `tests/contract/`.

## Acceptance criteria
(encoded 1:1 in `tests/contract/test_task_3_4_repl_streaming.py`)
1. A multi-chunk response reaches `out` as MULTIPLE calls whose
   concatenation is the full text — `test_ac1_chunks_stream_through`.
2. Text streamed before a tool call reaches `out` BEFORE the confirmation
   prompt renders — `test_ac2_text_precedes_confirmation`.

## Standing instructions
- Your goal: make `tests/contract/test_task_3_4_repl_streaming.py` pass with
  the ENTIRE suite green (`bash scripts/run-tests.sh`).
- NEVER modify files under `tests/contract/` — the gate rejects the commit
  if you do. If a contract test seems wrong or unimplementable, stop and
  flag it; do not commit.
- Deliver exactly ONE new commit on top of the HEAD you started from; never
  amend, rebase, or reset; leave the working tree clean.
- Commit message per AGENTS.md, with trailers:
  `Done: Task 3.4 — <one line>`, `Fixed: ISSUE-013 (REPL streaming passthrough)`,
  `Next: Task 4.1 — v2 planning (Tier 1)`, and the Co-Authored-By signature
  line from your dispatch prompt.
