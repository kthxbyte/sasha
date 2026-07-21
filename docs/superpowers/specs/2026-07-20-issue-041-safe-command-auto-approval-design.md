# Safe read-only command auto-approval — Design (ISSUE-041)

**Status:** Draft for review
**Date:** 2026-07-20
**Amends:** `docs/superpowers/specs/2026-07-17-multi-tool-registry-design.md` §4 (permission model)

## 1. Mission

Reduce confirmation-gate friction for genuinely read-only shell scanning
(`ls`, `wc`, `find`, `head`, ...) run through the `run_command` tool, without
reinventing those standard Unix utilities as bespoke tools. Four real
transcripts (`pwd && ls FILE`, `head -50 file.txt`, `ls -la dir && echo ---
&& pwd`, `wc -l *.txt`) were analyzed: three of four turned out not to need
any new capability at all — the current working directory is already in
every LLM context (`Session.build_messages(cwd)`), and `read_file` already
returns a clean "no such file" on a missing path and already returns up to
40,000 characters before truncating, covering both the existence-check and
format-preview use cases the LLM was reaching for `run_command` to solve.
Only line-counting (`wc`) is a genuine capability gap. Salvador wants
`run_command` itself to skip confirmation for read-only invocations rather
than adding more structured tools — this design makes that decision
per-call and safe, using the existing tool-registry pattern rather than a
new mechanism.

## 2. Scope

### In scope:

- Extend the tool registry's `requires_confirmation` field to accept a
  per-call classifier (in addition to the fixed `bool` every other tool
  already uses), wired to `run_command` only.
- A hardcoded verb allowlist + a config-editable sensitive-path denylist,
  combined into a fail-closed classifier: auto-approve only when the whole
  command is verifiably read-only and touches nothing sensitive; require
  confirmation for everything else, including anything the classifier
  can't cleanly parse.
- A visible marker (`[sasha] ran (auto-approved): <command>`) whenever this
  specific mechanism skips confirmation, so the auto-approval stays
  observable while trust in the classifier builds.

### Out of scope / non-goals:

- **The `local: bool` profile field and any privacy-warning feature for
  remote LLM providers.** Separate concern (exfiltration risk, not
  destructive-action risk), separate design, discussed but deliberately not
  designed here.
- **cwd path-sandboxing for `run_command`.** Unlike `read_file`/`list_dir`/
  `search_files`, this auto-approval does not confine paths to the project
  directory — the sensitive-path denylist is the accepted mitigation
  instead (§6).
- **Full shell-AST parsing.** The classifier is a fail-closed string/token
  check, not a real shell grammar — anything it can't confidently classify
  as safe simply keeps the existing confirmation behavior.
- **Making the verb allowlist user-configurable.** Explicitly rejected —
  see §6.

## 3. Key contracts & assumptions

1. **The two lists are not symmetric in risk, and are not both mutable the
   same way.** The sensitive-path denylist is purely safety-additive (a new
   entry can only make the system more cautious) and lives in
   `config.toml`, editable without a code change. The verb allowlist is the
   opposite — a careless addition directly reopens the risk this feature
   exists to close — and stays hardcoded in source, receiving the same
   scrutiny as adding a tool to the registry.
2. **Fail closed, always.** Any command the classifier doesn't confidently
   recognize as safe requires confirmation exactly as it does today. This
   applies to unparseable quoting, unrecognized command names, redirection,
   substitution, unexpanded shell variables, and denylisted paths alike —
   none of these block the command outright, they just mean the existing
   y/n/e gate still applies.
3. **This changes behavior for `run_command` only.** Every other tool's
   `requires_confirmation` stays a fixed `bool`, unchanged.
4. **The repetition/mute guard is untouched.** `agent.py`'s existing
   `(tool_name, repetition_key)` loop-detection already runs for every
   executed call regardless of whether it was confirmed or auto-approved
   (confirmed by reading the current dispatch loop) — no change needed
   there.

## 4. Mechanism

### 4.1 Registry interface change

`Tool.requires_confirmation`'s type becomes
`bool | Callable[[dict, Executor], bool]` — the same `(args, executor)`
shape `Tool.handler` already uses, so the classifier can read `executor.cwd`
for path resolution and the config-loaded denylist off `executor` (§4.3).
Every tool except `run_command` keeps a plain `bool`. `Tool` also gains one
new field, `announce_when_auto_approved: bool = False`, `True` only for
`run_command`'s entry.

