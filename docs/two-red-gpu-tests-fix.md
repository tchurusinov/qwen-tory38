# qwen-tory38 — the two red @gpu tests: situation, root cause, fix

Date: 2026-09-24. Branch `hotfix-gpu-2-4-techdebt` (PR #13, open, known-red).
Scope: S2.8 acceptance tests #2 (crash recovery) and #4 (drift ctx halve) in
`tests/gpu/test_lifecycle_2_11.py`. Everything here is grounded in the code,
the PR #11 follow-up doc, the hotfix PR body, the last @gpu verify log
(`test-logs/gpu-verify-2-4.log`), and the memory experiments from the
crashed session (`20260924_095247_1bcca1`).

---

## TL;DR

Both tests are red for two different, now-understood reasons. Neither is a
manager-logic bug; the S2.2 escalation and S2.5 drift logic are correct. Both
are the test harness failing to make the manager observe the spec condition:

- #2 (crash): the S2.2 health probe's 16-token completion round-trips in ~15 s
  when the engine is down (a 10 s `COMPLETION_RT_MAX_S` gate + a 5 s socket
  cap on a dead port). So even at the hotfix's 2 s probe cadence, a failed
  probe costs up to ~15 s, and the 60 s degraded-witness window is eaten by
  the engine's own `Restart=on-failure` + `RestartSec=5` recovery plus slow
  probes. The last health doc the test saw had instance 0 still `healthy` at
  t=60 s. Fix: a `BONSAI2_PROBE_TIMEOUT_S` env hook that caps the probe's
  socket timeout to ~3 s, so a dead probe fails fast and 2 consecutive fails
  register in ~4 s, well inside the ~8-20 s down window.

- #4 (drift): the cgroup charge mechanism. The hotfix writes a ~13.7 GB
  `dd` file to the container rootfs with `oflag=sync`, expecting the charged
  page cache to push `memory.current` over the manager cap. It does not.
  Observed live: `memory.current` moved only ~0.5 MB (1344086016 ->
  1344610304 B) over the 105 s window. Reclaimable page cache does not
  sustainably charge `memory.current`; it needs **anonymous memory held by a
  live process**. The runtime image has no `python3` (verified). Fix: bake
  `python3` into the runtime image (user direction: "bake in python if u
  have to") and allocate a real bytearray in-container, held by the process,
  so the charge lands and is sustained.

The hotfix landed the PR #11 doc's "Fix A" verbatim (the 2 s cadence hook, the
cap-crossing detection, the extended deadline, the diagnostics). That was
correct and is kept. What it got wrong was two box-specific assumptions: that
the probes are fast (#2), and that a `dd` file charges the cgroup (#4). Both
assumptions failed on this box, and the crash-state experiments measured
exactly how.

---

## Why this is the "GB10 unified-memory trap" (your question)

You asked whether the `dgx-spark-qwen38` repo's "GB10 unified-memory trap"
section is the same trap we are jumping head-on. It is the same physical
problem, a different lane, and it is directly relevant to the #4 design.

That repo serves the main agent (SGLang + qwen3.8-27b NVFP4 at :30001). Its
trap is on the SGLang side: SGLang's memory accounting does not see 25-40 GB
of transient allocations on GB10 unified memory (flashinfer fp8 autotuner +
CUDA graph capture allocate outside the tracked pool). Push
`--mem-fraction-static` too high and host available memory reaches zero; on a
box where SSH rides the same memory, that is a freeze only a power cycle
fixes. The operative rule they measured: the fraction is the real guard, the
Docker `--memory` cap bounds host RSS only, and a cgroup does not see CUDA
unified allocations.

Our subagent lane (this repo) is llama.cpp (Ternary-Bonsai-2, the
`prism-ml` fork), not SGLang, so we do not hit the SGLang autotuner/CUDA-graph
bursts. But the same physics applies and the repo's own `extras/gguf/README.md`
says it verbatim: "The unified-memory trap applies here too. llama.cpp's
allocations come out of the same 128 GB pool as everything else on a GB10, and
a Spark that runs out does not swap, it livelocks."

How that shows up in our two tests:

- The pool is one shared 127.5 GB. The main stack holds ~60 GB
  (`nvidia-smi` compute-app 11981 = 60851 MiB) plus ~1.2 GB, and the OS/other
  containers hold the rest. Our subagent pool is what's left. The manager's
  S1.4/S2.3 budget math (per-instance 14.78 GB decimal cap, kernel GiB cap
  15.87 GB) is sized against this shared pool. A subagent that over-allocates
  does not swap; it OOM-kills or livelocks.

- Test #4 is *about* this trap. It deliberately pushes one instance's cgroup
  `memory.current` above the manager's decimal cap and watches the manager
  halve that instance's ctx to recover. The cgroup is the only honest meter on
  unified memory (the Docker cap does not see the real allocation, exactly the
  SGLang-side lesson), so the test must charge the cgroup for real. The
  assumption that a `dd` file does that is the part that broke, for the same
  reason the SGLang repo's "the cgroup does not see the allocation" warning
  exists: the cgroup only charges what is actually resident, and a `dd` file's
  page cache is reclaimable, so it is not resident.

- The "does the whole thing make sense" question: yes, the architecture
  makes sense. One shared pool, main agent at :30001 (SGLang, ~60-76 GB),
  subagent pool (llama.cpp, N instances at 14.78 GB each, N default 3 but
  MVP runs N=1... here the @gpu test uses N=2 to exercise escalation + drift
  on siblings). The manager watches the pool and reacts: S2.2 crash escalation
  (two probe fails -> degraded, five -> restart), S2.5 drift watch (cgroup
  over cap sustained > 30 s -> halve ctx + in-place restart), S2.4 N=0 PTQ1_0
  fallback, S3.2 control queue (unload/reload). That is exactly the
  degradation mechanism the SGLang repo wants: a watchdog that halves context
  and restarts in place when an instance drifts, instead of the whole pool
  livelocking. We are implementing the reactive half; the SGLang repo already
  has the static half (the fraction pin + Docker caps). Both are needed.

- The manager's degradation mechanisms, and whether they have the traps the
  SGLang repo lists: they do, and they are handled.
  - Static budgeting (S2.3/S1.4): reads live `MemTotal`, the main container's
    live cgroup (not a baked footprint), OS reserved 8 GB, computes
    `free = total - main - reserved`, plans N' = floor(free / per-instance).
    The 1.9 rule asserts the pool is clear before any load. This is the
    "size the context the way you would for SGLang, not a discrete GPU" rule.
  - OOM backstop (S1.4): per-instance `--memory`/`--memory-swap` Docker caps +
    the S1.4 allocation tier (need = max(footprint + 1.0 GB margin, cap)) so a
    boot that cannot afford the allocation exits 4 before the instance's KV
    allocation, instead of OOM-killing mid-load.
  - Reactive (S2.5 drift): cgroup over cap sustained > 30 s -> halve ctx
    (rounded to a multiple of 8192, floor 8192), rewrite the unit's
    `--ctx-size` in place, `systemctl restart` that one instance. Only that
    instance; N unchanged; the clock resets when the cgroup returns under cap.
    This is the dynamic equivalent of the SGLang repo's "watch the
    GPU-side headroom after graph capture."
  - Crash (S2.2): two consecutive probe fails -> degraded; five -> manager
    restart attempt; the unit's own `Restart=on-failure`/`RestartSec=5` is the
    first mover and the manager defers to it (NRestarts check) to avoid a
    double restart.
  So yes, the manager has the degradation mechanisms, and they are the
  correct answer to the unified-memory trap at the subagent level. The two red
  tests are not "the thing we are doing does not make sense." They are the
  acceptance proof of those mechanisms, and they are red because the *test
  harness* cannot yet make the manager observe the conditions on this box.

---

## Current state (as of this writing)

Branch `hotfix-gpu-2-4-techdebt`, tip `f1efa7b` (MAP session log entry
documenting the known-red state). PR #13 open. Hermetic suite 218/218,
gate.sh PASS. The two @gpu tests RED:

- `test_2_crash_recovery`: the last health doc in the degraded-witness window
  (60 s) showed instance 0 `healthy` at t=60 s. The engine's own recovery plus
  the probe's slow round-trip kept it looking healthy, so the S2.2
  two-consecutive-fails rule never fired inside the window.
- `test_4_drift_ctx_halve`: `memory.current` moved 1344086016 B ->
  1344610304 B over the 105 s detection window (cap 14780000000 B). The
  `dd`-to-rootfs page cache did not sustainably charge the cgroup.
  `landed=False`.

The last @gpu verify log (`test-logs/gpu-verify-2-4.log`, 2026-09-24 10:55)
confirms both, and the machine is clean afterwards (no bonsai2 containers,
ports 30101/30102 free).

The crash-state experiments from the referenced session measured the cgroup
behavior that breaks #4, directly:

- `memtest4` (alpine, detached `dd` 3 GB to `/pin`, poll 360 s):
  `memory.current` stayed ~761-765 KB the whole time. The `dd` file's page
  cache did not charge the cgroup.
- `memtest5` (runtime image `bonsai2-server:1.0`, try to `python3` a 13.7 GB
  bytearray): `docker exec -d python3` failed exit 1. The runtime image has no
  `python3`.
- The image's toolbelt (verified): `dd`, `sleep`, `cat`, `head`, `yes` present;
  `python3`, `python3.12`, `busybox`, `stress` absent. `dd` is coreutils 9.4.

Conclusion the experiments support: the charge needs anonymous memory held by
a live process, and the image needs a way to allocate it (python3).

---

## Implementation status (this session)

Landed on `hotfix-gpu-2-4-techdebt`:
- `b49cad2`: the `BONSAI2_PROBE_TIMEOUT_S` hook in the manager (threaded
  through `run_probe_loop` -> `_refresh_states` -> `instance_healthy` ->
  `_probe_*`), 2 hermetic pins in `tests/test_manager.py`, the
  `Dockerfile.runtime` python3 bake + in-build `ERR_NO_PYTHON3` assertion,
  the test #4 holder rewritten to the in-container python3 bytearray, test
  #2 setting `BONSAI2_PROBE_TIMEOUT_S=3`.
- `f202317`: the #4 hold widened to 120 s (the cap can be crossed
  ~30-45 s after the holder starts, so the charge must outlive any
  drift-clock start inside the 90 s halve-poll window) + the rebuilt
  runtime image's S1.6 `image.pin` re-recorded in
  `etc/subagent-engine.yaml` (digest `sha256:6b4cd79f...`).

Hermetic: 220 tests OK (was 218; +2 probe-timeout pins). gate.sh PASS
(baseline unchanged: shellcheck soft-skipped, the 4 ruff errors in
`bin/bonsai2-manager` pre-date this work — identical on main).

@gpu re-run of #2 + #4 in progress against the new image
(`bonsai2-server:1.0` @ 6b4cd79f, python3 3.12.3 verified in-image).
Machine audit before the run: ~51 GB available, ports 30101/30102 free,
no stale bonsai2 containers, the main stack holding ~60 GB GPU (unchanged
by the run).

---

## The fix (what lands next)

Both are small, targeted, and follow the PR #11 doc's ranked fixes plus the
crash-state evidence.

### #2 — a `BONSAI2_PROBE_TIMEOUT_S` env hook in the manager

The root cause is the probe's socket timeout. A dead engine's 16-token
completion probe waits up to `COMPLETION_RT_MAX_S + 5 s` = 15 s before
returning, so a "fail" is not cheap. With the 2 s cadence, the manager
cannot register two consecutive fails inside the down window.

Change:
- `bin/bonsai2-manager`: add a `BONSAI2_PROBE_TIMEOUT_S` env hook (same
  pattern as `BONSAI2_HEALTH_POLL_S` / `BONSAI2_DRIFT_RECHECK_S` /
  `BONSAI2_DRIFT_PRESSURE_S`). It caps the probe's socket timeout. Default
  (unset) keeps today's `COMPLETION_RT_MAX_S + 5 s`. The hook is read in
  `run_probe_loop` and fed to the probe calls so a test can force ~3 s.
- `tests/gpu/test_lifecycle_2_11.py` test #2: set
  `BONSAI2_PROBE_TIMEOUT_S=3`. With a 3 s cap, a dead probe fails in ~3 s,
  so 2 consecutive fails register in ~6-8 s, inside the ~8-20 s down window
  (the unit's `RestartSec=5` + warm reload). The degraded witness lands
  naturally, no port holder, and the reload half stays under the 15 s budget.

Why 3 s: it is long enough that a *healthy* warm engine's 16-token completion
(rounds back well under 3 s on a warm cache) still passes, and short enough
that a *dead* port fails fast. It does not change the S2.2 `< 10 s` RT gate
for healthy probes; it only bounds how long a stuck connect may occupy a probe
cycle. The healthy gate stays `COMPLETION_RT_MAX_S`.

### #4 — anonymous-memory charge, python3 baked into the runtime image

The root cause is the charge mechanism, not the detection. The detection fix
from the hotfix (landed = `memory.current > cap_bytes`, deadline `hold_s + 60`)
is correct and stays. The pin mechanism is the remaining gap.

Change:
- `image/Dockerfile.runtime`: bake `python3` into the runtime image
  (`apt-get install -y --no-install-recommends python3` on the CUDA runtime
  base). Rebuild the runtime image via `bin/bonsai2_build_image.py`, which
  re-records `image.pin` (tag + content digest) in
  `etc/subagent-engine.yaml`. This is the user's explicit direction.
- `tests/gpu/test_lifecycle_2_11.py` `_inject_cgroup_pressure`: replace the
  `dd`-file holder with an in-container `python3` holder that allocates a
  `bytearray(alloc_bytes)` (anonymous, touched so it is resident), holds it
  for `hold_s`, then exits. Anonymous memory held by a live process charges
  `memory.current` by the full allocation and stays charged while the process
  runs. The helper still confirms `memory.current > cap_bytes` with the
  extended deadline before returning.
- Target arithmetic stays the hotfix's: read the live `memory.max` (kernel
  binary cap) and `memory.current` base, target `0.95 * kernel_cap`,
  allocate `target - base`. That lands strictly between the manager decimal
  cap (14.78 GB, what `check_drift` compares against) and the kernel cap
  (15.87 GB), so the cgroup crosses the manager cap without OOM-killing.

Why this works and the `dd` did not: anonymous pages are not reclaimable while
a process holds them, so they charge `memory.current` durably. A `dd` file's
pages are page cache, reclaimable under any pressure, so the cgroup's
`memory.current` only sees a tiny transient bump. The crash-state experiments
are the evidence: the detached `dd` moved `memory.current` ~0.5 MB; a real
allocation is what the cgroup meter wants.

Risk to watch: the 13.7 GB bytearray must allocate+touch within the
`hold_s + 60` detection deadline (~105 s). A 13.7 GB `bytearray` touch at
~300 MB/s is ~45 s, within the deadline. If a loaded box is slower, the
deadline is already generous; the alternative is to reduce the allocation or
raise the deadline, both tunable in one place.

### Verification to run (after the change)

- Hermetic: `python3 -m unittest discover -s tests` (expect 218/218, or +2
  for a new `BONSAI2_PROBE_TIMEOUT_S` pin).
- @gpu: `tests/run_gpu.sh` full discovery, target 19/19. Watch the two:
  - #2 should log "degraded witnessed at ~6-8 s" and "kill -> healthy in
    ~15-20 s (reload half < 15 s)".
  - #4 should log "cgroup charge: <base> B -> <~15.08e9> B (landed=True)"
    and then "drift: ctx 262144 -> 131072 (ctx_reduced=true, in-place
    restart), instance 1 untouched".
