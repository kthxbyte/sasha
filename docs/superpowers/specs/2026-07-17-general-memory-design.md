# Sasha — General (Cross-Project) Memory Design Specification (Phase 18)

**Status:** Draft for review
**Date:** 2026-07-17

## 1. Mission

Phase 17 gave Sasha long-term memory scoped to git repositories — a
"place" with no place has no memory. This design adds a second, broader
"place": `$HOME` itself, via a general memory store at
`~/.config/sasha/memory/` that's **always** visible to the LLM, alongside
whatever project-specific memory is also active. It also adds a third
automatic encoding trigger — a program crashing with a real fault signal
(segfault and friends) — that fires regardless of which place is current.
The result: personal preferences and general facts ("I always want
concise commit messages") are never shadowed by being inside a project,
and a crash's evidence survives even if nobody thinks to `remember` it in
the moment.

## 2. Scope

### In scope (v1):
- General memory store at `~/.config/sasha/memory/` (`XDG_CONFIG_HOME`-
  aware, same convention as `config.toml`/`PERSONALITY.md`), same
  `semantic.jsonl` + `episodic/` shape as project memory. No
  `.gitignore` protection needed there — same precedent as
  `PERSONALITY.md`/`config.toml` already living unprotected in that
  directory.
- `Session` gains a **second**, distinctly-labeled memory slot — general
  notes and project notes are never silently merged into one blob; the
  LLM sees which is which.
- **Always-layered visibility**: general memory is loaded once and stays
  visible for the whole session; project memory (Phase 17, unchanged)
  continues to load/swap on place-shift, adding to — never replacing —
  the general block.
- **Targeting rule** for every write (commit hook, `remember`, the new
  crash trigger): whichever place is currently active. Inside a git
  repo → project memory. Outside any repo → general memory. General
  memory's root is always resolvable (it doesn't depend on git), so
  unlike today, `remember` and the crash trigger are never a no-op
  outside a project — see §6 for the exact behavior change this makes to
  Phase 17's shipped `remember` tool.
- **New automatic trigger**: a command whose `ExecResult.signal` is one
  of `SIGILL`, `SIGABRT`, `SIGFPE`, `SIGSEGV`, `SIGBUS` — a real crash
  signal — encodes an entry with zero LLM involvement, same spirit as the
  `git commit` hook: a short auto-generated semantic gist plus a full
  verbatim episodic log.
- New `general_*`-prefixed sibling functions in `memory.py`
  (`encode_general_remember`, `load_general_memory_block`,
  `encode_general_crash`) alongside Phase 17's existing, **unmodified**
  `encode_remember`/`load_memory_block`/etc. Both sets share the same
  private read/write/cap logic internally (one source of truth, no
  duplicated JSONL handling) — see §4 and §6.

### Out of scope / non-goals:
- Letting `remember` (or the crash trigger) explicitly target general
  memory while inside a project. Logged as ISSUE-035, deliberately
  deferred — worth revisiting whether the LLM can infer project-vs-
  general scope from a note's own content, rather than adding an
  explicit parameter or interrupting the user to ask.
