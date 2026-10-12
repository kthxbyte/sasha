# Sasha

An LLM-backed interactive shell wrapper. You type shell lines; they run exactly
as bash would. When a line fails, names a command that doesn't exist, or is
plain natural language, Sasha consults an LLM grounded in your actual shell
context — and nothing the LLM proposes ever runs without your confirmation.

## Requirements

- Python ≥ 3.11
- Linux, bash at `/bin/bash`
- Stdlib-only at runtime — no third-party packages required

## Install

On Debian/Ubuntu-family systems (Debian 12+, Ubuntu 23.04+, and derivatives
like elementary OS), a plain `pip install -e .` will refuse with
`externally-managed-environment` — [PEP
668](https://peps.python.org/pep-0668/) blocks `pip` from writing into the
same Python `apt` manages. Sasha is a zero-dependency CLI you want available
everywhere, so [pipx](https://pipx.pypa.io/) — which installs it into its own
isolated environment and puts just the `sasha` command on your `$PATH` — is
the recommended way to install it:

```
pipx install --editable .
```

(`--editable` points pipx at this source tree instead of copying it, so
changes to `src/sasha/` take effect immediately — useful if you're hacking
on Sasha itself, harmless if you're not.)

If you'd rather manage the environment yourself (e.g. to also install the
`pytest` dev dependency), use a venv instead:

```
python3 -m venv .venv
source .venv/bin/activate
pip install -e .[dev]
```

Run the full suite with `scripts/run-tests.sh`. On a system that isn't
externally-managed (or inside either environment above), the classic
`pip install -e .` also works.

## Quick start

```
sasha
```

Runs with the default profile (`llm7` — free, keyless, streaming, zero
setup). First run self-installs `config.toml` and `PERSONALITY.md` for you.
`python -m sasha` works identically — same entry point, invoked as a module
instead of via the installed console script.

```
sasha --profile local
# or: python -m sasha --profile local
```

Runs against the `local` profile instead of the default — point its
`base_url` in `config.toml` at your own llama.cpp (or compatible) server
first; see Configuration below. `--profile <name>` works with any profile
you've defined, not just `local`.

Other flags: `--config <path>` to use a config file somewhere other than
`~/.config/sasha/config.toml`, `--reasoning compact|full` to control how much
of the model's reasoning is shown (see Reasoning display below), `--version`,
`-h`/`--help`.

`sasha repair` starts a session for working on Sasha itself — see Repair mode
below. It takes `--bundle <path>` (which crash report to work from) and
`--repair-profile <name>` (which model to use).

## Configuration

Sasha reads `~/.config/sasha/config.toml`. On first run, if the file doesn't
exist, Sasha writes one for you (and says so, once):

```toml
default_profile = "llm7"

# How much of the model's reasoning to show on screen:
#   "compact" (default) — one line, updated in place, advancing as each
#                          reasoning paragraph's first line completes. The
#                          full text stays available via 'sasha reasoning'.
#   "full"              — stream every reasoning chunk, as in earlier versions.
# Override per session with --reasoning compact|full.
reasoning_mode = "compact"

# Which profile 'sasha repair' uses. Worth pointing at a frontier model even
# if you drive day-to-day work on a local one. Empty = reuse the active
# profile. Override with --repair-profile <name>.
repair_profile = ""

[profiles.llm7]
base_url = "https://api.llm7.io/v1"
model = "gpt-oss:20b"
api_key = ""
api_key_required = false

[profiles.openai]
base_url = "https://api.openai.com/v1"
model = "gpt-4o-mini"
api_key = ""
api_key_required = true

[profiles.deepseek]
base_url = "https://api.deepseek.com/v1"
model = "deepseek-chat"
api_key_env = "DEEPSEEK_API_KEY"
api_key = ""
api_key_required = true

[profiles.local]
base_url = "http://192.168.8.253:11434/v1"
model = "local"
api_key = ""
api_key_required = false

[limits]
history_tokens = 12000
stderr_capture_bytes = 8192
max_tool_turns = 10

[safe_commands]
sensitive_paths = [
  "~/.ssh/**", "/etc/passwd", "/etc/shadow*", "/etc/sudoers*",
  "~/.aws/**", "~/.gnupg/**", "~/.kube/**", "~/.config/gcloud/**",
  "~/.docker/config.json", "~/.netrc", "~/.pgpass",
  "~/.git-credentials", "~/.npmrc", "**/.env", "**/.env.*",
]

[tool_log]
max_size_mb = 100

[activity_indicator]
enabled = true
interval_s = 0.05
thinking_color = "#C084FC"
tool_color = "#FFD700"

[llm_debug]
enabled = false
```

- `[safe_commands] sensitive_paths` — glob patterns that keep a command
  out of `run_command`'s safe-auto-approval, even if built entirely from
  allowlisted verbs — see Tools below.
- `[tool_log] max_size_mb` — see State below.
- `[activity_indicator]` — the terminal cursor animation shown while the
  LLM is thinking or a tool is running; `enabled = false` turns it off.
- `[llm_debug] enabled` — raw request/response logging for
  troubleshooting; see State below.
- `prompt`, `sasha_color`, `reasoning_color` — the prompt-line template
  (bash-`PS1`-style `\u`, `\h`, `\w`, `\W`, plus `\[token\]` colour escapes)
  and the colours used for Sasha's own lines and for reasoning text. These are
  **not** in the self-installed file — add them yourself, e.g.
  `prompt = "\[magenta\][sasha]\[reset\] \[bold-blue\]\w\[reset\] $ "`. Valid
  colour tokens are the names in `src/sasha/prompt.py` (`red`, `bold-cyan`,
  `dim-white`, …). A malformed template is a startup config error, not a
  silently ignored key.

`[limits]` is written by the self-installed file with the built-in defaults
shown in the example above; drop the keys you don't need, or delete the whole
table to fall back to the same defaults. `max_tool_turns` (10 by default) is
the ceiling on tool calls in one consult — raise it if Sasha stops mid-task
with `[sasha] stopping: hit the 10-action limit`.

Four profiles are configured by default:

- **llm7** — the default. Free, keyless, streaming — works with zero setup.
  Best-effort only; the provider gives no uptime guarantee.
- **openai** — the right endpoint and model, no key yet.
- **deepseek** — the right endpoint and model; also reads `DEEPSEEK_API_KEY`
  from the environment if set.
- **local** — for a local llama.cpp server at `192.168.8.253:11434`. No API
  key required. Note that llama.cpp ignores the `model` field (it serves
  whatever it has loaded), so `model = "local"` works as-is; set it to the
  GGUF id only if your server is strict about it.

Switch profiles at runtime with `sasha profile <name>`. If the profile needs
a key it doesn't have (`api_key_required = true` and no key set), Sasha asks
you to paste one — input is hidden, and pressing Enter with nothing typed
cancels without switching. The key is written straight into `config.toml`
(only that profile's `api_key` line changes — nothing else in the file is
touched) so you only enter it once.

## Personality

Sasha reads `~/.config/sasha/PERSONALITY.md` and includes its content in the
system prompt sent on every request. On first run, if the file doesn't
exist, Sasha writes a default template for you (and says so, once) explaining
what it's for — identity, tone, and behavior notes are all fair game. Edit it
freely; changes take effect the next time you start Sasha. It's capped at
8000 characters (an over-long file is truncated for that session, with a
one-time notice). This only shapes tone — the confirmation gate and every
other safety behavior described below is unconditional and unaffected by it.

## Usage

Start Sasha:

```
$ sasha

 Profile    : llm7 (model: gpt-oss:20b)
 API server : https://api.llm7.io/v1
 Tip : type ?? followed by a question to chat with me directly
[sasha] user@host:~/project $ 
```

Sasha prints its active profile, the API endpoint, and a one-time tip about
`??` once at the start of every session. On a genuinely first run, you'll
also see one-time setup notices above the banner — `config.toml` and
`PERSONALITY.md` both self-install and announce themselves the first time
they're created.

The prompt itself is `[sasha] user@host:cwd $ ` — bash-`PS1`-style, with
`$HOME` abbreviated to `~`. It is fully configurable; see `prompt` in
Configuration above.

While Sasha is waiting on the model — or running a command it proposed — the
terminal cursor is animated instead of the prompt, driven by
`[activity_indicator]` in Configuration. Nothing about it touches your command
output, and `enabled = false` turns it off.

### Normal commands

Type any shell command and press Enter — it runs exactly as it would in bash:

```
[sasha] user@host:~/project $ ls
[sasha] user@host:~/project $ echo hello
[sasha] user@host:~/project $ cd /tmp
[sasha] user@host:/tmp $ 
```

### Explicit ask

Prefix a line with `?? ` to consult the LLM directly:

```
[sasha] user@host:~/project $ ?? how do I find files larger than 100MB
```

The LLM receives your shell context (cwd, recent commands, captured stderr) and
responds in the terminal. It may propose commands to run, but you must confirm
each one (see Confirmation below).

### Trigger matrix

After every executed command, Sasha decides whether to consult the LLM based on
how the command behaved:

| Situation | Detection | Behaviour |
|---|---|---|
| Command not found or plain natural language (typos, math, English) | Exit code 127 | **Consults immediately.** The LLM sees the line and answers or suggests a corrected command. |
| Not valid bash syntax (unclosed quote, bad pipe) | Syntax pre-check fails — line is never executed | **Consults immediately.** The LLM sees the bad line and explains the syntax error. |
| Command ran but failed | Any non-zero exit code | **Consults immediately.** The LLM sees the exit code and the captured stderr and explains what went wrong. |
| User terminated the command (Ctrl-C) | Signal (SIGINT, SIGPIPE) | **Nothing.** You stopped it; there is nothing to explain. |
| Command succeeded | Exit code 0 | **Nothing.** |

Every failure consults. An earlier version consulted only on exit 127 and
syntax errors and merely *offered* a consult for other failures — waiting for a
bare Enter — with a mute store to suppress repeat offers. That apparatus existed
to spend a paid API budget conservatively. Against a local model the marginal
token is free while a failed command is exactly when an explanation is worth
having, so there is nothing left to ration and no offer to accept or ignore.

If the noise is unwanted for a particular command, the honest answer is to fix
the command, or raise `history_tokens` and ignore it — not to teach Sasha to
stay quiet about failures.

### Reasoning display

Models that "think" out loud — most local ones do — produce reasoning text
several times longer than their actual answer. Sasha shows one line of it by
default rather than streaming the whole thing into your scrollback:

```
[sasha] thinking…
[thinking] The user wants to know which files changed since the last commit.
Here's what changed since HEAD~1:

  src/sasha/repl.py   |  38 ++++++++++++---
```

The line is the first line of the model's most recent **completed** reasoning
paragraph, so it holds still and then advances as the model finishes each
paragraph — it doesn't flicker with every token. It's dim, capped to one
terminal width, and when the model starts answering it's settled onto its own
line and the answer follows normally.

Nothing is thrown away: the full reasoning for the session is kept in memory
and `sasha reasoning` prints it back. Reasoning beyond the last 200,000
characters is dropped, and Sasha says so when it prints.

- `--reasoning compact` — the default described above.
- `--reasoning full` — the older behaviour: stream every reasoning chunk as it
  arrives, dim, interleaved with the answer.
- `reasoning_mode` in `config.toml` sets the session default; the flag wins
  over it.

If the model doesn't expose reasoning at all (most hosted APIs don't), none of
this appears and the answer streams as usual.

### Git headline

The first time in a session that you `cd` into a repository, Sasha prints one
free line naming its last commit:

```
[sasha] sasha: last commit 2 hours ago — "Track Task 61.1"
```

No LLM call, no background thread, no configuration. It costs one `git log -1`
and it is the only directory orientation Sasha does.

This replaced a much larger feature. The old "directory recap" classified every
directory with five heuristic constants (umbrella directories, activity fan-out,
staleness, vendor/cache exclusion, marker files), printed the same git line when
it felt that was enough, and otherwise spawned a background LLM thread to write
a summary. Its own design document recorded a **44% false-positive rate** for the
heuristic it rejected, the background thread caused two separate flaky-test
investigations, and it accounted for roughly 1,300 lines of code and tests to
produce one line on `cd`. `sasha recap` and `sasha directory-recap on|off` are
gone with it.

### Tools available to the LLM

The LLM can call eight tools:

| Tool | Confirmation | Purpose |
|---|---|---|
| `run_command` | Confirms, *unless recognized as safe* | Propose one bash command to run in your shell. |
| `write_file` | Confirms | Write content to a file (creates parent directories; overwrites if it exists). Not sandboxed — same unrestricted-but-confirmed model as `run_command`. |
| `read_file` | Auto-runs | Read a file's contents. Sandboxed to the current directory and below. |
| `list_dir` | Auto-runs | List a directory's entries. Sandboxed to the current directory and below. |
| `search_files` | Auto-runs | Regex search over file contents, recursively. Sandboxed to the current directory and below. |
| `remember` | Auto-runs | Save a durable note for future sessions — the current project's memory if you're in a git-tracked directory, general memory otherwise. See Memory below. |
| `question` | Auto-runs (its own UI, not the confirmation gate) | Ask you one or more structured, menu-driven questions instead of guessing between materially different approaches. See Questions below. |
| `use_skill` | Auto-runs | Load a named skill's full instructions into context. See Skills below. |

`read_file`, `list_dir`, and `search_files` run immediately with no prompt —
a path that resolves outside the current directory is rejected with an error
message the LLM can see and correct, never a silent no-op. All three cap
their output (40000 characters for `read_file`, 200 entries for `list_dir`,
100 matches for `search_files`) and note when they've truncated.

`run_command` normally goes through the confirmation gate below, but a
narrow allowlist of read-only verbs (`ls`, `pwd`, `wc`, `cat`, `head`,
`tail`, `file`, `stat`, `du`, `find`, `grep`, `tree`, `sort`, `uniq`,
`cut`, `diff`, `echo`) auto-runs without asking — only when the *entire*
command is built from those verbs (chained with `&&`/`;`/`|`/`||`), has no
backticks/`$()`/`>` redirection/`$VAR` expansion, and touches no path
matching your configured `sensitive_paths` patterns. Anything else about
the command routes it back through the normal confirmation gate.

### Memory

Sasha remembers things automatically, with zero setup — two tiers, both
always visible to the LLM at once:

- **General memory** (`~/.config/sasha/memory/`) — not tied to any
  project. Personal preferences, general facts — visible in every
  session, everywhere, always.
- **Project memory** (`<repo-root>/.sasha/memory/`) — scoped to whichever
  git repository you're currently in. Swaps automatically as you `cd`
  between projects (including in a second terminal); adds on top of
  general memory, never replaces it. Nothing is ever stored for a
  directory that isn't a git repository — those sessions fall back to
  general memory instead.

Three things trigger encoding, only the last one costing an LLM call:

- **Every successful `git commit`** (project memory only — there's no
  general-memory equivalent of a commit) — the commit message becomes a
  remembered note, no LLM involved, no extra cost.
- **A program crashing** — `SIGSEGV`, `SIGABRT`, `SIGBUS`, `SIGILL`, or
  `SIGFPE` (real fault signals; Ctrl+Z and an OOM `SIGKILL` never
  trigger this) — captured automatically and verbatim, into whichever
  tier is currently active, no LLM involved.
- **The LLM calling `remember`** — its own judgment about what's worth
  keeping (a conclusion, a decision, a preference you stated), or in
  response to you explicitly asking, e.g. `?? please remember that this
  output looks like a bug`. Targets the current project's memory if
  you're in one, general memory otherwise.

Project memory's `.sasha/` is **always automatically git-ignored** the
moment it's first created — a `.sasha/.gitignore` containing `*` is
written before anything else, so it can never end up in a commit by
accident. General memory needs no such protection — `~/.config/sasha/`
was never part of any project's git history to begin with. Both tiers
share the same on-disk shape: `semantic.jsonl` for the notes themselves,
`episodic/` for verbatim records that `remember`/the crash trigger attach
when a command is in scope.

Moving between projects swaps what Sasha remembers to match wherever you
currently are:

```
[sasha] project: sasha (14 memories, last: "Track Task 17.3")
```

The first time anything is ever remembered for a given *project* with no
prior history to bootstrap from (see below), Sasha says so once (general
memory currently self-installs silently — no equivalent notice yet):

```
[sasha] created .sasha/memory/ — remembering context for this project from now on (gitignored automatically)
```

#### Starting memory for an existing project

The first time Sasha enters a git repository it has no memory for, it gives
itself a running start by replaying recent commit messages into project
memory, exactly as if they had been encoded as they happened:

```
[sasha] no memory yet for sasha — replayed 20 recent commit(s) into project memory
```

That is free and mechanical: no LLM call, nothing invented, and because it is
derived from git it simply happens again if you ever delete the memory. A
repository with no commits is left completely untouched.

There used to be a second stage here: the model was asked to summarise the
repository's history and root-level docs into a handful of higher-level facts,
which were then injected into every later prompt. It was removed. A 26B's
summary of git history is not authoritative, a hallucination in it is durable
and indistinguishable from something you told Sasha yourself, and there was no
way to refresh it — the only fix was `sasha forget`, which destroys all memory.
The mechanical replay cannot go wrong and costs nothing; the summariser could
go wrong quietly and cost you trust in everything else Sasha remembered.

`sasha memory` (show what's remembered — general memory always, project
memory too when you're in a repo) and `sasha forget` (permanently delete
the *current project's* memory only; general memory isn't touched by it,
by design) are built-in commands — see Built-in commands below.

### Skills

Sasha can load named "skills" — packaged markdown playbooks describing a
structured procedure — into context on request, the same concept
Anthropic's own Claude Code uses via `SKILL.md` files. No installer is
built in; populate `~/.config/sasha/skills/` yourself, laid out the same
way Claude Code's own plugin cache is:

```
~/.config/sasha/skills/
  <marketplace>/
    <plugin>/
      <version>/          ← highest semver wins if more than one is present
        skills/
          <skill-name>/
            SKILL.md       ← frontmatter (name:, description:) + body
```

A repo whose own layout already matches this shape can be `git clone`d
straight into place; most published skill repos don't nest this deeply
though (a `skills/<name>/SKILL.md` directory at the repo root is a
common shape instead), so the usual path is cloning it somewhere
scratch and copying (or symlinking) its actual `skills/` directory in
under a `<marketplace>/<plugin>/<version>/` you choose, e.g.:

```
git clone https://github.com/<owner>/<repo> /tmp/<repo>
mkdir -p ~/.config/sasha/skills/<marketplace>/<plugin>/<version>
cp -r /tmp/<repo>/skills ~/.config/sasha/skills/<marketplace>/<plugin>/<version>/skills
```

Every installed skill with both a `name:` and `description:` in its
frontmatter (single-line or YAML block-scalar `>`/`|` style, both parse
correctly) is listed for the LLM at startup; it decides on its own when a
listed skill's description matches the task at hand, and loads the full
file via the `use_skill` tool. Skills referencing machinery Sasha doesn't
have (subagent dispatch, published artifacts, and similar) simply won't
have those instructions fulfilled — the plain prose parts still work.

### Confirmation gate (safety guarantees)

When the LLM proposes `write_file`, or a `run_command` that isn't
recognized as safe (see Tools above), Sasha never executes it
automatically. You see a confirmation block:

```
[sasha] wants to run:  <command or action summary>
        └ <description>
        [y]es / [n]o / [e]dit — or type a reply
```

- **`y`** or **`yes`** — run the action as proposed.
- **`n`**, **`no`**, or **empty** — decline.
- **`e`** or **`edit`** — for `run_command` only, edit the command before
  running it (Sasha shows a readline-prefilled prompt with the proposed
  command); for `write_file`, treated the same as a free-text reply.
- **Anything else** — treated as a free-text reply to the LLM. Free-text
  replies never execute anything; they are messages to the model, and the
  model may respond with more text or another proposal.

If the model doesn't provide a description, `(no description provided)` is
shown. Anything that isn't a narrow, read-only, auto-approved `run_command`
always goes through this gate — nothing else the LLM proposes ever runs
without your explicit confirmation.

### Questions

The LLM can also pause and ask you directly, via the `question` tool,
instead of guessing between materially different approaches:

```
Question 1/1: Cache strategy

  What approach should I take for the caching layer?

  [1] In-memory — Fastest, lost on restart.
  [2] File-based — Persists across runs.

  [C] Type your own answer

  Your choice [1-2, C]:
```

Pick a number (or comma-separated numbers if the question allows multiple
answers), press `C` to type a free-text answer instead, or press Ctrl-C /
Enter with nothing typed to decline answering. This is a separate UI from
the `y`/`n`/`e` confirmation gate above — no action is proposed here, just
a question — and can chain into a multi-question batch (`Question 2/2`,
etc.) before control returns to the model.

### Built-in commands

- `sasha profile [<name>]` — show current profile (`[sasha] profile: <name> (<model> @ <base_url>)`), or switch to `<name>`.
- `sasha memory` — show general memory (always) and the current
  project's memory (when you're in a git repository), each with an entry
  count and the most recent ones — see Memory above.
