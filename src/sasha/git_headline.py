"""One free line of orientation when entering a repository.

Replaces the former directory-recap cascade: five heuristic constants, a
filesystem-shape classifier, an async LLM escalation, a config toggle and two
built-ins, plus 984 lines of tests — all to print one line on `cd`. Its own
design document recorded a 44% false-positive rate for the heuristic it
rejected, and the background thread it spawned was the root cause of two
separate flaky-test investigations (ISSUE-057, ISSUE-062).

What survives is the part that was always free and always useful: the
repository's last commit. No LLM, no heuristics, no background thread, no
config knob.
"""

from __future__ import annotations

import pathlib
import subprocess

_TIMEOUT_S = 2.0


def last_commit(repo_root: pathlib.Path | str) -> tuple[str, str] | None:
    """(relative age, subject) of the newest commit, or None.

    None outside a repository, for a repo with no commits, and for anything
    else that goes wrong — this is a courtesy line, never worth an error.
    """
    try:
        out = subprocess.run(
            ["git", "-C", str(repo_root), "log", "-1", "--format=%ar\t%s"],
            capture_output=True, text=True, timeout=_TIMEOUT_S)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0:
        return None
    raw = out.stdout.strip()
    if "\t" not in raw:
        return None
    age, _, subject = raw.partition("\t")
    age, subject = age.strip(), subject.strip()
    if not age or not subject:
        return None
    return age, subject


def headline(repo_root: pathlib.Path | str) -> str | None:
    """The single line to print, or None when there is nothing to say."""
    commit = last_commit(repo_root)
    if commit is None:
        return None
    age, subject = commit
    name = pathlib.Path(str(repo_root)).name
    return f'[sasha] {name}: last commit {age} — "{subject}"'
