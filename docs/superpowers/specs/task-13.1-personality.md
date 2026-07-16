# Task 13.1: personality.py + Session integration + __main__.py wiring

## Authoritative contract
`docs/superpowers/specs/2026-07-16-personality-design.md` is the
complete design record for this task — read it first, especially §4
(interface) and §5 (mechanism — the exact `load_personality()` logic and
notice wording).

## What already exists
- `src/sasha/config.py` `_default_config_path()` — not imported or
  modified by this task, but `personality.py`'s own directory-resolution
  helper mirrors its exact logic (`XDG_CONFIG_HOME` env var, else
  `~/.config`) independently, matching how `prompt.py` (Task 9.1) is
  also a dependency-free leaf module.
- `src/sasha/session.py` `Session.__init__` and `_build_system_prompt`
  (the two things this task modifies — every other `Session` method
  `note_command`, `add_user`, `add_assistant`, `add_tool_result`,
  `_estimate_tokens`, `build_messages`, `system_prompt_dict` is
  unchanged).
- `src/sasha/__main__.py`'s `main()` — already constructs `session =
  Session(limits.history_tokens)` at a specific point; this task adds a
  `load_personality()` call before that construction, passes the result
  into `Session(...)`, and prints the notice (if any) via
  `sink.write_line(...)` — `sink` already exists earlier in `main()`.

## What's new
New file `src/sasha/personality.py`:
```python
import dataclasses
import os
import pathlib

_MAX_CHARS = 8000

DEFAULT_TEMPLATE = """<exact content from design §4 — copy verbatim>"""


@dataclasses.dataclass(frozen=True)
class PersonalityResult:
    text: str
    notice: str | None


def _personality_path() -> pathlib.Path:
    xdg = os.environ.get("XDG_CONFIG_HOME", "")
    if xdg:
        return pathlib.Path(xdg) / "sasha" / "PERSONALITY.md"
    return pathlib.Path.home() / ".config" / "sasha" / "PERSONALITY.md"


def load_personality(path: pathlib.Path | None = None) -> PersonalityResult:
    """Exact logic in design §5.1: self-install DEFAULT_TEMPLATE if the
    path doesn't exist (notice naming the path); on any OSError writing
    it, fall back to DEFAULT_TEMPLATE in memory with a distinct notice;
    on any OSError/UnicodeDecodeError reading an existing file, fall back
    to DEFAULT_TEMPLATE in memory with a distinct notice; truncate to
    exactly _MAX_CHARS characters with a notice if longer; otherwise
    return the file's exact content with notice=None."""
```

Changes to `src/sasha/session.py`:
```python
class Session:
    def __init__(self, history_token_budget: int = 12000,
                 personality: str = "") -> None:
        self.history_token_budget = history_token_budget
        self._personality = personality
        self.history: list[dict[str, Any]] = []
        self.notes: list[NoteCommand] = []
        self.last_stderr: str = ""
        self.system_prompt: str = self._build_system_prompt()

    def _build_system_prompt(self) -> str:
        functional = (
            f"You are Sasha, an interactive shell wrapper. "
            f"Proposed commands require user confirmation with 'y' or 'e'. "
            f"Free-text replies never execute anything. "
            f"The platform is {platform.platform()}. "
            f"The shell is bash."
        )
        if self._personality:
            return f"{self._personality}\n\n{functional}"
        return functional
```

Changes to `src/sasha/__main__.py` — add `from sasha import personality`
import, call `load_personality()` before `Session(...)` is constructed,
pass `personality=result.text` into that call, and print
`result.notice` via `sink.write_line(...)` if not `None` (any point
before `repl.loop()` is called — ordering relative to the existing
history-file setup is not load-bearing).

## Constraints
- New file `src/sasha/personality.py`. Modify `src/sasha/session.py`
  (`__init__` signature + `_build_system_prompt` body only) and
  `src/sasha/__main__.py` (the `load_personality()` call, the `Session(...)`
  call's new keyword argument, and the notice print only).
- Do not touch: `scripts/`, `docs/superpowers/`, `CLAUDE.md`, `AGENTS.md`,
  `PLAN.md`, `ISSUE-TRACKING.md`, `README.md`, `.gitignore`,
  `pyproject.toml`, `src/sasha/__init__.py`, `src/sasha/config.py`,
  `src/sasha/mutes.py`, `src/sasha/triggers.py`, `src/sasha/agent.py`,
  `src/sasha/llm.py`, `src/sasha/executor.py`, `src/sasha/repl.py`,
  `src/sasha/prompt.py`, `src/sasha/completion.py`, `tests/contract/`.
- `personality` defaults to `""` on `Session.__init__` — every existing
  contract test that constructs `Session(...)` without a `personality`
  argument (there are several, across multiple test files) must keep
  passing unmodified.
- `load_personality()` must never raise — every failure mode (absent
  path, write failure, read failure, decode failure) is caught and
  degrades to `DEFAULT_TEMPLATE` plus a `notice`.
- No new dependencies — stdlib only (`dataclasses`, `os`, `pathlib`).
- Greenfield-vs-surgical routing: creates one new module but also makes
  small, precise edits to two existing files' control flow. Dispatch to
  the default model (deepseek-v4-pro); do not use a
  `SASHA_DISPATCH_MODEL` local-model override.

## Acceptance criteria
(encoded in `tests/contract/test_task_13_1_personality.py`)
1. `load_personality(path)` with a non-existent `path`: writes
   `DEFAULT_TEMPLATE` to `path` (parent directories created as needed),
   returns `PersonalityResult(DEFAULT_TEMPLATE, notice)` with `notice`
   naming the path and indicating creation — `test_ac1_*`.
2. `load_personality(path)` with an existing, normal-sized file: returns
   `PersonalityResult(<file content exactly>, None)` — `test_ac2_*`.
3. `load_personality(path)` when the file can't be read (e.g. `chmod
   0o000`, or `path` is actually a directory) — or, for the absent-path
   case, when the parent directory can't be created/written to — falls
   back to `PersonalityResult(DEFAULT_TEMPLATE, notice)` with a `notice`
   distinct in wording from the "created" case, and does not raise —
   `test_ac3_*`.
4. `load_personality(path)` with a file over 8000 characters: returns
   `PersonalityResult(<first 8000 chars exactly>, notice)` with a notice
   naming the path and mentioning the size limit — `test_ac4_*`.
5. `Session()` (no `personality` argument) produces a `system_prompt`
   byte-identical to before this task — `test_ac5_*`. `Session(...,
   personality="Custom text")` produces `system_prompt ==
   "Custom text\n\n" + <the exact functional prompt string>` —
   `test_ac6_*`.
6. Every existing contract test that constructs `Session(...)` continues
   to pass unmodified (confirmed by the full suite, not a new test in
   this file).

## Standing instructions
- Your goal: make the full contract suite pass:
  `PYTHONPATH=src python3 -m pytest tests/contract/`.
- NEVER modify files under `tests/contract/` — the gate rejects the
  commit if you do. No existing contract test should need to change for
  this task (verified at spec time: `personality=""` default keeps every
  existing `Session()` construction's output identical).
- Deliver exactly ONE new commit on top of the HEAD you started from;
  never amend, rebase, or reset; leave the working tree clean.
- Commit message per AGENTS.md, with trailers:
  `Done: Task 13.1 — <one line>`, `Fixed: ISSUE-030`,
  `Next: Task 13.2 — Phase 13 review (Tier 1, with Salvador)`, and the
  Co-Authored-By signature line from your dispatch prompt.
