from __future__ import annotations

import json
import pathlib
import subprocess
import threading
from datetime import datetime, timezone
from typing import Callable

from sasha import tool_log
from sasha.llm import ChatError, background_request

SILENT = "silent"
RECAP_DIRECT = "recap_direct"
ESCALATE = "escalate"

UMBRELLA_CHILD_REPO_THRESHOLD = 2
FANOUT_THRESHOLD = 3
STALE_DAYS = 14
MAX_CHILD_REPOS_FOR_LLM = 5
MAX_LOG_ENTRIES_FOR_LLM = 10

README_EXCERPT_CHARS = 500
TOP_LEVEL_LISTING_MAX = 20

MARKER_NAMES = {
    "package.json", "pyproject.toml", "setup.py", "cargo.toml", "go.mod",
    "gemfile", "pom.xml", "build.gradle", "makefile", "cmakelists.txt",
    "dockerfile", ".envrc", "requirements.txt", "composer.json",
}
MARKER_PREFIXES = ("readme", "license")

VENDOR_DIR_NAMES = {
    "node_modules", "venv", "site-packages", "dist", "build", "target",
    "vendor", "__pycache__",
}


def _has_project_marker(target: pathlib.Path) -> bool:
    if not _safe_is_dir(target):
        return False
    try:
        entries = list(target.iterdir())
    except (PermissionError, OSError):
        return False
    for entry in entries:
        name = entry.name.lower()
        if name in MARKER_NAMES or name.endswith(".sln"):
            return True
        if name.startswith(MARKER_PREFIXES):
            return True
    return False


def _is_vendor_or_cache(target: pathlib.Path) -> bool:
    try:
        resolved = target.resolve()
    except OSError:
        resolved = target
    return any(part.startswith(".") or part in VENDOR_DIR_NAMES
               for part in resolved.parts)


def _readme_excerpt(target: pathlib.Path) -> str | None:
    if not _safe_is_dir(target):
        return None
    try:
        entries = sorted(target.iterdir(), key=lambda p: p.name)
    except (PermissionError, OSError):
        return None
    for entry in entries:
        if entry.name.lower().startswith("readme"):
            try:
                if entry.is_file():
                    return entry.read_text(errors="replace")[:README_EXCERPT_CHARS]
            except OSError:
                return None
    return None


def _top_level_listing(target: pathlib.Path) -> list[str]:
    if not _safe_is_dir(target):
        return []
    try:
        entries = [p.name for p in target.iterdir() if not p.name.startswith(".")]
    except (PermissionError, OSError):
        return []
    return sorted(entries)[:TOP_LEVEL_LISTING_MAX]


def _is_git_repo(path: pathlib.Path) -> bool:
    try:
        return (path / ".git").exists()
    except OSError:
        return False


def _safe_is_dir(path: pathlib.Path) -> bool:
    try:
        return path.is_dir()
    except OSError:
        return False


def _child_git_repos(path: pathlib.Path) -> list[pathlib.Path]:
    if not _safe_is_dir(path):
        return []
    try:
        children = [p for p in path.iterdir()
                    if not p.name.startswith(".") and _safe_is_dir(p)]
    except (PermissionError, OSError):
        return []
    return sorted((p for p in children if _is_git_repo(p)),
                  key=lambda p: p.name)


def _last_commit(path: pathlib.Path) -> str | None:
    try:
        out = subprocess.run(
            ["git", "-C", str(path), "log", "-1", "--format=%ar\t%s"],
            capture_output=True, text=True, timeout=2,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0 or not out.stdout.strip():
        return None
    return out.stdout.strip()


def _load_log_entries() -> list[dict]:
    path = tool_log.log_path()
    entries = []
    if not path.exists():
        return entries
    with path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return entries


def _subtree_entries(target: str, entries: list[dict]) -> list[dict]:
    target_s = target.rstrip("/")
    prefix = target_s + "/"
    return [e for e in entries
            if e.get("cwd") == target_s or e.get("cwd", "").startswith(prefix)]


def _fanout(target: str, subtree: list[dict]) -> int:
    target_s = target.rstrip("/")
    children = set()
    for e in subtree:
        cwd = e.get("cwd", "")
        if cwd == target_s:
            continue
        rest = cwd[len(target_s):].lstrip("/")
        if rest:
            children.add(rest.split("/", 1)[0])
    return len(children)


def _parse_ts(ts: str) -> datetime:
    return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)


def _most_recent_age_days(subtree: list[dict]) -> float | None:
    ages = []
    for e in subtree:
        ts = e.get("ts")
        if not ts:
            continue
        try:
            ages.append(_parse_ts(ts))
        except ValueError:
            continue
    if not ages:
        return None
    most_recent = max(ages)
    return (datetime.now(timezone.utc) - most_recent).total_seconds() / 86400


