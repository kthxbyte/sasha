# ISSUE-015: REPL Blocking on Synchronous Command Execution

## Description
Running a command that produces infinite output (e.g., `yes`) or a long-running/hanging process causes the entire Sasha REPL to become unresponsive. 

### Symptoms
- The REPL loop is blocked by the `Executor.run` method.
- Terminal signals like `Ctrl+C` (SIGINT), `Ctrl+D` (EOF), and `Ctrl+Z` (SIGTSTP) do not interrupt the command or return control to the REPL.
- The user is unable to type new commands or exit Sasha.

### Root Cause
The `Executor.run` method is a synchronous, blocking call. When a command is executed, the main execution thread of the `Repl` is trapped within this method until the command completes. Because the `Repl` loop is not running, it cannot process keyboard input or signal handlers that require the main loop to respond.

### Proposed Solution
Transition the command execution model to a non-blocking architecture. 
- **Recommended**: Refactor the core loop and `Executor` to use `asyncio`. This would allow command execution to be `awaited`, keeping the main loop responsive to signals and user input.
- **Alternative**: Use a dedicated thread for command execution and implement a mechanism for the `Repl` to interrupt the thread/subprocess.

### Reproduction Steps
1. Start Sasha.
2. Type `yes` and press Enter.
3. Attempt to interrupt using `Ctrl+C` or `Ctrl+Z`.
4. Observe that the loop continues indefinitely without returning to the prompt.
