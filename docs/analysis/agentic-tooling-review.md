# Is Sasha capable enough for agentic work on a 26B-class local model?

**Question.** Not "does Sasha match Claude Code" — that is the wrong target. The
question is what an agent loop actually needs to finish engineering work when the
model is a 26B local model rather than a frontier API model, and whether Sasha
has it.

**Method.** Three sources, in descending order of trust:

1. **Measured, locally.** The tool registries of opencode and the DeepSeek
   Harness were read out of their installed code (`opencode.exe` contains the
   literal array `["bash","read","edit","glob","grep","webfetch","task",
   "todowrite","websearch","lsp","skill"]`), and two capability probes were run
   against the real local model through Sasha's real loop.
2. **Official documentation** for harnesses not installed here, with anything
   undocumented marked as such.
3. **Published measurements** on small-model tool calling, labelled by evidence
   strength rather than presented as settled fact.

---

## 1. What Sasha has

Nine tools. Six are auto-approved (the three read-only ones, plus memory,
questions and skills) and three go through the confirmation gate.

| Tool | Gate | Purpose |
|---|---|---|
| `run_command` | confirms, unless entirely allowlisted read-only verbs | shell |
| `read_file` | auto | read, capped at 40 000 chars |
| `write_file` | confirms | whole-file write, atomic |
| `edit_file` | confirms | exact-match region replace |
| `list_dir` | auto | one directory level |
| `search_files` | auto | recursive regex over contents |
| `remember` | auto | durable cross-session memory |
| `question` | auto (own UI) | structured clarification |
| `use_skill` | auto | load a skill's instructions |

---

## 2. What other harnesses ship

Counts vary by more than 5x, which by itself tells you there is no consensus set.

| Harness | Tools | Plan/todo | Subagent | Editing | grep + glob |
|---|---|---|---|---|---|
| **Claude Code** | **46** | `EnterPlanMode`/`ExitPlanMode` + `Task*` (model-gated) | `Agent` (was `Task`) | `Write` whole file + `Edit` exact string | yes — **but both absent by default on macOS/Linux/WSL, where shell search is used instead** |
| **Codex CLI** | ~32, heavily feature-gated | `update_plan` | `spawn_agent` + multi-agent sets | `apply_patch`, unified diff only | **neither — no grep/glob/read tools at all; shell + patch only** |
| **Gemini CLI** | 24 | `write_todos` + plan mode | one tool per subagent | `replace` exact string + `write_file` | yes |
| **Cursor** | 9 capabilities (names unpublished) | plan mode, no todo tool | yes, automatic | undocumented | one combined search capability |
| **opencode** | 11 in its permission registry, plus `write`/`patch`/`list` definitions | `todowrite` | `task` | `edit`, `patch` | yes |
| **DeepSeek Harness** | ~26 incl. goals, jobs, plan mode, workflow | `todo_write` + goals | `subagent`, `subagent_fork`, jobs | `edit` | yes |

