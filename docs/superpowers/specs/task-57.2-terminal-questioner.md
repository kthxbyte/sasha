# Task 57.2: `TerminalQuestioner` + `Repl`/`__main__.py` wiring

## Authoritative contract
`docs/superpowers/specs/2026-08-07-issue-059-question-tool-design.md` §4.7
(rendering mechanics, input handling) and §4.2 (the `Questioner` protocol
`TerminalQuestioner` must implement) are the complete contract for this
task. Task 57.1 (done, commit `b573df4`) already shipped the `Questioner`
protocol, the `question` tool's agent-loop dispatch, and everything else
upstream of the terminal — this task is the last piece: a real renderer
and wiring it into the process that actually runs.

## What already exists to bind, not reimplement
- `src/sasha/question.py`'s `Questioner` Protocol (Task 57.1):
  `ask(self, questions: list[dict]) -> list[tuple[str, str]] | None`. Each
  `dict` in `questions` is already `validate_shape()`-normalized:
  `{"question": str, "header": str, "multiple": bool, "options": [{"label":
  str, "description": str}, ...]}` — `multiple` is always present (defaulted
  `False` if the model omitted it), never missing.
- `src/sasha/agent.py`'s `run_consult()` already has a `questioner:
  Questioner | None = None` keyword parameter (Task 57.1) and calls
  `questioner.ask(questions)` exactly once per `question` tool_call when
  `questioner is not None`. This task does not touch `agent.py`.
- `src/sasha/repl.py:625-650`, `TerminalConfirmer` — the direct precedent
  for this task's class. Same two constructor dependencies (`input_fn`,
  `sink`), same pattern of building a colored multi-line message via ONE
  `self._sink.write_line(...)` call joined with `"\n"`, then a SEPARATE,
  short `self._input_fn(prompt)` call for the actual blocking read (the
  descriptive text and the read prompt are different things — see
  `TerminalConfirmer.ask()`: the y/n/e explanation is written via
  `write_line`, then `self._input_fn("> ")` is the actual prompt the
  cursor sits at).
- `src/sasha/repl.py:653-660` (pre-existing, immediately after
  `TerminalConfirmer`), the shared color constants: `_COMMAND_COLOR` (bold
  yellow), `_DIM` (faint), `_PROMPT_COLOR` (bold green), `_RESET`. Reuse
  these exact names — do not define new colors.
- `src/sasha/repl.py:665-...`, `TerminalSink` — exposes `color_enabled:
  bool`, `write_line(text, color=None)`, `write_chunk`, `ensure_newline`.
  `TerminalQuestioner` only needs `color_enabled` and `write_line`.
- `src/sasha/repl.py:55-67`, `Repl.__init__(self, config, executor, mutes,
  session, client_factory, confirmer, input_fn, sink,
  getpass_fn=getpass.getpass)` — 17 existing contract test files across
  the suite construct `Repl(...)` positionally with up to 9 args (through
  `getpass_fn`); none pass a 10th. Any new parameter MUST be added after
  `getpass_fn` with a default, exactly mirroring how `questioner` was
  added to `run_consult()` in Task 57.1 for the identical reason.
- `src/sasha/repl.py:93-126`, `Repl._consult()` — calls
  `run_consult(self._session, client, self._executor, self._confirmer,
  self._sink.write_chunk, on_reasoning=..., on_status=..., on_warning=...,
  on_async_status=..., max_tool_turns=..., on_phase=self._on_activity_phase)`.
  This is the only call site that needs a new `questioner=self._questioner`
  keyword argument.
- `src/sasha/__main__.py:19` imports `Repl, TerminalConfirmer, TerminalSink`
  from `sasha.repl`; `__main__.py:102`,
  `confirmer = TerminalConfirmer(_joined_input, sink)`; `__main__.py:112-122`,
  the `Repl(...)` construction call, all-keyword. `_joined_input` (defined
  `__main__.py:32-33`) is the paste-aware input function already used for
  both the main prompt and `TerminalConfirmer` — reuse it unchanged for
  `TerminalQuestioner` too (design §4.7: "inherits paste-join behavior...
  for free").

## What's new
1. `TerminalQuestioner` class in `src/sasha/repl.py`, declared immediately
   after `TerminalConfirmer` (before the `_SASHA_COLOR = ...` line):

```python
class TerminalQuestioner:
    def __init__(self, input_fn, sink):
        self._input_fn = input_fn
        self._sink = sink

    def ask(self, questions):
        all_pairs = []
        n = len(questions)
        try:
            for i, q in enumerate(questions, start=1):
                pairs = self._ask_one(i, n, q)
                if pairs is None:
                    return None
                all_pairs.extend(pairs)
        except (KeyboardInterrupt, EOFError):
            return None
        return all_pairs

    def _wrap(self, text, color):
        if self._sink.color_enabled:
            return f"{color}{text}{_RESET}"
        return text

    def _ask_one(self, i, n, q):
        header = q["header"]
        options = q["options"]
        multiple = q["multiple"]
        lines = [self._wrap(f"Question {i}/{n}: {header}", _COMMAND_COLOR),
                 "", f"  {q['question']}", ""]
        for idx, opt in enumerate(options, start=1):
            lines.append(f"  [{idx}] {opt['label']} — {opt['description']}")
        lines.append("")
        lines.append(self._wrap("  [C] Type your own answer", _DIM))
        lines.append("")
        if multiple:
            choice_line = (f"  Your choice(s) [1-{len(options)}, "
                            f"comma-separated, C]:")
        else:
            choice_line = f"  Your choice [1-{len(options)}, C]:"
        lines.append(self._wrap(choice_line, _PROMPT_COLOR))
        self._sink.write_line("\n".join(lines))

        while True:
            response = self._input_fn("> ").strip()
            if response == "":
                return None
            if response.upper() == "C":
                custom = self._input_fn("custom> ")
                return [(header, f"{header}: {custom}")]
            tokens = [t.strip() for t in response.split(",")]
            if not multiple and len(tokens) != 1:
                self._sink.write_line("  Invalid choice — try again.")
                continue
            indices = []
            valid = True
            for t in tokens:
                if not t.isdigit() or not (1 <= int(t) <= len(options)):
                    valid = False
                    break
                idx = int(t)
                if idx in indices:
                    valid = False
                    break
                indices.append(idx)
            if not valid:
                self._sink.write_line("  Invalid choice — try again.")
                continue
            return [(header, options[idx - 1]["label"]) for idx in indices]
