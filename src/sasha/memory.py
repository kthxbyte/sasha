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
