# Task 2.2: Session & context building

## Authoritative contract
`docs/superpowers/specs/2026-07-14-sasha-design.md` §5.6 (interface), §7.3
(message building) and §9.4 (trimming) are the complete contract.

## What already exists
- `src/sasha/executor.py` → `ExecResult` (Task 1.3) — consumed, not modified.
- `src/sasha/llm.py` → `ChatResponse`, `ToolCall` (Task 2.1) — consumed, not
  modified.
- `tests/contract/test_task_2_2_session.py` — executable acceptance criteria.

## What's new
- `src/sasha/session.py` — per §5.6:
  - `Session(history_token_budget: int = 12000)` with `note_command(line,
    result)`, `add_user(text)`, `add_assistant(response)`,
    `add_tool_result(call_id, output)`, `build_messages(cwd) -> list[dict]`.

## Pinned semantics (decisions already made — implement exactly these)
1. **Message dict shapes** (OpenAI wire format):
   - system: `{"role": "system", "content": <str>}`
   - user: `{"role": "user", "content": <str>}`
   - assistant (from `add_assistant`): `{"role": "assistant", "content":
     response.text}` plus, when `response.tool_calls` is non-empty,
     `"tool_calls": [{"id": c.id, "type": "function", "function":
     {"name": c.name, "arguments": json.dumps(c.arguments)}} ...]`
     (arguments re-serialized as a JSON **string**).
   - tool (from `add_tool_result`): `{"role": "tool", "tool_call_id":
     call_id, "content": output}`.
2. **System prompt** (static per session): must contain, at minimum, the
   word `confirm` (describing that proposed commands require user
   confirmation and that free-text replies never execute anything), the
   exact output of `platform.platform()`, and name `bash` as the shell.
3. **`build_messages(cwd)` returns** `[system] + trimmed_history +
   [final_user]`, where `final_user` is the LAST stored user message with
   the fresh context block appended to its content (returned copy only —
   stored history is never mutated by `build_messages`). If the last stored
   message is not a user message (or history is empty), a new user message
   containing only the context block is appended instead.
4. **Fresh context block** (§7.3), appended as text:
   - the literal cwd string passed in;
   - the last (up to) 10 `note_command` entries, oldest first, each showing
     the command line and its exit code;
   - the `stderr_text` of the most recent noted command, verbatim, when
     non-empty (it is already tail-capped upstream by the executor).
5. **Token estimate** of a message: `ceil(len(content or "") / 4)` plus, for
   assistant messages, `ceil(len(function.arguments) / 4)` summed over its
   tool_calls.
6. **Trimming** (§9.4): applied to stored history (never the system prompt,
   never the fresh context block) at `build_messages` time: while the summed
   estimate of the history messages (including the final user message's
   base text, excluding the context block) exceeds the budget, drop the
   oldest message; when the dropped message is an assistant message with
   `tool_calls`, also drop every `role:"tool"` message whose `tool_call_id`
   belongs to it (no orphaned tool messages may remain). Trimming
   permanently removes messages from the stored history.
7. `note_command` entries are context, not conversation: they are not
   messages, don't count against the budget, and only surface via the
   context block (keep at most the 10 most recent; older ones may be
   discarded).

## Constraints
- `src/sasha/session.py` imports stdlib + `sasha.executor` + `sasha.llm`
  (types) only. Only `src/sasha/session.py` is created (plus optional tests
  under `tests/impl/`).
- Do not touch: `scripts/`, `docs/superpowers/`, `CLAUDE.md`, `AGENTS.md`,
  `PLAN.md`, `ISSUE-TRACKING.md`, `README.md`, `.gitignore`,
  `pyproject.toml`, `src/sasha/{__init__,__main__,config,mutes,executor,triggers}.py`,
  `tests/contract/`.

## Acceptance criteria
(encoded 1:1 in `tests/contract/test_task_2_2_session.py`)
1. System prompt first; contains `confirm`, `platform.platform()`, `bash` —
   `test_ac1_system_prompt_first_and_grounded`.
2. Final user message carries the context block: cwd, last ≤10 commands
   with exit codes, triggering command's stderr — `test_ac2_*`.
3. Over-budget history drops oldest first; system prompt survives; the
   post-trim history estimate is within budget —
   `test_ac3_trim_oldest_first_within_budget`.
4. Dropping an assistant with tool_calls drops its paired tool results; no
   orphaned tool messages ever — `test_ac4_no_orphaned_tool_messages`.
5. Assistant tool_calls and tool results round-trip to valid OpenAI wire
   dicts (arguments as JSON string, matching `tool_call_id`) —
   `test_ac5_wire_format_round_trip`.

## Standing instructions
- Your goal: make `tests/contract/test_task_2_2_session.py` pass
  (`PYTHONPATH=src python3 -m pytest tests/contract/test_task_2_2_session.py`).
- NEVER modify files under `tests/contract/` — the gate rejects the commit
  if you do. If a contract test seems wrong or unimplementable, stop and
  flag it; do not commit.
- You may add supplementary tests under `tests/impl/`.
- Flag unexpected state; do not create or modify anything outside the files
  listed in "What's new".
- Deliver exactly ONE new commit on top of the HEAD you started from; never
  amend, rebase, or reset; leave the working tree clean.
- Commit message per AGENTS.md, with trailers:
  `Done: Task 2.2 — <one line>`,
  `Next: Task 2.3 — Agent loop with confirmation gate`, and the
  Co-Authored-By signature line from your dispatch prompt.
