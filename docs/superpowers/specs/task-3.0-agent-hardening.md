# Task 3.0: Agent loop hardening (tool-turn liveness)

## Authoritative contract
`docs/superpowers/specs/2026-07-14-sasha-design.md` §7.4. This task fixes
ISSUE-012: `run_consult` counts a "tool turn" only when the response carried a
`run_command` call, so a model that keeps emitting only unknown tool names
never reaches `max_tool_turns` and the loop spins forever.

## What already exists
- `src/sasha/agent.py` (Task 2.3) — the ONLY file this task modifies. All of
  `tests/contract/test_task_2_3_agent.py` must stay green.
- `tests/contract/sse_stub.py`, `tests/contract/test_task_3_0_agent_hardening.py`.

## What's new
- In `run_consult`: every chat turn whose response has a non-empty
  `tool_calls` list counts toward `max_tool_turns`, whether or not any call
  was a known tool. Everything else (gate, result strings, identical-command
  detection, final tools-less round, §10 stopping line) is unchanged.

## Fix sketch (authoritative — a previous attempt failed by keeping the stop
## check inside the run_command-only branch)
The current code ends the per-response processing with a block guarded by
`if turn_has_tool_call:` — a flag set only when a `run_command` call was seen.
That guard is the bug: for a response containing only unknown tool names the
flag stays False, so the turn counter and the stop check are both skipped and
the loop re-chats forever.

Replace that entire block so it is guarded by the response itself:

```python
if response.tool_calls:                    # ANY tool-bearing turn counts
    tool_turns += 1
    if ((current_executed_command is not None
            and current_executed_command == previous_command)
            or tool_turns >= max_tool_turns):
        out("[sasha] stopping: repeated identical command / tool-turn limit reached")
        session.add_user("Answer in text now; no further commands will be run.")
        final_messages = session.build_messages(executor.cwd)
        final_response = client.chat(final_messages, tools=[], on_text=out)
        session.add_assistant(final_response)
        return
    if current_executed_command is not None:
        previous_command = current_executed_command
```

The `turn_has_tool_call` flag becomes unused and should be deleted. The stop
check MUST run for every tool-bearing turn — that is the entire fix.

## Constraints
- Modify `src/sasha/agent.py` only (plus optional tests under `tests/impl/`).
- Do not touch: `scripts/`, `docs/superpowers/`, `CLAUDE.md`, `AGENTS.md`,
  `PLAN.md`, `ISSUE-TRACKING.md`, `README.md`, `.gitignore`,
  `pyproject.toml`, any other `src/sasha/*.py`, `tests/contract/`.

## Acceptance criteria
(encoded 1:1 in `tests/contract/test_task_3_0_agent_hardening.py`)
1. Unknown-tool-only turns hit `max_tool_turns`: the loop stops with the §10
   stopping line and one final tools-less round; the executor is never
   called — `test_ac1_unknown_tool_turns_hit_limit`.
2. The confirmer is never asked about unknown tools; each unknown call feeds
   back `Unknown tool: <name>` — `test_ac2_unknown_tool_feedback_and_no_gate`.

## Standing instructions
- Your goal: make `tests/contract/test_task_3_0_agent_hardening.py` pass with
  `tests/contract/test_task_2_3_agent.py` still green
  (`PYTHONPATH=src python3 -m pytest tests/contract/test_task_2_3_agent.py tests/contract/test_task_3_0_agent_hardening.py`).
- NEVER modify files under `tests/contract/` — the gate rejects the commit if
  you do. If a contract test seems wrong or unimplementable, stop and flag
  it; do not commit.
- Deliver exactly ONE new commit on top of the HEAD you started from; never
  amend, rebase, or reset; leave the working tree clean.
- Commit message per AGENTS.md, with trailers:
  `Done: Task 3.0 — <one line>`, `Fixed: ISSUE-012 (unknown-tool turn liveness)`,
  `Next: Task 3.1 — REPL dispatch`, and the Co-Authored-By signature line
  from your dispatch prompt.
