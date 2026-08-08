# Task 58.1: Split repeated-action vs tool-turn-limit stop messages (ISSUE-063)

## Authoritative contract
No separate design doc — this is a well-scoped, single-file behavioral fix
inside code that already works, matching the Task 8.1/28.1/30.1 precedent
of going straight to a task spec. The contract is this spec in full.

`src/sasha/agent.py`'s `run_consult()` per-round loop (`agent.py:177-196`)
currently detects two independent stop conditions — the repetition guard
(`current_key == previous_key`: the model called the identical action
twice in a row) and the `max_tool_turns` budget being exhausted — and
conflates them into ONE fixed message string, with an action-description
suffix appended only when repetition was the cause:

```python
if response.tool_calls:
    tool_turns += 1
    if ((current_key is not None and current_key == previous_key)
            or tool_turns >= max_tool_turns):
        message = ("[sasha] stopping: repeated identical action / "
                   "tool-turn limit reached")
        if current_key is not None and current_key == previous_key:
            name, args = current_action
            desc = f"{name}({args})"
            if len(desc) > 200:
                desc = desc[:200] + "…"
            message += f" — {desc}"
        warn(message)
        session.add_user("Answer in text now; no further commands will be run.")
        final_messages = session.build_messages(executor.cwd)
        final_response = _consult_llm(final_messages, [])
        session.add_assistant(final_response)
        return
    if current_key is not None:
        previous_key = current_key
```

Salvador's framing (2026-08-08, ISSUE-063): these are two different
signals that deserve different visibility. Repetition means the model is
plausibly stuck — surface exactly what kept repeating so the user can
judge it. Turn-limit exhaustion is a budget cutoff on a session that may
have been making genuine progress — the message should read that way
("why is this taking so many steps?"), not like a suspected bug, and
should point at the fix (`max_tool_turns` in `config.toml`) since
Salvador already hit the default-10 ceiling repeatedly in real usage
(concretely, multi-round `question`-tool dialogues).

## What already exists to bind, not reimplement
- `agent.py`'s `current_key`/`current_action`/`previous_key`/`tool_turns`
  bookkeeping — completely unchanged. `current_key` is set alongside
  `current_action` in exactly two places: the generic per-tool path
  (`agent.py:173-175`, `if executed: ...`) and the `question`-tool branch
  (`agent.py:102-103`). Both already guarantee `current_action is not
  None` whenever `current_key is not None` — this task's fix relies on
  that invariant (see Fix sketch) but does not need to touch either
  assignment site.
- `warn(message)` — the existing `on_warning`-or-`out` callable
  (`agent.py:33`). Unchanged; still called exactly once per stop, still
  passed a single string.
- The `session.add_user("Answer in text now; no further commands will be
  run.")` synthetic follow-up turn (`agent.py:190`) — the model-facing
  text, as opposed to `warn(message)`'s user-facing text. **Unchanged.**
  Salvador's request was about what the human sees, not what the model is
  told; this line's wording works identically regardless of which
  condition triggered the stop, so it stays a single fixed string.

## What's new
Nothing new — the `if repeated or exhausted:` block's message-construction
logic changes from one conflated string to two cause-specific ones. No new
functions, no new files, no signature changes anywhere.

## Fix sketch (authoritative)

Replace the block above with:

```python
if response.tool_calls:
    tool_turns += 1
    repeated = current_key is not None and current_key == previous_key
    exhausted = tool_turns >= max_tool_turns
    if repeated or exhausted:
        desc = None
        if current_action is not None:
            name, args = current_action
            desc = f"{name}({args})"
            if len(desc) > 200:
                desc = desc[:200] + "…"
        if repeated:
            message = "[sasha] stopping: repeated identical action"
            if desc is not None:
                message += f" — {desc}"
        else:
            message = (f"[sasha] stopping: hit the {max_tool_turns}-"
                       f"action limit for this turn")
            if desc is not None:
                message += f" — last action: {desc}"
            message += (" — raise max_tool_turns under [limits] "
                       "in config.toml if this looks like real progress")
        warn(message)
        session.add_user("Answer in text now; no further commands will be run.")
        final_messages = session.build_messages(executor.cwd)
        final_response = _consult_llm(final_messages, [])
        session.add_assistant(final_response)
        return
    if current_key is not None:
        previous_key = current_key
```

