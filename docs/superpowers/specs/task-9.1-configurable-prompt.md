# Task 9.1: src/sasha/prompt.py — template engine + config.toml `prompt` key

## Authoritative contract
`docs/superpowers/specs/2026-07-16-configurable-prompt-design.md` is the
complete design record for this task — read it first, especially §4
(token grammar), §5 (interface), §6 (mechanism, including §6.1's exact
`\W` edge-case rules and §6.3's `DEFAULT_TEMPLATE` byte-equivalence
requirement).

## What already exists
- `src/sasha/config.py`: `Config`, `Profile`, `Limits` frozen dataclasses,
  `ConfigError`, and `load()`. `load()` already wraps
  `tomllib.TOMLDecodeError` into `ConfigError` — follow that exact same
  try/except pattern for `prompt.PromptError`. Do not change
  `Profile`, `Limits`, `_resolve_api_key`, `_default_config_path`,
  `_build_default_profiles`, or `_merge_limits`.
- `src/sasha/repl.py`: `Repl.loop()` (the method this task modifies —
  only the prompt-construction block, lines building `raw_prompt`/
  `prompt` before the `self._input_fn(prompt)` call). `_PROMPT_COLOR`,
  `_RL_IGNORE_START`, `_RL_IGNORE_END` stay in `repl.py` — they are still
  used by `TerminalConfirmer` (Task 7.1) and must not be removed, only
  stop being used by `loop()`'s prompt construction.
- `Repl.__init__` already receives `self._config` — no constructor
  changes needed to reach `self._config.prompt` from `loop()`.

## What's new
A new file, `src/sasha/prompt.py`, with:
- `COLOR_TOKENS: dict[str, str]` — 25 entries (8 colors × plain/bold/dim
  + `reset`), built from the ANSI SGR codes in the design doc's §4 table.
  Exported, not inlined into a function.
- `DEFAULT_TEMPLATE: str = r"\[green\]\u@\h:\w ✦ \[reset\]"`.
- `class PromptError(Exception)`.
- `def validate_template(template: str) -> None` — raises `PromptError`
  per design §6.2.
- `def render(template: str, *, user: str, host: str, cwd: str, home: str, color_enabled: bool) -> str`
  — per design §6.1.

Changes to `src/sasha/config.py`:
- `import sasha.prompt as prompt` (or `from sasha import prompt` — your
  choice of import style, just avoid `from sasha.prompt import *`).
- `Config` gains `prompt: str = prompt.DEFAULT_TEMPLATE` as its 4th field
  (after `limits`).
- `load()`: resolve the raw `prompt` value from `raw.get("prompt",
  prompt.DEFAULT_TEMPLATE)` (mirroring how `default_profile_name` is
  already resolved from `raw.get(...)`), guard with `isinstance(...,
  str)` the same way `default_profile_name` already is, then call
  `prompt.validate_template(value)` wrapped in
  `try/except prompt.PromptError as exc: raise ConfigError(str(exc)) from exc`
  (same shape as the existing `tomllib.TOMLDecodeError` handling a few
  lines above). Pass the validated value into the returned `Config(...)`
  call (both the "file not found, using built-in defaults" branch and the
  normal branch need `prompt=` added to their `Config(...)` calls).

Changes to `src/sasha/repl.py`:
- `import sasha.prompt as prompt` (or equivalent) at the top of the file.
- `Repl.loop()`: replace the `raw_prompt = ...` / `if color_enabled: ...`
  block with a single call:
  ```python
  rendered = prompt.render(
      self._config.prompt, user=user, host=host, cwd=cwd, home=home,
      color_enabled=self._sink.color_enabled)
  ```
  then `line = self._input_fn(rendered)`. The `home`/`user`/`host`
  variables computed once before the `while True:` loop, and `cwd`
  computed each iteration from `self._executor.cwd`, are unchanged — only
  what happens with them changes.

## Constraints
- Do not touch: `scripts/`, `docs/superpowers/`, `CLAUDE.md`, `AGENTS.md`,
  `PLAN.md`, `ISSUE-TRACKING.md`, `README.md`, `.gitignore`,
  `pyproject.toml`, `src/sasha/__init__.py`, `src/sasha/mutes.py`,
  `src/sasha/triggers.py`, `src/sasha/session.py`, `src/sasha/agent.py`,
  `src/sasha/llm.py`, `src/sasha/executor.py`, `src/sasha/__main__.py`,
  `tests/contract/`.
