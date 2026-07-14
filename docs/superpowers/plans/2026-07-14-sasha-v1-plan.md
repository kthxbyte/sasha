# Sasha v1 Implementation Plan

> **For agentic workers:** This project executes via the three-tier protocol in
> `CLAUDE.md`, NOT via subagent-driven-development or executing-plans. Tier 1
> writes per-task specs + contract tests; `scripts/run-phase.sh` dispatches
> deepseek-v4-pro one-shots and gates them with `scripts/verify.sh`. The root
> `PLAN.md` is the machine-tracked checkbox mirror of this plan.

**Goal:** Build Sasha v1 — an interactive Python shell wrapper that executes
lines via bash and consults an LLM (with one confirmed `run_command` tool) when
lines fail, don't exist, or are natural language.

**Architecture:** Nine stdlib-only modules under `src/sasha/` (spec §4): a
per-line `bash -c` executor with PTY-tee capture and a cwd/env side channel; a
pure trigger engine with deterministic mute counters; a streaming
OpenAI-compatible chat client; an agent loop gated by y/n/e/reply confirmation;
a rolling session; a thin REPL on top.

**Tech Stack:** Python ≥ 3.11, stdlib only at runtime (`pty`, `termios`,
`readline`, `urllib.request`, `tomllib`, `json`, `shlex`). `pytest` dev-only.

**Authoritative contract:** `docs/superpowers/specs/2026-07-14-sasha-design.md`
(referenced below as §N). Where this plan and the spec disagree, the spec wins.

## Global Constraints

- Runtime imports in `src/sasha/` restricted to the Python standard library (§3.1).
- Python ≥ 3.11; Linux; bash at `/bin/bash` (§3.7).
- Nothing LLM-proposed executes without explicit `y`/`e` confirmation; free-text
  replies never execute anything (§3.2).
- LLM-layer failure must never break command execution (§3.3).
- Trigger and mute logic is pure and deterministic — no LLM involvement (§3.5).
- All Sasha-originated terminal lines use the `[sasha] ` prefix and the exact
  vocabulary of §10.
- Contract tests live in `tests/contract/test_task_X_Y_*.py`, are written by
  Tier 1 with each task spec, and are immutable to the implementer.
- Defaults everywhere: `history_tokens=12000`, `stderr_capture_bytes=8192`,
  `max_tool_turns=10`, `mute_threshold=3` (§5.7).

---

## Phase 1 — Core shell (a working "dumb" shell, no LLM)

### Task 1.1: Package skeleton + config module

**Files:**
- Create: `pyproject.toml`, `.gitignore`, `README.md` (stub),
  `src/sasha/__init__.py` (`__version__ = "0.1.0"`),
  `src/sasha/__main__.py` (minimal: `--version` prints `sasha 0.1.0`, exit 0;
  no-arg prints a not-yet-wired notice, exit 0 — replaced in Task 3.2),
  `src/sasha/config.py`, `tests/impl/__init__.py`
- Spec: §5.7, §8.1

**Interfaces:**
- Consumes: nothing (first task).
- Produces: `Profile(name, base_url, model, api_key)`, `Limits(history_tokens,
  stderr_capture_bytes, max_tool_turns, mute_threshold)`,
  `Config(default_profile, profiles, limits)`, `load(path: Path | None) -> Config`,
  `ConfigError(Exception)` — exactly as §5.7.

**Acceptance criteria** (contract tests `tests/contract/test_task_1_1_config.py`):
1. `load(None)` with no config file present returns built-in defaults: profiles
   `deepseek` (`https://api.deepseek.com/v1`, model `deepseek-chat`, key from
   `$DEEPSEEK_API_KEY`) and `local` (`http://192.168.8.253:11441/v1`, model
   `local`, no key), `default_profile == "deepseek"`, default `Limits`.
2. A TOML file overrides defaults; unknown keys are ignored silently.
3. Key resolution order: `api_key_env` env var if set and non-empty, else
   literal `api_key` value, else `""`.
4. Malformed TOML raises `ConfigError` carrying the parser message.
5. `pip install -e .` exposes console script `sasha`; `sasha --version` prints
   `sasha 0.1.0` and exits 0.

### Task 1.2: Mute store

**Files:**
- Create: `src/sasha/mutes.py`
- Spec: §5.3, §8.2