In `agent.py`'s dispatch loop, the single line
`if tool.requires_confirmation:` becomes: resolve to a bool first (call it
if it's callable, passing `call.arguments` and `executor`; use it directly
if it's already a bool), then branch exactly as today. When the resolved
value is `False` *and* `tool.announce_when_auto_approved` is `True`, call
`out(f"[sasha] ran (auto-approved): {final_arguments.get('command', '')}")`
before invoking the handler. No other tool sets that flag, so no other
tool's behavior changes.

### 4.2 Classifier algorithm (`src/sasha/safe_commands.py`)

One function, `requires_confirmation(args: dict, executor: Executor) ->
bool`, returning `True` (needs confirmation) by default and `False` only
when every check below passes, in order:

1. Reject if the raw command string contains `` ` ``, `$(`, `>`, or `>>`.
2. Tokenize with `shlex.split`; reject on a `ValueError` (unbalanced
   quoting).
3. Split the token stream into segments at `&&`, `;`, `|`, `||`; reject if a
   bare `&` (backgrounding) appears anywhere.
4. Every segment's leading token must exactly match one of the hardcoded
   allowlisted verbs (§4.4). Any other leading token — unrecognized command,
   variable assignment (`FOO=bar`), path-qualified invocation (`/bin/ls`) —
   fails closed.
5. Reject if any token contains an unexpanded `$` (a shell variable
   reference sasha cannot safely resolve without running the command).
6. For every non-flag token (doesn't start with `-`): expand `~`, then
   resolve it to an absolute path (`os.path.normpath`, joined against
   `executor.cwd` if not already absolute — this is purely for collapsing
   `..`/`.` segments before matching, not a sandboxing boundary per §2's
   non-goals) and check the result against the sensitive-path denylist
   (`fnmatch`-style glob match); any match fails closed. Normalizing before
   matching is what keeps `../../etc/passwd` from slipping past the literal
   `/etc/passwd` pattern.
7. If every check above passed: return `False` (skip confirmation).

### 4.3 Denylist plumbing

`Executor` gains one new constructor parameter, `sensitive_path_patterns:
tuple[str, ...]`, populated once at startup in `__main__.py` from
`Config`/a new `config.toml` section (§4.5) — the same way `cwd`/`env`
already flow into `Executor`. `safe_commands.requires_confirmation` reads
`executor.sensitive_path_patterns` directly; it takes no other new
dependency.

### 4.4 The hardcoded allowlist

```
ls, pwd, wc, cat, head, tail, file, stat, du, find, grep, tree,
sort, uniq, cut, diff, echo
```

Lives as a module-level constant in `safe_commands.py`. Changing this list
is a source change like any other — reviewed the same way a new
auto-approved tool would be, not a config edit.

### 4.5 Config schema

New `[safe_commands]` section in `config.toml`:

```toml
[safe_commands]
sensitive_paths = [
  "~/.ssh/**", "/etc/passwd", "/etc/shadow*", "/etc/sudoers*",
  "~/.aws/**", "~/.gnupg/**", "~/.kube/**", "~/.config/gcloud/**",
  "~/.docker/config.json", "~/.netrc", "~/.pgpass",
  "~/.git-credentials", "~/.npmrc", "**/.env", "**/.env.*",
]
```

This exact list is the built-in default: written into a freshly
self-installed `config.toml` (matching the existing self-install pattern
from Task 15.1), and used as the fallback when an existing (pre-this-
feature) `config.toml` doesn't have the section at all — so upgrading
users get the protection without hand-editing anything.

## 5. Interface changes summary

- `Tool.requires_confirmation: bool | Callable[[dict, Executor], bool]`
  (was `bool`).
- `Tool.announce_when_auto_approved: bool = False` (new field).
- `Executor.__init__` gains `sensitive_path_patterns: tuple[str, ...]`.
- New module `src/sasha/safe_commands.py`.
- New `config.toml` section `[safe_commands]`.

No changes to `ExecResult`, `ToolResult`, or any other tool's schema.

## 6. Decisions log

- **Decision:** Sensitive-path denylist lives in `config.toml`
  (user-editable); verb allowlist stays hardcoded in source.
  **Rationale:** The two lists carry opposite risk when grown. A denylist
  addition only ever makes the system more cautious; an allowlist addition
  is exactly the action that could reopen the risk the feature exists to
  close. This mirrors ISSUE-033's existing decision that tool permission is
  fixed by code, not config — extended here to say *which* list gets that
  protection and which doesn't, rather than applying it uniformly to both.
- **Decision:** Auto-approved `run_command` calls print a visible
  `[sasha] ran (auto-approved): ...` line; the pre-existing auto-approved
  tools (`read_file` etc.) stay silent, unchanged.
  **Rationale:** Salvador's call — this mechanism is new and its
  classifier hasn't earned the same silent trust yet; visibility costs
  nothing and keeps the behavior inspectable while confidence builds.
- **Decision:** No cwd path-sandboxing for this auto-approval path.
  **Rationale:** Salvador's call, made explicit rather than assumed — the
  denylist is the accepted mitigation for the "reads outside the project"
  concern instead of a hard boundary.
- **Decision:** Fail closed on every ambiguous case (unparseable quoting,
  unrecognized verb, unexpanded variable, denylisted path) rather than
  attempting a best-effort classification.
  **Rationale:** This is what makes a low-effort, non-shell-AST classifier
  acceptable — every failure mode falls back to today's existing
  confirmation behavior, never to unconfirmed execution.

## 7. Testing strategy

Pure string/token logic — no PTY or real-terminal machinery needed (unlike
the executor.py job-control work), just table-driven unit tests:

- **Safe cases** (expect `requires_confirmation() == False`): the four real
  transcript commands and chains of allowlisted verbs joined by `&&`/`;`/
  `|`.
- **Unsafe cases** (expect `True`): output redirection, command
  substitution, an unrecognized command anywhere in the chain, background
  (`&`), an unexpanded shell variable, a path matching each denylist
  pattern category (`~/.ssh/id_rsa`, `/etc/passwd`, `.env`, etc.), and a
  relative-path traversal that normalizes into a denylisted path (e.g.
  `cat ../../etc/passwd`).
- **Registry/dispatch test**: a `run_command` call classified safe skips
  the confirmer entirely and still shows the `(auto-approved)` message; a
  call classified unsafe goes through the confirmer exactly as today; no
  other tool's dispatch behavior changes.
- **Config test**: a fresh self-installed `config.toml` contains the
  default `[safe_commands]` section; loading an existing config.toml
  without that section falls back to the same default list.
