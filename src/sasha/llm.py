from __future__ import annotations

import dataclasses
import json
import urllib.request
import urllib.error
from typing import Callable, Any

from sasha.config import Profile
from sasha import llm_debug

@dataclasses.dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict

@dataclasses.dataclass(frozen=True)
class ChatResponse:
    text: str
    tool_calls: list[ToolCall]

class ChatError(Exception):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)

    def __str__(self) -> str:
        return self.reason

class ChatClient:
    def __init__(self, profile: Profile, timeout_s: float = 120.0) -> None:
        self.profile = profile
        self.timeout_s = timeout_s

    def chat(self, messages: list[dict], tools: list[dict], 
             on_text: Callable[[str], None],
             on_reasoning: Callable[[str], None] = lambda chunk: None) -> ChatResponse:
        url = f"{self.profile.base_url.rstrip('/')}/chat/completions"
        
        payload = {
            "model": self.profile.model,
            "messages": messages,
            "stream": True,
        }
        if tools:
            payload["tools"] = tools

        llm_debug.log_request(profile=self.profile.name, model=self.profile.model,
                               messages=messages, tools=tools)

        req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), method="POST")
        req.add_header("Content-Type", "application/json")
        if self.profile.api_key:
            req.add_header("Authorization", f"Bearer {self.profile.api_key}")

        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as response:
                full_text = ""
                tool_call_fragments: dict[int, dict[str, Any]] = {}
                done_seen = False

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

                    if "reasoning_content" in delta and delta["reasoning_content"]:
                        on_reasoning(delta["reasoning_content"])

                    if "tool_calls" in delta:
                        for tc_delta in delta["tool_calls"]:
                            index = tc_delta["index"]
                            if index not in tool_call_fragments:
                                tool_call_fragments[index] = {
                                    "id": tc_delta.get("id", ""),
                                    "name": "",
                                    "args_parts": []
                                }
                            
                            if "id" in tc_delta:
                                tool_call_fragments[index]["id"] = tc_delta["id"]
                            if "function" in tc_delta and "name" in tc_delta["function"]:
                                tool_call_fragments[index]["name"] = tc_delta["function"]["name"]
                            if "function" in tc_delta and "arguments" in tc_delta["function"]:
                                tool_call_fragments[index]["args_parts"].append(tc_delta["function"]["arguments"])

                if not done_seen:
                    raise ChatError("Stream ended before [DONE] was received")

                assembled_tool_calls = []
                for index in sorted(tool_call_fragments.keys()):
                    fragment = tool_call_fragments[index]
                    if not fragment["id"] or not fragment["name"]:
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
                        continue
                
                return ChatResponse(text=full_text, tool_calls=assembled_tool_calls)

        except urllib.error.HTTPError as exc:
            raise ChatError(f"HTTP error: {exc.code}")
        except (urllib.error.URLError, OSError) as exc:
            raise ChatError(f"{type(exc).__name__}: {str(exc)}")
        except ChatError:
            raise
        except Exception as exc:
            raise ChatError(f"Unexpected error: {str(exc)}")
