# Task 22.2: Tool-call logging — compaction, config, manual command

## Authoritative contract
`docs/superpowers/specs/2026-07-21-tool-call-logging-design.md` §4.3
(compaction), §5 (config field), plus the `sasha compact-log` command
implied by §4.3's "manual command" paragraph are the complete contract
for this task. This task completes ISSUE-042, building on Task 22.1's
`tool_log.py`/`log_call()` (already shipped — do not modify its body).

**Two corrections to the design doc, found while spec'ing this task (not
implementation surprises — decided now, before dispatch):**
1. The design doc says compaction reuses "the Phase 19 memory-bootstrap
   background-task pattern (busy-star indicator)." That indicator
   (`Repl._begin_background_task`/`_end_background_task`) lives in
   `repl.py`, but this task's hook point is `agent.py` (same place
   Task 22.1 hooked `log_call`), which has no access to a `Repl`
   instance — and threading a new callback through `run_consult`'s
   signature to reach it would reopen exactly the blast-radius problem
   Task 22.1's design deliberately avoided. **Decision: automatic
   compaction runs on a plain daemon thread with no busy-star indicator.**
   The trade-off (no visual spinner during the rare event of the log
   crossing its size threshold) is accepted — worth flagging plainly
   rather than silently dropping.
2. The design doc's example warning message uses `mv` with a literal
   `<date>` placeholder; the actual message this task produces substitutes
   a real path, not a template — see the exact string in the fix sketch.

## What already exists
- `src/sasha/tool_log.py` (Task 22.1) — `log_call()`, `_log_path()`,
  `_utc_timestamp()`, `_session_id`. **Do not modify `log_call()`'s body
  or signature** — this task only adds new functions alongside it
  (`configure`, `compact`, `check_and_maybe_compact_async`,
  `CompactionResult`).
- `src/sasha/agent.py` (Task 22.1) — already imports `tool_log` and calls
  `tool_log.log_call(...)` once per tool call. This task adds exactly one
  more line right after that call.
- `src/sasha/safe_commands.py`, `tests/contract/test_task_21_1_*.py` — the
  `[safe_commands]` config section's parse-with-fallback pattern in
  `config.py` is the template for this task's `[tool_log]` section.
