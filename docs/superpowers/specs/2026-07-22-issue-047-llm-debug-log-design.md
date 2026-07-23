# LLM request debug log (ISSUE-047 investigation tooling)

**Status:** Draft for review
**Date:** 2026-07-22

## 1. Mission

Give Salvador a way to inspect exactly what sasha sends the LLM provider on
every turn, so he can confirm or rule out his suspicion (ISSUE-047) that
sasha "repeats itself" — specifically the two unconfirmed candidate causes
already logged: `cwd` sent unconditionally every turn, and the full system
prompt (personality + memory blocks) rebuilt and resent in full every turn
with no caching. This is investigation tooling, not a fix — it makes the
outbound payload visible so the actual cause can be confirmed by hand.

## 2. Scope

### In scope:
- New `src/sasha/llm_debug.py` module: a config-gated, best-effort JSONL
  logger for the exact outbound request payload (model, messages, tools)
  sent by `ChatClient.chat()`.
- New `[llm_debug]` config.toml section, single `enabled` boolean, default
  `false`.
- One new call site in `llm.py`'s `ChatClient.chat()`, one new wiring line
  in `__main__.py`.

### Out of scope / non-goals:
- **Response logging.** Only the outbound request is captured. Salvador's
  call — this directly answers "what is sasha sending," which is the
  question at hand; response logging would need a second hook and a
  request/response correlation scheme for a question that isn't being
  asked yet.
- **Compaction/rotation.** Unlike `tool_log.jsonl`, this file has no
  size-based compaction or background thread. Salvador's call — this is a
  temporary debug toggle (turn on, reproduce, turn off), not a permanent
  audit trail; he's expected to clear the file himself between debug
  sessions. If it's ever promoted to an always-on feature, compaction can
  be added then.
- **Redaction.** The logged content is not filtered or withheld for
  sensitive paths (unlike `tool_log`'s `touches_sensitive_path`
  withholding). This log mirrors exactly what's already transmitted to the
  LLM provider — it exposes nothing that hasn't already left the machine
  over the wire; there's nothing new to protect against by withholding it
  locally.
- **A browsing/query UI for the log.** Plain JSONL, read with any JSON
  tool or a one-off script — no new `sasha` builtin command for this.
- **Fixing ISSUE-047 itself.** Once Salvador inspects the log and confirms
  (or rules out) a cause, that's a separate follow-up task.

## 3. Key contracts & assumptions

1. **Hook point: `ChatClient.chat()`, right after the `payload` dict is
   built** (`src/sasha/llm.py`, currently around line 39-46) — this is the
   single choke point every profile and every turn passes through,
   upstream of the actual `urllib.request` call. The `payload` dict at
   that point already has exactly `model`, `messages`, and (if any)
   `tools` — the same three pieces of information sent over the wire.
2. **Config-gated, not call-site-gated.** `llm.py`'s call site is
   unconditional (`llm_debug.log_request(...)` every turn); the module
   itself checks its internal `enabled` flag first and returns
   immediately if `False` — matches `tool_log`'s "cheap when off, but the
   call site doesn't need an `if` around it" style, and keeps `llm.py`
   from importing `Config` just to check one flag.
