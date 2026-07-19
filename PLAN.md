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

## Phase 12 — Path completion fixes (ISSUE-029)

- [x] Task 12.1 — readline delims + Executor.cwd threading for path completion

## Phase 13 — Personality file (ISSUE-030)

- [x] Task 13.1 — personality.py + Session integration + __main__.py wiring

## Phase 14 — Consume notes/stderr once (ISSUE-031)

- [x] Task 14.1 — build_messages() clears notes/last_stderr after use

## Phase 15 — First-run LLM setup & provider activation (ISSUE-026)

- [x] Task 15.1 — config.py: self-install, api_key_required, set_api_key patcher
- [x] Task 15.2 — repl.py + __main__.py: masked key-entry flow on 'sasha profile <name>'

## Phase 16 — Multi-tool registry & per-tool permissions (ISSUE-033)

- [x] Task 16.1 — src/sasha/tools.py: Tool registry, ToolResult, 5 tools, path sandboxing
- [x] Task 16.2 — agent.py registry-driven dispatch + generalized repetition guard + README

## Phase 17 — Project memory (git-repo-scoped long-term memory) (ISSUE-034)

- [x] Task 17.1 — src/sasha/memory.py + Executor.last_command/last_result
- [x] Task 17.2 — remember tool (tools.py) + Session mutable memory block
- [x] Task 17.3 — repl.py: place-shift detection, commit hook, sasha memory/forget built-ins

## Phase 18 — General (cross-project) memory + crash trigger (ISSUE-036)

- [x] Task 18.1 — memory.py: private _at layer, general_* functions, crash encode pair
- [x] Task 18.2 — remember tool general-memory fallback + Session's second memory slot
- [x] Task 18.3 — repl.py: load general memory, crash-trigger hook, two-tier sasha memory

## Phase 19 — Memory bootstrap from git history (ISSUE-038)

- [x] Task 19.1 — memory.py: bounded_history, root_docs, encode_bootstrap, write lock
- [ ] Task 19.2 — prompt.py busy-star token (\S, blink) + Repl background-task counter ← current
- [ ] Task 19.3 — repl.py: stage 1/2 bootstrap wiring into _check_place_shift
