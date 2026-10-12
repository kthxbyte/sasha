import dataclasses
import os
import pathlib

_MAX_CHARS = 8000

DEFAULT_TEMPLATE = """\
# Sasha — Personality File

This file lets you customize Sasha's tone and character. Sasha includes
this file's content directly in the system prompt sent to the LLM on
every request — there's no required structure, write whatever guides the
voice and behavior you want.

A few things people include:
- **Identity** — who Sasha is, its role, its values
- **Tone & voice** — formal or casual, humor style, verbosity
- **Behavior notes** — how to react to errors, uncertainty, or edge cases

Sasha's core functional behavior (confirmation gates before running
commands, platform/shell context) is always included after this file's
content — personality shapes tone, not safety behavior.

Edit this file freely. Changes take effect the next time you start Sasha.
"""


@dataclasses.dataclass(frozen=True)
class PersonalityResult:
    text: str
    notice: str | None


def _personality_path() -> pathlib.Path:
    xdg = os.environ.get("XDG_CONFIG_HOME", "")
    if xdg:
        return pathlib.Path(xdg) / "sasha" / "PERSONALITY.md"
    return pathlib.Path.home() / ".config" / "sasha" / "PERSONALITY.md"


def load_personality(path: pathlib.Path | None = None) -> PersonalityResult:
    if path is None:
        path = _personality_path()
    if not path.exists():
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(DEFAULT_TEMPLATE, encoding="utf-8")
            return PersonalityResult(
                DEFAULT_TEMPLATE,
                f"[sasha] created {path} — edit it to customize my tone/character",
            )
        except OSError:
            return PersonalityResult(
                DEFAULT_TEMPLATE,
                f"[sasha] couldn't create {path} — using the default personality for this session",
            )
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return PersonalityResult(
            DEFAULT_TEMPLATE,
            f"[sasha] couldn't read {path} — using the default personality for this session",
        )
    if len(text) > _MAX_CHARS:
        return PersonalityResult(
            text[:_MAX_CHARS],
            f"[sasha] {path} is over {_MAX_CHARS} characters — truncated for this session",
        )
    return PersonalityResult(text, None)
