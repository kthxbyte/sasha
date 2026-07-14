# Task 1.1: Package skeleton + config module

## Authoritative contract
`docs/superpowers/specs/2026-07-14-sasha-design.md` §5.7 (config interfaces) and
§8.1 (config file format & defaults) are the complete contract for `config.py`.
§2 (packaging bullet) governs `pyproject.toml`.

## What already exists
- `.gitignore` and `README.md` (stub) — do not modify either.
- `tests/contract/test_task_1_1_config.py` — the executable acceptance criteria.
- No `src/` yet: this is the first implementation task.

## What's new
- `pyproject.toml` — `[project]` name `sasha`, version `0.1.0` (static),
  `requires-python = ">=3.11"`, **no runtime dependencies**, console script
  `sasha = "sasha.__main__:main"`, optional dependency group `dev = ["pytest"]`.
  Build backend: setuptools with `src/` layout (`package-dir`/`packages` as needed).
- `src/sasha/__init__.py` — `__version__ = "0.1.0"`.
- `src/sasha/__main__.py` — `main(argv: list[str] | None = None) -> int`, plus
  `if __name__ == "__main__": raise SystemExit(main())`.
  `--version` → print exactly `sasha 0.1.0` to stdout, return 0.
  No arguments → print `[sasha] not wired yet — REPL lands in Task 3.2`, return 0.
  (No other flags in this task; this stub is replaced in Task 3.2.)
- `src/sasha/config.py` — per §5.7:
  - `Profile(name, base_url, model, api_key)` — frozen dataclass.
  - `Limits(history_tokens=12000, stderr_capture_bytes=8192, max_tool_turns=10,
    mute_threshold=3)` — frozen dataclass.
  - `Config(default_profile, profiles, limits)` — frozen dataclass.
  - `ConfigError(Exception)`.
  - `load(path: pathlib.Path | None = None) -> Config`.
- `tests/impl/__init__.py` — empty file (implementer test package).

## Pinned semantics (decisions already made — implement exactly these)
1. **Default config path** for `load(None)`:
   `$XDG_CONFIG_HOME/sasha/config.toml` when `XDG_CONFIG_HOME` is set and
   non-empty, else `~/.config/sasha/config.toml` (via `Path.home()` /
   `os.path.expanduser`, which honor `$HOME`).
2. **Missing file**: `load(None)` with no file at the default path returns the
   built-in defaults of §8.1 exactly (profiles `deepseek` and `local`,
   `default_profile = "deepseek"`, default `Limits()`). A missing file at an
   **explicitly passed** `path` raises `ConfigError`.
3. **Merge, don't replace**: defaults are the base. `default_profile` overrides
   the scalar; each `[profiles.NAME]` table merges field-by-field into the
   existing profile of that name (creating a new profile if the name is new);
   `[limits]` merges per-key. Unknown keys anywhere are ignored silently.
4. **default_profile must exist** in the merged profile map, else `ConfigError`.
5. **api_key resolution** (per profile, at load time): if `api_key_env` is a
   non-empty string AND that env var is set to a non-empty value → that value;
   else if literal `api_key` is present and non-empty → that literal; else `""`.
6. **Malformed TOML** → raise `ConfigError` whose `str()` contains the
   `tomllib` parser message (non-empty).
7. Built-in default profiles carry `api_key_env = "DEEPSEEK_API_KEY"` for
   `deepseek` and `api_key_env = ""` for `local`, exactly as §8.1.

## Constraints
- Runtime imports in `src/sasha/` restricted to the Python standard library.
- Do not touch: `scripts/`, `docs/superpowers/`, `CLAUDE.md`, `AGENTS.md`,
  `PLAN.md`, `ISSUE-TRACKING.md`, `README.md`, `.gitignore`, `tests/contract/`.

## Acceptance criteria
(encoded 1:1 in `tests/contract/test_task_1_1_config.py`)
1. `load(None)` with no config file returns the §8.1 built-in defaults —
   `test_ac1_defaults_when_no_config_file`, `test_ac1_default_limits`,
   `test_ac1_profile_dataclass_frozen`.
2. A TOML file merges over defaults; unknown keys ignored; the default XDG
   location is honored — `test_ac2_toml_overrides_merge_into_defaults`,
   `test_ac2_default_location_is_xdg`.
3. Key resolution order env → literal → `""` (empty env var counts as unset) —
   `test_ac3_env_var_wins`, `test_ac3_literal_fallback_when_env_unset`,
   `test_ac3_literal_fallback_when_env_empty`, `test_ac3_empty_when_neither`.
4. Error vocabulary: malformed TOML, missing explicit path, unknown
   `default_profile` each raise `ConfigError` — `test_ac4_*`.
5. Packaging: `python -m sasha --version` prints `sasha 0.1.0`, exit 0;
   `pyproject.toml` declares the `sasha` console script, `requires-python`
   3.11, zero runtime dependencies; `sasha.__version__ == "0.1.0"` —
   `test_ac5_*`.

## Standing instructions
- Your goal: make `tests/contract/test_task_1_1_config.py` pass
  (`PYTHONPATH=src python3 -m pytest tests/contract/test_task_1_1_config.py`).
- NEVER modify files under `tests/contract/` — the gate rejects the commit if
  you do. If a contract test seems wrong or unimplementable, stop and flag it;
  do not commit.
- You may add supplementary tests under `tests/impl/`.
- Flag unexpected state; do not create or modify anything outside the files
  listed in "What's new".
- Deliver exactly ONE new commit on top of the HEAD you started from; never
  amend, rebase, or reset; leave the working tree clean.
- Commit message per AGENTS.md, with trailers:
  `Done: Task 1.1 — <one line>` and `Next: Task 1.2 — Mute store`.
