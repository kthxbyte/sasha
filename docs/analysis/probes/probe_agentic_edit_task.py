#!/usr/bin/env python3
"""A harder agentic probe: explore, EDIT an existing file, run tests, iterate.

The first probe (probe_agentic_task.py) only needed new files, so it exercised
write_file and run_command. This one needs the model to find its way around an
existing small project, change a file it did not write, and verify the result --
which is where a missing search/list capability would actually show up.

    PYTHONPATH=src python3 docs/analysis/probes/probe_agentic_edit_task.py
"""
from __future__ import annotations

import dataclasses
import os
import pathlib
import subprocess
import sys
import tempfile
import time

os.environ.setdefault("XDG_CONFIG_HOME", tempfile.mkdtemp())
os.environ.setdefault("XDG_STATE_HOME", tempfile.mkdtemp())
sys.path.insert(0, "src")

from sasha.agent import run_consult                      # noqa: E402
from sasha.config import Profile                        # noqa: E402
from sasha.executor import Executor                     # noqa: E402
from sasha.llm import ChatClient                        # noqa: E402
from sasha.session import Session                       # noqa: E402
import sasha.tools as T                                 # noqa: E402

CORE = '''"""Core arithmetic."""


def compute(values):
    """Sum the values."""
    return sum(values)
'''

CLI = '''"""Command line entry point."""
import argparse

from app.core import compute


def main(argv=None):
    parser = argparse.ArgumentParser(prog="app")
    parser.add_argument("numbers", nargs="+", type=float)
    args = parser.parse_args(argv)
    print(compute(args.numbers))


if __name__ == "__main__":
    main()
'''

TEST = '''from app.core import compute


def test_compute_sums():
    assert compute([1, 2, 3]) == 6
'''

TASK = (
    "In this project, add a `--scale` option to the CLI in app/cli.py. It should "
    "be a float with a default of 1.0, and it multiplies the value returned by "
    "core.compute. Add a test for the new behaviour under tests/, then run the "
    "full test suite and make sure everything passes. Tell me the final result."
)

CALLS: list[tuple[str, str, str]] = []


def _spy() -> None:
    for name in list(T.REGISTRY):
        tool = T.REGISTRY[name]
        original = tool.handler

        def make(n, o):
            def wrapped(args, ex):
                result = o(args, ex)
                CALLS.append((n, str(args.get("path", args.get("command", "")))[:46],
                              result.text[:56].replace("\n", " ")))
                return result
            return wrapped

        T.REGISTRY[name] = dataclasses.replace(tool, handler=make(name, original))


class AutoConfirm:
    def __init__(self):
        self.seen = []

    def ask(self, summary, description):
        self.seen.append(summary.splitlines()[0][:58])
        return ("yes", "")


def main() -> int:
    _spy()
    workdir = pathlib.Path(tempfile.mkdtemp())
    (workdir / "app").mkdir()
    (workdir / "tests").mkdir()
    (workdir / "app" / "__init__.py").write_text("")
    (workdir / "app" / "core.py").write_text(CORE)
    (workdir / "app" / "cli.py").write_text(CLI)
    (workdir / "tests" / "test_core.py").write_text(TEST)
    (workdir / "README.md").write_text("# Demo app\n\nSums numbers.\n")

    profile = Profile(name="local", base_url="http://192.168.8.253:11434/v1",
                      model="local", api_key="", api_key_required=False)
    session = Session()
    session.add_user(TASK)
    confirm = AutoConfirm()
    print(f"  workdir: {workdir}")
    print(f"  task: {TASK[:72]}...\n")
    started = time.time()
    try:
        run_consult(session, ChatClient(profile, timeout_s=200.0,
                                        max_think_seconds=180),
                    Executor(str(workdir), {}), confirm,
                    out=lambda t: None, max_tool_turns=50)
    except Exception as exc:                       # noqa: BLE001
        print(f"  !! the loop raised {type(exc).__name__}: {str(exc)[:70]}")
    elapsed = time.time() - started

    print(f"  elapsed: {elapsed:.0f}s   tool calls: {len(CALLS)}   "
          f"confirmations: {len(confirm.seen)}")
    for name, target, result in CALLS:
        print(f"    {name:12} {target:48} -> {result}")

    suite = subprocess.run([sys.executable, "-m", "pytest", "-q"],
                           cwd=workdir, capture_output=True, text=True, timeout=180)
    print(f"\n  pytest (run by the probe): rc={suite.returncode}  "
          f"{(suite.stdout.strip().splitlines() or [''])[-1]}")

    feature = subprocess.run([sys.executable, "-m", "app.cli", "1", "2", "3",
                              "--scale", "2"], cwd=workdir, capture_output=True,
                             text=True, timeout=60)
    print(f"  the new feature, exercised by the probe: "
          f"rc={feature.returncode} stdout={feature.stdout.strip()!r} "
          f"stderr={feature.stderr.strip()[-60:]!r}")
    print("  (expected stdout: 12.0)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
