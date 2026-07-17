# Task 16.2: agent.py registry-driven dispatch + generalized repetition guard

## Authoritative contract
`docs/superpowers/specs/2026-07-17-multi-tool-registry-design.md` is the
complete design record for this task — read it first, especially §5.2 (the
exact `run_consult` algorithm) and the Decisions log entries about wording
changes. Task 16.1 (already landed) built `src/sasha/tools.py` — this task
wires it in.

## What already exists
- `src/sasha/tools.py` (Task 16.1, landed): `REGISTRY: dict[str, Tool]` with
  keys `run_command`, `write_file`, `read_file`, `list_dir`, `search_files`.
  Each `Tool` has `.schema` (dict, the OpenAI-style function schema),
  `.requires_confirmation` (bool), `.confirmation_summary` (`Callable[[dict],
  str] | None` — `None` for the three auto-approved tools), `.handler`
  (`Callable[[dict, Executor], ToolResult]`), `.repetition_key`
  (`Callable[[dict], object]`). `ToolResult` is a frozen dataclass with one
  field, `.text: str`. Read `src/sasha/tools.py` directly for the exact,
  already-shipped definitions — do not modify it.
- `src/sasha/agent.py`'s current `Confirmer` protocol
  (`ask(command: str, description: str) -> tuple[str, str]`, returning
  `('yes', ''), ('no', ''), ('edit', edited_text), ('reply', text)`) is
  unchanged — every `Tool.confirmation_summary(args)` result is passed as
  the `command` positional argument to `confirmer.ask(...)`, so
  `TerminalConfirmer` (in `repl.py`, not touched by this task) needs no
  changes and its existing byte-exact contract tests
  (`test_task_3_1_repl.py`) stay untouched.
- `src/sasha/session.py`'s `Session.add_tool_result(call_id, output)`,
  `Session.build_messages(cwd)`, `Session.add_assistant(response)`,
  `Session.add_user(text)` — all unchanged, called exactly as `run_consult`
  calls them today.
- `repl.py`'s `_consult` method calls `run_consult(session, client, executor,
  confirmer, out, max_tool_turns=...)` — this signature does not change, so
  `_consult` requires zero edits.

## What's new

Replace the entire contents of `src/sasha/agent.py` with:

```python
from typing import Callable, Protocol
from sasha.llm import ChatClient, ChatResponse
from sasha.session import Session
from sasha.executor import Executor
from sasha.tools import REGISTRY

class Confirmer(Protocol):
    def ask(self, command: str, description: str) -> tuple[str, str]:
        """Returns (verdict, payload):
        ('yes', ''), ('no', ''), ('edit', edited_command), ('reply', text)."""
        ...

def run_consult(
    session: Session,
    client: ChatClient,
    executor: Executor,
    confirmer: Confirmer,
    out: Callable[[str], None],
    max_tool_turns: int = 10,
) -> None:
    previous_key: tuple | None = None
    tool_turns = 0

    while True:
        messages = session.build_messages(executor.cwd)
        schema = [t.schema for t in REGISTRY.values()]
        response = client.chat(messages, tools=schema, on_text=out)
        session.add_assistant(response)

        if not response.tool_calls:
            return

        current_key: tuple | None = None

        for call in response.tool_calls:
            tool = REGISTRY.get(call.name)
            if tool is None:
                session.add_tool_result(call.id, f"Unknown tool: {call.name}")
                continue

            final_arguments = call.arguments
            executed = False

            if tool.requires_confirmation:
                summary = tool.confirmation_summary(call.arguments)
                description = call.arguments.get("description", "")
                verdict, payload = confirmer.ask(summary, description)

                if verdict == "yes":
                    executed = True
                elif verdict == "edit" and call.name == "run_command":
                    final_arguments = {**call.arguments, "command": payload}
                    executed = True
                elif verdict == "edit":
                    result_str = f"User did not run this and replied: {payload}"
                elif verdict == "reply":
                    result_str = f"User did not run this and replied: {payload}"
                else:  # "no"
                    result_str = "User declined this action."

                if executed:
                    result = tool.handler(final_arguments, executor)
                    result_str = result.text
            else:
                result = tool.handler(final_arguments, executor)
                result_str = result.text
                executed = True

            session.add_tool_result(call.id, result_str)

            if executed:
                current_key = (call.name, tool.repetition_key(final_arguments))

        if response.tool_calls:
            tool_turns += 1
            if ((current_key is not None and current_key == previous_key)
                    or tool_turns >= max_tool_turns):
                out("[sasha] stopping: repeated identical action / "
                    "tool-turn limit reached")
                session.add_user("Answer in text now; no further commands will be run.")
                final_messages = session.build_messages(executor.cwd)
                final_response = client.chat(final_messages, tools=[], on_text=out)
                session.add_assistant(final_response)
                return
            if current_key is not None:
                previous_key = current_key
