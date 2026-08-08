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
- [x] Task 19.2 — prompt.py busy-star token (\S, blink) + Repl background-task counter
- [x] Task 19.3 — repl.py: stage 1/2 bootstrap wiring into _check_place_shift

## Phase 20 — Pager keystroke fix (ISSUE-040)

- [x] Task 20.1 — executor.py: ignore SIGTTIN symmetrically with SIGTTOU + stop-cause classification

## Phase 21 — Safe command auto-approval (ISSUE-041)

- [x] Task 21.1 — safe_commands.py classifier + tools/agent/config/executor wiring for run_command auto-approval

## Phase 22 — Tool-call logging (ISSUE-042)

- [x] Task 22.1 — tool_log.py core write path + safe_commands.touches_sensitive_path extraction + agent.py hook
- [x] Task 22.2 — compaction (background thread), [tool_log] config, sasha compact-log command

## Phase 23 — Bare-directory denylist matching fix (ISSUE-043)

- [x] Task 23.1 — safe_commands.py: match dir/** patterns against the bare directory too

## Phase 24 — Foreground-group keystroke relay helper (ISSUE-040)

- [x] Task 24.1 — _kb_relay_helper.py + executor.py fd-passing/wrapped-script wiring

## Phase 25 — Raw-typed command activity awareness (ISSUE-044)

- [x] Task 25.1 — repl.py tool_log hook + Session recency-window enrichment

## Phase 26 — Cursor-based activity indicator (ISSUE-045)

- [x] Task 26.1 — activity_indicator.py + agent.py on_phase hook + repl.py/config.py wiring
- [x] Task 26.2 — remove the Phase 19 busy-star mechanism

## Phase 27 — Scope activity indicator away from raw commands (ISSUE-048)

- [x] Task 27.1 — remove ActivityIndicator wrap around the raw-typed command path

## Phase 28 — Bold TerminalSink colors (ISSUE-046)

- [x] Task 28.1 — bold _SASHA_COLOR/_LLM_COLOR/_PROMPT_COLOR/_COMMAND_COLOR, leave _DIM alone

## Phase 29 — LLM request debug log (ISSUE-047 investigation tooling)

- [x] Task 29.1 — llm_debug.py + config + llm.py/__main__.py wiring

## Phase 30 — Drop synthetic continuation-turn cwd message (ISSUE-047 fix)

- [x] Task 30.1 — Session.build_messages() no longer synthesizes a bare cwd-only user turn

## Phase 31 — Reasoning-stage coloring + tool-turn display framing (ISSUE-049)

- [x] Task 31.1 — on_reasoning/on_status/on_warning plumbing, TerminalSink chunk-run-kind + reasoning color, round-separator newlines, enriched stopping message

## Phase 32 — General context-injection syntax, applied to cwd (ISSUE-050)

- [x] Task 32.1 — [context:KEY] VALUE convention + system-prompt anchor; cwd delivered as a change-tracked history message, not glued onto the user message

## Phase 51 — Directory recap on explicit cd (ISSUE-051)

- [x] Task 51.1 — directory_recap.py cascade module (filesystem shape, cheap log rules, async LLM escalation) + repl.py gap-filler/dedup trigger wiring + config enabled flag
- [x] Task 51.2 — sasha recap (explicit, sync, always-LLM) + sasha directory-recap on|off builtin + config.py enabled-toggle patcher

## Phase 54 — Multi-line paste burst-join (ISSUE-054)

- [x] Task 54.1 — paste_join.py module + __main__.py wiring (unify Repl main prompt + TerminalConfirmer behind one paste-aware input function)

## Phase 56 — Directory-recap cold-start fallback (ISSUE-056)

- [x] Task 56.1 — marker-file content-sniff: gather_context()/build_prompt() README+listing fallback, decide()'s new escalation branch, vendor/cache exclusion

## Phase 57 — `question` tool (ISSUE-059)

- [x] Task 57.1 — question.py module (schema, Questioner protocol, validate_shape, format_result) + tools.py REGISTRY entry + agent.py dedicated dispatch branch + questioner param + session.py system-prompt guidance
- [x] Task 57.2 — TerminalQuestioner (repl.py) + __main__.py wiring (spec written once Task 57.1 ships, matching Phase 51's task-at-a-time precedent)

## Phase 58 — Split repeated-action vs tool-turn-limit stop messages (ISSUE-063)

- [x] Task 58.1 — agent.py: split the conflated stop message into two cause-specific messages, turn-limit variant shows last action + config hint

## Phase 59 — Sasha skill loading, stage 1 load-only (ISSUE-064)

- [x] Task 59.1 — skills.py module (frontmatter parsing, discovery, listing, load_skill) + tools.py REGISTRY entry + agent.py unknown-tool logging + session.py skills_listing param + __main__.py wiring
