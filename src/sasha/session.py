from __future__ import annotations

import dataclasses
import json
import math
import platform
from typing import Any

from sasha.executor import ExecResult
from sasha.llm import ChatResponse, ToolCall

@dataclasses.dataclass
class NoteCommand:
    line: str
    exit_code: int

class Session:
    def __init__(self, history_token_budget: int = 12000,
                 personality: str = "") -> None:
        self.history_token_budget = history_token_budget
        self._personality = personality
        self.history: list[dict[str, Any]] = []  # Store message dicts
        self.notes: list[NoteCommand] = []  # Store note_command entries
        self.last_stderr: str = ""
        self.system_prompt: str = self._build_system_prompt()

    def _build_system_prompt(self) -> str:
        functional = (
            f"You are Sasha, an interactive shell wrapper. "
            f"Proposed commands require user confirmation with 'y' or 'e'. "
            f"Free-text replies never execute anything. "
            f"The platform is {platform.platform()}. "
            f"The shell is bash."
        )
        if self._personality:
            return f"{self._personality}\n\n{functional}"
        return functional

    def note_command(self, line: str, result: ExecResult) -> None:
        self.notes.append(NoteCommand(line, result.exit_code))
        if len(self.notes) > 10:
            self.notes.pop(0)
        self.last_stderr = result.stderr_text

    def add_user(self, text: str) -> None:
        self.history.append({"role": "user", "content": text})

    def add_assistant(self, response: ChatResponse) -> None:
        msg = {"role": "assistant", "content": response.text}
        if response.tool_calls:
            msg["tool_calls"] = []
            for tc in response.tool_calls:
                msg["tool_calls"].append({
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.name,
                        "arguments": json.dumps(tc.arguments)
                    }
                })
        self.history.append(msg)

    def add_tool_result(self, call_id: str, output: str) -> None:
        self.history.append({"role": "tool", "tool_call_id": call_id, "content": output})

    def _estimate_tokens(self, msg: dict[str, Any]) -> int:
        content = msg.get("content") or ""
        estimate = math.ceil(len(content) / 4)
        if msg.get("role") == "assistant" and "tool_calls" in msg:
            for tc in msg["tool_calls"]:
                args_str = tc["function"]["arguments"]
                estimate += math.ceil(len(args_str) / 4)
        return estimate

    def build_messages(self, cwd: str) -> list[dict[str, Any]]:
        # Build context block
        context_lines = [cwd]
        for note in self.notes:
            context_lines.append(f"{note.line} ({note.exit_code})")
        
        if self.last_stderr:
            context_lines.append(self.last_stderr)
        
        context_block = "\n".join(context_lines)
        
        # Prepare to handle trimming and final user message
        history_to_trim = list(self.history)
        
        # Determine if the last message is user and if so, define final_user_msg and base_text
        if history_to_trim and history_to_trim[-1].get("role") == "user":
            last_user_msg = history_to_trim.pop()
            base_text = last_user_msg.get("content", "")
            # Final user message has context appended.
            # We use \n if there is already content to separate them nicely.
            # The spec doesn't specify, but "appended to its content" is the instruction.
            # I'll use \n if base_text is not empty.
            final_user_msg = {
                "role": "user",
                "content": (base_text + "\n" + context_block) if base_text else context_block
            }
        else:
            base_text = ""
            final_user_msg = {"role": "user", "content": context_block}

        # Trimming logic:
        # "while the summed estimate of the history messages (including the final user message's base text, 
        # excluding the context block) exceeds the budget, drop the oldest message"
        
        def get_current_total_estimate() -> int:
            total = sum(self._estimate_tokens(m) for m in history_to_trim)
            total += math.ceil(len(base_text) / 4)
            return total

        while get_current_total_estimate() > self.history_token_budget and history_to_trim:
            oldest = history_to_trim.pop(0)
            if oldest.get("role") == "assistant" and "tool_calls" in oldest:
                tool_call_ids = {tc["id"] for tc in oldest["tool_calls"]}
                history_to_trim = [m for m in history_to_trim if not (m.get("role") == "tool" and m.get("tool_call_id") in tool_call_ids)]

        # Assemble messages: [system] + trimmed_history + [final_user]
        messages = [self.system_prompt_dict()] + history_to_trim + [final_user_msg]
        self.notes = []
        self.last_stderr = ""
        return messages

    def system_prompt_dict(self) -> dict[str, Any]:
        return {"role": "system", "content": self.system_prompt}
