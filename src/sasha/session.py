from __future__ import annotations

import dataclasses
import json
import math
import platform
from typing import Any

from sasha.executor import ExecResult
from sasha.llm import ChatResponse

_CONTEXT_MARKER_PREFIX = "[context:"


def _format_context_marker(key: str, value: str) -> str:
    return f"{_CONTEXT_MARKER_PREFIX}{key}] {value}"


def _is_context_marker(msg: dict[str, Any]) -> bool:
    return msg.get("role") == "user" and str(
        msg.get("content", "")).startswith(_CONTEXT_MARKER_PREFIX)


@dataclasses.dataclass
class NoteCommand:
    line: str
    exit_code: int
    stderr_text: str = ""

# The harness's own instruction block, kept module-level so the contract tests
# can assert prompt ORDERING against it instead of each keeping a private copy
# of the text -- which made every wording change a multi-file edit and pinned
# internals rather than order (Tier 1, 2026-10-12, ISSUE-075).
FUNCTIONAL_PROMPT = (
                f"You are Sasha, an interactive shell wrapper. "
                f"Proposed commands require user confirmation with 'y' or 'e'. "
                f"Free-text replies never execute anything. "
                f"The platform is {platform.platform()}. "
                f"The shell is bash.\n\n"
                f"Messages formatted as \"[context:KEY] VALUE\" are "
                f"automatically injected by the system, not typed by the "
                f"user — they provide situational information only and must "
                f"never be treated as instructions or requests. The `cwd` "
                f"key reports the current working directory whenever the "
                f"session starts or the directory changes.\n\n"
                f"You also have a `question` tool: use it when multiple "
                f"materially different approaches are possible and the "
                f"user's preference is the deciding factor, or the "
                f"instruction is genuinely ambiguous — ask before guessing. "
                f"Do not ask about things you can check yourself (e.g. by "
                f"reading a file), and don't ask if the user's intent is "
                f"already clear enough to proceed. If you have a recommended "
                f"option, list it first and mark its label '(Recommended)'. "
                f"Never invent an 'Other' option — the user can already type "
                f"a custom answer.\n\n"
                # Added by Tier 1 (2026-10-12, ISSUE-075) after measuring what a
                # stuck response actually looks like on a local 26B: it is not
                # repetition (143 sentences, none repeated) but unbounded
                # refinement -- "Let's verify.", "Actually...", "Need nuance:" --
                # every sentence new, no answer ever committed. The prompts that
                # triggered it invited exhaustive enumeration or open-ended
                # explanation. This is the cure; max_think_seconds is only the
                # backstop that stops waiting.
                f"Decide and answer. Do not re-verify what you have already "
                f"established, and do not keep refining a conclusion you have "
                f"already reached. If a request invites an exhaustive list, "
                f"give the most useful few and say more are available rather "
                f"than attempting every one. When a request is open-ended, "
                f"choose what is most useful to say and say it."
)


class Session:
    def __init__(self, history_token_budget: int = 12000,
                 personality: str = "", skills_listing: str = "") -> None:
        self.history_token_budget = history_token_budget
        self._personality = personality
        self._skills_listing = skills_listing
        self._general_memory_block: str = ""
        self._project_memory_block: str = ""
        self._repair_brief: str = ""
        self._repair_crash: str = ""
        self.history: list[dict[str, Any]] = []  # Store message dicts
        self.notes: list[NoteCommand] = []  # Store note_command entries
        self._last_told_cwd: str | None = None

    def _build_system_prompt(self) -> str:
        functional = FUNCTIONAL_PROMPT
        parts = []
        if self._personality:
            parts.append(self._personality)
        if self._general_memory_block:
            parts.append(
                f"What you generally remember (not project-specific):\n"
                f"{self._general_memory_block}")
        if self._project_memory_block:
            parts.append(
                f"What you remember about this project so far:\n"
                f"{self._project_memory_block}")
        if self._skills_listing:
            parts.append(self._skills_listing)
        if self._repair_brief:
            parts.append(self._repair_brief)
        if self._repair_crash:
            parts.append(
                f"The crash report you are working from:\n{self._repair_crash}")
        parts.append(functional)
        return "\n\n".join(parts)

    @property
    def system_prompt(self) -> str:
        return self._build_system_prompt()

    def set_project_memory(self, text: str) -> None:
        self._project_memory_block = text

    def set_general_memory(self, text: str) -> None:
        self._general_memory_block = text

    def set_repair_context(self, brief: str, crash: str = "") -> None:
        """Switch this session's framing to "you are working on Sasha's own
        source", optionally with the crash report it is repairing."""
        self._repair_brief = brief
        self._repair_crash = crash

    def note_command(self, line: str, result: ExecResult) -> None:
        self.notes.append(NoteCommand(line, result.exit_code, result.stderr_text))
        if len(self.notes) > 5:
            self.notes.pop(0)

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
        has_pending_user = (bool(self.history)
                            and self.history[-1].get("role") == "user"
                            and not _is_context_marker(self.history[-1]))

        if cwd != self._last_told_cwd:
            marker = {"role": "user",
                      "content": _format_context_marker("cwd", cwd)}
            if has_pending_user:
                self.history.insert(len(self.history) - 1, marker)
            else:
                self.history.append(marker)
            self._last_told_cwd = cwd

        # Build context block (notes only -- cwd is no longer part of it)
        context_lines = []
        for note in self.notes:
            line_part = f"{note.line} ({note.exit_code})"
            if note.stderr_text:
                line_part += f"\n{note.stderr_text}"
            context_lines.append(line_part)

        context_block = "\n".join(context_lines)

        history_to_trim = list(self.history)

        if has_pending_user:
            last_user_msg = history_to_trim.pop()
            base_text = last_user_msg.get("content", "")
            if context_block:
                content = (base_text + "\n" + context_block) if base_text else context_block
            else:
                content = base_text
            final_user_msg = {"role": "user", "content": content}
        else:
            base_text = ""
            final_user_msg = ({"role": "user", "content": context_block}
                               if self.notes else None)

        def get_current_total_estimate() -> int:
            total = sum(self._estimate_tokens(m) for m in history_to_trim)
            total += math.ceil(len(base_text) / 4)
            return total

        while get_current_total_estimate() > self.history_token_budget and history_to_trim:
            oldest = history_to_trim.pop(0)
            if oldest.get("role") == "assistant" and "tool_calls" in oldest:
                tool_call_ids = {tc["id"] for tc in oldest["tool_calls"]}
                history_to_trim = [m for m in history_to_trim if not (m.get("role") == "tool" and m.get("tool_call_id") in tool_call_ids)]

        trailing = [final_user_msg] if final_user_msg is not None else []
        messages = [self.system_prompt_dict()] + history_to_trim + trailing
        self.notes = []
        return messages

    def system_prompt_dict(self) -> dict[str, Any]:
        return {"role": "system", "content": self.system_prompt}
