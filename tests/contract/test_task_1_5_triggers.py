"""Contract tests — Task 1.5: trigger engine.

Written by Tier 1 at spec time; immutable to the implementer.
Spec: docs/superpowers/specs/task-1.5-triggers.md (design §5.2, §6).
Pure logic; uses real ExecResult and MuteStore instances.
"""

import pytest


def _result(exit_code, sig=None):
    from sasha.executor import ExecResult

    return ExecResult(
        exit_code=exit_code,
        signal=sig,
        stderr_text="",
        stdout_text="",
        cwd_after="/",
        duration_ms=1,
    )


@pytest.fixture
def mutes(tmp_path):
    from sasha.mutes import MuteStore

    return MuteStore(tmp_path / "mutes.json")


# --- AC1: exit 127 auto-consults, mute-exempt --------------------------------

def test_ac1_127_auto_consults(mutes):
    from sasha.triggers import Decision, decide

    assert decide("nosuchcmd", _result(127), mutes) is Decision.AUTO_CONSULT


def test_ac1_127_exempt_from_pair_mute(mutes):
    from sasha.triggers import Decision, decide

    mutes.mute("nosuchcmd", 127)
    assert decide("nosuchcmd", _result(127), mutes) is Decision.AUTO_CONSULT


def test_ac1_127_exempt_from_wildcard_mute(mutes):
    from sasha.triggers import Decision, decide

    mutes.mute("nosuchcmd")
    assert decide("nosuchcmd args", _result(127), mutes) is Decision.AUTO_CONSULT


# --- AC2: plain failures offer unless muted ------------------------------------

def test_ac2_unmuted_failure_offers(mutes):
    from sasha.triggers import Decision, decide

    assert decide("grep -r foo .", _result(1), mutes) is Decision.OFFER
    assert decide("make -j4", _result(2), mutes) is Decision.OFFER


def test_ac2_pair_mute_silences_only_that_pair(mutes):
    from sasha.triggers import Decision, decide

    mutes.mute("grep", 1)
    assert decide("grep -r foo .", _result(1), mutes) is Decision.NOTHING
    assert decide("grep -r foo .", _result(2), mutes) is Decision.OFFER


def test_ac2_wildcard_mute_silences_all_exits(mutes):
    from sasha.triggers import Decision, decide

    mutes.mute("grep")
    assert decide("grep -r foo .", _result(1), mutes) is Decision.NOTHING
    assert decide("grep -r foo .", _result(2), mutes) is Decision.NOTHING


def test_ac2_mute_lookup_keys_on_command_word(mutes):
    from sasha.triggers import Decision, decide

    mutes.mute("grep", 1)
    # different command word, same exit — still offers
    assert decide("rg -r foo .", _result(1), mutes) is Decision.OFFER


# --- AC3: signal deaths are silent -----------------------------------------------

def test_ac3_signal_deaths_are_nothing(mutes):
    from sasha.triggers import Decision, decide

    assert decide("cat bigfile", _result(130, sig=2), mutes) is Decision.NOTHING
    assert decide("yes | head", _result(141, sig=13), mutes) is Decision.NOTHING
    assert decide("sleep 999", _result(143, sig=15), mutes) is Decision.NOTHING


# --- AC4: success is silent ---------------------------------------------------------

def test_ac4_success_is_nothing(mutes):
    from sasha.triggers import Decision, decide

    assert decide("ls", _result(0), mutes) is Decision.NOTHING


# --- AC5: command_word -----------------------------------------------------------------

def test_ac5_command_word_first_token():
    from sasha.triggers import command_word

    assert command_word("grep -r foo .") == "grep"
    assert command_word("  spaced   out  ") == "spaced"


def test_ac5_command_word_unparseable_or_empty():
    from sasha.triggers import command_word

    assert command_word('echo "unclosed') == ""
    assert command_word("") == ""
    assert command_word("   ") == ""


# --- AC6: enum vocabulary ------------------------------------------------------------------

def test_ac6_decision_enum_values():
    from sasha.triggers import Decision

    assert Decision.AUTO_CONSULT.value == "auto_consult"
    assert Decision.OFFER.value == "offer"
    assert Decision.NOTHING.value == "nothing"
