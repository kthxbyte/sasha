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


# --- AC1: machinery failures consult -------------------------------------------

def test_ac1_127_auto_consults():
    from sasha.triggers import Decision, decide

    assert decide(_result(127)) is Decision.AUTO_CONSULT


# Amended by Tier 1 (2026-10-12, ISSUE-078): INVERTED BACK, deliberately. This
# test used to assert that every ordinary failure consults, on the reasoning
# that explaining it costs nothing against a local model. Salvador reported the
# cost that argument missed: a non-zero exit is a NORMAL thing for a healthy
# command to report -- `grep` exits 1 with no match, `diff` exits 1 when files
# differ, `git diff --quiet` exits 1 when there are changes, and a command
# killed by SIGPIPE while paging reports 141 -- so consulting on those explains
# something that needs no explanation, constantly, and breaks the rhythm of
# using a shell. Ordinary failures are now silent; the shell statuses that mean
# "the shell could not run this" are what consult.
def test_ac1_ordinary_failures_are_silent():
    from sasha.triggers import Decision, decide

    assert decide(_result(1)) is Decision.NOTHING      # grep: no match
    assert decide(_result(1)) is Decision.NOTHING      # diff: files differ
    assert decide(_result(2)) is Decision.NOTHING      # ls: missing path
    assert decide(_result(141)) is Decision.NOTHING    # SIGPIPE while paging
    assert decide(_result(128)) is Decision.NOTHING    # git log: no commits yet


def test_ac1_not_executable_consults():
    from sasha.triggers import Decision, decide

    assert decide(_result(126)) is Decision.AUTO_CONSULT


def test_ac1_crash_signals_consult():
    """Bash reports a signaled child as 128+N, so a real crash arrives as an
    ordinary exit code and must not be swallowed by the rule above."""
    from sasha.triggers import Decision, decide

    assert decide(_result(139)) is Decision.AUTO_CONSULT   # SIGSEGV
    assert decide(_result(134)) is Decision.AUTO_CONSULT   # SIGABRT
    assert decide(_result(132)) is Decision.AUTO_CONSULT   # SIGILL
    # ...but a signal that means "stopped", not "broke", stays silent
    assert decide(_result(130)) is Decision.NOTHING        # SIGINT
    assert decide(_result(143)) is Decision.NOTHING        # SIGTERM


def test_ac1_decision_does_not_depend_on_the_line():
    """`decide()` reads only the result — there is no line-dependent muting."""
    from sasha.triggers import Decision, decide

    assert decide(_result(1)) is decide(_result(1)) is Decision.NOTHING
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
