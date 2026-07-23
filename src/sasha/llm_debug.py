from __future__ import annotations

import datetime
import json
import os
import pathlib
import sys
import uuid

_session_id = str(uuid.uuid4())
_warned = False
_enabled = False


def configure(enabled: bool) -> None:
    global _enabled
    _enabled = enabled


def _log_path() -> pathlib.Path:
    xdg = os.environ.get("XDG_STATE_HOME", "")
    if xdg:
        base = pathlib.Path(xdg) / "sasha"
    else:
        base = pathlib.Path.home() / ".local" / "state" / "sasha"
    return base / "llm_debug.jsonl"


def _utc_timestamp() -> str:
    now = datetime.datetime.now(datetime.timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"


def log_request(*, profile: str, model: str, messages: list[dict],
                 tools: list[dict]) -> None:
    global _warned
    if not _enabled:
        return
    path = _log_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "ts": _utc_timestamp(),
            "session_id": _session_id,
            "profile": profile,
            "model": model,
            "messages": messages,
            "tools": tools,
        }
        line = json.dumps(entry) + "\n"
        fd = os.open(str(path), os.O_CREAT | os.O_WRONLY | os.O_APPEND, 0o600)
        with os.fdopen(fd, "a") as f:
            f.write(line)
    except Exception as exc:
        if not _warned:
            _warned = True
            print(f"[sasha] llm_debug: couldn't write to {path}: {exc} "
                  f"— LLM debug logging disabled for this session",
                  file=sys.stderr)
