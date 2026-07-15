# Task 7.1: Per-line colors for the confirmation gate

## Authoritative contract
`issues/ISSUE-023-confirmation-gate-colors.md` is the complete design
record for this task — read it first. Summary: `TerminalConfirmer.ask`
prints a three-line block (command / description / y-n-e decision prompt)
that today renders as a single solid color. This task colors each line
distinctly: yellow for the command, dim/faint for the description, and the
existing green `_PROMPT_COLOR` (reused, not a new color) for the decision
prompt — since green already means "you're at a decision point" via the
Task 6.2 shell prompt.

## What already exists
- `src/sasha/repl.py` `TerminalConfirmer.ask` (the method this task
  modifies) and the color constants block (`_SASHA_COLOR`, `_LLM_COLOR`,
  `_PROMPT_COLOR`, `_RESET`, `_RL_IGNORE_START`, `_RL_IGNORE_END`, sitting
  between `class TerminalConfirmer` and `class TerminalSink`) — this task
  adds two more constants to that same block.
- `TerminalSink.write_line` (Task 6.1/6.2, unmodified by this task) — it
  already wraps whatever string it's given in `_SASHA_COLOR`...`_RESET`
  when `color_enabled`. This task's embedded per-line colors compose with
  that outer wrap without conflict (see Design note below) — **do not
  change `write_line`, `write_chunk`, `ensure_newline`, or the `Sink`
  protocol to accommodate this task. Nothing outside `TerminalConfirmer.
  ask` and the constants block changes.**
- `Repl` (all of it) — untouched, this task doesn't touch `Repl` at all.

## What's new
Add two module-level constants to the existing color-constants block in
`src/sasha/repl.py` (same block as `_SASHA_COLOR` etc.):

```python
_COMMAND_COLOR = "\x1b[33m"  # yellow — the literal command about to run
_DIM = "\x1b[2m"             # faint — secondary/explanatory text
```

Replace `TerminalConfirmer.ask` with:

```python
    def ask(self, command, description):
        desc = description if description else "(no description provided)"
        if self._sink.color_enabled:
            text = (f"{_COMMAND_COLOR}[sasha] wants to run:  {command}{_RESET}\n"
                    f"{_DIM}        └ {desc}{_RESET}\n"
                    f"{_PROMPT_COLOR}        [y]es / [n]o / [e]dit — or type a reply{_RESET}")
        else:
            text = (f"[sasha] wants to run:  {command}\n"
                    f"        └ {desc}\n"
                    f"        [y]es / [n]o / [e]dit — or type a reply")
        self._sink.write_line(text)
        response = self._input_fn("> ").strip()
        if response in ("y", "yes"):
            return ("yes", "")
        elif response in ("n", "no", ""):
            return ("no", "")
        elif response in ("e", "edit"):
            edited = self._input_fn("edit> ")
            return ("edit", edited)
        else:
            return ("reply", response)
```

Only the `text` construction changes; the response-parsing logic below it
is copied verbatim, unchanged.

### Design note: why the outer `write_line` wrap doesn't need touching

`TerminalSink.write_line` always wraps its whole argument in `_SASHA_COLOR`
(cyan) + `_RESET` when color is enabled. With this task's change, the
string it receives already contains three independently-colored,
independently-reset lines. The composed output is:

```
_SASHA_COLOR + <yellow-command-line+RESET> + "\n" + <dim-description+RESET> + "\n" + <green-prompt-line+RESET> + _RESET
```

The leading `_SASHA_COLOR` is set and then immediately overridden by the
first embedded escape code before any cyan-colored character is ever
printed — it's never visible. The trailing `_RESET` from `write_line`'s
own wrap lands after the last line's own `_RESET` has already returned the
terminal to its default state — a redundant, harmless no-op. No visible
artifact, no color bleed between lines, no change needed to `write_line`
itself. This is deliberate: it keeps the blast radius to exactly one
method and two constants, per Salvador's suggestion to keep this a single
string through a single `write_line()` call rather than restructuring the
call site into multiple sink calls.

## Constraints
- Modify `src/sasha/repl.py` only (plus optional supplementary tests under
  `tests/impl/`) — specifically only `TerminalConfirmer.ask` and the two
  new constants.
- Do not touch: `scripts/`, `docs/superpowers/`, `CLAUDE.md`, `AGENTS.md`,
  `PLAN.md`, `ISSUE-TRACKING.md`, `README.md`, `.gitignore`,
  `pyproject.toml`, `src/sasha/__init__.py`, `src/sasha/config.py`,
  `src/sasha/mutes.py`, `src/sasha/triggers.py`, `src/sasha/executor.py`,
  `src/sasha/session.py`, `src/sasha/agent.py`, `src/sasha/llm.py`,
  `src/sasha/__main__.py`, `tests/contract/`.
- Do not change `Sink`, `TerminalSink`, or `Repl` in any way — this task's
  entire diff is `TerminalConfirmer.ask` plus two constants.
- No new color parameter threaded through call sites; the three colors are
  fixed per-line as specified, matching the existing one-color-per-category
  convention (`_SASHA_COLOR` for all other `write_line` calls, `_LLM_COLOR`
  for streamed chunks).
- Greenfield-vs-surgical routing: surgical edit to one existing method.
  Dispatch to the default model (deepseek-v4-pro); do not use a
  `SASHA_DISPATCH_MODEL` local-model override.

## Acceptance criteria
(encoded in `tests/contract/test_task_7_1_confirmation_colors.py`)
1. Plain rendering (`color_enabled=False`) is byte-identical to the
   pre-Task-7.1 output, including the missing-description placeholder —
   `test_ac1_*`.
2. The command line is wrapped in `_COMMAND_COLOR`/`_RESET` when
   `color_enabled` — `test_ac2_*`.
3. The description line (and its missing-description placeholder) is
   wrapped in `_DIM`/`_RESET` — `test_ac3_*`.
4. The decision-prompt line is wrapped in the existing `_PROMPT_COLOR`/
   `_RESET` — `test_ac4_*`.
5. Stripping all ANSI escape codes from the colored rendering yields
   exactly the plain rendering — no visible text added, removed, or
   reordered by colorization — `test_ac5_*`.
6. The colored block composes correctly through the real `TerminalSink`'s
   own outer `write_line` wrap: all three inner colors appear intact and
   un-mangled in the actual printed output — `test_ac6_*`.

## Standing instructions
- Your goal: make the full contract suite pass:
  `PYTHONPATH=src python3 -m pytest tests/contract/`.
- NEVER modify files under `tests/contract/` — the gate rejects the commit
  if you do.
- Deliver exactly ONE new commit on top of the HEAD you started from; never
  amend, rebase, or reset; leave the working tree clean.
- Commit message per AGENTS.md, with trailers:
  `Done: Task 7.1 — <one line>`, `Fixed: ISSUE-023`,
  `Next: Task 7.2 — Phase 7 review (Tier 1, with Salvador)`, and the
  Co-Authored-By signature line from your dispatch prompt.
