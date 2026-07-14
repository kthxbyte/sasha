# Task 1.5: Trigger engine

## Authoritative contract
`docs/superpowers/specs/2026-07-14-sasha-design.md` §5.2 (interface) and §6
(trigger matrix) are the complete contract.

## What already exists
- `src/sasha/executor.py` → `ExecResult` (Task 1.3) — consumed, not modified.
- `src/sasha/mutes.py` → `MuteStore` (Task 1.2) — consumed, not modified.
- `tests/contract/test_task_1_5_triggers.py` — executable acceptance criteria.

## What's new
- `src/sasha/triggers.py` — per §5.2:
  - `Decision(enum.Enum)` with members `AUTO_CONSULT = "auto_consult"`,
    `OFFER = "offer"`, `NOTHING = "nothing"`.
  - `decide(line: str, result: ExecResult, mutes: MuteStore) -> Decision`.
  - `command_word(line: str) -> str`.

## Pinned semantics (decisions already made — implement exactly these)
1. **`command_word`**: the FIRST token of `shlex.split(line)`; `""` when the
   line is empty, whitespace-only, or `shlex` raises `ValueError`.
2. **`decide` evaluation order** (first match wins):
   a. `result.signal is not None` → `NOTHING` (T5 — covers SIGINT/SIGPIPE and
      every other signal death; a signaled child is never an offer candidate);
   b. `exit_code == 0` → `NOTHING` (T6);
   c. `exit_code == 127` → `AUTO_CONSULT` (T1 — exempt from muting, no mute
      lookup at all);
   d. `mutes.is_muted(command_word(line), result.exit_code)` → `NOTHING` (T4);
   e. otherwise → `OFFER` (T3).
3. `decide` is pure apart from the `is_muted` read: it never mutates the mute
   store, never does I/O, never consults an LLM (§3.5). Syntax errors (T2) and
   `??` (T7) are REPL concerns — not handled here.

## Constraints
- `src/sasha/triggers.py` imports stdlib + `sasha.executor` (types) +
  `sasha.mutes` (types) only.
- Do not modify any existing file. Only `src/sasha/triggers.py` is created
  (plus optional tests under `tests/impl/`).
- Do not touch: `scripts/`, `docs/superpowers/`, `CLAUDE.md`, `AGENTS.md`,
  `PLAN.md`, `ISSUE-TRACKING.md`, `README.md`, `.gitignore`, `tests/contract/`.

## Acceptance criteria
(encoded 1:1 in `tests/contract/test_task_1_5_triggers.py`)
1. Exit 127 → `AUTO_CONSULT`, even when the pair (or the whole command) is
   muted — `test_ac1_*`.
2. Non-{0,127} exit, no signal: unmuted → `OFFER`; pair-muted or
   wildcard-muted → `NOTHING`; the mute lookup keys on `command_word(line)` —
   `test_ac2_*`.
3. Any signal death (SIGINT 130, SIGPIPE 141, SIGTERM 143) → `NOTHING` —
   `test_ac3_signal_deaths_are_nothing`.
4. Exit 0 → `NOTHING` — `test_ac4_success_is_nothing`.
5. `command_word` per pinned rule 1 — `test_ac5_*`.
6. Enum member values are the §5.2 strings — `test_ac6_decision_enum_values`.

## Standing instructions
- Your goal: make `tests/contract/test_task_1_5_triggers.py` pass
  (`PYTHONPATH=src python3 -m pytest tests/contract/test_task_1_5_triggers.py`).
- NEVER modify files under `tests/contract/` — the gate rejects the commit if
  you do. If a contract test seems wrong or unimplementable, stop and flag it;
  do not commit.
- You may add supplementary tests under `tests/impl/`.
- Flag unexpected state; do not create or modify anything outside the files
  listed in "What's new".
- Deliver exactly ONE new commit on top of the HEAD you started from; never
  amend, rebase, or reset; leave the working tree clean.
- Commit message per AGENTS.md, with trailers:
  `Done: Task 1.5 — <one line>` and
  `Next: Task 2.1 — Streaming chat client`.
