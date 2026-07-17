# Task 15.2: repl.py + __main__.py — masked key-entry flow on 'sasha profile <name>'

## Authoritative contract
`docs/superpowers/specs/2026-07-17-llm-setup-design.md` is the complete
design record for this task — read it first, especially §4 (interface)
and §5.3 (mechanism — the exact profile-activation flow). Task 15.1
(already landed) implemented the `config.py` half this task binds to:
`Profile.api_key_required`, `Config.path`, `Config.notice`,
`config.set_api_key(path, profile_name, api_key)`,
`config.ConfigError` — read `src/sasha/config.py` directly for their
exact, already-shipped signatures rather than trusting a summary.

## What already exists
- `src/sasha/config.py` (Task 15.1, landed): `Profile.api_key_required:
  bool` (defaults `True`); `Config.path: pathlib.Path | None` (the
  resolved config file path — `None` only when self-install failed and
  Sasha is running off in-memory defaults with nothing on disk);
  `Config.notice: str | None` (a one-time boot message, `None` when
  there's nothing to report); `config.set_api_key(path, profile_name,
  api_key) -> None` (patches one profile's `api_key` line in place,
  raises `config.ConfigError` on any failure — missing table, unreadable
  file, unwritable file); `config.ConfigError`. None of these are
  modified by this task — bind to them exactly as shipped.
- `src/sasha/repl.py` `Repl.__init__`, `_handle_builtin`,
  `_profile_line` — the three things this task modifies. Every other
  method (`_consult`, `_handle_pending_offer_ignore`, `run_once`,
  `loop`, `TerminalConfirmer`) is unchanged. `_profile_line(profile)`
  (module-level function, line ~29) already renders `"[sasha] profile:
  {name} ({model} @ {base_url})"` — reuse it as-is; it never touches
  `api_key`, so it stays safe to call after a key is entered.
- `src/sasha/__main__.py` `main()` — already constructs `sink =
  TerminalSink()`, prints `personality_result.notice` if set, then
  constructs `confirmer` and finally `repl = Repl(...)`. This task adds
  a `config.notice` print alongside the personality one, and a
  `getpass_fn` keyword argument to the `Repl(...)` call — no other line
  in `main()` changes.
- `tests/contract/test_task_3_1_repl.py`'s `test_ac7_profile_builtin`
  and `tests/contract/test_task_10_1_boot_hint.py`'s
  `test_ac4_profile_verb_switch_unchanged` — both already amended by
  Tier 1 in this same spec commit (their `"other"` fixture profile now
  carries a non-empty `api_key` so they keep exercising the *immediate*
  switch path, unaffected by this task). Not something you need to
  touch; every other assertion in both files is untouched and still
  pins prior behavior exactly.

## What's new

Changes to `src/sasha/repl.py`. Two new imports at the top of the file:
```python
import dataclasses
import getpass
import os
import shlex
import socket
import sys
from typing import Protocol

import sasha.config as config
from sasha.agent import run_consult
from sasha.llm import ChatError
from sasha.triggers import Decision, command_word, decide
import sasha.prompt as prompt_mod
```
(`getpass` was already imported for `getuser()` in `loop()` — this task
adds `dataclasses` and `sasha.config as config`; no import cycle,
`config.py` imports nothing from `repl.py`.)

`Repl.__init__` gains one new parameter with a default, so every
existing all-positional or all-keyword call site (there are many across
`tests/contract/`) keeps working unmodified:
```python
class Repl:
    def __init__(self, config, executor, mutes, session, client_factory,
                 confirmer, input_fn, sink, getpass_fn=getpass.getpass):
        self._config = config
        self._executor = executor
        self._mutes = mutes
        self._session = session
        self._client_factory = client_factory
        self._confirmer = confirmer
        self._input_fn = input_fn
        self._sink = sink
        self._getpass_fn = getpass_fn
        self._current_profile = config.profiles[config.default_profile]
        self._pending_offer = None  # (line, exit_code) or None
```
Note the constructor parameter is also named `config` (shadowing the
module import `config` within `__init__`'s local scope only — this
already matches the current code exactly, unchanged). The new
`_activate_profile` method below is not inside `__init__`, so it sees
the module-level `config` import, not the shadowed local.

