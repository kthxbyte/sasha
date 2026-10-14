# Sasha — Local-26B Simplification Audit

**Date:** 2026-10-11 (revised after live testing)
**Scope:** `/home/salvador/Code/sasha` at `bceed3b`, clean tree, 764/764 tests green.
**Question:** which of Sasha's features are actually necessary when the backend LLM is a
local 26B-class model, and what should be cut to simplify the system?

**Method (revised):** two passes.

1. *Reading pass* — every module under `src/sasha/`, `PLAN.md`, `README.md`,
   `ISSUE-TRACKING.md`, the v1 design spec, `scripts/`, and the test tree; full suite run;
   test suite mapped to features. Model-behaviour claims in this pass came from the repo's own
   logged incidents.
2. *Live pass* — probes against the running endpoint at `192.168.8.253:11434/v1` driving the
   real `ChatClient`, the real `agent.run_consult` loop, and the real REPL under a pty.

**The live pass overturned several of the reading pass's central claims.** Corrections are in
§0 and are marked **[measured]** throughout. Everything unmarked is inference or reading.

---

## 0.0 Status — what has actually landed

Updated 2026-10-12. Eleven commits on top of the `2c98283` this audit was
written against:

| Commit | What |
|---|---|
| `bceed3b` | Local-backend hardening: LLM request lock, loud tool-call degradation, Ctrl+C at the confirmation gate (ISSUE-061), `[limits]` self-install (ISSUE-052) |
| `ada582e` | Compact one-line reasoning display by default, `--reasoning full` for the old streaming mode |
| `e477d78` | Crash safety net: the executor cleanup fix (real bug — see §0.4-1), `guard.py` bundles + circuit breaker, in-process recovery, `state_dir` fallback |
| `36a34b0` | `sasha repair` + `repair_profile` (frontier model for self-repair) |
| `18e75e8` | The three debts: `local` port 11441→11434, ISSUE-060 closed as stale, the request lock replaced by a **foreground-priority gate** |
| `c594295` | **§4.2 done** — trigger-economy inversion: every failed command consults; `mutes.py`, `Decision.OFFER`, `command_word`, the mute builtins and the offer vocabulary deleted |
| `2c87fca` | Version single-sourced (three copies → one); the crash net now points at `sasha repair`; the `use_skill` description fixed a **measured** tool mis-selection; ISSUE-057 closed |
| `12d0cd7` | **§4.1 done** — directory recap deleted (1,423 deletions against 316 insertions), replaced by a 46-line free `git log -1` headline; ISSUE-055 and ISSUE-062 closed as moot |
| `803f979` | Version bumped to **0.6.0** (one file, because the version is single-sourced) |
| `21efc43` | **§4.3 done** — memory-bootstrap stage 2 deleted (226 insertions against 568 deletions); only the free mechanical commit replay remains; ISSUE-039 closed as moot |
| `c139544` | The now-dead background priority machinery removed from `llm.py` (279 deletions against 15 insertions); it existed only for the two background callers that §4.1 and §4.3 deleted |
| `e8b34d9` | README rot: six usage examples showed a prompt star that has not existed since Task 26.2, and `prompt`/`sasha_color`/`reasoning_color` were undocumented |

**Metrics, revised:**

| | Audit baseline | Peak (after §4.2) | Now |
|---|---|---|---|
| Source | 4,593 | 5,446 | **4,781** |
| Tests | 14,115 | 15,220 | **14,165** |
| `src` modules | 24 | 23 | **24** |
| Collected tests | 750 | 817 | **769** |
| Open issues | 12 | 11 | **7** |
| Full suite runtime | 68 s | 81 s | **69 s** |

**This session's net is now +188 source lines and +50 test lines** — against a
peak of +853 — so the audit's cuts have almost entirely paid for the session's
additions (crash safety net, `sasha repair`, compact reasoning display, the
foreground-priority work). Four commits removed substantially more than they
added (−663, −1,107, −342, −264 net); only small metadata, tracker and
documentation commits went the other way.

