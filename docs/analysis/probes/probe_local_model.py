"""Probe: does the real sasha LLM path get tool calls out of the 26B local model?

Drives sasha.llm.ChatClient (the actual production code path, including the
request lock and the new on_warning reporting) against the live endpoint.
Nothing is executed — tool calls are only reported, never dispatched.

Scratch tooling: gitignored, not part of the test suite.
"""

from __future__ import annotations

import sys
import time

sys.path.insert(0, "/home/salvador/Code/sasha/src")

from sasha.config import Profile  # noqa: E402
from sasha.llm import ChatClient, ChatError  # noqa: E402
from sasha.tools import REGISTRY  # noqa: E402

BASE_URL = "http://192.168.8.253:11434/v1"
MODEL = "dealignai/Bonsai-2-27B-Ternary-CRACK-GGUF:Q2_0"

profile = Profile(name="local", base_url=BASE_URL, model=MODEL,
                  api_key="", api_key_required=False)

SCHEMA = [t.schema for t in REGISTRY.values()]
print(f"tools offered: {sorted(REGISTRY)}\n")


def run(label, messages, tools):
    print("=" * 72)
    print(label)
    print("=" * 72)
    text, reasoning, warnings = [], [], []
    client = ChatClient(profile, timeout_s=180.0)
    t0 = time.time()
    try:
        resp = client.chat(
            messages, tools=tools,
            on_text=text.append,
            on_reasoning=reasoning.append,
            on_warning=warnings.append,
        )
    except ChatError as exc:
        print(f"ChatError after {time.time() - t0:.1f}s: {exc}")
        return None
    dt = time.time() - t0

    r = "".join(reasoning).strip()
    t = "".join(text).strip()
    print(f"[{dt:.1f}s] tool_calls={len(resp.tool_calls)} "
          f"content_chars={len(t)} reasoning_chars={len(r)}")
    if r:
        print(f"--- reasoning (first 600) ---\n{r[:600]}")
    if t:
        print(f"--- content (first 600) ---\n{t[:600]}")
    for tc in resp.tool_calls:
        print(f"--- tool_call: {tc.name}({tc.arguments})")
    for w in warnings:
        print(f"--- WARNING: {w}")
    if not resp.tool_calls and not warnings:
        print("--- no tool calls, no warnings")
    print()
    return resp


# 1. The plain assistant path ('??' in the REPL): no tools.
run("1. plain question, no tools",
    [{"role": "user",
      "content": "In one short sentence: what does the shell command 'du -sh *' do?"}],
    [])

# 2. The tool path with the full 8-tool registry.
run("2. tool-forcing prompt, all 8 tools",
    [{"role": "user",
      "content": "List the files in the current directory. You must use the "
                 "run_command tool with command 'ls'."}],
    SCHEMA)

# 3. Same, but only one tool, to isolate schema breadth as a variable.
run("3. tool-forcing prompt, run_command only",
    [{"role": "user",
      "content": "List the files in the current directory. You must use the "
                 "run_command tool with command 'ls'."}],
    [REGISTRY["run_command"].schema])
