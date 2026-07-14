# Task 3.3: End-to-end smoke + README

## Authoritative contract
The whole design spec (`docs/superpowers/specs/2026-07-14-sasha-design.md`);
§11 for the smoke-test strategy. This task makes the e2e contract pass and
completes the README.

## What already exists
Everything (Tasks 1.1–3.2). `tests/contract/test_task_3_3_e2e.py` drives a
real `python -m sasha` subprocess under a PTY against `sse_stub.py` via a
temp config whose `local` profile points at the stub.

## What's new / may change
- `README.md` — full usage docs: install (`pip install -e .`), config file
  format with both profiles, the trigger matrix in prose, muting
  (`sasha mute`/`unmute`), the confirmation UX, the safety guarantees
  (nothing runs unconfirmed; replies never execute), and EVERY §10 line
  verbatim in a reference table.
- Glue fixes: if an e2e contract test exposes an integration bug, fix it in
  the responsible module — any file EXCEPT the protected paths below and
  `tests/contract/`. Keep fixes minimal; all previous contract tests must
  stay green.

## Constraints
- Do not touch: `scripts/`, `docs/superpowers/`, `CLAUDE.md`, `AGENTS.md`,
  `PLAN.md`, `ISSUE-TRACKING.md`, `.gitignore`, `pyproject.toml`,
  `src/sasha/config.py`, `src/sasha/mutes.py`, `src/sasha/executor.py`,
  `src/sasha/triggers.py`, `tests/contract/`.

## Acceptance criteria
(encoded 1:1 in `tests/contract/test_task_3_3_e2e.py`)
1. Typing `550*11` streams the stub's answer to the terminal and the prompt
   returns — `test_ac1_natural_language_line`.
2. A real failing command prints the §10 offer line; Enter consults; the
   mute streak was NOT incremented — `test_ac2_offer_accept_no_penalty`.
3. Three ignored offers print the §10 muting notice; a fourth failure prints
   no offer — `test_ac3_mute_after_three_ignores`.
4. A stub-scripted `run_command` renders the §7.4 confirmation block;
   answering `n` executes nothing; follow-up text appears —
   `test_ac4_confirmation_gate_end_to_end`.
5. With the endpoint down, `??` prints the §10 unreachable line and the next
   ordinary command still executes — `test_ac5_llm_down_shell_survives`.
6. README documents every §10 line and both profiles —
   `test_ac6_readme_documents_vocabulary`.

## Standing instructions
- Your goal: make `tests/contract/test_task_3_3_e2e.py` pass with the ENTIRE
  suite green (`bash scripts/run-tests.sh`).
- NEVER modify files under `tests/contract/` — the gate rejects the commit
  if you do. If a contract test seems wrong or unimplementable, stop and
  flag it; do not commit.
- Deliver exactly ONE new commit on top of the HEAD you started from; never
  amend, rebase, or reset; leave the working tree clean.
- Commit message per AGENTS.md, with trailers:
  `Done: Task 3.3 — <one line>`,
  `Next: Task 4.1 — v2 planning (Tier 1 phase review first)`, and the
  Co-Authored-By signature line from your dispatch prompt.