- Any user configuration of whether general memory layers with project
  memory. Considered and explicitly rejected (Salvador's call): the whole
  point of general notes is that they stay visible everywhere; gating
  that behind a setting would just recreate the "is it currently active"
  confusion a toggle was supposed to avoid, for a case nobody's actually
  hit yet.
- Size- or content-based crash heuristics ("chunky stderr" without a
  signal). Rejected in favor of signal-based detection alone: unambiguous
  ( `ExecResult.signal` already exists, no new capture logic), and
  doesn't require a fuzzy size threshold that would both over- and
  under-fire.
- Treating `SIGKILL` as a crash trigger. Ambiguous — it's what an OOM
  kill looks like, but it's also literally what Sasha's own Ctrl+Z
  handling already sends (`ctrl_z_terminated`, which already prints its
  own message); including it would double-fire on every Ctrl+Z.
- `sasha forget`'s scope. It continues to operate on project memory only
  (unchanged from Phase 17) — clearing general memory isn't specced here;
  it's small enough to fold into a task's acceptance criteria rather than
  needing its own design section, decided at task-spec time. Deliberately
  conservative: general memory is meant to persist across everything, so
  it shouldn't be deletable by the same single-word confirmation that
  clears one project's notes.

## 3. Key contracts & assumptions

1. **General memory's root never depends on git and is always
   resolvable.** Unlike `resolve_git_root`, which can return `None`,
   general memory's location is a pure function of `XDG_CONFIG_HOME`/
   `$HOME` — it always exists as a target, even if nothing's been written
   there yet.
2. **Phase 17's public `memory.py` functions keep their exact signatures
   and behavior.** `encode_commit`, `encode_remember`, `load_memory_block`,
   `most_recent_gist`, `ensure_memory_dir`, `forget`, `encode_latest_commit`
   are untouched — still take `repo_root`, still compute `.sasha/memory`
   internally, still git-ignore-protect it. General memory gets its own
   `general_*`-prefixed siblings instead of the existing functions being
   generalized to take an arbitrary root — see §4.
3. **General memory is loaded once per session and updates in place.**
   It isn't re-resolved on place-shift the way project memory is (there's
   nothing to re-resolve — the root never changes) — it's loaded once,
   and any write to it (a `remember` or crash-trigger encoding that
   happens while outside a project) updates `Session`'s general-memory
   slot immediately, the same "write, then refresh what's loaded"
   pattern Phase 17 already established for project memory.
4. **The system prompt shows general and project memory as clearly
   separate, labeled sections** — never concatenated into one
   undifferentiated block. The LLM must be able to tell "this is general"
   from "this is about the current project" from the text alone.
5. **The crash trigger is a third encoding path, structurally identical
   to the commit hook** — zero LLM cost, auto-generated semantic text, a
   verbatim episodic log, targeted at whichever place is current. It is
   not routed through `remember`.
6. **Nothing about this feature can block, error, or visibly break the
   shell** — same governing principle as Phase 17. A crash-triggered
   encoding failure is exactly as silent as a commit-hook encoding
   failure already is.

## 4. Architecture & on-disk format

```
~/.config/sasha/memory/            # XDG_CONFIG_HOME-aware, same
  ├── semantic.jsonl                # convention as config.toml/PERSONALITY.md
  └── episodic/
      └── 2026-07-18T09-14-02.log
```

Same entry shape as project memory (§4 of the Phase 17 design), with one
addition to the `kind` vocabulary:

```json
{"timestamp": "2026-07-18T09:14:02Z", "kind": "crash", "text": "pytest crashed with SIGSEGV (exit 139)", "pointer": "episodic/2026-07-18T09-14-02.log"}
```

`kind` is now one of `"commit"`, `"remember"`, or `"crash"`. `"commit"`'s
`pointer` remains a git commit hash; `"remember"` and `"crash"` both
point at an episodic log file path — for project memory, still relative
to the repo root exactly as Phase 17 produced it (unchanged, since
`encode_remember`'s internals aren't touched); for general memory,
relative to the general memory root (there's no repo root to be relative
to).

**`memory.py` gains a private internal layer parameterized by an
already-resolved directory**, and Phase 17's existing public functions
become thin wrappers over it — their signatures and behavior are
byte-identical to today's:

```python
def _memory_root_for(repo_root: pathlib.Path) -> pathlib.Path:
    return repo_root / ".sasha" / "memory"


def general_memory_root() -> pathlib.Path:
    xdg = os.environ.get("XDG_CONFIG_HOME", "")
    base = pathlib.Path(xdg) if xdg else pathlib.Path.home() / ".config"
    return base / "sasha" / "memory"


def _ensure_memory_dir_at(memory_root: pathlib.Path, *,
                           gitignore: bool) -> tuple[pathlib.Path, bool]:
    ...  # today's ensure_memory_dir body, operating on memory_root
         # directly; the .gitignore-write step is skipped when
         # gitignore=False (general memory doesn't need it — see §2)


def ensure_memory_dir(repo_root: pathlib.Path) -> tuple[pathlib.Path, bool]:
    return _ensure_memory_dir_at(_memory_root_for(repo_root), gitignore=True)
```

`encode_commit`, `encode_remember`, `load_memory_block`,
`most_recent_gist`, `forget` all become similarly-thin wrappers: each
keeps its exact existing signature, computes `_memory_root_for(repo_root)`
internally, and delegates to a private `_..._at(memory_root, ...)`
implementation. `encode_latest_commit` needs **no changes at all** — it
already just calls `encode_commit(repo_root, ...)`, whose public contract
doesn't change.

