"""Contract tests — Task 3.0: agent loop hardening (tool-turn liveness).

Written by Tier 1 at spec time; immutable to the implementer.
Fixes ISSUE-012. Spec: docs/superpowers/specs/task-3.0-agent-hardening.md.
"""

import pathlib
import signal
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from sse_stub import StubServer  # noqa: E402

STOP_LINE = "[sasha] stopping: repeated identical command / tool-turn limit reached"


@pytest.fixture(autouse=True)
def _hang_guard():
    def boom(signum, frame):
        raise TimeoutError("contract test timed out (agent loop spin?)")

    old = signal.signal(signal.SIGALRM, boom)
    signal.alarm(60)
    yield
    signal.alarm(0)
    signal.signal(signal.SIGALRM, old)


class SpyExecutor:
    def __init__(self):
        self.calls = []

    @property
    def cwd(self):
        return "/spy"

    def run(self, command, capture_stdout=False):
        raise AssertionError("executor must never run for unknown tools")


class ScriptedConfirmer:
    def __init__(self):
        self.asked = []

    def ask(self, command, description):
        self.asked.append((command, description))
        return ("no", "")


def _unknown(call_id):
    return {"kind": "tool_call", "id": call_id, "name": "read_file",
            "arguments": {"path": "/etc/passwd"}, "fragments": 2}


def _consult(stub_url, max_tool_turns):
    from sasha.agent import run_consult
    from sasha.config import Profile
    from sasha.llm import ChatClient
    from sasha.session import Session

    session = Session()
    session.add_user("do something")
    client = ChatClient(Profile(name="stub", base_url=stub_url,
                                model="stub-model", api_key=""), timeout_s=10.0)
    executor = SpyExecutor()
    confirmer = ScriptedConfirmer()
    out_chunks = []
    run_consult(session, client, executor, confirmer,
                out=out_chunks.append, max_tool_turns=max_tool_turns)
    return executor, confirmer, "".join(out_chunks)


def test_ac1_unknown_tool_turns_hit_limit():
    with StubServer([_unknown("call_a"), _unknown("call_b"),
                     {"kind": "text", "text": "final words"}]) as stub:
        executor, _, out = _consult(stub.base_url, max_tool_turns=2)
        assert len(stub.requests) == 3
        last_body = stub.requests[2]["body"]
    assert executor.calls == []
    assert not last_body.get("tools")      # final round is tools-less
    assert STOP_LINE in out
    assert "final words" in out


def test_ac2_unknown_tool_feedback_and_no_gate():
    with StubServer([_unknown("call_a"), {"kind": "text", "text": "ok"}]) as stub:
        _, confirmer, _ = _consult(stub.base_url, max_tool_turns=5)
        second = stub.requests[1]["body"]["messages"]
        tool_msgs = [m for m in second if m.get("role") == "tool"]
    assert confirmer.asked == []           # unknown tools never reach the gate
    assert len(tool_msgs) == 1
    assert tool_msgs[0]["content"] == "Unknown tool: read_file"
    assert tool_msgs[0]["tool_call_id"] == "call_a"
