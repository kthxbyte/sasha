"""Contract tests — Task 57.1: `question` tool core module, registry
entry, and agent-loop dispatch branch.

Written by Tier 1 at spec time; immutable to the implementer.
Spec: docs/superpowers/specs/task-57.1-question-core.md
Design: docs/superpowers/specs/2026-08-07-issue-059-question-tool-design.md

TerminalQuestioner (real terminal rendering) is Task 57.2's scope, not
this one — every test here drives run_consult() with a stub Questioner,
never a real input_fn.
"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from sse_stub import StubServer  # noqa: E402

STOP_LINE = "[sasha] stopping: repeated identical action / tool-turn limit reached"


class SpyExecutor:
    def __init__(self, cwd):
        self._cwd = cwd
        self.calls = []

    @property
    def cwd(self):
        return self._cwd

    def run(self, command, capture_stdout=False):
        raise AssertionError("executor.run must not be called for the question tool")


class ScriptedConfirmer:
    def __init__(self, verdicts):
        self.verdicts = list(verdicts)
        self.asked = []

    def ask(self, summary, description):
        self.asked.append((summary, description))
        return self.verdicts.pop(0)


class RefusingConfirmer:
    """Spy that asserts it is never called — question bypasses Confirmer entirely."""

    def ask(self, summary, description):
        raise AssertionError("confirmer.ask must not be called for the question tool")


class ScriptedQuestioner:
    def __init__(self, results):
        self.results = list(results)
        self.asked = []

    def ask(self, questions):
        self.asked.append(questions)
        return self.results.pop(0)


class RefusingQuestioner:
    """Spy that asserts it is never called — for the malformed-payload path."""

    def ask(self, questions):
        raise AssertionError("questioner.ask must not be called for a malformed payload")


def _tc(name, arguments, call_id="call_1"):
    return {"kind": "tool_call", "id": call_id, "name": name,
            "arguments": arguments, "fragments": 2}


def _consult(stub_url, confirmer, executor=None, questioner=None,
             seed="help me", max_tool_turns=10):
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
    kwargs = {}
    if questioner is not None:
        kwargs["questioner"] = questioner
    run_consult(session, client, executor, confirmer,
                out=out_chunks.append, max_tool_turns=max_tool_turns, **kwargs)
    return session, executor, confirmer, "".join(out_chunks)


def _tool_message_contents(stub, request_index):
    msgs = stub.requests[request_index]["body"]["messages"]
    return [m["content"] for m in msgs if m.get("role") == "tool"]


_WELL_FORMED_ARGS = {
    "questions": [
        {
            "question": "What approach should I take for the caching layer?",
            "header": "Cache strategy",
            "options": [
                {"label": "In-memory", "description": "Fastest, lost on restart."},
                {"label": "File-based", "description": "Persists across runs."},
            ],
        }
    ]
}

_WELL_FORMED_NORMALIZED = [
    {
        "question": "What approach should I take for the caching layer?",
        "header": "Cache strategy",
        "multiple": False,
        "options": [
            {"label": "In-memory", "description": "Fastest, lost on restart."},
            {"label": "File-based", "description": "Persists across runs."},
        ],
    }
]

_LLM7_SHAPED_ARGS = {
    "questions": [
        "What approach should I take for the caching layer?",
        "Do you prefer in-memory or file-based?",
    ]
}


# --- AC1: exact schema --------------------------------------------------------

def test_ac1_question_schema_exact():
    from sasha.tools import REGISTRY
    from sasha import question
    assert REGISTRY["question"].schema == question.QUESTION_TOOL_SCHEMA
    assert "strict" not in REGISTRY["question"].schema["function"]
    params = REGISTRY["question"].schema["function"]["parameters"]
    assert params == {
        "type": "object",
        "properties": {
            "questions": {
                "type": "array",
                "minItems": 1,
                "items": {
                    "type": "object",
                    "properties": {
                        "question": {"type": "string"},
                        "header": {"type": "string"},
                        "multiple": {"type": "boolean", "default": False},
                        "options": {
                            "type": "array",
                            "minItems": 1,
                            "items": {
                                "type": "object",
                                "properties": {
                                    "label": {"type": "string"},
                                    "description": {"type": "string"},
                                },
                                "required": ["label", "description"],
                                "additionalProperties": False,
                            },
                        },
                    },
                    "required": ["question", "header", "options"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["questions"],
        "additionalProperties": False,
    }


# --- AC2-AC5: validate_shape() -------------------------------------------------

def test_ac2_validate_shape_accepts_well_formed_payload():
    from sasha import question
    assert question.validate_shape(_WELL_FORMED_ARGS) == _WELL_FORMED_NORMALIZED


def test_ac3_validate_shape_rejects_flat_string_list():
    from sasha import question
    assert question.validate_shape(_LLM7_SHAPED_ARGS) is None


def test_ac4_validate_shape_rejects_missing_required_keys():
    from sasha import question
    missing_header = {"questions": [{
        "question": "q?", "options": [{"label": "a", "description": "d"}]}]}
    missing_description = {"questions": [{
        "question": "q?", "header": "H",
        "options": [{"label": "a"}]}]}
    empty_questions = {"questions": []}
    assert question.validate_shape(missing_header) is None
    assert question.validate_shape(missing_description) is None
    assert question.validate_shape(empty_questions) is None


def test_ac5_validate_shape_rejects_wrong_types_and_empty_strings():
    from sasha import question
    bad_multiple = {"questions": [{
        "question": "q?", "header": "H", "multiple": "yes",
        "options": [{"label": "a", "description": "d"}]}]}
    empty_header = {"questions": [{
        "question": "q?", "header": "",
        "options": [{"label": "a", "description": "d"}]}]}
    empty_label = {"questions": [{
        "question": "q?", "header": "H",
        "options": [{"label": "", "description": "d"}]}]}
    assert question.validate_shape(bad_multiple) is None
    assert question.validate_shape(empty_header) is None
    assert question.validate_shape(empty_label) is None


# --- AC6-AC7: format_result() --------------------------------------------------

def test_ac6_format_result_single_pair():
    from sasha import question
    result = question.format_result([("Cache strategy", "File-based")])
    assert result == (
        'User has answered your questions:\n'
        '"Cache strategy"="File-based"\n\n'
        'You can now continue with the user\'s answers in mind.')


def test_ac7_format_result_multiple_pairs_shared_header():
    from sasha import question
    result = question.format_result([
        ("Feature scope", "Retry on failure"),
        ("Feature scope", "Logging"),
    ])
    assert result == (
        'User has answered your questions:\n'
        '"Feature scope"="Retry on failure", "Feature scope"="Logging"\n\n'
        'You can now continue with the user\'s answers in mind.')


# --- AC8-AC11: agent-loop dispatch branch --------------------------------------

def test_ac8_malformed_payload_tool_result():
    confirmer = RefusingConfirmer()
    questioner = RefusingQuestioner()
    with StubServer([_tc("question", _LLM7_SHAPED_ARGS),
                     {"kind": "text", "text": "ok"}]) as stub:
        _consult(stub.base_url, confirmer, questioner=questioner)
        tool_contents = _tool_message_contents(stub, 1)
    assert tool_contents == [
        "Malformed question payload — each entry in `questions` must be "
        "an object with question/header/options, not a plain string. "
        "Try again."]


def test_ac9_no_questioner_configured():
    confirmer = RefusingConfirmer()
    with StubServer([_tc("question", _WELL_FORMED_ARGS),
                     {"kind": "text", "text": "ok"}]) as stub:
        _consult(stub.base_url, confirmer)  # no questioner passed at all
        tool_contents = _tool_message_contents(stub, 1)
    assert tool_contents == ["question tool is not available in this session."]


def test_ac10_rejected_batch():
    confirmer = RefusingConfirmer()
    questioner = ScriptedQuestioner([None])
    with StubServer([_tc("question", _WELL_FORMED_ARGS),
                     {"kind": "text", "text": "ok"}]) as stub:
        _consult(stub.base_url, confirmer, questioner=questioner)
        tool_contents = _tool_message_contents(stub, 1)
    assert tool_contents == ["REJECTED: user dismissed the question(s)"]


def test_ac11_answered_batch_matches_format_result():
    from sasha import question
    confirmer = RefusingConfirmer()
    pairs = [("Cache strategy", "File-based")]
    questioner = ScriptedQuestioner([pairs])
    with StubServer([_tc("question", _WELL_FORMED_ARGS),
                     {"kind": "text", "text": "ok"}]) as stub:
        _consult(stub.base_url, confirmer, questioner=questioner)
        tool_contents = _tool_message_contents(stub, 1)
    assert tool_contents == [question.format_result(pairs)]
    assert questioner.asked == [_WELL_FORMED_NORMALIZED]


# --- AC12: schema sent to the LLM ----------------------------------------------

def test_ac12_schema_sent_includes_question():
    with StubServer([{"kind": "text", "text": "hi"}]) as stub:
        _consult(stub.base_url, ScriptedConfirmer([]))
        sent_tools = stub.requests[0]["body"]["tools"]
    names = {t["function"]["name"] for t in sent_tools}
    assert names == {"run_command", "write_file", "read_file", "list_dir",
                      "search_files", "remember", "question"}


# --- AC13: repetition guard -----------------------------------------------------

def test_ac13_repetition_guard_fires_on_identical_question_calls():
    questioner = ScriptedQuestioner([
        [("Cache strategy", "File-based")],
        [("Cache strategy", "File-based")],
    ])
    with StubServer([
            _tc("question", _WELL_FORMED_ARGS, "call_a"),
            _tc("question", _WELL_FORMED_ARGS, "call_b"),
            {"kind": "text", "text": "final answer"},
    ]) as stub:
        _, _, _, out = _consult(stub.base_url, RefusingConfirmer(),
                                 questioner=questioner)
        assert len(stub.requests) == 3
        last_body = stub.requests[2]["body"]
    assert not last_body.get("tools")
    assert STOP_LINE in out
    assert "final answer" in out


# --- AC14: questioner parameter is optional -------------------------------------

def test_ac14_questioner_parameter_is_optional():
    from sasha.agent import run_consult
    from sasha.config import Profile
    from sasha.llm import ChatClient
    from sasha.session import Session

    with StubServer([{"kind": "text", "text": "hi"}]) as stub:
        session = Session()
        session.add_user("hello")
        client = ChatClient(Profile(name="stub", base_url=stub.base_url,
                                    model="stub-model", api_key=""), timeout_s=10.0)
        out_chunks = []
        # Exact pre-Task-57.1 call shape: no questioner argument at all.
        run_consult(session, client, SpyExecutor("/spy"), ScriptedConfirmer([]),
                    out=out_chunks.append)
    assert "hi" in "".join(out_chunks)
