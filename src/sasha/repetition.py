"""Guards against a response that never ends.

Why this exists: `max_tool_turns` only bounds *tool* loops. A single response is
unbounded -- the model can stream forever inside one turn -- so the turn cap
never fires and nothing else was watching. The user simply waits, with no sign
that anything is wrong.

**What the measurements actually showed** (local 26B,
dealignai/Bonsai-2-27B-Ternary, 2026-10-12):

| prompt | terminated | reasoning | content |
|---|---|---|---|
| "What does termios.tcsetattr do?" | yes | 526 chars | 256 chars |
| "Explain how a PTY works between a shell wrapper and vim" | **no** | 17,882 | **0** |
| "List every stdlib module ... be thorough" | **no** | 18,663 | **0** |

Two conclusions, and the second is the important one:

1. **The real failure mode is unbounded reasoning that never produces an
   answer** -- not verbatim repetition. The stuck text kept re-listing module
   names with small variations ("There is also `curses.window`? I recall module
   names?"), so no 80-character block recurs exactly and
   :class:`RepetitionDetector` never fires. It is kept as a cheap second net for
   the classic degenerate case, not as the answer to this one.
2. **No text statistic separates the two cases.** Measured over 4,000-character
   windows, the stuck stream scored a distinct-character ratio of 0.015, a zlib
   ratio of 0.423 and a unique-5-gram ratio of 0.978 -- while a *healthy* 6,584
   character reasoning stream scored 0.017, 0.372 and 0.914, i.e. it looked
   *more* repetitive than the loop. Compression, entropy and n-gram novelty all
   fail to discriminate.

So the effective guard is not a cleverness test but a **budget**: see
`ChatClient._reasoning_budget_tripped`, which cuts a response that has streamed
`max_reasoning_chars` of reasoning without producing any content at all. That
fires on both failures above and on nothing healthy measured. Bounded-but-curt
beats unbounded.

Both guards live in `llm.chat()` because that is the one place every streamed
chunk passes through: the agent loop, `??` chat mode and `sasha repair` all
inherit them, so no caller can forget to opt in.

**What it looks for.** The last `match_chars` characters of a sliding window of
recent output, however many times they recur *anywhere* in that window. Requiring
the tail itself to recur means we only trip while the model is *currently*
looping, not merely because it repeated itself once earlier and moved on.

Occurrences are counted *overlapping*, and counting stops as soon as the
threshold is met. Both details matter: a plain `str.count` counts
non-overlapping matches only, so a loop whose period does not divide the match
length is undercounted -- "I will rewrite the file. " has a 26-character period,
and 80 characters of it appear once, not nine times, under non-overlapping
counting. Only ever performing `threshold` searches keeps the overlapping count
as cheap as the wrong version.

**What it deliberately does not scan.** Tool-call arguments stream on a separate
channel (`delta.tool_calls`), and that is on purpose: a `write_file` payload may
legitimately contain a file with repeated lines, and a call truncated by this
guard never executes anyway (its arguments no longer parse).

**Known trade-off.** A response that genuinely contains the same 80 characters
eight times inside 4,000 -- a generated fixture with identical lines, say --
will be cut short. That is accepted: the message says so explicitly and the
answer can be re-asked, whereas the failure it prevents is an unbounded hang.
The per-character distinctness floor filters the common innocent case, long
`====` or `----` separator runs.
"""

from __future__ import annotations

# How much recent output to consider, how long the repeated block must be, how
# many times it must recur, and how varied that block must be to count as
# content rather than decoration.
_WINDOW_CHARS = 4000
_MATCH_CHARS = 80
_THRESHOLD = 8
_MIN_DISTINCT_CHARS = 8


class RepetitionDetector:
    """Feed it streamed text; it reports once the model is stuck in a loop."""

    def __init__(self, window_chars: int = _WINDOW_CHARS,
                 match_chars: int = _MATCH_CHARS,
                 threshold: int = _THRESHOLD,
                 min_distinct_chars: int = _MIN_DISTINCT_CHARS) -> None:
        self._match = match_chars
        self._threshold = threshold
        self._min_distinct = min_distinct_chars
        # A window smaller than the evidence required could never trip.
        self._window = max(window_chars, match_chars * threshold)
        self._tail = ""
        self.triggered = False
        self.evidence = ""

    def feed(self, text: str) -> bool:
        """Add streamed text; True once the tail is repeating itself."""
        if self.triggered:
            return True
        if not text:
            return False

        self._tail = (self._tail + text)[-self._window:]
        if len(self._tail) < self._match * self._threshold:
            return False

        block = self._tail[-self._match:]
        if len(set(block)) < self._min_distinct:
            return False
        if self._count_overlapping(block) >= self._threshold:
            self.triggered = True
            self.evidence = block
        return self.triggered

    def _count_overlapping(self, block: str) -> int:
        """Occurrences of `block` in the window, stopping at the threshold.

        Never does more than `threshold` C-level searches, so the overlapping
        count costs the same as the naive one it replaces.
        """
        count = 0
        start = 0
        while count < self._threshold:
            found = self._tail.find(block, start)
            if found < 0:
                break
            count += 1
            start = found + 1
        return count
