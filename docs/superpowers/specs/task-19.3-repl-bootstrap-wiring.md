# Task 19.3: repl.py — stage 1/2 bootstrap wiring into _check_place_shift

## Authoritative contract
`docs/superpowers/specs/2026-07-18-memory-bootstrap-design.md` is the
complete design record for this task — read it first, especially §4 (the
full sequence diagram), §5 (all six algorithms), and §6 (error handling
table). Tasks 19.1 (`memory.py`: `bounded_history`, `root_docs`,
`encode_bootstrap`, the write lock) and 19.2 (`prompt.py`'s `\S`/blink
token, `Repl._background_tasks` + `_begin_background_task`/
`_end_background_task`) have both landed by the time this task dispatches
— this task is what finally sequences all of it together during real
place-shift detection. Unlike Tasks 19.1/19.2, this task does **not** add
anything new to `memory.py` — everything it needs lives in `repl.py`
alone (see "one important refinement" below for why).

## One important refinement versus the design doc's literal wording
The design doc's §5.6 describes retry eligibility as "no `kind=bootstrap`
entry exists yet." Implemented literally, that would make **every**
pre-existing `.sasha/memory/` directory from Phases 17–18 (which never
had `kind="bootstrap"` entries, because the concept didn't exist yet)
look retry-eligible the moment this code lands — meaning bootstrap logic
would start silently spawning background LLM calls for repos that have
nothing to do with this feature, breaking the "additive, not a redesign"
promise those phases made. **The fix implemented here**: retry eligibility
is tracked with an explicit sentinel file,
`.sasha/memory/.bootstrap_pending`, written *only* by this task's own
`_spawn_bootstrap_summary` (the single place that ever starts a stage-2
attempt) and removed once stage 2 succeeds. A directory without this file
— whether because it predates this feature entirely, or because its
stage 2 already finished — is never touched by the retry path. This is
why no `memory.py` changes are needed: the sentinel is a plain file
existence check, handled entirely in `repl.py`, the same way
`_check_memory_created` already does its own inline `(repo_root /
".sasha" / "memory").exists()` check without going through `memory.py`.

## What already exists
- `src/sasha/memory.py`: `resolve_git_root(cwd) -> Path | None`,
  `ensure_memory_dir(repo_root) -> tuple[Path, bool]`,
  `encode_commit(repo_root, message, commit_hash) -> bool`,
  `load_memory_block(repo_root, max_chars=8000) -> tuple[str, int]`,
  `most_recent_gist(repo_root) -> str | None`,
  `bounded_history(repo_root, max_chars=10_000) -> list[tuple[str, str]]`
  (Task 19.1, newest-first `(hash, message)` pairs),
  `root_docs(repo_root, max_chars=6_000) -> list[tuple[str, str]]`
  (Task 19.1, newest-mtime-first `(filename, content)` pairs),
  `encode_bootstrap(repo_root, text) -> bool` (Task 19.1, writes one
  `kind="bootstrap"` entry, `pointer=None`). Read the file directly for
  exact signatures. **This task does not modify `memory.py` at all.**
- `src/sasha/llm.py`: `ChatClient.chat(messages: list[dict], tools:
  list[dict], on_text: Callable[[str], None]) -> ChatResponse` (positional
  args — not keyword), `ChatResponse.text: str`, `ChatError` (raised for
  every failure mode: HTTP error, unreachable, malformed stream, and a
  catch-all for anything unexpected — read `llm.py`'s `chat()` method
  directly; nothing it can raise escapes `ChatError`). Not modified by
  this task.
- `Repl._client_factory`, `Repl._current_profile`, `Repl._sink` (all
  existing instance attributes, already used by `_consult`) —
  `_bootstrap_stage_2` (below) reuses the exact same
  `self._client_factory(self._current_profile)` pattern `_consult`
  already uses.
- `Repl._background_tasks`, `Repl._begin_background_task()`,
  `Repl._end_background_task()` (Task 19.2, landed) — this task is the
  first caller of the begin/end pair.

## What's new — `src/sasha/repl.py` only, full file, in full:
```python
import dataclasses
import getpass
import os
import re
import shlex
import socket
import sys
import threading
from typing import Protocol

import sasha.config as config
from sasha.agent import run_consult
from sasha.llm import ChatError
from sasha import memory
from sasha.triggers import Decision, command_word, decide
import sasha.prompt as prompt_mod


_BULLET_RE = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s*")


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
        self._background_tasks = 0
        self._bootstrap_attempted = set()      # set[pathlib.Path]
        self._last_bootstrap_thread = None     # threading.Thread | None, test seam

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
        just_bootstrapped = False
        memory_dir = new_root / ".sasha" / "memory"
        pending_marker = memory_dir / ".bootstrap_pending"
        if not memory_dir.exists():
            just_bootstrapped = self._bootstrap_repo(new_root)
        elif new_root not in self._bootstrap_attempted and pending_marker.exists():
            self._retry_bootstrap_summary(new_root)
        block, count = memory.load_memory_block(new_root)
        self._session.set_project_memory(block)
        self._memory_dir_existed = memory_dir.exists()
        if count and not just_bootstrapped:
            gist = memory.most_recent_gist(new_root)
            self._sink.write_line(
                f'[sasha] project: {new_root.name} ({count} memories, '
                f'last: "{gist}")')

    def _bootstrap_repo(self, repo_root):
        history = memory.bounded_history(repo_root)
        docs = memory.root_docs(repo_root)
        if not history and not docs:
            return False
        memory.ensure_memory_dir(repo_root)
        for commit_hash, message in reversed(history):
            memory.encode_commit(repo_root, message, commit_hash)
        self._sink.write_line(
            f"[sasha] no memory yet for {repo_root.name} — building "
            f"initial context from history in the background")
        self._spawn_bootstrap_summary(repo_root, history, docs)
        return True

    def _retry_bootstrap_summary(self, repo_root):
        history = memory.bounded_history(repo_root)
        docs = memory.root_docs(repo_root)
        if history or docs:
            self._spawn_bootstrap_summary(repo_root, history, docs)
        else:
            self._bootstrap_attempted.add(repo_root)
            self._clear_bootstrap_pending(repo_root)

    def _spawn_bootstrap_summary(self, repo_root, history, docs):
        self._bootstrap_attempted.add(repo_root)
        (repo_root / ".sasha" / "memory" / ".bootstrap_pending").write_text("")
        self._begin_background_task()
        thread = threading.Thread(
            target=self._bootstrap_stage_2,
            args=(repo_root, history, docs), daemon=True)
        self._last_bootstrap_thread = thread
        thread.start()

    def _bootstrap_stage_2(self, repo_root, history, docs):
        try:
            prompt = self._build_bootstrap_prompt(history, docs)
            client = self._client_factory(self._current_profile)
            response = client.chat(
                [{"role": "user", "content": prompt}], [], lambda _: None)
            lines = self._parse_bootstrap_lines(response.text)
        except ChatError:
            lines = []
        if lines:
            for line in lines:
                memory.encode_bootstrap(repo_root, line)
            self._sink.write_line(
                self._bootstrap_finish_message(len(lines), docs))
            self._clear_bootstrap_pending(repo_root)
        self._end_background_task()

    def _clear_bootstrap_pending(self, repo_root):
        pending = repo_root / ".sasha" / "memory" / ".bootstrap_pending"
        if pending.exists():
            pending.unlink()

    def _build_bootstrap_prompt(self, history, docs):
        parts = [
            "You are helping build initial long-term memory for a "
            "software project neither of us has seen before. Read the "
            "material below and respond with 3 to 8 short bullet lines "
            "(one fact per line, starting with '- '), each a distinct, "
            "concrete fact about what this project is or does. Do not "
            "add any other text before, between, or after the bullets."
        ]
        if docs:
            parts.append("### Project documentation")
            for filename, content in docs:
                parts.append(f"--- {filename} ---\n{content}")
        if history:
            parts.append("### Recent commit history")
            for _, message in history:
                parts.append(f"- {message}")
        return "\n\n".join(parts)

    def _parse_bootstrap_lines(self, text):
        lines = []
        for raw_line in text.splitlines():
            stripped = _BULLET_RE.sub("", raw_line).strip()
            if stripped:
                lines.append(stripped)
            if len(lines) == 8:
                break
        return lines

    def _bootstrap_finish_message(self, count, docs):
        if len(docs) == 1:
            return (f"[sasha] finished building context: {count} entries "
                     f"(from {docs[0][0]})")
        if len(docs) > 1:
            return (f"[sasha] finished building context: {count} entries "
                     f"(from {len(docs)} files)")
        return f"[sasha] finished building context: {count} entries"

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

    def _begin_background_task(self):
        self._background_tasks += 1

    def _end_background_task(self):
        self._background_tasks = max(0, self._background_tasks - 1)

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
                home=home, color_enabled=self._sink.color_enabled,
                busy=self._background_tasks > 0)
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
1. **`_bootstrap_repo` checks `history`/`docs` *before* creating anything.**
   A repo with zero commits and zero root docs is left completely
   untouched — no directory, no sentinel, nothing — exactly matching
   this repo's behavior before this task existed. Only a repo with real
   content to bootstrap ever gets `.sasha/memory/` eagerly created.
2. **The `.bootstrap_pending` sentinel, not "absence of `kind=bootstrap`
   entries," gates retry eligibility.** See "One important refinement"
   above — this is the key difference from the design doc's literal
   §5.6 wording, and it's why no pre-existing `.sasha/memory/` directory
   from Phases 17–18 (or any test fixture built with `encode_commit`/
   `encode_remember` directly) is ever touched by the retry path: none of
   them have ever had a reason to write this file.
3. **`just_bootstrapped` suppresses the ordinary "project: ... (N
   memories...)" announcement** exactly once, the first time a repo with
   real history/docs is seen — it would otherwise print immediately
   after (and redundantly with) the "no memory yet ... building ..."
   line, since stage 1's own commit replay makes `count` nonzero in that
   same call.
4. **Two spawn paths, one bookkeeping call.** `_bootstrap_repo`
   (brand-new-to-Sasha repo) and `_retry_bootstrap_summary` (repo already
   has `.sasha/memory/` and a pending marker from an earlier, unfinished
   attempt) both funnel into `_spawn_bootstrap_summary`, the single place
   `_bootstrap_attempted.add(...)`, the sentinel write, and
   `_begin_background_task()` all happen.
5. **`_bootstrap_stage_2` catches only `ChatError`** — `ChatClient.chat`
   already wraps every failure mode in `ChatError`, so nothing else can
   escape from that call. `memory.encode_bootstrap`'s own writes are
   deliberately not wrapped in a broader try/except, matching how the
   existing commit-hook never guards its own writes either.
6. **Sink writes from the background thread are unsynchronized with the
   main thread's own writes** — a deliberate, accepted, cosmetic-only
   limitation for v1 (two `write_line` calls could in principle
   interleave on screen); never a data-safety issue, since
   `semantic.jsonl` writes are protected by Task 19.1's lock regardless.
7. **`_last_bootstrap_thread` is a deliberate test seam**, not used by any
   production code path — it exists purely so contract tests can
   deterministically `.join()` the spawned thread instead of polling or
   sleeping.
8. **Four pre-existing Task 17.3 contract tests were amended** (in
   `tests/contract/test_task_17_3_repl_memory.py`, the commit-hook
   section): each needs one real git commit to exist for
   `encode_latest_commit`'s own `git log -1` call to read something
   genuine, but previously made that commit *before* the first
   `_check_place_shift()` — which, now that a repo with real history at
   first place-shift eagerly bootstraps, would exercise bootstrap instead
   of the commit-hook path those tests exist to verify. Each was fixed by
   moving its `_commit(...)` call to *after* the first
   `_check_place_shift()` (see the comment already in that file) — the
   repo is empty (matching pre-Task-19.3 behavior exactly) at the moment
   bootstrap's history/docs check runs, so every original assertion is
   unchanged. You do not need to touch that file; it is already correct
   on the base you're starting from.

## Constraints
- Modify only `src/sasha/repl.py` (the full file above). Do **not**
  modify `src/sasha/memory.py`, `src/sasha/prompt.py`,
  `src/sasha/session.py`, `src/sasha/tools.py`, `src/sasha/executor.py`,
  `src/sasha/llm.py`, `README.md`, or anything under `docs/`.
- `Repl.__init__`'s existing constructor call shape (9 positional/keyword
  args, unchanged from Task 17.3/18.x) must keep working with zero
  changes — every existing test constructing a `Repl` this way must keep
  passing unmodified.
- Do not change the wording or trigger conditions of the existing
  `"[sasha] created .sasha/memory/ ..."` notice (`_check_memory_created`)
  or the existing commit-hook/`remember`-tool/crash-trigger behavior —
  this task only adds new behavior around repos with real, pre-existing
  history/docs at first place-shift time.

## Acceptance criteria
(encoded in `tests/contract/test_task_19_3_repl_bootstrap_wiring.py`)

1. First place-shift into a repo with commits and no `.sasha/memory/`
   yet: replayed commit entries appear in `semantic.jsonl` oldest-to-newest
   (their original chronological order, not git-log's newest-first
   order); exactly one "no memory yet for `<repo>` — building initial
   context from history in the background" line is printed; the ordinary
   "project: ... (N memories...)" line is *not* printed in that same
   call; a `.bootstrap_pending` sentinel file exists immediately (the
   spawn happens synchronously, only the LLM call itself is
   backgrounded).
2. A repo with zero commits and zero root docs: `.sasha/memory/` is
   **not** created at all; no start line is printed; `_background_tasks`
   is `0`; no thread is spawned (`_last_bootstrap_thread` stays `None`) —
   byte-identical to this repo's behavior before this task.
3. A repo with root docs but zero commits still spawns stage 2 (history
   passed to it is `[]`, docs is non-empty).
4. Stage 2 success (via a real HTTP round-trip against a scripted stub
   server returning a well-formed multi-bullet response): writes one
   `kind="bootstrap"` entry per parsed bullet line, `pointer: null` on
   each; prints the finish line; the `.bootstrap_pending` sentinel is
   removed; `_background_tasks` returns to `0` after
   `_last_bootstrap_thread.join()`.
5. The finish-line wording: exactly one root doc consumed names that
   file; more than one states a bare count; zero root docs (history-only
   summary) omits any file mention — all three cases exercised.
6. Stage 2 failure via an unreachable stub (`sse_stub.refused_base_url()`):
   zero `kind="bootstrap"` entries written, no finish line printed,
   the `.bootstrap_pending` sentinel is **still present** (not cleared on
   failure), `_background_tasks` returns to `0`.
7. Stage 2 receiving an empty/whitespace-only response body: treated
   identically to failure — zero entries, no finish line, sentinel
   remains.
8. `_parse_bootstrap_lines` (tested directly): strips `-`, `*`, `•`,
   `1.`, `2)`-style leading markers; drops blank lines; caps at 8 lines
   even if the model returns more; a marker-less plain-text line is still
   kept (just stripped of surrounding whitespace).
9. `_build_bootstrap_prompt` (tested directly): includes a
   "### Project documentation" section only when `docs` is non-empty,
   and a "### Recent commit history" section only when `history` is
   non-empty; the content of every doc/commit passed in appears verbatim
   somewhere in the built prompt.
10. Retry bookkeeping: a repo whose `.sasha/memory/` and
    `.bootstrap_pending` sentinel already exist (simulating a prior
    session's stage 1 that ran but never reached a successful stage 2)
    triggers stage 2 on the first place-shift into it in a *new* `Repl`
    instance; a second `_check_place_shift()` call for the *same* repo
    within the *same* `Repl` instance (simulating bouncing out and back
    in) does not spawn a second attempt — verified by asserting only one
    HTTP request ever reached the stub server across both calls.
11. A repo whose `.sasha/memory/` exists but has **no**
    `.bootstrap_pending` sentinel (simulating either a fully-succeeded
    prior bootstrap, or — critically — a Phase-17/18-era directory that
    predates this feature and was never touched by bootstrap at all)
    never spawns anything on place-shift, in a fresh `Repl` instance
    whose `_bootstrap_attempted` is empty — this specifically proves the
    sentinel check, not the attempted-set check, is what's gating it.
12. Regression guard: the existing `"[sasha] created .sasha/memory/ ..."`
    notice (`_check_memory_created`, unmodified by this task) still fires
    correctly via the commit-hook and `remember`-tool paths on a repo
    with no history/docs at first place-shift (i.e. bootstrap is a
    no-op there, exactly as before this task).

## Standing instructions
- Your goal: make the contract tests in
  `tests/contract/test_task_19_3_repl_bootstrap_wiring.py` pass, and keep
  the full contract suite green:
  `PYTHONPATH=src python3 -m pytest tests/contract/`.
- NEVER modify files under `tests/contract/` — the gate rejects the
  commit if you do.
- Deliver exactly ONE new commit on top of the HEAD you started from;
  never amend, rebase, or reset; leave the working tree clean.
- Commit message per `AGENTS.md`, with trailers: `Done: Task 19.3 — <one
  line>`, `Fixed: ISSUE-038`, `Next: Task 19.4 — Phase 19 review (Tier 1,
  with Salvador)`, and the `Co-Authored-By:` signature line from your
  dispatch prompt.
- Greenfield-vs-surgical routing: this task makes precise, well-specified
  additions to `repl.py`'s existing control flow (new methods, new call
  sites inside an existing method, one new module-level regex, three new
  imports) — not a rewrite of its existing logic, and touches no other
  file. Dispatch to the default model (deepseek-v4-pro); do not use a
  `SASHA_DISPATCH_MODEL` local-model override.
