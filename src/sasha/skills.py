from __future__ import annotations

import os
import pathlib

_MAX_SKILL_CHARS = 40000  # matches tools.py's _READ_FILE_MAX_CHARS

USE_SKILL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "use_skill",
        "description": (
            "Load a named skill into context — a packaged set of "
            "instructions for a specific kind of task. Use one when the "
            "current task matches a listed skill's description."),
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "args": {"type": "string"},
            },
            "required": ["name"],
            "additionalProperties": False,
        },
    },
}


def _skills_root() -> pathlib.Path:
    xdg = os.environ.get("XDG_CONFIG_HOME", "")
    if xdg:
        return pathlib.Path(xdg) / "sasha" / "skills"
    return pathlib.Path.home() / ".config" / "sasha" / "skills"


def _parse_frontmatter(text: str) -> dict | None:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    result: dict[str, str] = {}
    closed = False
    for line in lines[1:]:
        if line.strip() == "---":
            closed = True
            break
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        result[key.strip()] = value.strip()
    if not closed:
        return None
    return result


def _version_sort_key(version: str) -> tuple:
    try:
        nums = tuple(int(p) for p in version.split("."))
    except ValueError:
        return (0,)
    return (1, nums)


def _discover(root: pathlib.Path | None = None) -> dict[str, pathlib.Path]:
    if root is None:
        root = _skills_root()
    if not root.is_dir():
        return {}

    plugin_versions: dict[tuple[str, str], list[pathlib.Path]] = {}
    for marketplace_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        for plugin_dir in sorted(p for p in marketplace_dir.iterdir() if p.is_dir()):
            for version_dir in sorted(p for p in plugin_dir.iterdir() if p.is_dir()):
                if (version_dir / "skills").is_dir():
                    plugin_versions.setdefault(
                        (marketplace_dir.name, plugin_dir.name), []).append(version_dir)

    result: dict[str, pathlib.Path] = {}
    for version_dirs in plugin_versions.values():
        best = max(version_dirs, key=lambda d: _version_sort_key(d.name))
        skills_dir = best / "skills"
        for skill_dir in sorted(p for p in skills_dir.iterdir() if p.is_dir()):
            skill_md = skill_dir / "SKILL.md"
            if not skill_md.is_file():
                continue
            try:
                text = skill_md.read_text()
            except OSError:
                continue
            frontmatter = _parse_frontmatter(text)
            name = None
            if frontmatter is not None:
                name = frontmatter.get("name")
            if not name:
                name = skill_dir.name
            if name not in result or str(skill_md) < str(result[name]):
                result[name] = skill_md
    return result


def build_skills_listing() -> str:
    discovered = _discover()
    entries = []
    for name in sorted(discovered):
        try:
            text = discovered[name].read_text()
        except OSError:
            continue
        frontmatter = _parse_frontmatter(text)
        if frontmatter is None:
            continue
        fm_name = frontmatter.get("name", "")
        description = frontmatter.get("description", "")
        if not fm_name or not description:
            continue
        entries.append((name, description))
    if not entries:
        return ""
    lines = "\n".join(f"- {name}: {description}" for name, description in entries)
    return (f"Available skills:\n{lines}\n\n"
            f"Use the `use_skill` tool to load one when it applies.")


def load_skill(name: str, args: str = "") -> str | None:
    discovered = _discover()
    path = discovered.get(name)
    if path is None:
        return None
    try:
        text = path.read_text()
    except OSError:
        return None
    if len(text) > _MAX_SKILL_CHARS:
        text = text[:_MAX_SKILL_CHARS] + f"\n... [truncated at {_MAX_SKILL_CHARS} characters]"
    if args:
        text += f"\n\nARGUMENTS: {args}"
    return text
