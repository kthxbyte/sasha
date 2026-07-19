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
`~/.config/sasha/config.toml`, `--version`, `-h`/`--help`.

## Configuration

Sasha reads `~/.config/sasha/config.toml`. On first run, if the file doesn't
exist, Sasha writes one for you (and says so, once) with four profiles:

```toml
default_profile = "llm7"

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
base_url = "http://192.168.8.253:11441/v1"
model = "local"
api_key = ""
api_key_required = false
```

`[limits]` isn't written by the self-installed file — it falls back to
built-in defaults (`history_tokens = 12000`, `stderr_capture_bytes = 8192`,
`max_tool_turns = 10`, `mute_threshold = 3`) unless you add the table
yourself.

Four profiles are configured by default:

- **llm7** — the default. Free, keyless, streaming — works with zero setup.
  Best-effort only; the provider gives no uptime guarantee.
- **openai** — the right endpoint and model, no key yet.
- **deepseek** — the right endpoint and model; also reads `DEEPSEEK_API_KEY`
  from the environment if set.
- **local** — for a local llama.cpp server at `192.168.8.253:11441`. No API
  key required.

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
[sasha]✦ user@host:~/project $ 
```

Sasha prints its active profile, the API endpoint, and a one-time tip about
`??` once at the start of every session. On a genuinely first run, you'll
also see one-time setup notices above the banner — `config.toml` and
`PERSONALITY.md` both self-install and announce themselves the first time
they're created.

The prompt itself shows a `[sasha]✦` tag, then `user@host`, then your current
working directory (with `$HOME` abbreviated to `~`), bash-`PS1`-style. The
star blinks whenever Sasha is doing background work — currently, that's
only the initial-memory bootstrap described under Memory below — and sits
static the rest of the time.

### Normal commands

Type any shell command and press Enter — it runs exactly as it would in bash:

```
[sasha]✦ user@host:~/project $ ls
[sasha]✦ user@host:~/project $ echo hello
[sasha]✦ user@host:~/project $ cd /tmp
[sasha]✦ user@host:/tmp $ 
```

### Explicit ask

Prefix a line with `?? ` to consult the LLM directly:

```
[sasha]✦ user@host:~/project $ ?? how do I find files larger than 100MB
```

The LLM receives your shell context (cwd, recent commands, captured stderr) and
responds in the terminal. It may propose commands to run, but you must confirm
each one (see Confirmation below).

### Trigger matrix

After every executed command, Sasha decides whether to consult the LLM based on
how the command behaved:

| Situation | Detection | Behaviour |
|---|---|---|
| Command not found or plain natural language (typos, math, English) | Exit code 127 | **Auto-consults immediately.** The LLM sees the line and answers or suggests a corrected command. |
| Not valid bash syntax (unclosed quote, bad pipe) | Syntax pre-check fails — line is never executed | **Auto-consults immediately.** The LLM sees the bad line and explains the syntax error. |
| Command ran but failed (non-zero exit) | Any exit code besides 0 and 127, and the command is not muted | **Passive offer.** Sasha prints `[sasha] failed (exit N) — press Enter to ask` and waits. Press Enter to consult the LLM, or type any other command to ignore the offer and move on. |
| Command failed but is muted | Mute store says this `(command, exit)` pair is muted | **Nothing.** No offer, no consultation. |
| User terminated the command (Ctrl-C) | Signal (SIGINT, SIGPIPE) | **Nothing.** |
| Command succeeded | Exit code 0 | **Nothing.** |

Auto-consults on exit 127 and syntax errors happen because the line had no
other purpose — the user wasn't running a real command, so spending tokens is
appropriate. All other failures are zero-token offers: the user decides whether
to consult.

### Tools available to the LLM

The LLM can call six tools, each with a permission fixed by its type — not
configurable, not per-call:

| Tool | Confirmation | Purpose |
|---|---|---|
| `run_command` | Confirms | Propose one bash command to run in your shell. |
| `write_file` | Confirms | Write content to a file (creates parent directories; overwrites if it exists). Not sandboxed — same unrestricted-but-confirmed model as `run_command`. |
| `read_file` | Auto-runs | Read a file's contents. Sandboxed to the current directory and below. |
| `list_dir` | Auto-runs | List a directory's entries. Sandboxed to the current directory and below. |
| `search_files` | Auto-runs | Regex search over file contents, recursively. Sandboxed to the current directory and below. |
| `remember` | Auto-runs | Save a durable note for future sessions — the current project's memory if you're in a git-tracked directory, general memory otherwise. See Memory below. |

`read_file`, `list_dir`, and `search_files` run immediately with no prompt —
a path that resolves outside the current directory is rejected with an error
message the LLM can see and correct, never a silent no-op. All three cap
their output (40000 characters for `read_file`, 200 entries for `list_dir`,
100 matches for `search_files`) and note when they've truncated.

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

#### Bootstrapping memory for an existing project

The first time Sasha enters a git repository that already has real
history to learn from — commits, or root-level docs (`README.md` and
other `.md`/`.txt`/`.htm(l)` files directly at the repo root) — it builds
a running start automatically, in two stages, instead of starting from
nothing:

1. **Instant, mechanical**: recent commit messages are replayed into
   project memory, exactly as if they'd been encoded as they happened.
2. **Background, LLM-driven**: those same commits plus any root docs are
   summarized into a handful of higher-level facts — without blocking
   the shell. You'll see:

```
[sasha] no memory yet for sasha — building initial context from history in the background
```

...and the `[sasha]✦` star blinks while it works. When it finishes:

```
[sasha] finished building context: 6 entries (from README.md)
```

(naming the file when exactly one root doc contributed, a bare count for
several, nothing at all if the summary came from commit history alone).
If the LLM step can't complete — no profile configured, network down —
the mechanical replay still stands; nothing is lost, and a later session
gets one more automatic attempt. A repo with neither commits nor root
docs is left completely untouched, exactly as if this feature didn't
exist.

`sasha memory` (show what's remembered — general memory always, project
memory too when you're in a repo) and `sasha forget` (permanently delete
the *current project's* memory only; general memory isn't touched by it,
by design) are built-in commands — see Built-in commands under Muting
below.

### Confirmation gate (safety guarantees)

When the LLM proposes a mutating action (`run_command` or `write_file`),
Sasha never executes it automatically. You see a confirmation block:

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
shown. The gate always applies — no mutating action proposed by the LLM ever
runs without your explicit confirmation.

### Muting

When you repeatedly ignore offers for the same `(command, exit_code)` pair,
Sasha learns and stops bothering you. After 3 ignored offers (configurable via
`mute_threshold`), Sasha prints:

```
[sasha] muting offers for <cmd> exit <N> — 'sasha unmute <cmd>' to restore
```

From then on, that command failing with that exit code produces no offer.

**Built-in commands:**

- `sasha mute <cmd> [<N>]` — mute a command for a specific exit code `N`, or
  all exit codes if `N` is omitted.
- `sasha unmute <cmd> [<N>]` — unmute a command for a specific exit code `N`,
  or all exit codes if `N` is omitted.
- `sasha profile [<name>]` — show current profile (`[sasha] profile: <name> (<model> @ <base_url>)`), or switch to `<name>`.
- `sasha memory` — show general memory (always) and the current
  project's memory (when you're in a git repository), each with an entry
  count and the most recent ones — see Memory above.
- `sasha forget` — permanently delete the current project's remembered
  memory only (general memory is untouched). Asks for confirmation
  first; nothing is deleted on any answer other than `yes`.
- `sasha help` — show available built-ins.

Mute state is saved to `~/.local/state/sasha/mutes.json` and persists across
sessions. Accepting an offer (pressing Enter) resets the ignore streak for
that pair to zero.

### Tab completion

Press TAB like you would in bash. The first word on a line completes against
`sasha`'s own built-ins (`mute`, `unmute`, `profile`, `help`), plus every
executable on `$PATH`. Arguments to `sasha mute`/`sasha unmute` complete
against currently-muted commands; arguments to `sasha profile` complete
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

### Loop detection

If the agent's most recently executed tool call is identical to the previous
turn's (same tool, same key arguments — e.g. the same command, or the same
`write_file` path and content) or the loop reaches the maximum tool-turn
limit (10 by default), Sasha stops the loop:

```
[sasha] stopping: repeated identical action / tool-turn limit reached
```

The agent does one final text-only call so the LLM can answer or explain
without proposing further commands.

## Error vocabulary (reference)

All Sasha-originated lines are prefixed `[sasha] ` and written to stdout.
Command output is never prefixed or altered.

| Event | Line |
|---|---|
| Offer | `[sasha] failed (exit N) — press Enter to ask` |
| Auto-mute | `[sasha] muting offers for <cmd> exit <N> — 'sasha unmute <cmd>' to restore` |
| LLM unreachable | `[sasha] <profile> unreachable: <reason> — 'sasha profile <name>' to switch` |
| Loop stopped | `[sasha] stopping: repeated identical action / tool-turn limit reached` |
| Profile switch | `[sasha] profile: <name> (<model> @ <base_url>)` |
| Ctrl+Z | `[sasha] Ctrl+Z not supported — command terminated` |
| Project switch | `[sasha] project: <name> (<N> memories, last: "<gist>")` |
| Memory created | `[sasha] created .sasha/memory/ — remembering context for this project from now on (gitignored automatically)` |
| Bad config | `sasha: config error: <message>` (startup, exit 2) |

## State

Sasha writes its state to `~/.local/state/sasha/`:

- `mutes.json` — mute counters (atomic write, survives corruption)
- `history` — readline command history (1000-entry cap)

Config, personality, and general memory live separately, under
`~/.config/sasha/`:

- `config.toml` — profiles, limits, prompt template (see Configuration)
- `PERSONALITY.md` — tone/character customization (see Personality)
- `memory/semantic.jsonl` — general (cross-project) remembered notes
- `memory/episodic/*.log` — general memory's verbatim command records

Project memory lives with the project itself, not under your home
directory's state/config folders — see Memory above:

- `<repo-root>/.sasha/memory/semantic.jsonl` — remembered notes
- `<repo-root>/.sasha/memory/episodic/*.log` — verbatim command records
- `<repo-root>/.sasha/.gitignore` — self-installed, keeps all of the above
  out of git automatically

## License

MIT — see [LICENSE](LICENSE).
