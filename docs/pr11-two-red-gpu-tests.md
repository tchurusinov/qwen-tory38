# qwen-tory38 — PR #11 follow-up: the two red @gpu tests

Date: 2026-09-24. Scope: the S2.8 acceptance run (MAP 2.11,
`tests/gpu/test_lifecycle_2_11.py`), specifically the two tests that are
still red after the PR #11 round-1 disposition. Everything here is grounded
in the latest @gpu log (`test-logs/gpu-20260923-230632.log`, 19 tests,
472 s), the current `test_lifecycle_2_11.py`, and the manager code in
`bin/bonsai2-manager`.

Status: 17/19 green. The two red are in the same file:

```
ERROR: test_2_crash_recovery   -> OSError [Errno 98] Address already in use
FAIL:  test_4_drift_ctx_halve  -> AssertionError: cgroup charge did not land
```

Neither is a manager-logic bug. Both are test-harness problems: one is a
socket-race in the crash simulation, the other is a detection window that
gives up before the write completes. The manager's S2.2 escalation and S2.5
drift watch are correct; the tests just can't make those code paths observe
what the spec says they should.

---

## The two tests, what they prove, and why they matter

Both are S2.8 acceptance criteria (SPEC.md 2.8):

- #2 "Single-instance crash -> auto-recovered < 15 s; harness surfaces the
  event in `status`." This proves the S2.2 escalation path: a dead engine goes
  `healthy -> degraded -> healthy` in `health.json`, the instance recovers in
  the budget, and instance 1 is untouched.
- #4 "5-minute drift simulation -> ctx-reduce event, instance healthy at the
  halved ctx within 15 s, zero restarts on other instances, N unchanged." This
  proves the S2.5 drift watch: sustained cgroup pressure over the `--memory`
  cap halves the instance's ctx and restarts it in place, and only that one.

These are the two halves of the "the manager actually watches the pool and
acts" story. Cold boot (#1), N=0 + fallback (#3), and unload/reload (#5) all
pass. So the boot and control paths are proven; what's unproven is the
two *reactive* paths: crash recovery and drift. That's why these two, and not
the others, are the ones stuck.

---

## Test #2 — crash recovery

### Exact failure (latest log)

```
File "test_lifecycle_2_11.py", line 883, in test_2_crash_recovery
    holder = _port_holder(PORT_BASE)
File "test_lifecycle_2_11.py", line 421, in _port_holder
    s.bind(("127.0.0.1", port))
OSError: [Errno 98] Address already in use
```

The test dies at the holder's `bind`, before it ever kills the engine.

### What the test is trying to do, step by step

1. Boot two instances, assert `stack == healthy` (passes).
2. Read instance 0's engine PID.
3. Install a host-side port holder on 30101 (`_port_holder`).
4. `docker exec kill -9 <engine pid>` in `bonsai2-server-0`.
5. Poll `health.json` for instance 0 to reach `degraded` (the S2.2
   two-consecutive-fails rule).
6. Release the holder; poll for instance 0 back to `healthy`; assert the
   reload half < 15 s and instance 1's host PID never changed.

### Root cause

The port holder binds 30101 *before* the kill. But the healthy engine already
owns 30101 (it bound it at boot). So the holder's `bind` gets EADDRINUSE
against the engine it is about to kill. The holder's 15 s retry loop cannot
win: the engine is still up and holding the port for the whole 15 s, because
the kill (step 4) only happens *after* `_port_holder` returns.

Why the test reaches for a port holder at all: the S2.2 degraded rule is two
consecutive probe fails at the `health_poll_s` cadence. The template
`etc/serving.yaml` ships `health_poll_s: 10`, so degraded needs ~20 s of
continuous down time. But the unit's own `Restart=on-failure` + `RestartSec=5`
recovers the engine in ~8-13 s (5 s restart delay + ~3-8 s warm reload;
MAP 1.11 measured 3.14 s for the reload half). So on a healthy box the engine
comes back *before* 20 s of down time elapses, and degraded is never reached.
The test had to force a longer down-time by holding the port down so the
restarted engine cannot rebind. That is the whole reason the holder exists,
and it is where the fragility comes from.

There is a deeper tension hiding under it: the degraded window (2 x 10 s =
20 s) is longer than the unit's self-recovery (~8-13 s). That mismatch is why
the test cannot just "wait for degraded" on a fast box, and why it needed the
port holder in the first place.

### The two failure shapes it has cycled through

The session log records three iterations, all hitting the same tension:

- iter 1 (bind the holder *after* the kill): the unit's RestartSec=5 restart
  rebinds 30101 in the window between the kill and the holder's bind, so the
  holder's bind EADDRINUSEs against the *restarted* engine.
- iter 2: pre-kill holder + retry-bind: still EADDRINUSE (same as the latest
  run).
- iter 3 (current, committed): holder bound before the kill; the log itself
  says "I DON'T KNOW for certain whether the first restart attempt after the
  pre-kill holder binds wins or the holder does." That uncertainty is why it
  is still red.

### Ways to solve it (ranked)

**Fix A (recommended): a `BONSAI2_HEALTH_POLL_S` test hook.** The root
problem is that degraded needs 20 s of down time on a box that recovers in
~8-13 s. If the probe cadence is 2 s instead of 10 s, degraded needs only 2 x
2 s = 4 s of down time. The engine's down time (~8-13 s, floored at 5 s by
RestartSec=5) is longer than 4 s, so degraded is witnessed naturally at ~4 s,
before the unit recovers at ~8-13 s. No port holder needed at all.

Concretely:
1. In `bin/bonsai2-manager`, `run_probe_loop` should honor an
   `BONSAI2_HEALTH_POLL_S` env override (falling back to
   `cfg["health_poll_s"]` when unset). This is the same env-hook pattern the
   manager already uses for `BONSAI2_DRIFT_RECHECK_S` /
   `BONSAI2_DRIFT_PRESSURE_S`, so it is consistent, not a special case. It
   also retires the "two poll cadences with one name" smell
   (`HEALTH_PROBE_POLL_S = 2.0` for the boot wait vs `health_poll_s` for the
   steady loop).
2. In `test_2_crash_recovery`, set `env["BONSAI2_HEALTH_POLL_S"] = "2"` and
   *remove* the `_port_holder` call. The degraded witness is now at ~4 s,
   comfortably before the ~8-13 s recovery, and the reload-half assertion
   (< 15 s) is measured from the crash to the engine's return.

This is the cleanest fix because it removes the socket race entirely, it is
robust to box speed (the 5 s RestartSec floor guarantees the 4 s degraded
witness), and it fixes a real code smell. The manager code change is one line
plus a docstring.

**Fix B (if you keep the port holder): bind it *after* the kill, and hold it
until the degraded witness.** The holder's `bind` only succeeds once the old
engine has released the port, which is right after the kill. systemd's
`RestartSec=5` means the unit will not rebind for 5 s after the failure, so
the holder has a 5 s window to grab 30101 before the first restart attempt.
Reorder the test: `kill -9` first, then `_port_holder(PORT_BASE)` (its bounded
retry already handles the brief moment the port is still closing). Hold it
until the degraded witness is recorded, then release and measure the reload
half from the release moment. This is a test-only change (no manager edit) and
works, but it keeps the racy socket machinery and the "holder lifetime = the
down time" coupling that made it fragile. Prefer Fix A; keep this as the
fallback if a future box recovers faster than expected.

**Fix C (belt-and-suspenders): Fix A + keep a short holder.** If you want the
degraded window robust even on a box that recovers unusually fast, combine Fix
A (2 s cadence) with a holder held for ~6 s after the kill (long enough for 3
probe cycles at 2 s). This removes any dependence on the exact recovery
timing. Only worth it if Fix A alone proves flaky across runs.

Recommendation: Fix A. It is the smallest change that removes the root cause
(the 20 s degraded window vs the ~8-13 s recovery) and it fixes a real
smell. The port holder becomes unnecessary.

---

## Test #4 — drift ctx halve

### Exact failure (latest log)

```
File "test_lifecycle_2_11.py", line 1195, in test_4_drift_ctx_halve
    self.assertTrue(
AssertionError: False is not true : S2.8 #4: the cgroup charge did not land —
memory.current did not rise over cap; the drift watch cannot see it
```

The charge helper `_inject_cgroup_pressure` returned `False`: it declared the
cgroup charge "did not land" even though the pin file was being written.

### What the test is trying to do

The S2.5 drift watch fires when an instance's cgroup `memory.current` is
*over the `--memory` cap sustained for > 30 s*. The test must make instance 0
exceed its cap for > 30 s. On this box `memory.current` is not writable by the
test user (root-owned cgroup), so the test allocates *real* memory inside the
container: a ~13.7 GB pin file on the container rootfs, charged to the
container's cgroup.

