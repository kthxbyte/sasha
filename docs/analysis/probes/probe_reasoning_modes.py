"""Probe 5: compact vs full reasoning display, against the live 26B.

Drives the real REPL under a pty and renders the captured byte stream the way a
terminal would (interpreting CR, EL, and SGR), so the one-line in-place preview
is visible as the user would actually see it.

Scratch tooling: gitignored, not part of the test suite.
"""

from __future__ import annotations

import os
import pty
import re
import select
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path("/home/salvador/Code/sasha")
BASE_URL = "http://192.168.8.253:11434/v1"
MODEL = "dealignai/Bonsai-2-27B-Ternary-CRACK-GGUF:Q2_0"

_SGR = re.compile(r"\x1b\[[0-9;]*m")


def render_screen(raw: str) -> str:
    """Minimal terminal interpreter: applies CR, EL(2K), LF and SGR to show
    the final visible screen rather than the raw escape soup."""
    lines: list[str] = []
    col = 0
    cur = ""
    i = 0
    while i < len(raw):
        ch = raw[i]
        if ch == "\x1b":
            if raw.startswith("\x1b[2K", i):
                cur = ""
                col = 0
                i += 4
                continue
            m = _SGR.match(raw, i)
            if m:
                i = m.end()
                continue
            i += 1
            continue
        if ch == "\r":
            col = 0
            i += 1
            continue
        if ch == "\n":
            lines.append(cur)
            cur = ""
            col = 0
            i += 1
            continue
        cur = cur[:col] + ch + cur[col + 1:]
        col += 1
        i += 1
    if cur:
        lines.append(cur)
    return "\n".join(lines)


def write_config(workdir: Path, reasoning_line: str) -> Path:
    config = workdir / "config.toml"
    config.write_text(f"""\
default_profile = "local"
{reasoning_line}
[profiles.local]
base_url = "{BASE_URL}"
model = "{MODEL}"
api_key = ""
api_key_required = false

[limits]
history_tokens = 12000
stderr_capture_bytes = 8192
max_tool_turns = 4
mute_threshold = 3

[activity_indicator]
enabled = false

[llm_debug]
enabled = false

[directory_recap]
enabled = false
""")
    return config


def run_session(label: str, reasoning_line: str, script: list[str]) -> str:
    workdir = Path(tempfile.mkdtemp(prefix="sasha-reason-"))
    config = write_config(workdir, reasoning_line)

    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO / "src")
    env["TERM"] = "xterm"
    env["NO_COLOR"] = "1"          # keep the rendered screen readable here
    env["XDG_STATE_HOME"] = str(workdir / "state")
    env["XDG_CONFIG_HOME"] = str(workdir / "xdgconfig")

    master, slave = pty.openpty()
    proc = subprocess.Popen(
        [sys.executable, "-m", "sasha", "--config", str(config)],
        stdin=slave, stdout=slave, stderr=slave,
        cwd=str(workdir), env=env, start_new_session=True,
    )
    os.close(slave)

    buf = ""

    def drain(seconds):
        nonlocal buf
        end = time.time() + seconds
        while time.time() < end:
            r, _, _ = select.select([master], [], [], 0.25)
            if not r:
                continue
            try:
                chunk = os.read(master, 65536)
            except OSError:
                break
            if not chunk:
                break
            buf += chunk.decode("utf-8", "replace")

    try:
        drain(12)
        for line in script:
            os.write(master, (line + "\n").encode())
            drain(45)
        os.write(master, b"exit\n")
        drain(4)
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except Exception:
                pass
        os.close(master)

    print("#" * 74)
    print(f"# {label}")
    print("#" * 74)
    print(render_screen(buf))
    print()
    return buf


QUESTION = ("?? In two short sentences, explain what the shell command "
            "'du -sh *' does and when it is useful.")

run_session("DEFAULT (reasoning_mode = compact)",
            "", [QUESTION, "sasha reasoning"])

run_session("--reasoning full (via config equivalent)",
            'reasoning_mode = "full"', [QUESTION])
