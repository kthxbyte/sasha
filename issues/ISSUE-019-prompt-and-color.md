# ISSUE-019: Enhanced shell prompt and terminal colorization

## Description
The current Sasha shell prompt is minimalist (`~/Sasha ✦ `), lacking the context (username and hostname) typically provided by shells like bash (`user@hostname:path $`). Additionally, the entire interface is monochrome, making it difficult to visually distinguish between different types of output (the prompt, command output, stderr, and LLM responses).

### Symptoms
- Prompt provides minimal context (only current directory, abbreviated).
- No visual differentiation between:
    - The shell prompt.
    - Standard output (`stdout`).
    - Error output (`stderr`).
    - LLM-generated responses.

### Root Cause
The prompt is hardcoded as a simple string in `src/sasha/repl.py`. The output mechanism in `src/sasha/repl.py` and `src/sasha/executor.py` writes raw text to the terminal without any ANSI color escape sequences.

### Proposed Solution
Enhance the visual interface of Sasha through a more informative prompt and the use of terminal colors.

#### 1. Enhanced Prompt
- Update the prompt in `src/sasha/repl.py` to include the current username and hostname (e.g., `user@hostname:path ✦ `).
- Use standard environment variables (`USER`, `HOSTNAME`) to construct this prompt.

#### 2. Terminal Colorization
Implement a color scheme using ANSI escape sequences to differentiate elements:
- **Shell Prompt**: A distinct color (e.g., Cyan or Green).
- **stderr**: A standard error color (e.g., Red).
- **LLM Responses**: A unique color (e.g., Yellow or Magenta) to clearly separate them from command output.
- **Sasha-specific messages**: Use a consistent color for messages prefixed with `[sasha]`.

### Reproduction Steps
1. Start Sasha.
2. Observe the current minimalist, monochrome prompt and output.
