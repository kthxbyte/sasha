# Task 11.2: bash-completion subprocess integration + graceful degradation

## Authoritative contract
`docs/superpowers/specs/2026-07-16-tab-completion-design.md` is the
complete design record for this task — read it first, especially §4
(interface — the three functions this task adds), §5.1 (the exact
subprocess script, already validated interactively), §5.2 (dispatch
order), and the "Task 11.2 changes only the `else` branch" code block in
§4.

## What already exists
- `src/sasha/completion.py` (Task 11.1) — `complete_first_word`,
  `complete_builtin_arg`, `words_and_cword`. This task adds three more
  functions to the same file; none of the existing three change.
- `src/sasha/__main__.py`'s `_completer` closure (Task 11.1) — currently:
  ```python
  else:
      builtin = completion.complete_builtin_arg(
          words, cword, list(config.profiles), mutes.muted_commands())
      _last_matches = builtin if builtin is not None else []
  ```
  This task changes ONLY the `_last_matches = builtin if builtin is not
  None else []` line, per design §4's "Task 11.2 changes only the `else`
  branch" block — everything else in `_completer` (the `cword == 0`
  branch, the caching via `_last_line`, the `readline.set_completer`/
  `parse_and_bind` calls) is untouched.
- `/usr/share/bash-completion/bash_completion` and
  `/usr/share/bash-completion/completions/*` — the real, installed
  bash-completion package this task drives. Validated interactively at
  design time: `git chec` → `checkout`, `git checkout ma` → `main`,
  `cat PLAN` → `PLAN.md`, unregistered commands fall back to
  bash-completion's own `_minimal` (itself correct for filenames).

## What's new
Three more functions in `src/sasha/completion.py`, exact behavior per
design §4/§5.1:

```python
def complete_via_bash(words: list[str], cword: int,
                       timeout_s: float = 0.5) -> list[str]:
    """Spawns bash, sources bash-completion, drives the registered (or
    _minimal-fallback) completer for `words`/`cword`, returns COMPREPLY
    lines. Empty list on subprocess.TimeoutExpired or any OSError — never
    raises. See design §5.1 for the exact script text."""

def bash_completion_available() -> bool:
    """shutil.which('bash') is not None and
    pathlib.Path('/usr/share/bash-completion/bash_completion').exists()."""

def complete_filenames(prefix: str) -> list[str]:
    """glob.glob(prefix + '*'), each match suffixed '/' if
    os.path.isdir(match). Sorted."""
```

Change to `src/sasha/__main__.py` — replace the one line noted above with
the three-way branch from design §4's "Task 11.2 changes" block:
```python
if builtin is not None:
    _last_matches = builtin
elif _bash_ok:
    _last_matches = completion.complete_via_bash(words, cword)
else:
    _last_matches = completion.complete_filenames(text)
```
`_bash_ok = completion.bash_completion_available()` computed once,
alongside the existing completer setup, before `repl.loop()` is called
(same place `_last_line`/`_last_matches` are already initialized).

## Constraints
- Modify `src/sasha/completion.py` (add three functions only — do not
  change `complete_first_word`, `complete_builtin_arg`, or
  `words_and_cword`) and `src/sasha/__main__.py` (the one branch replacement
  plus the `_bash_ok` line — nothing else in that file changes).
- Do not touch: `scripts/`, `docs/superpowers/`, `CLAUDE.md`, `AGENTS.md`,
  `PLAN.md`, `ISSUE-TRACKING.md`, `README.md`, `.gitignore`,
  `pyproject.toml`, `src/sasha/__init__.py`, `src/sasha/config.py`,
  `src/sasha/mutes.py`, `src/sasha/triggers.py`, `src/sasha/session.py`,
  `src/sasha/agent.py`, `src/sasha/llm.py`, `src/sasha/executor.py`,
  `src/sasha/repl.py`, `src/sasha/prompt.py`, `tests/contract/`.
- `complete_via_bash()` must never raise out of the function — wrap the
  `subprocess.run(...)` call in `try/except (subprocess.TimeoutExpired,
  OSError)` returning `[]`, exactly as designed.
- No new dependencies — stdlib only (`subprocess`, `shlex`, `shutil`,
  `glob`, `pathlib` — all stdlib).
- Greenfield-vs-surgical routing: adds new functions to an existing file
  plus a small, precise edit to existing control flow in `__main__.py`.
  Dispatch to the default model (deepseek-v4-pro); do not use a
  `SASHA_DISPATCH_MODEL` local-model override.

## Acceptance criteria
(encoded in `tests/contract/test_task_11_2_completion_bash_integration.py`)
1. `complete_via_bash(["git", "chec"], 1)` returns a list containing
   `"checkout"`; `complete_via_bash(["git", "checkout", "ma"], 2)` returns
   a list containing `"main"` (this repo's default branch); `complete_via_bash(["cat", "PLAN"], 1)` returns a list containing
   `"PLAN.md"` (run with `cwd` set to the repo root) — `test_ac1_*`.
2. `complete_via_bash()` returns `[]` (not an exception) when
   `subprocess.run` raises `subprocess.TimeoutExpired` (monkeypatched) and
   when it raises `OSError` (e.g. `bash` not found, monkeypatched) —
   `test_ac2_*`.
3. `bash_completion_available()` returns `True` when both
   `shutil.which('bash')` and the bash-completion script path exist
   (monkeypatched to controlled values), `False` when either is missing
   — `test_ac3_*`.
4. `complete_filenames(prefix)` returns matching paths from a `tmp_path`
   fixture, directories suffixed with `/`, files not suffixed, sorted,
   empty list when nothing matches — `test_ac4_*`.
5. Existing Task 11.1 behavior (`complete_first_word`,
   `complete_builtin_arg`, `words_and_cword`, `MuteStore.muted_commands`)
   is completely unaffected — confirmed by
   `test_task_11_1_completion_own_logic.py` continuing to pass unmodified.

## Standing instructions
- Your goal: make the full contract suite pass:
  `PYTHONPATH=src python3 -m pytest tests/contract/`.
- NEVER modify files under `tests/contract/` — the gate rejects the
  commit if you do. This task's own contract tests run against the real
  bash-completion installation on this system (already confirmed present
  at spec time) — do not treat a failure there as license to change the
  test; if bash-completion behaves differently than expected, stop and
  flag it rather than editing `tests/contract/`.
- Deliver exactly ONE new commit on top of the HEAD you started from;
  never amend, rebase, or reset; leave the working tree clean.
- Commit message per AGENTS.md, with trailers:
  `Done: Task 11.2 — <one line>`, `Fixed: ISSUE-028`,
  `Next: Task 11.3 — Phase 11 review (Tier 1, with Salvador)`, and the
  Co-Authored-By signature line from your dispatch prompt.
