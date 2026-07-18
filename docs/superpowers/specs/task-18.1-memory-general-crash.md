# Task 18.1: memory.py — private _at layer, general_* functions, crash encode pair

## Authoritative contract
`docs/superpowers/specs/2026-07-17-general-memory-design.md` is the
complete design record for this task — read it first, especially §4
(the wrapper refactor) and §5.3 (the crash pair). This is the
foundational task for Phase 18; it does not touch `tools.py`,
`session.py`, or `repl.py` — those are Tasks 18.2/18.3.

## What already exists
- `src/sasha/memory.py` (Task 17.1/17.3, landed): the full current file
  is reproduced below as "What already exists" context. Every one of its
  **public** functions — `resolve_git_root`, `ensure_memory_dir`,
  `encode_commit`, `encode_remember`, `load_memory_block`,
  `encode_latest_commit`, `forget`, `most_recent_gist` — must keep
  **byte-identical signatures and behavior** after this task. This is
  the central constraint of the whole task: `tests/contract/
  test_task_17_1_memory.py` must pass **unmodified** — its continued
  green-ness is the acceptance proof that nothing public changed.
- `src/sasha/executor.py`: `ExecResult` (`.exit_code`, `.signal`,
  `.stderr_text`, `.stdout_text`, `.cwd_after`, `.duration_ms`). Not
  touched by this task.

Current `src/sasha/memory.py`, for reference (this is what you're
refactoring, not what you're producing — see "What's new" for the
target):

```python
from __future__ import annotations

import json
import pathlib
import shutil
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


def encode_latest_commit(repo_root: pathlib.Path) -> bool | None:
    try:
        result = subprocess.run(
            ["git", "log", "-1", "--format=%H%n%B"],
            cwd=str(repo_root), capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    output = result.stdout
    newline_idx = output.find("\n")
    if newline_idx == -1:
        return None
    commit_hash = output[:newline_idx].strip()
    message = output[newline_idx + 1:].strip()
    if not commit_hash or not message:
        return None
    return encode_commit(repo_root, message, commit_hash)


def forget(repo_root: pathlib.Path) -> None:
    memory_dir = _memory_dir(repo_root)
    if memory_dir.exists():
        shutil.rmtree(memory_dir)


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

## What's new

Replace `src/sasha/memory.py` in its entirety with the following. Every
public function listed in "What already exists" is preserved with
identical signature and behavior — trace through each one below to
confirm before you conclude the refactor is correct; this is the whole
point of the task.

```python
from __future__ import annotations

import json
import os
import pathlib
import shutil
import signal
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


def _memory_root_for(repo_root: pathlib.Path) -> pathlib.Path:
    return repo_root / ".sasha" / "memory"


def general_memory_root() -> pathlib.Path:
    xdg = os.environ.get("XDG_CONFIG_HOME", "")
    base = pathlib.Path(xdg) if xdg else pathlib.Path.home() / ".config"
    return base / "sasha" / "memory"


def _semantic_path(memory_root: pathlib.Path) -> pathlib.Path:
    return memory_root / "semantic.jsonl"


def _ensure_memory_dir_at(memory_root: pathlib.Path, *,
                           gitignore: bool) -> tuple[pathlib.Path, bool]:
    if memory_root.exists():
        return memory_root, False
    if gitignore:
        gitignore_path = memory_root.parent / ".gitignore"
        gitignore_path.parent.mkdir(parents=True, exist_ok=True)
        gitignore_path.write_text("*\n")
    memory_root.mkdir(parents=True, exist_ok=True)
    (memory_root / "episodic").mkdir(exist_ok=True)
    return memory_root, True


def ensure_memory_dir(repo_root: pathlib.Path) -> tuple[pathlib.Path, bool]:
    return _ensure_memory_dir_at(_memory_root_for(repo_root), gitignore=True)


def _append_entry_at(memory_root: pathlib.Path, kind: str, text: str,
                      pointer: str | None, *, gitignore: bool) -> bool:
    _, just_created = _ensure_memory_dir_at(memory_root, gitignore=gitignore)
    entry = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "kind": kind,
        "text": text,
        "pointer": pointer,
    }
    path = _semantic_path(memory_root)
    with open(path, "a") as f:
        f.write(json.dumps(entry) + "\n")
    return just_created


def encode_commit(repo_root: pathlib.Path, message: str, commit_hash: str) -> bool:
    return _append_entry_at(_memory_root_for(repo_root), "commit", message,
                             commit_hash, gitignore=True)


