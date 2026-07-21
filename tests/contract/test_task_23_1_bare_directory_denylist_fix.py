"""Contract tests — Task 23.1: fix bare-directory denylist matching.

Written by Tier 1 at spec time; immutable to the implementer.
Spec: docs/superpowers/specs/task-23.1-bare-directory-denylist-fix.md
Design: docs/superpowers/specs/2026-07-21-issue-043-bare-directory-denylist-fix-design.md
"""

from __future__ import annotations


class FakeExecutor:
    def __init__(self, cwd,
                 sensitive_path_patterns=("~/.ssh/**", "~/.aws/**", "/etc/passwd")):
        self.cwd = cwd
        self.sensitive_path_patterns = sensitive_path_patterns


# --- AC1: bare denylisted directory now matches -----------------------------

def test_ac1_bare_denylisted_directory_now_matches():
    from sasha.safe_commands import requires_confirmation, touches_sensitive_path

    executor = FakeExecutor("/home/salvador/project")
    assert touches_sensitive_path("ls ~/.ssh", executor) is True
    assert touches_sensitive_path("ls ~/.aws", executor) is True
    assert requires_confirmation({"command": "ls ~/.ssh"}, executor) is True


# --- AC2: trailing slash also matches ---------------------------------------

def test_ac2_bare_denylisted_directory_with_trailing_slash_matches():
    from sasha.safe_commands import touches_sensitive_path

    executor = FakeExecutor("/home/salvador/project")
    assert touches_sensitive_path("ls ~/.ssh/", executor) is True


# --- AC3: fix is not over-broad ----------------------------------------------

def test_ac3_bare_non_denylisted_directory_still_no_match():
    from sasha.safe_commands import requires_confirmation, touches_sensitive_path

    executor = FakeExecutor("/home/salvador/project")
    assert touches_sensitive_path("ls ~/Documents", executor) is False
    assert requires_confirmation({"command": "ls ~/Documents"}, executor) is False