- `sasha forget` — permanently delete the current project's remembered
  memory only (general memory is untouched). Asks for confirmation
  first; nothing is deleted on any answer other than `yes`.
- `sasha reasoning` — print back everything the model has reasoned this
  session, including the parts the default one-line display didn't show —
  see Reasoning display above.
- `sasha compact-log` — manually strip stored results out of
  `tool_log.jsonl` entries; the same compaction also runs automatically
  in the background once the file passes `tool_log_max_size_mb` (100 MB
  by default) — see State below.
- `sasha help` — show available built-ins.

### Tab completion

Press TAB like you would in bash. The first word on a line completes against
`sasha`'s own built-ins (`profile`, `help`), plus every
executable on `$PATH`. Arguments to `sasha profile` complete
against your configured profile names. For everything else, Sasha shells out
to your system's real bash-completion (so `git checkout <TAB>` and similar
command-specific completions work exactly as they do in bash), falling back
to plain filename completion if bash-completion isn't installed.

### Job control

Sasha doesn't implement job control (`fg`/`bg`/`jobs`) — there's no
background-and-resume model. What it does guarantee: pressing Ctrl+Z never
leaves a stopped, orphaned process behind. Instead of suspending, Sasha
cleanly terminates the foreground command and prints
`[sasha] Ctrl+Z not supported — command terminated`. Full-screen TUI programs
(`vim`, `mc`, `htop`, and similar) get proper terminal control handed to them
while running, so they render and receive input correctly — not something
you'd get from a naive subprocess wrapper.

