# Task 23.1: Fix bare-directory denylist matching (ISSUE-043)

## Authoritative contract
`docs/superpowers/specs/2026-07-21-issue-043-bare-directory-denylist-fix-design.md`
(all sections) is the complete contract for this task. This task fixes
ISSUE-043: a command referencing a denylisted directory with no trailing
filename (e.g. `ls ~/.ssh`) currently does not match the sensitive-path
denylist and auto-approves, because `fnmatch`-style patterns like
`~/.ssh/**` require a literal `/` plus content after `.ssh`.

## What already exists
- `src/sasha/safe_commands.py` (Task 21.1/22.1) —
  `touches_sensitive_path(command, executor)`, `requires_confirmation(args,
  executor)` (which delegates to `touches_sensitive_path` for its final
  check), `_tokenize`, `_normalize`. **Bind to all of this — only the
  single inline `fnmatch.fnmatch(...)` check inside
  `touches_sensitive_path` changes.**
- `tests/contract/test_task_21_1_safe_command_auto_approval.py`,
  `tests/contract/test_task_22_1_tool_call_logging.py` — MUST stay green
  unmodified. Neither exercises a bare denylisted directory (confirmed
  during this issue's diagnosis and again while writing the design doc),
  so this fix needs no contract-test amendments — it is purely additive.

## What's new
One change, confined to `touches_sensitive_path` in
`src/sasha/safe_commands.py`: the path-matching check gains a second
condition alongside the existing `fnmatch.fnmatch` call, so a
`/**`-suffixed pattern also matches the bare directory it's rooted at
(with the `/**` stripped), not just paths underneath it.

## Fix sketch (authoritative)

Replace this line inside `touches_sensitive_path`:
```python
            if fnmatch.fnmatch(normalized, expanded_pattern):
                return True
```
with:
```python
            if fnmatch.fnmatch(normalized, expanded_pattern):
                return True
            if (expanded_pattern.endswith("/**")
                    and normalized == expanded_pattern[:-3]):
                return True
```
Nothing else in `safe_commands.py` changes. This has been verified
directly (Tier 1 applied it to a scratch copy of `safe_commands.py` and
ran the full contract suite — 558/558 tests green, including the new ACs
below and all of `test_task_21_1_safe_command_auto_approval.py`/
`test_task_22_1_tool_call_logging.py` unmodified) before being committed
as authoritative.

## Constraints
- Modify `src/sasha/safe_commands.py` only.
- Do not touch: `scripts/`, `docs/superpowers/`, `CLAUDE.md`, `AGENTS.md`,
  `PLAN.md`, `ISSUE-TRACKING.md`, `README.md`, `.gitignore`,
  `pyproject.toml`, `src/sasha/__init__.py`, `src/sasha/triggers.py`,
  `src/sasha/tools.py`, `src/sasha/agent.py`, `src/sasha/executor.py`,
  `src/sasha/config.py`, `src/sasha/tool_log.py`, `src/sasha/repl.py`,
  `src/sasha/__main__.py`, `tests/contract/`.
- Do not change matching behavior for any pattern shape other than ones
  ending in `/**` — the other denylist categories (exact file,
  wildcard-suffix, any-depth glob) are untouched.
- **Executor routing (per CLAUDE.md's standing rule): this is a surgical,
  one-line edit to code that already works. Dispatch to the default
  model (deepseek-v4-pro) — do not launch under a local-model
  `SASHA_DISPATCH_MODEL` override.**

## Acceptance criteria
(encoded 1:1 in `tests/contract/test_task_23_1_bare_directory_denylist_fix.py`)
1. A bare denylisted directory with no trailing filename (`ls ~/.ssh`,
   `ls ~/.aws`) now requires confirmation via `requires_confirmation`, and
   `touches_sensitive_path` returns `True` for it —
   `test_ac1_bare_denylisted_directory_now_matches`.
2. The same directory with a trailing slash (`ls ~/.ssh/`) also matches —
   `test_ac2_bare_denylisted_directory_with_trailing_slash_matches`.
3. A bare *non*-denylisted directory (`ls ~/Documents`) still does not
   match — the fix is not over-broad —
   `test_ac3_bare_non_denylisted_directory_still_no_match`.
4. Existing per-file matches (`cat ~/.ssh/id_rsa`, `cat /etc/passwd`)
   still match exactly as before — no new test; the full existing
   `test_task_21_1_safe_command_auto_approval.py` and
   `test_task_22_1_tool_call_logging.py` suites stay green, unmodified,
   as part of this task's own gate.

## Standing instructions
- Your goal: make
  `tests/contract/test_task_23_1_bare_directory_denylist_fix.py` pass,
  with the full existing suite still green: `bash scripts/run-tests.sh`.
- NEVER modify files under `tests/contract/` — the gate rejects the
  commit if you do. If a contract test seems wrong or unimplementable,
  stop and flag it; do not commit.
- You may add your own supplementary tests under `tests/impl/`.
- Deliver exactly ONE new commit on top of the HEAD you started from;
  never amend, rebase, or reset; leave the working tree clean.
- Commit message per AGENTS.md, with trailers: `Done: Task 23.1 — <one
  line>`, `Fixed: ISSUE-043`,
  `Next: Task 23.2 — Phase 23 review (Tier 1, with Salvador)`, and the
  Co-Authored-By signature line from your dispatch prompt.
