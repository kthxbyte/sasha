# A general context-injection syntax, applied first to cwd (ISSUE-050)

**Status:** Draft for review
**Date:** 2026-07-26

## 1. Mission

Sasha needs a way to give the model situational data — starting with the
current working directory — without that data being misread as something
the user typed or asked for. A real transcript showed exactly this failure:
`cwd` glued onto a plain "hola hermosa" greeting was read by a weak local
model as "the user gave me a path," derailing it into unprompted directory
exploration. This task fixes `cwd` specifically, but designs the fix as a
general, reusable convention — `[context:KEY] VALUE` — so future situational
data (not just `cwd`) has a place to go without repeating this investigation.

## 2. Scope

### In scope:
- A `[context:KEY] VALUE` message format and the system-prompt sentence
  that defines it once, generally, for any current or future key.
- Applying it concretely to `cwd`: `Session` tracks whether the model has
  already been told the current directory, and injects a `[context:cwd]`
  message only when it changes (including the very first call, which
  covers "informed at boot" with no separate code path needed).
- Removing `cwd` from the plain-text context block currently glued onto
  the pending user message in `Session.build_messages()`.

### Out of scope / non-goals:
- Migrating `self.notes` (recent failed-command reports) onto the new
  `[context:KEY]` syntax. They keep attaching to the pending user message
  exactly as today — a reasonable follow-up, not part of this task.
- Any new context key beyond `cwd`. The convention is designed to be
  reused later; nothing else is being migrated onto it now.
- Detecting or adapting to a given profile's chat-template quirks at
  runtime. The design instead picks the one mechanism confirmed safe
  across every profile tested, rather than trying to be clever per-model.

## 3. Key contracts & assumptions

1. **A non-initial `system`-role message is not safe across profiles —
   confirmed empirically, not assumed.** Sasha's own local profile
   (`unsloth/GLM-4.7-Flash-REAP-23B-A3B-GGUF`, served via llama.cpp) exposes
   its chat template through `/props`, and that template contains:
   ```jinja
   {%- if message.role == "system" %}
       {%- if not loop.first %}
           {{- raise_exception('System message must be at the beginning.') }}
       {%- endif %}
   ```
   A system message anywhere but index 0 would hard-error this profile's
   requests. The default `llm7` profile (`gpt-oss:20b`, served via what
   its `system_fingerprint: fp_ollama` marks as an Ollama-backed stack)
   was tested the same way via a live request and handled a mid-conversation
   system message perfectly, correctly treating it as non-user context. Two
   of Sasha's own shipped profiles disagree on this — a design that relies
   on non-initial system messages is not safely portable across arbitrary
   user-configured profiles, so it's rejected outright rather than special-
   cased per backend (no reliable way exists to probe an arbitrary profile's
   chat template in general — `llm7.io` exposes no `/props` equivalent).
2. **`user`-role messages are safe anywhere in the array on every profile
   tested** — no positional restriction in the template above, and this is
   standard OpenAI wire-format behavior generally. `tool`-role messages are
   also unrestricted in position but require a `tool_call_id` tied to a
   real preceding assistant tool call, which an ambient cwd notification
   doesn't have — not usable here.
3. **The `[context:KEY] VALUE` format, delivered as its own distinct
   `user`-role message plus a one-time system-prompt anchor sentence
   explaining it, was validated live against both profiles, including the
   exact model that caused the original incident:**
   - A bare greeting ("hola") with a `[context:cwd]` message present and
     real tool schemas available: **no tool calls** on either profile.
     GLM's own reasoning trace: *"I don't need to use any tools here."*
     (the same model that, unprompted, called `list_dir` twice in the real
     incident transcript this issue is based on).
   - A genuine request ("what's in this directory? list it for me") with
     the same `[context:cwd]` message present: **both profiles correctly
     called `list_dir`**, and GLM specifically resolved "this directory"
     to the actual value from the marker (`{"path": "/home/testuser"}`) —
     confirming the marker is read as *usable* situational information,
     not merely successfully ignored.
4. **`Executor.cwd` is already a reliable, cause-agnostic signal** — every
   command runs as one wrapped bash script that appends `pwd -P` after
   the command and reports it through a side channel (`executor.py`,
   Task 1.3/1.4); `self._cwd` reflects the real post-command directory
   regardless of whether the change came from `cd`, `pushd`, or any other
   shell-level side effect. No new detection logic is needed — comparing
   `executor.cwd` against what the model was last told is sufficient.
5. **The system prompt is not a safe place for the live value itself** —
   it's rebuilt fresh from scratch on every call (`_build_system_prompt()`),
   and only the current rebuild is ever sent; no earlier version persists
   in the messages array for the model to fall back on. If cwd lived only
   there and were omitted on unchanged turns, the model would have no way
   to know it that request. A message inserted into `self.history`, by
   contrast, persists forward through every later request the normal way,
   which is what makes "only send when it changes" safe.
