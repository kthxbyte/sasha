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

### Open-source and local-first harnesses

Surveyed separately because these are the projects that have to cope with weak
models, which is Sasha's situation. Aider, Cline and Continue keep a fixed
built-in set; OpenHands, goose and gptme are registry/extension-based.

| Project | Tools | Editing | Local-model provisions |
|---|---|---|---|
| **Aider** | **none — no tool API at all** | six+ edit formats: `whole`, SEARCH/REPLACE `diff`, `diff-fenced`, `udiff`, `patch` | `--edit-format` defaults to "depends on model"; `--weak-model` does summaries; reflection retry capped at 3; model-specific hacks (Deepseek Coder v2 puts the filename inside the fence) |
| **Cline** | 9 SDK tools | exact-string `editor`; `apply_patch` mutually exclusive with it | per-tool retry policy (editing tools never retry); plan mode keeps shell read-only; "do not enable two similar tools at once" |
| **Continue** | 18 built-ins | exact-string `single_find_and_replace`, `multi_edit`, lazy-snippet `edit_existing_file` | `PROVIDER_TOOL_SUPPORT` decides whether native tool calling is used **at all**, per provider and model |
| **OpenHands** | registry; default `terminal`, `file_editor`, `task_tracker` | `str_replace_editor` (exact string) or `edit_file` (1-indexed line range) | native function calling *probed*, not assumed; text-parsing fallback when absent; flags shrink the list |
| **goose** | extension-based; `developer` is exactly `write`, `edit`, `shell`, `tree`, `read_image` | `edit` find/replace with single-match enforcement; `write` whole file | **tool shim** for models that emit tool calls as plaintext; Code Mode; lead/worker model split; per-tool repetition counter |
| **gptme** | ~40, allowlist-controlled | `patch` (fenced SEARCH/REPLACE — **not** unified diff), `save`, `append` | three tool formats (`markdown`/`xml`/`tool`); `--tools`, `TOOL_ALLOWLIST`, `read-only`, `none`; "try xml first" for weak models; native tools need 7B+ |

Sources: `aider/coders/editblock_coder.py`, `cline/sdk/.../tools/definitions.ts`,
`continue/core/tools/builtIn.ts`, `openhands/controller/stuck.py` (v0.20.0) and
`software-agent-sdk`, `goose/.../platform_extensions/developer/mod.rs`,
`gptme/docs/tool-formats.rst`.

**Cross-cutting results:**

- **Exact-string replace is universal** — Aider's SEARCH/REPLACE, Cline's
  `editor`, Continue's `single_find_and_replace`, OpenHands'
  `str_replace_editor`, goose's `edit`. Five of the six also ship whole-file
  write alongside it. **Unified diff appears in only two projects in this entire
  review** (Aider's `udiff`, Codex's `apply_patch`). Sasha's `edit_file` +
  `write_file` pair is the mainstream choice, not a compromise.
- **No project was verifiable as AST-aware.** Nothing to chase.
- **Aider is the most instructive because it has no tools at all**: it makes the
  *edit format* the interface and varies it per model. Its SEARCH/REPLACE apply
  path is a three-tier tolerant matcher — perfect match, then
  whitespace-insensitive, then `...` elision — and on failure it emits
  `SearchReplaceNoExactMatch` plus a "Did you mean" and asks for corrected
  blocks. That is a direct, proven answer to the thing Sasha does strictly.
- **Only two projects publish a numeric tool-count rule**: goose (Code Mode is
  "best for 5+ extensions"; traditional calling suits "1-3") and gptme, which
  notes that every enabled tool's schema is serialised into every request.
- **goose ships a todo extension but leaves it off by default**, and gptme's
  `todo` is opt-in. That is a third data point against treating a todo tool as
  mandatory.

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

### What the tool schemas cost, measured

gptme's point is measurable in Sasha, and it is bigger than it looks. Every
request serialises all nine schemas:

| tool | chars | ~tokens |
|---|---|---|
| `edit_file` | 1,007 | 251 |
| `question` | 917 | 229 |
| `write_file` | 662 | 165 |
| `search_files` | 633 | 158 |
| `remember` | 582 | 145 |
| `use_skill` | 579 | 144 |
| `list_dir` | 503 | 125 |
| `run_command` | 486 | 121 |
| `read_file` | 408 | 102 |
| **total** | **5,777** | **~1,444** |

So the tool definitions consume **~12% of the 12,000-token history budget**
before a single message is sent (~15% including the system prompt). That reframes
"how many tools does a 26B need": each tool is paid for in history on every turn,
whether or not it is used. `edit_file` and `question` alone are 480 tokens, and
both descriptions can be tightened substantially without losing information —
the cheapest context win available after compaction.

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

### Probe 3 — find files by name in a deep tree (the glob question)

> A project ~5 levels deep with ~45 files, four of them named `settings.py`.
> Add `TIMEOUT = 30` to every one that does not already define it, then run the
> suite. Only the *name* of the target files is known, never the location.

**Result: passed, and it settles the question.** 10 tool calls, 74 seconds, all
four files correct, suite green:

```
search_files                                          found which one already had TIMEOUT
list_dir .                                            app/ project/ tests/
run_command  find ... -name 'settings*.py' | sort      one call, auto-approved (both verbs allowlisted)
read_file    notify/settings.py, auth/settings.py, billing/settings.py
edit_file x3                                          exact tier
run_command  pytest -> exit code 0
```

