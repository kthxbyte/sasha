from typing import Callable, Protocol
from sasha.llm import ChatClient, ChatResponse
from sasha.session import Session
from sasha.executor import Executor

RUN_COMMAND_TOOL = {
    "type": "function",
    "function": {
        "name": "run_command",
        "description": "Propose one bash command to run in the user's shell. The user sees the command and your description and must approve it before it runs.",
        "parameters": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "exact bash command"},
                "description": {"type": "string", "description": "one line: what this does and why"},
            },
            "required": ["command", "description"],
        },
    },
}

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
    previous_command: str | None = None
    tool_turns = 0

    while True:
        messages = session.build_messages(executor.cwd)
        response = client.chat(messages, tools=[RUN_COMMAND_TOOL], on_text=out)
        session.add_assistant(response)

        if not response.tool_calls:
            return

        current_executed_command: str | None = None

        for call in response.tool_calls:
            if call.name != "run_command":
                session.add_tool_result(call.id, f"Unknown tool: {call.name}")
                continue

            command = call.arguments.get("command", "")
            description = call.arguments.get("description", "")
            
            verdict, payload = confirmer.ask(command, description)
            
            if verdict == "yes":
                result = executor.run(command, capture_stdout=True)
                result_str = f"exit code: {result.exit_code}\nstdout:\n{result.stdout_text}\nstderr:\n{result.stderr_text}"
                current_executed_command = command
            elif verdict == "edit":
                result = executor.run(payload, capture_stdout=True)
                result_str = f"exit code: {result.exit_code}\nstdout:\n{result.stdout_text}\nstderr:\n{result.stderr_text}"
                current_executed_command = payload
            elif verdict == "no":
                result_str = "User declined to run this command."
            elif verdict == "reply":
                result_str = f"User did not run the command and replied: {payload}"
            else:
                result_str = "User declined to run this command."
            
            session.add_tool_result(call.id, result_str)

        if response.tool_calls:
            tool_turns += 1
            if ((current_executed_command is not None
                    and current_executed_command == previous_command)
                    or tool_turns >= max_tool_turns):
                out("[sasha] stopping: repeated identical command / tool-turn limit reached")
                session.add_user("Answer in text now; no further commands will be run.")
                final_messages = session.build_messages(executor.cwd)
                final_response = client.chat(final_messages, tools=[], on_text=out)
                session.add_assistant(final_response)
                return
            if current_executed_command is not None:
                previous_command = current_executed_command
