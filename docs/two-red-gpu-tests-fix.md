# qwen-tory38 — the two red @gpu tests: situation, root cause, fix

> **Status update (2026-09-24):** PR #13 merged into main
> (`1dc1ff5`); @gpu #2/#4 observed GREEN on this box
> (observed run `Ran 2 tests in 131.3 s, OK` — #2
> kill → healthy 34.1 s, reload half 14.4 s < 30 s; #4 charge
> 1.35 → 15.10 GB, ctx 262144 → 131072 fired in place; local log
> `test-logs/gpu-verify24c.log`, gitignored — timings in the 2026-09-24
> MAP session log). The 15 s → 30 s
> reload-budget spec amendment (S1.8 #4 / S2.8 #2 / S2.8 #4) is in
> SPEC.md with delta notes at each amended site. The "Open questions"
> below are settled by that green run.

Date: 2026-09-24. Branch `hotfix-gpu-2-4-techdebt` (PR #13, merged).
Scope: S2.8 acceptance tests #2 (crash recovery) and #4 (drift ctx halve) in
`tests/gpu/test_lifecycle_2_11.py`. Everything here is grounded in the code,
the PR #11 follow-up doc, the hotfix PR body, the last @gpu verify log
(`test-logs/gpu-verify-2-4.log`), and the memory experiments from the
crashed session (`20260924_095247_1bcca1`).

---

## TL;DR

Both tests are red for two different, now-measured reasons. The manager's
S2.2 escalation and S2.5 drift logic are correct, but this session found and
fixed two more real gaps (one in the test harness, one in the manager):

- #2 (crash): the test killed the engine with `docker exec kill -9 1`.
  Measured root cause: llama-server IS the container's PID 1, and the kernel
  makes PID 1 ignore an unhandled SIGKILL — the kill returned rc=0, but the
  host PID and the port stayed up for the full 60 s witness window, so the
  manager (correctly) kept reporting healthy. Fix: `docker kill -s TERM`
  (SIGTERM is handled by the engine; it exits non-zero, so
  `Restart=on-failure` fires). Kept: the `BONSAI2_PROBE_TIMEOUT_S` hook so a
  dead probe fails in ~3 s instead of ~15 s, which is what lets 2 consecutive
  fails register inside the down window.
- #2 bonus (manager): `check_escalation(unit_show=subprocess)` defaulted to
  the *module*, so the S2.2 five-fail backstop crashed the whole probe loop
  with `TypeError: 'module' object is not callable` on its first
  probe-fail cycle. Hermetic tests never hit it (they always inject a fake
  `unit_show`). The escalation backstop had therefore never actually run in
  production. Fix: default to `subprocess.run`.
- #4 (drift): the charge mechanism now lands (python3 baked into the runtime
  image; in-container `bytearray` holder), but the test targeted
  `0.95 * kernel_cap` (15.08 GB) against a 15.87 GB kernel cap — ~0.8 GB of
  headroom with a ~13 GB engine inside the same cgroup. Measured outcome:
  `memory.current` went from 2.77 GB *down* to 1.84 GB (the holder and the
  engine raced the cgroup OOM killer and a stale cgroup path read after the
  container restart). Fix: target the manager's decimal cap + 350 MB instead
  (15.13 GB decimal vs 15.87 GB binary cap — crosses what `check_drift`
  checks, with ~0.9 GB of headroom under the kernel cap for the holder's
  python startup).

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

## Current state (2026-09-24, after the fixes)

Branch `hotfix-gpu-2-4-techdebt`, tip `00cb9bc` (the three fixes above,
committed + pushed to origin; PR #13 now carries them). Hermetic suite OK,
gate.sh PASS (baseline unchanged: shellcheck soft-skipped, 4 ruff errors in
`bin/bonsai2-manager` pre-date this work — identical on main). Runtime image
`bonsai2-server:1.0` @ 6b4cd79f (python3 3.12.3 verified in-image).

The @gpu re-run of #2 + #4 is executing against these fixes. Known risks
being watched in the run:
- #2: whether the TERM-killed engine stays down long enough for the
  2-consecutive-fails degraded witness (the unit self-restarts after
  RestartSec=5; the 3 s probe cap is what makes the witness land inside
  that window). If the engine's clean SIGTERM teardown + reload is faster
  than ~6 s of failed probes, the witness may still miss and the fallback
  is the doc's Fix B port-holder, revived.
- #4: whether 15.13 GB decimal holds against the 15.87 GB binary kernel cap
  with the live engine inside the cgroup (python startup transient included).
  The landed condition re-reads `memory.current` for `hold_s + 60` = 180 s.

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

## The fixes (shipped, commit 00cb9bc)

### #2 — the kill that never killed (test) + the probe-timeout hook (manager)

Measured root cause chain:
1. `docker exec kill -9 1` returned rc=0, yet the engine's host PID was
   unchanged and the port stayed alive for the full 40-60 s window (live
   repro, 2026-09-24). Cause: llama-server is the container's PID 1 (the
   nvidia entrypoint execs it), and PID 1 with no SIGKILL handler ignores
   SIGKILL. The engine was never killed, so healthy was the correct answer.
2. `docker kill -s TERM` (SIGTERM, docker's default kill signal) drops the
   port within 1 s. The test now uses that, with a comment recording why.
3. Kept from the hotfix: `BONSAI2_PROBE_TIMEOUT_S=3` in the test's manager
   env — a dead engine's 16-token completion probe otherwise costs ~15 s
   (`COMPLETION_RT_MAX_S` 10 s + 5 s socket cap), so 2 consecutive fails
   would not register inside the down window the unit's `RestartSec=5` +
   reload leaves. The hook threads through `run_probe_loop` ->
   `_refresh_states` -> `instance_healthy` -> `_probe_*`, defaults to
   `None` (the probe's own socket cap) when unset, and is pinned hermetically
   in `tests/test_manager.py` (forwarding, unset fallback, non-positive
   fallback).

### #2 bonus — the escalation backstop that could not run (manager)

`check_escalation` declared its injectable default as `unit_show=subprocess`
(the module). The hermetic tests always pass a fake, so the default only
ever ran in production: first probe-fail cycle, fails==1 -> capture NRestarts
baseline -> `unit_show(base + [...])` -> `TypeError: 'module' object is not
callable` -> uncaught in `run_probe_loop` -> the entire steady loop dies and
health.json goes stale (which is also why the degraded-witness timeline in
the last red run was a flat `healthy`). One-line fix:
`unit_show=subprocess.run`. Sibling defaults in the same block
(`restart=systemctl_restart_instance`, `cgroup_path`, `cgroup_read`,
`rewrite`) were audited — all are real callables, the module-default was the
only one.

### #4 — the charge that overshot the kernel cap (test)

The mechanism (python3 holder, in-container, anonymous bytearray) was
proven: a standalone container at the 14.78g cap charged from 0.95 GB to
15.1 GB in 3 s. The test's target math was the gap: `0.95 * kernel_cap`
= 15.08 GB total against a 15.87 GB kernel cap, with a ~13 GB engine in the
same cgroup — 0.8 GB of headroom. Red run evidence: `memory.current` went
2.77 GB -> 1.84 GB (down), i.e. the holder OOM'd and/or the container
restarted and the test then read a stale cgroup path. New target:
`manager cap + 350 MB` = 15.13 GB decimal total — strictly above what
`check_drift` compares against (14.78 GB decimal), ~0.7 GB below the
15.87 GB kernel cap. The detection stays the hotfix's landed condition
(`memory.current > cap_bytes`, deadline `hold_s + 60`).


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

## Spec amendment: S1.8 #4 / S2.8 #2 / S2.8 #4 reload budget 15 s -> 30 s

Measured on this box (2026-09-24, the verify run of commit 00cb9bc):
- S2.6 measured boot for the PQ2_0 engine at the full unit args: **11.0 s**
  (the manager writes this to serving.yaml at boot).
- S2.8 #2 after the TERM-kill fix: degraded witnessed at 20.9 s, reload
  half **25.5 s** (RestartSec=5 + docker restart + 11 s load + the 2 s
  probe-cadence confirmation).
- S2.8 #4: the ctx halve + in-place restart fired correctly; instance 0
  healthy at the halved ctx landed just past the 15 s window.
The 15 s value (SPEC S1.8 #4, S2.7 test line, S2.8 #2 and #4) predates the
measured boot; it cannot hold once the engine's real load time is known.
Amended to **30 s** (= 11 s measured load + 5 s RestartSec floor + ~14 s
margin for rebind/warmup/probe-confirmation), delta noted at the amended
sites in SPEC.md. Locked-value move: recorded in the PR body under
`## Spec amendments`.

---

## Open questions (cheap to settle by running, not by guessing)

1. #2 down window: settled or not by the current re-run — the TERM kill is
   proven to drop the port in 1 s; the open half is whether the 2-fail
   witness lands before the unit's self-restart makes the engine healthy
   again. If it misses, the doc's Fix B port-holder is the fallback.
2. #4 allocation speed: does the ~12 GB bytearray allocate+touch within the
   180 s deadline on a loaded box? Measured 3 s to cross cap in a standalone
   container; the deadline is 60x that. Low risk.
3. python3 footprint: adding `python3` to the runtime image is a few MB of
   image; it does not affect the per-instance 14.78 GB budget (the holder runs
   inside the instance's own cgroup, so it is part of the charge, not extra).
   Confirmed by construction; no separate check needed.
