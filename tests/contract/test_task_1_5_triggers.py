"""Contract tests — Task 1.5: trigger engine.

Written by Tier 1 at spec time; immutable to the implementer.
Spec: docs/superpowers/specs/task-1.5-triggers.md (design §5.2, §6).

AMENDED BY TIER 1 (2026-10-12) for the trigger-economy inversion (ISSUE-067).
The original contract encoded paid-token rationing: exit 127 auto-consulted,
every other failure only *offered* a consult the user had to accept with a bare
Enter, and a MuteStore suppressed repeat offers after three ignores. That whole
apparatus existed to spend a paid API budget conservatively. Against a local
model the marginal token is free while a failed command is exactly when an
explanation is worth having, so there is nothing left to ration and no offer to
accept or ignore: every non-zero exit consults. `Decision.OFFER`, the mute
store, and `command_word()` are gone, and `decide()` now takes only the result.
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


# --- AC1: any failure consults -------------------------------------------------

def test_ac1_127_auto_consults():
    from sasha.triggers import Decision, decide

    assert decide(_result(127)) is Decision.AUTO_CONSULT


def test_ac1_ordinary_failures_also_consult():
    """The heart of the inversion: `grep` finding nothing is normal life, but
    explaining it costs nothing locally, so it consults instead of offering."""
    from sasha.triggers import Decision, decide

    assert decide(_result(1)) is Decision.AUTO_CONSULT
    assert decide(_result(2)) is Decision.AUTO_CONSULT
    assert decide(_result(123)) is Decision.AUTO_CONSULT


def test_ac1_decision_does_not_depend_on_the_line():
    """`decide()` reads only the result — there is no line-dependent muting."""
    from sasha.triggers import Decision, decide

    assert decide(_result(1)) is decide(_result(1)) is Decision.AUTO_CONSULT
    import inspect

    assert list(inspect.signature(decide).parameters) == ["result"]


# --- AC2: success is silent ----------------------------------------------------

def test_ac2_success_is_nothing():
    from sasha.triggers import Decision, decide

    assert decide(_result(0)) is Decision.NOTHING


# --- AC3: signal deaths are silent ---------------------------------------------

def test_ac3_signal_deaths_are_nothing():
    from sasha.triggers import Decision, decide

    assert decide(_result(130, sig=2)) is Decision.NOTHING
    assert decide(_result(141, sig=13)) is Decision.NOTHING
    assert decide(_result(143, sig=15)) is Decision.NOTHING


# --- AC4: the removed vocabulary stays removed ---------------------------------

def test_ac4_decision_enum_values():
    from sasha.triggers import Decision

    assert Decision.AUTO_CONSULT.value == "auto_consult"
    assert Decision.NOTHING.value == "nothing"


def test_ac4_offer_is_gone():
    """A vestigial OFFER would be a state the REPL can no longer service."""
    from sasha.triggers import Decision

    assert not hasattr(Decision, "OFFER")


def test_ac4_mute_store_module_is_gone():
    with pytest.raises(ImportError):
        import sasha.mutes  # noqa: F401


def test_ac4_command_word_is_gone():
    """It existed only to key mute lookups; nothing else used it."""
    import sasha.triggers as triggers

    assert not hasattr(triggers, "command_word")