### Resilience

If the LLM endpoint is down or unreachable, Sasha prints:

```
[sasha] <profile> unreachable: <reason> — 'sasha profile <name>' to switch
```

The shell keeps working as a normal shell — the LLM layer failing never breaks
command execution.

#### Internal errors

An unexpected error *inside Sasha* no longer ends the session. Sasha repairs
the terminal, writes a crash report, and returns to the prompt with your
conversation, working directory and scrollback intact:

```
[sasha] internal error: RuntimeError: something went wrong  (repl.py:412)
[sasha] full traceback: ~/.local/state/sasha/crashes/20261012T063352Z-4821.txt
[sasha] this session is still alive — press Enter to continue
```

Crash reports land in `~/.local/state/sasha/crashes/` as a `.txt` (readable)
and a `.json` twin (machine-readable), each carrying the traceback, cwd,
profile, argv, your last few commands, and the git revision of the running
checkout. So "it broke" is reproducible after the fact, not just once.

If the same failure keeps recurring — a systematic fault that no amount of
retrying will get past — Sasha stops catching it after 3 occurrences and exits
cleanly to bash rather than looping on the same traceback:

```
[sasha] that same failure has now happened 3 times — exiting instead of looping. Your shell is unaffected.
```

An unwritable state directory (read-only `$HOME`, full disk, an unusual
container) no longer prevents startup either: Sasha falls back to a temporary
directory for the session and says so, instead of dying before the first
prompt.

