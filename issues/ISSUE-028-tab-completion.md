# ISSUE-028: No TAB completion

**Status:** Specced — see
`docs/superpowers/specs/2026-07-16-tab-completion-design.md` for the full
design (that document is the authoritative record; this file is just the
tracker entry).
**Discovered:** 2026-07-16, in conversation after the boot-time hint
(Task 10.1) landed.

## Context

Salvador asked how hard bash-style TAB completion would be to add.
Investigation (documented in full in the design doc) confirmed the real
`bash-completion` package (2.11-8, 967 per-command scripts) is installed
on this system and can be driven directly from a spawned `bash`
subprocess — correctly, cheaply (~30ms/request), covering filenames,
git branches, and hundreds of other tools — rather than needing to
reimplement any of that logic.

## Next steps

Specced as two tasks, ready for dispatch:
- Task 11.1 — own-logic completion (PATH scan, Sasha's own builtins,
  readline wiring, no subprocess).
- Task 11.2 — bash-completion subprocess integration + graceful
  degradation.
