# Task 20.1: Pager keystroke fix (SIGTTIN ignore + stop-cause classification)

## Authoritative contract
`docs/superpowers/specs/2026-07-20-issue-040-pager-keystrokes-design.md`
(all sections) is the complete contract for this task. It amends
`docs/superpowers/specs/2026-07-14-sasha-v2-job-control-design.md` §4.1,
§4.2. This task fixes ISSUE-040 (a foreground interactive program that
reads raw keystrokes — e.g. `less`, invoked directly or via `git log` —
never receives them; arrow keys and `q` are echoed raw and do nothing; only
Ctrl+Z escapes) by ignoring `SIGTTIN` in the child tree symmetrically with
the existing `SIGTTOU` ignore, and by classifying the cause of any detected
stop before deciding whether to kill the job.

## What already exists
- `src/sasha/executor.py` (Task 4.1) — the full job-control handoff this
  task extends: `tcsetpgrp` foreground handoff, the `SIGCHLD` self-pipe
  merged into the relay `select()` loop, the stderr-always/stdout-opt-in
  PTY tee, the cwd/env side-channel, `SIGWINCH` propagation,
  `_parse_export_p`, `check_syntax()`. **Bind to all of this — do not
  rewrite the relay/capture/side-channel/winsize/handoff-sequencing logic.**
  Only the lines named in the fix sketch below change.
- `tests/contract/test_task_4_1_job_control.py`,
  `tests/contract/test_task_1_3_executor_core.py`,
  `tests/contract/test_task_1_4_executor_pty.py` — MUST stay green
  unmodified. `test_task_4_1_job_control.py` in particular proves the real
  Ctrl+Z (SIGTSTP) path still kills the job exactly as before; nothing in
  it should change behavior.
- `tests/contract/pty_harness.py` — the existing PTY harness (Task 4.1),
  reused here unmodified. Read it to understand how
  `test_task_20_1_pager_keystrokes.py` drives real terminal/signal
  scenarios.

## What's new
Two changes, both confined to `Executor.run()` in `src/sasha/executor.py`:
1. `SIGTTIN` is set to `SIG_IGN` before fork, alongside the existing
   `SIGTTOU` ignore, and restored at both existing restore points
   (normal end-of-`run()` cleanup, and the `tcsetpgrp` `OSError`
   degrade-to-non-interactive fallback).
2. The `chld_read` branch's stop handling inspects `os.WSTOPSIG(wstatus)`:
   `SIGTTIN`/`SIGTTOU` → resume the process group (`SIGCONT`) and keep
   waiting, without touching `stopped`/`child_done`; any other stop signal
   (`SIGTSTP`, `SIGSTOP`) → unchanged from Task 4.1 (`stopped = True`, job
   killed).

No interface changes: `Executor.run()`'s signature, `ExecResult`'s fields,
and `repl.py` are all untouched.

## Fix sketch (authoritative)

Four isolated edits to the existing `Executor.run()` in
`src/sasha/executor.py`. Everything else in the file is unchanged. Apply
exactly these edits (each shown as the existing lines being replaced):

**Edit 1** — variable init, in the "job-control setup (interactive only)"
block:

```python
        # --- job-control setup (interactive only) --------------------------
        sasha_pgid = None
        old_sigttou = None
        old_sigttin = None
        chld_read = None
        chld_write = None
        old_sigchld = None
```

**Edit 2** — install the ignore, in the `if interactive:` setup block:

```python
        if interactive:
            sasha_pgid = os.getpgrp()
            old_sigttou = signal.signal(signal.SIGTTOU, signal.SIG_IGN)
            old_sigttin = signal.signal(signal.SIGTTIN, signal.SIG_IGN)
            chld_read, chld_write = os.pipe()
            os.set_blocking(chld_write, False)
            old_sigchld = signal.signal(signal.SIGCHLD, _on_sigchld)
```

**Edit 3** — restore on the `tcsetpgrp` `OSError` degrade fallback:

```python
        if interactive:
            try:
                os.tcsetpgrp(0, pid)
            except OSError:
                # No real controlling terminal despite isatty(0) — degrade to
                # the non-interactive path for the rest of this call.
                signal.signal(signal.SIGTTOU, old_sigttou)
                signal.signal(signal.SIGTTIN, old_sigttin)
                signal.signal(signal.SIGCHLD, old_sigchld)
                os.close(chld_read)
                os.close(chld_write)
                chld_read = None
                chld_write = None
                interactive = False
```

**Edit 4** — stop-cause classification, in the `chld_read` branch of the
relay loop's `for fd in readable:`:

```python
                    elif fd == chld_read:
                        try:
                            os.read(chld_read, 4096)
                        except OSError:
                            pass
                        wpid, wstatus = os.waitpid(
                            pid, os.WNOHANG | os.WUNTRACED)
                        if wpid == 0:
                            continue
                        if os.WIFSTOPPED(wstatus):
                            stopsig = os.WSTOPSIG(wstatus)
                            if stopsig in (signal.SIGTTIN, signal.SIGTTOU):
                                # Protective stop, not a user Ctrl+Z — resume
                                # transparently, the job is not being killed.
                                try:
                                    os.killpg(pid, signal.SIGCONT)
                                except OSError:
                                    pass
                                continue
                            stopped = True
                        else:
                            already_reaped = True
                            reaped_status = wstatus
                        child_done = True
                        break
```

**Edit 5** — restore on normal end-of-`run()` cleanup:

```python
        if interactive:
            try:
                os.tcsetpgrp(0, sasha_pgid)
            except OSError:
                pass
            signal.signal(signal.SIGTTOU, old_sigttou)
            signal.signal(signal.SIGTTIN, old_sigttin)
            signal.signal(signal.SIGCHLD, old_sigchld)
            os.close(chld_read)
            os.close(chld_write)
```

This has been verified directly (Tier 1 applied it to a scratch copy of
`executor.py` and ran the full contract suite — 496/496 tests green,
including the 2 new ACs below and all of `test_task_4_1_job_control.py`
unmodified) before being committed as authoritative.

## Constraints
- Modify `src/sasha/executor.py` only (plus optional supplementary tests
  under `tests/impl/`).
- Do not touch: `scripts/`, `docs/superpowers/`, `CLAUDE.md`, `AGENTS.md`,
  `PLAN.md`, `ISSUE-TRACKING.md`, `README.md`, `.gitignore`,
  `pyproject.toml`, `src/sasha/__init__.py`, `src/sasha/repl.py`,
  `src/sasha/config.py`, `src/sasha/mutes.py`, `src/sasha/triggers.py`,
  `tests/contract/`.
- **Executor routing (per CLAUDE.md's standing rule): this is a surgical
  edit to code that already works, not a greenfield module. Dispatch to
  the default model (deepseek-v4-pro) — do not launch under a local-model
  `SASHA_DISPATCH_MODEL` override.**

## Acceptance criteria
(encoded 1:1 in `tests/contract/test_task_20_1_pager_keystrokes.py`)
1. `SIGTTIN` is `SIG_IGN` in the child tree (inherited through `bash -c`
   and anything it forks) —
   `test_ac1_sigttin_ignored_in_child`.
2. A stop whose cause is `SIGTTIN` (simulated directly, independent of AC1,
   by having the wrapped bash builtin-reset its own disposition to
   `SIG_DFL` and signal itself) is resumed rather than killing the job —
   `test_ac2_sigttin_stop_does_not_kill_job`.
3. A real user Ctrl+Z (`SIGTSTP`) still kills the job exactly as Task 4.1
   specified: no new test, verified by
   `tests/contract/test_task_4_1_job_control.py::test_ac2_ctrl_z_terminates_and_reaps`
   staying green unmodified as part of this task's own gate.

## Standing instructions
- Your goal: make `tests/contract/test_task_20_1_pager_keystrokes.py` pass
  with `tests/contract/test_task_1_3_executor_core.py`,
  `tests/contract/test_task_1_4_executor_pty.py`, and
  `tests/contract/test_task_4_1_job_control.py` still green:
  `PYTHONPATH=src python3 -m pytest tests/contract/test_task_1_3_executor_core.py tests/contract/test_task_1_4_executor_pty.py tests/contract/test_task_4_1_job_control.py tests/contract/test_task_20_1_pager_keystrokes.py`.
- NEVER modify files under `tests/contract/` — the gate rejects the commit
  if you do. If a contract test seems wrong or unimplementable, stop and
  flag it; do not commit.
- The fix sketch above is authoritative and has already been verified to
  pass the full suite — implement it exactly as given. If you believe a
  deviation is necessary, flag it in the commit message via `Deferred:`
  rather than silently diverging.
- Deliver exactly ONE new commit on top of the HEAD you started from; never
  amend, rebase, or reset; leave the working tree clean.
- Commit message per AGENTS.md, with trailers:
  `Done: Task 20.1 — <one line>`, `Fixed: ISSUE-040`,
  `Next: Task 20.2 — Phase 20 review (Tier 1, with Salvador)`, and the
  Co-Authored-By signature line from your dispatch prompt.