Two things follow. First, **the model reaches for allowlisted `find` naturally**,
so filename search costs it one call and zero confirmations — a glob tool would
add a schema, a tool-selection decision and ~130 tokens per request to duplicate
something the shell already does frictionlessly. Second, orientation fell from 9
calls (probe 2) to 5, because `find` did in one call what `list_dir` needed
three for. It also **discriminated correctly**: it never opened the one
`settings.py` that already defined `TIMEOUT`.

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

1. **Nine tools.** Inside the measured single-digit-to-low-teens band, and both
   Codex and Claude Code demonstrate that fewer tools is a valid design rather
   than a limitation. Claude Code's 46 is not a target.
2. **Exact-string `edit_file` beside whole-file `write_file`.** The universal
   choice across every harness surveyed, including the two that use no tools at
   all. The probe also shows the 26B discriminates correctly: `edit_file` on the
   file it did not write, `write_file` for the new ones.
3. **Sequential tool execution.** Matches every local serving stack's default.
4. **Turn cap + repeated-action detector.** BFCL caps turns for the same reason.
5. **No todo tool — for now.** No published ablation exists, the one planning
   measurement found planning is not the binding constraint, goose and gptme both
   leave theirs opt-in, and the reason it looked necessary here was a bug that is
   now fixed (ISSUE-077).
6. **No `apply_patch`.** Unified diff appears in two projects out of ten.

### Improve, ranked

1. **Enable DRY on the endpoint** (`dry_multiplier: 0.0` → ~0.8). The only
   *measured* anti-loop intervention: 47% fewer loops while preserving quality,
   where the repeat penalties already active trade fluency for it. Server-side,
   not a Sasha change.
2. **Make `edit_file` tolerant, the way Aider's SEARCH/REPLACE is.** Today a
   whitespace difference yields a bare "not found", and a 26B will produce
   near-miss whitespace regularly. Aider's three tiers — exact, then
   whitespace-insensitive, then `...` elision — plus a "did you mean" on failure
   is the proven shape, and it costs no schema tokens. **This is the highest-value
   code change on the list.**
3. **Clear stale tool *results* instead of dropping whole exchanges.** Anthropic's
   documented answer to context rot. Sasha trims oldest-first and discards the
   assistant/tool pair entirely; replacing old result bodies with a short
   placeholder keeps the conversation coherent at a fraction of the tokens.
4. **Trim the two fattest schemas.** `edit_file` and `question` are 480 tokens of
   the 1,444, on every request. Recovering ~300 of them is ~2.5% of the history
   budget for zero capability loss.
5. **Restate the task near the *end* of the window.** "Lost in the middle" says
   both ends matter; the pin (ISSUE-077) covers only the start.
6. **Borrow OpenHands' stuck heuristics.** Sasha has only the consecutive-action
   case. OpenHands adds three concrete thresholds worth adopting: three identical
   actions whose observations are *also* all errors; three agent messages with no
   observation between; and a six-step alternating A-B-A-B pattern. Comparing
   the observation as well as the action is what makes the run-tests-fix-run-tests
   pattern safe to allow.
7. **Reduce confirmation friction for edits.** Six prompts for one small task is
   the difference between usable and not. A session-scoped "allow edits under this
   project" opt-in would preserve the safety model while making agentic runs
   practical. **A design decision for Salvador, not a bug.**
8. **One `task`-style subagent — the genuine "maybe".** It is the recommended
   context-isolation technique for small windows, and 9-of-14 orientation calls is
   exactly the cost it removes. Only worth it if larger-tree work shows the cost
   is real.

### Do not add

- **Web tools.** Codex gates them; the local-first choice stands; `curl` already
  works (confirmed), so the gain would be ergonomics.
- **An LSP tool.** For a Python stdlib codebase, `run_command` with a linter
  covers it at zero selection cost.
- **Glob — resolved: do not add.** The deciding probe (probe 3) found the model
  issuing one allowlisted `find ... -name 'settings*.py' | sort`, auto-approved,
  and finishing a five-level/45-file task in 10 calls with orientation down to 5.
  Claude Code's choice to disable `Glob`/`Grep` on Linux is therefore the right
  model to copy, and the fact that Continue/OpenHands/goose ship filename search
  is beside the point: they are not driving a local model through a shell that
  already has `find` on the auto-approve list.
- **Anything from a 46-tool harness** without a measurement showing the loop needs
  it.

## 6. Open questions

1. Is the 26B's demonstrated competence (two clean passes) representative, or did
   both probes fall in its comfort zone? Larger, multi-file tasks would tell us —
   and would also test the orientation cost.
2. Is the confirmation gate the real blocker for agentic use, or is it fine
   because a human is watching anyway? This changes recommendation 4 completely.
3. Should context work (2–4) precede any new tool? The measurements say yes.
4. ~~Does the model need glob?~~ **Closed by probe 3: no.** It used one
   auto-approved `find` and finished in 10 calls.

## Sources for the harness survey

Claude Code: <https://code.claude.com/docs/en/tools-reference> ·
Gemini CLI: <https://github.com/google-gemini/gemini-cli/blob/main/docs/reference/tools.md> ·
Cursor: <https://cursor.com/docs/agent/overview> ·
Codex tool specs (source-derived; OpenAI publishes no tool reference) ·
opencode and DeepSeek Harness: read from the installed code on this machine.
The open-source survey's per-project source files are listed in its section.
