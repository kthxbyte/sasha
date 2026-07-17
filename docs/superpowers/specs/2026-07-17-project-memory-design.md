# Sasha — Project Memory Design Specification (Phase 17)

**Status:** Draft for review
**Date:** 2026-07-17

## 1. Mission

Today Sasha forgets everything the moment it exits — every session starts
from zero, no matter how much context you and the LLM built up working in a
project together. This design gives Sasha **project-scoped long-term
memory**: git repositories get a durable, automatically git-ignored record
of what's worth remembering — commit gists and deliberately-noted
conclusions — separate from the existing ephemeral working memory
(`Session.notes`/`last_stderr`), retrievable on demand through the tool
Sasha already has (`read_file`), and requiring zero configuration from the
user. Non-git directories are completely unaffected — this is additive,
not a redesign of anything that exists.

## 2. Scope

### In scope (v1):
- Git-repo-scoped long-term memory stored at `<repo-root>/.sasha/memory/`:
  `semantic.jsonl` (narrative entries) + `episodic/` (verbatim command
  records).
- Automatic encoding on every successful `git commit`: the commit message
  becomes a semantic entry, the commit hash becomes its pointer.
- Deliberate encoding via a new `remember(note)` tool — sixth entry in the
  Phase 16 registry, auto-approved (no confirmation prompt).
- Automatic episodic capture: when `remember` is called and a command has
  actually run since Sasha started, the handler writes that command's full
  `ExecResult` verbatim to `episodic/` and attaches its path as the
  semantic entry's pointer.
- Loading: a capped, chronologically-ordered block of semantic entries
  folded into the system prompt — re-resolved only when the git root
  changes, not on every turn.
- Place-shift detection: re-run `git rev-parse --show-toplevel` whenever
  `Executor.cwd` changes since the last check (covers both session start
  and mid-session `cd`).
- Two user-facing one-time/one-shot notices: entering a repo that already
  has memory (`[sasha] project: ...`), and a repo's memory being created
  for the first time (`[sasha] created .sasha/memory/ ...`).
