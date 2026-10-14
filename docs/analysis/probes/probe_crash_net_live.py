"""Probe 6: the crash safety net, live, in a real REPL under a pty.

Injects one genuine fault into Repl.run_once on the first line, then types a
second command. Proves, in a real process with a real terminal:

  1. the session does NOT exit on an internal error,
  2. a crash report is written with real content,
  3. the terminal is left usable, and
  4. the very next command works normally.

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

_SGR = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]|\x1b\][0-9]+;[^\x07]*\x07")


def render_screen(raw: str) -> str:
    lines, col, cur, i = [], 0, "", 0
    while i < len(raw):
        ch = raw[i]
        if ch == "\x1b":
            if raw.startswith("\x1b[2K", i):
                cur, col = "", 0
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
            cur, col = "", 0
            i += 1
            continue
        cur = cur[:col] + ch + cur[col + 1:]
        col += 1
        i += 1
    if cur:
        lines.append(cur)
    return "\n".join(lines)


workdir = Path(tempfile.mkdtemp(prefix="sasha-crash-"))
config = workdir / "config.toml"
config.write_text(f"""\
default_profile = "local"

[profiles.local]
base_url = "{BASE_URL}"
model = "{MODEL}"
api_key = ""
api_key_required = false

[activity_indicator]
enabled = false

[directory_recap]
enabled = false
""")

# A runner that injects one real fault into the first run_once() call.
runner = workdir / "runner.py"
runner.write_text(f"""\
import sys
sys.path.insert(0, {str(REPO / "src")!r})
sys.argv = ["sasha", "--config", {str(config)!r}]
from sasha.repl import Repl
_orig = Repl.run_once
_state = {{"n": 0}}
def flaky(self, line):
    _state["n"] += 1
    if _state["n"] == 1:
        raise RuntimeError("injected live fault for the crash-net probe")
    return _orig(self, line)
Repl.run_once = flaky
from sasha.__main__ import main
raise SystemExit(main())
""")

env = dict(os.environ)
env["PYTHONPATH"] = str(REPO / "src")
env["TERM"] = "xterm"
env["NO_COLOR"] = "1"
env["XDG_STATE_HOME"] = str(workdir / "state")
env["XDG_CONFIG_HOME"] = str(workdir / "xdgconfig")

master, slave = pty.openpty()
proc = subprocess.Popen([sys.executable, str(runner)],
                        stdin=slave, stdout=slave, stderr=slave,
                        cwd=str(workdir), env=env, start_new_session=True)
os.close(slave)

buf = ""


def drain(seconds):
    global buf
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
    drain(10)
    os.write(master, b"echo first-line-triggers-the-injected-crash\n")
    drain(6)
    os.write(master, b"echo second-command-works-after-the-crash\n")
    drain(6)
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

print("=" * 74)
print("RENDERED SESSION")
print("=" * 74)
print(render_screen(buf))

print("=" * 74)
print("CRASH REPORT ON DISK")
print("=" * 74)
crashes = sorted((workdir / "state" / "sasha" / "crashes").glob("*.txt"))
print(f"files: {[c.name for c in crashes]}")
if crashes:
    text = crashes[0].read_text()
    print(f"bytes: {len(text)}")
    print(text[:1400])
print()
print(f"[exit code: {proc.returncode}]")
