#!/usr/bin/env bash
# scripts/run-phase.sh — Tier 2 loop driver: dispatch → gate → track, task by
# task, until a phase boundary or an escalation. No LLM judgment lives here.
# Every exit path prints why; fixing anything is a Tier 1 decision.
set -u
cd "$(dirname "$0")/.."

MODEL="deepseek/deepseek-v4-pro"

escalate() { echo "ESCALATE: $1"; exit 1; }

[ -z "$(git status --porcelain)" ] || escalate "working tree not clean before dispatch"

while true; do
  CURRENT_LINE=$(grep -m1 -E '^- \[ \] Task [0-9]+\.[0-9]+ — .*← current' PLAN.md) \
    || escalate "no '← current' task found in PLAN.md"
  TASK=$(echo "$CURRENT_LINE" | grep -oE 'Task [0-9]+\.[0-9]+' | head -1)
  NUM=${TASK#Task }
  NUM_ESC=${NUM//./\\.}

  SPEC=$(ls docs/superpowers/specs/task-${NUM}-*.md 2>/dev/null | head -1)
  [ -n "$SPEC" ] || escalate "no task spec matching docs/superpowers/specs/task-${NUM}-*.md"

  BASE=$(git rev-parse HEAD)
  echo "=== $TASK — dispatching $(basename "$SPEC") on base ${BASE:0:12} ==="
  opencode run --model "$MODEL" "$(realpath "$SPEC")"

  scripts/verify.sh "$BASE" "$TASK" || escalate "gate failure on $TASK (see VERIFY FAIL above)"
  git log -1 --format=%B | grep -q '^Opened:' && escalate "issue opened during $TASK (see last commit)"

  # ---- tracking commit (deterministic, sed-driven) ----
  TODAY=$(date +%F)
  sed -i "s/^- \[ \] \(Task ${NUM_ESC} — .*\) ← current\$/- [x] \1/" PLAN.md
  sed -i "/Task ${NUM_ESC} —/{s/| Open |/| Done |/; s/| — |\$/| ${TODAY} |/}" ISSUE-TRACKING.md

  NEXT=$(grep -m1 -oE '^- \[ \] Task [0-9]+\.[0-9]+' PLAN.md | grep -oE '[0-9]+\.[0-9]+' || true)
  if [ -n "$NEXT" ]; then
    NEXT_ESC=${NEXT//./\\.}
    sed -i "s/^- \[ \] \(Task ${NEXT_ESC} — .*\)\$/- [ ] \1 ← current/" PLAN.md
  fi

  git add PLAN.md ISSUE-TRACKING.md
  git commit -q -m "Track $TASK" -m "Verified: $TASK — PASS — gate + contract tests green" \
    || escalate "tracking commit failed for $TASK"
  echo "=== $TASK done and tracked ==="

  [ -n "$NEXT" ] || { echo "PLAN COMPLETE — Tier 1 review required"; exit 0; }
  if [ "${NEXT%%.*}" != "${NUM%%.*}" ]; then
    echo "PHASE ${NUM%%.*} COMPLETE — Tier 1 phase review required (next: Task $NEXT)"
    exit 0
  fi
done
