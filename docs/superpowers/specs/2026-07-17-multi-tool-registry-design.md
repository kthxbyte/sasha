# Sasha — Multi-Tool Registry & Per-Tool Permissions Design

**Status:** Draft for review
**Date:** 2026-07-17

## 1. Mission

Today `src/sasha/agent.py` hardcodes exactly one LLM tool —
`run_command`, a bash-command proposal gated by the confirmation prompt.
This design replaces that hardcoding with a small tool registry and adds
four new tools (`write_file`, `read_file`, `list_dir`, `search_files`),
each carrying a fixed-by-type permission (mutating tools confirm,
read-only tools auto-run), so the LLM stops reaching for shell tricks
(`cat`, `echo >`, `ls`, `grep -r`) to do things Sasha can now do
natively — and, as a direct side effect, the repetition/stop-loop guard
that already protects `run_command` gets generalized to cover every
tool, closing the gap ISSUE-032 flagged (repetitive tool-calling loops
reported specifically during file-writing sessions, a class of call the
current guard's raw-command-string comparison never covered).

## 2. Scope

### In scope:
- New module `src/sasha/tools.py`: a `Tool` dataclass and a `REGISTRY:
  dict[str, Tool]` covering all five tools (`run_command` moves here
  unchanged in behavior; four new tools are added).
- `write_file(path, content, description)` — confirmation-gated, no
  path restriction (matches `run_command`'s existing unrestricted-but-
  confirmed model), creates parent directories as needed.
- `read_file(path)`, `list_dir(path=".")`, `search_files(pattern,
  path=".")` — auto-approved (no confirmation prompt), each restricted
  to `executor.cwd` and below via a shared path-sandboxing helper.
- Output size caps on all three read-oriented tools (character cap on
  `read_file`, result-count caps on `list_dir`/`search_files`), each
  with a truncation note appended to the text returned to the LLM.
- `agent.py`'s `run_consult` rewritten to build its tool schema list
  from `REGISTRY` and dispatch by registry lookup instead of the
  current single-tool `if`/`else`.
- Generalizing the repetition/stop-loop guard from a raw command-string
  comparison to a `(tool_name, key)` comparison, applied to every
  executed tool call (confirmed or auto-approved), not just
  `run_command`.
- `TerminalConfirmer` is reused exactly as-is — no changes to its
  rendering or its tested behavior. Each confirmation-gated tool
  produces its own short "action summary" string that stands in for
  `run_command`'s command text in the existing `confirmer.ask(summary,
  description)` call.

### Out of scope / non-goals:
- User-configurable per-tool permissions (e.g. a config.toml knob to
  auto-approve `write_file`, or a mute-store-style override). Salvador's
  call: permission is fixed by tool type (mutating vs. read-only) for
  v1 — a config surface for this is a future issue if it turns out to
  be wanted.
- An `edit_file` (diff/patch-style) tool. `write_file` is a full-content
  overwrite; targeted in-place edits are a plausible future tool, not
  this phase's job.
- Generalizing the "edit" confirmation verdict beyond `run_command`.
  Editing a single-line shell command inline is well-established;
  editing file content through a one-line terminal prompt isn't a good
  interaction, so every other confirmation-gated tool treats "edit" the
  same as "reply" (see §3.3).
- Multi-call-per-turn repetition tracking. The existing guard already
  only tracks a single "most recently executed" slot per LLM turn, even
  though a turn can contain multiple tool calls (`response.tool_calls`
  is already a list). This design widens *what* that single slot
  compares (from a command string to a `(tool_name, key)` tuple) but
  does not change *how many* slots are tracked — a real redesign there
  is bigger than generalizing the existing mechanism and isn't needed
  to address ISSUE-032's reported symptom.
- Actually resolving/closing ISSUE-032. This design generalizes an
  already-proven mechanism to plausibly prevent the reported symptom
  going forward, but ISSUE-032 itself stays open until Salvador confirms
  (or a new transcript proves otherwise) — no reproduced transcript
  exists to verify against.
- Sandboxing `run_command` or `write_file` to `executor.cwd`. Both
  already have (or gain) a human confirmation step before anything
  happens; the sandboxing in this design specifically targets the three
  *auto-approved* tools, where there is no human in the loop to catch
  an unexpected path.

## 3. Key contracts & assumptions

1. **Permission is a fixed property of the tool, not the call.**
   `Tool.requires_confirmation: bool` is set once per tool at
   registration time — there's no per-invocation override. Read-only
   tools (`read_file`, `list_dir`, `search_files`) are always
   auto-approved; mutating tools (`run_command`, `write_file`) always
   confirm.
2. **The confirmer's interface never changes.** `Confirmer.ask(command:
   str, description: str) -> tuple[str, str]` (repl.py's existing
   protocol) is called with a tool-specific "action summary" string in
   place of `command` — from the confirmer's point of view, nothing
   about the call shape is new. `TerminalConfirmer`'s rendering and its
   existing byte-exact contract tests (`test_task_3_1_repl.py`'s AC9)
   are untouched.
3. **"Yes" always executes; every other verdict for every tool except
   `run_command` produces the same non-execution outcome "edit" and
   "reply" already produce today** — the typed/edited text is reported
   back to the LLM as user feedback, nothing runs. `run_command` alone
   keeps its existing "edit re-executes the edited text" behavior.
4. **Auto-approved tools bypass the confirmer entirely** — no
   `confirmer.ask` call happens for `read_file`/`list_dir`/
   `search_files`; their handler runs immediately when the LLM calls
   them.
5. **Path sandboxing is enforced before the handler runs, for the three
   auto-approved tools only.** A path that resolves (after joining with
   `executor.cwd` and normalizing `..`/symlinks) outside `executor.cwd`
   produces a tool-result error string, not an exception and not a
   silent no-op — the LLM sees exactly why the call failed and can
   retry with a corrected path.
6. **The repetition guard's key is tool-specific but always
   comparable.** `run_command`'s key is the exact executed command
   string (unchanged from today — description text is not part of the
   key). `write_file`'s key is `(path, content)`. Each read-only tool's
   key is its full arguments dict. Two consecutive turns whose most
   recently executed tool call share the same `(tool_name, key)` trip
   the existing stop-loop message and forced final text-only turn —
   this is a widening of today's exact mechanism, not new logic.
7. **Output caps degrade gracefully, never raise.** A file over the
   character cap, or a directory/search with more results than the cap,
   returns the capped content/results plus a trailing note (e.g. `"...
   [truncated at 40000 characters]"` / `"... [42 more results not
   shown]"`) — the LLM always gets a valid, bounded tool result.

