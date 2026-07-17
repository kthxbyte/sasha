# Task 17.1: src/sasha/memory.py + Executor.last_command/last_result

## Authoritative contract
`docs/superpowers/specs/2026-07-17-project-memory-design.md` is the
complete design record for this task — read it first, especially §4
(architecture & on-disk format) and §5.2/§5.5 (loading/capping,
lazy+self-protecting directory creation). This task builds the foundation
the rest of Phase 17 wires in — `src/sasha/repl.py` and `src/sasha/tools.py`
are not touched by this task.

## What already exists
- `src/sasha/executor.py`: `Executor` class, `ExecResult` dataclass
  (`.exit_code`, `.signal`, `.stderr_text`, `.stdout_text`, `.cwd_after`,
  `.duration_ms`, `.ctrl_z_terminated`). This task adds two small pieces of
  state to `Executor` — everything else in the file (the entire `run()`
  relay/capture/job-control logic, `_on_sigwinch`, `_parse_export_p`,
  `check_syntax`) is unchanged. Do not rewrite any of it.

## What's new

### 1. `Executor` gains `last_command`/`last_result` tracking

In `src/sasha/executor.py`, `__init__` gains two new instance attributes,
added after the existing three:
```python
    def __init__(self, cwd: str, env: dict[str, str], *,
                 stderr_capture_bytes: int = 8192) -> None:
        self._cwd = os.path.realpath(cwd)
        self._env = dict(env)
        self._stderr_capture_bytes = stderr_capture_bytes
        self._last_command: str | None = None
        self._last_result: ExecResult | None = None
```
Two new read-only properties, placed right after the existing `cwd`
property:
```python
    @property
    def last_command(self) -> str | None:
        return self._last_command

    @property
    def last_result(self) -> ExecResult | None:
        return self._last_result
```
At the very end of `run()`, the existing `return ExecResult(...)` becomes:
```python
        result = ExecResult(
            exit_code=exit_code,
            signal=signal_num,
            stderr_text=stderr_text,
            stdout_text=stdout_text,
            cwd_after=self._cwd,
            duration_ms=duration_ms,
            ctrl_z_terminated=ctrl_z_terminated,
        )
        self._last_command = line
        self._last_result = result
        return result
```
That is the entire change to `executor.py` — the dataclass construction
itself is byte-identical to today's, just assigned to a local first instead
of returned directly.

### 2. New file `src/sasha/memory.py`, in full:

```python
from __future__ import annotations

import json
import pathlib
import subprocess
import time

from sasha.executor import ExecResult

_MAX_MEMORY_CHARS = 8000


def resolve_git_root(cwd: str) -> pathlib.Path | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=cwd, capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    path = result.stdout.strip()
    if not path:
        return None
    return pathlib.Path(path)


def _memory_dir(repo_root: pathlib.Path) -> pathlib.Path:
    return repo_root / ".sasha" / "memory"


def _semantic_path(repo_root: pathlib.Path) -> pathlib.Path:
    return _memory_dir(repo_root) / "semantic.jsonl"


def ensure_memory_dir(repo_root: pathlib.Path) -> tuple[pathlib.Path, bool]:
    memory_dir = _memory_dir(repo_root)
    if memory_dir.exists():
        return memory_dir, False
    gitignore = repo_root / ".sasha" / ".gitignore"
    gitignore.parent.mkdir(parents=True, exist_ok=True)
    gitignore.write_text("*\n")
    memory_dir.mkdir(parents=True, exist_ok=True)
    (memory_dir / "episodic").mkdir(exist_ok=True)
    return memory_dir, True


def _append_entry(repo_root: pathlib.Path, kind: str, text: str,
                   pointer: str | None) -> bool:
    _, just_created = ensure_memory_dir(repo_root)
    entry = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "kind": kind,
        "text": text,
        "pointer": pointer,
    }
    path = _semantic_path(repo_root)
    with open(path, "a") as f:
        f.write(json.dumps(entry) + "\n")
    return just_created


def encode_commit(repo_root: pathlib.Path, message: str, commit_hash: str) -> bool:
    return _append_entry(repo_root, "commit", message, commit_hash)


def _write_episodic_log(repo_root: pathlib.Path, command: str,
                         result: ExecResult) -> str:
    memory_dir, _ = ensure_memory_dir(repo_root)
    stamp = time.strftime("%Y-%m-%dT%H-%M-%SZ", time.gmtime())
    log_path = memory_dir / "episodic" / f"{stamp}.log"
    counter = 1
    while log_path.exists():
        log_path = memory_dir / "episodic" / f"{stamp}-{counter}.log"
        counter += 1
    content = (
        f"command: {command}\n"
        f"exit code: {result.exit_code}\n"
        f"duration: {result.duration_ms}ms\n"
        f"--- stdout ---\n{result.stdout_text}\n"
        f"--- stderr ---\n{result.stderr_text}\n"
    )
    log_path.write_text(content)
    return str(log_path.relative_to(repo_root))


def encode_remember(repo_root: pathlib.Path, note: str,
                     last_command: str | None,
                     last_result: ExecResult | None) -> bool:
    pointer = None
    if last_command is not None and last_result is not None:
        pointer = _write_episodic_log(repo_root, last_command, last_result)
    return _append_entry(repo_root, "remember", note, pointer)


def load_memory_block(repo_root: pathlib.Path,
                       max_chars: int = _MAX_MEMORY_CHARS) -> tuple[str, int]:
    path = _semantic_path(repo_root)
    if not path.exists():
        return "", 0
    entries = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    total = len(entries)
    kept: list[str] = []
    used = 0
    for entry in reversed(entries):
        rendered = f"- [{entry.get('timestamp', '')[:10]}] {entry.get('text', '')}"
        if used + len(rendered) + 1 > max_chars and kept:
            break
        kept.append(rendered)
        used += len(rendered) + 1
    kept.reverse()
    return "\n".join(kept), total


def most_recent_gist(repo_root: pathlib.Path) -> str | None:
    path = _semantic_path(repo_root)
    if not path.exists():
        return None
    last_valid = None
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        last_valid = entry
    if last_valid is None:
        return None
    return last_valid.get("text")
```

That is the complete file — copy it verbatim. Every function signature is
final; nothing here is a sketch.

### Design points worth calling out explicitly

1. **`load_memory_block`'s cap always keeps at least the most recent
   entry**, even if that single entry alone exceeds `max_chars`. The loop
   condition `used + len(rendered) + 1 > max_chars and kept` is `and kept`
   deliberately — on the very first (newest) entry, `kept` is still empty,
   so the check never blocks adding it, no matter its size. Only the
   *second* and later (older) entries can be dropped for budget reasons.
2. **`ensure_memory_dir` is idempotent and cheap to call repeatedly** — it
   checks `memory_dir.exists()` first and returns immediately with
   `just_created=False` if so. `_append_entry` and `_write_episodic_log`
   both call it unconditionally on every write; this is intentional, not
   wasted work, since it's what makes lazy creation "just happen" the
   first time without any separate "have I created this yet" bookkeeping
   elsewhere.
3. **`.sasha/.gitignore` is written before `memory_dir.mkdir(...)`** — this
   ordering is load-bearing, not incidental. Do not reorder it.
4. **Episodic log filenames use `-` instead of `:`** in the time component
   (filesystem-safe), and collision-handle within the same second by
   appending `-1`, `-2`, etc. — two `remember` calls in the same second
   must never silently overwrite one another's episodic record.
5. **`encode_remember`'s pointer is `None` (not an empty string, not
   omitted from the dict)** when `last_command`/`last_result` aren't both
   present. The JSON entry always has all four keys.
6. **`most_recent_gist` and `load_memory_block` each independently parse
   `semantic.jsonl`** — this is a deliberate, accepted minor redundancy
   (simplicity over the marginal I/O cost of one extra file read), not an
   oversight to "fix" by merging them.

