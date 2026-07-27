# Task 51.2: sasha recap + sasha directory-recap on|off

## Authoritative contract
`docs/superpowers/specs/2026-07-26-issue-051-directory-recap-design.md`
§4.7 (`sasha recap`) and §5 (the toggle command + patcher) is the
complete contract for this task.

## What already exists to bind, not reimplement
- `src/sasha/directory_recap.py` (Task 51.1): `gather_context(target,
  log_entries=None) -> dict` and `build_prompt(context: dict) -> str`.
  `sasha recap` calls these two functions directly and verbatim — do not
  duplicate their logic, do not modify this file.
- `src/sasha/llm.py`: `ChatClient.chat(messages, tools, on_text,
  on_reasoning=...)` — bind exactly as `Repl._consult` already does
  (`client.chat([...], [], self._sink.write_chunk)`), raising `ChatError`
  on transport failure. Do not modify this file.
- `src/sasha/config.py`: `set_api_key` (lines ~351-390) is the exact
  pattern to mirror for the new `set_directory_recap_enabled` patcher —
  same read/locate-table/patch-or-insert-key/write shape, same
  `ConfigError` semantics. `Config.directory_recap_enabled` (Task 51.1)
  already exists; do not touch the `Config` dataclass or `_build_config`.
- `src/sasha/repl.py`:
  - `Repl._handle_builtin` (line ~135): the `verb ==` dispatch chain for
    `sasha <verb> ...` commands. Add two new branches here; do not alter
    existing ones.
  - `Repl._activate_profile` (line ~401): the exact pattern to mirror for
    updating live config after a successful disk patch —
    `dataclasses.replace(self._config, ...)` — and for the "no config
    file on disk" guard (`if self._config.path is None: ...`).
  - `Repl._consult` (line ~89): the exact pattern to mirror for a
    synchronous LLM call's `ChatError` handling (the "unreachable" message
    wording) and for streaming via `self._sink.write_chunk`.

## What's new

1. **`src/sasha/config.py`**: add
   ```python
   def set_directory_recap_enabled(path: pathlib.Path, enabled: bool) -> None:
       """Mirrors set_api_key's exact shape: locate the [directory_recap]
       table, find or insert its `enabled` line, rewrite only that line.
       Raises ConfigError if the file can't be read/written, or if no
       [directory_recap] table exists in the file (same fail-closed
       contract as set_api_key's "no [profiles.X] table" case — do NOT
       auto-create the table; every self-installed config.toml has one
       since Task 51.1, so this only fails for a hand-edited file that
       predates the feature, which is an acceptable, clearly-messaged
       failure, not something to silently paper over)."""
   ```
   Placed immediately after `set_api_key` in the file. The written line
   must be exactly `enabled = true\n` or `enabled = false\n` (lowercase,
   matching TOML boolean literal syntax — not Python's `True`/`False`).

2. **`src/sasha/repl.py`**:
   - New method `_handle_recap_builtin(self) -> None`:
     ```python
     def _handle_recap_builtin(self):
         cwd = self._executor.cwd
         context = directory_recap.gather_context(pathlib.Path(cwd))
         prompt = directory_recap.build_prompt(context)
         self._sink.write_line("[sasha] thinking…")
         try:
             client = self._client_factory(self._current_profile)
             client.chat([{"role": "user", "content": prompt}], [],
                         self._sink.write_chunk)
             self._sink.ensure_newline()
         except ChatError as e:
             self._sink.write_line(
                 f"[sasha] {self._current_profile.name} unreachable: "
                 f"{e.reason} — 'sasha profile <name>' to switch")
     ```
     This is deliberately simpler than `_consult`: no tool-calling loop,
     no `run_consult`, no `ActivityIndicator`/`on_phase` wiring, no
     `Session` interaction at all — a single one-shot `chat()` call, same
     shape as `_bootstrap_stage_2`'s existing one-shot pattern. It must
     NOT check `self._config.directory_recap_enabled`, must NOT check or
     mutate `self._recapped_dirs`, and must NOT go through
     `directory_recap.decide()` or `escalate_async()` at all — per design
     §4.7, it bypasses the cascade entirely and always calls the LLM.
   - New method `_handle_directory_recap_toggle(self, arg: str) -> None`:
     ```python
     def _handle_directory_recap_toggle(self, arg):
         if arg not in ("on", "off"):
             self._sink.write_line(f"[sasha] unknown command: directory-recap {arg}")
             return
         enabled = (arg == "on")
         if self._config.path is None:
             self._sink.write_line(
                 "[sasha] couldn't update directory-recap setting: no "
                 "config file on disk — setting unchanged")
             return
         try:
             config.set_directory_recap_enabled(self._config.path, enabled)
         except config.ConfigError as e:
             self._sink.write_line(
                 f"[sasha] couldn't update directory-recap setting: {e} "
                 f"— setting unchanged")
             return
         self._config = dataclasses.replace(
             self._config, directory_recap_enabled=enabled)
         state = "on" if enabled else "off"
         self._sink.write_line(f"[sasha] directory-recap: {state}")
     ```
   - In `_handle_builtin`'s dispatch chain, add (order among the existing
     `elif` branches is your choice, but do not remove/reorder existing
     ones):
     ```python
     elif verb == "recap":
         self._handle_recap_builtin()
     elif verb == "directory-recap":
         if len(tokens) >= 2:
             self._handle_directory_recap_toggle(tokens[1])
         else:
             self._sink.write_line(f"[sasha] unknown command: {rest}")
     ```
   - Extend the existing `help` branch's message to also mention
     `sasha recap` and `sasha directory-recap on|off` (append to the
     existing joined string; do not remove any existing mentions —
     6 existing tests in other files pin substrings of the old profile/
     memory/compact-log mentions, per `_handle_compact_log_builtin`'s own
     precedent of appending rather than rewriting this line).

