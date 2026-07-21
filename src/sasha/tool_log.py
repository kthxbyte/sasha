from __future__ import annotations

import datetime
import json
import os
import pathlib
import sys
import uuid

_session_id = str(uuid.uuid4())
_warned = False


def _log_path() -> pathlib.Path:
    xdg = os.environ.get("XDG_STATE_HOME", "")
    if xdg:
        base = pathlib.Path(xdg) / "sasha"
    else:
        base = pathlib.Path.home() / ".local" / "state" / "sasha"
    return base / "tool_log.jsonl"


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
