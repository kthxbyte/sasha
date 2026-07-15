# ISSUE-016: TUI programs (vi, mc, etc.) do not display correctly

## Description
Interactive TUI (Text User Interface) programs like `vi`, `less`, or `mc` do not display their interfaces when run through Sasha. The screen remains blank or only shows the initial command, and the program hangs because it cannot receive user input through the terminal.

### Symptoms
- Running an interactive command (e.g., `vi`) results in no visual interface being rendered in the terminal.
- The command appears to hang indefinitely.
- Terminal signals like `Ctrl+C` do not immediately return control to the Sasha REPL, often leaving orphaned processes (e.g., `vi` still running in the background) that must be killed manually.

### Root Cause
The `Executor.run` method is designed to capture and sanitize the output of a command into a buffer (`err_capture`, `out_capture`) and returns it only after the process terminates. 

For TUI programs, this is fatal because:
1. **No Real-time Streaming:** The output (including ANSI escape sequences used for drawing the UI) is buffered rather than streamed directly to the user's terminal.
2. **Sanitization:** The `_sanitize` function explicitly strips out the ANSI escape sequences that TUI programs rely on to render their interfaces.
3. **Input Starvation:** Since the UI is never rendered, the user cannot see prompts or menus, and the process waits indefinitely for input that cannot be provided.

### Proposed Solution
Sasha needs a way to handle "interactive" commands where output is streamed directly to the user's terminal in real-time, bypassing the capture and sanitization logic.
- Implement a "passthrough" mode for commands known to be interactive.
- Refactor the `Executor` to allow streaming raw output to the current TTY while simultaneously capturing a buffer for the `ExecResult`.
- Ensure that signals like `SIGINT` are correctly propagated to the child process and that the REPL regains control immediately.

### Reproduction Steps
1. Start Sasha.
2. Run an interactive command like `vi`.
3. Observe that no interface is displayed.
4. Attempt to interrupt with `Ctrl+C` and observe the unresponsiveness.
