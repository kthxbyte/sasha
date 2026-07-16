# Sasha — Personality File Design (ISSUE-030)

**Status:** Draft for review
**Date:** 2026-07-16

## 1. Mission

Let Salvador (and any user) customize Sasha's tone and character by
editing a plain-text file, `~/.config/sasha/PERSONALITY.md`, whose
content is prepended to the LLM's system prompt on every request. If the
file doesn't exist yet, Sasha writes a neutral, self-documenting default
in its place and tells the user once — so the feature is discoverable
without requiring any manual setup step.

## 2. Scope

### In scope:
- New `src/sasha/personality.py`: `PersonalityResult` (a frozen
  dataclass: `text`, `notice`) and `load_personality(path=None)`.
- Self-installing a neutral, instructional `DEFAULT_TEMPLATE` when the
  file is absent (writes it to disk, uses it for the current session
  too).
- Graceful fallback (never crashes Sasha's startup) on any read error —
  falls back to `DEFAULT_TEMPLATE` in memory, surfaces a one-time
  `[sasha]` notice.
- An 8000-character cap with truncation + notice, matching the token-
  estimation convention already used in `session.py`
  (`math.ceil(len(content) / 4)`).
- `Session.__init__` gains a `personality: str = ""` parameter (default
  empty — exact backward compatibility with every existing `Session()`
  construction in the current test suite).
- `Session._build_system_prompt()` prepends non-empty personality text,
  separated by a blank line, before the existing functional prompt
  (identity, confirmation-gate rules, platform/shell info) — personality
  never replaces or reorders the functional instructions, only precedes
  them.
- `__main__.py` wiring: `load_personality()` called once in `main()`,
  its `text` passed into `Session(...)`, its `notice` (if any) printed
  via `sink.write_line(...)` before `repl.loop()`.

### Out of scope / non-goals:
- Any config.toml key for personality (e.g. a custom file path,
  an enable/disable toggle). The file's mere presence/absence at the
  fixed, XDG-respecting path is the only control surface — matches the
  original scratch note's "no TOML/dataclass machinery needed."
- A built-in example character as the shipped default — the self-
  installed template is neutral/instructional, not a specific voice
  (Salvador's call; his own customized `PERSONALITY.md` is unaffected
  either way since self-install only fires when the file is *absent*).
- Any change to the confirmation gate, `TerminalSink`, `TerminalConfirmer`,
  or `prompt.py`'s color-token system — this feature touches
  `session.py`, a new `personality.py`, and `__main__.py`'s wiring only.
- Re-reading or hot-reloading `PERSONALITY.md` mid-session if the user
  edits it while Sasha is running — loaded once at startup, same as
  `config.toml`.
- Any size unit smarter than a flat character cap (e.g. an actual
  tokenizer) — matches the project's existing `len(content) / 4`
  estimation convention, not a new dependency.

## 3. Key contracts & assumptions

1. **`personality=""` is byte-identical to today.** `Session`'s default
   parameter value and `_build_system_prompt`'s `if self._personality:`
   guard together guarantee zero output change when personality is
   empty — this is what keeps every existing `Session()`-constructing
   test passing unamended.
2. **`load_personality()` never raises.** Absent file, unreadable file,
   undecodable bytes, a failed self-install write (e.g. read-only
   `~/.config`) — every failure mode degrades to `DEFAULT_TEMPLATE` in
   memory plus a `notice` describing what happened, never an exception
   that could crash `main()`.
3. **`notice` is the single source of truth for "what should the user be
   told."** Exactly one of: `None` (ordinary read, nothing to report),
   or a ready-to-print `[sasha] ...` string — `main()` doesn't inspect
   separate booleans and assemble its own message text.
4. **Personality precedes, never replaces, the functional system
   prompt.** A user's `PERSONALITY.md` cannot remove or reorder the
   confirmation-gate/platform instructions — those are appended
   unconditionally after whatever personality text is present (including
   empty).
5. **Self-install is a startup-time side effect, not a hidden one.**
   Creating the file always comes with a `notice` — Sasha never silently
   writes to the user's filesystem without saying so on the same run.

## 4. Interface

New file `src/sasha/personality.py`:

```python
import dataclasses
import os
import pathlib

_MAX_CHARS = 8000

DEFAULT_TEMPLATE = """\
# Sasha — Personality File

This file lets you customize Sasha's tone and character. Sasha includes
this file's content directly in the system prompt sent to the LLM on
every request — there's no required structure, write whatever guides the
voice and behavior you want.

A few things people include:
- **Identity** — who Sasha is, its role, its values
- **Tone & voice** — formal or casual, humor style, verbosity
- **Behavior notes** — how to react to errors, uncertainty, or edge cases

Sasha's core functional behavior (confirmation gates before running
commands, platform/shell context) is always included after this file's
content — personality shapes tone, not safety behavior.

Edit this file freely. Changes take effect the next time you start Sasha.
"""


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
    """See §5 for the exact self-install / fallback / truncation logic."""
    ...
```

Change to `src/sasha/session.py`:
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
Only `__init__`'s signature and `_build_system_prompt`'s body change;
every other `Session` method is untouched.

Change to `src/sasha/__main__.py` — in `main()`, before `Session(...)` is
constructed:
```python
from sasha import personality
...
personality_result = personality.load_personality()
...
session = Session(limits.history_tokens, personality=personality_result.text)
...
if personality_result.notice:
    sink.write_line(personality_result.notice)
```

## 5. Mechanism

### 5.1 `load_personality()` exact logic

```
path = the given path, or _personality_path()
if path does not exist:
    try: create parent dirs, write DEFAULT_TEMPLATE to path
         return PersonalityResult(DEFAULT_TEMPLATE,
             f"[sasha] created {path} — edit it to customize my tone/character")
    except OSError:
         return PersonalityResult(DEFAULT_TEMPLATE,
             f"[sasha] couldn't create {path} — using the default personality for this session")
try: text = path.read_text()
except (OSError, UnicodeDecodeError):
    return PersonalityResult(DEFAULT_TEMPLATE,
        f"[sasha] couldn't read {path} — using the default personality for this session")
if len(text) > _MAX_CHARS:
    return PersonalityResult(text[:_MAX_CHARS],
        f"[sasha] {path} is over {_MAX_CHARS} characters — truncated for this session")
return PersonalityResult(text, None)
```
No re-read after a successful self-install write — the in-memory
`DEFAULT_TEMPLATE` is already exactly what was written, avoiding a
redundant round-trip.

### 5.2 Notice delivery

`main()` prints `personality_result.notice` via `sink.write_line(...)`
once, at startup, independent of `Repl.loop()`'s own per-launch profile/
tip banner (Task 10.1) — personality events are conditional and one-time
(only when something notable happened this run), unlike the profile/tip
lines which print unconditionally every launch. Ordering between the two
banners is not load-bearing; either may print first.

### 5.3 Why `Session` gains a parameter instead of doing its own I/O

`Session` currently has zero file I/O — every existing test constructs
it with plain in-memory arguments. Keeping `personality` as a plain
`str` constructor parameter (computed once by `load_personality()` in
`main()`, the same dependency-injection shape already used for
`Executor`, `MuteStore`, and `Config`) means `Session` stays exactly as
easy to unit-test as it is today — no filesystem mocking needed to test
`_build_system_prompt`'s personality-prepending logic.

## 6. Decisions log

- **Decision:** Self-install `DEFAULT_TEMPLATE` when
  `PERSONALITY.md` is absent, rather than leaving today's behavior
  unchanged. **Rationale:** Salvador's call — a personality file is
  low-stakes (no secrets, no functional config) compared to
  `config.toml`, so proactively creating it (with a clear one-time
  notice) is reasonable, unlike ISSUE-026's finding that `config.toml`
  is deliberately never auto-written.
- **Decision:** The self-installed default is a neutral,
  instructional template, not a built-in character. **Rationale:**
  Salvador's call — doesn't change Sasha's default voice for everyone,
  just documents and invites the customization.
- **Decision:** Personality text precedes the functional system prompt,
  unconditionally followed by it. **Rationale:** Salvador's call — a
  personality file should shape tone, never be able to silently remove
  safety-relevant functional instructions by omission.
- **Decision:** Any read/write error degrades gracefully to the in-
  memory default plus a notice, rather than crashing startup like a bad
  `config.toml` does. **Rationale:** Salvador's call — personality is
  cosmetic; `config.toml` errors are load-bearing (wrong API endpoint,
  broken profile) in a way a personality file's errors never are.
- **Decision:** 8000-character cap with truncation + notice.
  **Rationale:** Salvador's call — bounds accidental token/context waste
  from an oversized file; the character-based estimate matches the
  project's existing convention in `session.py` rather than introducing
  a real tokenizer dependency.
- **Decision:** `Session` receives `personality` as a plain constructor
  parameter; `personality.py`'s file I/O happens once in `main()`.
  **Rationale:** Keeps `Session` dependency-injected and file-I/O-free,
  matching its current design and every existing test's construction
  pattern.

## 7. Testing strategy

`personality.py`'s contract tests exercise `load_personality(path=...)`
against `tmp_path`-based fake paths (never the real
`~/.config/sasha/`): absent file → self-install (file written with
exactly `DEFAULT_TEMPLATE` content, `notice` set and naming the path),
existing normal file → `notice is None` and `text` matches file content
exactly, unreadable file (e.g. `chmod 000`, or a directory where a file
is expected) → falls back to `DEFAULT_TEMPLATE` with a `notice`, a file
over `8000` characters → `text` is truncated to exactly `8000` characters
with a `notice`, and a self-install write failure (e.g. an unwritable
parent directory) → falls back to `DEFAULT_TEMPLATE` with its own
distinct `notice` wording.

`session.py`'s contract tests cover: `Session()` (no `personality` arg)
produces byte-identical `system_prompt` to today (regression guard —
existing tests are the primary proof of this, but a dedicated test pins
it explicitly); `Session(personality="X")` produces
`"X\n\n" + <functional prompt>` exactly.
