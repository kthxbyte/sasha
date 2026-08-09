# Task 61.1: YAML block-scalar frontmatter parsing (ISSUE-065)

## Authoritative contract
No separate design doc — small, well-scoped single-file fix, matching the
Task 8.1/28.1/30.1/58.1/60.1 precedent. This spec is the complete
contract.

**Bug, found 2026-08-08 while Salvador installed real skill libraries**
(ISSUE-064's stage 1 shipped as Task 59.1): after installing both
`superpowers` and `ponytail`
(https://github.com/DietrichGebert/ponytail) into
`~/.config/sasha/skills/`, `skills.build_skills_listing()` showed
`ponytail: >` in the system-prompt listing instead of ponytail's actual
description — useless to the LLM deciding whether to reach for it.

**Root cause:** `_parse_frontmatter()` (`skills.py:36-51` pre-Task-61.1)
is a simple line-based `key: value` parser — split each line on its
first colon, done. Real-world `SKILL.md` frontmatter routinely uses
YAML's **block scalar** syntax for multi-line values: `description: >`
(folded style) or `description: |` (literal style), followed by
indented continuation lines that are the actual value. Ponytail's real
`SKILL.md` does exactly this:
```yaml
---
name: ponytail
description: >
  Forces the laziest solution that actually works, simplest, shortest, most
  minimal. Channels a senior dev who has seen everything: question whether the
  task needs to exist at all (YAGNI), reach for the standard library before
  ...
argument-hint: "[lite|full|ultra]"
license: MIT
---
```
The old parser did two wrong things here: (1) it captured the literal
value `>` for `description`, never looking at the continuation lines at
all; (2) several of those continuation lines themselves contain colons
("...has seen everything: question...", "...intensity levels: lite..."),
so the old per-line `":" in line` check treated them as *new* key:value
pairs, polluting the result dict with garbage keys derived from prose
fragments. Both bugs stem from the same gap: the parser has no concept of
a multi-line value at all.

## What already exists to bind, not reimplement
- `src/sasha/skills.py`: `_parse_frontmatter(text: str) -> dict | None`
  (`skills.py:36-51`) — this task rewrites its body and adds one new
  helper, `_consume_block_scalar()`. The function's signature, its
  "missing/unclosed `---` → `None`" contract, and its "line without a
  colon is skipped" behavior for plain (non-block) lines are unchanged.
- `src/sasha/skills.py`: `_discover()`, `build_skills_listing()`,
  `load_skill()` — all call `_parse_frontmatter()` already and need no
  changes; they automatically benefit once it returns correct values.
- `tests/contract/test_task_59_1_skill_loading.py` — existing, immutable.
  All of its `_parse_frontmatter`/`build_skills_listing`/`load_skill`
  tests use single-line frontmatter values only, so none of them exercise
  the new block-scalar path; verify they still pass unmodified (they do
  — see "Verified fix sketch").

## What's new
Rewrite `_parse_frontmatter()` in `src/sasha/skills.py` and add one new
helper. Full implementation, verified working as-is in a scratch
worktree (see "Verified fix sketch" below):

```python
_BLOCK_SCALAR_INDICATORS = (">", ">-", ">+", "|", "|-", "|+")


def _consume_block_scalar(lines: list[str], start: int) -> tuple[list[str], int]:
    """lines[start] is the first line after a `key: >`/`key: |` line. Reads
    consecutive lines more-indented than the first continuation line's own
    indentation (blank lines pass through) as the block's body, stopping at
    the closing '---', a dedent back to or past the key's own column, or
    end of input. Returns (joined_lines_as_raw_list_reference, next_index)
    -- callers fold/join per their scalar style."""
    body: list[str] = []
    indent: int | None = None
    i = start
    while i < len(lines):
        raw = lines[i]
        if raw.strip() == "---":
            break
        if raw.strip() == "":
            body.append("")
            i += 1
            continue
        this_indent = len(raw) - len(raw.lstrip(" "))
        if indent is None:
            indent = this_indent
        if this_indent < indent:
            break
        body.append(raw[indent:])
        i += 1
    return body, i


def _parse_frontmatter(text: str) -> dict | None:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    result: dict[str, str] = {}
    closed = False
    i = 1
    while i < len(lines):
        line = lines[i]
        if line.strip() == "---":
            closed = True
            break
        if ":" not in line:
            i += 1
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()
        if value in _BLOCK_SCALAR_INDICATORS:
            body, i = _consume_block_scalar(lines, i + 1)
            if value[0] == ">":
                # Folded style: adjacent non-blank lines join with a space;
                # a blank line becomes a paragraph break.
                paragraphs: list[str] = []
                current: list[str] = []
                for body_line in body:
                    if body_line == "":
                        if current:
                            paragraphs.append(" ".join(current))
                            current = []
                    else:
                        current.append(body_line)
                if current:
                    paragraphs.append(" ".join(current))
                value = "\n".join(paragraphs).strip()
            else:
                value = "\n".join(body).strip()
        else:
            i += 1
        result[key] = value
    if not closed:
        return None
    return result
```

Mechanism, precisely:
1. The main loop switches from a `for line in lines[1:]` iteration to an
   index-based `while i < len(lines)` loop, because block-scalar
   consumption needs to advance `i` by a variable number of lines (not
   always 1).
2. When a `key: value` line's stripped value is exactly one of the six
   YAML block-scalar indicators (`_BLOCK_SCALAR_INDICATORS` — folded `>`,
   literal `|`, each with optional `-`/`+` chomping suffix), the
   continuation lines are consumed as that key's real value instead of
   being scanned line-by-line for their own colons.
3. `_consume_block_scalar()` establishes the block's indentation from its
   *first* non-blank continuation line, then consumes lines at that
   indentation or deeper (stripping exactly that many leading spaces),
   passing blank lines through unchanged. It stops at the closing `---`,
   at a line dedented back to the key's own column or shallower (a new
   top-level key, e.g. `argument-hint:`/`license:` after the description
   block in the real ponytail example), or at end of input.
4. Folded style (`>`, `>-`, `>+`) joins consecutive non-blank lines with a
   single space (matching YAML's folding rule for the common case — no
   attempt at YAML's more-indented-line-preservation edge case, out of
   scope, see "Constraints"); a blank line inside the block starts a new
   paragraph, paragraphs joined with `\n`.
5. Literal style (`|`, `|-`, `|+`) joins every consumed line (including
   blanks) with `\n`, preserving the author's line breaks verbatim.
6. Chomping suffixes (`-`/`+`) are recognized (so the indicator-detection
   `in _BLOCK_SCALAR_INDICATORS` check matches them) but not given
   distinct trailing-newline semantics — every result is `.strip()`-ed
   regardless of chomping mode. Real `SKILL.md` frontmatter values are
   always used as single display/context strings, never round-tripped
   back to YAML, so exact chomping fidelity has no observable effect
   here (see "Constraints").

## Constraints
- Do not touch `_discover()`, `build_skills_listing()`, `load_skill()`,
  `_skills_root()`, `USE_SKILL_SCHEMA`, or `_version_sort_key()` —
  unchanged.
- Do not add a YAML library dependency (`pyyaml` or similar) —
  `pyproject.toml` has no runtime dependencies today and this fix doesn't
  need one; a hand-rolled block-scalar reader is sufficient for
  `SKILL.md` frontmatter's actual real-world shape.
- Do not implement full YAML block-scalar fidelity (explicit indentation
  indicators like `>2`, YAML's more-indented-line preservation within a
  folded block, comments after the indicator). These are legitimate YAML
  features but essentially never appear in hand-written `SKILL.md`
  frontmatter; treat any of them as an ordinary non-block single-line
  value if encountered (i.e. don't crash — the existing `_parse_frontmatter`
  behavior for anything not exactly matching `_BLOCK_SCALAR_INDICATORS`
  already falls through unchanged).
- `_parse_frontmatter()`'s existing contract must not regress: still
  returns `None` for missing/unclosed `---`, still skips (rather than
  erroring on) any line without a colon outside a block scalar's body.

## Acceptance criteria
1. Parsing the real ponytail `SKILL.md`'s frontmatter (folded `description:
   >`, including continuation lines that themselves contain colons, plus
   `argument-hint`/`license` single-line keys after the block) produces
   the exact clean description string, with no garbage keys from the
   colon-containing continuation lines — `test_ac1_folded_scalar_matches_real_ponytail_skill`.
2. A basic two-line folded scalar joins with a single space —
   `test_ac2_folded_scalar_basic`.
3. A blank line inside a folded scalar's body starts a new paragraph
   (paragraphs joined with `\n`, lines within a paragraph joined with a
   space) — `test_ac3_folded_scalar_blank_line_is_paragraph_break`.
4. A literal scalar (`|`) preserves line breaks exactly —
   `test_ac4_literal_scalar_preserves_newlines`.
5. Each chomping-indicator variant (`>-`, `>+`, `|-`, `|+`) is recognized
   as a block-scalar trigger — `test_ac5_chomping_indicator_variants`.
6. A plain single-line `key: value` frontmatter entry parses exactly as
   before (regression guard) — `test_ac6_plain_single_line_value_unaffected`.
7. `build_skills_listing()` shows the real folded description text for a
   skill installed with ponytail's exact frontmatter shape, never the
   literal `>` indicator — `test_ac7_listing_uses_real_description_not_indicator`.

## Standing instructions
- Your goal: make the contract tests in
  `tests/contract/test_task_61_1_yaml_block_scalar_frontmatter.py` pass.
  All 7 already exist and are committed; 6 are red against the base you're
  starting from. `test_ac6_plain_single_line_value_unaffected` already
  passes today by coincidence — the current implementation already
  handles a plain single-line value correctly, since the block-scalar
  path doesn't exist yet to interfere with it. It is not vacuous: it
  encodes a real regression constraint (your rewrite must not break the
  existing single-line case) your implementation must not violate,
  matching the Task 57.1 AC14 / Task 60.1 AC3 precedent.
- `tests/contract/test_task_59_1_skill_loading.py`'s existing tests must
  keep passing unmodified — they don't exercise block scalars, so your
  change should have zero effect on them, but don't break them.
- NEVER modify files under `tests/contract/` — the gate rejects the
  commit if you do. If a contract test seems wrong or unimplementable,
  stop and flag it.
- You may add your own supplementary tests under `tests/impl/`.
- Flag unexpected state; do not modify anything outside this task.
- Deliver exactly ONE new commit on top of the HEAD you started from;
  never amend, rebase, or reset; leave the working tree clean.
- Commit message must follow the task-commit trailer format in
  `CLAUDE.md`: `Done: Task 61.1 — <description>`, `Fixed: ISSUE-065`,
  `Next: Task 61.2 — Phase 61 review (Tier 1, with Salvador)`, and your
  `Co-Authored-By:` signature line exactly as given in the dispatch
  prompt.

## Verified fix sketch

Implemented and verified in a scratch worktree before committing: 7/7
new contract tests green, full suite 750/750 on two consecutive clean
runs (743 prior + 7 new, 0 deselected). Manually confirmed against the
real, as-installed ponytail `SKILL.md` at
`~/.config/sasha/skills/ponytail/ponytail/4.9.0/skills/ponytail/SKILL.md`:
```python
>>> from sasha.skills import _parse_frontmatter
>>> _parse_frontmatter(open(".../ponytail/SKILL.md").read())
{'name': 'ponytail', 'description': 'Forces the laziest solution that actually
works, simplest, shortest, most minimal. Channels a senior dev who has seen
everything: question whether the task needs to exist at all (YAGNI), ...
Do NOT use for non-coding requests (general knowledge, prose, translation,
summaries, recipes).', 'argument-hint': '"[lite|full|ultra]"', 'license': 'MIT'}
```

`src/sasha/skills.py` diff — exactly as given in "What's new" above,
verified working as-is, no changes needed from the spec's version.
