# Sasha — First-Run LLM Setup & Provider Activation Design (ISSUE-026)

**Status:** Draft for review
**Date:** 2026-07-17

## 1. Mission

Today `config.py` never writes `config.toml` to disk, and its in-memory
default profile (`deepseek`) has an empty `api_key` unless
`DEEPSEEK_API_KEY` happens to be set — so a brand-new install silently
sends unauthenticated requests instead of working or explaining why not.
This design makes Sasha usable immediately on first run via a free,
keyless provider, while pre-seeding "template" profiles for OpenAI and
DeepSeek (right endpoint, no key) that the user can activate from inside
Sasha itself — `sasha profile <name>`, paste a key when prompted, done —
with no manual `config.toml` editing required.

## 2. Scope

### In scope:
- `config.py`: self-install a real `config.toml` to disk on first run
  (mirrors `personality.py`'s precedent), seeded with four profiles —
  `llm7` (new default: free, keyless, streaming, works immediately),
  `openai` and `deepseek` (correct `base_url`/`model`, empty `api_key` —
  need a key), `local` (unchanged).
- A new `Profile.api_key_required: bool` field (default `True`) to
  distinguish "empty because this provider is deliberately keyless"
  (`llm7`, `local`) from "empty because no key has been entered yet"
  (`openai`, `deepseek`, and any user-added profile by default).
- `Config` gains `notice: str | None` and `path: pathlib.Path` fields —
  same shape as `PersonalityResult`, populated by `load()`.
- A new `config.set_api_key(path, profile_name, api_key)`: a targeted
  text patch that sets (or inserts) one profile's `api_key` line in an
  existing `config.toml`, leaving every other line untouched.
- `repl.py`: extend the existing `sasha profile <name>` builtin — if the
  target profile has `api_key_required=True` and an empty `api_key`,
  prompt for a key (masked, via an injectable `getpass_fn`) instead of
  switching immediately. Empty input cancels; a pasted key is persisted
  via `set_api_key` and the session switches to the now-complete profile.
- `__main__.py`: wire `Config.notice` into the same one-time boot notice
  pattern already used for `personality_result.notice`; wire a new
  `getpass_fn` parameter into `Repl(...)`.
- One-line addition to `sasha help`'s text noting that `sasha profile
  <name>` will ask for a key if the profile needs one.

### Out of scope / non-goals:
- **Anthropic.** Its wire format (`/v1/messages`, `x-api-key` header,
  non-OpenAI request/response/streaming shape) doesn't fit `ChatClient`
  at all — a protocol adapter is real scope, not a config change.
  Salvador's call: separate future issue, not ISSUE-026.
- **Switching `config.toml` to JSON.** Considered and rejected —
  Salvador's call: stdlib `tomllib` plus a small targeted patcher solves
  the actual problem (persist one field) without a breaking format
  change to two already-shipped phases (Phase 9 configurable prompt,
  Phase 13 personality) or losing TOML's multi-line-string ergonomics
  for the hand-edited `prompt` template.
- **Live key validation** (a test ping before persisting). A bad key
  surfaces through the existing `ChatError` path in `Repl._consult()`
  ("`<profile>` unreachable: `<reason>` — `'sasha profile <name>'` to
  switch") — already-shipped behavior, no new code needed.
- **Retry/health-check machinery for `llm7`.** It's a best-effort,
  third-party free tier with no SLA, documented as such. If it's down,
  the same `ChatError` fallback applies.
- **Hot-reloading `config.toml`** if the user hand-edits it mid-session —
  loaded once at startup, matching today's behavior.
- **A multi-step onboarding wizard.** One masked prompt, one cancel path
  — no provider-picker menu, no re-prompt-on-invalid-input loop beyond
  what's specified in §5.

## 3. Key contracts & assumptions

1. **A `config.toml` always exists after first launch.** Self-install is
   unconditional on the default path when no file is present — the
   patcher (`set_api_key`) therefore never needs a "create from scratch"
   branch; it only ever edits an existing file. An explicit `--config
   <path>` that doesn't exist still raises `ConfigError` unchanged (self-
   install only applies to the default path, exactly like today's
   `FileNotFoundError` branch already distinguishes the two).
2. **`api_key_required` disambiguates "keyless by design" from "keyless
   because unset."** `llm7` and `local` ship `api_key_required = false`;
   everything else defaults to `true`. The Repl's prompt-for-key
   condition is exactly `not profile.api_key and profile.api_key_required`
   — this is the only place that ambiguity is resolved, and it's a single
   boolean, not a hardcoded provider-name list.
3. **The key is never echoed or logged.** Read via `getpass_fn` (masked,
   injectable for tests — same DI shape as `input_fn`/`confirmer`), never
   passed through `write_line`/`_profile_line` (which only prints
   `name`/`model`/`base_url`), never written anywhere but the one
   `api_key = "..."` line `set_api_key` touches.
4. **Persistence failure fails closed.** If `set_api_key` can't write
   (permissions, disk full, missing `[profiles.<name>]` table in a hand-
   edited file), the profile does **not** switch — Sasha reports why and
   the user stays on their current, working profile. Never silently run
   a session on a key that won't survive restart without saying so.
5. **`set_api_key` touches only its target line.** Every other line —
   comments, the `prompt` template, `[limits]`, other profiles — is
   byte-identical before and after. This is what makes self-installing a
   file safe: nothing downstream of that write destroys user
   customization the way a full TOML regeneration would.

## 4. Interface

Changes to `src/sasha/config.py`:

```python
@dataclasses.dataclass(frozen=True)
class Profile:
    name: str
    base_url: str
    model: str
    api_key: str
    api_key_required: bool = True


@dataclasses.dataclass(frozen=True)
class Config:
    default_profile: str
    profiles: dict[str, Profile]
    limits: Limits
    prompt: str = prompt.DEFAULT_TEMPLATE
    path: pathlib.Path | None = None
    notice: str | None = None


_DEFAULT_PROFILE_NAME = "llm7"

_DEFAULT_PROFILE_FIELDS_BY_NAME = {
    "llm7": {
        "base_url": "https://api.llm7.io/v1",
        "model": "gpt-oss:20b",
        "api_key_env": "",
        "api_key": "",
        "api_key_required": False,
    },
    "openai": {
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
        "api_key_env": "",
        "api_key": "",
        "api_key_required": True,
    },
    "deepseek": {
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-chat",
        "api_key_env": "DEEPSEEK_API_KEY",
        "api_key": "",
        "api_key_required": True,
    },
    "local": {
        "base_url": "http://192.168.8.253:11441/v1",
        "model": "local",
        "api_key_env": "",
        "api_key": "",
        "api_key_required": False,
    },
}

_DEFAULT_CONFIG_TOML = """\
# Sasha config — generated on first run. Edit freely; re-running Sasha
# never overwrites this file. See README for the full option reference.
default_profile = "llm7"

# Free, keyless, streaming — works immediately, best-effort only (no
# uptime guarantee from the provider). Switch anytime with
# 'sasha profile <name>'; picking 'openai' or 'deepseek' below will ask
# you to paste an API key.
[profiles.llm7]
base_url = "https://api.llm7.io/v1"
model = "gpt-oss:20b"
api_key = ""
api_key_required = false

[profiles.openai]
base_url = "https://api.openai.com/v1"
model = "gpt-4o-mini"
api_key = ""
api_key_required = true

[profiles.deepseek]
base_url = "https://api.deepseek.com/v1"
model = "deepseek-chat"
api_key_env = "DEEPSEEK_API_KEY"
api_key = ""
api_key_required = true

[profiles.local]
base_url = "http://192.168.8.253:11441/v1"
model = "local"
api_key = ""
api_key_required = false
"""


def set_api_key(path: pathlib.Path, profile_name: str, api_key: str) -> None:
    """See §5.2 for the exact patch algorithm. Raises ConfigError if the
    file or the [profiles.<profile_name>] table can't be found, or the
    file can't be written."""
    ...
```

Changes to `src/sasha/repl.py` (adds two new imports — `dataclasses` and
`sasha.config as config`; no cycle risk, `config.py` imports nothing from
`repl.py`):

```python
import dataclasses
import sasha.config as config

class Repl:
    def __init__(self, config, executor, mutes, session, client_factory,
                 confirmer, input_fn, sink, getpass_fn=getpass.getpass):
        ...
        self._getpass_fn = getpass_fn

    def _activate_profile(self, name):
        """See §5.3. Called from _handle_builtin's 'profile' verb in
        place of the current unconditional switch."""
        ...
```

Changes to `src/sasha/__main__.py` — after `config = load(args.config)`:
```python
if config.notice:
    sink.write_line(config.notice)
...
repl = Repl(..., getpass_fn=getpass.getpass)
```

## 5. Mechanism

### 5.1 `load()`'s self-install branch

Replaces the current `FileNotFoundError` branch's in-memory-only return:

```
if path does not exist:
    if path != _default_config_path():
        raise ConfigError(...)          # unchanged
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_DEFAULT_CONFIG_TOML)
        notice = f"[sasha] created {path} — using the free 'llm7' tier; " \
                 f"'sasha profile openai' or 'sasha profile deepseek' " \
                 f"to switch and add your own key"
    except OSError:
        notice = f"[sasha] couldn't create {path} — using defaults " \
                  f"for this session only"
    return Config(
        default_profile=_DEFAULT_PROFILE_NAME,
        profiles={name: Profile(name=name, api_key_required=pf["api_key_required"],
                                 base_url=pf["base_url"], model=pf["model"],
                                 api_key=_resolve_api_key(pf))
                  for name, pf in _DEFAULT_PROFILE_FIELDS_BY_NAME.items()},
        limits=Limits(), prompt=prompt.DEFAULT_TEMPLATE,
        path=path, notice=notice)
