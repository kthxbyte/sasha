# Task 2.3: Agent loop with confirmation gate

## Authoritative contract
`docs/superpowers/specs/2026-07-14-sasha-design.md` §5.5 (interface), §7.2
(tool schema), §7.4 (loop algorithm) and §10 (loop-stopped line) are the
complete contract. Design contract §3.2 is absolute: NOTHING executes
without a `yes`/`edit` verdict; free-text replies never execute anything.

## What already exists
- `src/sasha/llm.py` → `ChatClient`, `ChatResponse`, `ChatError` (Task 2.1).
- `src/sasha/session.py` → `Session` (Task 2.2).
- `src/sasha/executor.py` → `Executor`, `ExecResult` (Tasks 1.3/1.4).
  All consumed, none modified. The agent uses the executor ONLY via
  `.run(command, capture_stdout=True)` and `.cwd` (duck-typed — contract
  tests drive it with a spy).
- `tests/contract/sse_stub.py` — scriptable SSE server (never modify).
- `tests/contract/test_task_2_3_agent.py` — executable acceptance criteria.

## What's new
- `src/sasha/agent.py` — per §5.5:
  - `RUN_COMMAND_TOOL: dict` — module-level constant, EXACTLY the §7.2 JSON
    schema.
  - `Confirmer` — `typing.Protocol` with
    `ask(command: str, description: str) -> tuple[str, str]` returning
    `('yes','')`, `('no','')`, `('edit', edited_command)`, or
    `('reply', text)`.
  - `run_consult(session, client, executor, confirmer, out,
    max_tool_turns: int = 10) -> None`.

## Pinned semantics (decisions already made — implement exactly these)
1. **Round**: `response = client.chat(session.build_messages(executor.cwd),
   tools=[RUN_COMMAND_TOOL], on_text=out)`, then
   `session.add_assistant(response)` ALWAYS (text and tool_calls both enter
   the session before any tool handling).
2. **No tool calls** → return (the streamed text was already printed via
   `on_text`).
3. **Per tool call**, in order:
   - name ≠ `run_command` → tool result `Unknown tool: <name>` (no
     confirmation, nothing executes);
   - else `command = arguments.get("command", "")`, `description =
     arguments.get("description", "")`; `verdict, payload =
     confirmer.ask(command, description)`;
   - `yes` → `result = executor.run(command, capture_stdout=True)`;
   - `edit` → `result = executor.run(payload, capture_stdout=True)`;
   - for yes/edit the tool result string is exactly:
     `f"exit code: {result.exit_code}\nstdout:\n{result.stdout_text}\nstderr:\n{result.stderr_text}"`;
   - `no` → tool result exactly `User declined to run this command.`;
   - `reply` → tool result exactly
     `User did not run the command and replied: {text}`;
   - any other verdict string → treat as `no`;
   - each tool result: `session.add_tool_result(call.id, result_str)`.
4. **Loop detection**: a "turn" is one `chat()` whose response carried tool
   calls. Stop conditions, checked after processing a turn's tool calls:
   (a) the command actually executed this turn (post-edit) is identical to
   the command actually executed in the previous turn, or (b) the number of
   tool turns reached `max_tool_turns`. On stop:
   - `out()` receives exactly the §10 line:
     `[sasha] stopping: repeated identical command / tool-turn limit reached`
   - `session.add_user("Answer in text now; no further commands will be run.")`
   - one final `client.chat(session.build_messages(executor.cwd), tools=[],
     on_text=out)` + `session.add_assistant(...)`, then return.
5. **`ChatError` propagates** to the caller — the REPL owns the §10
   unreachable message; the agent must not catch it (and must not leave a
   half-recorded turn: nothing special needed beyond the ordering above).
6. No printing except via `out`; no input except via `confirmer`.

## Constraints
- `src/sasha/agent.py` imports stdlib + `sasha.llm` + `sasha.session` +
  `sasha.executor` (types) only. Only `src/sasha/agent.py` is created (plus
  optional tests under `tests/impl/`).
- Do not touch: `scripts/`, `docs/superpowers/`, `CLAUDE.md`, `AGENTS.md`,
  `PLAN.md`, `ISSUE-TRACKING.md`, `README.md`, `.gitignore`,
  `pyproject.toml`, `src/sasha/{__init__,__main__,config,mutes,executor,triggers}.py`,
  `tests/contract/`.

## Acceptance criteria
(encoded 1:1 in `tests/contract/test_task_2_3_agent.py`; driven by the SSE
stub, a scripted Confirmer, and a spy executor)
1. Text-only response: streamed via `out`, stored in session, executor
   untouched — `test_ac1_text_only_runs_nothing`.
2. `('yes','')` → `executor.run(command, capture_stdout=True)`; tool result
   carries exit code, stdout tail, stderr tail —
   `test_ac2_yes_executes_and_reports`.
3. `('edit', edited)` → the edited string runs — `test_ac3_edit_runs_edited`.
4. `('no','')` → nothing executes; declined message fed back —
   `test_ac4_no_executes_nothing`.
5. `('reply', text)` → nothing executes; reply reaches the model verbatim —
   `test_ac5_reply_never_executes`.
6. Two consecutive identical approved commands → loop stops with one final
   tools-less round and the §10 stopping line —
   `test_ac6_identical_command_stops_loop`.
7. `max_tool_turns` reached → same final-round behavior —
   `test_ac7_turn_limit_stops_loop`.
8. `RUN_COMMAND_TOOL` equals the §7.2 schema exactly —
   `test_ac8_tool_schema_exact`.
9. `ChatError` propagates out of `run_consult` —
   `test_ac9_chaterror_propagates`.

## Standing instructions
- Your goal: make `tests/contract/test_task_2_3_agent.py` pass
  (`PYTHONPATH=src python3 -m pytest tests/contract/test_task_2_3_agent.py`).
- NEVER modify files under `tests/contract/` (including `sse_stub.py`) — the
  gate rejects the commit if you do. If a contract test seems wrong or
  unimplementable, stop and flag it; do not commit.
- You may add supplementary tests under `tests/impl/`.
- Flag unexpected state; do not create or modify anything outside the files
  listed in "What's new".
- Deliver exactly ONE new commit on top of the HEAD you started from; never
  amend, rebase, or reset; leave the working tree clean.
- Commit message per AGENTS.md, with trailers:
  `Done: Task 2.3 — <one line>`,
  `Next: Task 3.1 — REPL dispatch`, and the Co-Authored-By signature line
  from your dispatch prompt.
