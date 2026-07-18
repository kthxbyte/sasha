# Task 18.2: remember tool's general-memory fallback + Session's second memory slot

## Authoritative contract
`docs/superpowers/specs/2026-07-17-general-memory-design.md` is the
complete design record for this task — read it first, especially §4
(Session's second slot) and §5.2 (the targeting rule). Task 18.1
(already landed) built `memory.py`'s `general_memory_root()`,
`encode_general_remember`, `load_general_memory_block`,
`is_crash_signal`, `encode_crash`, `encode_general_crash`. This task
wires the first two into `remember` and `Session`; the crash trigger,
general-memory loading at startup, and the two-tier `sasha memory`
built-in are Task 18.3, not this task.

## What already exists
- `src/sasha/memory.py` (Task 18.1, landed): `resolve_git_root(cwd) ->
  pathlib.Path | None`, `encode_remember(repo_root, note, last_command,
  last_result) -> bool` (unchanged from Task 17.1),
  `encode_general_remember(note, last_command, last_result) -> bool`
  (new). Read the file directly for exact signatures — not modified by
  this task.
- `src/sasha/tools.py` (Task 16.1/16.2/17.2, landed): the `remember`
  entry in `REGISTRY`, its `_remember_handler`. Every other entry
  (`run_command`, `write_file`, `read_file`, `list_dir`, `search_files`)
  is unchanged.
- `src/sasha/session.py` (Task 2.2/17.2, landed): `Session.__init__`,
  `_build_system_prompt`, `system_prompt` (property), `set_memory`,
  `system_prompt_dict`, and every history/build_messages method. This
  task changes `set_memory`'s name and `_build_system_prompt`'s
  layering; every other method (`note_command`, `add_user`,
  `add_assistant`, `add_tool_result`, `_estimate_tokens`,
  `build_messages`, `system_prompt_dict`) is byte-identical to today's.
- `src/sasha/repl.py` (Task 17.3, landed): calls `self._session.set_memory(...)`
  at three call sites — inside `_handle_forget_builtin` and twice inside
  `_check_place_shift`. **This task renames those three call sites to
  match Session's renamed method** (a mechanical fix, not new logic) —
  it does not add general-memory loading, the crash-trigger hook, or
  the two-tier `sasha memory` display; those are Task 18.3. Without this
  three-line fix, `repl.py`'s already-shipped code would call a method
  that no longer exists the moment `Session.set_memory` is renamed —
  this task's own gate would fail if it were skipped.

## What's new

### 1. `src/sasha/tools.py` — `_remember_handler` falls back to general memory

```python
def _remember_handler(args: dict, executor: Executor) -> ToolResult:
    note = args.get("note", "")
    root = memory.resolve_git_root(executor.cwd)
    if root is not None:
        memory.encode_remember(root, note, executor.last_command, executor.last_result)
    else:
        memory.encode_general_remember(note, executor.last_command, executor.last_result)
    return ToolResult(f"remembered: {note}")
```

The `remember` `Tool` entry's schema `description` also changes — it
currently claims "Only takes effect in a git-tracked directory; has no
effect otherwise," which becomes false. Replace the whole `description`
string with:

```python
                "description": (
                    "Save a durable note for future sessions — a decision, "
                    "a conclusion, a fact worth not re-discovering. Saved "
                    "against the current project if you're in a "
                    "git-tracked directory, or as a general note otherwise. "
                    "If a command has run recently, its exact output is "
                    "automatically attached as a verbatim record."),
```

Nothing else in the `remember` `Tool` entry (`requires_confirmation`,
`confirmation_summary`, `repetition_key`, the `parameters` schema) or
anywhere else in `tools.py` changes.

### 2. `src/sasha/session.py` — full file, in full:

```python
from __future__ import annotations

import dataclasses
import json
import math
import platform
from typing import Any

from sasha.executor import ExecResult
from sasha.llm import ChatResponse, ToolCall

@dataclasses.dataclass
class NoteCommand:
    line: str
    exit_code: int

class Session:
    def __init__(self, history_token_budget: int = 12000,
                 personality: str = "") -> None:
        self.history_token_budget = history_token_budget
        self._personality = personality
        self._general_memory_block: str = ""
        self._project_memory_block: str = ""
        self.history: list[dict[str, Any]] = []  # Store message dicts
        self.notes: list[NoteCommand] = []  # Store note_command entries
        self.last_stderr: str = ""

    def _build_system_prompt(self) -> str:
        functional = (
            f"You are Sasha, an interactive shell wrapper. "
            f"Proposed commands require user confirmation with 'y' or 'e'. "
            f"Free-text replies never execute anything. "
            f"The platform is {platform.platform()}. "
            f"The shell is bash."
        )
        parts = []
        if self._personality:
            parts.append(self._personality)
        if self._general_memory_block:
            parts.append(
                f"What you generally remember (not project-specific):\n"
                f"{self._general_memory_block}")
        if self._project_memory_block:
            parts.append(
                f"What you remember about this project so far:\n"
                f"{self._project_memory_block}")
        parts.append(functional)
        return "\n\n".join(parts)

    @property
    def system_prompt(self) -> str:
        return self._build_system_prompt()

    def set_project_memory(self, text: str) -> None:
        self._project_memory_block = text

    def set_general_memory(self, text: str) -> None:
        self._general_memory_block = text

    def note_command(self, line: str, result: ExecResult) -> None:
        self.notes.append(NoteCommand(line, result.exit_code))
        if len(self.notes) > 10:
            self.notes.pop(0)
        self.last_stderr = result.stderr_text

    def add_user(self, text: str) -> None:
        self.history.append({"role": "user", "content": text})

    def add_assistant(self, response: ChatResponse) -> None:
        msg = {"role": "assistant", "content": response.text}
        if response.tool_calls:
            msg["tool_calls"] = []
            for tc in response.tool_calls:
                msg["tool_calls"].append({
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.name,
                        "arguments": json.dumps(tc.arguments)
                    }
                })
        self.history.append(msg)

    def add_tool_result(self, call_id: str, output: str) -> None:
        self.history.append({"role": "tool", "tool_call_id": call_id, "content": output})

    def _estimate_tokens(self, msg: dict[str, Any]) -> int:
        content = msg.get("content") or ""
        estimate = math.ceil(len(content) / 4)
        if msg.get("role") == "assistant" and "tool_calls" in msg:
            for tc in msg["tool_calls"]:
                args_str = tc["function"]["arguments"]
                estimate += math.ceil(len(args_str) / 4)
        return estimate

    def build_messages(self, cwd: str) -> list[dict[str, Any]]:
        # Build context block
        context_lines = [cwd]
        for note in self.notes:
            context_lines.append(f"{note.line} ({note.exit_code})")

        if self.last_stderr:
            context_lines.append(self.last_stderr)

        context_block = "\n".join(context_lines)

        # Prepare to handle trimming and final user message
        history_to_trim = list(self.history)

        # Determine if the last message is user and if so, define final_user_msg and base_text
        if history_to_trim and history_to_trim[-1].get("role") == "user":
            last_user_msg = history_to_trim.pop()
            base_text = last_user_msg.get("content", "")
            final_user_msg = {
                "role": "user",
                "content": (base_text + "\n" + context_block) if base_text else context_block
            }
        else:
            base_text = ""
            final_user_msg = {"role": "user", "content": context_block}

        def get_current_total_estimate() -> int:
            total = sum(self._estimate_tokens(m) for m in history_to_trim)
            total += math.ceil(len(base_text) / 4)
            return total

        while get_current_total_estimate() > self.history_token_budget and history_to_trim:
            oldest = history_to_trim.pop(0)
            if oldest.get("role") == "assistant" and "tool_calls" in oldest:
                tool_call_ids = {tc["id"] for tc in oldest["tool_calls"]}
                history_to_trim = [m for m in history_to_trim if not (m.get("role") == "tool" and m.get("tool_call_id") in tool_call_ids)]

        messages = [self.system_prompt_dict()] + history_to_trim + [final_user_msg]
        self.notes = []
        self.last_stderr = ""
        return messages

    def system_prompt_dict(self) -> dict[str, Any]:
        return {"role": "system", "content": self.system_prompt}
```

### 3. `src/sasha/repl.py` — rename three call sites only

Change exactly these three lines (inside `_handle_forget_builtin` and
`_check_place_shift`), and nothing else in the file:

```python
        self._session.set_memory("")
```
→ (appears twice, once in each method)
```python
        self._session.set_project_memory("")
```

and:
```python
        self._session.set_memory(block)
```
→
```python
        self._session.set_project_memory(block)
```

Do not add general-memory loading, the crash-trigger check, or any
change to `_handle_memory_builtin`/`_handle_forget_builtin`'s other
logic — those are Task 18.3.

### Design points worth calling out explicitly

1. **Layering order is fixed: personality, then general, then project,
   then the functional/safety text — always in that order.** General
   comes before project because it's the outer, always-present layer;
   functional text is always last regardless of what else is present
   (same invariant Phase 17 already established for personality+project).
2. **`set_project_memory("")` and `set_general_memory("")` each clear
   only their own section** — clearing one never affects the other. This
   is what lets Task 18.3's place-shift handling clear project memory on
   leaving a repo without losing general memory.
3. **The `remember` tool's targeting logic moved the `note = ...` line
   above the `resolve_git_root` call** compared to Task 17.2's original
   handler — purely so `note` is available to both branches without
   repeating `args.get("note", "")`. Not a behavior change.
4. **The `repl.py` rename is scoped tightly on purpose.** It's tempting
   to also add the general-memory loading call here since you're already
   touching `_check_place_shift`'s neighborhood — don't. That's Task
   18.3's job, with its own spec and contract tests; adding it here
   would be undertested scope creep even though it might "just work."

## Constraints
- Modify only `src/sasha/tools.py`, `src/sasha/session.py`, and exactly
  the three renamed lines in `src/sasha/repl.py` described above. Do not
  touch `src/sasha/memory.py`, `src/sasha/executor.py`,
  `src/sasha/agent.py`, `README.md`, or anything under `docs/`. Do not
  add any new logic to `repl.py` beyond the rename — no general-memory
  loading, no crash hook, no built-in changes.
- Every other `Tool` entry in `tools.py`'s `REGISTRY`, and every other
  method on `Session`, must be byte-identical to today's.

## Acceptance criteria
(encoded in `tests/contract/test_task_18_2_remember_general_session.py`,
9 new tests, plus amended assertions in
`tests/contract/test_task_17_2_remember_tool.py` — 6 tests renamed from
`set_memory` to `set_project_memory` calls, 1 test
(`test_ac_remember_outside_git_repo_writes_nothing`, renamed
`test_ac_remember_outside_git_repo_falls_back_to_general_memory`)
rewritten for the new fallback behavior — all already amended by Tier 1
in this same spec commit)

1. `Session.set_general_memory(text)` renders under `"What you generally
   remember (not project-specific):\n"`; `set_project_memory(text)`
   (renamed from `set_memory`) renders under `"What you remember about
   this project so far:\n"`, exactly as Task 17.2 already specified.
2. Either slot alone renders without an empty heading for the other.
3. Both slots present render general before project, in that order,
   with personality first (if set) and the functional text always last.
4. Clearing one slot (`set_project_memory("")`) never clears the other.
5. `remember`'s schema description no longer contains "no effect" —
   the interface Sasha exposes to the LLM matches what it actually does
   now.
6. `remember`'s handler, called outside any git repository, writes to
   general memory (verified against `general_memory_root()` under a
   `tmp_path`-pointed `XDG_CONFIG_HOME`) instead of writing nothing —
   including attaching an episodic log when a command is in scope,
   exactly like the project-scoped path already does.
7. `remember`'s handler, called inside a git repository, still targets
   only that project's memory — general memory is untouched (regression
   guard proving the targeting rule didn't get inverted).
8. The full contract suite passes with `repl.py`'s three renamed call
   sites in place — proving Task 17.3's shipped `_check_place_shift`/
   `_handle_forget_builtin` keep working unmodified in every other
   respect after the rename.

## Standing instructions
- Your goal: make `tests/contract/test_task_18_2_remember_general_session.py`
  pass, and keep the full contract suite green:
  `PYTHONPATH=src python3 -m pytest tests/contract/`.
- NEVER modify files under `tests/contract/` — the gate rejects the
  commit if you do. The amendments to `test_task_17_2_remember_tool.py`
  are already in your base commit; you don't need to do anything about
  them.
- Deliver exactly ONE new commit on top of the HEAD you started from;
  never amend, rebase, or reset; leave the working tree clean.
- Commit message per `AGENTS.md`, with trailers: `Done: Task 18.2 — <one
  line>`, `Next: Task 18.3 — repl.py: load general memory, crash-trigger
  hook, two-tier sasha memory`, and the `Co-Authored-By:` signature line
  from your dispatch prompt.
- Greenfield-vs-surgical routing: this task makes precise, small changes
  to three existing files (a handler's targeting logic, a session's
  memory-slot naming/layering, and a three-line rename in a fourth) —
  surgical, not greenfield. Dispatch to the default model
  (deepseek-v4-pro); do not use a `SASHA_DISPATCH_MODEL` local-model
  override.