## Constraints
- Do not touch `src/sasha/directory_recap.py`, `src/sasha/llm.py`, or the
  `Config` dataclass / `_build_config` in `src/sasha/config.py`.
- Do not modify `_check_directory_recap`, `_check_place_shift`, or either
  of their two call sites in `repl.py` (Task 51.1's cascade/gap-filler
  wiring) — this task only adds new builtin-command methods and dispatch
  branches alongside them.
- `sasha recap` must never touch `self._recapped_dirs` or
  `self._config.directory_recap_enabled` — it is fully independent of the
  passive cascade's state.

## Acceptance criteria
1. `config.set_directory_recap_enabled(path, False)` on a file with an
   existing `[directory_recap]\nenabled = true` table rewrites only that
   line to `enabled = false`, leaving every other line byte-identical
   (comments, other sections, blank lines) — `test_ac1_patch_flips_only_the_enabled_line`.
2. `config.set_directory_recap_enabled(path, True)` inserts the line when
   the table exists but has no `enabled` key yet — `test_ac2_patch_inserts_missing_enabled_key`.
3. `config.set_directory_recap_enabled(path, True)` raises `ConfigError`
   when the file has no `[directory_recap]` table at all —
   `test_ac3_patch_raises_when_table_missing`.
4. `sasha recap` (`repl._handle_builtin("sasha recap")`) sends exactly one
   chat request whose message content matches
   `directory_recap.build_prompt(directory_recap.gather_context(...))`
   for the current cwd, and streams the stubbed response text into the
   sink via `write_chunk` — `test_ac4_recap_sends_gathered_context_prompt`.
5. `sasha recap` performs its LLM call even when
   `self._config.directory_recap_enabled` is `False` —
   `test_ac5_recap_ignores_disabled_config`.
6. `sasha recap` performs its LLM call even when the current cwd is
   already in `self._recapped_dirs` (simulating an already-recapped-this-
   session path), and does not add to or otherwise consult that set —
   `test_ac6_recap_ignores_dedup_state`.
7. Calling `sasha recap` twice in a row for the same cwd sends two
   separate chat requests (no suppression on the second call) —
   `test_ac7_recap_has_no_dedup_of_its_own`.
8. `sasha recap` against an unreachable profile (refused connection)
   prints a line containing `"unreachable"` and the profile name, and
   does not raise — `test_ac8_recap_handles_chat_error`.
9. `sasha directory-recap on` / `sasha directory-recap off` update
   `repl._config.directory_recap_enabled` in memory to `True`/`False`
   respectively, and the on-disk file reflects the change when re-read —
   `test_ac9_toggle_updates_memory_and_disk`.
10. `sasha directory-recap` with no argument, or with an argument other
    than `on`/`off`, leaves `directory_recap_enabled` unchanged and
    prints an "unknown command" line —
    `test_ac10_toggle_invalid_argument_is_a_no_op`.
11. `sasha directory-recap on` when `repl._config.path is None` prints a
    "couldn't update... no config file on disk" line and leaves
    `directory_recap_enabled` unchanged —
    `test_ac11_toggle_no_config_path_is_a_no_op`.
12. `sasha help`'s output mentions both `sasha recap` and
    `sasha directory-recap on|off` —
    `test_ac12_help_mentions_both_new_commands`.

## Verified fix sketch

