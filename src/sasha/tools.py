from __future__ import annotations

import contextlib
import difflib
import dataclasses
import os
import pathlib
import re
import stat
import tempfile
from typing import Callable

from sasha import memory
from sasha import question
from sasha import safe_commands
from sasha import skills
from sasha.executor import Executor

_READ_FILE_MAX_CHARS = 40000

# Files that are not valid UTF-8 are read with errors="replace" rather than
# refused: a latin-1 text file is more useful read-with-U+FFFD than not read at
# all, and a binary file read this way is obviously binary. The model is told
# which happened.
_REPLACEMENT_CHAR = "\ufffd"
_NOT_UTF8_NOTICE = ("[note: this file is not valid UTF-8; undecodable bytes are "
                    "shown as U+FFFD]\n")
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


def _atomic_write_text(path: pathlib.Path, content: str) -> None:
    """Write text without truncating an existing file first (ISSUE-072).

    `Path.write_text` opens with mode "w" -- destroying the old contents
    immediately -- and only then encodes and writes. Any failure after that
    point (a surrogate in the content, a full disk, a signal) therefore leaves
    the previous file gone. For an existing target we write a sibling temp file
    and `os.replace` it into place, which is atomic, and copy the original mode
    across so an executable script stays executable. A brand-new file has
    nothing to lose, so it is written directly and the umask applies as usual.
    """
    if not path.exists():
        with path.open("w", encoding="utf-8", newline="") as handle:
            handle.write(content)
        return
    mode = stat.S_IMODE(path.stat().st_mode)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent),
                               prefix=f".{path.name}.", suffix=".tmp")
    try:
        # newline="" everywhere: the default universal-newline translation would
        # silently rewrite CRLF as LF (and, on Windows, LF as CRLF). For an edit
        # that touches one region, that would corrupt every other line in the
        # file.
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(content)
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        # Leave no debris, but never mask the real failure.
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def _content_bytes(content: str) -> int:
    """UTF-8 byte length, which is what "bytes" should mean.

    `len(str)` is a character count; for accented text it understates the file
    size the user is being asked to approve. A lone surrogate cannot be encoded
    at all, so fall back to the character count -- the write itself will report
    the real error.
    """
    try:
        return len(content.encode("utf-8"))
    except UnicodeEncodeError:
        return len(content)


def _write_file_handler(args: dict, executor: Executor) -> ToolResult:
    raw_path = args.get("path", "")
    content = args.get("content", "")
    resolved = pathlib.Path(executor.cwd) / raw_path
    try:
        resolved.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write_text(resolved, content)
    except OSError as exc:
        return ToolResult(f"error writing {raw_path}: {exc}")
    except UnicodeError as exc:
        # Reachable from model output, with no unusual locale involved:
        # json.loads accepts a lone \udXXX escape in tool arguments, and such a
        # str cannot be encoded to UTF-8. The existing file is untouched because
        # the write above is atomic, so this is a reportable error, not a crash.
        return ToolResult(
            f"error writing {raw_path}: content is not encodable as UTF-8 ({exc})")
    return ToolResult(f"wrote {_content_bytes(content)} bytes to {raw_path}")


# Editing is tolerant in tiers, the way Aider's SEARCH/REPLACE is, because the
# models Sasha runs get whitespace slightly wrong and a bare "not found" costs a
# whole turn to recover from. Exact matching is still tried first and is the only
# tier that can match part of a line; the others are line-based and all of them
# must be UNIQUE, so tolerance never means guessing.
_ELISION_MARKERS = ("...", "# ...", "// ...", "# ... existing code ...",
                    "# ... (existing code) ...")


def _line_spans(text: str) -> list[tuple[int, int, str]]:
    """(start, end, line-without-terminator) for every line, offsets preserved."""
    spans = []
    offset = 0
    for line in text.splitlines(keepends=True):
        body = line.rstrip("\r\n")
        spans.append((offset, offset + len(line), body))
        offset += len(line)
    if offset < len(text):                       # a final line with no terminator
        spans.append((offset, len(text), text[offset:]))
    return spans


