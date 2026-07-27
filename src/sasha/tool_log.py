from __future__ import annotations

import dataclasses
import datetime
import json
import os
import pathlib
import sys
import threading
import uuid

_session_id = str(uuid.uuid4())
_warned = False
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


def compact(max_size_mb: float | None = None) -> CompactionResult:
    if max_size_mb is None:
        max_size_mb = _max_size_mb
    max_bytes = int(max_size_mb * 1_000_000)
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


def _log_path() -> pathlib.Path:
    xdg = os.environ.get("XDG_STATE_HOME", "")
    if xdg:
        base = pathlib.Path(xdg) / "sasha"
    else:
        base = pathlib.Path.home() / ".local" / "state" / "sasha"
    return base / "tool_log.jsonl"


def log_path() -> pathlib.Path:
    return _log_path()


def _utc_timestamp() -> str:
    now = datetime.datetime.now(datetime.timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"


def log_call(*, tool_name: str, call_id: str, arguments: dict,
             executed_command: str | None, cwd: str, verdict: str,
             executed: bool, result_text: str | None,
             result_omitted_reason: str | None) -> None:
    global _warned
    path = _log_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "ts": _utc_timestamp(),
            "session_id": _session_id,
            "cwd": cwd,
            "tool": tool_name,
            "call_id": call_id,
            "arguments": arguments,
            "executed_command": executed_command,
            "verdict": verdict,
            "executed": executed,
            "result": result_text,
            "result_omitted_reason": result_omitted_reason,
        }
        line = json.dumps(entry) + "\n"
        fd = os.open(str(path), os.O_CREAT | os.O_WRONLY | os.O_APPEND, 0o600)
        with os.fdopen(fd, "a") as f:
            f.write(line)
    except Exception as exc:
        if not _warned:
            _warned = True
            print(f"[sasha] tool_log: couldn't write to {path}: {exc} "
                  f"— tool-call logging disabled for this session",
                  file=sys.stderr)
