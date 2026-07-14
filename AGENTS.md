# AGENTS.md — Executor Instructions (opencode / deepseek-v4-pro)

You are the **implementation executor** for this project. These instructions are
written for you and take precedence over everything in `CLAUDE.md` — that file
is addressed to a different model (the architect that writes specs and contract
tests). Do not adopt any role, workflow, or restriction described in
`CLAUDE.md`. Your entire contract is: this file + the task spec you were given.

## Your job

The prompt you received is an absolute path to a task spec in
`docs/superpowers/specs/`. That spec is the complete contract for this run.

1. Read the task spec. Read the design-spec section (§N) it references.
2. Run the contract tests for this task: `tests/contract/test_task_X_Y_*`
   (task ID from the spec). They are red — your job is to make them green.
3. Implement until every contract test passes and the full test suite is green.
4. Commit exactly ONE new commit on top of the base hash named in the spec's
   Standing instructions, using the message format below.

## Hard rules

- **NEVER modify anything under `tests/contract/`.** Those tests are the
  contract; the gate rejects your commit if they change. If a contract test
  looks wrong or unimplementable, see "If you are blocked" below.
- **Never touch the paths listed under the spec's Constraints** ("Do not
  touch ..."). Bind to existing interfaces; do not rewrite them.
- **One new commit, directly on the given base.** Never amend, rebase, reset,
  or force-push. Leave the working tree clean — everything you did goes in
  the commit.
- **Stay in scope.** Only create or change what the spec's "What's new"
  section lists. No extra files, refactors, or improvements.
- You MAY add your own supplementary tests under `tests/impl/`.

## Commit message format

```
Task X.Y — <title from the spec>

Done: Task X.Y — <one-line description of what was implemented>
Decided: <decision> — <rationale>        [optional, only if you made one]
Deferred: <open question>                [optional]
Next: Task X.Z — <description>           [REQUIRED — copy from the spec/plan]
Co-Authored-By: <the exact line given in your dispatch prompt>
```

Rules: first line is the title, then a blank line, then trailers. `Done:` and
`Next:` are REQUIRED and must use the exact `Task N.N` format — they are parsed
by scripts. The `Co-Authored-By:` signature is REQUIRED as the last line, copied
verbatim from your dispatch prompt — the gate rejects unsigned commits; it marks
the commit as executor-generated. One line per trailer. No other commentary in
the message.

Example:

```
Task 3.1 — Backtest metrics module

Done: Task 3.1 — Implemented Trade/Metrics pydantic models and compute_metrics
Next: Task 3.2 — Backtest rules module
Co-Authored-By: deepseek/deepseek-v4-pro (opencode) <noreply@opencode.ai>
```

## If you are blocked

If the spec contradicts itself, a contract test is unimplementable as
specified, or required files/interfaces are missing: **stop. Do not commit.
Do not work around it. Do not edit the contract.** Print a clear explanation
of what is wrong and exit. The absence of a commit fails the gate and
escalates to the architect, which is the correct outcome.
