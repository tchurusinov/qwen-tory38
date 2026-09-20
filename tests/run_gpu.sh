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
  echo "--- free -h ---"
  free -h | sed -n '1,2p'
  echo "--- unittest discover tests/gpu ---"
} > "$LOG"

set +e
python3 -m unittest discover -s tests/gpu -v 2>&1 | tee -a "$LOG"
rc=${PIPESTATUS[0]}
set -e

{
  echo "--- nvidia-smi snapshot (post) ---"
  nvidia-smi --query-gpu=memory.used,memory.total,utilization.gpu --format=csv
  echo "--- exit code: $rc ---"
} >> "$LOG"

echo "log: $LOG"
exit "$rc"
