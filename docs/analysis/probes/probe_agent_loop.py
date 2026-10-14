"""Probe 2: the real agent loop + the prompt-size hypothesis, live.

(a) run_consult() end-to-end: build_messages -> tool call -> confirmation gate
    -> real execution -> tool result -> final answer. Uses sasha.agent as
    shipped, scripted confirmer so nothing needs a terminal.
(b) The same tool-forcing prompt with a *full-size* system prompt (personality
    + general memory + project memory + skills listing), to test the audit's
    claim that a long heterogeneous system prompt degrades tool calling on a
    26B. Measured, not assumed.

Scratch tooling: gitignored, not part of the test suite.
"""

from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, "/home/salvador/Code/sasha/src")

from sasha import agent  # noqa: E402
from sasha.config import Profile  # noqa: E402
from sasha.executor import Executor  # noqa: E402
from sasha.llm import ChatClient  # noqa: E402
from sasha.session import Session  # noqa: E402

BASE_URL = "http://192.168.8.253:11434/v1"
MODEL = "dealignai/Bonsai-2-27B-Ternary-CRACK-GGUF:Q2_0"
PROFILE = Profile(name="local", base_url=BASE_URL, model=MODEL,
                  api_key="", api_key_required=False)


class ScriptedConfirmer:
    """Approves the first N proposals, then declines."""

    def __init__(self, approve=2):
        self.approve = approve
        self.seen = []

    def ask(self, command, description):
        self.seen.append((command, description))
        if len(self.seen) <= self.approve:
            return ("yes", "")
        return ("no", "")


def run(label, session, prompt, cwd, approve=2, max_tool_turns=3):
    print("=" * 72)
    print(label)
    print("=" * 72)
    executor = Executor(cwd, {"PATH": "/usr/bin:/bin", "HOME": cwd,
                              "TERM": "dumb"})
    confirmer = ScriptedConfirmer(approve=approve)
    out, reasoning, status, warn = [], [], [], []

    session.add_user(prompt)
    t0 = time.time()
    agent.run_consult(
        session, ChatClient(PROFILE, timeout_s=180.0), executor, confirmer,
        out=out.append,
        on_reasoning=reasoning.append,
        on_status=status.append,
        on_warning=warn.append,
        max_tool_turns=max_tool_turns,
    )
    dt = time.time() - t0

    print(f"[{dt:.1f}s] system_prompt={len(session.system_prompt)} chars")
    print(f"proposals: {[c for c, _ in confirmer.seen]}")
    print(f"history turns: {len(session.history)}")
    text = "".join(out).strip()
    if text:
        print(f"--- assistant text (first 700) ---\n{text[:700]}")
    for w in warn:
        print(f"--- WARNING: {w}")
    print()
    return {"text": text, "proposals": confirmer.seen, "warnings": warn,
            "system_chars": len(session.system_prompt)}


cwd = tempfile.mkdtemp(prefix="sasha-probe-")
Path(cwd, "alpha.txt").write_text("a")
Path(cwd, "beta.txt").write_text("b")
print(f"cwd: {cwd}\n")

# --- (a) end-to-end agent loop, normal-size prompt -------------------------
s1 = Session(history_token_budget=12000)
run("a. agent loop, minimal system prompt",
    s1, "List the files in the current directory, then tell me how many there are.",
    cwd)

# --- (b) same task, full-size system prompt -------------------------------
PERSONALITY = ("You are Sasha, a warm, concise shell assistant. "
               + ("Prefer plain language and short answers. " * 150))
GENERAL = "\n".join(f"- [2026-01-{i % 28 + 1:02d}] general note {i}: "
                    f"user prefers terse output and {i} other facts."
                    for i in range(90))
PROJECT = "\n".join(f"- [2026-02-{i % 28 + 1:02d}] project note {i}: "
                    f"the build uses pytest and {i} other details."
                    for i in range(90))
SKILLS = ("Available skills:\n"
          + "\n".join(f"- skill-{i}: a playbook for doing task number {i} "
                      f"carefully and thoroughly" for i in range(25))
          + "\n\nUse the `use_skill` tool to load one when it applies.")

s2 = Session(history_token_budget=12000, personality=PERSONALITY,
             skills_listing=SKILLS)
s2.set_general_memory(GENERAL)
s2.set_project_memory(PROJECT)
res2 = run("b. agent loop, full-size system prompt (personality+memory+skills)",
           s2, "List the files in the current directory, then tell me how many there are.",
           cwd)

print("=" * 72)
print("SUMMARY")
print("=" * 72)
print(f"(b) system prompt: {res2['system_chars']} chars "
      f"(~{res2['system_chars'] // 4} tokens est)")
print(f"(b) proposals: {res2['proposals']}")
print(f"(b) warnings: {res2['warnings']}")
