# Task 19.2: prompt.py busy-star token (\S, blink) + Repl background-task counter

## Tier 1 pre-dispatch step — required, do this first
`scripts/run-tests.sh` only runs contract tests for tasks marked `[x]` or
`← current` in PLAN.md — a landed task's tests are *always* included,
regardless of which task is current. `tests/contract/test_task_9_1_configurable_prompt.py`'s
`test_ac2_color_tokens_table_exact` asserts `COLOR_TOKENS` is an *exact*
25-entry dict. **Before dispatching this task**, Tier 1 must re-amend
that one test to expect 26 entries including `"blink": "\x1b[5m"`
(the amendment was written once already during this phase's spec-writing
pass, then deliberately reverted — see the "Track Task 19.1" era commits
— specifically because landing it early broke Task 19.1's gate). Redo it
now, as its own small commit, immediately before dispatching this task —
not any earlier, or it will break the gate for whatever task is current
at the time.

## Authoritative contract
`docs/superpowers/specs/2026-07-18-memory-bootstrap-design.md` §4 ("Busy
indicator plumbing") and §8's blink decision are the complete design
record for this task — read them first. This task builds the generic
"Sasha is doing background work" indicator infrastructure standalone; it
does not itself trigger any background work. Task 19.3 is what actually
spawns a background thread and calls the counter methods this task adds.

## What already exists
- `src/sasha/prompt.py` (Phase 9, landed): `COLOR_TOKENS: dict[str, str]`
  (built from `_BASE_CODES` + `reset`), `_POSITIONAL = {"u", "h", "w",
  "W"}`, `_walk(template)` (tokenizes escape syntax — recognizes any name
  in `_POSITIONAL` automatically, no changes needed there), `render(...)`,
  `validate_template(...)`, `DEFAULT_TEMPLATE`. Read the file directly —
  reproduced in full below as "What's new" context since this task's diff
  touches several non-adjacent spots.
- `src/sasha/repl.py`'s `Repl.__init__` and `Repl.loop()` — read the file
  directly. This task adds one new instance attribute and two new methods
  to `Repl`, and changes exactly one existing line inside `loop()` (the
  `prompt_mod.render(...)` call gains one new keyword argument). Nothing
  else in `repl.py` changes.

## What's new

### 1. `src/sasha/prompt.py`

Add `"blink": "\x1b[5m"` to the `COLOR_TOKENS` dict literal (alongside
the existing `"reset": _RESET` entry, before the `_BASE_CODES` loop that
adds the named colors — order within the dict doesn't matter, but put it
next to `"reset"` for readability).

Change:
```python
_POSITIONAL = {"u", "h", "w", "W"}
```
to:
```python
_POSITIONAL = {"u", "h", "w", "W", "S"}
```
(`_walk` needs no other change — it already yields `("positional", "S")`
for `\S` automatically once "S" is in this set.)

Add a new module-level helper, placed after `_walk` and before
`validate_template`:
```python
def _busy_star(busy: bool, color_enabled: bool) -> str:
    """The status star: plain "✦" when colors are disabled (matches how
    the literal star in today's DEFAULT_TEMPLATE already behaves), or
    "✦" wrapped in yellow (idle) / blink+yellow (busy) escape codes,
    zero-width-marked the same way every other color token already is."""
    if not color_enabled:
        return "✦"
    code = (COLOR_TOKENS["blink"] + COLOR_TOKENS["yellow"]) if busy \
        else COLOR_TOKENS["yellow"]
    return (_RL_IGNORE_START + code + _RL_IGNORE_END + "✦"
            + _RL_IGNORE_START + COLOR_TOKENS["reset"] + _RL_IGNORE_END)
```

Change `render`'s signature from:
```python
def render(template: str, *, user: str, host: str, cwd: str, home: str,
           color_enabled: bool) -> str:
```
to:
```python
def render(template: str, *, user: str, host: str, cwd: str, home: str,
           color_enabled: bool, busy: bool = False) -> str:
```

Inside `render`'s positional-handling branch, insert a new `elif` for
`"S"` **before** the final bare `else:` (the bare `else:` currently
handles `"W"` implicitly by being the fallback for anything that isn't
`"u"`/`"h"`/`"w"` — `"S"` must not fall into it, or it would incorrectly
render as a cwd basename):
```python
        elif kind == "positional":
            if value == "u":
                parts.append(user)
            elif value == "h":
                parts.append(host)
            elif value == "w":
                if cwd.startswith(home):
                    parts.append("~" + cwd[len(home):])
                else:
                    parts.append(cwd)
            elif value == "S":
                parts.append(_busy_star(busy, color_enabled))
            else:
                if cwd == home:
                    parts.append("~")
                elif cwd == "/":
                    parts.append("/")
                else:
                    parts.append(os.path.basename(cwd))
```

Change `DEFAULT_TEMPLATE` from:
```python
DEFAULT_TEMPLATE: str = r"\[magenta\][sasha]\[reset\]\[yellow\]✦\[reset\] \[bold-green\]\u@\h\[reset\]:\[bold-blue\]\w\[reset\] $ "
```
to:
```python
DEFAULT_TEMPLATE: str = r"\[magenta\][sasha]\[reset\]\S \[bold-green\]\u@\h\[reset\]:\[bold-blue\]\w\[reset\] $ "
```

### 2. `src/sasha/repl.py`

In `Repl.__init__`, add one new instance attribute, alongside the
existing `self._memory_dir_existed = False` line:
```python
        self._background_tasks = 0   # int — count of in-flight background jobs
```

Add two new methods anywhere in the `Repl` class (e.g. right after
`_check_memory_created`):
```python
    def _begin_background_task(self):
        self._background_tasks += 1

    def _end_background_task(self):
        self._background_tasks = max(0, self._background_tasks - 1)
```

In `Repl.loop()`, change the existing:
```python
            rendered = prompt_mod.render(
                self._config.prompt, user=user, host=host, cwd=cwd,
                home=home, color_enabled=self._sink.color_enabled)
```
to:
```python
            rendered = prompt_mod.render(
                self._config.prompt, user=user, host=host, cwd=cwd,
                home=home, color_enabled=self._sink.color_enabled,
                busy=self._background_tasks > 0)
```

## Constraints
- Modify only `src/sasha/prompt.py` and `src/sasha/repl.py` (only the
  exact additions/changes shown above in each). Do not touch
  `src/sasha/memory.py`, `src/sasha/session.py`, `src/sasha/config.py`,
  `src/sasha/tools.py`, `README.md`, or anything under `docs/`.
- `render()`'s new `busy` parameter must default to `False` and be
  keyword-only (after the existing `*`) — every existing call site
  (dozens across `tests/contract/test_task_9_1_configurable_prompt.py`
  and elsewhere) must keep working with zero changes.
- Do not spawn any thread, call any LLM client, or touch
  `_check_place_shift`/`_check_commit`/`_check_memory_created` in this
  task — `_begin_background_task`/`_end_background_task` are added but
  deliberately unused by anything else yet; Task 19.3 is what calls them.
- This task also amends one existing contract test:
  `tests/contract/test_task_9_1_configurable_prompt.py`'s
  `test_ac2_color_tokens_table_exact`, whose exact-dict/exact-count
  assertion (25 entries) has already been updated by Tier 1 to include
  `"blink"` (26 entries) as part of this task's spec commit — you do not
  need to touch that file; it is already correct on the base you're
  starting from.

## Acceptance criteria
(encoded in `tests/contract/test_task_19_2_busy_star_indicator.py`)

1. `COLOR_TOKENS["blink"] == "\x1b[5m"`.
2. `render(r"\S", ..., color_enabled=True, busy=False)` renders the star
   wrapped in plain yellow (no blink code), zero-width-marked exactly like
   every other color-token render already is.
3. `render(r"\S", ..., color_enabled=True, busy=True)` renders the star
   wrapped in blink+yellow (both codes present, blink first), zero-width
   marked.
4. `render(r"\S", ..., color_enabled=False, busy=True)` and
   `busy=False` both render as the bare `"✦"` character with no escape
   codes at all — color disabled means no visual distinction, matching
   how every other color token already degrades.
5. `render(r"\S", ...)` called without passing `busy` at all defaults to
   idle (same output as explicit `busy=False`) — the keyword-only default
   must actually work, not just be documented.
6. `DEFAULT_TEMPLATE` rendered end-to-end (not just the isolated `\S`
   token) produces the expected full prompt string, both busy and idle,
   with `color_enabled=True`.
7. A repo's `\W`-style basename rendering is unaffected by the `"S"`
   addition to `_POSITIONAL` — a regression guard proving `"S"` didn't
   accidentally fall into the bare-`else` (basename) branch.
8. `Repl.__init__` leaves a fresh instance with `_background_tasks == 0`.
9. `_begin_background_task()` increments; `_end_background_task()`
   decrements; two `_begin_background_task()` calls followed by one
   `_end_background_task()` call leaves the counter at `1`, not `0`.
10. `_end_background_task()` called on an already-zero counter leaves it
    at `0` (never negative).
11. `Repl.loop()`'s rendered prompt (captured via a scripted
    single-prompt-then-`EOFError` `input_fn`, the same pattern
    `test_task_10_1_boot_hint.py` already uses) contains the blink escape
    code when `_background_tasks` is nonzero before `loop()` is called,
    and does not when it's zero — proving the wiring in `loop()`
    actually reads the live counter, not a snapshot taken at
    `__init__` time.

## Standing instructions
- Your goal: make the contract tests in
  `tests/contract/test_task_19_2_busy_star_indicator.py` pass, and keep
  the full contract suite green (including the already-amended
  `test_task_9_1_configurable_prompt.py`):
  `PYTHONPATH=src python3 -m pytest tests/contract/`.
- NEVER modify files under `tests/contract/` — the gate rejects the
  commit if you do.
- Deliver exactly ONE new commit on top of the HEAD you started from;
  never amend, rebase, or reset; leave the working tree clean.
- Commit message per `AGENTS.md`, with trailers: `Done: Task 19.2 — <one
  line>`, `Next: Task 19.3 — repl.py: stage 1/2 bootstrap wiring into
  _check_place_shift`, and the `Co-Authored-By:` signature line from your
  dispatch prompt.
- Greenfield-vs-surgical routing: this task makes precise, well-specified
  additions to two existing files' control flow (one new dict entry, one
  new set member, one new helper function, one new parameter, one new
  `elif` branch, one changed default-template string, two new `Repl`
  methods, one changed method call) — not a rewrite of either file's
  existing logic. Dispatch to the default model (deepseek-v4-pro); do not
  use a `SASHA_DISPATCH_MODEL` local-model override.
