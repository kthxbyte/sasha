# Sasha — Tab Completion Design (ISSUE-028)

**Status:** Draft for review
**Date:** 2026-07-16

## 1. Mission

Give Sasha bash-style TAB completion: command names and Sasha's own verbs
on the first word, and rich, correct argument completion (filenames,
git branches, and hundreds of other tools) on subsequent words — by
driving the real `bash-completion` ecosystem already installed on the
system, rather than reimplementing any of it.

## 2. Scope

Split into two tasks — the higher-risk external-subprocess piece
(11.2) is separable from and builds on the self-contained own-logic
piece (11.1), matching how Phase 6 split its structural task from its
additive layer.

### Task 11.1 — Own-logic completion (in scope):
- New `src/sasha/completion.py`: `complete_first_word()` (PATH-executable
  scan, cached), `complete_builtin_arg()` (verb completion for `sasha
  <TAB>`, profile-name completion for `sasha profile <TAB>`, muted-command
  completion for `sasha unmute <TAB>`), and `words_and_cword()` (line →
  `words`/`cword` reconstruction, a plain testable function, not a
  closure — see §4).
- `MuteStore.muted_commands() -> list[str]` — one new read-only method
  (`src/sasha/mutes.py`, never touched since Phase 1).
- `readline.set_completer(...)` wiring in `__main__.py`: a small closure
  that calls `completion.words_and_cword()` then dispatches, plus the
  `(text, state)` per-line caching (see §4/§5.3).
- Ships as a complete, useful increment on its own: TAB completes PATH
  commands, `sasha`'s own verbs, profile names, and muted-command names.
  Argument completion for anything else returns nothing (no crash, just
  no extra candidates) until 11.2 lands.

### Task 11.2 — bash-completion integration (in scope):
- `complete_via_bash()` — the subprocess-driven mechanism validated
  interactively (see §5.1): spawn `bash`, source `bash-completion`,
  invoke the registered (or `_minimal`-fallback) completer, read
  `COMPREPLY`. Hard timeout (500ms), silent empty-list fallback on
  timeout or any subprocess error — never raises into the REPL.
- `bash_completion_available()` — cached startup check (`bash` binary +
  `bash-completion`'s main script both present).
- Graceful-degradation fallback: plain `glob`-based filename completion
  when bash-completion isn't available, so argument completion still
  does *something* useful either way.
- Wiring `complete_via_bash()` (or the glob fallback) as what
  `complete_builtin_arg()`'s `None` return falls through to.

### Out of scope / non-goals (either task):
- Reimplementing any per-command completion logic ourselves — the whole
  point is driving the existing ecosystem (967 completion scripts on
  this system alone), not rebuilding even a fraction of it.
- Windows or any non-bash-completion-having system beyond the documented
  graceful degradation — no attempt to port bash-completion's logic
  itself, only to detect its absence and degrade cleanly.
- Completing mid-line in exotic ways (e.g. completing a word in the
  middle of a quoted string with embedded escapes) beyond whatever
  `readline`'s own tokenization already gives us via `get_begidx()`/
  `get_endidx()`.
- Caching `complete_via_bash()` results across lines/sessions — every
  request is a fresh subprocess call; no completion result persistence.

## 3. Key contracts & assumptions

1. **First word vs. everything else is the fundamental dispatch split.**
   Word 0 is completed by Sasha's own PATH scan (bash's *own* core
   command-completion behavior, not something `bash-completion` the
   *package* provides — confirmed by testing: `complete -p` only
   registers completions for a command once it's already the known
   first word). Word 1+ goes through `complete_builtin_arg()` then falls
   through to `complete_via_bash()`.
2. **`complete_via_bash()` never raises and never blocks past its
   timeout.** A slow or hanging third-party completion script degrades
   to "no extra candidates for this TAB press," not a frozen REPL.
