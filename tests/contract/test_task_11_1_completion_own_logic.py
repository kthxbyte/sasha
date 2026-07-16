"""Contract tests — Task 11.1: own-logic completion (ISSUE-028).

Written by Tier 1 at spec time; immutable to the implementer.
Spec: docs/superpowers/specs/task-11.1-completion-own-logic.md
Design: docs/superpowers/specs/2026-07-16-tab-completion-design.md
"""

import os
import stat

import pytest


def _make_exe(path):
    path.write_text("#!/bin/sh\n")
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


# --- AC1: complete_first_word -------------------------------------------

def test_ac1_finds_executables_across_multiple_path_dirs(tmp_path):
    from sasha.completion import complete_first_word

    d1 = tmp_path / "bin1"
    d2 = tmp_path / "bin2"
    d1.mkdir()
    d2.mkdir()
    _make_exe(d1 / "gitgud")
    _make_exe(d2 / "gitgood")

    result = complete_first_word("git", [str(d1), str(d2)])
    assert result == ["gitgood", "gitgud"]


def test_ac1_deduplicates_name_present_in_multiple_dirs(tmp_path):
    from sasha.completion import complete_first_word

    d1 = tmp_path / "bin1"
    d2 = tmp_path / "bin2"
    d1.mkdir()
    d2.mkdir()
    _make_exe(d1 / "python3")
    _make_exe(d2 / "python3")

    result = complete_first_word("python", [str(d1), str(d2)])
    assert result == ["python3"]


def test_ac1_ignores_non_executable_files(tmp_path):
    from sasha.completion import complete_first_word

    d = tmp_path / "bin"
    d.mkdir()
    (d / "readme.txt").write_text("not executable\n")
    _make_exe(d / "runme")

    result = complete_first_word("r", [str(d)])
    assert result == ["runme"]


def test_ac1_ignores_nonexistent_directories(tmp_path):
    from sasha.completion import complete_first_word

    d = tmp_path / "bin"
    d.mkdir()
    _make_exe(d / "thing")

    result = complete_first_word("thing", [str(d), str(tmp_path / "does-not-exist")])
    assert result == ["thing"]


def test_ac1_empty_prefix_matches_all(tmp_path):
    from sasha.completion import complete_first_word

    d = tmp_path / "bin"
    d.mkdir()
    _make_exe(d / "alpha")
    _make_exe(d / "beta")

    result = complete_first_word("", [str(d)])
    assert result == ["alpha", "beta"]


# --- AC2: words_and_cword ------------------------------------------------

def test_ac2_mid_word_completion():
    from sasha.completion import words_and_cword

    line = "git chec"
    words, cword = words_and_cword(line, begidx=4, text="chec")
    assert words == ["git", "chec"]
    assert cword == 1


def test_ac2_fresh_empty_word_after_trailing_space():
    from sasha.completion import words_and_cword

    line = "git "
    words, cword = words_and_cword(line, begidx=4, text="")
    assert words == ["git", ""]
    assert cword == 1


def test_ac2_first_word_completion():
    from sasha.completion import words_and_cword

    line = "gi"
    words, cword = words_and_cword(line, begidx=0, text="gi")
    assert words == ["gi"]
    assert cword == 0


def test_ac2_empty_line_first_word():
    from sasha.completion import words_and_cword

    words, cword = words_and_cword("", begidx=0, text="")
    assert words == [""]
    assert cword == 0


def test_ac2_unterminated_quote_does_not_raise():
    from sasha.completion import words_and_cword

    line = 'cat "unterminated more'
    words, cword = words_and_cword(line, begidx=len(line), text="")
    assert isinstance(words, list)
    assert isinstance(cword, int)
    assert cword == len(words) - 1


def test_ac2_third_word_after_two_complete_words():
    from sasha.completion import words_and_cword

    line = "sasha profile "
    words, cword = words_and_cword(line, begidx=len(line), text="")
    assert words == ["sasha", "profile", ""]
    assert cword == 2


# --- AC3: complete_builtin_arg --------------------------------------------

def test_ac3_verb_completion():
    from sasha.completion import complete_builtin_arg

    result = complete_builtin_arg(["sasha", "m"], 1, [], [])
    assert result == ["mute"]


def test_ac3_verb_completion_empty_prefix_all_verbs():
    from sasha.completion import complete_builtin_arg

    result = complete_builtin_arg(["sasha", ""], 1, [], [])
    assert sorted(result) == ["help", "mute", "profile", "unmute"]


def test_ac3_profile_arg_completion():
    from sasha.completion import complete_builtin_arg

    result = complete_builtin_arg(["sasha", "profile", "d"], 2,
                                   ["deepseek", "local"], [])
    assert result == ["deepseek"]


def test_ac3_unmute_arg_completion():
    from sasha.completion import complete_builtin_arg

    result = complete_builtin_arg(["sasha", "unmute", "n"], 2,
                                   [], ["npm", "yes"])
    assert result == ["npm"]


def test_ac3_none_when_not_sasha():
    from sasha.completion import complete_builtin_arg

    result = complete_builtin_arg(["git", "chec"], 1, ["deepseek"], ["npm"])
    assert result is None


def test_ac3_none_for_cword_outside_one_or_two():
    from sasha.completion import complete_builtin_arg

    result = complete_builtin_arg(["sasha", "profile", "deepseek", "x"], 3,
                                   ["deepseek"], ["npm"])
    assert result is None


def test_ac3_none_for_unrecognized_verb_at_cword_two():
    from sasha.completion import complete_builtin_arg

    result = complete_builtin_arg(["sasha", "help", "x"], 2,
                                   ["deepseek"], ["npm"])
    assert result is None


# --- AC4: MuteStore.muted_commands() --------------------------------------

def test_ac4_empty_store_returns_empty_list(tmp_path):
    from sasha.mutes import MuteStore

    store = MuteStore(tmp_path / "mutes.json")
    assert store.muted_commands() == []


def test_ac4_returns_muted_command_names(tmp_path):
    from sasha.mutes import MuteStore

    store = MuteStore(tmp_path / "mutes.json", threshold=1)
    store.record_ignored("npm", 1)
    store.record_ignored("yes", 1)
    assert store.muted_commands() == ["npm", "yes"]


def test_ac4_deduplicates_multiple_exit_codes_for_same_command(tmp_path):
    from sasha.mutes import MuteStore

    store = MuteStore(tmp_path / "mutes.json", threshold=1)
    store.record_ignored("npm", 1)
    store.record_ignored("npm", 2)
    assert store.muted_commands() == ["npm"]


def test_ac4_unmuted_entries_excluded(tmp_path):
    from sasha.mutes import MuteStore

    store = MuteStore(tmp_path / "mutes.json", threshold=1)
    store.record_ignored("npm", 1)
    store.unmute("npm")
    assert store.muted_commands() == []


def test_ac4_below_threshold_not_muted(tmp_path):
    from sasha.mutes import MuteStore

    store = MuteStore(tmp_path / "mutes.json", threshold=3)
    store.record_ignored("npm", 1)
    assert store.muted_commands() == []
