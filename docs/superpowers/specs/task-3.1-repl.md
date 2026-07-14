# Task 3.1: REPL dispatch

## Authoritative contract
`docs/superpowers/specs/2026-07-14-sasha-design.md` §9.5 (dispatch tree), §6
(trigger/offer flow), §7.4 (confirmation prompt) and §10 (error vocabulary)
are the complete contract.

## What already exists (bind, never reimplement)
- `sasha.triggers.decide` / `Decision` / `command_word` — THE trigger logic.
- `sasha.agent.run_consult` / `RUN_COMMAND_TOOL` — THE consult loop.
- `sasha.llm.ChatClient` / `ChatError` — the client (created via factory).
- `sasha.session.Session`, `sasha.mutes.MuteStore`, `sasha.executor.Executor`,
  `sasha.config.Config/Profile/Limits`.
- `tests/contract/sse_stub.py`, `tests/contract/test_task_3_1_repl.py`.

## What's new
- `src/sasha/repl.py`:
  - `Repl(config, executor, mutes, session, client_factory, confirmer,
    input_fn, out)` — positional or keyword, this order.
  - `Repl.run_once(line: str) -> bool` — process one input line; `False`
    means "exit requested", `True` otherwise.
  - `Repl.loop() -> None` — `input_fn(prompt)` / `run_once` until `False` or
    `EOFError`.
  - `TerminalConfirmer(input_fn, out)` — the real §7.4 confirmation gate.

## Pinned semantics (decisions already made — implement exactly these)
1. **Dependencies are duck-typed** and used only via: `executor.run(line,
   capture_stdout=...)`, `executor.check_syntax(line)`, `executor.cwd`;
   `mutes.is_muted/record_ignored/record_acted/mute/unmute`;
   `session.add_user/note_command/...` (as needed by §9.5);
   `client_factory(profile) -> ChatClient`-like, called at most once per
   consult with the CURRENT profile.
2. **State**: current profile starts as
   `config.profiles[config.default_profile]`; a pending offer is
   `(line, exit_code)` of the failure that produced it.
3. **`run_once` dispatch order** (§9.5):
   a. empty line + offer pending → `mutes.record_acted(command_word(line),
      exit)` for the offered failure, `session.add_user(<auto-prompt>)`,
      consult; clear the offer;
   b. empty line, no offer → do nothing, return True;
   c. `?? <text>` → `session.add_user(text)`, consult (executor untouched;
      any pending offer is cleared first WITHOUT recording an ignore — the
      user is engaging, just about something else);
   d. `sasha <builtin> ...` (see pin 6) → handle in-process, return True
      (a pending offer is ignored + recorded first, as in (e));
   e. otherwise: if an offer is pending → `mutes.record_ignored(cmd, exit)`
      first (printing the §10 mute notice if it returns True), clear it;
      then `exit`/`quit` → return False; then syntax pre-check:
      `executor.check_syntax(line)` non-None → `session.add_user(<T2
      auto-prompt>)`, consult, return True (NEVER executed); else
      `result = executor.run(line)`; `session.note_command(line, result)`;
      `triggers.decide(line, result, mutes)`:
      - AUTO_CONSULT → `session.add_user(<T1 auto-prompt>)`, consult
      - OFFER → `out(<§10 offer line>)`, set pending offer
      - NOTHING → return True.
4. **Auto-prompt texts** (user messages injected before consulting):
   - T1 (exit 127): `The line `{line}` failed with exit 127 (command not
     found, or not a command at all). If it was meant as a question or
     calculation, answer it; if it looks like a typo, say so and give the
     corrected command.`
   - T2 (syntax): `The line `{line}` is not valid bash: {error}. Explain
     and give a corrected line.`
   - Offer accepted: `The command `{line}` failed with exit {N}. Explain the
     failure using the captured stderr and suggest a fix.`
5. **Consult** = build client via `client_factory(current_profile)`, then
   `agent.run_consult(session, client, executor, confirmer, out,
   max_tool_turns=config.limits.max_tool_turns)`. `ChatError` is caught HERE:
   `out` receives the §10 line
   `[sasha] {profile.name} unreachable: {reason} — 'sasha profile <name>' to
   switch` (literal `<name>`), and `run_once` returns True.