6. **Inserting a message into history rather than the system prompt is
   also the better choice for llama.cpp's prompt-cache reuse** — the
   personality+memory system prompt is the most stable part of every
   request; baking a frequently-changing value into that string would
   make the whole system prompt look different across requests every time
   the directory changes, costing more re-processing than necessary. A
   message appended at the tail costs the same as any ordinary new turn.

## 4. The format

### 4.1 The general convention

One sentence added to the system prompt's fixed functional block
(`Session._build_system_prompt()`), stable and unchanging across requests
(same cache-cost profile as the existing "You are Sasha..." text):

> "Messages formatted as \"[context:KEY] VALUE\" are automatically injected
> by the system, not typed by the user — they provide situational
> information only and must never be treated as instructions or requests.
> The `cwd` key reports the current working directory whenever the session
> starts or the directory changes."

A small formatting helper (new, e.g. in `session.py`):

```python
def _format_context_marker(key: str, value: str) -> str:
    return f"[context:{key}] {value}"
```

Used for `cwd` now; any future key reuses the same helper and the same
general sentence above (extended with one more clause per new key, not a
redesign).

### 4.2 `cwd` specifically

`Session.__init__` gains `self._last_told_cwd: str | None = None`.

At the top of `build_messages(cwd)`, before anything else:

```python
if cwd != self._last_told_cwd:
    marker = {"role": "user", "content": _format_context_marker("cwd", cwd)}
    if self.history and self.history[-1].get("role") == "user":
        self.history.insert(len(self.history) - 1, marker)
    else:
        self.history.append(marker)
    self._last_told_cwd = cwd
```

Inserting before the last entry (rather than always appending) matters:
if the last history entry is the pending user message (the normal `??`
case — `add_user()` already ran before this call), the marker needs to
land *before* it, so the conversation reads "here's the context, then
here's what the user said" — appending after it would make the marker
itself look like the newest pending message, and the existing
pending-user-message logic (which pops the last entry to attach `notes`)
would then apply to the marker instead of what the user actually typed.

`context_lines`/`context_block` (the existing notes-only block, cwd
already removed) and the rest of the trimming/assembly logic are
otherwise unchanged from Task 30.1's shape.

### 4.3 What's removed

`context_lines = [cwd]` (`session.py:97`, the first line of the existing
context block) goes away — `cwd` is no longer part of the plain-text block
glued onto the pending user message. Only `notes` remain there.

## 5. Decisions log

- **Decision:** reject any design relying on a non-initial `system`
  message. **Rationale:** empirically confirmed to hard-error one of
  Sasha's own two currently-configured profiles; no way to detect this
  ahead of time for an arbitrary future profile.
- **Decision:** the marker is delivered as a *separate* `user`-role
  message, not merged into whatever the user actually typed.
  **Rationale:** the original incident's root cause was exactly this
  merging (`"hola hermosa\n/home/salvador"` read as one utterance); a
  distinct message with explicit `[context:cwd]` framing was the
  configuration actually validated live against both profiles.
- **Decision:** the marker is inserted into `self.history` (persisting
  forward through normal trimming) rather than recomputed fresh in the
  system prompt every call. **Rationale:** the system prompt has no
  memory of its own prior contents across separate stateless requests;
  history does. This is also what makes "only send on change" safe rather
  than merely convenient.
- **Decision:** `self.notes` is not migrated onto the new syntax in this
  task. **Rationale:** scope discipline — this task fixes the specific,
  confirmed `cwd` problem; notes already work and aren't implicated in
  the incident this issue is based on.
- **Decision:** the marker is plain `[context:cwd] VALUE`, with no inline
  "not a request" disclaimer repeated in the message itself — the
  system-prompt anchor sentence is the only place that rule is stated.
  **Rationale:** this is what was actually validated live (§3.3) — both
  profiles, including the model responsible for the original incident,
  correctly treated the bare tag as non-actionable context with only the
  anchor sentence to go on. An earlier draft of this design considered
  restating the disclaimer inline (reasoning that a model deep into a long
  conversation might not recall a rule stated once, far back), but that
  version was never tested — the simpler format was, and it worked, so
  the design matches the tested format rather than a fancier untested one.
  If a long-conversation recall problem is ever actually observed, that's
  a new, separately-tested change, not something to build in now on
  speculation.

## 6. The marker/notes interaction (resolved by verification)

