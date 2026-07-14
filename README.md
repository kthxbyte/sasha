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

```
pip install -e .
```

This installs the `sasha` console script. The only dev dependency is `pytest`
(`pip install -e .[dev]`). Run the full suite with `scripts/run-tests.sh`.

## Configuration

Sasha reads `~/.config/sasha/config.toml`. If the file does not exist, built-in
defaults are used:

```toml
default_profile = "deepseek"

[profiles.deepseek]
base_url = "https://api.deepseek.com/v1"
model = "deepseek-chat"
api_key_env = "DEEPSEEK_API_KEY"
# api_key = "sk-..."  # optional fallback literal

[profiles.local]
base_url = "http://192.168.8.253:11441/v1"
model = "local"
api_key_env = ""

[limits]
history_tokens = 12000
stderr_capture_bytes = 8192
max_tool_turns = 10
mute_threshold = 3
```

Two profiles are configured by default:

- **deepseek** — the default, connects to `api.deepseek.com`. Requires
  `DEEPSEEK_API_KEY` in the environment.
- **local** — for a local llama.cpp server at `192.168.8.253:11441`. No API key
  required.

You can switch profiles at runtime with `sasha profile <name>`.

## Usage

Start Sasha:

```
$ sasha
~/project ✦ 
```

The prompt shows your current working directory (with `$HOME` abbreviated to
`~`) followed by ` ✦ `.

### Normal commands

Type any shell command and press Enter — it runs exactly as it would in bash:

```
~/project ✦ ls
~/project ✦ echo hello
~/project ✦ cd /tmp
/tmp ✦ 
```

### Explicit ask

Prefix a line with `?? ` to consult the LLM directly:

```
~/project ✦ ?? how do I find files larger than 100MB
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

### Confirmation gate (safety guarantees)

When the LLM proposes a command via `run_command`, Sasha never executes it
automatically. You see a confirmation block:

```
[sasha] wants to run:  <command>
        └ <description>
        [y]es / [n]o / [e]dit — or type a reply
```

- **`y`** or **`yes`** — run the command as proposed.
- **`n`**, **`no`**, or **empty** — decline.
- **`e`** or **`edit`** — edit the command before running it; Sasha shows a
  readline-prefilled prompt with the proposed command.
- **Anything else** — treated as a free-text reply to the LLM. Free-text
  replies never execute anything; they are messages to the model, and the
  model may respond with more text or another proposal.

If the model doesn't provide a description, `(no description provided)` is
shown. The gate always applies — no command proposed by the LLM ever runs
without your explicit confirmation.

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
- `sasha help` — show available built-ins.

Mute state is saved to `~/.local/state/sasha/mutes.json` and persists across
sessions. Accepting an offer (pressing Enter) resets the ignore streak for
that pair to zero.

### Resilience

If the LLM endpoint is down or unreachable, Sasha prints:

```
[sasha] <profile> unreachable: <reason> — 'sasha profile <name>' to switch
```

The shell keeps working as a normal shell — the LLM layer failing never breaks
command execution.

### Loop detection

If the agent proposes the same command twice in a row or reaches the maximum
tool-turn limit (10 by default), Sasha stops the loop:

```
[sasha] stopping: repeated identical command / tool-turn limit reached
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
| Loop stopped | `[sasha] stopping: repeated identical command / tool-turn limit reached` |
| Profile switch | `[sasha] profile: <name> (<model> @ <base_url>)` |
| Bad config | `sasha: config error: <message>` (startup, exit 2) |

## State

Sasha writes its state to `~/.local/state/sasha/`:

- `mutes.json` — mute counters (atomic write, survives corruption)
- `history` — readline command history (1000-entry cap)
