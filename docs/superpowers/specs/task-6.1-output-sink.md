# Task 6.1: Sink protocol + TerminalSink (structural)

## Authoritative contract
`docs/superpowers/specs/2026-07-15-sasha-v2-output-sink-design.md` (all
sections) is the complete contract. This task fixes **ISSUE-014** (the
`is not print` identity-check hack in `_consult`'s streaming closure) and
**ISSUE-018** (LLM output missing its trailing newline). It delivers the
complete, stable `Sink` protocol — including the `color_enabled` flag — but
does **not** implement colorization or the prompt changes: `write_line`/
`write_chunk` output plain, unwrapped text in this task. Colorization and
`user@host` land in Task 6.2, on top of this task's now-stable interface.

## What already exists
- `src/sasha/repl.py` as it stands after Task 5.1 and Task 5.2 have landed
  (both precede this task in `PLAN.md`) — i.e. `_consult`'s AUTO_CONSULT
  caller already calls `self._consult()` with no argument (Task 5.1), and
  `_consult` already opens with a `self._out("[sasha] thinking…")` call
  (Task 5.2). This task's fix sketch below is the complete file assuming
  that starting point; if for any reason 5.1/5.2 haven't landed yet when
  this task is dispatched, stop and flag it rather than guessing.
- `src/sasha/__main__.py` — wires `Repl`/`TerminalConfirmer` for real
  terminal use; the diff below is exact.
- `src/sasha/agent.py` `run_consult(..., on_text: Callable[[str], None], ...)`
  — unmodified; this task passes `self._sink.write_chunk` directly as
  `on_text` (matching signature exactly), replacing the old `stream_out`
  closure entirely.

## What's new
Replace `src/sasha/repl.py` in its entirety with:

```python
import os
import shlex
import sys
from typing import Protocol

from sasha.agent import run_consult
from sasha.llm import ChatError
from sasha.triggers import Decision, command_word, decide


class Sink(Protocol):
    color_enabled: bool

    def write_line(self, text: str) -> None:
        """Write one complete message; a newline is appended."""

    def write_chunk(self, text: str) -> None:
        """Write raw streamed text with no added newline. No-op for empty
        text. May be called many times for one logical message."""

    def ensure_newline(self) -> None:
        """Write a newline only if the last write didn't already end in
        one. No-op if nothing has been written yet."""


class Repl:
    def __init__(self, config, executor, mutes, session, client_factory,
                 confirmer, input_fn, sink):
        self._config = config
        self._executor = executor
        self._mutes = mutes
        self._session = session
        self._client_factory = client_factory
        self._confirmer = confirmer
        self._input_fn = input_fn
        self._sink = sink
        self._current_profile = config.profiles[config.default_profile]
        self._pending_offer = None  # (line, exit_code) or None

    def _consult(self, auto_prompt=None):
        if auto_prompt:
            self._session.add_user(auto_prompt)
        self._sink.write_line("[sasha] thinking…")
        try:
            client = self._client_factory(self._current_profile)
            run_consult(self._session, client, self._executor,
                        self._confirmer, self._sink.write_chunk,
                        max_tool_turns=self._config.limits.max_tool_turns)
        except ChatError as e:
            self._sink.write_line(
                f"[sasha] {self._current_profile.name} unreachable: "
                f"{e.reason} — 'sasha profile <name>' to switch")
        finally:
            self._sink.ensure_newline()

    def _handle_pending_offer_ignore(self):
        if self._pending_offer is not None:
            off_line, off_exit = self._pending_offer
            cmd = command_word(off_line)
            crossed = self._mutes.record_ignored(cmd, off_exit)
            if crossed:
                self._sink.write_line(f"[sasha] muting offers for {cmd} "
                          f"exit {off_exit} — "
                          f"'sasha unmute {cmd}' to restore")
            self._pending_offer = None

    def _handle_builtin(self, stripped):
        rest = stripped[6:]
        try:
            tokens = shlex.split(rest)
        except ValueError:
            self._sink.write_line(f"[sasha] unknown command: {rest}")
            return True

        if not tokens:
            return True

        verb = tokens[0]

        if verb == "mute":
            if len(tokens) >= 2:
                cmd = tokens[1]
                if len(tokens) >= 3:
                    try:
                        n = int(tokens[2])
                        self._mutes.mute(cmd, n)
                    except ValueError:
                        self._sink.write_line(f"[sasha] unknown command: {rest}")
                else:
                    self._mutes.mute(cmd, None)
        elif verb == "unmute":
            if len(tokens) >= 2:
                cmd = tokens[1]
                if len(tokens) >= 3:
                    try:
                        n = int(tokens[2])
                        self._mutes.unmute(cmd, n)
                    except ValueError:
                        self._sink.write_line(f"[sasha] unknown command: {rest}")
                else:
                    self._mutes.unmute(cmd, None)
        elif verb == "profile":
            if len(tokens) == 1:
                p = self._current_profile
                self._sink.write_line(f"[sasha] profile: {p.name} "
                          f"({p.model} @ {p.base_url})")
            elif len(tokens) >= 2:
                name = tokens[1]
                if name in self._config.profiles:
                    self._current_profile = self._config.profiles[name]
                    p = self._current_profile
                    self._sink.write_line(f"[sasha] profile: {p.name} "
                              f"({p.model} @ {p.base_url})")
                else:
                    self._sink.write_line(f"[sasha] unknown profile: {name}")
        elif verb == "help":
            self._sink.write_line("[sasha] sasha mute <cmd> [<N>], "
                      "sasha unmute <cmd>, "
                      "sasha profile [<name>], "
                      "?? <question>")
        else:
            self._sink.write_line(f"[sasha] unknown command: {rest}")

        return True

    def run_once(self, line):
        stripped = line.strip()

        # a. empty + offer pending → consult about the offered failure
        if not stripped:
            if self._pending_offer is not None:
                off_line, off_exit = self._pending_offer
                self._mutes.record_acted(command_word(off_line), off_exit)
                self._pending_offer = None
                prompt = (f"The command `{off_line}` failed with exit "
                          f"{off_exit}. Explain the failure using the "
                          f"captured stderr and suggest a fix.")
                self._consult(prompt)
                return True
            # b. empty, no offer → do nothing
            return True

        # c. ?? <text> → consult (executor untouched)
        if stripped.startswith("?? "):
            text = stripped[3:].strip()
            self._pending_offer = None
            self._session.add_user(text)
            self._consult()
            return True

        # d. sasha <builtin>
        if stripped.startswith("sasha "):
            self._handle_pending_offer_ignore()
            return self._handle_builtin(stripped)

        # e. otherwise
        self._handle_pending_offer_ignore()

        if stripped in ("exit", "quit"):
            return False

        syntax_error = self._executor.check_syntax(stripped)
        if syntax_error is not None:
            prompt = (f"The line `{stripped}` is not valid bash: "
                      f"{syntax_error}. Explain and give a corrected line.")
            self._consult(prompt)
            return True

        result = self._executor.run(stripped)
        self._session.note_command(stripped, result)

        if result.ctrl_z_terminated:
            self._sink.write_line("[sasha] Ctrl+Z not supported — command terminated")
            return True

        decision = decide(stripped, result, self._mutes)
        if decision == Decision.AUTO_CONSULT:
            self._consult()
        elif decision == Decision.OFFER:
            self._sink.write_line(f"[sasha] failed (exit {result.exit_code}) "
                      f"— press Enter to ask")
            self._pending_offer = (stripped, result.exit_code)

        return True

    def loop(self):
        home = os.path.expanduser("~")
        while True:
            cwd = self._executor.cwd
            if cwd.startswith(home):
                display_cwd = "~" + cwd[len(home):]
            else:
                display_cwd = cwd
            prompt = f"{display_cwd} ✦ "
            try:
                line = self._input_fn(prompt)
            except EOFError:
                return
            except KeyboardInterrupt:
                continue
            if not self.run_once(line):
                return


class TerminalConfirmer:
    def __init__(self, input_fn, sink):
        self._input_fn = input_fn
        self._sink = sink

    def ask(self, command, description):
        desc = description if description else "(no description provided)"
        self._sink.write_line(f"[sasha] wants to run:  {command}\n"
                  f"        └ {desc}\n"
                  f"        [y]es / [n]o / [e]dit — or type a reply")
        response = self._input_fn("> ").strip()
        if response in ("y", "yes"):
            return ("yes", "")
        elif response in ("n", "no", ""):
            return ("no", "")
        elif response in ("e", "edit"):
            edited = self._input_fn("edit> ")
            return ("edit", edited)
        else:
            return ("reply", response)


class TerminalSink:
    def __init__(self) -> None:
        self.color_enabled = (sys.stdout.isatty()
                              and "NO_COLOR" not in os.environ)
        self._ends_in_newline = True

    def write_line(self, text: str) -> None:
        print(text)
        self._ends_in_newline = True

    def write_chunk(self, text: str) -> None:
        if not text:
            return
        sys.stdout.write(text)
        sys.stdout.flush()
        self._ends_in_newline = text.endswith("\n")

    def ensure_newline(self) -> None:
        if not self._ends_in_newline:
            sys.stdout.write("\n")
            sys.stdout.flush()
            self._ends_in_newline = True
```

Note the `NO_COLOR` check: `"NO_COLOR" not in os.environ`, not
`not os.environ.get("NO_COLOR")` — the `NO_COLOR` convention is that
*presence* of the variable disables color regardless of its value,
including `NO_COLOR=` (empty string, which is falsy and would incorrectly
compute `color_enabled=True` under a `.get()`-based check).

`_consult` uses `self._sink.write_chunk` directly as `run_consult`'s
`on_text` callback — no wrapping closure needed, since `write_chunk`
already matches the required `Callable[[str], None]` signature exactly.
`ensure_newline()` runs in a `finally` block so it fires on every exit path
from `_consult` (successful stream, `ChatError`, or any other exception),
not just the two paths that were explicitly handled before.

In `src/sasha/__main__.py`:

```python
from sasha.repl import Repl, TerminalConfirmer, TerminalSink
```
(was `from sasha.repl import Repl, TerminalConfirmer`)

```python
    sink = TerminalSink()

    confirmer = TerminalConfirmer(input, sink)
```
(was `confirmer = TerminalConfirmer(input, print)`)

```python
        input_fn=_repl_input,
        sink=sink,
    )
```
(was `input_fn=_repl_input,` followed by `out=print,`)

## Constraints
- Modify `src/sasha/repl.py` and `src/sasha/__main__.py` only (plus optional
  supplementary tests under `tests/impl/`).
- Do not touch: `scripts/`, `docs/superpowers/`, `CLAUDE.md`, `AGENTS.md`,
  `PLAN.md`, `ISSUE-TRACKING.md`, `README.md`, `.gitignore`,
  `pyproject.toml`, `src/sasha/__init__.py`, `src/sasha/config.py`,
  `src/sasha/mutes.py`, `src/sasha/triggers.py`, `src/sasha/executor.py`,
  `src/sasha/session.py`, `src/sasha/agent.py`, `src/sasha/llm.py`,
  `tests/contract/`.
- Do not implement any ANSI color wrapping or change the prompt string —
  that is Task 6.2, deliberately out of scope here. `write_line`/
  `write_chunk` must output exactly the text they're given, unwrapped.
- Greenfield-vs-surgical routing: this is a surgical, near-total rewrite of
  one existing file plus a small wiring change to another — code that
  already works. Dispatch to the default model (deepseek-v4-pro); do not
  use a `SASHA_DISPATCH_MODEL` local-model override.

## Acceptance criteria
(encoded in `tests/contract/test_task_6_1_output_sink.py`, plus the amended
existing files listed below — all already committed with this spec)
1. `TerminalSink.write_line` prints the given text with a trailing newline
   — `test_ac1_write_line_adds_newline`.
2. `TerminalSink.write_chunk` writes raw text with no added newline and
   no-ops on empty text — `test_ac2_*`.
3. `TerminalSink.ensure_newline` writes a newline only when the last write
   didn't already end in one, and no-ops before any write has happened —
   `test_ac3_*`.
4. `TerminalSink.color_enabled` is `True` only when `sys.stdout.isatty()`
   and `NO_COLOR` is absent from the environment — `test_ac4_*`.
5. `TerminalSink.write_line`/`write_chunk` never wrap output in ANSI codes
   in this task, even when `color_enabled` is `True` —
   `test_ac5_write_line_unwrapped_even_when_color_enabled`. **(Retired at
   Task 6.2 spec time — see that task's Decided: entry. Its premise, "no
   color yet," is inherently a Task-6.1-only transitional statement that
   Task 6.2 legitimately supersedes; kept red permanently otherwise.)**
6. Every existing `Repl`/`TerminalConfirmer` contract test still passes
   using the new `sink=` constructor argument: `test_task_3_1_repl.py`,
   `test_task_3_4_repl_streaming.py`, `test_task_4_1_job_control.py`,
   `test_task_5_1_drop_127_preamble.py`, `test_task_5_2_thinking_indicator.py`
   (Tier 1-amended immediately before this task's own dispatch — see the
   correction below — this is the ISSUE-014/018 fix landing).

## Standing instructions
- Your goal: make the full contract suite pass:
  `PYTHONPATH=src python3 -m pytest tests/contract/`
  (every task done or current per `PLAN.md` — this task's own tests plus
  every existing file must be green together).
- NEVER modify files under `tests/contract/` — the gate rejects the commit
  if you do. The 5 existing files listed above carry a Tier 1 amendment
  (SpySink instead of a bare `out` callable) that this task's dispatch
  must NOT touch — it is already in place by the time this task starts.

**Correction (logged when Task 5.1 was dispatched and escalated):** this
spec originally amended the 5 existing files at spec-commit time (`ce298f9`),
far ahead of Task 5.1/5.2 actually landing. Since `scripts/run-tests.sh`
selects every done-or-current task's file — not just this task's own — that
broke the gate for Task 5.1 immediately: the amendments assume the `Sink`
interface this task introduces, but the actually-landed code at that point
still used the old bare `out` callable. Deepseek correctly refused to guess
and escalated rather than touch `tests/contract/`. Fix: the amendments were
reverted to their pre-`ce298f9` (bare-callable) state so Task 5.1/5.2 could
dispatch against the interface that actually exists, and are re-applied by
Tier 1 as a dedicated commit immediately before *this* task (Task 6.1)
dispatches — not before. See the fix commit's trailer for detail. General
rule going forward: a task's blast-radius test amendments to *other* tasks'
files land with that task's own spec commit only if those other tasks are
already done; if they're still pending, the amendment is deferred and
applied right before the interface-changing task's own dispatch.
- Deliver exactly ONE new commit on top of the HEAD you started from; never
  amend, rebase, or reset; leave the working tree clean.
- Commit message per AGENTS.md, with trailers:
  `Done: Task 6.1 — <one line>`, `Fixed: ISSUE-014, ISSUE-018`,
  `Next: Task 6.2 — Colorization + user@host prompt`, and the
  Co-Authored-By signature line from your dispatch prompt.
