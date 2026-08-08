# ISSUE-064 — Sasha skill loading (stage 1: load-only, no marketplace)

**Status:** Draft for review
**Date:** 2026-08-08

## 1. Mission

Give sasha's LLM a `use_skill` tool that loads a named, pre-existing
markdown playbook into the conversation — the same "packaged procedure"
concept this assistant's own harness (Claude Code) uses via `SKILL.md`
files, sized and shaped like sasha's existing `remember`/`question`
tools. Popular skill libraries (`ponytail`, `superpowers`) are almost
entirely `SKILL.md` prose with a frontmatter description and no
dependency on harness-specific machinery — they fit sasha's actual tool
set (`run_command`/`write_file`/`remember`/`question`) effortlessly, and
a `brainstorming`-style skill's own "ask one thing at a time" instruction
naturally composes with the `question` tool sasha just shipped (Phase 57,
ISSUE-059).

## 2. Scope

### In scope (v1 / stage 1):
- A sasha-owned skills directory, structured identically to Claude Code's
  verified plugin-cache shape (§4.1), never reading Claude Code's actual
  `~/.claude/...` files.
- Startup-time discovery/listing of installed skills in the system
  prompt.
- A `use_skill(name, args="")` tool that loads one skill's full file
  content into the conversation as a one-shot tool result.
- A durable signal for what stage-2 machinery a loaded skill's own
  instructions actually reach for and don't find, via the existing
  `tool_log`.

### Out of scope / non-goals (this design):
- **Stage 2 — Agent/Artifact/TaskCreate/ToolSearch-equivalent runtime.**
  Skills that lean on those mechanisms load fine as prose; instructions
  referencing them just go unfulfilled today (the model's own attempt to
  call them lands in the existing "Unknown tool" path, now durably
  logged — see §4.5). A much larger, separate evaluation for later,
  explicitly deferred per ISSUE-064.
- **Any install/marketplace-fetch mechanism.** Populating the skills
  directory (cloning a repo, hand-copying, symlinking) is manual or
  `run_command`-assisted, not sasha's job. `git clone <repo> ~/.config/
  sasha/skills/<name>` already works today with zero new code. A real
  marketplace-manifest-aware installer is a larger, security-sensitive
  feature (auto-cloning and then having an LLM follow arbitrary fetched
  instructions unsupervised is a real trust question) deserving its own
  future design, not bundled here.
- No project-local skills — user-global only (`~/.config/sasha/skills/`),
  matching `PERSONALITY.md`'s single-shared-file model, not memory's
  project+general layering. Skills are generic procedures, not
  project-specific knowledge.
- No `config.toml` toggle — purely additive, no executor side effect,
  matches `read_file`/`list_dir`/`search_files`/`remember`/`question`.
- No cap on the total listing size — accepted debt; log a fresh issue if
  a real installation ever makes this a problem.

## 3. Key contracts & assumptions

1. **Sasha never reads Claude Code's actual files.** The skills directory
   is fully independent; only its *shape* replicates Claude Code's
   verified plugin-cache layout, so real marketplace content drops in
   unmodified once a human copies or clones it into place.
2. **The replicated shape targets the verified cache layout specifically**
   (`marketplace/plugin/version/skills/name/SKILL.md`, directly observed
   in this session's own skill-loading paths) — not a guessed, unverified
   raw-marketplace-repo layout, which may differ and hasn't been
   confirmed.
3. **A skill's identity is its resolved name**: frontmatter `name:` if
   present and non-empty, else the skill's directory name. This single
   resolved name is what both the system-prompt listing and `use_skill`'s
   lookup use — no separate "display name" vs "lookup key" split.
4. **Discovery is a one-time startup scan**, not per-request — mirrors
   `personality.py`'s "changes take effect next start" convention.
   `use_skill` itself still re-scans fresh on every call (cheap, stateless,
   matches `read_file`/`list_dir`/`search_files`'s existing per-call I/O
   style) so a skill added mid-session is still loadable even though it
   won't appear in that session's listing until restart.
5. **A skill needs both `name:` and `description:` to be advertised** in
   the listing, but only needs to exist as a readable file to be
   *loadable* via `use_skill` — an undescribed skill is usable, just not
   discoverable by browsing the listing.
6. **The missing-machinery signal reuses `tool_log`, not a new log.** The
   existing "Unknown tool: X" dispatch branch (`agent.py`) gains a
   `tool_log.log_call(..., verdict="unknown_tool")` call — the exact
   fields and machinery (append-only JSONL, XDG path, compaction) already
   exist and are already tested.

## 4. Architecture

### 4.1 Directory layout (verified shape)

```
~/.config/sasha/skills/
  <marketplace>/
    <plugin>/
      <version>/          ← e.g. "6.2.0"; highest semver wins per plugin
        skills/
          <skill-name>/
            SKILL.md       ← frontmatter (name:, description:) + body
