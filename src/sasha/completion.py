import glob
import os
import pathlib
import shlex
import shutil
import subprocess

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


def complete_via_bash(words: list[str], cword: int, timeout_s: float = 0.5,
                      cwd: str | None = None) -> list[str]:
    comp_line = " ".join(words)
    comp_words_bash = "(" + " ".join(shlex.quote(w) for w in words) + ")"
    script = (
        "source /usr/share/bash-completion/bash_completion 2>/dev/null\n"
        f"_completion_loader {shlex.quote(words[0])} 2>/dev/null\n"
        f"COMP_LINE={shlex.quote(comp_line)}\n"
        "COMP_POINT=${#COMP_LINE}\n"
        f"COMP_WORDS={comp_words_bash}\n"
        f"COMP_CWORD={cword}\n"
        f'func=$(complete -p {shlex.quote(words[0])} 2>/dev/null | '
        r'sed -n "s/.*-F \([^ ]*\).*/\1/p")' "\n"
        '[ -n "$func" ] && "$func"\n'
        'printf "%s\\n" "${COMPREPLY[@]}"\n'
    )
    try:
        r = subprocess.run(["bash", "-c", script], capture_output=True,
                           text=True, timeout=timeout_s, cwd=cwd)
    except (subprocess.TimeoutExpired, OSError):
        return []
    return [line.rstrip() for line in r.stdout.splitlines() if line.rstrip()]


def bash_completion_available() -> bool:
    return (shutil.which("bash") is not None
            and pathlib.Path("/usr/share/bash-completion/bash_completion").exists())


def complete_filenames(prefix: str, cwd: str | None = None) -> list[str]:
    pattern = os.path.join(cwd, prefix) if cwd else prefix
    is_abs = os.path.isabs(prefix)
    matches = glob.glob(pattern + "*")
    results = []
    for m in matches:
        display = m if (is_abs or not cwd) else os.path.relpath(m, cwd)
        results.append(display + "/" if os.path.isdir(m) else display)
    return sorted(results)