- Tear-down check: no leftover `bonsai2-server-*` containers, ports
  30101/30102 free, tracked `etc/*.yaml` restored.

Before the @gpu re-run: the VRAM audit (AGENTS.md rule) — the main stack is
asleep-ish (qwen38-sglang ~1.8 GB RSS but 60 GB GPU), and two 14.78 GB
instances need ~30 GB of the free pool. Free pool is ~52 GB available. The
@gpu run is one-box; nothing else loads the GPU in parallel.

### Spec / PR deltas to record

- New env hook `BONSAI2_PROBE_TIMEOUT_S` (test hook, same pattern as the
  existing three; no locked value moved).
- Runtime image changes (python3 added) -> `image.pin` digest re-recorded by
  `bonsai2_build_image.py`; `etc/subagent-engine.yaml` `image.pin` updated.
- MAP session log entry for this fix.
- The PR #13 body's "Known-red state" and "NOT claimed" sections get updated
  to green (or the remaining gap, if any), and the DOD #2/#3 "OBSERVED: red"
  lines get the observed timings.

---

## Open questions (cheap to settle by running, not by guessing)

1. #2 down window: is the engine's real down window on this box long enough
   for a ~6-8 s degraded witness at the 3 s probe cap? The last log said the
   engine was still `healthy` at t=60, which implies either the restart is
   slow or the probe kept passing during the warm reload. The 3 s cap + a
   re-run settles which. If the engine recovers in < 8 s, the degraded
   witness may still miss; the fallback is a `BONSAI2_DRIFT_PRESSURE_S`-style
   shortening of the degraded fails or a longer down window (the doc's Fix B
   holder, but only if needed).
2. #4 allocation speed: does the 13.7 GB bytearray allocate+touch within the
   105 s deadline on a loaded box? If not, reduce the allocation (still > cap)
   or raise the deadline. One-line change either way.
3. python3 footprint: adding `python3` to the runtime image is a few MB of
   image; it does not affect the per-instance 14.78 GB budget (the holder runs
   inside the instance's own cgroup, so it is part of the charge, not extra).
   Confirmed by construction; no separate check needed.
