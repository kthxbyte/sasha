import argparse
import os
import pathlib
import readline
import sys

from sasha.config import ConfigError, load
from sasha.executor import Executor
from sasha.llm import ChatClient
from sasha.mutes import MuteStore
from sasha.repl import Repl, TerminalConfirmer
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

    session = Session(limits.history_tokens)

    client_factory = lambda p: ChatClient(p)

    confirmer = TerminalConfirmer(input, print)

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
        out=print,
    )

    if profile_name != config.default_profile:
        repl._current_profile = config.profiles[profile_name]

    history_path = state_dir / "history"
    try:
        if history_path.exists():
            readline.read_history_file(str(history_path))
    except Exception:
        pass

    repl.loop()

    readline.set_history_length(1000)
    readline.write_history_file(str(history_path))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
