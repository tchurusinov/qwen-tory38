#!/usr/bin/env bash
# run_gpu.sh — runner for @gpu tests (Global testing discipline).
#
# GPU-touching tests live ONLY in tests/gpu/ and never run in the
# hermetic default (tests/ is the no-GPU/no-network suite). Before any
# @gpu test, nvidia-smi is snapshotted into the test log so a failed
# run is auditable: log at <repo>/test-logs/gpu-<ts>.log.
#
# Usage: tests/run_gpu.sh [extra args passed to unittest]
# Convention: never run @gpu tests in parallel with a live review or
# subagent session (one-box constraint, AGENTS.md).

set -uo pipefail

cd "$(git rev-parse --show-toplevel)"

LOGDIR="test-logs"
mkdir -p "$LOGDIR"
TS="$(date +%Y%m%d-%H%M%S)"
LOG="$LOGDIR/gpu-$TS.log"

{
  echo "=== @gpu test run $TS ==="
  echo "--- nvidia-smi snapshot (pre) ---"
  nvidia-smi --query-gpu=driver_version,memory.used,memory.total,utilization.gpu --format=csv || true
  nvidia-smi -q -d MEMORY | grep -E 'Used|Total' | head -4 || true
  # /proc/meminfo is the reliable unified-memory view on this GB10 box
  # (nvidia-smi --query-gpu reports [N/A] for memory fields — MAP 0.3).
  grep -E 'MemTotal|MemAvailable' /proc/meminfo || true
  echo "--- free -h ---"
  free -h | sed -n '1,2p'
  echo "--- unittest discover tests/gpu ---"
} > "$LOG"

# Run the test and capture ITS exit code (PIPESTATUS[0]) — the result
# is authoritative. The pipe into tee is for the log only; a tee
# failure (disk full) must NOT change the recorded test result, so
# tee's exit code is intentionally ignored (G3c round 12 P1: the old
# `set +e`/`set -e` toggle around this block was a no-op — `set -e`
# was never globally enabled — and made the intent look load-bearing;
# dropped it and documented the intent instead).
python3 -m unittest discover -s tests/gpu -v 2>&1 | tee -a "$LOG"
rc=${PIPESTATUS[0]}
# "NO TESTS RAN" (exit 5) is the documented Phase-0 state (tests/gpu/ is
# empty until MAP 1.7 adds the first @gpu test) — not a failure.
if [ "$rc" -eq 5 ]; then
  echo "@gpu runner: no tests in tests/gpu/ yet (expected until MAP 1.7) — passing"
  rc=0
fi

{
  echo "--- nvidia-smi snapshot (post) ---"
  nvidia-smi --query-gpu=memory.used,memory.total,utilization.gpu --format=csv || true
  grep -E 'MemTotal|MemAvailable' /proc/meminfo || true
  echo "--- exit code: $rc ---"
} >> "$LOG"

echo "log: $LOG"
exit "$rc"
