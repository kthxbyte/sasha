# Task 21.1: Safe read-only command auto-approval

## Authoritative contract
`docs/superpowers/specs/2026-07-20-issue-041-safe-command-auto-approval-design.md`
(all sections) is the complete contract for this task. It amends
`docs/superpowers/specs/2026-07-17-multi-tool-registry-design.md` §4
(permission model). This task fixes ISSUE-041 (repeated confirmation-gate
friction for genuinely read-only shell scanning — `ls`, `wc`, `find`,
`head`, etc. — run through `run_command`) by making `run_command`'s
`requires_confirmation` a per-call classifier instead of a fixed `True`,
while every other tool's permission stays a fixed `bool`, unchanged.

## What already exists
- `src/sasha/tools.py` (Task 16.1) — the `Tool` frozen dataclass and the
  `REGISTRY` dict. `run_command`'s entry (currently
  `requires_confirmation=True`) is the only entry this task changes the
  *value* of; every other entry (`write_file`, `read_file`, `list_dir`,
  `search_files`, `remember`) is untouched. **Bind to the existing
  `_run_command_result`, schema, `confirmation_summary`, and
  `repetition_key` for `run_command` — do not rewrite them.**
- `src/sasha/agent.py` (Task 16.2) — `run_consult`'s dispatch loop. The
  single line `if tool.requires_confirmation:` is the one branch point
  this task changes; the confirm/execute/repetition-guard logic on either
  side of it is otherwise untouched.
- `src/sasha/executor.py` (Task 1.3/4.1/20.1) — `Executor.__init__`,
  `Executor.cwd`. **Do not touch `Executor.run()` or any job-control
  logic** — this task only adds a new constructor parameter that is read,
  never used to change execution behavior.
- `src/sasha/config.py` (Task 1.1/9.1/15.1) — `Config`/`Profile`
  dataclasses, `_DEFAULT_CONFIG_TOML`, `_build_config`, `load()`. Follow
  the existing pattern used for `[profiles.*]` and `[limits]`: a new
  section merged from `raw` over a hardcoded default, both in
  `_build_config` and in `_DEFAULT_CONFIG_TOML`.
- `src/sasha/__main__.py` — constructs the single `Executor(...)` call
  site. This task adds one keyword argument to that call.

## What's new
- **`src/sasha/safe_commands.py`** (new module) — one function:
  `requires_confirmation(args: dict, executor) -> bool`. Implements the
  7-step classifier in the design doc §4.2. Returns `True` (needs
  confirmation) by default; returns `False` only when every step passes.
- **`Tool.requires_confirmation`'s type** widens from `bool` to
  `bool | Callable[[dict, Executor], bool]`. Only `run_command`'s entry
  uses the callable form (`safe_commands.requires_confirmation`); every
  other entry keeps a literal `bool`.
- **`Tool.announce_when_auto_approved: bool = False`** — new field on the
  `Tool` dataclass, defaulting to `False` for every existing entry.
  `run_command`'s entry sets it to `True`.