- `src/sasha/config.py` is unlocked for this task (see
  `scripts/verify.conf`) — but only for the changes described above.
  `Profile`, `Limits`, and every function not named above stay as-is.
- Do not modify `TerminalConfirmer`, `TerminalSink`, or any of `repl.py`'s
  existing color constants (`_SASHA_COLOR`, `_LLM_COLOR`, `_PROMPT_COLOR`,
  `_COMMAND_COLOR`, `_DIM`, `_RESET`, `_RL_IGNORE_START`,
  `_RL_IGNORE_END`) — `Repl.loop()`'s prompt block is the only thing in
  `repl.py` that changes.
- `prompt.py` must not import anything from `config.py` (avoids a
  circular import — `config.py` imports `prompt.py`, not the reverse).
- No new dependencies — stdlib only, matching the rest of the project.
- Greenfield-vs-surgical routing: this task creates one new module but
  also modifies existing control flow in two files that already work
  (`config.py`'s `load()`, `repl.py`'s `loop()`). Dispatch to the default
  model (deepseek-v4-pro); do not use a `SASHA_DISPATCH_MODEL`
  local-model override.

## Acceptance criteria
(encoded in `tests/contract/test_task_9_1_configurable_prompt.py`)
1. Each positional escape (`\u`, `\h`, `\w`, `\W`) substitutes correctly,
   including `\w`'s `~`-abbreviation and `\W`'s three-way rule from design
   §6.1 (`cwd == home` → `"~"`, `cwd == "/"` → `"/"`, otherwise →
   `os.path.basename(cwd)`) — `test_ac1_*`.
2. Each of the 25 color tokens (8 colors × plain/bold/dim + `reset`) maps
   to its exact ANSI SGR code, wrapped in `\001`/`\002` when
   `color_enabled=True` — `test_ac2_*`.
3. Color tokens render as empty string when `color_enabled=False`;
   positional escapes are unaffected by `color_enabled` — `test_ac3_*`.
4. An unrecognized escape (e.g. `\q`) or unknown token name inside
   `\[...\]` (e.g. `\[grene\]`) raises `prompt.PromptError` from
   `validate_template()`, naming the bad substring in the message; an
   unterminated `\[` also raises `PromptError` — `test_ac4_*`.
5. `prompt.render(prompt.DEFAULT_TEMPLATE, ...)` byte-matches today's
   hardcoded `Repl.loop()` prompt output for both `color_enabled=True`
   and `color_enabled=False`, per design §6.3 — `test_ac5_*`.
6. `config.load()`: `Config.prompt` defaults to `prompt.DEFAULT_TEMPLATE`
   when `config.toml` has no `prompt` key; a valid custom `prompt` string
   in TOML round-trips into `Config.prompt` unchanged; an invalid `prompt`
   string raises `ConfigError` (not `PromptError`) at `load()` time —
   `test_ac6_*`.
7. `Repl.loop()` actually uses `prompt.render(...)` with the real
   `Config.prompt` value and real `color_enabled` — exercised via the
   existing `test_task_6_2_colorization_prompt.py::test_ac4_*` tests,
   which must continue to pass **unmodified** (not part of this task's
   own contract test file, but a hard requirement — running the full
   suite must show zero regressions).

## Standing instructions
- Your goal: make the full contract suite pass:
  `PYTHONPATH=src python3 -m pytest tests/contract/`.
- NEVER modify files under `tests/contract/` — the gate rejects the
  commit if you do. AC7 above requires the *existing*
  `test_task_6_2_colorization_prompt.py` tests to keep passing through
  your changes to `repl.py`/`config.py`, not to be edited.
- Deliver exactly ONE new commit on top of the HEAD you started from;
  never amend, rebase, or reset; leave the working tree clean.
- Commit message per AGENTS.md, with trailers:
  `Done: Task 9.1 — <one line>`, `Fixed: ISSUE-022`,
  `Next: Task 9.2 — Phase 9 review (Tier 1, with Salvador)`, and the
  Co-Authored-By signature line from your dispatch prompt.
