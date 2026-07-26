# Directory recap on explicit `cd` — Design Specification

**Status:** Approved
**Date:** 2026-07-26
**Issue:** ISSUE-051

## 1. Mission

Sasha already recaps on `cd` into a git repo with saved project memory
(ISSUE-034/038, `repl.py:252`). This extends the same "quiet, show-up-only-
when-useful" recap to arbitrary directories — including git repos with no
project memory yet, and non-git directories — using `tool_log.jsonl`
(ISSUE-042/044) and git history as the "last activity" signal instead of
requiring a memory store.

## 2. Scope

### In scope (v1):
- A 3-stage escalation cascade (cheap deterministic checks first, an LLM
  call as the rare last resort) that decides, for a directory just `cd`'d
  into, whether there's anything coherent to recap and if so what.
- A new `src/sasha/directory_recap.py` module implementing the cascade.
- Wiring into `repl.py`'s existing per-command cwd-change detection,
  composed with the existing git-repo/memory recap as a gap-filler (never
  both print for the same `cd`).
- A `[directory_recap]` config.toml section (`enabled = true` default) and
  a `sasha directory-recap on|off` builtin command.

### Out of scope / non-goals:
- Migrating the existing git-repo/memory recap (ISSUE-034/038) onto this
  mechanism — it is left completely untouched.
- A general LLM-driven / conversational config-editing capability. This
  ships one narrow `config.py` patcher for one boolean, mirroring
  `set_api_key`'s pattern — not a reusable "Sasha edits its own settings"
  tool. (Deferred; see `docs/superpowers/STEERING.md`.)
- config.toml completeness / silent-bad-value-defaulting fixes — tracked
  separately as ISSUE-052.
- Detecting nested git repos more than one level deep (e.g. a repo nesting
  another repo under a `src/` subdirectory). Known limitation, not solved.
- Tuning the cascade's thresholds (`>= 2` child repos, `>= 3` fan-out,
  `> 14` days staleness) against anything beyond the real data explored in
  `docs/superpowers/STEERING.md`'s 2026-07-26 entry. They are starting
  points, not derived constants.

## 3. Key contracts & assumptions

1. **Escalation cascade, not pluggable engines.** Three exit points in one
   plain function, evaluated in fixed order. No interface, no runtime
   swapping — a pluggable "context engines" architecture was considered and
   explicitly rejected as premature (no second implementation exists to
   justify the abstraction).
2. **Filesystem shape is checked first, unconditionally, in every branch.**
   Log-derived signals are blind to any path Sasha has never been run
   inside (confirmed empirically: `/home/salvador/brains`, a real 14-project
   umbrella folder, showed only 7 sparse log entries all at one cwd — every
   log-only signal read as "thin but coherent"). The same blindness hid in
   what looked like the confident fast path too: `/home/salvador/mqtt`
   nests 2 independent child repos but has zero log history, so a rule
   order that checked "is a git repo" before filesystem shape misfired
   there identically. The fix is unconditional: filesystem shape (immediate
   child directories that are themselves git repos) is evaluated before any
   log-derived confidence, in every branch, with no exception.
3. **The synchronous path (stages 1–2) makes zero subprocess calls**, except
   exactly one `git log -1` in the direct-recap branch. The probe's
   log-derived `distinct_git_roots` signal (which shelled out to
   `git rev-parse` once per distinct historical cwd under the target) was
   found redundant against every real directory tested — stage 1's
   filesystem check already caught every umbrella case it did — and is
   dropped from the real design for exactly that reason.
4. **Gap-filler composition with the existing recap.** For any one `cd`,
   at most one recap prints. If the existing git-repo/memory recap
   (ISSUE-034/038) has something to say (a git root with ≥1 memory), this
   mechanism stays silent for that transition. It only runs when the
   existing mechanism does not — no git root, or a git root with zero
   memories yet.
5. **Dedup is per distinct path, per session** — independent of
   `_check_place_shift`'s existing git-root-only dedup. The first time a
   session `cd`'s into an exact path, it's considered once; returning to it
   later that session stays silent, matching the existing recap's own
   dedup philosophy (recap once, not on every revisit).
