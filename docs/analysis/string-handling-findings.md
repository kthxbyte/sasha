# Sasha — string / bytes handling static-analysis findings

**Date:** 2026-10-12
**Scope:** `src/sasha/` at `0381ab7` (v0.6.0), ~4,800 lines across 24 modules.
**Question asked:** potential risks in string handling — bytes vs strings, encode/decode
inconsistencies, and that family.

**Method.** Two passes:

1. *Tooling* — no analysers were installed on this machine, so I created a venv at
   `scratchpad/sa-venv` (gitignored, keeps the tree clean) with **ruff 0.17.0** and
   **mypy 2.4.0**, then ran:
   - `ruff check src tests --select E,F,W,B,S,RUF,UP,SIM,PIE,C4,PT,DTZ` (pycodestyle,
     pyflakes, bugbear, bandit, refurb, modernization) — 1,842 findings, 104 of them in
     `src` once `S101` (assert-in-tests) is excluded; overwhelmingly style.
   - `mypy src/sasha --check-untyped-defs --ignore-missing-imports` — 16 findings.
2. *Targeted review* of every decode/encode and filesystem/subprocess text-I/O site,
   with a **reproduction for each confirmed finding** rather than a reading.

**Headline: mypy found zero bytes/str confusion.** That is not reassurance — it is a
coverage statement. The codebase is largely unannotated, so `--check-untyped-defs` only
reaches so far, and every real defect below was found by reading the boundaries and then
proving them. Ruff has no bytes-vs-str rule, so it found none either.

---

## 0. The one root cause behind almost all of it

`UnicodeDecodeError` and `UnicodeEncodeError` derive from **`ValueError`**, not from
`OSError`. Every handler written as `except OSError` therefore does *not* catch a decoding
failure, and neither does `except FileNotFoundError`.

This codebase has **52 handlers of the form `except OSError` / `except (OSError, …)`**,
and exactly **two** sites that account for `UnicodeError`:

| Site | Verdict |
|---|---|
| `src/sasha/personality.py:59` — `except (OSError, UnicodeDecodeError)` | correct |
| `src/sasha/tools.py:127` — `except (UnicodeDecodeError, OSError)` | correct |

`tools.py:127` is in `_search_files_handler`. `tools.py:72` is in `_read_file_handler` —
**the same file, ten lines apart**, and it does not. That asymmetry is the clearest
evidence these are oversights rather than decisions, and it makes the fix mechanical.

---

## 1. Confirmed findings, ranked

### S1 — `write_file` destroys the target file when the write fails — **HIGH (data loss)**

`src/sasha/tools.py:53-62`

```python
try:
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_text(content)          # truncates FIRST, then encodes
except OSError as exc:                    # UnicodeError is not an OSError
    return ToolResult(f"error writing {raw_path}: {exc}")
```

Two independent defects:

1. **The exception escapes.** `Path.write_text` opens the file with mode `"w"` —
   **truncating it** — and only then encodes the content. An encode failure therefore
   cannot possibly be reported by the existing `except OSError`: the file is already gone.
2. **The write is not atomic.** Even a *caught* failure (e.g. `ENOSPC` mid-write) leaves a
   truncated or partial file while the model is told only "error writing X".

Reproduced, with no unusual locale involved — the content comes from ordinary model
output, because JSON permits a lone surrogate:

```
content from JSON is a lone surrogate: '\ud800'
*** UnicodeEncodeError ESCAPED the handler: 'utf-8' codec can't encode character '\ud800' ...
file now contains: ''
...size: 0 bytes (was 38)
```

`json.loads('"\\ud800"')` yields a lone surrogate, and `llm.py` passes tool-call arguments
through `json.loads` untouched, so a model can reach this. The realistic non-exotic trigger
is a full disk mid-write — same truncate-then-fail path, same destroyed file.

**The pattern already exists in this repo**: `tool_log.py:65-70` writes to a temp file and
`os.replace`s it. `write_file` just does not use it.

### S2 — one bad byte in general memory stops Sasha starting, everywhere — **HIGH**

`src/sasha/memory.py:137` (inside `_load_memory_block_at`) and `:235`
(`_most_recent_gist_at`) do `path.read_text()` with nothing catching a decode failure, and
both are on the startup path:

```
src/sasha/repl.py:530   general_block, _ = memory.load_general_memory_block()
src/sasha/repl.py:532   if not self._check_place_shift():
src/sasha/repl.py:537       while True:            # <- crash net starts here
```

