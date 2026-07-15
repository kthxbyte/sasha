# ISSUE-017: Redundant LLM explanation on "command not found" (exit 127)

## Description
When a user enters natural language that is not a valid bash command, Sasha triggers an automatic LLM consultation (`AUTO_CONSULT`). The user currently sees three distinct layers of feedback for a single input:
1. The standard shell error (e.g., `/bin/bash: line 1: hi: command not found`).
2. A hardcoded Sasha prompt explaining that the command failed and suggesting it might be a question.
3. The LLM's own response interpreting the input.

This sequence is redundant and creates a clunky, "chatty" user experience.

### Symptoms
- User input: `hi there`
- Output:
    ```
    /bin/bash: line 1: hi: command not found
    The line `hi there` failed with exit 127 (command not found, or not a command at all). If it was meant as a question or calculation, answer it; if it looks like a typo, say so and give the corrected command.
    [LLM Response]
    ```

### Root Cause
In `src/sasha/repl.py`, the `run_once` method checks if a command resulted in `Decision.AUTO_CONSULT` (which occurs on exit code 127). If so, it calls `self._consult(prompt)` with a verbose, instructional string that describes exactly what happened. Since the LLM also receives the command and its exit status in the session history, the instruction is redundant.

### Proposed Solution
Simplify the `AUTO_CONSULT` flow in `src/sasha/repl.py` to provide a more seamless experience:
- **Option A (Minimalist):** Instead of `self._consult(prompt)`, simply call `self._consult()`. This uses the existing session history (which contains the failed command and the error) to let the LLM decide how to respond without the redundant preamble.
- **Option B (Refined Prompt):** Replace the verbose prompt with a very short, invisible prompt or a much more natural one that doesn't repeat the exit code.

### Reproduction Steps
1. Start Sasha.
2. Type a string that is clearly not a command (e.g., `hello world`).
3. Observe the redundant feedback loop.