- New built-ins: `sasha memory` (metrics + browse) and `sasha forget`
  (delete the current repo's memory, with confirmation).
- An unconditional, automatic `.sasha/.gitignore` (containing `*`),
  written atomically before any memory content exists, guaranteeing
  `.sasha/memory/` can never be accidentally committed.

### Out of scope / non-goals:
- Any user configuration of this feature (on/off switches, thresholds,
  retention policy). It's automatic, the same way `PERSONALITY.md` and
  `config.toml` self-install with no setup step.
- Cross-worktree memory sharing. `git rev-parse --show-toplevel` returns
  the worktree root; each worktree is its own "place" with its own memory.
  Not solved here, not treated as a bug.
- Locking or conflict resolution for concurrent writers. Correctness comes
  from small atomic appends (`O_APPEND`), not mutual exclusion — see §3.5
  and §5.4.
- A persistent git-branch/status indicator in the Sasha prompt itself.
  Explicitly considered and rejected for this phase — it's a recurring,
  per-render cost unrelated to memory, and belongs (if ever built) as an
  opt-in `prompt.py` template token, not a default-on feature. Log as a
  future issue if wanted; not specced here.
- Mid-session rolling/incremental summarization, or blanket
  end-of-session summarization. Encoding is either automatic (commit) or
  deliberate (the LLM/user explicitly decides something is worth keeping,
  via `remember`) — never a blanket "summarize everything that happened."
- Literal conversation replay. `sasha --resume` in the sense of "reopen
  the exact past transcript" is not what this builds — memory is folded
  into the system prompt as distilled context, not replayed as fake
  history turns.
- Pruning/rotating `semantic.jsonl` or `episodic/` on disk. Both grow
  indefinitely absent an explicit `sasha forget`; only what's loaded into
  *active* context is capped (§5.2).

## 3. Key contracts & assumptions

1. **The git repository root is the sole context key for long-term
   memory.** Determined by `git rev-parse --show-toplevel` against the
   current `Executor.cwd`. No repo, no memory — behavior is byte-identical
   to today's.
2. **Two-tier memory, matching the split between semantic and episodic
   memory:** narrative entries (always loaded, small, human-readable) vs.
   verbatim records (large, retrieved on demand, never auto-loaded). A
   semantic entry's `pointer` is the bridge between them.
3. **Encoding is never automatic-and-blanket.** Only two triggers ever
   write a semantic entry: a successful `git commit`, or an explicit
   `remember` tool call. Nothing summarizes a session just because it
   happened.
4. **`.sasha/memory/` creation is lazy and self-protecting.** Nothing is
   created until the first actual encoding event. When it is created,
   `.sasha/.gitignore` (containing `*`) is written first, as part of the
   same operation that creates the directory — there is no window where
   `.sasha/` exists on disk without already being invisible to git.
5. **Concurrency safety comes from write shape, not from locks.** Multiple
   Sasha processes (same repo, different terminals) or a single process
   moving between repos all just append. `semantic.jsonl` only ever
   receives small, single-line appends; `episodic/` files are never shared
   between writers — each capture gets its own new timestamped file.
6. **Non-git `cwd` is unaffected.** Zero new behavior, zero new files,
   identical to Sasha today.
7. **Nothing about this feature can block, error, or visibly break the
   shell.** Every failure mode degrades to "no persistence right now" —
   silently for the common/expected cases (not a repo), with at most a
   one-time notice for structural failures (can't write to disk).

## 4. Architecture & on-disk format

```
<repo-root>/.sasha/memory/
  ├── semantic.jsonl        # append-only, one JSON object per line
  └── episodic/
      ├── 2026-07-17T14-32-01.log
      └── 2026-07-17T18-05-44.log
<repo-root>/.sasha/.gitignore     # contains a single line: *
```

**`semantic.jsonl` entry shape** (one per line):

```json
{"timestamp": "2026-07-17T14:32:01Z", "kind": "commit", "text": "Track Task 16.2", "pointer": "ed63bcf4edb30100d2b94ece4c9bb0a1ace1782"}
{"timestamp": "2026-07-17T18:05:44Z", "kind": "remember", "text": "the flaky test was a race in the mute-store write, not the test itself", "pointer": ".sasha/memory/episodic/2026-07-17T18-05-44.log"}
{"timestamp": "2026-07-17T18:07:12Z", "kind": "remember", "text": "user prefers tests that build real tmp_path git repos over mocked subprocess calls", "pointer": null}
```

- `timestamp`: ISO 8601 UTC, standard colon form (this is JSON text, not a
  filename — no collision with filesystem constraints).
- `kind`: `"commit"` or `"remember"`.
- `text`: for `commit`, the full commit message (`git log -1
  --format=%B`, whitespace preserved); for `remember`, the LLM-authored
  note verbatim.
- `pointer`: for `commit`, the full 40-character commit hash (retrievable
  with `git show <hash>` — no Sasha-owned copy of the diff, git's own
  object store already is the verbatim record); for `remember`, a
  repo-root-relative path to an episodic log file, or `null` if no
  command was in scope when `remember` was called.

**Episodic log file** (`episodic/<timestamp>.log`, plain text, one per
capture — filename timestamp uses `-` in place of `:` for filesystem
safety):

```
command: pytest tests/contract/test_task_16_1_tools.py -k search_files
exit code: 1
duration: 340ms
--- stdout ---
<verbatim ExecResult.stdout_text>
--- stderr ---
<verbatim ExecResult.stderr_text>
```

**`Executor` gains two small pieces of internal state**, updated at the
end of every `run()` call (mirroring how it already owns `cwd`):
`last_command: str | None` and `last_result: ExecResult | None`, exposed
as read-only properties. This is what lets the `remember` tool's handler
attach a verbatim record without the `Tool.handler(args, executor)`
signature changing at all — no Phase 16 interface touched, no
special-casing in `agent.py`'s dispatch loop.

**`Session` gains a mutable memory block.** Unlike `personality` (fixed
once at construction), the loaded memory has to change mid-session when
the git root changes. `_build_system_prompt()` (already private) stops
being computed once in `__init__` and instead recomputes on demand from
`self._personality` + `self._memory_block` + the existing functional
text; `system_prompt_dict()` (already called fresh on every
`build_messages()`) calls it fresh too. A new `Session.set_memory(text:
str) -> None` sets `self._memory_block` and nothing else — the next
`build_messages()` call picks it up automatically, no other call site
changes.

## 5. Mechanism

### 5.1 Place-shift detection (repl.py)

Runs once per executed command (cheap: a string comparison; the git
subprocess call only happens when `cwd` actually changed), in the same
vicinity `run_once` already inspects a command's result for the trigger
engine — independent of whether that command triggers `AUTO_CONSULT`.

```
def _check_place_shift(self):
    cwd = self._executor.cwd
    if cwd == self._last_checked_cwd:
        return
    self._last_checked_cwd = cwd
    new_root = memory.resolve_git_root(cwd)      # None if not a repo
    if new_root == self._current_repo_root:
        return
    self._current_repo_root = new_root
    if new_root is None:
        self._session.set_memory("")
        return
    block, count = memory.load_memory_block(new_root, max_chars=8000)
    self._session.set_memory(block)
    if count:
        gist = memory.most_recent_gist(new_root)
        self._sink.write_line(
            f'[sasha] project: {new_root.name} ({count} memories, '
            f'last: "{gist}")')
```

This also runs once at `Repl.loop()` startup (same call, `cwd` is
whatever Sasha started in) — starting inside a repo with existing memory
goes through the identical "entering a repo" path as a later `cd` would,
not a separate boot-time code path.

### 5.2 Loading & capping (`memory.load_memory_block`)

```
def load_memory_block(repo_root, max_chars):
    path = repo_root / ".sasha" / "memory" / "semantic.jsonl"
    if not path.exists():
        return "", 0
    entries = []
    for line in path.read_text().splitlines():
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue          # skip a torn/malformed line, keep the rest
    total = len(entries)
    kept = []
    used = 0
    for entry in reversed(entries):           # newest first while fitting
        rendered = f"- [{entry['timestamp'][:10]}] {entry['text']}"
        if used + len(rendered) > max_chars:
            break
        kept.append(rendered)
        used += len(rendered)
    kept.reverse()                            # back to chronological order
    return "\n".join(kept), total
```

Whole entries are dropped from the oldest end once the budget's exceeded
— never a mid-entry character truncation (unlike `PERSONALITY.md`'s flat
truncation, these are discrete structured records, not free prose; cutting
one off mid-sentence would be worse than dropping it entirely). `total`
(not `len(kept)`) is what the place-shift notice reports — "14 memories"
means the repo has 14, even if only the most recent handful actually made
it into this session's context.

### 5.3 Commit-triggered encoding (repl.py, alongside 5.1)

```
def _check_commit(self, line, result):
    if result.exit_code != 0 or command_word(line) != "git":
        return
    if not _is_git_commit_invocation(line):     # "git commit", "git commit -m ...", etc.
        return
    root = self._current_repo_root
    if root is None:
        return
    message = self._executor.run("git log -1 --format=%B", capture_stdout=True)
    commit_hash = self._executor.run("git log -1 --format=%H", capture_stdout=True)
    if message.exit_code != 0 or commit_hash.exit_code != 0:
        return                                   # skip silently — see §6 error handling
    memory.encode_commit(root, message.stdout_text.strip(),
                          commit_hash=commit_hash.stdout_text.strip())
```

### 5.4 `remember` tool (tools.py registry, sixth entry)

```python
"remember": Tool(
    name="remember",
    schema={
        "type": "function",
        "function": {
            "name": "remember",
            "description": (
                "Save a durable note about this project for future "
                "sessions — a decision, a conclusion, a fact worth not "
                "re-discovering. Only takes effect in a git-tracked "
                "directory. If a command has run recently, its exact "
                "output is automatically attached as a verbatim record."),
            "parameters": {
                "type": "object",
                "properties": {
                    "note": {"type": "string",
                              "description": "one or two sentences: what's worth remembering and why"},
                },
                "required": ["note"],
            },
        },
    },
    requires_confirmation=False,
    confirmation_summary=None,
    handler=_remember_handler,
    repetition_key=lambda args: args.get("note", ""),
)
```

`_remember_handler(args, executor)`:
1. `root = memory.resolve_git_root(executor.cwd)`; if `None`, return
   `ToolResult("not in a git-tracked directory — nothing to remember into")`,
   write nothing.
2. If `executor.last_result` is set: write it verbatim (using both
   `executor.last_command` and `executor.last_result`, per the log
   template in §4) to a new `episodic/<timestamp>.log`, pointer = that
   path.
3. Else (`executor.last_command`/`last_result` both `None` — nothing has
   run yet this session): pointer = `None`.
4. `memory.encode_remember(root, args["note"], pointer)` appends the
   semantic entry. If this is the repo's first-ever encoding, this is
   also where `.sasha/memory/` and `.sasha/.gitignore` get created
   (§5.5), and the one-time creation notice fires.
5. Return `ToolResult(f"remembered: {args['note']}")`.

`remember` is always present in the registry and the schema sent to the
LLM — it does not vary by whether the current directory happens to be a
git repo (avoids making the tool list dynamic; the handler's own
directory check in step 1 is sufficient and simpler).

### 5.5 Lazy, self-protecting directory creation

```
def ensure_memory_dir(repo_root):
    memory_dir = repo_root / ".sasha" / "memory"
    if memory_dir.exists():
        return memory_dir, False       # already existed
    gitignore = repo_root / ".sasha" / ".gitignore"
    gitignore.parent.mkdir(parents=True, exist_ok=True)
    gitignore.write_text("*\n")        # written before anything else
    memory_dir.mkdir(parents=True, exist_ok=True)
    (memory_dir / "episodic").mkdir(exist_ok=True)
    return memory_dir, True            # True = just created
```

Both `encode_commit` and `encode_remember` call this before writing.
When it returns `just_created=True`, the caller (repl.py, after the
encode call returns) prints the one-time notice:
```
[sasha] created .sasha/memory/ — remembering context for this project from now on (gitignored automatically)
```
printed directly via the sink — never routed through the LLM's own reply
text, so it's reliable regardless of what the model says.

## 6. Error handling

Every failure degrades to "no persistence right now" — nothing here can
surface as a shell-breaking error:

| Failure | Behavior |
|---|---|
| Not a git repo / `git` missing / `rev-parse` fails | Silent — this is the normal case for most directories, not a failure. |
| Can't create `.sasha/memory/` or `.gitignore` (permissions, read-only fs) | Skip persistence for that repo this session; one-time notice, same pattern as `config.toml`/`PERSONALITY.md`'s own self-install failures. |
| A malformed line in `semantic.jsonl` | Skipped individually on load; rest of the file still loads. |
| `git log -1` fails right after a successful commit | Skipped silently — the commit already succeeded and was already reported normally; a memory hiccup must never retroactively look like the commit failed. |
| `sasha forget` with nothing to forget | Friendly no-op, not an error. |
| `sasha forget` partially fails mid-deletion | Reports what actually happened, not blanket success. |
| Concurrent writers | Handled by write shape (§3.5), not locking — not bulletproof against arbitrarily large simultaneous writes, but entries are inherently small (LLM-authored gists), so real risk is negligible. |
| A user force-adds an ignored path (`git add -f`) | The `.gitignore` guard is best-effort against *accidental* commits, same as any `.gitignore` entry anywhere — deliberate override is always possible and out of scope to prevent. |

## 7. Testing strategy

- **Place-shift & commit encoding**: real, throwaway git repos under
  `tmp_path` (`git init`, `git commit`) — not mocked subprocess calls;
  same spirit as the existing PTY harness.
- **`semantic.jsonl` load/cap/render**: pure `tmp_path` unit tests, no git
  needed; a torn/malformed line and an over-budget entry list are both
  explicit cases.
- **`remember` handler**: `FakeExecutor`/`tmp_path`, matching Phase 16's
  own tool-test style — with `executor.last_result` set (episodic file
  written, pointer attached) and unset (pure narrative, no file, `pointer:
  null`).
- **Lazy + self-protecting creation**: asserts a session with nothing
  encoded leaves *no* `.sasha/` directory at all, and that `.gitignore`
  exists and reads `*` immediately upon first creation.
- **Place-shift detection cadence**: asserts the git-root check only
  re-runs when `cwd` actually changed across turns, not on every
  render/consult (a call-count spy, same idea as asserting against
  `confirmer.asked` elsewhere).
- **`sasha memory` / `sasha forget`**: same shape as the existing
  `mute`/`unmute` built-in tests.
- **Concurrent appends**: two threads appending to the same
  `semantic.jsonl` simultaneously; asserts every entry lands intact and
  individually parseable afterward — validates the "small atomic appends
  are safe" claim without needing to simulate separate OS processes.

## 8. Decisions log

- **Decision:** Long-term memory is scoped to the git repository root,
  not the literal cwd. **Rationale:** Salvador's framing — git presence is
  a free, zero-config signal for "this matters enough to remember";
  everything else (general shell chores) is exactly as ephemeral as Sasha
  already is today.
- **Decision:** One continuous, append-only store per repo, not discrete
  named/dated sessions. **Rationale:** matches "just cd in and it's
  already there" — no session picker, no manual setup, ever.
- **Decision:** Concurrent Sasha processes in the same repo just append,
  no locking, no degraded mode. **Rationale:** in practice this is one
  person with two terminal windows, not two strangers racing — and once
  the trunk holds curated narrative entries rather than raw interleaved
  conversation, simultaneous contributions from two terminals are
  harmless, not corrupting.
- **Decision:** Two-tier semantic/episodic split, with `read_file` as the
  retrieval mechanism for episodic pointers. **Rationale:** avoids
  inventing a new retrieval tool — `read_file` is already sandboxed,
  already auto-approved, already something the LLM knows how to call.
- **Decision:** Commits need no episodic log file of their own.
  **Rationale:** git's own object store already is the verbatim record;
  duplicating a diff Sasha didn't need to capture would be pure waste.
- **Decision:** Encoding is only automatic (commit) or deliberate
  (`remember`) — no blanket end-of-session or rolling summarization.
  **Rationale:** considered and rejected two alternatives: end-of-session
  summarization pays an LLM call even when nothing happened and loses its
  chance entirely on a crash; rolling mid-session compaction is real
  complexity (a moving compaction boundary) for a first version. The
  `remember` tool already reuses Phase 16's registry, costs nothing when
  unused, and naturally serves both the LLM's own judgment and explicit
  user requests ("?? please keep a note about this") through the same
  mechanism.
- **Decision:** `.sasha/memory/` lives at the git root, not under
  `~/.local/state/sasha/`. **Rationale:** Salvador's call — avoids
  accumulating orphaned state for every repo ever touched, forever;
  memory's lifecycle now naturally follows the repo's own (delete the
  repo, the memory goes with it); also drops the path-mangling scheme
  entirely, since location is the key.
- **Decision:** `.sasha/.gitignore` (containing `*`) is unconditional,
  automatic, and written before any other content, every time.
  **Rationale:** episodic logs can contain arbitrary command output —
  secrets, tokens, anything that showed up in stdout/stderr. This is a
  hard safety requirement, not a convention Sasha merely suggests.
- **Decision:** No persistent git-branch/status segment added to the
  default prompt. **Rationale:** Salvador's call, after weighing it — a
  per-render recurring cost (has to reflect dirty state, not just branch)
  competing for visual space with a prompt just redesigned this session;
  the one-time place-shift announcement already communicates the context
  change without repeating it on every line. If ever wanted, it belongs
  as an opt-in `prompt.py` token, not a default-on feature, and isn't
  specced here.
