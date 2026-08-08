"""Contract tests — Task 57.2: TerminalQuestioner (repl.py) + Repl/__main__.py
wiring.

Written by Tier 1 at spec time; immutable to the implementer.
Spec: docs/superpowers/specs/task-57.2-terminal-questioner.md
Design: docs/superpowers/specs/2026-08-07-issue-059-question-tool-design.md
"""

import re

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

_SINGLE_Q = {
    "question": "What approach should I take for the caching layer?",
    "header": "Cache strategy",
    "multiple": False,
    "options": [
        {"label": "In-memory", "description": "Fastest, but lost on restart."},
        {"label": "File-based", "description": "Persists across runs with low setup cost."},
        {"label": "Redis", "description": "Distributed, configurable TTL."},
    ],
}

_MULTI_Q = {
    "question": "Which linters should run in CI?",
    "header": "Linters",
    "multiple": True,
    "options": [
        {"label": "ruff", "description": "Fast Python linter."},
        {"label": "mypy", "description": "Static type checker."},
        {"label": "bandit", "description": "Security linter."},
    ],
}


class _CaptureSink:
    """Fake sink exposing a controllable color_enabled flag — mirrors
    test_task_7_1_confirmation_colors.py's _CaptureSink."""

    def __init__(self, color_enabled):
        self.color_enabled = color_enabled
        self.lines = []

    def write_line(self, text):
        self.lines.append(text)

    def write_chunk(self, text):
        pass

    def ensure_newline(self):
        pass


def _questioner(color_enabled, responses):
    from sasha.repl import TerminalQuestioner

    sink = _CaptureSink(color_enabled)
    it = iter(responses)

    def input_fn(prompt):
        return next(it)

    q = TerminalQuestioner(input_fn, sink)
    return q, sink


class _RaisingIter:
    """Fake input_fn that yields normal responses until it's exhausted the
    scripted list, then raises the given exception on the next call —
    lets a test script "N normal calls, then Ctrl+C/EOF"."""

    def __init__(self, responses, exc):
        self._responses = list(responses)
        self._exc = exc

    def __call__(self, prompt):
        if self._responses:
            return self._responses.pop(0)
        raise self._exc


# --- AC1: single-select happy path returns exactly one (header, label) pair -

def test_ac1_single_select_returns_one_pair():
    q, sink = _questioner(color_enabled=False, responses=["2"])
    result = q.ask([_SINGLE_Q])
    assert result == [("Cache strategy", "File-based")]


# --- AC2: multi-select comma-separated returns one pair per selection,
# preserving input order, sharing the header ---------------------------------

def test_ac2_multi_select_comma_separated_returns_pairs_in_order():
    q, sink = _questioner(color_enabled=False, responses=["3,1"])
    result = q.ask([_MULTI_Q])
    assert result == [("Linters", "bandit"), ("Linters", "ruff")]


# --- AC3: bare Enter at the choice prompt aborts the whole batch -----------

def test_ac3_bare_enter_aborts_whole_batch():
    q, sink = _questioner(color_enabled=False, responses=[""])
    assert q.ask([_SINGLE_Q, _MULTI_Q]) is None


def test_ac3_bare_enter_on_second_question_still_aborts_whole_batch():
    # First question answered normally; the abort on question 2 must
    # still discard the whole batch, not return question 1's partial pair.
    q, sink = _questioner(color_enabled=False, responses=["2", ""])
    assert q.ask([_SINGLE_Q, _MULTI_Q]) is None


# --- AC4: out-of-range or unparseable input reprompts, no attempt limit ----

def test_ac4_out_of_range_reprompts_then_succeeds():
    q, sink = _questioner(color_enabled=False, responses=["99", "abc", "2"])
    result = q.ask([_SINGLE_Q])
    assert result == [("Cache strategy", "File-based")]
    assert sum("Invalid choice" in line for line in sink.lines) == 2


def test_ac4_single_select_rejects_multiple_tokens_then_succeeds():
    q, sink = _questioner(color_enabled=False, responses=["1,2", "2"])
    result = q.ask([_SINGLE_Q])
    assert result == [("Cache strategy", "File-based")]
    assert any("Invalid choice" in line for line in sink.lines)


