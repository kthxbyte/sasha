# Task 1.4: Executor PTY tees (stderr always, stdout opt-in)

## Authoritative contract
`docs/superpowers/specs/2026-07-14-sasha-design.md` §9.1 (PTY tee algorithm)
and §9.2 (capture hygiene) are the complete contract. §5.1's signatures are
already implemented (Task 1.3) and MUST NOT change, with the one pinned
extension below.

## What already exists
- `src/sasha/executor.py` (Task 1.3): `Executor`/`ExecResult`, exit codes,
  own-process-group spawn, cwd/env side channel, `check_syntax`. All of Task
  1.3's contract tests (`tests/contract/test_task_1_3_executor_core.py`) must
  STAY green — the behavior changes here are only: stderr now flows through a
  PTY tee, stdout optionally does, and the capture fields become real.

## What's new
- Modify `src/sasha/executor.py` only:
  - stderr of every child = PTY slave; parent relays master → its own fd 2
    while capturing (§9.1 steps 1–5).
  - `capture_stdout=True` → stdout gets its own independent PTY tee relayed to
    the parent's fd 1 and captured into `stdout_text`; default keeps stdout
    inherited and `stdout_text == ""`.
  - `ExecResult.stderr_text` / `.stdout_text` populated per §9.2 + pinned
    hygiene below.

## Pinned semantics (decisions already made — implement exactly these)
1. **Signature extension**: `Executor.__init__(cwd, env, *,
   stderr_capture_bytes: int = 8192)` — keyword-only, default 8192. The same
   cap applies to the stdout capture buffer when `capture_stdout=True`.
   (Task 1.3 tests construct `Executor(cwd=..., env=...)` — still valid.)
2. **Relay**: every chunk read from a PTY master is written unmodified to the
   executor process's fd 2 (stderr tee) or fd 1 (stdout tee) before/while being
   captured. Relay uses a `select()` loop over the active masters plus the
   side-channel read end; EIO from a master after child exit is normal EOF.
3. **Tail cap**: each capture buffer keeps only the last `stderr_capture_bytes`
   BYTES (cap applied to raw bytes, before decoding).
4. **Hygiene** (applied to the capped raw bytes, in order):
   a. strip ANSI/CSI sequences (`ESC [ ...` params + final byte), OSC
      sequences (`ESC ] ...` terminated by BEL or `ESC \`), and remaining
      lone `ESC`+single-char sequences;
   b. decode UTF-8 with `errors="replace"`;
   c. remove all carriage returns (`\r`).
   Result → `stderr_text` / `stdout_text`.
5. **Window size**: at spawn, copy the winsize from the executor's fd 2 via
   `TIOCGWINSZ` if `os.isatty(2)`, else use rows=24, cols=80; apply to every
   PTY slave via `TIOCSWINSZ`. Install a SIGWINCH handler for the child's
   lifetime that re-copies (restore the previous handler after reaping).
6. **isatty**: the child must see `isatty(2) == True` always, and
   `isatty(1) == True` when `capture_stdout=True`.
7. Side channel, exit-code/signal mapping, cwd/env tracking, process group,
   and `check_syntax` behavior are UNCHANGED from Task 1.3.

## Constraints
- Only `src/sasha/executor.py` may change (plus optional tests under
  `tests/impl/`). Public signatures of §5.1 unchanged except pinned item 1.
- Stdlib only.
- Do not touch: `scripts/`, `docs/superpowers/`, `CLAUDE.md`, `AGENTS.md`,
  `PLAN.md`, `ISSUE-TRACKING.md`, `README.md`, `.gitignore`, `tests/contract/`.

## Acceptance criteria
(encoded 1:1 in `tests/contract/test_task_1_4_executor_pty.py`)
1. Child sees `isatty(2)` true; with `capture_stdout=True`, `isatty(1)` true —
   `test_ac1_*`.
2. Child stderr bytes are relayed to the executor's stderr (fd 2) —
   `test_ac2_stderr_relayed_to_parent`.
3. `stderr_text` captured with ANSI/CSI/OSC stripped, no `\r`, no `ESC` —
   `test_ac3_*`.
4. Tail cap honors `stderr_capture_bytes` (last bytes win) —
   `test_ac4_tail_cap`.
5. `capture_stdout=True` captures AND relays stdout; default leaves
   `stdout_text == ""` — `test_ac5_*`.
6. PTY window size = parent's terminal size, or 24×80 without a tty —
   `test_ac6_winsize_propagated`.

## Standing instructions
- Your goal: make `tests/contract/test_task_1_4_executor_pty.py` pass while
  `tests/contract/test_task_1_3_executor_core.py` stays green
  (`PYTHONPATH=src python3 -m pytest tests/contract/test_task_1_3_executor_core.py tests/contract/test_task_1_4_executor_pty.py`).
- NEVER modify files under `tests/contract/` — the gate rejects the commit if
  you do. If a contract test seems wrong or unimplementable, stop and flag it;
  do not commit.
- You may add supplementary tests under `tests/impl/`.
- Flag unexpected state; do not create or modify anything outside the files
  listed in "What's new".
- Deliver exactly ONE new commit on top of the HEAD you started from; never
  amend, rebase, or reset; leave the working tree clean.
- Commit message per AGENTS.md, with trailers:
  `Done: Task 1.4 — <one line>` and `Next: Task 1.5 — Trigger engine`.
