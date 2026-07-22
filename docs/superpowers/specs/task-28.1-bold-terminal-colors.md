# Task 28.1: Bold TerminalSink colors (ISSUE-046)

## Authoritative contract
`docs/superpowers/specs/2026-07-21-issue-046-bold-terminal-colors-design.md`
§4 is the complete contract: `_SASHA_COLOR`, `_LLM_COLOR`, `_PROMPT_COLOR`,
`_COMMAND_COLOR` (`src/sasha/repl.py`) each gain the bold SGR prefix
(`1;`); `_DIM` stays exactly as-is.

## What already exists
- `src/sasha/repl.py` lines 561-566 — the six color constants
  (`_SASHA_COLOR`, `_LLM_COLOR`, `_PROMPT_COLOR`, `_COMMAND_COLOR`, `_DIM`,
  `_RESET`) and every place that references them (confirmation-gate text,
  `TerminalSink._wrap()`, the LLM streaming write). **Bind to all of this —
  only the four constants' literal string values change.**

## What's new
Nothing new — four literal string edits.

## Fix sketch (authoritative)

In `src/sasha/repl.py`, change exactly these four lines:

```python
_SASHA_COLOR = "\x1b[36m"   # cyan
_LLM_COLOR = "\x1b[35m"     # magenta
_PROMPT_COLOR = "\x1b[32m"  # green
_COMMAND_COLOR = "\x1b[33m"  # yellow — the literal command about to run
```

to:

```python
_SASHA_COLOR = "\x1b[1;36m"   # bold cyan
_LLM_COLOR = "\x1b[1;35m"     # bold magenta
_PROMPT_COLOR = "\x1b[1;32m"  # bold green
_COMMAND_COLOR = "\x1b[1;33m"  # bold yellow — the literal command about to run
```

`_DIM = "\x1b[2m"` and `_RESET = "\x1b[0m"` are unchanged — do not touch
either line.

## Pre-existing contract tests — no amendment needed

`tests/contract/test_task_7_1_confirmation_colors.py`,
`test_task_8_1_write_line_newline_fix.py`, and
`test_task_6_2_colorization_prompt.py` all import the color constants
symbolically (`from sasha.repl import _SASHA_COLOR`, etc.) and build their
expected strings from the imported constant, never a hardcoded escape
value — confirmed by grep before this spec was written. They pass
unchanged once the constants' values change. New contract test
`tests/contract/test_task_28_1_bold_terminal_colors.py` (already committed
in the same commit as this spec, RED against base) pins the exact new
bold values and confirms `_DIM`/`_RESET` are unchanged.

Verified locally in a scratch worktree together with the `repl.py` change
above: full suite green, 576/576 (570 + the 6 new tests in this task's
contract file).

## Constraints
- Modify only `src/sasha/repl.py`, and only the four lines named above.
- Do not touch `_DIM` or `_RESET`.
- Do not touch `tests/contract/` at all — the new contract test file is
  already committed as part of this spec.
- Do not introduce a config option, a new abstraction, or any other color
  changes — this is a four-line constant-value edit, nothing more.
- **Executor routing (per CLAUDE.md's standing rule): this is a surgical,
  precisely-specified edit to code that already works. Dispatch to the
  default model (deepseek-v4-pro) — do not launch under a local-model
  `SASHA_DISPATCH_MODEL` override.**

## Acceptance criteria
(encoded 1:1 in `tests/contract/test_task_28_1_bold_terminal_colors.py`)
1. `test_ac1_sasha_color_is_bold_cyan` — `_SASHA_COLOR == "\x1b[1;36m"`.
2. `test_ac2_llm_color_is_bold_magenta` — `_LLM_COLOR == "\x1b[1;35m"`.
3. `test_ac3_prompt_color_is_bold_green` — `_PROMPT_COLOR == "\x1b[1;32m"`.
4. `test_ac4_command_color_is_bold_yellow` — `_COMMAND_COLOR == "\x1b[1;33m"`.
5. `test_ac5_dim_is_unchanged_and_not_bold` — `_DIM == "\x1b[2m"`.
6. `test_ac6_reset_is_unchanged` — `_RESET == "\x1b[0m"`.
7. The full existing suite is green: 576/576 — `bash scripts/run-tests.sh`.

## Standing instructions
- Your goal: make the contract tests in
  `tests/contract/test_task_28_1_bold_terminal_colors.py` pass, and keep
  the full suite green (576/576): `bash scripts/run-tests.sh`.
- NEVER modify files under `tests/contract/` — the gate rejects the commit
  if you do.
- Deliver exactly ONE new commit on top of the HEAD you started from;
  never amend, rebase, or reset; leave the working tree clean.
- Commit message per AGENTS.md, with trailers: `Done: Task 28.1 — <one
  line>`, `Fixed: ISSUE-046`,
  `Next: Task 28.2 — Phase 28 review (Tier 1, with Salvador)`, and the
  Co-Authored-By signature line from your dispatch prompt.