3. **The readline `(text, state)` completer computes its match list once
   per line, not once per `state` call.** `state==0` triggers the actual
   dispatch (cheap for `complete_first_word()`, one subprocess spawn for
   `complete_via_bash()`); `state>=1` serves from the cached list.
4. **Sasha's own builtins take priority over bash-completion.** `sasha
   profile <TAB>` always completes profile names from `Config`, never
   falls through to bash's generic completion for an unrecognized
   `sasha` subcommand structure.
5. **`bash_completion_available()` is checked once, not per keystroke.**
   Computed at `Repl`/completer construction time and held for the
   process's lifetime — filesystem/PATH state isn't expected to change
   mid-session.

## 4. Interface

New file `src/sasha/completion.py`:

```python
def complete_first_word(prefix: str, path_dirs: list[str]) -> list[str]:
    """PATH-executable names starting with `prefix`, deduplicated,
    sorted. `path_dirs` is the caller's os.environ["PATH"].split(os.pathsep)
    — no os.environ read inside this function, keeps it a pure function
    of its arguments."""

def complete_builtin_arg(words: list[str], cword: int, profile_names: list[str],
                          muted_commands: list[str]) -> list[str] | None:
    """words[0] == 'sasha' (words[cword] is the current, possibly-empty
    prefix being typed — this function filters internally, same
    contract as complete_first_word; callers never re-filter):
       - cword == 1: filter {'mute', 'unmute', 'profile', 'help'} by
         words[cword]
       - cword == 2 and words[1] == 'profile': filter profile_names by
         words[cword]
       - cword == 2 and words[1] == 'unmute': filter muted_commands by
         words[cword]
       Anything else (including words[0] != 'sasha'): return None — signals
       'not my job, fall through to bash-completion / glob fallback'."""

def complete_via_bash(words: list[str], cword: int,
                       timeout_s: float = 0.5) -> list[str]:
    """Task 11.2. See §5.1 for the exact subprocess script. Empty list on
    timeout or any error."""

def bash_completion_available() -> bool:
    """Task 11.2. shutil.which('bash') is not None and
    pathlib.Path('/usr/share/bash-completion/bash_completion').exists()."""

def complete_filenames(prefix: str) -> list[str]:
    """Task 11.2 fallback path. glob.glob(prefix + '*'), each match
    suffixed '/' if it's a directory — used only when
    bash_completion_available() is False."""
```

`src/sasha/mutes.py` gains:
```python
def muted_commands(self) -> list[str]:
    """Command names with at least one muted (command, exit_code) pair,
    deduplicated, sorted — for `sasha unmute <TAB>`."""
    data = self._load()
    return sorted({key.rsplit(":", 1)[0]
                   for key, v in data["pairs"].items() if v["muted"]})
```

`src/sasha/__main__.py` gains a completer closure, wired via
`readline.set_completer(...)` and `readline.parse_and_bind("tab: complete")`,
placed alongside the existing history setup (both configure `readline`
before `repl.loop()` is called).

`completion.py` also gets a fifth function, `words_and_cword()`, reconstructing
`words`/`cword` from the raw readline line — a plain, independently
testable function rather than a closure buried in `__main__.py`. Note
`prior_words` is computed from the text *before* the cursor only, and
`text` (the word currently being typed, possibly `""`) is always appended
to form `words`, so `cword` is always `len(prior_words)` regardless of
whether the current word is empty (e.g. the cursor sits right after
`"git "`, starting a fresh, still-empty word at index 1 — not still
completing index 0):

```python
def words_and_cword(line: str, begidx: int, text: str) -> tuple[list[str], int]:
    prefix = line[:begidx]
    try:
        prior_words = shlex.split(prefix) if prefix.strip() else []
    except ValueError:
        prior_words = prefix.split()
    return prior_words + [text], len(prior_words)
```

**Task 11.1's version** of the completer (no bash-completion yet — a
`None` from `complete_builtin_arg()` just yields no extra candidates):

```python
_last_line = None
_last_matches = []

