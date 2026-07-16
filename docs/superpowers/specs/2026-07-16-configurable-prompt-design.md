# Sasha — Configurable Prompt Design (ISSUE-022, Tier 1)

**Status:** Draft for review
**Date:** 2026-07-16

## 1. Mission

Let the shell prompt's format be configured via `config.toml`, using a
small bash-`$PS1`-inspired escape/token language, instead of the format
being hardcoded in `Repl.loop()`. This is Tier 1 of ISSUE-022 — the
"small, safe subset" the issue's own findings recommended, explicitly
excluding the parts of real `$PS1` that would require shelling out
(command substitution, parameter expansion, OSC title-setting).

## 2. Scope

### In scope:
- A new `src/sasha/prompt.py` module: `render()` (pure function) and
  `validate_template()` (raises `prompt.PromptError`, a new exception
  local to this module — `prompt.py` has no dependency on `config.py` at
  all, avoiding a circular import; `config.load()` catches `PromptError`
  and re-raises as `ConfigError`, same pattern already used for
  `tomllib.TOMLDecodeError`).
- Positional escapes: `\u` (user), `\h` (host), `\w` (full cwd,
  `~`-abbreviated), `\W` (cwd basename).
- Named color tokens inside `\[ \]`: the 8 standard ANSI colors, each
  also as `bold-<color>` and `dim-<color>` (24 total), plus `reset` — 25
  tokens total, each auto-wrapped in the existing `\001`/`\002`
  readline-ignore markers.
- A `prompt` key in `Config` / `config.toml`, validated at `config.load()`
  time (fail-fast `ConfigError` on any unrecognized escape or token,
  naming the offending token).
- `Repl.loop()` calling `prompt.render(self._config.prompt, ...)` instead
  of its own hardcoded f-string logic. Exactly one rendering code path:
  `prompt.DEFAULT_TEMPLATE` (a template string reproducing today's exact
  output) is what `Config.prompt` defaults to when unset — there is no
  separate hardcoded fallback branch in `repl.py`.
- Suppressing color tokens (rendering as empty string) when
  `color_enabled` is `False`, matching the rest of the app's `NO_COLOR`/
  non-tty behavior. Positional escapes (`\u` etc.) are unaffected by
  `color_enabled` — only color tokens are conditional.

### Out of scope / non-goals:
- `$PS1` environment variable lookup. (Considered and explicitly dropped —
  `PS1` is a shell variable, essentially never exported to child
  processes, so this layer would almost always be a no-op. Two layers
  only: `config.toml` → hardcoded default.)