**Decisions pinned by this fix, all encoded as contract-test acceptance
criteria below:**
1. **Precedence when both trigger in the same round** (e.g. the exact
   round that hits `max_tool_turns` also happens to repeat the previous
   action): `repeated` is checked first, so the repetition message wins
   — it is the more specific, more actionable diagnosis of the two.
   `exhausted` still being true in that round is not surfaced at all.
2. **`desc` is computed once, shared by both branches** — the 200-char
   truncation-with-ellipsis behavior is identical in both messages, just
   attached under different prefixes (`"— {desc}"` for repetition, `"—
   last action: {desc}"` for turn-limit).
3. **The turn-limit message never omits the config hint**, even when
   `desc is None` (no tool actually executed this round — e.g. every call
   was to an unknown tool name, or every confirmation-gated call was
   declined). The hint is unconditional; only the `"— last action: ..."`
   fragment is conditional on `current_action is not None`.
4. **The repetition message never carries the config hint or the word
   "limit"** — the two messages share no vocabulary, so a human (or a
   test) can distinguish cause from the message text alone with no
   ambiguity.

Verified in a scratch worktree: applied verbatim, full suite green
(707/709, 2 deselected per ISSUE-058, no new failures), including all 6
new contract tests below and all 5 amended pre-existing files.

## Pre-existing contract tests — amended (5 files)

The old conflated string `"[sasha] stopping: repeated identical action /
tool-turn limit reached"` was pinned as a shared `STOP_LINE` constant (or
inline literal) in 5 files. Each already-committed in the same commit as
this spec, verified green against the Fix sketch above in the same scratch
worktree:

1. **`tests/contract/test_task_2_3_agent.py`** — has both a repetition
   scenario (`test_ac6_identical_command_stops_loop`) and a turn-limit
   scenario (`test_ac7_turn_limit_stops_loop`) sharing one `STOP_LINE`.
   Split into `REPEATED_STOP_LINE = "[sasha] stopping: repeated identical
   action"` (ac6, substring check, unaffected by the split since the new
   repetition message still starts with this exact prefix) and
   `LIMIT_STOP_LINE = "[sasha] stopping: hit the"` (ac7, updated to use
   the new constant).
2. **`tests/contract/test_task_3_0_agent_hardening.py`** —
   `test_ac1_unknown_tool_turns_hit_limit` is turn-limit-only (two
   `delete_everything` calls to an unregistered name, `current_action`
   never set). `STOP_LINE` renamed to `LIMIT_STOP_LINE`, same substring
   check, still passes since the new turn-limit message still starts with
   `"[sasha] stopping: hit the"`.
3. **`tests/contract/test_task_16_2_agent_registry.py`** — all three
   `STOP_LINE`-using tests are repetition-only (`write_file`/`read_file`
   repeated-call scenarios). `STOP_LINE` renamed to
   `REPEATED_STOP_LINE`, same value, same 3 substring/negative-substring
   checks.
4. **`tests/contract/test_task_57_1_question_core.py`** —
   `test_ac13_repetition_guard_fires_on_identical_question_calls` is
   repetition-only (two identical `question` calls). Same rename as #3.
