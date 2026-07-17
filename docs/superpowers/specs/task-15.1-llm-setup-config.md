# Task 15.1: config.py — self-install, api_key_required, set_api_key patcher

## Authoritative contract
`docs/superpowers/specs/2026-07-17-llm-setup-design.md` is the complete
design record for this task — read it first, especially §4 (interface)
and §5.1/§5.2 (mechanism: the exact self-install and `set_api_key` patch
algorithms). This task implements the `config.py` half of that design
only — the `repl.py`/`__main__.py` key-entry flow is Task 15.2, dispatched
separately once this task lands.

## What already exists
- `src/sasha/config.py` — every function and dataclass in the current
  file (`Profile`, `Limits`, `Config`, `ConfigError`, `_resolve_api_key`,
  `_default_config_path`, `_build_default_profiles`, `_merge_limits`,
  `load`) is being modified or extended by this task; see "What's new"
  for the exact target shape of each.
- `src/sasha/prompt.py` `DEFAULT_TEMPLATE`, `validate_template`,
  `PromptError` — unchanged, still called exactly as today from `load()`.
- `tests/contract/test_task_1_1_config.py` and
  `tests/contract/test_task_9_1_configurable_prompt.py` — the full
  existing suites for `config.py`. One assertion in the former was
  amended by Tier 1 (not you) in this same spec commit — see
  "Constraints" below. Every other assertion in both files must keep
  passing unmodified; they encode `load()` behavior this task must not
  regress (TOML override merging, api_key env/literal resolution order,
  `ConfigError` vocabulary, prompt round-tripping).

## What's new

Full target contents of `src/sasha/config.py`:

