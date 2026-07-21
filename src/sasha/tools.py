from __future__ import annotations

import dataclasses
import pathlib
import re
from typing import Callable

from sasha import memory
from sasha import safe_commands
from sasha.executor import Executor

_READ_FILE_MAX_CHARS = 40000
_LIST_DIR_MAX_ENTRIES = 200
_SEARCH_MAX_MATCHES = 100


@dataclasses.dataclass(frozen=True)
class ToolResult:
    text: str


@dataclasses.dataclass(frozen=True)
class Tool:
    name: str
    schema: dict
    requires_confirmation: bool | Callable[[dict, Executor], bool]
    confirmation_summary: Callable[[dict], str] | None
    handler: Callable[[dict, Executor], ToolResult]
    repetition_key: Callable[[dict], object]
    announce_when_auto_approved: bool = False


def _safe_path(raw_path: str, cwd: str) -> pathlib.Path | None:
    candidate = pathlib.Path(cwd) / raw_path
    try:
        resolved = candidate.resolve(strict=False)
        root = pathlib.Path(cwd).resolve(strict=False)
    except OSError:
        return None
    if resolved != root and root not in resolved.parents:
        return None
    return resolved


def _run_command_result(args: dict, executor: Executor) -> str:
    result = executor.run(args.get("command", ""), capture_stdout=True)
    return (f"exit code: {result.exit_code}\nstdout:\n{result.stdout_text}"
            f"\nstderr:\n{result.stderr_text}")


def _write_file_handler(args: dict, executor: Executor) -> ToolResult:
    raw_path = args.get("path", "")
    content = args.get("content", "")
    resolved = pathlib.Path(executor.cwd) / raw_path
    try:
        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_text(content)
    except OSError as exc:
        return ToolResult(f"error writing {raw_path}: {exc}")
    return ToolResult(f"wrote {len(content)} bytes to {raw_path}")


def _read_file_handler(args: dict, executor: Executor) -> ToolResult:
    raw_path = args.get("path", "")
    resolved = _safe_path(raw_path, executor.cwd)
    if resolved is None:
        return ToolResult(
            f"path escapes the current working directory: {raw_path}")
    try:
        text = resolved.read_text()
    except FileNotFoundError:
        return ToolResult(f"no such file: {raw_path}")
    except OSError as exc:
        return ToolResult(f"error reading {raw_path}: {exc}")
    if len(text) <= _READ_FILE_MAX_CHARS:
        return ToolResult(text)
    return ToolResult(
        text[:_READ_FILE_MAX_CHARS]
        + f"\n... [truncated at {_READ_FILE_MAX_CHARS} characters]")


def _list_dir_handler(args: dict, executor: Executor) -> ToolResult:
    raw_path = args.get("path", ".")
    resolved = _safe_path(raw_path, executor.cwd)
    if resolved is None:
        return ToolResult(
            f"path escapes the current working directory: {raw_path}")
    if not resolved.exists():
        return ToolResult(f"no such directory: {raw_path}")
    if not resolved.is_dir():
        return ToolResult(f"not a directory: {raw_path}")
    try:
        entries = sorted(resolved.iterdir())
    except OSError as exc:
        return ToolResult(f"error listing {raw_path}: {exc}")
    lines = [f"{e.name}/" if e.is_dir() else e.name for e in entries]
    total = len(lines)
    if total > _LIST_DIR_MAX_ENTRIES:
        shown = lines[:_LIST_DIR_MAX_ENTRIES]
        shown.append(f"... [{total - _LIST_DIR_MAX_ENTRIES} more entries not shown]")
        return ToolResult("\n".join(shown))
    return ToolResult("\n".join(lines))


def _search_files_handler(args: dict, executor: Executor) -> ToolResult:
    raw_path = args.get("path", ".")
    pattern = args.get("pattern", "")
    resolved = _safe_path(raw_path, executor.cwd)
    if resolved is None:
        return ToolResult(
            f"path escapes the current working directory: {raw_path}")
    try:
        regex = re.compile(pattern)
    except re.error as exc:
        return ToolResult(f"invalid pattern: {exc}")
    if not resolved.exists():
        return ToolResult(f"no such path: {raw_path}")
    matches: list[str] = []
    total = 0
    for file_path in sorted(resolved.rglob("*")):
        if file_path.is_dir():
            continue
        try:
            text = file_path.read_text()
        except (UnicodeDecodeError, OSError):
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            if regex.search(line):
                total += 1
                if len(matches) < _SEARCH_MAX_MATCHES:
                    relpath = file_path.relative_to(resolved)
                    matches.append(f"{relpath}:{lineno}: {line}")
    if not matches:
        return ToolResult("no matches")
    if total > _SEARCH_MAX_MATCHES:
        matches.append(f"... [{total - _SEARCH_MAX_MATCHES} more matches not shown]")
    return ToolResult("\n".join(matches))


