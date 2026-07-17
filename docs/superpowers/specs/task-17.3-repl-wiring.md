# Task 17.3: repl.py wiring — place-shift detection, commit hook, sasha memory/forget

## Authoritative contract
`docs/superpowers/specs/2026-07-17-project-memory-design.md` is the
complete design record for this task — read it first, especially §5.1
(place-shift detection), §5.3 (commit-triggered encoding), and §5.5
(lazy, self-protecting creation + the one-time notice). Tasks 17.1 and
17.2 (both landed) built `src/sasha/memory.py`,
`Executor.last_command`/`.last_result`, the `remember` tool, and
`Session.set_memory()`. This is the final task in Phase 17 — it makes all
of that actually fire during real REPL usage. This task also adds two
small functions to the already-landed `memory.py` (see below) — that file
being previously "done" doesn't mean it's frozen for the rest of the
project, the same way `config.py` was revisited in a later phase after
Task 1.1.

## What already exists
- `src/sasha/memory.py` (Task 17.1, landed): `resolve_git_root(cwd: str)
  -> pathlib.Path | None`, `encode_commit(repo_root, message, commit_hash)
  -> bool`, `load_memory_block(repo_root, max_chars=8000) -> tuple[str,
  int]`, `most_recent_gist(repo_root) -> str | None`. Read the file
  directly for exact signatures. This task adds two new functions to it
  (below) — every existing function is otherwise unchanged.
- `src/sasha/executor.py` (Task 17.1, landed): `Executor.last_command`,
  `Executor.last_result` — read-only properties. Not touched by this
  task.
- `src/sasha/tools.py` / `src/sasha/session.py` (Task 17.2, landed): the
  `remember` tool (auto-approved, writes into whatever repo
  `executor.cwd` resolves to at call time) and
  `Session.set_memory(text: str) -> None`. Not touched by this task —
  this task is what finally *calls* `set_memory`.
- `src/sasha/repl.py`: the full current file is reproduced below as "What
  already exists" context, since this task's diff touches several
  non-adjacent spots in it. Every line not shown as changed in the "What's
  new" section is unchanged from today.
- `src/sasha/triggers.py`: `command_word(line: str) -> str` (first shlex
  token of a line, `""` on empty/unparseable input). Already imported in
  `repl.py`; used unchanged elsewhere in the file. This task does **not**
  use `command_word` for commit detection (see the commit-hook note
  below) — it uses `shlex.split` directly, already imported in
  `repl.py`.

## What's new

### 1. `src/sasha/memory.py` — two new functions

Add `import shutil` to the existing import block (alongside `json`,
`pathlib`, `subprocess`, `time`). Add these two functions, placed right
before `most_recent_gist`:

```python
def encode_latest_commit(repo_root: pathlib.Path) -> bool | None:
    try:
        result = subprocess.run(
            ["git", "log", "-1", "--format=%H%n%B"],
            cwd=str(repo_root), capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    output = result.stdout
    newline_idx = output.find("\n")
    if newline_idx == -1:
        return None
    commit_hash = output[:newline_idx].strip()
    message = output[newline_idx + 1:].strip()
    if not commit_hash or not message:
        return None
    return encode_commit(repo_root, message, commit_hash)


def forget(repo_root: pathlib.Path) -> None:
    memory_dir = _memory_dir(repo_root)
    if memory_dir.exists():
        shutil.rmtree(memory_dir)
```

**Why this reads HEAD via a direct `subprocess` call, not through
`Executor`:** `Executor.run()` (Task 17.1) unconditionally updates
`last_command`/`last_result` on every call. If the commit hook fetched
the commit message via `executor.run("git log -1 ...")`, it would
*overwrite* what the user's actual last command was — so a later
`remember` call would attach an episodic record of Sasha's own internal
housekeeping query instead of whatever the user was really doing. Reading
git metadata directly via `subprocess` (the same pattern
`resolve_git_root` already uses) sidesteps this entirely. This single git
invocation (`--format=%H%n%B`) also gets the hash and the full message —
including multi-line bodies — in one call.

### 2. `src/sasha/repl.py` — full file, in full:

