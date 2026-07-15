# Task 5.2: "Thinking" indicator during LLM consult

## Authoritative contract
`docs/superpowers/specs/2026-07-14-sasha-design.md` §7.4 (agent loop) and
§10 (error vocabulary) are the base contract; this task adds one new
user-visible line to §10. This task fixes ISSUE-020: there is no feedback
between when a consult starts and when the first streamed token arrives
(network latency + LLM think time), so Sasha can appear hung.

## What already exists
- `src/sasha/repl.py` `Repl._consult` — the method this task modifies. Its
  `stream_out` closure already calls `self._out(text)` for every streamed
  chunk (Task 3.4) — this task does not change that streaming path, only
  adds one line before it starts.
- `src/sasha/agent.py` `run_consult` — unmodified. The latency this task
  addresses is entirely inside `client.chat(...)`'s blocking network call,
  before its first `on_text` invocation; nothing about the agent loop itself
  changes.

## What's new
Per the design's stdlib-only, YAGNI-first stance (§3 contract 1, §12
decisions log): a **static line**, not a spinner thread. A rotating spinner
needs its own thread and terminal cursor control (`\r` overwrite) that would
have to coexist with `Executor`'s concurrent PTY relay loop — real
complexity for a cosmetic feature the issue's own write-up already accepts a
simple text line for ("Option B").

In `src/sasha/repl.py`, `Repl._consult`, add one line — the exact string is
`"[sasha] thinking…"` (single Unicode ellipsis character U+2026, matching
the em-dashes and `✦` already used elsewhere in this file's user-visible
strings, not three ASCII periods):

```python
    def _consult(self, auto_prompt=None):
        if auto_prompt:
            self._session.add_user(auto_prompt)
        self._out("[sasha] thinking…")
        try:
            client = self._client_factory(self._current_profile)
            def stream_out(text):
```

Everything else in `_consult` (and the rest of the file) is unchanged. The
line prints unconditionally, on every path into `_consult` — auto-consult,
explicit `??`, and empty-Enter offer-accept alike — and is never erased or
overwritten; it simply becomes part of the visible transcript, exactly like
every other `[sasha] `-prefixed line (design §10: "All Sasha-originated
lines are prefixed `[sasha] ` and written to stdout"). It prints even when
the subsequent request immediately fails with a `ChatError` — the indicator
is about latency before a response of *any kind*, not specifically a
successful one.

## Constraints
- Modify `src/sasha/repl.py` only (plus optional supplementary tests under
  `tests/impl/`).
- Do not touch: `scripts/`, `docs/superpowers/`, `CLAUDE.md`, `AGENTS.md`,
  `PLAN.md`, `ISSUE-TRACKING.md`, `README.md`, `.gitignore`,
  `pyproject.toml`, `src/sasha/__init__.py`, `src/sasha/config.py`,
  `src/sasha/mutes.py`, `src/sasha/triggers.py`, `src/sasha/executor.py`,
  `src/sasha/session.py`, `src/sasha/agent.py`, `src/sasha/llm.py`,
  `tests/contract/`.
- No new threads, no `\r`/cursor-control escape sequences, no new
  dependencies. If you believe a spinner is genuinely needed, stop and flag
  it in a `Deferred:` trailer rather than implementing one — this task's
  contract is the static line above.
- Greenfield-vs-surgical routing: this is a surgical one-line edit to code
  that already works. Dispatch to the default model (deepseek-v4-pro); do
  not use a `SASHA_DISPATCH_MODEL` local-model override.

## Acceptance criteria
(encoded 1:1 in `tests/contract/test_task_5_2_thinking_indicator.py`)
1. `"[sasha] thinking…"` is emitted before any streamed response text, on a
   successful consult — `test_ac1_indicator_precedes_response_text`.
2. It is also emitted, and is the only line, when the consult immediately
   fails with a `ChatError` (before the "unreachable" line) —
   `test_ac2_indicator_precedes_chaterror`.
3. It is emitted on every `_consult` entry point (auto-consult, `??`,
   empty-Enter offer-accept), not just one path —
   `test_ac3_indicator_on_every_consult_path`.

## Standing instructions
- Your goal: make `tests/contract/test_task_5_2_thinking_indicator.py` pass
  with `tests/contract/test_task_3_1_repl.py`,
  `tests/contract/test_task_4_1_job_control.py`, and
  `tests/contract/test_task_5_1_drop_127_preamble.py` still green:
  `PYTHONPATH=src python3 -m pytest tests/contract/test_task_3_1_repl.py tests/contract/test_task_4_1_job_control.py tests/contract/test_task_5_1_drop_127_preamble.py tests/contract/test_task_5_2_thinking_indicator.py`.
- NEVER modify files under `tests/contract/` — the gate rejects the commit if
  you do.
- Deliver exactly ONE new commit on top of the HEAD you started from; never
  amend, rebase, or reset; leave the working tree clean.
- Commit message per AGENTS.md, with trailers:
  `Done: Task 5.2 — <one line>`, `Fixed: ISSUE-020`,
  `Next: Task 5.3 — Phase 5 review (Tier 1, with Salvador)`, and the
  Co-Authored-By signature line from your dispatch prompt.