The new `general_*` functions call the *same* private `_..._at` helpers,
just with `general_memory_root()` (and `gitignore=False`) instead of
`_memory_root_for(repo_root)`:

```python
def encode_general_remember(note: str, last_command: str | None,
                             last_result: ExecResult | None) -> bool:
    root = general_memory_root()
    pointer = None
    if last_command is not None and last_result is not None:
        pointer = _write_episodic_log_at(root, last_command, last_result)
    return _append_entry_at(root, "remember", note, pointer)


def load_general_memory_block(max_chars: int = _MAX_MEMORY_CHARS) -> tuple[str, int]:
    return _load_memory_block_at(general_memory_root(), max_chars)
```

One source of truth for the JSONL read/append/cap/gist logic; zero risk
to Phase 17's shipped call sites or contract tests, since nothing they
already call changes shape or behavior.

**`Session` gains a second memory slot.** `set_memory` (Phase 17) is
renamed `set_project_memory` for clarity now that there's a sibling; a
new `set_general_memory(text: str) -> None` sets the other. Both fold
into `_build_system_prompt` under their own labeled headings, general
first (it's the outer, always-present layer), then project:

```python
parts = []
if self._personality:
    parts.append(self._personality)
if self._general_memory_block:
    parts.append(f"What you generally remember (not project-specific):\n"
                 f"{self._general_memory_block}")
if self._project_memory_block:
    parts.append(f"What you remember about this project so far:\n"
                 f"{self._project_memory_block}")
parts.append(functional)
```

## 5. Mechanism

### 5.1 Loading general memory

Loaded once, at the same point `repl.py`'s `loop()` already does its
initial `_check_place_shift()` call — general memory doesn't depend on
`cwd`, so it's a single unconditional load, not a per-command check:

```python
def loop(self):
    ...
    block, _ = memory.load_memory_block(memory.general_memory_root())
    self._session.set_general_memory(block)
    self._check_place_shift()
    ...
```

### 5.2 Targeting rule (shared by `remember` and the crash trigger)

No new abstraction needed — each call site just branches on whether a
repo root is current, calling the bare (project-scoped) function or its
`general_*` sibling:

```python
# tools.py's remember handler
def _remember_handler(args: dict, executor: Executor) -> ToolResult:
    note = args.get("note", "")
    root = memory.resolve_git_root(executor.cwd)
    if root is not None:
        memory.encode_remember(root, note, executor.last_command, executor.last_result)
    else:
        memory.encode_general_remember(note, executor.last_command, executor.last_result)
    return ToolResult(f"remembered: {note}")
```

This *is* the behavior change to Phase 17's shipped tool flagged in §6 —
today's handler returns early with "not in a git-tracked directory —
nothing to remember into" when `root is None`; that branch is replaced
with the `encode_general_remember` call above.

### 5.3 The crash trigger (`repl.py`, alongside the commit hook)

```python
_CRASH_SIGNALS = frozenset({
    signal.SIGILL, signal.SIGABRT, signal.SIGFPE,
    signal.SIGSEGV, signal.SIGBUS,
})

def _check_crash(self, line, result):
    if result.signal not in _CRASH_SIGNALS:
        return
    if self._current_repo_root is not None:
        memory.encode_crash(self._current_repo_root, line, result)
    else:
        memory.encode_general_crash(line, result)
```

`memory.encode_crash(repo_root, command, result)` and
`memory.encode_general_crash(command, result)` are a bare/`general_`
pair exactly like `encode_remember`/`encode_general_remember` — both new
(the crash trigger doesn't exist in Phase 17, so there's no backward
compatibility to preserve here, just the same bare/`general_` naming
convention), both thin wrappers over a shared private `_encode_crash_at
(memory_root, command, result)`:

```python
def _encode_crash_at(memory_root: pathlib.Path, command: str,
                      result: ExecResult) -> bool:
    pointer = _write_episodic_log_at(memory_root, command, result)
    sig_name = signal.Signals(result.signal).name
    text = f"{command} crashed with {sig_name} (exit {result.exit_code})"
    return _append_entry_at(memory_root, "crash", text, pointer)


def encode_crash(repo_root: pathlib.Path, command: str, result: ExecResult) -> bool:
    return _encode_crash_at(_memory_root_for(repo_root), command, result)


def encode_general_crash(command: str, result: ExecResult) -> bool:
    return _encode_crash_at(general_memory_root(), command, result)
```

Called from `run_once`'s branch (e), alongside the existing
`_check_place_shift`/`_check_commit` calls — before the trigger-engine
`decide()` call, same ordering rationale as Phase 17: update memory state
first, let the trigger engine's decision (which still fires normally —
this doesn't change `decide()` or the OFFER/AUTO_CONSULT behavior at all)
use the now-current state.

### 5.4 `sasha memory` shows both tiers when both are present

Phase 17's `_handle_memory_builtin` only ever knew about one tier and
said `"no project memory here"` outside any repo. That message no longer
makes sense — general memory is always present as a fallback, so there's
always *something* to show. The built-in now reports general memory
unconditionally, and project memory additionally when
`self._current_repo_root is not None`, each under its own labeled
heading (mirroring the system prompt's own general-then-project
layering) — never a bare "nothing here" outside a project anymore.
`sasha forget` is unaffected by this — see the Scope section above for
why its reach stays limited to project memory.

## 6. What changes in already-shipped Phase 17 code

Smaller than it might look — most of Phase 17's shipped surface is
untouched:

1. **`memory.py`'s existing public functions are unmodified** —
   `encode_commit`, `encode_remember`, `load_memory_block`,
   `most_recent_gist`, `ensure_memory_dir`, `forget`,
   `encode_latest_commit` keep their exact signatures and behavior (§4).
   No existing `tests/contract/test_task_17_1_memory.py` assertion that
   calls these directly needs to change.
2. **`remember`'s behavior outside a git repo changes.** Today (Phase
   17) it returns `"not in a git-tracked directory — nothing to remember
   into"` and writes nothing. After this design, it writes to general
   memory instead (§5.2) — the tool's description text needs updating to
   match (it currently says "Only takes effect in a git-tracked
   directory; has no effect otherwise," which becomes false). This is
   the one real behavior change to a Phase 17 call site, and it means
   `tests/contract/test_task_17_2_remember_tool.py`'s
   `test_ac_remember_outside_git_repo_writes_nothing` needs Tier-1
   amendment — it currently asserts the old message and asserts nothing
   is written; after this design, something *is* written, just to
   general memory instead of nowhere.
3. **`Session.set_memory` is renamed `set_project_memory`.** Its
   behavior for project memory is otherwise unchanged; this is a rename
   for clarity now that a sibling method exists, not a behavior change.
   `repl.py`'s Phase 17 call sites (`_check_place_shift`,
   `_handle_forget_builtin`) and `tests/contract/test_task_17_2_remember_tool.py`'s
   tests that call `set_memory` directly need updating to the new name —
   a mechanical rename, not a behavior change, but still a Tier-1
   contract-test amendment since it touches files under `tests/contract/`.
