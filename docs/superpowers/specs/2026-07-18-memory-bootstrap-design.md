# Sasha — Memory Bootstrap Design Specification (Phase 19)

**Status:** Draft for review
**Date:** 2026-07-18

## 1. Mission

Phase 17 gave Sasha project-scoped long-term memory, but only going
forward: a repo you've been working in for years starts with exactly the
same blank slate as a repo created five minutes ago, because memory only
ever accrues from commits and `remember` calls made *after* Sasha first
sees the directory. This design lets Sasha organically bootstrap that
memory the first time it enters an existing repo with none yet —
instantly and mechanically replaying recent commit history into semantic
memory, then, without blocking the user, asking the LLM to distill
root-level project docs and that same history into a handful of
higher-level facts. A repo with real history gets a running start; a
brand-new repo behaves exactly as it does today (nothing to bootstrap
from). Non-git directories and general (cross-project) memory are
completely unaffected.

## 2. Scope

### In scope (v1):
- Detecting, at place-shift time (`_check_place_shift`, repl.py), a git
  repo whose `.sasha/memory/` does not exist at all yet — the trigger for
  everything below.
- **Stage 1 (mechanical, synchronous):** create `.sasha/memory/` +
  `.gitignore` (reusing the existing `_ensure_memory_dir_at`), then
  replay a bounded, newest-first slice of `git log` into `semantic.jsonl`
  via the existing `encode_commit()`, written oldest-to-newest.
- **Stage 2 (background, LLM-driven):** read root-level `*.md`/`*.txt`/
  `*.htm*` files (non-recursive, mtime-newest-first, char-budget capped)
  plus the *same* bounded git-log excerpt stage 1 already read, and ask
  the LLM (via the existing `ChatClient`/`client_factory`) for 3–8 bullet
  facts, writing each as a new `kind="bootstrap"` semantic entry.
- A generalized "Sasha is doing background work" indicator: an in-flight
  task counter on `Repl`, surfaced as the existing `✦` prompt glyph
  blinking (SGR 5) instead of static.
- Two short one-time announcements: a start line and a finish line (exact
  wording in §5.4/§5.5).
- A `threading.Lock` around `_append_entry_at`'s write — the first
  same-process concurrency memory.py has ever had (see §3.4).
- Per-session, per-repo bookkeeping so a failed/unreachable stage 2 tries
  once per session, not on every `cd` back into the repo.

### Out of scope / non-goals:
- Bootstrapping general (cross-project) memory (`~/.config/sasha/memory/`)
  — it isn't scoped to one repo, "backfill from git log" doesn't apply to
  it, and it isn't touched by this design at all.
- Any user-facing configuration — no on/off switch, no custom glob
  patterns, no tunable budgets. Automatic and self-installing, matching
  every other self-installing Sasha feature (`config.toml`,
  `PERSONALITY.md`, `.sasha/memory/` itself).
- Recursive doc discovery (`docs/`, nested READMEs, subdirectory
  markdown). Repo root only.
- Per-entry source attribution — tagging each bootstrap bullet with which
  specific file informed it. Considered and rejected for v1 (§8).
- A continuously-ticking, timer-driven spinner animation while sitting at
  an unfinished, already-drawn prompt line. Considered and rejected —
  requires replacing readline-based input (§8).
- Any explicit "rebuild summary" / "resummarize" user command. If stage 2
  never succeeds, the repo simply keeps stage 1's raw commit history as
  its only memory indefinitely, until a future session's place-shift
  happens to retry it successfully.

## 3. Key contracts & assumptions

1. **Stage 1 fires at most once per repo, ever** — gated on
   `.sasha/memory/` not existing at all. Once it exists (for any reason,
   including this feature itself), stage 1 never re-runs for that repo.
2. **Stage 2 may retry, but at most once per Sasha process per repo** —
   gated on no `kind="bootstrap"` entry existing yet in `semantic.jsonl`.
   A fresh Sasha session is a fresh attempt; the same running session
   never re-attempts a repo it already tried this session, whether that
   attempt succeeded, failed, or found nothing to summarize.
