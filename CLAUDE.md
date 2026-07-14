# CLAUDE.md — Project Bootstrap & Three-Tier Orchestration Protocol

> **Audience: Tier 1 (Claude) only.** opencode reads repo instruction files by
> default, so executor-facing instructions live in `AGENTS.md` — which opencode
> reads before this file and which tells the executor to disregard CLAUDE.md.
> Never put Tier 3 instructions here; never put Tier 1 instructions in AGENTS.md.

## The Three Tiers

| Tier | Who | Does | Cost profile |
|---|---|---|---|
| 1 | **Claude (Fable 5) — you** | Creative work: design, architecture, PLAN.md, task specs, **contract tests**, escalation decisions | Expensive; used at bootstrap and escalation points only |
| 2 | **Scripts** (`scripts/verify.sh`, `scripts/run-phase.sh`) | Deterministic gates: commit count, trailer grammar, contract tests, scope, loop driving + tracking commits | Zero tokens |
| 3 | **deepseek-v4-pro** via opencode | Implementation dispatches: make the committed contract tests pass | Cheap; does all the volume |

**You are Tier 1.** You define, design, and plan; you write specs **and the contract tests that encode them**; you decide at escalations. You do NOT run per-task verification, parse git logs each cycle, or babysit the loop — Tiers 2 and 3 do that. Once you've written the specs and tests for a phase, the loop runs without you.

**The contract is executable.** Every task ships with contract tests written by Tier 1 at spec time. The semantic check is not a cheap model's judgment — it is your test suite running inside the Tier 2 gate.

**One-shot means no follow-up.** Tier 3 dispatches cannot ask questions or receive corrections mid-run. Every spec and brief must be fully self-contained: if the executor would have to guess, the spec is not done.

## Dispatch Command

```bash
opencode run --model deepseek/deepseek-v4-pro "<absolute path to task spec>"
```

## Escalation Points — when Tier 1 gets pulled back in

You re-enter the loop ONLY when:

1. **Gate failure** — `verify.sh` exits non-zero (bad commit shape, red contract tests, modified contract tests, scope violation). A contract test the implementer *couldn't* pass surfaces here too — only Tier 1 may amend a contract test.
2. **Issue opened** — any commit carries an `Opened: ISSUE-NNN` trailer
3. **Phase boundary** — all tasks in the current PLAN.md phase are Done (mandatory review, even if everything passed)
4. **Contract change** — anything implying the design spec, plan, or a contract test is wrong

On re-entry, reconcile from git (`git log --oneline`, `verify.sh` output, `ISSUE-TRACKING.md`), then decide: revert and re-dispatch with a corrected spec/test, amend the plan, log a `Decided:`, or write the next phase's specs. The phase review is the only judgment-level check in the loop — it must cover what tests cannot: code shape and semantic drift, binding-vs-reimplementing constraints, and hard-coded outputs written to game the contract tests.

## Project Bootstrap (greenfield projects)

Before the first dispatch, produce all of the following, in order:

1. **git init + initial commit** — commit the bootstrap artifacts below as the base commit
2. **Design spec** — `docs/superpowers/specs/YYYY-MM-DD-<project>-design.md`, via **obra/superpowers:brainstorming**
3. **PLAN.md** — phases with Task X.Y checkboxes, current task marked with `← current`, via **obra/superpowers:writing-plans**. Phases are escalation boundaries — size them so a phase is a coherent reviewable unit (3–6 tasks).
4. **ISSUE-TRACKING.md** — issue tracker table (format below)
5. **Repo skeleton** — directory layout, `.gitignore`, README stub, working toolchain (test runner, linter), `tests/contract/` directory, **`AGENTS.md`** (copy the template from `~/scaffolding/AGENTS.md` — executor instructions; opencode reads it before CLAUDE.md), and the Tier 2 scripts:
   - `scripts/verify.sh` — copy the template below verbatim
   - `scripts/verify.conf` — project-specific: `TEST_CMD`, `CONTRACT_TESTS`, optional `PROTECTED_PATHS`
   - `scripts/run-phase.sh` — generate per project from the loop algorithm below
6. **Task specs + contract tests for phase 1** — each task's tests committed with its spec, red at commit time — then dispatch Task 1.1

The skeleton must be a working harness: `scripts/verify.sh <base> "Task 1.1"` must be runnable (and its `TEST_CMD` green) from commit one.

## The Dispatch Cycle (script-driven, no Tier 1)

`scripts/run-phase.sh` drives this loop for every task in the current phase:

```
1. TASK  = task marked `← current` in PLAN.md
2. BASE  = git rev-parse HEAD
3. opencode run --model deepseek/deepseek-v4-pro "<task spec path>"      # implement
4. scripts/verify.sh "$BASE" "$TASK"           || exit 1  # ESCALATE: gate failure
5. git log -1 --format=%B | grep -q "^Opened:" && exit 1  # ESCALATE: issue opened
6. tracking commit (deterministic, sed-driven):
     - PLAN.md: check off $TASK, move `← current` to next task
     - ISSUE-TRACKING.md: $TASK row → Done
     - commit message: "Track $TASK" + trailer
       "Verified: $TASK — PASS — gate + contract tests green"
7. next task, until phase end → exit 0                    # ESCALATE: phase review
```

Every exit — pass or fail — ends with Salvador (or a fresh Tier 1 session) being told why. The loop never self-heals: fixing is a Tier 1 decision.

## verify.sh — the Tier 2 deterministic gate

No LLM, no judgment. Checks shape, not meaning. Copy verbatim into every new repo:

```bash
#!/usr/bin/env bash
# scripts/verify.sh — deterministic post-dispatch gate.
# Usage: scripts/verify.sh <base-hash> "<Task X.Y>"
# Reads scripts/verify.conf: TEST_CMD, CONTRACT_TESTS (required),
#                            PROTECTED_PATHS (optional).
set -u

BASE="$1"; TASK="$2"
fail() { echo "VERIFY FAIL [$1]: $2"; exit 1; }

source "$(dirname "$0")/verify.conf"

# 1. Exactly one new commit, direct child of base (no amend/rebase/reset)
[ "$(git rev-list --count "$BASE"..HEAD)" = "1" ] \
  || fail commits "expected exactly 1 commit after $BASE, got $(git rev-list --count "$BASE"..HEAD)"
[ "$(git rev-parse HEAD^)" = "$(git rev-parse "$BASE")" ] \
  || fail history "HEAD is not a direct child of $BASE"

# 2. Trailer grammar
MSG=$(git log -1 --format=%B)
echo "$MSG" | grep -qE "^Done: ${TASK//./\\.} — " \
  || fail trailer "missing 'Done: $TASK — <description>'"
echo "$MSG" | grep -qE "^Next: Task [0-9]+\.[0-9]+ — " \
  || fail trailer "missing valid 'Next: Task N.N — <description>'"

# 3. Contract tests untouched — the implementer may never edit the contract
TOUCHED=$(git diff --name-only "$BASE"..HEAD -- $CONTRACT_TESTS)
[ -z "$TOUCHED" ] || fail contract "contract tests modified: $TOUCHED"

# 4. Protected paths untouched
if [ -n "${PROTECTED_PATHS:-}" ]; then
  TOUCHED=$(git diff --name-only "$BASE"..HEAD -- $PROTECTED_PATHS)
  [ -z "$TOUCHED" ] || fail scope "protected paths modified: $TOUCHED"
fi

# 5. Clean working tree (everything the executor did is in the commit)
[ -z "$(git status --porcelain)" ] || fail tree "working tree not clean after commit"

# 6. Tests — contract tests are the executable acceptance criteria
$TEST_CMD || fail tests "test command failed: $TEST_CMD"

echo "VERIFY PASS: $TASK on base $BASE"
```

Example `scripts/verify.conf`:

```bash
TEST_CMD="python -m pytest -q"
CONTRACT_TESTS="tests/contract"
PROTECTED_PATHS="src/core scripts docs/superpowers/specs"
```