`_handle_builtin`'s `profile` verb branch changes from an inline
if/elif to a delegating call:
```python
        elif verb == "profile":
            if len(tokens) == 1:
                p = self._current_profile
                self._sink.write_line(_profile_line(p))
            elif len(tokens) >= 2:
                self._activate_profile(tokens[1])
```
(Everything else in `_handle_builtin` — the `mute`/`unmute` branches,
the final `else`, the `return True` — is unchanged.)

New method on `Repl`, placed anywhere convenient in the class body:
```python
    def _activate_profile(self, name):
        if name not in self._config.profiles:
            self._sink.write_line(f"[sasha] unknown profile: {name}")
            return

        profile = self._config.profiles[name]
        if profile.api_key or not profile.api_key_required:
            self._current_profile = profile
            self._sink.write_line(_profile_line(profile))
            return

        if self._config.path is None:
            self._sink.write_line(
                "[sasha] couldn't save API key: no config file on disk "
                "— profile unchanged")
            return

        self._sink.write_line(
            f"[sasha] {name} has no API key configured. "
            f"Paste it now (input hidden), or press Enter to cancel:")
        key = self._getpass_fn("> ").strip()
        if not key:
            self._sink.write_line("[sasha] cancelled — profile unchanged")
            return

        try:
            config.set_api_key(self._config.path, name, key)
        except config.ConfigError as e:
            self._sink.write_line(
                f"[sasha] couldn't save API key: {e} — profile unchanged")
            return

        updated_profile = dataclasses.replace(profile, api_key=key)
        updated_profiles = dict(self._config.profiles)
        updated_profiles[name] = updated_profile
        self._config = dataclasses.replace(self._config,
                                            profiles=updated_profiles)
        self._current_profile = updated_profile
        self._sink.write_line(_profile_line(updated_profile))
```

`sasha help`'s text (inside `_handle_builtin`'s `verb == "help"` branch)
gains one clause:
```python
        elif verb == "help":
            self._sink.write_line("[sasha] sasha mute <cmd> [<N>], "
                      "sasha unmute <cmd>, "
                      "sasha profile [<name>] (asks for a key if needed), "
                      "?? <question>")
```

Changes to `src/sasha/__main__.py`:
- Add `import getpass` alongside the existing `import argparse`,
  `import os`, `import pathlib`, `import readline`, `import sys` block.
- After the existing `if personality_result.notice: sink.write_line(...)`
  block, add:
  ```python
      if config.notice:
          sink.write_line(config.notice)
  ```
  (This shadows nothing — `main()` already has a local variable named
  `config`, the `Config` instance returned by `load()`, from line
  `config = load(args.config)`; `config.notice` reads that instance's
  field, not the module.)
- The `Repl(...)` construction call gains one keyword argument:
  ```python
      repl = Repl(
          config=config,
          executor=executor,
          mutes=mutes,
          session=session,
          client_factory=client_factory,
          confirmer=confirmer,
          input_fn=_repl_input,
          sink=sink,
          getpass_fn=getpass.getpass,
      )
  ```

## Constraints
- Modify only `src/sasha/repl.py` and `src/sasha/__main__.py`. Do not
  touch `src/sasha/config.py` (Task 15.1's landed interface is a fixed
  dependency for this task, not something to adjust), `src/sasha/
  session.py`, `src/sasha/personality.py`, `src/sasha/prompt.py`,
  `README.md`, or anything under `docs/`.
- `Repl.__init__`'s new `getpass_fn` parameter must default to
  `getpass.getpass` and be the last parameter — every existing
  `Repl(config, executor, mutes, session, client_factory, confirmer,
  input_fn, sink)` 8-positional-argument call site across
  `tests/contract/` must keep working with zero changes.
