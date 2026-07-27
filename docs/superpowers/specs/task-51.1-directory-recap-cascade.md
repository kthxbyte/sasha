# Task 51.1: directory_recap.py cascade + repl.py trigger wiring

## Authoritative contract
`docs/superpowers/specs/2026-07-26-issue-051-directory-recap-design.md`
§3 (contracts #1–#6), §4.1–§4.6, §5 (the `enabled` flag only — the
toggle command itself is Task 51.2) is the complete contract for this
task. §4.7 (`sasha recap`) is explicitly OUT of scope here — Task 51.2.

## What already exists to bind, not reimplement
- `src/sasha/tool_log.py`: `_log_path()` (private) resolves
  `tool_log.jsonl`'s path (`$XDG_STATE_HOME/sasha/tool_log.jsonl` or
  `~/.local/state/sasha/tool_log.jsonl`). This task adds one new public
  function, `log_path() -> pathlib.Path`, that returns exactly what
  `_log_path()` already returns (a one-line wrapper — do not change
  `_log_path()` or any other existing behavior in this file).
- `src/sasha/llm.py`: `ChatClient.chat(messages, tools, on_text,
  on_reasoning=...)` returns `ChatResponse(text, tool_calls)`; raises
  `ChatError` on transport failure. Bind to this exactly as
  `repl.py:_bootstrap_stage_2` already does (`client.chat([...], [],
  lambda _: None)`) — do not modify `llm.py`.
- `src/sasha/repl.py`: `_check_place_shift` (line ~252), the existing
  git-repo/memory recap. Read but do not alter its recap logic — only
  change its **return value** (see "What's new" below) and add one new
  call site alongside it.
- `src/sasha/config.py`: `Config` (frozen dataclass), `_build_config`,
  `_DEFAULT_CONFIG_TOML`. Follow the exact pattern already used for
  `llm_debug_enabled` (the simplest existing boolean section — parse
  `raw.get("directory_recap", {})`, default `True`, override only if the
  table is a dict and its `enabled` key is a `bool`).

## What's new
1. **`src/sasha/tool_log.py`**: add
   ```python
   def log_path() -> pathlib.Path:
       return _log_path()
   ```
   placed immediately after `_log_path()`'s definition.

