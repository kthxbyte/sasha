import dataclasses
import getpass
import os
import pathlib
import re
import shlex
import socket
import sys
import threading
import uuid
from typing import Protocol

import sasha.config as config
from sasha.activity_indicator import ActivityIndicator
from sasha.agent import run_consult
from sasha.llm import ChatError
from sasha import directory_recap
from sasha import memory
from sasha import safe_commands
from sasha import tool_log
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
        self._activity = ActivityIndicator(
            colors={"thinking": config.activity_indicator_thinking_color,
                    "tool": config.activity_indicator_tool_color},
            interval_s=config.activity_indicator_interval_s,
            enabled=config.activity_indicator_enabled and sink.color_enabled,
        )
        self._pending_offer = None  # (line, exit_code) or None
        self._current_repo_root = None   # pathlib.Path | None
        self._last_checked_cwd = None    # str | None
        self._memory_dir_existed = False
        self._bootstrap_attempted = set()      # set[pathlib.Path]
        self._last_bootstrap_thread = None     # threading.Thread | None, test seam
        self._recapped_dirs: set[str] = set()
        self._last_recap_thread = None
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
                        on_phase=self._on_activity_phase)
        except ChatError as e:
            self._sink.write_line(
                f"[sasha] {self._current_profile.name} unreachable: "
                f"{e.reason} — 'sasha profile <name>' to switch")
        finally:
            self._activity.stop()
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
        elif verb == "compact-log":
            self._handle_compact_log_builtin()
        elif verb == "help":
            self._sink.write_line("[sasha] sasha mute <cmd> [<N>], "
                      "sasha unmute <cmd>, "
                      "sasha profile [<name>] (asks for a key if needed), "
                      "sasha memory, sasha forget, "
                      "sasha compact-log, "
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
            return True
        return False

    def _check_directory_recap(self) -> None:
        if not self._config.directory_recap_enabled:
            return
        cwd = self._executor.cwd
        if cwd in self._recapped_dirs:
            return
        self._recapped_dirs.add(cwd)
        verdict, text = directory_recap.decide(pathlib.Path(cwd))
        if verdict == directory_recap.SILENT:
            return
        if verdict == directory_recap.RECAP_DIRECT:
            self._sink.write_line(text)
            return
        on_result = getattr(self._sink, "write_async_line", self._sink.write_line)
        self._last_recap_thread = directory_recap.escalate_async(
            pathlib.Path(cwd), self._client_factory, self._current_profile,
            on_result)

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
            write_async_line = getattr(self._sink, "write_async_line",
                                       self._sink.write_line)
            write_async_line(self._bootstrap_finish_message(len(lines), docs))
            self._clear_bootstrap_pending(repo_root)

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
            self._check_directory_recap()
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
        if not self._check_place_shift():
            self._check_directory_recap()
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


class TerminalSink:
    def __init__(self, sasha_color: str = _SASHA_COLOR,
                 reasoning_color: str = _REASONING_COLOR) -> None:
        self.color_enabled = (sys.stdout.isatty()
                              and "NO_COLOR" not in os.environ)
        self._sasha_color = sasha_color
        self._reasoning_color = reasoning_color
        self._ends_in_newline = True
        self._chunk_run_kind = None   # None | "llm" | "reasoning"

    def _wrap(self, text: str, color: str) -> str:
        if not self.color_enabled:
            return text
        return f"{color}{text}{_RESET}"

    def _close_chunk_run(self) -> None:
        if self._chunk_run_kind is not None:
            sys.stdout.write(_RESET)
            self._chunk_run_kind = None

    def write_line(self, text: str, color: str | None = None) -> None:
        self._close_chunk_run()
        if not self._ends_in_newline:
            sys.stdout.write("\n")
        print(self._wrap(text, color if color is not None else self._sasha_color))
        self._ends_in_newline = True

    def write_async_line(self, text: str, color: str | None = None) -> None:
        """Like write_line(), but for messages that can arrive from a
        background thread at an unpredictable time relative to the
        interactive prompt — which is printed via input() directly,
        bypassing this sink, so _ends_in_newline can't be trusted to know
        whether the terminal is actually at a fresh line. Always writes a
        leading newline unconditionally rather than checking that state."""
        self._close_chunk_run()
        sys.stdout.write("\n")
        print(self._wrap(text, color if color is not None else self._sasha_color))
        self._ends_in_newline = True

    def _write_colored_chunk(self, text: str, kind: str, color: str) -> None:
        if not text:
            return
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
        self._write_colored_chunk(text, "llm", _LLM_COLOR)

    def write_reasoning_chunk(self, text: str) -> None:
        self._write_colored_chunk(text, "reasoning", self._reasoning_color)

    def ensure_newline(self) -> None:
        self._close_chunk_run()
        if not self._ends_in_newline:
            sys.stdout.write("\n")
            self._ends_in_newline = True
        sys.stdout.flush()
