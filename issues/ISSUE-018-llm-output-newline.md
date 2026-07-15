# ISSUE-018: LLM output missing trailing newline causes prompt discontinuity

## Description
LLM responses often do not end with a trailing newline. When Sasha renders these responses, the shell prompt is printed on the same line as the last line of the LLM text, breaking the visual continuity and expectation of a standard command-line interface.

### Symptoms
- LLM output is printed, and the shell prompt immediately follows on the same line.
- Example:
    ```
    ~/Sasha ✦ hello
    This is an LLM response.~/Sasha ✦ 
    ```
- Instead of:
    ```
    ~/Sasha ✦ hello
    This is an LLM response.
    ~/Sasha ✦ 
    ```

### Root Cause
In `src/sasha/repl.py`, the `stream_out` callback inside `_consult` writes text directly to `sys.stdout`. If the LLM's final chunk of text does not contain a trailing newline, the next `print(prompt)` (or equivalent in the loop) will append the prompt to that same line.

### Proposed Solution
Ensure that the terminal output always ends with a newline after an LLM consultation.
- Modify the `stream_out` callback in `src/sasha/repl.py` to keep track of whether the last character written was a newline.
- If the LLM finishes and the last character was not a newline, explicitly print a newline before the REPL loop proceeds to the next prompt.

### Reproduction Steps
1. Start Sasha.
2. Ask a question where the LLM response is likely to be a single line without a newline (e.g., a simple greeting or a short fact).
3. Observe the prompt appearing on the same line as the response.
