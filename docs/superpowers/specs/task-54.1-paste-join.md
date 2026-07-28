# Task 54.1: paste_join.py + __main__.py wiring

## Authoritative contract
`docs/superpowers/specs/2026-07-27-issue-054-paste-join-design.md` §3
(contracts #1–#5), §4 (all of §4.1–§4.3), §5 (no config surface) is the
complete contract for this task.

## What already exists to bind, not reimplement
- `src/sasha/__main__.py`: `_repl_input(prompt)` (currently wraps
  `input(prompt)` + conditional `readline.add_history`), the
  `confirmer = TerminalConfirmer(input, sink)` construction, and the
  `_isatty = sys.stdin.isatty()` variable already computed just above
  `_repl_input`'s definition. Bind to these — do not change
  `readline.add_history`'s existing conditional logic, `_isatty`'s
  computation, or anything about how `Repl`/`TerminalConfirmer` use the
  function they're given (their own code is unchanged; only which
  function they're constructed with changes).
- `src/sasha/repl.py`: `TerminalConfirmer.__init__(self, input_fn, sink)`
  and `.ask()` (lines ~625-650) — read-only reference, do not modify this
  file at all in this task.
- `tests/contract/pty_harness.py`: `spawn(target)` — the existing
  fork-with-controlling-PTY helper (immutable, already used by Task
  4.1/24.1's job-control tests). Bind to this for the real end-to-end
  tests; do not modify it.

## What's new
1. **`src/sasha/paste_join.py`** (new file). Exact public surface:
   ```python
   MAX_BURST_LINES = 1000

   def read_joined_input(prompt: str, read_line, has_pending) -> str:
       """read_line(prompt: str) -> str: same contract as input() — may
       raise EOFError. has_pending() -> bool: True if another line's
       worth of input is already available with no new keypress needed.

       Reads one line via read_line(prompt). While has_pending() is true
       afterward and fewer than MAX_BURST_LINES lines have been
       collected, keeps reading further lines via read_line("") (empty
       string prompt for every continuation read — nothing additional
       should print for a line the user already typed/pasted) and
       joining them with "\\n". EOFError raised by the FIRST read_line
       call propagates unchanged. EOFError raised by any later
       read_line call during the join stops the loop immediately and
       returns whatever has been joined so far (does not propagate,
       does not discard already-collected lines).
       """
   ```
   `has_pending` is called exactly once per loop iteration, always
   *after* a successful `read_line` call and before deciding whether to
   read another line — never called before the very first `read_line`.

2. **`src/sasha/__main__.py`**: add near the top-level imports and near
   `_repl_input`'s existing definition:
   ```python
   import select

   from sasha import paste_join

   _PASTE_GRACE_S = 0.01

   def _has_pending_stdin():
       if not sys.stdin.isatty():
           return False
       r, _, _ = select.select([sys.stdin], [], [], _PASTE_GRACE_S)
       return bool(r)

   def _joined_input(prompt):
       return paste_join.read_joined_input(prompt, input, _has_pending_stdin)
   ```
   Then:
   - `_repl_input(prompt)`'s body changes its first line from
     `line = input(prompt)` to `line = _joined_input(prompt)` — every
     other line in this function (the `if line.strip() and not _isatty:
     readline.add_history(line)` block and the `return line`) is
     unchanged.
   - `confirmer = TerminalConfirmer(input, sink)` changes to
     `confirmer = TerminalConfirmer(_joined_input, sink)`.
   - No other line in `__main__.py` changes. `repl = Repl(...,
     input_fn=_repl_input, ...)` is unchanged (it already passes
     `_repl_input`, which now internally routes through
     `_joined_input`).

## Constraints
- Do not touch `src/sasha/repl.py`, `src/sasha/executor.py`, or anything
  on the command-*execution* path — this task only changes how one line
  of input is *read*, in `__main__.py`, plus the new standalone
  `paste_join.py` module.
- Do not add any config.toml surface for `MAX_BURST_LINES` or
  `_PASTE_GRACE_S` — both are plain module constants per design §5.
- `getpass_fn`/`getpass.getpass` (the masked API-key entry) is untouched
  — burst-detection does not apply there and this task must not go near
  it.
- **`tests/contract/test_task_3_3_e2e.py` is already amended as part of
  this task's own spec commit** (found necessary during Tier 1
  verification, not left for you to do): its `SashaProc.send()` now
  sleeps briefly after writing, and `read_until()`/`drain_prompt()` track
  a consumed-position cursor instead of matching anywhere in the whole
  cumulative transcript. Reason: that test's own rapid, zero-gap
  back-to-back `send()` calls could otherwise be genuinely (and
  correctly, per this task's own design) misdetected as one pasted
  block. You do not need to touch this file — it is already fixed and
  will already be green against your work if you match the fix sketch
  below.

## Acceptance criteria
1. `read_joined_input` with a `has_pending` that returns `False`
   immediately returns exactly the first line, unmodified, and
   `read_line` is called exactly once —
   `test_ac1_single_line_no_pending_is_unchanged`.
2. `read_joined_input` with a `has_pending` that returns `True` for the
   first two follow-up checks and `False` after joins exactly three
   lines with `"\n"`, in order —
   `test_ac2_multiple_lines_joined_in_order`.
3. Every continuation call to `read_line` (every call after the first)
   is made with an empty-string prompt, regardless of what `prompt` was
   originally passed to `read_joined_input` — `test_ac3_continuation_reads_use_empty_prompt`.
4. `EOFError` raised by the *first* `read_line` call propagates out of
   `read_joined_input` unchanged — `test_ac4_first_read_eof_propagates`.
5. `EOFError` raised by a *later* `read_line` call (after at least one
   successful line) is caught; `read_joined_input` returns the lines
   successfully collected so far, joined — does not raise —
   `test_ac5_mid_burst_eof_returns_partial_join`.
6. A `has_pending` that always returns `True` results in exactly
   `MAX_BURST_LINES` calls to `read_line` in total, not more —
   `test_ac6_max_burst_lines_caps_pathological_pending`.
7. Real end-to-end, via `pty_harness`: genuine one-at-a-time typing (each
   line written with a real time gap between them, no bracketed-paste
   sequence) results in `_has_pending_stdin`-equivalent behavior that
   never joins — each line arrives at the target program separately —
   `test_ac7_real_typing_is_never_joined`.
8. Real end-to-end, via `pty_harness`: a paste-style burst (multiple
   lines delivered in one write, no bracketed-paste sequence — the exact
   real failure mode from ISSUE-054) is joined into one string containing
   all the pasted lines — `test_ac8_real_pasted_burst_is_joined`.
9. Real end-to-end, via `pty_harness`, run against the *actual*
   `python -m sasha` entry point (not a standalone harness script): pasting
   `x=5\necho $x` as a single burst at the Sasha prompt produces `5` in
   the output (proving the two lines executed as one shell invocation,
   since a bash variable set on one line and read on the next only
   survives if both lines run in the *same* subprocess — if split into
   two separate top-level Sasha commands, each gets its own fresh
   `Executor.run()` subprocess and `$x` would be empty) —
   `test_ac9_real_sasha_joins_multiline_paste_into_one_command`.
10. Real end-to-end, via subprocess with **piped (non-tty) stdin** run
    against the actual `python -m sasha` entry point: multiple lines fed
    via a pipe are each still dispatched as separate commands, exactly as
    before this change — confirms the non-interactive path is unaffected
    — `test_ac10_piped_stdin_unaffected`.
11. `TerminalConfirmer` is constructed in `__main__.py`'s `main()` with
    the exact same function object as `Repl`'s `input_fn` — verified
    in-process (no subprocess needed): monkeypatch
    `sasha.__main__.TerminalConfirmer` with a spy class that records the
    `input_fn` it was constructed with, run `main()` with an isolated
    environment and stdin immediately at EOF (so the REPL loop exits
    right away), and assert the spy recorded exactly
    `sasha.__main__._joined_input` — not the raw `input` builtin —
    `test_ac11_confirmer_uses_the_same_joined_input_function`.

## Verified fix sketch

Implemented and verified in a scratch worktree: 11/11 contract tests
green, full suite 662/662 (one pre-existing environmental flake in
`test_task_24_1_kb_relay_helper.py`, unrelated to this task, deselected).
Two real bugs were found and fixed during verification, both in test
infrastructure, not the implementation itself — see below.

`src/sasha/paste_join.py` (complete file):
```python
MAX_BURST_LINES = 1000


def read_joined_input(prompt: str, read_line, has_pending) -> str:
    lines = [read_line(prompt)]
    while len(lines) < MAX_BURST_LINES:
        if not has_pending():
            break
        try:
            lines.append(read_line(""))
        except EOFError:
            break
    return "\n".join(lines)
```

`src/sasha/__main__.py`: `import select` added; `from sasha import
paste_join` added; near `_repl_input`'s existing definition:
```python
_PASTE_GRACE_S = 0.01


def _has_pending_stdin():
    if not sys.stdin.isatty():
        return False
    r, _, _ = select.select([sys.stdin], [], [], _PASTE_GRACE_S)
    return bool(r)


def _joined_input(prompt):
    return paste_join.read_joined_input(prompt, input, _has_pending_stdin)
```
Then `confirmer = TerminalConfirmer(input, sink)` becomes
`confirmer = TerminalConfirmer(_joined_input, sink)`, and
`_repl_input`'s first line becomes `line = _joined_input(prompt)`
(everything else in that function is unchanged).

**Bug #1 (contract test infra, already fixed as part of this spec's own
commit — see Constraints):** `tests/contract/test_task_3_3_e2e.py`'s
`SashaProc.read_until()` matched anywhere in the whole cumulative
transcript (already flagged as "loose" in its own pre-existing comment),
and its `send()` wrote back-to-back with zero gap. Together these meant
`test_ac3_mute_after_three_ignores`'s rapid three-round loop could
genuinely get two of its own separate commands joined by the new
burst-detection — correct behavior for the timing actually presented,
but not what that test intended. Fixed with a consumed-position cursor
in `read_until()` and a brief `time.sleep(0.05)` in `send()` (restoring
the realistic human-typing gap the heuristic depends on).

**Bug #2 (this task's own contract test, already fixed above in this
spec):** the original `test_ac9` draft asserted `b"5" in out` after
pasting `x=5\necho $x\n` — but the tty echoes the raw pasted input
verbatim regardless of whether it's later executed correctly, so `"5"`
(from the echoed `x=5` line) appeared even when the paste was
*incorrectly* split. Fixed by using a unique marker and asserting it
appears at least twice (once from input echo, once from real `echo $x`
output) — one occurrence means the fix isn't working, not a false pass.

