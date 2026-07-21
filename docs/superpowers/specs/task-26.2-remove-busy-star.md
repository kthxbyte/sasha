# Task 26.2: Remove the Phase 19 busy-star mechanism (ISSUE-045)

## Authoritative contract
`docs/superpowers/specs/2026-07-21-issue-045-cursor-activity-indicator-design.md`
§6 is the complete contract for this task. Task 26.1 (already shipped)
built the real replacement — the cursor-based `ActivityIndicator`. This
task removes the mechanism it replaces: the `\S` busy-star prompt token,
`prompt.py`'s `_busy_star()`, `render()`'s `busy` parameter, and
`Repl`'s `_background_tasks`/`_begin_background_task`/
`_end_background_task` machinery.

## What already exists
- `src/sasha/activity_indicator.py`, `agent.py`'s `on_phase` hook,
  `repl.py`'s `self._activity` wiring (Task 26.1) — **do not touch any
  of this.** This task is purely subtractive, removing a *different,
  now-redundant* mechanism.
- `src/sasha/prompt.py` — `render()`, `_walk()`, `COLOR_TOKENS`,
  `DEFAULT_TEMPLATE`, `validate_template()`. **Bind to the color-token
  system and `\u`/`\h`/`\w`/`\W` positional handling — only the `\S`-
  related pieces named below are removed.**
- `src/sasha/repl.py` — `_spawn_bootstrap_summary`, `_bootstrap_stage_2`,
  `loop()`'s `prompt_mod.render(...)` call. **Bind to the bootstrap
  thread's own spawning/threading logic and the render call's other
  arguments — only the `_background_tasks`-related lines named below are
  removed.**

## What's new
Nothing new — this task only deletes code and updates the tests that
pinned the mechanism being deleted.

## Fix sketch (authoritative)

### `src/sasha/prompt.py`

1. `DEFAULT_TEMPLATE` — remove `\S ` (the token plus nothing else; the
   existing space after it stays):
   ```python
   DEFAULT_TEMPLATE: str = r"\[magenta\][sasha]\[reset\] \[bold-green\]\u@\h\[reset\]:\[bold-blue\]\w\[reset\] $ "
   ```
   (was: `r"\[magenta\][sasha]\[reset\]\S \[bold-green\]\u@\h\[reset\]:\[bold-blue\]\w\[reset\] $ "`)
2. `_POSITIONAL = {"u", "h", "w", "W", "S"}` → `_POSITIONAL = {"u", "h", "w", "W"}`.
3. Delete the `_busy_star()` function entirely.
4. `render()`: remove the `busy: bool = False` parameter from the
   signature, and remove the `elif value == "S": parts.append(_busy_star(busy, color_enabled))`
   branch — the remaining `else:` branch (for `"W"`) is unchanged.

A pre-existing `config.toml` with a custom `prompt` field containing
`\S` will raise `PromptError` (`unrecognized escape '\S'`) on next
`load()` — expected per the design doc's decision; not a bug to guard
against.

### `src/sasha/repl.py`

1. `Repl.__init__` — delete `self._background_tasks = 0`.
2. Delete the `_begin_background_task`/`_end_background_task` methods
   entirely.
3. `_spawn_bootstrap_summary` — delete the `self._begin_background_task()`
   line.
4. `_bootstrap_stage_2` — delete the `self._end_background_task()` line
   (the last line of the method).
5. `loop()`'s `prompt_mod.render(...)` call — remove the
   `busy=self._background_tasks > 0` keyword argument entirely (the call
   ends at `color_enabled=self._sink.color_enabled,` instead).

## Pre-existing contract tests — already amended by Tier 1, in the same
## commit as this spec (not the implementer's job)

