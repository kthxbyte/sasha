"""Crash safety net: traceback bundles and a repeated-failure circuit breaker.

Deliberately dependency-light and failure-tolerant. This module only ever runs
*after* something has already gone wrong, so it must not assume a healthy
interpreter, a writable state directory, or a git checkout. Every public
function is safe to call on the exception path and degrades to None/empty
rather than raising.
"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import time
import traceback

MAX_TRACEBACK_CHARS = 60_000
DEFAULT_BREAKER_LIMIT = 3


def sasha_version() -> str:
    try:
        from importlib.metadata import version

        return version("sasha")
    except Exception:
        return "unknown"


def crash_dir(state_dir: pathlib.Path | None) -> pathlib.Path | None:
    if state_dir is None:
        return None
    return pathlib.Path(state_dir) / "crashes"


def signature(exc: BaseException) -> tuple[str, str, int]:
    """(exception type, file, line) of the *deepest* frame.

    The deepest frame is the most specific description of where the fault
    actually lives, so genuinely repeated failures share a signature even when
    they are reached through different top-level paths.
    """
    filename, lineno = "<unknown>", 0
    tb = exc.__traceback__
    while tb is not None:
        filename = tb.tb_frame.f_code.co_filename
        lineno = tb.tb_lineno
        tb = tb.tb_next
    return (type(exc).__name__, filename, lineno)


def describe(exc: BaseException) -> str:
    """One line: 'ValueError: bad thing' plus where it came from."""
    _, filename, lineno = signature(exc)
    kind = type(exc).__name__
    message = str(exc).strip()
    head = f"{kind}: {message}" if message else kind
    if lineno:
        try:
            where = f"{pathlib.Path(filename).name}:{lineno}"
        except Exception:
            where = f"{filename}:{lineno}"
        return f"{head}  ({where})"
    return head


class CircuitBreaker:
    """Stops the safety net from becoming an infinite traceback loop.

    A systematic fault — every consult raising in the same place — would
    otherwise be swallowed forever, turning one crash into a session that can
    never make progress. After `limit` occurrences of the same signature the
    caller is told to give up and let Sasha exit cleanly to bash.
    """

    def __init__(self, limit: int = DEFAULT_BREAKER_LIMIT) -> None:
        self.limit = limit
        self._counts: dict[tuple[str, str, int], int] = {}

    def record(self, sig: tuple[str, str, int]) -> bool:
        """Count one occurrence of `sig`; True once the limit is reached."""
        count = self._counts.get(sig, 0) + 1
        self._counts[sig] = count
        return count >= self.limit

    def count(self, sig: tuple[str, str, int]) -> int:
        return self._counts.get(sig, 0)


def git_state(start: pathlib.Path | None = None) -> dict[str, str]:
    """Best-effort revision/dirty state, so a crash report records exactly
    which code was running. Optional: takes itself out on any failure."""
    if start is None:
        start = pathlib.Path(__file__).resolve().parent
    state: dict[str, str] = {}
    try:
        head = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(start), capture_output=True, text=True, timeout=2)
        if head.returncode == 0:
            state["git_head"] = head.stdout.strip()
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(start), capture_output=True, text=True, timeout=2)
        if status.returncode == 0:
            dirty = [ln for ln in status.stdout.splitlines() if ln.strip()]
            state["git_dirty_files"] = str(len(dirty))
    except Exception:
        pass
    return state


def write_crash_bundle(state_dir: pathlib.Path | None,
                       exc: BaseException,
                       context: dict[str, str],
                       tb_text: str | None = None,
                       signature_name: tuple[str, str, int] | None = None
                       ) -> pathlib.Path | None:
    """Write a crash report and return its path, or None if it can't be
    written (no state dir, read-only filesystem, anything else).

    Nothing here is authoritative to the app: losing the report must never be
    worse than the crash that produced it.
    """
    root = crash_dir(state_dir)
    if root is None:
        return None
    try:
        root.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        sig = signature_name if signature_name is not None else signature(exc)
        # A repeating fault produces several reports inside the same second, so
        # the pid alone does not keep names unique -- and a collision would
        # silently lose every report but the last. Same existence-loop idiom as
        # memory._write_episodic_log_at.
        path = root / f"{stamp}-{os.getpid()}.txt"
        counter = 1
        while path.exists():
            path = root / f"{stamp}-{os.getpid()}-{counter}.txt"
            counter += 1

        if tb_text is None:
            tb_text = "".join(
                traceback.format_exception(type(exc), exc, exc.__traceback__))
        if len(tb_text) > MAX_TRACEBACK_CHARS:
            tb_text = (tb_text[:MAX_TRACEBACK_CHARS]
                       + "\n... [traceback truncated]\n")

        lines = [
            "# Sasha crash report",
            f"when: {stamp}",
            f"sasha: {sasha_version()}",
            f"exception: {type(exc).__name__}",
            f"site: {sig[1]}:{sig[2]}",
            "",
            "# context",
        ]
        for key in sorted(context):
            lines.append(f"{key}: {context[key]}")
        lines += ["", "# traceback", tb_text.rstrip(), ""]

        # errors="replace": the report must be written even if the traceback
        # holds a surrogate or a byte the locale cannot encode.
        path.write_text("\n".join(lines) + "\n", encoding="utf-8",
                        errors="replace")

        # A JSON twin, for tooling that would rather not parse the text.
        try:
            path.with_suffix(".json").write_text(json.dumps({
                "when": stamp,
                "sasha": sasha_version(),
                "exception": type(exc).__name__,
                "message": str(exc),
                "site": {"file": sig[1], "line": sig[2]},
                "context": context,
                "traceback": tb_text,
            }, indent=2) + "\n", encoding="utf-8")
        except Exception:
            pass

        return path
    except Exception:
        return None
