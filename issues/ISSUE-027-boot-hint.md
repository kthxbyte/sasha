# ISSUE-027: Sasha looks like a thin bash wrapper — no boot-time hint

**Status:** Specced as Task 10.1 — see
`docs/superpowers/specs/task-10.1-boot-hint.md`.
**Discovered:** 2026-07-16, in conversation after the configurable-prompt
feature (Task 9.1) landed.

## Context

With the prompt now bash-colored (`user@host:path $ `) via the
configurable-prompt feature, Salvador noted Sasha "looks and feels a lot
like bash now, like a thin wrapper" — a new user has no visual cue that
`??` opens a chat with an LLM, or which provider/model is actually behind
it.

## Decided design (conversational brainstorm, no separate design doc —
## small enough to spec directly, same pattern as Phases 7/8)

Two `[sasha]`-prefixed lines printed once, at the very top of
`Repl.loop()`, before the first prompt is ever shown:

```
[sasha] profile: deepseek (deepseek-chat @ https://api.deepseek.com/v1)
[sasha] tip: type ?? followed by a question to chat with me directly
```

- **Line 1 (profile)** reuses the exact format the existing `sasha
  profile` command already prints (`repl.py`'s `profile` verb handler,
  `f"[sasha] profile: {p.name} ({p.model} @ {p.base_url})"`) — factored
  into a small shared helper so the format lives in one place, not two.
- **Line 2 (tip)** is new, fixed text, no interpolation.
- **No suppression/config toggle for v1** — Salvador's call ("it may bore
  the user, but not me"). Shown on every launch, unconditionally.
- Blast radius checked before speccing: only
  `test_task_6_2_colorization_prompt.py`'s `test_ac4_prompt_*` tests call
  `Repl.loop()` among existing contract tests, and they only assert on
  `input_fn.prompts[0]` (the actual prompt string), never on `sink` writes
  — the two new `write_line()` calls before the prompt don't touch those
  assertions. Zero existing contract tests need amending.

## Next steps

Task 10.1 is specced and ready for dispatch — this issue is resolved by
that task, not a further deferred note.
