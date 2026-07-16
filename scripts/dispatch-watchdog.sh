#!/usr/bin/env bash
# scripts/dispatch-watchdog.sh — wraps one `opencode run` dispatch with hang
# detection, so an unattended run-phase.sh loop doesn't stall indefinitely
# on a dispatch that never reaches its first model call (see
# docs/superpowers/DISPATCH-INCIDENTS.md — cause still undetermined, but
# the signature is consistent: opencode.log logs bootstrap/"init" then goes
# silent, no "stream" line, near-zero CPU).
#
# Usage: scripts/dispatch-watchdog.sh <model> "<prompt>"
# Env vars:
#   WATCHDOG_TIMEOUT_S   seconds with no "stream" line before treating the
#                        dispatch as hung (default 180).
#   WATCHDOG_FULL_TRACE  0 (default) = cheap /proc snapshots only (status,
#                        wchan, fd, cmdline — plain reads, zero overhead on
#                        every dispatch). 1 = also wrap the whole dispatch
#                        in `strace -f` from launch, so a live syscall
#                        trace exists if a hang is detected. This system's
#                        Yama ptrace_scope=1 blocks attaching to an
#                        already-running sibling process after the fact —
#                        confirmed by testing — so the only way to get a
#                        real strace is to be the tracer from the start.
#                        Costs real CPU/time on every dispatch (measured
#                        20-300+MB of trace per successful run in testing)
#                        but writes only to tmpfs and is discarded
#                        immediately on a normal exit.
#
# Exit codes:
#   <opencode's own exit code> — opencode finished on its own (success or
#     failure); caller proceeds to scripts/verify.sh as normal.
#   2 — watchdog detected no "stream" line within WATCHDOG_TIMEOUT_S
#     seconds, captured diagnostics, and killed the process. Caller should
#     escalate immediately — there is no commit to check.
#
# Diagnostics on a detected hang go to /dev/shm/sasha-dispatch-traces/ (a
# tmpfs — RAM-backed, never touches disk, cleared on reboot).
set -u

MODEL="$1"
PROMPT="$2"

TRACE_DIR=/dev/shm/sasha-dispatch-traces
mkdir -p "$TRACE_DIR"

WATCHDOG_TIMEOUT_S="${WATCHDOG_TIMEOUT_S:-180}"
POLL_INTERVAL_S=5
FULL_TRACE="${WATCHDOG_FULL_TRACE:-0}"

OPENCODE_LOG="$HOME/.local/share/opencode/log/opencode.log"
LOG_LINES_BEFORE=$(wc -l < "$OPENCODE_LOG" 2>/dev/null || echo 0)

LIVE_TRACE_FILE="$TRACE_DIR/$(date -u +%Y%m%dT%H%M%SZ)-live.strace"

if [ "$FULL_TRACE" = "1" ]; then
  strace -f -tt -o "$LIVE_TRACE_FILE" opencode run --model "$MODEL" "$PROMPT" < /dev/null &
else
  opencode run --model "$MODEL" "$PROMPT" < /dev/null &
fi
WRAP_PID=$!

ELAPSED=0
RUN_ID=""
HUNG=0
while kill -0 "$WRAP_PID" 2>/dev/null; do
  sleep "$POLL_INTERVAL_S"
  ELAPSED=$((ELAPSED + POLL_INTERVAL_S))

  NEW_LINES=$(tail -n +"$((LOG_LINES_BEFORE + 1))" "$OPENCODE_LOG" 2>/dev/null || true)
  if [ -z "$RUN_ID" ]; then
    RUN_ID=$(printf '%s\n' "$NEW_LINES" | grep -oE 'run=[a-f0-9]+' | head -1 | cut -d= -f2)
  fi
  if [ -n "$RUN_ID" ] && printf '%s\n' "$NEW_LINES" | grep -q "run=$RUN_ID message=stream"; then
    wait "$WRAP_PID"; EXIT_CODE=$?
    rm -f "$LIVE_TRACE_FILE"
    exit "$EXIT_CODE"
  fi

  if [ "$ELAPSED" -ge "$WATCHDOG_TIMEOUT_S" ]; then
    HUNG=1
    break
  fi
done

if [ "$HUNG" -eq 0 ]; then
  # Process exited on its own without ever showing "stream" (e.g. crashed
  # immediately) — not our hang signature. Let the caller's verify.sh catch
  # a missing/bad commit as normal.
  wait "$WRAP_PID"; EXIT_CODE=$?
  rm -f "$LIVE_TRACE_FILE"
  exit "$EXIT_CODE"
fi

# Under FULL_TRACE, WRAP_PID is strace's PID — find the opencode child it's
# actually tracing so /proc snapshots point at the right process.
if [ "$FULL_TRACE" = "1" ]; then
  TARGET_PID=$(pgrep -P "$WRAP_PID" | head -1)
  [ -n "$TARGET_PID" ] || TARGET_PID="$WRAP_PID"
else
  TARGET_PID="$WRAP_PID"
fi

TS=$(date -u +%Y%m%dT%H%M%SZ)
SNAP="$TRACE_DIR/${TS}-pid${TARGET_PID}"
echo "WATCHDOG: no 'stream' line for run=${RUN_ID:-unknown} after ${ELAPSED}s — capturing diagnostics to ${SNAP}.*" >&2

cat "/proc/$TARGET_PID/status" > "$SNAP.status" 2>/dev/null
{ cat "/proc/$TARGET_PID/wchan" 2>/dev/null; echo; } > "$SNAP.wchan"
ls -la "/proc/$TARGET_PID/fd" > "$SNAP.fd" 2>/dev/null
{ tr '\0' ' ' < "/proc/$TARGET_PID/cmdline" 2>/dev/null; echo; } > "$SNAP.cmdline"

if [ "$FULL_TRACE" = "1" ]; then
  mv "$LIVE_TRACE_FILE" "$SNAP.strace" 2>/dev/null
  echo "WATCHDOG: full strace trace preserved at ${SNAP}.strace" >&2
fi

kill -TERM "$WRAP_PID" 2>/dev/null
[ "$TARGET_PID" != "$WRAP_PID" ] && kill -TERM "$TARGET_PID" 2>/dev/null
sleep 2
kill -9 "$WRAP_PID" 2>/dev/null
[ "$TARGET_PID" != "$WRAP_PID" ] && kill -9 "$TARGET_PID" 2>/dev/null

echo "WATCHDOG: killed hung dispatch (run=${RUN_ID:-unknown}). Diagnostics: ${SNAP}.*" >&2
exit 2