Cut-list status: **§4.1, §4.2 and §4.3 done**; §4.4 (flatten the skills
listing) and §4.8 (demote `tool_log`) remain. **§4.6 is closed as rejected by
usage** (see below). **§4.5 and §4.7 are withdrawn** — measurement showed the
`question` schema and long prompts are not the problem the reading pass
assumed. ISSUE-057 and ISSUE-062 are closed; the only
remaining known flake family is gone with the recap feature.

**§4.6 is now CLOSED — rejected by actual usage (2026-10-12).** Salvador
confirmed he regularly runs `less`, `git log` through a pager, `vim`, `htop`
and `whiptail` *inside* Sasha, precisely so he does not have to leave the shell
to check something. That is the whole reason the stderr PTY tee and
`_kb_relay_helper` exist, and it makes the audit's "switch stderr to a pipe"
recommendation wrong for this user. **Do not re-propose it.** The v1 design
spec's §12 rationale (stderr through a PTY so `dialog`/`whiptail` fd-swap idioms
work, `script(1)`-style) is vindicated by real usage; the complexity is
buying something. Note the crash-net work (`e477d78`) is what makes this
workflow safe: the executor's cleanup was previously skipped on an exception,
which would have left the terminal in raw mode if Sasha crashed mid-`vim`.

Also fixed since the audit (2026-10-12, `bceed3b..<blockA>`): the version
string had **three** copies (`pyproject.toml`, `__main__.py`, `sasha/__init__.py`)
with `--version` printing the hardcoded one, now single-sourced from
`__version__` via a `dynamic` pyproject declaration; and §0.4-3's tool
mis-selection (`use_skill(name="remember")`) is **fixed** — the `use_skill`
description now states that the name must come from the listed skills and that
it only reads. Re-running the live probe: scenario 4 now returns
`remember(note=...)` correctly, and both skill-selection scenarios still pass.

Two corrections to §0.2 above worth carrying forward: the request lock is no
longer FIFO (it is now background-yields-to-foreground, and background requests
are skipped entirely when the user is active), and the `local` default port is
fixed.

**One behavioural subtlety of the §4.1 replacement, verified live:** the git
headline is printed on the same condition the old direct-recap was, i.e. only
when `_check_place_shift()` did *not* already print the
`[sasha] project: <name> (N memories, last: ...)` line. So a repo Sasha has
memory for shows the project line and suppresses the git line; a repo with git
history but no Sasha memory yet shows the bootstrap notice and the git line; a
non-repo shows nothing. That avoids printing two orientation lines on one `cd`,
at the cost of the git line being invisible in the common steady-state case —
which is fine, because the project line already carries a "last:" gist.

---

## 0. Read this first: the live endpoint, measured

### 0.1 What is actually running

```
GET /v1/models  ->  1 model
  id         dealignai/Bonsai-2-27B-Ternary-CRACK-GGUF:Q2_0
  owned_by   llamacpp
  n_params   26,895,998,464        (26.9B — genuinely "26B class")
  n_ctx      65,536                n_ctx_train 262,144
  n_embd     5,120
  size       7.19 GB
  ftype      PQ2_0 - 2.13 bpw (group 128)
  capabilities ["completion"]

GET /props
  total_slots 4                    chat_format "Content-only"
  model_path  C:\hf-cache\...\Bonsai-2-27B-PQ2_0-CRACK.gguf   (a Windows host)
  reasoning_format "none"          reasoning_in_content false
  timings: prompt 26.6 ms/tok (37.6 tok/s), generation 27.1 ms/tok (36.9 tok/s)
```

This answers the audit's original open questions 1–3 outright. It is **llama.cpp**, not Ollama,
despite listening on Ollama's default port 11434. The model is a **2.13-bit-per-weight ternary
quant** of a 27B — about as compressed as a 26B-class model gets.

### 0.2 Corrections to the reading pass