6. **The LLM stage is real but rare.** Across ~110 real git repos scanned
   on Salvador's machine, exactly one (`mqtt`) needed it — confirmed by
   Salvador to be a project that scans a firmware repo to extract
   credentials/service pointers for a separate consuming project, i.e. a
   case only content-reading (not structure-counting) can meaningfully
   describe. It runs on a background thread; it must never block the shell
   prompt from returning after `cd`.

## 4. Architecture

### 4.1 The cascade

```
cd into a NEW distinct path (not seen this session)
        |
        v
existing git-repo/memory recap already printed for this transition?
        |                                   |
       yes                                 no
        |                                   |
   (stop, nothing more to do)               v
                                  STAGE 1 — filesystem shape
                                  (immediate child dirs w/ their own .git)
                                       |
                     child_git_repo_count >= 2 ?
                     /                         \
                   yes                          no
                    |                            |
         self itself a git repo?          STAGE 2 — cheap log rules
           /              \                      |
         yes              no            entry_count == 0 ?  --------> SKIP (silent)
    STAGE 3            SKIP (silent,          |
   (escalate,          umbrella,         fan-out >= 3 ?  ------------> SKIP (silent)
    async)             not a repo)             |
                                        most-recent age > 14d ? ------> SKIP (silent)
                                                |
                                        self_is_git_root ?
                                          /            \
                                        yes             no
                                         |               |
                              RECAP DIRECTLY        STAGE 3
                              (sync, last commit)   (escalate, async)
```

### 4.2 Stage 1 — filesystem shape (sync, no subprocess)

For the target directory, list immediate subdirectories (skip dotfiles)
and count how many contain their own `.git` (file or directory — covers
worktrees). `child_git_repo_count >= 2` marks the path as an umbrella
shape. This check runs first and unconditionally, before any log data is
trusted, per contract #2.

### 4.3 Stage 2 — cheap log-derived rules (sync, no subprocess)

Read `tool_log.jsonl` (path resolved the same way `tool_log.py` does),
filter entries whose `cwd` equals the target or starts with
`target + "/"`. From that subset, compute: entry count, distinct cwd
count, immediate child fan-out (distinct first path segments below
target among the matched cwds), and the most recent entry's age. Apply
the rules in the order shown in §4.1. If `self_is_git_root` (target
itself contains `.git`) and none of the SKIP rules fired, recap directly.

### 4.4 Stage 2 output — direct recap

`git log -1 --format=%ar: %s` run once against the target (the one
permitted subprocess call), rendered as:

```
[sasha] <target.name>: last commit <age> — "<subject>"
```

printed synchronously, same framing/newline discipline as every other
Sasha status line (`TerminalSink.write_line`).

### 4.5 Stage 3 — LLM escalation (async, background thread)

Reached when: (a) the umbrella check fired but the target is itself a git
repo (the `mqtt` shape), or (b) stage 2's rules produced neither a SKIP
nor a direct recap.

**Input gathered before the call:** for the target and each of its child
git repos (capped at 5), the last commit's subject + relative age (same
`git log -1 --format=%ar: %s` call, one per repo); plus, if any exist, the
most recent ~10 `tool_log.jsonl` entries under the target's subtree
(executed command + relative age).

**Call:** uses whichever profile/`ChatClient` the current session's normal
consult already uses — no dedicated model or endpoint config.

**Prompt contract:** produce exactly one line, in the voice of the
existing recap lines (e.g.
`mqtt: extracts MQTT credentials from the iotd firmware repo for an
external consumer`), from the gathered data only. If nothing coherent can
be said, output nothing at all — explicitly instructed not to guess or
pad with filler.

**Execution:** runs on a background thread, started after the cascade
decides to escalate; `cd` and the shell prompt are never blocked. The
result (if non-empty) is printed via `write_async_line` (the fix already
shipped this session for background-thread status messages), prefixed
`[sasha] `, so it can never glue onto a subsequent prompt line.

### 4.6 Trigger integration (`repl.py`)

New module `src/sasha/directory_recap.py` (parallel to
`activity_indicator.py` / `tool_log.py` / `safe_commands.py`) implements
the cascade as a pure-ish function taking a target path and returning one
of: nothing, a direct-recap string, or "escalate" (in which case the
caller starts the background thread).