Implemented and verified in a scratch worktree: 12/12 contract tests
green, full suite 651/651 (one pre-existing environmental flake in
`test_task_24_1_kb_relay_helper.py`, unrelated to this task, deselected).
One real bug was found and fixed in the *test file itself* during
verification, not the implementation: several assertions checked
`any(text in l for l in sink.lines)`, but `write_chunk` streams responses
in small pieces (no single chunk contains the full phrase) — fixed to
`text in "".join(sink.lines)`. Matching this sketch is not mandatory —
the contract tests are the real acceptance bar — but it removes
essentially all ambiguity.

`src/sasha/config.py`, appended immediately after `set_api_key`:
```python
def set_directory_recap_enabled(path: pathlib.Path, enabled: bool) -> None:
    try:
        text = path.read_text()
    except OSError as exc:
        raise ConfigError(f"couldn't read {path}: {exc}") from exc

    lines = text.splitlines(keepends=True)
    header = "[directory_recap]"
    start = None
    for i, line in enumerate(lines):
        if line.strip() == header:
            start = i
            break
    if start is None:
        raise ConfigError(f"no [directory_recap] table in {path}")

    end = len(lines)
    for i in range(start + 1, len(lines)):
        stripped = lines[i].strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            end = i
            break

    enabled_line = f"enabled = {'true' if enabled else 'false'}\n"
    key_re = re.compile(r"^\s*enabled\s*=")
    found = None
    for i in range(start + 1, end):
        if key_re.match(lines[i]):
            found = i
            break

    if found is not None:
        lines[found] = enabled_line
    else:
        lines.insert(end, enabled_line)

    try:
        path.write_text("".join(lines))
    except OSError as exc:
        raise ConfigError(f"couldn't write {path}: {exc}") from exc
```

`src/sasha/repl.py`, both new methods placed immediately after
`_handle_compact_log_builtin`:
```python
def _handle_recap_builtin(self):
    cwd = self._executor.cwd
    context = directory_recap.gather_context(pathlib.Path(cwd))
    prompt = directory_recap.build_prompt(context)
    self._sink.write_line("[sasha] thinking…")
    try:
        client = self._client_factory(self._current_profile)
        client.chat([{"role": "user", "content": prompt}], [],
                    self._sink.write_chunk)
        self._sink.ensure_newline()
    except ChatError as e:
        self._sink.write_line(
            f"[sasha] {self._current_profile.name} unreachable: "
            f"{e.reason} — 'sasha profile <name>' to switch")

def _handle_directory_recap_toggle(self, arg):
    if arg not in ("on", "off"):
        self._sink.write_line(f"[sasha] unknown command: directory-recap {arg}")
        return
    enabled = (arg == "on")
    if self._config.path is None:
        self._sink.write_line(
            "[sasha] couldn't update directory-recap setting: no "
            "config file on disk — setting unchanged")
        return
    try:
        config.set_directory_recap_enabled(self._config.path, enabled)
    except config.ConfigError as e:
        self._sink.write_line(
            f"[sasha] couldn't update directory-recap setting: {e} "
            f"— setting unchanged")
        return
    self._config = dataclasses.replace(
        self._config, directory_recap_enabled=enabled)
    state = "on" if enabled else "off"
    self._sink.write_line(f"[sasha] directory-recap: {state}")
```

Dispatch chain (`_handle_builtin`), added between the existing
`compact-log` and `help` branches:
```python
elif verb == "recap":
    self._handle_recap_builtin()
elif verb == "directory-recap":
    if len(tokens) >= 2:
        self._handle_directory_recap_toggle(tokens[1])
    else:
        self._sink.write_line(f"[sasha] unknown command: {rest}")
```

`help`'s existing message string gains `"sasha recap, sasha
directory-recap on|off, "` inserted before the trailing `"?? <question>"`.

## Standing instructions
- Your goal: make the contract tests in
  `tests/contract/test_task_51_2_sasha_recap_command.py` pass.
- NEVER modify files under `tests/contract/` — the gate rejects the
  commit if you do. If a contract test seems wrong or unimplementable,
  stop and flag it.
- You may add your own supplementary tests under `tests/impl/`.
- Flag unexpected state; do not modify anything outside this task.
- Deliver exactly ONE new commit on top of the HEAD you started from;
  never amend, rebase, or reset; leave the working tree clean.
- Commit message must follow the task-commit trailer format in
  `CLAUDE.md`: `Done: Task 51.2 — <description>`, `Fixed: ISSUE-051`
  (this closes the issue — Task 51.2 is the last task in Phase 51),
  `Next: Task 51.3 — Phase 51 review (Tier 1, with Salvador)`, and your
  `Co-Authored-By:` signature line exactly as given in the dispatch
  prompt.