3. **Stage 1 and stage 2 share one bounded git-log read.** There is
   exactly one `git log` subprocess call per bootstrap event, newest-first,
   capped at a character budget — not a fixed commit count (§8 explains
   why a fixed count was rejected). Both the raw per-commit replay and the
   LLM prompt's "recent history" section are built from that same read.
4. **This is the first feature to introduce same-process concurrency into
   `memory.py`.** Phase 17's contract "concurrency safety comes from write
   shape, not locks" (2026-07-17 design, §3 item 5) is amended, not
   reversed: that contract's reasoning was specifically about *separate
   Sasha processes* (different terminals, same repo) relying on
   `O_APPEND`'s cross-process atomicity for small writes — that reasoning
   is untouched and still holds. This feature adds the first
   *intra-process* writer (a background thread) that didn't exist when
   that contract was written, so a `threading.Lock` is added around
   `_append_entry_at` specifically for the same-process case. The two
   mechanisms are complementary, not competing: the lock protects this
   process's two threads from each other; `O_APPEND` still protects this
   process from any sibling Sasha process in another terminal.
5. **Stage 2 never blocks the REPL loop.** It runs on a daemon thread
   spawned right after stage 1 finishes; the main thread returns to
   accepting input immediately. If the process exits before the thread
   finishes, the thread is simply abandoned — no partial-entry risk,
   because nothing is written to `semantic.jsonl` until the LLM call has
   fully returned and its response has been successfully parsed into at
   least one bullet line.
6. **Every stage-2 failure mode degrades to "stage 1's raw commits stand
   alone."** No usable profile, an unreachable/timed-out LLM call, or an
   empty/unparseable response all result in zero entries written and zero
   visible error — matching Phase 17's existing contract that nothing
   here can surface as a shell-breaking error.
7. **A repo with nothing to summarize skips stage 2 entirely, and prints
   no start announcement either.** If there are zero commits *and* zero
   root docs, `.sasha/memory/` is still created (so future commits/
   `remember` calls have somewhere to land), but silently — no LLM call,
   no busy indicator, no "building ... in the background" line, since
   nothing will in fact happen in the background. The start announcement
   in §5.5 only fires when stage 2 is actually about to be spawned.

## 4. Architecture

On-disk layout is unchanged from Phase 17:

```
<repo-root>/.sasha/memory/
  ├── semantic.jsonl        # append-only, one JSON object per line
  └── episodic/
<repo-root>/.sasha/.gitignore     # contains a single line: *
```

This feature adds exactly one new `kind` value alongside the existing
`"commit"` / `"remember"` / `"crash"`: **`"bootstrap"`** — LLM-authored,
`pointer: null`, otherwise identical shape (`timestamp`, `kind`, `text`).

Sequence, triggered from `_check_place_shift`:

```
cd into repo, .sasha/memory/ absent
        │
        ▼
_check_place_shift()                              (repl.py, existing)
        │  new_root resolved, memory dir absent
        ▼
STAGE 1 — main thread, synchronous, sub-second
  ensure_memory_dir_at(new_root)                   # mkdir + .gitignore
  history_excerpt = bounded_git_log(new_root)      # newest→oldest,
                                                    # char-budget capped
  for hash, msg in reversed(history_excerpt):       # oldest→newest
      encode_commit(new_root, msg, hash)
  docs = root_docs(new_root)          # *.md/*.txt/*.htm*, non-recursive,
                                       # mtime newest-first, char-budget capped
                                       # (cheap: a handful of small reads —
                                       # done here, in the main thread, so
                                       # stage 1 can decide whether there's
                                       # anything for stage 2 to do at all)
  if history_excerpt or docs:
      sink.write_line("[sasha] no memory yet for <repo> — "
                       "building initial context from history "
                       "in the background")
      bootstrap_attempted.add(new_root)
      background_tasks += 1
      Thread(target=_bootstrap_stage_2,
             args=(new_root, history_excerpt, docs),
             daemon=True).start()
  # else: nothing to summarize — .sasha/memory/ still exists from the
  # ensure_memory_dir_at() call above, but silently; no announcement,
  # no thread, no busy indicator (see §3 item 7).
        │
        ▼ (main thread returns immediately; REPL loop keeps accepting input)
STAGE 2 — background daemon thread (only spawned if there was something to do)
  prompt = build_prompt(docs, history_excerpt)
  try:
      response = client_factory(current_profile).chat(
          [{"role": "user", "content": prompt}], tools=[],
          on_text=lambda _: None)
      lines = parse_bullets(response.text)          # [] on garbled/empty
  except ChatError:
      lines = []
  if lines:
      with _WRITE_LOCK:
          for line in lines:
              _append_entry_at(memory_root, "bootstrap", line, None,
                                gitignore=True)
      sink.write_line(finish_message(len(lines), docs))
  background_tasks -= 1
```

