# Task 22.1: Tool-call logging — core write path

## Authoritative contract
`docs/superpowers/specs/2026-07-21-tool-call-logging-design.md` §4.1
(where it lives, the one hook point), §4.2 (entry schema), §6 (error
handling) are the complete contract for this task. This task implements
the write path only: every tool call gets one JSONL entry, including the
sensitive-path privacy carve-out for `run_command`. Compaction (§4.3, the
`[tool_log]` config section, and the `sasha compact-log` command) is
**out of scope for this task** — it's Task 22.2, dispatched only after
this one lands and is reviewed.

## What already exists
- `src/sasha/agent.py` (Task 16.2/21.1) — `run_consult`'s dispatch loop.
  **Bind to the existing confirm/auto-approve/execute branching — do not
  restructure it.** This task only adds tracking variables and one
  logging call per branch; the confirmation/execution logic itself,
  `session.add_tool_result`, and the repetition-guard code below it are
  unchanged.
- `src/sasha/safe_commands.py` (Task 21.1) — `requires_confirmation(args,
  executor)`, `_tokenize`, `_split_segments`, `_normalize`,
  `_ALLOWLISTED_VERBS`, `_OPERATORS`. **The full existing contract suite
  (`tests/contract/test_task_21_1_safe_command_auto_approval.py`) must
  stay green, unmodified, proving the refactor below doesn't change
  `requires_confirmation`'s behavior.**
- `src/sasha/executor.py` (Task 1.3/21.1) — `Executor.cwd`,
  `Executor.sensitive_path_patterns`. Not modified by this task.
- `tests/contract/test_task_2_3_agent.py`,
  `tests/contract/test_task_3_0_agent_hardening.py`,
  `tests/contract/test_task_16_2_agent_registry.py`,
  `tests/contract/test_task_3_4_repl_streaming.py` — all exercise
  `run_consult` with duck-typed executors/confirmers. **None of these
  need amending** — this task adds no new required parameter to
  `run_consult`'s signature, only new lines inside its body, and the new
  `tests/contract/conftest.py` (see "What's new") isolates every test's
  state directory automatically, so none of them start writing to a real
  `~/.local/state/sasha/tool_log.jsonl` on the machine running the suite.

## What's new
1. **`src/sasha/tool_log.py`** (new module) — one public function,
   `log_call(...)`, that appends one JSON line per call to
   `<state_dir>/tool_log.jsonl`, where `<state_dir>` is resolved the same
   way `__main__.py`'s `_state_dir()` already does
   (`XDG_STATE_HOME`/`~/.local/state/sasha`), computed fresh on every
   call — **no new parameter threading through `run_consult` or
   `__main__.py`**, mirroring how `memory.py`'s functions resolve their
   own paths without being constructor-injected.
2. **`src/sasha/safe_commands.py`** gains one new exported function,
   `touches_sensitive_path(command: str, executor) -> bool`, extracted
   from `requires_confirmation`'s existing final path-matching step. Pure
   extraction — `requires_confirmation`'s body changes to call it, but
   returns identically for every input.
3. **`src/sasha/agent.py`**'s dispatch loop calls `tool_log.log_call(...)`
   once per tool call, for every branch (yes/no/edit/reply/auto-approved),
   after `result_str`/`executed` are finalized and before
   `session.add_tool_result(...)`.
4. **`tests/contract/conftest.py`** (new) — a session-wide autouse
   fixture isolating `XDG_STATE_HOME` per test (see "Constraints" for
   exactly what it must and must not do).

## `tool_log.py` — exact contract

```python
def log_call(*, tool_name: str, call_id: str, arguments: dict,
             executed_command: str | None, cwd: str, verdict: str,
             executed: bool, result_text: str | None,
             result_omitted_reason: str | None) -> None:
```

- Writes one JSON object as a single line (`json.dumps(...) + "\n"`),
  appended to `<state_dir>/tool_log.jsonl`, with these keys:
  `ts` (UTC ISO-8601 with milliseconds, e.g.
  `"2026-07-21T14:32:10.481Z"`), `session_id` (see below), `cwd`, `tool`
  (`tool_name`), `call_id`, `arguments`, `executed_command`, `verdict`,
  `executed`, `result` (`result_text`), `result_omitted_reason`.
- `session_id`: one `uuid.uuid4()` string generated once per process
  (module-level, first-use or import-time — implementer's choice, but it
  MUST be identical across every `log_call()` invocation within the same
  process and MUST be a valid UUID string).