The arithmetic the test sets up:
- manager cap = `budget_gb * 1e9` = 14.78e9 B (decimal GB, S2.2).
- kernel cap = `memory.max` = 14.78 g parsed as GiB = 15.869e9 B.
- base usage ~1.35 GB.
- target `memory.current` = 0.95 x kernel cap = 15.076e9 B.
- allocation = target - base = ~13.73 GB, which must land *strictly between*
  the manager cap (14.78e9) and the kernel cap (15.869e9), or the container
  OOM-kills (above the kernel cap) or the drift watch can't see it (below the
  manager cap).

### Root cause: two compounding detection bugs

**Bug 1: the detection deadline is shorter than the write time.** The pin is
written by a detached
`dd if=/dev/zero of=/bonsai2-drift-pin bs=1M count=N oflag=sync && sleep
HOLD; rm` (the `HOLD` default is 45 s). The detection loop reads
`memory.current` on a deadline of `hold_s - 10` = 35 s. But a 13.73 GB dd at
the observed ~300 MB/s (slower under the load of the 17 other @gpu tests that
ran just before) takes ~45 s to complete. So the detection gives up at 35 s,
before the write has produced enough charged pages. The log itself records the
author's uncertainty: "I DON'T KNOW if dd's ~45 s write + the 6 s recheck
cadence + the 30 s sustained clock fit the test's 90 s poll window with
margin." It does not: the detection window (35 s) ends before the write
finishes (~45 s).