def _write_episodic_log_at(memory_root: pathlib.Path, command: str,
                            result: ExecResult, *, gitignore: bool,
                            relative_to: pathlib.Path) -> str:
    _ensure_memory_dir_at(memory_root, gitignore=gitignore)
    stamp = time.strftime("%Y-%m-%dT%H-%M-%SZ", time.gmtime())
    log_path = memory_root / "episodic" / f"{stamp}.log"
    counter = 1
    while log_path.exists():
        log_path = memory_root / "episodic" / f"{stamp}-{counter}.log"
        counter += 1
    content = (
        f"command: {command}\n"
        f"exit code: {result.exit_code}\n"
        f"duration: {result.duration_ms}ms\n"
        f"--- stdout ---\n{result.stdout_text}\n"
        f"--- stderr ---\n{result.stderr_text}\n"
    )
    log_path.write_text(content)
    return str(log_path.relative_to(relative_to))


def encode_remember(repo_root: pathlib.Path, note: str,
                     last_command: str | None,
                     last_result: ExecResult | None) -> bool:
    pointer = None
    if last_command is not None and last_result is not None:
        pointer = _write_episodic_log_at(
            _memory_root_for(repo_root), last_command, last_result,
            gitignore=True, relative_to=repo_root)
    return _append_entry_at(_memory_root_for(repo_root), "remember", note,
                             pointer, gitignore=True)


def encode_general_remember(note: str, last_command: str | None,
                             last_result: ExecResult | None) -> bool:
    root = general_memory_root()
    pointer = None
    if last_command is not None and last_result is not None:
        pointer = _write_episodic_log_at(
            root, last_command, last_result,
            gitignore=False, relative_to=root)
    return _append_entry_at(root, "remember", note, pointer, gitignore=False)


def _load_memory_block_at(memory_root: pathlib.Path,
                           max_chars: int) -> tuple[str, int]:
    path = _semantic_path(memory_root)
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


def load_memory_block(repo_root: pathlib.Path,
                       max_chars: int = _MAX_MEMORY_CHARS) -> tuple[str, int]:
    return _load_memory_block_at(_memory_root_for(repo_root), max_chars)


def load_general_memory_block(
        max_chars: int = _MAX_MEMORY_CHARS) -> tuple[str, int]:
    return _load_memory_block_at(general_memory_root(), max_chars)