### Repair mode

`sasha repair` starts a session whose job is fixing Sasha itself:

```
$ sasha repair
[sasha] repair mode — checkout /home/you/Code/sasha (profile: deepseek, model: deepseek-chat)
[sasha] git: ada582e — 2 uncommitted change(s)
[sasha] crash report: ~/.local/state/sasha/crashes/20261012T063712Z-6.txt
[sasha] edits here change the `sasha` you are running. The test suite is the check: 'bash scripts/run-tests.sh'. Consider a branch.
```

What's different from a normal session:

- **It runs in the source checkout**, so `read_file`, `grep`, `write_file` and
  `run_command` all operate on Sasha's own code.
- **The newest crash report is loaded into the system prompt**, so the session
  starts already knowing what broke. `--bundle <path>` picks a specific one;
  with no reports at all it's an ordinary maintenance session.
- **The model is your `repair_profile`** rather than the active one — see
  below.
- **Nothing else changes.** The same tools, and the same confirmation gate:
  every `write_file` and every non-safe `run_command` still waits for your `y`.

Repair mode does *not* edit anything by itself, and does not commit. You watch,
you approve, you decide.

#### Pointing repair at a frontier model

Repair is the one job where a weak model is the wrong tool — the traceback is
the easy part, and editing your own source without breaking it is the hard
part. So Sasha keeps the model choice separate from the profile you drive
daily:

