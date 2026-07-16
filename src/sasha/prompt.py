import os

_RL_IGNORE_START = "\001"
_RL_IGNORE_END = "\002"
_RESET = "\x1b[0m"

_BASE_CODES = {
    "black": "30", "red": "31", "green": "32", "yellow": "33",
    "blue": "34", "magenta": "35", "cyan": "36", "white": "37",
}

COLOR_TOKENS: dict[str, str] = {"reset": _RESET}
for _name, _code in _BASE_CODES.items():
    COLOR_TOKENS[_name] = f"\x1b[{_code}m"
    COLOR_TOKENS[f"bold-{_name}"] = f"\x1b[1;{_code}m"
    COLOR_TOKENS[f"dim-{_name}"] = f"\x1b[2;{_code}m"

DEFAULT_TEMPLATE: str = r"\[green\]\u@\h:\w ✦ \[reset\]"

_POSITIONAL = {"u", "h", "w", "W"}


class PromptError(Exception):
    """Raised by validate_template(). Local to this module — prompt.py
    has no dependency on config.py, so config.load() catches this and
    re-raises as ConfigError (same pattern it already uses for
    tomllib.TOMLDecodeError)."""


def _walk(template: str):
    """Yield ("literal", char), ("positional", name), or ("color", token)
    parts of `template`, raising PromptError on any malformed escape."""
    i = 0
    n = len(template)
    while i < n:
        ch = template[i]
        if ch != "\\":
            yield ("literal", ch)
            i += 1
            continue
        if i + 1 >= n:
            raise PromptError("unrecognized escape '\\' at end of template")
        nxt = template[i + 1]
        if nxt in _POSITIONAL:
            yield ("positional", nxt)
            i += 2
            continue
        if nxt == "[":
            end = template.find(r"\]", i + 2)
            if end == -1:
                raise PromptError(
                    f"unterminated '\\[' in prompt template: "
                    f"'{template[i:]}'")
            token = template[i + 2:end]
            if token not in COLOR_TOKENS:
                valid = ", ".join(sorted(COLOR_TOKENS))
                raise PromptError(
                    f"unknown prompt token '\\[{token}\\]' — "
                    f"valid tokens: {valid}")
            yield ("color", token)
            i = end + 2
            continue
        raise PromptError(f"unrecognized escape '\\{nxt}'")


def validate_template(template: str) -> None:
    """Raise PromptError if `template` contains any unrecognized escape
    or unknown \\[token\\] name. Called by config.load()."""
    for _ in _walk(template):
        pass


def render(template: str, *, user: str, host: str, cwd: str, home: str,
           color_enabled: bool) -> str:
    """Pure substitution — no I/O. `cwd`/`home` are pre-computed by the
    caller; \\w abbreviates home to '~', \\W is cwd's basename."""
    parts = []
    for kind, value in _walk(template):
        if kind == "literal":
            parts.append(value)
        elif kind == "positional":
            if value == "u":
                parts.append(user)
            elif value == "h":
                parts.append(host)
            elif value == "w":
                if cwd.startswith(home):
                    parts.append("~" + cwd[len(home):])
                else:
                    parts.append(cwd)
            else:
                if cwd == home:
                    parts.append("~")
                elif cwd == "/":
                    parts.append("/")
                else:
                    parts.append(os.path.basename(cwd))
        else:
            if color_enabled:
                parts.append(_RL_IGNORE_START + COLOR_TOKENS[value]
                             + _RL_IGNORE_END)
    return "".join(parts)