| Reading-pass claim | Measured reality |
|---|---|
| **§0 "no tool call works at all"** — every tool inert, Sasha degrades to text-only advice | **WRONG.** Tool calling works cleanly. The model returns proper `delta.tool_calls` with valid JSON args. Verified across 12 prompts. |
| ISSUE-060's leaked `<｜tool▁calls▁begin｜>` markup | Does not reproduce. That issue describes a *different* server state (Ollama + `DeepSeek-Coder-V2-Lite`); the endpoint has since changed model and runtime. **ISSUE-060 is stale and should be closed.** |
| **C2** "the server serves roughly one request at a time… a hard block" | **WRONG.** `total_slots: 4`. Concurrency costs throughput (shared GPU), not correctness. |
| **C4** "~24,000 chars ≈ 6k tokens is over half a ~32k window; the full prompt is re-sent with no caching" | **PARTLY WRONG.** Context is **65,536**, so a full-size prompt is ~9% of the window. Prompt-prefix **caching is active** (`cached_tokens: 42` of 57 on a trivial request), so repeated prefixes are cheap. |
| **C4/§4.4** "long heterogeneous system prompts degrade a weak model's tool use" | **NOT SUPPORTED.** With a **23,059-char** system prompt (personality + two memory tiers + 25-skill listing), the model still selected a tool correctly and answered correctly. Cost was **latency only**: 14.6 s vs 6.1 s (2.4×). |
| **§4.6** "`read_file`/`list_dir`/`search_files` are redundant — the model doesn't use them" (citing ISSUE-041) | **WRONG.** Unprompted, the model chose `read_file` for "show me the contents" and `run_command wc -l` for "count the lines" — correct discrimination between overlapping tools. |
| **§4.4** "a 26B choosing the right skill from a description list is what it's weakest at" | **WRONG.** It picked `deploy-to-kubernetes` and, on near-miss wording, `rotate-signing-keys` — both correct from a 3-skill listing. |
| **§4.5** "`question`'s nested schema is the part that breaks" | **WRONG.** It produced a nested payload that passes the real `question.validate_shape()` (the gating check ISSUE-059 added precisely because gpt-oss:20b flattened it). |
| Server metadata as a diagnostic | **Misleading.** `/v1/models` reports `capabilities: ["completion"]` and `/props` reports `chat_format: "Content-only"` — yet tool calls work. Do not use either to conclude tools are dead. |

### 0.3 What the live pass confirmed

- **The full REPL works.** Under a pty against this endpoint: `??` consult streamed a correct
  answer; a nonexistent command auto-consulted on exit 127 and explained the error; a plain
  `echo` correctly did **not** consult. The trigger matrix behaves exactly as designed.
- **`write_file` emits exact content** (`"hello from sasha"`, byte-correct) — so the audit's
  one keep-recommendation in the tool registry holds, and for the stated reason (JSON string
  beats a shell heredoc).
- **The agent loop closes.** `run_consult` → tool call → result → final natural-language answer
  naming the right files, with 5 history turns.
- **`tool_log`'s failure path degrades gracefully** — on a read-only state dir it disabled
  itself with one line and stayed up.

### 0.4 What the live pass newly found

1. **Startup is not resilient to an unwritable state dir.** [`__main__.py:76`](src/sasha/__main__.py)
   does `state_dir.mkdir(parents=True, exist_ok=True)` unguarded. On a read-only `$HOME` Sasha
   dies with a raw `OSError` traceback before the first prompt — even though running shell
   commands, the core product, needs no state at all. `config.py` already degrades gracefully
   on a write failure ("couldn't create… using defaults"); the state dir should too.
   **Found by accident; reproducible; independent of model choice.**
2. **The shipped `local` profile points at the wrong port.** The built-in default is
   `http://192.168.8.253:11441/v1` ([config.py](src/sasha/config.py) `_DEFAULT_PROFILE_FIELDS_BY_NAME`,
   README, and asserted by two contract tests). The live server is **11434**. Out of the box,
   `sasha profile local` cannot reach the machine it is configured for.