**Interfaces:**
- Consumes: nothing.
- Produces: `MuteStore(path, threshold=3)` with `is_muted`, `record_ignored`
  (returns `True` exactly on the mutation that crosses the threshold),
  `record_acted`, `mute(command, exit_code=None)`, `unmute(command,
  exit_code=None)` — exactly as §5.3.

**Acceptance criteria** (`tests/contract/test_task_1_2_mutes.py`):
1. Three consecutive `record_ignored` for a pair mute it; the third call (and
   only that call) returns `True`; the pair is then `is_muted`.
2. `record_acted` resets the pair's streak; a subsequent ignore starts at 1.
3. State survives a store reopen (JSON round-trip, schema of §8.2).
4. The state file parses as valid JSON after every mutation (atomic writes).
5. A corrupt/unreadable state file yields an empty store without raising.
6. `mute`/`unmute` with `exit_code=None` affect all exit codes for the command.

### Task 1.3: Executor core (subprocess, exit codes, side channel)

**Files:**
- Create: `src/sasha/executor.py`
- Spec: §5.1 (signatures), §9.3 (side channel). PTY capture is Task 1.4;
  in this task `stderr_text`/`stdout_text` are `""` and stderr is inherited.

**Interfaces:**
- Consumes: nothing.
- Produces: `ExecResult(exit_code, signal, stderr_text, stdout_text, cwd_after,
  duration_ms)` (frozen dataclass), `Executor(cwd, env)` with `.cwd`,
  `.run(line, capture_stdout=False) -> ExecResult`,
  `Executor.check_syntax(line) -> str | None` — exactly as §5.1.

**Acceptance criteria** (`tests/contract/test_task_1_3_executor_core.py`):
1. `run("true")` → exit 0; `run("exit 7")` → exit 7; unknown command → 127.
2. A child killed by a signal reports `signal == N` and `exit_code == 128 + N`.
3. `run("cd /tmp")` updates `.cwd` to `/tmp` (resolved); the next `run` starts
   there; `cwd_after` reflects it.
4. `export FOO=bar` in one line is visible as `$FOO` in the next line;
   `unset` removes it. State persists across a line containing `exit 3`.
5. A child that dies without reporting (SIGKILL) leaves `.cwd`/env unchanged.
6. `check_syntax('echo "unclosed')` returns bash's error text;
   `check_syntax("echo ok")` returns `None`. Neither executes anything.
7. Child runs in its own process group (`os.getpgid(child) != os.getpgid(0)`).

### Task 1.4: Executor PTY tees (stderr always, stdout opt-in)