```

   Notes on exact behavior pinned by the acceptance criteria below:
   - Rendering is ONE `write_line` call per question containing every line
     through the choice-prompt line (joined by `"\n"`, no trailing
     newline in the joined string itself — `write_line` appends its own).
     The actual read is a SEPARATE `self._input_fn("> ")` call — mirrors
     `TerminalConfirmer` exactly (see "What already exists" above).
   - Colored elements, matching `TerminalConfirmer`'s per-line-role
     convention: the `"Question i/N: <header>"` line in `_COMMAND_COLOR`;
     the `"  [C] Type your own answer"` line in `_DIM`; the final
     `"  Your choice..."` / `"  Your choice(s)..."` line in
     `_PROMPT_COLOR`. Every other line (blank lines, the question-text
     line, every option line) is never colored, in both modes.
   - Multi-select (`multiple: true`) uses the choice line `"  Your
     choice(s) [1-N, comma-separated, C]:"`; single-select uses `"  Your
     choice [1-N, C]:"` (no `(s)`, no "comma-separated" hint).
   - `"C"` is matched case-insensitively (`.upper() == "C"`) against the
     input after `.strip()`.
   - The custom-answer sub-prompt uses `self._input_fn("custom> ")`
     (mirrors `TerminalConfirmer`'s `"edit> "` sub-prompt naming). Its
     raw return value (not further `.strip()`ed) becomes `custom` in the
     pair `(header, f"{header}: {custom}")`.
   - Bare Enter (empty string after `.strip()`) at the CHOICE prompt (not
     the custom-answer sub-prompt) aborts the whole batch immediately —
     `ask()` returns `None`, discarding any already-answered questions
     from earlier in the same batch.
   - `KeyboardInterrupt`/`EOFError` raised by `self._input_fn(...)` at ANY
     point — the choice prompt or the custom-answer sub-prompt, on any
     question in the batch — aborts the whole batch the same way:
     `ask()` returns `None`. Caught once, around the whole per-question
     loop in `ask()` — not inside `_ask_one()`.
   - Invalid input (wrong token count for a single-select, a
     non-digit token, an out-of-range index, a duplicate index within one
     multi-select answer) writes `"  Invalid choice — try again."` via
     `self._sink.write_line(...)` (uncolored, both modes) and re-prompts
     at the SAME choice prompt (`self._input_fn("> ")` again) — the
     question block itself is not re-rendered. No attempt limit.
   - Multi-select selection order in the response is preserved exactly as
     typed (e.g. `"3,1"` produces `[(header, options[2].label), (header,
     options[0].label)]`, in that order) — not re-sorted.

2. `Repl.__init__` (`src/sasha/repl.py:55-67`) gains a `questioner=None`
   keyword parameter after `getpass_fn`, stored as `self._questioner`.

3. `Repl._consult()` (`src/sasha/repl.py:111-118`)'s `run_consult(...)`
   call gains `questioner=self._questioner`.

4. `src/sasha/__main__.py`:
   - Import `TerminalQuestioner` alongside the existing `Repl,
     TerminalConfirmer, TerminalSink` import (line 19).
   - `questioner = TerminalQuestioner(_joined_input, sink)`, immediately
     after the existing `confirmer = TerminalConfirmer(_joined_input,
     sink)` line (line 102).
   - Add `questioner=questioner` to the `Repl(...)` construction call
     (lines 112-122), alongside the existing `getpass_fn=getpass.getpass`.

## Constraints
- Do not touch `src/sasha/agent.py`, `src/sasha/tools.py`,
  `src/sasha/question.py`, or `src/sasha/session.py` — Task 57.1's
  territory, already shipped and gated.
- Do not modify `TerminalConfirmer`, `TerminalSink`, or any of the shared
  color constants (`_COMMAND_COLOR`, `_DIM`, `_PROMPT_COLOR`, `_RESET`,
  etc.) — reuse them as-is.
- Do not change `Repl.__init__`'s existing 9 parameters (`config` through
  `getpass_fn`) — `questioner` is strictly additive, appended after
  `getpass_fn`, defaulted to `None`. None of the 17 existing contract
  test files that construct `Repl(...)` may need to change; if your diff
  requires changing any of them, stop and flag it — that means the
  parameter wasn't added correctly.
