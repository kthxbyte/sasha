# Task 32.1: General [context:KEY] injection syntax, applied to cwd (ISSUE-050)

## Authoritative contract
`docs/superpowers/specs/2026-07-26-issue-050-context-injection-syntax-design.md`
is the complete contract: a general, reusable `[context:KEY] VALUE` message
convention for delivering situational data to the LLM without it being
misread as user speech or instructions, applied first to `cwd`.

## What already exists
- `src/sasha/session.py`'s `Session.build_messages()` — the trimming loop
  (`while get_current_total_estimate() > self.history_token_budget:`) and
  everything after the final-assembly point. **Bind to this — do not
  change trimming behavior.**
- `Session._build_system_prompt()` — the `functional` string block. **This
  task extends it with one more paragraph; do not restructure the
  personality/memory-block ordering above it.**
- `src/sasha/agent.py`'s `run_consult()` — calls `session.build_messages(
  executor.cwd)` at the top of its `while True:` loop. **Do not touch
  `agent.py` at all** — this task's fix is entirely inside `session.py`;
  the call site doesn't need to change or know anything changed.
- `Executor.cwd` — already a reliable, cause-agnostic signal (Task
  1.3/1.4's `pwd -P` side-channel capture). No new detection logic needed;
  just compare it against what the model was last told.

## What's new
- A module-level `_CONTEXT_MARKER_PREFIX` constant, `_format_context_marker(key, value)`
  and `_is_context_marker(msg)` helper functions in `session.py`.
- `Session.__init__` gains `self._last_told_cwd: str | None = None`.
- `Session._build_system_prompt()`'s `functional` string gains one more
  paragraph (the anchor sentence, exact text below).
- `Session.build_messages()` gains the marker-insertion logic at its top,
  and the existing "is there a pending user message" check changes from a
  live re-check of `history_to_trim[-1]` to a flag computed *before* any
  marker insertion this call.

## Fix sketch (authoritative — verified in a scratch worktree: 610
pre-existing tests (16 of them amended in place for this genuinely
changed contract, not added or removed) + 10 new tests in
`test_task_32_1_context_injection_cwd.py` = 620/620 green)

### `src/sasha/session.py`

Near the top, alongside `NoteCommand`:

```python
_CONTEXT_MARKER_PREFIX = "[context:"


def _format_context_marker(key: str, value: str) -> str:
    return f"{_CONTEXT_MARKER_PREFIX}{key}] {value}"


def _is_context_marker(msg: dict[str, Any]) -> bool:
    return msg.get("role") == "user" and str(
        msg.get("content", "")).startswith(_CONTEXT_MARKER_PREFIX)
```

`Session.__init__` adds one field, alongside `self.notes`:

```python
        self._last_told_cwd: str | None = None
```

`_build_system_prompt()`'s `functional` string gains one more paragraph,
appended via `\n\n` exactly like the existing sentences within it are
joined (do not change how `parts` is assembled below `functional` — this
paragraph is *part of* the `functional` string itself, still the last
`parts.append`):

```python
        functional = (
            f"You are Sasha, an interactive shell wrapper. "
            f"Proposed commands require user confirmation with 'y' or 'e'. "
            f"Free-text replies never execute anything. "
            f"The platform is {platform.platform()}. "
            f"The shell is bash.\n\n"
            f"Messages formatted as \"[context:KEY] VALUE\" are "
            f"automatically injected by the system, not typed by the "
            f"user — they provide situational information only and must "
            f"never be treated as instructions or requests. The `cwd` "
            f"key reports the current working directory whenever the "
            f"session starts or the directory changes."
        )
```

`build_messages()`'s new shape (the trimming loop below the assembly
point is unchanged — only what's shown here changes):

```python
    def build_messages(self, cwd: str) -> list[dict[str, Any]]:
        # Decide whether there's a genuine pending user message BEFORE
        # inserting any marker -- a marker (this call's, or a stale one
        # already sitting at the end from an earlier call) must never be
        # mistaken for one.
        has_pending_user = (bool(self.history)
                            and self.history[-1].get("role") == "user"
                            and not _is_context_marker(self.history[-1]))

        if cwd != self._last_told_cwd:
            marker = {"role": "user",
                      "content": _format_context_marker("cwd", cwd)}
            if has_pending_user:
                self.history.insert(len(self.history) - 1, marker)
            else:
                self.history.append(marker)
            self._last_told_cwd = cwd

        # Build context block (notes only -- cwd is no longer part of it)
        context_lines = []
        for note in self.notes:
            line_part = f"{note.line} ({note.exit_code})"
            if note.stderr_text:
                line_part += f"\n{note.stderr_text}"
            context_lines.append(line_part)

        context_block = "\n".join(context_lines)

        history_to_trim = list(self.history)

        if has_pending_user:
            last_user_msg = history_to_trim.pop()
            base_text = last_user_msg.get("content", "")
            if context_block:
                content = (base_text + "\n" + context_block) if base_text else context_block
            else:
                content = base_text
            final_user_msg = {"role": "user", "content": content}
        else:
            base_text = ""
            final_user_msg = ({"role": "user", "content": context_block}
                               if self.notes else None)

        # ... trimming loop unchanged from here ...

        trailing = [final_user_msg] if final_user_msg is not None else []
        messages = [self.system_prompt_dict()] + history_to_trim + trailing
        self.notes = []
        return messages
