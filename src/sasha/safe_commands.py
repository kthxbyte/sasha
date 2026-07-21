from __future__ import annotations

import fnmatch
import os
import re
import shlex

_ALLOWLISTED_VERBS = frozenset({
    "ls", "pwd", "wc", "cat", "head", "tail", "file", "stat", "du", "find",
    "grep", "tree", "sort", "uniq", "cut", "diff", "echo",
})

_OPERATORS = frozenset({"&&", ";", "|", "||"})

_OPERATOR_RE = re.compile(r'(\|\||&&|;|\||&)')


def _tokenize(command: str) -> list[str]:
    padded = _OPERATOR_RE.sub(r' \1 ', command)
    return shlex.split(padded)


def _split_segments(tokens: list[str]) -> list[list[str]] | None:
    segments: list[list[str]] = []
    current: list[str] = []
    for tok in tokens:
        if tok == "&":
            return None
        if tok in _OPERATORS:
            segments.append(current)
            current = []
        else:
            current.append(tok)
    segments.append(current)
    if any(not seg for seg in segments):
        return None
    return segments


def _normalize(token: str, cwd: str) -> str:
    expanded = os.path.expanduser(token)
    if not os.path.isabs(expanded):
        expanded = os.path.join(cwd, expanded)
    return os.path.normpath(expanded)


def touches_sensitive_path(command: str, executor) -> bool:
    try:
        tokens = _tokenize(command)
    except ValueError:
        return False

    for tok in tokens:
        if tok in _OPERATORS or tok in _ALLOWLISTED_VERBS:
            continue
        if tok.startswith("-"):
            continue
        normalized = _normalize(tok, executor.cwd)
        for pattern in executor.sensitive_path_patterns:
            expanded_pattern = os.path.expanduser(pattern)
            if fnmatch.fnmatch(normalized, expanded_pattern):
                return True
    return False


def requires_confirmation(args: dict, executor) -> bool:
    command = args.get("command", "")

    if "`" in command or "$(" in command or ">" in command:
        return True

    try:
        tokens = _tokenize(command)
    except ValueError:
        return True

    segments = _split_segments(tokens)
    if segments is None:
        return True

    for segment in segments:
        if segment[0] not in _ALLOWLISTED_VERBS:
            return True

    for tok in tokens:
        if tok in _OPERATORS or tok in _ALLOWLISTED_VERBS:
            continue
        if "$" in tok:
            return True

    return touches_sensitive_path(command, executor)