def _completer(text, state):
    global _last_line, _last_matches
    line = readline.get_line_buffer()
    if line != _last_line:
        _last_line = line
        words, cword = completion.words_and_cword(line, readline.get_begidx(), text)
        if cword == 0:
            _last_matches = completion.complete_first_word(
                text, os.environ.get("PATH", "").split(os.pathsep))
        else:
            builtin = completion.complete_builtin_arg(
                words, cword, list(config.profiles), mutes.muted_commands())
            _last_matches = builtin if builtin is not None else []
    return _last_matches[state] if state < len(_last_matches) else None

readline.set_completer(_completer)
readline.parse_and_bind("tab: complete")
```

**Task 11.2 changes only the `else` branch** (the `builtin is not None`
case, `cword == 0` case, and everything else stay exactly as 11.1 left
them) — replacing `_last_matches = builtin if builtin is not None else []`
with:

```python
        if builtin is not None:
            _last_matches = builtin
        elif _bash_ok:
            _last_matches = completion.complete_via_bash(words, cword)
        else:
            _last_matches = completion.complete_filenames(text)
```

where `_bash_ok = completion.bash_completion_available()` is computed
once, alongside the closure's other setup, before `repl.loop()` is called.

## 5. Mechanism

### 5.1 The bash-completion subprocess script (Task 11.2)

Validated interactively before writing this spec (see conversation) —
`git chec` → `checkout`, `git checkout ma` → `main` (a real branch in
this repo), `cat PLAN` → `PLAN.md`, and an unregistered/unknown command
falls back to bash-completion's own `_minimal` completer (which itself
does correct filename completion) rather than erroring. Measured
~27-31ms per invocation including a fresh bash spawn and completer load
— comfortably within "feels instant" for interactive use.

```python
import shlex
import subprocess

def complete_via_bash(words, cword, timeout_s=0.5):
    comp_line = " ".join(words)
    comp_words_bash = "(" + " ".join(shlex.quote(w) for w in words) + ")"
    script = (
        "source /usr/share/bash-completion/bash_completion 2>/dev/null\n"
        f"_completion_loader {shlex.quote(words[0])} 2>/dev/null\n"
        f"COMP_LINE={shlex.quote(comp_line)}\n"
        "COMP_POINT=${#COMP_LINE}\n"
        f"COMP_WORDS={comp_words_bash}\n"
        f"COMP_CWORD={cword}\n"
        f'func=$(complete -p {shlex.quote(words[0])} 2>/dev/null | '
        r'sed -n "s/.*-F \([^ ]*\).*/\1/p")' "\n"
        '[ -n "$func" ] && "$func"\n'
        'printf "%s\\n" "${COMPREPLY[@]}"\n'
    )
    try:
        r = subprocess.run(["bash", "-c", script], capture_output=True,
                            text=True, timeout=timeout_s)
    except (subprocess.TimeoutExpired, OSError):
        return []
    return [line for line in r.stdout.splitlines() if line]