- No `TIMEOUT` handling, no `config.toml` toggle, no partial-batch state —
  all explicitly out of scope per the design (§2).
- `TerminalQuestioner` must not import from `sasha.agent` or
  `sasha.question` — it only ever receives an already-normalized
  `questions: list[dict]` and returns `list[tuple[str, str]] | None`; it
  has no reason to know about `validate_shape`, `format_result`, or the
  tool-dispatch machinery.

## Acceptance criteria
1. `ask()` on a single-select question with a valid numeric choice
   returns exactly one `(header, label)` pair —
   `test_ac1_single_select_returns_one_pair`.
2. `ask()` on a multi-select question with comma-separated indices
   returns one `(header, label)` pair per index, in the order typed —
   `test_ac2_multi_select_comma_separated_returns_pairs_in_order`.
3. Bare Enter at the choice prompt on any question aborts the whole
   batch (`ask()` returns `None`), including discarding an
   already-answered earlier question in the same batch —
   `test_ac3_bare_enter_aborts_whole_batch`,
   `test_ac3_bare_enter_on_second_question_still_aborts_whole_batch`.
4. Invalid input (out-of-range index, non-numeric token, multiple
   comma-separated tokens on a single-select question, a duplicate index
   within one answer) writes `"  Invalid choice — try again."` and
   re-prompts at the same choice prompt, with no attempt limit, until a
   valid answer is given — `test_ac4_out_of_range_reprompts_then_succeeds`,
   `test_ac4_single_select_rejects_multiple_tokens_then_succeeds`,
   `test_ac4_duplicate_indices_reprompts`.
5. Typing `"C"` (case-insensitive) at the choice prompt triggers a
   `"custom> "` sub-prompt; the pair returned is `(header, f"{header}:
   {custom_text}")` — `test_ac5_custom_answer_prompts_again_and_formats_pair`,
   `test_ac5_custom_answer_case_insensitive`.
6. `KeyboardInterrupt` or `EOFError` raised by `input_fn` at the choice
   prompt, at the custom-answer sub-prompt, or on any question in a
   multi-question batch (discarding earlier answers), aborts the whole
   batch (`ask()` returns `None`) —
   `test_ac6_keyboard_interrupt_at_choice_prompt_aborts_batch`,
   `test_ac6_eof_error_at_choice_prompt_aborts_batch`,
   `test_ac6_eof_error_on_custom_text_subprompt_aborts_batch`,
   `test_ac6_interrupt_on_second_question_discards_first_answer`.
