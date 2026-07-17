# Task 16.1: src/sasha/tools.py — Tool registry + 5 tools + path sandboxing

## Authoritative contract
`docs/superpowers/specs/2026-07-17-multi-tool-registry-design.md` is the
complete design record for this task — read it first, especially §4
(interface), §5.1 (path sandboxing), and §5.3 (per-tool schemas, summaries,
handlers, repetition keys). This task builds the module the design describes
in isolation — `src/sasha/agent.py` is not touched by this task; wiring the
registry into the agent loop is Task 16.2.

## What already exists
- `src/sasha/executor.py`: `Executor` class — `Executor.cwd` (property,
  returns the realpath string of the shell's current directory) and
  `Executor.run(line: str, capture_stdout: bool = False) -> ExecResult`
  (`ExecResult` has `.exit_code`, `.stdout_text`, `.stderr_text`, plus other
  fields not relevant here). This task's `run_command` handler and its
  `write_file`/`read_file`/`list_dir`/`search_files` handlers all read
  `executor.cwd`; only `run_command`'s handler calls `executor.run(...)`.
  Do not modify `executor.py`.
- `src/sasha/agent.py`: currently defines `RUN_COMMAND_TOOL` (a schema dict)
  and inlines command execution/formatting directly inside `run_consult`.
  This task does **not** touch `agent.py` — the duplication between
  `agent.py`'s current inline logic and this task's new
  `REGISTRY["run_command"]` handler is temporary and expected; Task 16.2
  removes `agent.py`'s copy and wires `run_consult` through this registry.

## What's new

New file `src/sasha/tools.py`, in full:

```python
from __future__ import annotations

import dataclasses
import pathlib
import re
from typing import Callable

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
    requires_confirmation: bool
    confirmation_summary: Callable[[dict], str] | None
    handler: Callable[[dict, Executor], ToolResult]
    repetition_key: Callable[[dict], object]


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
        requires_confirmation=True,
        confirmation_summary=lambda args: args.get("command", ""),
        handler=lambda args, executor: ToolResult(_run_command_result(args, executor)),
        repetition_key=lambda args: args.get("command", ""),
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
}
```

That is the complete file — copy it verbatim. Every function signature,
constant, and dict above is final; nothing in this file is a sketch.

### Design points worth calling out explicitly (resolving points the design
left implicit)

1. **All four file-tool error/status messages report the raw `path` argument
   as given by the caller**, never the resolved absolute path — e.g.
   `"no such file: {raw_path}"`, `"wrote {n} bytes to {raw_path}"`. This is
   deliberate and uniform across all four tools; do not switch any of them to
   report `resolved`.
2. **`search_files`' relative path in each match line is relative to the
   *search root* (`resolved`, i.e. the directory actually searched), not
   relative to `executor.cwd`.** When `path="."` these are the same thing;
   when `path` names a subdirectory they differ.
3. **`search_files` with zero matches returns `ToolResult("no matches")`**
   — not an empty string.
4. **`list_dir` on an empty (but existing, valid) directory returns
   `ToolResult("")`** — an empty string, not an error and not a placeholder
   line.
5. **`write_file`'s error path** — any `OSError` raised by either
   `resolved.parent.mkdir(...)` or `resolved.write_text(...)` is caught in
   the same `try`/`except`; the handler never raises out to its caller.

## Constraints
- Create only `src/sasha/tools.py` and its test file
  (`tests/contract/test_task_16_1_tools.py`, already committed — see
  below). Do not touch `src/sasha/agent.py`, `src/sasha/repl.py`,
  `src/sasha/executor.py`, `src/sasha/session.py`, `README.md`, or any other
  existing file.
- No new third-party dependencies — this file uses only `dataclasses`,
  `pathlib`, `re`, and `typing` from the standard library plus
  `sasha.executor.Executor`.

## Acceptance criteria
(encoded in `tests/contract/test_task_16_1_tools.py`, 37 tests — all
`test_ac_*`)

1. `REGISTRY` contains exactly the five keys `run_command`, `write_file`,
   `read_file`, `list_dir`, `search_files`.
