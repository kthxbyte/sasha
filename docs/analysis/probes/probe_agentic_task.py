#!/usr/bin/env python3
"""Can Sasha's agent loop finish a realistic multi-step task on the local 26B?

This is the capability half of the tool-set question: rather than comparing tool
lists, give the loop a genuine (if small) engineering task and see whether it
completes, how many turns it takes, and how it fails when it does.

    PYTHONPATH=src python3 docs/analysis/probes/probe_agentic_task.py

Everything is auto-approved (there is no human in the loop here), so the count of
confirmations is reported separately -- in a real session each of those would
have been a y/n prompt, which is itself a cost worth knowing.
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

TASK = (
    "Create a Python module `stats.py` in this directory with three functions: "
    "mean(numbers), median(numbers) and mode(numbers), each returning a number "
    "or raising ValueError on an empty list. Then write `test_stats.py` with "
    "pytest tests covering the normal cases and the empty-list case, run it, and "
    "fix anything that fails. Finish by telling me the test result."
)

CONFIRMATIONS: list[str] = []
CALLS: list[tuple[str, str, str]] = []


def _spy() -> None:
    for name in list(T.REGISTRY):
        tool = T.REGISTRY[name]
        original = tool.handler

        def make(n, o):
            def wrapped(args, ex):
                result = o(args, ex)
                CALLS.append((n, str(args.get("path", args.get("command", "")))[:44],
                              result.text[:58].replace("\n", " ")))
                return result
            return wrapped

        T.REGISTRY[name] = dataclasses.replace(tool, handler=make(name, original))


class AutoConfirm:
    """Approves everything, but records what a human would have been asked."""

    def ask(self, summary, description):
        CONFIRMATIONS.append(summary.splitlines()[0][:60])
        return ("yes", "")


def main() -> int:
    _spy()
    workdir = pathlib.Path(tempfile.mkdtemp())
    profile = Profile(name="local", base_url="http://192.168.8.253:11434/v1",
                      model="local", api_key="", api_key_required=False)
    session = Session()
    session.add_user(TASK)
    client = ChatClient(profile, timeout_s=200.0, max_think_seconds=180)

    print(f"  task: {TASK[:70]}...")
    print(f"  workdir: {workdir}\n")
    started = time.time()
    try:
        run_consult(session, client, Executor(str(workdir), {}), AutoConfirm(),
                    out=lambda t: None, max_tool_turns=50)
    except Exception as exc:                       # noqa: BLE001
        print(f"  !! the loop raised {type(exc).__name__}: {str(exc)[:70]}")
    elapsed = time.time() - started

    print(f"  elapsed: {elapsed:.0f}s   tool calls: {len(CALLS)}   "
          f"confirmations a human would have seen: {len(CONFIRMATIONS)}")
    for name, target, result in CALLS:
        print(f"    {name:12} {target:46} -> {result}")
    print()
    for name in ("stats.py", "test_stats.py"):
        path = workdir / name
        print(f"  {name:14} exists={path.exists()}"
              + (f"  {len(path.read_text().splitlines())} lines" if path.exists() else ""))

    if (workdir / "test_stats.py").exists():
        check = subprocess.run([sys.executable, "-m", "pytest", "-q",
                                "test_stats.py"], cwd=workdir,
                               capture_output=True, text=True, timeout=120)
        tail = (check.stdout.strip().splitlines() or [""])[-1]
        print(f"  pytest (run by the probe, not the model): rc={check.returncode}  {tail}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
