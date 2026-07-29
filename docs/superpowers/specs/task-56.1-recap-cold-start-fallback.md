# Task 56.1: Directory-recap cold-start fallback (marker-file content-sniff)

## Authoritative contract
`docs/superpowers/specs/2026-07-28-issue-056-recap-cold-start-design.md`
§3 (contracts #1-#5), §4 (all of §4.1-§4.3), §5 (constants) is the
complete contract for this task.

## What already exists to bind, not reimplement
- `src/sasha/directory_recap.py`: `decide()`, `gather_context()`,
  `build_prompt()`, `_is_git_repo`, `_safe_is_dir`, `_child_git_repos`,
  `_last_commit`, `_load_log_entries`, `_subtree_entries`, `_fanout`,
  `_parse_ts`, `_most_recent_age_days`, `escalate_async` — all unchanged
  in shape/signature. `_safe_is_dir`'s existing `PermissionError`/`OSError`
  handling pattern is what the new helpers below must follow.
- `SILENT`, `RECAP_DIRECT`, `ESCALATE` verdict constants — unchanged, no
  new verdict value.
- `MAX_CHILD_REPOS_FOR_LLM`, `MAX_LOG_ENTRIES_FOR_LLM`,
  `UMBRELLA_CHILD_REPO_THRESHOLD`, `FANOUT_THRESHOLD`, `STALE_DAYS` —
  unchanged.
- `src/sasha/repl.py`'s `_check_directory_recap()` and
  `_handle_recap_builtin()` — read-only reference, do not modify this
  file at all in this task. Both already call `directory_recap.decide()`
  / `directory_recap.gather_context()`/`build_prompt()` exactly as today;
  they need no changes since this task only changes what those functions
  return/consider internally.

## What's new
All changes confined to `src/sasha/directory_recap.py`.

1. **Four new module constants** (§5 of the design):
   ```python
   README_EXCERPT_CHARS = 500
   TOP_LEVEL_LISTING_MAX = 20

   MARKER_NAMES = {
       "package.json", "pyproject.toml", "setup.py", "cargo.toml", "go.mod",
       "gemfile", "pom.xml", "build.gradle", "makefile", "cmakelists.txt",
       "dockerfile", ".envrc", "requirements.txt", "composer.json",
   }
   MARKER_PREFIXES = ("readme", "license")

   VENDOR_DIR_NAMES = {
       "node_modules", "venv", "site-packages", "dist", "build", "target",
       "vendor", "__pycache__",
   }
   ```

2. **Four new helper functions** (place near `_child_git_repos`, same
   style — non-recursive, `PermissionError`/`OSError` caught and treated
   as "nothing found"):
   - `_has_project_marker(target: pathlib.Path) -> bool` — non-recursive
     `target.iterdir()`; `True` if any entry's name, lowercased, is in
     `MARKER_NAMES`, ends with `.sln`, or starts with any of
     `MARKER_PREFIXES`.
   - `_is_vendor_or_cache(target: pathlib.Path) -> bool` — `True` if any
     component of `target.resolve()`'s `.parts` starts with `.` or is
     exactly in `VENDOR_DIR_NAMES`. Use `target` itself (not resolved) if
     `.resolve()` raises `OSError`.
   - `_readme_excerpt(target: pathlib.Path) -> str | None` — non-recursive
     `target.iterdir()`, sorted by name; the first entry whose name
     lowercased starts with `"readme"` **and** is a file: return its text
     (`errors="replace"` on read) truncated to `README_EXCERPT_CHARS`.
     `None` if no such entry, directory unreadable, or the read fails.
   - `_top_level_listing(target: pathlib.Path) -> list[str]` —
     non-recursive `target.iterdir()`, entry names excluding any starting
     with `.`, sorted alphabetically, capped at `TOP_LEVEL_LISTING_MAX`.
     `[]` if unreadable.

3. **`decide()`'s new escalation branch** — exact change, at the existing
   `if not subtree:` block:
   ```python
   subtree = _subtree_entries(str(target), log_entries)
   if not subtree:
       if _has_project_marker(target) and not _is_vendor_or_cache(target):
           return (ESCALATE, None)
       return (SILENT, None)
   ```
   Nothing else in `decide()` changes.

4. **`gather_context()`'s new fallback keys** — after building the
   existing `result` dict (rename the returned dict to a local `result`
   variable if it's currently an inline `return {...}` — see exact
   diff below), before returning:
   ```python
   if not self_commit and not child_commits and not recent_commands:
       result["readme_excerpt"] = _readme_excerpt(target)
       result["top_level_listing"] = _top_level_listing(target)
   return result
   ```
   These two keys must be **absent** from the dict (not present as
   `None`/`[]`) whenever any of `self_commit`/`child_commits`/
   `recent_commands` is truthy.

5. **`build_prompt()`'s new conditional section** — appended after the
   existing `recent_commands` block, using `.get()` (the keys may be
   absent):
   ```python
   if context.get("readme_excerpt") or context.get("top_level_listing"):
       parts.append("Directory contents:")
       if context.get("readme_excerpt"):
           parts.append(f"  README excerpt: {context['readme_excerpt']}")
       if context.get("top_level_listing"):
           parts.append(f"  Files here: {', '.join(context['top_level_listing'])}")
   ```
   No change to the existing instruction preamble or any other section.

## Constraints
- Do not touch `src/sasha/repl.py`, `src/sasha/tool_log.py`,
  `src/sasha/llm.py`, or any file other than `src/sasha/directory_recap.py`.
- Do not add recursion anywhere — every new scan is a single
  non-recursive `.iterdir()` call on the target directory itself.
- Do not use `os.stat().st_mtime`, `st_ctime`, or any file-timestamp API
  anywhere in this task — the design explicitly rejects recency-based
  signals (see design doc §2 "Out of scope").
- Do not add a "count files by extension" rule of any kind — the design
  explicitly rejects this (measured 44% false-positive rate; see design
  doc's Decisions log).
- Do not add any `config.toml` surface for the new constants.
- Do not change the `SILENT`/`RECAP_DIRECT`/`ESCALATE` vocabulary or any
  existing function's signature.
- The vendor/cache exclusion (`_is_vendor_or_cache`) must be called **only**
  from `decide()`'s new branch — `gather_context()` must never call it
  (explicit `sasha recap` always content-sniffs regardless of vendor/cache
  status; see design contract #5).

## Acceptance criteria
1. `decide()` returns `ESCALATE` for a directory with a top-level marker
   file and empty `tool_log` subtree (no vendor/cache ancestor) —
   `test_ac1_marker_file_escalates_with_no_history`.
2. `decide()` returns `SILENT` for a directory with no marker file and no
   history — `test_ac2_no_marker_no_history_stays_silent`.
3. `decide()` returns `SILENT` for a marker-file-having directory under a
   dot-prefixed ancestor path component, despite the marker —
   `test_ac3_dot_prefixed_ancestor_stays_silent_despite_marker`.
4. `decide()` returns `SILENT` for a marker-file-having directory under a
   `VENDOR_DIR_NAMES` ancestor path component, despite the marker —
   `test_ac4_vendor_dir_ancestor_stays_silent_despite_marker`.
5. `decide()` returns `ESCALATE` for a directory that is a git repo, has a
   marker file, and has zero `tool_log` subtree entries (previously
   silently missed) — `test_ac5_git_repo_with_marker_and_no_subtree_escalates`.
6. `decide()` returns `SILENT` for a directory with several files matching
   common source-code extensions but no marker file and no history (proves
   no extension-counting rule exists) — `test_ac6_source_extensions_alone_stay_silent`.
7. `gather_context()` in the cold-start case (all three existing signals
   empty) returns a non-empty `readme_excerpt` (truncated to
   `README_EXCERPT_CHARS`) and a `top_level_listing` excluding hidden
   entries when a README exists; both empty/`None` when the directory has
   neither history nor a README —
   `test_ac7_gather_context_cold_start_readme_and_listing`.
8. `build_prompt()` includes a "Directory contents:" section only when
   `readme_excerpt`/`top_level_listing` are present in the context dict,
   and omits it when the existing three signals are populated instead —
   `test_ac8_build_prompt_includes_directory_contents_only_when_present`.
9. `gather_context()`'s content-sniff fires regardless of vendor/cache
   directory status (the vendor/cache exclusion is `decide()`-only) —
   `test_ac9_gather_context_ignores_vendor_gate`.

## Standing instructions
- Your goal: make the contract tests in
  `tests/contract/test_task_56_1_recap_cold_start.py` pass. All 9 already
  exist and are committed; 5 are red against the base you're starting
  from, 4 (AC2/AC3/AC4/AC6) already pass today by coincidence (the base
  code's unconditional `SILENT` on empty subtree happens to match) but
  encode real behavior your implementation must not break — read them
  as real constraints, not incidental noise.
- NEVER modify files under `tests/contract/` — the gate rejects the
  commit if you do. If a contract test seems wrong or unimplementable,
  stop and flag it.
- You may add your own supplementary tests under `tests/impl/`.
- Flag unexpected state; do not modify anything outside this task.
- Deliver exactly ONE new commit on top of the HEAD you started from;
  never amend, rebase, or reset; leave the working tree clean.
- Commit message must follow the task-commit trailer format in
  `CLAUDE.md`: `Done: Task 56.1 — <description>`, `Fixed: ISSUE-056`,
  `Next: Task 56.2 — Phase 56 review (Tier 1, with Salvador)`, and your
  `Co-Authored-By:` signature line exactly as given in the dispatch
  prompt.

## Verified fix sketch

Implemented and verified in a scratch worktree: 9/9 new contract tests
green, full suite 670/670 (deselecting two pre-existing, confirmed-
unrelated timing-race flakes in `test_task_3_1_repl.py` — logged as
ISSUE-057; reproduced identically with this task's own diff reverted in
the same worktree, so it is not caused by this change).

Constants and helpers, inserted after the existing threshold constants
and before `_is_git_repo`:
```python
README_EXCERPT_CHARS = 500
TOP_LEVEL_LISTING_MAX = 20

MARKER_NAMES = {
    "package.json", "pyproject.toml", "setup.py", "cargo.toml", "go.mod",
    "gemfile", "pom.xml", "build.gradle", "makefile", "cmakelists.txt",
    "dockerfile", ".envrc", "requirements.txt", "composer.json",
}
MARKER_PREFIXES = ("readme", "license")

VENDOR_DIR_NAMES = {
    "node_modules", "venv", "site-packages", "dist", "build", "target",
    "vendor", "__pycache__",
}
```

Helpers, inserted just before `_child_git_repos`:
```python
def _has_project_marker(target: pathlib.Path) -> bool:
    if not _safe_is_dir(target):
        return False
    try:
        entries = list(target.iterdir())
    except (PermissionError, OSError):
        return False
    for entry in entries:
        name = entry.name.lower()
        if name in MARKER_NAMES or name.endswith(".sln"):
            return True
        if name.startswith(MARKER_PREFIXES):
            return True
    return False


def _is_vendor_or_cache(target: pathlib.Path) -> bool:
    try:
        resolved = target.resolve()
    except OSError:
        resolved = target
    return any(part.startswith(".") or part in VENDOR_DIR_NAMES
               for part in resolved.parts)


def _readme_excerpt(target: pathlib.Path) -> str | None:
    if not _safe_is_dir(target):
        return None
    try:
        entries = sorted(target.iterdir(), key=lambda p: p.name)
    except (PermissionError, OSError):
        return None
    for entry in entries:
        if entry.name.lower().startswith("readme"):
            try:
                if entry.is_file():
                    return entry.read_text(errors="replace")[:README_EXCERPT_CHARS]
            except OSError:
                return None
    return None


def _top_level_listing(target: pathlib.Path) -> list[str]:
    if not _safe_is_dir(target):
        return []
    try:
        entries = [p.name for p in target.iterdir() if not p.name.startswith(".")]
    except (PermissionError, OSError):
        return []
    return sorted(entries)[:TOP_LEVEL_LISTING_MAX]
```

`decide()` diff (only the empty-subtree branch changes):
```python
    subtree = _subtree_entries(str(target), log_entries)
    if not subtree:
        if _has_project_marker(target) and not _is_vendor_or_cache(target):
            return (ESCALATE, None)
        return (SILENT, None)
```

`gather_context()` diff (replaces the final `return {...}`):
```python
    result = {
        "target_name": target.name,
        "self_commit": self_commit,
        "child_commits": child_commits,
        "recent_commands": recent_commands,
    }
    if not self_commit and not child_commits and not recent_commands:
        result["readme_excerpt"] = _readme_excerpt(target)
        result["top_level_listing"] = _top_level_listing(target)
    return result
```

`build_prompt()` diff (appended after the existing `recent_commands`
block, before `return "\n".join(parts)`):
```python
    if context.get("readme_excerpt") or context.get("top_level_listing"):
        parts.append("Directory contents:")
        if context.get("readme_excerpt"):
            parts.append(f"  README excerpt: {context['readme_excerpt']}")
        if context.get("top_level_listing"):
            parts.append(f"  Files here: {', '.join(context['top_level_listing'])}")
```

**Note on AC2/AC3/AC4/AC6 (already covered above in Standing
instructions):** these four contract tests pass against the unmodified
base too, since the base's unconditional `SILENT` on an empty subtree
coincidentally matches the correct post-fix outcome for those specific
inputs (no marker, or marker-but-excluded). They are not vacuous: an
implementation that adds the marker check but omits the vendor/cache
exclusion (or omits it incorrectly) will fail AC3/AC4 specifically. Do
not treat their pre-existing pass as license to skip
`_is_vendor_or_cache` or get it wrong.

**Note on ISSUE-057 (test infrastructure, not this task's code):**
`test_task_3_1_repl.py::test_ac2_empty_accepts_pending_offer` and
`::test_ac5_127_auto_consults` may occasionally show 2 requests instead
of 1 — a pre-existing, confirmed-unrelated timing race between
`escalate_async()`'s background thread and those tests' synchronous
assertions (root-caused and logged as ISSUE-057; reproduces identically
with this task's diff absent). If the gate's `TEST_CMD` run hits this,
it is not an issue with your implementation — flag it, do not attempt to
fix `test_task_3_1_repl.py` (out of scope, and it's a test file, not
under your control regardless).