```

That is the complete file — copy it verbatim. `RUN_COMMAND_TOOL` no longer
exists in `agent.py` (it lives in `sasha.tools.REGISTRY["run_command"]
.schema`, shipped in Task 16.1). `run_command`'s own execution and wording
behavior is now driven entirely by the loop above via the registry — do not
add a `run_command`-specific branch anywhere in `run_consult` beyond the
one `elif verdict == "edit" and call.name == "run_command":` line already
in the listing (that line is the *only* place `run_consult` still
special-cases a tool by name).

### Wording changes this task makes (deliberate, not incidental)

Three tool-result strings change from their current wording as a direct,
intended consequence of generalizing from `run_command`-only phrasing to
tool-agnostic phrasing. All three are already reflected in the amended
contract tests (see below) — implement exactly what the loop listing above
produces, which is:

1. `"no"` verdict (any confirmation-gated tool): `"User declined this
   action."` — was `"User declined to run this command."`
2. `"reply"` verdict, and `"edit"` verdict for any tool other than
   `run_command` (any confirmation-gated tool): `"User did not run this and
   replied: {payload}"` — was `"User did not run the command and replied:
   {payload}"`
3. Stop-loop message: `"[sasha] stopping: repeated identical action /
   tool-turn limit reached"` — was `"...repeated identical command..."`

### README.md changes

`README.md` documents user-facing behavior and is checked by
`test_task_3_3_e2e.py`'s `test_ac6_readme_documents_vocabulary` (needle
`"stopping: repeated identical action"`, already amended — see below).
Make exactly these three edits:

1. Replace the `### Confirmation gate (safety guarantees)` section's
   opening paragraph and command block, by inserting a new `### Tools
   available to the LLM` section immediately before it and rewording the
   paragraph beneath `### Confirmation gate (safety guarantees)` itself.
   Replace this block:
   ```markdown
   ### Confirmation gate (safety guarantees)

   When the LLM proposes a command via `run_command`, Sasha never executes it
   automatically. You see a confirmation block:

   ```
   [sasha] wants to run:  <command>
           └ <description>
           [y]es / [n]o / [e]dit — or type a reply
   ```

   - **`y`** or **`yes`** — run the command as proposed.
   - **`n`**, **`no`**, or **empty** — decline.
   - **`e`** or **`edit`** — edit the command before running it; Sasha shows a
     readline-prefilled prompt with the proposed command.
   - **Anything else** — treated as a free-text reply to the LLM. Free-text
     replies never execute anything; they are messages to the model, and the
     model may respond with more text or another proposal.

   If the model doesn't provide a description, `(no description provided)` is
   shown. The gate always applies — no command proposed by the LLM ever runs
   without your explicit confirmation.
   ```
   with exactly this (note the `[sasha] wants to run:` block's `<command>`
   placeholder becomes `<command or action summary>`, and prose says
   "mutating action" instead of "command" where it means any of the two
   confirmation-gated tools):
   ```markdown
   ### Tools available to the LLM

   The LLM can call five tools, each with a permission fixed by its type — not
   configurable, not per-call:

   | Tool | Confirmation | Purpose |
   |---|---|---|
   | `run_command` | Confirms | Propose one bash command to run in your shell. |
   | `write_file` | Confirms | Write content to a file (creates parent directories; overwrites if it exists). Not sandboxed — same unrestricted-but-confirmed model as `run_command`. |
   | `read_file` | Auto-runs | Read a file's contents. Sandboxed to the current directory and below. |
   | `list_dir` | Auto-runs | List a directory's entries. Sandboxed to the current directory and below. |
   | `search_files` | Auto-runs | Regex search over file contents, recursively. Sandboxed to the current directory and below. |

   `read_file`, `list_dir`, and `search_files` run immediately with no prompt —
   a path that resolves outside the current directory is rejected with an error
   message the LLM can see and correct, never a silent no-op. All three cap
   their output (40000 characters for `read_file`, 200 entries for `list_dir`,
   100 matches for `search_files`) and note when they've truncated.

   ### Confirmation gate (safety guarantees)

   When the LLM proposes a mutating action (`run_command` or `write_file`),
   Sasha never executes it automatically. You see a confirmation block:

   ```
   [sasha] wants to run:  <command or action summary>
           └ <description>
           [y]es / [n]o / [e]dit — or type a reply
   ```

   - **`y`** or **`yes`** — run the action as proposed.
   - **`n`**, **`no`**, or **empty** — decline.
   - **`e`** or **`edit`** — for `run_command` only, edit the command before
     running it (Sasha shows a readline-prefilled prompt with the proposed
     command); for `write_file`, treated the same as a free-text reply.
   - **Anything else** — treated as a free-text reply to the LLM. Free-text
     replies never execute anything; they are messages to the model, and the
     model may respond with more text or another proposal.

   If the model doesn't provide a description, `(no description provided)` is
   shown. The gate always applies — no mutating action proposed by the LLM ever
   runs without your explicit confirmation.
   ```
2. Replace the `### Loop detection` section's body:
   ```markdown
   ### Loop detection

   If the agent proposes the same command twice in a row or reaches the maximum
   tool-turn limit (10 by default), Sasha stops the loop:

   ```
   [sasha] stopping: repeated identical command / tool-turn limit reached
   ```
   ```
   with:
   ```markdown
   ### Loop detection

   If the agent's most recently executed tool call is identical to the previous
   turn's (same tool, same key arguments — e.g. the same command, or the same
   `write_file` path and content) or the loop reaches the maximum tool-turn
   limit (10 by default), Sasha stops the loop:

   ```
   [sasha] stopping: repeated identical action / tool-turn limit reached
   ```
   ```
   (The sentence "The agent does one final text-only call so the LLM can
   answer or explain without proposing further commands." immediately after
   this block is unchanged — leave it as-is.)
3. In the "Error vocabulary (reference)" table, change the `Loop stopped`
   row's line from
   `` `[sasha] stopping: repeated identical command / tool-turn limit reached` ``
   to
   `` `[sasha] stopping: repeated identical action / tool-turn limit reached` ``.

No other README.md content changes.

## Constraints
- Modify only `src/sasha/agent.py` and `README.md`. Do not touch
  `src/sasha/tools.py`, `src/sasha/repl.py`, `src/sasha/session.py`,
  `src/sasha/executor.py`, `src/sasha/llm.py`, or anything under `docs/` or
  `tests/`.
- `run_consult`'s public signature does not change:
  `run_consult(session, client, executor, confirmer, out,
  max_tool_turns=10) -> None`. Every existing caller (`repl.py`'s
  `_consult`) must keep working with zero changes.
- `RUN_COMMAND_TOOL` must not exist anywhere in `agent.py` after this task —
  it was Task 16.1's job to move it into `sasha.tools`, already done.
- Greenfield-vs-surgical routing: this task rewrites `agent.py`'s existing
  control flow around an already-shipped dependency (`sasha.tools.REGISTRY`)
  and edits established prose in `README.md`. Per the project's standing
  executor-routing rule, this is a surgical edit — dispatch to the default
  model (deepseek-v4-pro); do not use a `SASHA_DISPATCH_MODEL` local-model
  override.