7. A multi-question batch renders `"Question i/N: <header>"` for each
   question in order (1-indexed, `N` = total question count) and the
   returned list concatenates every question's pairs in question order —
   `test_ac7_multi_question_batch_concatenates_pairs_in_order`.
8. Plain (color-disabled) rendering is byte-exact, including the
   multi-select choice-line variant —
   `test_ac8_plain_rendering_exact`, `test_ac8_plain_rendering_multi_select_choice_line`.
9. When color is enabled: the header line is wrapped in `_COMMAND_COLOR`,
   the `[C]` line in `_DIM`, the choice-prompt line in `_PROMPT_COLOR`,
   nothing else is colored, and stripping ANSI codes from the colored
   rendering reproduces the plain rendering exactly —
   `test_ac9_header_line_colored`, `test_ac9_custom_answer_line_dim`,
   `test_ac9_choice_prompt_line_uses_prompt_color`,
   `test_ac9_colored_text_matches_plain_text_when_ansi_stripped`.
10. `Repl(...)` accepts an optional `questioner` keyword argument
    (defaulting to `None`) without requiring any change to existing
    positional construction, and `_consult()` passes it through to
    `run_consult()` unchanged — `test_ac10_repl_questioner_defaults_to_none`,
    `test_ac10_repl_threads_questioner_into_run_consult`.
11. `src/sasha/__main__.py` constructs exactly one `TerminalQuestioner(...)`
    call and passes a `questioner=` keyword argument to its single
    `Repl(...)` construction call —
    `test_ac11_main_module_constructs_terminal_questioner`.

## Standing instructions
- Your goal: make the contract tests in
  `tests/contract/test_task_57_2_terminal_questioner.py` pass. All 23
  already exist and are committed, red against the base you're starting
  from.
- NEVER modify files under `tests/contract/` — the gate rejects the
  commit if you do. If a contract test seems wrong or unimplementable,
  stop and flag it.
- You may add your own supplementary tests under `tests/impl/`.
- Flag unexpected state; do not modify anything outside this task.
- Deliver exactly ONE new commit on top of the HEAD you started from;
  never amend, rebase, or reset; leave the working tree clean.
- Commit message must follow the task-commit trailer format in
  `CLAUDE.md`: `Done: Task 57.2 — <description>`, `Next: Task 57.3 —
  Phase 57 review (Tier 1, with Salvador)` (Task 57.2 is the last task in
  Phase 57 per `PLAN.md`; this synthetic "Next" line — a real `Task N.N`
  token satisfying `verify.sh`'s trailer grammar check even though no
  Task 57.3 spec exists — matches the exact precedent set by Task 51.2's
  own commit, the last task of Phase 51: `Next: Task 51.3 — Phase 51
  review (Tier 1, with Salvador)`), and your `Co-Authored-By:` signature
  line exactly as given in the dispatch prompt.

## Verified fix sketch

Implemented and verified in a scratch worktree before committing: 23/23
new contract tests green, full suite 707/709 (2 deselected, ISSUE-058) —
one additional pre-existing flake surfaced during verification,
`test_task_51_2_sasha_recap_command.py::test_ac4_recap_sends_gathered_context_prompt`
(~1/3 runs), reproduced identically with this task's diff stashed out —
confirmed pre-existing and unrelated, logged as ISSUE-062, not fixed
here. No bugs specific to this task's own diff were found; the design's
§4.7 pseudocode-level description translated directly into working code
on the first pass, unlike Task 57.1 (which needed 2 fixes) — the only
judgment calls were the exact byte format of rendering (design §4.7's
mockup was illustrative, not byte-exact) and the precise invalid-input
vocabulary, both now pinned exactly by AC8/AC9 above and the "What's new"
section's notes.

`src/sasha/repl.py` diff (both the `Repl` class changes and the new
`TerminalQuestioner` class, declared right after `TerminalConfirmer`):