Both calls sit **before** `while True:`, so they are outside the crash net and propagate to
`main()`'s setup handler. `load_general_memory_block()` reads
`~/.config/sasha/memory/semantic.jsonl`, shared by *every* directory:

```
*** load_general_memory_block RAISED: UnicodeDecodeError 'utf-8' codec can't decode byte 0xe9 ...
```

and for project memory:

```
*** ESCAPED _check_place_shift: UnicodeDecodeError 'utf-8' codec can't decode byte 0xe9 ...
```

Net effect: `sasha: failed to start: ...` with no REPL, in every directory at once — and
Sasha is the tool you would use to debug it. Note Sasha will not *create* such a file
itself: `json.dumps` defaults to `ensure_ascii=True`, so its own output is pure ASCII. The
realistic triggers are a hand-edit (the README documents this file), an appending tool, or
a copy from another encoding.

### S3 — one non-UTF-8 `SKILL.md` prevents startup — **HIGH, likelihood moderate**

`src/sasha/skills.py:138`, `:157`, `:181` — three `read_text()` calls, all guarded by
`except OSError` only. `build_skills_listing()` runs during setup (`__main__._run`), so:

```
*** UnicodeDecodeError ESCAPED build_skills_listing: 'utf-8' codec can't decode byte 0xe9 ...
```