def _remember_handler(args: dict, executor: Executor) -> ToolResult:
    note = args.get("note", "")
    root = memory.resolve_git_root(executor.cwd)
    if root is not None:
        memory.encode_remember(root, note, executor.last_command, executor.last_result)
    else:
        memory.encode_general_remember(note, executor.last_command, executor.last_result)
    return ToolResult(f"remembered: {note}")


REGISTRY: dict[str, Tool] = {
    "run_command": Tool(
        name="run_command",
        schema={
            "type": "function",
            "function": {
                "name": "run_command",
                "description": (
                    "Propose one bash command to run in the user's shell. "
                    "The user sees the command and your description and "
                    "must approve it before it runs."),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "command": {"type": "string",
                                    "description": "exact bash command"},
                        "description": {
                            "type": "string",
                            "description": "one line: what this does and why"},
                    },
                    "required": ["command", "description"],
                },
            },
        },
        requires_confirmation=safe_commands.requires_confirmation,
        confirmation_summary=lambda args: args.get("command", ""),
        handler=lambda args, executor: ToolResult(_run_command_result(args, executor)),
        repetition_key=lambda args: args.get("command", ""),
        announce_when_auto_approved=True,
    ),
    "write_file": Tool(
        name="write_file",
        schema={
            "type": "function",
            "function": {
                "name": "write_file",
                "description": (
                    "Write content to a file, creating parent directories "
                    "as needed. Overwrites the file if it already exists. "
                    "The user sees the path, size, and your description "
                    "and must approve before anything is written."),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string",
                                 "description": "file path, absolute or relative to the current directory"},
                        "content": {"type": "string",
                                    "description": "exact file content"},
                        "description": {
                            "type": "string",
                            "description": "one line: what this does and why"},
                    },
                    "required": ["path", "content", "description"],
                },
            },
        },
        requires_confirmation=True,
        confirmation_summary=lambda args: (
            f"write_file: {args.get('path', '')} "
            f"({len(args.get('content', ''))} bytes)"),
        handler=_write_file_handler,
        repetition_key=lambda args: (args.get("path", ""), args.get("content", "")),
    ),
    "read_file": Tool(
        name="read_file",
        schema={
            "type": "function",
            "function": {
                "name": "read_file",
                "description": (
                    "Read a file's contents. Restricted to the current "
                    "working directory and below; runs immediately without "
                    "asking for confirmation."),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string",
                                 "description": "file path, absolute or relative to the current directory"},
                    },
                    "required": ["path"],
                },
            },
        },
        requires_confirmation=False,
        confirmation_summary=None,
        handler=_read_file_handler,
        repetition_key=lambda args: args,
    ),
    "list_dir": Tool(
        name="list_dir",
        schema={
            "type": "function",
            "function": {
                "name": "list_dir",
                "description": (
                    "List a directory's entries (names only, one per line, "
                    "directories suffixed with '/'). Restricted to the "
                    "current working directory and below; runs immediately "
                    "without asking for confirmation."),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string",
                                 "description": "directory path, absolute or relative to the current directory; defaults to the current directory"},
                    },
                    "required": [],
                },
            },
        },
        requires_confirmation=False,
        confirmation_summary=None,
        handler=_list_dir_handler,
        repetition_key=lambda args: args,
    ),
    "search_files": Tool(
        name="search_files",
        schema={
            "type": "function",
            "function": {
                "name": "search_files",
                "description": (
                    "Search file contents for a regex pattern under a "
                    "directory, recursively. Returns matching "
                    "'path:line: text' lines. Restricted to the current "
                    "working directory and below; runs immediately without "
                    "asking for confirmation."),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "pattern": {"type": "string",
                                    "description": "regex pattern to search for"},
                        "path": {"type": "string",
                                 "description": "directory to search under, absolute or relative to the current directory; defaults to the current directory"},
                    },
                    "required": ["pattern"],
                },
            },
        },
        requires_confirmation=False,
        confirmation_summary=None,
        handler=_search_files_handler,
        repetition_key=lambda args: args,
    ),
    "remember": Tool(
        name="remember",
        schema={
            "type": "function",
            "function": {
                "name": "remember",
                "description": (
                    "Save a durable note for future sessions — a decision, "
                    "a conclusion, a fact worth not re-discovering. Saved "
                    "against the current project if you're in a "
                    "git-tracked directory, or as a general note otherwise. "
                    "If a command has run recently, its exact output is "
                    "automatically attached as a verbatim record."),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "note": {"type": "string",
                                 "description": "one or two sentences: what's worth remembering and why"},
                    },
                    "required": ["note"],
                },
            },
        },
        requires_confirmation=False,
        confirmation_summary=None,
        handler=_remember_handler,
        repetition_key=lambda args: args.get("note", ""),
    ),
}
