# ISSUE-024: TerminalSink.write_line() doesn't call ensure_newline() — mid-consult write_line runs onto an open streamed line

## Description
`TerminalSink.write_line()` prints its text with a bare `print(...)`, which
only *appends* a trailing newline — it never checks whether the previous
write (a `write_chunk()` call mid-stream) already left the cursor at the
start of a line. Any `write_line()` call that happens while a streamed
answer is still open therefore lands glued onto the end of that answer's
last line, with no separating newline.

This is distinct from ISSUE-018 (Done). ISSUE-018's fix
(`_consult`'s `finally: self._sink.ensure_newline()`) only covers the
transition from the *end of a whole consult* back to the next REPL prompt.
It does nothing for a `write_line()` call that happens *during* the same
consult, before that `finally` fires — which is exactly what
`TerminalConfirmer.ask()` does when a tool call is offered mid-consult.

### Symptoms — real session capture (2026-07-15)
```
salvador@Hackbook:~/Sasha/src ✦ cat sasha/llm.py |grep print
[sasha] failed (exit 1) — press Enter to ask
salvador@Hackbook:~/Sasha/src ✦
[sasha] thinking…
Let me take a look at the file and see what's going on.[sasha] wants to run:  cat sasha/llm.py | grep print
        └ Search for "print" in sasha/llm.py to see if it exists
        [y]as / [n]o / [e]dit — or type a reply
```
The streamed sentence "Let me take a look at the file and see what's going
on." has no trailing newline from the LLM, and the confirmation gate's
`write_line()` call is printed immediately after it on the same line.

### Root cause
`src/sasha/repl.py::TerminalSink.write_line()`:
```python
def write_line(self, text: str) -> None:
    self._close_chunk_run()          # only resets color escape, not newline
    print(self._wrap(text, _SASHA_COLOR))
    self._ends_in_newline = True
```
It calls `_close_chunk_run()` (closes the ANSI color run) but never checks
`self._ends_in_newline` / emits a separating `\n` the way `ensure_newline()`
does. Contrast with `ensure_newline()`:
```python
def ensure_newline(self) -> None:
    self._close_chunk_run()
    if not self._ends_in_newline:
        sys.stdout.write("\n")
    sys.stdout.flush()
```
`write_line()` needs the same "close the dangling line first" behavior
`ensure_newline()` already has — every call site that writes a discrete
line (confirmation gate, mute/profile/help messages, etc.) is exposed
whenever it fires while a streamed chunk run is still open, not just at the
end of `_consult`.

### Proposed direction (not specced)
Have `write_line()` call the same missing-newline check `ensure_newline()`
performs before printing (or have `write_line()` call `self.ensure_newline()`
internally before `print()`). Needs a contract test that opens a
`write_chunk()` run without a trailing newline and asserts a following
`write_line()` call is preceded by exactly one `\n`.

### Reproduction steps
1. Start Sasha, ask a question whose answer streams without a trailing
   newline and whose handling triggers a tool-call confirmation
   (e.g. a request that makes the LLM call a tool immediately after a short
   sentence).
2. Observe the confirmation gate's first line concatenated onto the last
   line of the streamed answer.
