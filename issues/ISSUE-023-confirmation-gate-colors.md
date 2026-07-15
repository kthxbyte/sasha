# ISSUE-023: Confirmation gate — distinct colors per line

**Status:** Specced as Task 7.1.
**Discovered:** 2026-07-15, in conversation after Task 6.2 landed and
Salvador ran Sasha interactively.

## Context

`TerminalConfirmer.ask` prints a three-line block for every command that
needs confirmation:

```
[sasha] wants to run:  ls -laR /home/salvador/Sasha/src/sasha
        └ Recursively list contents of sasha subdirectory
        [y]es / [n]o / [e]dit — or type a reply
```

All three lines currently render in the same color (`_SASHA_COLOR`, cyan —
Task 6.2 applies one wrap around the whole block, since `TerminalConfirmer`
builds it as a single string passed to one `write_line()` call). Salvador
asked whether the three lines — the command itself, its description, and
the y/n/e decision prompt — could use different colors to hint at each
line's role.

## Decision

Three-color scheme, chosen to reuse existing semantics where possible:
- **Command line** — new yellow (`\x1b[33m`). It's literal shell syntax
  about to execute, not Sasha's own narration — distinct from cyan
  (Sasha's voice) and magenta (LLM streamed text).
- **Description line** — new dim/faint (`\x1b[2m`, SGR "faint"). Secondary,
  explanatory context, visually subordinate to the command above it.
- **Decision prompt line** — reuses the existing green `_PROMPT_COLOR`
  (Task 6.2's shell-prompt color). Deliberate reuse, not a new color: green
  already means "you're at a decision point, type something" everywhere
  else in Sasha (the `user@host:path ✦ ` prompt), so the confirmation gate
  now shares that same visual language instead of introducing a fourth,
  unrelated meaning.

Salvador's implementation suggestion: keep this a single string and a
single `write_line()` call (don't restructure the call site into three
separate sink calls) — wrap each line in its own ANSI color + reset
directly in the string `TerminalConfirmer.ask` builds. `TerminalSink.
write_line`'s own outer cyan wrap (`_SASHA_COLOR`...`_RESET` around the
whole string) still applies on top and is untouched — it becomes visually
inert (immediately overridden by the first embedded color, and the final
outer reset is a harmless no-op after the last line's own reset), so no
changes to the `Sink` protocol or `TerminalSink` are needed at all. See
`docs/superpowers/specs/task-7.1-confirmation-colors.md` for the exact
fix sketch.
