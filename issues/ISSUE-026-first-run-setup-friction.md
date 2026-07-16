# ISSUE-026: First-run setup friction — no friendly API key prompt

**Status:** Design note only — explicitly deferred, not specced, not scheduled.
**Discovered:** 2026-07-16, during brainstorming for the configurable-prompt
feature (ISSUE-022's Tier 1), while checking how `Config` behaves with no
`config.toml` present.

## Context

Salvador asked whether Sasha creates a default `config.toml` on first run,
thinking about how much friction a brand-new user hits before Sasha is
usable, and whether there should be an easy way to input a deepseek API
key if none is configured.

## Findings

1. **`config.toml` is never written to disk.** `config.load()`
   (`src/sasha/config.py`) only synthesizes an in-memory default `Config`
   (the `deepseek` and `local` profiles) when the file is missing — there
   is no first-run write, no scaffold file, nothing persisted.
2. **An empty API key fails silently, not helpfully.** If there's no
   `config.toml` *and* no `DEEPSEEK_API_KEY` environment variable,
   `Profile.api_key` resolves to `""`. `llm.py`'s `ChatClient` (lines
   49-50) checks `if self.profile.api_key:` before adding the
   `Authorization` header — when empty, the header is just omitted
   entirely, and the request goes out unauthenticated. A brand-new user's
   first experience is whatever raw rejection deepseek's API sends back
   for a missing auth header, not a clear "you need to set up a key"
   message from Sasha itself.

## What a fix might look like (not designed, just a starting point)

- Detect the empty-api-key case explicitly (rather than letting the
  request fail) and print a clear `[sasha]` message pointing at
  `~/.config/sasha/config.toml` and `DEEPSEEK_API_KEY`.
- Possibly an interactive first-run prompt that offers to write a starter
  `config.toml` with a pasted key — raises its own questions (should Sasha
  ever write files into `~/.config` unprompted? where does the prompt fit
  in `__main__.py`'s startup sequence relative to the REPL loop?) that
  need a real brainstorm, not a quick decision here.

## Next steps (when picked up)

Brainstorm + design spec via the usual Tier 1 flow if/when Salvador wants
to schedule this — this issue is a note to start from, not a task ready
for dispatch.