```toml
default_profile = "local"      # day-to-day: your local 26B, free and fast
repair_profile  = "deepseek"   # 'sasha repair': a frontier model
```

`repair_profile` is a *setting*, not a profile named "repair" — a profile is an
endpoint, so `deepseek` stays usable interactively too. Empty (the default)
means repair reuses whatever profile is active. `--repair-profile <name>`
overrides the config for one session, and a profile that needs an API key it
doesn't have will prompt for it, exactly as `sasha profile <name>` does. If you
cancel that prompt, repair continues on the active profile and warns you why
that's a bad idea.

Once inside, `sasha profile <name>` still works normally, so you can switch
models mid-session if a particular fix needs a different one.

### Loop detection

Sasha stops the agent loop for two distinct reasons, worded differently so
you can tell them apart at a glance:

- **Repetition** — the most recently executed tool call is identical to the
  previous turn's (same tool, same key arguments — e.g. the same command,
  or the same `write_file` path and content):
  ```
  [sasha] stopping: repeated identical action — <action>
  ```
- **Turn-limit exhaustion** — the loop reaches the maximum tool-turn limit
  for one consult (10 by default; raise it with `max_tool_turns` under
  `[limits]` in `config.toml`):
  ```
  [sasha] stopping: hit the 10-action limit for this turn — last action: <action> — raise max_tool_turns under [limits] in config.toml if this looks like real progress
  ```