2. `requires_confirmation` is `True` for `run_command`/`write_file`, `False`
   for the other three; `confirmation_summary` is `None` exactly for the
   three auto-approved tools and non-`None` for the two confirmation-gated
   ones.
3. Each tool's `schema` dict matches the design exactly (name, description,
   parameter properties, `required` list).
4. `confirmation_summary` for `run_command` returns the command text
   verbatim; for `write_file` it contains the path and the content's byte
   length.
5. `repetition_key` for `run_command` returns the command string alone
   (excludes `description`); for `write_file` returns `(path, content)`
   (excludes `description`); for each read-only tool returns the full
   arguments dict unchanged.
6. `_safe_path`: a relative path inside `cwd` resolves; `..` that escapes
   `cwd` returns `None`; an absolute path inside `cwd` resolves; an absolute
   path outside `cwd` returns `None`; a symlink whose target escapes `cwd`
   returns `None`.
7. `read_file`: success returns the file's exact text; a missing file
   returns `"no such file: {raw_path}"`; a path escaping the sandbox returns
   `"path escapes the current working directory: {raw_path}"`; a file over
   40000 characters is truncated to exactly 40000 characters plus the
   trailing `"\n... [truncated at 40000 characters]"` note.
8. `list_dir`: success returns sorted names, one per line, directories
   suffixed `/`; defaults to `"."` when `path` is omitted; a missing
   directory, and a path that names a file rather than a directory, each
   produce a descriptive, non-raising `ToolResult`; a directory with 201
   entries returns the first 200 (sorted) plus a trailing
   `"... [1 more entries not shown]"` line.
9. `search_files`: success returns `"{relpath}:{lineno}: {line}"` lines for
   each match; zero matches returns `"no matches"`; an invalid regex returns
   a `"invalid pattern: ..."` message without raising; a path escaping the
   sandbox is rejected the same way as the other read-only tools; a binary
   file (one that raises `UnicodeDecodeError` on `.read_text()`) is silently
   skipped, not reported as an error; 101 matches returns the first 100
   (file-then-line order) plus a trailing `"... [1 more matches not shown]"`
   line.
10. `write_file`: creates missing parent directories and writes the exact
    content, returning `"wrote {n} bytes to {raw_path}"`; overwrites an
    existing file's content; is **not** sandboxed — a path outside
    `executor.cwd` still succeeds; any `OSError` during directory creation or
    writing is caught and reported as `"error writing {raw_path}: {exc}"`,
    never raised.
11. `run_command`'s handler is a pure refactor of today's `agent.py` inline
    logic: it calls `executor.run(command, capture_stdout=True)` and formats
    `"exit code: {n}\nstdout:\n{stdout}\nstderr:\n{stderr}"` — byte-identical
    to the current format string in `agent.py`.

## Standing instructions
- Your goal: make `tests/contract/test_task_16_1_tools.py` pass, and keep
  the full contract suite green:
  `PYTHONPATH=src python3 -m pytest tests/contract/`.
- NEVER modify files under `tests/contract/` — the gate rejects the commit
  if you do. (Note: `tests/contract/test_task_2_3_agent.py`'s
  `test_ac8_tool_schema_exact` was already amended by Tier 1 in this same
  spec commit to import `REGISTRY` from `sasha.tools` instead of
  `RUN_COMMAND_TOOL` from `sasha.agent` — that amendment is already in your
  base commit; you don't need to do anything about it, it will pass once
  your `tools.py` exists with the right schema.)
- Deliver exactly ONE new commit on top of the HEAD you started from; never
  amend, rebase, or reset; leave the working tree clean.
- Commit message per `AGENTS.md`, with trailers: `Done: Task 16.1 — <one
  line>`, `Next: Task 16.2 — agent.py registry-driven dispatch +
  generalized repetition guard`, and the `Co-Authored-By:` signature line
  from your dispatch prompt.
- Greenfield routing: this task is a self-contained new module built
  entirely against a committed contract test suite — no existing control
  flow is modified. Per the project's standing executor-routing rule, this
  MAY be dispatched with a local-model `SASHA_DISPATCH_MODEL` override; the
  default (deepseek-v4-pro) is also fine and requires no extra setup.