Sources: [Claude Code tools reference](https://code.claude.com/docs/en/tools-reference) ·
[Gemini CLI tools.md](https://github.com/google-gemini/gemini-cli/blob/main/docs/reference/tools.md) ·
Codex tool specs (source-derived, not documented) ·
[Cursor agent docs](https://cursor.com/docs/agent/overview) (capabilities only).

**Two findings here are load-bearing for Sasha:**

- **Claude Code ships `Glob` and `Grep` — and disables both by default on Linux,
  macOS and WSL, using shell search instead.** Sasha's position (allowlisted
  `cat`/`head`/`tail`/`find`/`grep` auto-run; no separate glob tool) is the same
  design choice, made independently. The "add a glob tool" argument is weaker
  than the tool lists alone suggest.
- **Codex has no read, grep or glob tools at all.** Its whole file-interaction
  surface is a shell plus one patch tool. That is a second data point that a
  small tool set is sufficient, not a compromise.

The convergence across every harness is exactly: **shell, read, write/edit,
search**. Sasha has all four, and its extra tools (`remember`, `question`,
`use_skill`) exist in none of the others — so this is not a one-way gap.

---

## 3. What the evidence says about *small* models specifically

Labelled by strength. The distinction matters: much of what is written about
agent tooling is advice from frontier-model experience, which does not transfer.

**Tool-set size.**
- [measured] Shrinking a tool catalog by retrieval took selection accuracy from
  **13.62% → 43.13%** and halved prompt tokens ([2505.03275](https://arxiv.org/abs/2505.03275)).
- [measured] An adaptive shortlist averaging **7 tools** matched showing 50 tools
  on coverage (90.3% vs 90.8%) ([2605.24660](https://arxiv.org/abs/2605.24660)).
- [measured] Retriever *quality* dominates count: adding BM25 at test time
  dropped Gorilla accuracy from 71.68% → 11.28% ([2305.15334](https://arxiv.org/abs/2305.15334)).
- [documented] Anthropic: "Too many tools or overlapping tools can also distract
  agents" ([writing tools for agents](https://www.anthropic.com/engineering/writing-tools-for-agents)).
- No evidence found for a universal maximum. The supported regime is a
  **single-digit to low-teens** tool set.

**Constrained decoding for tool calls.**
- llama.cpp's GBNF/JSON-schema constraints apply to `response_format`, and its
  docs are explicit that **tool-calling schemas are injected into the prompt, not
  constrained** ([grammars README](https://github.com/ggml-org/llama.cpp/blob/master/grammars/README.md)).
- [measured] "Constrained decoding removes parse failures but can induce
  non-termination" ([2609.26693](https://arxiv.org/abs/2609.26693)).
- [measured] The dominant cost of output formats is the **prompt-level
  instruction**, not the decoder constraint — "sampling bias accounts for only a
  fraction" ([2604.03616](https://arxiv.org/abs/2604.03616)).

**Parallel tool calls.**
- [documented] llama.cpp supports parallel calls but **disables them by default**
  ([function-calling.md](https://github.com/ggml-org/llama.cpp/blob/master/docs/function-calling.md));
  Ollama lacks them for some models. Sasha's sequential execution matches the
  default every local stack chooses.

**Context at small windows.**
- [measured] "Lost in the middle": accuracy is best for information at the start
  or the end ([2307.03172](https://arxiv.org/abs/2307.03172)).
- [measured] Chroma's Context Rot: degradation begins **well before** the nominal
  window limit; coherent long inputs did *worse* than shuffled ones; focused
  ~300-token prompts beat ~113k-token prompts. Qwen3-8B produced degenerate
  non-attempts on 4.21% of tasks ([context rot](https://www.trychroma.com/research/context-rot)).
- [documented] Anthropic's mitigations: compaction, **clearing stale tool
  results**, structured notes persisted outside the window, and subagents
  returning 1–2k-token summaries ([context engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)).

**Todo/plan tools.**
- [documented] Anthropic frames a todo list as a memory device. No numbers.
- [measured] The one planning ablation found "data analysis is a consistent
  bottleneck while **planning is less so**" ([2606.13904](https://arxiv.org/abs/2606.13904)).
- **No published on/off ablation of a todo tool for 7B–35B models was found.**
  This is the weakest-supported "best practice" in the field.

**Loops and failure modes.**
- [measured] DRY (the sampler) cut loop rate by **47%** across 1.5B–120B models
  while preserving MT-Bench/MMLU/GSM8K — whereas repetition/presence/frequency
  penalties and n-gram blocking "often suppress looping only at strengths that
  also degrade formatting or fluency" ([2608.22761](https://arxiv.org/abs/2608.22761)).
- [measured] BFCL force-terminates a turn after 20 steps because models "get
  stuck in a loop or are unable to make progress" ([BFCL v3](https://gorilla.cs.berkeley.edu/blogs/13_bfcl_v3_multi_turn.html)).
- [measured] For small models the failure is the *combination* of planning,
  calling and summarising ([2401.07324](https://arxiv.org/abs/2401.07324)).

---

## 4. Can it actually do the work? Two probes

Tool lists prove nothing on their own, so the loop was given two real tasks with
the real 26B and auto-approval.

### Probe 1 — write a module, write tests, run them

> Create `stats.py` with `mean`, `median`, `mode` (raising `ValueError` on an
> empty list), write pytest tests including the empty cases, run them, fix
> failures, report the result.

**Result: passed.** 3 tool calls, 40 seconds, no iteration needed.
`write_file stats.py` (30 lines) → `write_file test_stats.py` (47 lines) →
`run_command pytest` (exit 0). An independent pytest run by the probe confirms
**10 passed**.

### Probe 2 — explore a project, edit a file it did not write, iterate

> Add a `--scale` option to the CLI in `app/cli.py`, defaulting to 1.0,
> multiplying `core.compute`; add a test; run the suite and make sure it passes.

**Result: passed**, and the trajectory is the interesting part — 14 tool calls,
156 seconds:

```
list_dir .            -> README.md app/ tests/
list_dir app          -> __init__.py cli.py core.py
list_dir tests        -> test_core.py
read_file app/cli.py, app/core.py, tests/test_core.py
edit_file app/cli.py  -> replaced 3 line(s) with 4        <- correct tool, existing file
read_file README.md
write_file tests/test_cli.py   (589 bytes)
write_file tests/test_cli.py   (520 bytes)                <- rewrote its own new file
run_command pytest    -> exit code: 1                     <- FAILED
read_file tests/test_cli.py
write_file tests/test_cli.py   (724 bytes)                <- fixed it
run_command pytest    -> exit code: 0                     <- PASSED
```

An independent check confirms the feature works: `python3 -m app.cli 1 2 3
--scale 2` prints `12.0`, and pytest reports 4 passed. **It wrote, ran, failed,
read, fixed and re-ran** — that is the loop working, not a lucky single shot.

**What the probes also expose:**

- **Orientation dominates.** 9 of 14 calls in probe 2 were listing and reading.
  For a 5-file project that is tolerable; in a large repository it would not be.
- **A wasted turn**: it wrote `tests/test_cli.py` twice before ever running it.
- **No `search_files` use at all** — it navigated with `list_dir` + `read_file`.
- **Six confirmations** would have been required from a human for a 14-call task.
  The gate is the largest UX cost of agentic use, and it is working as designed.

---

## 5. Recommendations

### Keep, with evidence

1. **Nine tools.** Inside the measured single-digit-to-low-teens band. Claude
   Code's 46 is not a target; Codex does serious work with a shell and a patch
   tool; Claude Code disables its own search tools on Linux.
2. **No glob tool.** Sasha's allowlisted shell search is the same choice Anthropic
   makes on this platform. Adding a tool would spend selection attention on
   something the shell already does without a prompt.
3. **No todo tool — for now.** No ablation supports it, the one planning
   measurement found planning is *not* the binding constraint, and the reason it
   looked necessary here was a bug (history trimming discarded the original task,
   fixed in ISSUE-077). Revisit only if long runs demonstrably lose the thread.
4. **Sequential tool execution.** Matches every local serving stack's default.
5. **Turn cap + repeated-action detector.** BFCL caps turns for the same reason.

### Improve, ranked

1. **Enable DRY on the endpoint** (`dry_multiplier: 0.0` → ~0.8). It is the only
   *measured* anti-loop intervention (47% fewer loops, quality preserved),
   whereas the repeat penalties already active on the endpoint trade fluency for
   it. Server-side, not a Sasha change.
2. **Clear stale tool results instead of dropping whole exchanges.** Anthropic's
   documented technique and the direct answer to Context Rot. Sasha currently
   trims oldest-first, discarding the assistant/tool pair entirely; replacing old
   tool *result bodies* with a short placeholder keeps the conversation coherent
   at a fraction of the tokens. Highest-value remaining context work.
3. **Restate the task near the end of the window.** "Lost in the middle" says
   both ends matter; the pin (ISSUE-077) covers the start only.
4. **Reduce confirmation friction for edits.** Six prompts for one small task is
   the difference between usable and not. A session-scoped "allow edits under
   this project" opt-in would preserve the safety model while making agentic runs
   practical. **This is a design decision for Salvador, not a bug.**
5. **Subagent delegation — the one genuine "maybe".** It is the recommended
   context-isolation technique for small windows, and 9-of-14 orientation calls
   is exactly the cost it removes. But one `task`-style tool is the useful part;
   the orchestration families in DSH/Codex are not, and no small-model evidence
   supports them.

### Do not add

- **Web tools.** Codex gates them; the local-first choice stands; and `curl` via
  `run_command` already works (confirmed), so the gain would be ergonomics.
- **An LSP tool.** opencode and Claude Code have one; for a Python stdlib
  codebase, `run_command` with a linter covers it at zero selection cost.
- **Anything from a 46-tool harness** without a measurement showing the loop
  needs it.

---

## 6. Open questions

1. Is the 26B's demonstrated competence (two clean passes) representative, or did
   both probes fall in its comfort zone? Larger, multi-file tasks would tell us —
   and would also test the orientation cost.
2. Is the confirmation gate the real blocker for agentic use, or is it fine
   because a human is watching anyway? This changes recommendation 4 completely.
3. Should context work (2) precede any new tool? The measurements say yes.
