# ISSUE-056 — Directory-recap cold-start fallback (marker-file content-sniff)

**Status:** Draft for review
**Date:** 2026-07-28

## 1. Mission

`directory_recap.py`'s cascade (ISSUE-051) and the explicit `sasha recap`
command can only describe a directory using two signals: git commit history
(self + child repos) and `tool_log.jsonl` command history for that subtree.
When a directory has neither — a freshly cloned or extracted project with no
recorded Sasha activity, and no git history at all — both paths currently
produce nothing worth saying. This adds a third, static content-sniff
signal (README excerpt + top-level file listing) so a genuine cold-start
directory can still get a real recap, while gating the *passive* cascade
tightly enough that it doesn't fire on ordinary non-project folders
(`~/Downloads`, `/etc`, cache/vendor directories).

## 2. Scope

### In scope (v1):
- A new context-gathering step in `gather_context()`, engaged only when
  `self_commit`, `child_commits`, and `recent_commands` are all empty: a
  non-recursive top-level scan for a README excerpt and a capped filename
  listing, fed into `build_prompt()` as a new conditional section.
- A new escalation branch in `decide()`'s passive cascade: when the subtree
  has no `tool_log` history (today's unconditional `SILENT` case), check for
  a top-level marker file before giving up; escalate instead of staying
  silent if one is found and the directory isn't vendor/cache-excluded.
- The marker-file check and the vendor/cache exclusion rule (both detailed
  below), as plain module constants in `directory_recap.py`.
- Contract tests encoding the empirically-verified exclusion behavior
  (dot-prefixed ancestors, named vendor directories, no source-extension-count
  rule).

### Out of scope / non-goals:
- No use of file mtime or ctime anywhere. Rejected during design: `ctime` on
  Linux is inode-change time, not creation time, and `mtime` is routinely
  bulk-reset by `git clone`/checkout, `tar` extraction, `rsync` without `-t`,
  and `docker COPY` — exactly the operations that produce a directory with no
  git history yet, i.e. precisely the case this feature targets. Since
  `build_prompt()` instructs the LLM to "use only the data given — do not
  invent," a false mtime would be reported as fact, not just unhelpful.
- No "≥N files with a recognized source extension" heuristic. Empirically
  measured (disposable scan script, not committed) against Salvador's real
  `$HOME`: this rule alone fired on 44% of all 11,723 scanned directories,
  overwhelmingly false positives — vendored language source trees
  (`~/go/pkg/mod/...`) and browser "Save Page As" asset folders
  (`*_files/` containing incidental `.html`/`.js`/`.css`) both trip a
  ≥2-extension threshold with zero relation to the user's own work.
- No recursive directory walk anywhere in the real (non-scratch) code —
  only `pathlib.Path.iterdir()` on the target directory itself, matching
  `_child_git_repos()`'s existing non-recursive pattern.
- No new config.toml surface — all thresholds are plain module constants,
  matching `MAX_CHILD_REPOS_FOR_LLM`/`MAX_LOG_ENTRIES_FOR_LLM`.
- No change to the `SILENT`/`RECAP_DIRECT`/`ESCALATE` verdict vocabulary —
  this only changes what feeds an existing `ESCALATE`.
- No attempt to enumerate every language's package-cache convention by name
  (see Decisions log — `go/pkg/mod` accepted as a known, low-consequence gap).

## 3. Key contracts & assumptions

1. **Cold-start-only.** The new content-sniff in `gather_context()` only
   engages when `self_commit`, `child_commits`, and `recent_commands` are
   *all* falsy/empty. Any existing signal takes priority; this never
   overrides or blends with real git/log history.
2. **Non-recursive, target-directory-only scan.** The README/listing scan
   uses `target.iterdir()` on the directory itself — never descends, never
   inspects children's contents.
3. **Marker-file check, not source-extension-count.** The passive cascade's
   new escalation branch is gated solely by presence of a known marker
   filename at the top level (case-insensitive). No file-extension-counting
   rule exists anywhere in this feature.
4. **Two-part vendor/cache exclusion, checked before the marker check:**
   (a) any path component of the target directory's absolute path — from
   filesystem root down to and including the target itself — starts with
   `.`; or (b) any path component exactly matches a name in
   `VENDOR_DIR_NAMES`. Either match forces `SILENT` regardless of markers
   found. This is a *structural* rule (generalizes to any future tool that
   follows the same convention of a dot-prefixed state/cache directory),
   confirmed empirically against real `~/.cache` (chrome, mesa shader cache,
   fontconfig, go-build, huggingface, etc., all sharing one dot-prefixed
   parent) rather than an enumerated per-application list.
5. **The vendor/cache exclusion applies only to the passive cascade.**
   Explicit `sasha recap` always runs the content-sniff regardless of
   vendor/cache status — the user asked directly, so an honest description
   (even of a dependency's own README) is appropriate; the exclusion exists
   only to control passive *firing frequency*, not content correctness.

## 4. Architecture

### 4.1 `gather_context()` — new fallback context source

When `self_commit`, `child_commits`, and `recent_commands` are all
empty/falsy, add two new keys to the returned dict:

- `readme_excerpt: str | None` — first `README_EXCERPT_CHARS` (500)
  characters of the first top-level file whose name (case-insensitive)
  starts with `readme`, or `None` if none exists.
- `top_level_listing: list[str]` — up to `TOP_LEVEL_LISTING_MAX` (20)
  non-hidden entry names at the top level, sorted alphabetically, or `[]`
  if the directory is empty/unreadable.

Both come from a single `target.iterdir()` call (mirrors
`_child_git_repos()`'s existing `PermissionError`/`OSError` handling —
return empty rather than raising).

### 4.2 `build_prompt()` — new conditional section

If `context.get("readme_excerpt")` or `context.get("top_level_listing")` is
present, append:

```
Directory contents:
  README excerpt: <first 500 chars>
  Files here: <comma-joined top_level_listing>
```

Same shape as the existing `child_commits`/`recent_commands` sections —
appended only when non-empty, no change to the existing instruction
preamble ("use only the data given ... if nothing coherent, output
nothing").

### 4.3 `decide()` — passive cascade's new escalation branch

Current code (`directory_recap.py:131-133`):
```python
subtree = _subtree_entries(str(target), log_entries)
if not subtree:
    return (SILENT, None)
```

New behavior at that same point:
```python
subtree = _subtree_entries(str(target), log_entries)
if not subtree:
    if _has_project_marker(target) and not _is_vendor_or_cache(target):
        return (ESCALATE, None)
    return (SILENT, None)
```

`_has_project_marker(target)` — non-recursive `target.iterdir()`, true if
any top-level entry's name (case-insensitive) is in `MARKER_NAMES` or
matches `MARKER_PREFIXES` (`readme`, `license`) as a prefix, or ends with
`.sln`.

`_is_vendor_or_cache(target)` — true if any component of
`target.resolve()`'s parts (excluding the leading filesystem-root
component) starts with `.`, or exactly matches an entry in
`VENDOR_DIR_NAMES`.

This single inserted branch also naturally fixes the pre-existing gap noted
during ISSUE-051 review: a directory that *is* a git repo but has no
`tool_log` subtree entries previously returned `SILENT` without ever
checking `self_is_repo` or attempting a commit lookup. A real git repo's
top level almost always contains a marker file (README/LICENSE/manifest),
so it now correctly escalates instead.

## 5. Constants (module-level, `directory_recap.py`, no config surface)

```python
README_EXCERPT_CHARS = 500
TOP_LEVEL_LISTING_MAX = 20

MARKER_NAMES = {
    "package.json", "pyproject.toml", "setup.py", "cargo.toml", "go.mod",
    "gemfile", "pom.xml", "build.gradle", "makefile", "cmakelists.txt",
    "dockerfile", ".envrc", "requirements.txt", "composer.json",
}
MARKER_PREFIXES = ("readme", "license")  # matches README, README.md, LICENSE.txt, etc.
# *.sln matched separately (suffix check)

VENDOR_DIR_NAMES = {
    "node_modules", "venv", "site-packages", "dist", "build", "target",
    "vendor", "__pycache__",
}
```

## 6. Testing strategy

Contract tests build synthetic directories under `tmp_path` (no real
filesystem scanning of `$HOME` in the test suite — the empirical home-dir
scan was a disposable, uncommitted verification script). Coverage:

1. Marker file present, no vendor/cache ancestor → `decide()` returns
   `ESCALATE`.
2. No marker file, no history → `decide()` returns `SILENT` (unchanged
   baseline behavior).
3. Marker file present but under a dot-prefixed ancestor (e.g.
   `tmp_path/.cache/some-app`) → stays `SILENT` despite the marker.
4. Marker file present but under a `VENDOR_DIR_NAMES` ancestor (e.g.
   `tmp_path/node_modules/some-pkg`) → stays `SILENT` despite the marker.
5. A directory that *is* a git repo, has a marker file, but has zero
   `tool_log` subtree entries → now escalates (previously silently missed).
6. A directory with several files matching common source extensions
   (`.py`, `.js`) but no marker file and no history → stays `SILENT` —
   directly encodes the empirical finding that the source-extension-count
   rule is intentionally absent, so it can't quietly regress back in.
7. `gather_context()` in the cold-start case returns a non-empty
   `readme_excerpt`/`top_level_listing` when a README exists at the top
   level, and empty values when the directory has neither history nor a
   README.
8. `build_prompt()` includes the new "Directory contents:" section only
   when those keys are non-empty, and omits it entirely when the existing
   three signals are all present (no behavior change for the common case).
9. Explicit `sasha recap`'s existing call path (`repl.py`'s
   `_handle_recap_builtin`) still runs the content-sniff even inside a
   vendor/cache directory — the exclusion is passive-cascade-only.

## 7. Decisions log

- **Decision:** Drop mtime/ctime entirely; use only static content
  (README + listing).
  **Rationale:** `ctime` is commonly misread as creation time (it isn't,
  on Linux); `mtime` is bulk-reset by exactly the operations (clone,
  extract, sync) that create the cold-start case this feature targets,
  and a false recency claim is actively misleading, not merely unhelpful.
- **Decision:** Drop the "≥N source-extension files" rule entirely.
  **Rationale:** Empirically measured at 44% false-positive rate against
  a real `$HOME` (11,723 directories scanned); driven by vendored
  language source trees and browser-saved-page asset folders, neither of
  which relate to the user's own project.
- **Decision:** Exclude by structural rule (dot-prefixed ancestor) rather
  than an enumerated per-application cache directory list.
  **Rationale:** `~/.cache` alone was confirmed to host dozens of
  unrelated per-app caches (chrome, mesa, fontconfig, go-build,
  huggingface) under one dot-prefixed parent; an enumerated list would
  always lag behind new tools, while the structural rule generalizes for
  free.
- **Decision:** Additionally exclude a short named list
  (`node_modules`, `venv`, `site-packages`, `dist`, `build`, `target`,
  `vendor`, `__pycache__`) since these don't follow the dot-prefix
  convention.
  **Rationale:** Empirically measured 6-32% marker-file false-positive
  rates inside real `node_modules`/`venv`/`site-packages` trees — every
  npm package ships its own `package.json`/`LICENSE`, every Python
  package's `dist-info` ships its own `LICENSE`; these are routinely
  `cd`-ed into (unlike deep language-cache internals), so leaving them
  ungated would misfire often.
- **Decision:** Accept `~/go/pkg/mod/...`-style package caches (neither
  dot-prefixed nor on the named vendor list) as a known, unaddressed gap.
  **Rationale:** Only ~6 marker-file hits out of 667 in the full-home
  scan came from this source, and it's only reachable by manually `cd`-ing
  deep into vendored Go stdlib internals — low frequency, low consequence
  (worst case is an accurate description of a dependency's own README).
  Enumerating every language's module-cache convention by name was judged
  not worth chasing pre-emptively (YAGNI); revisit if it proves noisy in
  practice.
- **Decision:** The vendor/cache exclusion gates the *passive* cascade
  only; explicit `sasha recap` always runs the content-sniff.
  **Rationale:** The exclusion exists to control unwanted passive firing
  frequency on ordinary `cd`s, not to hide accurate information from a
  user who explicitly asked for a recap.