- **`agent.py`'s dispatch branch** — resolves `tool.requires_confirmation`
  to a `bool` before branching (call it with `(call.arguments, executor)`
  if it's callable; use it directly if it already is a `bool`). When the
  resolved value is `False` *and* `tool.announce_when_auto_approved` is
  `True`, call
  `out(f"[sasha] ran (auto-approved): {final_arguments.get('command', '')}")`
  before invoking the handler.
- **`Executor.__init__`** gains one new keyword-only parameter,
  `sensitive_path_patterns: tuple[str, ...] = ()`, stored and exposed as
  `executor.sensitive_path_patterns` (read-only property, mirroring the
  existing `cwd` property). Default is an empty tuple so every existing
  call site and test that constructs `Executor` without the new argument
  is unaffected.
- **`config.py`**: new `Limits`-sibling dataclass or field carrying the
  denylist. Add a `sensitive_paths: tuple[str, ...]` field to `Config`
  (not `Limits` — it is not a numeric limit). Parse a new `[safe_commands]`
  TOML table with one key, `sensitive_paths` (a list of strings), merged
  the same way `[limits]` already is: present-and-valid values override a
  hardcoded default list; absent section or malformed values fall back to
  the same default. The default list (used both as the fallback and
  written into `_DEFAULT_CONFIG_TOML`) is exactly:
  ```
  ["~/.ssh/**", "/etc/passwd", "/etc/shadow*", "/etc/sudoers*",
   "~/.aws/**", "~/.gnupg/**", "~/.kube/**", "~/.config/gcloud/**",
   "~/.docker/config.json", "~/.netrc", "~/.pgpass",
   "~/.git-credentials", "~/.npmrc", "**/.env", "**/.env.*"]
  ```
- **`__main__.py`**: pass `sensitive_path_patterns=config.sensitive_paths`
  (or equivalent field name chosen above) into the `Executor(...)` call.

## The hardcoded allowlist (verbatim, do not add to or remove from)
```
ls, pwd, wc, cat, head, tail, file, stat, du, find, grep, tree,
sort, uniq, cut, diff, echo
```
Lives as a module-level constant (e.g. `_ALLOWLISTED_VERBS`) in
`safe_commands.py`. This exact set is what `test_ac1`/`test_ac2`/`test_ac7`
below pin — do not add commands (e.g. `xargs`, `awk`, `sed`, `tee`) beyond
this list even if they seem read-only; that is a deliberate scope
boundary from the design (§6), not an oversight.

## Classifier algorithm (design §4.2, authoritative — implement exactly)
Given `args["command"]` (raw string) and `executor`:
1. Reject (`return True`) if the raw string contains `` ` ``, `$(`, `>`,
   or `>>` (checking for `>` as a substring also catches `>>`).
2. **Pad operators, then tokenize.** `shlex.split` only splits `&&`/`;`/
   `|`/`||`/`&` into their own tokens when they're whitespace-separated on
   both sides (`"ls;pwd"` tokenizes as one glued token). First apply
   `re.sub(r'(\|\||&&|;|\||&)', r' \1 ', command)`, then
   `shlex.split(...)` the padded string; reject on `ValueError`. (A quoted
   operator, e.g. `echo "a;b"`, can be mis-split into extra segments by
   this padding — always fails *closed*, never open, an accepted
   limitation, not a correctness bug.)
3. Split the token stream into segments at `&&`, `;`, `|`, `||` (now their
   own tokens after step 2); reject if a bare `&` token appears anywhere.
4. For every segment, its first token must be exactly one of the
   allowlisted verbs above (exact string match — not a prefix, not a
   path-qualified form like `/bin/ls`, not a variable assignment like
   `FOO=bar`). Any segment whose first token doesn't match: reject.
5. Reject if any token contains an unexpanded `$` (a literal `$`
   character anywhere in the token — sasha cannot safely resolve shell
   variables without running the command).
6. For every token in every segment that does not start with `-` (i.e.
   not a flag) and is not itself one of the verbs/operators already
   classified in steps 3-4: expand a leading `~` via
   `os.path.expanduser`, then resolve to an absolute path via
   `os.path.normpath`, joining against `executor.cwd` first if the token
   is not already absolute (this normalization is only to collapse `..`/
   `.` segments before matching — it is not a sandboxing boundary; paths
   outside `executor.cwd` are otherwise allowed). Match the normalized
   path against every pattern in `executor.sensitive_path_patterns` using
   `fnmatch.fnmatch`; expand `~` in each pattern the same way before
   matching. Any match: reject.
7. If every check passed: `return False`.

## Fix sketch (authoritative — verified directly)

This has been verified directly (Tier 1 applied it to a scratch git
worktree and ran the full contract suite — 533/533 tests green, including
all 37 new ACs below and the three pre-existing tests amended alongside
this spec, see "Pre-existing contract tests amended" below) before being
committed as authoritative. Implement exactly as given; five files change,
one file is new.

**New file — `src/sasha/safe_commands.py`:**

```python
from __future__ import annotations

import fnmatch
import os
import re
import shlex

_ALLOWLISTED_VERBS = frozenset({
    "ls", "pwd", "wc", "cat", "head", "tail", "file", "stat", "du", "find",
    "grep", "tree", "sort", "uniq", "cut", "diff", "echo",
})

_OPERATORS = frozenset({"&&", ";", "|", "||"})

_OPERATOR_RE = re.compile(r'(\|\||&&|;|\||&)')


def _tokenize(command: str) -> list[str]:
    padded = _OPERATOR_RE.sub(r' \1 ', command)
    return shlex.split(padded)


def _split_segments(tokens: list[str]) -> list[list[str]] | None:
    segments: list[list[str]] = []
    current: list[str] = []
    for tok in tokens:
        if tok == "&":
            return None
        if tok in _OPERATORS:
            segments.append(current)
            current = []
        else:
            current.append(tok)
    segments.append(current)
    if any(not seg for seg in segments):
        return None
    return segments


def _normalize(token: str, cwd: str) -> str:
    expanded = os.path.expanduser(token)
    if not os.path.isabs(expanded):
        expanded = os.path.join(cwd, expanded)
    return os.path.normpath(expanded)


def requires_confirmation(args: dict, executor) -> bool:
    command = args.get("command", "")

    if "`" in command or "$(" in command or ">" in command:
        return True

    try:
        tokens = _tokenize(command)
    except ValueError:
        return True

    segments = _split_segments(tokens)
    if segments is None:
        return True

    for segment in segments:
        if segment[0] not in _ALLOWLISTED_VERBS:
            return True

    for tok in tokens:
        if tok in _OPERATORS or tok in _ALLOWLISTED_VERBS:
            continue
        if "$" in tok:
            return True

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

**`src/sasha/tools.py`** — three edits:
1. Add `from sasha import safe_commands` alongside the existing `from
   sasha import memory` import.
2. `Tool`'s field list: change `requires_confirmation: bool` to
   `requires_confirmation: bool | Callable[[dict, Executor], bool]`, and
   add one new field at the end: `announce_when_auto_approved: bool =
   False`.
3. `run_command`'s registry entry: change `requires_confirmation=True` to
   `requires_confirmation=safe_commands.requires_confirmation`, and add
   `announce_when_auto_approved=True` (as the last field in that entry).
   No other registry entry changes.

**`src/sasha/agent.py`** — in `run_consult`'s dispatch loop, replace:
```python
            if tool.requires_confirmation:
```
with:
```python
            needs_confirmation = tool.requires_confirmation
            if callable(needs_confirmation):
                needs_confirmation = needs_confirmation(call.arguments, executor)

            if needs_confirmation:
```
and replace the trailing `else:` branch:
```python
            else:
                result = tool.handler(final_arguments, executor)
                result_str = result.text
                executed = True
```
with:
```python
            else:
                if tool.announce_when_auto_approved:
                    out(f"[sasha] ran (auto-approved): {final_arguments.get('command', '')}")
                result = tool.handler(final_arguments, executor)
                result_str = result.text
                executed = True
```

**`src/sasha/executor.py`** — `Executor.__init__` and the `cwd` property:
```python
    def __init__(self, cwd: str, env: dict[str, str], *,
                 stderr_capture_bytes: int = 8192,
                 sensitive_path_patterns: tuple[str, ...] = ()) -> None:
        self._cwd = os.path.realpath(cwd)
        self._env = dict(env)
        self._stderr_capture_bytes = stderr_capture_bytes
        self._sensitive_path_patterns = sensitive_path_patterns
        self._last_command: str | None = None
        self._last_result: ExecResult | None = None

    @property
    def cwd(self) -> str:
        return self._cwd

    @property
    def sensitive_path_patterns(self) -> tuple[str, ...]:
        return self._sensitive_path_patterns
```
(everything else in `Executor` — `run()`, job-control, PTY capture — is
byte-for-byte unchanged).

**`src/sasha/config.py`** — four edits:
1. Before the `Config` dataclass, add:
   ```python
   _DEFAULT_SENSITIVE_PATHS: tuple[str, ...] = (
       "~/.ssh/**", "/etc/passwd", "/etc/shadow*", "/etc/sudoers*",
       "~/.aws/**", "~/.gnupg/**", "~/.kube/**", "~/.config/gcloud/**",
       "~/.docker/config.json", "~/.netrc", "~/.pgpass",
       "~/.git-credentials", "~/.npmrc", "**/.env", "**/.env.*",
   )
   ```
2. Add one field to `Config`, right after `prompt`:
   `sensitive_paths: tuple[str, ...] = _DEFAULT_SENSITIVE_PATHS`.
3. Append to `_DEFAULT_CONFIG_TOML` (after the `[profiles.local]` table):
   ```toml

   [safe_commands]
   sensitive_paths = [
     "~/.ssh/**", "/etc/passwd", "/etc/shadow*", "/etc/sudoers*",
     "~/.aws/**", "~/.gnupg/**", "~/.kube/**", "~/.config/gcloud/**",
     "~/.docker/config.json", "~/.netrc", "~/.pgpass",
     "~/.git-credentials", "~/.npmrc", "**/.env", "**/.env.*",
   ]
   ```
4. In `_build_config`, right after the `limits = _merge_limits(...)` line:
   ```python
       sensitive_paths = _DEFAULT_SENSITIVE_PATHS
       safe_commands_table = raw.get("safe_commands", {})
       if isinstance(safe_commands_table, dict):
           raw_paths = safe_commands_table.get("sensitive_paths")
           if isinstance(raw_paths, list) and all(isinstance(p, str) for p in raw_paths):
               sensitive_paths = tuple(raw_paths)
   ```
   and add `sensitive_paths=sensitive_paths,` to the `return Config(...)`
   call.

**`src/sasha/__main__.py`** — the single `Executor(...)` construction gains
one keyword argument:
```python
    executor = Executor(
        os.getcwd(),
        dict(os.environ),
        stderr_capture_bytes=limits.stderr_capture_bytes,
        sensitive_path_patterns=config.sensitive_paths,
    )
```

## Pre-existing contract tests amended (Tier 1, this same commit)

This is a deliberate, documented contract change (ISSUE-033's "permission
fixed by tool type" invariant now has one named exception). Three
pre-existing tests, all for already-`Done` tasks and therefore in
`scripts/run-tests.sh`'s selection, were amended by Tier 1 in the same
commit as this spec — **the implementer must not touch any of these, or
any other file under `tests/contract/`:**
- `tests/contract/test_task_16_1_tools.py::test_ac_permission_fixed_by_tool_type`
  — assertion changed from `is True` to `callable(...)` for `run_command`
  only; every other tool's assertion is unchanged.
- `tests/contract/test_task_2_3_agent.py::test_ac2_yes_executes_and_reports`
  — example command changed from `"ls -la /var"` (now auto-approved) to
  `"mv a b"` (not allowlisted, stays gated); the SpyExecutor there also
  gained a `sensitive_path_patterns = ()` class attribute, needed because
  the classifier now runs for every `run_command` call regardless of
  whether the outcome is auto-approval or confirmation.
- `tests/contract/test_task_3_4_repl_streaming.py::test_ac2_text_precedes_confirmation`
  — example command changed from `"ls"` to `"touch /tmp/marker"` for the
  same reason; its `SpyExecutor` gained the same
  `sensitive_path_patterns = ()` attribute.

## Constraints
- Modify `src/sasha/tools.py`, `src/sasha/agent.py`, `src/sasha/executor.py`
  (constructor only), `src/sasha/config.py`, `src/sasha/__main__.py`, and
  add `src/sasha/safe_commands.py`.
- Do not touch: `scripts/`, `docs/superpowers/`, `CLAUDE.md`, `AGENTS.md`,
  `PLAN.md`, `ISSUE-TRACKING.md`, `README.md`, `.gitignore`,
  `pyproject.toml`, `src/sasha/__init__.py`, `src/sasha/triggers.py`,
  `src/sasha/repl.py`, `src/sasha/mutes.py`, `src/sasha/completion.py`,
  `src/sasha/memory.py`, `src/sasha/personality.py`, `src/sasha/prompt.py`,
  `src/sasha/session.py`, `src/sasha/llm.py`, `tests/contract/`.
- Do not touch `Executor.run()`'s body, job-control logic, or the
  stderr/stdout PTY capture mechanism — this task only adds a constructor
  parameter and a read-only accessor.
- Do not add commands to, or remove commands from, the hardcoded
  allowlist beyond exactly what's listed above.
- **Executor routing (per CLAUDE.md's standing rule): this task edits
  existing control flow in `tools.py`/`agent.py`/`config.py`/`executor.py`
  that already works (surgical), even though `safe_commands.py` itself is
  new. Dispatch to the default model (deepseek-v4-pro) — do not launch
  under a local-model `SASHA_DISPATCH_MODEL` override.**

## Acceptance criteria
(encoded 1:1 in `tests/contract/test_task_21_1_safe_command_auto_approval.py`)
1. The four real transcript commands (`pwd && ls FILE`, `head -50 file`,
   `ls -la dir && echo --- && pwd`, `wc -l *.txt`) classify as not
   requiring confirmation — `test_ac1_real_transcript_commands_auto_approve`.
2. Chains of allowlisted verbs joined by `;`, `|`, or `||` (not just
   `&&`) also auto-approve — `test_ac2_chained_allowlisted_verbs_auto_approve`.
3. Output redirection (`>`, `>>`) anywhere in the command requires
   confirmation — `test_ac3_redirection_requires_confirmation`.
4. Command substitution (backtick or `$(`) requires confirmation —
   `test_ac4_command_substitution_requires_confirmation`.
5. Unparseable quoting (`shlex.split` raises) requires confirmation —
   `test_ac5_unparseable_quoting_requires_confirmation`.
6. Backgrounding (bare `&`) requires confirmation —
   `test_ac6_backgrounding_requires_confirmation`.
7. An unrecognized leading token in any segment — an unlisted command, a
   variable assignment (`FOO=bar ls`), or a path-qualified invocation
   (`/bin/ls`) — requires confirmation, even when every other segment is
   allowlisted — `test_ac7_unrecognized_verb_requires_confirmation`.
8. A token containing an unexpanded shell variable (`$HOME`, `$UNSET`)
   requires confirmation — `test_ac8_unexpanded_variable_requires_confirmation`.
9. A path argument matching any sensitive-path denylist pattern requires
   confirmation, covering at least one pattern from each category
   (dotfile-glob, exact file, wildcard-suffix, any-depth glob) —
   `test_ac9_denylisted_path_requires_confirmation`.
10. A relative path that normalizes (via `executor.cwd`) into a
    denylisted path requires confirmation, even though the raw token
    doesn't literally match the pattern —
    `test_ac10_path_traversal_into_denylisted_path_requires_confirmation`.
11. In `agent.py`'s dispatch: a `run_command` call classified safe skips
    the confirmer entirely and the `out` callback receives an
    `(auto-approved)` line containing the command —
    `test_ac11_dispatch_auto_approves_safe_and_announces`. The
    unsafe-still-gated and other-tools-unaffected paths are unchanged
    behavior and are not separately re-tested here (a test asserting them
    would pass vacuously against the pre-fix baseline, per CLAUDE.md's
    contract-test rule) — they stay pinned by
    `tests/contract/test_task_16_2_agent_registry.py` and
    `tests/contract/test_task_2_3_agent.py`, both of which must stay green
    unmodified as part of this task's own gate.
12. A freshly self-installed `config.toml` contains the default
    `[safe_commands]` section with the exact default `sensitive_paths`
    list; loading a pre-existing `config.toml` that has no
    `[safe_commands]` section at all still yields the same default list
    — `test_ac12_config_default_and_fallback`.

## Standing instructions
- Your goal: make
  `tests/contract/test_task_21_1_safe_command_auto_approval.py` pass, with
  `tests/contract/test_task_16_1_tools.py`,
  `tests/contract/test_task_16_2_agent_registry.py`,
  `tests/contract/test_task_2_3_agent.py`,
  `tests/contract/test_task_3_4_repl_streaming.py`,
  `tests/contract/test_task_1_1_config.py`,
  `tests/contract/test_task_1_3_executor_core.py`, and
  `tests/contract/test_task_1_4_executor_pty.py` still green (the four
  named tool/agent/streaming files already reflect this task's contract
  change — see "Pre-existing contract tests amended" above — so "green"
  here means passing exactly as committed, not passing their pre-Task-21.1
  form):
  `PYTHONPATH=src python3 -m pytest tests/contract/test_task_1_1_config.py tests/contract/test_task_1_3_executor_core.py tests/contract/test_task_1_4_executor_pty.py tests/contract/test_task_16_1_tools.py tests/contract/test_task_16_2_agent_registry.py tests/contract/test_task_2_3_agent.py tests/contract/test_task_3_4_repl_streaming.py tests/contract/test_task_21_1_safe_command_auto_approval.py`.
  Simplest to just run the full suite instead, since it's what `verify.sh`
  actually gates on: `bash scripts/run-tests.sh`.
- NEVER modify files under `tests/contract/` — the gate rejects the commit
  if you do. If a contract test seems wrong or unimplementable, stop and
  flag it; do not commit.
- You may add your own supplementary tests under `tests/impl/`.
- Deliver exactly ONE new commit on top of the HEAD you started from;
  never amend, rebase, or reset; leave the working tree clean.
- Commit message per AGENTS.md, with trailers:
  `Done: Task 21.1 — <one line>`, `Fixed: ISSUE-041`,
  `Next: Task 21.2 — Phase 21 review (Tier 1, with Salvador)`, and the
  Co-Authored-By signature line from your dispatch prompt.