def encode_latest_commit(repo_root: pathlib.Path) -> bool | None:
    try:
        result = subprocess.run(
            ["git", "log", "-1", "--format=%H%n%B"],
            cwd=str(repo_root), capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    output = result.stdout
    newline_idx = output.find("\n")
    if newline_idx == -1:
        return None
    commit_hash = output[:newline_idx].strip()
    message = output[newline_idx + 1:].strip()
    if not commit_hash or not message:
        return None
    return encode_commit(repo_root, message, commit_hash)


def _forget_at(memory_root: pathlib.Path) -> None:
    if memory_root.exists():
        shutil.rmtree(memory_root)


def forget(repo_root: pathlib.Path) -> None:
    _forget_at(_memory_root_for(repo_root))


def _most_recent_gist_at(memory_root: pathlib.Path) -> str | None:
    path = _semantic_path(memory_root)
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


def most_recent_gist(repo_root: pathlib.Path) -> str | None:
    return _most_recent_gist_at(_memory_root_for(repo_root))


_CRASH_SIGNALS = frozenset({
    signal.SIGILL, signal.SIGABRT, signal.SIGFPE,
    signal.SIGSEGV, signal.SIGBUS,
})


def is_crash_signal(signum: int | None) -> bool:
    return signum in _CRASH_SIGNALS


def _crash_text(command: str, result: ExecResult) -> str:
    sig_name = signal.Signals(result.signal).name
    return f"{command} crashed with {sig_name} (exit {result.exit_code})"


def encode_crash(repo_root: pathlib.Path, command: str,
                  result: ExecResult) -> bool:
    pointer = _write_episodic_log_at(
        _memory_root_for(repo_root), command, result,
        gitignore=True, relative_to=repo_root)
    return _append_entry_at(_memory_root_for(repo_root), "crash",
                             _crash_text(command, result), pointer,
                             gitignore=True)


def encode_general_crash(command: str, result: ExecResult) -> bool:
    root = general_memory_root()
    pointer = _write_episodic_log_at(
        root, command, result, gitignore=False, relative_to=root)
    return _append_entry_at(root, "crash", _crash_text(command, result),
                             pointer, gitignore=False)
```

That is the complete file — copy it verbatim. Nothing here is a sketch.

### Design points worth calling out explicitly

1. **Every public function's pointer/path behavior for project memory is
   byte-identical to today's.** `encode_remember`'s episodic pointer is
   still relative to `repo_root` (e.g. `.sasha/memory/episodic/<stamp>.log`)
   — the `relative_to=repo_root` argument in the refactored version
   reproduces exactly what `str(log_path.relative_to(repo_root))`
   produced in the current code. Do not accidentally make project
   pointers relative to the memory root instead — that would be a silent
   behavior change `test_task_17_1_memory.py` is specifically there to
   catch.
2. **General memory's pointers are relative to the *general memory
   root*, not `repo_root`** (there is no repo root for general memory) —
   `encode_general_remember`/`encode_general_crash` pass
   `relative_to=root` where `root = general_memory_root()`, giving
   shorter pointers like `episodic/<stamp>.log`.
3. **`gitignore=False` for every general-memory code path.** General
   memory lives in `~/.config/sasha/memory/`, which is not inside an
   arbitrary git working tree the way project directories are — no
   `.gitignore` is ever written there. `PERSONALITY.md`/`config.toml`
   already live unprotected in that same directory today.
4. **`is_crash_signal`** is a small new public helper (not explicitly in
   the design doc's pseudocode, but a natural, minimal addition) so
   `repl.py` (Task 18.3) doesn't need to duplicate the `_CRASH_SIGNALS`
   set — it just calls `memory.is_crash_signal(result.signal)`.
5. **`encode_crash`/`encode_general_crash` are brand new** — there is no
   Phase 17 backward-compatibility constraint on their shape, only
   internal consistency with `encode_remember`'s pointer convention
   (point 1/2 above), which the design deliberately mirrors.

## Constraints
- Modify only `src/sasha/memory.py`. Do not touch `src/sasha/tools.py`,
  `src/sasha/session.py`, `src/sasha/repl.py`, `src/sasha/executor.py`,
  `README.md`, or anything under `docs/`.
- Every function listed under "What already exists" must be callable
  exactly as it is today — same parameter names, same order, same
  defaults, same return type, same exceptions (none — all failure modes
  return `None`/`False`/empty values as they already do). This is
  verified mechanically: `tests/contract/test_task_17_1_memory.py` is
  part of your gate and must pass completely unmodified.

## Acceptance criteria
(encoded in `tests/contract/test_task_18_1_memory_general_crash.py`, 13
new tests, plus `tests/contract/test_task_17_1_memory.py`'s existing 21
tests re-run unmodified as a regression gate — 34 tests total exercise
this task)

1. `general_memory_root()` respects `XDG_CONFIG_HOME` when set, falls
   back to `~/.config/sasha/memory` when unset.
2. `encode_general_remember`: writes no `.gitignore`; produces a
   `"remember"`-kind entry with the exact note text and `pointer: null`
   when no command is in scope; when a command is in scope, the pointer
   starts with `"episodic/"` (relative to the general root, not a repo
   root) and the log file contains the command.
3. `load_general_memory_block`: `("", 0)` when nothing's been written;
   correct count and rendered text after multiple `encode_general_remember`
   calls.
4. `is_crash_signal`: `True` for `SIGILL`/`SIGABRT`/`SIGFPE`/`SIGSEGV`/
   `SIGBUS`; `False` for `SIGKILL`, `SIGTERM`, and `None`.
5. `encode_crash`: writes both a `"crash"`-kind semantic entry (text
   exactly `"{command} crashed with {SIGNAL_NAME} (exit {code})"`) and a
   verbatim episodic log; the pointer follows `encode_remember`'s exact
   convention (relative to `repo_root`, `.sasha/memory/episodic/...`).
6. `encode_general_crash`: same shape as `encode_crash`, targeting
   `general_memory_root()`, no `.gitignore` written, pointer relative to
   the general root.
7. **Every existing `tests/contract/test_task_17_1_memory.py` assertion
   passes unmodified** — the acceptance proof that
   `encode_commit`/`encode_remember`/`load_memory_block`/
   `most_recent_gist`/`ensure_memory_dir`/`forget`/`encode_latest_commit`/
   `resolve_git_root` are behaviorally untouched by this refactor.

## Standing instructions
- Your goal: make `tests/contract/test_task_18_1_memory_general_crash.py`
  pass, **and** keep `tests/contract/test_task_17_1_memory.py` passing
  completely unmodified, and keep the full contract suite green:
  `PYTHONPATH=src python3 -m pytest tests/contract/`.
- NEVER modify files under `tests/contract/` — the gate rejects the
  commit if you do. This includes `test_task_17_1_memory.py`: if you
  find yourself wanting to change it to make it pass, that's a signal
  your refactor introduced a real behavior change — stop and re-check
  against the target file above instead.
- Deliver exactly ONE new commit on top of the HEAD you started from;
  never amend, rebase, or reset; leave the working tree clean.
- Commit message per `AGENTS.md`, with trailers: `Done: Task 18.1 — <one
  line>`, `Next: Task 18.2 — remember tool general-memory fallback +
  Session's second memory slot`, and the `Co-Authored-By:` signature
  line from your dispatch prompt.
- Greenfield-vs-surgical routing: this task refactors an existing file's
  internals behind an unchanged public contract, and adds new functions
  alongside it — a precise, fully-specified transformation, not a
  rewrite of behavior. Dispatch to the default model (deepseek-v4-pro);
  do not use a `SASHA_DISPATCH_MODEL` local-model override.
