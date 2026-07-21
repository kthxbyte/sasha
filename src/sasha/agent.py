from typing import Callable, Protocol
from sasha import safe_commands
from sasha import tool_log
from sasha.llm import ChatClient, ChatResponse
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
    max_tool_turns: int = 10,
) -> None:
    previous_key: tuple | None = None
    tool_turns = 0

    while True:
        messages = session.build_messages(executor.cwd)
        schema = [t.schema for t in REGISTRY.values()]
        response = client.chat(messages, tools=schema, on_text=out)
        session.add_assistant(response)

        if not response.tool_calls:
            return

        current_key: tuple | None = None

        for call in response.tool_calls:
            tool = REGISTRY.get(call.name)
            if tool is None:
                session.add_tool_result(call.id, f"Unknown tool: {call.name}")
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
                    result = tool.handler(final_arguments, executor)
                    result_str = result.text
            else:
                verdict = "auto-approved"
                if tool.announce_when_auto_approved:
                    out(f"[sasha] ran (auto-approved): {final_arguments.get('command', '')}")
                result = tool.handler(final_arguments, executor)
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

            if executed:
                current_key = (call.name, tool.repetition_key(final_arguments))

        if response.tool_calls:
            tool_turns += 1
            if ((current_key is not None and current_key == previous_key)
                    or tool_turns >= max_tool_turns):
                out("[sasha] stopping: repeated identical action / "
                    "tool-turn limit reached")
                session.add_user("Answer in text now; no further commands will be run.")
                final_messages = session.build_messages(executor.cwd)
                final_response = client.chat(final_messages, tools=[], on_text=out)
                session.add_assistant(final_response)
                return
            if current_key is not None:
                previous_key = current_key