**Busy indicator plumbing:**
- `Repl._background_tasks: int` — incremented right before a background
  thread starts, decremented in that thread's `finally` block. A counter,
  not a bool, so a second unrelated background task (anything future that
  reuses this same mechanism) can't have its completion prematurely clear
  the indicator while another task is still running.
- `busy = self._background_tasks > 0`, passed into `prompt.render(...,
  busy=busy)` at the same point `cwd`/`user`/`host` are already
  recomputed, once per loop iteration (repl.py's `loop()`).
- `prompt.py`'s `COLOR_TOKENS` gains `"blink": "\x1b[5m"`, wrapped in the
  same `\001…\002` zero-width markers every existing color token already
  uses — no new escaping mechanism. The default template's `✦` segment
  (currently `\[yellow\]✦\[reset\]`, prompt.py:18) is rendered as
  `\[blink\]\[yellow\]✦\[reset\]` while `busy`, and the current plain form
  while idle. Evaluated once per prompt draw; the terminal itself
  animates the blink for as long as that prompt line is on screen — no
  redraw thread, no readline interference.

## 5. Algorithms

**5.1 Bounded git-log read** (new function, shared by both stages):
```
def bounded_history(repo_root, max_chars=10_000) -> list[tuple[hash, message]]:
    # git log --format=%H%x1f%B%x1e, newest first
    # accumulate (hash, message) pairs until buffer would exceed max_chars
    # return newest-first list
```
Record-separator formatting (`%x1f`/`%x1e`) is used instead of the
existing `encode_latest_commit`'s `%H%n%B` because that format only ever
needs to parse a single commit (`-1`); a multi-commit read needs an
unambiguous boundary that survives multi-line commit bodies, which plain
newlines don't guarantee.

**5.2 Stage 1 replay:** iterate `bounded_history(...)` **reversed**
(oldest-first) and call the existing `encode_commit()` once per commit.
Reversing matters: `_load_memory_block_at`'s recency-biased truncation
(memory.py:127) keeps whatever was appended *last* when a memory block
exceeds its load budget — appending oldest-first means the newest commits
are what survive truncation later, which is the only ordering that
matches what "recent" should mean when this memory is eventually loaded.

**5.3 Root-doc discovery:**
```
def root_docs(repo_root, max_chars=6_000) -> list[tuple[filename, content]]:
    # glob repo_root / "*.md", "*.txt", "*.htm*" (non-recursive, case-insensitive)
    # sort by mtime, newest first
    # accumulate full file contents until max_chars exhausted
    # (a file that doesn't fully fit is truncated, not skipped)
```

**5.4 Stage 2 prompt and parse:** a single user-role message containing
the root docs (labeled by filename) and the bounded history excerpt
(labeled as recent commits), asking explicitly for 3–8 short, distinct
bullet lines and nothing else. The response is split on newlines,
leading bullet markers (`-`, `*`, numbered) stripped, blank lines
dropped, capped to the first 8 non-empty lines. An empty result after
parsing is treated identically to a `ChatError` — no entries written, no
finish message.

**5.5 Announcements:**
- Start (fires only when stage 2 is actually about to be spawned, i.e.
  at least one commit or root doc was found — see §3 item 7):
  `[sasha] no memory yet for <repo-name> — building initial context from history in the background`
- Finish (stage 2, only on success):
  - one root doc consumed: `[sasha] finished building context: N entries (from <filename>)`
  - multiple root docs consumed: `[sasha] finished building context: N entries (from <count> files)`
  - no root docs consumed (history-only summary): `[sasha] finished building context: N entries`
- No finish message at all on failure/empty-result — the absence of a
  finish line *is* the signal that stage 2 didn't add anything this time.

**5.6 Retry bookkeeping:** `Repl._bootstrap_attempted: set[pathlib.Path]`.
Stage 2 is spawned only if `new_root not in self._bootstrap_attempted`
**and** no `kind="bootstrap"` entry exists yet in that repo's
`semantic.jsonl`. The repo is added to the set the moment stage 2 is
spawned (not when it finishes) — a slow-running attempt still counts as
"tried this session" even if a second `cd` into the same repo happens
before it completes.

## 6. Error handling

| Failure | Behavior |
|---|---|
| Not a git repo | Unaffected — identical to today; this feature only ever triggers from `_check_place_shift`'s existing git-root resolution. |
| Repo has zero commits | Stage 1 still creates `.sasha/memory/` (silently — no commits to replay, no announcement, matching the "trivial first encoding event" case); stage 2 still runs if root docs exist. |
| Repo has zero commits *and* zero root docs | Stage 2 is never spawned and no start announcement prints — `.sasha/memory/` is still created, silently, so future commits/`remember` calls have somewhere to land. |
| `git log` subprocess fails/times out | Stage 1 degrades to "just the empty directory" (same as today's lazy creation with nothing yet encoded); stage 2 runs on root docs alone if any exist. |
| No usable LLM profile / API key | Stage 2 skipped silently — no error line, no crash. |
| LLM unreachable / times out | Same as above. |
| LLM returns an empty or unparseable response | Treated as failure — zero entries, no finish line. |
| Root doc unreadable (permissions, broken symlink) | Skipped individually; the rest of the glob still proceeds. |
| Two writers race on `semantic.jsonl` within one process | Serialized by the new `threading.Lock` (§3 item 4) — no interleaved/corrupted lines. |
| Two separate Sasha processes race on the same repo's `semantic.jsonl` | Unchanged from Phase 17 — small `O_APPEND` writes, no cross-process lock, same as before this feature existed. |
| Sasha exits while stage 2 is still running | Daemon thread is abandoned; nothing partial was ever written (see §3 item 5). |

## 7. Testing strategy

- **Stage 1 replay**: real, throwaway git repos under `tmp_path` (`git
  init`, several commits) — asserts entries land oldest-to-newest, in the
  exact shape `encode_commit` already produces, and that a repo exceeding
  the char budget only replays its newest slice.
- **Stage 2, LLM call**: a fake `ChatClient`/`client_factory` injected the
  same way existing consult tests already fake the LLM boundary — no real
  network calls. Cases: a well-formed multi-bullet response, an empty
  response, a `ChatError` raise, a response with fewer than 3 or more than
  8 candidate lines.
- **Root-doc discovery**: `tmp_path` fixtures with a mix of `.md`/`.txt`/
  `.htm`/unrelated extensions and nested subdirectories — asserts
  non-recursion, mtime ordering, and budget truncation.
- **Busy indicator**: a call-count/state spy around `render()` asserting
  `busy=True` is passed while a background task is registered and
  `busy=False` once it's decremented; a `prompt.py` unit test asserting
  the `blink` token renders `\x1b[5m` wrapped in the existing zero-width
  markers.
- **Thread-safety**: two threads appending to the same `semantic.jsonl`
  concurrently (matching Phase 17's own concurrent-append test) — now
  additionally asserting the lock is actually held (e.g. via a
  slow/instrumented write) rather than merely relying on small-write luck.
- **Retry bookkeeping**: asserts a second `_check_place_shift` into the
  same repo within one `Repl` instance does not re-spawn stage 2, whether
  the first attempt succeeded, failed, or found nothing.
- **Announcement wording**: one test per case in §5.5 (one file, multiple
  files, no files, no finish line on failure).

## 8. Decisions log

- **Decision:** Split into a mechanical stage (raw commit replay, reusing
  the existing `encode_commit`) and a separate LLM-driven stage
  (higher-level bullet facts). **Rationale:** Salvador's framing —
  mechanical replay is free, deterministic, and testable without any LLM
  dependency; it should not wait on, or be blocked by, whether an LLM
  profile is even configured.
- **Decision:** Stage 1's replay window is bounded by the *same
  character budget* as stage 2's LLM input, not an independent fixed
  commit count (originally proposed as "last 200 commits").
  **Rationale:** `git log` itself is cheap regardless of depth — the real
  cost is `_load_memory_block_at` parsing every line of `semantic.jsonl`
  on every future load, forever, for entries that mostly sit past the
  8000-char default load budget and are therefore never actually
  surfaced. A fixed count of 200 was disproportionate to what the
  existing load path would ever expose; unifying onto one bounded read
  also removes a second redundant `git log` call.
- **Decision:** Root-doc discovery generalized from "look for a README"
  to "glob `*.md`/`*.txt`/`*.htm*` at repo root." **Rationale:**
  Salvador's call — simpler than name-guessing across README/README.md/
  README.txt/readme.rst conventions, and naturally also picks up other
  root-level project docs (e.g. this repo's own `CLAUDE.md`/`AGENTS.md`)
  that carry real project context a README-only search would miss.
- **Decision:** Multiple root docs are prioritized by mtime (newest
  first) rather than a fixed name-priority list. **Rationale:** matches
  the recency-biased philosophy already used for git-log inclusion and
  for `_load_memory_block_at` itself — a recently-touched doc is more
  likely to reflect current project state than an untouched one,
  regardless of filename convention.
- **Decision:** The busy indicator reuses the `✦` character already in
  the default prompt template, distinguished by SGR blink (code 5) while
  busy vs. plain color while idle — rather than a new glyph, a printed
  "[thinking...]" line, or a timer-driven animated spinner sequence.
  **Rationale:** Sasha's `input_fn` is backed by GNU readline
  (`__main__.py`, also wired for tab-completion and history) — readline
  owns the terminal line while blocked waiting for keystrokes, so a
  background thread repainting that line risks corrupting in-progress
  editing. SGR blink sidesteps this entirely: the escape sequence is
  printed once as part of the static prompt string, and the *terminal
  itself* renders the ongoing animation with zero further writes from
  Sasha. Confirmed empirically (Salvador tested a throwaway script) that
  SGR 5 renders as an actual blink in his terminal; SGR 6 ("rapid blink")
  was found to render identically to SGR 5 there, so only SGR 5 is used.
  A true continuously-ticking spinner (frame changing on a timer,
  independent of prompt draws) was considered and rejected as requiring
  a readline replacement — out of scope for this feature.
- **Decision:** Stage 2 retries at most once per Sasha session per repo,
  keyed on session-lifetime bookkeeping (`_bootstrap_attempted`), not on
  every place-shift. **Rationale:** avoids repeated LLM calls/cost if a
  user bounces in and out of a repo whose profile happens to be
  unreachable; a fresh process is a fresh, cheap opportunity to retry
  without needing any explicit "resummarize" command.
- **Decision:** A `threading.Lock` is added around `_append_entry_at`,
  amending (not reversing) Phase 17's "write shape, not locks" contract.
  **Rationale:** that contract's reasoning addressed cross-process
  concurrency (separate terminals) via `O_APPEND`'s small-write
  atomicity; this feature introduces the first same-process background
  thread memory.py has ever had, a genuinely new condition that
  contract's original reasoning didn't cover and shouldn't be stretched
  to cover.
- **Decision:** The completion announcement names the single file when
  exactly one root doc was consumed, states a bare count when several
  were, and omits any doc mention when the summary was built from commit
  history alone — no full itemized file listing, no commit count.
  **Rationale:** Salvador's call — full provenance detail (which exact
  files, how many commits) doesn't need to be pushed into an
  unprompted announcement; the underlying entries are already inspectable
  via the existing `sasha memory` built-in and already loaded into the
  session's context, so asking Sasha directly is sufficient for anyone
  who wants the detail.
- **Decision (rejected):** Per-entry source attribution via the existing
  `pointer` field (e.g. `pointer="README.md"` per bootstrap bullet).
  **Rationale for rejection:** would require prompting the LLM to tag
  each bullet's source and parsing that out reliably — meaningfully more
  moving parts for a traceability feature nobody asked for yet; the
  aggregate one-line announcement already satisfies the "quick reference"
  need that motivated this discussion.
