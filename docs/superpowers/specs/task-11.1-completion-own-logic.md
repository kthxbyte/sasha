# Task 11.1: Own-logic completion — PATH scan, sasha builtins, readline wiring

## Authoritative contract
`docs/superpowers/specs/2026-07-16-tab-completion-design.md` is the
complete design record for this task — read it first, especially §3
(key contracts), §4 (interface — this task implements everything except
`complete_via_bash`, `bash_completion_available`, `complete_filenames`,
which are Task 11.2), and §5.2-§5.3 (dispatch order, readline caching).

## What already exists
- `src/sasha/mutes.py` `MuteStore` — `_load()` (returns
  `{"version": 1, "pairs": {...}}`, keys formatted `f"{command}:{exit_code}"`
  or `f"{command}:*"`, values `{"muted": bool, "ignored_streak": int}`).
  This task adds one new method; every existing method (`is_muted`,
  `record_ignored`, `record_acted`, `mute`, `unmute`, `_load`, `_save`)
  is unchanged.
- `src/sasha/config.py` `Config.profiles` (`dict[str, Profile]`) — profile
  names are `list(config.profiles)`. Not modified by this task.
- `src/sasha/repl.py` — the four `sasha` verbs (`mute`, `unmute`,
  `profile`, `help`) live in `Repl.run_once`'s `elif verb == ...` chain.
  Not modified by this task — completion is entirely additive, driven
  from `__main__.py`, not from inside `Repl`.
- `src/sasha/__main__.py` — already `import readline`, already calls
  `readline.add_history`/`read_history_file`/`set_history_length`/
  `write_history_file`. This task adds `readline.set_completer(...)` +
  `readline.parse_and_bind("tab: complete")` alongside that existing
  setup, before `repl.loop()` is called (same ordering constraint the
  existing history-file read already follows).

## What's new
A new file, `src/sasha/completion.py`, with exactly these three
functions (signatures and behavior per design §4):

```python
def complete_first_word(prefix: str, path_dirs: list[str]) -> list[str]:
    """Executable file names (os.access(path, os.X_OK)) found directly
    inside any directory in path_dirs, whose name starts with `prefix`,
    deduplicated (a name present in multiple PATH dirs appears once),
    sorted."""

def complete_builtin_arg(words: list[str], cword: int, profile_names: list[str],
                          muted_commands: list[str]) -> list[str] | None:
    """See design §4 for the exact cword==1/2 rules. Filters internally
    by words[cword] (the current prefix). Returns None for anything not
    matching one of the documented cases."""

def words_and_cword(line: str, begidx: int, text: str) -> tuple[list[str], int]:
    """See design §4 for the exact reconstruction, including the
    cursor-starts-a-fresh-word case (cword must equal len(prior_words)
    regardless of whether `text` is empty)."""
```

Changes to `src/sasha/mutes.py` — one new method on `MuteStore`:
```python
def muted_commands(self) -> list[str]:
    """Command names with at least one muted (command, exit_code) pair,
    deduplicated, sorted."""
    data = self._load()
    return sorted({key.rsplit(":", 1)[0]
                   for key, v in data["pairs"].items() if v["muted"]})
```

