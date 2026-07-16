# ISSUE-030: Personality file

**Status:** Specced — see
`docs/superpowers/specs/2026-07-16-personality-design.md` for the full
design (authoritative record; this file is just the tracker entry).
**Discovered:** 2026-07-16, following up on an earlier scratch note
(`proposal-personality.md`, gitignored, never committed to the repo)
where Salvador drafted a personality document for Sasha adapted from a
movie script, and asked about a filesystem-based personality-loading
feature.

## Context

Salvador drafted `~/.config/sasha/PERSONALITY.md` by hand (a detailed,
TARS-inspired character document) and wants Sasha to load it into the
LLM's system prompt. Unlike `config.toml` (which ISSUE-026 found is
deliberately never auto-written), Salvador wants `PERSONALITY.md` to be
self-installing: if absent, Sasha writes a neutral, instructional
default and tells the user once, so the feature is discoverable without
a manual setup step.

## Next steps

Specced as Task 13.1, ready for dispatch.
