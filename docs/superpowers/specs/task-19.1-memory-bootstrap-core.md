# Task 19.1: memory.py — bounded_history, root_docs, encode_bootstrap, write lock

## Authoritative contract
`docs/superpowers/specs/2026-07-18-memory-bootstrap-design.md` is the
complete design record for this task — read it first, especially §3 item
4 (concurrency amendment), §5.1 (bounded git-log read), §5.3 (root-doc
discovery), and §4 (the new `"bootstrap"` entry kind). This task builds
the three new pure primitives `src/sasha/memory.py` needs; wiring them
into `repl.py`'s place-shift sequencing is Task 19.3, not this task.

## What already exists
- `src/sasha/memory.py` (Phases 17–18, landed): `_memory_root_for(repo_root)
  -> pathlib.Path`, `_append_entry_at(memory_root, kind, text, pointer, *,
  gitignore) -> bool`, `encode_commit(repo_root, message, commit_hash) ->
  bool`, `_ensure_memory_dir_at(memory_root, *, gitignore) -> tuple[Path,
  bool]`. Read the file directly for exact signatures. This task adds new
  functions and one new module-level lock — every existing function's
  *behavior* is unchanged, but `_append_entry_at`'s body gains lock
  protection (see "What's new" below — its signature does not change).
- `encode_latest_commit(repo_root) -> bool | None` (Task 17.3, landed):
  reads `HEAD` via `git log -1 --format=%H%n%B`. Not touched by this
  task — it only ever reads one commit, so it has no need for the
  record-separator technique this task introduces for multi-commit reads.

## What's new

### 1. Module-level write lock
Add `import threading` to the existing import block. Add, near the top of
the file (module level, alongside `_MAX_MEMORY_CHARS`):
```python
_WRITE_LOCK = threading.Lock()
```
Wrap `_append_entry_at`'s entire existing body in `with _WRITE_LOCK:` —
the `_ensure_memory_dir_at(...)` call and the `open(path, "a")` write both
move inside the `with` block. The function's signature and return value
are unchanged; only its internal body gains the lock.

**Why:** every prior concurrency guarantee in this file (Phase 17's
design, §3 item 5 there) relied on separate *processes* each doing small
`O_APPEND` writes — that guarantee is unaffected and still holds. This
task is the first to introduce a background *thread* within one process
(landing in Task 19.3) that can call `_append_entry_at` at the same time
the main thread does (via a commit, `remember`, or a crash). A
`threading.Lock` protects exactly that new same-process case; it does
nothing for, and does not replace, the cross-process `O_APPEND` reasoning.