```

### 5.2 Dispatch order

For a given TAB press: `cword == 0` → `complete_first_word()`. Otherwise
→ `complete_builtin_arg()`; a non-`None` result wins outright (Sasha's
own builtins are never second-guessed by bash-completion); `None` falls
through to `complete_via_bash()` when available, else
`complete_filenames()`.

### 5.3 readline `(text, state)` caching

Readline calls the completer repeatedly with `state = 0, 1, 2, ...` until
it returns `None`, to enumerate every match for one TAB press (and TAB-TAB
to list them all). Dispatching to `complete_via_bash()` on *every* state
call would mean one subprocess spawn per candidate — the closure in §4
caches the whole match list keyed by whether `readline.get_line_buffer()`
changed since the last call, so a single line's full completion request
does exactly one dispatch (cheap for the own-logic path, one subprocess
spawn for the bash-completion path).

### 5.4 Why word-0 completion isn't part of `bash-completion`

Confirmed by testing: `complete -p <cmd>` only returns a registered
completer once bash already knows `<cmd>` is the command being completed
*for* — i.e. `complete -F __git_wrap__git_main git` fires when
completing git's *arguments*, not when completing "gi" into "git" in the
first place. Command-name completion is bash's own core readline
behavior (PATH search + builtins + functions), entirely separate from the
`bash-completion` *package*'s per-command argument completers. This is
why `complete_first_word()` needs its own PATH scan rather than also
routing through `complete_via_bash()`.

## 6. Decisions log

- **Decision:** Drive the real `bash-completion` ecosystem via a
  subprocess rather than reimplementing per-command completion.
  **Rationale:** Salvador's ask — validated interactively that it's
  cheap (~30ms), correct (real git branches, real filenames), and covers
  967 commands' worth of completion logic on this system alone, which
  reimplementing even a fraction of would never match.
- **Decision:** Split into Task 11.1 (own logic, no subprocess) and Task
  11.2 (bash-completion integration). **Rationale:** Salvador's call —
  isolates the external-subprocess/timeout/safety concerns from the
  simpler, fully self-contained PATH-scan-and-builtins piece; 11.1 ships
  useful completion on its own even if 11.2 were delayed or dropped.
- **Decision:** 500ms hard timeout on `complete_via_bash()`, silent
  empty-list fallback. **Rationale:** Salvador's call — some real-world
  completion scripts do network lookups (cloud CLI tools); a bounded,
  invisible-to-the-user worst case beats an unbounded hang.
- **Decision:** Graceful degradation (plain glob-based filename
  completion) when bash/bash-completion isn't installed, rather than
  disabling all completion. **Rationale:** Salvador's call — first-word
  completion (Sasha's own logic) has no dependency on bash-completion at
  all, no reason to lose it just because the argument-completion layer
  is unavailable.
- **Decision:** `sasha profile <TAB>` / `sasha unmute <TAB>` completion
  kept in scope, including the new `MuteStore.muted_commands()` method
  and unlocking `mutes.py` for the first time since Phase 1.
  **Rationale:** Salvador's call — a small, self-contained, read-only
  addition; not worth losing builtin-arg completion for `unmute`
  specifically just to avoid touching one file.
- **Decision:** Verb completion (`sasha <TAB>` → `mute`/`unmute`/
  `profile`/`help`) lives in `complete_builtin_arg()`, not
  `complete_first_word()`. **Rationale:** Salvador's call — positionally
  it's a `cword==1` completion (same shape as profile-name/muted-command
  completion), not a `cword==0` PATH-scan case; `sasha` itself (word 0)
  just goes through the normal PATH scan like any other command name.

## 7. Testing strategy

**Task 11.1** contract tests exercise `complete_first_word()`,
`complete_builtin_arg()`, and `MuteStore.muted_commands()` as pure
functions with fixed/fake inputs (a fake PATH directory list built in a
`tmp_path` fixture with a few executable files; fake profile-name lists;
a real `MuteStore` against a temp file with known muted entries) — no
subprocess, no dependency on the real system PATH or bash-completion.
The `readline` closure's per-line caching behavior is tested by extracting
the dispatch logic (line → words/cword → result) as a plain function
separate from the `set_completer` registration itself, so it's callable
directly without a real `readline`/terminal.

**Task 11.2** contract tests exercise `complete_via_bash()` against the
*real* bash-completion installation on this system (already confirmed
present) — meaningful assertions like `complete_via_bash(["git",
"chec"], 1) == ["checkout"]` and `complete_via_bash(["cat", "PLAN"], 1)`
containing `"PLAN.md"`, plus the timeout path (a stub `words[0]` that
resolves to a deliberately slow completion, or monkeypatching
`subprocess.run` to raise `TimeoutExpired`) and
`bash_completion_available()`'s True/False branches (monkeypatching
`shutil.which`/the script path check). `complete_filenames()` is tested
directly against a `tmp_path` with known files/directories.
