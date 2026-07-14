from __future__ import annotations

import enum
import shlex

from sasha.executor import ExecResult
from sasha.mutes import MuteStore


class Decision(enum.Enum):
    AUTO_CONSULT = "auto_consult"
    OFFER = "offer"
    NOTHING = "nothing"


def command_word(line: str) -> str:
    stripped = line.strip()
    if not stripped:
        return ""
    try:
        tokens = shlex.split(stripped)
    except ValueError:
        return ""
    if not tokens:
        return ""
    return tokens[0]


def decide(line: str, result: ExecResult, mutes: MuteStore) -> Decision:
    if result.signal is not None:
        return Decision.NOTHING
    if result.exit_code == 0:
        return Decision.NOTHING
    if result.exit_code == 127:
        return Decision.AUTO_CONSULT
    if mutes.is_muted(command_word(line), result.exit_code):
        return Decision.NOTHING
    return Decision.OFFER