### 2. `bounded_history`
```python
def bounded_history(repo_root: pathlib.Path,
                     max_chars: int = 10_000) -> list[tuple[str, str]]:
    """Newest-first list of (commit_hash, full_message) pairs, read via
    one `git log` call, accumulated until the next commit's message would
    push the running total past max_chars — except the first (newest)
    commit is always included regardless of size. Returns [] if the repo
    has no commits, `git` is missing, or the subprocess fails/times out.
    """
```
Implementation approach: run
`git log --format=%H%x1f%B%x1e` (timeout=10, capture_output=True,
text=True, cwd=str(repo_root)) — `%x1f` (unit separator) divides hash
from message within one commit, `%x1e` (record separator) divides
commits, so multi-line commit bodies never get misparsed as a boundary
(this is why `encode_latest_commit`'s simpler `%H%n%B` format, which only
ever needs to parse a single commit, doesn't work here). Split the
stdout on `\x1e`, discard the trailing empty piece, split each piece on
`\x1f` into `(hash, message)`, strip both. Walk that newest-first list,
always keep the first tuple, and keep accumulating while
`running_total_of_message_lengths + len(next_message) <= max_chars`
(mirroring the "always keep at least one" pattern already used by
`_load_memory_block_at`, memory.py's existing recency-truncation
function — read it for the exact shape of that pattern before
implementing this one).

### 3. `root_docs`
```python
def root_docs(repo_root: pathlib.Path,
              max_chars: int = 6_000) -> list[tuple[str, str]]:
    """Newest-mtime-first list of (filename, content) pairs for every
    regular file directly under repo_root (non-recursive) whose extension,
    lowercased, is one of .md, .txt, .htm, .html. Accumulates full file
    contents until the next file's content would push the running total
    past max_chars — except the first (newest-mtime) file is always
    included regardless of size. A file that can't be read (permission
    error, race with deletion) is skipped individually; the rest of the
    scan still proceeds. Returns [] if no matching files exist.
    """
```
Implementation approach: `repo_root.iterdir()`, filter to
`p.is_file() and p.suffix.lower() in {".md", ".txt", ".htm", ".html"}`,
sort by `p.stat().st_mtime` descending, then read and accumulate with the
same always-keep-first, budget-capped pattern as `bounded_history`.
Filenames in the returned tuples are the bare filename (`p.name`), not a
full path.

### 4. `encode_bootstrap`
```python
def encode_bootstrap(repo_root: pathlib.Path, text: str) -> bool:
    """Appends one kind="bootstrap" semantic entry with pointer=None.
    Returns True iff this call is what just created .sasha/memory/ for
    this repo (same just_created convention as encode_commit/
    encode_remember)."""
    return _append_entry_at(_memory_root_for(repo_root), "bootstrap", text,
                             None, gitignore=True)
```
This is the only way `kind="bootstrap"` entries ever get written — the
per-bullet-line loop that calls this once per LLM-summarized fact is
Task 19.3's responsibility (in `repl.py`), not this task's.

## Constraints
- Modify only `src/sasha/memory.py`. Do not touch `src/sasha/repl.py`,
  `src/sasha/prompt.py`, `src/sasha/session.py`, `src/sasha/tools.py`,
  `src/sasha/executor.py`, `src/sasha/llm.py`, `README.md`, or anything
  under `docs/`.
- Every existing function in `memory.py` keeps its exact current
  signature and observable behavior. `_append_entry_at`'s only change is
  internal (the lock) — callers see no difference.
- `bounded_history` and `root_docs` are pure functions: no writes to
  `.sasha/`, no `print`/`sink` output, no side effects beyond reading git
  history / reading files on disk.

## Acceptance criteria
(encoded in `tests/contract/test_task_19_1_memory_bootstrap_core.py`)

1. `bounded_history` returns commits newest-first; a repo with multiple
   commits, including at least one multi-line commit body, round-trips
   correctly (hash and full message both recovered intact).
2. `bounded_history` on a char budget smaller than even the newest
   commit's message still returns that one commit in full (never returns
   an empty list when commits exist).
3. `bounded_history` stops accumulating once a further commit would
   exceed the budget, while always including the first (newest) one
   regardless of its own size.
4. `bounded_history` on a repo with zero commits returns `[]`, no
   exception, no `.sasha/` directory created.
5. `root_docs` finds `.md`/`.txt`/`.htm`/`.html` files at repo root
   case-insensitively (e.g. `README.MD`, `notes.TXT`), ignores
   non-matching extensions, and ignores files in subdirectories.
6. `root_docs` returns files newest-mtime-first.
7. `root_docs` respects the same "always keep the first, cap the rest"
   char-budget behavior as `bounded_history`.
8. `root_docs` on a repo root with no matching files returns `[]`.
9. `encode_bootstrap` appends an entry with `kind="bootstrap"`,
   `pointer: null`, and the given text; multiple calls append multiple
   entries in call order.
10. Concurrent callers (multiple threads calling `encode_bootstrap`/
    `encode_commit` on the same repo simultaneously) still produce a
    `semantic.jsonl` where every line is independently valid JSON and the
    total line count matches the number of calls — a regression guard for
    the same-process concurrency this task's lock exists to protect
    (Phase 17 already covers the analogous cross-process-style guarantee;
    this test specifically exercises many threads in one process).

## Standing instructions
- Your goal: make the contract tests in
  `tests/contract/test_task_19_1_memory_bootstrap_core.py` pass, and keep
  the full contract suite green:
  `PYTHONPATH=src python3 -m pytest tests/contract/`.
- NEVER modify files under `tests/contract/` — the gate rejects the
  commit if you do.
- Deliver exactly ONE new commit on top of the HEAD you started from;
  never amend, rebase, or reset; leave the working tree clean.
- Commit message per `AGENTS.md`, with trailers: `Done: Task 19.1 — <one
  line>`, `Fixed: ISSUE-038` only if you believe the whole issue is
  resolved by this task alone (it isn't — do not include `Fixed:` for
  this task), `Next: Task 19.2 — prompt.py busy-star token + Repl
  background-task counter`, and the `Co-Authored-By:` signature line from
  your dispatch prompt.
- Greenfield-vs-surgical routing: this task adds precise, well-specified
  new functions to an existing, already-tested module, plus one
  internal-only change (the lock) to a function whose external contract
  does not change. Dispatch to the default model (deepseek-v4-pro); do
  not use a `SASHA_DISPATCH_MODEL` local-model override.