`repl.py` gains a new `set[str]` of paths already considered this session,
checked/updated on every distinct `Executor.cwd` change (not gated on git
root, unlike `_check_place_shift`'s own dedup) — see contract #5. The
cascade only runs when the existing git-repo/memory recap did not already
print for that same transition (contract #4).

## 5. Configuration

New `[directory_recap]` section:

```toml
[directory_recap]
enabled = true
```

Added to `_DEFAULT_CONFIG_TOML` (`config.py`) alongside the existing
sections, setting the precedent ISSUE-052 should later generalize to
every config surface. A new builtin command, `sasha directory-recap
on|off`, flips it via a narrow `config.py` patcher function mirroring
`set_api_key`'s already-proven safe in-place-edit pattern (touches only
its own line, leaves comments/other sections byte-identical). This is
deliberately not a general config-editing tool — see §2 non-goals.

## 6. Testing strategy

- Cascade decision logic (stages 1–2, the ordering in §4.1) tested as pure
  functions against constructed directory-shape/log fixtures — no real
  filesystem or subprocess needed, matching "interface not internals."
- Stage 3's LLM call tested via the existing `StubServer` pattern already
  used elsewhere in this codebase's contract tests (e.g.
  `test_task_30_1_no_synthetic_continuation_turn.py`).
- `repl.py`'s gap-filler composition and per-path-per-session dedup tested
  at the integration level: existing recap fires → new mechanism silent;
  existing recap has nothing to say → new mechanism runs; same path
  revisited same session → silent both times.
- The one permitted synchronous subprocess call (`git log -1` in the
  direct-recap branch) and stage 3's own git calls are exercised against
  real throwaway git repos created in test fixtures, not against
  Salvador's actual machine.

## 7. Decisions log

- **Decision:** Escalation cascade (cheap rules first, LLM last resort) as
  a single plain function, not a pluggable engine architecture.
  **Rationale:** No second implementation existed to justify an interface;
  Salvador's own instinct toward "context engines" was explicitly flagged
  by him as overengineered before any design work began.
- **Decision:** Filesystem child-git-repo-count checked first, unconditionally,
  in every branch — including the "confident single repo" fast path.
  **Rationale:** Empirically necessary — log-derived signals alone
  misjudged both an ambiguous case (`brains`) and what looked like the
  confident case (`mqtt`) identically, both due to sparse/absent log
  history at paths Sasha had never been run inside.
- **Decision:** Drop the log-derived `distinct_git_roots` signal (would
  have required a `git rev-parse` subprocess call per distinct historical
  cwd under the target).
  **Rationale:** Redundant against every real directory tested once stage
  1's filesystem check exists; removing it makes the entire synchronous
  path subprocess-free except one `git log -1` call.
- **Decision:** Gap-filler composition — this mechanism never prints
  alongside the existing git-repo/memory recap for the same `cd`.
  **Rationale:** Salvador's explicit choice, to keep the existing shipped
  feature (ISSUE-034/038) completely untouched and avoid two recap lines
  competing for attention on one `cd`, consistent with the "calm, show up
  only when needed" direction from the STEERING.md steering discussion.
- **Decision:** Stage 3 runs async/background, never blocking the prompt.
  **Rationale:** Matches the existing memory-bootstrap precedent (Task 19)
  and this session's own `write_async_line` fix; the LLM stage is rare
  (1/110 real repos) but its latency shouldn't be paid synchronously by
  every plain `cd` that happens to hit it.
- **Decision:** One narrow `config.py` patcher for the `enabled` toggle,
  not a general LLM-driven config-editing tool.
  **Rationale:** Salvador's own scoping call — a general "Sasha edits its
  own settings via conversation" capability is a bigger, separate idea
  (touches every config key, raises its own trust/agency questions per the
  STEERING.md discussion) and shouldn't be bootstrapped as a side effect
  of one feature's config flag.
- **Decision:** config.toml completeness and silent-bad-value-defaulting
  are out of scope here, tracked as ISSUE-052.
  **Rationale:** Real, valid, but orthogonal problem — discovered while
  brainstorming this design, but fixing it doesn't require or block
  anything this design needs, and folding it in would blur two unrelated
  fixes into one task.