3. **Best-effort, like `tool_log`.** A write failure (permissions, disk
   full) never raises — it's caught, and a one-time stderr warning is
   printed on first failure per process (same pattern as
   `tool_log.log_call`'s `_warned` flag), after which the module silently
   no-ops for the rest of the session.
4. **New module, not an extension of `tool_log.py`.** Different subject
   (LLM requests vs. tool calls), different schema, different lifecycle
   (no compaction) — bolting it onto `tool_log.py` would mix two unrelated
   concerns into one file.

## 4. Format

**Config** (`config.toml`):
```toml
[llm_debug]
enabled = false
```
`Config` gains one new field: `llm_debug_enabled: bool = False`. Parsing
follows the exact pattern already used for `[activity_indicator]`'s
`enabled` key: read `raw.get("llm_debug", {})`, if it's a dict pull
`enabled` and use it only if it's an actual `bool`.

**Log path**: `${XDG_STATE_HOME:-~/.local/state}/sasha/llm_debug.jsonl` —
same directory as `tool_log.jsonl`, same `0o600` permissions, same
append-only (`O_CREAT | O_WRONLY | O_APPEND`) write.

**Schema** — one JSON object per line, one line per outbound request:
```json
{
  "ts": "2026-07-22T10:15:30.123Z",
  "session_id": "6a1b...-uuid",
  "profile": "deepseek",
  "model": "deepseek-chat",
  "messages": [ {"role": "system", "content": "..."}, ... ],
  "tools": [ {"type": "function", "function": {...}}, ... ]
}
```
`session_id` is a fresh UUID generated once per process (own copy of
`tool_log`'s `_session_id`/`_utc_timestamp()` pattern — duplicated rather
than imported, keeping the module self-contained the same way `tool_log`
is). `tools` is always present as a list (empty list when no tools were
sent), for schema consistency — unlike the actual wire `payload`, which
omits the key entirely when there are no tools.

## 5. Algorithm / module surface

```python
# src/sasha/llm_debug.py
def configure(enabled: bool) -> None: ...   # sets a module-global flag

def log_request(*, profile: str, model: str, messages: list[dict],
                 tools: list[dict]) -> None:
    # no-op immediately if not enabled
    # else: build the schema dict above, append one JSON line to
    # llm_debug.jsonl, mkdir -p the parent dir first, 0o600
    # any OSError: warn once to stderr, then silently no-op for the
    # rest of the process (mirrors tool_log.log_call's _warned pattern)
```

`llm.py`'s `ChatClient.chat()`:
```python
payload = {
    "model": self.profile.model,
    "messages": messages,
    "stream": True,
}
if tools:
    payload["tools"] = tools

llm_debug.log_request(profile=self.profile.name, model=self.profile.model,
                       messages=messages, tools=tools)

req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), method="POST")
...
```

`__main__.py`, right after the existing `tool_log.configure(...)` line:
```python
tool_log.configure(config.tool_log_max_size_mb)
llm_debug.configure(config.llm_debug_enabled)
```

## 6. Decisions log

- **Decision:** Request-only, no response logging. **Rationale:**
  Salvador's call — directly answers the question at hand without the
  added complexity of correlating request/response pairs.
- **Decision:** No compaction/rotation, unbounded append. **Rationale:**
  Salvador's call — this is a temporary debug toggle, not a permanent
  audit trail; simpler code for a feature meant to be turned on briefly.
- **Decision:** No redaction of logged content. **Rationale:** the log
  mirrors data already sent to the LLM provider; there's nothing new being
  exposed that withholding would protect.
- **Decision:** New standalone module rather than extending `tool_log.py`.
  **Rationale:** different subject, schema, and lifecycle — keeps each
  module's responsibility singular, matching this project's established
  file-boundary conventions.

## 7. Testing strategy

New contract test file, `tests/contract/test_task_29_1_llm_debug_log.py`:
1. `configure(enabled=False)` (or never configured — default) →
   `log_request(...)` writes nothing, creates no file.
2. `configure(enabled=True)` → `log_request(...)` appends exactly one
   JSON line with the correct schema (`ts`, `session_id`, `profile`,
   `model`, `messages`, `tools` all present and matching the call's
   arguments).
3. `tools=[]` (no tools passed) → the logged `tools` field is `[]`, not
   omitted.
4. A write failure (e.g. an unwritable log directory) doesn't raise —
   `log_request()` returns normally, and a warning is printed to stderr
   exactly once even across multiple calls in the same process.
5. Config parsing: `[llm_debug]\nenabled = true` → `Config.llm_debug_enabled
   is True`; absent section or non-bool `enabled` → default `False`
   (mirrors the existing `test_task_26_1_activity_indicator.py`-style
   config parsing tests for `[activity_indicator]`).
6. An integration-level test using the existing `sse_stub` harness: drive
   one full `ChatClient.chat()` call with `llm_debug` configured on,
   assert the resulting `llm_debug.jsonl` (in an isolated
   `XDG_STATE_HOME`, per `tests/contract/conftest.py`'s existing autouse
   fixture) contains one line whose `messages` matches what was actually
   passed to `chat()`.

No amendments to existing contract tests are anticipated — this is a new
module, a new config field with a default that preserves current behavior
(`enabled = False`), and one new call site whose only effect when disabled
is a single cheap boolean check.
