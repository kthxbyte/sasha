# ISSUE-022: Configurable prompt — bash $PS1-style convention

**Status:** Design note only — explicitly deferred, not specced, not scheduled.
**Discovered:** 2026-07-15, in conversation following Task 6.2 (colorization +
user@host prompt) landing.

## Context

Task 6.2 hardcodes the prompt format in `Repl.loop`:
`user@host:path ✦ ` (green when `color_enabled`, via `\001`/`\002`-wrapped
ANSI codes). Salvador asked whether Sasha should instead follow bash's
`$PS1` convention for prompt definition, so the prompt is configurable the
way bash users already expect.

## Findings

1. **`$PS1` is not exported to child processes by default.** Confirmed
   empirically:
   ```
   >>> import os
   >>> os.environ.get("PS1")
   >>>
   ```
   `PS1` is a shell variable, not an environment variable — `~/.bashrc` sets
   it fresh in each interactive shell rather than inheriting it, unless the
   user explicitly ran `export PS1=...`. A naive `os.environ.get("PS1")`
   read would silently return nothing for most users.

2. **Real `$PS1` values are genuinely complex**, not just color + escapes.
   Salvador's actual prompt:
   ```
   \[\e]0;$(print_title)\a\]${debian_chroot:+($debian_chroot)}\[\033[01;32m\]\u@\h\[\033[00m\]:\[\033[01;34m\]\w\[\033[00m\]\$
   ```
   Breaking it down:
   - `\[\e]0;$(print_title)\a\]` — sets the terminal tab title via an OSC
     escape sequence, and calls a shell **function** (`print_title`) via
     command substitution `$(...)` to compute the title text.
   - `${debian_chroot:+($debian_chroot)}` — bash parameter expansion
     (conditionally show chroot name).
   - `\[\033[01;32m\]\u@\h\[\033[00m\]` — bold green `user@host`, `\[ \]`
     wrapping raw ANSI (bash's equivalent of Sasha's `\001`/`\002`
     readline-ignore markers — same underlying problem, same fix shape).
   - `:` — literal.
   - `\[\033[01;34m\]\w\[\033[00m\]` — bold blue full cwd.
   - `\$` — prompt character (`#` for root, `$` otherwise).

   A real PS1 parser would need to support shell command substitution and
   parameter expansion to be faithful — i.e., actually invoke a shell to
   evaluate the string, not just template-substitute a few tokens. That's
   meaningfully more scope (and a command-injection-shaped surface, since
   the config would now contain executable shell) than a handful of
   `\u`/`\h`/`\w`/`\W` escapes.

## Recommendation (not yet agreed to be implemented)

Layered lookup, cheapest-first:
1. `os.environ.get("PS1")` — free when it works (user explicitly exported
   it), no-op otherwise.
2. A `prompt` key in `~/.config/sasha/config.toml` supporting a **small,
   template-substituted subset**: `\u` (user), `\h` (host), `\w` (full
   cwd), `\W` (basename), and `\[ \]` for wrapping raw ANSI (mapping
   directly onto Sasha's existing `\001`/`\002` mechanism from Task 6.2).
   No command substitution, no parameter expansion, no shell evaluation —
   pure string templating.
3. Current hardcoded default (`user@host:path ✦ `), unchanged, if neither
   of the above is set.

Explicitly **not** in scope for a first pass: `$(...)` command
substitution, `${...}` parameter expansion, OSC title-setting, or any
other escape beyond the four listed. Full PS1 fidelity would require
shelling out to evaluate the string, which is both meaningfully more
implementation work and a real security consideration for a value read
from a config file.

## Next steps (when picked up)

Brainstorm + design spec via the usual Tier 1 flow if/when Salvador wants
to schedule this — this issue is a note to start from, not a task ready
for dispatch.
