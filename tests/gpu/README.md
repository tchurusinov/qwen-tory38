# tests/gpu/ — @gpu tests ONLY (Global testing discipline)

GPU-touching tests live here and NEVER in the hermetic default
(`tests/`). Run them via `../run_gpu.sh`, which snapshots nvidia-smi
into `test-logs/gpu-<ts>.log` before discovery. One-box constraint:
never run @gpu tests in parallel with a live review or subagent
session (AGENTS.md VRAM audit rule — audit before every @gpu run).

First resident: MAP 1.7 (single-instance bring-up integration).

Full suite as of 2026-09-22: 1.7 bring-up, 1.8 sustained load,
1.9 OOM ceiling (S1.4 allocation tier), 1.10 port collision,
1.11 kill-9 restart. Estimated wall time ~15-20 min (three 27B
loads in the 1.9 test; run the VRAM audit first — the 1.9 test
asserts the pool state itself and fails loudly if the pool is not
clear).
