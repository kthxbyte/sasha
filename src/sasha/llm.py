from __future__ import annotations

import dataclasses
import json
import threading
import time
import urllib.request
import urllib.error
from typing import Callable, Any

from sasha.config import Profile
from sasha import llm_debug
from sasha.repetition import RepetitionDetector

# One in-flight request at a time, process-wide.
#
# Local inference servers may serve only one request at a time (llama.cpp's
# default `--parallel 1`, one Ollama slot), so Sasha serializes its own calls
# rather than letting the server decide who waits.
#
# This replaced a *priority* gate, which existed for the two background
# context-building callers -- directory recap and memory bootstrap. Both have
# since been deleted (ISSUE-069, ISSUE-070), so every remaining caller is
# interactive and the priority machinery had no users. `git show 18e75e8` has
# the design if a background caller is ever added again: a background request
# must never make the user's own question wait.
_REQUEST_LOCK = threading.Lock()


# A DeepSeek-family chat template without a tool-calling section emits the
# model's native special-token tool-call syntax as plain content instead of
# populating delta.tool_calls[] (ISSUE-060). Detecting it turns a silent "the
# model chose not to act" into an actionable diagnosis: every tool is dead on
# that profile, and no amount of prompt tuning will show it.
_LEAKED_TOOL_CALL_MARKER = "\uff5ctool\u2581calls\u2581begin\uff5c"


@dataclasses.dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict

@dataclasses.dataclass(frozen=True)
class ChatResponse:
    text: str
    tool_calls: list[ToolCall]
    # None normally; "repetition" when the stream was cut short because the
    # model had begun repeating itself (see sasha/repetition.py).
    stopped_reason: str | None = None

class ChatError(Exception):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)

    def __str__(self) -> str:
        return self.reason

