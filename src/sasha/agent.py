from __future__ import annotations

from typing import Callable, Protocol
from sasha import question
from sasha import safe_commands
from sasha import tool_log
from sasha.llm import ChatClient
from sasha.session import Session
from sasha.executor import Executor
from sasha.tools import REGISTRY

class Confirmer(Protocol):
    def ask(self, command: str, description: str) -> tuple[str, str]:
        """Returns (verdict, payload):
        ('yes', ''), ('no', ''), ('edit', edited_command), ('reply', text)."""
        ...

def run_consult(
    session: Session,
    client: ChatClient,
    executor: Executor,
    confirmer: Confirmer,
    out: Callable[[str], None],
    on_reasoning: Callable[[str], None] = lambda chunk: None,
    on_status: Callable[[str], None] | None = None,
    on_warning: Callable[[str], None] | None = None,
    on_async_status: Callable[[str], None] | None = None,
    max_tool_turns: int = 50,
    on_phase: Callable[[str | None], None] = lambda phase: None,
    questioner: question.Questioner | None = None,
) -> None:
    status = on_status if on_status is not None else out
    warn = on_warning if on_warning is not None else out
    async_status = on_async_status if on_async_status is not None else status
    previous_key: tuple | None = None
    tool_turns = 0

    def _consult_llm(messages, tools):
        on_phase("thinking")
        stopped = False

        def _on_text(chunk):
            nonlocal stopped
            if not stopped:
                on_phase(None)
                stopped = True
            out(chunk)

        def _on_reasoning(chunk):
            nonlocal stopped
            if not stopped:
                on_phase(None)
                stopped = True
            on_reasoning(chunk)

        response = client.chat(messages, tools=tools, on_text=_on_text,
                                on_reasoning=_on_reasoning, on_warning=warn)
        on_phase(None)
        return response

    while True:
        messages = session.build_messages(executor.cwd)
        schema = [t.schema for t in REGISTRY.values()]
        response = _consult_llm(messages, schema)
        session.add_assistant(response)

        if not response.tool_calls:
            return

        current_key: tuple | None = None
        current_action: tuple | None = None

        for call in response.tool_calls:
            tool = REGISTRY.get(call.name)
            if tool is None:
                session.add_tool_result(call.id, f"Unknown tool: {call.name}")
                tool_log.log_call(
                    tool_name=call.name, call_id=call.id, arguments=call.arguments,
                    executed_command=None, cwd=executor.cwd, verdict="unknown_tool",
                    executed=False, result_text=None, result_omitted_reason=None)
                continue

            if call.name == "question":
                questions = question.validate_shape(call.arguments)
                if questions is None:
                    result_str = ("Malformed question payload — each entry in "
                                   "`questions` must be an object with question/"
                                   "header/options, not a plain string. Try again.")
                    verdict = "malformed"
                elif questioner is None:
                    result_str = "question tool is not available in this session."
                    verdict = "unavailable"
                else:
                    pairs = questioner.ask(questions)
                    if pairs is None:
                        result_str = "REJECTED: user dismissed the question(s)"
                        verdict = "rejected"
                    else:
                        result_str = question.format_result(pairs)
                        verdict = "answered"
                session.add_tool_result(call.id, result_str)
                tool_log.log_call(
                    tool_name="question", call_id=call.id, arguments=call.arguments,
                    executed_command=None, cwd=executor.cwd, verdict=verdict,
                    executed=True, result_text=result_str, result_omitted_reason=None)
                current_key = ("question", call.arguments)
                current_action = ("question", call.arguments)
                continue

            final_arguments = call.arguments
            executed = False
            executed_command: str | None = None

            needs_confirmation = tool.requires_confirmation
            if callable(needs_confirmation):
                needs_confirmation = needs_confirmation(call.arguments, executor)

            if needs_confirmation:
                summary = tool.confirmation_summary(call.arguments)
                description = call.arguments.get("description", "")
                verdict, payload = confirmer.ask(summary, description)

                if verdict == "yes":
                    executed = True
                elif verdict == "edit" and call.name == "run_command":
                    final_arguments = {**call.arguments, "command": payload}
                    executed_command = payload
                    executed = True
                elif verdict == "edit":
                    result_str = f"User did not run this and replied: {payload}"
                elif verdict == "reply":
                    result_str = f"User did not run this and replied: {payload}"
                else:  # "no"
                    result_str = "User declined this action."

                if executed:
                    on_phase("tool")
                    result = tool.handler(final_arguments, executor)
                    on_phase(None)
                    result_str = result.text
            else:
                verdict = "auto-approved"
                if tool.announce_when_auto_approved:
                    status(f"[sasha] ran (auto-approved): {final_arguments.get('command', '')}")
                on_phase("tool")
                result = tool.handler(final_arguments, executor)
                on_phase(None)
                result_str = result.text
                executed = True

            session.add_tool_result(call.id, result_str)

            result_text = None
            result_omitted_reason = "declined"
            if executed:
                if (call.name == "run_command"
                        and safe_commands.touches_sensitive_path(
                            final_arguments.get("command", ""), executor)):
                    result_omitted_reason = "sensitive_path"
                else:
                    result_text = result_str
                    result_omitted_reason = None

            tool_log.log_call(
                tool_name=call.name,
                call_id=call.id,
                arguments=call.arguments,
                executed_command=executed_command,
                cwd=executor.cwd,
                verdict=verdict,
                executed=executed,
                result_text=result_text,
                result_omitted_reason=result_omitted_reason,
            )
            tool_log.check_and_maybe_compact_async(async_status)

            if executed:
                current_key = (call.name, tool.repetition_key(final_arguments))
                current_action = (call.name, final_arguments)

        if response.tool_calls:
            tool_turns += 1
            repeated = current_key is not None and current_key == previous_key
            exhausted = tool_turns >= max_tool_turns
            if repeated or exhausted:
                desc = None
                if current_action is not None:
                    name, args = current_action
                    desc = f"{name}({args})"
                    if len(desc) > 200:
                        desc = desc[:200] + "\u2026"
                if repeated:
                    message = "[sasha] stopping: repeated identical action"
                    if desc is not None:
                        message += f" \u2014 {desc}"
                else:
                    message = (f"[sasha] stopping: hit the {max_tool_turns}-"
                               f"action limit for this turn")
                    if desc is not None:
                        message += f" \u2014 last action: {desc}"
                    message += (" \u2014 raise max_tool_turns under [limits] "
                               "in config.toml if this looks like real progress")
                warn(message)
                session.add_user("Answer in text now; no further commands will be run.")
                final_messages = session.build_messages(executor.cwd)
                final_response = _consult_llm(final_messages, [])
                session.add_assistant(final_response)
                return
            if current_key is not None:
                previous_key = current_key
