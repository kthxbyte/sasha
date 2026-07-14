# Task 1.2: Mute store

## Authoritative contract
`docs/superpowers/specs/2026-07-14-sasha-design.md` §5.3 (MuteStore interface)
and §8.2 (JSON persistence format) are the complete contract.

## What already exists
- `src/sasha/config.py`, `src/sasha/__init__.py`, `src/sasha/__main__.py`,
  `pyproject.toml` (Task 1.1) — bind, do not modify. This task does not
  actually import any of them; `mutes.py` is stdlib-only and standalone.
- `tests/contract/test_task_1_2_mutes.py` — the executable acceptance criteria.

## What's new
- `src/sasha/mutes.py` — per §5.3:
  - `MuteStore(path: pathlib.Path, threshold: int = 3)`
  - `is_muted(command: str, exit_code: int) -> bool`
  - `record_ignored(command: str, exit_code: int) -> bool`
  - `record_acted(command: str, exit_code: int) -> None`
  - `mute(command: str, exit_code: int | None = None) -> None`
  - `unmute(command: str, exit_code: int | None = None) -> None`

## Pinned semantics (decisions already made — implement exactly these)
1. **Persistence file** (§8.2): JSON object `{"version": 1, "pairs": {...}}`;
   pair keys are `"<command>:<exit_code>"`; each entry is
   `{"muted": <bool>, "ignored_streak": <int>}`. Written on EVERY mutation
   (`record_ignored`, `record_acted`, `mute`, `unmute`) via atomic
   write-and-rename (temp file in the same directory, then `os.replace`).
   Parent directories are created if missing.
2. **Threshold crossing**: `record_ignored` increments the pair's streak. It
   returns `True` exactly when the increment moves the streak from
   `threshold - 1` to `threshold` on a not-yet-muted pair, and sets
   `muted = true` at that moment. Any call on an already-muted pair returns
   `False` (and the pair stays muted).
3. **`record_acted`** resets the pair's streak to 0. It does not change
   `muted`.
4. **All-exit-codes mute**: `mute(command, None)` stores a wildcard entry under
   key `"<command>:*"`. `is_muted(command, exit_code)` returns `True` if the
   exact pair is muted OR the command's wildcard entry is muted.
   `unmute(command, None)` unmutes the wildcard entry AND every
   `"<command>:<N>"` entry.
5. **`unmute`** sets `muted = false` and resets `ignored_streak` to 0 for the
   affected entries (a fresh start — the user asked for offers back).
   `mute`/`unmute` on unknown commands/pairs create/normalize entries; they
   never raise.
6. **Corrupt or unreadable state file** (bad JSON, wrong shape, unreadable):
   treated as an empty store, no exception; the next mutation overwrites the
   file with valid state.
7. Missing file at construction is normal (empty store); the file is only
   created on first mutation.

## Constraints
- `src/sasha/mutes.py` imports stdlib only; no imports from other sasha modules.
- Do not modify any existing file. Only `src/sasha/mutes.py` is created
  (plus optional tests under `tests/impl/`).
- Do not touch: `scripts/`, `docs/superpowers/`, `CLAUDE.md`, `AGENTS.md`,
  `PLAN.md`, `ISSUE-TRACKING.md`, `README.md`, `.gitignore`, `tests/contract/`.

## Acceptance criteria
(encoded 1:1 in `tests/contract/test_task_1_2_mutes.py`)
1. Three consecutive `record_ignored` for a pair mute it; only the third call
   returns `True`; a fourth returns `False`; other pairs unaffected; custom
   `threshold` honored — `test_ac1_*`.
2. `record_acted` resets the streak; muting then requires a full fresh streak —
   `test_ac2_acted_resets_streak`.
3. State (streaks and mute flags, including wildcard mutes) survives reopening
   the store at the same path — `test_ac3_*`.
4. The state file is valid JSON with the §8.2 schema after every mutation —
   `test_ac4_valid_json_after_each_mutation`.
5. A corrupt state file yields an empty working store without raising —
   `test_ac5_corrupt_file_treated_as_empty`.
6. `mute`/`unmute` with `exit_code=None` affect all exit codes; `unmute`
   restores offers (streak reset) — `test_ac6_*`.

## Standing instructions
- Your goal: make `tests/contract/test_task_1_2_mutes.py` pass
  (`PYTHONPATH=src python3 -m pytest tests/contract/test_task_1_2_mutes.py`).
- NEVER modify files under `tests/contract/` — the gate rejects the commit if
  you do. If a contract test seems wrong or unimplementable, stop and flag it;
  do not commit.
- You may add supplementary tests under `tests/impl/`.
- Flag unexpected state; do not create or modify anything outside the files
  listed in "What's new".
- Deliver exactly ONE new commit on top of the HEAD you started from; never
  amend, rebase, or reset; leave the working tree clean.
- Commit message per AGENTS.md, with trailers:
  `Done: Task 1.2 — <one line>` and
  `Next: Task 1.3 — Executor core (subprocess, exit codes, side channel)`.