3. **Tool mis-selection, not schema failure, is this model's real weakness.** Asked to remember
   a fact, it called `use_skill` with `name="remember"` — it mistook a *tool* for a *skill*.
   It also made one gratuitous `list_dir` call before selecting a skill. Nothing was executed
   (the call would have returned "no such skill: remember"), but it is a genuine failure mode
   and the one piece of evidence here that favours **fewer, less overlapping tools** — note the
   confusable pair is `remember`/`use_skill`, not the read tools.
4. **Reasoning text dominates output.** On this model `reasoning_content` is typically 3–4×
   longer than the final answer, and Sasha renders all of it (ISSUE-049's design). Useful, but
   it makes every response much noisier than the answer warrants — a UX decision worth
   revisiting now that reasoning is visible.

### 0.5 The one model-independent blocker still worth naming

**ISSUE-061** — Ctrl+C at the y/n/e confirmation gate propagating a traceback out of `main()` —
was real and is **fixed** in `bceed3b`, along with the request lock, loud tool-call
degradation, and `[limits]` self-install. Those four fixes stand; see §9.

---

## 1. Where the weight actually is

| Metric | Value |
|---|---|
| Runtime source | **4,593 LOC** across 24 modules |
| Tests | **14,115 LOC**, 66 files, 62 contract files, 703 test functions |
| Test : source ratio | **3.1 : 1** |
| Suite runtime | 72 s (764 passed) |
| Git history | 320 commits |
| Design + task specs | **95 files** in `docs/superpowers/specs/` |
| Plan | 65 tasks across 40 phases, all checked |
| Open issues | 12 (9 of them Low) |
| Process docs | `CLAUDE.md` 27 KB + `AGENTS.md` 3 KB |

The v1 design spec (§2) scoped **9 modules, 1 tool, 13 tasks**. The product is now 24 modules,
8 tools, and 65 tasks. Nothing in the v1 spec's "out of scope" list was respected: it excluded
MCP, subagents, multimodal, file-path completion, and job control beyond one foreground
command — Sasha ended up with bespoke file tools, a full bash-completion integration, and a
keystroke-relay helper process for pagers.

**LOC by concern:**

| Concern | Modules | LOC |
|---|---|---|
| Shell core (executor, repl, config, agent, session, llm, triggers, mutes, prompt, completion, personality, paste_join) | 12 | 2,690 |
| Tools + tool-adjacent (tools, safe_commands, question, skills) | 4 | 735 |
| Context/memory (memory, directory_recap) | 2 | 632 |
| Instrumentation (tool_log, llm_debug) | 2 | 208 |
| Terminal/TUI plumbing (activity_indicator, _kb_relay_helper) | 2 | 148 |
| `__main__.py` | 1 | 179 |
| **Total** | **24** | **4,593** |

**Test LOC by feature** (excerpt): memory 2,263 · core shell 3,936 · output sink 1,159 ·
activity indicator 1,066 · directory recap 984 · tool_log/llm_debug 844 · multi-tool registry
679 · `question` 676 · completion 533 · skills 528 · loop detection 313 · prompt templating 302 ·
safe_commands 294 · personality 200.

Two features — memory and directory recap — are **3,247 test lines (23%)** and **632 source
lines (14%)**.

---

## 2. What genuinely changes with a local 26B, after measurement

The reading pass produced six constraints. Measurement kept three, weakened two, and deleted
one. What survives:

**C1 — Tokens are free; latency and the GPU are the scarce resources. KEPT.** Still the
central inversion. The v1 trigger design rations *paid* tokens (design spec §12: auto-consult
only when the line had no other purpose; a mute store to stop offering). Locally the marginal
token is free, while a consult costs seconds and a *background* consult costs the foreground
one. Measured: ~37 tok/s, 6.1 s for a minimal prompt, 14.6 s for a 23k-char prompt.

**C2 — Concurrency costs throughput, not correctness. WEAKENED.** `total_slots: 4`, so three
simultaneous requests do not deadlock — they share the GPU and all get slower. Serializing
still buys determinism, but with an important caveat: a FIFO lock lets a slow *background*
request delay the user's foreground one. See §9.2 for the fix direction.

**C3 — Tool calling works, but selection is imperfect. REVISED.** The old claim ("unreliable,
fails silently") was wrong for this profile. The real residual risk is mis-selection between
overlapping tools (`remember` → `use_skill`) and occasional gratuitous calls. Malformed
tool-call JSON is still silently dropped by `llm.py` — now warned, see §9.1.

**C4 — Long prompts cost latency, not correctness. REVISED, mostly deleted.** 23k chars of
system prompt did **not** break tool use; it cost 2.4× latency. With `n_ctx 65536` and working
prefix caching, the "prompt tax" argument for cutting personality/memory/skills is much weaker
than the reading pass claimed. The latency argument survives; the capability argument does not.

**C5 — Long tool loops are a real cost. KEPT, with weaker evidence.** ISSUE-047/049 recorded
genuine repetition on `Ornith-35B` and `GLM-4.7-Flash-REAP-23B`. **Not observed on this model**
in 12 prompts, though it did make one gratuitous `list_dir`. Each round re-sends the prompt, so
`max_tool_turns` still matters for latency; the repetition guard is cheap insurance.

**C6 — DELETED.** The endpoint is now identified, measured, and reachable. It is llama.cpp,
11434, `Bonsai-2-27B-Ternary-CRACK-GGUF:Q2_0`, 26.9B params, 2.13 bpw, 64k context, 4 slots.

---

## 3. Verdicts

**KEEP** = necessary · **SIMPLIFY** = keep the capability, cut the machinery · **CUT** = remove ·
**DEFER** = resolve with evidence, not reasoning.

### 3.1 Core shell wrapper — KEEP (model-independent value)

| Feature | Verdict |
|---|---|
| `executor.py` — per-line `bash -c`, exit/signal capture, PTY stderr tee, fd-9 cwd/env side channel, job control (476 LOC) | **KEEP** — but see §4.6 |
| `triggers.py` (38 LOC) | **SIMPLIFY** — §4.2 |
| `session.py` (183 LOC) | **KEEP** — `[context:KEY]` is a verified fix (ISSUE-050) |
| `llm.py` (145 LOC) | **KEEP** — now locked + warns |
| `config.py` (432 LOC) | **SIMPLIFY** — 8 option groups; drop cut features' sections; **fix the `local` port** |
| `repl.py` (805 LOC) | **SIMPLIFY** — largest module; most growth is feature wiring |
| `prompt.py` (102 LOC) | **SIMPLIFY to a constant** — 302 test LOC for a cosmetic string |
| `completion.py` (108 LOC) | **KEEP** — pure shell UX, no LLM |
| `paste_join.py` (13 LOC) | **KEEP** — cheapest feature in the repo |
| `safe_commands.py` (94 LOC) | **KEEP** — measured: it is what makes the model's `run_command wc -l` choice frictionless |
| `mutes.py` (109 LOC) | **CUT** — §4.2 |
| `_kb_relay_helper.py` (86 LOC) | **CUT with the stderr PTY tee** — §4.6 |
| `activity_indicator.py` (62 LOC) | **KEEP, freeze** — more useful locally (long waits); but 1,066 test LOC for a blinking cursor is poor value |

### 3.2 Features that exist *for* weak models — KEEP

| Feature | Why |
|---|---|
| `[context:KEY]` convention + prompt anchor | Built because GLM-4.7 derailed on unmarked injected cwd (ISSUE-050); the follow-up chose a `user`-role marker because the local template hard-errors on mid-history `system` |
| `reasoning_content` streaming | This server emits it as a separate field (`reasoning_format: "none"`); measured — reasoning is 3–4× the answer length, so rendering it visibly matters |
| Repetition guard + split stop messages | Observed on other local models (ISSUE-047/049/063); cheap insurance here |
| Defensive tool-payload validation (`question.validate_shape`) | **Measured: it passes.** Keep — it is what distinguishes a valid payload from gpt-oss:20b's flattened one |
| `llm_debug.py` (59 LOC, off by default) | Highest diagnostic value per line in the codebase |

### 3.3 Feature verdicts after measurement

| Feature | Reading-pass verdict | **Measured verdict** |
|---|---|---|
| `read_file` / `list_dir` / `search_files` | CUT (redundant, unused) | **KEEP** — model uses them and discriminates correctly; the `run_command` allowlist is the redundancy, not the tools |
| `question` tool | DEFER → likely CUT | **KEEP** — produces valid nested payloads |
| `use_skill` | CUT | **KEEP** — selects correctly, including near-misses. (Reduce the *listing*, not the tool) |
| `remember` | KEEP | **KEEP**, but flag the `remember`/`use_skill` confusion |
| `write_file` | KEEP | **KEEP** — confirmed byte-exact |
| Directory recap | CUT | **CUT** — on complexity/value grounds, not model capability (§4.1) |
| Memory bootstrap stage 2 | CUT | **CUT** — hallucination-as-fact argument is model-independent (§4.3) |
| Personality | SIMPLIFY | **SIMPLIFY, weakly** — 23k chars didn't break behaviour; it costs latency only |
| Prompt templating | SIMPLIFY | **SIMPLIFY** — unchanged, cosmetic |
| `tool_log` | DEMOTE | **DEMOTE** — unchanged |
| Mute store + OFFER economy | CUT/invert | **CUT/invert** — C1 unchanged |
| `activity_indicator` | KEEP, freeze | **KEEP, freeze** |
| stderr PTY tee + relay helper | CUT the tee | **Unchanged** — model-independent complexity (§4.6) |

---

## 4. The cuts, with reasons

### 4.1 Directory recap — cut it

303 source LOC + **984 test LOC** to print **one line** when you `cd`. A five-constant heuristic
cascade plus an async LLM call, wired into the REPL at four points (repl.py:335-351, 348-351,
583-584, 608-609), re-checked on *every* command.

The model-capability objection is gone (it can write a recap line). The case now rests on:

- **Complexity/value:** the repo's most issue-dense feature — ISSUE-055 (fires one command
  late), ISSUE-056 (the design's own measurement found a **44% false-positive rate** for the
  rejected mtime heuristic; 6–32% for marker files in vendored trees).
- **Latency on a shared GPU (C1/C2):** a background request the user did not ask for,
  competing with the consult they did.
- **Redundancy:** the free `RECAP_DIRECT` git-commit line needs no LLM and no cascade.

**Replace with:** the `git log -1` line, or nothing.

### 4.2 The token economy — invert it, then delete the machinery

Unchanged by measurement, because it rests on C1 (free tokens) rather than model capability.
The trigger matrix, the OFFER state, and the mute store exist to spend *paid* tokens
conservatively.

With a free local model the policy should be: **consult on any non-zero exit**, no offer, no
mute. That deletes `mutes.py` and its JSON store, `Decision.OFFER` and the `_pending_offer` /
`_handle_pending_offer_ignore` paths, two builtins, a config limit, and the mute-completion
wiring. `triggers.decide` collapses to: signal or exit 0 → NOTHING, else AUTO_CONSULT.

Net **−109 src / −169 test LOC**, and Sasha gets strictly more useful.

### 4.3 Memory bootstrap (LLM stage 2) — cut, keep the free half

**Unchanged by measurement** — rests on an epistemic argument, not model capability. A 26B's
summary of git history is written to `semantic.jsonl` as `kind="bootstrap"` and injected into
every subsequent prompt *as fact*, up to 8,000 chars. A hallucination there is durable and
authoritative, and cannot be distinguished from something the user said. ISSUE-039 notes there
is no refresh path; the only remedy is `sasha forget`, which destroys all memory.

Keep the free, mechanical half: `git commit` capture, crash-signal capture, the `remember`
tool, and stage 1's replay of recent commit messages.

### 4.4 Skills — keep the tool, drop the listing machinery

**Reversed from the reading pass.** Skill *selection works* on this model, including near-miss
wording. What is still questionable is the 185-LOC discovery layer (marketplace/plugin/semver
walk, path tie-breaking, YAML block-scalar frontmatter — 528 test LOC) to produce a list of
`name: description` lines.

The measured cost is the *listing*, not the tool: an unbounded block prepended to every prompt.
**[inference]** Prefer a flat `~/.config/sasha/skills/<name>.md` convention and a listing capped
at N entries, over the Claude-Code marketplace tree. That deletes most of `skills.py` while
keeping `use_skill` and its measured value.

### 4.5 `question` — keep

**Reversed.** It emitted a nested payload that passes `question.validate_shape()`. The
676 test LOC are guarding a working path. Keep as-is.

### 4.6 The stderr PTY tee and the keystroke relay — the biggest fidelity-for-complexity trade

**Model-independent and unchanged.** The most intricate machinery in the repo:

- `pty.openpty()` for stderr on every command, win-size propagation (`TIOCGWINSZ`/`TIOCSWINSZ`),
  a `SIGWINCH` handler, a `select()` relay loop, ANSI/CSI/OSC stripping, tail-capped capture.
- `_kb_relay_helper.py` (86 LOC): a helper process launched per interactive command, sharing the
  target's foreground process group, reading fd 0 and writing into the capture PTY's master —
  because Sasha's own process is backgrounded via `tcsetpgrp` and a background process cannot
  read its controlling terminal.
- Plus task-20.1's `SIGTTIN`-ignore and stop-cause classification.

ISSUE-040 spans **three design documents and two task specs** and was misdiagnosed once
(Task 20.1 claimed a fix and did not address the mechanism).

**Recommendation unchanged:** capture stderr through a **pipe** instead of a PTY. A pipe yields
the same `stderr_text` for LLM context with none of the winsize/`SIGWINCH`/two-master/relay
machinery. stdout/`isatty(1)` is untouched, so `vim`/`htop`/`mc` on stdout still work. The cost
is `isatty(2) == False` — `whiptail`/`dialog` idioms and the pager-fd-2 convention. **[inference]**
For a local-model assistant shell that is a favourable trade.

### 4.7 Personality and prompt templating — simplify, but for latency, not correctness

**Weakened.** The reading pass argued personality (up to 8,000 chars) trades away
instruction-following. Measured: a 23k-char prompt kept tool use correct. So the argument is now
**latency only** (14.6 s vs 6.1 s on a fresh prefix). Still worth folding personality to a line
and the prompt template to a constant — 69 + 102 source LOC and 502 test LOC for cosmetics — but
this is housekeeping, not a correctness fix. Prefix caching means the cost is paid once per
prefix, not per turn.

### 4.8 `tool_log` — demote to opt-in

149 src + 844 test LOC, a compaction thread, a config knob, a builtin, and a documented
archive-your-log workflow — for a research instrument ("Requested by Salvador to study his own
tool-usage patterns later", ISSUE-042). Gate it behind `[tool_log] enabled = false` and drop
auto-compaction from the hot path. (Measured: its failure path already degrades gracefully.)

### 4.9 Things to fix regardless of any cut

1. **`state_dir.mkdir` crash** (§0.4-1) — wrap it; degrade to a no-state session with one notice.
2. **`local` profile port** (§0.4-2) — 11441 → 11434 in `config.py` + README, amending the two
   contract tests that assert 11441. **Tier 1 contract change.**
3. **ISSUE-060 is stale** — re-measured as not reproducing; close it and record the endpoint
   facts in §0.1 so the next reader isn't misled.
4. **Tool mis-selection** (§0.4-3) — the `remember`/`use_skill` confusion argues for sharpening
   those two descriptions (or giving `remember` a clearer name), not for deleting tools.
5. **Keep the two flaky test families (ISSUE-057, ISSUE-062)** on the list — both assert exact
   equality between two independently-timed reads of live state.

---

## 5. The harness axis

Unchanged by the live pass, because it is about the process, not the model.

If the intent is *"which features are necessary so a local 26B can implement this project"*: the
three-tier protocol generates **95 spec documents, 65 tasks, and 14,115 lines of contract tests
for a 4,593-LOC product**. Every feature multiplies that surface roughly 1:1 (the `question` tool
is 676 test lines; memory 2,263; directory recap 984). Product scope, not task granularity, is
the variable — cutting a feature deletes its spec, tasks, contract tests, and escalation surface
together.

`tests/impl/` was **empty** until `bceed3b`; the suite was 100% contract tests, with the
"immutable contract" enforced by PROTECTED_PATHS rather than by any test of the harness itself.
No test exercises `verify.sh`, `run-phase.sh`, or the trailer grammar.

---

## 6. Concrete target: "sasha-local" (revised)

**Kept (unchanged):** `__main__`, `repl`, `config` (trimmed), `session`, `llm` (+lock),
`triggers` (2-line rule), `agent`, `tools`, `safe_commands`, `llm_debug`, `completion`,
`paste_join`, `question`, `skills` (flattened), `activity_indicator`, `personality` (a line),
`prompt` (a constant).

**Deleted:** `mutes`, `directory_recap`, `_kb_relay_helper` (with the PTY tee), `tool_log`
(optional), and the memory-bootstrap stage-2 path.

| | Now | Target |
|---|---|---|
| Source LOC | 4,593 | **~3,300–3,600** |
| Modules | 24 | **~21–22** |
| Tools | 8 | **8** (unchanged — measurement vindicated them) |
| Test LOC | 14,115 | **~10,500–11,500** |
| Specs | 95 | **~70** |
| Background threads | 4 | **1** |
| Boot-time LLM requests | up to 2 | **0** |

The revision matters: the reading pass proposed deleting three of eight tools and two whole
subsystems on *capability* grounds that measurement did not support. The defensible cuts are the
ones motivated by **complexity, latency, and epistemic risk** — directory recap, the token
economy, the bootstrap summariser, the PTY/relay stack — not by underestimating the model.

---

## 7. Sequencing (revised)

1. **Fix the two concrete bugs found by running it** — `state_dir.mkdir`, and the `local` port
   (contract change). Both are small and both block or mislead real use today.
2. **Close ISSUE-060 as stale** and record §0.1's endpoint facts.
3. **Invert the trigger economy** (§4.2) — deletes a module and two builtins, and is a behaviour
   *improvement* locally.
4. **Cut directory recap** (§4.1) — deletes a module and 984 test lines.
5. **Cut memory bootstrap stage 2** (§4.3).
6. **Demote `tool_log`** (§4.8); flatten the skills listing (§4.4).
7. **Decide the stderr PTY tee / relay helper** (§4.6) — biggest single complexity reduction.

---

## 8. Open questions

Answered by the live pass: endpoint/model/quant/context (1), slot count (2), prompt caching (3).

Still open:

1. **Is 11434 the durable address, or was 11441 correct for an earlier server?** The shipped
   default should match reality; if the server moves, a hardcoded LAN IP in a *default* is the
   wrong shape anyway — consider an env var (`SASHA_LOCAL_BASE_URL`) with the IP as fallback.
2. **How does `max_tool_turns = 10` interact with this model's multi-step behaviour in
   practice?** ISSUE-063 recorded hitting it in extended sessions. Measurable from
   `tool_log.jsonl` once logging is on by default.
3. **Does reasoning-length vary with prompt size?** 401 reasoning chars on a minimal prompt;
   worth measuring whether the 23k-char prompt also inflates reasoning, which would compound the
   latency finding.
4. **Which features do you actually use?** `tool_log.jsonl` records `use_skill`/`question`
   calls with verdicts. One script would settle the remaining keep/cut questions empirically —
   which is exactly the lesson of this revision.