- `\$` (bash's root-vs-non-root prompt character) — Sasha has no
  root/non-root execution model in the same sense bash does.
- Command substitution (`$(...)`), parameter expansion (`${...}`), OSC
  title-setting (`\e]0;...\a`), or any other real-bash-PS1 escape beyond
  the four listed above. Full fidelity would require actually invoking an
  interactive bash process to decode the string (bash's own `\u`/`\h`/`\w`
  are decoded by readline's prompt-printing code, not generic shell
  expansion) and turns a config value into an arbitrary-code-execution
  surface — a much bigger decision than this task, logged separately if
  ever wanted.
- An escape-the-escape mechanism for a literal backslash or literal
  `\[`/`\]` in the rendered text. Bare `[`/`]` (no leading backslash) are
  never special and pass through as literal text already; wanting a
  *literal* `\[` in your prompt is unsupported in v1.
- Reusing the color-token table for any surface other than the prompt
  (confirmation gate, thinking indicator, `TerminalSink`'s own colors) —
  logged as ISSUE-025, not this task. The token table is built as a
  clean, exported dict specifically so that reuse is easy *later*,
  without being wired up now.
- First-run config/API-key setup UX — logged as ISSUE-026, unrelated to
  prompt formatting.
- Any change to `TerminalSink`, `TerminalConfirmer`, or the confirmation
  gate's colors (Task 7.1) — this task touches `config.py` (new field)
  and `repl.py` (`Repl.loop()` only) plus the new `prompt.py` module.

## 3. Key contracts & assumptions

1. **One rendering code path.** `Config.prompt` is always a template
   string (never `None`/optional) — `config.load()` fills in
   `prompt.DEFAULT_TEMPLATE` when the user hasn't set one. `Repl.loop()`
   always calls `prompt.render(self._config.prompt, ...)`; there is no
   `if config.prompt: ... else: <hardcoded string>` branch anywhere.
2. **Validation happens once, at load time, not at render time.**
   `config.load()` calls `prompt.validate_template()` immediately after
   reading `[prompt]` from TOML (or resolving the default). A malformed
   template is a startup-time `ConfigError`, identical in spirit to how a
   bad `default_profile` already fails — never a silently-wrong prompt
   discovered mid-session.
3. **`render()` is pure and I/O-free.** Takes `user`, `host`, `cwd`,
   `home`, `color_enabled` as plain arguments (no `getpass`/`socket`/`os`
   calls inside `prompt.py` itself) — `Repl.loop()` still owns collecting
   those values, exactly as it does today. Keeps `prompt.py` trivially
   unit-testable with fixed inputs.
4. **The color-token table is public, exported data — not implementation
   detail.** `COLOR_TOKENS: dict[str, str]` (token name → ANSI SGR code)
   is a module-level constant, not inlined into a parsing function. This
   task does not consume that reusability (see ISSUE-025) but the shape
   is deliberate.
5. **`prompt.DEFAULT_TEMPLATE` reproduces today's exact rendered bytes.**
   For both the colored and plain cases, `prompt.render(DEFAULT_TEMPLATE,
   ...)` must byte-match what `Repl.loop()` currently hardcodes — this is
   what lets Task 6.2's existing prompt contract tests
   (`test_task_6_2_colorization_prompt.py::test_ac4_*`) keep passing
   unamended.

## 4. Token grammar

```
template     := (literal | positional | color-token)*
literal      := any character not starting a recognized escape
positional   := "\u" | "\h" | "\w" | "\W"
color-token  := "\[" TOKEN-NAME "\]"
TOKEN-NAME   := "reset"
              | COLOR
              | "bold-" COLOR
              | "dim-" COLOR
COLOR        := "black" | "red" | "green" | "yellow"
              | "blue" | "magenta" | "cyan" | "white"
```

Any `\` not immediately followed by `u`, `h`, `w`, `W`, `[`, or the start
of a recognized token inside `\[...\]` is a validation error — not a
literal passthrough. (Contrast with real bash, which silently ignores
unrecognized backslash sequences; this project's existing config-error
convention is fail-fast, and a silently-wrong prompt is worse than a
startup error naming the typo.)

### `COLOR_TOKENS` (the 8 SGR base codes; `bold-`/`dim-` are prefix
modifiers, `reset` is separate):

| Color | Code | Color | Code |
|---|---|---|---|
| black | `30` | blue | `34` |
| red | `31` | magenta | `35` |
| green | `32` | cyan | `36` |
| yellow | `33` | white | `37` |

Plain: `\x1b[<code>m`. Bold: `\x1b[1;<code>m`. Dim: `\x1b[2;<code>m`.
Reset: `\x1b[0m`. This table is independent of and does not reuse
`repl.py`'s existing `_PROMPT_COLOR`/`_COMMAND_COLOR`/`_DIM`/etc.
constants (used by `TerminalSink`/`TerminalConfirmer`) — see Scope,
non-goals.

## 5. Interface

New file `src/sasha/prompt.py`:

```python
_RL_IGNORE_START = "\001"
_RL_IGNORE_END = "\002"
_RESET = "\x1b[0m"

_BASE_CODES = {
    "black": "30", "red": "31", "green": "32", "yellow": "33",
    "blue": "34", "magenta": "35", "cyan": "36", "white": "37",
}

COLOR_TOKENS: dict[str, str] = {"reset": _RESET}
for _name, _code in _BASE_CODES.items():
    COLOR_TOKENS[_name] = f"\x1b[{_code}m"
    COLOR_TOKENS[f"bold-{_name}"] = f"\x1b[1;{_code}m"
    COLOR_TOKENS[f"dim-{_name}"] = f"\x1b[2;{_code}m"

DEFAULT_TEMPLATE = r"\[green\]\u@\h:\w ✦ \[reset\]"

_POSITIONAL = {"u", "h", "w", "W"}


class PromptError(Exception):
    """Raised by validate_template(). Local to this module — prompt.py
    has no dependency on config.py, so config.load() catches this and
    re-raises as ConfigError (same pattern it already uses for
    tomllib.TOMLDecodeError)."""


def validate_template(template: str) -> None:
    """Raise PromptError if `template` contains any unrecognized escape
    or unknown \\[token\\] name. Called by config.load()."""
    ...


def render(template: str, *, user: str, host: str, cwd: str, home: str,
           color_enabled: bool) -> str:
    """Pure substitution — no I/O. `cwd`/`home` are pre-computed by the
    caller (Repl.loop already does this); \\w abbreviates home to '~'
    exactly as today's hardcoded logic does, \\W is cwd's basename."""
    ...
```

`Config` (in `config.py`) gains a `prompt: str = prompt.DEFAULT_TEMPLATE`
field — a dataclass-level default (not just a `config.load()`-time
fallback), so every existing contract test that constructs `Config(...)`
directly with keyword arguments (six files — `test_task_3_1_repl.py`,
`test_task_3_4_repl_streaming.py`, `test_task_4_1_job_control.py`,
`test_task_5_1_drop_127_preamble.py`, `test_task_5_2_thinking_indicator.py`,
`test_task_6_2_colorization_prompt.py` — none of which pass `prompt=`)
keeps working unamended, with a real, valid template rather than `None`.
This requires `config.py` to `import sasha.prompt as prompt` (or
equivalent) at module level — one-way dependency only, since `prompt.py`
imports nothing from `config.py`.

`config.toml` gains a plain top-level `prompt` key, parallel to
`default_profile` (not a nested table like `[limits]`, since the value is
a single string):

```toml
prompt = "\\[bold-green\\]\\u@\\h\\[reset\\]:\\[bold-blue\\]\\w\\[reset\\] $ "
```

`config.load()`: after resolving the raw `prompt` string (TOML value or
`prompt.DEFAULT_TEMPLATE` if absent), calls
`prompt.validate_template(value)`, catching `prompt.PromptError` and
re-raising `ConfigError(str(exc))` — same place `default_profile`'s
existence is already checked, same wrapping pattern as
`tomllib.TOMLDecodeError` a few lines above it in the existing function.

`Repl.loop()` changes from its current hardcoded block to:

```python
def loop(self):
    home = os.path.expanduser("~")
    user = getpass.getuser()
    host = socket.gethostname()
    while True:
        cwd = self._executor.cwd
        rendered = prompt.render(
            self._config.prompt, user=user, host=host, cwd=cwd,
            home=home, color_enabled=self._sink.color_enabled)
        try:
            line = self._input_fn(rendered)
        ...  # rest of loop() unchanged
```

`_PROMPT_COLOR`, `_RL_IGNORE_START`, `_RL_IGNORE_END` stay defined in
`repl.py` for `TerminalConfirmer`'s continued use (Task 7.1) — this task
does not remove them, only stops using them for the prompt itself.

## 6. Mechanism

### 6.1 Rendering order

`render()` walks the template left to right, emitting: literal
characters as-is; `\[TOKEN\]` substituted with
`_RL_IGNORE_START + COLOR_TOKENS[TOKEN] + _RL_IGNORE_END` if
`color_enabled`, or `""` if not; `\u`/`\h`/`\w`/`\W` substituted with the
corresponding argument as follows.

`\w` = `"~" + cwd[len(home):]` if `cwd.startswith(home)` else `cwd`,
matching today's exact logic (so `cwd == home` renders exactly `"~"`).

`\W`'s exact rule, made explicit to avoid two implementers reading
"basename" two different ways:
- `cwd == home` → `"~"` (matches bash: the home directory itself is `~`
  for both `\w` and `\W`, not `os.path.basename(home)`).
- `cwd == "/"` → `"/"` (not `""` — `os.path.basename("/")` returns `""`
  in Python, which would silently render nothing; bash's `\W` shows `/`
  for the root directory).
- otherwise → `os.path.basename(cwd)`.

`Executor.cwd` is assumed already normalized (absolute, no trailing
slash except for `"/"` itself) — same assumption `\w`'s existing logic
already makes today; `render()` does no normalization of its own.

### 6.2 Validation

`validate_template()` performs the same left-to-right walk but only
checks well-formedness — any `\` not starting `\u`, `\h`, `\w`, `\W`, or
`\[<valid-token-name>\]` raises `PromptError` with a message naming the
exact bad substring (e.g. `unknown prompt token '\[grene\]'` or
`unrecognized escape '\q' `) and, for unknown tokens, lists the valid
names. An unterminated `\[` (no matching `\]`) is also a `PromptError`.
`config.load()` catches `PromptError` and re-raises `ConfigError(str(exc))`
— callers of `config.load()` only ever see `ConfigError`; `PromptError` is
an implementation detail of `prompt.py` used to avoid importing `config.py`.

### 6.3 `DEFAULT_TEMPLATE` equivalence

`DEFAULT_TEMPLATE = r"\[green\]\u@\h:\w ✦ \[reset\]"` — rendering this
with `color_enabled=True` produces:
`\001\x1b[32m\002` + `user@host:cwd ✦ ` + `\001\x1b[0m\002`, which is
byte-identical to today's hardcoded
`{_RL_IGNORE_START}{_PROMPT_COLOR}{_RL_IGNORE_END}...{_RL_IGNORE_START}{_RESET}{_RL_IGNORE_END}`
output (`_PROMPT_COLOR = "\x1b[32m"` already equals `COLOR_TOKENS["green"]`
under this table). With `color_enabled=False`, both color tokens render
as `""`, leaving exactly `user@host:cwd ✦ ` — matching today's plain
branch.

## 7. Decisions log

- **Decision:** Two-layer lookup (`config.toml` → hardcoded default), not
  three. **Rationale:** Salvador's call — `$PS1` is essentially never
  exported to child processes in practice (confirmed empirically in
  ISSUE-022), so a third layer would almost never fire; not worth the
  extra code path and test surface.
- **Decision:** Named color tokens (`\[green\]`), not literal ANSI escapes
  wrapped in `\[ \]` (real bash's actual convention). **Rationale:**
  Salvador's call — TOML has no octal-escape support (`\033` isn't valid;
  you'd need TOML's own backslash-u Unicode escape for a literal ESC byte in every color code), which is
  awkward to author and read. Named tokens are readable and keep the
  authoring surface friendly.
- **Decision:** Combined tokens (`bold-green`, `dim-red`) rather than
  stackable independent modifiers (`\[bold\]\[green\]`). **Rationale:**
  Salvador's call — a fixed, finite lookup table (25 entries) is simpler
  to implement and test exhaustively than a small state machine tracking
  which modifiers are currently "open."
- **Decision:** Fail-fast `ConfigError` at `config.load()` time for any
  unrecognized escape/token, not silent passthrough. **Rationale:**
  Salvador's call — matches `config.py`'s existing convention (bad TOML,
  missing `default_profile` already raise immediately); a typo should
  surface at startup, not render as garbled/wrong prompt text.
- **Decision:** Color tokens suppressed (render as `""`) when
  `color_enabled` is `False`; positional escapes unaffected.
  **Rationale:** Salvador's call — matches the rest of the app's
  `NO_COLOR`/non-tty behavior (`TerminalSink`, confirmation gate); a
  custom prompt shouldn't be a way to bypass the global color policy.
- **Decision:** No escape-the-escape mechanism for a literal backslash/
  bracket. **Rationale:** Salvador's call — an edge case not worth
  designing around for v1; explicitly unsupported rather than
  half-supported.
- **Decision:** `prompt.py`'s color table is independent of `repl.py`'s
  existing `_PROMPT_COLOR`/`_COMMAND_COLOR`/`_DIM` constants, not unified
  with them. **Rationale:** Reusing/refactoring those is a separate,
  bigger decision (ISSUE-025) — unifying now would be unrelated scope
  creep into `TerminalSink`/`TerminalConfirmer`.
- **Fixed:** `prompt.py` raises its own `PromptError` rather than
  `config.ConfigError`; `config.load()` catches it and re-raises
  `ConfigError`. **Rationale:** Caught while writing the task spec — the
  original draft had `prompt.py` importing `ConfigError` from `config.py`
  while `config.py` needed to import `DEFAULT_TEMPLATE`/`validate_template`
  from `prompt.py`, a circular import. A module-local exception, wrapped
  at the one call site that needs `ConfigError`, is the same pattern
  `config.py` already uses for `tomllib.TOMLDecodeError` — no new pattern
  introduced, and `prompt.py` stays a dependency-free leaf module.

## 8. Testing strategy

New task's contract tests (`tests/contract/test_task_9_1_*.py`) cover, per
`prompt.py` directly (no `Repl` construction needed — pure function):
each positional escape substitutes correctly, including `\w`'s `~`-
abbreviation and `\W`'s three-way edge cases from §6.1 (`cwd == home` →
`"~"`, `cwd == "/"` → `"/"`, otherwise → basename); each of the 25 color tokens maps to
its exact ANSI code, wrapped in `\001`/`\002`; `reset`; color suppression
when `color_enabled=False` (positional escapes still substitute); unknown
token and unrecognized escape each raise `PromptError` via
`validate_template()`, message naming the bad substring; unterminated
`\[` raises `PromptError`. One dedicated equivalence test asserts
`prompt.render(prompt.DEFAULT_TEMPLATE, ...)` byte-matches today's
hardcoded `Repl.loop()` output for both the colored and plain cases (§6.3)
— this is what lets `test_task_6_2_colorization_prompt.py::test_ac4_*`
stay unamended. The same contract test file also covers `config.load()`'s
new behavior directly: `Config.prompt` defaults to `DEFAULT_TEMPLATE` when
absent from TOML, a valid custom `prompt` value round-trips into
`Config.prompt` unchanged, and an invalid one raises `ConfigError` (not
`PromptError` — confirming the wrap-and-reraise happens) at `load()` time.
