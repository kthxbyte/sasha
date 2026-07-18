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
| ISSUE-026 | Issue | Done | Normal | First-run setup friction — no config.toml is ever written, empty api_key silently sends an unauthenticated request instead of a friendly setup prompt | 2026-07-16 | 2026-07-17 |
| ISSUE-027 | Issue | Done | Low | Sasha looks like a thin bash wrapper — no boot-time hint about the ?? LLM trigger or which profile is active | 2026-07-16 | 2026-07-16 |
| ISSUE-028 | Issue | Done | Normal | No TAB completion — bash-style command/argument completion via the real bash-completion ecosystem | 2026-07-16 | 2026-07-16 |
| ISSUE-029 | Issue | Done | High | Path completion broken after cd and for any path containing '/' — completer ignores Executor.cwd, readline's default delims split words on '/' | 2026-07-16 | 2026-07-16 |
| ISSUE-030 | Issue | Done | Normal | Personality file — load ~/.config/sasha/PERSONALITY.md into the LLM system prompt, self-installing a neutral default when absent | 2026-07-16 | 2026-07-16 |
| ISSUE-031 | Issue | Done | High | Stale command notes/stderr silently re-surface into every subsequent LLM turn — Session.notes/last_stderr are never consumed after being shown once | 2026-07-16 | 2026-07-16 |
| ISSUE-032 | Issue | Open | Normal | Possible second, distinct repetition mechanism in agent.py's tool-calling loop (file writes etc.) — not covered by ISSUE-031's fix; needs a reproducible transcript — design note only, not specced | 2026-07-16 | — |
| ISSUE-033 | Issue | Done | Normal | Multi-tool registry & per-tool permissions (v2) — replace hardcoded run_command with a tool registry, add write_file/read_file/list_dir/search_files, generalize the repetition guard to (tool_name, key) as a plausible mitigation for ISSUE-032 | 2026-07-17 | 2026-07-17 |
| ISSUE-034 | Issue | Done | Normal | Project memory (v2) — git-repo-scoped long-term memory (semantic.jsonl + episodic/ logs under .sasha/memory/), automatic encoding on git commit, deliberate encoding via a new remember() tool, place-shift detection on cwd change | 2026-07-17 | 2026-07-17 |
| ISSUE-035 | Issue | Open | Low | remember() can't explicitly target general (cross-project) memory while inside a git repo — always writes to whichever place is current. Deferred by design (Salvador's call, during general-memory brainstorm) — revisit whether the LLM can infer project-vs-general scope from a note's own content, rather than adding an explicit scope parameter or asking the user — design note only, not specced | 2026-07-17 | — |
