# Multi-line paste handling at the Sasha prompt — Design Specification

**Status:** Approved
**Date:** 2026-07-27
**Issue:** ISSUE-054

## 1. Mission

Pasting multi-line text at the Sasha prompt currently gets submitted one
line at a time as separate top-level commands, rather than as the single
block the user intended — confirmed live on Salvador's machine (Terminator,
Ctrl+Shift+V) to cascade into repeated `command not found` errors and
wasted LLM auto-consult round-trips. This adds burst-detection so a
paste-delivered block of lines is joined into one logical submission,
without depending on the terminal's own bracketed-paste cooperation
(confirmed unreliable on Salvador's setup) and without touching Sasha's
historically fragile PTY/job-control machinery.

## 2. Scope

### In scope (v1):
- A burst-detection join: after each line is read via `input()`, peek
  whether more input is already buffered with no new keypress; if so,
  keep reading and join with `\n` until nothing more is pending.
- Unifying the REPL's main-prompt input path and the confirmation gate's
  input path (y/n/e, edit, reply) behind this same mechanism — they are
  two separate, unwrapped functions today (`_repl_input` vs. the raw
  `input` builtin passed to `TerminalConfirmer`).
- A new `src/sasha/paste_join.py` module holding the pure join logic.

### Out of scope / non-goals:
- The "[Pasted text #N +K lines]" compact placeholder display (matching
  Claude Code's own terminal UI convention). Requires Sasha to take over
  raw terminal echo — a much larger, riskier change than this fix.
  Deferred as a separate follow-up.
- Any change to `Executor.run()`, its tcsetpgrp/termios job-control
  machinery, or anything on the command-*execution* path. This fix lives
  entirely in how a line of input is *read*, before any dispatch happens.
- The masked API-key entry (`getpass_fn`) — burst-detection doesn't apply
  to a password-style masked field.
- Any change to non-interactive/piped-stdin behavior (see contract #2).

## 3. Key contracts & assumptions

1. **Bracketed-paste terminal cooperation cannot be assumed.** Confirmed
   empirically: Python's `input()` + GNU readline correctly buffers a real
   bracketed paste as one atomic string when the terminal sends
   `\e[200~...\e[201~` — but Salvador's terminal (Terminator,
   Ctrl+Shift+V) does not send these for that paste action, confirmed by
   reproducing the identical line-by-line split in plain bash with zero
   Sasha involvement. The fix must work without relying on the terminal's
   cooperation.
2. **Must only activate on a real interactive tty.** Sasha's existing test
   suite pipes multi-line scripted input as separate intentional commands
   in the non-interactive path; a pipe can make many lines "pending" at
   once for reasons unrelated to pasting. The peek function returns
   `False` unconditionally when `sys.stdin.isatty()` is false, so the join
   loop degenerates to exactly today's one-line-per-call behavior there —
   no separate code path, no risk of regressing existing piped-input
   tests.
3. **Burst timing reliably separates paste from typing.** Verified via a
   `pty_harness`-based repro: genuine one-at-a-time typing (with realistic
   inter-line gaps) never shows pending input after a line; a paste-style
   burst (all bytes delivered in one write, no bracketed-paste markers —
   Salvador's exact real failure mode) does. Human inter-keystroke gaps
   and a terminal's near-instant burst delivery differ by orders of
   magnitude, so this heuristic is expected to be reliable in practice,
   though it is a heuristic, not a formal guarantee — see §7 decisions.
4. **This must not touch the PTY/job-control machinery.** The join
   happens entirely within how one line of input is read, strictly before
   any command dispatch or `Executor.run()` call — zero interaction with
   the tcsetpgrp/termios code implicated in ISSUE-040's history.
5. **Two separate, currently-unwrapped input paths must unify.** The main
   REPL prompt (`_repl_input`, wraps `input` + conditional
   `readline.add_history`) and the confirmation gate (`TerminalConfirmer`,
   constructed with the raw `input` builtin directly) are distinct
   function objects today. Both must route through the same join logic —
   confirmed by Salvador that the edit/reply prompts are free-text entry
   points that can just as legitimately receive a multi-line paste as the
   main prompt.

## 4. Architecture

### 4.1 `src/sasha/paste_join.py` (new module)

```python
MAX_BURST_LINES = 1000

def read_joined_input(prompt: str, read_line, has_pending) -> str:
    """read_line(prompt: str) -> str: same contract as input() — may
    raise EOFError. has_pending() -> bool: True if another line's worth
    of input is already available with no new keypress needed.

    Reads one line via read_line(prompt). If has_pending() afterward,
    keeps reading further lines via read_line("") (no prompt — nothing
    additional should print for a line the user already typed/pasted)
    and joining with '\\n', until has_pending() is false or
    MAX_BURST_LINES is reached. EOFError from the first read_line call
    propagates (matches today's exact Ctrl+D-at-an-empty-prompt
    behavior). EOFError from a later read_line call during the join
    stops the loop and returns whatever has been joined so far, rather
    than propagating and discarding already-collected input.
    """
```

Pure orchestration — no I/O, no `select`, no `sys.stdin` reference of its
own, so it is directly unit-testable with fake `read_line`/`has_pending`
callables.

### 4.2 Real wiring (`src/sasha/__main__.py`)

```python
import select

_PASTE_GRACE_S = 0.01

def _has_pending_stdin():
    if not sys.stdin.isatty():
        return False
    r, _, _ = select.select([sys.stdin], [], [], _PASTE_GRACE_S)
    return bool(r)

def _joined_input(prompt):
    return paste_join.read_joined_input(prompt, input, _has_pending_stdin)

def _repl_input(prompt):
    line = _joined_input(prompt)
    if line.strip() and not _isatty:
        readline.add_history(line)
    return line

confirmer = TerminalConfirmer(_joined_input, sink)
```

`_repl_input`'s existing shape (the join, then the conditional
`add_history` for the non-tty path) is unchanged apart from routing
through `_joined_input` instead of calling `input(prompt)` directly.
`TerminalConfirmer`'s construction changes from `TerminalConfirmer(input,
sink)` to `TerminalConfirmer(_joined_input, sink)` — its own code
(`ask()`) is untouched; it already just calls whatever `input_fn` it was
given.

### 4.3 Behavior at each call site

- **Main command prompt** (`Repl.loop()`'s `self._input_fn(rendered)`):
  a pasted multi-line block is now dispatched as one command. If it's a
  `??` question, the whole block (with its embedded newlines) becomes the
  question text. If it's a raw shell command, the whole block is handed
  to `Executor.run()` as one multi-line string — for genuinely multi-line
  shell content (e.g. a small pasted script), this is *more correct* than
  today's per-line fragmentation, since bash itself handles a multi-line
  script as sequential statements within one invocation.
- **Confirmation gate** (`TerminalConfirmer.ask()`): the initial `"> "`
  prompt (y/n/e or a free-text reply) and the `"edit> "` prompt both now
  correctly receive a pasted multi-line reply/replacement as one string
  instead of splitting it across multiple confirmation rounds.

## 5. Configuration

None. `MAX_BURST_LINES` and `_PASTE_GRACE_S` are plain module constants,
not exposed via config.toml — consistent with this project's existing
pattern of pinning heuristic thresholds as constants (e.g. ISSUE-051's
cascade thresholds) rather than adding config surface for knobs nobody's
asked to tune yet.

## 6. Testing strategy

- `paste_join.read_joined_input`'s pure logic tested directly with fake
  `read_line`/`has_pending` callables: multiple lines get joined in order;
  a single line (no pending) is a no-op, returned as-is; `EOFError` from
  the first read propagates; `EOFError` mid-burst stops and returns the
  partial join; `MAX_BURST_LINES` caps a pathological unbounded-pending
  case.
- Real end-to-end behavior (genuine typing vs. a real pasted burst told
  apart correctly, with real `select`/`sys.stdin`) tested via
  `tests/contract/pty_harness.py` — the same harness already used for
  Task 4.1/24.1's job-control tests — formalizing the exact repro already
  verified live in this conversation.
- Non-tty guard tested directly: `_has_pending_stdin`-equivalent returns
  `False` when `isatty()` is false, regardless of how much is actually
  buffered, confirming the non-interactive/piped-stdin path is unchanged.
- `TerminalConfirmer` construction tested to confirm it now receives the
  same joined-input function as `Repl`, not the raw `input` builtin.

## 7. Decisions log

- **Decision:** Burst-timing heuristic (peek via `select` after each
  line), not terminal bracketed-paste cooperation.
  **Rationale:** Confirmed empirically that Salvador's terminal doesn't
  reliably send bracketed-paste sequences, and that this affects even
  plain bash — relying on terminal cooperation is not sufficient on his
  actual setup.
- **Decision:** Small non-zero grace timeout (`0.01s`), not a hard `0`.
  **Rationale:** Absorbs minor OS-level delivery jitter on very large
  pastes without meaningfully affecting perceived latency for normal
  typing (whose inter-line gaps are orders of magnitude larger). Untuned
  beyond what was verified — a candidate to revisit if real usage
  disagrees.
- **Decision:** Only activate when `sys.stdin.isatty()` is true.
  **Rationale:** Piped/non-interactive stdin (used throughout the
  existing test suite) can make many lines "pending" at once for reasons
  unrelated to pasting; burst-joining there would be a real regression.
- **Decision:** Unify the main prompt and the confirmation gate behind
  one shared input function, rather than fixing only the main prompt.
  **Rationale:** Salvador's explicit correction — the confirmation gate's
  edit/reply prompts are free-text entry points, not single-token
  confirmations, and can just as legitimately receive a multi-line paste.
- **Decision:** The "[Pasted text #N +K lines]" placeholder display is
  explicitly out of scope here.
  **Rationale:** Requires Sasha to take over raw terminal echo entirely —
  a much larger, riskier change than this fix, and orthogonal to actually
  fixing the reported bug. Deferred as a separate follow-up.
- **Decision:** No config surface for the grace timeout or line cap.
  **Rationale:** Consistent with this project's existing pattern —
  heuristic thresholds are pinned constants until real experience says
  otherwise, not speculative config knobs.