- `src/sasha/repl.py` (Task 17.3/18.3) — `_handle_builtin`'s
  `verb == "memory"`/`verb == "forget"` dispatch and the corresponding
  `_handle_memory_builtin`/`_handle_forget_builtin` methods are the
  template for `verb == "compact-log"` /
  `_handle_compact_log_builtin`. `_spawn_bootstrap_summary`/
  `_bootstrap_stage_2`/`threading.Thread(daemon=True)` is the template
  for how a background thread is started and reports back via
  `self._sink.write_line(...)` when done — this task's automatic-trigger
  thread follows the same shape but reports via the `out` callback
  already passed into `run_consult` (see correction #1 above for why it
  doesn't touch the busy-star counter).
- `src/sasha/config.py` (Task 1.1/9.1/21.1) — `Config`, `_build_config`,
  `_DEFAULT_CONFIG_TOML`, `load()`. Bind to these; extend the same way
  `sensitive_paths` was added in Task 21.1.

## What's new
1. **`src/sasha/tool_log.py`** gains:
   - `configure(max_size_mb: int) -> None` — sets the module-level
     threshold used by `check_and_maybe_compact_async`.
   - `CompactionResult` — frozen dataclass:
     `before_bytes: int, after_bytes: int, still_too_big: bool`.
   - `compact(max_size_mb: int | None = None) -> CompactionResult` —
     synchronous; strips `result` from the oldest entries (setting
     `result_omitted_reason: "compacted"`) until the file's serialized
     size drops to 50% of `max_size_mb` (module default if `None`) or
     every entry has been stripped, whichever comes first. Atomic
     temp-file + `os.replace()`. Skips unparseable lines. Guarded by a
     module-level `threading.Lock` so overlapping calls (one automatic,
     one manual) serialize rather than race.
   - `check_and_maybe_compact_async(out: Callable[[str], None]) ->
     threading.Thread | None` — cheap size check; if over threshold,
     spawns and returns a daemon thread (a test seam, same idea as
     `Repl._last_bootstrap_thread`) that calls `compact()` and, if the
     result's `still_too_big` is `True`, calls `out(...)` with the
     warning message. Returns `None` (no thread spawned) if under
     threshold.
2. **`src/sasha/agent.py`** — one new line in `run_consult`'s per-call
   loop, immediately after the existing `tool_log.log_call(...)` call:
   `tool_log.check_and_maybe_compact_async(out)`.
3. **`src/sasha/config.py`** — new `Config.tool_log_max_size_mb: int =
   100` field; `[tool_log]` TOML section parsed the same way
   `[safe_commands]` already is (present-and-valid overrides the
   default; absent or malformed falls back to it); appended to
   `_DEFAULT_CONFIG_TOML`.
4. **`src/sasha/__main__.py`** — one new import
   (`from sasha import tool_log`) and one new line after `config =
   load(args.config)`: `tool_log.configure(config.tool_log_max_size_mb)`.
5. **`src/sasha/repl.py`** — new `elif verb == "compact-log":` branch in
   `_handle_builtin` dispatching to a new `_handle_compact_log_builtin`
   method; the `sasha help` line gains `", sasha compact-log"`.

## `tool_log.py` additions — exact contract

```python
import dataclasses
import threading

_max_size_mb = 100
_compaction_lock = threading.Lock()


def configure(max_size_mb: int) -> None:
    global _max_size_mb
    _max_size_mb = max_size_mb


@dataclasses.dataclass(frozen=True)
class CompactionResult:
    before_bytes: int
    after_bytes: int
    still_too_big: bool


def compact(max_size_mb: int | None = None) -> CompactionResult:
    ...


def check_and_maybe_compact_async(out) -> "threading.Thread | None":
    ...
```

- `compact()`: reads `_log_path()`; if the file doesn't exist, returns
  `CompactionResult(0, 0, False)` immediately (no lock needed — nothing
  to do). Otherwise, under `_compaction_lock`: read all lines, `json.loads`
  each (skip — don't raise on — any line that fails to parse), then walk
  entries oldest→newest, computing each entry's serialized byte length
  (`len(json.dumps(entry)) + 1` for the trailing newline) once up front
  and maintaining a running total; for each entry in order, if the
  running total already at-or-below `(max_size_mb or _max_size_mb) *
  1_000_000 // 2`, stop; otherwise, if the entry's `result` is not
  already `None`, set it to `None` and `result_omitted_reason` to
  `"compacted"`, and update the running total by the exact delta (the
  entry's new serialized length minus its old one). After the walk,
  serialize all entries back to JSONL, write to `<path>.tmp` (created
  with mode `0o600`, same `os.open`/`os.fdopen` idiom as `log_call`),
  then `os.replace(tmp, path)` — atomic on POSIX, so a crash between the
  read and the replace leaves the original file untouched. On any
  `OSError` writing the temp file or replacing, return
  `CompactionResult(before_bytes, before_bytes, before_bytes >
  max_bytes)` (the attempt failed, file is unchanged). On success, return
  `CompactionResult(before_bytes, after_bytes, after_bytes > max_bytes)`
  where `after_bytes` is the real post-write file size (not the estimate).
- `check_and_maybe_compact_async(out)`: `os.stat()` the log path (any
  `OSError`, e.g. file doesn't exist yet — return `None`, nothing to do).
  If `size <= _max_size_mb * 1_000_000`: return `None`. Otherwise spawn
  `threading.Thread(target=_run, daemon=True)` where `_run` calls
  `compact()` and, if `result.still_too_big`, calls:
  ```python
  out(f"[sasha] tool_log.jsonl is {result.after_bytes // 1_000_000} MB "
      f"even with all results stripped — delete it or archive it "
      f"(e.g. mv {_log_path()} "
      f"{_log_path().parent / ('tool_log-archive-' + _utc_timestamp()[:10] + '.jsonl')}) "
      f"to reclaim space.")
  ```
  Start the thread and return it (test seam — real callers ignore the
  return value).

## `agent.py` — exact change

Immediately after the existing:
```python
            tool_log.log_call(
                ...
            )
```
add:
```python
            tool_log.check_and_maybe_compact_async(out)
```
Nothing else in `run_consult` changes.

## `config.py` — exact changes

1. Add `tool_log_max_size_mb: int = 100` as a new field on `Config`,
   after `sensitive_paths`.
2. In `_build_config`, after the existing `sensitive_paths = ...` /
   `safe_commands_table` block, add:
   ```python
   tool_log_max_size_mb = 100
   tool_log_table = raw.get("tool_log", {})
   if isinstance(tool_log_table, dict):
       raw_max = tool_log_table.get("max_size_mb")
       if isinstance(raw_max, int) and not isinstance(raw_max, bool) and raw_max > 0:
           tool_log_max_size_mb = raw_max
   ```
   (the `not isinstance(raw_max, bool)` guard matters because `bool` is a
   subclass of `int` in Python — `True`/`False` must not silently become
   `1`/`0`).
3. Add `tool_log_max_size_mb=tool_log_max_size_mb,` to the `return
   Config(...)` call.
4. Append to `_DEFAULT_CONFIG_TOML` (after the `[safe_commands]` table):
   ```toml

   [tool_log]
   max_size_mb = 100
   ```

## `__main__.py` — exact change

Add `from sasha import tool_log` to the imports. Immediately after:
```python
    try:
        config = load(args.config)
    except ConfigError as e:
        ...
```
add:
```python
    tool_log.configure(config.tool_log_max_size_mb)
```

## `repl.py` — exact changes

1. Add `from sasha import tool_log` to the module-level imports.
2. In `_handle_builtin`, add a new branch alongside `elif verb ==
   "forget":`:
   ```python
           elif verb == "compact-log":
               self._handle_compact_log_builtin()
   ```
3. Update the `sasha help` line to also mention
   `"sasha compact-log"` (insert after `"sasha forget, "`).
4. Add a new method, next to `_handle_forget_builtin`:
   ```python
       def _handle_compact_log_builtin(self):
           result = tool_log.compact()
           before_mb = result.before_bytes / 1_000_000
           after_mb = result.after_bytes / 1_000_000
           self._sink.write_line(
               f"[sasha] tool_log.jsonl compacted: {before_mb:.1f} MB "
               f"→ {after_mb:.1f} MB")
           if result.still_too_big:
               self._sink.write_line(
                   f"[sasha] still {after_mb:.1f} MB even with all results "
                   f"stripped — consider deleting or archiving it")
   ```

## Fix sketch (authoritative — verified directly)

Verified end to end in a scratch git worktree — 558/558 tests green,
including all 12 new ACs and the full pre-existing suite unmodified —
before being committed as authoritative. The full, exact additions to
`src/sasha/tool_log.py` (everything above `log_call` and its module-level
globals is untouched):

```python
import dataclasses
import threading
# (added to the existing import block, alongside datetime/json/os/pathlib/sys/uuid)

_max_size_mb = 100
_compaction_lock = threading.Lock()
# (added alongside the existing _session_id/_warned module globals)


def configure(max_size_mb: int) -> None:
    global _max_size_mb
    _max_size_mb = max_size_mb


@dataclasses.dataclass(frozen=True)
class CompactionResult:
    before_bytes: int
    after_bytes: int
    still_too_big: bool


def compact(max_size_mb: float | None = None) -> CompactionResult:
    if max_size_mb is None:
        max_size_mb = _max_size_mb
    max_bytes = max_size_mb * 1_000_000
    target_bytes = max_bytes / 2
    path = _log_path()

    if not path.exists():
        return CompactionResult(0, 0, False)

    with _compaction_lock:
        before_bytes = path.stat().st_size

        entries = []
        for line in path.read_text().splitlines():
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue

        sizes = [len(json.dumps(e)) + 1 for e in entries]
        running_total = sum(sizes)
        for idx, entry in enumerate(entries):
            if running_total <= target_bytes:
                break
            if entry.get("result") is not None:
                entry["result"] = None
                entry["result_omitted_reason"] = "compacted"
                new_size = len(json.dumps(entry)) + 1
                running_total += new_size - sizes[idx]
                sizes[idx] = new_size

        new_text = "".join(json.dumps(e) + "\n" for e in entries)
        tmp_path = path.with_name(path.name + ".tmp")
        try:
            fd = os.open(str(tmp_path), os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as f:
                f.write(new_text)
            os.replace(str(tmp_path), str(path))
        except OSError:
            return CompactionResult(before_bytes, before_bytes, before_bytes > max_bytes)

        after_bytes = path.stat().st_size
        return CompactionResult(before_bytes, after_bytes, after_bytes > max_bytes)


def check_and_maybe_compact_async(out):
    path = _log_path()
    try:
        size = path.stat().st_size
    except OSError:
        return None
    if size <= _max_size_mb * 1_000_000:
        return None

    def _run():
        result = compact()
        if result.still_too_big:
            archive_name = "tool_log-archive-" + _utc_timestamp()[:10] + ".jsonl"
            out(f"[sasha] tool_log.jsonl is {result.after_bytes // 1_000_000} MB "
                f"even with all results stripped — delete it or archive it "
                f"(e.g. mv {path} {path.parent / archive_name}) "
                f"to reclaim space.")

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    return thread
```

The `agent.py`, `config.py`, `__main__.py`, and `repl.py` changes are
exactly as given in their "exact contract"/"exact change(s)" sections
above — implement them verbatim.

## Constraints
- Modify `src/sasha/tool_log.py` (additive only — do not touch
  `log_call`, `_log_path`, `_utc_timestamp`, `_session_id`,
  `_warned`), `src/sasha/agent.py` (one line), `src/sasha/config.py`,
  `src/sasha/__main__.py`, `src/sasha/repl.py`.
- Do not touch `src/sasha/safe_commands.py`, `src/sasha/tools.py`,
  `src/sasha/executor.py` — nothing in this task's contract exercises
  them.
- Do not add busy-star/`Repl._begin_background_task` wiring for the
  automatic compaction path — see correction #1 above.
- **Executor routing (per CLAUDE.md's standing rule): this task edits
  existing control flow in `agent.py`/`config.py`/`__main__.py`/`repl.py`
  that already works (surgical), even though the new `tool_log.py`
  functions are additive. Dispatch to the default model
  (deepseek-v4-pro) — do not launch under a local-model
  `SASHA_DISPATCH_MODEL` override.**

## Acceptance criteria
(encoded 1:1 in `tests/contract/test_task_22_2_tool_call_log_compaction.py`)
1. `compact()` strips `result` from the oldest entries first, stopping
   once the file drops to ~50% of the given `max_size_mb` —
   `test_ac1_compact_strips_oldest_first_to_target`.
2. A failure during the atomic write/replace step leaves the original
   file completely untouched — `test_ac2_compact_atomic_rename_leaves_original_untouched_on_failure`.
3. A corrupt/unparseable line in the log is skipped, not fatal —
   `test_ac3_compact_skips_corrupt_lines`.
4. If stripping every entry's result still leaves the file over
   `max_size_mb`, `CompactionResult.still_too_big` is `True` —
   `test_ac4_compact_reports_still_too_big_when_stripping_isnt_enough`.
5. `configure(max_size_mb)` changes the threshold
   `check_and_maybe_compact_async` checks against —
   `test_ac5_configure_changes_threshold`.
6. `check_and_maybe_compact_async` is a no-op (returns `None`, spawns no
   thread) when the file is at or under the threshold —
   `test_ac6_check_and_maybe_compact_noop_when_under_threshold`.
7. `check_and_maybe_compact_async` spawns a thread that compacts and,
   when the result is still too big, calls `out(...)` with a warning
   message containing the exact phrase "even with all results stripped" —
   `test_ac7_check_and_maybe_compact_triggers_and_warns_when_still_too_big`.
8. Two overlapping `compact()` calls are serialized by the module-level
   lock (one blocks until the other releases it) —
   `test_ac8_concurrent_compaction_is_serialized_by_lock`.
9. `[tool_log].max_size_mb` parses with the default-and-fallback pattern,
   including rejecting a `bool` value —
   `test_ac9_config_default_and_fallback`.
10. `agent.py`'s dispatch calls `tool_log.check_and_maybe_compact_async`
    once per tool call, immediately after `log_call` —
    `test_ac10_dispatch_calls_check_and_maybe_compact`.
11. The `sasha compact-log` REPL built-in calls `tool_log.compact()` and
    reports a before/after size line —
    `test_ac11_repl_compact_log_builtin_reports_size`.
12. `sasha help` mentions `"sasha compact-log"` —
    `test_ac12_help_mentions_compact_log`.

## Standing instructions
- Your goal: make
  `tests/contract/test_task_22_2_tool_call_log_compaction.py` pass, with
  the full existing suite still green: `bash scripts/run-tests.sh`.
- NEVER modify files under `tests/contract/` — the gate rejects the
  commit if you do. If a contract test seems wrong or unimplementable,
  stop and flag it; do not commit.
- You may add your own supplementary tests under `tests/impl/`.
- Deliver exactly ONE new commit on top of the HEAD you started from;
  never amend, rebase, or reset; leave the working tree clean.
- Commit message per AGENTS.md, with trailers: `Done: Task 22.2 — <one
  line>`, `Fixed: ISSUE-042`,
  `Next: Task 22.3 — Phase 22 review (Tier 1, with Salvador)`, and the
  Co-Authored-By signature line from your dispatch prompt.