**Bug #3 (pty_harness usage, not a codebase bug):** a forked
`pty_harness` child that calls `input()` directly (rather than `exec`ing
a fresh subprocess) inherits the *parent's* `sys.stdin`/`sys.stdout`
Python objects — under pytest, these are capture-manager wrappers, not
the real fd-backed originals, even though the OS-level fds 0/1/2 have
already been `dup2`'d to the new pty. `input()` then either errors
("reading from stdin while output is captured") or silently writes the
prompt to pytest's capture instead of the pty. Fix (already applied in
the AC7/AC8 test bodies, nothing for you to do): reassign
`sys.stdin = sys.__stdin__` and `sys.stdout = sys.__stdout__` at the top
of the harness `target()` function before calling `input()`.

## Standing instructions
- Your goal: make the contract tests in
  `tests/contract/test_task_54_1_paste_join.py` pass.
- NEVER modify files under `tests/contract/` — the gate rejects the
  commit if you do. If a contract test seems wrong or unimplementable,
  stop and flag it.
- You may add your own supplementary tests under `tests/impl/`.
- Flag unexpected state; do not modify anything outside this task.
- Deliver exactly ONE new commit on top of the HEAD you started from;
  never amend, rebase, or reset; leave the working tree clean.
- Commit message must follow the task-commit trailer format in
  `CLAUDE.md`: `Done: Task 54.1 — <description>`, `Fixed: ISSUE-054`,
  `Next: Task 54.2 — Phase 54 review (Tier 1, with Salvador)`, and your
  `Co-Authored-By:` signature line exactly as given in the dispatch
  prompt.
