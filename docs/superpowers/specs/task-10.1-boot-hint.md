# Task 10.1: Profile + ?? tip lines printed once at Repl.loop() startup

## Authoritative contract
`issues/ISSUE-027-boot-hint.md` is the complete design record for this
task — read it first. Summary: with the prompt now bash-colored, a new
user has no visual cue that `??` opens a chat with an LLM or which
provider/model is active. This task prints two `[sasha]`-prefixed lines,
once, at the very top of `Repl.loop()`, before the first prompt is shown.

## What already exists
- `src/sasha/repl.py` `Repl.loop()` (the method this task modifies —
  only its top, before the existing `home = os.path.expanduser("~")`
  line; nothing inside the `while True:` loop changes).
- `Repl`'s `profile` verb handler (inside `run_once`, the `elif verb ==
  "profile":` branch) already prints the exact line format this task
  reuses for its first line:
  `f"[sasha] profile: {p.name} ({p.model} @ {p.base_url})"` — appears
  twice in that handler (the no-argument case and the switch-profile
  case). This task factors that format into one shared helper both call.
- `self._current_profile` (set in `Repl.__init__`, reassignable via the
  `profile` verb) — the profile whose info line 1 shows. `Repl.loop()`
  already has access to it via `self._current_profile`, no new
  constructor plumbing needed.
- `self._sink.write_line(...)` — the mechanism both new lines use, same
  as every other `[sasha]`-prefixed message in the codebase.

## What's new
A small module-level or `Repl`-private helper (your choice of shape —
e.g. a `_profile_line(profile)` function or a `Repl._profile_line(self,
profile)` method) returning
`f"[sasha] profile: {profile.name} ({profile.model} @ {profile.base_url})"`,
used by both the existing `profile` verb handler (replacing its two
duplicated inline f-strings) and the new boot banner.

`Repl.loop()`'s first two statements (before `home = os.path.expanduser("~")`):
```python
def loop(self):
    p = self._current_profile
    self._sink.write_line(<profile-line-helper>(p))
    self._sink.write_line(
        "[sasha] tip: type ?? followed by a question to chat with me directly")
    home = os.path.expanduser("~")
    ...  # rest of loop() unchanged
```

Exact tip text (no interpolation, must match byte-for-byte):
`[sasha] tip: type ?? followed by a question to chat with me directly`

## Constraints
- Modify `src/sasha/repl.py` only — specifically only `Repl.loop()`'s
  first two statements, plus extracting the profile-line format into one
  shared helper used by both the `profile` verb handler and `loop()`. No
  other behavior of the `profile` verb handler changes (same two call
  sites, same output, just no longer duplicating the format string).
- Do not touch: `scripts/`, `docs/superpowers/`, `CLAUDE.md`, `AGENTS.md`,
  `PLAN.md`, `ISSUE-TRACKING.md`, `README.md`, `.gitignore`,
  `pyproject.toml`, `src/sasha/__init__.py`, `src/sasha/config.py`,
  `src/sasha/mutes.py`, `src/sasha/triggers.py`, `src/sasha/session.py`,
  `src/sasha/agent.py`, `src/sasha/llm.py`, `src/sasha/executor.py`,
  `src/sasha/__main__.py`, `src/sasha/prompt.py`, `tests/contract/`.
- No suppression/config toggle, no env var, no persisted "seen it before"
  state — both lines print unconditionally on every `loop()` call.
  Explicitly out of scope for v1 (Salvador's call).
- No changes to `TerminalConfirmer`, `TerminalSink`, or `prompt.py`.
- Greenfield-vs-surgical routing: surgical edit to one existing method
  plus a small refactor-for-reuse in the same file. Dispatch to the
  default model (deepseek-v4-pro); do not use a `SASHA_DISPATCH_MODEL`
  local-model override.

## Acceptance criteria
(encoded in `tests/contract/test_task_10_1_boot_hint.py`)
1. Calling `Repl.loop()` writes the profile line as the *first* call to
   `sink.write_line(...)`, in the exact format
   `[sasha] profile: {name} ({model} @ {base_url})`, using whatever
   `self._current_profile` is at the time `loop()` is called — `test_ac1_*`.
2. The *second* call to `sink.write_line(...)` is the exact tip text
   `[sasha] tip: type ?? followed by a question to chat with me directly`
   — `test_ac2_*`.
3. Both lines are written before the first call to `input_fn` (i.e.
   before any prompt is requested) — `test_ac3_*`.
4. The existing `profile` verb handler's output is byte-identical to
   before this task (both the no-argument and switch-profile cases) —
   confirms the refactor-for-reuse didn't change its behavior —
   `test_ac4_*`.

## Standing instructions
- Your goal: make the full contract suite pass:
  `PYTHONPATH=src python3 -m pytest tests/contract/`.
- NEVER modify files under `tests/contract/` — the gate rejects the
  commit if you do. No existing contract test needs to change for this
  task (verified at spec time: only
  `test_task_6_2_colorization_prompt.py`'s `test_ac4_prompt_*` tests call
  `Repl.loop()`, and they assert on `input_fn.prompts[0]`, never on sink
  writes, so the two new lines don't affect them).
- Deliver exactly ONE new commit on top of the HEAD you started from;
  never amend, rebase, or reset; leave the working tree clean.
- Commit message per AGENTS.md, with trailers:
  `Done: Task 10.1 — <one line>`, `Fixed: ISSUE-027`,
  `Next: Task 10.2 — Phase 10 review (Tier 1, with Salvador)`, and the
  Co-Authored-By signature line from your dispatch prompt.
