"""Regression tests for the repetition guard (ISSUE-073).

The guard exists because `max_tool_turns` only bounds *tool* loops: a single
response is unbounded, and a reasoning model can degenerate into a verbatim loop
with no tool call anywhere, so the turn cap never fires. These tests pin both
halves -- what it must catch, and what it must leave alone.
"""

import json


# ==========================================================================
# The detector in isolation
# ==========================================================================

def test_does_not_trip_on_ordinary_prose():
    from sasha.repetition import RepetitionDetector

    detector = RepetitionDetector()
    text = ("Let me look at the executor module first. The PTY handling is the "
            "risky part, so I should read it carefully and check how the "
            "terminal state is restored on an exception. Then I can decide "
            "whether the cleanup belongs in a finally block.")

    assert detector.feed(text) is False
    assert detector.triggered is False


def test_trips_on_a_verbatim_repeated_sentence():
    from sasha.repetition import RepetitionDetector

    detector = RepetitionDetector()
    sentence = "Let me check the file to see what it contains. "

    tripped = False
    for _ in range(30):
        if detector.feed(sentence):
            tripped = True
            break

    assert tripped, "a sentence repeated 30 times must be caught"
    assert detector.evidence


def test_trips_on_a_character_level_loop():
    """The pathological 'abcabcabc...' degeneration."""
    from sasha.repetition import RepetitionDetector

    detector = RepetitionDetector()

    assert detector.feed("The quick brown fox jumps over the lazy dog. " * 40)


def test_does_not_trip_on_a_long_separator_run():
    """A long '====' rule is decoration, not a loop -- the distinctness floor
    is what keeps this from being a false positive."""
    from sasha.repetition import RepetitionDetector

    detector = RepetitionDetector()

    assert detector.feed("=" * 4000) is False
    assert detector.feed("-" * 2000 + "\n") is False
    assert detector.feed("#" * 1200) is False


def test_does_not_trip_before_there_is_enough_evidence():
    from sasha.repetition import RepetitionDetector

    detector = RepetitionDetector()
    sentence = "A sentence that is long enough to be a candidate block. "

    for _ in range(3):                     # far below the threshold of 8
        assert detector.feed(sentence) is False


def test_evidence_needs_the_tail_to_recur_now_not_merely_once():
    """Repeating a block and then moving on must not trip: the count is taken
    over a sliding window, so an old repeat ages out."""
    from sasha.repetition import RepetitionDetector

    detector = RepetitionDetector()
    block = "This line appears a few times in a legitimate document. "

    for _ in range(6):
        detector.feed(block)
    # ...then a long stretch of genuinely different text pushes the block out
    for i in range(60):
        detector.feed(f"Unique follow-up sentence number {i} with fresh words.\n")

    assert detector.triggered is False


def test_stays_triggered_once_tripped():
    from sasha.repetition import RepetitionDetector

    detector = RepetitionDetector()
    detector.feed("Repeat this exact sentence over and over again. " * 40)

    assert detector.triggered is True
    assert detector.feed("anything at all") is True


def test_a_window_smaller_than_the_evidence_still_trips():
    """Defensive: window and threshold are both configurable, and a window
    below match*threshold would otherwise make triggering impossible."""
    from sasha.repetition import RepetitionDetector

    detector = RepetitionDetector(window_chars=10, match_chars=20, threshold=3)
    # period 8 divides the 20-char block, so the block recurs at every phase
    detector.feed("abcdefgh" * 40)

    assert detector._window >= 20 * 3
    assert detector.triggered is True


# ==========================================================================
# Wired into chat()
# ==========================================================================

def _sse(chunks):
    """Build a minimal OpenAI-style SSE body from delta dicts."""
    lines = []
    for delta in chunks:
        lines.append("data: " + json.dumps({"choices": [{"delta": delta}]}))
    lines.append("data: [DONE]")
    return ("\n".join(lines) + "\n").encode("utf-8")


class _FakeResponse:
    def __init__(self, body):
        self._body = body

    def __iter__(self):
        return iter(self._body.splitlines(keepends=True))

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _client(max_think_seconds=0):
    from sasha.config import Profile
    from sasha.llm import ChatClient

    return ChatClient(Profile(name="p", base_url="http://stub/v1",
                              model="m", api_key=""), timeout_s=5.0,
                      max_think_seconds=max_think_seconds)


def _run(monkeypatch, chunks, max_think_seconds=0, clock=None):
    import urllib.request

    body = _sse(chunks)
    monkeypatch.setattr(urllib.request, "urlopen",
                        lambda *a, **k: _FakeResponse(body))
    warnings = []
    text = []
    reasoning = []
    client = _client(max_think_seconds)
    if clock is not None:
        client._now = clock
    response = client.chat([], [], text.append, reasoning.append,
                           warnings.append)
    return response, warnings, "".join(text)


def test_chat_cuts_a_repeating_content_stream_and_says_so(monkeypatch):
    chunks = [{"content": "I will check the file now. "} for _ in range(60)]

    response, warnings, streamed = _run(monkeypatch, chunks)

    assert response.stopped_reason == "repetition"
    assert response.tool_calls == []
    assert any("repeating itself" in w for w in warnings)
    # the stream really was stopped early, not consumed to the end
    assert len(streamed) < len(chunks) * len("I will check the file now. ")


def test_chat_cuts_a_repeating_reasoning_stream(monkeypatch):
    chunks = [{"reasoning_content": "Let me think about this again. "}
              for _ in range(60)]

    response, warnings, _ = _run(monkeypatch, chunks)

    assert response.stopped_reason == "repetition"
    assert any("repeating itself" in w for w in warnings)