class ChatClient:
    def __init__(self, profile: Profile, timeout_s: float = 120.0,
                 max_think_seconds: int = 0) -> None:
        self.profile = profile
        self.timeout_s = timeout_s
        # 0 disables the budget; see _think_budget_tripped below.
        self.max_think_seconds = max_think_seconds
        # Test seam: overridden to make the patience budget deterministic.
        self._now = time.monotonic

    def chat(self, messages: list[dict], tools: list[dict], 
             on_text: Callable[[str], None],
             on_reasoning: Callable[[str], None] = lambda chunk: None,
             on_warning: Callable[[str], None] | None = None) -> ChatResponse:
        url = f"{self.profile.base_url.rstrip('/')}/chat/completions"
        
        payload = {
            "model": self.profile.model,
            "messages": messages,
            "stream": True,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        llm_debug.log_request(profile=self.profile.name, model=self.profile.model,
                               messages=messages, tools=tools)

        req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), method="POST")
        req.add_header("Content-Type", "application/json")
        if self.profile.api_key:
            req.add_header("Authorization", f"Bearer {self.profile.api_key}")

        def _warn(message: str) -> None:
            if on_warning is not None:
                on_warning(message)

        try:
            # Held for the whole request, streaming included -- see _REQUEST_LOCK.
            with _REQUEST_LOCK:
                with urllib.request.urlopen(req, timeout=self.timeout_s) as response:
                    full_text = ""
                    tool_call_fragments: dict[int, dict[str, Any]] = {}
                    done_seen = False
                    stopped_reason = None
                    reasoning_chars = 0
                    thinking_since = None
                    # Separate detectors: a model can loop in its visible answer
                    # or in its reasoning, and each degenerates independently.
                    content_guard = RepetitionDetector()
                    reasoning_guard = RepetitionDetector()

                    for line_bytes in response:
                        line = line_bytes.decode("utf-8").strip()
                        if not line:
                            continue
                        
                        if line == "data: [DONE]":
                            done_seen = True
                            break
                        
                        if not line.startswith("data: "):
                            continue
                        
                        payload_str = line[6:].strip()
                        if not payload_str:
                            continue

                        try:
                            data = json.loads(payload_str)
                        except json.JSONDecodeError:
                            raise ChatError(f"Invalid JSON payload: {payload_str}")

                        choices = data.get("choices", [])
                        if not choices:
                            continue
                        delta = choices[0].get("delta", {})
                        
                        if "content" in delta and delta["content"]:
                            content = delta["content"]
                            full_text += content
                            on_text(content)
                            if content_guard.feed(content):
                                stopped_reason = "repetition"
                                break

                        if "reasoning_content" in delta and delta["reasoning_content"]:
                            reasoning = delta["reasoning_content"]
                            on_reasoning(reasoning)
                            reasoning_chars += len(reasoning)
                            if reasoning_guard.feed(reasoning):
                                stopped_reason = "repetition"
                                break
                            if thinking_since is None:
                                thinking_since = self._now()
                            if self._think_budget_tripped(
                                    thinking_since, bool(full_text)):
                                stopped_reason = "thinking_budget"
                                break

                        if "tool_calls" in delta:
                            for tc_delta in delta["tool_calls"]:
                                index = tc_delta["index"]
                                if index not in tool_call_fragments:
                                    tool_call_fragments[index] = {
                                        "id": tc_delta.get("id", ""),
                                        "name": "",
                                        "args_parts": []
                                    }
                                
                                if "id" in tc_delta and tc_delta["id"]:
                                    tool_call_fragments[index]["id"] = tc_delta["id"]
                                if "function" in tc_delta:
                                    if "name" in tc_delta["function"] and tc_delta["function"]["name"]:
                                        tool_call_fragments[index]["name"] = tc_delta["function"]["name"]
                                    if "arguments" in tc_delta["function"] and tc_delta["function"]["arguments"] is not None:
                                        tool_call_fragments[index]["args_parts"].append(tc_delta["function"]["arguments"])

                    if stopped_reason is not None:
                        # Cut short on purpose: say why, and drop any tool call
                        # we were mid-way through assembling. A stream we
                        # abandoned is not evidence of a complete call, and
                        # guessing into an execution is the one thing worse than
                        # a truncated answer.
                        if stopped_reason == "repetition":
                            _warn(
                                "[sasha] llm: stopped the model mid-stream — it "
                                "had begun repeating itself, so the answer above "
                                "is truncated. Ask again or narrow the question.")
                        else:
                            waited = self._now() - (thinking_since or self._now())
                            _warn(
                                f"[sasha] llm: stopped the model — it reasoned "
                                f"for {waited:.0f}s ({reasoning_chars:,} "
                                f"characters) without producing any answer. "
                                f"Raise max_think_seconds under [limits] in "
                                f"config.toml if this is real progress.")
                        return ChatResponse(text=full_text, tool_calls=[],
                                            stopped_reason=stopped_reason)

                    if not done_seen:
                        raise ChatError("Stream ended before [DONE] was received")

                    assembled_tool_calls = []
                    for index in sorted(tool_call_fragments.keys()):
                        fragment = tool_call_fragments[index]
                        if not fragment["id"] or not fragment["name"]:
                            # Truncated or headerless call — dropping it is
                            # correct (never guess into an execution), but a
                            # silent drop is indistinguishable from the model
                            # choosing not to act, which is the confusing case.
                            _warn(
                                "[sasha] llm: dropped an incomplete tool call "
                                f"(index {index}: id={fragment['id']!r}, "
                                f"name={fragment['name']!r}) — nothing was run")
                            continue
                        
                        args_str = "".join(fragment["args_parts"])
                        try:
                            args_dict = json.loads(args_str)
                            assembled_tool_calls.append(ToolCall(
                                id=fragment["id"],
                                name=fragment["name"],
                                arguments=args_dict
                            ))
                        except (json.JSONDecodeError, TypeError, ValueError):
                            _warn(
                                f"[sasha] llm: dropped a {fragment['name']!r} "
                                "tool call whose arguments were not valid JSON "
                                f"({args_str[:120]!r}) — nothing was run")
                            continue

                    if not assembled_tool_calls and _LEAKED_TOOL_CALL_MARKER in full_text:
                        _warn(
                            "[sasha] llm: the model emitted a tool call as raw "
                            "special-token text instead of a tool_calls delta — "
                            "no tool ran. This endpoint's chat template has no "
                            "tool-calling section, so every tool is inert on "
                            "this profile (see ISSUE-060).")
                    
                    return ChatResponse(text=full_text, tool_calls=assembled_tool_calls)

        except urllib.error.HTTPError as exc:
            raise ChatError(f"HTTP error: {exc.code}")
        except (urllib.error.URLError, OSError) as exc:
            raise ChatError(f"{type(exc).__name__}: {str(exc)}")
        except ChatError:
            raise
        except Exception as exc:
            raise ChatError(f"Unexpected error: {str(exc)}")

    def _think_budget_tripped(self, thinking_since: float,
                              produced_content: bool) -> bool:
        """True once a response has been reasoning too long without answering.

        Measured on the local 26B at ISSUE-073 (see sasha/repetition.py). The
        model is a thinking model and reasoning at length is normal, so this is a
        PATIENCE limit, not a budget for thought:

          "List every stdlib module ... be thorough"  no answer, 90s+ (cut)
          "Explain how a PTY works ..."               no answer, 90s+ (cut)
          "... in three sentences" (same question)    answered, 4,241 chars, 29s
          "Name three modules"                        answered,   919 chars,  7s

        Seconds rather than characters because the endpoint serves four slots and
        its character rate varies with load; how long the user is willing to wait
        does not. The clock starts at the first reasoning chunk, so a slow
        prompt-processing phase is not counted against the model.

        Once content starts this stops watching: the model is making progress,
        and the failure mode is no answer at all.
        """
        if self.max_think_seconds <= 0 or produced_content:
            return False
        return (self._now() - thinking_since) > self.max_think_seconds
