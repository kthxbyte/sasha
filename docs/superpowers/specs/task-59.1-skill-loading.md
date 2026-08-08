# Task 59.1: sasha skill loading — core module, registry, wiring

## Authoritative contract
`docs/superpowers/specs/2026-08-08-issue-064-skill-loading-design.md` §3
(key contracts), §4.1 (directory layout), §4.2 (`src/sasha/skills.py`),
§4.3 (`tools.py` REGISTRY entry), §4.4 (`session.py` composition), §4.5
(`agent.py` missing-machinery signal), §4.6 (`__main__.py` wiring) is the
complete contract for this task. This is the whole of stage 1 — there is
no follow-up task; §2's "Out of scope" list (stage 2 runtime, any
install/marketplace-fetch mechanism, project-local skills, a
`config.toml` toggle, a listing-size cap) stays out of scope permanently
for this design, not deferred to a later task.

## What already exists to bind, not reimplement
- `src/sasha/personality.py`: `_personality_path()` (`personality.py:34-38`)
  — the exact `XDG_CONFIG_HOME`-or-`~/.config` pattern `_skills_root()`
  mirrors (§3 note below covers the one difference: the skills root is a
  directory, not a file, and this task does not self-install it — an
  absent root is simply "no skills found", never created).
- `src/sasha/tools.py`: `Tool` dataclass (`name`, `schema`,
  `requires_confirmation`, `confirmation_summary`, `handler`,
  `repetition_key`, `announce_when_auto_approved`), `ToolResult`
  dataclass, `REGISTRY: dict[str, Tool]` (currently 7 entries ending with
  `"question"` at `tools.py:328-337`) — unchanged in shape; this task
  adds one new entry and one new handler function, does not modify any
  existing entry, handler, or the dataclasses themselves.
- `src/sasha/agent.py`: `run_consult()`'s existing unknown-tool branch
  (`agent.py:74-77`, exactly `if tool is None: session.add_tool_result(...);
  continue`) — this task adds one `tool_log.log_call(...)` line inside
  that branch, before the existing `continue`. Nothing else in
  `run_consult()` changes. `tool_log` is already imported
  (`agent.py:6`) — no new import needed here.
- `src/sasha/tool_log.py`: `log_call(*, tool_name, call_id, arguments,
  executed_command, cwd, verdict, executed, result_text,
  result_omitted_reason)` — call it exactly as `agent.py` already calls
  it for every other tool (see `agent.py:98-104`, the `question` branch,
  for the closest existing call site to copy the keyword shape from); no
  changes to `tool_log.py` itself.
- `src/sasha/session.py`: `Session.__init__` (`session.py:31-39`) and
  `Session._build_system_prompt()` (`session.py:41-77`) — this task adds
  one new constructor parameter and one new conditional block to the
  `parts` list; no other change to `Session`.
- `src/sasha/__main__.py`: the `personality.load_personality()` /
  `Session(...)` call site (`__main__.py:86-89`) — this task adds one new
  import, one new function call, and one new keyword argument; no other
  change to `__main__.py`.
- **`tests/contract/test_task_16_1_tools.py`,
  `tests/contract/test_task_16_2_agent_registry.py`,
  `tests/contract/test_task_57_1_question_core.py`, and
  `tests/contract/test_task_22_1_tool_call_logging.py` were already
  amended by Tier 1 in this same spec commit** (verified in a scratch
  worktree — see "Verified fix sketch" below): the first three pin the
  exact `REGISTRY` tool set / exact tool-schema set sent to the LLM
  byte-for-byte and now include `"use_skill"`; the fourth,
  `test_task_22_1_tool_call_logging.py::test_ac10_unknown_tool_not_logged`,
  pinned the *old* "unknown tools are never logged" behavior that this
  design deliberately reverses (§4.5) — it is renamed
  `test_ac10_unknown_tool_logged_as_unknown_tool_verdict` and now asserts
  the new logged-entry shape. You do not need to touch any of these four
  files — they are already correct for this task's implementation.

## What's new