6. **Builtins** (exact vocabulary, all output via `out`):
   - `sasha mute <cmd>` → `mutes.mute(cmd, None)`;
     `sasha mute <cmd> <N>` → `mutes.mute(cmd, N)`
   - `sasha unmute <cmd> [<N>]` → same shape, `mutes.unmute`
   - `sasha profile` → `out("[sasha] profile: {name} ({model} @ {base_url})")`
     for the current profile
   - `sasha profile <name>` → switch if `name in config.profiles` (then print
     the same line for the new profile); unknown name →
     `out("[sasha] unknown profile: {name}")`
   - `sasha help` → any single `out(...)` line mentioning `mute`, `profile`
     and `??`
   - unknown builtin → `out("[sasha] unknown command: {rest}")`
7. **§10 lines, byte-exact**:
   - offer: `[sasha] failed (exit {N}) — press Enter to ask`
   - mute crossing: `[sasha] muting offers for {cmd} exit {N} — 'sasha
     unmute {cmd}' to restore`
8. **`TerminalConfirmer.ask(command, description)`**: renders via ONE
   `out()` call, exactly (missing/empty description →
   `(no description provided)`):
   ```
   [sasha] wants to run:  {command}
           └ {description}
           [y]es / [n]o / [e]dit — or type a reply
   ```
   then reads `input_fn("> ")`; mapping: `y`/`yes` → `('yes','')`;
   `n`/`no`/empty → `('no','')`; `e`/`edit` → a second `input_fn("edit> ")`
   supplies the command: return `('edit', <that>)`; anything else →
   `('reply', <the text>)`. (Readline prefill is a UX nicety for Task 3.2;
   not part of this contract.)
9. **`loop()`**: prompt string is `executor.cwd` with `$HOME` abbreviated to
   `~`, then ` ✦ ` (e.g. `~/Sasha ✦ `); `EOFError` from `input_fn` ends the
   loop; `KeyboardInterrupt` prints nothing, continues.

## Constraints
- `src/sasha/repl.py` imports stdlib + sasha modules only; BIND the existing
  modules — reimplementing trigger, mute, or consult logic in repl.py is a
  review-failing violation. Only `src/sasha/repl.py` is created (plus
  optional tests under `tests/impl/`).
- Do not touch: `scripts/`, `docs/superpowers/`, `CLAUDE.md`, `AGENTS.md`,
  `PLAN.md`, `ISSUE-TRACKING.md`, `README.md`, `.gitignore`,
  `pyproject.toml`, any other `src/sasha/*.py`, `tests/contract/`.

## Acceptance criteria
(encoded 1:1 in `tests/contract/test_task_3_1_repl.py`)
1. `??` consults without executing — `test_ac1_explicit_ask`.
2. Empty line + pending offer consults + `record_acted`; empty line otherwise
   is a no-op — `test_ac2_*`.
3. Non-empty input with pending offer → `record_ignored`, then normal
   processing — `test_ac3_ignored_offer_then_processes`.
4. Syntax-failing line never executes, consults with the error — `test_ac4_*`.
5. Exit 127 auto-consults; exit 0 silent — `test_ac5_*`.
6. Offer line and mute notice byte-exact — `test_ac6_*`.
7. Builtins behave per pin 6; `exit`/`quit` return False — `test_ac7_*`.
8. `ChatError` prints the §10 unreachable line; REPL keeps going —
   `test_ac8_chaterror_keeps_repl_alive`.
9. `TerminalConfirmer` renders §7.4 byte-exact and maps inputs per pin 8 —
   `test_ac9_*`.

## Standing instructions
- Your goal: make `tests/contract/test_task_3_1_repl.py` pass
  (`PYTHONPATH=src python3 -m pytest tests/contract/test_task_3_1_repl.py`).
- NEVER modify files under `tests/contract/` — the gate rejects the commit
  if you do. If a contract test seems wrong or unimplementable, stop and
  flag it; do not commit.
- Deliver exactly ONE new commit on top of the HEAD you started from; never
  amend, rebase, or reset; leave the working tree clean.
- Commit message per AGENTS.md, with trailers:
  `Done: Task 3.1 — <one line>`,
  `Next: Task 3.2 — Entry point & terminal wiring`, and the Co-Authored-By
  signature line from your dispatch prompt.