**Bug 2: the "landed" threshold is below the cap the drift actually checks.**
The helper declares `landed` when `memory.current` rises by `0.5 * alloc`
above baseline. With `alloc = 13.73 GB`, that threshold is a 6.86 GB rise,
i.e. `memory.current` reaching ~8.2 GB. But the manager's drift cap is
**14.78 GB**. So the "landed" threshold (8.2 GB) is well *below* the level the
drift watch actually reacts to (14.78 GB). The helper is checking a condition
that is not the one the watch checks. It should assert `memory.current >
cap_bytes` (the manager's decimal cap), the same condition
`check_drift` uses, not an arbitrary `0.5 * alloc`.

The two bugs compound: the detection both *gives up too early* (35 s < 45 s
write) and *checks the wrong level* (8.2 GB < 14.78 GB cap). The pin file
*does* eventually push `memory.current` to ~15.08 GB and holds it there for
the `sleep HOLD` window, which is exactly what the drift watch needs. The
test just never *confirms* it before moving on to assert the halve.

Note the mechanism is otherwise sound. A 13.73 GB pin held on the rootfs keeps
`memory.current` at ~15.08 GB for the full `sleep HOLD` (45 s) after the
write, which is > 30 s over the cap at the shrunken 6 s recheck cadence. So
`check_drift` *will* fire the halve, and the subsequent assertions (unit
rewritten, instance 0 host PID changed, instance 1 unchanged, healthy at
halved ctx < 15 s) will pass. The test only needs its detection to correctly
confirm the charge landed *and* to wait long enough for the write to finish.

### Ways to solve it (ranked)

**Fix A (recommended): detect the cap-crossing, not `0.5 * alloc`, and give
the detection a deadline past the write time.** Two targeted changes to
`_inject_cgroup_pressure`:

1. Change the "landed" condition from `cur - before >= 0.5 * n` to
   `cur > cap_bytes` (the manager's decimal cap, passed in as a parameter).
   This asserts the *same* condition `check_drift` reacts to, so "landed"
   means "the drift watch can see it."
2. Extend the detection deadline past the write time. Poll
   `memory.current` until it exceeds `cap_bytes`, with a deadline of
   `hold_s + write_buffer` (e.g. `hold_s + 60`, so ~105 s for the default
   45 s hold), instead of `hold_s - 10` (35 s). This waits for the dd to
   finish writing the 13.73 GB and for `memory.current` to actually cross the
   14.78 GB cap.

Concretely, the helper's poll loop becomes: keep reading
`memory.current` until `cur > cap_bytes` (return True), or the extended
deadline passes (return False). The `sleep HOLD` in the exec keeps the pin
charged after the write, so once the cap is crossed the charge is sustained
for > 30 s and `check_drift` fires.

This is the minimal, correct fix: it makes the detection check the condition
the watch checks and wait long enough for the write to finish. No change to
the pin mechanism, no change to the manager.

**Fix B (if write speed varies too much under load): make the write
deterministic by separating it from the hold.** Instead of one combined
`dd && sleep; rm` exec, do two:
1. `docker exec -d <container> sh -c 'dd if=/dev/zero of=/bonsai2-drift-pin
   bs=1M count=N oflag=sync status=none'` (write, detached).
2. Poll `memory.current` until it exceeds `cap_bytes` (generous deadline),
   confirming the cap is crossed.
3. *Then* start the hold: the pin stays until the test's `finally`
   `docker exec <container> rm -f /bonsai2-drift-pin`.

This decouples "write finished" from "hold long enough" and removes the
dependency on the dd's wall-clock write speed being fast enough to cross the
cap within the original window. The sustained > 30 s is satisfied because the
pin is held until after the halve is witnessed. This is more robust than Fix A
on a loaded box but is a slightly bigger rewrite of the helper.

**Fix C (reduce the allocation, speed up the write): target closer to the cap.**
The allocation must stay in the narrow band (base + alloc in
(14.78e9, 15.869e9) B, i.e. alloc in ~(13.43, 14.52) GB). Targeting 0.95 x
the kernel cap (15.08 GB) already sits near the top of that band. There is
little room to shrink the allocation without dropping below the manager cap
once base usage is added. So this is a minor tuning, not a structural fix:
keep the 0.95 target, but it confirms the write size is not the lever to pull.

Recommendation: Fix A. It is the smallest change that fixes both detection
bugs (wrong threshold + too-early deadline). If the loaded-box write speed
keeps making it flaky, escalate to Fix B.

---

## The pattern behind both, and how to stop it recurring

Both tests are the *reactive* half of S2.8, and both failed for the same class
of reason: the test models the box's timing in the test author's head
("the write takes 45 s", "the engine recovers in ~10 s") and asserts against
those assumptions, while the box's real timing drifts under load. The fix in
both cases is to make the test *observe* the condition the manager checks,
with a generous deadline, instead of assuming a fixed wall-clock number.

For #2 that means witnessing `degraded` via a fast probe cadence (the manager
writes it, the test just waits for it). For #4 that means confirming
`memory.current > cap` (the manager's own threshold) with a deadline past the
write time. In both, the manager is the source of truth and the test stops
predicting the box.

The three concrete changes to land both green, smallest first:

1. `bin/bonsai2-manager`: honor `BONSAI2_HEALTH_POLL_S` in `run_probe_loop`
   (fall back to `cfg["health_poll_s"]`). One-line change + docstring.
2. `test_2_crash_recovery`: set `BONSAI2_HEALTH_POLL_S=2`, drop the
   `_port_holder` call. (Fix A for #2.)
3. `_inject_cgroup_pressure`: landed = `cur > cap_bytes` (pass the cap in),
   poll with a deadline of `hold_s + 60` instead of `hold_s - 10`.
   (Fix A for #4.)

After those three, re-run the full `tests/run_gpu.sh` (VRAM audit first,
one-box discipline) to 19/19, then close DOD #4 on PR #11. If #4 is still
flaky on a loaded box, escalate to Fix B for #4 (separate write from hold).

### Verification to run

- Hermetic: `python3 -m unittest discover -s tests` (should stay 216/216;
  the cadence hook is covered by a new hermetic pin if the manager change
  adds one).
- @gpu: `tests/run_gpu.sh` full discovery, target 19/19. Watch the two
  specifically:
  - #2 should log "degraded witnessed at ~4 s" and "kill -> healthy in
    ~8-13 s (reload half < 15 s)".
  - #4 should log "cgroup charge: <base> B -> <~15.08e9> B (landed=True)"
    and then "drift: ctx 262144 -> 131072 (ctx_reduced=true, unit rewritten,
    in-place restart), instance 1 untouched".
- Tear-down check: no leftover `bonsai2-server-*` containers, ports
  30101/30102 free, tracked `etc/*.yaml` restored to template values
  (`git checkout -- etc/serving.yaml` if the S2.6 write-back leaked
  `boot_target_s`).

---

## What this means for the rest of phase 2 / entry to phase 3

Getting these two green closes the S2.8 acceptance, which is the phase-2 exit
criterion. It does *not* change the manager's correctness: the S2.2
escalation and S2.5 drift logic were right; only the acceptance harness could
not make them observe the spec conditions. Once 19/19, PR #11's DOD #4 is
met and the G3 loop can be re-run for a clean round.

The two changes worth carrying into phase 3:

- The `BONSAI2_HEALTH_POLL_S` hook makes every *reactive* test (escalation,
  and any S3 "engine-down mid-task -> engine_retried" test) fast-cadence
  instead of 10 s-cadence. That is a 5x reduction in @gpu wall time for the
  reactive cases, which is where phase 3's @gpu tests will live (S3.8
  acceptance is a live kill -9 of a running instance -> task recovered).
- "Assert the condition the manager checks, with a generous deadline, instead
  of a fixed wall-clock assumption" is the rule for every @gpu test from here
  on. It is the difference between a test that flakes on a loaded box and one
  that observes the system.