Either way, the agent does one final text-only call so the LLM can answer
or explain without proposing further commands.

## Error vocabulary (reference)

All Sasha-originated lines are prefixed `[sasha] ` and written to stdout.
Command output is never prefixed or altered.

| Event | Line |
|---|---|
| LLM unreachable | `[sasha] <profile> unreachable: <reason> — 'sasha profile <name>' to switch` |
| Loop stopped (repetition) | `[sasha] stopping: repeated identical action — <action>` |
| Loop stopped (turn limit) | `[sasha] stopping: hit the N-action limit for this turn — last action: <action> — raise max_tool_turns under [limits] in config.toml if this looks like real progress` |
| Profile switch | `[sasha] profile: <name> (<model> @ <base_url>)` |
| Ctrl+Z | `[sasha] Ctrl+Z not supported — command terminated` |
| Project switch | `[sasha] project: <name> (<N> memories, last: "<gist>")` |
| Memory created | `[sasha] created .sasha/memory/ — remembering context for this project from now on (gitignored automatically)` |
| Git headline | `[sasha] <repo>: last commit <age> — "<subject>"` |
| Log compacted | `[sasha] tool_log.jsonl compacted: <before> MB → <after> MB` |
| Bad config | `sasha: config error: <message>` (startup, exit 2) |