## Acceptance criteria
(encoded in `tests/contract/test_task_16_2_agent_registry.py`, 12 new
tests, plus amended assertions in `test_task_2_3_agent.py` (`STOP_LINE`,
`test_ac4`, `test_ac5`), `test_task_3_0_agent_hardening.py` (`STOP_LINE`,
`_unknown`'s tool name), and `test_task_3_3_e2e.py` (`test_ac6`'s README
needle) — all already amended by Tier 1 in this same spec commit)

1. The `tools=` schema list sent to the LLM contains all five tools, not
   just `run_command` — `test_ac_schema_sent_includes_all_five_tools`.
2. A stubbed `read_file`/`list_dir`/`search_files` tool call dispatches to
   the real handler (against a `tmp_path`-backed executor), the confirmer is
   never called, and the handler's `ToolResult.text` reaches
   `session.add_tool_result` — `test_ac_read_file_dispatches_to_real_handler`,
   `test_ac_list_dir_dispatches_to_real_handler`,
   `test_ac_search_files_dispatches_to_real_handler`.
3. An unrecognized tool name still produces exactly `"Unknown tool:
   {name}"` and never reaches the confirmer —
   `test_ac_unknown_tool_unchanged` (also still covered by
   `test_task_3_0_agent_hardening.py`'s existing AC1/AC2, amended only in
   the tool name used).
4. `write_file`'s four verdicts behave per the design: `"no"` and `"reply"`
   don't write and produce the shared non-execution wording; `"edit"` is
   treated identically to `"reply"` (not re-executed, unlike `run_command`);
   `"yes"` writes and the confirmer receives a summary containing the path
   and byte count — `test_ac_write_file_no_does_not_write`,
   `test_ac_write_file_edit_treated_as_reply`,
   `test_ac_write_file_reply_does_not_write`,
   `test_ac_write_file_yes_writes_and_confirmer_sees_summary`.
5. The repetition guard fires across two consecutive `write_file` calls with
   identical `(path, content)` even when `description` differs (proving the
   key excludes `description`, matching `Tool.repetition_key`), and does
   *not* fire when `content` differs —
   `test_ac_repetition_guard_fires_across_write_file_calls_ignoring_description`,
   `test_ac_repetition_guard_does_not_fire_on_different_content`.
6. The repetition guard also fires across two consecutive auto-approved
   `read_file` calls with identical arguments (proving the guard now covers
   every executed tool call, not just confirmed ones) —
   `test_ac_repetition_guard_fires_on_identical_auto_approved_calls`.
7. `run_command`'s own confirm/execute/edit/turn-limit behavior from Task
   2.3/3.0 is unchanged except for the three wording changes listed above —
   verified by the amended (not new) assertions in `test_task_2_3_agent.py`
   and `test_task_3_0_agent_hardening.py` staying green.
8. `README.md` documents the new tool set and the updated stop-loop wording
   — verified by `test_task_3_3_e2e.py`'s `test_ac6_readme_documents_vocabulary`
   staying green with its amended needle.

## Standing instructions
- Your goal: make the full contract suite pass:
  `PYTHONPATH=src python3 -m pytest tests/contract/`.
- NEVER modify files under `tests/contract/` — the gate rejects the commit
  if you do. Every amendment needed in `test_task_2_3_agent.py`,
  `test_task_3_0_agent_hardening.py`, and `test_task_3_3_e2e.py` is already
  in your base commit; you don't need to do anything about them, they will
  pass once `agent.py` and `README.md` match this spec.
- Deliver exactly ONE new commit on top of the HEAD you started from; never
  amend, rebase, or reset; leave the working tree clean.
- Commit message per `AGENTS.md`, with trailers: `Done: Task 16.2 — <one
  line>`, `Next: Task 16.3 — Phase 16 review (Tier 1, with Salvador)`, and
  the `Co-Authored-By:` signature line from your dispatch prompt.
