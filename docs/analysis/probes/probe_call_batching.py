#!/usr/bin/env python3
"""Does the local model ever propose SEVERAL tool calls in one response?

Batch approval only helps if a response actually contains more than one step
needing approval. If this model emits one call per response, then the
confirmation friction comes from the number of TURNS, not from multi-call
responses, and batching changes nothing in practice for it.

Records the per-response call count and which gate path was used.

    PYTHONPATH=src python3 docs/analysis/probes/probe_call_batching.py
"""
from __future__ import annotations

import collections
import dataclasses
import os
import pathlib
import tempfile
import time

os.environ.setdefault("XDG_CONFIG_HOME", tempfile.mkdtemp())
os.environ.setdefault("XDG_STATE_HOME", tempfile.mkdtemp())
import sys                                              # noqa: E402
sys.path.insert(0, "src")

from sasha.agent import run_consult                      # noqa: E402
from sasha.config import Profile                        # noqa: E402
from sasha.executor import Executor                     # noqa: E402
from sasha.llm import ChatClient                        # noqa: E402
from sasha.session import Session                       # noqa: E402
import sasha.tools as T                                 # noqa: E402

TASK = (
    "Create `stats.py` with mean/median/mode (ValueError on empty), then "
    "`test_stats.py` with pytest tests for the normal and empty cases, run it, "
    "fix anything that fails, and tell me the result."
)

CALLS: list[str] = []
PER_RESPONSE: list[int] = []
GATE = collections.Counter()


def _spy_tools() -> None:
    for name in list(T.REGISTRY):
        tool = T.REGISTRY[name]
        original = tool.handler

        def make(o):
            def wrapped(args, ex):
                CALLS.append(o.__name__)
                return o(args, ex)
            return wrapped

        T.REGISTRY[name] = dataclasses.replace(tool, handler=make(original))


class SpyClient(ChatClient):
    def chat(self, messages, tools, on_text,
             on_reasoning=lambda c: None, on_warning=None):
        response = super().chat(messages, tools, on_text, on_reasoning, on_warning)
        PER_RESPONSE.append(len(response.tool_calls))
        return response


class Confirmer:
    """Records which gate path the loop chose; approves everything."""

    def ask(self, summary, description):
        GATE["single"] += 1
        return ("yes", "")

    def ask_many(self, steps):
        GATE["batch"] += 1
        GATE["batch_steps"] += len(steps)
        return ("yes", "")


def main() -> int:
    _spy_tools()
    workdir = pathlib.Path(tempfile.mkdtemp())
    profile = Profile(name="local", base_url="http://192.168.8.253:11434/v1",
                      model="local", api_key="", api_key_required=False)
    session = Session()
    session.add_user(TASK)
    started = time.time()
    run_consult(session, SpyClient(profile, timeout_s=200.0, max_think_seconds=180),
                Executor(str(workdir), {}), Confirmer(), out=lambda t: None)
    elapsed = time.time() - started

    print(f"  elapsed: {elapsed:.0f}s   tool calls: {len(CALLS)}")
    print(f"  responses containing tool calls: {len(PER_RESPONSE)}")
    print(f"  calls per response: {PER_RESPONSE}")
    multi = [n for n in PER_RESPONSE if n > 1]
    print(f"  responses with MORE than one call: {len(multi)}")
    print(f"  gate path used: {dict(GATE)}")
    print()
    if multi:
        print("  => batching would have collapsed those into one prompt each.")
    else:
        print("  => this model emits ONE call per response, so batching never")
        print("     fires for it: the confirmation count equals the number of")
        print("     turns. The friction is turns, not multi-call responses.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