2. **`src/sasha/directory_recap.py`** (new file). Exact public surface:

   ```python
   SILENT = "silent"
   RECAP_DIRECT = "recap_direct"
   ESCALATE = "escalate"

   UMBRELLA_CHILD_REPO_THRESHOLD = 2
   FANOUT_THRESHOLD = 3
   STALE_DAYS = 14
   MAX_CHILD_REPOS_FOR_LLM = 5
   MAX_LOG_ENTRIES_FOR_LLM = 10

   def decide(target: pathlib.Path,
              log_entries: list[dict] | None = None) -> tuple[str, str | None]:
       """Stages 1-2 of the cascade (design §4.1-§4.4). Pure given
       log_entries; loads tool_log.log_path() itself when log_entries is
       None. Returns (SILENT, None), (RECAP_DIRECT, "<one line>"), or
       (ESCALATE, None)."""

   def gather_context(target: pathlib.Path,
                       log_entries: list[dict] | None = None) -> dict:
       """Stage 3 input-gathering (design §4.5), reused verbatim by
       Task 51.2's `sasha recap`. Returns:
       {
         "target_name": str,                       # target.name
         "self_commit": str | None,                 # "<age>\\t<subject>"
         "child_commits": list[tuple[str, str | None]],  # (dir name, "<age>\\t<subject>" or None), capped at MAX_CHILD_REPOS_FOR_LLM, sorted by directory name
         "recent_commands": list[tuple[str, str]],  # (command, ts), most recent first, capped at MAX_LOG_ENTRIES_FOR_LLM
       }
       """

   def build_prompt(context: dict) -> str:
       """Builds the one-shot prompt per design §4.5's prompt contract:
       instructs the model to produce exactly one line in the voice of
       the existing recap lines, from the gathered data only, or output
       nothing if there's nothing coherent to say. Must include every
       non-None/non-empty field of `context` in the prompt text."""

   def escalate_async(target: pathlib.Path, client_factory, profile,
                       on_result) -> threading.Thread:
       """Design §4.5's async execution. Starts and returns a daemon
       thread (unjoined) that: calls gather_context(target), then
       build_prompt(...), then client_factory(profile).chat([{"role":
       "user", "content": prompt}], [], lambda _: None). If this raises
       ChatError, swallow it silently (best-effort — a failed recap must
       never surface an error). Otherwise, if response.text.strip() is
       non-empty, call on_result(f"[sasha] {response.text.strip()}").
       If empty, call nothing."""
   ```

   Helper functions (names/signatures your choice, not part of the
   pinned public surface, but their *behavior* is pinned by the
   acceptance criteria below):
   - Filesystem: does a path directly contain its own `.git` (file or
     directory — this must return `True` for a git worktree, where
     `.git` is a file, not just a directory)? **Must catch `OSError`
     (including `PermissionError`) around every filesystem check in this
     helper and in the subdirectory-listing helper below, returning
     `False`/skipping the entry rather than raising** — real directories
     like `/tmp` routinely contain other-user/system-owned entries
     (e.g. `systemd-private-*` dirs) that raise `PermissionError` on
     `.exists()`/`.is_dir()`; this must never crash the cascade.
   - List a directory's immediate subdirectories (skip dotfile-prefixed
     names), and how many of them satisfy the check above. Same
     OSError-tolerance requirement applies here.
   - Run `git log -1 --format=%ar<TAB>%s` against a path (2s timeout);
     return `None` on any non-zero exit, timeout, or `OSError` (matches
     `_check_place_shift`'s existing tolerance for git being absent/slow
     — never raise out of this call).
   - Read+parse `tool_log.log_path()` into a list of dicts, skipping
     unparseable lines silently (same tolerance already used
     elsewhere in this codebase for this file, e.g. `tool_log.compact`).
   - Given a target path string and the full entries list, the subtree
     (entries whose `cwd` equals the target or starts with
     `target.rstrip("/") + "/"`), the fan-out (count of distinct first
     path segments below target among the subtree's `cwd` values), and
     the most-recent-entry age in days (parse each entry's `ts` field as
     `"%Y-%m-%dT%H:%M:%S.%fZ"` UTC, compare to `datetime.now(timezone.utc)`).

   **The exact cascade** (design §4.1, restated precisely — implement
   `decide()` to match this control flow exactly):
   ```
   child_repo_count = count of target's immediate subdirs containing .git
   self_is_repo = target itself contains .git

   if child_repo_count >= UMBRELLA_CHILD_REPO_THRESHOLD:
       return (ESCALATE, None) if self_is_repo else (SILENT, None)

   subtree = entries under target (see helper above)
   if not subtree:
       return (SILENT, None)
   if fanout(target, subtree) >= FANOUT_THRESHOLD:
       return (SILENT, None)
   if most_recent_age_days(subtree) > STALE_DAYS:
       return (SILENT, None)

   if self_is_repo:
       commit = last_commit(target)
       if commit is not None:
           age, subject = commit.split("\t", 1)
           return (RECAP_DIRECT, f'[sasha] {target.name}: last commit {age} — "{subject}"')
       return (ESCALATE, None)   # self is a repo, has activity, but no commits yet
   return (ESCALATE, None)
   ```

3. **`src/sasha/config.py`**: add `directory_recap_enabled: bool = True`
   to the `Config` dataclass; in `_build_config`, parse a `[directory_recap]`
   table exactly like `llm_debug_enabled` is parsed (a dict with an
   `enabled` bool key, default `True` on absence or bad type); pass it
   through to the `Config(...)` constructor call. Add to
   `_DEFAULT_CONFIG_TOML`:
   ```toml
   [directory_recap]
   enabled = true
   ```
   placed after the existing `[llm_debug]` block.

4. **`src/sasha/repl.py`**:
   - `Repl.__init__` gains `self._recapped_dirs: set[str] = set()` and
     `self._last_recap_thread = None` (test seam, mirroring
     `_last_bootstrap_thread`).
   - `_check_place_shift` gains an explicit `-> bool` return: `True` on
     every code path that prints the
     `[sasha] project: ... (N memories, last: "...")` line (design
     contract #4's "existing recap has something to say"); `False` on
     every other path (no git root, git root but zero memories,
     unchanged cwd/root, just-bootstrapped). Every existing early
     `return` becomes `return False`; the line printing the project
     recap becomes `... ; return True` (falling through the function's
     end, after the `if count and not just_bootstrapped:` block, must
     also `return False` when that condition is false). No other change
     to this method's existing behavior.
   - New method `_check_directory_recap(self) -> None`:
     ```python
     def _check_directory_recap(self):
         if not self._config.directory_recap_enabled:
             return
         cwd = self._executor.cwd
         if cwd in self._recapped_dirs:
             return
         self._recapped_dirs.add(cwd)
         verdict, text = directory_recap.decide(pathlib.Path(cwd))
         if verdict == directory_recap.SILENT:
             return
         if verdict == directory_recap.RECAP_DIRECT:
             self._sink.write_line(text)
             return
         on_result = getattr(self._sink, "write_async_line", self._sink.write_line)
         self._last_recap_thread = directory_recap.escalate_async(
             pathlib.Path(cwd), self._client_factory, self._current_profile,
             on_result)
     ```
   - At both existing call sites of `self._check_place_shift()`
     (`run_once`, line ~511, and `loop`, line ~535), change to:
     ```python
     if not self._check_place_shift():
         self._check_directory_recap()
     ```
     This is the entire gap-filler composition (design contract #4): the
     new mechanism runs only when the existing one printed nothing for
     this transition.
   - Add `import pathlib` and `from sasha import directory_recap` to the
     existing import block.

## Constraints
- Do not touch `src/sasha/llm.py`, `src/sasha/memory.py`, or
  `src/sasha/agent.py`.
- Do not touch `_check_place_shift`'s existing recap *logic* — only its
  return type, per "What's new" item 4.
- Do not implement `sasha recap` or `sasha directory-recap on|off` —
  Task 51.2.
- `escalate_async`'s thread must be a daemon thread and must not be
  joined by `directory_recap.py` itself (matches
  `_spawn_bootstrap_summary`'s existing pattern) — tests will join it
  explicitly via the `_last_recap_thread` seam.

## Acceptance criteria
1. `decide()` returns `(SILENT, None)` for a target with `child_repo_count
   >= 2` immediate git-repo subdirectories when the target itself is not
   a git repo — `test_ac1_umbrella_non_repo_is_silent`.
2. `decide()` returns `(ESCALATE, None)` for a target with
   `child_repo_count >= 2` immediate git-repo subdirectories when the
   target itself IS a git repo (the `mqtt` shape) —
   `test_ac2_umbrella_self_repo_escalates`.
3. `decide()` returns `(SILENT, None)` for a target with zero matching
   `tool_log` entries in its subtree (and no umbrella shape) —
   `test_ac3_no_history_is_silent`.
4. `decide()` returns `(SILENT, None)` when subtree fan-out `>= 3` (and
   no umbrella shape) — `test_ac4_high_fanout_is_silent`.
5. `decide()` returns `(SILENT, None)` when the most recent subtree entry
   is older than 14 days (and no umbrella shape, fan-out `< 3`) —
   `test_ac5_stale_activity_is_silent`.
6. `decide()` returns `(RECAP_DIRECT, text)` for a coherent single git
   repo (not umbrella, has commits, recent non-scattered activity), and
   `text` contains the repo's directory name and its last commit
   subject — `test_ac6_coherent_repo_recaps_directly`.
7. `decide()` returns `(ESCALATE, None)` for a target that is itself a
   git repo with real recent subtree activity but zero commits —
   `test_ac7_repo_no_commits_escalates`.
8. `decide()` returns `(ESCALATE, None)` for a non-git-repo target with
   real recent, non-scattered subtree activity (the "genuinely
   ambiguous, non-repo" case) — `test_ac8_active_non_repo_escalates`.
9. `gather_context()` includes the target's own last-commit text, every
   child repo's name and last-commit text (capped at 5, sorted by name),
   and the most recent tool_log commands (capped at 10, most-recent
   first) — `test_ac9_gather_context_shape`.
10. `escalate_async()` calls `on_result` with a string prefixed
    `"[sasha] "` when the stubbed LLM returns non-empty text, and does
    NOT call `on_result` at all when the stubbed LLM returns empty text
    — `test_ac10_escalate_async_result_and_abstain`.
11. `escalate_async()` does not raise and does not call `on_result` when
    the LLM call raises `ChatError` (e.g. connection refused) —
    `test_ac11_escalate_async_swallows_chat_error`.
12. `tool_log.log_path()` returns the exact same value as the module's
    existing (private) path resolution for the current environment —
    `test_ac12_tool_log_log_path_matches_internal`.
13. `Config().directory_recap_enabled` defaults to `True`; a
    `[directory_recap]` table with `enabled = false` turns it off; a
    non-bool `enabled` value is ignored (stays at the current default) —
    `test_ac13_config_directory_recap_enabled`.
14. `_DEFAULT_CONFIG_TOML` contains a `[directory_recap]` section with
    `enabled = true` — `test_ac14_default_toml_has_directory_recap`.
15. `Repl._check_place_shift()` returns `True` exactly when it prints the
    project-memory recap line, `False` otherwise (covering: no git root;
    git root with zero memories; unchanged cwd; just-bootstrapped) —
    `test_ac15_check_place_shift_return_value`.
16. Gap-filler composition: when `_check_place_shift()` returns `True`
    for a `cd`, `_check_directory_recap()` is never invoked for that same
    transition (verified via the `run_once`/`loop` call-site pattern,
    directly exercising the two-line `if not ...` construct) —
    `test_ac16_gap_filler_suppresses_new_mechanism`.
17. Gap-filler composition: when `_check_place_shift()` returns `False`,
    the directory-recap cascade runs and can print a direct recap or
    start an escalation thread — `test_ac17_gap_filler_allows_new_mechanism`.
18. Per-session dedup: calling the cwd-change check twice in a row with
    the same `cwd` only ever considers the cascade once (the second call
    produces no additional recap output, even if the underlying data
    changed between calls) — `test_ac18_per_session_dedup`.
19. `[directory_recap].enabled = false` fully suppresses
    `_check_directory_recap` (no recap line, no escalation thread
    started) even when the cascade would otherwise recap or escalate —
    `test_ac19_disabled_config_suppresses_everything`.
20. `decide()` does not raise when a target directory contains an
    unreadable child (a subdirectory with permissions removed, `chmod
    0`) — the child is silently excluded from the filesystem-shape scan
    rather than crashing the whole cascade —
    `test_ac20_unreadable_child_directory_does_not_crash`.

## Verified fix sketch

The following `src/sasha/directory_recap.py` was implemented and verified
in a scratch git worktree against all 20 contract tests below (20/20
green) and the full existing suite (639/639 green, one pre-existing
environmental flake unrelated to this task deselected). Matching this
sketch is not mandatory — the contract tests are the real acceptance bar —
but it removes essentially all ambiguity:

```python
from __future__ import annotations

import json
import pathlib
import subprocess
import threading
from datetime import datetime, timezone
from typing import Callable

from sasha import tool_log
from sasha.llm import ChatError

SILENT = "silent"
RECAP_DIRECT = "recap_direct"
ESCALATE = "escalate"

UMBRELLA_CHILD_REPO_THRESHOLD = 2
FANOUT_THRESHOLD = 3
STALE_DAYS = 14
MAX_CHILD_REPOS_FOR_LLM = 5
MAX_LOG_ENTRIES_FOR_LLM = 10


def _is_git_repo(path: pathlib.Path) -> bool:
    try:
        return (path / ".git").exists()
    except OSError:
        return False


def _safe_is_dir(path: pathlib.Path) -> bool:
    try:
        return path.is_dir()
    except OSError:
        return False


def _child_git_repos(path: pathlib.Path) -> list[pathlib.Path]:
    if not _safe_is_dir(path):
        return []
    try:
        children = [p for p in path.iterdir()
                    if not p.name.startswith(".") and _safe_is_dir(p)]
    except (PermissionError, OSError):
        return []
    return sorted((p for p in children if _is_git_repo(p)),
                  key=lambda p: p.name)


def _last_commit(path: pathlib.Path) -> str | None:
    try:
        out = subprocess.run(
            ["git", "-C", str(path), "log", "-1", "--format=%ar\t%s"],
            capture_output=True, text=True, timeout=2,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0 or not out.stdout.strip():
        return None
    return out.stdout.strip()


def _load_log_entries() -> list[dict]:
    path = tool_log.log_path()
    entries = []
    if not path.exists():
        return entries
    with path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return entries


def _subtree_entries(target: str, entries: list[dict]) -> list[dict]:
    target_s = target.rstrip("/")
    prefix = target_s + "/"
    return [e for e in entries
            if e.get("cwd") == target_s or e.get("cwd", "").startswith(prefix)]


def _fanout(target: str, subtree: list[dict]) -> int:
    target_s = target.rstrip("/")
    children = set()
    for e in subtree:
        cwd = e.get("cwd", "")
        if cwd == target_s:
            continue
        rest = cwd[len(target_s):].lstrip("/")
        if rest:
            children.add(rest.split("/", 1)[0])
    return len(children)


def _parse_ts(ts: str) -> datetime:
    return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)


def _most_recent_age_days(subtree: list[dict]) -> float | None:
    ages = []
    for e in subtree:
        ts = e.get("ts")
        if not ts:
            continue
        try:
            ages.append(_parse_ts(ts))
        except ValueError:
            continue
    if not ages:
        return None
    most_recent = max(ages)
    return (datetime.now(timezone.utc) - most_recent).total_seconds() / 86400


def decide(target: pathlib.Path,
           log_entries: list[dict] | None = None) -> tuple[str, str | None]:
    if log_entries is None:
        log_entries = _load_log_entries()

    child_repos = _child_git_repos(target)
    self_is_repo = _is_git_repo(target)

    if len(child_repos) >= UMBRELLA_CHILD_REPO_THRESHOLD:
        return (ESCALATE, None) if self_is_repo else (SILENT, None)

    subtree = _subtree_entries(str(target), log_entries)
    if not subtree:
        return (SILENT, None)
    if _fanout(str(target), subtree) >= FANOUT_THRESHOLD:
        return (SILENT, None)
    age_days = _most_recent_age_days(subtree)
    if age_days is not None and age_days > STALE_DAYS:
        return (SILENT, None)

    if self_is_repo:
        commit = _last_commit(target)
        if commit is not None:
            age, subject = commit.split("\t", 1)
            return (RECAP_DIRECT,
                    f'[sasha] {target.name}: last commit {age} — "{subject}"')
        return (ESCALATE, None)
    return (ESCALATE, None)


def gather_context(target: pathlib.Path,
                    log_entries: list[dict] | None = None) -> dict:
    if log_entries is None:
        log_entries = _load_log_entries()

    self_commit = _last_commit(target)
    child_repos = _child_git_repos(target)[:MAX_CHILD_REPOS_FOR_LLM]
    child_commits = [(p.name, _last_commit(p)) for p in child_repos]

    subtree = _subtree_entries(str(target), log_entries)

    def _sort_key(e):
        ts = e.get("ts", "")
        try:
            return _parse_ts(ts)
        except ValueError:
            return datetime.min.replace(tzinfo=timezone.utc)

    recent = sorted(subtree, key=_sort_key, reverse=True)[:MAX_LOG_ENTRIES_FOR_LLM]
    recent_commands = [(e.get("executed_command")
                         or e.get("arguments", {}).get("command", ""),
                         e.get("ts", "")) for e in recent]

    return {
        "target_name": target.name,
        "self_commit": self_commit,
        "child_commits": child_commits,
        "recent_commands": recent_commands,
    }


def build_prompt(context: dict) -> str:
    parts = [
        "You are producing a one-line recap for a terminal assistant, "
        "shown right after the user changed into this directory. Given "
        "the data below, write EXACTLY ONE short line describing what "
        "this place is or what's been happening, in a plain factual "
        "voice (e.g. \"mqtt: bridges MQTT credentials from the iotd "
        "firmware repo to an external consumer\"). Use only the data "
        "given below -- do not invent anything. If nothing coherent can "
        "be said, output nothing at all (an empty response) -- do not "
        "guess or pad with filler.",
        f"Directory: {context['target_name']}",
    ]
    if context["self_commit"]:
        parts.append(f"Last commit here: {context['self_commit']}")
    if context["child_commits"]:
        parts.append("Nested repositories:")
        for name, commit in context["child_commits"]:
            parts.append(f"  - {name}: {commit or '(no commits)'}")
    if context["recent_commands"]:
        parts.append("Recent commands run here:")
        for command, ts in context["recent_commands"]:
            parts.append(f"  - {command} ({ts})")
    return "\n".join(parts)


def escalate_async(target: pathlib.Path, client_factory, profile,
                    on_result: Callable[[str], None]) -> threading.Thread:
    def _run():
        context = gather_context(target)
        prompt = build_prompt(context)
        try:
            client = client_factory(profile)
            response = client.chat(
                [{"role": "user", "content": prompt}], [], lambda _: None)
        except ChatError:
            return
        text = response.text.strip()
        if text:
            on_result(f"[sasha] {text}")

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    return thread
```

`repl.py` changes verified alongside it: `import pathlib` and
`from sasha import directory_recap` added to the import block;
`Repl.__init__` gains `self._recapped_dirs = set()` and
`self._last_recap_thread = None`; `_check_place_shift`'s four early
`return` statements become `return False`, and its project-memory-line
branch becomes `... ; return True` with a final `return False` if that
branch isn't taken; the new `_check_directory_recap` method matches
"What's new" item 4 above verbatim; both existing call sites become
`if not self._check_place_shift(): self._check_directory_recap()`.
`config.py` changes: `directory_recap_enabled: bool = True` added to
`Config`; a `[directory_recap]` table parsed exactly like
`llm_debug_enabled` (see `_build_config`'s existing `llm_debug_table`
block for the pattern to mirror); `[directory_recap]\nenabled = true`
appended to `_DEFAULT_CONFIG_TOML` after `[llm_debug]`. `tool_log.py`
gains the one-line `log_path()` wrapper immediately after `_log_path()`.

## Standing instructions
- Your goal: make the contract tests in
  `tests/contract/test_task_51_1_directory_recap_cascade.py` pass.
- NEVER modify files under `tests/contract/` — the gate rejects the
  commit if you do. If a contract test seems wrong or unimplementable,
  stop and flag it.
- You may add your own supplementary tests under `tests/impl/`.
- Flag unexpected state; do not modify anything outside this task.
- Deliver exactly ONE new commit on top of the HEAD you started from;
  never amend, rebase, or reset; leave the working tree clean.
- Commit message must follow the task-commit trailer format in
  `CLAUDE.md`: `Done: Task 51.1 — <description>`, `Next: Task 51.2 —
  sasha recap + directory-recap toggle command`, and your
  `Co-Authored-By:` signature line exactly as given in the dispatch
  prompt.