def _find_line_span(text: str, old: str, *, ignore_whitespace: bool,
                    include_last_terminator: bool = False):
    """Offset span of a UNIQUE line-run match, or None, or "ambiguous"."""
    spans = _line_spans(text)
    wanted = old.splitlines()
    if not wanted or len(wanted) > len(spans):
        return None
    if ignore_whitespace:
        def same(a, b):
            return a.strip() == b.strip()
    else:
        def same(a, b):
            return a == b

    ends_with_newline = (include_last_terminator
                         or old.endswith(("\n", "\r")))
    hits = []
    for index in range(len(spans) - len(wanted) + 1):
        window = spans[index:index + len(wanted)]
        if all(same(body, want) for (_, _, body), want in zip(window, wanted)):
            # Mirror what was quoted: if old_string did not end with a newline,
            # the span stops at the end of the last line's text and leaves the
            # file's own terminator in place (otherwise replacing "b\nc" in
            # "a\nb\nc" would silently swallow the final newline).
            stop = window[-1][1] if ends_with_newline else (
                window[-1][0] + len(window[-1][2]))
            hits.append((window[0][0], stop))
            if len(hits) > 1:
                return "ambiguous"
    return hits[0] if hits else None


def _suggest_match(text: str, old: str) -> str:
    """The closest real line to the first line of old_string, for a correction."""
    first = next((ln for ln in old.splitlines() if ln.strip()), "")
    if not first:
        return ""
    spans = _line_spans(text)
    best = None
    for number, (_, _, body) in enumerate(spans, start=1):
        if not body.strip():
            continue
        ratio = difflib.SequenceMatcher(None, body.strip(),
                                        first.strip()).ratio()
        if best is None or ratio > best[0]:
            best = (ratio, number, body)
    if best is None or best[0] < 0.6:
        return ""
    ratio, number, body = best
    return (f"\nThe closest line in the file is line {number}: "
            f"{body.strip()[:120]!r} ({ratio:.0%} similar).")


def _with_matching_endings(span_text: str, replacement: str) -> str:
    """Keep a CRLF file CRLF: a replacement typed with LF only would leave the
    one edited region with different endings from the rest of the file."""
    if "\r\n" in span_text and "\r\n" not in replacement:
        return replacement.replace("\n", "\r\n")
    return replacement


