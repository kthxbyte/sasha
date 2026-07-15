# Task 8.1: write_line() closes a dangling unterminated line before printing

## Authoritative contract
`issues/ISSUE-024-write-line-missing-ensure-newline.md` is the complete
design record for this task — read it first. Summary: `TerminalSink.
write_line()` closes any open color run (`_close_chunk_run()`) but never
checks whether the previous write left the cursor mid-line. `ensure_newline()`
already has that check; `write_line()` needs the same check applied to
itself, before it prints its own text.

## What already exists
- `src/sasha/repl.py` `TerminalSink` (the class this task modifies) —
  specifically `write_line`, `write_chunk`, `ensure_newline`,
  `_close_chunk_run`, and the `_ends_in_newline` / `_chunk_run_open` instance
  state, all landed in Task 6.1 and extended for color in Task 6.2. This
  task changes `write_line` only.
- `Sink` protocol (the `typing.Protocol` class) — its docstrings already
  describe the intended behavior correctly; no change needed there.
- `Repl` and `TerminalConfirmer` — both call `self._sink.write_line(...)`
  at various points (mute/unmute/profile/help messages, the ChatError
  message, the Ctrl+Z message, the confirmation gate). None of these call
  sites change — the fix lives entirely inside `TerminalSink.write_line`,
  so every caller is fixed at once.

## What's new
Replace `TerminalSink.write_line` with a version that emits a separating
newline when the previous write left the line unterminated — the same
condition `ensure_newline()` already checks:

```python
    def write_line(self, text: str) -> None:
        self._close_chunk_run()
        if not self._ends_in_newline:
            sys.stdout.write("\n")
        print(self._wrap(text, _SASHA_COLOR))
        self._ends_in_newline = True
```

Only `write_line` changes. `_close_chunk_run`, `write_chunk`,
`ensure_newline`, `_wrap`, and `__init__` are copied verbatim, unchanged.
Ordering matters: the newline check happens *after* `_close_chunk_run()`
(so an open color run's `_RESET` is emitted before the separating `\n`,
never after) and *before* the `print()` call (so the separating `\n` is
never itself colored).

### Design note: why this fixes ISSUE-024 at the root
Every `write_line()` call site in `Repl`/`TerminalConfirmer` — including
`TerminalConfirmer.ask`, which fires mid-consult whenever a tool call is
offered — routes through this one method. Fixing it here means the
confirmation gate (and every other `write_line` call) is automatically
protected against gluing onto an open `write_chunk` run, with no changes
needed anywhere else in the codebase.

### Contract-test amendments (Tier 1, done at this spec's commit)
Two existing contract tests pinned the exact buggy byte sequence this task
fixes. Both are amended in this same commit (Tier 1 amending its own prior
tests, per CLAUDE.md's contract-test rule 6):

1. `tests/contract/test_task_6_1_output_sink.py::
   test_ac3_write_line_after_chunk_still_tracked` asserted
   `"partialcomplete\n"` (chunk immediately followed by write_line with no
   separator) — this was Task 6.1's era, before the newline gap was known to
   be a bug. Amended to assert `"partial\ncomplete\n"`.
2. `tests/contract/test_task_6_2_colorization_prompt.py::
   test_ac1_write_line_closes_open_chunk_run` asserted
   `f"{_LLM_COLOR}partial{_RESET}{_SASHA_COLOR}done{_RESET}\n"` (no
   separating newline between the closed chunk run and the colored line) —
   amended to assert
   `f"{_LLM_COLOR}partial{_RESET}\n{_SASHA_COLOR}done{_RESET}\n"`.

No other existing contract test asserts the real `TerminalSink`'s exact
byte output across a `write_chunk` → `write_line` sequence — every other
file (`test_task_3_1_repl.py`, `test_task_3_4_repl_streaming.py`,
`test_task_4_1_job_control.py`, `test_task_5_1_drop_127_preamble.py`,
`test_task_5_2_thinking_indicator.py`) uses a fake `SpySink`/equivalent test
double, not the real `TerminalSink`, so they are unaffected and untouched by
this task.

## Constraints
- Modify `src/sasha/repl.py` only — specifically only the body of
  `TerminalSink.write_line`. No new methods, no new module-level constants,
  no changes to `Sink`, `Repl`, or `TerminalConfirmer`.
- Do not touch: `scripts/`, `docs/superpowers/`, `CLAUDE.md`, `AGENTS.md`,
  `PLAN.md`, `ISSUE-TRACKING.md`, `README.md`, `.gitignore`,
  `pyproject.toml`, `src/sasha/__init__.py`, `src/sasha/config.py`,
  `src/sasha/mutes.py`, `src/sasha/triggers.py`, `src/sasha/session.py`,
  `src/sasha/agent.py`, `src/sasha/llm.py`, `src/sasha/executor.py`,
  `src/sasha/__main__.py`, `tests/contract/`.
- The two amendments listed above are already committed as part of this
  task's spec commit (by Tier 1, before dispatch) — they are red against
  the pre-fix code and green against the fix in this spec. You are not
  asked or permitted to touch `tests/contract/` yourself.
- Greenfield-vs-surgical routing: surgical edit to one existing method in
  code that already works. Dispatch to the default model (deepseek-v4-pro);
  do not use a `SASHA_DISPATCH_MODEL` local-model override.

## Acceptance criteria
(encoded in `tests/contract/test_task_8_1_write_line_newline_fix.py`, plus
the two amended assertions in `test_task_6_1_output_sink.py` and
`test_task_6_2_colorization_prompt.py` listed above)

1. `write_line()` inserts exactly one `\n` before its own text when the
   preceding write (a `write_chunk` call) left the line unterminated, with
   color disabled — `test_ac1_*`.
2. `write_line()` adds no extra separating newline when the preceding write
   already ended in `\n` — no double blank line — `test_ac2_*`.
3. With color enabled, the separating `\n` lands after the closed chunk
   run's `_RESET` and before the new line's own color-wrapped text (never
   colored itself) — `test_ac3_*`.
4. `write_line()` called as the very first write of a fresh `TerminalSink`
   emits no leading newline — `test_ac4_*`.
5. Two consecutive `write_line()` calls with nothing in between produce
   exactly one `\n` per line, no extra blank lines — `test_ac5_*`.
6. `ensure_newline()` called immediately after `write_line()` remains a
   no-op (unaffected regression guard) — `test_ac6_*`.

## Standing instructions
- Your goal: make the full contract suite pass:
  `PYTHONPATH=src python3 -m pytest tests/contract/`.
- NEVER modify files under `tests/contract/` — the gate rejects the commit
  if you do. The two amendments described above are already committed;
  making the tests pass means changing `write_line`, not the tests.
- Deliver exactly ONE new commit on top of the HEAD you started from; never
  amend, rebase, or reset; leave the working tree clean.
- Commit message per AGENTS.md, with trailers:
  `Done: Task 8.1 — <one line>`, `Fixed: ISSUE-024`,
  `Next: Task 8.2 — Phase 8 review (Tier 1, with Salvador)`, and the
  Co-Authored-By signature line from your dispatch prompt.