```

Directly observed and verified in this session (Claude Code's own
plugin-cache paths, not a design guess):
```
~/.claude/plugins/cache/superpowers-marketplace/superpowers/6.2.0/skills/brainstorming/SKILL.md
~/.claude/plugins/cache/superpowers-marketplace/superpowers/6.2.0/skills/writing-plans/SKILL.md
```
Sasha's own `~/.config/sasha/skills/` replicates this exact nesting —
independently, never reading the `~/.claude/...` paths themselves.

### 4.2 `src/sasha/skills.py` (new module)

```python
_MAX_SKILL_CHARS = 40000  # matches tools.py's _READ_FILE_MAX_CHARS

USE_SKILL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "use_skill",
        "description": (
            "Load a named skill into context — a packaged set of "
            "instructions for a specific kind of task. Use one when the "
            "current task matches a listed skill's description."),
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "args": {"type": "string"},
            },
            "required": ["name"],
            "additionalProperties": False,
        },
    },
}


def _skills_root() -> pathlib.Path:
    """XDG-based, mirrors personality.py's _personality_path()."""


def _parse_frontmatter(text: str) -> dict | None:
    """Splits on a leading '---\\n...\\n---' block, parses each line as
    key: value (first colon only). Returns None if the block is missing
    or malformed (no closing '---')."""


def _discover(root: pathlib.Path | None = None) -> dict[str, pathlib.Path]:
    """Walks <root>/*/*/*/skills/*/SKILL.md. Within one plugin folder,
    keeps only the highest-semver version subfolder, comparing dot-
    separated numeric segments as a tuple of ints (e.g. "6.2.0" <
    "6.10.0"); a version folder name that isn't parseable this way sorts
    last (lowest precedence) rather than raising. Resolves each skill's
    identity per §3.3 (frontmatter name: else directory name). Returns
    {resolved_name: path_to_SKILL.md}. On a name collision across
    different plugins/marketplaces, the alphabetically-first full path
    wins — deterministic, documented as a low-likelihood edge case."""


def build_skills_listing() -> str:
    """Runs _discover(), parses frontmatter for each result, keeps only
    entries with both a non-empty name: and description:. Returns
    "Available skills:\\n- name: description\\n...\\n\\nUse the
    `use_skill` tool to load one when it applies." — or "" if none
    qualify."""


def load_skill(name: str, args: str = "") -> str | None:
    """Fresh _discover() call, looks up name. Returns None if not found.
    Otherwise reads the full SKILL.md file verbatim (frontmatter
    included — matches what a real skill load actually shows), truncated
    at _MAX_SKILL_CHARS with the same "... [truncated at N characters]"
    suffix read_file uses, then appends "\\n\\nARGUMENTS: {args}" if args
    is non-empty."""
```

### 4.3 `tools.py` REGISTRY entry

```python
def _use_skill_handler(args: dict, executor: Executor) -> ToolResult:
    name = args.get("name", "")
    content = skills.load_skill(name, args.get("args", ""))
    if content is None:
        return ToolResult(f"no such skill: {name}")
    return ToolResult(content)


