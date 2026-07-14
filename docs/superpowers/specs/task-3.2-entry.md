# Task 3.2: Entry point & terminal wiring

## Authoritative contract
`docs/superpowers/specs/2026-07-14-sasha-design.md` §9.5 (prompt, signals,
history), §10 (startup error line), §8.3 (readline history) are the complete
contract.

## What already exists (bind, never reimplement)
- `sasha.repl.Repl` / `TerminalConfirmer` (Task 3.1), `sasha.config.load` /
  `ConfigError` (1.1), `sasha.executor.Executor` (1.3/1.4),
  `sasha.mutes.MuteStore` (1.2), `sasha.session.Session` (2.2),
  `sasha.llm.ChatClient` (2.1).
- `tests/contract/test_task_3_2_entry.py` — executable acceptance criteria.

## What's new
- Rewrite `src/sasha/__main__.py` (replacing the Task 1.1 stub):
  `main(argv: list[str] | None = None) -> int`, CLI
  `sasha [--profile NAME] [--config PATH] [--version]` via argparse.

## Pinned semantics (decisions already made — implement exactly these)
1. `--version` → print exactly `sasha 0.1.0` to stdout, return 0 (before any
   config loading).
2. Config: `config.load(Path(--config))` or `load(None)`. `ConfigError` →
   print `sasha: config error: {message}` to STDERR, return 2. `--profile
   NAME` not in `config.profiles` → same treatment, message
   `unknown profile: {NAME}`.
3. Wiring: `Executor(os.getcwd(), dict(os.environ),
   stderr_capture_bytes=limits.stderr_capture_bytes)`;
   `MuteStore(<state_dir>/mutes.json, threshold=limits.mute_threshold)`;
   `Session(limits.history_tokens)`;
   `client_factory = lambda p: ChatClient(p)`;
   `TerminalConfirmer(input_fn, out)`; `Repl(...)` from Task 3.1 with
   `input_fn = input` and `out = print`.
   `<state_dir>` = `$XDG_STATE_HOME/sasha` when `XDG_STATE_HOME` is set and
   non-empty, else `~/.local/state/sasha` (created if missing).
4. **Prompt**: `executor.cwd` with a leading `$HOME` abbreviated to `~`,
   then ` ✦ ` — recomputed every iteration (cwd changes).
5. **History** (§8.3): at startup, `readline.read_history_file` from
   `<state_dir>/history` if present; every non-empty input line is
   `readline.add_history`'d (when not already, e.g. non-tty stdin); on clean
   exit (including EOF), `readline.set_history_length(1000)` +
   `readline.write_history_file` to the same path.
6. **EOF** (Ctrl-D / closed stdin) → clean exit, return 0. `KeyboardInterrupt`
   at the prompt → fresh prompt, keep running.
7. Exit code 0 on `exit`/`quit` too. No other output added by `__main__`.

## Constraints
- Modify `src/sasha/__main__.py` ONLY (plus optional tests under
  `tests/impl/`). Note: `tests/contract/test_task_1_1_config.py` keeps
  asserting `--version` and the pyproject console script — do not break them.
- Do not touch: `scripts/`, `docs/superpowers/`, `CLAUDE.md`, `AGENTS.md`,
  `PLAN.md`, `ISSUE-TRACKING.md`, `README.md`, `.gitignore`,
  `pyproject.toml`, any other `src/sasha/*.py`, `tests/contract/`.

## Acceptance criteria
(encoded 1:1 in `tests/contract/test_task_3_2_entry.py`, subprocess-driven)
1. `sasha --version` → `sasha 0.1.0`, exit 0 — `test_ac1_version`.
2. Malformed config → `sasha: config error: ...` on stderr, exit 2 —
   `test_ac2_bad_config`.
3. `--profile local` starts on the local profile (visible via
   `sasha profile`); unknown profile → config error, exit 2 — `test_ac3_*`.
4. Prompt renders `~` + ` ✦ ` when cwd is `$HOME` — `test_ac4_prompt`.
5. History written to `$XDG_STATE_HOME/sasha/history` on clean exit —
   `test_ac5_history_written`.
6. EOF exits 0 — `test_ac6_eof_exits_zero`.

## Standing instructions
- Your goal: make `tests/contract/test_task_3_2_entry.py` pass
  (`PYTHONPATH=src python3 -m pytest tests/contract/test_task_3_2_entry.py`).
- NEVER modify files under `tests/contract/` — the gate rejects the commit
  if you do. If a contract test seems wrong or unimplementable, stop and
  flag it; do not commit.
- Deliver exactly ONE new commit on top of the HEAD you started from; never
  amend, rebase, or reset; leave the working tree clean.
- Commit message per AGENTS.md, with trailers:
  `Done: Task 3.2 — <one line>`,
  `Next: Task 3.3 — End-to-end smoke + README`, and the Co-Authored-By
  signature line from your dispatch prompt.
