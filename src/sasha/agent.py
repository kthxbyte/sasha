from typing import Callable, Protocol
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

            if tool.requires_confirmation:
                summary = tool.confirmation_summary(call.arguments)
                description = call.arguments.get("description", "")
                verdict, payload = confirmer.ask(summary, description)

                if verdict == "yes":
                    executed = True
                elif verdict == "edit" and call.name == "run_command":
                    final_arguments = {**call.arguments, "command": payload}
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
                result = tool.handler(final_arguments, executor)
                result_str = result.text
                executed = True

            session.add_tool_result(call.id, result_str)

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