5. **`tests/contract/test_task_31_1_reasoning_color_and_display_framing.py`**
   — the most detailed file, 3 exact-equality tests under its own "AC6:
   stopping message enrichment" section:
   - `test_ac6a_repeated_action_stop_includes_action_description` —
     repetition case (`list_dir` called twice on the same path). Updated
     to assert against `REPEATED_STOP_LINE + " — list_dir(...)"` (same
     structure as before, only the prefix text changed).
   - `test_ac6b_turn_limit_only_stop_leaves_message_unchanged` —
     **renamed** to
     `test_ac6b_turn_limit_only_stop_has_no_action_desc_but_has_hint`,
     since its own name asserted the exact behavior this task
     intentionally changes. Uses two `delete_everything` (unregistered
     tool) calls, so `current_action` stays `None` — the amended
     assertion now checks the message equals `"[sasha] stopping: hit the
     2-action limit for this turn"` plus the config-hint suffix, with no
     `"last action:"` fragment (this exact scenario is what pins
     Decision #3 above — hint present, action fragment absent).
   - `test_ac6c_action_description_truncated_at_200_chars` — repetition
     case (two identical long `write_file` calls). Same truncation
     assertion, prefix swapped to `REPEATED_STOP_LINE`.

## Constraints
- Modify only `src/sasha/agent.py`, and only inside the `if
  response.tool_calls:` block shown in the Fix sketch above.
- Do not touch `current_key`/`current_action`/`previous_key`/`tool_turns`
  assignment logic anywhere else in the function — only how the stop
  message is constructed from them once `repeated or exhausted` is true.
- Do not change the `session.add_user("Answer in text now; no further
  commands will be run.")` line — that's the model-facing synthetic turn,
  out of scope (see "What already exists" above).
- Do not add a `config.toml` self-install template change here — that's
  ISSUE-052's broader, already-logged scope (the `[limits]` section is
  missing from `_DEFAULT_CONFIG_TOML` entirely, not just this one field);
  this task only changes what the stop message says, not config
  discoverability.
- Do not touch `tests/contract/` at all — every file this task needs
  (5 amended, 1 new) is already committed as part of this spec.
- **Executor routing (per CLAUDE.md's standing rule): this is a surgical,
  precisely-specified edit to code that already works. Dispatch to the
  default model (deepseek-v4-pro) — do not launch under a local-model
  `SASHA_DISPATCH_MODEL` override.**

## Acceptance criteria
(encoded 1:1 in `tests/contract/test_task_58_1_split_stop_messages.py`,
plus the 5 amended files above re-asserting the split doesn't regress
their own original scenarios)
1. A repetition-only stop never contains turn-limit vocabulary
   (`"action limit"`, `"max_tool_turns"`) anywhere in the output —
   `test_ac1_repeated_stop_message_has_no_limit_vocabulary`.
2. A turn-limit-only stop with a real executed last action shows the
   exact string `"[sasha] stopping: hit the {N}-action limit for this
   turn — last action: {name}({args}) — raise max_tool_turns under
   [limits] in config.toml if this looks like real progress"`, and never
   contains `REPEATED_STOP_LINE` —
   `test_ac2_limit_stop_with_real_action_shows_last_action_and_hint`.
3. A turn-limit-only stop with no executed action (every call this round
   was to an unregistered tool name) omits the `"last action:"` fragment
   entirely but still carries the config hint —
   `test_ac3_limit_stop_with_no_action_omits_last_action_fragment`.
4. When both conditions are true in the same round (the model repeats its
   previous action on exactly the round that also exhausts the turn
   budget), the repeated-action message wins: no `"action limit"` text,
   no config hint anywhere in the output —
   `test_ac4_repeated_takes_precedence_when_both_trigger`.
5. The 200-char-plus-ellipsis truncation cap applies identically to the
   turn-limit message's `"last action:"` fragment as it already does to
   the repetition message's fragment —
   `test_ac5_limit_stop_truncates_long_action_description`.
6. A `question`-tool call as the last action before a turn-limit stop is
   reported the same way as any other tool (`"last action:
   question(...)"`, hint present) — regression guard proving the split
   doesn't special-case any one tool —
   `test_ac6_limit_stop_reports_question_tool_as_last_action`.

## Standing instructions
- Your goal: make the contract tests in
  `tests/contract/test_task_58_1_split_stop_messages.py` pass, and keep
  the full suite green: `bash scripts/run-tests.sh`.
- NEVER modify files under `tests/contract/` — the gate rejects the
  commit if you do. All 6 files this task touches (1 new, 5 amended) are
  already committed as part of this spec.
- Deliver exactly ONE new commit on top of the HEAD you started from;
  never amend, rebase, or reset; leave the working tree clean.
- Commit message per `AGENTS.md`, with trailers: `Done: Task 58.1 — <one
  line>`, `Fixed: ISSUE-063`, `Next: Task 58.2 — Phase 58 review (Tier 1,
  with Salvador)`, and the `Co-Authored-By:` signature line from your
  dispatch prompt.