4. **`sasha memory`'s "no project memory here" message outside a repo is
   replaced** (§5.4) — `tests/contract/test_task_17_3_repl_memory.py`'s
   `test_ac_sasha_memory_outside_git_repo` needs Tier-1 amendment to
   match the new behavior (general memory is shown instead of a bare
   "not inside a git repository" message).
5. **Episodic pointer paths for project memory are unchanged** — since
   `encode_remember`'s internals aren't touched, a project's pointers
   stay relative to the repo root exactly as Phase 17 produced them.
   Only *general* memory's pointers are new (relative to the general
   memory root) — there's no prior format to be compatible with there,
   since general memory doesn't exist until this phase.

## 7. Error handling

Same governing principle as Phase 17 — nothing here can block or error
the shell:

| Failure | Behavior |
|---|---|
| Can't create `~/.config/sasha/memory/` (permissions) | Skip persistence for general memory this session; one-time notice, same pattern as `PERSONALITY.md`'s self-install failure. |
| A crash-triggered encoding itself fails (disk full, etc.) | Skipped silently — the crash was already visible to the user via the command's own stderr/exit code; a memory-encoding hiccup must never look like the crash itself failed to report. |
| `resolve_git_root` succeeds but the resolved root's `.sasha/memory/` can't be created | Identical to Phase 17's existing behavior — unchanged by this design. |

