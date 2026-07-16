# Task 12.1: readline delims + Executor.cwd threading for path completion

## Authoritative contract
`issues/ISSUE-029-path-completion-broken.md` is the complete design
record for this task — read it first. Two independent, confirmed root
causes, both fixed by this task:
1. `readline`'s default completer delimiters include `/`, splitting the
   "current word" at the last slash and breaking any path completion
   containing `/` (absolute paths, `dir/file`-style relative paths).
2. `complete_via_bash`/`complete_filenames` never receive Sasha's tracked
   `Executor.cwd`, so they always operate from wherever Sasha was
   *launched*, not wherever the user has since `cd`'d to (`Executor`'s
   `os.chdir` only runs inside each forked child, never in the main
   process).

## What already exists
- `src/sasha/completion.py`: `complete_via_bash(words, cword,
  timeout_s=0.5)` and `complete_filenames(prefix)` (Task 11.2) — this
  task adds a `cwd` parameter to both, default `None` (preserving current
  behavior when omitted, so existing Task 11.2 contract tests that don't
  pass `cwd` keep working unmodified). `complete_first_word`,
  `complete_builtin_arg`, `words_and_cword`, `bash_completion_available`
  are unchanged — no cwd dependency in any of them.
- `src/sasha/__main__.py`'s `_completer` closure and the `readline.set_completer(...)`/
  `readline.parse_and_bind("tab: complete")` calls (Task 11.1/11.2) — this
  task adds one new `readline.set_completer_delims(...)` call alongside
  them, and threads `executor.cwd` (the `Executor` instance already
  constructed earlier in `main()`) into the two call sites that invoke
  `completion.complete_via_bash`/`completion.complete_filenames`.
- `src/sasha/executor.py` `Executor.cwd` (a property, read-only from
  outside) — not modified by this task; this task only *reads* it.

## What's new
`src/sasha/completion.py` changes:
```python
def complete_via_bash(words: list[str], cword: int, timeout_s: float = 0.5,
                       cwd: str | None = None) -> list[str]:
    ...
    try:
        r = subprocess.run(["bash", "-c", script], capture_output=True,
                            text=True, timeout=timeout_s, cwd=cwd)
    except (subprocess.TimeoutExpired, OSError):
        return []
    ...  # rest unchanged
```

```python
def complete_filenames(prefix: str, cwd: str | None = None) -> list[str]:
    pattern = os.path.join(cwd, prefix) if cwd else prefix
    is_abs = os.path.isabs(prefix)
    matches = glob.glob(pattern + "*")
    results = []
    for m in matches:
        display = m if (is_abs or not cwd) else os.path.relpath(m, cwd)
        results.append(display + "/" if os.path.isdir(m) else display)
    return sorted(results)
```
(`os.path.join(cwd, prefix)` already discards `cwd` when `prefix` is
absolute — standard `os.path.join` behavior — so this one line correctly
handles both the relative and absolute cases without a branch.)

`src/sasha/__main__.py` changes:
- Add `readline.set_completer_delims(" \t\n")` immediately before (or
  after — either is fine, order between these two setup calls doesn't
  matter) `readline.set_completer(_completer)` /
  `readline.parse_and_bind("tab: complete")`.
- In `_completer`'s `elif _bash_ok:` / `else:` branches, pass
  `cwd=executor.cwd`:
  ```python
  elif _bash_ok:
      _last_matches = completion.complete_via_bash(words, cword, cwd=executor.cwd)
  else:
      _last_matches = completion.complete_filenames(text, cwd=executor.cwd)
  ```

## Constraints
- Modify `src/sasha/completion.py` (add the `cwd` parameter to
  `complete_via_bash` and `complete_filenames` only — no other function
  changes) and `src/sasha/__main__.py` (the delimiter call plus the two
  `cwd=executor.cwd` additions only — nothing else in the completer
  wiring changes).
- Do not touch: `scripts/`, `docs/superpowers/`, `CLAUDE.md`, `AGENTS.md`,
  `PLAN.md`, `ISSUE-TRACKING.md`, `README.md`, `.gitignore`,
  `pyproject.toml`, `src/sasha/__init__.py`, `src/sasha/config.py`,
  `src/sasha/mutes.py`, `src/sasha/triggers.py`, `src/sasha/session.py`,
  `src/sasha/agent.py`, `src/sasha/llm.py`, `src/sasha/executor.py`,
  `src/sasha/repl.py`, `src/sasha/prompt.py`, `tests/contract/`.
- `cwd` defaults to `None` on both functions — existing Task 11.2
  contract tests that call them without `cwd` must keep passing
  unmodified (this is the backward-compatibility mechanism, not an
  incidental detail).
- No new dependencies — stdlib only.
- Greenfield-vs-surgical routing: precise, small edits to existing
  control flow in code that already works. Dispatch to the default model
  (deepseek-v4-pro); do not use a `SASHA_DISPATCH_MODEL` local-model
  override.

## Acceptance criteria
(encoded in `tests/contract/test_task_12_1_completion_path_fixes.py`)
1. `complete_via_bash(["cat", "PLAN"], 1, cwd=<some other dir>)` finds
   nothing relevant there, but `complete_via_bash(["cat", "PLAN"], 1,
   cwd=<repo root>)` finds `"PLAN.md"` — proving `cwd` is actually passed
   to the subprocess, not ignored — `test_ac1_*`.
2. `complete_filenames(prefix, cwd=...)`: a relative `prefix` resolves
   against `cwd` and returns relative-to-`cwd` results (not absolute
   paths, not paths still relative to the real process cwd); an absolute
   `prefix` is unaffected by `cwd` (returns absolute results regardless
   of what `cwd` is set to); `cwd=None` behaves exactly as Task 11.2 left
   it (relative to the real process cwd) — `test_ac2_*`.
3. `readline.set_completer_delims(" \t\n")` is actually called during
   `__main__.py`'s setup — test by invoking `sasha` as a subprocess (or
   by directly asserting on `readline.get_completer_delims()` after
   importing/running the relevant setup code path) and confirming `/` is
   no longer in the delimiter set — `test_ac3_*`.
4. `words_and_cword()` (unchanged code, Task 11.1) correctly reconstructs
   an absolute path and a `dir/file`-style relative path as ONE word when
   given `begidx`/`text` values consistent with the *narrowed* delimiter
   set (i.e. the whole path in `text`, not just the substring after the
   last `/`) — confirms the two fixes compose correctly together, not
   just individually — `test_ac4_*`.
5. Existing Task 11.1/11.2 contract tests (`test_task_11_1_*.py`,
   `test_task_11_2_*.py`) continue to pass unmodified — confirms this
   task's `cwd=None` default preserves prior behavior exactly.

## Standing instructions
- Your goal: make the full contract suite pass:
  `PYTHONPATH=src python3 -m pytest tests/contract/`.
- NEVER modify files under `tests/contract/` — the gate rejects the
  commit if you do.
- Deliver exactly ONE new commit on top of the HEAD you started from;
  never amend, rebase, or reset; leave the working tree clean.
- Commit message per AGENTS.md, with trailers:
  `Done: Task 12.1 — <one line>`, `Fixed: ISSUE-029`,
  `Next: Task 12.2 — Phase 12 review (Tier 1, with Salvador)`, and the
  Co-Authored-By signature line from your dispatch prompt.
