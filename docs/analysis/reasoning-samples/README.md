# Reasoning samples

Raw `reasoning_content` streams captured from the local endpoint
(`dealignai/Bonsai-2-27B-Ternary-CRACK-GGUF:Q2_0` via llama.cpp at
`192.168.8.253:11434`) on 2026-10-12. Each `<name>.reasoning.txt` has a sibling
`<name>.meta.txt` recording the prompt, whether the stream terminated on its
own, elapsed seconds, and character counts.

| sample | terminated | reasoning | content |
|---|---|---|---|
| `stuck_a_pty_unbounded` | **no** (cut at 75s) | 10,383 chars | 0 |
| `stuck_b_list_every` | **no** (cut at 75s) | 9,372 chars | 0 |
| `healthy_a_pty_3sent` | yes | 1,015 chars | 459 |
| `healthy_b_three_modules` | yes | 1,671 chars | 336 |
| `healthy_c_leaked_fd` | yes | 797 chars | 361 |

The two "stuck" prompts are the *same kind of question* as the healthy ones,
phrased without a bound. Bounding the request is what changes the outcome, not
the difficulty.

## Why they are kept

They are the evidence for the negative result that mattered most: measured over
these streams, a stuck response is **not** repetitive.

- `stuck_a`: 143 sentences, **143 unique** — zero duplicated. A `difflib`
  near-duplicate scan (ratio >= 0.80 against the previous 15 sentences) scored
  **0.0%**, while a *healthy* sample that terminated and answered scored 4.8%.
- Distinct-character ratio, zlib compression ratio, unique-5-gram ratio,
  4-word-prefix duplication and hedge-marker rate all failed to separate the two
  groups as well — and on compression and n-gram novelty, healthy reasoning
  scored as *more* repetitive than the loop.

The stuck text is novel but endless: it keeps finding one more nuance to verify
("Let's verify.", "Actually...", "Need nuance:") and never commits to an answer.
Read the middle of `stuck_a_pty_unbounded.reasoning.txt` and the pattern is
unmistakable.

Anyone re-testing a repetition detector should try it against these files first.
If it does not fire on `stuck_a`, it will not solve this.

## Reproducing

```
PYTHONPATH=src python3 docs/analysis/probes/capture_reasoning_samples.py
```

It overwrites this directory, so run it on a copy if you want to compare. The
endpoint must be up; it is flaky and may need retries.
