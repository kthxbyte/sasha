import argparse
import os
import pathlib
import readline
import sys

from sasha import completion
from sasha import personality
from sasha.config import ConfigError, load
from sasha.executor import Executor
from sasha.llm import ChatClient
from sasha.mutes import MuteStore
from sasha.repl import Repl, TerminalConfirmer, TerminalSink
from sasha.session import Session


def _state_dir() -> pathlib.Path:
    xdg = os.environ.get("XDG_STATE_HOME", "")
    if xdg:
        return pathlib.Path(xdg) / "sasha"
    return pathlib.Path.home() / ".local" / "state" / "sasha"


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]

    parser = argparse.ArgumentParser(prog="sasha")
    parser.add_argument("--version", action="store_true")
    parser.add_argument("--config", type=pathlib.Path, default=None)
    parser.add_argument("--profile", type=str, default=None)
    args = parser.parse_args(argv)

    if args.version:
        print("sasha 0.1.0")
        return 0

    try:
        config = load(args.config)
    except ConfigError as e:
        print(f"sasha: config error: {e}", file=sys.stderr)
        return 2

    profile_name = args.profile or config.default_profile
    if profile_name not in config.profiles:
        print(f"sasha: config error: unknown profile: {profile_name}",
              file=sys.stderr)
        return 2

    limits = config.limits

    state_dir = _state_dir()
    state_dir.mkdir(parents=True, exist_ok=True)

    executor = Executor(
        os.getcwd(),
        dict(os.environ),
        stderr_capture_bytes=limits.stderr_capture_bytes,
    )

    mutes = MuteStore(state_dir / "mutes.json", threshold=limits.mute_threshold)

    personality_result = personality.load_personality()

    session = Session(limits.history_tokens,
                      personality=personality_result.text)

    client_factory = lambda p: ChatClient(p)

    sink = TerminalSink()

    if personality_result.notice:
        sink.write_line(personality_result.notice)

    confirmer = TerminalConfirmer(input, sink)

    _isatty = sys.stdin.isatty()

    def _repl_input(prompt):
        line = input(prompt)
        if line.strip() and not _isatty:
            readline.add_history(line)
        return line

    repl = Repl(
        config=config,
        executor=executor,
        mutes=mutes,
        session=session,
        client_factory=client_factory,
        confirmer=confirmer,
        input_fn=_repl_input,
        sink=sink,
    )

    if profile_name != config.default_profile:
        repl._current_profile = config.profiles[profile_name]

    history_path = state_dir / "history"
    try:
        if history_path.exists():
            readline.read_history_file(str(history_path))
    except Exception:
        pass

    _last_line = None
    _last_matches = []
    _bash_ok = completion.bash_completion_available()

    def _completer(text, state):
        nonlocal _last_line, _last_matches
        line = readline.get_line_buffer()
        if line != _last_line:
            _last_line = line
            words, cword = completion.words_and_cword(
                line, readline.get_begidx(), text)
            if cword == 0:
                _last_matches = completion.complete_first_word(
                    text, os.environ.get("PATH", "").split(os.pathsep))
            else:
                builtin = completion.complete_builtin_arg(
                    words, cword, list(config.profiles), mutes.muted_commands())
                if builtin is not None:
                    _last_matches = builtin
                elif _bash_ok:
                    _last_matches = completion.complete_via_bash(
                        words, cword, cwd=executor.cwd)
                else:
                    _last_matches = completion.complete_filenames(
                        text, cwd=executor.cwd)
        return _last_matches[state] if state < len(_last_matches) else None

    readline.set_completer_delims(" \t\n")
    readline.set_completer(_completer)
    readline.parse_and_bind("tab: complete")

    repl.loop()

    readline.set_history_length(1000)
    readline.write_history_file(str(history_path))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
