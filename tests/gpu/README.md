# tests/gpu/ — @gpu tests ONLY (Global testing discipline)

GPU-touching tests live here and NEVER in the hermetic default
(`tests/`). Run them via `../run_gpu.sh`, which snapshots nvidia-smi
into `test-logs/gpu-<ts>.log` before discovery. One-box constraint:
never run @gpu tests in parallel with a live review or subagent
session (AGENTS.md VRAM audit rule — audit before every @gpu run).

First resident: MAP 1.7 (single-instance bring-up integration).

Full suite as of 2026-09-22: 1.7 bring-up, 1.8 sustained load,
1.9 OOM ceiling (S1.4 allocation tier), 1.10 port collision,
1.11 kill-9 restart, 2.5 manager live spawn (S2.2/S2.5 — the
manager boots from the tracked templates, reads the live pool,
spawns 2 real 27B instances in order under user-scope S2.2-shaped
units; ~60–90 s, pool must be ≥ 2×14.78 + 1.0 GB free) (1 case
each) + the hermetic image checks (build-banner 3, runtime-image
5) = 14 cases. Estimated wall time ~15-20 min on a clear pool, up
to ~30 min in the worst case:
the 1.9 test alone can add ~5-8 min (holder sizing loop up to
3 rounds x 20 s, the tight-pool spawn settle, and a cold 6.0 g
load that polls to readiness in steady state — probed ~114 s at
the looser pool); the 27B loads (1.7 ×1, 1.8 ×1, 1.9 ×3, 1.10 ×2,
2.5 ×2) dominate the rest. Run the VRAM
audit first — the 1.9 and 2.5 tests assert the pool state
itself and fail loudly if the pool is not clear.
