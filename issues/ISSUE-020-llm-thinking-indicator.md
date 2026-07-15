# ISSUE-020: Missing "thinking" indicator during LLM queries

## Description
When Sasha consults an LLM (either via `??` or due to a command failure), there is a period of latency while waiting for the LLM response. Currently, Sasha provides no visual feedback to the user that a query is in progress. This can make the application appear hung or unresponsive.

### Symptoms
- A perceptible delay between a user command/question and the LLM response.
- No visual indication (e.g., a spinner or "Thinking..." message) is displayed during this latency period.

### Root Cause
The `_consult` method in `src/sasha/repl.py` calls `run_consult`, which blocks until the LLM responds. There is no mechanism to trigger a UI update (like a spinner) before or during this blocking call.

### Proposed Solution
Provide visual feedback to the user while the LLM is processing.
- **Option A (Spinner)**: Implement a rotating spinner in the terminal (using characters like `| / - \` or Braille patterns) that runs on a separate thread or via `asyncio` while the LLM request is pending.
- **Option B (Simple Text)**: Display a simple `[Thinking...]` message before calling `run_consult`.

### Reproduction Steps
1. Start Sasha.
2. Input a query that requires significant LLM processing time (e.g., a complex question).
3. Observe the lack of feedback during the latency period.