1. **`src/sasha/skills.py`** (new file) — schema constant, directory
   discovery, listing, and load. Full implementation, verified working
   as-is in a scratch worktree (see "Verified fix sketch" below):

   ```python
   from __future__ import annotations

   import os
   import pathlib

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
       xdg = os.environ.get("XDG_CONFIG_HOME", "")
       if xdg:
           return pathlib.Path(xdg) / "sasha" / "skills"
       return pathlib.Path.home() / ".config" / "sasha" / "skills"


   def _parse_frontmatter(text: str) -> dict | None:
       lines = text.splitlines()
       if not lines or lines[0].strip() != "---":
           return None
       result: dict[str, str] = {}
       closed = False
       for line in lines[1:]:
           if line.strip() == "---":
               closed = True
               break
           if ":" not in line:
               continue
           key, _, value = line.partition(":")
           result[key.strip()] = value.strip()
       if not closed:
           return None
       return result


   def _version_sort_key(version: str) -> tuple:
       try:
           nums = tuple(int(p) for p in version.split("."))
       except ValueError:
           return (0,)
       return (1, nums)


   def _discover(root: pathlib.Path | None = None) -> dict[str, pathlib.Path]:
       if root is None:
           root = _skills_root()
       if not root.is_dir():
           return {}

       plugin_versions: dict[tuple[str, str], list[pathlib.Path]] = {}
       for marketplace_dir in sorted(p for p in root.iterdir() if p.is_dir()):
           for plugin_dir in sorted(p for p in marketplace_dir.iterdir() if p.is_dir()):
               for version_dir in sorted(p for p in plugin_dir.iterdir() if p.is_dir()):
                   if (version_dir / "skills").is_dir():
                       plugin_versions.setdefault(
                           (marketplace_dir.name, plugin_dir.name), []).append(version_dir)

       result: dict[str, pathlib.Path] = {}
       for version_dirs in plugin_versions.values():
           best = max(version_dirs, key=lambda d: _version_sort_key(d.name))
           skills_dir = best / "skills"
           for skill_dir in sorted(p for p in skills_dir.iterdir() if p.is_dir()):
               skill_md = skill_dir / "SKILL.md"
               if not skill_md.is_file():
                   continue
               try:
                   text = skill_md.read_text()
               except OSError:
                   continue
               frontmatter = _parse_frontmatter(text)
               name = None
               if frontmatter is not None:
                   name = frontmatter.get("name")
               if not name:
                   name = skill_dir.name
               if name not in result or str(skill_md) < str(result[name]):
                   result[name] = skill_md
       return result


   def build_skills_listing() -> str:
       discovered = _discover()
       entries = []
       for name in sorted(discovered):
           try:
               text = discovered[name].read_text()
           except OSError:
               continue
           frontmatter = _parse_frontmatter(text)
           if frontmatter is None:
               continue
           fm_name = frontmatter.get("name", "")
           description = frontmatter.get("description", "")
           if not fm_name or not description:
               continue
           entries.append((name, description))
       if not entries:
           return ""
       lines = "\n".join(f"- {name}: {description}" for name, description in entries)
       return (f"Available skills:\n{lines}\n\n"
               f"Use the `use_skill` tool to load one when it applies.")


   def load_skill(name: str, args: str = "") -> str | None:
       discovered = _discover()
       path = discovered.get(name)
       if path is None:
           return None
       try:
           text = path.read_text()
       except OSError:
           return None
       if len(text) > _MAX_SKILL_CHARS:
           text = text[:_MAX_SKILL_CHARS] + f"\n... [truncated at {_MAX_SKILL_CHARS} characters]"
       if args:
           text += f"\n\nARGUMENTS: {args}"
       return text
   ```

   Notes binding this to the design's decisions log:
   - `_discover()`'s per-plugin version selection uses `max()` with
     `_version_sort_key`: parseable dot-separated-int versions sort by
     their numeric tuple (so `"6.10.0"` correctly beats `"6.2.0"` —
     lexical string comparison would get this backwards); any
     unparseable version name (e.g. `"dev"`) gets the sentinel key
     `(0,)`, which is always less than every parseable key `(1, ...)`,
     guaranteeing it never wins over a real version.
   - Name-collision tie-break (§3.3, "the alphabetically-first full path
     wins") is implemented by the `if name not in result or str(skill_md)
     < str(result[name])` comparison — order-independent: whichever
     candidate has the lexicographically smaller full path string always
     ends up stored, regardless of filesystem iteration order.
   - `build_skills_listing()` re-parses each discovered file's
     frontmatter independently (cheap, matches `_discover()`'s own
     already-stateless style) rather than having `_discover()` carry
     frontmatter dicts alongside paths — keeps `_discover()`'s return
     type exactly `dict[str, Path]` per the design.
   - `load_skill()`'s truncate-then-append order matters: a skill body
     long enough to hit `_MAX_SKILL_CHARS` gets truncated first, then
     `ARGUMENTS:` is appended after the truncation suffix — matches
     `read_file`'s truncation convention (`tools.py:76-80`) and the
     design's stated order ("truncated ... then appends").
   - `_skills_root()` never creates the directory (unlike
     `personality.py`'s self-installing `_personality_path()` sibling) —
     an absent root is simply empty discovery, per §2's non-goals (no
     install mechanism in this design).

2. **`src/sasha/tools.py`** — one new import, one new handler function,
   one new `REGISTRY` entry:
   ```python
   from sasha import skills
   ```
   (added alongside the existing `from sasha import safe_commands`,
   before `from sasha.executor import Executor`)
   ```python
   def _use_skill_handler(args: dict, executor: Executor) -> ToolResult:
       name = args.get("name", "")
       content = skills.load_skill(name, args.get("args", ""))
       if content is None:
           return ToolResult(f"no such skill: {name}")
       return ToolResult(content)
   ```
   (added immediately after `_remember_handler`, before the `REGISTRY =`
   dict opens)
   ```python
   "use_skill": Tool(
       name="use_skill",
       schema=skills.USE_SKILL_SCHEMA,
       requires_confirmation=False,
       confirmation_summary=None,
       handler=_use_skill_handler,
       repetition_key=lambda args: args,
   ),
   ```
   (added as the new last entry in `REGISTRY`, immediately after
   `"question"`'s entry). Unlike `question`, `use_skill` is a completely
   ordinary tool — no dedicated `agent.py` dispatch branch, no special
   casing. It runs through the same `tool.handler(final_arguments,
   executor)` path as `read_file`/`list_dir`/`search_files`.

3. **`src/sasha/agent.py`** — one new call inside the existing
   unknown-tool branch (`agent.py:74-77`):
   ```python
   tool = REGISTRY.get(call.name)
   if tool is None:
       session.add_tool_result(call.id, f"Unknown tool: {call.name}")
       tool_log.log_call(
           tool_name=call.name, call_id=call.id, arguments=call.arguments,
           executed_command=None, cwd=executor.cwd, verdict="unknown_tool",
           executed=False, result_text=None, result_omitted_reason=None)
       continue
   ```
   This fires for **any** unrecognized tool name — not just an
   `Agent`/`Artifact`/`TaskCreate`/`ToolSearch`-style allow-list — the
   point (§4.5) is discovering whatever the model actually reaches for
   that sasha doesn't have, not confirming a guess about what it might
   reach for.

4. **`src/sasha/session.py`** — one new constructor parameter, one new
   `_build_system_prompt()` block:
   ```python
   def __init__(self, history_token_budget: int = 12000,
                personality: str = "", skills_listing: str = "") -> None:
       self.history_token_budget = history_token_budget
       self._personality = personality
       self._skills_listing = skills_listing
       self._general_memory_block: str = ""
       self._project_memory_block: str = ""
       self.history: list[dict[str, Any]] = []
       self.notes: list[NoteCommand] = []
       self._last_told_cwd: str | None = None
   ```
   `skills_listing` is appended as the new third parameter (after
   `personality`) so every existing positional call site
   (`Session(limits.history_tokens, ...)` in `__main__.py`, `Session()`
   everywhere in tests) keeps working unmodified.

   In `_build_system_prompt()`, insert the new block immediately after
   the existing `_project_memory_block` block and before `parts.append(functional)`:
   ```python
       if self._project_memory_block:
           parts.append(
               f"What you remember about this project so far:\n"
               f"{self._project_memory_block}")
       if self._skills_listing:
           parts.append(self._skills_listing)
       parts.append(functional)
       return "\n\n".join(parts)
   ```
   This exact position is a Tier 1 decision (the design's §4.4 says only
   "alongside personality/memory, omitted entirely when empty" — this
   spec pins the precise slot): grouped with the other identity/context
   blocks, before the fixed functional-behavior block. When
   `skills_listing` is `""` (the default, and the only case for any
   session with no installed skills), `_build_system_prompt()`'s output
   is byte-for-byte identical to today's — this is exactly what makes
   the four already-amended test files' *other*, unrelated assertions
   (the ones that don't mention `use_skill`/skills at all) continue to
   pass untouched.

5. **`src/sasha/__main__.py`** — one new import, one new call, one new
   keyword argument:
   ```python
   from sasha import skills
   ```
   (added alongside the existing `from sasha import prompt as prompt_mod`,
   before `from sasha import tool_log`)
   ```python
       personality_result = personality.load_personality()
       skills_listing = skills.build_skills_listing()

       session = Session(limits.history_tokens,
                         personality=personality_result.text,
                         skills_listing=skills_listing)
   ```

## Constraints
- Do not touch `src/sasha/repl.py` — no terminal-rendering component
  exists for skills in stage 1; `use_skill`'s result is plain tool-result
  text like `read_file`, nothing bespoke to render.
- Do not implement any install/marketplace-fetch mechanism — populating
  `~/.config/sasha/skills/` is manual/`run_command`-assisted, entirely
  out of this task's and this design's scope (§2 "Out of scope").
- Do not add a `config.toml` toggle for skills — the design explicitly
  rejects this (§2, Decisions log).
- Do not add a cap on listing size — accepted debt per the design (§2,
  Decisions log).
- Do not change `Tool`, `ToolResult`, `Confirmer`, `Questioner`, or any
  existing `REGISTRY` entry other than adding the new `use_skill` one.
- `_skills_root()` must never create the skills directory — an absent
  root means zero discovered skills, not an error and not a self-install.
- `_discover()`, `build_skills_listing()`, and `load_skill()` must be
  read-only — no writes anywhere under the skills directory.
- `use_skill` must be `requires_confirmation=False` (auto-approved, no
  executor side effect) — matches `read_file`/`list_dir`/`search_files`,
  not `write_file`/`run_command`.

## Acceptance criteria
1. `REGISTRY["use_skill"].schema == skills.USE_SKILL_SCHEMA` and it
   matches the design's §4.2 schema exactly (including
   `"additionalProperties": False`) — `test_ac1_use_skill_schema_exact`.
2. `_parse_frontmatter()` parses a well-formed `---\nkey: value\n---`
   block into `{key: value}` — `test_ac2_parse_frontmatter_valid`.
3. `_parse_frontmatter()` returns `None` when the text has no leading
   `---` block — `test_ac3_parse_frontmatter_missing_block`.
4. `_parse_frontmatter()` returns `None` when the block never closes
   with a second `---` line — `test_ac4_parse_frontmatter_unclosed_block`.
5. `_discover()` walks the `<root>/<marketplace>/<plugin>/<version>/skills/<name>/SKILL.md`
   shape and resolves a skill's identity from frontmatter `name:` when
   present — `test_ac5_discover_nested_shape_frontmatter_name`.
6. `_discover()` falls back to the skill directory's own name when
   frontmatter is absent — `test_ac6_discover_fallback_to_dirname`.
7. `_discover()` keeps the numerically-highest version per plugin (e.g.
   `"6.10.0"` beats `"6.2.0"`, not lexical-string order) —
   `test_ac7_discover_latest_semver_wins_numerically`.
8. `_discover()` treats an unparseable version folder name (e.g. `"dev"`)
   as lowest precedence, never chosen over any numeric version —
   `test_ac8_discover_unparseable_version_sorts_last`.
9. `_discover()` resolves a same-name collision across two different
   marketplace/plugin trees by keeping the alphabetically-first full path
   — `test_ac9_discover_name_collision_alphabetical_path_wins`.
10. `build_skills_listing()` returns `""` when the skills root doesn't
    exist — `test_ac10_listing_empty_when_root_absent`.
11. `build_skills_listing()` returns `""` when the only discovered skill
    has no frontmatter (no `name:`/`description:`) at all —
    `test_ac11_listing_empty_when_no_skill_qualifies`.
12. `build_skills_listing()` returns the exact documented format, sorted
    alphabetically by resolved name, for two qualifying skills —
    `test_ac12_listing_formatted_and_sorted`.
13. A skill with `name:` but no `description:` is loadable via
    `load_skill()` but excluded from `build_skills_listing()` —
    `test_ac13_undescribed_skill_loadable_not_listed`.
14. `load_skill()` returns `None` for a name with no discovered skill —
    `test_ac14_load_skill_unknown_name`.
15. `load_skill()` returns the file's exact verbatim content, frontmatter
    included — `test_ac15_load_skill_verbatim_including_frontmatter`.
16. `load_skill()` appends `"\n\nARGUMENTS: {args}"` when `args` is
    non-empty — `test_ac16_load_skill_args_appended_when_present`.
17. `load_skill()` appends nothing extra when `args` is empty (the
    default) — `test_ac17_load_skill_args_omitted_when_empty`.
18. `load_skill()` truncates content over `_MAX_SKILL_CHARS` with the
    exact `"... [truncated at N characters]"` suffix —
    `test_ac18_load_skill_truncation`.
19. In `run_consult()`, a `use_skill` tool_call for an unknown name
    produces the exact `"no such skill: <name>"` tool_result —
    `test_ac19_use_skill_unknown_name_tool_result`.
20. In `run_consult()`, a `use_skill` tool_call for a real skill runs
    without ever calling `confirmer.ask()` (auto-approved) and its
    tool_result exactly equals `skills.load_skill(name)` —
    `test_ac20_use_skill_auto_approved_dispatch`.
21. `use_skill`'s schema is included in the tools list sent to the LLM
    alongside the existing seven tools —
    `test_ac21_schema_sent_includes_use_skill`.
22. In `run_consult()`, a tool_call for a name not in `REGISTRY` now
    produces a `tool_log` entry with `verdict="unknown_tool"`,
    `executed=False`, `result_text=None`, and the call's own arguments —
    `test_ac22_unknown_tool_logs_tool_log_entry`.
23. `Session._build_system_prompt()` includes a non-empty
    `skills_listing` positioned after the personality/memory blocks and
    before the fixed functional block —
    `test_ac23_skills_listing_present_and_positioned`.
24. `Session._build_system_prompt()`'s output is byte-for-byte identical
    with `skills_listing=""` (explicit) and with the parameter omitted
    entirely (the default) — `test_ac24_skills_listing_absent_when_empty`.

## Standing instructions
- Your goal: make the contract tests in
  `tests/contract/test_task_59_1_skill_loading.py` pass. All 24 already
  exist and are committed; every one is red against the base you're
  starting from (the module doesn't exist yet).
- The four already-amended pre-existing files listed under "What already
  exists" above are also committed in this same spec commit, already
  updated for `use_skill`'s existence — you do not need to touch them,
  and they should pass unmodified once your implementation matches this
  spec.
- NEVER modify files under `tests/contract/` — the gate rejects the
  commit if you do. If a contract test seems wrong or unimplementable,
  stop and flag it.
- You may add your own supplementary tests under `tests/impl/`.
- Flag unexpected state; do not modify anything outside this task.
- Deliver exactly ONE new commit on top of the HEAD you started from;
  never amend, rebase, or reset; leave the working tree clean.
- Commit message must follow the task-commit trailer format in
  `CLAUDE.md`: `Done: Task 59.1 — <description>`, `Fixed: ISSUE-064`,
  `Next: Task 59.2 — Phase 59 review (Tier 1, with Salvador)`, and your
  `Co-Authored-By:` signature line exactly as given in the dispatch
  prompt.
- If you run `bash scripts/run-tests.sh` locally to check your own work,
  note it only selects contract-test files for tasks marked done or
  `← current` in `PLAN.md` — Task 59.1 is already marked `← current`, so
  `test_task_59_1_skill_loading.py` is included automatically. Two
  already-known, already-logged flakes may appear in an unlucky run and
  are not your concern: `ISSUE-057`
  (`test_task_3_1_repl.py::test_ac5_127_auto_consults` and
  `::test_ac2_empty_accepts_pending_offer`, and the same root cause in
  `test_task_5_1_drop_127_preamble.py::test_ac1_no_verbose_preamble_sent_to_llm`)
  and `ISSUE-058` (already deselected by `run-tests.sh` itself). Rerun
  once if you see either; do not attempt to fix them.

## Verified fix sketch

Implemented and verified in a scratch worktree before committing: 24/24
new contract tests green, full suite 737/739 on one run (2 failures were
`ISSUE-057`'s known flake, confirmed by reproducing the identical failure
on unmodified `main` outside the worktree, 5 runs, ~50% fail rate,
unrelated to this task's diff), 737/737 (2 deselected `ISSUE-058`) on a
clean rerun.

One real bug found during verification, not present in the design's
original pseudocode/prose: **`tests/contract/test_task_22_1_tool_call_logging.py::test_ac10_unknown_tool_not_logged`**
asserted the exact opposite of §4.5's new behavior — it's Task 22.1's
original contract that unknown-tool calls are *never* logged. This
design deliberately reverses that (the whole point of §4.5 is a durable
signal for unrecognized tool names). Renamed to
`test_ac10_unknown_tool_logged_as_unknown_tool_verdict` and rewritten to
assert the new logged shape (`tool_name`, `call_id`, `arguments`,
`executed_command=None`, `verdict="unknown_tool"`, `executed=False`,
`result_text=None`, `result_omitted_reason=None`). This file is
pre-amended and committed alongside this spec; you do not need to touch
it.

`src/sasha/skills.py` (new file) — exactly as given in item 1 above,
verified working as-is, no changes needed from the spec's version.

`src/sasha/tools.py` diff:
```python
from sasha import memory
from sasha import question
from sasha import safe_commands
from sasha import skills
from sasha.executor import Executor
```
```python
def _use_skill_handler(args: dict, executor: Executor) -> ToolResult:
    name = args.get("name", "")
    content = skills.load_skill(name, args.get("args", ""))
    if content is None:
        return ToolResult(f"no such skill: {name}")
    return ToolResult(content)


REGISTRY: dict[str, Tool] = {
```
```python
    "use_skill": Tool(
        name="use_skill",
        schema=skills.USE_SKILL_SCHEMA,
        requires_confirmation=False,
        confirmation_summary=None,
        handler=_use_skill_handler,
        repetition_key=lambda args: args,
    ),
}
```

`src/sasha/agent.py` diff:
```python
            tool = REGISTRY.get(call.name)
            if tool is None:
                session.add_tool_result(call.id, f"Unknown tool: {call.name}")
                tool_log.log_call(
                    tool_name=call.name, call_id=call.id, arguments=call.arguments,
                    executed_command=None, cwd=executor.cwd, verdict="unknown_tool",
                    executed=False, result_text=None, result_omitted_reason=None)
                continue
```

`src/sasha/session.py` diff:
```python
class Session:
    def __init__(self, history_token_budget: int = 12000,
                 personality: str = "", skills_listing: str = "") -> None:
        self.history_token_budget = history_token_budget
        self._personality = personality
        self._skills_listing = skills_listing
        self._general_memory_block: str = ""
        self._project_memory_block: str = ""
```
```python
            parts.append(
                f"What you remember about this project so far:\n"
                f"{self._project_memory_block}")
        if self._skills_listing:
            parts.append(self._skills_listing)
        parts.append(functional)
        return "\n\n".join(parts)
```

`src/sasha/__main__.py` diff:
```python
from sasha import paste_join
from sasha import personality
from sasha import llm_debug
from sasha import prompt as prompt_mod
from sasha import skills
from sasha import tool_log
```
```python
    personality_result = personality.load_personality()
    skills_listing = skills.build_skills_listing()

    session = Session(limits.history_tokens,
                      personality=personality_result.text,
                      skills_listing=skills_listing)
```
