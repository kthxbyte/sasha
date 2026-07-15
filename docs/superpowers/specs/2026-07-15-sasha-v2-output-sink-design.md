# Sasha v2 — Output Sink Design (ISSUE-014 / ISSUE-018 / ISSUE-019)

**Status:** Draft for review
**Date:** 2026-07-15
**Amends:** `docs/superpowers/specs/2026-07-14-sasha-design.md` §5.5, §9.5, §10

## 1. Mission

Replace `Repl`'s single overloaded `out: Callable[[str], None]` parameter —
which today is either literally `print` (real terminal) or a test spy,
distinguished at runtime by an `is not print` identity check inside the
`_consult` streaming closure — with a small `Sink` interface that cleanly
separates "write one complete message" from "write raw streamed text." This
fixes ISSUE-014 (the identity-check hack) and, using the same mechanism,
ISSUE-018 (LLM output missing its trailing newline) and the non-stderr half
of ISSUE-019 (colored prompt, colored `[sasha]` messages, colored LLM
responses, `user@host` in the prompt).

## 2. Scope

### In scope:
- A `Sink` protocol (`write_line`, `write_chunk`, `ensure_newline`,
  `color_enabled`) and a real `TerminalSink` implementation.
- Migrating every existing `self._out(...)` call in `Repl` and
  `TerminalConfirmer` to `self._sink.write_line(...)`, and the `_consult`
  streaming closure to `self._sink.write_chunk`.
- `_consult()` calling `self._sink.ensure_newline()` before it returns
  (fixes ISSUE-018).
- Coloring `[sasha]`-prefixed messages and LLM response text, each with one
  fixed color (not per-message severity coloring — ISSUE-019 asks for one
  consistent color per category, not a whole theming system).
- Coloring the shell prompt and adding `user@host` to it (`Repl.loop()`).
- `NO_COLOR` env var and `sys.stdout.isatty()` respected — color is skipped
  for non-terminal output (piping, redirection, non-interactive runs).
- Amending the 5 existing contract test files whose `Repl`/`TerminalConfirmer`
  construction uses the old bare-callable shape (§6 below has the exact,
  file-by-file scope of each change — Tier 1 work, done before Task 6.1 is
  dispatched, not part of the dispatch itself).

### Out of scope / non-goals:
- Coloring `Executor`'s stderr relay (raw third-party program output, never
  passes through this sink; Salvador's call, 2026-07-15).
- Per-message-severity coloring (e.g. errors red vs. info cyan) — one color
  per category (`sasha`, `llm`) is the whole ask.
- Any change to `Executor`, `agent.py`, `session.py`, `llm.py`, `triggers.py`,
  `mutes.py`, `config.py` — this phase is `repl.py` + `__main__.py` only.
- A spinner or any further Task 5.2 rework — the `"[sasha] thinking…"` line
  is a `write_line` call like any other under the new sink; no behavior
  change to it.

## 3. Key contracts & assumptions

1. **Test doubles never color.** Every spy `Sink` implementation used in
   contract tests reports `color_enabled = False` and stores raw,
   unwrapped text — existing string-content assertions keep working after
   the constructor-wiring swap, with the one exception in §6 (a
   trailing-newline change that is the literal ISSUE-018 fix landing).
2. **`write_line` always ends the visible line; `write_chunk` never adds
   anything.** This is the whole distinction the protocol exists to make —
   callers never need to reason about newlines themselves; the sink does.
3. **Coloring is late-bound in the terminal implementation only.** `Repl`
   and `TerminalConfirmer` never construct ANSI codes themselves — they call
   `write_line`/`write_chunk` with plain text; only `TerminalSink` decides
   whether/how to wrap it. This keeps `Repl` colorblind and easy to test.

## 4. Interface

Both defined in `src/sasha/repl.py`, alongside the existing
`TerminalConfirmer` (matching where `Confirmer` lives relative to `agent.py`
in the current codebase — the real implementation of an injected protocol
lives next to the class that consumes it).

```python
from typing import Protocol


class Sink(Protocol):
    color_enabled: bool

    def write_line(self, text: str) -> None:
        """Write one complete message. Implementations append a newline."""

    def write_chunk(self, text: str) -> None:
        """Write raw streamed text with no added newline. May be called
        many times for one logical message. A no-op for empty text."""

    def ensure_newline(self) -> None:
        """Write a newline only if the last write didn't already end in
        one. No-op if nothing has been written yet."""


_SASHA_COLOR = "\x1b[36m"   # cyan
_LLM_COLOR = "\x1b[35m"     # magenta
_RESET = "\x1b[0m"


class TerminalSink:
    def __init__(self) -> None:
        self.color_enabled = (sys.stdout.isatty()
                              and not os.environ.get("NO_COLOR"))
        self._ends_in_newline = True

    def _wrap(self, text: str, color: str) -> str:
        if not self.color_enabled:
            return text
        return f"{color}{text}{_RESET}"

    def write_line(self, text: str) -> None:
        print(self._wrap(text, _SASHA_COLOR))
        self._ends_in_newline = True

    def write_chunk(self, text: str) -> None:
        if not text:
            return
        sys.stdout.write(self._wrap(text, _LLM_COLOR))
        sys.stdout.flush()
        self._ends_in_newline = text.endswith("\n")

    def ensure_newline(self) -> None:
        if not self._ends_in_newline:
            sys.stdout.write("\n")
            sys.stdout.flush()
            self._ends_in_newline = True
```