Changes to `src/sasha/__main__.py` — per design §4's "Task 11.1's
version" code block exactly: a module-level (or `main()`-local, your
choice, matching the file's existing style) `_completer(text, state)`
closure with `_last_line`/`_last_matches` state, calling
`completion.words_and_cword`, then `completion.complete_first_word` when
`cword == 0`, else `completion.complete_builtin_arg` (empty list, not
`None`'s own special value, when it returns `None` — no bash-completion
fallback yet, that's Task 11.2), wired via `readline.set_completer(...)`
and `readline.parse_and_bind("tab: complete")` before `repl.loop()`.

## Constraints
- New file `src/sasha/completion.py`. Modify `src/sasha/mutes.py` (one
  new method only — do not change any existing method's behavior) and
  `src/sasha/__main__.py` (the completer wiring only — do not change
  argument parsing, config loading, or any other existing behavior).
- Do not touch: `scripts/`, `docs/superpowers/`, `CLAUDE.md`, `AGENTS.md`,
  `PLAN.md`, `ISSUE-TRACKING.md`, `README.md`, `.gitignore`,
  `pyproject.toml`, `src/sasha/__init__.py`, `src/sasha/config.py`,
  `src/sasha/triggers.py`, `src/sasha/session.py`, `src/sasha/agent.py`,
  `src/sasha/llm.py`, `src/sasha/executor.py`, `src/sasha/repl.py`,
  `src/sasha/prompt.py`, `tests/contract/`.
- Do NOT implement `complete_via_bash`, `bash_completion_available`, or
  `complete_filenames` — those are Task 11.2. `complete_builtin_arg`
  returning `None` means the completer closure falls back to an empty
  list for now (per design §2, Task 11.1 scope).
- No new dependencies — stdlib only (`os`, `shlex`, `readline` — all
  already used elsewhere in the project).
- Greenfield-vs-surgical routing: creates one new module but also
  modifies two existing files' control flow (`mutes.py`'s new method is
  additive/low-risk; `__main__.py`'s completer wiring touches real
  startup sequencing). Dispatch to the default model (deepseek-v4-pro);
  do not use a `SASHA_DISPATCH_MODEL` local-model override.

## Acceptance criteria
(encoded in `tests/contract/test_task_11_1_completion_own_logic.py`)
1. `complete_first_word(prefix, path_dirs)`: finds executables across
   multiple PATH directories, filters by prefix, deduplicates a name
   present in more than one directory, sorts results, ignores
   non-executable files and non-existent directories in `path_dirs` —
   `test_ac1_*`.
2. `words_and_cword(line, begidx, text)`: correctly reconstructs
   `(words, cword)` for a mid-word case (`"git chec"`, completing "chec"
   at word 1), a fresh-empty-word case (`"git "`, cursor right after the
   space, `text=""`, must be `cword==1` not `0`), a first-word case
   (`""`/partial first word, `cword==0`), and a case where `line[:begidx]`
   has an unterminated quote (falls back to `.split()`, doesn't raise) —
   `test_ac2_*`.
3. `complete_builtin_arg(words, cword, profile_names, muted_commands)`:
   `cword==1` and `words[0]=='sasha'` filters `{mute, unmute, profile,
   help}` by `words[1]`; `cword==2`, `words[0:2]==['sasha','profile']`
   filters `profile_names` by `words[2]`; same shape for `'unmute'` and
   `muted_commands`; returns `None` for `words[0] != 'sasha'`, for
   `cword` values outside `{1, 2}`, and for `words[1]` not one of the
   four verbs when `cword==2` — `test_ac3_*`.
4. `MuteStore.muted_commands()`: returns command names with at least one
   muted entry, deduplicated across multiple muted exit codes for the
   same command, sorted, empty list when nothing is muted, unaffected by
   *unmuted* (`muted: False`) entries — `test_ac4_*`. Every existing
   `MuteStore` method's behavior is unchanged (regression: existing
   `is_muted`/`mute`/`unmute`/`record_ignored`/`record_acted` tests, if
   any exist under `tests/contract/`, must still pass unmodified).
5. `Repl`/`Config`/existing `sasha` verb behavior is completely
   unaffected — this task adds a new file and one new read-only method,
   changes nothing about how existing commands execute. Confirmed by the
   full existing suite passing unmodified.

## Standing instructions
- Your goal: make the full contract suite pass:
  `PYTHONPATH=src python3 -m pytest tests/contract/`.
- NEVER modify files under `tests/contract/` — the gate rejects the
  commit if you do. No existing contract test should need to change for
  this task (it's purely additive: a new module, one new method, and
  wiring in a file no existing contract test exercises via `readline`
  directly).
- Deliver exactly ONE new commit on top of the HEAD you started from;
  never amend, rebase, or reset; leave the working tree clean.
- Commit message per AGENTS.md, with trailers:
  `Done: Task 11.1 — <one line>`, `Next: Task 11.2 — bash-completion
  subprocess integration + graceful degradation`, and the Co-Authored-By
  signature line from your dispatch prompt. (No `Fixed:` trailer — 
  ISSUE-028 isn't fully resolved until Task 11.2 also lands.)
