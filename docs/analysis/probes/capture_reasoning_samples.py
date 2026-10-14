#!/usr/bin/env python3
"""Capture reasoning streams from the local endpoint for analysis.

This is the script that produced ../reasoning-samples/. It was originally run
from /tmp, so nothing here depends on the repository layout beyond `src` being
importable.

    PYTHONPATH=src python3 docs/analysis/probes/capture_reasoning_samples.py

Writes <name>.reasoning.txt and <name>.meta.txt per prompt. Each prompt is cut
off after a fixed number of seconds rather than waiting for `[DONE]`, because
the whole point is that some of them never finish.
"""
import json
import pathlib
import sys
import time
import urllib.request

BASE_URL = "http://192.168.8.253:11434/v1"
OUT = pathlib.Path(__file__).resolve().parent.parent / "reasoning-samples"


def stream(prompt, cap_s):
    req = urllib.request.Request(
        BASE_URL + "/chat/completions",
        data=json.dumps({"model": "local", "stream": True,
                         "messages": [{"role": "user", "content": prompt}]}).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    content, reasoning, start, done = [], [], time.time(), False
    with urllib.request.urlopen(req, timeout=30) as response:
        for line in response:
            if time.time() - start > cap_s:
                break
            line = line.decode("utf-8", "replace").strip()
            if line == "data: [DONE]":
                done = True
                break
            if not line.startswith("data: "):
                continue
            delta = json.loads(line[6:]).get("choices", [{}])[0].get("delta", {})
            if delta.get("reasoning_content"):
                reasoning.append(delta["reasoning_content"])
            if delta.get("content"):
                content.append(delta["content"])
    return done, "".join(reasoning), "".join(content), round(time.time() - start, 1)


CASES = [
    ("stuck_a_pty_unbounded",
     "Explain how a PTY works between a shell wrapper and vim.", 75),
    ("stuck_b_list_every",
     "List every Python standard-library module for terminal handling, one "
     "sentence each. Be thorough.", 75),
    ("healthy_a_pty_3sent",
     "Explain in three sentences how a PTY works between a shell wrapper and "
     "vim.", 45),
    ("healthy_b_three_modules",
     "Name three Python standard-library modules for terminal handling, one "
     "sentence each.", 45),
    ("healthy_c_leaked_fd",
     "Why does a leaked file descriptor matter in a shell wrapper? Answer in "
     "three sentences.", 45),
]

if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    for name, prompt, cap in CASES:
        done, reasoning, content, elapsed = stream(prompt, cap)
        (OUT / f"{name}.reasoning.txt").write_text(reasoning, encoding="utf-8")
        (OUT / f"{name}.meta.txt").write_text(
            f"prompt: {prompt}\nterminated: {done}\nelapsed_s: {elapsed}\n"
            f"reasoning_chars: {len(reasoning)}\ncontent_chars: {len(content)}\n",
            encoding="utf-8")
        print(f"  {'OK ' if done else 'CUT'} {name:26} reason={len(reasoning):>6,} "
              f"content={len(content):>5,} elapsed={elapsed:>5.1f}s", flush=True)
