# Task 1.3: Executor core (subprocess, exit codes, side channel)

## Authoritative contract
`docs/superpowers/specs/2026-07-14-sasha-design.md` §5.1 (Executor/ExecResult
signatures) and §9.3 (cwd/env side channel) are the complete contract.
PTY tees (§9.1/§9.2) are **Task 1.4, not this task**: here stderr and stdout
are simply inherited from the parent, and `stderr_text`/`stdout_text` are `""`.

## What already exists
- `src/sasha/` package with `config.py`, `mutes.py` (Tasks 1.1–1.2) — this task
  imports none of them; `executor.py` is stdlib-only and standalone.
- `tests/contract/test_task_1_3_executor_core.py` — executable acceptance criteria.

## What's new
- `src/sasha/executor.py` — per §5.1:
  - `ExecResult(exit_code, signal, stderr_text, stdout_text, cwd_after,
    duration_ms)` — **frozen** dataclass.
  - `Executor(cwd: str, env: dict[str, str])` with property `.cwd`,
    `run(line: str, capture_stdout: bool = False) -> ExecResult`, and
    `@staticmethod check_syntax(line: str) -> str | None`.

## Pinned semantics (decisions already made — implement exactly these)
1. **Execution**: `run(line)` spawns `/bin/bash -c <wrapped line>` with
   stdin/stdout/stderr inherited from the parent process, cwd = the executor's
   tracked cwd, env = the executor's tracked env (plus `SASHA_STATE_FD`, below).
2. **Own process group**: the child is spawned in its own process group
   (`preexec_fn`-style `os.setpgid(0, 0)` or `start_new_session`) so signals
   for the child never hit Sasha. While waiting, the parent ignores SIGINT and
   restores the previous handler afterward.
3. **Exit codes and signals**: after `os.waitpid`, use
   `os.waitstatus_to_exitcode`. Normal exit N → `exit_code = N, signal = None`.
   Death by signal N → `exit_code = 128 + N, signal = N`.
4. **Side channel** (§9.3): before spawn, create `os.pipe()`; pass the write
   end to the child (`pass_fds`) and export `SASHA_STATE_FD=<fd>`. The wrapped
   line is:
   `trap 'pwd -P >&$SASHA_STATE_FD; printf "\0" >&$SASHA_STATE_FD; export -p >&$SASHA_STATE_FD' EXIT; <user line>`
   (fd number interpolated by the executor; the trap string is one line).
   Close the write end in the parent after spawn; read the read end to EOF
   after the child exits.
5. **Report parsing**: bytes before the first NUL = final cwd (stripped);
   after the NUL, parse only `declare -x NAME="VALUE"` lines from `export -p`
   (values unescaped via `shlex.split` semantics on the `NAME="VALUE"` part;
   `declare -x NAME` lines without `=` are skipped). The parsed map REPLACES
   the tracked env, except: `SASHA_STATE_FD`, `SHLVL`, `_`, `PWD`, `OLDPWD`
   are dropped from the parsed report before replacing.
6. **No report** (empty side-channel read — e.g. SIGKILL): keep the previous
   cwd and env unchanged.
7. **`cwd_after`**: the executor's cwd after the run (== new tracked cwd, or
   the unchanged previous one). `.cwd` always returns an absolute path;
   `pwd -P` output is physical, symlink-free.
8. **`check_syntax(line)`**: run `/bin/bash -n -c <line>` (no trap wrapping,
   nothing executed). Exit 0 → `None`; non-zero → bash's stderr text
   (non-empty, stripped).
9. **`duration_ms`**: wall-clock milliseconds (int ≥ 0) from just before spawn
   to reaped child.
10. **In this task** `stderr_text = ""` and `stdout_text = ""` always
    (`capture_stdout` accepted but captures nothing yet — Task 1.4 fills both).

## Constraints
- `src/sasha/executor.py` imports stdlib only; no imports from other sasha modules.
- Do not modify any existing file. Only `src/sasha/executor.py` is created
  (plus optional tests under `tests/impl/`).
- Do not touch: `scripts/`, `docs/superpowers/`, `CLAUDE.md`, `AGENTS.md`,
  `PLAN.md`, `ISSUE-TRACKING.md`, `README.md`, `.gitignore`, `tests/contract/`.

## Acceptance criteria
(encoded 1:1 in `tests/contract/test_task_1_3_executor_core.py`)
1. Exit codes: `true` → 0, `exit 7` → 7, unknown command → 127; result fields
   typed per §5.1; `ExecResult` is frozen — `test_ac1_*`.
2. Signal death: `kill -9 $$` → `signal == 9`, `exit_code == 137` —
   `test_ac2_signal_death_reports_signal_and_128n`.
3. `cd` persists across runs; `.cwd`/`cwd_after` reflect it (physical paths) —
   `test_ac3_cd_persists_across_runs`.
4. `export`/`unset` persist across runs, including across a failing line —
   `test_ac4_*`.
5. SIGKILL (no report) leaves cwd and env unchanged —
   `test_ac5_sigkill_preserves_cwd_and_env`.
6. `check_syntax`: error text for bad syntax, `None` for good, executes
   nothing either way — `test_ac6_*`.
7. The child runs in its own process group — `test_ac7_child_in_own_process_group`.

## Standing instructions
- Your goal: make `tests/contract/test_task_1_3_executor_core.py` pass
  (`PYTHONPATH=src python3 -m pytest tests/contract/test_task_1_3_executor_core.py`).
- NEVER modify files under `tests/contract/` — the gate rejects the commit if
  you do. If a contract test seems wrong or unimplementable, stop and flag it;
  do not commit.
- You may add supplementary tests under `tests/impl/`.
- Flag unexpected state; do not create or modify anything outside the files
  listed in "What's new".
- Deliver exactly ONE new commit on top of the HEAD you started from; never
  amend, rebase, or reset; leave the working tree clean.
- Commit message per AGENTS.md, with trailers:
  `Done: Task 1.3 — <one line>` and
  `Next: Task 1.4 — Executor PTY tees (stderr always, stdout opt-in)`.
