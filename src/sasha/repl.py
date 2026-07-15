import os
import shlex
import sys

from sasha.agent import run_consult
from sasha.llm import ChatError
from sasha.triggers import Decision, command_word, decide


class Repl:
    def __init__(self, config, executor, mutes, session, client_factory,
                 confirmer, input_fn, out):
        self._config = config
        self._executor = executor
        self._mutes = mutes
        self._session = session
        self._client_factory = client_factory
        self._confirmer = confirmer
        self._input_fn = input_fn
        self._out = out
        self._current_profile = config.profiles[config.default_profile]
        self._pending_offer = None  # (line, exit_code) or None

    def _consult(self, auto_prompt=None):
        if auto_prompt:
            self._session.add_user(auto_prompt)
        try:
            client = self._client_factory(self._current_profile)
            def stream_out(text):
                sys.stdout.write(text)
                sys.stdout.flush()
                if self._out is not print:
                    self._out(text)
            run_consult(self._session, client, self._executor,
                        self._confirmer, stream_out,
                        max_tool_turns=self._config.limits.max_tool_turns)
        except ChatError as e:
            self._out(f"[sasha] {self._current_profile.name} unreachable: "
                      f"{e.reason} — 'sasha profile <name>' to switch")

    def _handle_pending_offer_ignore(self):
        if self._pending_offer is not None:
            off_line, off_exit = self._pending_offer
            cmd = command_word(off_line)
            crossed = self._mutes.record_ignored(cmd, off_exit)
            if crossed:
                self._out(f"[sasha] muting offers for {cmd} "
                          f"exit {off_exit} — "
                          f"'sasha unmute {cmd}' to restore")
            self._pending_offer = None

    def _handle_builtin(self, stripped):
        rest = stripped[6:]
        try:
            tokens = shlex.split(rest)
        except ValueError:
            self._out(f"[sasha] unknown command: {rest}")
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
                        self._out(f"[sasha] unknown command: {rest}")
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
                        self._out(f"[sasha] unknown command: {rest}")
                else:
                    self._mutes.unmute(cmd, None)
        elif verb == "profile":
            if len(tokens) == 1:
                p = self._current_profile
                self._out(f"[sasha] profile: {p.name} "
                          f"({p.model} @ {p.base_url})")
            elif len(tokens) >= 2:
                name = tokens[1]
                if name in self._config.profiles:
                    self._current_profile = self._config.profiles[name]
                    p = self._current_profile
                    self._out(f"[sasha] profile: {p.name} "
                              f"({p.model} @ {p.base_url})")
                else:
                    self._out(f"[sasha] unknown profile: {name}")
        elif verb == "help":
            self._out("[sasha] sasha mute <cmd> [<N>], "
                      "sasha unmute <cmd>, "
                      "sasha profile [<name>], "
                      "?? <question>")
        else:
            self._out(f"[sasha] unknown command: {rest}")

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
            self._out("[sasha] Ctrl+Z not supported — command terminated")
            return True

        decision = decide(stripped, result, self._mutes)
        if decision == Decision.AUTO_CONSULT:
            prompt = (f"The line `{stripped}` failed with exit 127 "
                      f"(command not found, or not a command at all). "
                      f"If it was meant as a question or calculation, "
                      f"answer it; if it looks like a typo, say so "
                      f"and give the corrected command.")
            self._consult(prompt)
        elif decision == Decision.OFFER:
            self._out(f"[sasha] failed (exit {result.exit_code}) "
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
    def __init__(self, input_fn, out):
        self._input_fn = input_fn
        self._out = out

    def ask(self, command, description):
        desc = description if description else "(no description provided)"
        self._out(f"[sasha] wants to run:  {command}\n"
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
