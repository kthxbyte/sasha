#!/usr/bin/env bash
# scripts/verify.sh — deterministic post-dispatch gate.
# Usage: scripts/verify.sh <base-hash> "<Task X.Y>"
# Reads scripts/verify.conf: TEST_CMD, CONTRACT_TESTS (required),
#                            PROTECTED_PATHS (optional).
set -u
BASE="$1"; TASK="$2"
fail() { echo "VERIFY FAIL [$1]: $2"; exit 1; }

source "$(dirname "$0")/verify.conf"

# 1. Exactly one new commit, direct child of base (no amend/rebase/reset)
[ "$(git rev-list --count "$BASE"..HEAD)" = "1" ] \
  || fail commits "expected exactly 1 commit after $BASE, got $(git rev-list --count "$BASE"..HEAD)"
[ "$(git rev-parse HEAD^)" = "$(git rev-parse "$BASE")" ] \
  || fail history "HEAD is not a direct child of $BASE"

# 2. Trailer grammar
MSG=$(git log -1 --format=%B)
echo "$MSG" | grep -qE "^Done: ${TASK//./\\.} — " \
  || fail trailer "missing 'Done: $TASK — <description>'"
echo "$MSG" | grep -qE "^Next: Task [0-9]+\.[0-9]+ — " \
  || fail trailer "missing valid 'Next: Task N.N — <description>'"
echo "$MSG" | grep -qE "^Co-Authored-By: " \
  || fail trailer "missing 'Co-Authored-By:' executor signature (see AGENTS.md)"

# 3. Contract tests untouched — the implementer may never edit the contract
TOUCHED=$(git diff --name-only "$BASE"..HEAD -- $CONTRACT_TESTS)
[ -z "$TOUCHED" ] || fail contract "contract tests modified: $TOUCHED"

# 4. Protected paths untouched
if [ -n "${PROTECTED_PATHS:-}" ]; then
  TOUCHED=$(git diff --name-only "$BASE"..HEAD -- $PROTECTED_PATHS)
  [ -z "$TOUCHED" ] || fail scope "protected paths modified: $TOUCHED"
fi

# 5. Clean working tree (everything the executor did is in the commit)
[ -z "$(git status --porcelain)" ] || fail tree "working tree not clean after commit"

# 6. Tests — contract tests are the executable acceptance criteria
$TEST_CMD || fail tests "test command failed: $TEST_CMD"

echo "VERIFY PASS: $TASK on base $BASE"