**Files:**
- Modify: `src/sasha/executor.py` (Task 1.3's interfaces unchanged)
- Spec: §9.1, §9.2

**Interfaces:**
- Consumes: Task 1.3's `Executor`/`ExecResult` (fields now populated).
- Produces: same signatures; `stderr_text` always captured; `stdout_text`
  captured when `capture_stdout=True`.

**Acceptance criteria** (`tests/contract/test_task_1_4_executor_pty.py`):
1. In the child, `isatty(2)` is true; with `capture_stdout=True`, `isatty(1)`
   is true.
2. Child stderr bytes still reach the parent's terminal (relay, not swallow).
3. `stderr_text` is captured with ANSI/CSI/OSC sequences stripped.
4. Capture keeps only the last `stderr_capture_bytes` bytes (tail cap).
5. With `capture_stdout=True`, `stdout_text` contains the child's stdout;
   with the default, `stdout_text == ""` and stdout is the inherited terminal.
6. The PTY window size matches the controlling terminal's at spawn.

### Task 1.5: Trigger engine

**Files:**
- Create: `src/sasha/triggers.py`
- Spec: §5.2, §6

**Interfaces:**
- Consumes: `ExecResult` (Task 1.3), `MuteStore` (Task 1.2).
- Produces: `Decision` enum (`AUTO_CONSULT`, `OFFER`, `NOTHING`),
  `decide(line, result, mutes) -> Decision`, `command_word(line) -> str` —
  exactly as §5.2.

**Acceptance criteria** (`tests/contract/test_task_1_5_triggers.py`):
1. Exit 127 → `AUTO_CONSULT`, even when the pair is muted (T1 mute exemption).
2. Exit not in {0,127}, no signal, pair unmuted → `OFFER`; muted → `NOTHING`.
3. Signal exits SIGINT (130) and SIGPIPE (141) → `NOTHING`; other
   signal deaths → `NOTHING` per T5's user-initiated rule extended to all
   signals (§6 table: signal present → not an OFFER candidate).
4. Exit 0 → `NOTHING`.
5. `command_word("grep -r foo .")` == `"grep"`; unparseable line → `""`.

**Phase 1 exit state:** `python -m pytest` green; the pieces of a dumb shell
exist and are individually proven. Phase boundary review, then Phase 2 specs.

---

## Phase 2 — LLM plumbing (text answers end-to-end)

### Task 2.1: Streaming chat client

**Files:**
- Create: `src/sasha/llm.py`
- Spec: §5.4, §7.1
- Test helper (Tier 1-provided, in contract dir): `tests/contract/sse_stub.py`
  — a stdlib `http.server` that replays canned SSE scripts.

**Interfaces:**
- Consumes: `Profile` (Task 1.1).
- Produces: `ToolCall(id, name, arguments)`, `ChatResponse(text, tool_calls)`,
  `ChatError(reason)`, `ChatClient(profile, timeout_s=120.0)` with
  `.chat(messages, tools, on_text) -> ChatResponse` — exactly as §5.4.

**Acceptance criteria** (`tests/contract/test_task_2_1_llm.py`):
1. Text deltas reach `on_text` incrementally and in order; `ChatResponse.text`
   is their concatenation.
2. Tool calls fragmented across SSE chunks are assembled into `ToolCall`s with
   parsed dict arguments.
3. `Authorization: Bearer` header sent iff the profile's key is non-empty
   (stub asserts on received headers).
4. Connection refused, HTTP ≥ 400, and mid-stream disconnect each raise
   `ChatError` with a non-empty `.reason`.
5. A tool call whose arguments fail JSON parsing yields no `ToolCall`; the call
   still returns normally with the streamed text (§5.4 degradation).
6. Request body contains `model`, `messages`, `tools`, `stream: true` (§7.1).

### Task 2.2: Session & context building

**Files:**
- Create: `src/sasha/session.py`
- Spec: §5.6, §7.3, §9.4

**Interfaces:**
- Consumes: `ExecResult` (Task 1.3), `ChatResponse` (Task 2.1).
- Produces: `Session(history_token_budget=12000)` with `note_command`,
  `add_user`, `add_assistant`, `add_tool_result`, `build_messages(cwd) ->
  list[dict]` — exactly as §5.6.

**Acceptance criteria** (`tests/contract/test_task_2_2_session.py`):
1. `build_messages` returns the system prompt first; it mentions the
   confirmation rules and the platform string.
2. The final user message carries the fresh context block: cwd, up to the last
   10 noted commands with exit codes, and the triggering command's stderr.
3. Over-budget conversations drop oldest messages first; the system prompt is
   never dropped; total estimate (`len/4`) ends ≤ budget.
4. Dropping an assistant message that carried tool_calls also drops its paired
   tool-result messages (no orphaned `role:"tool"` messages remain).
5. Assistant tool_calls and tool results round-trip into valid
   OpenAI-format message dicts (`tool_calls[].function.arguments` as JSON
   string, `tool_call_id` matching).

### Task 2.3: Agent loop with confirmation gate

**Files:**
- Create: `src/sasha/agent.py`
- Spec: §5.5, §7.2, §7.4

**Interfaces:**
- Consumes: `ChatClient` (2.1), `Session` (2.2), `Executor` (1.3/1.4).
- Produces: `Confirmer` protocol (`ask(command, description) ->
  tuple[str, str]`), `run_consult(session, client, executor, confirmer, out,
  max_tool_turns=10) -> None`, and the module-level `RUN_COMMAND_TOOL` schema
  dict of §7.2 — exactly as §5.5.

**Acceptance criteria** (`tests/contract/test_task_2_3_agent.py`, driven by a
scripted Confirmer + stub SSE server; a spy Executor records calls):
1. Text-only response: printed via `out`, stored in session, executor untouched.
2. `('yes','')` → `executor.run(command, capture_stdout=True)`; the tool result
   fed back contains exit code, stdout tail, stderr tail.
3. `('edit', edited)` → the edited string is what runs.
4. `('no','')` → nothing executes; tool result is the §7.4 declined message.
5. `('reply', text)` → nothing executes; tool result contains `text` verbatim.
6. Two consecutive approved identical command strings → loop stops with one
   final `tools=[]` round (stub asserts the last request has no tools).
7. `max_tool_turns` reached → same final-round behavior.

**Phase 2 exit state:** `??`-style consults work end-to-end against the stub
(and manually against real DeepSeek). Phase boundary review, then Phase 3 specs.

---

## Phase 3 — REPL & product assembly

### Task 3.1: REPL dispatch

**Files:**
- Create: `src/sasha/repl.py`
- Spec: §9.5 (dispatch), §6 (offer flow), §10 (vocabulary)

**Interfaces:**
- Consumes: everything from Phases 1–2.
- Produces: `Repl(config, executor, mutes, session, client_factory, confirmer,
  input_fn, out)` with `.run_once(line) -> bool` (False on exit request) and
  `.loop()` — I/O injected so contract tests drive it in-process with fakes;
  exact constructor pinned in the task spec.

**Acceptance criteria** (`tests/contract/test_task_3_1_repl.py`):
1. `?? <text>` consults (fake agent records the user message) without executing.
2. Empty line with a pending offer consults about the failure and calls
   `record_acted`; empty line otherwise is a no-op.
3. Non-empty input while an offer is pending calls `record_ignored` and then
   processes the input normally.
4. A line failing `check_syntax` is never executed and auto-consults with the
   error text (T2).
5. Exit 127 auto-consults (T1); exit 0 stays silent (T6).
6. Offer line and mute-crossing notice match §10 byte-for-byte.
7. Builtins: `sasha mute grep`, `sasha unmute grep`, `sasha profile local`,
   `sasha help`, `exit`/`quit` behave per §9.5/§10.
8. `ChatError` during a consult prints the §10 unreachable line and the REPL
   keeps accepting input.

### Task 3.2: Entry point & terminal wiring

**Files:**
- Modify: `src/sasha/__main__.py` (replace 1.1 stub)
- Spec: §9.5 (prompt, Ctrl-C, history), §10, §8.3

**Interfaces:**
- Consumes: `Repl` (3.1), `config.load` (1.1).
- Produces: `main(argv) -> int`; CLI `sasha [--profile NAME] [--config PATH]
  [--version]`.

**Acceptance criteria** (`tests/contract/test_task_3_2_entry.py`, subprocess-driven):
1. `sasha --version` → `sasha 0.1.0`, exit 0.
2. Malformed config → `sasha: config error: <msg>` on stderr, exit 2.
3. `--profile local` starts with the local profile (visible via
   `sasha profile` builtin output); unknown profile name → config error, exit 2.
4. Prompt renders cwd with `$HOME` → `~` and the ` ✦ ` suffix.
5. Readline history is written to `~/.local/state/sasha/history` (honoring
   `$XDG_STATE_HOME`) on clean exit.
6. EOF (Ctrl-D) exits 0 cleanly.

### Task 3.3: End-to-end smoke + README

**Files:**
- Modify: `README.md` (full usage docs: install, config, profiles, triggers,
  muting, confirmation UX, safety guarantees)
- Create: nothing new in `src/` expected — this task exists to make the e2e
  contract pass and may fix glue bugs anywhere outside protected paths.
- Spec: whole document; §11 (REPL smoke).

**Acceptance criteria** (`tests/contract/test_task_3_3_e2e.py`, driving a real
`sasha` subprocess under a PTY against `sse_stub.py` via a temp config whose
`local` profile points at the stub):
1. Typing `550*11` yields a streamed LLM answer on the terminal (stub scripted
   to answer `550*11 = 6050`), prefixed output intact, prompt returns.
2. A failing-but-real command prints the offer line; pressing Enter consults
   and the stub's answer appears; the mute counter was NOT incremented.
3. Ignoring three offers for the same `(command, exit)` prints the §10 muting
   notice; a fourth failure prints no offer.
4. A stub-scripted `run_command` tool call renders the §7.4 confirmation block;
   answering `n` executes nothing and the model's follow-up text appears.
5. With the stub down, a `??` ask prints the §10 unreachable line and the next
   ordinary command still executes.
6. README documents every §10 line and both profiles.

**Phase 3 exit state:** v1 complete — installable, documented, e2e-proven.
Phase boundary review, then v2 planning.

---

## v2 outline (not planned in detail yet — specced after the v1 review)

- **Phase 4 — Multi-tool registry & permissions:** `read_file`, `write_file`,
  `edit_file`, `glob`, `grep` tools; per-tool allow/ask/deny config;
  file-modifying tools ask-only by default with operation descriptions (§2 v2).
- **Phase 5 — Session persistence:** transcripts under
  `~/.local/state/sasha/sessions/`, `sasha --resume`, per-tool loop detection.
