import dataclasses
import getpass
import os
import re
import shlex
import shutil
import socket
import sys
import traceback
import uuid
from typing import Protocol

import sasha.config as config
from sasha.activity_indicator import ActivityIndicator
from sasha.agent import run_consult
from sasha.llm import ChatError
from sasha import git_headline
from sasha import guard
from sasha import memory
from sasha import safe_commands
from sasha import textio
from sasha import tool_log
from sasha.triggers import Decision, decide
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
    def __init__(self, config, executor, session, client_factory,
                 confirmer, input_fn, sink, getpass_fn=getpass.getpass,
                 questioner=None, state_dir=None, breaker=None):
        self._config = config
        self._executor = executor
        self._session = session
        self._client_factory = client_factory
        self._confirmer = confirmer
        self._input_fn = input_fn
        self._sink = sink
        self._getpass_fn = getpass_fn
        self._questioner = questioner
        # Where crash reports go; None means "no persistent state" (the
        # read-only-$HOME fallback), in which case the net still runs but
        # prints the traceback instead of writing it.
        self._state_dir = state_dir
        self._breaker = breaker if breaker is not None else guard.CircuitBreaker()
        self._current_profile = config.profiles[config.default_profile]
        self._activity = ActivityIndicator(
            colors={"thinking": config.activity_indicator_thinking_color,
                    "tool": config.activity_indicator_tool_color},
            interval_s=config.activity_indicator_interval_s,
            enabled=config.activity_indicator_enabled and sink.color_enabled,
        )
        self._current_repo_root = None   # pathlib.Path | None
        self._last_checked_cwd = None    # str | None
        self._memory_dir_existed = False
        self._headlined_dirs: set[str] = set()
        self._phase_needs_newline = False

    def _on_activity_phase(self, phase):
        if phase is None:
            self._activity.stop()
        else:
            if self._phase_needs_newline:
                self._sink.ensure_newline()
            self._phase_needs_newline = True
            self._activity.start(phase)

    def _consult(self, auto_prompt=None):
        if auto_prompt:
            self._session.add_user(auto_prompt)
        self._phase_needs_newline = False
        self._sink.write_line("[sasha] thinking…")
        try:
            client = self._client_factory(self._current_profile)
            on_reasoning = getattr(self._sink, "write_reasoning_chunk",
                                   self._sink.write_chunk)
            on_async_status = getattr(self._sink, "write_async_line",
                                      self._sink.write_line)

            def _on_warning(text):
                try:
                    self._sink.write_line(text, color=_WARNING_COLOR)
                except TypeError:
                    self._sink.write_line(text)

            run_consult(self._session, client, self._executor,
                        self._confirmer, self._sink.write_chunk,
                        on_reasoning=on_reasoning,
                        on_status=self._sink.write_line,
                        on_warning=_on_warning,
                        on_async_status=on_async_status,
                         max_tool_turns=self._config.limits.max_tool_turns,
                         on_phase=self._on_activity_phase,
                         questioner=self._questioner)
        except ChatError as e:
            self._sink.write_line(
                f"[sasha] {self._current_profile.name} unreachable: "
                f"{e.reason} — 'sasha profile <name>' to switch")
        finally:
            self._activity.stop()
            self._sink.ensure_newline()
            self._check_memory_created()

    def _repair_after_crash(self) -> None:
        """Best-effort terminal repair. Never raises: this runs *because*
        something already failed."""
        try:
            self._activity.stop()
        except Exception:
            pass
        self._phase_needs_newline = False
        try:
            ActivityIndicator.restore_terminal()
        except Exception:
            pass
        try:
            self._sink.repair_terminal_state()
        except Exception:
            pass

    def _crash_context(self) -> dict:
        notes = getattr(self._session, "notes", None) or []
        context = {
            "cwd": self._executor.cwd,
            "profile": self._current_profile.name,
            "model": self._current_profile.model,
            "argv": " ".join(sys.argv),
            "recent_commands": " | ".join(
                f"{n.line} ({n.exit_code})" for n in notes[-5:]),
        }
        context.update(guard.git_state())
        return context

    def _handle_crash(self, exc: Exception) -> bool:
        """Keep the session alive through an internal error.

        Repairs the terminal, writes a crash report, and tells the user where
        it is. Returns False when the same failure keeps recurring, in which
        case the caller should exit cleanly rather than loop forever.
        """
        sig = guard.signature(exc)
        tripped = self._breaker.record(sig)
        repeats = self._breaker.count(sig)

        self._repair_after_crash()

        bundle = guard.write_crash_bundle(
            self._state_dir, exc, context=self._crash_context(),
            signature_name=sig)

        self._sink.write_line(f"[sasha] internal error: {guard.describe(exc)}")
        if bundle is not None:
            self._sink.write_line(f"[sasha] full traceback: {bundle}")
        else:
            # Nowhere durable to put it, so don't lose it.
            for line in traceback.format_exc().splitlines():
                self._sink.write_line(line, color=_DIM)
        # Close the loop between the crash net and `sasha repair`: without this
        # the two features don't know about each other, and the report just
        # sits on disk.
        self._sink.write_line(
            "[sasha] 'sasha repair' opens a session for working on this")

        if tripped:
            self._sink.write_line(
                f"[sasha] that same failure has now happened {repeats} times — "
                f"exiting instead of looping. Your shell is unaffected.")
            if bundle is not None:
                self._sink.write_line(
                    f"[sasha] start here: {bundle}")
            return False

        self._sink.write_line(
            "[sasha] this session is still alive — press Enter to continue")
        return True

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

        if verb == "profile":
            if len(tokens) == 1:
                p = self._current_profile
                self._sink.write_line(_profile_line(p))
            elif len(tokens) >= 2:
                self._activate_profile(tokens[1])
        elif verb == "memory":
            self._handle_memory_builtin()
        elif verb == "forget":
            self._handle_forget_builtin()
        elif verb == "compact-log":
            self._handle_compact_log_builtin()
        elif verb == "reasoning":
            self._handle_reasoning_builtin()
        elif verb == "repair":
            self._sink.write_line(
                "[sasha] 'repair' runs as its own session — exit this one and "
                "run 'sasha repair' at your shell prompt")
        elif verb == "help":
            self._sink.write_line("[sasha] sasha profile [<name>] "
                      "(asks for a key if needed), "
                      "sasha memory, sasha forget, "
                      "sasha compact-log, "
                      "sasha reasoning, "
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

    def _handle_compact_log_builtin(self):
        result = tool_log.compact()
        before_mb = result.before_bytes / 1_000_000
        after_mb = result.after_bytes / 1_000_000
        self._sink.write_line(
            f"[sasha] tool_log.jsonl compacted: {before_mb:.1f} MB "
            f"→ {after_mb:.1f} MB")
        if result.still_too_big:
            self._sink.write_line(
                f"[sasha] still {after_mb:.1f} MB even with all results "
                f"stripped — consider deleting or archiving it")

    def _handle_reasoning_builtin(self):
        getter = getattr(self._sink, "reasoning_text", None)
        text = getter() if getter is not None else ""
        if not text.strip():
            self._sink.write_line(
                "[sasha] no reasoning recorded this session")
            return
        if getattr(self._sink, "reasoning_truncated", False):
            self._sink.write_line(
                "[sasha] earlier reasoning dropped — showing the tail")
        for line in text.splitlines():
            self._sink.write_line(line, color=_REASONING_COLOR)

    def _check_place_shift(self) -> bool:
        cwd = self._executor.cwd
        if cwd == self._last_checked_cwd:
            return False
        self._last_checked_cwd = cwd
        new_root = memory.resolve_git_root(cwd)
        if new_root == self._current_repo_root:
            return False
        self._current_repo_root = new_root
        if new_root is None:
            self._session.set_project_memory("")
            self._memory_dir_existed = False
            return False
        just_replayed = False
        memory_dir = new_root / ".sasha" / "memory"
        if not memory_dir.exists():
            just_replayed = self._replay_history(new_root)
        block, count = memory.load_memory_block(new_root)
        self._session.set_project_memory(block)
        self._memory_dir_existed = memory_dir.exists()
        if count and not just_replayed:
            gist = memory.most_recent_gist(new_root)
            self._sink.write_line(
                f'[sasha] project: {new_root.name} ({count} memories, '
                f'last: "{gist}")')
            return True
        return False

    def _check_git_headline(self) -> None:
        """One free line the first time this session enters a repository.

        The last commit, printed once per directory. No LLM, no heuristics, no
        background thread — see git_headline.py for what this replaced.
        """
        cwd = self._executor.cwd
        if cwd in self._headlined_dirs:
            return
        self._headlined_dirs.add(cwd)
        if self._current_repo_root is None:
            return
        text = git_headline.headline(self._current_repo_root)
        if text is not None:
            self._sink.write_line(text)

    def _replay_history(self, repo_root):
        """Seed project memory from recent commit messages. Free and mechanical.

        This is all that survives of the old two-stage memory bootstrap. Stage 2
        asked the model to summarise the repository's history and root docs, then
        wrote its answer into semantic memory as `kind="bootstrap"` entries,
        which were injected into EVERY later prompt as fact. Three problems made
        it a net negative: a 26B's summary of git history is not authoritative,
        a hallucination there is durable and indistinguishable from something the
        user said, and ISSUE-039 recorded that there was no refresh path — the
        only remedy was `sasha forget`, which destroys all memory.

        The replay costs nothing, cannot hallucinate, and is regenerated from git
        whenever memory is absent. A repository with no commits is left
        completely untouched.
        """
        history = memory.bounded_history(repo_root)
        if not history:
            return False
        memory.ensure_memory_dir(repo_root)
        for commit_hash, message in reversed(history):
            memory.encode_commit(repo_root, message, commit_hash)
        self._sink.write_line(
            f"[sasha] no memory yet for {repo_root.name} — replayed "
            f"{len(history)} recent commit(s) into project memory")
        return True

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

        # a. empty line → nothing to do
        if not stripped:
            return True

        # b. ?? <text> → consult (executor untouched)
        if stripped.startswith("?? "):
            text = stripped[3:].strip()
            self._session.add_user(text)
            self._consult()
            return True

        # c. sasha <builtin>
        if stripped.startswith("sasha "):
            return self._handle_builtin(stripped)

        # d. otherwise
        if stripped in ("exit", "quit"):
            return False

        syntax_error = self._executor.check_syntax(stripped)
        if syntax_error is not None:
            prompt = (f"The line `{stripped}` is not valid bash: "
                      f"{syntax_error}. Explain and give a corrected line.")
            self._consult(prompt)
            return True

        result = self._executor.run(stripped)

        result_text = f"exit code: {result.exit_code}\nstderr:\n{result.stderr_text}"
        result_omitted_reason = None
        if safe_commands.touches_sensitive_path(stripped, self._executor):
            result_text = None
            result_omitted_reason = "sensitive_path"

        tool_log.log_call(
            tool_name="run_command",
            call_id=str(uuid.uuid4()),
            arguments={"command": stripped},
            executed_command=None,
            cwd=self._executor.cwd,
            verdict="direct",
            executed=True,
            result_text=result_text,
            result_omitted_reason=result_omitted_reason,
        )
        tool_log.check_and_maybe_compact_async(self._sink.write_line)

        self._session.note_command(stripped, result)

        if result.ctrl_z_terminated:
            self._sink.write_line("[sasha] Ctrl+Z not supported — command terminated")
            return True

        if not self._check_place_shift():
            self._check_git_headline()
        self._check_commit(stripped, result)
        self._check_crash(stripped, result)
        self._check_memory_created()

        if decide(result) == Decision.AUTO_CONSULT:
            self._consult()

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
        if not self._check_place_shift():
            self._check_git_headline()
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
            # run_once is inside the handler too: Ctrl+C during a consult or a
            # tool call must abort the request and return to the prompt (v1
            # design §9.5), not unwind out of main() as a traceback (ISSUE-061).
            try:
                if not self.run_once(line):
                    return
            except KeyboardInterrupt:
                self._sink.write_line("")
                continue
            except EOFError:
                return
            except Exception as exc:
                # The crash safety net. Keeping the process alive here is what
                # preserves the session: history, cwd/env, and scrollback all
                # survive, so the repair consult starts warm. Except clauses are
                # ordered so KeyboardInterrupt/EOFError keep their meaning.
                if not self._handle_crash(exc):
                    return
                continue


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
        response = self._read("> ")
        if response is None:
            return ("no", "")
        response = response.strip()
        if response in ("y", "yes"):
            return ("yes", "")
        elif response in ("n", "no", ""):
            return ("no", "")
        elif response in ("e", "edit"):
            edited = self._read("edit> ")
            if edited is None:
                # A cancelled edit is a decline. Returning ("edit", "") would
                # hand the agent loop an empty command to execute.
                return ("no", "")
            return ("edit", edited)
        else:
            return ("reply", response)

    def _read(self, prompt):
        """Read one line at the gate; None means cancelled.

        A Ctrl+C or a closed stdin here means "cancel", not "crash": without
        this, the KeyboardInterrupt unwinds out of Repl.loop() and main() as a
        raw traceback, because loop()'s own handler wraps only its main-prompt
        input() call (ISSUE-061). An interrupt is reported as None rather than
        '' so it stays distinguishable from a deliberate empty answer, which
        already means "decline" at the first prompt but "edit to empty" at the
        second.
        """
        try:
            return self._input_fn(prompt)
        except (KeyboardInterrupt, EOFError):
            self._sink.write_line("[sasha] cancelled")
            return None


class TerminalQuestioner:
    def __init__(self, input_fn, sink):
        self._input_fn = input_fn
        self._sink = sink

    def ask(self, questions):
        all_pairs = []
        n = len(questions)
        try:
            for i, q in enumerate(questions, start=1):
                pairs = self._ask_one(i, n, q)
                if pairs is None:
                    return None
                all_pairs.extend(pairs)
        except (KeyboardInterrupt, EOFError):
            return None
        return all_pairs

    def _wrap(self, text, color):
        if self._sink.color_enabled:
            return f"{color}{text}{_RESET}"
        return text

    def _ask_one(self, i, n, q):
        header = q["header"]
        options = q["options"]
        multiple = q["multiple"]
        lines = [self._wrap(f"Question {i}/{n}: {header}", _COMMAND_COLOR),
                 "", f"  {q['question']}", ""]
        for idx, opt in enumerate(options, start=1):
            lines.append(f"  [{idx}] {opt['label']} — {opt['description']}")
        lines.append("")
        lines.append(self._wrap("  [C] Type your own answer", _DIM))
        lines.append("")
        if multiple:
            choice_line = (f"  Your choice(s) [1-{len(options)}, "
                            f"comma-separated, C]:")
        else:
            choice_line = f"  Your choice [1-{len(options)}, C]:"
        lines.append(self._wrap(choice_line, _PROMPT_COLOR))
        self._sink.write_line("\n".join(lines))

        while True:
            response = self._input_fn("> ").strip()
            if response == "":
                return None
            if response.upper() == "C":
                custom = self._input_fn("custom> ")
                return [(header, f"{header}: {custom}")]
            tokens = [t.strip() for t in response.split(",")]
            if not multiple and len(tokens) != 1:
                self._sink.write_line("  Invalid choice — try again.")
                continue
            indices = []
            valid = True
            for t in tokens:
                if not t.isdigit() or not (1 <= int(t) <= len(options)):
                    valid = False
                    break
                idx = int(t)
                if idx in indices:
                    valid = False
                    break
                indices.append(idx)
            if not valid:
                self._sink.write_line("  Invalid choice — try again.")
                continue
            return [(header, options[idx - 1]["label"]) for idx in indices]


_SASHA_COLOR = "\x1b[1;36m"   # bold cyan
_LLM_COLOR = "\x1b[1;35m"     # bold magenta
_PROMPT_COLOR = "\x1b[1;32m"  # bold green
_COMMAND_COLOR = "\x1b[1;33m"  # bold yellow — the literal command about to run
_DIM = "\x1b[2m"             # faint — secondary/explanatory text
_REASONING_COLOR = "\x1b[2;37m"  # dim white — thinking/reasoning-stage text
_WARNING_COLOR = "\x1b[1;5;31m"  # bold, blink, red — repetition/turn-limit stop only
_RESET = "\x1b[0m"
_RL_IGNORE_START = "\001"
_RL_IGNORE_END = "\002"

# Compact ("one line") reasoning display, mirroring the mechanism DSH's own
# ReasoningRow uses: the visible line is the first line of the most recent
# *completed* reasoning paragraph, so it holds still and then advances, rather
# than trailing every token as it streams.
_THINKING_PREFIX = "[thinking] "
_CLEAR_LINE = "\r\x1b[2K"
_MAX_REASONING_LOG_CHARS = 200_000

_PARA_SEPARATOR = re.compile(r"\r?\n(?:[\t ]*\r?\n)+")


def _first_line(text: str) -> str:
    newline = text.find("\n")
    return text if newline == -1 else text[:newline]


def _latest_completed_paragraph_first_line(text: str) -> str:
    """First line of the last blank-line-separated paragraph whose own first
    line has already completed; '' while the in-flight paragraph is still
    unterminated.

    Direct port of DSH's latestCompletedParagraphFirstLine (see
    dsh-client-ui-chat/lib/client.js, ReasoningRow).
    """
    summary = ""
    paragraph_start = 0
    while True:
        match = _PARA_SEPARATOR.search(text, paragraph_start)
        if match is None:
            paragraph_end = len(text)
        else:
            paragraph_end = match.start() + match.group(0).index("\n")
        newline = text.find("\n", paragraph_start)
        if newline != -1 and newline <= paragraph_end:
            candidate = text[paragraph_start:newline].strip()
            if candidate:
                summary = candidate
        if match is None:
            return summary
        paragraph_start = match.end()


class TerminalSink:
    def __init__(self, sasha_color: str = _SASHA_COLOR,
                 reasoning_color: str = _REASONING_COLOR,
                 reasoning_mode: str = "full") -> None:
        self.color_enabled = (sys.stdout.isatty()
                              and "NO_COLOR" not in os.environ)
        self._sasha_color = sasha_color
        self._reasoning_color = reasoning_color
        # "full" is the library default so a bare TerminalSink() keeps the
        # long-standing streaming behaviour (and its contract tests). The
        # product default, "compact", is applied by __main__ from config,
        # where the user can see and override it.
        self.reasoning_mode = reasoning_mode
        self._interactive = sys.stdout.isatty()
        self._ends_in_newline = True
        self._chunk_run_kind = None   # None | "llm" | "reasoning"
        # compact-mode preview state
        self._reasoning_buf = ""
        self._preview_text = ""
        self._preview_shown = False
        # session-wide retention, so compact mode stays recoverable
        self._reasoning_log = ""
        self._reasoning_block_open = False
        self.reasoning_truncated = False

    def _wrap(self, text: str, color: str) -> str:
        if not self.color_enabled:
            return text
        return f"{color}{text}{_RESET}"

    def _close_chunk_run(self) -> None:
        if self._chunk_run_kind is not None:
            sys.stdout.write(_RESET)
            self._chunk_run_kind = None

    def write_line(self, text: str, color: str | None = None) -> None:
        self._finish_reasoning_preview()
        self._reasoning_block_open = False
        self._close_chunk_run()
        if not self._ends_in_newline:
            sys.stdout.write("\n")
        print(self._wrap(textio.printable(text),
                        color if color is not None else self._sasha_color))
        self._ends_in_newline = True

    def write_async_line(self, text: str, color: str | None = None) -> None:
        """Like write_line(), but for messages that can arrive from a
        background thread at an unpredictable time relative to the
        interactive prompt — which is printed via input() directly,
        bypassing this sink, so _ends_in_newline can't be trusted to know
        whether the terminal is actually at a fresh line. Always writes a
        leading newline unconditionally rather than checking that state."""
        self._finish_reasoning_preview()
        self._reasoning_block_open = False
        self._close_chunk_run()
        sys.stdout.write("\n")
        print(self._wrap(textio.printable(text),
                        color if color is not None else self._sasha_color))
        self._ends_in_newline = True

    def _write_colored_chunk(self, text: str, kind: str, color: str) -> None:
        if not text:
            return
        # stdout's error handler is 'strict', so a surrogate -- from a
        # surrogateescaped path, or from a JSON \udXXX escape in model output --
        # would raise here and abort the consult.
        text = textio.printable(text)
        if self.color_enabled:
            if self._chunk_run_kind is not None and self._chunk_run_kind != kind:
                sys.stdout.write(_RESET)
                if not self._ends_in_newline:
                    sys.stdout.write("\n")
                    self._ends_in_newline = True
                self._chunk_run_kind = None
            if self._chunk_run_kind != kind:
                sys.stdout.write(color)
                self._chunk_run_kind = kind
        sys.stdout.write(text)
        sys.stdout.flush()
        self._ends_in_newline = text.endswith("\n")

    def write_chunk(self, text: str) -> None:
        if self.reasoning_mode == "compact" and self._reasoning_buf:
            self._finish_reasoning_preview()
        self._reasoning_block_open = False
        self._write_colored_chunk(text, "llm", _LLM_COLOR)

    # -- reasoning display -------------------------------------------------

    def _retain_reasoning(self, text: str) -> None:
        """Keep this session's reasoning in memory so compact mode is a
        display choice, not a loss — 'sasha reasoning' prints it back."""
        self._reasoning_log += text
        if len(self._reasoning_log) > _MAX_REASONING_LOG_CHARS:
            self._reasoning_log = self._reasoning_log[-_MAX_REASONING_LOG_CHARS:]
            self.reasoning_truncated = True

    def reasoning_text(self) -> str:
        """Everything the model has reasoned this session (a capped tail)."""
        return self._reasoning_log

    def _preview_line(self, summary: str) -> str:
        columns = shutil.get_terminal_size((80, 24)).columns
        available = max(1, columns - len(_THINKING_PREFIX) - 1)
        if len(summary) > available:
            summary = summary[:max(0, available - 1)] + "\u2026"
        return self._wrap(textio.printable(_THINKING_PREFIX + summary),
                          self._reasoning_color)

    def _draw_preview(self, summary: str) -> None:
        if self._preview_shown:
            sys.stdout.write(_CLEAR_LINE)
        sys.stdout.write(self._preview_line(summary))
        sys.stdout.flush()
        self._preview_shown = True
        self._preview_text = summary
        self._ends_in_newline = False

    def _finish_reasoning_preview(self) -> None:
        """Settle the one-line preview and break the line, so what stays in
        scrollback is exactly one dim line rather than the whole reasoning."""
        if self.reasoning_mode != "compact" or not self._reasoning_buf:
            return
        summary = (_latest_completed_paragraph_first_line(self._reasoning_buf)
                   or _first_line(self._reasoning_buf))
        if self._interactive:
            if not (self._preview_shown and summary == self._preview_text):
                self._draw_preview(summary)
        else:
            # Never drawn incrementally off a tty; emit the settled line once.
            sys.stdout.write(self._preview_line(summary))
        sys.stdout.write("\n")
        sys.stdout.flush()
        self._preview_shown = False
        self._preview_text = ""
        self._reasoning_buf = ""
        self._reasoning_block_open = False
        self._ends_in_newline = True

    def write_reasoning_chunk(self, text: str) -> None:
        if not text:
            return
        if not self._reasoning_block_open and self._reasoning_log:
            self._reasoning_log += "\n"
        self._reasoning_block_open = True
        self._retain_reasoning(text)
        if self.reasoning_mode != "compact":
            self._write_colored_chunk(text, "reasoning", self._reasoning_color)
            return
        if self._chunk_run_kind is not None:
            # A content run was still open; the preview needs its own line.
            self._close_chunk_run()
            if not self._ends_in_newline:
                sys.stdout.write("\n")
                self._ends_in_newline = True
        self._reasoning_buf += text
        if not self._interactive:
            return
        summary = _latest_completed_paragraph_first_line(self._reasoning_buf)
        if summary and summary != self._preview_text:
            self._draw_preview(summary)

    def ensure_newline(self) -> None:
        self._finish_reasoning_preview()
        self._reasoning_block_open = False
        self._close_chunk_run()
        if not self._ends_in_newline:
            sys.stdout.write("\n")
            self._ends_in_newline = True
        sys.stdout.flush()

    def repair_terminal_state(self) -> None:
        """Return the terminal to a sane state after an exception escaped
        mid-render. Best effort and never raises — this runs *because*
        something already failed, so it must not add a second failure.

        Without it, a crash mid-stream can leave an unterminated SGR run (the
        rest of the session comes out in the wrong colour), a half-drawn
        reasoning preview, and the prompt printed on the same line as
        half-finished output.
        """
        try:
            self._finish_reasoning_preview()
        except Exception:
            pass
        try:
            self._close_chunk_run()
        except Exception:
            pass
        try:
            sys.stdout.write(_RESET)
            if not self._ends_in_newline:
                sys.stdout.write("\n")
            sys.stdout.flush()
        except Exception:
            pass
        self._ends_in_newline = True
        self._preview_shown = False
