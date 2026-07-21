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


_DEFAULT_SENSITIVE_PATHS: tuple[str, ...] = (
    "~/.ssh/**", "/etc/passwd", "/etc/shadow*", "/etc/sudoers*",
    "~/.aws/**", "~/.gnupg/**", "~/.kube/**", "~/.config/gcloud/**",
    "~/.docker/config.json", "~/.netrc", "~/.pgpass",
    "~/.git-credentials", "~/.npmrc", "**/.env", "**/.env.*",
)


@dataclasses.dataclass(frozen=True)
class Config:
    default_profile: str
    profiles: dict[str, Profile]
    limits: Limits
    prompt: str = prompt.DEFAULT_TEMPLATE
    sensitive_paths: tuple[str, ...] = _DEFAULT_SENSITIVE_PATHS
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

[safe_commands]
sensitive_paths = [
  "~/.ssh/**", "/etc/passwd", "/etc/shadow*", "/etc/sudoers*",
  "~/.aws/**", "~/.gnupg/**", "~/.kube/**", "~/.config/gcloud/**",
  "~/.docker/config.json", "~/.netrc", "~/.pgpass",
  "~/.git-credentials", "~/.npmrc", "**/.env", "**/.env.*",
]
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

    sensitive_paths = _DEFAULT_SENSITIVE_PATHS
    safe_commands_table = raw.get("safe_commands", {})
    if isinstance(safe_commands_table, dict):
        raw_paths = safe_commands_table.get("sensitive_paths")
        if isinstance(raw_paths, list) and all(isinstance(p, str) for p in raw_paths):
            sensitive_paths = tuple(raw_paths)

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
        sensitive_paths=sensitive_paths,
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
