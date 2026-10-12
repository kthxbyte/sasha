"""Contract tests — Task 3.4: REPL streaming passthrough.

Written by Tier 1 at spec time; immutable to the implementer.
Fixes ISSUE-013. Spec: docs/superpowers/specs/task-3.4-repl-streaming.md.
"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from sse_stub import StubServer  # noqa: E402


class SpyExecutor:
    def __init__(self):
        from sasha.executor import ExecResult

        self.calls = []
        self._result = ExecResult(exit_code=0, signal=None, stderr_text="",
                                  stdout_text="ok", cwd_after="/spy",
                                  duration_ms=1)

    @property
    def cwd(self):
        return "/spy"

    # Amended by Tier 1 for Task 21.1 (safe command auto-approval,
    # ISSUE-041): the classifier reads executor.sensitive_path_patterns
    # for every run_command call, so any duck-typed executor used with
    # run_command now needs this attribute.
    sensitive_path_patterns = ()

    def check_syntax(self, line):
        return None

    def run(self, line, capture_stdout=False):
        self.calls.append(line)
        return self._result


class SpySink:
    """Sink test double (Task 6.1) — tags entries "line" (write_line, e.g.
    the Task 5.2 thinking indicator) vs "chunk" (write_chunk, streamed LLM
    text) distinctly, alongside this file's existing "ask" tag, so a test
    checking streamed-text-only doesn't pick up unrelated write_line calls."""

    color_enabled = False

    def __init__(self, events):
        self._events = events

    def write_line(self, text):
        self._events.append(("line", text))

    def write_chunk(self, text):
        if text:
            self._events.append(("chunk", text))

    def ensure_newline(self):
        last = next((v for k, v in reversed(self._events)
                    if k in ("line", "chunk")), "")
        if last and not last.endswith("\n"):
            self._events.append(("chunk", "\n"))


def _repl(stub_url, events):
    """Build a Repl whose sink and confirmer log into one shared event list."""
    from sasha.config import Config, Limits, Profile
    from sasha.llm import ChatClient
    from sasha.repl import Repl
    from sasha.session import Session

    class LoggingConfirmer:
        def ask(self, command, description):
            events.append(("ask", command))
            return ("no", "")

    profile = Profile(name="stubprof", base_url=stub_url,
                      model="stub-model", api_key="")
    config = Config(default_profile="stubprof",
                    profiles={"stubprof": profile}, limits=Limits())
    return Repl(config, SpyExecutor(), Session(),
                lambda p: ChatClient(p, timeout_s=10.0), LoggingConfirmer(),
                lambda prompt: "", SpySink(events))


def test_ac1_chunks_stream_through():
    events = []
    with StubServer([{"kind": "text",
                      "text": "a reasonably long streamed answer",
                      "chunk_size": 4}]) as stub:
        _repl(stub.base_url, events).run_once("?? tell me")
    out_chunks = [c for kind, c in events if kind == "chunk"]
    assert len(out_chunks) >= 2, "text must stream through as multiple write_chunk() calls"
    # Trailing "\n" is Task 6.1's ensure_newline() (ISSUE-018 fix) — the
    # stub text itself doesn't end in one, so the sink adds it.
    assert "".join(out_chunks) == "a reasonably long streamed answer\n"


def test_ac2_text_precedes_confirmation():
    events = []
    with StubServer([
        {"kind": "tool_call", "id": "call_1", "name": "run_command",
         # Amended by Tier 1 for Task 21.1 (safe command auto-approval,
         # ISSUE-041): "ls" is now classified as safe and would skip the
         # confirmer entirely, so this test (which asserts on preamble
         # text arriving before the confirmation prompt) needs a command
         # outside the auto-approval allowlist. "touch" is not allowlisted.
         "arguments": {"command": "touch /tmp/marker", "description": "create a marker"},
         "text": "let me look around first", "chunk_size": 6},
        {"kind": "text", "text": "fine, not running it"},
    ]) as stub:
        _repl(stub.base_url, events).run_once("?? check something")

    first_ask = next(i for i, (k, v) in enumerate(events) if k == "ask")
    pre_ask_text = "".join(v for k, v in events[:first_ask] if k == "chunk")
    assert "let me look around first" in pre_ask_text, (
        "assistant preamble must reach write_chunk() before the confirmation prompt")