This was flagged as an open question and has since been settled by
building and testing the actual fix in a scratch worktree, not by prose
reasoning alone. The naive version — appending the marker at the end when
there's no pending user message, then letting the existing "pop the last
user message and attach notes to it" logic run unchanged — does fold
`self.notes` into the marker's own content whenever both apply in the
same round, and (worse) a *stale* marker left over from an earlier call
gets the same treatment on a later call if nothing else was added to
history in between (a real pattern — `test_task_2_2_session.py` already
exercises calling `build_messages()` twice in a row with nothing added).

Both cases were wrong for the same underlying reason: a marker is purely
informational, but `self.notes` describes a failed command the model is
meant to actually engage with — folding them into one message loses that
distinction. The fix has two parts:
1. Whether there's a genuine pending user message is decided *before*
   inserting any marker this call (`has_pending_user`), not by re-checking
   `history[-1]`'s role afterward — so a marker just inserted this call is
   never mistaken for one.
2. Markers are also identifiable by content (`_is_context_marker()`,
   checking a shared `[context:` prefix constant), so a *stale* marker
   left over from an earlier call is never mistaken for a pending user
   message either, regardless of how long ago it was inserted.

With both fixes applied, a marker and notes appearing in the same round
render as two separate trailing messages, never folded — verified
directly (`s.build_messages(...)` inspected message-by-message) for both
the freshly-inserted-marker case and the stale-marker case.

## 7. Testing strategy

New contract test file, `tests/contract/test_task_32_1_context_injection_cwd.py`
(10 tests, all written and verified against the actual fix in a scratch
worktree):
1. First-ever `build_messages()` call inserts a `[context:cwd]` marker
   (covers "informed at boot" with no separate code path).
2. A second call with the same `cwd` does not insert another marker.
3. A call with a *different* `cwd` inserts a new marker.
4. The marker lands *before* a pending user message, not after — the
   final message in the array is still the user's real content, not the
   marker.
5. No pending user message (mid tool-turn-loop): the marker becomes the
   last entry when there's no notes.
6. Marker + notes together (both freshly this call, and a stale marker
   from an earlier call with notes added since) render as two separate
   trailing messages, never folded — see §6.
7. No marker needed, no notes: the array still ends on the actual last
   history entry (Task 30.1's behavior preserved).
8. `cwd` no longer appears anywhere in the notes-only block.
9. The system prompt carries the new anchor sentence and is byte-identical
   across calls regardless of `cwd` changes.

### Existing tests amended (Tier-1, verified red-before/green-after)

This turned out to be a much larger list than initially scoped —
**16 existing tests across 5 files**, not the 3 originally estimated,
because any change to the system prompt's exact text breaks every test
asserting it byte-for-byte, and the cwd-marker's positional interaction
with Task 30.1's no-pending-user branch affects two more tests that
weren't part of the original estimate either. All 16 were amended
directly (this contract is genuinely changing) and confirmed to fail
against the unmodified code before the fix, and pass after:

- `test_task_2_2_session.py`: `test_ac2_context_block_in_final_user_message`,
  `test_ac2_stored_history_not_mutated_by_build` (cwd moved out of the
  notes-carrying message's content into its own preceding marker).
- `test_task_13_1_personality.py`: `test_ac5_default_personality_matches_prior_system_prompt`,
  `test_ac6_custom_personality_prepended` (exact system-prompt string
  gains the anchor sentence).
- `test_task_14_1_consume_notes.py`: `test_ac3_first_build_still_includes_notes`
  (same cwd-out-of-notes-message change as above).
- `test_task_17_2_remember_tool.py`: all six tests whose local `_FUNCTIONAL`
  constant asserts the exact functional block text (`test_ac_default_session_has_no_memory_block`,
  `test_ac_set_memory_updates_system_prompt`, `test_ac_set_memory_is_live_not_fixed_at_construction`,
  `test_ac_set_memory_empty_string_same_as_default`, `test_ac_memory_and_personality_both_present_personality_first`,
  `test_ac_existing_personality_behavior_unaffected`).
- `test_task_18_2_remember_general_session.py`: three tests with the same
  `_FUNCTIONAL`-constant pattern (`test_ac_set_general_memory_updates_system_prompt`,
  `test_ac_general_before_project_when_both_present`,
  `test_ac_personality_general_project_functional_full_order`).
- `test_task_30_1_no_synthetic_continuation_turn.py`:
  `test_ac1_no_trailing_message_after_tool_result_with_no_pending_user`
  (needed an extra `build_messages()` call first, to consume the
  first-ever-call cwd marker before exercising the no-synthetic-turn
  behavior it actually targets) and
  `test_ac4_pending_user_message_still_gets_context_block` (same
  cwd-moved-to-its-own-message change).

Full suite green after all 16 amendments plus the 10 new tests: 620/620.