```python
import dataclasses
import getpass
import os
import shlex
import socket
import sys
from typing import Protocol

import sasha.config as config
from sasha.agent import run_consult
from sasha.llm import ChatError
from sasha import memory
from sasha.triggers import Decision, command_word, decide
import sasha.prompt as prompt_mod


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


def _profile_line(profile):
    return f"[sasha] profile: {profile.name} ({profile.model} @ {profile.base_url})"


def _boot_profile_line(profile):
    return f" Profile    : {profile.name} (model: {profile.model})"


def _boot_api_line(profile):
    return f" API server : {profile.base_url}"


class Repl:
    def __init__(self, config, executor, mutes, session, client_factory,
                 confirmer, input_fn, sink, getpass_fn=getpass.getpass):
        self._config = config
        self._executor = executor
        self._mutes = mutes
        self._session = session
        self._client_factory = client_factory
        self._confirmer = confirmer
        self._input_fn = input_fn
        self._sink = sink
        self._getpass_fn = getpass_fn
        self._current_profile = config.profiles[config.default_profile]
        self._pending_offer = None  # (line, exit_code) or None
        self._current_repo_root = None   # pathlib.Path | None
        self._last_checked_cwd = None    # str | None
        self._memory_dir_existed = False

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
            self._check_memory_created()

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
                self._sink.write_line(_profile_line(p))
            elif len(tokens) >= 2:
                self._activate_profile(tokens[1])
        elif verb == "memory":
            self._handle_memory_builtin()
        elif verb == "forget":
            self._handle_forget_builtin()
        elif verb == "help":
            self._sink.write_line("[sasha] sasha mute <cmd> [<N>], "
                      "sasha unmute <cmd>, "
                      "sasha profile [<name>] (asks for a key if needed), "
                      "sasha memory, sasha forget, "
                      "?? <question>")
        else:
            self._sink.write_line(f"[sasha] unknown command: {rest}")

        return True

    def _handle_memory_builtin(self):
        if self._current_repo_root is None:
            self._sink.write_line(
                "[sasha] no project memory here — not inside a git repository")
            return
        block, count = memory.load_memory_block(
            self._current_repo_root, max_chars=1_000_000)
        if count == 0:
            self._sink.write_line("[sasha] no memory yet for this project")
            return
        self._sink.write_line(
            f"[sasha] memory: {count} entries for "
            f"{self._current_repo_root.name}")
        for entry_line in block.split("\n")[-10:]:
            self._sink.write_line(f"[sasha]   {entry_line}")

    def _handle_forget_builtin(self):
        if self._current_repo_root is None:
            self._sink.write_line(
                "[sasha] nothing to forget — not inside a git repository")
            return
        _, count = memory.load_memory_block(
            self._current_repo_root, max_chars=1_000_000)
        if count == 0:
            self._sink.write_line("[sasha] nothing to forget")
            return
        self._sink.write_line(
            f"[sasha] this will permanently delete {count} memory "
            f"entries for this project. Type 'yes' to confirm:")
        response = self._input_fn("> ").strip()
        if response != "yes":
            self._sink.write_line("[sasha] cancelled")
            return
        memory.forget(self._current_repo_root)
        self._session.set_memory("")
        self._memory_dir_existed = False
        self._sink.write_line("[sasha] forgotten")

    def _check_place_shift(self):
        cwd = self._executor.cwd
        if cwd == self._last_checked_cwd:
            return
        self._last_checked_cwd = cwd
        new_root = memory.resolve_git_root(cwd)
        if new_root == self._current_repo_root:
            return
        self._current_repo_root = new_root
        if new_root is None:
            self._session.set_memory("")
            self._memory_dir_existed = False
            return
        block, count = memory.load_memory_block(new_root)
        self._session.set_memory(block)
        self._memory_dir_existed = (new_root / ".sasha" / "memory").exists()
        if count:
            gist = memory.most_recent_gist(new_root)
            self._sink.write_line(
                f'[sasha] project: {new_root.name} ({count} memories, '
                f'last: "{gist}")')

    def _check_commit(self, line, result):
        if result.exit_code != 0 or self._current_repo_root is None:
            return
        try:
            tokens = shlex.split(line)
        except ValueError:
            return
        if len(tokens) < 2 or tokens[0] != "git" or tokens[1] != "commit":
            return
        memory.encode_latest_commit(self._current_repo_root)

    def _check_memory_created(self):
        if self._current_repo_root is None or self._memory_dir_existed:
            return
        if (self._current_repo_root / ".sasha" / "memory").exists():
            self._memory_dir_existed = True
            self._sink.write_line(
                "[sasha] created .sasha/memory/ — remembering context for "
                "this project from now on (gitignored automatically)")

    def _activate_profile(self, name):
        if name not in self._config.profiles:
            self._sink.write_line(f"[sasha] unknown profile: {name}")
            return

        profile = self._config.profiles[name]
        if profile.api_key or not profile.api_key_required:
            self._current_profile = profile
            self._sink.write_line(_profile_line(profile))
            return

        if self._config.path is None:
            self._sink.write_line(
                "[sasha] couldn't save API key: no config file on disk "
                "— profile unchanged")
            return

        self._sink.write_line(
            f"[sasha] {name} has no API key configured. "
            f"Paste it now (input hidden), or press Enter to cancel:")
        key = self._getpass_fn("> ").strip()
        if not key:
            self._sink.write_line("[sasha] cancelled — profile unchanged")
            return

        try:
            config.set_api_key(self._config.path, name, key)
        except config.ConfigError as e:
            self._sink.write_line(
                f"[sasha] couldn't save API key: {e} — profile unchanged")
            return

        updated_profile = dataclasses.replace(profile, api_key=key)
        updated_profiles = dict(self._config.profiles)
        updated_profiles[name] = updated_profile
        self._config = dataclasses.replace(self._config,
                                            profiles=updated_profiles)
        self._current_profile = updated_profile
        self._sink.write_line(_profile_line(updated_profile))

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

        self._check_place_shift()
        self._check_commit(stripped, result)
        self._check_memory_created()

        decision = decide(stripped, result, self._mutes)
        if decision == Decision.AUTO_CONSULT:
            self._consult()
        elif decision == Decision.OFFER:
            self._sink.write_line(f"[sasha] failed (exit {result.exit_code}) "
                      f"— press Enter to ask")
            self._pending_offer = (stripped, result.exit_code)

        return True

    def loop(self):
        p = self._current_profile
        self._sink.write_line("")
        self._sink.write_line(_boot_profile_line(p))
        self._sink.write_line(_boot_api_line(p))
        self._sink.write_line(
            " Tip : type ?? followed by a question to chat with me directly")
        self._check_place_shift()
        home = os.path.expanduser("~")
        user = getpass.getuser()
        host = socket.gethostname()
        while True:
            cwd = self._executor.cwd
            rendered = prompt_mod.render(
                self._config.prompt, user=user, host=host, cwd=cwd,
                home=home, color_enabled=self._sink.color_enabled)
            try:
                line = self._input_fn(rendered)
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
        if self._sink.color_enabled:
            text = (f"{_COMMAND_COLOR}[sasha] wants to run:  {command}{_RESET}\n"
                    f"{_DIM}        └ {desc}{_RESET}\n"
                    f"{_PROMPT_COLOR}        [y]es / [n]o / [e]dit — or type a reply{_RESET}")
        else:
            text = (f"[sasha] wants to run:  {command}\n"
                    f"        └ {desc}\n"
                    f"        [y]es / [n]o / [e]dit — or type a reply")
        self._sink.write_line(text)
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


_SASHA_COLOR = "\x1b[36m"   # cyan
_LLM_COLOR = "\x1b[35m"     # magenta
_PROMPT_COLOR = "\x1b[32m"  # green
_COMMAND_COLOR = "\x1b[33m"  # yellow — the literal command about to run
_DIM = "\x1b[2m"             # faint — secondary/explanatory text
_RESET = "\x1b[0m"
_RL_IGNORE_START = "\001"
_RL_IGNORE_END = "\002"


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
        if not self._ends_in_newline:
            sys.stdout.write("\n")
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

That is the complete file — copy it verbatim.

### Design points worth calling out explicitly

1. **`_check_place_shift` is called from two places only**: once in
   `loop()` right after the boot banner (so starting Sasha already inside
   a repo with memory announces it immediately), and once in
   `run_once`'s "otherwise" branch (e), right after `result =
   self._executor.run(stripped)` — the *only* branch where a real command
   (and therefore a possible `cd`) actually executes. `??` lines and
   `sasha` built-ins never call `executor.run()`, so they can never change
   `cwd` and don't need this check.
2. **The `cwd == self._last_checked_cwd` short-circuit is the whole
   performance story** — `git rev-parse --show-toplevel` only runs when
   `cwd` has actually changed since the last check, never on every
   prompt/command.
3. **`_check_commit` deliberately does not use `command_word`.** It needs
   to check the *second* token (the git subcommand), not just the first —
   `shlex.split(line)` with `tokens[1] != "commit"` correctly rejects
   `git commit-tree ...` (a different, unrelated subcommand whose name
   merely starts with "commit"), which a substring check on the raw line
   would not.
4. **`_check_memory_created` is called from exactly two places**: at the
   end of `_consult`'s `finally` block (covers the `remember`-tool
   trigger, which only ever happens inside a consult), and right after
   `_check_commit` in `run_once` (covers the commit-hook trigger, which
   never goes through `_consult` at all). Both converge on the same
   generic "does `.sasha/memory/` exist now, when it didn't before"
   check — this is deliberately not a per-trigger notification channel;
   see the design's Decisions log for why a `ToolResult`-based channel
   was considered and rejected for this.
5. **`_handle_forget_builtin`'s confirmation is a plain typed "yes"**, not
   the `TerminalConfirmer`'s y/n/e/reply protocol — that protocol exists
   specifically for LLM-proposed tool actions; a REPL built-in reads its
   own confirmation directly via `self._input_fn`, the same pattern
   `_activate_profile` already uses for its key-entry prompt.
6. **`sasha memory` shows at most the last 10 rendered entry lines**,
   regardless of how many total entries exist — the total count is always
   stated separately in the header line (`"N entries for ..."`), so
   truncation never hides how much is actually stored.

## Constraints
- Modify only `src/sasha/repl.py` and `src/sasha/memory.py` (only the two
  additions shown above — every existing function in `memory.py` is
  unchanged). Do not touch `src/sasha/executor.py`, `src/sasha/tools.py`,
  `src/sasha/session.py`, `src/sasha/agent.py`, `README.md`, or anything
  under `docs/`.
- `Repl.__init__`'s existing 8-positional-argument call shape (`config,
  executor, mutes, session, client_factory, confirmer, input_fn, sink`,
  `getpass_fn` keyword-optional) must keep working with zero changes —
  every existing test constructing a `Repl` this way must keep passing
  unmodified.
- Documenting the new `sasha memory`/`sasha forget` built-ins in
  `README.md` is **not** part of this task — Phase 17's phase-boundary
  review (Tier 1, after this task lands) will handle README updates for
  the whole phase at once, matching how Phase 16's review covered its
  README changes.

## Acceptance criteria
(encoded in `tests/contract/test_task_17_3_repl_memory.py`, 30 tests —
25 `test_ac_*` for repl.py wiring, 5 for `memory.py`'s two new functions)

1. `encode_latest_commit`: reads `HEAD`'s hash and full message (including
   multi-line bodies) via a direct `git log` call, delegates to
   `encode_commit`; returns `None` (writes nothing) on a repo with no
   commits yet.
2. `forget`: removes `.sasha/memory/` entirely; a no-op (never raises) on
   a repo that has none.
3. `loop()`'s initial place-shift check announces existing memory
   (`"project: <name> (N memories, last: \"...\")"`) and loads it into
   `Session` before the first prompt is ever rendered.
4. A repo with no memory yet produces no announcement; a directory
   outside any git repo produces no announcement and no memory content in
   the system prompt.
5. Re-checking with an unchanged `cwd` across multiple calls never
   re-announces and never re-invokes git.
6. Moving to a different repo (via `cd`, simulated by the executor's
   `cwd` changing) swaps the loaded memory block entirely; moving out of
   any repo clears it.
7. A successful `git commit ...` command encodes a commit entry with the
   real message; `git status` and other non-commit git commands do not; a
   lookalike subcommand (`git commit-tree ...`) does not; a *failed*
   `git commit` (non-zero exit) does not.
8. The "created `.sasha/memory/`" notice fires exactly once, the first
   time a repo's memory is created (whether via the commit hook or via a
   `remember` tool call reached through a real `run_consult`), and never
   again for that repo afterward.
9. `sasha memory` reports "not inside a git repository" outside any repo,
   "no memory yet" inside a repo with none, and otherwise the entry count
   plus recent entries.
10. `sasha forget` reports "nothing to forget" appropriately, cancels
    without deleting anything on any non-`"yes"` response, and on `"yes"`
    deletes `.sasha/memory/` and clears the session's loaded memory block.
11. `sasha help`'s output still contains `"mute"`, `"profile"`, and
    `"??"` in the same line (regression guard, already covered by
    `test_task_3_1_repl.py`'s `test_ac7_help_and_exit` — re-asserted here
    for completeness, not a new requirement).

## Standing instructions
- Your goal: make `tests/contract/test_task_17_3_repl_memory.py` pass,
  and keep the full contract suite green:
  `PYTHONPATH=src python3 -m pytest tests/contract/`.
- NEVER modify files under `tests/contract/` — the gate rejects the
  commit if you do.
- Deliver exactly ONE new commit on top of the HEAD you started from;
  never amend, rebase, or reset; leave the working tree clean.
- Commit message per `AGENTS.md`, with trailers: `Done: Task 17.3 — <one
  line>`, `Fixed: ISSUE-034`, `Next: Task 17.4 — Phase 17 review (Tier 1,
  with Salvador)`, and the `Co-Authored-By:` signature line from your
  dispatch prompt.
- Greenfield-vs-surgical routing: this task makes precise, well-specified
  additions to `repl.py`'s existing control flow (new methods, two new
  call sites in already-existing methods) and two new functions appended
  to `memory.py` — not a rewrite of either file's existing logic.
  Dispatch to the default model (deepseek-v4-pro); do not use a
  `SASHA_DISPATCH_MODEL` local-model override.
