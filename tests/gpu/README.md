# tests/gpu/ — @gpu tests ONLY (Global testing discipline)

GPU-touching tests live here and NEVER in the hermetic default
(`tests/`). Run them via `../run_gpu.sh`, which snapshots nvidia-smi
into `test-logs/gpu-<ts>.log` before discovery. One-box constraint:
never run @gpu tests in parallel with a live review or subagent
session (AGENTS.md VRAM audit rule — audit before every @gpu run).

First resident: MAP 1.7 (single-instance bring-up integration).

Full suite as of 2026-09-22: 1.7 bring-up, 1.8 sustained load,
1.9 OOM ceiling (S1.4 allocation tier), 1.10 port collision,
1.11 kill-9 restart (1 case each) + the hermetic image checks
(build-banner 3, runtime-image 5) = 13 cases. Estimated wall time
~15-20 min on a clear pool, up to ~25-30 min in the worst case:
the 1.9 test alone can add ~5-8 min (holder sizing loop up to
3 rounds x 20 s, the tight-pool spawn settle, and a cold 6.0 g
load that polls to readiness in steady state — probed ~114 s at
the looser pool); three 27B loads dominate the rest. Run the
VRAM audit first — the 1.9 test asserts the pool state itself
and fails loudly if the pool is not clear.
