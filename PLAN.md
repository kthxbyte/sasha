# Sasha — Project Plan

Tracker parsed by `scripts/run-phase.sh`. Detailed plan:
`docs/superpowers/plans/2026-07-14-sasha-v1-plan.md`. Authoritative contract:
`docs/superpowers/specs/2026-07-14-sasha-design.md`.

## Phase 1 — Core shell (working dumb shell, no LLM)

- [x] Task 1.1 — Package skeleton + config module
- [x] Task 1.2 — Mute store
- [x] Task 1.3 — Executor core (subprocess, exit codes, side channel)
- [x] Task 1.4 — Executor PTY tees (stderr always, stdout opt-in)
- [x] Task 1.5 — Trigger engine

## Phase 2 — LLM plumbing (text answers end-to-end)

- [x] Task 2.1 — Streaming chat client
- [ ] Task 2.2 — Session & context building ← current
- [ ] Task 2.3 — Agent loop with confirmation gate

## Phase 3 — REPL & product assembly

- [ ] Task 3.1 — REPL dispatch
- [ ] Task 3.2 — Entry point & terminal wiring
- [ ] Task 3.3 — End-to-end smoke + README

## v2 (outline only — specced after v1 review)

- Phase 4 — Multi-tool registry & per-tool permissions
- Phase 5 — Session persistence & resume
