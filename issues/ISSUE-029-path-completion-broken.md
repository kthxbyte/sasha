# ISSUE-029: Path completion broken after `cd`, and for any path containing `/`

**Status:** Specced as Task 12.1 — see
`docs/superpowers/specs/task-12.1-completion-path-fixes.md`.
**Discovered:** 2026-07-16, in Salvador's manual testing of Task 11.1/11.2
(Phase 11, tab completion).

## Symptoms

- Bare filename completion in the launch directory works
  (`cat PLAN<TAB>` → `PLAN.md`).
- After the user runs `cd` inside Sasha, path completion silently stops
  finding anything relevant.
- Absolute-path completion (`cat /home/user/Sa<TAB>`) and relative paths
  with a directory component (`cat src/sa<TAB>`) don't work, even in the
  original launch directory.

## Root causes (both confirmed directly, see conversation)

1. **Completion ignores `Executor.cwd`.** `Executor` tracks its own
   logical cwd as a plain string; the real Sasha process's OS-level cwd
   never changes — `os.chdir(self._cwd)` (`executor.py:144`) only runs
   inside each forked child, right before `execve`, not in the main
   process. `complete_via_bash`/`complete_filenames` (Task 11.2) never
   pass a `cwd=`, so they always operate from wherever Sasha was
   *launched*, not wherever the user has since `cd`'d to. Confirmed:
   running `complete_via_bash(["cat", "PLAN"], 1)` from `/tmp` (standing
   in for "Sasha's tracked cwd moved elsewhere") returns `[]` for a file
   that only exists in the Sasha repo.
2. **`readline`'s default completer delimiters include `/`.**
   `readline.get_completer_delims()` — confirmed — is
   `` ' \t\n`~!@#$%^&*()-=+[{]}\|;:\'",<>/?' ``, which includes `/`.
   Readline itself splits "the current word" at the last delimiter
   character before the cursor, so for `cat /home/salvador/Sa<TAB>` it
   hands the completer `text="Sa"` with the word boundary sitting right
   after `salvador/` — not the full `/home/salvador/Sa`.
   `words_and_cword()` (Task 11.1) assumes shell-word (shlex) boundaries
   line up with readline's own boundaries; they don't, once `/` is a
   readline delimiter. This breaks any path containing `/`, independent
   of the cwd issue above.

## Fix direction (specced, not yet implemented)

1. `readline.set_completer_delims(" \t\n")` — narrow readline's own
   delimiter set to whitespace only, so `/` (and other path-safe
   characters) stay part of one word, matching what `words_and_cword`
   already assumes. Standard fix for this exact, well-known gotcha in
   Python readline-based completers.
2. Thread `Executor.cwd` through to `complete_via_bash` (native
   `subprocess.run(..., cwd=...)`) and `complete_filenames` (join
   `cwd` with the prefix before globbing, strip it back off relative
   results — `os.path.join` already handles the "prefix is absolute"
   case correctly by discarding `cwd`).

## Next steps

Task 12.1 is specced and ready for dispatch.
