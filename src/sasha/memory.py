from __future__ import annotations

import json
import os
import pathlib
import shutil
import signal
import subprocess
import threading
import time

from sasha.executor import ExecResult

_MAX_MEMORY_CHARS = 8000

_WRITE_LOCK = threading.Lock()


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
        gitignore_path.write_text("*\n", encoding="utf-8")
    memory_root.mkdir(parents=True, exist_ok=True)
    (memory_root / "episodic").mkdir(exist_ok=True)
    return memory_root, True


def ensure_memory_dir(repo_root: pathlib.Path) -> tuple[pathlib.Path, bool]:
    return _ensure_memory_dir_at(_memory_root_for(repo_root), gitignore=True)


def _append_entry_at(memory_root: pathlib.Path, kind: str, text: str,
                      pointer: str | None, *, gitignore: bool) -> bool:
    with _WRITE_LOCK:
        _, just_created = _ensure_memory_dir_at(memory_root, gitignore=gitignore)
        entry = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "kind": kind,
            "text": text,
            "pointer": pointer,
        }
        path = _semantic_path(memory_root)
        with open(path, "a", encoding="utf-8") as f:
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
    log_path.write_text(content, encoding="utf-8")
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
    # errors="replace": one undecodable byte in a memory file must not stop
    # Sasha starting (see ISSUE-072); a mangled character beats a dead REPL.
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
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


def bounded_history(repo_root: pathlib.Path,
                     max_chars: int = 10_000) -> list[tuple[str, str]]:
    """Newest-first list of (commit_hash, full_message) pairs, read via
    one `git log` call, accumulated until the next commit's message would
    push the running total past max_chars — except the first (newest)
    commit is always included regardless of size. Returns [] if the repo
    has no commits, `git` is missing, or the subprocess fails/times out.
    """
    try:
        result = subprocess.run(
            ["git", "log", "--format=%H%x1f%B%x1e"],
            cwd=str(repo_root), capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if result.returncode != 0:
        return []
    kept: list[tuple[str, str]] = []
    used = 0
    for piece in result.stdout.split("\x1e"):
        if "\x1f" not in piece:
            continue
        commit_hash, _, message = piece.partition("\x1f")
        commit_hash = commit_hash.strip()
        message = message.strip()
        if kept and used + len(message) > max_chars:
            break
        kept.append((commit_hash, message))
        used += len(message)
    return kept


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
    for line in path.read_text(encoding="utf-8",
                                errors="replace").splitlines():
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