def test_ac4_duplicate_indices_reprompts():
    q, sink = _questioner(color_enabled=False, responses=["1,1", "2"])
    result = q.ask([_MULTI_Q])
    assert result == [("Linters", "mypy")]
    assert any("Invalid choice" in line for line in sink.lines)


# --- AC5: [C] custom-answer path prompts once more and formats the pair
# as "<header>: <custom text>" ----------------------------------------------

def test_ac5_custom_answer_prompts_again_and_formats_pair():
    q, sink = _questioner(color_enabled=False, responses=["C", "use memcached instead"])
    result = q.ask([_SINGLE_Q])
    assert result == [("Cache strategy", "Cache strategy: use memcached instead")]


def test_ac5_custom_answer_case_insensitive():
    q, sink = _questioner(color_enabled=False, responses=["c", "whatever"])
    result = q.ask([_SINGLE_Q])
    assert result == [("Cache strategy", "Cache strategy: whatever")]


# --- AC6: Ctrl+C / EOF at any point abort the whole batch, no partial state -

def test_ac6_keyboard_interrupt_at_choice_prompt_aborts_batch():
    from sasha.repl import TerminalQuestioner
    sink = _CaptureSink(color_enabled=False)
    q = TerminalQuestioner(_RaisingIter([], KeyboardInterrupt), sink)
    assert q.ask([_SINGLE_Q]) is None


def test_ac6_eof_error_at_choice_prompt_aborts_batch():
    from sasha.repl import TerminalQuestioner
    sink = _CaptureSink(color_enabled=False)
    q = TerminalQuestioner(_RaisingIter([], EOFError), sink)
    assert q.ask([_SINGLE_Q]) is None


def test_ac6_eof_error_on_custom_text_subprompt_aborts_batch():
    from sasha.repl import TerminalQuestioner
    sink = _CaptureSink(color_enabled=False)
    q = TerminalQuestioner(_RaisingIter(["C"], EOFError), sink)
    assert q.ask([_SINGLE_Q]) is None


def test_ac6_interrupt_on_second_question_discards_first_answer():
    from sasha.repl import TerminalQuestioner
    sink = _CaptureSink(color_enabled=False)
    q = TerminalQuestioner(_RaisingIter(["2"], KeyboardInterrupt), sink)
    assert q.ask([_SINGLE_Q, _MULTI_Q]) is None


# --- AC7: multi-question batch renders "Question i/N: <header>" per
# question, in order, and concatenates every question's pairs -------------

def test_ac7_multi_question_batch_concatenates_pairs_in_order():
    q, sink = _questioner(color_enabled=False, responses=["2", "1,3"])
    result = q.ask([_SINGLE_Q, _MULTI_Q])
    assert result == [("Cache strategy", "File-based"),
                       ("Linters", "ruff"), ("Linters", "bandit")]
    assert "Question 1/2: Cache strategy" in sink.lines[0]
    assert "Question 2/2: Linters" in sink.lines[1]


# --- AC8: plain rendering (color disabled) is byte-exact -------------------

def test_ac8_plain_rendering_exact():
    q, sink = _questioner(color_enabled=False, responses=["2"])
    q.ask([_SINGLE_Q])
    assert sink.lines[0] == (
        "Question 1/1: Cache strategy\n"
        "\n"
        "  What approach should I take for the caching layer?\n"
        "\n"
        "  [1] In-memory — Fastest, but lost on restart.\n"
        "  [2] File-based — Persists across runs with low setup cost.\n"
        "  [3] Redis — Distributed, configurable TTL.\n"
        "\n"
        "  [C] Type your own answer\n"
        "\n"
        "  Your choice [1-3, C]:")


def test_ac8_plain_rendering_multi_select_choice_line():
    q, sink = _questioner(color_enabled=False, responses=["1"])
    q.ask([_MULTI_Q])
    assert sink.lines[0].endswith(
        "  Your choice(s) [1-3, comma-separated, C]:")