**Important correction caught while finalizing this spec**: an earlier
draft told the implementer it could touch these files as "the one
exception" to the usual rule. That's wrong — `verify.sh`'s clean-tree/
contract-test check (`CONTRACT_TESTS`) fails the gate unconditionally if
*anything* under `tests/contract/` changes in the dispatch commit,
regardless of what a spec says; the script doesn't read prose. So, as
with every other task this project has shipped, **Tier 1 makes these
amendments directly, before dispatch, in the same commit as this spec** —
verified locally in a scratch worktree together with the `prompt.py`/
`repl.py` changes below (full suite green: 570/570, i.e. 585 minus the
15 deleted tests). The implementer's commit touches only `src/sasha/
prompt.py` and `src/sasha/repl.py` — see Constraints.

Five files, not four (grepping for `✦`/`_background_tasks`/`busy=`
across `tests/contract/` the first time missed two live e2e-test
usages — found by grepping again, more broadly, before finalizing):
- `tests/contract/test_task_19_2_busy_star_indicator.py` — deleted
  outright (all 15 tests exist solely to pin the mechanism being
  removed).
- `tests/contract/test_task_19_3_repl_bootstrap_wiring.py` — five
  `assert repl._background_tasks == 0` lines removed; every other
  assertion in each of those five tests is unchanged.
- `tests/contract/test_task_6_2_colorization_prompt.py` — four hardcoded
  prompt-string assertions updated to the star-free form (see the actual
  diff for exact strings — already committed, not restated here).
- `tests/contract/test_task_9_1_configurable_prompt.py` — the same two
  patterns (colored + plain `DEFAULT_TEMPLATE` expected strings).
- `tests/contract/test_task_3_3_e2e.py` — a real e2e test helper,
  `SashaProc.drain_prompt()`, waited for the literal star character in a
  live subprocess's PTY output; now waits for `"$ "` instead (the
  prompt's own trailing marker) — same "cumulative transcript, first
  occurrence anywhere satisfies it" reliability characteristics as
  before, not a regression. `test_task_3_2_entry.py` had a stale comment
  referencing the star's old position, updated for accuracy; its actual
  assertion (`"~ $ " in r.stdout`) was already about the trailing `$ `
  and needed no behavior change.

## Constraints
- Modify only `src/sasha/prompt.py` and `src/sasha/repl.py`. The
  `tests/contract/` amendments above are already committed — **do not
  touch anything under `tests/contract/` at all.**
- Do not touch `src/sasha/activity_indicator.py`, `src/sasha/agent.py`,
  `src/sasha/config.py` — Task 26.1 already shipped and verified; nothing
  in this task's contract exercises them.
- Do not add any backwards-compatibility handling for a stale `\S` in a
  custom config.toml prompt — the `PromptError` on next load is the
  intended behavior (design doc §6).
- **Executor routing (per CLAUDE.md's standing rule): this is a
  surgical removal from code that already works. Dispatch to the
  default model (deepseek-v4-pro) — do not launch under a local-model
  `SASHA_DISPATCH_MODEL` override.**

## Acceptance criteria
(this task has no new contract-test file of its own — it is purely
subtractive, so its acceptance criteria are the already-amended tests
above passing against your `src/sasha/prompt.py`/`repl.py` changes)
1. The full existing suite is green: 570/570 (585 minus the 15 deleted
   tests) — `bash scripts/run-tests.sh`.
2. `src/sasha/prompt.py` and `src/sasha/repl.py` contain no remaining
   reference to `_busy_star`, `\S` (as a template token), `busy` (as a
   `render()` parameter), `_background_tasks`,
   `_begin_background_task`, or `_end_background_task`.

## Standing instructions
- Your goal: the full existing suite green (570/570) after your two
  file changes: `bash scripts/run-tests.sh`.
- NEVER modify files under `tests/contract/` — the gate rejects the
  commit if you do. All the amendments this task needed there are
  already committed as part of this spec. If something still seems
  wrong once you've made the `prompt.py`/`repl.py` changes, stop and
  flag it; do not commit.
- Deliver exactly ONE new commit on top of the HEAD you started from;
  never amend, rebase, or reset; leave the working tree clean.
- Commit message per AGENTS.md, with trailers: `Done: Task 26.2 — <one
  line>`, `Fixed: ISSUE-045`,
  `Next: Task 26.3 — Phase 26 review (Tier 1, with Salvador)`, and the
  Co-Authored-By signature line from your dispatch prompt.
