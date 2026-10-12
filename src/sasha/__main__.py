import argparse
import getpass
import os
import pathlib
import readline
import select
import sys
import tempfile
import traceback

from sasha import completion
from sasha import guard
from sasha import paste_join
from sasha import personality
from sasha import llm_debug
from sasha import prompt as prompt_mod
from sasha import skills
from sasha import tool_log
from sasha.config import ConfigError, load
from sasha.executor import Executor
from sasha.llm import ChatClient
from sasha.repl import Repl, TerminalConfirmer, TerminalQuestioner, TerminalSink
from sasha.session import Session

_PASTE_GRACE_S = 0.01

_REPAIR_BRIEF = (
    "You are working on Sasha's own source code — the program you are running "
    "inside. The current directory is the Sasha checkout.\n\n"
    "Rules for this mode:\n"
    "- Diagnose before editing: read the traceback, reproduce the fault, and "
    "find the smallest change that fixes the cause rather than the symptom.\n"
    "- The test suite is the oracle, not your own judgement. Run "
    "`bash scripts/run-tests.sh` after a change and do not call something "
    "fixed while it is red.\n"
    "- Never edit anything under tests/contract/ — those are the frozen "
    "acceptance criteria, and 'fixing' an assertion is not a fix. If one looks "
    "wrong, say so instead of changing it.\n"
    "- Prefer a git branch for edits: this checkout is what your own `sasha` "
    "command runs, so a bad edit changes the tool you are using to repair it.\n"
    "- Say what you changed and why, and what you verified."
)

_REPAIR_CRASH_MAX_CHARS = 20_000


def _sasha_checkout_root() -> pathlib.Path | None:
    """The source checkout this Sasha is running from, or None if it was
    installed as a copy (where there is nothing to repair in place)."""
    try:
        candidate = pathlib.Path(__file__).resolve().parents[2]
    except (IndexError, OSError):
        return None
    if (candidate / "pyproject.toml").is_file() and (
            candidate / "src" / "sasha").is_dir():
        return candidate
    return None


def _newest_crash_bundle(state_dir: pathlib.Path | None) -> pathlib.Path | None:
    root = guard.crash_dir(state_dir)
    if root is None or not root.is_dir():
        return None
    try:
        reports = sorted(root.glob("*.txt"))
    except OSError:
        return None
    return reports[-1] if reports else None


def _print_repair_banner(sink, repl, crash_path, checkout: pathlib.Path) -> None:
    """Say exactly what is about to be edited, with what model, and from
    which report — so 'repair mode' is never a surprise."""
    profile = repl._current_profile
    sink.write_line(
        f"[sasha] repair mode — checkout {checkout} "
        f"(profile: {profile.name}, model: {profile.model})")

    state = guard.git_state(checkout)
    if state.get("git_head"):
        dirty = state.get("git_dirty_files", "0")
        note = (f"[sasha] git: {state['git_head']}"
                + (f" — {dirty} uncommitted change(s)" if dirty != "0" else ""))
        sink.write_line(note)

    if crash_path is not None:
        sink.write_line(f"[sasha] crash report: {crash_path}")
    else:
        sink.write_line("[sasha] no crash report found — starting a general "
                        "maintenance session")

    sink.write_line(
        "[sasha] edits here change the `sasha` you are running. The test "
        "suite is the check: 'bash scripts/run-tests.sh'. Consider a branch.")


def _has_pending_stdin():
    if not sys.stdin.isatty():
        return False
    r, _, _ = select.select([sys.stdin], [], [], _PASTE_GRACE_S)
    return bool(r)


def _joined_input(prompt):
    return paste_join.read_joined_input(prompt, input, _has_pending_stdin)


def _state_dir() -> pathlib.Path:
    xdg = os.environ.get("XDG_STATE_HOME", "")
    if xdg:
        return pathlib.Path(xdg) / "sasha"
    return pathlib.Path.home() / ".local" / "state" / "sasha"


def _resolve_reasoning_mode(cli_value: str | None, config) -> str:
    """The --reasoning flag wins over config.toml; config wins over the
    built-in default (already applied by the Config dataclass)."""
    return cli_value or config.reasoning_mode


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]

    parser = argparse.ArgumentParser(prog="sasha")
    parser.add_argument("--version", action="store_true")
    parser.add_argument("--config", type=pathlib.Path, default=None)
    parser.add_argument("--profile", type=str, default=None)
    parser.add_argument("--reasoning", choices=("compact", "full"), default=None,
                        help="how much of the model's reasoning to show: "
                             "'compact' (default) draws one line that advances "
                             "as each reasoning paragraph's first line "
                             "completes; 'full' streams every chunk. Overrides "
                             "reasoning_mode in config.toml.")
    parser.add_argument("command", nargs="?", choices=("repair",), default=None,
                        help="repair: start a session for fixing Sasha itself")
    parser.add_argument("--bundle", type=pathlib.Path, default=None,
                        help="with 'repair': the crash report to work from "
                             "(default: the newest one)")
    parser.add_argument("--repair-profile", type=str, default=None,
                        help="with 'repair': which profile to use, overriding "
                             "repair_profile in config.toml")
    args = parser.parse_args(argv)

    if args.version:
        print("sasha 0.5.0")
        return 0

    # Setup failures have no session state to preserve, so this net is about
    # reporting rather than recovery: one clean line plus a crash report,
    # instead of a raw traceback out of a half-built REPL.
    try:
        return _run(args, repair_mode=(args.command == "repair"))
    except Exception as exc:
        return _report_setup_failure(exc)


