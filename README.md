# Sasha

An LLM-backed interactive shell wrapper. You type shell lines; they run exactly
as bash would. When a line fails, names a command that doesn't exist, or is
plain natural language, Sasha consults an LLM grounded in your actual shell
context — and nothing the LLM proposes ever runs without your confirmation.

**Status: under construction.** This README is a stub; full usage docs land
with Task 3.3. The authoritative design lives in
`docs/superpowers/specs/2026-07-14-sasha-design.md`.

- Python ≥ 3.11, stdlib-only at runtime, Linux, bash at `/bin/bash`.
- Dev setup: `pip install -e .[dev]` (pytest only), tests via `scripts/run-tests.sh`.