```python
from __future__ import annotations

import dataclasses
import json
import os
import pathlib
import re
import tomllib

import sasha.prompt as prompt


@dataclasses.dataclass(frozen=True)
class Profile:
    name: str
    base_url: str
    model: str
    api_key: str
    api_key_required: bool = True


@dataclasses.dataclass(frozen=True)
class Limits:
    history_tokens: int = 12000
    stderr_capture_bytes: int = 8192
    max_tool_turns: int = 10
    mute_threshold: int = 3


@dataclasses.dataclass(frozen=True)
class Config:
    default_profile: str
    profiles: dict[str, Profile]
    limits: Limits
    prompt: str = prompt.DEFAULT_TEMPLATE
    path: pathlib.Path | None = None
    notice: str | None = None


class ConfigError(Exception):
    pass


_DEFAULT_PROFILE_NAME = "llm7"

_DEFAULT_PROFILE_FIELDS_BY_NAME = {
    "llm7": {
        "base_url": "https://api.llm7.io/v1",
        "model": "gpt-oss:20b",
        "api_key_env": "",
        "api_key": "",
        "api_key_required": False,
    },
    "openai": {
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
        "api_key_env": "",
        "api_key": "",
        "api_key_required": True,
    },
    "deepseek": {
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-chat",
        "api_key_env": "DEEPSEEK_API_KEY",
        "api_key": "",
        "api_key_required": True,
    },
    "local": {
        "base_url": "http://192.168.8.253:11441/v1",
        "model": "local",
        "api_key_env": "",
        "api_key": "",
        "api_key_required": False,
    },
}

_VALID_PROFILE_FIELDS = {"base_url", "model", "api_key_env", "api_key"}
_VALID_LIMITS_FIELDS = {
    "history_tokens",
    "stderr_capture_bytes",
    "max_tool_turns",
    "mute_threshold",
}

_DEFAULT_CONFIG_TOML = """\
# Sasha config — generated on first run. Edit freely; re-running Sasha
# never overwrites this file. See README for the full option reference.
default_profile = "llm7"

# Free, keyless, streaming — works immediately, best-effort only (no
# uptime guarantee from the provider). Switch anytime with
# 'sasha profile <name>'; picking 'openai' or 'deepseek' below will ask
# you to paste an API key.
[profiles.llm7]
base_url = "https://api.llm7.io/v1"
model = "gpt-oss:20b"
api_key = ""
api_key_required = false

[profiles.openai]
base_url = "https://api.openai.com/v1"
model = "gpt-4o-mini"
api_key = ""
api_key_required = true

[profiles.deepseek]
base_url = "https://api.deepseek.com/v1"
model = "deepseek-chat"
api_key_env = "DEEPSEEK_API_KEY"
api_key = ""
api_key_required = true

[profiles.local]
base_url = "http://192.168.8.253:11441/v1"
model = "local"
api_key = ""
api_key_required = false
"""


def _resolve_api_key(profile_fields: dict) -> str:
    env_name = profile_fields.get("api_key_env", "")
    if env_name and os.environ.get(env_name):
        return os.environ[env_name]
    literal = profile_fields.get("api_key", "")
    if literal:
        return literal
    return ""


def _default_config_path() -> pathlib.Path:
    xdg = os.environ.get("XDG_CONFIG_HOME", "")
    if xdg:
        return pathlib.Path(xdg) / "sasha" / "config.toml"
    return pathlib.Path.home() / ".config" / "sasha" / "config.toml"


def _build_default_profiles() -> dict:
    return {
        name: dict(fields)
        for name, fields in _DEFAULT_PROFILE_FIELDS_BY_NAME.items()
    }


def _merge_limits(defaults: Limits, overrides: dict) -> Limits:
    kwargs = dataclasses.asdict(defaults)
    for key in _VALID_LIMITS_FIELDS:
        if key in overrides:
            kwargs[key] = overrides[key]
    return Limits(**kwargs)


def _build_config(raw: dict, path: pathlib.Path | None,
                   notice: str | None) -> Config:
    default_profile_name = raw.get("default_profile", _DEFAULT_PROFILE_NAME)
    if not isinstance(default_profile_name, str):
        default_profile_name = _DEFAULT_PROFILE_NAME

    prompt_template = raw.get("prompt", prompt.DEFAULT_TEMPLATE)
    if not isinstance(prompt_template, str):
        prompt_template = prompt.DEFAULT_TEMPLATE
    try:
        prompt.validate_template(prompt_template)
    except prompt.PromptError as exc:
        raise ConfigError(str(exc)) from exc

    profile_fields_by_name = _build_default_profiles()

    for name, table in raw.get("profiles", {}).items():
        if not isinstance(table, dict):
            continue
        existing = profile_fields_by_name.get(
            name,
            {"base_url": "", "model": "", "api_key_env": "", "api_key": "",
             "api_key_required": True},
        )
        for key, value in table.items():
            if key in _VALID_PROFILE_FIELDS and isinstance(value, str):
                existing[key] = value
            elif key == "api_key_required" and isinstance(value, bool):
                existing[key] = value
        profile_fields_by_name[name] = existing

    limits = _merge_limits(Limits(), raw.get("limits", {}))

    if default_profile_name not in profile_fields_by_name:
        raise ConfigError(
            f"default_profile '{default_profile_name}' not found in profiles"
        )

    profiles = {}
    for name, pf in profile_fields_by_name.items():
        profiles[name] = Profile(
            name=name,
            base_url=pf["base_url"],
            model=pf["model"],
            api_key=_resolve_api_key(pf),
            api_key_required=pf.get("api_key_required", True),
        )

    return Config(
        default_profile=default_profile_name,
        profiles=profiles,
        limits=limits,
        prompt=prompt_template,
        path=path,
        notice=notice,
    )


def load(path: pathlib.Path | None = None) -> Config:
    if path is None:
        path = _default_config_path()

    try:
        with open(path, "rb") as f:
            raw = tomllib.load(f)
    except FileNotFoundError:
        if path != _default_config_path():
            raise ConfigError(f"config file not found: {path}") from None
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(_DEFAULT_CONFIG_TOML)
        except OSError:
            notice = (f"[sasha] couldn't create {path} — using defaults "
                       f"for this session only")
            return _build_config({}, None, notice)
        notice = (f"[sasha] created {path} — using the free 'llm7' tier; "
                   f"'sasha profile openai' or 'sasha profile deepseek' "
                   f"to switch and add your own key")
        raw = tomllib.loads(_DEFAULT_CONFIG_TOML)
        return _build_config(raw, path, notice)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(str(exc)) from exc

    return _build_config(raw, path, None)


def set_api_key(path: pathlib.Path, profile_name: str, api_key: str) -> None:
    try:
        text = path.read_text()
    except OSError as exc:
        raise ConfigError(f"couldn't read {path}: {exc}") from exc

    lines = text.splitlines(keepends=True)
    header = f"[profiles.{profile_name}]"
    start = None
    for i, line in enumerate(lines):
        if line.strip() == header:
            start = i
            break
    if start is None:
        raise ConfigError(f"no [profiles.{profile_name}] table in {path}")

    end = len(lines)
    for i in range(start + 1, len(lines)):
        stripped = lines[i].strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            end = i
            break

    key_line = f"api_key = {json.dumps(api_key)}\n"
    key_re = re.compile(r"^\s*api_key\s*=")
    found = None
    for i in range(start + 1, end):
        if key_re.match(lines[i]):
            found = i
            break

    if found is not None:
        lines[found] = key_line
    else:
        lines.insert(end, key_line)

    try:
        path.write_text("".join(lines))
    except OSError as exc:
        raise ConfigError(f"couldn't write {path}: {exc}") from exc
```

Note the `key_re` pattern `^\s*api_key\s*=` deliberately does **not**
match `api_key_required = false` — after `api_key` that line continues
with `_required`, not whitespace-then-`=`, so the regex fails on it. This
is what AC6b's contract test below pins directly.