## 4. Interface

New file `src/sasha/tools.py`:

```python
from __future__ import annotations

import dataclasses
import pathlib
import re
from typing import Callable

from sasha.executor import Executor

_READ_FILE_MAX_CHARS = 40000
_LIST_DIR_MAX_ENTRIES = 200
_SEARCH_MAX_MATCHES = 100


@dataclasses.dataclass(frozen=True)
class ToolResult:
    text: str


@dataclasses.dataclass(frozen=True)
class Tool:
    name: str
    schema: dict
    requires_confirmation: bool
    # Called only for requires_confirmation=True tools, to build the
    # confirmer.ask(summary, description) call's `summary` argument.
    confirmation_summary: Callable[[dict], str] | None
    # Called with the call's final arguments (see §5.2 for what "final"
    # means for run_command's edit case) and the live Executor.
    handler: Callable[[dict, Executor], ToolResult]
    # The tool-specific repetition-guard key, or None if this tool
    # never participates in the repetition guard (none do today, but
    # the hook exists so a future tool can opt out deliberately).
    repetition_key: Callable[[dict], object]


def _safe_path(raw_path: str, cwd: str) -> pathlib.Path | None:
    """Resolve raw_path against cwd; return None if the result escapes
    cwd. See §5.1 for the exact resolution/normalization algorithm."""
    ...


REGISTRY: dict[str, Tool] = {
    "run_command": Tool(...),
    "write_file": Tool(...),
    "read_file": Tool(...),
    "list_dir": Tool(...),
    "search_files": Tool(...),
}
```

