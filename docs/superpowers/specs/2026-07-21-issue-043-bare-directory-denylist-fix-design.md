# Bare-directory denylist matching fix — Design (ISSUE-043)

**Status:** Draft for review
**Date:** 2026-07-21
**Amends:** `docs/superpowers/specs/2026-07-20-issue-041-safe-command-auto-approval-design.md` §4.2 step 6

## 1. Mission

Fix a gap in the sensitive-path denylist matching shipped in Task 21.1
(ISSUE-041): a command referencing a denylisted directory with no
trailing filename — e.g. `ls ~/.ssh` — currently does not match the
denylist and auto-approves, because `fnmatch`-style patterns like
`~/.ssh/**` require a literal `/` plus at least one character after
`.ssh`, which a bare directory path doesn't have. Any command whose
denylisted-directory argument happens to have no filename component
slips through the exact mechanism ISSUE-041 built to catch it.

## 2. Scope

### In scope:
- Fix pattern matching so a `dir/**`-style pattern also matches `dir`
  itself (the bare directory, with nothing after it).

### Out of scope / non-goals:
- Any other denylist pattern category (`/etc/passwd`, `/etc/shadow*`,
  `**/.env`) — none of these have this gap; they're all file-reference
  patterns, not directory-prefix patterns, so a "bare" version of them
  isn't a meaningful concept the same way it is for `dir/**`.
- Redesigning the denylist format or matching engine — this is a
  minimal, targeted correction to the existing `fnmatch`-based approach,
  not a rewrite.

## 3. Key contracts & assumptions

1. **Safety-only change.** This fix can only make `requires_confirmation`
   and `touches_sensitive_path` return `True` in cases where they
   previously (incorrectly) returned `False` — it never turns a
   previously-gated command into an auto-approved one. No existing
   passing test (Task 21.1, 22.1, 22.2) exercises a bare denylisted
   directory, so none pin the buggy behavior; this fix needs no
   contract-test amendments, only new tests.
2. **Scoped to `/**`-suffixed patterns only.** The four other pattern
   shapes in the default denylist (exact file, wildcard-suffix file,
   any-depth-glob file) don't have an equivalent "bare form" gap and are
   untouched.

## 4. The fix

One shared helper, used by both `requires_confirmation` (via
`touches_sensitive_path`) and `touches_sensitive_path` directly — replace
the inline `fnmatch.fnmatch(normalized, expanded_pattern)` check with:

```python
def _matches_denylist_pattern(normalized: str, expanded_pattern: str) -> bool:
    if fnmatch.fnmatch(normalized, expanded_pattern):
        return True
    if expanded_pattern.endswith("/**") and normalized == expanded_pattern[:-3]:
        return True
    return False
```

For a pattern like `~/.ssh/**` (expanded to `/home/user/.ssh/**`): the
existing `fnmatch` call still catches `/home/user/.ssh/id_rsa` and
anything deeper; the new second check catches the bare directory itself
(`/home/user/.ssh`, with the trailing `/**` stripped, compared for exact
equality — not another glob, since the directory path itself has no
wildcard content to match against).

## 5. Decisions log

- **Decision:** Fix scoped to `/**`-suffixed patterns; other pattern
  shapes untouched. **Rationale:** They don't share the gap — a "bare
  form" of `/etc/passwd` or `**/.env` isn't a meaningful additional case
  the way a bare directory is for a `dir/**` pattern.
- **Decision:** No contract-test amendments needed. **Rationale:**
  Verified no existing test (Task 21.1/22.1/22.2) exercises a bare
  denylisted directory — the gap was only surfaced by a *new* test
  written during Task 22.1's spec work (deliberately using
  `~/.ssh/config` instead, to avoid depending on unfixed behavior), so
  this fix is purely additive from the test suite's point of view.

## 6. Testing strategy

Pure unit tests on `safe_commands.touches_sensitive_path` and
`requires_confirmation`:
- A bare denylisted directory (`ls ~/.ssh`, `ls ~/.aws`) now requires
  confirmation / is flagged as touching a sensitive path.
- The same directory with a trailing slash (`ls ~/.ssh/`) also matches.
- A bare *non*-denylisted directory (`ls ~/Documents`) still doesn't
  match — the fix isn't over-broad.
- Existing per-file cases (`cat ~/.ssh/id_rsa`, `cat /etc/passwd`) still
  match exactly as before — regression coverage via the full existing
  Task 21.1/22.1 suites staying green, unmodified.
