# Analysis

Measured findings about Sasha, from a session on 2026-10-12 that combined a
reading pass with live measurement against the local 26B endpoint. These are not
specs and not plans — they are the reasoning behind a set of changes, kept
because the *evidence* is more valuable than the conclusions.

| Document | Answers |
|---|---|
| [local-26b-simplification-audit.md](local-26b-simplification-audit.md) | What in Sasha exists to ration a paid API or a weak model, and what is worth deleting now that the model is local? Also: which of the audit's own recommendations were **wrong**, and what the measurements showed instead. |
| [string-handling-findings.md](string-handling-findings.md) | What can go wrong at the bytes/str, encode/decode and locale boundaries? Nine confirmed defects with reproductions, plus one correction where the contract suite proved the original reading wrong. |
| [agentic-tooling-review.md](agentic-tooling-review.md) | Is Sasha capable enough for agentic work on a 26B-class local model? Compares the tool sets of Claude Code, Codex, Gemini CLI, Cursor, opencode and DSH; reviews the published evidence on small-model tool calling; and runs two capability probes against the real model. Concludes with what to keep, what to improve, and what not to add. |

Both documents carry a status section recording what was acted on, so you can
tell which parts describe a past state and which are still live.

## The evidence

- [`reasoning-samples/`](reasoning-samples/) — real captured reasoning streams,
  two that never terminated and three that did. These are the primary evidence
  for the most surprising result in the audit: **no text statistic distinguishes
  a stuck reasoning loop from healthy reasoning**, which is why the guard is a
  patience limit rather than a repetition detector. See the sample README for how
  they were captured.
- [`probes/`](probes/) — the ad-hoc scripts that produced the other
  measurements (what the endpoint actually reports, whether tool-calling works,
  how reasoning renders, whether the crash net survives a real fault), plus
  `probe_agentic_task.py` and `probe_agentic_edit_task.py`, which give the real
  model two ordinary engineering tasks and check the result independently of
  whatever the model claims.

## A caution about method

The audit began as a reading pass and was **wrong in several places** because of
it: it concluded that tool calling did not work on the local model, that the
endpoint had a single slot, that caching was off, and that long prompts broke
tool use. Direct measurement overturned all four. The lesson is recorded in the
audit's own status section: reading code tells you what it looks like, not what
it does, and the difference was large enough here to change which features were
worth cutting.
