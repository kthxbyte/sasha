# Task 18.3: repl.py — general memory load, crash-trigger hook, two-tier sasha memory

## Authoritative contract
`docs/superpowers/specs/2026-07-17-general-memory-design.md` is the
complete design record for this task — read it first, especially §5.1
(loading general memory), §5.3 (the crash trigger), and §5.4 (`sasha
memory` showing both tiers). This is the final task in Phase 18. Tasks
18.1 (landed) built `memory.py`'s `general_memory_root`/`general_*`
functions and the crash-encode pair; Task 18.2 (landed) wired
`remember`'s fallback and gave `Session` its second memory slot
(`set_general_memory`/`set_project_memory`). This task makes both
actually fire during real REPL usage.

## What already exists
- `src/sasha/memory.py` (Task 18.1, landed): `load_general_memory_block(max_chars=8000)
  -> tuple[str, int]`, `is_crash_signal(signum: int | None) -> bool`,
  `encode_crash(repo_root, command, result) -> bool`,
  `encode_general_crash(command, result) -> bool`. Not modified by this
  task.
- `src/sasha/session.py` (Task 18.2, landed): `Session.set_general_memory(text: str) -> None`.
  Not modified by this task.
- `src/sasha/repl.py`: the full current file is reproduced below as
  "What already exists" context. Every line not shown as changed in
  "What's new" is unchanged from today.

## What's new

