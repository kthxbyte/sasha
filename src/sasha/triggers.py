from __future__ import annotations

import enum
import signal as _signal

from sasha.executor import ExecResult

# The statuses bash uses when the command itself could not run at all.
_COMMAND_NOT_FOUND = 127
_NOT_EXECUTABLE = 126

# Signals that mean a program crashed rather than being stopped. Bash reports
# those as 128+N, so they reach us as ordinary exit codes. This deliberately
# mirrors memory.is_crash_signal, which decides what is worth *recording* as a
# crash; both answer "did this program break, or was it stopped?".
_CRASH_SIGNALS = frozenset({
    _signal.SIGSEGV, _signal.SIGABRT, _signal.SIGBUS,
    _signal.SIGILL, _signal.SIGFPE,
})


class Decision(enum.Enum):
    AUTO_CONSULT = "auto_consult"
    NOTHING = "nothing"


def decide(result: ExecResult) -> Decision:
    """Decide whether a finished line is worth consulting the model about.

    DELIBERATELY NARROW (ISSUE-078). An earlier version consulted on *every*
    non-zero exit, reasoning that a failed command is exactly when an
    explanation is worth having. That is wrong at a terminal, because a non-zero
    exit is a normal thing for a healthy command to report:

      - `grep foo file` exits 1 when nothing matched
      - `diff a b` exits 1 when the files differ
      - `git diff --quiet` exits 1 when there are changes
      - any command killed by SIGPIPE while paging reports 128+13 = 141

    Consulting on those explains something that needs no explanation, and it
    happens constantly, so it breaks the ordinary rhythm of using a shell.

    So consult when the SHELL could not do what was asked, not when a command did
    its job and reported a status:

      - 127 — command not found (also how a line of plain English fails)
      - 126 — found, but not executable
      - a crash signal (SIGSEGV/SIGABRT/SIGBUS/SIGILL/SIGFPE) reported as 128+N

    Everything else is silent. The stderr is already on screen, and
    `?? why did that fail` asks on demand.
    """
    if result.signal is not None:
        return Decision.NOTHING            # the user stopped it themselves
    if result.exit_code in (_COMMAND_NOT_FOUND, _NOT_EXECUTABLE):
        return Decision.AUTO_CONSULT
    if result.exit_code > 128 and (result.exit_code - 128) in _CRASH_SIGNALS:
        return Decision.AUTO_CONSULT
    return Decision.NOTHING
