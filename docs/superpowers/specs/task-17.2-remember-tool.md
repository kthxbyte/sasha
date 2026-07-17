# Task 17.2: remember tool (tools.py) + Session's mutable memory block

## Authoritative contract
`docs/superpowers/specs/2026-07-17-project-memory-design.md` is the
complete design record for this task — read it first, especially §4 (the
`Session` memory-block shape) and §5.4 (the `remember` tool). Task 17.1
(already landed) built `src/sasha/memory.py` and
`Executor.last_command`/`.last_result` — this task wires both into the
existing Phase 16 tool registry and gives `Session` somewhere to receive
loaded memory. Wiring the *loading* side (place-shift detection, the
commit hook, the `sasha memory`/`sasha forget` built-ins) is Task 17.3,
not this task — nothing calls `Session.set_memory(...)` yet after this
task lands, and that's expected.

## What already exists
- `src/sasha/memory.py` (Task 17.1, landed): `resolve_git_root(cwd: str)
  -> pathlib.Path | None`, `encode_remember(repo_root: pathlib.Path, note:
  str, last_command: str | None, last_result: ExecResult | None) -> bool`.
  Read `src/sasha/memory.py` directly for the exact, already-shipped
  signatures — do not modify it.
- `src/sasha/executor.py` (Task 17.1, landed): `Executor.last_command:
  str | None` and `Executor.last_result: ExecResult | None`, both
  read-only properties, both `None` until a command has run. Not modified
  by this task.
- `src/sasha/tools.py` (Task 16.1/16.2, landed): the `Tool`/`ToolResult`
  dataclasses, `REGISTRY: dict[str, Tool]` with five existing entries
  (`run_command`, `write_file`, `read_file`, `list_dir`, `search_files`).
  This task adds a sixth entry; every existing entry's schema, handler,
  and behavior is unchanged.
- `src/sasha/session.py` (Task 2.2, landed, untouched since): `Session`
  currently computes `self.system_prompt` **once**, in `__init__`, from
  `self._personality` alone, and stores it as a plain instance attribute.
  `system_prompt_dict()` reads that stored attribute. This task changes
  *how* `system_prompt` is computed (on demand, not once) so a later
  `set_memory()` call can change what it returns — see below.

## What's new

### 1. `src/sasha/tools.py`

Add one import at the top of the file, alongside the existing one:
```python
from sasha import memory
from sasha.executor import Executor
```
(`from sasha import memory` is new; `from sasha.executor import Executor`
is unchanged, shown for placement.)

Add one new handler function, placed after `_search_files_handler` and
before `REGISTRY = {`:
```python
def _remember_handler(args: dict, executor: Executor) -> ToolResult:
    root = memory.resolve_git_root(executor.cwd)
    if root is None:
        return ToolResult(
            "not in a git-tracked directory — nothing to remember into")
    note = args.get("note", "")
    memory.encode_remember(root, note, executor.last_command, executor.last_result)
    return ToolResult(f"remembered: {note}")
```

Add one new entry to `REGISTRY`, after the existing `"search_files"` entry
(order within the dict doesn't matter functionally, but keep it last for
a clean diff):
```python
    "remember": Tool(
        name="remember",
        schema={
            "type": "function",
            "function": {
                "name": "remember",
                "description": (
                    "Save a durable note about this project for future "
                    "sessions — a decision, a conclusion, a fact worth not "
                    "re-discovering. Only takes effect in a git-tracked "
                    "directory; has no effect otherwise. If a command has "
                    "run recently, its exact output is automatically "
                    "attached as a verbatim record."),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "note": {"type": "string",
                                 "description": "one or two sentences: what's worth remembering and why"},
                    },
                    "required": ["note"],
                },
            },
        },
        requires_confirmation=False,
        confirmation_summary=None,
        handler=_remember_handler,
        repetition_key=lambda args: args.get("note", ""),
    ),
```

Nothing else in `tools.py` changes — every existing `Tool` entry, every
existing handler function, `ToolResult`, `Tool`, and `_safe_path` are
byte-identical to today's.

### 2. `src/sasha/session.py`

Replace the file's contents with the following. Every change from today's
file is confined to `__init__` (one new attribute) and the
`system_prompt` handling (goes from a stored attribute to a computed
property, plus the new `set_memory` method) — every other method
(`note_command`, `add_user`, `add_assistant`, `add_tool_result`,
`_estimate_tokens`, `build_messages`, `system_prompt_dict`) is
byte-identical to today's.

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
        self._memory_block: str = ""
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
        if self._memory_block:
            parts.append(
                f"What you remember about this project so far:\n"
                f"{self._memory_block}")
        parts.append(functional)
        return "\n\n".join(parts)

    @property
    def system_prompt(self) -> str:
        return self._build_system_prompt()

    def set_memory(self, text: str) -> None:
        self._memory_block = text

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

### Design points worth calling out explicitly

1. **`system_prompt` goes from a stored attribute to a `@property`.**
   Nothing in the codebase ever assigns to `session.system_prompt` (only
   reads it) — confirmed by grep across `src/` and `tests/` before writing
   this spec — so this is safe. If your own testing somehow needs to
   assign to it, stop and flag it; that would mean something changed
   since this spec was written.
2. **Ordering in `_build_system_prompt` is personality, then memory, then
   the functional/safety text — always in that order, and functional text
   is always last.** This exactly matches the design's rationale:
   personality is identity/tone, memory is project-specific factual
   context, and the safety rules always come last regardless of what
   else is present.
