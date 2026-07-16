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
- [x] Task 2.2 — Session & context building
- [x] Task 2.3 — Agent loop with confirmation gate

## Phase 3 — REPL & product assembly

- [x] Task 3.0 — Agent loop hardening (tool-turn liveness)
- [x] Task 3.1 — REPL dispatch
- [x] Task 3.2 — Entry point & terminal wiring
- [x] Task 3.3 — End-to-end smoke + README
- [x] Task 3.4 — REPL streaming passthrough (v1 review finding)

## Phase 4 — Job control (ISSUE-015 / ISSUE-016)

- [x] Task 4.1 — tcsetpgrp handoff, Ctrl+Z termination, TUI passthrough

## Phase 5 — Quick wins (ISSUE-017 / ISSUE-020)

- [x] Task 5.1 — Drop redundant AUTO_CONSULT preamble on exit 127
- [x] Task 5.2 — "Thinking" indicator during LLM consult

## Phase 6 — Output sink redesign (ISSUE-014 / ISSUE-018 / ISSUE-019)

- [x] Task 6.1 — Sink protocol + TerminalSink (structural; ISSUE-014/018)
- [x] Task 6.2 — Colorization + user@host prompt (ISSUE-019)

## Phase 7 — Confirmation gate colorization (ISSUE-023)

- [x] Task 7.1 — Per-line colors for the confirmation gate

## Phase 8 — write_line() missing-newline fix (ISSUE-024)

- [x] Task 8.1 — write_line() closes a dangling unterminated line before printing

## Phase 9 — Configurable prompt (ISSUE-022, Tier 1)

- [x] Task 9.1 — src/sasha/prompt.py: template engine + config.toml `prompt` key

## Phase 10 — Boot-time hint (ISSUE-027)

- [x] Task 10.1 — Profile + ?? tip lines printed once at Repl.loop() startup

## Phase 11 — Tab completion (ISSUE-028)

- [x] Task 11.1 — Own-logic completion: PATH scan, sasha builtins, readline wiring
- [x] Task 11.2 — bash-completion subprocess integration + graceful degradation

## v2 (outline only — specced after v1 review)

- Phase 12 — Multi-tool registry & per-tool permissions
- Phase 13 — Session persistence & resume
