"""Contract tests — Task 58.1: split the conflated repeated-action /
tool-turn-limit stop message into two distinct, cause-specific messages.

Written by Tier 1 at spec time; immutable to the implementer.
Spec: docs/superpowers/specs/task-58.1-split-stop-messages.md
"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from sse_stub import StubServer  # noqa: E402

REPEATED_STOP_LINE = "[sasha] stopping: repeated identical action"
LIMIT_HINT = (" — raise max_tool_turns under [limits] in config.toml if "
             "this looks like real progress")


class SpyExecutor:
    def __init__(self, cwd):
        self._cwd = cwd
        self.calls = []

    @property
    def cwd(self):
        return self._cwd

    def run(self, command, capture_stdout=False):
        raise AssertionError("executor.run must not be called for non-run_command tools")


class ScriptedConfirmer:
    def __init__(self, verdicts):
        self.verdicts = list(verdicts)
        self.asked = []

    def ask(self, summary, description):
        self.asked.append((summary, description))
        return self.verdicts.pop(0)


class RefusingConfirmer:
    def __init__(self):
        self.asked = []

    def ask(self, summary, description):
        self.asked.append((summary, description))
        raise AssertionError("confirmer.ask must not be called for auto-approved tools")


def _tc(name, arguments, call_id="call_1"):
    return {"kind": "tool_call", "id": call_id, "name": name,
            "arguments": arguments, "fragments": 2}


def _unknown(call_id):
    return {"kind": "tool_call", "id": call_id, "name": "delete_everything",
            "arguments": {"path": "/etc/passwd"}, "fragments": 2}


def _consult(stub_url, confirmer, executor=None, seed="help me", max_tool_turns=10):
    from sasha.agent import run_consult
    from sasha.config import Profile
    from sasha.llm import ChatClient
    from sasha.session import Session

    session = Session()
    session.add_user(seed)
    client = ChatClient(Profile(name="stub", base_url=stub_url,
                                model="stub-model", api_key=""), timeout_s=10.0)
    if executor is None:
        executor = SpyExecutor("/spy")
    out_chunks = []
    run_consult(session, client, executor, confirmer,
                out=out_chunks.append, max_tool_turns=max_tool_turns)
    return session, executor, confirmer, "".join(out_chunks)


# --- AC1: repetition-only stop never mentions the turn-limit vocabulary ----

def test_ac1_repeated_stop_message_has_no_limit_vocabulary(tmp_path):
    executor = SpyExecutor(str(tmp_path))
    confirmer = RefusingConfirmer()
    with StubServer([
            _tc("read_file", {"path": "f.txt"}, "call_a"),
            _tc("read_file", {"path": "f.txt"}, "call_b"),
            {"kind": "text", "text": "final"},
    ]) as stub:
        _, _, _, out = _consult(stub.base_url, confirmer, executor=executor,
                                 max_tool_turns=10)
    assert REPEATED_STOP_LINE + " — read_file({'path': 'f.txt'})" in out
    assert "action limit" not in out
    assert "max_tool_turns" not in out


# --- AC2: turn-limit-only stop, with a real last action, shows it and the
# config hint -----------------------------------------------------------------

def test_ac2_limit_stop_with_real_action_shows_last_action_and_hint(tmp_path):
    executor = SpyExecutor(str(tmp_path))
    confirmer = RefusingConfirmer()
    with StubServer([
            _tc("read_file", {"path": "a.txt"}, "call_a"),
            _tc("read_file", {"path": "b.txt"}, "call_b"),
            {"kind": "text", "text": "final"},
    ]) as stub:
        _, _, _, out = _consult(stub.base_url, confirmer, executor=executor,
                                 max_tool_turns=2)
    expected = ("[sasha] stopping: hit the 2-action limit for this turn"
                " — last action: read_file({'path': 'b.txt'})" + LIMIT_HINT)
    assert expected in out
    assert REPEATED_STOP_LINE not in out


# --- AC3: turn-limit-only stop with no executed action omits "last action:"
# entirely but still carries the hint -----------------------------------------

def test_ac3_limit_stop_with_no_action_omits_last_action_fragment():
    confirmer = ScriptedConfirmer([])
    with StubServer([_unknown("call_a"), _unknown("call_b"),
                     {"kind": "text", "text": "final"}]) as stub:
        _, _, _, out = _consult(stub.base_url, confirmer, max_tool_turns=2)
    expected = "[sasha] stopping: hit the 2-action limit for this turn" + LIMIT_HINT
    assert expected in out
    assert "last action:" not in out


# --- AC4: when both conditions trigger in the same round, the repeated-
# action message wins — no turn-limit vocabulary, no hint ---------------------

def test_ac4_repeated_takes_precedence_when_both_trigger(tmp_path):
    executor = SpyExecutor(str(tmp_path))
    confirmer = RefusingConfirmer()
    with StubServer([
            _tc("read_file", {"path": "f.txt"}, "call_a"),
            _tc("read_file", {"path": "f.txt"}, "call_b"),
            {"kind": "text", "text": "final"},
    ]) as stub:
        _, _, _, out = _consult(stub.base_url, confirmer, executor=executor,
                                 max_tool_turns=2)
    assert REPEATED_STOP_LINE + " — read_file({'path': 'f.txt'})" in out
    assert "action limit" not in out
    assert LIMIT_HINT not in out


# --- AC5: the 200-char truncation cap applies identically on the turn-limit
# "last action:" fragment ------------------------------------------------------

def test_ac5_limit_stop_truncates_long_action_description(tmp_path):
    long_content = "X" * 300

    class _AcceptingConfirmer:
        def ask(self, command, description):
            return ("yes", "")

    executor = SpyExecutor(str(tmp_path))
    confirmer = _AcceptingConfirmer()
    tc1 = {"kind": "tool_call", "id": "c1", "name": "write_file",
           "arguments": {"path": str(tmp_path / "a.txt"), "content": long_content,
                         "description": "d"}, "fragments": 2}
    tc2 = {"kind": "tool_call", "id": "c2", "name": "write_file",
           "arguments": {"path": str(tmp_path / "b.txt"), "content": long_content,
                         "description": "d"}, "fragments": 2}
    with StubServer([tc1, tc2, {"kind": "text", "text": "final"}]) as stub:
        _, _, _, out = _consult(stub.base_url, confirmer, executor=executor,
                                 max_tool_turns=2)
    prefix = "[sasha] stopping: hit the 2-action limit for this turn — last action: "
    start = out.index(prefix) + len(prefix)
    end = out.index(LIMIT_HINT, start)
    desc = out[start:end]
    assert desc.endswith("…")
    assert len(desc) == 201


# --- AC6: a question-tool call as the last action before turn-limit is
# reported the same way as any other tool ---------------------------------

def test_ac6_limit_stop_reports_question_tool_as_last_action():
    class ScriptedQuestioner:
        def ask(self, questions):
            return [("Cache strategy", "File-based")]

    args = {"questions": [{"question": "Which cache?", "header": "Cache strategy",
                           "options": [{"label": "File-based", "description": "d"}]}]}
    confirmer = ScriptedConfirmer([])
    with StubServer([
            _tc("question", args, "call_a"),
            _tc("question", {**args, "questions": [dict(args["questions"][0],
                question="Which cache? (again)")]}, "call_b"),
            {"kind": "text", "text": "final"},
    ]) as stub:
        from sasha.agent import run_consult
        from sasha.config import Profile
        from sasha.llm import ChatClient
        from sasha.session import Session

        session = Session()
        session.add_user("help me")
        client = ChatClient(Profile(name="stub", base_url=stub.base_url,
                                    model="stub-model", api_key=""), timeout_s=10.0)
        out_chunks = []
        run_consult(session, client, SpyExecutor("/spy"), confirmer,
                    out=out_chunks.append, max_tool_turns=2,
                    questioner=ScriptedQuestioner())
        out = "".join(out_chunks)
    assert "last action: question(" in out
    assert LIMIT_HINT in out
