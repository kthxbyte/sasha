"""Contract tests — Task 30.1: drop the synthetic continuation-turn cwd
message (ISSUE-047 fix).

Written by Tier 1 at spec time; immutable to the implementer.
Spec: docs/superpowers/specs/task-30.1-no-synthetic-continuation-turn.md
Design: docs/superpowers/specs/2026-07-23-issue-047-drop-synthetic-continuation-turn-design.md
"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from sse_stub import StubServer  # noqa: E402


def _tool_response(call_id, command="ls", text=""):
    from sasha.llm import ChatResponse, ToolCall

    return ChatResponse(text=text, tool_calls=[ToolCall(
        id=call_id, name="run_command",
        arguments={"command": command, "description": "desc"})])


# --- AC1: no pending user message -> no synthetic trailing turn -------------

def test_ac1_no_trailing_message_after_tool_result_with_no_pending_user():
    # Amended by Tier 1 for Task 32.1 (ISSUE-050): the very first
    # build_messages() call on a fresh Session always tells cwd via its
    # own [context:cwd] marker message (boot-time awareness), so that
    # call is made first here to consume it -- the same way a real
    # consult's first round would -- before exercising the no-pending-
    # user/no-synthetic-turn behavior this test actually targets. See
    # docs/superpowers/specs/2026-07-26-issue-050-context-injection-syntax-design.md.
    from sasha.session import Session

    s = Session()
    s.add_user("do something")
    s.build_messages("/somewhere")
    s.add_assistant(_tool_response("call_1", command="ls -la"))
    s.add_tool_result("call_1", "exit code: 0")

    msgs = s.build_messages("/somewhere")

    assert msgs[-1]["role"] == "tool"
    assert msgs[-1]["tool_call_id"] == "call_1"


def test_ac2_no_bare_cwd_only_message_anywhere_in_array():
    from sasha.session import Session

    s = Session()
    s.add_user("do something")
    s.add_assistant(_tool_response("call_2", command="ls -la"))
    s.add_tool_result("call_2", "exit code: 0")

    msgs = s.build_messages("/distinctive-marker-dir")

    for m in msgs:
        assert m.get("content") != "/distinctive-marker-dir"


def test_ac3_multiple_continuation_rounds_never_synthesize_a_turn():
    from sasha.session import Session

    s = Session()
    s.add_user("do something")
    s.add_assistant(_tool_response("call_a", command="ls"))
    s.add_tool_result("call_a", "exit code: 0")
    s.build_messages("/w")  # first continuation build

    s.add_assistant(_tool_response("call_b", command="pwd"))
    s.add_tool_result("call_b", "exit code: 0")
    msgs = s.build_messages("/w")  # second continuation build

    assert msgs[-1]["role"] == "tool"
    assert msgs[-1]["tool_call_id"] == "call_b"


# --- AC4: regression guard — pending user message still gets cwd/notes -----

def test_ac4_pending_user_message_still_gets_context_block():
    # Amended by Tier 1 for Task 32.1 (ISSUE-050): cwd now arrives as its
    # own preceding [context:cwd] marker message rather than merged into
    # the pending user message's own content.
    from sasha.session import Session

    s = Session()
    s.add_user("a real question")

    msgs = s.build_messages("/regression-check-dir")

    assert msgs[-1]["role"] == "user"
    assert "a real question" in msgs[-1]["content"]
    assert "[context:cwd] /regression-check-dir" in msgs[-2]["content"]


# --- AC5: agent-level integration — second wire request has no synthetic
# trailing user turn -----------------------------------------------------

def test_ac5_second_round_trip_request_has_no_synthetic_user_turn():
    from sasha.agent import run_consult
    from sasha.config import Profile
    from sasha.executor import ExecResult
    from sasha.llm import ChatClient
    from sasha.session import Session

    class SpyExecutor:
        def __init__(self):
            self.calls = []
            self._result = ExecResult(exit_code=0, signal=None,
                                       stderr_text="", stdout_text="ok",
                                       cwd_after="/spy", duration_ms=1)

        @property
        def cwd(self):
            return "/spy"

        sensitive_path_patterns = ()

        def run(self, command, capture_stdout=False):
            self.calls.append((command, capture_stdout))
            return self._result

    class ScriptedConfirmer:
        def __init__(self, verdicts):
            self.verdicts = list(verdicts)

        def ask(self, command, description):
            return self.verdicts.pop(0)

    tc1 = {"kind": "tool_call", "id": "call_1", "name": "run_command",
           "arguments": {"command": "ls", "description": "a step"},
           "fragments": 2}
    tc2 = {"kind": "tool_call", "id": "call_2", "name": "run_command",
           "arguments": {"command": "pwd", "description": "another step"},
           "fragments": 2}

    with StubServer([tc1, tc2, {"kind": "text", "text": "done"}]) as stub:
        session = Session()
        session.add_user("help me")
        client = ChatClient(Profile(name="stub", base_url=stub.base_url,
                                     model="stub-model", api_key=""),
                             timeout_s=10.0)
        executor = SpyExecutor()
        confirmer = ScriptedConfirmer([("yes", ""), ("yes", "")])
        run_consult(session, client, executor, confirmer, out=lambda c: None)

        assert len(stub.requests) == 3
        second_body = stub.requests[1]["body"]
        assert second_body["messages"][-1]["role"] == "tool"
