# Sasha — Design Specification

**Status:** Draft for review
**Date:** 2026-07-14

## 1. Mission

Sasha is an interactive shell wrapper written in Python (stdlib-only at runtime).
The user runs `sasha` and gets a prompt that executes normal shell lines exactly
as bash would — but when a line fails, names a command that does not exist, or
is plain natural language, Sasha consults an LLM grounded in the actual shell
context (cwd, recent commands, captured stderr) and answers usefully instead of
emitting a generic error. The LLM may propose shell commands; nothing proposed
ever executes without explicit user confirmation.

## 2. Scope

### In scope (v1 — "the useful shell"):

- Interactive REPL with readline editing/history; per-line execution via `bash -c`.
- Executor with stdout/stdin inherited from the real terminal and stderr routed
  through a PTY tee (isatty-true, ANSI-stripped capped capture); cwd/env
  tracked across lines.
- Trigger matrix: auto-consult on exit 127 and on syntax errors; passive
  zero-token offer on other non-zero exits; never on success or user signals.
- Deterministic annoyance learning: per-`(command, exit)` ignore counters with
  auto-mute at threshold; `sasha mute` / `sasha unmute` built-ins.
- LLM client: OpenAI-compatible chat completions with SSE streaming, profiles
  `deepseek` (default) and `local` (llama.cpp at `http://192.168.8.253:11441`).
- Agent loop with exactly one tool, `run_command`, gated by a
  yes / no / edit / free-text-reply confirmation prompt.
- Rolling in-session conversation with token-budget trimming.
- `??` explicit ask prefix; empty-Enter accepts a pending offer.
- Packaging: `pyproject.toml`, console script `sasha`, zero runtime
  dependencies, Python ≥ 3.11; `pytest` as dev-only dependency.

### Planned (v2 — "the trusted agent"; designed for, not built in v1):

- Multi-tool registry: `read_file`, `write_file`, `edit_file`, `glob`, `grep`
  alongside `run_command`.
- Per-tool permission policy (allow/ask/deny) in config; file-modifying tools
  ask-only by default, always with a brief operation description.
- Session persistence and resume (`sasha --resume`), transcripts under
  `~/.local/state/sasha/sessions/`.
- Loop detection extended per-tool.

### Out of scope / non-goals (either version):

- Replacing bash as a login shell; job control beyond what a single foreground
  command does (`fg`/`bg`, `jobs`).
- File-path tab completion (readline history navigation only).
- Multimodal input/output, MCP, subagents, telemetry, Windows.
- Reimplementing shell parsing/expansion in Python — bash does all of that.

## 3. Key contracts & assumptions

1. **Stdlib-only runtime.** No third-party imports in `src/sasha/`. HTTP via
   `urllib.request`, config via `tomllib`, PTY via `pty`/`termios`/`fcntl`,
   line editing via `readline`. `pytest` is a dev dependency only.