- The API key is never written to any sink output. `_profile_line`
  already only renders `name`/`model`/`base_url` — do not change it to
  include `api_key`, and do not log or print `key`/`api_key` anywhere
  else in `_activate_profile`.
- Greenfield-vs-surgical routing: this task makes precise edits to
  `repl.py`'s existing control flow (`_handle_builtin`,
  `__init__`) and adds one small new method bound tightly to Task
  15.1's already-shipped `config.py` interface. Dispatch to the default
  model (deepseek-v4-pro); do not use a `SASHA_DISPATCH_MODEL`
  local-model override.

## Acceptance criteria
(encoded in `tests/contract/test_task_15_2_llm_setup_repl.py`)

1. `sasha profile <name>` on a profile with `api_key_required=False`
   (regardless of whether `api_key` is empty) switches immediately —
   `_current_profile` becomes that profile, `_profile_line(...)` is
   written to the sink, `getpass_fn` is never called — `test_ac1_*`.
2. `sasha profile <name>` on a profile that already has a non-empty
   `api_key` switches immediately regardless of `api_key_required` —
   `getpass_fn` is never called — `test_ac2_*`.
3. `sasha profile <name>` on a profile with `api_key_required=True` and
   an empty `api_key`, with a fake `getpass_fn` that returns `""` (or
   whitespace-only): the profile does **not** switch (`_current_profile`
   unchanged), a `"cancelled"` message is written, `config.set_api_key`
   is never called (verify via a monkeypatched/spy `config.set_api_key`
   — do not touch a real file) — `test_ac3_*`.
4. Same setup as AC3, but `getpass_fn` returns a real-looking key: a spy
   replacing `config.set_api_key` is called with exactly
   `(self._config.path, name, key)`; `_current_profile` becomes a
   `Profile` equal to the original except `api_key` is now that key;
   `_profile_line(...)` for the updated profile is written to the sink;
   no sink output anywhere contains the literal key string —
   `test_ac4_*`.
5. Same setup as AC4, but the spy `config.set_api_key` raises
   `config.ConfigError("boom")`: `_current_profile` is unchanged, the
   sink receives a message containing `"boom"` and `"profile
   unchanged"`, no `_profile_line` confirmation is written —
   `test_ac5_*`.
6. `sasha profile <name>` on a profile needing a key when
   `self._config.path is None` (self-install failed, no file on disk):
   the profile does not switch, a message naming the reason ("no config
   file") is written, `getpass_fn` is never called — `test_ac6_*`.
7. `sasha help`'s output still contains `"mute"`, `"profile"`, and
   `"??"` (the existing `test_ac7_help_and_exit` assertion in
   `test_task_3_1_repl.py` already covers this as a regression guard —
   no new test needed here, listed for completeness).
8. Boot notice: with a `Config` whose `notice` is a non-`None` string,
   `main()` prints it via the sink before `repl.loop()` runs — exercised
   as a subprocess/integration test analogous to the existing
   personality-notice pattern in `test_task_13_1_personality.py`'s
   `__main__.py` wiring, or as a focused unit test around the two lines
   changed in `main()` — `test_ac8_*`.

## Standing instructions
- Your goal: make the full contract suite pass:
  `PYTHONPATH=src python3 -m pytest tests/contract/`.
- NEVER modify files under `tests/contract/` — the gate rejects the
  commit if you do.
- Deliver exactly ONE new commit on top of the HEAD you started from;
  never amend, rebase, or reset; leave the working tree clean.
- Commit message per AGENTS.md, with trailers:
  `Done: Task 15.2 — <one line>`, `Fixed: ISSUE-026`,
  `Next: Task 15.3 — Phase 15 review (Tier 1, with Salvador)`, and the
  Co-Authored-By signature line from your dispatch prompt.