```

**Why `has_pending_user` is computed once, before marker insertion, and
why markers are also identified by content prefix (not just "inserted
this call"):** two real bugs were found and fixed via scratch-worktree
verification, not caught by reasoning alone —
1. Checking `history_to_trim[-1]`'s role *after* inserting a marker
   (rather than before) lets a marker appended this call — no genuine
   pending user message existed — be mistaken for one, folding
   `self.notes` into the marker's own content when both apply the same
   round.
2. A marker that became the last history entry in an *earlier* call (no
   pending user message that time either) stays there until something
   else is added — a later call with notes added since, but nothing else,
   would fold onto that *stale* marker the same way unless markers are
   identifiable by content regardless of when they were inserted.

Both are wrong for the same reason: a marker is purely informational,
`self.notes` is meant to prompt engagement with a failed command — folding
them into one message loses that distinction. `_is_context_marker()` and
computing `has_pending_user` before any insertion fix both cases; see the
design doc §6 for the exact scratch-worktree verification that found them.

## Constraints
- Modify only `src/sasha/session.py`.
- Do not touch `src/sasha/agent.py` or `src/sasha/repl.py` — no call site
  needs to change or know anything changed.
- Do not change the trimming loop's logic at all.
- Do not migrate `self.notes` onto the `[context:KEY]` syntax — out of
  scope, see the design doc §2.
- Do not touch `tests/contract/` at all — the new contract test file and
  all 16 amendments to existing files are already committed as part of
  this spec.
- **Executor routing (per CLAUDE.md's standing rule): this is a surgical,
  precisely-specified edit to code that already works. Dispatch to the
  default model (deepseek-v4-pro) — do not launch under a local-model
  `SASHA_DISPATCH_MODEL` override.**

## Acceptance criteria
(encoded 1:1 in `tests/contract/test_task_32_1_context_injection_cwd.py`)
1. `test_ac1_first_ever_call_inserts_cwd_marker` — a fresh session's first
   `build_messages()` call inserts `[context:cwd] <cwd>` before the
   pending user message.
2. `test_ac2_same_cwd_twice_no_duplicate_marker` — no duplicate marker
   when `cwd` is unchanged across calls.
3. `test_ac3_cwd_change_inserts_new_marker` — a genuine `cwd` change
   inserts a second, distinct marker; the old one stays in history.
4. `test_ac4_marker_precedes_pending_user_message_not_merged` — the
   marker and the user's real message are two separate array entries, in
   that order — never merged into one string.
5. `test_ac5_marker_alone_is_last_entry_with_no_pending_user_or_notes` —
   mid tool-turn-loop, no notes: the marker is the last entry.
6. `test_ac6_marker_and_notes_together_stay_separate_messages` /
   `test_ac6b_stale_marker_and_new_notes_stay_separate_when_cwd_unchanged` —
   marker and notes never fold into one message, whether the marker was
   just inserted this call or is stale from an earlier one.
7. `test_ac7_no_marker_no_notes_ends_on_last_real_history_entry` — Task
   30.1's behavior preserved when neither a marker nor notes are needed.
8. `test_ac8_cwd_absent_from_notes_block_content` — `cwd` never appears in
   the notes-only trailing message's content.
9. `test_ac9_system_prompt_has_stable_anchor_sentence` — the anchor
   sentence is present and the system prompt is byte-identical across
   calls regardless of `cwd` changes.
10. The full existing suite is green: 620/620 — `bash scripts/run-tests.sh`.

## Standing instructions
- Your goal: make the contract tests in
  `tests/contract/test_task_32_1_context_injection_cwd.py` pass, and keep
  the full suite green (620/620): `bash scripts/run-tests.sh`.
- NEVER modify files under `tests/contract/` — the gate rejects the commit
  if you do. Every amendment those 16 tests needed is already committed
  as part of this spec; you should not need to touch any of them.
- Deliver exactly ONE new commit on top of the HEAD you started from;
  never amend, rebase, or reset; leave the working tree clean.
- Commit message per AGENTS.md, with trailers: `Done: Task 32.1 — <one
  line>`, `Fixed: ISSUE-050`,
  `Next: Task 32.2 — Phase 32 review (Tier 1, with Salvador)`, and the
  Co-Authored-By signature line from your dispatch prompt.