def test_chat_leaves_a_normal_stream_alone(monkeypatch):
    chunks = [
        {"content": "Here is the answer: "},
        {"content": "the executor restores the terminal in a finally block."},
    ]

    response, warnings, streamed = _run(monkeypatch, chunks)

    assert response.stopped_reason is None
    assert warnings == []
    assert streamed == "Here is the answer: the executor restores the terminal in a finally block."


def test_chat_drops_a_tool_call_that_was_cut_off_by_the_guard(monkeypatch):
    """A stream abandoned mid-flight is not evidence of a complete call."""
    chunks = [{"content": "I will rewrite the file. "} for _ in range(30)]
    assert len("".join(c["content"] for c in chunks)) > 700
    chunks.append({"tool_calls": [{"index": 0, "id": "c1",
                                   "function": {"name": "write_file",
                                                "arguments": '{"path": "x",'}}]})

    response, warnings, _ = _run(monkeypatch, chunks)

    assert response.stopped_reason == "repetition"
    assert response.tool_calls == []


# ==========================================================================
# The raised turn cap
# ==========================================================================

def test_the_turn_cap_is_off_by_default():
    """0 = no ceiling (ISSUE-080). The cap never caught the failure it was aimed
    at -- runaway reasoning has no tool turns -- while interrupting long work.

    Asserts the default and the self-installed template rather than calling
    load(None), which would read the developer's real ~/.config/sasha/config.toml.
    """
    from sasha import config

    assert config.Limits().max_tool_turns == 0
    assert "max_tool_turns = 0" in config._DEFAULT_CONFIG_TOML


def test_documented_turn_cap_matches_the_code():
    """The README quotes the default in three places; keep them honest."""
    import pathlib

    readme = pathlib.Path("README.md").read_text()
    from sasha.config import Limits

    assert f"max_tool_turns = {Limits().max_tool_turns}" in readme
    assert f"({Limits().max_tool_turns} by default" in readme


# ==========================================================================
# The patience budget -- the failure mode actually measured on the local model
# ==========================================================================

def _fake_clock(*values):
    """A monotonic clock that returns the given values in order (then holds)."""
    seq = list(values)

    def now():
        return seq.pop(0) if len(seq) > 1 else seq[0]

    return now


def test_think_budget_stops_an_endless_think_with_no_answer(monkeypatch):
    """Measured on the local 26B: both "Explain how a PTY works between a shell
    wrapper and vim" and "list every stdlib module, be thorough" streamed
    13,000-18,000 characters of reasoning, produced NO content, and were still
    going at 150s. Verbatim repetition never happened -- the stuck text varies
    at the character level -- so the repetition detector alone misses this."""
    chunks = [{"reasoning_content": "thinking. "} for _ in range(400)]

    response, warnings, streamed = _run(
        monkeypatch, chunks, max_think_seconds=180,
        clock=_fake_clock(1000.0, 1000.5, 1300.0))     # jumps past the budget

    assert response.stopped_reason == "thinking_budget"
    assert streamed == ""                       # no answer was ever produced
    assert any("reasoned" in w for w in warnings)
    assert any("max_think_seconds" in w for w in warnings)


def test_a_legitimate_long_think_is_not_cut(monkeypatch):
    """The regression this budget was rewritten for. Measured: the SAME PTY
    question phrased as "in three sentences" answered after 4,241 characters of
    reasoning and 29.4 seconds. A character budget of 8,000 had under 2x
    headroom over that and would cut a thinking model short; 180 seconds of
    patience does not."""
    chunks = [{"reasoning_content": "y" * 500} for _ in range(40)]   # 20k chars
    chunks += [{"content": "Here is the answer."} for _ in range(10)]

    # the whole think takes 40s of wall clock -- well inside the budget
    response, warnings, streamed = _run(
        monkeypatch, chunks, max_think_seconds=180,
        clock=_fake_clock(1000.0, 1040.0))

    assert response.stopped_reason is None
    assert warnings == []
    assert streamed.startswith("Here is the answer.")


def test_think_budget_stops_watching_once_content_starts(monkeypatch):
    """After the answer begins, the clock is irrelevant: the model is
    demonstrably making progress."""
    chunks = [{"content": "Starting the answer. "}]
    chunks += [{"reasoning_content": "y" * 500} for _ in range(40)]

    response, _, streamed = _run(monkeypatch, chunks, max_think_seconds=180,
                                 clock=_fake_clock(1000.0, 9999.0))

    assert response.stopped_reason is None
    assert streamed.startswith("Starting the answer. ")


def test_think_budget_is_off_when_zero(monkeypatch):
    """0 disables it, which is what library callers and these tests default to."""
    chunks = [{"reasoning_content": "x" * 500} for _ in range(50)]

    response, _, _ = _run(monkeypatch, chunks, max_think_seconds=0,
                          clock=_fake_clock(1000.0, 9999.0))

    assert response.stopped_reason is None


def test_think_clock_starts_at_the_first_reasoning_chunk(monkeypatch):
    """A slow prompt-processing phase must not be charged to the model: the
    clock only starts once reasoning is actually flowing."""
    chunks = [{"reasoning_content": "short think. "}]
    chunks += [{"content": "Answer."}]

    # first call is the start mark; 5000s of "prompt processing" precedes it
    response, _, _ = _run(monkeypatch, chunks, max_think_seconds=180,
                          clock=_fake_clock(5000.0, 5001.0))

    assert response.stopped_reason is None


def test_shipped_think_budget_matches_the_documented_default():
    from sasha import config

    budget = config.Limits().max_think_seconds
    assert budget == 180
    assert f"max_think_seconds = {budget}" in config._DEFAULT_CONFIG_TOML