## State

Sasha writes its state to `~/.local/state/sasha/`:

- `history` — readline command history (1000-entry cap)
- `crashes/` — crash reports from internal errors, as `.txt` + `.json`
  (see Resilience above)
- `tool_log.jsonl` — an append-only record of every tool call (name,
  arguments, verdict, and result unless it touched a sensitive path),
  auto-compacted (stored results stripped) once it passes
  `tool_log_max_size_mb` (100 MB by default) — see `sasha compact-log`
  above.
- `llm_debug.jsonl` — raw LLM request/response logging, off by default
  (`[llm_debug] enabled = true` in `config.toml` to turn it on) —
  investigation tooling, not needed for normal use.

Config, personality, general memory, and skills live separately, under
`~/.config/sasha/`:

- `config.toml` — profiles, limits, prompt template (see Configuration)
- `PERSONALITY.md` — tone/character customization (see Personality)
- `memory/semantic.jsonl` — general (cross-project) remembered notes
- `memory/episodic/*.log` — general memory's verbatim command records
- `skills/` — installed skill libraries (see Skills above)

Project memory lives with the project itself, not under your home
directory's state/config folders — see Memory above:

- `<repo-root>/.sasha/memory/semantic.jsonl` — remembered notes
- `<repo-root>/.sasha/memory/episodic/*.log` — verbatim command records
- `<repo-root>/.sasha/.gitignore` — self-installed, keeps all of the above
  out of git automatically

## License

MIT — see [LICENSE](LICENSE).
