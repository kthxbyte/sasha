#!/usr/bin/env bash
# scripts/run-tests.sh — Tier 2 test selection (used as TEST_CMD by verify.sh).
# Runs implementer tests plus contract tests for every task checked off in
# PLAN.md and for the task marked '← current'. Contract tests for future
# tasks are committed early (red by design) and excluded here until their
# task becomes current. Deterministic; no LLM.
set -u
cd "$(dirname "$0")/.."
export PYTHONPATH="$PWD/src${PYTHONPATH:+:$PYTHONPATH}"

TASKS=$( { grep -E '^- \[x\] Task [0-9]+\.[0-9]+' PLAN.md
           grep -E '^- \[ \] Task [0-9]+\.[0-9]+.*← current' PLAN.md
         } | grep -oE 'Task [0-9]+\.[0-9]+' | sed 's/^Task //' | sort -uV )

ARGS=()
for t in $TASKS; do
  for f in tests/contract/test_task_${t//./_}_*.py; do
    [ -e "$f" ] && ARGS+=("$f")
  done
done
[ -d tests/impl ] && ARGS+=("tests/impl")

if [ "${#ARGS[@]}" -eq 0 ]; then
  echo "run-tests: no test targets selected (no tasks done or current)"
  exit 0
fi
exec python3 -m pytest -q "${ARGS[@]}"