2. **Nothing proposed by the LLM executes without explicit confirmation.**
   Only a literal `y` answer (or `e` followed by the user's edited command)
   runs a proposed command. Free-text replies NEVER execute anything — they are
   messages to the model.
3. **The LLM layer failing must never break command execution.** Network down,
   bad key, endpoint offline → one short diagnostic line, and the shell keeps
   working as a plain shell.
4. **Auto-spending tokens is allowed only when the line had no other purpose**
   (exit 127, syntax error, explicit `??`). Every other consult is opt-in.
5. **The trigger engine and mute store are pure and deterministic** — no LLM
   involvement in deciding when to consult or when to mute.
6. **Sasha writes nothing silently.** The only files it creates on its own are
   its state/history under `~/.local/state/sasha/`. Its only network peer is
   the configured LLM endpoint.
7. **Python ≥ 3.11, Linux, bash present at `/bin/bash`.** UTF-8 terminal.
8. **v1 interfaces must survive v2.** The tool registry, permission gate, and
   session abstractions are shaped so v2 adds entries and policies without
   rewriting v1 modules.

## 4. Architecture

```
        ┌────────────────────────────── sasha (one process) ─────────────────────────────┐
        │                                                                                 │
 user ──▶ REPL ──line──▶ Executor ──(exit,stderr)──▶ Trigger engine ──decision──┐         │
        │  ▲              │  bash -c child                 │ consults           │         │
        │  │              │  stdout/stdin: real tty        ▼                    │         │
        │  │              │  stderr: PTY tee ──▶ screen  Mute store             │         │
        │  │              └─ cwd/env side channel        (~/.local/state)       │         │
        │  │                                                                    ▼         │
        │  └──answers/confirmations──── Agent loop ◀──messages──── Session (rolling ctx)  │
        │                                 │  ▲                                            │
        │                                 ▼  │ SSE stream                                 │
        │                              LLM client ──HTTP──▶ deepseek / local llama.cpp    │
        └─────────────────────────────────────────────────────────────────────────────────┘
```

Modules under `src/sasha/` (one file each, one purpose each):

| Module | Purpose | Depends on |
|---|---|---|
| `repl.py` | prompt loop, built-ins, offer/consult dispatch | executor, triggers, agent, config |
| `executor.py` | run one line, capture exit/stderr, track cwd/env | stdlib only |
| `triggers.py` | pure decision function over execution results | mutes |
| `mutes.py` | ignore counters, mute state, JSON persistence | stdlib only |
| `llm.py` | OpenAI-compatible streaming chat client | config |
| `agent.py` | tool loop, confirmation gate, loop detection | llm, executor, session |
| `session.py` | rolling conversation, context injection, trimming | stdlib only |
| `config.py` | TOML config + profiles + limits, env-var keys | stdlib only |
| `__main__.py` | entry point, arg parsing (`sasha`, `sasha --profile X`) | repl |

## 5. Module interfaces (v1 public surface)

These signatures are the contract that task specs and contract tests bind to.

### 5.1 `executor.py`

```python
@dataclass(frozen=True)
class ExecResult:
    exit_code: int            # child's exit code; 128+N when killed by signal N
    signal: int | None        # N when killed by signal N, else None
    stderr_text: str          # ANSI-stripped, tail-capped capture (see §9.2)
    stdout_text: str          # '' unless capture_stdout=True; same hygiene
    cwd_after: str            # absolute path; == cwd_before if unreported
    duration_ms: int

class Executor:
    def __init__(self, cwd: str, env: dict[str, str]) -> None: ...
    @property
    def cwd(self) -> str: ...
    def run(self, line: str, capture_stdout: bool = False) -> ExecResult: ...
    @staticmethod
    def check_syntax(line: str) -> str | None:
        """None if bash -n accepts the line, else bash's syntax error text."""
```

`run()` executes `line` with `/bin/bash -c`, stdin/stdout inherited, stderr via
PTY tee (§9.1). With `capture_stdout=True` (agent-initiated runs), stdout also
goes through its own PTY tee — still displayed live, still a tty to the child,
but captured so the model can read the output of commands it proposed. After
the child exits, the executor's `cwd`/env reflect the child's final state via
the side channel (§9.3), so `cd`, `export`, and `unset` persist across lines.

### 5.2 `triggers.py`

```python
class Decision(enum.Enum):
    AUTO_CONSULT = "auto_consult"
    OFFER = "offer"
    NOTHING = "nothing"

def decide(line: str, result: ExecResult, mutes: MuteStore) -> Decision: ...
def command_word(line: str) -> str:
    """First shlex token of the line; '' if unparseable."""
```

### 5.3 `mutes.py`

```python
class MuteStore:
    def __init__(self, path: pathlib.Path, threshold: int = 3) -> None: ...
    def is_muted(self, command: str, exit_code: int) -> bool: ...
    def record_ignored(self, command: str, exit_code: int) -> bool:
        """Increment streak; returns True if this call crossed the threshold
        and muted the pair (caller prints the one-time notice)."""
    def record_acted(self, command: str, exit_code: int) -> None:
        """User accepted an offer: reset the pair's streak to 0."""
    def mute(self, command: str, exit_code: int | None = None) -> None: ...
    def unmute(self, command: str, exit_code: int | None = None) -> None: ...
```

`exit_code=None` in `mute`/`unmute` means all exit codes for that command.
State persists to JSON (§8.2) on every mutation (atomic write-and-rename).

### 5.4 `llm.py`

```python
@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict          # parsed JSON arguments

@dataclass(frozen=True)
class ChatResponse:
    text: str                # concatenated assistant text ('' if none)
    tool_calls: list[ToolCall]

class ChatError(Exception):
    """Endpoint unreachable, HTTP >= 400, or malformed stream. .reason: str"""

class ChatClient:
    def __init__(self, profile: Profile, timeout_s: float = 120.0) -> None: ...
    def chat(self, messages: list[dict], tools: list[dict],
             on_text: Callable[[str], None]) -> ChatResponse: ...
```

`chat()` streams: every text delta is passed to `on_text` as it arrives (the
REPL prints it), and the assembled result returns as `ChatResponse`. Malformed
tool-call JSON from the model is degraded to text (contract 3 in §3 — never
guessed into an execution).

### 5.5 `agent.py`

```python
class Confirmer(Protocol):
    def ask(self, command: str, description: str) -> tuple[str, str]:
        """Returns (verdict, payload):
        ('yes', ''), ('no', ''), ('edit', edited_command), ('reply', text)."""

def run_consult(session: Session, client: ChatClient, executor: Executor,
                confirmer: Confirmer, out: Callable[[str], None],
                max_tool_turns: int = 10) -> None: ...
```

`Confirmer` is a protocol so contract tests can drive the loop with a scripted
confirmer; the real one reads the terminal.

### 5.6 `session.py`

```python
class Session:
    def __init__(self, history_token_budget: int = 12000) -> None: ...
    def note_command(self, line: str, result: ExecResult) -> None: ...
    def add_user(self, text: str) -> None: ...
    def add_assistant(self, response: ChatResponse) -> None: ...
    def add_tool_result(self, call_id: str, output: str) -> None: ...
    def build_messages(self, cwd: str) -> list[dict]:
        """System prompt + trimmed conversation + fresh context block (§7.3)."""
```

### 5.7 `config.py`

```python
@dataclass(frozen=True)
class Profile:
    name: str
    base_url: str            # e.g. "https://api.deepseek.com/v1"
    model: str
    api_key: str             # resolved: env var first, config value second, ''

@dataclass(frozen=True)
class Limits:
    history_tokens: int = 12000
    stderr_capture_bytes: int = 8192
    max_tool_turns: int = 10
    mute_threshold: int = 3

@dataclass(frozen=True)
class Config:
    default_profile: str
    profiles: dict[str, Profile]
    limits: Limits

def load(path: pathlib.Path | None = None) -> Config:
    """Read ~/.config/sasha/config.toml; missing file → built-in defaults
    (deepseek + local profiles, Limits()). Malformed TOML → ConfigError."""
```

## 6. Trigger matrix

Evaluated by `triggers.decide()` after every executed line:

| # | Situation | Detection | Decision |
|---|---|---|---|
| T1 | First word is not a command (typos, `550*11`, plain English) | `exit_code == 127` | AUTO_CONSULT |
| T2 | Line is not valid bash (unclosed quote, bad pipe) | `Executor.check_syntax()` pre-check fails — line is NOT executed | AUTO_CONSULT |
| T3 | Command ran but failed | `exit_code` not in {0, 127}, `signal is None`, pair not muted | OFFER |
| T4 | Same as T3 but pair is muted | mute store lookup | NOTHING |
| T5 | User-initiated termination | `signal in {SIGINT, SIGPIPE}` (exit 130/141) | NOTHING |
| T6 | Success | `exit_code == 0` | NOTHING |
| T7 | Explicit ask `?? <text>` | REPL prefix match, executor bypassed | (consult directly) |

Notes:

- T2 runs before execution: `bash -n -c <line>` is cheap and side-effect-free;
  a line that cannot parse is consulted about, never executed.
- T1 is exempt from muting — it never fires on a line that meant something.
- OFFER prints exactly one dim line: `[sasha] failed (exit N) — press Enter to ask`.
  The next input decides: empty line → consult (record_acted); anything else →
  offer ignored (record_ignored), input processed normally.
- Crossing the mute threshold prints once:
  `[sasha] muting offers for <cmd> exit <N> — 'sasha unmute <cmd>' to restore`.

## 7. LLM protocol & agent loop

### 7.1 Wire format

OpenAI-compatible `POST {base_url}/chat/completions` with `stream: true`;
SSE `data:` lines parsed incrementally; `[DONE]` terminates. Request fields:
`model`, `messages`, `tools`, `stream`. `Authorization: Bearer <key>` header
only when the profile resolves a non-empty key (the local profile sends none).

### 7.2 The one v1 tool

```json
{"type": "function", "function": {
  "name": "run_command",
  "description": "Propose one bash command to run in the user's shell. The user sees the command and your description and must approve it before it runs.",
  "parameters": {"type": "object", "properties": {
      "command":     {"type": "string", "description": "exact bash command"},
      "description": {"type": "string", "description": "one line: what this does and why"}},
    "required": ["command", "description"]}}}
```

The tool registry is a `dict[str, ToolSpec]` with this single entry in v1; v2
adds entries without changing the agent loop.

### 7.3 Messages built per consult (`Session.build_messages`)

1. **System prompt** (static per session): Sasha's role, the confirmation
   rules, OS from `platform.platform()`, `bash` as the shell.
2. **Trimmed conversation**: prior user/assistant/tool messages; while the
   `len(chars)/4` estimate exceeds `history_tokens`, drop oldest messages
   first (never the system prompt).
3. **Fresh context block** appended to the new user message:
   cwd, last 10 commands with exit codes (from `note_command`), and the
   captured stderr of the triggering command (≤ `stderr_capture_bytes`).

### 7.4 Agent loop (`run_consult`)

```
1. messages = session.build_messages(cwd)
2. response = client.chat(messages, tools, on_text=out)   # streams text live
3. if no tool_calls: session.add_assistant(response); DONE
4. for each tool_call (run_command):
     verdict, payload = confirmer.ask(command, description)
     yes   → result = executor.run(command, capture_stdout=True); tool result
             = exit code + stdout tail + stderr tail
     edit  → same as yes, with payload as the command actually run
     no    → tool result = "User declined to run this command."
     reply → tool result = "User did not run the command and replied: <text>"
5. loop-detection: if this turn's approved command string equals the previous
   turn's, OR turn count == max_tool_turns → append a final user message
   telling the model to answer in text, do one last chat() with tools=[], DONE
6. goto 1 (with tool results appended)
```

Confirmation prompt rendering (exact format, for contract tests):

```
[sasha] wants to run:  <command>
        └ <description>
        [y]es / [n]o / [e]dit — or type a reply
```

Input mapping: `y`/`yes` → yes; `n`/`no` → no; `e`/`edit` → readline-prefilled
edit of the command, then run; empty → no; anything else → reply with that text.
Missing/empty `description` in the tool call → rendered as
`(no description provided)`; the gate still applies.

## 8. Persistent state & config

### 8.1 `~/.config/sasha/config.toml`

```toml
default_profile = "deepseek"

[profiles.deepseek]
base_url = "https://api.deepseek.com/v1"
model = "deepseek-chat"
api_key_env = "DEEPSEEK_API_KEY"   # env var consulted first
# api_key = "sk-..."               # optional fallback literal

[profiles.local]
base_url = "http://192.168.8.253:11441/v1"
model = "local"
api_key_env = ""

[limits]
history_tokens = 12000
stderr_capture_bytes = 8192
max_tool_turns = 10
mute_threshold = 3
```

A missing file yields exactly these built-in defaults. Unknown keys are
ignored; a malformed file raises `ConfigError` with the TOML parser's message
(fail loud at startup, contract 3 does not apply — no shell is running yet).

### 8.2 `~/.local/state/sasha/mutes.json`

```json
{"version": 1,
 "pairs": {"grep:1": {"muted": false, "ignored_streak": 2},
           "diff:1": {"muted": true,  "ignored_streak": 3}}}
```

Key is `"<command_word>:<exit_code>"`. Writes are atomic (temp file + rename).
An unreadable/corrupt file is treated as empty (and overwritten on next write)
— mute state is a convenience, never worth crashing the shell.

### 8.3 Readline history

`~/.local/state/sasha/history` via `readline.write_history_file`, loaded at
startup, 1000-entry cap.

## 9. Algorithms

### 9.1 Executor: stderr PTY tee

1. `pty.openpty()` → (master, slave). Copy the real terminal's window size to
   the slave (`TIOCGWINSZ` → `TIOCSWINSZ`); install a `SIGWINCH` handler that
   re-copies while the child runs.
2. Spawn `/bin/bash -c <line>` with stdin/stdout inherited, stderr = slave,
   cwd = tracked cwd, env = tracked env (+ side-channel fd, §9.3).
3. Parent `select()`-loop on master: every chunk is written through to the
   real terminal unchanged AND appended to a capture buffer.
4. Capture buffer: keep only the last `stderr_capture_bytes` bytes; on child
   exit, strip ANSI/CSI/OSC escape sequences (regex over the bytes, then
   UTF-8 decode with `errors="replace"`) → `ExecResult.stderr_text`.
5. Child exit: `os.waitpid`; exit code and signal from `os.waitstatus_to_exitcode`.
6. Ctrl-C while a child runs is delivered to the child's process group, not to
   Sasha (child spawned in its own group; Sasha ignores SIGINT while waiting).

Rationale: the child sees `isatty(2) == True`, so whiptail/dialog fd-swap
idioms and ncurses-on-stderr apps render correctly (same technique as
`script(1)`); mc/htop/vim draw on the untouched real stdout.

With `capture_stdout=True`, stdout gets a second, independent PTY tee with the
same relay/capture/winsize treatment; steps 3–5 apply to both masters in one
`select()` loop.

### 9.2 Stderr capture hygiene

The capture is a *tail* cap (last 8 KiB), because error summaries usually come
last. Full-screen apps that painted UI on stderr yield escape-stripped noise;
that is acceptable — those runs rarely trigger consults, and the cap bounds it.

### 9.3 Executor: cwd/env side channel

1. Before spawn, create a pipe; pass the write end to the child as fd 9
   (`pass_fds`), exporting `SASHA_STATE_FD=9`.
2. The line actually executed is the user line wrapped so that a bash EXIT
   trap reports final state: `trap 'pwd -P >&9; printf "\0" >&9; export -p >&9' EXIT`
   prepended to the user's line. The trap fires on normal exit, `exit`, and
   fatal signals bash can trap.
3. Parent reads fd until EOF: text before the first NUL is the final cwd;
   after it, `export -p` output is parsed (only `declare -x NAME="VALUE"`
   lines; values unescaped by `shlex`) into the tracked env for the next line.
4. No report (child killed with SIGKILL, or user unset the trap): keep the
   previous cwd/env — stale state for one line beats a broken shell.
5. Agent-initiated runs use the same executor and side channel deliberately:
   agent commands persist their cd/export effects exactly as if the user had
   typed the command themselves.

### 9.4 Session trimming

Token estimate = `ceil(len(chars)/4)` summed over message contents. While the
conversation (excluding system prompt) exceeds `history_tokens`, remove the
oldest message; if it is an assistant message with tool_calls, also remove its
paired tool results (orphaned tool messages are invalid in the wire format).

### 9.5 REPL turn

```
read line (readline)
├─ empty + offer pending        → consult about the offered failure
├─ empty                        → new prompt
├─ "??" prefix                  → session.add_user(rest); run_consult()
├─ "sasha <builtin> ..."        → mute/unmute/profile/help handled in-process
├─ "exit" / "quit" / Ctrl-D     → save history, exit 0
└─ otherwise:
     pending offer? → record_ignored(...)
     syntax pre-check fails (T2) → add_user(line + error); run_consult()
     result = executor.run(line); session.note_command(line, result)
     decide(...) → AUTO_CONSULT → add_user(auto-prompt); run_consult()
                 → OFFER        → print offer line, mark pending
                 → NOTHING      → new prompt
```

Prompt string: cwd with `$HOME` abbreviated to `~`, then ` ✦ ` (e.g.
`~/Sasha ✦ `). `ChatError` during any consult prints
`[sasha] <profile> unreachable: <reason> — 'sasha profile <name>' to switch`
and returns to the prompt (contract 3). Ctrl-C during a consult aborts the
HTTP request and returns to the prompt.

## 10. Error vocabulary (user-visible lines)

| Event | Line |
|---|---|
| Offer | `[sasha] failed (exit N) — press Enter to ask` |
| Auto-mute | `[sasha] muting offers for <cmd> exit <N> — 'sasha unmute <cmd>' to restore` |
| LLM unreachable | `[sasha] <profile> unreachable: <reason> — 'sasha profile <name>' to switch` |
| Loop stopped | `[sasha] stopping: repeated identical command / tool-turn limit reached` |
| Profile switch | `[sasha] profile: <name> (<model> @ <base_url>)` |
| Bad config | `sasha: config error: <message>` (startup, exit 2) |

All Sasha-originated lines are prefixed `[sasha] ` and written to stdout;
command output is never prefixed or altered.

## 11. Testing strategy

- **Pure logic** (`triggers`, `mutes`, `session` trimming, `config`): direct
  unit tests, no I/O beyond tmp_path.
- **Executor**: tests against real `/bin/bash` with synthetic commands — exit
  codes, 127, signals, stderr capture and ANSI stripping, `isatty(2)` true in
  the child, `cd`/`export` persistence, window-size propagation.
- **LLM client + agent loop**: stub OpenAI-compatible server on localhost
  (stdlib `http.server` in a thread) replaying canned SSE streams — text-only,
  tool-call, malformed-JSON, and mid-stream-disconnect cases. No real API
  calls, no keys, offline-safe.
- **Agent gate**: scripted `Confirmer` drives yes/no/edit/reply paths; contract
  asserts nothing executes without `yes`/`edit` and that reply text reaches
  the model verbatim.
- **REPL**: thin by design; its dispatch table is exercised through a
  pipe-driven subprocess smoke test, everything under it via the seams above.

These seams are the contract-test surface for the three-tier dispatch workflow.

## 12. Decisions log

- **Decision:** Full interactive wrapper (Sasha owns the prompt) over a bash
  `command_not_found_handle` hook.
  **Rationale:** The hook cannot see commands that exist but fail; covering
  failed commands was a stated requirement.
- **Decision:** Per-line `bash -c` children instead of one persistent PTY bash.
  **Rationale:** Deterministic exit codes and output boundaries, testable;
  avoids expect-style sentinel scraping. Cost (aliases/functions/jobs don't
  persist) accepted and documented.
- **Decision:** stderr through a PTY tee, stdout untouched.
  **Rationale:** whiptail/dialog draw and fd-swap on stderr; a plain pipe
  breaks `isatty` and their standard idioms. Same technique as `script(1)`.
- **Decision:** OpenAI-compatible endpoint layer, profiles `deepseek` (default)
  and `local` (llama.cpp `192.168.8.253:11441`).
  **Rationale:** One wire format covers DeepSeek, local llama.cpp, and most
  providers; validated by cluade ("HTTP + a JSON parser is enough").
- **Decision:** Single `run_command` tool in v1; cluade-style multi-tool set
  deferred to v2.
  **Rationale:** The shell is a universal tool; one gate covers ~90% of agent
  value with a fraction of the permission/testing surface. v2 planned now so
  v1 interfaces (tool registry, Confirmer) survive.
- **Decision:** Every proposed command confirms in v1 (no "always allow");
  free-text replies never execute anything.
  **Rationale:** User's explicit safety requirement; trust levels arrive with
  v2's per-tool policy.
- **Decision:** Deterministic mute counters (3 ignored offers per
  `(command, exit)` pair) instead of LLM-judged annoyance.
  **Rationale:** Predictable, testable, free; an LLM judge spends tokens to
  decide whether to save tokens.
- **Decision:** Auto-consult only on exit 127 / syntax errors / `??`; other
  failures are zero-token offers.
  **Rationale:** Auto-spending tokens is fine only when the line had no other
  purpose; `grep` exiting 1 is normal life.
- **Decision:** Rolling in-session conversation with char/4 token estimate and
  oldest-first trimming; persistence deferred to v2.
  **Rationale:** Follow-ups are the product feel; exact tokenization is not
  worth a dependency (contract 1).
- **Decision:** Python ≥ 3.11, stdlib-only runtime.
  **Rationale:** User preference; `tomllib` lands in 3.11; every needed
  primitive (pty, readline, urllib, http.server for tests) is stdlib.
