# Task 6.2: Colorization + user@host prompt

## Authoritative contract
`docs/superpowers/specs/2026-07-15-sasha-v2-output-sink-design.md` §5.2,
§5.3 is the complete contract. This task fixes the non-stderr half of
**ISSUE-019**: colored `[sasha]` messages, colored LLM responses, and a
`user@host:path ✦ ` prompt colored to match. Stderr coloring is explicitly
out of scope (design §2 non-goals — Salvador's call, 2026-07-15).

## What already exists
- `src/sasha/repl.py` as Task 6.1 leaves it: the `Sink` protocol,
  `TerminalSink` (plain, unwrapped `write_line`/`write_chunk`, working
  `ensure_newline` and `color_enabled`), and every `Repl`/`TerminalConfirmer`
  call site already routed through `self._sink`. **Bind to all of this — do
  not change `Repl.run_once`, `_consult`, `_handle_builtin`,
  `_handle_pending_offer_ignore`, or `TerminalConfirmer` at all.** This task
  touches exactly two things: `TerminalSink`'s `write_line`/`write_chunk`
  bodies, and `Repl.loop`'s prompt construction.
- `src/sasha/__main__.py` — unmodified by this task.

## What's new

Add six module-level constants directly above `class TerminalSink:` (after
`class TerminalConfirmer:`, in the same position `TerminalSink` already
occupies from Task 6.1):

```python
_SASHA_COLOR = "\x1b[36m"   # cyan
_LLM_COLOR = "\x1b[35m"     # magenta
_PROMPT_COLOR = "\x1b[32m"  # green
_RESET = "\x1b[0m"
_RL_IGNORE_START = "\001"
_RL_IGNORE_END = "\002"
```

Add `import getpass` and `import socket` to the top of the file, alongside
the existing `import os`, `import shlex`, `import sys`.

Replace `TerminalSink` in its entirety with (this adds one new instance
attribute, `_chunk_run_open`, to the `__init__` shown for context — it is
the only field this task adds):

```python
class TerminalSink:
    def __init__(self) -> None:
        self.color_enabled = (sys.stdout.isatty()
                              and "NO_COLOR" not in os.environ)
        self._ends_in_newline = True
        self._chunk_run_open = False

    def _wrap(self, text: str, color: str) -> str:
        if not self.color_enabled:
            return text
        return f"{color}{text}{_RESET}"

    def _close_chunk_run(self) -> None:
        if self._chunk_run_open:
            sys.stdout.write(_RESET)
            self._chunk_run_open = False

    def write_line(self, text: str) -> None:
        self._close_chunk_run()
        print(self._wrap(text, _SASHA_COLOR))
        self._ends_in_newline = True

    def write_chunk(self, text: str) -> None:
        if not text:
            return
        if self.color_enabled and not self._chunk_run_open:
            sys.stdout.write(_LLM_COLOR)
            self._chunk_run_open = True
        sys.stdout.write(text)
        sys.stdout.flush()
        self._ends_in_newline = text.endswith("\n")

    def ensure_newline(self) -> None:
        self._close_chunk_run()
        if not self._ends_in_newline:
            sys.stdout.write("\n")
            self._ends_in_newline = True
        sys.stdout.flush()
```

**Why `write_chunk` doesn't wrap each call individually:** an earlier draft
of this task wrapped every `write_chunk` call in its own
`_LLM_COLOR ... _RESET` pair. That is wrong: `run_consult`'s streaming
delivers arbitrary network-chunk boundaries, not word or line boundaries,
so a `RESET`+`COLOR` pair would land *inside* the visible text whenever a
word straddled two chunks — e.g. the digits of `6050` arriving as `"...6"`
then `"050..."` would render as `...6\x1b[0m\x1b[35m050...`, which breaks
any consumer doing substring matching on the raw byte stream (confirmed
against `tests/contract/test_task_3_3_e2e.py`'s real PTY harness, which
does exactly this — `test_ac1`, `test_ac2`, and `test_ac4` all failed
against the naively-wrapped draft). The fix: `write_chunk` opens the color
run on its *first* call after a line/newline boundary and leaves it open;
`write_line`/`ensure_newline` close it. A streamed message therefore gets
exactly one `_LLM_COLOR` and one `_RESET`, with the visible text byte-for-
byte contiguous in between, regardless of how many `write_chunk` calls
delivered it.

Replace `Repl.loop` in its entirety with:

```python
    def loop(self):
        home = os.path.expanduser("~")
        user = getpass.getuser()
        host = socket.gethostname()
        color_enabled = self._sink.color_enabled
        while True:
            cwd = self._executor.cwd
            if cwd.startswith(home):
                display_cwd = "~" + cwd[len(home):]
            else:
                display_cwd = cwd
            raw_prompt = f"{user}@{host}:{display_cwd} ✦ "
            if color_enabled:
                prompt = (f"{_RL_IGNORE_START}{_PROMPT_COLOR}{_RL_IGNORE_END}"
                          f"{raw_prompt}"
                          f"{_RL_IGNORE_START}{_RESET}{_RL_IGNORE_END}")
            else:
                prompt = raw_prompt
            try:
                line = self._input_fn(prompt)
            except EOFError:
                return
            except KeyboardInterrupt:
                continue
            if not self.run_once(line):
                return
```

`getpass.getuser()` / `socket.gethostname()` are called once, outside the
loop — the user/host don't change mid-session, only `cwd` needs to be
re-read every iteration (as today). `color_enabled` is likewise read once
from `self._sink.color_enabled`, not recomputed per-iteration.

The `\001`/`\002` markers (readline's "zero-width, don't count for cursor
math" convention) MUST wrap only the ANSI escape sequences, never the
visible prompt text itself — get this wrong and line editing (backspace,
arrow keys, history recall) desyncs the visible cursor position starting
on the second keystroke. The exact byte sequence is pinned in the code
above; do not restructure it.

## Constraints
- Modify `src/sasha/repl.py` only (plus optional supplementary tests under
  `tests/impl/`) — specifically only `TerminalSink.write_line`/`write_chunk`
  and `Repl.loop`, plus the new imports and module-level constants.
- Do not touch: `scripts/`, `docs/superpowers/`, `CLAUDE.md`, `AGENTS.md`,
  `PLAN.md`, `ISSUE-TRACKING.md`, `README.md`, `.gitignore`,
  `pyproject.toml`, `src/sasha/__init__.py`, `src/sasha/config.py`,
  `src/sasha/mutes.py`, `src/sasha/triggers.py`, `src/sasha/executor.py`,
  `src/sasha/session.py`, `src/sasha/agent.py`, `src/sasha/llm.py`,
  `src/sasha/__main__.py`, `tests/contract/`.
- Do not color `Executor`'s stderr relay — out of scope (design §2).
- Do not add per-message severity coloring — one fixed color per category
  (`_SASHA_COLOR` for all `write_line` calls, `_LLM_COLOR` for all
  `write_chunk` calls) is the entire contract; no color parameter threaded
  through call sites.
- Greenfield-vs-surgical routing: surgical edit to code that already works.
  Dispatch to the default model (deepseek-v4-pro); do not use a
  `SASHA_DISPATCH_MODEL` local-model override.

## Acceptance criteria
(encoded in `tests/contract/test_task_6_2_colorization_prompt.py`)
1. `write_line` wraps text in `_SASHA_COLOR`/`_RESET` when `color_enabled`,
   plain otherwise, and closes any chunk run left open by a preceding
   `write_chunk` streak before printing its own wrapped line — `test_ac1_*`.
2. `write_chunk` opens exactly one `_LLM_COLOR` run per contiguous streak of
   calls (not one per call) when `color_enabled`, plain otherwise, and still
   no-ops on empty text; consecutive `write_chunk` calls within a streak
   produce byte-contiguous visible text with no color code interleaved
   between them — `test_ac2_*`.
3. `ensure_newline` closes any open chunk run (writing the single trailing
   `_RESET`) and its own newline write is never itself colored — `test_ac3_*`.
4. The prompt is exactly `user@host:path ✦ ` (or `~`-abbreviated) when color
   is disabled, and exactly the same text wrapped in
   `\001`+color+`\002` … `\001`+reset+`\002` when enabled — `test_ac4_*`.

## Decisions log
- **Decided:** `write_chunk` colors per contiguous *run*, not per call —
  one `_LLM_COLOR` opened on the first chunk after a line boundary, one
  `_RESET` closed by the next `write_line`/`ensure_newline`. — **Rationale:**
  the design draft's original per-call wrapping breaks byte-contiguity of
  streamed text at chunk boundaries; caught during Tier 1's mandatory
  scratch-copy verification of this task's fix sketch, where it produced
  three real regressions in `tests/contract/test_task_3_3_e2e.py`
  (`test_ac1`, `test_ac2`, `test_ac4` — all fail to find substrings like
  `"6050"` or `"the hint you need"` that happen to straddle a stub-server
  chunk boundary). Not a hypothetical edge case — it broke on the very
  first two-word LLM response tested.
- **Decided:** retire `tests/contract/test_task_6_1_output_sink.py`'s
  `test_ac5_write_line_unwrapped_even_when_color_enabled` (Tier 1 amendment
  to an already-committed contract test, per CLAUDE.md's "only Tier 1
  amends a contract test" rule). — **Rationale:** that test correctly
  pinned Task 6.1's own scope (no color yet) at the time Task 6.1 was
  specced, but `scripts/run-tests.sh` runs every done-or-current task's
  contract file together, so once Task 6.2 becomes current this assertion
  is permanently and correctly false — it was gating a state the codebase
  is deliberately leaving. Caught at spec time, before either task
  dispatches, via the same scratch-copy verification pass; fixed here
  rather than left to surface as a live gate failure during Task 6.2's
  dispatch. AC1-AC4 in that file are untouched: they exercise
  `isatty=False` and remain permanently true.

## Standing instructions
- Your goal: make the full contract suite pass:
  `PYTHONPATH=src python3 -m pytest tests/contract/`.
- NEVER modify files under `tests/contract/` — the gate rejects the commit
  if you do.
- Deliver exactly ONE new commit on top of the HEAD you started from; never
  amend, rebase, or reset; leave the working tree clean.
- Commit message per AGENTS.md, with trailers:
  `Done: Task 6.2 — <one line>`, `Fixed: ISSUE-019 (prompt + coloring; stderr
  coloring explicitly out of scope)`,
  `Next: Task 6.3 — Phase 6 review (Tier 1, with Salvador)`, and the
  Co-Authored-By signature line from your dispatch prompt.