"use_skill": Tool(
    name="use_skill",
    schema=skills.USE_SKILL_SCHEMA,
    requires_confirmation=False,
    confirmation_summary=None,
    handler=_use_skill_handler,
    repetition_key=lambda args: args,
),
```
Auto-approved, no confirmation gate — matches `read_file`/`list_dir`/
`search_files` (no executor side effect to gate).

### 4.4 `session.py` — system prompt composition

`Session.__init__` gains a `skills_listing: str = ""` parameter (a
one-time value, not a live setter — see §3.4/decisions log). Appended as
its own optional block in `_build_system_prompt()`'s `parts` list,
alongside personality/memory, omitted entirely when empty:

```python
if self._skills_listing:
    parts.append(self._skills_listing)
```

### 4.5 `agent.py` — the missing-machinery signal

The existing unknown-tool branch (currently a bare `session.add_tool_result`
+ `continue`, no logging at all) gains one line:

```python
if tool is None:
    session.add_tool_result(call.id, f"Unknown tool: {call.name}")
    tool_log.log_call(tool_name=call.name, call_id=call.id, arguments=call.arguments,
                       executed_command=None, cwd=executor.cwd, verdict="unknown_tool",
                       executed=False, result_text=None, result_omitted_reason=None)
    continue
```
Fires for *any* unrecognized tool name, not just an `Agent`/`Artifact`/
`TaskCreate`/`ToolSearch` allow-list — the point is discovering the
unexpected, not confirming a guess.

### 4.6 `__main__.py` wiring

One new call alongside the existing `personality.load_personality()`:
```python
skills_listing = skills.build_skills_listing()
...
session = Session(limits.history_tokens, personality=personality_result.text,
                  skills_listing=skills_listing)
```

## 5. Testing strategy

Following `test_task_57_1_question_core.py`'s established harness style
(`SpyExecutor`, `ScriptedConfirmer`/`RefusingConfirmer`, `StubServer`):

1. Frontmatter parsing: valid, missing, malformed (no closing `---`).
2. Directory scanning (`tmp_path`-based fake root, monkeypatched
   `XDG_CONFIG_HOME`): nested discovery, latest-semver-wins per plugin,
   name-resolution fallback to directory name.
3. Listing: empty when absent/empty; correct formatting when populated;
   a described-less skill is loadable but not listed.
4. `use_skill`: exact schema match; successful load returns the file
   verbatim; `args` appended/omitted correctly; unknown name's exact
   error string; truncation at the cap.
5. Agent-loop dispatch via `StubServer`/`run_consult`: auto-approved,
   schema included in the tools sent to the LLM.
6. Unknown-tool logging: a new regression guard — an unrecognized tool
   name now produces a `tool_log` entry with `verdict="unknown_tool"`,
   `executed=False`, and the attempted arguments (today, nothing is
   logged for this path at all).
7. `session.py` composition: `skills_listing` present/positioned
   correctly when non-empty, byte-for-byte absent when empty (matching
   this project's existing exact-string system-prompt test convention).

## 6. Decisions log

- **Decision:** Sasha owns a fully independent skills directory,
  structured identically to Claude Code's verified cache shape, never
  reading `~/.claude/...` itself.
  **Rationale:** format-compatibility benefit (real marketplace content
  drops in unmodified once copied) with zero coupling to a directory
  sasha doesn't control.
- **Decision:** Target the verified cache shape, not a guessed raw-repo
  layout.
  **Rationale:** the cache shape is directly observed in this session;
  an un-installed marketplace repo's own internal layout is unverified.
- **Decision:** `use_skill` re-scans fresh on every call; no cached
  index.
  **Rationale:** matches `read_file`/`list_dir`/`search_files`'s
  existing stateless-per-call I/O; skill libraries are small.
- **Decision:** `skills_listing` is a one-time `Session` constructor
  value, not live-updatable.
  **Rationale:** matches `personality`'s "changes take effect next
  start" convention; no in-session event should ever change what's
  installed.
- **Decision:** No cap on listing size for v1.
  **Rationale:** YAGNI — speculative problem, better logged fresh if it
  actually happens.
- **Decision:** Reuse `tool_log.log_call` for the missing-machinery
  signal instead of a bespoke log.
  **Rationale:** the exact fields and machinery already exist and are
  already tested.
- **Decision:** No install/marketplace-fetch mechanism in this design.
  **Rationale:** `run_command` already covers population today; a real
  installer is a larger, security-sensitive feature for its own future
  design.