`~/.config/sasha/skills/` is documented as a place to install third-party skill libraries
(the repo's own history mentions installing `superpowers` and `ponytail`). User-contributed
markdown is exactly where a stray latin-1 or UTF-16 file appears, and one of them takes the
whole program down. Because this also runs before the REPL, there is no recovery path
except deleting the file.

### S4 — `read_file` on any binary file aborts the consult — **HIGH likelihood**

`src/sasha/tools.py:72`. The tool is **auto-approved** (`requires_confirmation=False`), so
there is no gate to catch it:

```
*** UnicodeDecodeError ESCAPED the handler: 'utf-8' codec can't decode byte 0x80 in position 128
```

while the sibling in the same file returns cleanly:

```
search_files returned: no matches
```

Any model exploring a repository will eventually read a `__pycache__/*.pyc`, an image, a
zip, or a UTF-16 file. The exception propagates `tool handler → run_consult → _consult`
(which catches only `ChatError`) `→ run_once → loop()`'s crash net, so the impact is: the
consult is aborted, the model's work is lost, and the circuit breaker counts it — **three
such reads in a session and Sasha exits.**

### S5 — a non-UTF-8 working directory breaks every command in it — **MODERATE**

`src/sasha/executor.py:427` and `:430`:

```python
final_cwd_raw = side_data[:nul_idx].decode().strip()   # strict UTF-8
env_data = side_data[nul_idx + 1:].decode()            # strict UTF-8
```

The side channel carries raw bytes from `pwd -P`. Filenames that are not valid UTF-8 are
legal on Linux and `os` represents them with `surrogateescape`, but decoding the raw bytes
strictly raises `UnicodeDecodeError` — *after* the `finally` that restores the terminal, so
the terminal is fine but the command's result is lost and `self._cwd` is never updated. Every
subsequent command in that directory fails the same way, so the breaker trips and the
session exits. Narrow trigger (you must actually `cd` into such a directory), total impact
when hit.

### S6 — 23 text-I/O sites depend on the ambient locale — **MODERATE, needs an unusual env**

No `read_text()`, `write_text()` or `open()` in `src/` passes `encoding=`. They inherit
`locale.getpreferredencoding(False)`.

I expected `LC_ALL=C` to be the trigger and **it is not** — Python auto-enables UTF-8 mode
for the C/POSIX locale (PEP 540), which I verified:

| Environment | preferred encoding | `utf8_mode` |
|---|---|---|
| `LC_ALL=C` | `utf-8` | 1 |
| `LC_ALL=C PYTHONUTF8=0` | `ANSI_X3.4-1968` | 0 |
| `LC_ALL=C.UTF-8` | `UTF-8` | 0 |

So the risk needs `PYTHONUTF8=0` in the environment or a non-UTF-8, non-C locale
(e.g. `en_US.ISO-8859-1`). Under such an environment:

```
*** write_text FAILED: UnicodeEncodeError 'ascii' codec can't encode character '\xe9' ...
```

The worst instance is `tools.py:59` (`write_file`), which is S1's destructive path again,
now triggered by ordinary accented content (`café`, `ñ`) rather than a surrogate. The
practical fix is one argument, and it makes the behaviour identical in every environment.

Worth noting the JSON paths are *accidentally* immune: `json.dumps(ensure_ascii=True)`
escapes non-ASCII, so `memory`, `tool_log` and `llm_debug` write pure ASCII regardless of
locale. That protection is a default nobody chose, and would vanish if anyone passed
`ensure_ascii=False`.

### S7 — strict decode of the model's stream — **LOW**

`src/sasha/llm.py:97` — `line_bytes.decode("utf-8").strip()`. Invalid UTF-8 from the server
raises, is caught by the broad `except Exception`, and surfaces as
`ChatError("Unexpected error: ...")`. Not a crash, but the message names neither the cause
nor the byte. Line-oriented iteration means a multi-byte character split across TCP reads
is *not* a problem — the file object buffers to `\n`. Low severity, poor diagnostics.

### S8 — `subprocess(text=True)` decodes with the locale, strictly — **LOW / unproven**

Eight sites (`executor.py:500`, `memory.py:23/179/204`, `completion.py:84`,
`git_headline.py:32`, `guard.py:101/106`) use `text=True` with no `encoding=`. The one I
could plausibly break — `complete_via_bash` over a non-UTF-8 filename — returned `[]`
rather than raising, so **I could not reproduce a failure and am flagging this as
theoretical, not confirmed.** If it does raise, the caught set is only
`(subprocess.TimeoutExpired, OSError)`, and the exception would leave the readline completer
and reach `main`'s setup handler.

### S9 — the crash report is written in two different encodings — **LOW**

`src/sasha/guard.py:164` writes the `.txt` with the locale encoding; `:168` writes the
`.json` twin via `json.dumps` (ASCII-escaped, locale-proof). A traceback containing any
non-ASCII character under a hostile locale therefore loses the human-readable report while
keeping the machine-readable one. Failure is handled gracefully (`write_crash_bundle`
returns `None` and the traceback is printed instead), so this is cosmetic-to-annoying, not
harmful — but it is an inconsistency of exactly the kind asked about.

---

## 2. Verified clean (worth stating, because this is the risky area)

- **`_kb_relay_helper.py`** — the keystroke relay you depend on for `less`/`vim`/`htop` is
  pure `os.read`/`os.write` on raw bytes. No decode, no encode, no text-mode I/O. Nothing
  can corrupt a keystroke.
- **`executor.py:41` `_sanitize`** — strips ANSI at the *byte* level (`re.compile(b'…')`)
  and only then decodes with `errors='replace'`. Arbitrary command output, including
  invalid UTF-8, cannot break capture. This is the highest-volume string path in the
  program and it is correct.
- **`llm.py`** — outbound `json.dumps(payload).encode("utf-8")`, inbound
  `.decode("utf-8")`: explicit and consistent. No encode/decode mismatch.
- **JSON persistence** — `memory`, `tool_log`, `llm_debug` and the crash-report `.json`
  are all `json.dumps` with the `ensure_ascii` default, so locale-proof and
  newline-safe (each record is one line, and pretty-printed JSON — which would break the
  line-oriented readers — is nowhere used).
- **`tools.py` `_safe_path`** — resolves and bounds-checks the path before any I/O, so the
  reading/writing story is at least not also a traversal problem.

---

## 3. Non-string findings from the same run

**mypy (16)** — none are bytes/str. Two categories worth a look:

- **Seven `int | None` fd arguments in `executor.py`** (`:191`, `:192`, `:221`, `:222`,
  `:235`, `:289`, `:416`). Most are narrowed by `interactive`/`is not None` checks that mypy
  cannot follow, but `:416` (`waitstatus_to_exitcode(status)`) is worth confirming: if any
  path reaches the result-building code with `status` still `None`, that is a `TypeError`
  rather than a wrong exit code. I traced the obvious paths and they all assign `status`;
  this is a *bounding* opportunity, not a demonstrated bug.
- **`session.py:116/118`** — genuine type confusion mypy does catch: `msg` is inferred as
  `dict[str, str]` from its literal, then assigned a list and `.append`ed. Runtime-correct
  today; the kind of latent mis-inference a later edit turns into a real bug. One annotation
  (`msg: dict[str, Any]`) settles it.

**ruff (104 in `src`, after excluding `S101`)** — mostly `E501` line length (cosmetic) and
`S110`/`SIM105` for the deliberate best-effort `try/except/pass` handlers in the crash net.
Genuinely worth acting on:

- `session.py:10` — `F401` `ToolCall` imported and unused.
- **4 × `B904`** — `raise ... ` inside `except` without `from`, discarding the cause chain.
  In a program whose whole premise is "explain the failure", losing the chain is a poor fit.
- `S607`/`S603`/`S606` — subprocess invocation hygiene (partial paths, no `shell=True`
  justification). Not a string issue, but adjacent to the same subprocess sites as S8.
- `S310` — `urlopen` with a computed URL. Expected: that is the LLM client.

---

## 3a. CORRECTION, found while fixing

The section above calls the `read_file` / `search_files` difference an
"oversight" because `search_files` catches `UnicodeDecodeError` and `read_file`
does not. That reading was **half wrong**, and the contract suite proved it: my
first fix made `search_files` read with `errors="replace"`, and
`test_ac_search_files_skips_binary_files` went red immediately.

`search_files` skipping undecodable files is *deliberate and pinned*: you do not
want grep hits found inside a `.pyc`. `read_file` letting the exception escape
was still a genuine bug, but the correct fix differs by intent:

- `read_file` — the model explicitly asked for this file, so answer: read with
  `errors="replace"` and tell it the file is not valid UTF-8.
- `search_files` — keep the strict read so binaries stay skipped.

The lesson is the one this whole exercise keeps teaching: an asymmetry between
two similar call sites is evidence, not proof, and the difference can be the
design.

---

## 4. Recommended fix, if you want it

The whole family collapses to four mechanical changes:

1. **Always pass `encoding="utf-8"`** on text file I/O (23 sites). One argument each;
   removes S6 and S9 and makes behaviour environment-independent.
2. **Widen the boundary handlers to `(OSError, UnicodeError)`** — or catch `UnicodeError`
   explicitly where the content is untrusted (a file the model asked to read, a skill file,
   a memory file). Removes S2, S3, S4 and the escaped half of S1.
3. **Make `write_file` atomic** — write a sibling temp file, then `os.replace`, exactly as
   `tool_log.py:65-70` already does. Removes S1's data-loss half; a failed write then leaves
   the original file untouched.
4. **Decode the side channel with `errors="surrogateescape"`** — matching how `os` handles
   paths, so a non-UTF-8 directory name round-trips instead of raising. Removes S5.

Plus one test per confirmed finding (S1–S5), each of which is a few lines given the
reproductions above.

I have **not changed any code** — this is analysis only. Nothing is committed except the
findings document itself, and the analyser venv lives in the gitignored `scratchpad/`.


---

## 5. WHAT WAS ACTUALLY FIXED (2026-10-12, ISSUE-072)

Implemented in one pass, direct Tier 1 work:

| Finding | Fix |
|---|---|
| S1 write_file destroys the target | New `_atomic_write_text`: sibling temp file + `os.replace`, preserving the original mode; a new file is written directly so the umask still applies. `UnicodeError` is now caught and reported. The "wrote N bytes" count is now actually bytes, not characters. |
| S2 undecodable project/general memory | `encoding="utf-8", errors="replace"` on both `semantic.jsonl` reads. Sasha still starts, and the other entries are intact. |
| S3 undecodable `SKILL.md` | Same treatment on all three `skills.py` reads: a bad third-party skill file no longer prevents startup. |
| S4 `read_file` on a binary | `errors="replace"` plus an explicit `[note: this file is not valid UTF-8 …]` prefix for the model. |
| S5 non-UTF-8 cwd | Side channel decoded with `surrogateescape`, matching how `os` represents such paths, so it round-trips instead of raising. |
| S6 locale-dependent I/O | `encoding="utf-8"` on all 23 sites. |
| S7 surrogates reaching a strict stdout | New `src/sasha/textio.py::printable()`, applied to the rendered prompt and to all four stdout chokepoints in `TerminalSink`. |
| S8 compaction mangling a log | `compact()` refuses and returns unchanged when the log cannot be decoded, rather than rewriting it mangled. |
| S9 crash report encoding | The `.txt` report is written with `encoding="utf-8", errors="replace"` so a report is always produced. |

Two further bugs surfaced while fixing:

- **`printable()` was wrong twice before it was right.** `surrogateescape` cannot
  encode a lone `\ud800` at all (it only covers U+DC80-U+DCFF), and
  `surrogatepass` turns one surrogate into three bytes that then decode to
  *three* U+FFFD. The shipped version substitutes on the `str` directly, which
  is exact: surrogates are the only code points that fail UTF-8.
- **My "fix" to `search_files` was a regression** (see section 3a).

Also removed four dead imports found by ruff along the way, one of which
(`contextlib.contextmanager` in `llm.py`) I had orphaned myself when the
background-priority gate was deleted.

18 regression tests in `tests/impl/test_string_handling_hardening.py`, one per
finding plus the two self-inflicted bugs above. Full suite: 787 passed.
