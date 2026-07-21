# Task 25.1: Raw-typed command activity awareness (ISSUE-044)

## Authoritative contract
`docs/superpowers/specs/2026-07-21-issue-044-raw-command-activity-design.md`
(all sections) is the complete contract for this task. This task extends
`tool_log`'s persistent audit trail (ISSUE-042) to raw commands typed
directly at the sasha prompt, and enriches `Session`'s recency window
(ISSUE-031) with per-entry stderr.

**One refinement to the design doc, found while spec'ing this task (not
an implementation surprise — decided now, before dispatch):** §4.1's
example used `result_text = result.stderr_text` directly. That silently
drops the exit code from the persistent record — unlike LLM-invoked
`run_command` entries, whose `result` text always embeds `"exit code:
N\n..."` (from `tools.py`'s `_run_command_result`). Since exit code is
core troubleshooting value (the whole point of this feature), the fix
sketch below formats it the same way, minus the `stdout:` section (never
captured for raw-typed commands, so it's omitted rather than shown
always-empty): `f"exit code: {result.exit_code}\nstderr:\n{result.stderr_text}"`.

## What already exists
- `src/sasha/tool_log.py` (Task 22.1/22.2) — `log_call()`,
  `check_and_maybe_compact_async()`. **Do not modify this file at all** —
  this task only adds a second call site in `repl.py`.
- `src/sasha/safe_commands.py` (Task 21.1/23.1) —
  `touches_sensitive_path(command, executor) -> bool`. **Do not modify.**
- `src/sasha/repl.py` — the raw-command path (the `# e. otherwise` branch
  in `_dispatch`/similar, ending in `result = self._executor.run(stripped)`
  through `decide(...)`). **Bind to this exactly as it exists — only the
  block described below is inserted, nothing else in this path changes:
  `_check_place_shift`/`_check_commit`/`_check_crash`/
  `_check_memory_created`/`decide(...)` are all untouched.**
- `src/sasha/session.py` (Task 2.2, ISSUE-031) — `NoteCommand`,
  `Session.note_command`, `Session.build_messages`. **Bind to the
  overall shape (trimming, system prompt, tool-call history) — only
  `NoteCommand`, `note_command`, the notes-cap, `last_stderr`, and the
  context-block construction inside `build_messages` change.**
- `tests/contract/test_task_14_1_consume_notes.py` — MUST stay green
  unmodified (verified during spec-writing: none of its assertions
  depend on the notes cap or on `last_stderr` as a named attribute, only
  on the observable behavior of what appears/disappears in context,
  which this task's change preserves).
- `tests/contract/test_task_2_2_session.py`,
  `tests/contract/test_task_3_1_repl.py`,
  `tests/contract/test_task_17_3_repl_memory.py`,
  `tests/contract/test_task_18_3_repl_general_crash.py`,
  `tests/contract/test_task_19_3_repl_bootstrap_wiring.py`,
  `tests/contract/test_task_4_1_job_control.py`,
  `tests/contract/test_task_5_1_drop_127_preamble.py`,
  `tests/contract/test_task_5_2_thinking_indicator.py` — **all eight
  need amendment, verified directly while spec'ing this task (see
  "Pre-existing contract tests amended" below)** — do not touch any
  other file under `tests/contract/`.

## What's new
1. **`src/sasha/repl.py`** — one new block in the raw-command path,
   right after `result = self._executor.run(stripped)` and before
   `self._session.note_command(stripped, result)`: builds `result_text`/
   `result_omitted_reason` (withholding via `touches_sensitive_path`,
   exactly like `agent.py` already does for LLM-invoked calls) and calls
   `tool_log.log_call(...)` with a new verdict, `"direct"`, followed by
   `tool_log.check_and_maybe_compact_async(self._sink.write_line)`.
2. **`src/sasha/session.py`** — `NoteCommand` gains `stderr_text: str =
   ""`; `note_command` stores it; `Session.last_stderr` is removed
   entirely; the notes cap drops from 10 to 5; `build_messages`'s
   context-block construction folds each note's `stderr_text` inline
   instead of appending a separate `last_stderr` line.

## Fix sketch (authoritative — verified directly)

Verified end to end in a scratch git worktree — full contract suite
green, including all new ACs and the five amended pre-existing test
files, all still asserting real, non-vacuous behavior — before being
committed as authoritative.

**`src/sasha/repl.py`** — add `import uuid` and `from sasha import
safe_commands` to the existing imports (it already has `from sasha
import tool_log`). Replace:
```python
        result = self._executor.run(stripped)
        self._session.note_command(stripped, result)
```
with:
```python
        result = self._executor.run(stripped)

        result_text = f"exit code: {result.exit_code}\nstderr:\n{result.stderr_text}"
        result_omitted_reason = None
        if safe_commands.touches_sensitive_path(stripped, self._executor):
            result_text = None
            result_omitted_reason = "sensitive_path"

        tool_log.log_call(
            tool_name="run_command",
            call_id=str(uuid.uuid4()),
            arguments={"command": stripped},
            executed_command=None,
            cwd=self._executor.cwd,
            verdict="direct",
            executed=True,
            result_text=result_text,
            result_omitted_reason=result_omitted_reason,
        )
        tool_log.check_and_maybe_compact_async(self._sink.write_line)

        self._session.note_command(stripped, result)
```
Nothing else in this method changes.

**`src/sasha/session.py`** — exact edits:
```python
@dataclasses.dataclass
class NoteCommand:
    line: str
    exit_code: int
    stderr_text: str = ""
```
In `Session.__init__`, delete `self.last_stderr: str = ""`.

Replace `note_command`:
```python
    def note_command(self, line: str, result: ExecResult) -> None:
        self.notes.append(NoteCommand(line, result.exit_code, result.stderr_text))
        if len(self.notes) > 5:
            self.notes.pop(0)
```

In `build_messages`, replace:
```python
        context_lines = [cwd]
        for note in self.notes:
            context_lines.append(f"{note.line} ({note.exit_code})")

        if self.last_stderr:
            context_lines.append(self.last_stderr)

        context_block = "\n".join(context_lines)
```
with:
```python
        context_lines = [cwd]
        for note in self.notes:
            line_part = f"{note.line} ({note.exit_code})"
            if note.stderr_text:
                line_part += f"\n{note.stderr_text}"
            context_lines.append(line_part)

        context_block = "\n".join(context_lines)
```
and remove the later `self.last_stderr = ""` line (near `self.notes =
[]` at the end of `build_messages`) — only `self.notes = []` remains.

## Pre-existing contract tests amended (Tier 1, this same commit)

Both amendments are mechanical consequences of a deliberate, documented
change (the notes cap dropping to 5; a new call site that any duck-typed
executor reaching the raw-command path must now support) — **the
implementer must not touch any of these, or any other file under
`tests/contract/`:**
- `tests/contract/test_task_2_2_session.py::test_ac2_context_block_in_final_user_message`
  — was asserting "only the last 10 [of 12] surface"; now asserts only
  the last 5 surface (commands 8-12), and 1-7 explicitly do not.
- `tests/contract/test_task_3_1_repl.py`'s `SpyExecutor`,
  `tests/contract/test_task_17_3_repl_memory.py`'s `FakeExecutor`,
  `tests/contract/test_task_18_3_repl_general_crash.py`'s `FakeExecutor`,
  `tests/contract/test_task_19_3_repl_bootstrap_wiring.py`'s
  `FakeExecutor`, `tests/contract/test_task_4_1_job_control.py`'s
  `_SpyExecutor`, `tests/contract/test_task_5_1_drop_127_preamble.py`'s
  `SpyExecutor`, `tests/contract/test_task_5_2_thinking_indicator.py`'s
  `SpyExecutor` — each gains a `sensitive_path_patterns = ()` class
  attribute (same fix already applied to other duck-typed executors
  during Task 21.1/22.1). All seven were found empirically, not
  predicted up front — the first full-suite run in the scratch worktree
  caught three more than the four found by grepping for `run_once`/
  `.loop()` beforehand (`test_task_4_1_job_control.py`,
  `test_task_5_1_drop_127_preamble.py`, `test_task_5_2_thinking_indicator.py`),
  which is exactly why this task's own gate is the full suite, not a
  hand-picked subset. Every one of these files exercises the raw-command
  path directly, which now calls
  `safe_commands.touches_sensitive_path(stripped, self._executor)` for
  every raw command, requiring this attribute to exist.

## Constraints
- Modify `src/sasha/repl.py`, `src/sasha/session.py` only.
- Do not touch `src/sasha/tool_log.py`, `src/sasha/safe_commands.py`,
  `src/sasha/agent.py`, `src/sasha/executor.py`, `src/sasha/config.py`,
  `src/sasha/tools.py`, `src/sasha/__main__.py` — nothing in this task's
  contract exercises them, and none of them change.
- Do not turn on `capture_stdout` for the raw-command path — deliberately
  out of scope (design doc §2).
- Do not add a "files touched" tracker or any new LLM-context privacy
  filtering — both deliberately out of scope (design doc §2).
- **Executor routing (per CLAUDE.md's standing rule): this task edits
  existing control flow in `repl.py`/`session.py` that already works
  (surgical). Dispatch to the default model (deepseek-v4-pro) — do not
  launch under a local-model `SASHA_DISPATCH_MODEL` override.**

## Acceptance criteria
(encoded 1:1 in `tests/contract/test_task_25_1_raw_command_activity.py`)
1. A raw-typed command (success and failure) produces a `tool_log` entry
   with `verdict="direct"`, `executed=True`, `arguments={"command":
   ...}`, and `result` formatted as `"exit code: N\nstderr:\n..."` —
   `test_ac1_raw_command_logged_with_direct_verdict`.
2. A raw-typed command whose text touches a sensitive path gets
   `result=None`, `result_omitted_reason="sensitive_path"` in the log
   entry — `test_ac2_raw_command_sensitive_path_withheld`.
3. `check_and_maybe_compact_async` is invoked after logging a raw
   command (verified with a spy in place of the real function) —
   `test_ac3_raw_command_triggers_compaction_check`.
4. `Session.notes` caps at 5 (not 10); each note carries its own
   `stderr_text`, included inline in the context block; notes are
   cleared after `build_messages()` returns, matching ISSUE-031's
   existing lifecycle — `test_ac4_notes_cap_at_five_with_stderr_per_note`.
5. `Session.last_stderr` no longer exists as an attribute —
   `test_ac5_last_stderr_attribute_removed`.
6. `tests/contract/test_task_14_1_consume_notes.py` stays green,
   unmodified — no new test; verified as part of this task's own gate.

## Standing instructions
- Your goal: make
  `tests/contract/test_task_25_1_raw_command_activity.py` pass, with the
  full existing suite green: `bash scripts/run-tests.sh`.
- NEVER modify files under `tests/contract/` — the gate rejects the
  commit if you do. If a contract test seems wrong or unimplementable,
  stop and flag it; do not commit.
- Deliver exactly ONE new commit on top of the HEAD you started from;
  never amend, rebase, or reset; leave the working tree clean.
- Commit message per AGENTS.md, with trailers: `Done: Task 25.1 — <one
  line>`, `Fixed: ISSUE-044`,
  `Next: Task 25.2 — Phase 25 review (Tier 1, with Salvador)`, and the
  Co-Authored-By signature line from your dispatch prompt.
