# tests/gpu/ — @gpu tests ONLY (Global testing discipline)

GPU-touching tests live here and NEVER in the hermetic default
(`tests/`). Run them via `../run_gpu.sh`, which snapshots nvidia-smi
into `test-logs/gpu-<ts>.log` before discovery. One-box constraint:
never run @gpu tests in parallel with a live review or subagent
session (AGENTS.md VRAM audit rule — audit before every @gpu run).

First resident: MAP 1.7 (single-instance bring-up integration).