def _report_setup_failure(exc: Exception) -> int:
    print(f"sasha: failed to start: {guard.describe(exc)}", file=sys.stderr)
    bundle = guard.write_crash_bundle(
        _state_dir(), exc,
        context={"cwd": os.getcwd(), "argv": " ".join(sys.argv)})
    if bundle is not None:
        print(f"sasha: full traceback: {bundle}", file=sys.stderr)
    else:
        traceback.print_exc()
    return 1


def _run(args, repair_mode: bool = False) -> int:
    try:
        config = load(args.config)
    except ConfigError as e:
        print(f"sasha: config error: {e}", file=sys.stderr)
        return 2

    tool_log.configure(config.tool_log_max_size_mb)
    llm_debug.configure(config.llm_debug_enabled)

    profile_name = args.profile or config.default_profile
    if profile_name not in config.profiles:
        print(f"sasha: config error: unknown profile: {profile_name}",
              file=sys.stderr)
        return 2

    limits = config.limits

    # `sasha repair` works on Sasha's own source, so it must run *in* the
    # checkout before anything derives paths from the cwd.
    repair_crash = ""
    repair_crash_path = None
    if repair_mode:
        checkout = _sasha_checkout_root()
        if checkout is None:
            print("sasha: repair needs a source checkout — this Sasha was "
                  "installed as a copy, so there is nothing to edit in place.",
                  file=sys.stderr)
            return 2
        os.chdir(checkout)

    state_dir = _state_dir()
    state_notice = None
    try:
        state_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        # An unwritable $HOME (read-only home, full disk, container) must not
        # stop Sasha from starting: running commands needs no state at all.
        # Fall back to a private temp dir so history/reports still work
        # for this session, and say so rather than dying with a raw traceback.
        original = state_dir
        try:
            state_dir = pathlib.Path(tempfile.mkdtemp(prefix="sasha-state-"))
        except OSError:
            # Nothing writable anywhere. Keep the unusable path: reads
            # already degrade to defaults, and a failed write is caught by
            # the REPL's crash net.
            state_dir = original
        reason = exc.strerror or str(exc)
        state_notice = (f"[sasha] can't use {original} ({reason}) — "
                        f"running without saved state this session")

    executor = Executor(
        os.getcwd(),
        dict(os.environ),
        stderr_capture_bytes=limits.stderr_capture_bytes,
        sensitive_path_patterns=config.sensitive_paths,
    )

    if repair_mode:
        requested = args.bundle or _newest_crash_bundle(state_dir)
        if requested is not None:
            try:
                repair_crash = pathlib.Path(requested).read_text()
                repair_crash_path = pathlib.Path(requested)
            except OSError as exc:
                repair_crash = f"(could not read {requested}: {exc})"
                repair_crash_path = pathlib.Path(requested)
        if len(repair_crash) > _REPAIR_CRASH_MAX_CHARS:
            repair_crash = (repair_crash[:_REPAIR_CRASH_MAX_CHARS]
                            + "\n... [report truncated]\n")

    personality_result = personality.load_personality()
    skills_listing = skills.build_skills_listing()

    session = Session(limits.history_tokens,
                      personality=personality_result.text,
                      skills_listing=skills_listing)

    if repair_mode:
        session.set_repair_context(_REPAIR_BRIEF, repair_crash)

    client_factory = lambda p: ChatClient(p)

    sink = TerminalSink(sasha_color=prompt_mod.COLOR_TOKENS[config.sasha_color],
                        reasoning_color=prompt_mod.COLOR_TOKENS[config.reasoning_color],
                        reasoning_mode=_resolve_reasoning_mode(args.reasoning,
                                                              config))

    if personality_result.notice:
        sink.write_line(personality_result.notice)

    if config.notice:
        sink.write_line(config.notice)

    if state_notice:
        sink.write_line(state_notice)

    confirmer = TerminalConfirmer(_joined_input, sink)
    questioner = TerminalQuestioner(_joined_input, sink)

    _isatty = sys.stdin.isatty()

    def _repl_input(prompt):
        line = _joined_input(prompt)
        if line.strip() and not _isatty:
            readline.add_history(line)
        return line

    repl = Repl(
        config=config,
        executor=executor,
        session=session,
        client_factory=client_factory,
        confirmer=confirmer,
        input_fn=_repl_input,
        sink=sink,
        getpass_fn=getpass.getpass,
        questioner=questioner,
        state_dir=state_dir,
    )

    if profile_name != config.default_profile:
        repl._current_profile = config.profiles[profile_name]

    if repair_mode:
        # Repair is the one job worth a frontier model even when daily driving
        # is local. Precedence: --repair-profile > config repair_profile >
        # whatever is active. _activate_profile handles the API-key prompt and
        # leaves the profile alone if the user cancels.
        chosen = args.repair_profile or config.repair_profile
        if chosen:
            repl._activate_profile(chosen)
            if repl._current_profile.name != chosen:
                sink.write_line(
                    f"[sasha] repair is falling back to "
                    f"{repl._current_profile.name} — a frontier model is "
                    f"strongly recommended for this, since the test suite is "
                    f"the only real check on the change.")
        _print_repair_banner(sink, repl, repair_crash_path,
                             checkout=pathlib.Path(os.getcwd()))

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
                    words, cword, list(config.profiles))
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
    try:
        readline.write_history_file(str(history_path))
    except OSError:
        pass

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
