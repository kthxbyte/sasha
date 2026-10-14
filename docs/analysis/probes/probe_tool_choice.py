"""Probe 3: which tools does the 26B actually reach for, and can it fill the
schemas the audit proposed cutting?

Calls the real ChatClient with the full shipped REGISTRY and reports the exact
tool chosen + arguments. Nothing is executed. `question` payloads are checked
with the real question.validate_shape().

Scratch tooling: gitignored, not part of the test suite.
"""

from __future__ import annotations

import json
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, "/home/salvador/Code/sasha/src")

from sasha import question  # noqa: E402
from sasha.config import Profile  # noqa: E402
from sasha.llm import ChatClient, ChatError  # noqa: E402
from sasha.tools import REGISTRY  # noqa: E402

PROFILE = Profile(name="local",
                  base_url="http://192.168.8.253:11434/v1",
                  model="dealignai/Bonsai-2-27B-Ternary-CRACK-GGUF:Q2_0",
                  api_key="", api_key_required=False)
SCHEMA = [t.schema for t in REGISTRY.values()]

cwd = tempfile.mkdtemp(prefix="sasha-probe3-")
Path(cwd, "alpha.txt").write_text("first line\nsecond line\nthird line\n")

SKILLS = ("Available skills:\n"
          "- deploy-to-kubernetes: roll out a service to a Kubernetes cluster\n"
          "- rotate-signing-keys: rotate and publish release signing keys\n"
          "- write-changelog: turn merged PRs into a changelog entry\n\n"
          "Use the `use_skill` tool to load one when it applies.")


def ask(label, prompt, skills_listing="", system_extra=""):
    system = ("You are Sasha, an interactive shell wrapper at "
              f"{cwd}. The shell is bash.")
    if skills_listing:
        system = skills_listing + "\n\n" + system
    if system_extra:
        system = system + "\n\n" + system_extra
    messages = [{"role": "system", "content": system},
                {"role": "user", "content": prompt}]
    text, warnings = [], []
    t0 = time.time()
    try:
        resp = ChatClient(PROFILE, timeout_s=180.0).chat(
            messages, tools=SCHEMA, on_text=text.append,
            on_warning=warnings.append)
    except ChatError as exc:
        print(f"  {label}: ChatError {exc}")
        return None
    dt = time.time() - t0
    print(f"--- {label}  [{dt:.1f}s]")
    if not resp.tool_calls:
        t = "".join(text).strip().replace("\n", " ")
        print(f"    NO TOOL CALL. text: {t[:180]!r}")
    for tc in resp.tool_calls:
        args = json.dumps(tc.arguments, ensure_ascii=False)
        print(f"    tool={tc.name}  args={args[:300]}")
        if tc.name == "question":
            ok = question.validate_shape(tc.arguments)
            print(f"    question.validate_shape -> "
                  f"{'VALID' if ok is not None else 'INVALID (would be rejected)'}")
    for w in warnings:
        print(f"    WARNING: {w}")
    return resp


print(f"cwd {cwd}\ntools {sorted(REGISTRY)}\n")

ask("1. read a file's contents (read_file vs run_command cat)",
    "Show me the contents of alpha.txt.")
ask("2. count lines (read_file vs run_command wc)",
    "How many lines are in alpha.txt?")
ask("3. create a file (write_file)",
    "Create a file called notes.md containing exactly the line: hello from sasha")
ask("4. remember a fact (remember)",
    "Please remember that this project uses pytest for its test suite.")
ask("5. clarifying question (question, nested schema)",
    "I want to add caching to my application. Ask me which caching approach "
    "I prefer before doing anything.")
ask("6. skill selection (use_skill)", 
    "Deploy this service to our Kubernetes cluster.",
    skills_listing=SKILLS)
ask("7. skill selection, near-miss wording",
    "I need to publish the release signing keys for the new version.",
    skills_listing=SKILLS)