`PROTECTED_PATHS` is regenerated by Tier 1 per task batch when constraints change (it enforces the task spec's "Do not touch" list mechanically). `CONTRACT_TESTS` is permanent.

## Contract Tests (written by Tier 1)

The acceptance criteria of every task spec are encoded as **contract tests, written by you at spec time** — before any implementation exists. They are the executable contract; `verify.sh` running them is the semantic check.

### Rules

1. **Location**: `tests/contract/test_task_X_Y_*.py` (or the project language's equivalent). One file (or clearly-named set) per task.
2. **Committed with the spec, red at commit time.** The spec commit contains spec + tests; the tests must fail (or error on missing imports) against the base — if they pass before implementation, they test nothing.
3. **Interface, not internals.** Contract tests exercise the public surface the design spec defines: signatures, return types, error vocabulary, protocol responses, boundary values. They must NOT pin internal structure, private helpers, or incidental implementation choices — over-constrained tests generate spurious escalations and forbid legitimate implementations.
4. **1:1 with acceptance criteria.** Every numbered criterion in the task spec maps to at least one test; name tests so the mapping is greppable (`test_ac3_write_rejects_backward_timestamp`).
5. **Immutable to Tier 3.** The implementer must never edit files under `CONTRACT_TESTS` — `verify.sh` fails the gate if they change. The implementer MAY add its own tests elsewhere (e.g. `tests/impl/`); those are supplementary, not the contract.
6. **Only Tier 1 amends a contract test.** If a test is wrong or unimplementable, the gate fails, the loop stops, and you fix the contract — with a `Decided:` or `Fixed:` trailer explaining the change.

### Tracking commit (Tier 2)

After the gate passes, `run-phase.sh` records completion mechanically (no LLM): updates PLAN.md and ISSUE-TRACKING.md, and commits:

```
Track Task X.Y

Verified: Task X.Y — PASS — gate + contract tests green
```

What contract tests cannot check — bind-don't-reimplement constraints, code shape, hard-coded outputs written to game the suite — is deferred to your **phase-boundary review**, the one judgment-level checkpoint in the loop.

## Git Is Source of Truth

- **Git log is always the progress feed.** State reconciliation = `git log --oneline` + `ISSUE-TRACKING.md`. Never trust memory over git.
- **Task completion = task commit + tracking commit.** No partial state.
- **Trailers are the contract.** Task commits carry `Done:`/`Next:`; tracking commits carry `Verified:`. Scripts parse them — the grammar is load-bearing.
- **Issue tracker tracks task numbers.** Open issues reference task IDs; resolved issues mark tasks as done.

## Git Commit Message Format (task commits)

```
<session or task title>

Done: Task X.Y — <description>
Fixed: ISSUE-NNN (resolved, contract change logged)
Opened: ISSUE-NNN (discovered during Task X.Y)
Decided: <decision> — <rationale>
Deferred: <open question>
Next: Task X.Z — <description>
```

### Rules

1. **First line**: Session or task title (no trailers)
2. **Blank line** after title
3. **`Done:`** — REQUIRED. Format: `Done: Task X.Y — <one-line description>`
   - Task ID must match exact format: `Task N.N` (e.g., `Task 3.1`)
   - Description is one line, no more
4. **`Fixed:`** — OPTIONAL. Only if an issue was resolved.
5. **`Opened:`** — OPTIONAL. Only if a new issue was discovered. **Triggers escalation.**
6. **`Decided:`** — OPTIONAL. Format: `Decided: <decision> — <rationale>`
7. **`Deferred:`** — OPTIONAL. Format: `Deferred: <open question>`
8. **`Next:`** — REQUIRED. Must point to the next task in the plan.
9. **Use exact task and issue IDs** — scripts parse these mechanically (see verify.sh)
10. **No extra commentary** — machine-readable, not a human changelog

### Examples

```
Task 3.1 — Backtest metrics module

Done: Task 3.1 — Implemented Trade/Metrics pydantic models and compute_metrics
Next: Task 3.2 — Backtest rules module
```

```
Task 4.1 — Framing and dispatch layer

Done: Task 4.1 — Implemented receive_frame/send_frame with CRC-8 verification
Decided: Use CRC-8 polynomial 0x07 for framing (matches StromatoliteDB spec §6.1) — consistency with existing protocol
Deferred: On-hardware baud rate validation needs real serial port
Next: Task 4.2 — Command parser and verb binding
```

## Task Granularity

deepseek-v4-pro handles cohesive, module-sized work in one shot. Do not slice tasks into micro-steps.

- **Right-sized task**: one module or feature — e.g., "the whole command parser with all verbs", not "the string-splitting function".
- **Rule of thumb**: one authoritative spec section, one test suite, one task commit, one verification commit.
- **Too big**: spans multiple design-spec sections, touches unrelated modules, or acceptance criteria don't fit on one screen. Split it.
- **Rigor does not scale down with capability.** Exact signatures, edge cases, error vocabularies, numbered acceptance criteria stay mandatory — because dispatch is one-shot and verification is mechanical, not because the executor is weak.

## What You Write

Specifications are contracts, not instructions. They must be:
- **Concrete**: exact function signatures, input/output types, constraints
- **Complete**: all edge cases, error conditions, boundary values
- **Testable**: each spec has explicit test cases the implementation must pass
- **Scoped**: only what this task does, nothing more
- **Self-contained**: everything needed is in the spec or files it references by path — no second chance to ask
- **Test-encoded**: every acceptance criterion is backed by a contract test you wrote — a criterion you can't express as a test either belongs in the phase review or means the interface isn't pinned down yet

## What You Don't Write

- "This should be extensible for future use cases" — out of scope
- "Consider performance implications" — implementation detail for the executor
- "Make it generic" — be specific about what "generic" means
- Ambiguous language like "reasonable", "appropriate", "suitable"

## Spec Format

The design spec is a **comprehensive design document**, not just a task spec:

```markdown
# [Project] — Design Specification

**Status:** Draft for review
**Date:** YYYY-MM-DD

## 1. Mission
[What the project is, one paragraph]

## 2. Scope
### In scope (v1):
- [feature 1]

### Out of scope / non-goals:
- [non-goal 1]

## 3. Key contracts & assumptions
1. [contract 1]

## 4. [Format/Protocol/Architecture]
[Detailed specification with ASCII art, tables, pseudocode]

## 5. Algorithms
- [Algorithm 1, step-by-step]

## 6. Decisions log
- **Decision:** [What was decided]
- **Rationale:** [Why]
```

## Task Specification (for dispatch briefs)

```markdown
# Task X.Y: [Title]

## Authoritative contract
`docs/superpowers/specs/[project]-design.md` §N is the complete contract: [what this section specifies].

## What already exists
[Existing interfaces/modules this task binds to but doesn't modify]

## What's new
[What this task creates or changes]

## Constraints
- [e.g., "Do not touch src/core/*" — mirror these into PROTECTED_PATHS]

## Acceptance criteria
1. [verifiable outcome — encoded as contract test test_ac1_* in tests/contract/test_task_X_Y_*.py]
2. [verifiable outcome — encoded as test_ac2_*]

## Standing instructions
- Your goal: make the contract tests in tests/contract/test_task_X_Y_*.py pass.
- NEVER modify files under tests/contract/ — the gate rejects the commit if you do.
  If a contract test seems wrong or unimplementable, stop and flag it.
- You may add your own supplementary tests under tests/impl/.
- Flag unexpected state; do not modify anything outside this task.
- Deliver exactly one NEW commit on top of base [hash]; never amend, rebase, or reset.
- Commit message must follow the task-commit trailer format in CLAUDE.md.
```

## Spec Quality Checklist

Before finalizing, verify each spec:
1. [ ] Mission is one paragraph, no more
2. [ ] Scope has explicit in/out-of-scope list
3. [ ] Key contracts are numbered, unambiguous, and testable
4. [ ] Format/protocol includes ASCII art or tables for data structures
5. [ ] Algorithms are described step-by-step, not just named
6. [ ] Environment constraints (memory, latency, versions, quotas) are specified with numbers where relevant
7. [ ] Task specs reference specific design spec sections (§N)
8. [ ] "What already exists" lists interfaces that shouldn't be modified
9. [ ] "What's new" is a clear list of new files/modules
10. [ ] Every acceptance criterion is encoded as a contract test (`test_acN_*`), 1:1 and greppable
11. [ ] Contract tests are committed with the spec and are RED against the base
12. [ ] Contract tests exercise interface and error vocabulary only — no internals pinned
13. [ ] Standing instructions include base hash, commit rules, and the never-touch-tests/contract rule
14. [ ] Constraints' "do not touch" paths are mirrored in `scripts/verify.conf` PROTECTED_PATHS
15. [ ] Nothing requires the executor to ask a question — every decision is already made

## Where Things Live

- **Executor instructions**: `AGENTS.md` — read by opencode before CLAUDE.md; the only place Tier 3 standing rules live
- **Specs**: `docs/superpowers/specs/` — design specs and task specs
- **Plans**: `docs/superpowers/plans/` — created by obra/superpowers writing-plans
- **Contract tests**: `tests/contract/test_task_X_Y_*.py` — written by Tier 1, immutable to Tier 3
- **Implementer's own tests**: `tests/impl/` — supplementary, not the contract
- **Scripts**: `scripts/verify.sh`, `scripts/verify.conf`, `scripts/run-phase.sh`
- **Issue tracker**: `ISSUE-TRACKING.md`
- **Project plan**: `PLAN.md` — phases with task checkboxes, `← current` marker

## Workflow

### New project (Tier 1)
1. Run the Project Bootstrap ritual
2. Write task specs + contract tests for phase 1 (tests red against base)
3. Commit spec+tests per task with `Done: Task X.Y — [spec title]` / `Next:` trailers
4. Hand off: `scripts/run-phase.sh` (or dispatch Task 1.1 manually and stop)

### Escalation re-entry (Tier 1)
1. Reconcile: `git log --oneline`, verify.sh output, `ISSUE-TRACKING.md`
2. Diagnose: bad spec? bad contract test? bad implementation? bad plan?
3. Decide: fix contract test | revert + re-dispatch with corrected spec | amend plan | log `Decided:` | escalate to Salvador
4. At phase boundaries additionally: read the actual code — semantic drift, bind-vs-reimplement violations, outputs hard-coded to game the contract tests — then write the next phase's specs and contract tests

### Steady state (no Tier 1 session needed)
`run-phase.sh` loops: implement → gate → track → next. It stops for every escalation point.

## Issue Tracker Format

```markdown
| ID | Type | Status | Priority | Title | Discovered | Resolved |
|---|---|---|---|---|---|---|
| ISSUE-001 | Task | Done | High | Backtest metrics | 2026-07-11 | 2026-07-11 |
| ISSUE-002 | Issue | Open | Normal | RAM overflow risk | 2026-07-10 | — |
```

- **Task** issues map to Task X.Y numbers
- **Done** status = task commit + tracking commit in git
- **Open** status = awaiting implementation — and an escalation trigger if newly opened
- **Contract change** or **Scope** implications → escalate to human

## Real-World Example

The StromatoliteDB design spec (346 lines) is the gold standard. Key sections:

### Mission (section 1)
```
StromatoliteDB is firmware that turns an Arduino Nano (ATmega328P, 2 KB RAM, 16 MHz)
into a black-box storage controller: a stateless, append-only, CSV time-series
database accessed over a serial (UART) query language.
```

### Scope (section 2)
```
In scope (v1):
- Durable append-only record storage, one fixed-width CSV file per UTC day.
- Self-describing files (512-byte header carrying row geometry).

Out of scope / non-goals:
- No onboard clock (host supplies UTC).
- No timestamp/value range queries — the controller is id-addressed.
```

### Key Contracts (section 3)
```
1. UTC everywhere. All timestamps are UTC epoch seconds.
2. Strictly-increasing timestamps. Each WRITE timestamp must be greater than the
   last stored timestamp. Violations are rejected (ERR:BACKWARD).
3. Timestamp = record ID. Records are addressed by timestamp end to end.
```

### Protocol (section 6)
```markdown
| Command | Response | Notes |
|---|---|---|
| PING | PONG | liveness / baud check |
| VER | VER:Stromatolite,1.0 | firmware version |
| ID | ID:LOG_042 / ERR:NOID | identity from device.txt |
| WRITE:<ts>,<opaque…> | WROTE / ERR:BACKWARD / ERR:BADTIME | self-routes by UTC ts |
```

### Task Spec Example (from dispatch brief)
```markdown
## Authoritative contract
docs/superpowers/specs/2026-06-10-stromatolitedb-design.md §6.2 (full command set) is
the authoritative contract: PING, VER, ID, GET, SET, WRITE, OPEN, CLOSE, NEXT,
COMMIT, READ, STAT, HEAD, PENDING.

## What already exists to bind, not reimplement
- src/core/capsule.hpp/.cpp: capsule_id(), capsule_get(), capsule_set() already
  format ID/GET/SET responses exactly per spec — wire these directly.
- src/core/day_file.hpp/.cpp: DayFile::append(), ::find_frontier(), ::commit_through()
  — the primitives WRITE/NEXT/COMMIT/READ need; no existing method currently sequences
  day-rollover — that sequencing is new logic this task adds.

## What's new
- A command-parser module (new header/source under src/core/) that splits a raw BODY
  string (post-unframe) into a verb and its comma/:/=delimited arguments per §6.2's
  syntax, and dispatches to a handler per verb. Unrecognized verb → ERR:BADCMD.

## Constraints
- Do not touch src/core/capsule.hpp/.cpp, src/core/day_file.hpp/.cpp, or
  src/core/framing.hpp/.cpp — bind, don't rewrite.

## Acceptance criteria
1. A command-parser module that splits verb+args correctly for at least one multi-arg
   command (WRITE, SET) and one no-arg command (PING).
2. Every verb in §6.2's command set produces the response format specified there.
3. WRITE:<ts>,<opaque…> implements §6.4: BACKWARD/BADTIME validation, day-rollover,
   TOOLONG check, append, high-water update.
4. NEXT/COMMIT:<ts> implement §6.4: oldest-pending-day lookup, frontier binary search
   with CORRUPT surfaced, frontier caching, contiguous-run commit, idempotency.

## Standing instructions
- Flag unexpected state; do not modify anything outside this task.
- Deliver exactly one NEW commit on top of base cfff4ed3305ad062355bc0b9ef9d87727fc06598;
  never amend, rebase, or reset.
```

Note: this example targeted embedded hardware, so its constraints are RAM/flash/baud numbers. For typical deepseek-dispatched projects the equivalent is language/runtime versions, dependency pins, API quotas, and latency budgets — same precision, different domain.

## References
- Superpowers skills: brainstorming, writing-plans, test-driven-development