```diff
@@ -54,7 +54,8 @@ def _boot_api_line(profile):
 
 class Repl:
     def __init__(self, config, executor, mutes, session, client_factory,
-                 confirmer, input_fn, sink, getpass_fn=getpass.getpass):
+                 confirmer, input_fn, sink, getpass_fn=getpass.getpass,
+                 questioner=None):
         self._config = config
         self._executor = executor
         self._mutes = mutes
@@ -64,6 +65,7 @@ class Repl:
         self._input_fn = input_fn
         self._sink = sink
         self._getpass_fn = getpass_fn
+        self._questioner = questioner
         self._current_profile = config.profiles[config.default_profile]
         self._activity = ActivityIndicator(
             colors={"thinking": config.activity_indicator_thinking_color,
@@ -115,7 +117,8 @@ class Repl:
                         on_warning=_on_warning,
                         on_async_status=on_async_status,
                         max_tool_turns=self._config.limits.max_tool_turns,
-                        on_phase=self._on_activity_phase)
+                        on_phase=self._on_activity_phase,
+                        questioner=self._questioner)
         except ChatError as e:
             self._sink.write_line(
                 f"[sasha] {self._current_profile.name} unreachable: "
@@ -650,6 +653,76 @@ class TerminalConfirmer:
             return ("reply", response)
 
 
+class TerminalQuestioner:
+    def __init__(self, input_fn, sink):
+        self._input_fn = input_fn
+        self._sink = sink
+
+    def ask(self, questions):
+        all_pairs = []
+        n = len(questions)
+        try:
+            for i, q in enumerate(questions, start=1):
+                pairs = self._ask_one(i, n, q)
+                if pairs is None:
+                    return None
+                all_pairs.extend(pairs)
+        except (KeyboardInterrupt, EOFError):
+            return None
+        return all_pairs
+
+    def _wrap(self, text, color):
+        if self._sink.color_enabled:
+            return f"{color}{text}{_RESET}"
+        return text
+
+    def _ask_one(self, i, n, q):
+        header = q["header"]
+        options = q["options"]
+        multiple = q["multiple"]
+        lines = [self._wrap(f"Question {i}/{n}: {header}", _COMMAND_COLOR),
+                 "", f"  {q['question']}", ""]
+        for idx, opt in enumerate(options, start=1):
+            lines.append(f"  [{idx}] {opt['label']} — {opt['description']}")
+        lines.append("")
+        lines.append(self._wrap("  [C] Type your own answer", _DIM))
+        lines.append("")
+        if multiple:
+            choice_line = (f"  Your choice(s) [1-{len(options)}, "
+                            f"comma-separated, C]:")
+        else:
+            choice_line = f"  Your choice [1-{len(options)}, C]:"
+        lines.append(self._wrap(choice_line, _PROMPT_COLOR))
+        self._sink.write_line("\n".join(lines))
+
+        while True:
+            response = self._input_fn("> ").strip()
+            if response == "":
+                return None
+            if response.upper() == "C":
+                custom = self._input_fn("custom> ")
+                return [(header, f"{header}: {custom}")]
+            tokens = [t.strip() for t in response.split(",")]
+            if not multiple and len(tokens) != 1:
+                self._sink.write_line("  Invalid choice — try again.")
+                continue
+            indices = []
+            valid = True
+            for t in tokens:
+                if not t.isdigit() or not (1 <= int(t) <= len(options)):
+                    valid = False
+                    break
+                idx = int(t)
+                if idx in indices:
+                    valid = False
+                    break
+                indices.append(idx)
+            if not valid:
+                self._sink.write_line("  Invalid choice — try again.")
+                continue
+            return [(header, options[idx - 1]["label"]) for idx in indices]
+
+
 _SASHA_COLOR = "\x1b[1;36m"   # bold cyan
 _LLM_COLOR = "\x1b[1;35m"     # bold magenta
 _PROMPT_COLOR = "\x1b[1;32m"  # bold green
```

`src/sasha/__main__.py` diff:

```diff
@@ -16,7 +16,7 @@ from sasha.config import ConfigError, load
 from sasha.executor import Executor
 from sasha.llm import ChatClient
 from sasha.mutes import MuteStore
-from sasha.repl import Repl, TerminalConfirmer, TerminalSink
+from sasha.repl import Repl, TerminalConfirmer, TerminalQuestioner, TerminalSink
 from sasha.session import Session
 
 _PASTE_GRACE_S = 0.01
@@ -100,6 +100,7 @@ def main(argv: list[str] | None = None) -> int:
         sink.write_line(config.notice)
 
     confirmer = TerminalConfirmer(_joined_input, sink)
+    questioner = TerminalQuestioner(_joined_input, sink)
 
     _isatty = sys.stdin.isatty()
 
@@ -119,6 +120,7 @@ def main(argv: list[str] | None = None) -> int:
         input_fn=_repl_input,
         sink=sink,
         getpass_fn=getpass.getpass,
+        questioner=questioner,
     )
 
     if profile_name != config.default_profile:
```
