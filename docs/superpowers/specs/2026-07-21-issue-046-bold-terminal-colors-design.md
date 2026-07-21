# Bold TerminalSink colors (ISSUE-046)

**Status:** Draft for review
**Date:** 2026-07-21

## 1. Mission

Make Sasha's own console output (`TerminalSink`'s `[sasha] ...` message
lines and the LLM's streamed response text) bold, for better visual
weight against plain shell output. Salvador's own request, already fully
scoped through a short back-and-forth — no open design questions remain.

## 2. Scope

### In scope:
- `_SASHA_COLOR`, `_LLM_COLOR`, `_PROMPT_COLOR`, `_COMMAND_COLOR`
  (`src/sasha/repl.py`) each gain the bold SGR prefix (`1;`).

### Out of scope / non-goals:
- `_DIM` stays non-bold. Salvador's call — bold and faint (SGR 1 and 2)
  are contradictory attributes; combining them renders inconsistently
  across terminals (some cancel one out, some render oddly). The
  description line under a proposed command keeps its own distinct,
  de-emphasized style.
- `prompt.py`'s `COLOR_TOKENS` vocabulary — already has `bold-*` variants
  for every color, already used in `DEFAULT_TEMPLATE`; nothing to change
  there.
- Retrofitting this into a unified, config-driven color scheme — the
  same non-goal already logged against ISSUE-025/ISSUE-045; still a
  separate, not-yet-scheduled follow-up.

## 3. Key contracts & assumptions

1. **Purely a constant-value change, no logic changes.** All five
   `TerminalSink` colors are already referenced symbolically everywhere
   they're used (in `repl.py` itself and in every contract test that
   touches them) — nothing hardcodes the literal escape bytes. Confirmed
   by grep before writing this doc: `tests/contract/test_task_7_1_confirmation_colors.py`,
   `test_task_8_1_write_line_newline_fix.py`, and
   `test_task_6_2_colorization_prompt.py` all `from sasha.repl import
   _SASHA_COLOR` (etc.) and build expected strings from the imported
   constant, not a hardcoded value.
2. **No contract-test amendments needed.** A direct consequence of
   contract #1 — every existing test keeps passing unchanged once the
   constants' values change, since they all resolve the new value
   automatically through the same import.

## 4. The change

```python
_SASHA_COLOR = "\x1b[1;36m"    # bold cyan (was "\x1b[36m")
_LLM_COLOR = "\x1b[1;35m"      # bold magenta (was "\x1b[35m")
_PROMPT_COLOR = "\x1b[1;32m"   # bold green (was "\x1b[32m")
_COMMAND_COLOR = "\x1b[1;33m"  # bold yellow (was "\x1b[33m")
_DIM = "\x1b[2m"               # unchanged — faint, not bold
```

## 5. Decisions log

- **Decision:** Bold `_SASHA_COLOR`/`_LLM_COLOR`/`_PROMPT_COLOR`/
  `_COMMAND_COLOR`; leave `_DIM` alone. **Rationale:** Salvador's call,
  after being shown the bold+faint contradiction concern directly.
- **Decision:** No new abstraction, no config exposure — four literal
  string edits. **Rationale:** Matches the actual size of the change;
  anything more would be solving a problem this ask doesn't have.

## 6. Testing strategy

No new test file needed in the usual "encode every AC" sense — the
existing suites (`test_task_7_1_confirmation_colors.py`,
`test_task_8_1_write_line_newline_fix.py`,
`test_task_6_2_colorization_prompt.py`) already assert against the
symbolic constants and will validate the new values automatically.
A small new contract test pins the *exact* new bold values directly
(guards against a future accidental revert), and confirms `_DIM`'s value
is unchanged.
