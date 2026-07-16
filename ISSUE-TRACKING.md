# Sasha — Issue Tracker

| ID | Type | Status | Priority | Title | Discovered | Resolved |
|---|---|---|---|---|---|---|
| ISSUE-001 | Task | Done | Normal | Task 1.1 — Package skeleton + config module | 2026-07-14 | 2026-07-14 |
| ISSUE-002 | Task | Done | Normal | Task 1.2 — Mute store | 2026-07-14 | 2026-07-14 |
| ISSUE-003 | Task | Done | Normal | Task 1.3 — Executor core (subprocess, exit codes, side channel) | 2026-07-14 | 2026-07-14 |
| ISSUE-004 | Task | Done | Normal | Task 1.4 — Executor PTY tees (stderr always, stdout opt-in) | 2026-07-14 | 2026-07-14 |
| ISSUE-005 | Task | Done | Normal | Task 1.5 — Trigger engine | 2026-07-14 | 2026-07-14 |
| ISSUE-006 | Task | Done | Normal | Task 2.1 — Streaming chat client | 2026-07-14 | 2026-07-14 |
| ISSUE-007 | Task | Done | Normal | Task 2.2 — Session & context building | 2026-07-14 | 2026-07-14 |
| ISSUE-008 | Task | Done | Normal | Task 2.3 — Agent loop with confirmation gate | 2026-07-14 | 2026-07-14 |
| ISSUE-009 | Task | Done | Normal | Task 3.1 — REPL dispatch | 2026-07-14 | 2026-07-14 |
| ISSUE-010 | Task | Done | Normal | Task 3.2 — Entry point & terminal wiring | 2026-07-14 | 2026-07-14 |
| ISSUE-011 | Task | Done | Normal | Task 3.3 — End-to-end smoke + README | 2026-07-14 | 2026-07-14 |
| ISSUE-012 | Task | Done | High | Task 3.0 — Agent loop hardening (tool-turn liveness) | 2026-07-14 | 2026-07-14 |
| ISSUE-013 | Task | Done | High | Task 3.4 — REPL streaming passthrough (v1 review finding) | 2026-07-14 | 2026-07-14 |
| ISSUE-014 | Issue | Done | Normal | out() conflates lines and stream chunks — repl hardcodes sys.stdout + a print identity check; redesign sink at v2 | 2026-07-14 | 2026-07-15 |
| ISSUE-015 | Issue | Done | High | REPL blocking on synchronous command execution (e.g. 'yes') | 2026-07-14 | 2026-07-14 |
| ISSUE-016 | Issue | Done | High | TUI programs (vi, mc, etc.) do not display correctly (best effort — mechanism fixed, visual rendering not contract-tested) | 2026-07-14 | 2026-07-14 |
| ISSUE-017 | Issue | Done | Low | Redundant LLM explanation on 'command not found' (exit 127) | 2026-07-14 | 2026-07-15 |
| ISSUE-018 | Issue | Done | Low | LLM output missing trailing newline causes prompt discontinuity | 2026-07-14 | 2026-07-15 |
| ISSUE-019 | Issue | Done | Low | Enhanced shell prompt and terminal colorization | 2026-07-14 | 2026-07-15 |
| ISSUE-020 | Issue | Done | Low | Missing 'thinking' indicator during LLM queries | 2026-07-14 | 2026-07-15 |
| ISSUE-021 | Task | Done | High | Task 4.1 — Job control handoff (tcsetpgrp, Ctrl+Z termination, TUI passthrough) | 2026-07-14 | 2026-07-14 |
| ISSUE-022 | Issue | Done | Low | Configurable prompt — bash $PS1-style convention (Tier 1: \u \h \w \W + named color tokens) | 2026-07-15 | 2026-07-16 |
| ISSUE-023 | Issue | Done | Low | Confirmation gate — distinct colors per line (command / description / prompt) | 2026-07-15 | 2026-07-15 |
| ISSUE-024 | Issue | Done | Normal | TerminalSink.write_line() doesn't call ensure_newline() — mid-consult write_line (e.g. confirmation gate) runs onto an open streamed line | 2026-07-15 | 2026-07-15 |
| ISSUE-025 | Issue | Open | Low | Reuse prompt.py's color-token vocabulary for other UI surfaces (confirmation gate, thinking indicator, sink colors) — design note only, not specced | 2026-07-16 | — |
| ISSUE-026 | Issue | Open | Normal | First-run setup friction — no config.toml is ever written, empty api_key silently sends an unauthenticated request instead of a friendly setup prompt — design note only, not specced | 2026-07-16 | — |
| ISSUE-027 | Issue | Open | Low | Sasha looks like a thin bash wrapper — no boot-time hint about the ?? LLM trigger or which profile is active | 2026-07-16 | — |
