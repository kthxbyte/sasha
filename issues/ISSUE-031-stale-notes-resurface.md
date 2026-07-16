# ISSUE-031: Stale command notes/stderr silently re-surface into every subsequent LLM turn

**Status:** Specced as Task 14.1 — see
`docs/superpowers/specs/task-14.1-consume-notes.md`.
**Discovered:** 2026-07-16, from a real interaction Salvador shared: after
`hi there` failed at the shell prompt (exit 127, auto-consulted
correctly), two subsequent unrelated `??` chat questions both caused the
LLM to re-explain the same exit-127 failure, unprompted.

## Root cause (confirmed against the code and the original Task 2.2 spec)

`Session.notes` (populated by `note_command()`, a rolling FIFO of the
last 10 `(line, exit_code)` pairs) and `Session.last_stderr` are
unconditionally re-included in the context block on **every**
`build_messages()` call (`task-2.2-session.md` §7.3) — this was a
deliberate Phase 2 design choice (give the LLM rolling situational
awareness of recent shell activity), but nothing ever clears a note once
it's been shown. Since `??` chat turns don't call `note_command()` (only
directly-typed shell commands do, via `Repl.run_once` → `repl.py:183`),
a note from a command run several turns ago keeps getting freshly
re-appended to the context block on every later consult — auto-triggered
or user-initiated — until either 10 more commands push it out of the
FIFO, or the process ends.

**Important correction to the local LLM's own self-diagnosis:** in the
actual conversation, the model attributed the repetition to a vague
"conversation history feedback effect" ("the raw conversation history
fed back into my context, making it look like I was seeing it fresh each
time"). That explanation doesn't match the mechanism — the repeated
content wasn't stored conversation history replaying; it was the *same
ephemeral context block* (which is never persisted to history — see
`build_messages`'s "returned copy only" contract) being freshly
reconstructed from unconsumed `Session.notes`/`last_stderr` state, and
looking identical each time because nothing had changed to consume or
age it out. Worth remembering generally: an LLM's own explanation for
its behavior is not reliable evidence of the actual mechanism — verify
against the code.

## Fix direction (specced, not yet implemented)

`build_messages()` clears `self.notes = []` and resets
`self.last_stderr = ""` after constructing the context block, so a given
note/stderr is shown exactly once (in the very next context block built
after it's recorded), not indefinitely. Confirmed this doesn't conflict
with any existing contract test — neither of `test_task_2_2_session.py`'s
`build_messages()`-calling tests calls it twice with notes present in
between.

## Next steps

Task 14.1 is specced and ready for dispatch.