def _edit_file_handler(args: dict, executor: Executor) -> ToolResult:
    """Replace one occurrence of old_string with new_string, tolerantly.

    Why this exists (ISSUE-076): write_file can only replace a WHOLE file, so
    changing one line of a large one meant regenerating all of it -- minutes of
    generation on a 26B, and impossible past read_file's 40000-character cap,
    because the model could never see the whole file to reproduce it.

    Matching is tried in tiers (ISSUE-079), and a tier is only used when it
    yields exactly ONE match:

      1. exact substring           -- also the only tier matching part of a line
      2. line-run, whitespace-insensitive -- indentation/trailing-space drift
      3. line-run separated by an elision marker ("...") -- quote the ends of a
         long block instead of all of it

    Measured elsewhere in the field: this is the shape Aider's SEARCH/REPLACE
    uses, and the reason is that small models produce near-miss whitespace
    constantly while a bare "not found" costs a turn to recover from.
    """
    raw_path = args.get("path", "")
    old = args.get("old_string", "")
    new = args.get("new_string", "")
    resolved = pathlib.Path(executor.cwd) / raw_path

    if not old:
        return ToolResult(
            "old_string must not be empty — use write_file to replace a whole file.")

    try:
        # newline="" so the file is read byte-faithfully; see _atomic_write_text.
        with resolved.open("r", encoding="utf-8", newline="") as handle:
            text = handle.read()
    except FileNotFoundError:
        return ToolResult(f"no such file: {raw_path}")
    except UnicodeDecodeError:
        return ToolResult(
            f"{raw_path} is not valid UTF-8, so it cannot be edited by matching "
            f"text — use run_command for a byte-level change.")
    except OSError as exc:
        return ToolResult(f"error reading {raw_path}: {exc}")

    how = ""
    start = stop = None

    # --- tier 1: exact, and the only tier that can match inside a line ---
    occurrences = text.count(old)
    if occurrences == 1:
        start = text.index(old)
        stop = start + len(old)
        how = "exact"
        # If the match begins *inside* the line's indentation, the model's
        # leading whitespace is a suffix of the real indentation -- it meant the
        # whole line and under-indented. Replacing the substring would add the
        # difference and silently produce e.g. 12 spaces where 8 were meant, so
        # take the line-aligned interpretation when it is unique.
        if (old[:1] in (" ", "\t") and start > 0
                and text[start - 1] in (" ", "\t")):
            span = _find_line_span(text, old, ignore_whitespace=True)
            if span and span != "ambiguous":
                start, stop = span
                how = "whitespace-insensitive"
    elif occurrences > 1:
        return ToolResult(
            f"old_string appears {occurrences} times in {raw_path}, so the edit "
            f"is ambiguous and nothing was changed. Include more of the "
            f"surrounding text to make it unique.")

    # --- tier 2: line-run, ignoring leading/trailing whitespace ---
    # Not restricted to multi-line old_string: a single line with trailing
    # whitespace or different indentation is the commonest drift of all, and
    # ambiguity is caught below, so tolerance here never means guessing.
    if start is None:
        span = _find_line_span(text, old, ignore_whitespace=True)
        if span == "ambiguous":
            return ToolResult(
                f"old_string matches more than one place in {raw_path} once "
                f"whitespace is ignored, so nothing was changed. Include more "
                f"surrounding text to make it unique.")
        if span:
            start, stop = span
            how = "whitespace-insensitive"

    # --- tier 3: an elision marker stands for lines the model did not quote ---
    if start is None:
        marker = next((m for m in _ELISION_MARKERS
                       if any(ln.strip() == m for ln in old.splitlines())), None)
        if marker is not None:
            head, _, tail = old.partition(marker)
            # The newlines either side of the marker are separators, not lines.
            head, tail = head.strip("\r\n"), tail.strip("\r\n")
            head_span = (_find_line_span(text, head, ignore_whitespace=True)
                         if head.strip() else None)
            # An elided edit replaces a whole BLOCK, so the tail line's own
            # terminator belongs to the span -- unlike a plain edit, where the
            # model's quoted ending decides.
            tail_span = (_find_line_span(text, tail, ignore_whitespace=True,
                                         include_last_terminator=True)
                         if tail.strip() else None)
            if head_span == "ambiguous" or tail_span == "ambiguous":
                return ToolResult(
                    f"the text either side of {marker!r} matches more than one "
                    f"place in {raw_path}, so nothing was changed. Quote more of "
                    f"the surrounding text.")
            if head_span and tail_span and head_span[1] <= tail_span[0]:
                start, stop = head_span[0], tail_span[1]
                how = f"elided on {marker!r}"

    if start is None:
        return ToolResult(
            f"old_string was not found in {raw_path}, so nothing was changed."
            + _suggest_match(text, old)
            + "\nRead the file (or use search_files) and copy the text from "
              "there. Leading/trailing whitespace may differ; an elision marker "
              "('...' on its own line) may stand in for lines you do not quote.")

    span_text = text[start:stop]
    updated = text[:start] + _with_matching_endings(span_text, new) + text[stop:]

    try:
        _atomic_write_text(resolved, updated)
    except OSError as exc:
        return ToolResult(f"error writing {raw_path}: {exc}")
    except UnicodeError as exc:
        return ToolResult(
            f"error writing {raw_path}: replacement is not encodable as UTF-8 ({exc})")

    removed = len(old.splitlines()) or 1
    added = len(new.splitlines()) or 1
    return ToolResult(
        f"edited {raw_path} ({how}): replaced {removed} line(s) with {added}")


