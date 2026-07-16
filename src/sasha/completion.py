import os
import shlex

_VERBS = ["help", "mute", "profile", "unmute"]


def complete_first_word(prefix: str, path_dirs: list[str]) -> list[str]:
    matches = set()
    for d in path_dirs:
        try:
            names = os.listdir(d)
        except OSError:
            continue
        for name in names:
            if not name.startswith(prefix):
                continue
            path = os.path.join(d, name)
            if os.path.isfile(path) and os.access(path, os.X_OK):
                matches.add(name)
    return sorted(matches)


def complete_builtin_arg(words: list[str], cword: int, profile_names: list[str],
                         muted_commands: list[str]) -> list[str] | None:
    if not words or words[0] != "sasha":
        return None
    prefix = words[cword] if cword < len(words) else ""
    if cword == 1:
        return [v for v in _VERBS if v.startswith(prefix)]
    if cword == 2:
        if words[1] == "profile":
            return [p for p in profile_names if p.startswith(prefix)]
        if words[1] == "unmute":
            return [c for c in muted_commands if c.startswith(prefix)]
        return None
    return None


def words_and_cword(line: str, begidx: int, text: str) -> tuple[list[str], int]:
    prefix = line[:begidx]
    try:
        prior_words = shlex.split(prefix) if prefix.strip() else []
    except ValueError:
        prior_words = prefix.split()
    return prior_words + [text], len(prior_words)