Replace `src/sasha/repl.py` in full with the following. Four changes
from today's file: `loop()` gains one general-memory load, a new
`_check_crash` method plus its call site in `run_once`, and
`_handle_memory_builtin` is rewritten for two-tier display. Every other
line is byte-identical to today's.

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
        general_block, general_count = memory.load_general_memory_block(
            max_chars=1_000_000)
        if general_count == 0:
            self._sink.write_line("[sasha] no general memory yet")
        else:
            self._sink.write_line(
                f"[sasha] general memory: {general_count} entries")
            for entry_line in general_block.split("\n")[-10:]:
                self._sink.write_line(f"[sasha]   {entry_line}")

        if self._current_repo_root is None:
            return
        block, count = memory.load_memory_block(
            self._current_repo_root, max_chars=1_000_000)
        if count == 0:
            self._sink.write_line("[sasha] no memory yet for this project")
            return
        self._sink.write_line(
            f"[sasha] project memory: {count} entries for "
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
        self._session.set_project_memory("")
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
            self._session.set_project_memory("")
            self._memory_dir_existed = False
            return
        block, count = memory.load_memory_block(new_root)
        self._session.set_project_memory(block)
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

    def _check_crash(self, line, result):
        if not memory.is_crash_signal(result.signal):
            return
        if self._current_repo_root is not None:
            memory.encode_crash(self._current_repo_root, line, result)
        else:
            memory.encode_general_crash(line, result)

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
        self._check_crash(stripped, result)
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
        general_block, _ = memory.load_general_memory_block()
        self._session.set_general_memory(general_block)
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

1. **General memory is loaded exactly once, in `loop()`, before the
   first prompt.** It is *not* reloaded on place-shift or after every
   command — this deliberately matches project memory's own behavior:
   project memory is also just a snapshot loaded at the moment you're
   recognized as being in a given repo, not live-refreshed after a
   same-repo write during that visit. Neither memory tier auto-refreshes
   mid-visit; both are "recent snapshot," not "live view." Do not add
   any refresh-after-write logic — it would be new behavior neither tier
   has today, not a gap to close.
2. **`_check_crash` runs after `_check_commit` and before
   `_check_memory_created`** in `run_once`'s branch (e) — same ordering
   rationale as everything else here: update memory state, then let the
   generic creation-notice check run last so it catches whichever of the
   two triggers (if either) just created `.sasha/memory/` for the first
   time.
3. **`_handle_memory_builtin` always calls `load_general_memory_block`
   and `load_memory_block` fresh** (not reading from `self._session`'s
   cached blocks) — same as Phase 17's original behavior for project
   memory. `sasha memory` is a deliberate user command that should show
   current on-disk state, not a possibly-stale LLM-context snapshot.
4. **The two-tier output always shows general memory first**, matching
   the system prompt's own general-before-project layering (Task 18.2).
   Outside a git repo, only the general section prints — no more "not
   inside a git repository" message, since general memory is always a
   valid thing to show now.
5. **The project section's header text changes from `"memory: N
   entries for X"` to `"project memory: N entries for X"`** — needed to
   distinguish it from the new general section's `"general memory: N
   entries"` header. This is a cosmetic label change only; the
   `"no memory yet for this project"`/`"nothing to forget"` messages and
   `_handle_forget_builtin`'s behavior are completely unchanged.

## Constraints
- Modify only `src/sasha/repl.py`. Do not touch `src/sasha/memory.py`,
  `src/sasha/tools.py`, `src/sasha/session.py`, `src/sasha/executor.py`,
  `README.md`, or anything under `docs/`.
- `_handle_forget_builtin`'s behavior (still project-memory-only, still
  requires typed `"yes"` confirmation) is completely unchanged — this
  task does not add a way to forget general memory (an explicit
  non-goal in the design's Scope section).
- `Repl.__init__`'s signature does not change.

## Acceptance criteria
(encoded in `tests/contract/test_task_18_3_repl_general_crash.py`, 7
new tests, plus amended assertions in
`tests/contract/test_task_17_3_repl_memory.py` — three tests
(`test_ac_sasha_memory_outside_git_repo`, `test_ac_sasha_memory_no_entries_yet`,
`test_ac_sasha_memory_shows_entries`) amended for the new two-tier
output and `XDG_CONFIG_HOME` test isolation — all already amended by
Tier 1 in this same spec commit)

1. `loop()` loads general memory into `Session` before the first prompt
   — a pre-existing general memory entry is visible in
   `session.system_prompt` immediately after `loop()` starts, even
   before any command runs.
2. With no general memory yet, `loop()` doesn't add a "What you
   generally remember" section at all.
3. `sasha memory` shows general memory (count + recent entries, or "no
   general memory yet") unconditionally, and additionally shows project
   memory when inside a repo — general section always first.
4. A command whose result carries a crash signal (`SIGSEGV`, `SIGABRT`,
   etc.) encodes a `"crash"`-kind entry — into the current project's
   memory when inside a repo, into general memory otherwise.
5. `SIGKILL` and ordinary non-zero exits (no signal at all) never
   trigger the crash encoding — regression guard against over-firing.
6. The full contract suite passes with the three amended
   `test_task_17_3_repl_memory.py` tests in place, each now isolating
   `XDG_CONFIG_HOME` to `tmp_path` so `sasha memory`'s new
   general-memory read never touches the real host's
   `~/.config/sasha/memory/`.

## Standing instructions
- Your goal: make `tests/contract/test_task_18_3_repl_general_crash.py`
  pass, and keep the full contract suite green:
  `PYTHONPATH=src python3 -m pytest tests/contract/`.
- NEVER modify files under `tests/contract/` — the gate rejects the
  commit if you do. The three amendments to
  `test_task_17_3_repl_memory.py` are already in your base commit.
- Deliver exactly ONE new commit on top of the HEAD you started from;
  never amend, rebase, or reset; leave the working tree clean.
- Commit message per `AGENTS.md`, with trailers: `Done: Task 18.3 — <one
  line>`, `Fixed: ISSUE-036`, `Next: Task 18.4 — Phase 18 review (Tier
  1, with Salvador)`, and the `Co-Authored-By:` signature line from
  your dispatch prompt.
- Greenfield-vs-surgical routing: this task makes precise, well-
  specified additions to `repl.py`'s existing control flow (one new
  method, two new call sites, one rewritten method) — not a rewrite of
  existing logic. Dispatch to the default model (deepseek-v4-pro); do
  not use a `SASHA_DISPATCH_MODEL` local-model override.