def _read_file_handler(args: dict, executor: Executor) -> ToolResult:
    raw_path = args.get("path", "")
    resolved = _safe_path(raw_path, executor.cwd)
    if resolved is None:
        return ToolResult(
            f"path escapes the current working directory: {raw_path}")
    try:
        # errors="replace" rather than a strict read + UnicodeDecodeError catch:
        # a model exploring a repository WILL open a .pyc, an image or a zip, and
        # refusing to read it is a worse answer than showing it is not text.
        text = resolved.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return ToolResult(f"no such file: {raw_path}")
    except OSError as exc:
        return ToolResult(f"error reading {raw_path}: {exc}")
    if _REPLACEMENT_CHAR in text:
        text = _NOT_UTF8_NOTICE + text
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
            # Deliberately STRICT, unlike read_file: search results must not
            # include garbage matches found inside a .pyc or an image, so a file
            # whose bytes are not valid UTF-8 is skipped rather than replaced.
            # Pinned by test_ac_search_files_skips_binary_files.
            text = file_path.read_text(encoding="utf-8")
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


def _use_skill_handler(args: dict, executor: Executor) -> ToolResult:
    name = args.get("name", "")
    content = skills.load_skill(name, args.get("args", ""))
    if content is None:
        return ToolResult(f"no such skill: {name}")
    return ToolResult(content)


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
                    "additionalProperties": False,
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
                    "additionalProperties": False,
                },
            },
        },
        requires_confirmation=True,
        confirmation_summary=lambda args: (
            f"write_file: {args.get('path', '')} "
            f"({_content_bytes(args.get('content', ''))} bytes)"),
        handler=_write_file_handler,
        repetition_key=lambda args: (args.get("path", ""), args.get("content", "")),
    ),
    "edit_file": Tool(
        name="edit_file",
        schema={
            "type": "function",
            "function": {
                "name": "edit_file",
                "description": (
                    "Replace one piece of text in an existing file. Prefer this "
                    "to write_file for changes to an existing file: it sends "
                    "only the changed region. old_string must match exactly "
                    "once, so include a line or two of context when it is not "
                    "unique. Indentation and trailing whitespace are matched "
                    "tolerantly, and a line containing only `...` stands in for "
                    "lines in between you do not quote. The user approves the "
                    "change."),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string",
                                 "description": "file path, absolute or relative to the current directory"},
                        "old_string": {
                            "type": "string",
                            "description": "existing text to replace; must match once. A line of `...` elides lines in between"},
                        "new_string": {
                            "type": "string",
                            "description": "the replacement text; empty deletes old_string"},
                        "description": {
                            "type": "string",
                            "description": "one line: what this does and why"},
                    },
                    "required": ["path", "old_string", "new_string", "description"],
                    "additionalProperties": False,
                },
            },
        },
        requires_confirmation=True,
        confirmation_summary=lambda args: (
            f"edit_file: {args.get('path', '')}\n"
            f"  - {' | '.join(args.get('old_string', '').splitlines()[:2])[:120]}\n"
            f"  + {' | '.join(args.get('new_string', '').splitlines()[:2])[:120]}"),
        handler=_edit_file_handler,
        repetition_key=lambda args: (args.get("path", ""),
                                     args.get("old_string", ""),
                                     args.get("new_string", "")),
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
                    "additionalProperties": False,
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
                    "additionalProperties": False,
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
                    "additionalProperties": False,
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
                    "additionalProperties": False,
                },
            },
        },
        requires_confirmation=False,
        confirmation_summary=None,
        handler=_remember_handler,
        repetition_key=lambda args: args.get("note", ""),
    ),
    "question": Tool(
        name="question",
        schema=question.QUESTION_TOOL_SCHEMA,
        requires_confirmation=False,
        confirmation_summary=None,
        handler=lambda args, executor: ToolResult(
            "question tool must be handled via the dedicated agent-loop "
            "branch, not Tool.handler — this indicates a dispatch bug."),
        repetition_key=lambda args: args,
    ),
    "use_skill": Tool(
        name="use_skill",
        schema=skills.USE_SKILL_SCHEMA,
        requires_confirmation=False,
        confirmation_summary=None,
        handler=_use_skill_handler,
        repetition_key=lambda args: args,
    ),
}
