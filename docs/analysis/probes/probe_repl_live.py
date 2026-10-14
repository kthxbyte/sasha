"""Probe 4: drive the real sasha REPL under a pty against the local 26B.

Exercises the actual interactive path: banner -> prompt -> trigger matrix
(exit 127 auto-consult) -> streaming consult -> back to the prompt. Uses a
throwaway config and a throwaway cwd so nothing is written into the repo.

Scratch tooling: gitignored, not part of the test suite.
"""

from __future__ import annotations

import os
import pty
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

workdir = Path(tempfile.mkdtemp(prefix="sasha-repl-probe-"))
config = workdir / "config.toml"
config.write_text(f"""\
default_profile = "local"

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

env = dict(os.environ)
env["PYTHONPATH"] = str(REPO / "src")
env["TERM"] = "xterm"
# This sandbox's $HOME is read-only; sasha's state/config dirs honour XDG_*.
# (Its unconditional `state_dir.mkdir` crashing on a read-only $HOME is a real
# independent finding — see the report.)
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


def wait_for(needle, seconds):
    end = time.time() + seconds
    while time.time() < end:
        drain(0.4)
        if needle in buf:
            return True
    return False


try:
    if not wait_for("Tip :", 30):
        print("!! REPL never printed its banner")
    else:
        print("banner OK")

        # 1. explicit ask ('??') -> streaming consult
        before = len(buf)
        os.write(master, b"?? In one short sentence, what does 'du -sh *' do?\n")
        wait_for("du", 5)
        drain(45)
        print("\n===== 1. '??' explicit ask =====")
        print(buf[before:][-1200:])

        # 2. a command that does not exist -> exit 127 -> AUTO_CONSULT
        before = len(buf)
        os.write(master, b"definitelynotarealcommand_xyz\n")
        drain(45)
        print("\n===== 2. exit-127 auto-consult =====")
        print(buf[before:][-1200:])

        # 3. a normal command -> no consult
        before = len(buf)
        os.write(master, b"echo hello-from-local-probe\n")
        drain(6)
        print("\n===== 3. plain command (expect no consult) =====")
        print(buf[before:][-500:])

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
    print(f"\n[exit code: {proc.returncode}]")
