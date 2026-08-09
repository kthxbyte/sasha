# Task 60.1: bash-completion default-delegation fallback (ISSUE-058, root-caused)

## Authoritative contract
No separate design doc — small, well-scoped single-file fix, matching the
Task 8.1/28.1/30.1/58.1 precedent. This spec is the complete contract.

**Bug report (Salvador, manual testing, 2026-08-08):** tab-completing a
directory name works, but completing a filename argument — e.g. `nano
filename.txt<TAB>` — finds nothing.

**Root cause, confirmed empirically** (see "Root cause investigation"
below): `src/sasha/completion.py`'s `complete_via_bash()` only ever reads
`${COMPREPLY[@]}` after invoking a resolved bash-completion function. But
bash-completion's own catch-all completer for any command *without* a
bespoke completion script — `_comp_complete_minimal`, which is what
`nano`, `cat`, and the overwhelming majority of everyday commands resolve
to — never populates `COMPREPLY` itself. Its entire body is:
```bash
_comp_complete_minimal()
{
    local cur prev words cword comp_args
    _comp_initialize -- "$@" || return
    compopt -o bashdefault -o default
}
```
`compopt -o default` is a signal to bash's own interactive readline C
code — "fall back to normal filename completion" — that exists entirely
inside a real interactive bash session and is invisible to a spawned
`bash -c <script>` subprocess inspecting `COMPREPLY` after the fact.
Because `complete_via_bash()` has no way to observe this signal, it
returns `[]` for `nano`/`cat`/almost anything, and the caller
(`__main__.py`'s `_completer`) never falls back to
`complete_filenames()` in that branch — only when bash-completion isn't
*installed at all* (`_bash_ok` is `False`). `cd` works today because
bash-completion ships a real, specific `_comp_cmd_cd` function that
populates `COMPREPLY` with directory names directly, instead of
delegating.

This is also the true, previously-undiagnosed root cause of **ISSUE-058**
(`test_task_11_2_completion_bash_integration.py::test_ac1_filename_completion_via_minimal_fallback`
and `test_task_12_1_completion_path_fixes.py::test_ac1_cwd_changes_where_bash_looks`,
both deselected since 2026-08-07 as "not yet root-caused" /
"environment-specific"). It is not environment-specific in the sense of
misconfiguration — `_comp_complete_minimal`'s `compopt -o default`
delegation is bash-completion's documented, portable default behavior for
any command without a dedicated completion script, confirmed by testing
against a completely made-up command name (`totallymadeupcmd123`), which
resolves to the exact same `_comp_complete_minimal` function via
bash-completion's `complete -D` catch-all loader.

## Root cause investigation (evidence, not to be re-derived)
```
$ bash -c '
source /usr/share/bash-completion/bash_completion 2>/dev/null
_completion_loader nano 2>/dev/null
func=$(complete -p nano 2>/dev/null | sed -n "s/.*-F \([^ ]*\).*/\1/p")
echo "func=[$func]"
'
func=[_comp_complete_minimal]

$ bash -c '
source /usr/share/bash-completion/bash_completion 2>/dev/null
_completion_loader totallymadeupcmd123 2>/dev/null
func=$(complete -p totallymadeupcmd123 2>/dev/null | sed -n "s/.*-F \([^ ]*\).*/\1/p")
echo "func for unknown cmd=[$func]"
'
func for unknown cmd=[_comp_complete_minimal]

$ bash -c '
source /usr/share/bash-completion/bash_completion 2>/dev/null
_completion_loader cd 2>/dev/null
func=$(complete -p cd 2>/dev/null | sed -n "s/.*-F \([^ ]*\).*/\1/p")
echo "func=[$func]"
'
func=[_comp_cmd_cd]
```
Both `nano` and a nonexistent command resolve to `_comp_complete_minimal`
(pure `compopt -o default` delegation, no `COMPREPLY`), while `cd`
resolves to a real, `COMPREPLY`-populating function — the exact
difference between "directory completion works" and "filename completion
for an arbitrary command doesn't."

## What already exists to bind, not reimplement
- `src/sasha/completion.py`: `complete_via_bash()`
  (`completion.py:52-73` in the pre-Task-60.1 file) — this task modifies
  the body of this one function only: the bash script it builds gains a
  `compopt` shadow function, and the Python-side result handling gains a
  sentinel check. The function's signature
  (`complete_via_bash(words, cword, timeout_s=0.5, cwd=None) -> list[str]`)
  does not change.
- `src/sasha/completion.py`: `complete_filenames(prefix, cwd=None)`
  (`completion.py:81-88`) — already implements exactly the filename/glob
  completion this task needs; call it, do not reimplement it.
- `src/sasha/__main__.py`'s `_completer()` closure (`__main__.py:143-164`)
  — unchanged by this task. It already calls `complete_via_bash()` and
  uses whatever list comes back; once `complete_via_bash()` itself
  produces the right filenames, no caller-side change is needed.
- `tests/contract/test_task_11_2_completion_bash_integration.py` and
  `tests/contract/test_task_12_1_completion_path_fixes.py` — existing,
  immutable, **already correctly specify** the fixed behavior
  (`test_ac1_filename_completion_via_minimal_fallback` and
  `test_ac1_cwd_changes_where_bash_looks` both already assert `"PLAN.md"`
  results for `cat`). Do not touch either file — your job is to make your
  implementation satisfy them, and they will pass once it does.
- **`scripts/run-tests.sh` was already amended by Tier 1** in this same
  spec commit (verified in a scratch worktree — see "Verified fix sketch"
  below): the `DESELECT` array that carved out ISSUE-058's two tests is
  now empty, so both tests run as part of every future `TEST_CMD`. You do
  not need to touch `scripts/` (it's in `PROTECTED_PATHS`) — this is
  already done for you.

## What's new
Modify `complete_via_bash()` in `src/sasha/completion.py`. Full
implementation, verified working as-is in a scratch worktree (see
"Verified fix sketch" below):

```python
_DEFAULT_COMPLETION_SENTINEL = "__SASHA_DEFAULT_COMPLETION__"


def complete_via_bash(words: list[str], cword: int, timeout_s: float = 0.5,
                      cwd: str | None = None) -> list[str]:
    comp_line = " ".join(words)
    comp_words_bash = "(" + " ".join(shlex.quote(w) for w in words) + ")"
    script = (
        "source /usr/share/bash-completion/bash_completion 2>/dev/null\n"
        f"_completion_loader {shlex.quote(words[0])} 2>/dev/null\n"
        "_sasha_default_requested=0\n"
        "compopt() {\n"
        "  local prev=\"\"\n"
        "  for a in \"$@\"; do\n"
        "    if [ \"$prev\" = \"-o\" ] && "
        "{ [ \"$a\" = \"default\" ] || [ \"$a\" = \"bashdefault\" ]; }; then\n"
        "      _sasha_default_requested=1\n"
        "    fi\n"
        "    prev=\"$a\"\n"
        "  done\n"
        "}\n"
        f"COMP_LINE={shlex.quote(comp_line)}\n"
        "COMP_POINT=${#COMP_LINE}\n"
        f"COMP_WORDS={comp_words_bash}\n"
        f"COMP_CWORD={cword}\n"
        f'func=$(complete -p {shlex.quote(words[0])} 2>/dev/null | '
        r'sed -n "s/.*-F \([^ ]*\).*/\1/p")' "\n"
        '[ -n "$func" ] && "$func"\n'
        'if [ "${#COMPREPLY[@]}" -eq 0 ] && [ "$_sasha_default_requested" = "1" ]; then\n'
        f'  echo "{_DEFAULT_COMPLETION_SENTINEL}"\n'
        'fi\n'
        'printf "%s\\n" "${COMPREPLY[@]}"\n'
    )
    try:
        r = subprocess.run(["bash", "-c", script], capture_output=True,
                           text=True, timeout=timeout_s, cwd=cwd)
    except (subprocess.TimeoutExpired, OSError):
        return []
    lines = [line.rstrip() for line in r.stdout.splitlines() if line.rstrip()]
    if lines and lines[0] == _DEFAULT_COMPLETION_SENTINEL:
        return complete_filenames(words[cword] if cword < len(words) else "", cwd=cwd)
    return lines
```

Mechanism, precisely:
1. The bash script shadows the `compopt` builtin with a shell function
   *before* invoking the resolved completion function. This serves two
   purposes: it captures the `-o default`/`-o bashdefault` signal into
   `_sasha_default_requested`, and it avoids the real `compopt` builtin's
   `"compopt: not currently executing completion function"` error (the
   real builtin only works inside bash's actual interactive completion
   machinery, which this simulated invocation isn't).
2. After the resolved function runs, if `COMPREPLY` ended up empty *and*
   the default flag was set, the script prints the sentinel line
   `__SASHA_DEFAULT_COMPLETION__` before the (empty) `COMPREPLY` dump.
3. Python-side: if the **first** output line is exactly the sentinel,
   call `complete_filenames()` on the word actually being completed
   (`words[cword]`) with the same `cwd`, and return that instead.
4. **This must not fire for a real, specific completer that legitimately
   found nothing** (e.g. `git checkout` with a prefix matching no
   branch) — those never call `compopt -o default`, so
   `_sasha_default_requested` stays `0`, no sentinel is printed, and the
   function correctly returns `[]`, exactly as before this task. Only
   the `_comp_complete_minimal`-style "I have nothing smarter, use
   bash's own default" path triggers the fallback — this is the same
   distinction real interactive bash draws.

## Constraints
- Do not touch `src/sasha/__main__.py` — no caller-side change is needed;
  `complete_via_bash()` becoming self-sufficient is the entire fix.
- Do not touch `complete_filenames()`, `complete_first_word()`,
  `complete_builtin_arg()`, `words_and_cword()`, or
  `bash_completion_available()` — unchanged.
- Do not change `complete_via_bash()`'s signature, return type, or its
  existing timeout/`OSError` handling (`except (subprocess.TimeoutExpired,
  OSError): return []` stays exactly as-is).
- The `compopt` shadow function must be defined *before* `func` is
  invoked in the script, and must never call the real `compopt` builtin
  (which would error outside real completion context) — it only needs to
  parse its own arguments.
- The sentinel string must not collide with any real filename in
  practice; `__SASHA_DEFAULT_COMPLETION__` (double-underscore-wrapped,
  matching no shell-glob-common naming) is the exact string to use.

## Acceptance criteria
1. `complete_via_bash(["nano", "PLAN"], 1, cwd=<repo>)` includes
   `"PLAN.md"` in its result, using the real installed bash-completion —
   `test_ac1_nano_gets_filename_completion_via_real_bash_completion`.
2. When the bash subprocess's stdout is exactly the sentinel line
   (mocked, isolated from the real environment's bash-completion
   version), `complete_via_bash()` returns exactly
   `complete_filenames()`'s result for the completed word/cwd —
   `test_ac2_default_sentinel_triggers_filename_fallback`.
3. When the bash subprocess's stdout is empty (no sentinel, no
   completions — mocked), `complete_via_bash()` returns `[]`, never
   falling back to filenames without the sentinel —
   `test_ac3_no_sentinel_no_fallback_stays_empty`.
4. A real completer with its own logic (`git chec` → `checkout`) is
   unaffected, and the sentinel string never appears in its output —
   `test_ac4_real_git_completer_unaffected_and_sentinel_never_leaks`.
5. The two previously-deselected ISSUE-058 tests
   (`test_task_11_2_completion_bash_integration.py::test_ac1_filename_completion_via_minimal_fallback`,
   `test_task_12_1_completion_path_fixes.py::test_ac1_cwd_changes_where_bash_looks`)
   now pass for real — covered by `scripts/run-tests.sh` already running
   them (Tier 1 already removed the deselect; no test file for you to
   write here, just don't break them).

## Standing instructions
- Your goal: make the contract tests in
  `tests/contract/test_task_60_1_bash_completion_default_fallback.py`
  pass. All 4 already exist and are committed; 3 are red against the base
  you're starting from. `test_ac3_no_sentinel_no_fallback_stays_empty`
  already passes today by coincidence — the current implementation
  already returns `[]` when there's nothing to fall back to, since the
  sentinel mechanism doesn't exist yet. It is not vacuous: it encodes a
  real constraint (no blanket fallback without the sentinel) your
  implementation must not break, matching the Task 57.1 AC14 precedent.
- The two ISSUE-058 tests in `test_task_11_2_completion_bash_integration.py`
  and `test_task_12_1_completion_path_fixes.py` are also currently red
  (the deselect that hid them is already removed) — your implementation
  must make them pass too, but you must not edit either file.
- NEVER modify files under `tests/contract/` — the gate rejects the
  commit if you do. If a contract test seems wrong or unimplementable,
  stop and flag it.
- You may add your own supplementary tests under `tests/impl/`.
- Flag unexpected state; do not modify anything outside this task.
- Deliver exactly ONE new commit on top of the HEAD you started from;
  never amend, rebase, or reset; leave the working tree clean.
- Commit message must follow the task-commit trailer format in
  `CLAUDE.md`: `Done: Task 60.1 — <description>`, `Fixed: ISSUE-058`,
  `Next: Task 60.2 — Phase 60 review (Tier 1, with Salvador)`, and your
  `Co-Authored-By:` signature line exactly as given in the dispatch
  prompt.

## Verified fix sketch

Implemented and verified in a scratch worktree before committing: 4/4 new
contract tests green, both previously-deselected ISSUE-058 tests green
for real, full suite 743/743 (0 deselected — the DESELECT array is now
empty) on two consecutive clean runs.

`src/sasha/completion.py` diff — exactly as given in "What's new" above,
verified working as-is, no changes needed from the spec's version.
Manually confirmed against the real system for the exact case from
Salvador's bug report:
```python
>>> from sasha.completion import complete_via_bash
>>> complete_via_bash(["nano", "PLAN"], 1, cwd="<repo>")
['PLAN.md']
>>> complete_via_bash(["git", "chec"], 1, cwd="<repo>")
['checkout']
```

`scripts/run-tests.sh` diff (Tier 1 change, already committed alongside
this spec — shown for context, not something you need to touch):
```diff
-# ISSUE-058: these 2 tests fail consistently on this machine (this
-# environment's bash-completion doesn't register a filename-completion
-# rule) — confirmed pre-existing and environment-specific, not caused by
-# any task's changes, by reproducing the identical failure in a disposable
-# worktree checked out before the diff under test existed. Deselected here
-# (rather than fixed) so the gate stays green for every future task's
-# TEST_CMD run instead of escalating on this same known gap every time.
-DESELECT=(
-  --deselect "tests/contract/test_task_11_2_completion_bash_integration.py::test_ac1_filename_completion_via_minimal_fallback"
-  --deselect "tests/contract/test_task_12_1_completion_path_fixes.py::test_ac1_cwd_changes_where_bash_looks"
-)
+# ISSUE-058 (Task 60.1): the 2 deselects previously here are gone — the
+# real root cause (complete_via_bash() couldn't observe bash-completion's
+# `compopt -o default` delegation signal, used by the catch-all completer
+# every command without a bespoke completion script resolves to) is fixed,
+# and both tests now pass for real.
+DESELECT=()
 exec python3 -m pytest -q "${ARGS[@]}" "${DESELECT[@]}"
```
