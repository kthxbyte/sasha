# Task 2.1: Streaming chat client

## Authoritative contract
`docs/superpowers/specs/2026-07-14-sasha-design.md` §5.4 (interfaces) and §7.1
(wire format) are the complete contract.

## What already exists
- `src/sasha/config.py` → `Profile` (Task 1.1) — consumed, not modified.
- `tests/contract/sse_stub.py` — Tier 1 test infrastructure (scriptable
  OpenAI-compatible SSE server). Under `tests/contract/`: NEVER modify it.
- `tests/contract/test_task_2_1_llm.py` — executable acceptance criteria.

## What's new
- `src/sasha/llm.py` — per §5.4:
  - `ToolCall(id: str, name: str, arguments: dict)` — frozen dataclass.
  - `ChatResponse(text: str, tool_calls: list[ToolCall])` — frozen dataclass.
  - `ChatError(Exception)` with attribute `.reason: str` (constructor
    `ChatError(reason)`; `str()` of the exception contains the reason).
  - `ChatClient(profile: Profile, timeout_s: float = 120.0)` with
    `chat(messages: list[dict], tools: list[dict],
          on_text: Callable[[str], None]) -> ChatResponse`.

## Pinned semantics (decisions already made — implement exactly these)
1. **Request**: `POST {profile.base_url}/chat/completions` via
   `urllib.request`, `timeout=timeout_s`. JSON body: `model` =
   `profile.model`, `messages` as given, `"stream": true`, and `"tools"` =
   the given list ONLY when it is non-empty (omit the key when `tools == []`
   — some servers reject empty tool lists). Header
   `Content-Type: application/json`; header
   `Authorization: Bearer {profile.api_key}` ONLY when `profile.api_key` is
   non-empty.
2. **SSE parsing**: read the response line by line; lines starting with
   `data: ` carry payloads; `data: [DONE]` terminates the stream normally.
   Other lines (blank, comments) are skipped. Each payload parses as JSON;
   `choices[0].delta.content` (string, when present) is passed to `on_text`
   immediately and accumulated into `ChatResponse.text`.
3. **Tool-call assembly**: `delta.tool_calls` fragments are merged by their
   `index`: the first fragment carries `id`/`function.name`; every fragment's
   `function.arguments` string is concatenated. After `[DONE]`, each
   assembled call's arguments string is parsed with `json.loads`; on success
   it becomes a `ToolCall` (arguments as dict, calls ordered by index); on
   `ValueError` that call is DROPPED silently — the chat still returns
   normally with its text (§5.4 degradation; never guess a command into
   existence).
4. **ChatError cases** (each with a non-empty `.reason`):
   a. connection failure / timeout (`URLError`, `OSError`);
   b. HTTP status ≥ 400;
   c. stream ends (EOF) before `data: [DONE]` was seen;
   d. a `data:` payload that is not valid JSON.
5. No retries, no logging, no printing — the caller owns presentation.

## Constraints
- `src/sasha/llm.py` imports stdlib + `sasha.config` (for `Profile` typing)
  only. Only `src/sasha/llm.py` is created (plus optional tests under
  `tests/impl/`).
- Do not touch: `scripts/`, `docs/superpowers/`, `CLAUDE.md`, `AGENTS.md`,
  `PLAN.md`, `ISSUE-TRACKING.md`, `README.md`, `.gitignore`,
  `pyproject.toml`, `src/sasha/{__init__,__main__,config,mutes,executor,triggers}.py`,
  `tests/contract/`.

## Acceptance criteria
(encoded 1:1 in `tests/contract/test_task_2_1_llm.py`)
1. Text deltas reach `on_text` incrementally and in order; `.text` is their
   concatenation — `test_ac1_text_streams_incrementally`.
2. Tool calls fragmented across SSE chunks are assembled with parsed dict
   arguments — `test_ac2_fragmented_tool_call_assembled`.
3. `Authorization: Bearer` sent iff the profile key is non-empty —
   `test_ac3_*`.
4. Connection refused, HTTP ≥ 400, and mid-stream disconnect each raise
   `ChatError` with a non-empty reason — `test_ac4_*`.
5. Malformed tool-call arguments JSON → no ToolCall, text preserved, no
   exception — `test_ac5_bad_tool_json_degrades_to_text`.
6. Request body carries `model`, `messages`, `stream: true`; `tools` present
   exactly when non-empty — `test_ac6_*`.

## Standing instructions
- Your goal: make `tests/contract/test_task_2_1_llm.py` pass
  (`PYTHONPATH=src python3 -m pytest tests/contract/test_task_2_1_llm.py`).
- NEVER modify files under `tests/contract/` (including `sse_stub.py`) — the
  gate rejects the commit if you do. If a contract test seems wrong or
  unimplementable, stop and flag it; do not commit.
- You may add supplementary tests under `tests/impl/`.
- Flag unexpected state; do not create or modify anything outside the files
  listed in "What's new".
- Deliver exactly ONE new commit on top of the HEAD you started from; never
  amend, rebase, or reset; leave the working tree clean.
- Commit message per AGENTS.md, with trailers:
  `Done: Task 2.1 — <one line>`,
  `Next: Task 2.2 — Session & context building`, and the Co-Authored-By
  signature line from your dispatch prompt.
