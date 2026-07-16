# ISSUE-025: Reuse prompt.py's color-token vocabulary for other UI surfaces

**Status:** Design note only — explicitly deferred, not specced, not scheduled.
**Discovered:** 2026-07-16, during brainstorming for the configurable-prompt
feature (ISSUE-022's Tier 1).

## Context

While designing `src/sasha/prompt.py`'s named color tokens (`\[green\]`,
`\[bold-red\]`, `\[reset\]`, etc. — a fixed dict mapping token names to ANSI
SGR codes), Salvador asked whether the same vocabulary could later drive
colors for other UI surfaces: the confirmation gate (`TerminalConfirmer`,
currently hardcoded `_COMMAND_COLOR`/`_DIM`/`_PROMPT_COLOR` from Task 7.1),
the "thinking" indicator, or `TerminalSink`'s own `_SASHA_COLOR`/`_LLM_COLOR`.

## Why deferred

The color-token table is inherently generic — nothing about a
name-to-ANSI-code dict is prompt-specific. But wiring it up to other
surfaces means touching `TerminalConfirmer` and `TerminalSink` again,
adding new config keys for each configurable surface, and deciding how
per-surface config composes with the existing hardcoded constants. That's
a meaningfully bigger scope than "add a configurable prompt" — closer to
"add a theming system" — and wasn't asked for beyond this passing thought.

## What to preserve for when this is picked up

`prompt.py`'s color table is being built as a clean, exported piece (e.g.
`COLOR_TOKENS: dict[str, str]`), not buried inside prompt-parsing logic —
specifically so it's reusable later without a rewrite. If this is picked
up: brainstorm which surfaces get config keys, whether they share one
`[colors]` config table or each surface gets its own key, and whether the
existing hardcoded constants become defaults or get removed entirely.

## Next steps (when picked up)

Brainstorm + design spec via the usual Tier 1 flow if/when Salvador wants
to schedule this — this issue is a note to start from, not a task ready
for dispatch.