3. **The memory heading text is exactly `"What you remember about this
   project so far:\n"`** — followed immediately by whatever
   `set_memory()` was last given, no additional framing.
4. **`set_memory("")` is equivalent to never having called it** — an
   empty string means "no memory block," not an empty section header.
   This matters for Task 17.3, which calls `set_memory("")` when moving
   into a directory that isn't a git repo.
5. **The `remember` tool is always in `REGISTRY` and always sent to the
   LLM** — it does not vary based on whether the current directory is a
   git repo. The directory check happens inside the handler (returning a
   descriptive `ToolResult` when there's no repo), not by conditionally
   excluding the tool from the schema list. This avoids making
   `agent.py`'s `schema = [t.schema for t in REGISTRY.values()]` line
   context-dependent — `agent.py` itself needs zero changes for this
   task.
6. **This task does not print anything, does not touch `Executor`, and
   does not call `Session.set_memory()` from anywhere.** Detecting "was
   `.sasha/memory/` just created" and announcing it is Task 17.3's job,
   done generically in `repl.py` by checking whether the memory directory
   newly exists — not by threading a notification channel back through
   `agent.py`'s dispatch loop. Do not add one here.

## Constraints
- Modify only `src/sasha/tools.py` and `src/sasha/session.py`. Do not
  touch `src/sasha/memory.py`, `src/sasha/executor.py`,
  `src/sasha/agent.py`, `src/sasha/repl.py`, `README.md`, or anything
  under `docs/`.
- Every existing entry in `tools.py`'s `REGISTRY` (schema, handler,
  `requires_confirmation`, `confirmation_summary`, `repetition_key`) must
  be byte-identical to today's — this task only *adds* the `"remember"`
  key, it doesn't touch the other five.
- Every method on `Session` other than `__init__`, `_build_system_prompt`,
  and the new `system_prompt` property / `set_memory` method must be
  byte-identical to today's — `build_messages`, `note_command`,
  `add_user`, `add_assistant`, `add_tool_result`, `_estimate_tokens`,
  `system_prompt_dict` are unchanged.

## Acceptance criteria
(encoded in `tests/contract/test_task_17_2_remember_tool.py`, 14 tests,
plus two amended assertions — `test_task_16_1_tools.py`'s
`test_ac_registry_has_exactly_five_tools` and
`test_task_16_2_agent_registry.py`'s
`test_ac_schema_sent_includes_all_five_tools`, both already amended by
Tier 1 in this same spec commit to expect six tools instead of five)

1. A `Session` with nothing remembered produces the exact same
   `system_prompt` as before this task existed (regression guard —
   proves the stored-attribute-to-property refactor changed nothing
   observable).
2. `set_memory(text)` changes `system_prompt` (and `system_prompt_dict()`)
   on the *next* read, without needing a new `Session` — it's live, not
   fixed at construction.
3. `set_memory("")` produces the exact same `system_prompt` as never
   calling it at all.
4. With both `personality` and a memory block set, the order is
   personality, then memory (under the `"What you remember about this
   project so far:\n"` heading), then the functional text — always in
   that order.
5. `REGISTRY["remember"]` has `requires_confirmation=False`,
   `confirmation_summary=None`, a schema with exactly one required
   parameter (`note`), and a `repetition_key` equal to the note text.
6. The handler, called with an `executor.cwd` outside any git repository,
   returns a `ToolResult` explaining that and writes nothing to disk.
7. The handler, called inside a git repository, appends a `"remember"`-
   kind entry to `semantic.jsonl` with the exact note text and `pointer:
   null` when `executor.last_command`/`.last_result` are both `None`.
8. The handler, called with `executor.last_command`/`.last_result` both
   set, additionally writes the verbatim episodic log and sets the
   entry's `pointer` to it — verified by reading the log back and finding
   the command, stdout, and stderr all present.
9. The handler resolves the git root fresh from `executor.cwd` — calling
   it from a subdirectory still finds and writes to the repo root's
   `.sasha/memory/`.
10. End-to-end through `agent.py`'s `run_consult`: a stubbed `remember`
    tool call never reaches the confirmer (auto-approved), its result
    text reaches `session.add_tool_result` unchanged, and the entry lands
    in `semantic.jsonl` — proving registry-driven dispatch (Task 16.2)
    needs no changes to support a sixth tool.

## Standing instructions
- Your goal: make `tests/contract/test_task_17_2_remember_tool.py` pass,
  and keep the full contract suite green:
  `PYTHONPATH=src python3 -m pytest tests/contract/`.
- NEVER modify files under `tests/contract/` — the gate rejects the
  commit if you do. (Note: the two amendments to
  `test_task_16_1_tools.py` and `test_task_16_2_agent_registry.py` are
  already in your base commit; you don't need to do anything about them,
  they'll pass once `tools.py` has the sixth entry.)
- Deliver exactly ONE new commit on top of the HEAD you started from;
  never amend, rebase, or reset; leave the working tree clean.
- Commit message per `AGENTS.md`, with trailers: `Done: Task 17.2 — <one
  line>`, `Next: Task 17.3 — repl.py: place-shift detection, commit hook,
  sasha memory/forget built-ins`, and the `Co-Authored-By:` signature
  line from your dispatch prompt.
- Greenfield-vs-surgical routing: this task makes precise additions to
  `tools.py`'s existing registry pattern and a small, well-specified
  refactor to `session.py`'s system-prompt computation — not a rewrite of
  existing control flow, but touching two files whose current behavior
  must be preserved exactly. Dispatch to the default model
  (deepseek-v4-pro); do not use a `SASHA_DISPATCH_MODEL` local-model
  override.
