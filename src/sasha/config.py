from __future__ import annotations

import dataclasses
import os
import pathlib
import tomllib


@dataclasses.dataclass(frozen=True)
class Profile:
    name: str
    base_url: str
    model: str
    api_key: str


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


class ConfigError(Exception):
    pass


_DEFAULT_PROFILE_NAME = "deepseek"

_DEFAULT_PROFILE_FIELDS_BY_NAME = {
    "deepseek": {
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-chat",
        "api_key_env": "DEEPSEEK_API_KEY",
        "api_key": "",
    },
    "local": {
        "base_url": "http://192.168.8.253:11441/v1",
        "model": "local",
        "api_key_env": "",
        "api_key": "",
    },
}

_VALID_PROFILE_FIELDS = {"base_url", "model", "api_key_env", "api_key"}
_VALID_LIMITS_FIELDS = {
    "history_tokens",
    "stderr_capture_bytes",
    "max_tool_turns",
    "mute_threshold",
}


def _resolve_api_key(profile_fields: dict[str, str]) -> str:
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


def _build_default_profiles() -> dict[str, dict[str, str]]:
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


def load(path: pathlib.Path | None = None) -> Config:
    if path is None:
        path = _default_config_path()

    try:
        with open(path, "rb") as f:
            raw = tomllib.load(f)
    except FileNotFoundError:
        if path != _default_config_path():
            raise ConfigError(
                f"config file not found: {path}"
            ) from None
        return Config(
            default_profile=_DEFAULT_PROFILE_NAME,
            profiles={
                name: Profile(
                    name=name,
                    base_url=pf["base_url"],
                    model=pf["model"],
                    api_key=_resolve_api_key(pf),
                )
                for name, pf in _DEFAULT_PROFILE_FIELDS_BY_NAME.items()
            },
            limits=Limits(),
        )
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(str(exc)) from exc

    default_profile_name = raw.get("default_profile", _DEFAULT_PROFILE_NAME)
    if not isinstance(default_profile_name, str):
        default_profile_name = _DEFAULT_PROFILE_NAME

    profile_fields_by_name = _build_default_profiles()

    for name, table in raw.get("profiles", {}).items():
        if not isinstance(table, dict):
            continue
        existing = profile_fields_by_name.get(name, {"base_url": "", "model": "", "api_key_env": "", "api_key": ""})
        for key, value in table.items():
            if key in _VALID_PROFILE_FIELDS and isinstance(value, str):
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
        )

    return Config(
        default_profile=default_profile_name,
        profiles=profiles,
        limits=limits,
    )
