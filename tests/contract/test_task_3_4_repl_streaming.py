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

    def check_syntax(self, line):
        return None

    def run(self, line, capture_stdout=False):
        self.calls.append(line)
        return self._result


class NullMutes:
    def is_muted(self, command, exit_code):
        return False

    def record_ignored(self, command, exit_code):
        return False

    def record_acted(self, command, exit_code):
        pass

    def mute(self, command, exit_code=None):
        pass

    def unmute(self, command, exit_code=None):
        pass


def _repl(stub_url, events):
    """Build a Repl whose out() and confirmer log into one shared event list."""
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
    return Repl(config, SpyExecutor(), NullMutes(), Session(),
                lambda p: ChatClient(p, timeout_s=10.0), LoggingConfirmer(),
                lambda prompt: "", lambda chunk: events.append(("out", chunk)))


def test_ac1_chunks_stream_through():
    events = []
    with StubServer([{"kind": "text",
                      "text": "a reasonably long streamed answer",
                      "chunk_size": 4}]) as stub:
        _repl(stub.base_url, events).run_once("?? tell me")
    out_chunks = [c for kind, c in events if kind == "out"]
    assert len(out_chunks) >= 2, "text must stream through as multiple out() calls"
    assert "".join(out_chunks) == "a reasonably long streamed answer"


def test_ac2_text_precedes_confirmation():
    events = []
    with StubServer([
        {"kind": "tool_call", "id": "call_1", "name": "run_command",
         "arguments": {"command": "ls", "description": "list"},
         "text": "let me look around first", "chunk_size": 6},
        {"kind": "text", "text": "fine, not running it"},
    ]) as stub:
        _repl(stub.base_url, events).run_once("?? check something")

    first_ask = next(i for i, (k, v) in enumerate(events) if k == "ask")
    pre_ask_text = "".join(v for k, v in events[:first_ask] if k == "out")
    assert "let me look around first" in pre_ask_text, (
        "assistant preamble must reach out() before the confirmation prompt")
