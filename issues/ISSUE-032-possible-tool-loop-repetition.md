# ISSUE-032: Possible second, distinct repetition mechanism in agent.py's tool-calling loop

**Status:** Design note only — explicitly deferred, not specced, not
scheduled. Needs a reproducible transcript before it can be properly
investigated.
**Discovered:** 2026-07-16, while reviewing a session extract Salvador
shared (the same conversation that led to ISSUE-031's fix landing).

## Context

While confirming ISSUE-031's fix (Session.notes/last_stderr consumed
once, not re-surfaced indefinitely) against a fresh session transcript,
two open threads came up that ISSUE-031 does **not** cover:

1. **The transcript Salvador shared ended in
   `[sasha] stopping: repeated identical command / tool-turn limit
   reached`** (`agent.py`'s `run_consult`, the single message covering
   *either* of two distinct stop conditions — see §ac1 below). All 6
   tool calls visible in the pasted extract were distinct commands, so
   the "identical command repeated" branch almost certainly wasn't what
   fired on those specific calls — but the extract was explicitly a
   partial copy-paste (Salvador's terminal buffer couldn't hold the
   whole session), and the session had not exited/flushed to
   `~/.local/state/sasha/history` at the time this was discussed, so it
   could not be verified retroactively which condition actually
   triggered the stop, or what happened earlier in the same consult
   that isn't visible in the pasted extract.

2. **A separate, earlier session** (found in the persisted `readline`
   history file, predating the `hi there`/exit-127 diagnosis that led to
   ISSUE-031 entirely) contains this line, typed directly to Sasha:
   > `?? sometimes sasha gets stuck in loops of repetitive thought after
   > a combination of conversation messsages and tool calling tasks,
   > like writing files, for example`

   This describes a **different mechanism** than ISSUE-031. The
   ISSUE-031 fix only clears `Session.notes`/`last_stderr` — state
   populated exclusively by `Repl.run_once()` when a *directly-typed*
   shell command runs (`repl.py:183`, `session.note_command(...)`).
   Commands the LLM proposes and executes via tool calls
   (`agent.py`'s `run_consult` loop, including file writes via the
   `run_command` tool) never call `note_command()` at all — so if
   there's a genuine repetition pattern specific to tool-calling/
   file-writing sequences, ISSUE-031's fix would not reach it. This
   would be a separate bug in a code path (`agent.py`'s `while True:`
   tool-calling loop, `build_messages()` being called fresh on every
   iteration) not yet examined for this specific failure mode.

## What's needed before this can be specced

- A concrete, reproducible transcript of the "writing files" /
  tool-calling repetition scenario — the historical note is real but
  vague ("a combination of conversation messages and tool calling
  tasks"), not enough on its own to root-cause.
- If/when the ambiguous stop message
  (`"[sasha] stopping: repeated identical command / tool-turn limit
  reached"`) is investigated, consider splitting it into two distinct
  messages so it's clear which condition fired — flagged in the same
  conversation as a minor, separate, low-risk UX fix candidate,
  independent of the repetition-mechanism question above.

## Next steps

Needs a reproducible transcript before a real investigation can start.
Brainstorm + design spec via the usual Tier 1 flow once one is available
— this issue is a note to start from, not a task ready for dispatch.
