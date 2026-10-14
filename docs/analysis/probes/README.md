# Probes

The ad-hoc scripts behind the measurements in the parent directory. They were
written to answer specific questions, run once, and kept because re-deriving
them is more work than reading them.

| Script | Question it answers |
|---|---|
| `probe_local_model.py` | What does the endpoint actually report about itself — context size, slots, quantisation, tokens/sec, prompt caching — versus what its `/v1/models` claims? |
| `probe_tool_choice.py` | Given the real tool schemas, does this model select the right tool? Includes near-miss cases (`use_skill(name="remember")`). |
| `probe_agent_loop.py` | Does a full multi-turn tool loop work end to end against the local model? |
| `probe_repl_live.py` | Does the real REPL drive the real model correctly? |
| `probe_reasoning_modes.py` | How does `compact` versus `full` reasoning display behave on real streams? |
| `probe_crash_net_live.py` | Does the crash safety net survive a genuine in-process fault? |
| `capture_reasoning_samples.py` | Captures the streams in `../reasoning-samples/` — the evidence for the repetition finding. |

## Running them

From the repository root:

```
PYTHONPATH=src python3 docs/analysis/probes/probe_tool_choice.py
```

They assume:
- the local endpoint in each script's `BASE_URL` is reachable (default
  `http://192.168.8.253:11434/v1`, the `local` profile's address);
- stdlib only, no test framework — they print what they find.

They are **not** part of the test suite and are not run by CI. Nothing in
`src/` imports them.