# --- AC9: colorization — header in _COMMAND_COLOR, [C] line in _DIM,
# choice-prompt line in _PROMPT_COLOR; nothing else colored -----------------

def test_ac9_header_line_colored():
    from sasha.repl import _COMMAND_COLOR, _RESET

    q, sink = _questioner(color_enabled=True, responses=["2"])
    q.ask([_SINGLE_Q])
    first_line = sink.lines[0].split("\n")[0]
    assert first_line == f"{_COMMAND_COLOR}Question 1/1: Cache strategy{_RESET}"


def test_ac9_custom_answer_line_dim():
    from sasha.repl import _DIM, _RESET

    q, sink = _questioner(color_enabled=True, responses=["2"])
    q.ask([_SINGLE_Q])
    c_line = sink.lines[0].split("\n")[8]
    assert c_line == f"{_DIM}  [C] Type your own answer{_RESET}"


def test_ac9_choice_prompt_line_uses_prompt_color():
    from sasha.repl import _PROMPT_COLOR, _RESET

    q, sink = _questioner(color_enabled=True, responses=["2"])
    q.ask([_SINGLE_Q])
    last_line = sink.lines[0].split("\n")[-1]
    assert last_line == f"{_PROMPT_COLOR}  Your choice [1-3, C]:{_RESET}"


def test_ac9_colored_text_matches_plain_text_when_ansi_stripped():
    plain_q, plain_sink = _questioner(color_enabled=False, responses=["2"])
    plain_q.ask([_SINGLE_Q])

    colored_q, colored_sink = _questioner(color_enabled=True, responses=["2"])
    colored_q.ask([_SINGLE_Q])

    assert _ANSI_RE.sub("", colored_sink.lines[0]) == plain_sink.lines[0]


# --- AC10: Repl accepts an optional questioner= kwarg and threads it into
# run_consult() unchanged, without breaking any existing positional
# Repl(...) construction site ------------------------------------------------

def _config():
    from sasha.config import Config, Limits, Profile

    profiles = {"stubprof": Profile(name="stubprof", base_url="http://x",
                                    model="stub-model", api_key="")}
    return Config(default_profile="stubprof", profiles=profiles,
                  limits=Limits())


def test_ac10_repl_questioner_defaults_to_none():
    from sasha.repl import Repl
    from sasha.session import Session

    class _FakeExecutor:
        cwd = "/spy"

    class _FakeMutes:
        pass

    repl = Repl(_config(), _FakeExecutor(), _FakeMutes(), Session(),
                lambda p: None, None, lambda prompt: "", _CaptureSink(False))
    assert repl._questioner is None


def test_ac10_repl_threads_questioner_into_run_consult(monkeypatch):
    from sasha.repl import Repl
    from sasha.session import Session

    captured = {}

    def _fake_run_consult(session, client, executor, confirmer, out, **kwargs):
        captured.update(kwargs)

    monkeypatch.setattr("sasha.repl.run_consult", _fake_run_consult)

    class _FakeExecutor:
        cwd = "/spy"

    class _FakeMutes:
        pass

    sentinel = object()
    repl = Repl(_config(), _FakeExecutor(), _FakeMutes(), Session(),
                lambda p: object(), None, lambda prompt: "",
                _CaptureSink(False), questioner=sentinel)
    repl._consult()
    assert captured["questioner"] is sentinel


# --- AC11: __main__.py wires a real TerminalQuestioner into Repl ----------

def test_ac11_main_module_constructs_terminal_questioner():
    import ast
    import pathlib

    main_src = pathlib.Path(
        __file__).resolve().parents[2] / "src" / "sasha" / "__main__.py"
    tree = ast.parse(main_src.read_text())
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)]
    questioner_calls = [
        c for c in calls
        if isinstance(c.func, ast.Name) and c.func.id == "TerminalQuestioner"]
    assert len(questioner_calls) == 1

    repl_calls = [
        c for c in calls
        if isinstance(c.func, ast.Name) and c.func.id == "Repl"]
    assert len(repl_calls) == 1
    kwarg_names = {kw.arg for kw in repl_calls[0].keywords}
    assert "questioner" in kwarg_names
