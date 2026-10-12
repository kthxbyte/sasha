from __future__ import annotations

import enum

from sasha.executor import ExecResult


class Decision(enum.Enum):
    AUTO_CONSULT = "auto_consult"
    NOTHING = "nothing"


def decide(result: ExecResult) -> Decision:
    """Decide whether a finished line is worth consulting the model about.

    Every non-zero exit consults. The previous design consulted only on exit
    127 / syntax errors and merely *offered* on other failures, with a mute
    store to suppress repeat offers — a token-economy apparatus built to ration
    a paid API. Against a local model the marginal token is free while a failed
    command is exactly when an explanation is worth having, so there is nothing
    left to ration and no offer to accept or ignore.

    A signal means the user stopped the command themselves, and exit 0 is
    success; neither is a failure to explain.
    """
    if result.signal is not None:
        return Decision.NOTHING
    if result.exit_code == 0:
        return Decision.NOTHING
    return Decision.AUTO_CONSULT