Changes to `src/sasha/agent.py` — `RUN_COMMAND_TOOL` is deleted (moved
into `tools.py`'s registry); `run_consult` is rewritten around the
registry (exact algorithm in §5.2), but its own signature
(`run_consult(session, client, executor, confirmer, out,
max_tool_turns=10) -> None`) does not change — every caller
(`repl.py`'s `_consult`) is untouched.

## 5. Mechanism

### 5.1 Path sandboxing

```
def _safe_path(raw_path, cwd):
    candidate = pathlib.Path(cwd) / raw_path   # if raw_path is
                                                # absolute, this yields
                                                # raw_path itself
                                                # (Path's own semantics)
    try:
        resolved = candidate.resolve(strict=False)
        root = pathlib.Path(cwd).resolve(strict=False)
    except OSError:
        return None
    if resolved != root and root not in resolved.parents:
        return None
    return resolved
```
`resolve(strict=False)` normalizes `..` segments and symlinks without
requiring the path to exist (needed for `write_file`, which is exempt
from this check anyway, but `read_file`/`search_files` may be asked
about a path that doesn't exist and should get a clean "not found"
result from the handler, not a sandboxing false-negative). A `None`
return means "outside the sandbox" — every read-only tool's handler
checks for `None` first and returns a `ToolResult` explaining the
rejection (not raising), e.g. `ToolResult("path escapes the current "
"working directory: <raw_path>")`.

### 5.2 `run_consult`'s rewritten dispatch loop

```python
def run_consult(session, client, executor, confirmer, out,
                 max_tool_turns=10):
    previous_key = None  # (tool_name, key) or None
    tool_turns = 0

    while True:
        messages = session.build_messages(executor.cwd)
        schema = [t.schema for t in REGISTRY.values()]
        response = client.chat(messages, tools=schema, on_text=out)
        session.add_assistant(response)

        if not response.tool_calls:
            return

        current_key = None

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
Note the stop message text changes from `"repeated identical command"`
to `"repeated identical action"` — see Decisions log for why this is a
deliberate, not incidental, wording change.

### 5.3 Per-tool schemas, summaries, handlers, and repetition keys

`run_command` (moved from `agent.py`, byte-identical schema to today):
```python
"run_command": Tool(
    name="run_command",
    schema=RUN_COMMAND_SCHEMA,  # identical dict to today's RUN_COMMAND_TOOL
    requires_confirmation=True,
    confirmation_summary=lambda args: args.get("command", ""),
    handler=lambda args, executor: ToolResult(_run_command_result(args, executor)),
    repetition_key=lambda args: args.get("command", ""),
)
```
(`_run_command_result` factors out today's exact `run_consult` body for
executing a command and formatting `"exit code: ...\nstdout:\n...\nstderr:\n..."`
— pure refactor, no behavior change.)

`write_file`:
```python
"write_file": Tool(
    name="write_file",
    schema={
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Write content to a file, creating parent directories as needed. Overwrites the file if it already exists. The user sees the path, size, and your description and must approve before anything is written.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "file path, absolute or relative to the current directory"},
                    "content": {"type": "string", "description": "exact file content"},
                    "description": {"type": "string", "description": "one line: what this does and why"},
                },
                "required": ["path", "content", "description"],
            },
        },
    },
    requires_confirmation=True,
    confirmation_summary=lambda args: f"write_file: {args.get('path', '')} ({len(args.get('content', ''))} bytes)",
    handler=_write_file_handler,
    repetition_key=lambda args: (args.get("path", ""), args.get("content", "")),
)
```
`_write_file_handler(args, executor)`: resolves `path` against
`executor.cwd` (no sandboxing check — write_file is unrestricted, see
§3 contract 5), creates parent directories with `mkdir(parents=True,
exist_ok=True)`, writes `content`, returns `ToolResult(f"wrote {len(content)}
bytes to {path}")` on success or `ToolResult(f"error writing {path}: {exc}")`
on any `OSError` (never raises out of the handler).

`read_file`:
```python
"read_file": Tool(
    name="read_file",
    schema={
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a file's contents. Restricted to the current working directory and below; runs immediately without asking for confirmation.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "file path, absolute or relative to the current directory"},
                },
                "required": ["path"],
            },
        },
    },
    requires_confirmation=False,
    confirmation_summary=None,
    handler=_read_file_handler,
    repetition_key=lambda args: args,
)
```
`_read_file_handler(args, executor)`: calls `_safe_path`; `None` →
`ToolResult("path escapes the current working directory: {path}")`.
Otherwise attempts `resolved.read_text()`; `FileNotFoundError` →
`ToolResult(f"no such file: {path}")`; any other `OSError` →
`ToolResult(f"error reading {path}: {exc}")`; on success, text is
returned as-is if `len(text) <= 40000`, else `text[:40000] + "\n...
[truncated at 40000 characters]"`.

`list_dir`:
```python
"list_dir": Tool(
    name="list_dir",
    schema={
        "type": "function",
        "function": {
            "name": "list_dir",
            "description": "List a directory's entries (names only, one per line, directories suffixed with '/'). Restricted to the current working directory and below; runs immediately without asking for confirmation.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "directory path, absolute or relative to the current directory; defaults to the current directory"},
                },
                "required": [],
            },
        },
    },
    requires_confirmation=False,
    confirmation_summary=None,
    handler=_list_dir_handler,
    repetition_key=lambda args: args,
)
```
`_list_dir_handler(args, executor)`: `path = args.get("path", ".")`;
`_safe_path` check same as `read_file`; not-a-directory or missing →
descriptive `ToolResult` error, never raises; otherwise
`sorted(resolved.iterdir())`, each entry rendered as `name` or `name/`
for directories, joined with `"\n"`; if more than 200 entries, only the
first 200 (sorted) are shown plus a trailing `"... [N more entries not
shown]"` line.

`search_files`:
```python
"search_files": Tool(
    name="search_files",
    schema={
        "type": "function",
        "function": {
            "name": "search_files",
            "description": "Search file contents for a regex pattern under a directory, recursively. Returns matching 'path:line: text' lines. Restricted to the current working directory and below; runs immediately without asking for confirmation.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "regex pattern to search for"},
                    "path": {"type": "string", "description": "directory to search under, absolute or relative to the current directory; defaults to the current directory"},
                },
                "required": ["pattern"],
            },
        },
    },
    requires_confirmation=False,
    confirmation_summary=None,
    handler=_search_files_handler,
    repetition_key=lambda args: args,
)
```
`_search_files_handler(args, executor)`: `path = args.get("path", ".")`;
`_safe_path` check same as above; invalid regex in `pattern` →
`ToolResult(f"invalid pattern: {exc}")` (never raises); otherwise walks
`resolved` recursively with `pathlib.Path.rglob("*")`, skipping
directories and any file that raises `UnicodeDecodeError`/`OSError` on
read (binary files are silently skipped, not errors — matches real
`grep`'s default behavior), collecting up to 100 `f"{relpath}:{lineno}:
{line}"` matches; if the walk produces more than 100 matches total, the
first 100 (file-then-line order) are returned plus a trailing `"... [N
more matches not shown]"` line.

## 6. Decisions log

- **Decision:** Permission (confirm vs. auto-approve) is fixed per tool
  type, not user-configurable in v1. **Rationale:** Salvador's call —
  keeps this phase's scope to the registry + tool set; a config surface
  is a plausible future issue if wanted, not assumed needed.
- **Decision:** Read tools (`read_file`, `list_dir`, `search_files`) are
  sandboxed to `executor.cwd` and below; `write_file` is not.
  **Rationale:** Salvador's call — auto-approval removes the human
  checkpoint that would otherwise catch an unexpected path, so the
  read-only tools need their own guard; `write_file` keeps a human in
  the loop (the confirmation prompt), matching `run_command`'s existing
  unrestricted-but-confirmed precedent exactly.
- **Decision:** `search_files` is content-grep only; no separate
  filename-glob tool this phase. **Rationale:** Salvador's call —
  `list_dir` already covers filename discovery reasonably well; content
  search is the harder gap `run_command`'s `grep -r` fallback otherwise
  fills.
- **Decision:** "Edit" stays `run_command`-specific; every other
  confirmation-gated tool treats "edit" identically to "reply."
  **Rationale:** Editing a single-line shell command through a
  single-line terminal prompt is an established, sensible interaction;
  editing file content the same way isn't — rather than build a
  half-useful generic edit path, "edit" for `write_file` degrades to
  "tell the LLM what you'd rather it do," matching "reply"'s existing
  semantics exactly.
- **Decision:** Repetition guard generalizes to `(tool_name, key)` and
  now covers auto-approved tool calls too, not just confirmed ones.
  **Rationale:** Directly targets ISSUE-032's reported symptom
  (repetition during file-writing/tool-calling sessions) by widening an
  already-proven mechanism rather than inventing new detection logic —
  low risk, and closes an actual gap (today's guard silently doesn't
  apply to anything except `run_command`).
- **Decision:** The stop message's wording changes from "repeated
  identical command" to "repeated identical action." **Rationale:**
  the message is user-facing and byte-tested
  (`test_task_5_1_drop_127_preamble.py` / related contract tests may
  reference it) — "command" would be actively misleading once the guard
  also fires for `write_file`/`read_file`/etc. calls. Contract tests
  referencing the old string are Tier 1's responsibility to amend at
  task-spec time, not the implementer's.
- **Decision:** `TerminalConfirmer` is not touched — every
  confirmation-gated tool adapts itself to the existing
  `ask(command, description)` shape via a per-tool `confirmation_summary`
  function, rather than generalizing the confirmer's own interface.
  **Rationale:** `TerminalConfirmer`'s rendering is byte-exact tested;
  reusing its existing two-string contract avoids touching a
  well-tested, working surface for a feature that doesn't actually need
  a richer confirmation UI.
- **Decision:** Output caps (40000 chars for `read_file`, 200 entries
  for `list_dir`, 100 matches for `search_files`) are flat constants,
  not configurable. **Rationale:** Matches the project's existing
  convention (`personality.py`'s flat 8000-character cap) — no
  tokenizer dependency, no new config surface, bounded LLM context
  usage by construction.
- **Decision:** ISSUE-032 itself is not closed by this design.
  **Rationale:** No reproduced transcript exists to verify the fix
  against — this design plausibly prevents the reported symptom class
  by closing an actual, identifiable gap in the existing mechanism, but
  "plausibly prevents" isn't the same as "confirmed fixed." The issue
  stays open for Salvador to close (or reopen with a new transcript)
  after using the shipped feature.

## 7. Testing strategy

`tools.py` contract tests, per tool: schema shape (name, required
params match the design exactly); `_safe_path` (relative path inside
cwd → resolved path; `..` escaping cwd → `None`; absolute path inside
cwd → resolved path; absolute path outside cwd → `None`; symlink
escaping cwd → `None`); each handler's success case, not-found/error
case (no exception raised, descriptive `ToolResult`), and cap/truncation
case (a file over 40000 chars, a directory with 201+ entries, a search
with 101+ matches) exercised against `tmp_path` fixtures, never the real
filesystem.

`agent.py` contract tests (extending `test_task_2_3_agent.py`'s existing
SSE-stub-driven style): registry-driven schema (the `tools=` payload
sent to the stub contains all five tool schemas, not just
`run_command`); dispatch by name for a new tool (a stubbed tool call to
`read_file` invokes the real handler against a `tmp_path` executor cwd
and the result reaches `session.add_tool_result`); unknown tool name
still produces `"Unknown tool: ..."` unchanged; confirmation-gated
non-run_command tool ("no"/"edit"/"reply" verdicts each produce a
non-executing result string, "yes" executes); auto-approved tool never
calls the confirmer (a spy confirmer asserts zero calls); repetition
guard fires across two turns with identical `write_file(path, content)`
calls even though `description` differs between turns (proving the key
excludes `description`); repetition guard does *not* fire when
`write_file` is followed by `write_file` with a different `content`;
the stop message reads "repeated identical action."

Existing `test_task_2_3_agent.py` / `test_task_3_0_agent_hardening.py`
assertions referencing `run_command`'s exact confirmation/execution
behavior and the tool-turn-limit path must keep passing unmodified —
this is a pure widening of the mechanism they already pin, not a
behavior change for `run_command` itself. Any assertion pinning the
literal string "repeated identical command" is a known, expected
amendment Tier 1 makes at task-spec time (per the Decisions log above),
not something the executor touches.
