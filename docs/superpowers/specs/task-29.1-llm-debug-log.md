# Task 29.1: LLM request debug log (ISSUE-047 investigation tooling)

## Authoritative contract
`docs/superpowers/specs/2026-07-22-issue-047-llm-debug-log-design.md` is
the complete contract for this task: a config-gated, best-effort JSONL
logger for the exact outbound LLM request (model/messages/tools), so
Salvador can inspect what sasha actually sends the provider on every turn.

## What already exists
- `src/sasha/tool_log.py` — the established pattern for this kind of
  module (JSONL append, `_log_path()` under
  `${XDG_STATE_HOME:-~/.local/state}/sasha/`, `0o600` perms, best-effort
  write with a one-time stderr warning via a `_warned` flag,
  `_session_id`/`_utc_timestamp()` helpers). **Bind to the pattern, do not
  import from or modify `tool_log.py` itself** — this task's module is
  self-contained, same as `tool_log.py` is.
- `src/sasha/config.py` — `Config` dataclass, `_build_config()`,
  `_DEFAULT_CONFIG_TOML`. The `[activity_indicator]` section (Task 26.1)
  is the direct template for how a new `[section]` + boolean field gets
  added: a new dataclass field with its default, a new block in
  `_build_config()` that reads `raw.get("<section>", {})` and only
  accepts the key if it's the right type, the new field threaded into the
  final `Config(...)` constructor call, and the new section appended to
  `_DEFAULT_CONFIG_TOML`.
- `src/sasha/llm.py`'s `ChatClient.chat()` — the `payload` dict (`model`,
  `messages`, conditionally `tools`) is fully assembled right before the
  `urllib.request.Request(...)` call. **Bind to this — do not change the
  request-building logic itself, only add one call alongside it.**
- `src/sasha/__main__.py` — `tool_log.configure(config.tool_log_max_size_mb)`
  is called once, right after `config = load(...)`. **Bind to this exact
  spot for the new wiring line.**

## What's new
- `src/sasha/llm_debug.py` — new module, full contents below.
- `Config.llm_debug_enabled: bool = False` — new field.
- `[llm_debug]` section in `_DEFAULT_CONFIG_TOML`.
- One new call in `ChatClient.chat()`, one new call in `__main__.py`.

## Fix sketch (authoritative)

### `src/sasha/llm_debug.py` (new file, in full)

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
_enabled = False


def configure(enabled: bool) -> None:
    global _enabled
    _enabled = enabled


def _log_path() -> pathlib.Path:
    xdg = os.environ.get("XDG_STATE_HOME", "")
    if xdg:
        base = pathlib.Path(xdg) / "sasha"
    else:
        base = pathlib.Path.home() / ".local" / "state" / "sasha"
    return base / "llm_debug.jsonl"


def _utc_timestamp() -> str:
    now = datetime.datetime.now(datetime.timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"


def log_request(*, profile: str, model: str, messages: list[dict],
                 tools: list[dict]) -> None:
    global _warned
    if not _enabled:
        return
    path = _log_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "ts": _utc_timestamp(),
            "session_id": _session_id,
            "profile": profile,
            "model": model,
            "messages": messages,
            "tools": tools,
        }
        line = json.dumps(entry) + "\n"
        fd = os.open(str(path), os.O_CREAT | os.O_WRONLY | os.O_APPEND, 0o600)
        with os.fdopen(fd, "a") as f:
            f.write(line)
    except Exception as exc:
        if not _warned:
            _warned = True
            print(f"[sasha] llm_debug: couldn't write to {path}: {exc} "
                  f"— LLM debug logging disabled for this session",
                  file=sys.stderr)