- **Directory/file creation**: create `<state_dir>` with
  `mkdir(parents=True, exist_ok=True)` if needed. Create the log file
  with mode `0o600` at first creation (e.g. via
  `os.open(path, os.O_CREAT | os.O_WRONLY | os.O_APPEND, 0o600)` +
  `os.fdopen(..., "a")` — creating and setting the mode in one syscall,
  not create-then-`chmod`, to avoid a race). Subsequent appends reuse the
  existing file/permissions.
- **Never raises.** Any exception during path resolution, directory
  creation, serialization, or writing is caught; the entry is dropped.
  The first such failure in a process prints one warning line to
  `sys.stderr` (e.g. `"[sasha] tool_log: couldn't write to <path>: <exc>
  — tool-call logging disabled for this session"`); subsequent failures
  in the same process are silent (a module-level flag remembers the
  first warning fired). This must never propagate to the caller —
  `run_consult` calls `log_call` unconditionally and must never fail
  because of it.

## `safe_commands.touches_sensitive_path` — exact contract

```python
def touches_sensitive_path(command: str, executor) -> bool:
```

- Independently tokenizes `command` via the existing `_tokenize` (so it
  works on ANY command string, not just ones that already passed the
  allowlist checks — a user can `yes` their way through any command,
  including ones with unlisted verbs, and this function must still be
  able to flag a sensitive path in them).
