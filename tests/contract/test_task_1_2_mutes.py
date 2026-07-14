"""Contract tests — Task 1.2: mute store.

Written by Tier 1 at spec time; immutable to the implementer.
Spec: docs/superpowers/specs/task-1.2-mutes.md (design §5.3, §8.2).
"""

import json

import pytest


def _store(tmp_path, **kw):
    from sasha.mutes import MuteStore

    return MuteStore(tmp_path / "mutes.json", **kw)


# --- AC1: threshold crossing -------------------------------------------------

def test_ac1_three_ignores_mute_only_third_returns_true(tmp_path):
    s = _store(tmp_path)
    assert s.record_ignored("grep", 1) is False
    assert s.is_muted("grep", 1) is False
    assert s.record_ignored("grep", 1) is False
    assert s.record_ignored("grep", 1) is True   # the crossing call
    assert s.is_muted("grep", 1) is True
    assert s.record_ignored("grep", 1) is False  # already muted
    assert s.is_muted("grep", 1) is True


def test_ac1_pairs_are_independent(tmp_path):
    s = _store(tmp_path)
    for _ in range(3):
        s.record_ignored("grep", 1)
    assert s.is_muted("grep", 1) is True
    assert s.is_muted("grep", 2) is False
    assert s.is_muted("diff", 1) is False


def test_ac1_custom_threshold(tmp_path):
    s = _store(tmp_path, threshold=2)
    assert s.record_ignored("make", 2) is False
    assert s.record_ignored("make", 2) is True
    assert s.is_muted("make", 2) is True


# --- AC2: acting resets the streak -------------------------------------------

def test_ac2_acted_resets_streak(tmp_path):
    s = _store(tmp_path)
    s.record_ignored("grep", 1)
    s.record_ignored("grep", 1)
    s.record_acted("grep", 1)
    # fresh streak: two more ignores must NOT mute, the third must
    assert s.record_ignored("grep", 1) is False
    assert s.record_ignored("grep", 1) is False
    assert s.is_muted("grep", 1) is False
    assert s.record_ignored("grep", 1) is True


# --- AC3: persistence round-trip ----------------------------------------------

def test_ac3_state_survives_reopen(tmp_path):
    s = _store(tmp_path)
    s.record_ignored("grep", 1)
    s.record_ignored("grep", 1)
    for _ in range(3):
        s.record_ignored("diff", 1)

    s2 = _store(tmp_path)
    assert s2.is_muted("diff", 1) is True
    assert s2.is_muted("grep", 1) is False
    # streak persisted: one more ignore crosses the threshold
    assert s2.record_ignored("grep", 1) is True


def test_ac3_wildcard_mute_survives_reopen(tmp_path):
    s = _store(tmp_path)
    s.mute("cargo")  # all exit codes
    s2 = _store(tmp_path)
    assert s2.is_muted("cargo", 1) is True
    assert s2.is_muted("cargo", 101) is True


# --- AC4: file is valid JSON (schema of §8.2) after every mutation --------------

def test_ac4_valid_json_after_each_mutation(tmp_path):
    path = tmp_path / "mutes.json"
    s = _store(tmp_path)

    def check():
        data = json.loads(path.read_text())
        assert data["version"] == 1
        assert isinstance(data["pairs"], dict)
        for key, entry in data["pairs"].items():
            assert isinstance(key, str) and ":" in key
            assert isinstance(entry["muted"], bool)
            assert isinstance(entry["ignored_streak"], int)

    s.record_ignored("grep", 1); check()
    s.record_acted("grep", 1); check()
    s.mute("diff", 2); check()
    s.unmute("diff", 2); check()
    s.mute("cargo"); check()
    s.unmute("cargo"); check()
    # normal pair entries use the "<command>:<exit_code>" key format
    s.record_ignored("tar", 2)
    data = json.loads(path.read_text())
    assert "tar:2" in data["pairs"]


# --- AC5: corrupt state file --------------------------------------------------

def test_ac5_corrupt_file_treated_as_empty(tmp_path):
    path = tmp_path / "mutes.json"
    path.write_text("{this is not json")
    s = _store(tmp_path)  # must not raise
    assert s.is_muted("grep", 1) is False
    s.record_ignored("grep", 1)  # next mutation rewrites valid state
    assert json.loads(path.read_text())["version"] == 1


# --- AC6: mute/unmute, wildcard and reset semantics -----------------------------

def test_ac6_mute_specific_pair(tmp_path):
    s = _store(tmp_path)
    s.mute("diff", 2)
    assert s.is_muted("diff", 2) is True
    assert s.is_muted("diff", 1) is False


def test_ac6_wildcard_mute_covers_all_exits(tmp_path):
    s = _store(tmp_path)
    s.mute("grep")
    assert s.is_muted("grep", 1) is True
    assert s.is_muted("grep", 2) is True
    s.unmute("grep")
    assert s.is_muted("grep", 1) is False
    assert s.is_muted("grep", 2) is False


def test_ac6_wildcard_unmute_clears_specific_pairs_too(tmp_path):
    s = _store(tmp_path)
    for _ in range(3):
        s.record_ignored("grep", 1)
    s.mute("grep", 2)
    s.unmute("grep")  # all exit codes
    assert s.is_muted("grep", 1) is False
    assert s.is_muted("grep", 2) is False


def test_ac6_unmute_resets_streak(tmp_path):
    s = _store(tmp_path)
    for _ in range(3):
        s.record_ignored("grep", 1)
    s.unmute("grep", 1)
    assert s.is_muted("grep", 1) is False
    # streak was reset: two ignores don't re-mute, the third does
    assert s.record_ignored("grep", 1) is False
    assert s.record_ignored("grep", 1) is False
    assert s.record_ignored("grep", 1) is True


def test_ac6_mute_unmute_never_raise_on_unknown(tmp_path):
    s = _store(tmp_path)
    s.unmute("never-seen", 7)
    s.unmute("never-seen")
    s.mute("never-seen", 7)
    assert s.is_muted("never-seen", 7) is True