```

No re-read after a successful write — same reasoning as
`personality.py`: the in-memory `Config` already matches exactly what
was written. On a write failure, behavior degrades to today's existing
in-memory-only default (still fully usable — `llm7` is keyless), just
with a notice explaining nothing was persisted.

The existing-file branch (`raw = tomllib.load(f)`) is unchanged except
it now also sets `path=path` and `notice=None`, and profile construction
reads `api_key_required` the same way it reads `base_url`/`model` (with
`bool` values merged from `raw["profiles"][name]` instead of `str`).

### 5.2 `set_api_key(path, profile_name, api_key)` — patch algorithm

```
lines = path.read_text().splitlines(keepends=True)
header = f"[profiles.{profile_name}]"
start = index of the line that, stripped, equals header
    → not found: raise ConfigError(f"no [profiles.{profile_name}] table in {path}")
end = index of the next line matching ^\[.*\] (any table header) after
      start, or len(lines) if none
within lines[start+1:end], find a line matching ^\s*api_key\s*=
    (a line whose first non-whitespace token is "api_key", NOT preceded
    by "#" — a commented-out api_key line doesn't count)
    → found at index i: replace lines[i] with f"api_key = {json.dumps(api_key)}\n"
    → not found: insert f"api_key = {json.dumps(api_key)}\n" immediately
      before lines[end] (i.e. at the end of the profile's table, before
      the next table or EOF)
path.write_text("".join(lines))
    → OSError on write: raise ConfigError(f"couldn't write {path}: {exc}")
```

`json.dumps(api_key)` is used purely as a string-escaping utility — for
any printable API key (no raw control characters) it produces a valid
TOML basic (double-quoted) string, and stdlib `json` is already a
dependency-free way to get correct quote/backslash escaping without a
TOML-writing library.

### 5.3 `Repl`'s profile-activation flow

Replaces the current unconditional-switch body of the `profile` verb in
`_handle_builtin` (`repl.py:117-128`):

```
name = tokens[1]
if name not in self._config.profiles:
    write_line(f"[sasha] unknown profile: {name}")
    return

profile = self._config.profiles[name]
if profile.api_key or not profile.api_key_required:
    self._current_profile = profile
    write_line(_profile_line(profile))
    return

# needs a key
write_line(f"[sasha] {name} has no API key configured. "
           f"Paste it now (input hidden), or press Enter to cancel:")
key = self._getpass_fn("> ").strip()
if not key:
    write_line("[sasha] cancelled — profile unchanged")
    return

try:
    config.set_api_key(self._config.path, name, key)
except config.ConfigError as e:
    write_line(f"[sasha] couldn't save API key: {e} — profile unchanged")
    return

updated_profile = dataclasses.replace(profile, api_key=key)
updated_profiles = dict(self._config.profiles, **{name: updated_profile})
self._config = dataclasses.replace(self._config, profiles=updated_profiles)
self._current_profile = updated_profile
write_line(_profile_line(updated_profile))
```

If `self._config.path` is `None` (theoretically only reachable if
`Config` were hand-constructed without going through `load()`, which
never happens in `main()`), the same `ConfigError`-caught branch reports
the failure and leaves the profile unchanged — no separate code path
needed.

## 6. Decisions log

- **Decision:** Default provider is `llm7` (`api.llm7.io`), not
  Pollinations. **Rationale:** live-tested during design — Pollinations'
  `gen.pollinations.ai` returns 401 for real (non-cached) requests on
  both streaming and non-streaming despite its docs claiming keyless
  access; `llm7` was verified working end-to-end including **streaming**,
  which `ChatClient` requires unconditionally (it has no non-streaming
  code path).
- **Decision:** Anthropic dropped from scope entirely. **Rationale:**
  Salvador's call — wire-format mismatch is a real adapter task, not a
  config tweak; keeping ISSUE-026 to providers `ChatClient` already
  speaks (OpenAI-shaped Chat Completions) keeps this task config/UX-only.
- **Decision:** Keep TOML; add a targeted line-patcher instead of
  switching to JSON or writing a general TOML serializer. **Rationale:**
  Salvador's call — the actual problem (persist one field) doesn't need
  a general writer, and a format change would break two already-shipped
  phases and lose the `prompt` template's multi-line-string ergonomics.
- **Decision:** New `Profile.api_key_required: bool` field, rather than
  inferring "needs a key" from `api_key_env` presence or a hardcoded
  provider-name list. **Rationale:** `api_key_env` doesn't work as a
  signal (`llm7` and `local` have none, but so would any user-added
  keyless profile; `deepseek` has one *and* still needs prompting when
  unset). An explicit, TOML-settable boolean is unambiguous and
  generalizes to any profile a user hand-adds later.
- **Decision:** Self-install is eager (a real file is always written on
  first run), not lazy (written only when the user first enters a key).
  **Rationale:** Matches `personality.py`'s established precedent
  exactly, and keeps `set_api_key` a single-purpose "edit an existing
  file" function with no "create from scratch" branch to also maintain.
- **Decision:** API key input is masked via `getpass.getpass` (already
  imported in `repl.py` for `getuser()`), injectable as `getpass_fn` for
  tests. **Rationale:** Salvador's call — standard practice for secrets;
  DI keeps it testable without mocking the real `getpass` module.
- **Decision:** Persistence failure fails closed — the profile does not
  switch if `set_api_key` fails. **Rationale:** Salvador's call (implicit
  in the "guide the user, don't surprise them" framing) — a session
  running on an unpersisted key that silently reverts next launch is a
  worse experience than a clear error telling the user their key wasn't
  saved.
- **Decision:** `openai`'s seeded `model` is `"gpt-4o-mini"`. **Rationale:**
  a reasonable, widely-known, low-cost default — **not a verified-current
  OpenAI catalog entry**; unlike `llm7`'s endpoint (live-tested during
  design) and `deepseek-chat` (already shipped and in use), this value
  wasn't checked against OpenAI's live API. The implementer should treat
  it as an editable placeholder, same as any hand-edited `config.toml`
  value — the profile is unusable until the user supplies a key anyway,
  so a stale model id fails obviously (a clear `ChatError`) rather than
  silently.
- **Decision:** No live key validation (test ping) before persisting.
  **Rationale:** The existing `ChatError`-in-`_consult()` fallback
  already reports a bad/unreachable profile with a "switch profile"
  hint — adding a synchronous validation call duplicates that path and
  introduces a new failure mode (network hiccup ≠ bad key) for no
  additional information the user doesn't already get on first use.

## 7. Testing strategy

`config.py` contract tests (against `tmp_path`-based fake paths, never
the real `~/.config/sasha/`): absent file at the default path →
self-install writes exactly `_DEFAULT_CONFIG_TOML`, returned `Config`
has `default_profile == "llm7"`, `notice` set and naming the path,
`profiles["llm7"].api_key_required is False`; absent file at an
explicit non-default `--config` path → `ConfigError`, unchanged from
today; self-install write failure (unwritable parent dir) → in-memory
default `Config` returned with a distinct failure `notice`, no
exception; existing file present → `notice is None`, `path` set, no
write attempted (verified via mtime or a read-only temp dir); a
hand-edited file with `api_key_required = false` on a custom profile →
parsed correctly as a bool, not silently dropped like an unrecognized
key would be.

`set_api_key` contract tests: profile with no existing `api_key` line →
inserts one at the end of that table, every other line byte-identical;
profile with an existing `api_key = "old"` line → replaces only that
line's value; a key containing a `"` or `\` → round-trips correctly
through `tomllib.load` after patching (proves the `json.dumps` escaping
is TOML-safe); target profile's table missing from the file →
`ConfigError` naming the profile; unwritable file → `ConfigError`,
original file content unchanged (no partial write).

`repl.py` contract tests (via a fake `getpass_fn` and fake `sink`, no
real terminal I/O): `sasha profile llm7` (or any `api_key_required=False`
profile) switches immediately, `getpass_fn` never called; `sasha profile
openai` with an already-set `api_key` switches immediately, no prompt;
`sasha profile openai` with empty `api_key` and a fake `getpass_fn`
returning `""` → profile unchanged, "cancelled" message, `set_api_key`
never called; same with a fake `getpass_fn` returning a real-looking key
→ `set_api_key` called with exactly that key and profile name, in-memory
`Config`/`_current_profile` updated, confirmation line printed without
the key appearing in any sink output; `set_api_key` raising `ConfigError`
→ error message surfaced verbatim, profile unchanged, `_current_profile`
still the prior one.