- If `_tokenize` raises `ValueError` (unparseable quoting): return
  `False`. This is a deliberate fail-*open* on the logging carve-out only
  (whether to withhold the log's result) — it has no bearing on
  `requires_confirmation`'s own fail-closed behavior, which is untouched.
- For every token that isn't an operator (`_OPERATORS`), an allowlisted
  verb (`_ALLOWLISTED_VERBS`), or a flag (starts with `-`): normalize via
  the existing `_normalize(tok, executor.cwd)` and check against
  `executor.sensitive_path_patterns` with `fnmatch.fnmatch`, expanding
  `~` in each pattern first — identical matching logic to what
  `requires_confirmation` already does inline. Return `True` on first
  match, `False` if no token matches.
- `requires_confirmation`'s existing final step (currently the inline
  loop over non-flag tokens checking `executor.sensitive_path_patterns`)
  is replaced with `return touches_sensitive_path(command, executor)` —
  everything before that (the `` ` ``/`$(`/`>` check, tokenize, segment/
  allowlist check, unexpanded-`$` check) is untouched.

## `agent.py` — exact dispatch changes

Add `from sasha import tool_log` and `from sasha import safe_commands` to
the imports. In the per-call loop body, track two new local variables
(`executed_command: str | None = None`, set only in the `edit` branch to
the edited command string) and, after `session.add_tool_result(call.id,
result_str)` for a *known* tool (skip logging entirely for the
`tool is None` / "Unknown tool" branch — see Constraints), compute and
log:

```python
result_text = None
result_omitted_reason = "declined"
if executed:
    if (call.name == "run_command"
            and safe_commands.touches_sensitive_path(
                final_arguments.get("command", ""), executor)):
        result_omitted_reason = "sensitive_path"
    else:
        result_text = result_str
        result_omitted_reason = None

tool_log.log_call(
    tool_name=call.name,
    call_id=call.id,
    arguments=call.arguments,
    executed_command=executed_command,
    cwd=executor.cwd,
    verdict=verdict,
    executed=executed,
    result_text=result_text,
    result_omitted_reason=result_omitted_reason,
)
```

Note `arguments=call.arguments` (the LLM's original proposal, always) is
distinct from `final_arguments` (used only to run the tool and to check
`touches_sensitive_path` against what actually executed — matters for the
`edit` verdict, where they can differ). `verdict` is whatever
`confirmer.ask(...)` returned (`"yes"`/`"no"`/`"edit"`/`"reply"`) in the
confirmation-gated branch, or the literal string `"auto-approved"` in the
auto-approved branch — **`session.add_tool_result`'s call and the
existing `result_str` values sent to the LLM are completely unchanged**;
`result_text`/`result_omitted_reason` are a separate, log-only
computation and must never alter what the LLM sees.

## `tests/contract/conftest.py` — exact contract

```python
import pytest


@pytest.fixture(autouse=True)
def _isolated_state_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg-state"))
```

This is the only content required. It must NOT touch `XDG_CONFIG_HOME`,
`HOME`, or any other variable — `test_task_1_1_config.py`'s own
`isolated_env` fixture already manages `XDG_CONFIG_HOME`/`HOME`
independently, and `test_task_3_3_e2e.py`/`test_task_3_2_entry.py`/
`test_task_15_2_llm_setup_repl.py` build their own explicit subprocess
`env` dicts (starting from `dict(os.environ)` and overriding
`XDG_STATE_HOME` themselves) — this fixture patches the *current*
process's environment, which those subprocess-env-dict tests don't read
from, so it cannot interact with any of them. Verified directly (Tier 1
ran the full existing suite with this fixture added, before any other
change in this task, and it stayed at 533/533 green).

## Fix sketch (authoritative — verified directly)

Verified end to end in a scratch git worktree — 546/546 tests green,
including all 13 new ACs and the full pre-existing suite unmodified —
before being committed as authoritative.

**New file `src/sasha/tool_log.py`:**

```python
from __future__ import annotations

import datetime
import json
import os
import pathlib
import sys
import uuid

_session_id = str(uuid.uuid4())
_warned = False


def _log_path() -> pathlib.Path:
    xdg = os.environ.get("XDG_STATE_HOME", "")
    if xdg:
        base = pathlib.Path(xdg) / "sasha"
    else:
        base = pathlib.Path.home() / ".local" / "state" / "sasha"
    return base / "tool_log.jsonl"


def _utc_timestamp() -> str:
    now = datetime.datetime.now(datetime.timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"


def log_call(*, tool_name: str, call_id: str, arguments: dict,
             executed_command: str | None, cwd: str, verdict: str,
             executed: bool, result_text: str | None,
             result_omitted_reason: str | None) -> None:
    global _warned
    path = _log_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "ts": _utc_timestamp(),
            "session_id": _session_id,
            "cwd": cwd,
            "tool": tool_name,
            "call_id": call_id,
            "arguments": arguments,
            "executed_command": executed_command,
            "verdict": verdict,
            "executed": executed,
            "result": result_text,
            "result_omitted_reason": result_omitted_reason,
        }
        line = json.dumps(entry) + "\n"
        fd = os.open(str(path), os.O_CREAT | os.O_WRONLY | os.O_APPEND, 0o600)
        with os.fdopen(fd, "a") as f:
            f.write(line)
    except Exception as exc:
        if not _warned:
            _warned = True
            print(f"[sasha] tool_log: couldn't write to {path}: {exc} "
                  f"— tool-call logging disabled for this session",
                  file=sys.stderr)
```

**`src/sasha/safe_commands.py`** — insert `touches_sensitive_path` right
before `requires_confirmation`, and replace `requires_confirmation`'s
final path-matching loop with a call to it:

```python
def touches_sensitive_path(command: str, executor) -> bool:
    try:
        tokens = _tokenize(command)
    except ValueError:
        return False

    for tok in tokens:
        if tok in _OPERATORS or tok in _ALLOWLISTED_VERBS:
            continue
        if tok.startswith("-"):
            continue
        normalized = _normalize(tok, executor.cwd)
        for pattern in executor.sensitive_path_patterns:
            expanded_pattern = os.path.expanduser(pattern)
            if fnmatch.fnmatch(normalized, expanded_pattern):
                return True
    return False
```

`requires_confirmation`'s body is unchanged up through the unexpanded-`$`
check; its final two loops (the ones that inline-checked
`executor.sensitive_path_patterns`) are deleted and replaced with:
```python
    return touches_sensitive_path(command, executor)
```

**`src/sasha/agent.py`** — add two imports:
```python
from sasha import safe_commands
from sasha import tool_log
```
Add `executed_command = None` alongside the existing `executed = False`
init. In the `edit` branch, set `executed_command = payload` alongside
`final_arguments = {...}`. In the auto-approved `else:` branch, add
`verdict = "auto-approved"` as its first line. Immediately after the
existing `session.add_tool_result(call.id, result_str)` line, insert:
```python
            result_text = None
            result_omitted_reason = "declined"
            if executed:
                if (call.name == "run_command"
                        and safe_commands.touches_sensitive_path(
                            final_arguments.get("command", ""), executor)):
                    result_omitted_reason = "sensitive_path"
                else:
                    result_text = result_str
                    result_omitted_reason = None

            tool_log.log_call(
                tool_name=call.name,
                call_id=call.id,
                arguments=call.arguments,
                executed_command=executed_command,
                cwd=executor.cwd,
                verdict=verdict,
                executed=executed,
                result_text=result_text,
                result_omitted_reason=result_omitted_reason,
            )
```
(before the existing `if executed: current_key = ...` line). The
`tool is None` / "Unknown tool" branch above `continue`s before reaching
any of this — correctly skipping the log entirely, per Constraints.

## Constraints
- Modify `src/sasha/safe_commands.py`, `src/sasha/agent.py`. Add
  `src/sasha/tool_log.py`, `tests/contract/conftest.py`.
- Do not touch `src/sasha/config.py`, `src/sasha/__main__.py`,
  `src/sasha/repl.py`, `src/sasha/executor.py`, `src/sasha/tools.py` —
  compaction, config, and the manual command are Task 22.2, not this one.
- Do not log the `tool is None` ("Unknown tool") branch at all — no entry
  is written for a hallucinated/unrecognized tool name. This is a
  deliberate v1 scope decision (marginal value, keeps this task's surface
  smaller), not an oversight.
- Do not change `run_consult`'s signature (no new parameters) and do not
  change what `session.add_tool_result` receives in any branch — logging
  is strictly additive and observationally invisible to the LLM.
- Do not add a `[tool_log]` config section, `sasha compact-log` command,
  or any size-based logic in this task — nothing in this task's contract
  tests exercises them.

## Acceptance criteria
(encoded 1:1 in `tests/contract/test_task_22_1_tool_call_logging.py`)
1. `log_call()` writes one JSON line to `<state_dir>/tool_log.jsonl`
   containing every documented key, correctly populated for a normal
   executed call — `test_ac1_log_call_writes_full_schema`.
2. `session_id` is a valid UUID string, identical across multiple
   `log_call()` calls in the same process —
   `test_ac2_session_id_stable_across_calls`.
3. The log file is created with mode `0o600` —
   `test_ac3_log_file_created_with_owner_only_permissions`.
4. A write failure (e.g. the resolved state directory path is occupied by
   a file, not a directory, so `mkdir` fails) is caught; `log_call()`
   does not raise — `test_ac4_write_failure_does_not_raise`.
5. `touches_sensitive_path()` returns `True` for a command touching a
   denylisted path and `False` for one that doesn't, independent of
   whether the command would itself require confirmation —
   `test_ac5_touches_sensitive_path_matches_and_no_match`. (Verified
   directly during spec-writing: a bare denylisted directory with no
   trailing filename, e.g. `ls ~/.ssh`, does NOT match — a pre-existing
   gap in the `fnmatch` pattern inherited unchanged from Task 21.1, now
   tracked as ISSUE-043 and explicitly out of scope for this task; the
   test uses `~/.ssh/config` instead of bare `~/.ssh` to avoid exercising
   that known gap.)
6. `touches_sensitive_path()` returns `False` (fails open) on unparseable
   quoting rather than raising — `test_ac6_touches_sensitive_path_fails_open_on_unparseable`.
7. `requires_confirmation`'s full existing behavior is unchanged after
   the extraction — no new test; `test_task_21_1_safe_command_auto_approval.py`
   stays green, unmodified, as part of this task's own gate.
8. In `agent.py`'s dispatch, `tool_log.log_call` (patched with a spy) is
   called exactly once per tool call, with correct
   `verdict`/`executed`/`executed_command`/`result_text`/
   `result_omitted_reason` for each of: `"yes"`, `"no"`, `"edit"`,
   `"reply"`, and auto-approved —
   `test_ac8_dispatch_logs_each_verdict_correctly`.
9. An approved (`"yes"`) `run_command` call whose command touches a
   sensitive path is logged with `result_text=None`,
   `result_omitted_reason="sensitive_path"`, while
   `session.add_tool_result` still receives the real, un-withheld result
   text (withholding is log-only) —
   `test_ac9_dispatch_withholds_sensitive_path_result_from_log_only`.
10. An unknown tool name produces no log entry at all —
    `test_ac10_unknown_tool_not_logged`.

## Standing instructions
- Your goal: make `tests/contract/test_task_22_1_tool_call_logging.py`
  pass, with the full existing suite still green:
  `bash scripts/run-tests.sh`.
- NEVER modify files under `tests/contract/` — the gate rejects the
  commit if you do. If a contract test seems wrong or unimplementable,
  stop and flag it; do not commit.
- You may add your own supplementary tests under `tests/impl/`.
- Deliver exactly ONE new commit on top of the HEAD you started from;
  never amend, rebase, or reset; leave the working tree clean.
- Commit message per AGENTS.md, with trailers: `Done: Task 22.1 — <one
  line>`, `Next: Task 22.2 — Compaction, config, and 'sasha compact-log'`,
  and the Co-Authored-By signature line from your dispatch prompt.
- **Executor routing (per CLAUDE.md's standing rule): this task edits
  existing control flow in `agent.py`/`safe_commands.py` that already
  works (surgical), even though `tool_log.py` and `conftest.py` are new.
  Dispatch to the default model (deepseek-v4-pro) — do not launch under a
  local-model `SASHA_DISPATCH_MODEL` override.**