def decide(target: pathlib.Path,
           log_entries: list[dict] | None = None) -> tuple[str, str | None]:
    if log_entries is None:
        log_entries = _load_log_entries()

    child_repos = _child_git_repos(target)
    self_is_repo = _is_git_repo(target)

    if len(child_repos) >= UMBRELLA_CHILD_REPO_THRESHOLD:
        return (ESCALATE, None) if self_is_repo else (SILENT, None)

    subtree = _subtree_entries(str(target), log_entries)
    if not subtree:
        if _has_project_marker(target) and not _is_vendor_or_cache(target):
            return (ESCALATE, None)
        return (SILENT, None)
    if _fanout(str(target), subtree) >= FANOUT_THRESHOLD:
        return (SILENT, None)
    age_days = _most_recent_age_days(subtree)
    if age_days is not None and age_days > STALE_DAYS:
        return (SILENT, None)

    if self_is_repo:
        commit = _last_commit(target)
        if commit is not None:
            age, subject = commit.split("\t", 1)
            return (RECAP_DIRECT,
                    f'[sasha] {target.name}: last commit {age} — "{subject}"')
        return (ESCALATE, None)
    return (ESCALATE, None)


def gather_context(target: pathlib.Path,
                    log_entries: list[dict] | None = None) -> dict:
    if log_entries is None:
        log_entries = _load_log_entries()

    self_commit = _last_commit(target)
    child_repos = _child_git_repos(target)[:MAX_CHILD_REPOS_FOR_LLM]
    child_commits = [(p.name, _last_commit(p)) for p in child_repos]

    subtree = _subtree_entries(str(target), log_entries)

    def _sort_key(e):
        ts = e.get("ts", "")
        try:
            return _parse_ts(ts)
        except ValueError:
            return datetime.min.replace(tzinfo=timezone.utc)

    recent = sorted(subtree, key=_sort_key, reverse=True)[:MAX_LOG_ENTRIES_FOR_LLM]
    recent_commands = [(e.get("executed_command")
                         or e.get("arguments", {}).get("command", ""),
                         e.get("ts", "")) for e in recent]

    result = {
        "target_name": target.name,
        "self_commit": self_commit,
        "child_commits": child_commits,
        "recent_commands": recent_commands,
    }
    if not self_commit and not child_commits and not recent_commands:
        result["readme_excerpt"] = _readme_excerpt(target)
        result["top_level_listing"] = _top_level_listing(target)
    return result


def build_prompt(context: dict) -> str:
    parts = [
        "You are producing a one-line recap for a terminal assistant, "
        "shown right after the user changed into this directory. Given "
        "the data below, write EXACTLY ONE short line describing what "
        "this place is or what's been happening, in a plain factual "
        "voice (e.g. \"mqtt: bridges MQTT credentials from the iotd "
        "firmware repo to an external consumer\"). Use only the data "
        "given below -- do not invent anything. If nothing coherent can "
        "be said, output nothing at all (an empty response) -- do not "
        "guess or pad with filler.",
        f"Directory: {context['target_name']}",
    ]
    if context["self_commit"]:
        parts.append(f"Last commit here: {context['self_commit']}")
    if context["child_commits"]:
        parts.append("Nested repositories:")
        for name, commit in context["child_commits"]:
            parts.append(f"  - {name}: {commit or '(no commits)'}")
    if context["recent_commands"]:
        parts.append("Recent commands run here:")
        for command, ts in context["recent_commands"]:
            parts.append(f"  - {command} ({ts})")
    if context.get("readme_excerpt") or context.get("top_level_listing"):
        parts.append("Directory contents:")
        if context.get("readme_excerpt"):
            parts.append(f"  README excerpt: {context['readme_excerpt']}")
        if context.get("top_level_listing"):
            parts.append(f"  Files here: {', '.join(context['top_level_listing'])}")
    return "\n".join(parts)


def escalate_async(target: pathlib.Path, client_factory, profile,
                    on_result: Callable[[str], None]) -> threading.Thread:
    def _run():
        context = gather_context(target)
        prompt = build_prompt(context)
        try:
            client = client_factory(profile)
            # Best-effort: skipped, or abandoned mid-stream, the moment the
            # user asks something of their own.
            with background_request():
                response = client.chat(
                    [{"role": "user", "content": prompt}], [], lambda _: None)
        except ChatError:
            return
        text = response.text.strip()
        if text:
            on_result(f"[sasha] {text}")

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    return thread