## Constraints
- Create only `src/sasha/memory.py` and its test file
  (`tests/contract/test_task_17_1_memory.py`, already committed). Modify
  only `src/sasha/executor.py`, and only in the two places shown above —
  every other line in that file (the entire `run()` body between its
  first line and the `result = ExecResult(...)` change, `_on_sigwinch`,
  `_parse_export_p`, `check_syntax`) is unchanged.
- Do not touch `src/sasha/repl.py`, `src/sasha/tools.py`,
  `src/sasha/session.py`, `src/sasha/agent.py`, or `README.md`. Wiring
  `memory.py` into the REPL and tool registry is later tasks in this
  phase.
- No new third-party dependencies — `memory.py` uses only `json`,
  `pathlib`, `subprocess`, `time`, and `sasha.executor.ExecResult` from
  the standard library / already-existing project code.

## Acceptance criteria
(encoded in `tests/contract/test_task_17_1_memory.py`, 21 tests — all
`test_ac_*`)

1. `resolve_git_root`: finds the root from the root itself and from any
   subdirectory; returns `None` outside any git repository.
2. `ensure_memory_dir`: first call creates `.sasha/memory/` (with an
   `episodic/` subdirectory) and `.sasha/.gitignore` containing exactly
   `"*\n"`, returns `just_created=True`; a second call returns the same
   path with `just_created=False` and creates nothing new. A repo that's
   never had anything encoded into it has *no* `.sasha/` directory at all.
3. `encode_commit`: appends a `{"kind": "commit", "text": <message>,
   "pointer": <hash>, "timestamp": ...}` line to `semantic.jsonl`;
   multiple calls append multiple lines in order; the first call's return
   value is `True` (dir just created), subsequent calls are `False`.
4. `encode_remember`: with no last command/result, the entry's `pointer`
   is `None`; with both present, a verbatim episodic log file is written
   (containing the command, exit code, duration, full stdout, full
   stderr) and the entry's `pointer` is a `.sasha/memory/episodic/...`
   path *relative to the repo root* (not absolute).
5. `load_memory_block`: returns `("", 0)` for a repo with no memory yet;
   renders entries in chronological order; skips a malformed JSON line
   without losing the well-formed entries around it; when entries exceed
   `max_chars`, drops the *oldest* first while always keeping at least the
   single most recent entry, even if it alone exceeds the budget.
6. `most_recent_gist`: returns `None` when there's no memory yet, and the
   `text` of the newest entry otherwise.
7. Concurrent appends: four threads each calling `encode_remember` 20
   times against the same repo (80 total calls) produce a
   `semantic.jsonl` with exactly 80 lines, every one of them
   independently valid JSON — no torn or interleaved lines.
8. `Executor.last_command`/`.last_result` are both `None` before any
   command has run, and after `run()` reflect exactly the command string
   passed in and the `ExecResult` returned (same object, via `is`).

## Standing instructions
- Your goal: make `tests/contract/test_task_17_1_memory.py` pass, and keep
  the full contract suite green:
  `PYTHONPATH=src python3 -m pytest tests/contract/`.
- NEVER modify files under `tests/contract/` — the gate rejects the commit
  if you do.
- Deliver exactly ONE new commit on top of the HEAD you started from; never
  amend, rebase, or reset; leave the working tree clean.
- Commit message per `AGENTS.md`, with trailers: `Done: Task 17.1 — <one
  line>`, `Next: Task 17.2 — remember tool + Session memory block`, and
  the `Co-Authored-By:` signature line from your dispatch prompt.
- Greenfield routing: `memory.py` is a self-contained new module built
  entirely against a committed contract test suite; the `Executor`
  addition is two small, precisely-specified insertions into an existing
  file, not a rewrite of any existing logic. Per the project's standing
  executor-routing rule, this MAY be dispatched with a local-model
  `SASHA_DISPATCH_MODEL` override; the default (deepseek-v4-pro) is also
  fine and requires no extra setup.
