#!/usr/bin/env bash
# scripts/run-phase.sh — Tier 2 loop driver: dispatch → gate → track, task by
# task, until a phase boundary or an escalation. No LLM judgment lives here.
# Every exit path prints why; fixing anything is a Tier 1 decision.
set -u
cd "$(dirname "$0")/.."

MODEL="${SASHA_DISPATCH_MODEL:-deepseek/deepseek-v4-pro}"

escalate() { echo "ESCALATE: $1"; exit 1; }

# Tier 3 run ledger — metrics/ is gitignored so FAIL rows never dirty the gate.
metrics_row() {  # $1 = result label
  mkdir -p metrics
  [ -f metrics/dispatches.csv ] \
    || echo "utc_start,utc_end,task,model,dispatch_seconds,result" > metrics/dispatches.csv
  echo "$T_START,$(date -u +%FT%TZ),$TASK,$MODEL,$(( SECONDS - S0 )),$1" >> metrics/dispatches.csv
}

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
  T_START=$(date -u +%FT%TZ); S0=$SECONDS
  echo "=== $TASK — dispatching $(basename "$SPEC") on base ${BASE:0:12} via $MODEL ==="
  opencode run --model "$MODEL" "$(realpath "$SPEC")

Sign your commit with exactly this trailer as the last line:
Co-Authored-By: $MODEL (opencode) <noreply@opencode.ai>"

  if ! scripts/verify.sh "$BASE" "$TASK"; then
    metrics_row "FAIL-gate"
    escalate "gate failure on $TASK (see VERIFY FAIL above)"
  fi
  if git log -1 --format=%B | grep -q '^Opened:'; then
    metrics_row "ESCALATE-issue"
    escalate "issue opened during $TASK (see last commit)"
  fi
  metrics_row "PASS"

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
