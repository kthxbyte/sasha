"""Batch approval, and the turn cap being off (ISSUE-080).

Two related changes to how a run is paced:

1. **The turn ceiling is off by default.** Salvador's verdict from use is that it
   "has never served the purpose I imagined it would": the runaway it aimed at is
   *reasoning* (which has no tool turns, and is bounded by `max_think_seconds`),
   while the ceiling interrupted legitimate long work. `0` now means no limit.

2. **A response's steps are approved as one unit.** The tool calls in one
   response are normally a single intent — write the module, write its tests, run
   them — and approving the middle while denying the rest is not a coherent thing
   to want. So they are shown together and approved together, via an OPTIONAL
   `ask_many` on the confirmer. A single step, or a confirmer without `ask_many`,
   behaves exactly as before.
"""

from sasha.agent import run_consult
from sasha.executor import Executor
from sasha.llm import ChatResponse, ToolCall
from sasha.session import Session


def _write(path):
    return ToolCall(id=f"call-{path}", name="write_file",
                    arguments={"path": path, "content": "x\n",
                               "description": f"create {path}"})


def _command(text):
    return ToolCall(id=f"call-{text}", name="run_command",
                    arguments={"command": text, "description": "run it"})


class FakeClient:
    """Returns scripted responses; one per `chat()` call."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0

    def chat(self, messages, tools, on_text, on_reasoning=lambda c: None,
             on_warning=None):
        self.calls += 1
        if self._responses:
            return self._responses.pop(0)
        return ChatResponse(text="done", tool_calls=[])


class Confirmer:
    """Records every gate interaction, and can batch."""

    def __init__(self, verdict="yes", payload="", batch=True):
        self.singles = []
        self.batches = []
        self._verdict = verdict
        self._payload = payload
        self._batch = batch

    def ask(self, summary, description):
        self.singles.append(summary)
        return (self._verdict, self._payload)

    if True:                                     # only present when batch=True
        def ask_many(self, steps):
            if not self._batch:
                raise AssertionError("ask_many should not have been called")
            self.batches.append(steps)
            return (self._verdict, self._payload)


def _run(tmp_path, responses, confirmer, **kwargs):
    session = Session()
    session.add_user("do the thing")
    executor = Executor(str(tmp_path), {}, **kwargs)
    run_consult(session, FakeClient(responses), executor, confirmer,
                out=lambda t: None)
    return session


# ==========================================================================
# The turn ceiling is off
# ==========================================================================

def test_zero_turns_means_no_ceiling(tmp_path):
    """Six tool turns, each a distinct action, then an answer. With the old
    default of 10-and-then-50 this passed; the point is that 0 does not stop it
    early, and the graceful-landing path is not entered."""
    responses = [ChatResponse(text="", tool_calls=[_write(f"f{i}.txt")])
                 for i in range(6)]
    responses.append(ChatResponse(text="all done", tool_calls=[]))
    client = FakeClient(responses)
    confirmer = Confirmer(batch=False)

    session = Session()
    session.add_user("go")
    run_consult(session, client, Executor(str(tmp_path), {}), confirmer,
                out=lambda t: None, max_tool_turns=0)

    assert client.calls == 7                     # six tool turns + the answer
    for i in range(6):
        assert (tmp_path / f"f{i}.txt").exists()


def test_a_configured_ceiling_still_lands_gracefully(tmp_path):
    """0 disables it; setting one keeps the existing behaviour intact."""
    responses = [ChatResponse(text="", tool_calls=[_write(f"g{i}.txt")])
                 for i in range(5)]
    responses.append(ChatResponse(text="stopping", tool_calls=[]))
    client = FakeClient(responses)

    session = Session()
    session.add_user("go")
    warnings = []
    run_consult(session, client, Executor(str(tmp_path), {}),
                Confirmer(batch=False), out=lambda t: None,
                on_warning=warnings.append, max_tool_turns=2)

    assert any("2-action limit" in w for w in warnings)
    assert not (tmp_path / "g2.txt").exists()    # stopped at the ceiling


# ==========================================================================
# Batch approval
# ==========================================================================

def test_several_steps_are_approved_as_one_unit(tmp_path):
    con = Confirmer()
    _run(tmp_path, [
        ChatResponse(text="", tool_calls=[_write("a.py"), _write("b.py"),
                                          _command("python -m pytest")]),
        ChatResponse(text="done", tool_calls=[]),
    ], con)

    assert len(con.batches) == 1                 # ONE gate interaction
    assert con.singles == []
    steps = con.batches[0]
    assert len(steps) == 3
    assert "a.py" in steps[0][0] and "create a.py" in steps[0][1]
    assert "pytest" in steps[2][0]


def test_denying_the_batch_denies_every_step(tmp_path):
    con = Confirmer(verdict="no")
    session = _run(tmp_path, [
        ChatResponse(text="", tool_calls=[_write("a.py"), _write("b.py")]),
        ChatResponse(text="ok", tool_calls=[]),
    ], con)

    assert len(con.batches) == 1
    assert not (tmp_path / "a.py").exists()
    assert not (tmp_path / "b.py").exists()
    declines = [m for m in session.history
                if m.get("role") == "tool" and "declined" in str(m.get("content"))]
    assert len(declines) == 2                    # every step got its own result


def test_a_reply_to_the_batch_reaches_every_step(tmp_path):
    con = Confirmer(verdict="reply", payload="use a different name")
    session = _run(tmp_path, [
        ChatResponse(text="", tool_calls=[_write("a.py"), _write("b.py")]),
        ChatResponse(text="ok", tool_calls=[]),
    ], con)

    replies = [m for m in session.history if m.get("role") == "tool"
               and "use a different name" in str(m.get("content"))]
    assert len(replies) == 2
    assert not (tmp_path / "a.py").exists()


def test_a_single_step_is_not_a_batch(tmp_path):
    """So the 'edit this command' option stays meaningful, and behaviour for the
    common case is byte-identical to before."""
    con = Confirmer()
    _run(tmp_path, [
        ChatResponse(text="", tool_calls=[_write("only.py")]),
        ChatResponse(text="done", tool_calls=[]),
    ], con)

    assert con.batches == []
    assert len(con.singles) == 1


def test_auto_running_calls_are_not_part_of_the_batch(tmp_path):
    """Only the steps that need approval are listed."""
    con = Confirmer()
    read = ToolCall(id="r1", name="read_file", arguments={"path": "x"})
    _run(tmp_path, [
        ChatResponse(text="", tool_calls=[read, _write("a.py"), _write("b.py")]),
        ChatResponse(text="done", tool_calls=[]),
    ], con)

    assert len(con.batches) == 1
    assert len(con.batches[0]) == 2              # the read is auto, so not listed


def test_a_sensitive_command_is_never_inside_a_batch(tmp_path):
    """It must be looked at on its own, not as item 2 of a list."""
    secret = tmp_path / "secret.txt"
    secret.write_text("hunter2\n")
    con = Confirmer()
    _run(tmp_path, [
        ChatResponse(text="", tool_calls=[_write("a.py"),
                                          _command(f"cat {secret}")]),
        ChatResponse(text="done", tool_calls=[]),
    ], con, sensitive_path_patterns=(str(tmp_path / "secret*"),))

    assert con.batches == []                     # batching declined
    assert len(con.singles) == 2                 # asked one at a time instead


def test_a_question_in_the_response_disables_batching(tmp_path):
    """The question UI has its own interaction, and its position in the sequence
    matters, so the response is not collapsed into one prompt."""
    con = Confirmer()
    question = ToolCall(id="q1", name="question",
                        arguments={"questions": [{"question": "which?",
                                                  "header": "h", "options": []}]})
    _run(tmp_path, [
        ChatResponse(text="", tool_calls=[_write("a.py"), _write("b.py"), question]),
        ChatResponse(text="done", tool_calls=[]),
    ], con)

    assert con.batches == []
    assert len(con.singles) == 2


def test_a_confirmer_without_ask_many_falls_back_to_one_prompt_per_step(tmp_path):
    """Backward compatibility: every existing confirmer still works."""
    class Plain:
        def __init__(self):
            self.asked = []

        def ask(self, summary, description):
            self.asked.append(summary)
            return ("yes", "")

    con = Plain()
    _run(tmp_path, [
        ChatResponse(text="", tool_calls=[_write("a.py"), _write("b.py")]),
        ChatResponse(text="done", tool_calls=[]),
    ], con)

    assert len(con.asked) == 2
    assert (tmp_path / "a.py").exists() and (tmp_path / "b.py").exists()


# ==========================================================================
# The rendered prompt
# ==========================================================================

def _sink():
    class Sink:
        color_enabled = False

        def __init__(self):
            self.lines = []

        def write_line(self, text, color=None):
            self.lines.append(text)

        def write_chunk(self, text):
            pass

        def ensure_newline(self):
            pass

    return Sink()


def test_rendered_batch_lists_every_step_and_offers_no_edit():
    from sasha.repl import TerminalConfirmer

    sink = _sink()
    confirmer = TerminalConfirmer(lambda prompt="": "y", sink)
    verdict = confirmer.ask_many([
        ("write_file: a.py (12 bytes)", "the module"),
        ("run_command: python -m pytest", "verify"),
    ])
    text = sink.lines[0]

    assert verdict == ("yes", "")
    assert "2 steps" in text
    assert "1. write_file: a.py (12 bytes)" in text
    assert "└ the module" in text
    assert "2. run_command: python -m pytest" in text
    assert "[y]es to all" in text
    assert "[e]dit" not in text                  # ambiguous inside a batch


def test_rendered_batch_maps_answers_to_verdicts():
    from sasha.repl import TerminalConfirmer

    steps = [("write_file: a.py", "x"), ("write_file: b.py", "y")]
    for answer, expected in (("y", "yes"), ("yes", "yes"), ("n", "no"),
                             ("", "no"), ("nope, do it differently", "reply")):
        sink = _sink()
        verdict = TerminalConfirmer(lambda prompt="", _a=answer: _a, sink).ask_many(steps)
        assert verdict[0] == expected, answer


def test_rendered_batch_treats_a_cancelled_read_as_decline():
    from sasha.repl import TerminalConfirmer

    sink = _sink()
    confirmer = TerminalConfirmer(lambda prompt="": None, sink)

    assert confirmer.ask_many([("write_file: a.py", "x"),
                               ("write_file: b.py", "y")]) == ("no", "")