```

### `src/sasha/config.py`

1. `Config` dataclass — add, right after `activity_indicator_tool_color`
   and before `path`:
   ```python
   llm_debug_enabled: bool = False
   ```
2. `_DEFAULT_CONFIG_TOML` — append, right after the existing
   `[activity_indicator]` block, before the closing `"""`:
   ```toml

   [llm_debug]
   enabled = false
   ```
3. `_build_config()` — right after the existing `activity_table`
   parsing block (the one that sets `activity_indicator_enabled` etc.),
   add:
   ```python
   llm_debug_enabled = False
   llm_debug_table = raw.get("llm_debug", {})
   if isinstance(llm_debug_table, dict):
       raw_llm_debug_enabled = llm_debug_table.get("enabled")
       if isinstance(raw_llm_debug_enabled, bool):
           llm_debug_enabled = raw_llm_debug_enabled
   ```
4. The final `return Config(...)` call — add
   `llm_debug_enabled=llm_debug_enabled,` alongside the existing
   `activity_indicator_tool_color=activity_indicator_tool_color,` line.

### `src/sasha/llm.py`

1. Add `from sasha import llm_debug` to the imports (alongside the
   existing `from sasha.config import Profile`).
2. In `ChatClient.chat()`, right after the `if tools: payload["tools"] =
   tools` block and before `req = urllib.request.Request(...)`, add:
   ```python
   llm_debug.log_request(profile=self.profile.name, model=self.profile.model,
                          messages=messages, tools=tools)
   ```

### `src/sasha/__main__.py`

1. Add `from sasha import llm_debug` to the imports (alongside the
   existing `from sasha import tool_log`).
2. Right after `tool_log.configure(config.tool_log_max_size_mb)`, add:
   ```python
   llm_debug.configure(config.llm_debug_enabled)
   ```

## Pre-existing contract tests — none amended

This task's contract test file,
`tests/contract/test_task_29_1_llm_debug_log.py`, is new — already
committed in the same commit as this spec, RED against base. No existing
contract test needed amendment: `Config.llm_debug_enabled` is a new field
with a default (`False`) that preserves current behavior for every
existing config test, and `ChatClient.chat()`'s new call is a no-op when
`llm_debug` isn't configured (its default module state is `_enabled =
False`), so no existing `llm.py`/`sse_stub`-based test observes any new
behavior.

Verified locally in a scratch worktree together with the four file
changes above: full suite green, 582/582 (570 existing + this task's 6 +
Task 28.1's already-counted 6 — see note: the running total after Task
28.1 was 576; this task's contract file adds 6 more, bringing the total to
582).

## Constraints
- Modify only `src/sasha/config.py`, `src/sasha/llm.py`,
  `src/sasha/__main__.py`, and create `src/sasha/llm_debug.py`.
- Do not touch `tests/contract/` at all — the new contract test file is
  already committed as part of this spec.
- Do not touch `src/sasha/tool_log.py` — this task's module is
  self-contained (duplicates the tiny `_utc_timestamp`/session-id pattern
  rather than importing from `tool_log.py`), per the design doc's decision
  log.
- Do not add response logging, compaction/rotation, redaction, or a
  browsing/query command — all explicitly out of scope per the design
  doc.
- **Executor routing (per CLAUDE.md's standing rule): this is a
  greenfield module (new file) plus small, precisely-specified wiring
  edits to existing files that already work. Dispatch to the default
  model (deepseek-v4-pro) — do not launch under a local-model
  `SASHA_DISPATCH_MODEL` override**, since the `llm.py`/`config.py`/
  `__main__.py` edits are surgical insertions into working code, not
  purely greenfield.

## Acceptance criteria
(encoded 1:1 in `tests/contract/test_task_29_1_llm_debug_log.py`)
1. `test_ac1_disabled_writes_nothing` — with `llm_debug` not enabled,
   `log_request(...)` creates no file.
2. `test_ac2_enabled_appends_one_line_with_correct_schema` — with
   `llm_debug` enabled, `log_request(...)` appends exactly one JSON line
   with `ts`, `session_id`, `profile`, `model`, `messages`, `tools` all
   present and matching the call's arguments.
3. `test_ac3_tools_field_is_empty_list_not_omitted_when_no_tools` —
   `tools=[]` logs as `[]`, never omitted from the JSON entry.
4. `test_ac4_write_failure_warns_once_and_never_raises` — a write failure
   never raises, and warns to stderr exactly once across multiple calls in
   the same process.
5. `test_ac5_config_default_and_fallback` — `[llm_debug]\nenabled = true`
   → `Config.llm_debug_enabled is True`; absent section or non-bool
   `enabled` → default `False`; the self-installed default config.toml
   contains `[llm_debug]`.
6. `test_ac6_chat_client_logs_the_exact_outbound_request` — an actual
   `ChatClient.chat()` call (via the `sse_stub` test harness) with
   `llm_debug` enabled logs an entry whose `messages`/`model`/`profile`
   match exactly what was sent to the stub server.
7. The full existing suite is green: 582/582 — `bash scripts/run-tests.sh`.

## Standing instructions
- Your goal: make the contract tests in
  `tests/contract/test_task_29_1_llm_debug_log.py` pass, and keep the full
  suite green (582/582): `bash scripts/run-tests.sh`.
- NEVER modify files under `tests/contract/` — the gate rejects the commit
  if you do.
- Deliver exactly ONE new commit on top of the HEAD you started from;
  never amend, rebase, or reset; leave the working tree clean.
- Commit message per AGENTS.md, with trailers: `Done: Task 29.1 — <one
  line>`, `Next: Task 29.2 — Phase 29 review (Tier 1, with Salvador)`, and
  the Co-Authored-By signature line from your dispatch prompt. **Do not**
  add a `Fixed: ISSUE-047` trailer — this task ships investigation tooling
  only; the underlying repeated-data question ISSUE-047 describes stays
  open until Salvador inspects the log and confirms a cause.