## 8. Testing strategy

- **Phase 17's existing memory.py tests are a pure regression guard
  here** — since `encode_commit`/`encode_remember`/`load_memory_block`/
  `most_recent_gist`/`ensure_memory_dir`/`forget`/`encode_latest_commit`
  are unmodified, `test_task_17_1_memory.py` staying green unmodified
  *is* the test that the wrapper refactor didn't change their behavior.
- **The new `general_*` functions and the private `_..._at` layer**: the
  same encode/load/cap/gist cases Phase 17 already covers for project
  memory, re-run against `general_memory_root()` (with
  `XDG_CONFIG_HOME` pointed at a `tmp_path` fixture, never the real
  `~/.config`) — proving the shared private layer behaves identically
  regardless of which public wrapper called it.
- **`general_memory_root()`**: `XDG_CONFIG_HOME` set vs. unset, matching
  the existing pattern already used for `config.toml`/`PERSONALITY.md`'s
  own path tests.
- **Layering**: a `Session` with both a general and a project memory
  block set renders both, clearly separated, general before project;
  either alone renders correctly without an empty labeled section for
  the other.
- **Targeting**: `remember` and the crash trigger both write to general
  memory outside any repo, and to the project's memory inside one, using
  the same `tmp_path` real-git-repo fixtures Phase 17's tests already
  established.
- **Crash trigger**: a real subprocess killed by `SIGSEGV` (e.g. `python3
  -c "import ctypes; ctypes.string_at(0)"` or an equivalent reliable
  crasher) produces a `"crash"`-kind entry with the correct signal name;
  `SIGKILL` and ordinary non-zero exits do not trigger it.

## 9. Decisions log

- **Decision:** General memory lives at `~/.config/sasha/memory/`, not a
  bespoke `~/.sasha/` path. **Rationale:** Salvador caught his own
  suggestion mid-brainstorm — `~/.config/sasha/` already exists for
  `config.toml`/`PERSONALITY.md`; reusing it is more consistent than
  inventing a new top-level dotdirectory.
- **Decision:** General memory always layers with project memory; no
  config toggle. **Rationale:** the point of general notes is staying
  visible everywhere; a toggle would recreate exactly the "which one's
  active" ambiguity it was meant to resolve, for a need nobody's hit yet.
  Salvador's own framing — "it's always the same user" — is the same
  reasoning that earlier resolved the concurrent-terminal-writers
  question in the Phase 17 design.
- **Decision:** `remember`/crash-trigger scope is not explicitly
  selectable while inside a project; deferred as ISSUE-035. **Rationale:**
  Salvador's call — leave the gap for now, revisit whether the LLM can
  infer scope from a note's content rather than adding interface
  complexity or interrupting the user to ask.
- **Decision:** Crash detection uses `ExecResult.signal` membership in a
  fixed crash-signal set, not a stderr-size heuristic. **Rationale:**
  signals are unambiguous and already captured by existing plumbing; a
  size threshold would be a new magic number prone to both false
  positives (noisy-but-harmless output) and false negatives (a
  one-line crash message).
- **Decision:** `SIGKILL` is excluded from the crash-signal set.
  **Rationale:** ambiguous with OOM kills, and literally overlaps
  Sasha's own Ctrl+Z termination mechanism, which already has its own
  dedicated message — including it would double-fire on every Ctrl+Z.
- **Decision:** `memory.py` gains a private, memory-root-parameterized
  internal layer, with Phase 17's existing public functions kept as
  unmodified thin wrappers over it, rather than those functions
  themselves being generalized to take an arbitrary root. **Rationale:**
  caught while working out the task breakdown — the originally-committed
  version of this doc had the existing functions changing shape
  (`repo_root` → `memory_root`), which would have required amending
  nearly every assertion in `test_task_17_1_memory.py` and several in
  `test_task_17_2_remember_tool.py`/`test_task_17_3_repl_memory.py` for
  zero behavioral gain. The wrapper approach gets the identical DRY
  benefit (one source of truth for the JSONL read/append/cap/gist logic)
  with a much smaller, more precisely bounded blast radius — see the
  revised §6 for exactly what still changes (three call sites, not a
  signature-wide rename).