`Repl.__init__`'s `out` parameter is renamed `sink` (same positional slot).
`TerminalConfirmer.__init__`'s `out` parameter is renamed `sink`; its one
`self._out(...)` call becomes `self._sink.write_line(...)`.

Every other `self._out(...)` call site in `repl.py` (the OFFER line, the mute
notice, the unknown-command/unknown-profile lines, the `sasha profile`/`sasha
help` output, the `ChatError` "unreachable" line, the Ctrl+Z-terminated
message, the `"[sasha] thinking…"` line) becomes `self._sink.write_line(...)`
verbatim — same text, same call sites, only the receiver changes.

## 5. Mechanism

### 5.1 Newline tracking (ISSUE-018)

`TerminalSink` tracks `_ends_in_newline` across both `write_line` (always
ends `True`, since `print` adds the newline) and `write_chunk` (set from
whether the just-written chunk's text ends in `\n`; empty chunks are a
no-op and don't touch the flag). `Repl._consult` calls
`self._sink.ensure_newline()` unconditionally right before it returns —
covering the ChatError path (already newline-terminated via `write_line`,
so this is a no-op there) and the successful-stream path (fixes the actual
bug) with one call, no branching needed at the call site.

### 5.2 Colorization

Every current `write_line` caller is already a `[sasha] `-prefixed message
and the only `write_chunk` caller is LLM streaming text, so each method
applies exactly one fixed color internally — no color parameter threaded
through `Repl`'s call sites, no risk of a caller picking the wrong color.
Each chunk is individually wrapped in `color…reset` (simpler than tracking
"stream started/ended" state for one continuous color run; the visual
result is identical, terminals coalesce adjacent identical SGR codes with
no visible artifact — deliberate simplicity, see §7 decisions log).

### 5.3 Prompt: color + `user@host`

Unrelated to the sink (the prompt string goes to `input()`/readline
directly, never through `write_line`/`write_chunk`). `Repl.loop()` builds:

```python
import getpass
import socket

_PROMPT_COLOR = "\x1b[32m"  # green
_RL_IGNORE_START = "\001"
_RL_IGNORE_END = "\002"

def loop(self):
    home = os.path.expanduser("~")
    user = getpass.getuser()
    host = socket.gethostname()
    color_enabled = self._sink.color_enabled
    while True:
        cwd = self._executor.cwd
        display_cwd = "~" + cwd[len(home):] if cwd.startswith(home) else cwd
        raw_prompt = f"{user}@{host}:{display_cwd} ✦ "
        if color_enabled:
            prompt = (f"{_RL_IGNORE_START}{_PROMPT_COLOR}{_RL_IGNORE_END}"
                      f"{raw_prompt}"
                      f"{_RL_IGNORE_START}{_RESET}{_RL_IGNORE_END}")
        else:
            prompt = raw_prompt
        try:
            line = self._input_fn(prompt)
        ...  # rest of loop() unchanged
```

`getpass.getuser()` (checks `LOGNAME`/`USER`/`USERNAME` env vars, then the
password database) and `socket.gethostname()` are used instead of reading
`$USER`/`$HOSTNAME` directly — `HOSTNAME` is a bash shell variable, not
reliably exported to child processes, so reading it via `os.environ` would
often be empty; both replacements are stdlib and always return something.

The `\001`/`\002` markers around the ANSI codes are readline's "these bytes
are zero-width, don't count them for cursor math" convention — without them,
line editing (backspace, arrow keys, history recall) visibly desyncs the
cursor position the moment the prompt contains raw escape codes. This is
the one genuine pitfall in this design; a naive implementation that skips
the markers will look fine on the first line and then corrupt on the second
keystroke of editing.

`color_enabled` is read from `self._sink.color_enabled` rather than
`Repl.loop()` recomputing its own `isatty`/`NO_COLOR` check — one source of
truth, so the prompt and the message coloring can never disagree about
whether color is on.

## 6. Existing contract test migration (Tier 1 work, done at spec time)

Every file below constructs `Repl(...)` and/or `TerminalConfirmer(...)`
positionally, with the last argument being a bare callable (`out_chunks.append`
or similar). That argument becomes a small `SpySink` instance instead. This
section is authoritative for exactly what changes in each file — Task 6.1's
dispatch does not touch `tests/contract/` at all (these amendments land
before the task spec is committed, same as the rest of the contract-test
rule: only Tier 1 amends existing contract tests).

- **`test_task_3_1_repl.py`** — wiring-only. Add a `SpySink` class
  (`write_line`/`write_chunk` both append the raw text to a shared list,
  `ensure_newline` appends `"\n"` only if the list is non-empty and its last
  entry doesn't already end in one, `color_enabled = False`). Swap
  `out_chunks.append` → `SpySink(out_chunks)` in both `_repl()` and
  `_confirmer()`. No assertion changes — every existing check is either a
  substring check (`_out_text`, unaffected by a possible trailing `"\n"`
  entry) or an exact match against one specific list element written by a
  single `write_line` call (unaffected by unrelated entries elsewhere in the
  list).
- **`test_task_3_4_repl_streaming.py`** — wiring, **plus one content
  change**. Its `SpySink` variant appends `("out", text)` tuples (matching
  the file's existing tagged-event convention) instead of bare strings.
  `test_ac1_chunks_stream_through`'s assertion
  `assert "".join(out_chunks) == "a reasonably long streamed answer"` must
  become `"a reasonably long streamed answer\n"` — this is ISSUE-018's fix
  landing exactly where it should: the old assertion encoded the bug
  (missing trailing newline) as correct behavior.
  `test_ac2_text_precedes_confirmation` needs no change — its assertion only
  inspects events *before* the first `"ask"` event, and `ensure_newline`
  only fires after `_consult` fully returns.
- **`test_task_4_1_job_control.py`** — wiring-only, in
  `test_ac5_repl_prints_ctrl_z_message_and_skips_triggers`'s inline `Repl(...)`
  construction. That test's path returns before any `_consult()`/streaming
  call, so no content change.
- **`test_task_5_1_drop_127_preamble.py`** — wiring-only in `_repl()`. All
  three tests either check `stub.requests` (unaffected) or use substring
  checks against `_out_text(out)`.
- **`test_task_5_2_thinking_indicator.py`** — wiring-only in `_repl()`.
  `out[0] == INDICATOR` and `out.count(INDICATOR)` check specific/counted
  entries, unaffected by an unrelated trailing `"\n"` entry elsewhere in the
  list.

## 7. Decisions log

- **Decision:** `Sink` is a `typing.Protocol` with `write_line`/`write_chunk`/
  `ensure_newline`/`color_enabled`, replacing the bare `out` callable.
  **Rationale:** The single-callable design forced the `is not print`
  identity-check hack (ISSUE-014) because one function was being asked to
  serve two different callers (real terminal vs. test spy) with two
  different needs (immediate raw write vs. observable capture). A protocol
  with named methods removes the ambiguity structurally.
- **Decision:** Stderr coloring is out of scope for this phase.
  **Rationale:** Salvador's call — stderr is relayed raw by `Executor`,
  architecturally unrelated to this sink, and coloring arbitrary third-party
  program output is a separate, riskier concern (may already contain its
  own ANSI codes).
- **Decision:** `color_enabled` lives on the `Sink` and is read by
  `Repl.loop()` for the prompt, rather than each site recomputing its own
  `isatty`/`NO_COLOR` check.
  **Rationale:** One source of truth — the prompt and message coloring can
  never disagree about whether color is active.
- **Decision:** Each streamed chunk is individually color-wrapped rather
  than tracking a "color run" start/end state.
  **Rationale:** Simplicity — identical visible result, no extra protocol
  surface, no extra state to get wrong.
- **Decision:** `getpass.getuser()` / `socket.gethostname()`, not
  `os.environ["USER"]`/`os.environ["HOSTNAME"]`.
  **Rationale:** `HOSTNAME` is a bash shell variable, not reliably present
  in a child process's environment; the stdlib functions are the portable
  equivalent and match what real shells actually use.
- **Decision:** Task 6.1 (the `Sink` protocol + `TerminalSink` +
  ISSUE-014/018 fix + all `repl.py`/`__main__.py`/existing-test wiring) is
  split from Task 6.2 (colorization + prompt format, ISSUE-019).
  **Rationale:** Salvador's call — isolates the higher-risk, existing-test-
  touching plumbing from the purely additive cosmetic layer built on top of
  it; 6.2 needs no further contract-test amendments once 6.1 lands.

## 8. Testing strategy

Extends design §11. No new testing *technique* is needed — this is pure
Python object substitution, no PTYs or real terminals involved (unlike
Phase 4). Task 6.1's new contract tests (`tests/contract/test_task_6_1_*.py`)
exercise `TerminalSink` directly (newline tracking, `color_enabled` off when
`NO_COLOR` is set or stdout isn't a tty) and exercise `Repl`/`TerminalConfirmer`
through a `SpySink` the same way the amended existing files do. Task 6.2's
tests exercise `TerminalSink`'s actual ANSI output (color codes present when
`color_enabled`, absent when not) and the prompt string's `\001`/`\002`
markers and `user@host` content.