## Constraints
- Modify only `src/sasha/config.py`. Do not touch `src/sasha/repl.py`,
  `src/sasha/__main__.py`, `src/sasha/prompt.py`, `src/sasha/session.py`,
  `src/sasha/personality.py`, `README.md`, or anything under `docs/` —
  those are Task 15.2 (repl/`__main__` wiring) and Tier 1's own
  follow-up (README), not this task.
- No new runtime dependencies — `json` and `re` are both stdlib, same
  tier as the existing `tomllib`/`dataclasses`/`os`/`pathlib` imports.
- Every existing assertion in `tests/contract/test_task_1_1_config.py`
  and `tests/contract/test_task_9_1_configurable_prompt.py` must keep
  passing, **except** one line Tier 1 already amended in this same spec
  commit (`test_ac1_defaults_when_no_config_file` now asserts
  `cfg.default_profile == "llm7"`, not `"deepseek"` — this is a Tier-1
  contract amendment, not something you need to touch; the file is
  otherwise exactly as strict as before and is off-limits to you like
  every other file under `tests/contract/`).
- Greenfield-vs-surgical routing: this task makes precise, non-trivial
  edits to `load()`'s existing control flow (extracting `_build_config`,
  adding the self-install branch) rather than adding an isolated new
  module. Dispatch to the default model (deepseek-v4-pro); do not use a
  `SASHA_DISPATCH_MODEL` local-model override.

## Acceptance criteria
(encoded in `tests/contract/test_task_15_1_llm_setup_config.py`)

1. `load(None)` at a fresh default path (no file, no env vars) writes
   `_DEFAULT_CONFIG_TOML` verbatim to disk (parent directories created),
   and returns a `Config` with `default_profile == "llm7"`, `path`
   equal to the resolved default path, a `notice` that names the path
   and mentions `llm7`, and all four profiles (`llm7`, `openai`,
   `deepseek`, `local`) present with the exact `base_url`/`model`
   values from `_DEFAULT_PROFILE_FIELDS_BY_NAME`, `llm7.api_key_required
   is False`, `openai.api_key_required is True`,
   `deepseek.api_key_required is True`, `local.api_key_required is
   False` — `test_ac1_*`.
2. `load(<explicit non-default path>)` when that path doesn't exist
   still raises `ConfigError` and writes nothing — unchanged from
   today — `test_ac2_*`.
3. `load(None)` when a `config.toml` already exists at the default path:
   `notice is None`, `path` equals that file's path, the file's bytes
   are unchanged after the call (no overwrite) — `test_ac3_*`.
4. `load(None)` when the self-install write fails (e.g. an unwritable
   parent directory) returns a working in-memory `Config`
   (`default_profile == "llm7"`, all four profiles present) with
   `path is None` and a `notice` distinct in wording from AC1's
   success notice, and does not raise — `test_ac4_*`.
5. `api_key_required` parsing: a profile table in the TOML file that
   sets `api_key_required = false` (or `= true`) is honored exactly;
   a profile that omits the key defaults to `True`, including a
   brand-new user-added profile not in `_DEFAULT_PROFILE_FIELDS_BY_NAME`;
   a non-bool value for that key (e.g. a string) is ignored, falling
   back to the default rather than raising — `test_ac5_*`.
6. `set_api_key(path, profile_name, api_key)`:
   - (a) a profile table with no existing `api_key` line gets one
     inserted at the end of that table (immediately before the next
     `[...]` header or EOF); every other line in the file is
     byte-identical before and after — `test_ac6a_*`.
   - (b) a profile table with an existing `api_key = "old"` line has
     only that line's value replaced; a same-prefix `api_key_required`
     line in the same table is untouched — `test_ac6b_*`.
   - (c) a key value containing `"` and `\` characters round-trips
     exactly through `tomllib.load()` of the patched file —
     `test_ac6c_*`.
   - (d) a `path` whose file has no `[profiles.<profile_name>]` table
     raises `ConfigError` naming the profile, and does not modify the
     file — `test_ac6d_*`.
   - (e) a nonexistent `path`, and separately a file that exists but
     can't be written (e.g. `chmod 0o444`), each raise `ConfigError`
     without partial writes (original content unchanged where a file
     existed) — `test_ac6e_*`.

## Standing instructions
- Your goal: make the full contract suite pass:
  `PYTHONPATH=src python3 -m pytest tests/contract/`.
- NEVER modify files under `tests/contract/` — the gate rejects the
  commit if you do.
- Deliver exactly ONE new commit on top of the HEAD you started from;
  never amend, rebase, or reset; leave the working tree clean.
- Commit message per AGENTS.md, with trailers:
  `Done: Task 15.1 — <one line>`,
  `Next: Task 15.2 — repl.py + __main__.py masked key-entry flow`, and
  the Co-Authored-By signature line from your dispatch prompt.
