# Consolidated direct fixes — frontier model reviews of qwen-tory38

## 0. Context, provenance, scope

Source: two independent code-review reports on commit `b17eed0`
(2026-10-05):

- `qwen-tory38-codex-sol-6.1-code-review.md` (OpenAI sol 6.1) — "SOL"
- `qwen-tory38-review-2026-10-05-fable-5.5.md` (Anthropic fable 5.5) — "FABLE"

Both reviewed the same commit; findings were de-duplicated and ranked.
This file is the consolidated **fix list for the integration branch**
`integration/consolidated-review-fixes` (branched from `main` @ b17eed0).

Selected for implementation (user decision 2026-10-06):

- Tier 1 — task state machine (F01–F05)
- Tier 2 — boot / lifecycle / memory (F06–F13)
- Tier 3 — security (F14–F17)
- Tier 5 — gate & tests (F18–F19)
- Tier 6 — skill (F20, F21)
- Tier 7 — installer / ops robustness + smaller defects (F22–F26)

Excluded (bench tiers 4 and 8): bench timing/rubric/audit defects
(Fable B1–B14, Sol F11) and docs (Fable D1). The bench stays as-is; its
numbers are treated as **unproven** until a later bench PR. The current
skill is frozen; a **new v2 skill is authored from scratch** (F20) rather
than patching the 953-line original.

Conventions:

- `path:line` references are against commit b17eed0 (the branch start).
- "CURRENT" excerpts are verbatim (elided with `...` where noted).
- "EXEMPLARY" code is illustrative, not a finished patch — implementers
  keep the project's atomic-write, closed-exit-code, and per-task-lock
  conventions, and add a regression test per fix (the test must fail
  before the fix and pass after).
- Spec deltas: SPEC is retired to `archive/SPEC.md` (see §7). Record every
  spec-delta note in the commit/PR body, per the AGENTS.md
  spec-amendment rule.

---

## 1. Task state machine (tier 1)

### F01 — Count `engine_retried` in the slot charge; check capacity on retry

**Problem.** `CHARGED_STATES` omits `engine_retried`, so a task that was
retried onto a second instance stops counting against both slot counters.
Routing admits more completions than the engine has slots;
`bonsai2-status` can show 0 slots in use while the task is running; and
the skill's "all instances at 0 slots → `unload --all`" rule kills a live
task. The retry picker also never checks the target's occupied slots.

**CURRENT — `bin/bonsai2_harness.py:250`:**

```python
TERMINAL_STATES = ("done", "failed", "timeout")
CHARGED_STATES = ("queued", "running")  # S3.4 whole-lifetime slot charge
ROUTABLE_STATES = ("healthy", "degraded")
```

`slots_in_use` (:635–642) and `_instance_loads` (:655–670) both filter on
`CHARGED_STATES`; `running_count` (:645–652) counts `state == "running"`
only.

**CURRENT — `bin/bonsai2-taskworker.py:226–263` (`_pick_retry_instance`):**
picks `min(fresh, key=lambda e: e.get("i", 0))` from healthy
untried-with-ctx-fit instances; never reads the target's occupied slots.

**EXEMPLARY — harness:**

```python
# `engine_retried` is a LIVE charge: the worker holds a slot on the new
# instance for the rest of the task's lifetime (S3.4). It must count in
# BOTH the whole-lifetime slot charge and the engine's concurrent charge —
# the engine is actually decoding on that instance.
CHARGED_STATES = ("queued", "running", "engine_retried")

def running_count(d: dict, i: int) -> int:
    return sum(
        1
        for r in iter_tasks(d)
        if r.get("instance") == i
        and r.get("state") in ("running", "engine_retried")
    )
```

(and `_instance_loads` counts `entry[1]` for both live states).

**EXEMPLARY — worker `_pick_retry_instance`:** after choosing the target,
re-read the charge from the harness (pass `d` through or read task
records) and refuse a target where
`H.slots_in_use(d, i) >= doc_total_slots` unless it is the only
untried instance with ctx fit — in which case proceed but log
`WARN_RETRY_OVERSUBSCRIBED`. Keep the "one retry, next instance" rule.

**Test.** Seed 2 instances × 8 slots; 7 tasks `running` on instance 0;
force an engine error on the 8th task (instance 0) → retry lands on
instance 1; assert `slots_in_use(0) == 8` while the task is
`engine_retried` on instance 1, and that a 9th spawn queues instead of
starting on instance 0. Two simultaneous failures racing for the last
free slot on the same instance: exactly one is accepted.

---

### F02 — Context admission must use the effective PER-SLOT limit

**Problem.** Admission compares `ctx_need` against the instance's whole
`ctx` (262,144). The pinned engine splits that context across `--parallel`
sequences: 262,144 / 8 = 32,768 tokens per slot. A 65,536-token request
is accepted today and dies at generation (or silently truncates). The
skill already tells the main agent the effective limit is 32,768 — code
and instructions disagree.

**CURRENT — `bin/bonsai2_harness.py:812` (route_target) and `:959–963`
(claim_slot):**

```python
    fit = [e for e in instances if ctx_need is None or (_inst_ctx(e) or 0) >= ctx_need]
...
                    r.get("ctx_need") is None
                    or (doc_ctx.get(i) or 0) >= r.get("ctx_need")
```

**EXEMPLARY — add one helper, use it in both sites:**

```python
def effective_slot_ctx(e: dict) -> int:
    """The engine divides the configured context across the parallel
    sequences (verified in the pinned llama-context.cpp): one task may
    use at most ctx // parallel tokens before truncation. Routing must
    gate on THIS number, not the whole-instance ctx."""
    ctx = int(e.get("ctx") or 0)
    par = int(e.get("slots_total") or 0) or 1
    return ctx // par

# route_target:
    fit = [e for e in instances
           if ctx_need is None or effective_slot_ctx(e) >= ctx_need]
# claim_slot eligibility:
            eligible = [
                r for r in by_inst.get(i, [])
                if (
                    r.get("ctx_need") is None
                    or effective_slot_ctx(e_by_i) >= r.get("ctx_need")
                )
            ]
```

Also: publish `ctx_slot_effective` in `health.json` per instance (manager
computes it from its live ctx value, so drift-halved ctxs report the
correct effective number) and have `spawn` echo it in the JSON so the
main agent sees the real limit.

**Test.** Instance ctx=262144, slots=8: `--ctx 32768` routes,
`--ctx 32769` exits 5 with a detail naming `ctx_slot_effective=32768`.
A claim of a queued task with ctx_need=65536 stays queued.

---

### F03 — Queued tasks must have a dispatcher that outlives the spawn process

**Problem.** `claim_slot` is only ever called from the queue-wait loop
inside `cmd_spawn` (`:2281` — the single caller). When that spawn exits
8 (queue_wait budget), the task stays `queued` and charged with nothing
left to promote it. `bonsai2-collect` returns 0 for it indefinitely, and
a re-spawn of the same prompt hits the dedup path (`:2184–2191`) and
returns the stuck task with exit 0.

**CURRENT — `bin/bonsai2_harness.py:2293–2298` (spawn exit-8 tail):**

```python
        if time.monotonic() >= wait_deadline:
            fresh["detail"] = (
                f"queue wait ({cfg['queue_wait_max_s']} s) expired — no free slot on i={instance_i}"
            )
            write_task(d, fresh)
            return emit(spawn_json(fresh), EXIT_QUEUE_WAIT, fresh["detail"])
```

The record stays `state: queued`.

**FIX (three parts).**

1. **Manager-owned dispatch.** `bin/bonsai2-manager` imports the harness
   module and runs, per probe cycle (right next to the existing reaper
   pass at `:2596–2601`):

```python
        # S3.3 (review F03): the manager owns queue progress — a queued
        # task whose spawn process has exited must still be promoted the
        # moment a slot frees. claim_slot is idempotent and locks
        # slot-claim itself.
        try:
            promoted = H.claim_slot_once(health_dir)
            if promoted:
                log(f"S3.3 dispatcher: promoted {promoted['task_id']} "
                    f"to running on i={promoted.get('instance')}")
        except Exception as exc:  # noqa: BLE001
            log(f"S3.3 dispatcher error (cycle {cycle}): {exc!r}")
```

   Add `claim_slot_once(health_dir)` in the harness: load the health doc,
   run `claim_slot`, and `start_worker` on the claimed record (the
   manager is the only process that may start workers for promoted
   records; workers are `start_new_session` so they survive manager
   restarts).

2. **Dedup path promotes.** In `cmd_spawn`'s `dup_winner` branch
   (`:2184–2191`): if the dup winner is `queued`, run the same claim pass
   once before returning; if it was promoted, `start_worker` and report
   `running`. If still queued, return exit 0 with detail
   `"dedup: duplicate of QUEUED task … — manager will dispatch on slot free"`.

3. **Queue expiry is terminal.** A queued task whose spawn exited 8 must
   not sit charged forever: record `queue_wait_deadline_ts` on the record
   at spawn time. The manager dispatcher (part 1) marks a queued record
   whose `created_ts` + `queue_wait_max_s` (from harness.yaml) + a 30 s
   margin has passed `failed` / `ERR_QUEUE_EXPIRED` — UNLESS a claim
   just promoted it. (The task's own `--deadline` stays the worker's
   clock; this is the spawn-side budget, S3.3.)

**Test.** Spawn 2 tasks; the first is running, the second queues; the
second spawn's wait loop exits 8 (mock clock). Manager dispatcher cycle
→ second task promotes on the next free slot WITHOUT any spawn process
alive. Re-spawn the same prompt while it is queued → returns the same
task id, exit 0, and does not double-charge. After queue expiry margin →
record is `failed`/`ERR_QUEUE_EXPIRED`, slot freed.

---

### F04 — Reaper must not overwrite a record that raced it terminal

**Problem.** `reap_stale` takes a snapshot of every record, checks PIDs
later, and writes its snapshot back with no re-read. If a worker writes
`done` and exits after the snapshot but before the PID check, the reaper
writes `failed`/`ERR_ENGINE_DOWN` on top of a finished task; collect then
returns 21 with the answer still in the payload. The same write path
also rewrites ctx-too-long errors, cancellations and timeouts as
`ERR_ENGINE_DOWN` on the pid-dead rule (stale local state).

**CURRENT — `bin/bonsai2_harness.py:1145–1169` (reap_stale core):**

```python
    for rec in iter_tasks(d):
        if rec.get("state") not in REAPABLE_STATES:
            continue
        pid = rec.get("worker_pid")
        ...
        if not pid_gone and not deadline_gone:
            continue
        prior = rec.get("state")
        rec["state"] = "timeout" if deadline_gone else "failed"
        rec["error"] = "ERR_DEADLINE" if deadline_gone else "ERR_ENGINE_DOWN"
        ...
```

`rec` here is the snapshot from the initial `iter_tasks` pass; no lock
and no re-read before the write at the bottom of the function.

**EXEMPLARY — per-record re-read under the per-task lock, terminal
monotonicity:**

```python
    for rec in iter_tasks(d):
        tid = rec.get("task_id")
        ...  # same pid_gone / deadline_gone pre-filter on the snapshot
        with lock_wait(d["base"], f"task-{tid}"):
            fresh = read_task(d, tid)
            if fresh.get("state") not in REAPABLE_STATES:
                continue  # raced terminal (done/failed/timeout/ERR_CANCELLED)
            pid = fresh.get("worker_pid")
            # recompute pid_gone / deadline_gone from `fresh` (the
            # deadline uses fresh's running_ts/deadline_s)
            ...
            fresh["state"] = "timeout" if deadline_gone else "failed"
            ...
            write_task(d, fresh)
```

Also add a module-level transition table and enforce it at every state
write site (worker `_set_state`, reaper, cancel, claim, dedup):

```python
# Terminal states are terminal. A write that would move a terminal
# record back to a non-terminal state is a no-op (log WARN_STATE_REGRESS).
TERMINAL_STATES = ("done", "failed", "timeout")
```

**Test.** Mock: worker writes `done` at T, dies at T+ε; reaper snapshot
at T−ε. After the pass, record is `done`, not `failed`. Repeat with
`timeout` and `failed` written by the worker — the reaper keeps them
verbatim (including their original `error`), never `ERR_ENGINE_DOWN`.

---

### F05 — Cancel must be compare-and-set; a cancelled task can never restart

**Problem.** `claim_slot` and `start_worker` run under the global
`slot-claim` lock and write back their own copy of the record. A cancel
of a `queued` task that lands while a claim is in flight gets clobbered:
the claim writes `running`, the worker starts, and the cancel already
reported success.

**CURRENT — `bin/bonsai2_harness.py:974–981` (claim_slot write-back):**

```python
            head["state"] = "running"
            head.setdefault("running_ts", time.time())
            head["updated_ts"] = time.time()
            write_task(d, head)
            return head
```

(no per-task lock; `head` is the claim's own read from `iter_tasks`).

**EXEMPLARY — CAS under the per-task lock at both sites:**

```python
            with lock_wait(d["base"], f"task-{head['task_id']}"):
                fresh = read_task(d, head["task_id"])
                if fresh.get("state") != "queued":
                    return None  # cancelled / reaped mid-claim; another
                                 # task (or none) will be claimed next pass
                fresh["state"] = "running"
                fresh.setdefault("running_ts", time.time())
                fresh["updated_ts"] = time.time()
                write_task(d, fresh)
                return fresh
```

Apply the same CAS shape to `start_worker`'s record write (:1002–1004)
and to the worker's own state writes (`_set_state` in
`bin/bonsai2-taskworker.py`): re-read under `task-<id>` lock, refuse the
write if the on-disk state is terminal and the intended state is not
the same terminal, merge the `steer_messages` field (as
`_persist_rec_preserving` already does).

**Test.** Two threads: thread A claims the only free slot for task X
(queued); thread B cancels X in the gap. Exactly one outcome: X is
`running` with a live worker AND the cancel returned exit 0 with
`"cancelled …"` only after the worker was signalled, OR the claim saw
`failed`/`ERR_CANCELLED` and promoted a different task. No path where X
is `running` with a worker and the cancel reported "cancelled queued
task" with no worker to signal.

---

## 2. Boot / lifecycle / memory (tier 2)

### F06 — Cold-start ordering: instances must not wait for the manager's READY

**Problem.** Instance units carry `After=bonsai2-sglang.service`
(installer template), and the manager is `Type=notify`, sending
`READY=1` only AFTER `spawn_sequence` returns. On a cold boot systemd
holds every instance start job until the manager's start job completes;
the manager polls ports nothing listens on until its boot deadline, marks
instances degraded, sends READY — then both start jobs run. The second
instance's memory check ran while the first had allocated nothing, so a
tight pool gets two instances granted and the kernel OOM-killer decides.
Runs 1–2 hit this: run 2's "measured boot" was 180.1 s = the deadline.

**CURRENT — `bin/bonsai2_install.py:680–686` (unit template):**

```python
_UNIT_SERVICE_TMPL = (
    "[Unit]\n"
    "Description=bonsai2 subagent instance %i (llama-server container)\n"
    "Requires=docker.service\n"
    "After=docker.service bonsai2-sglang.service\n"
    ...
```

**CURRENT — `bin/bonsai2-manager:2892–2894` (post-spawn READY):**

```python
    # systemd Type=notify: signal readiness, then run the probe loop forever.
    notify_sd("READY=1")
    run_probe_loop(cfg, runtime, health_dir, 0.0, log=_log)
```

`--no-block` (`systemctl_start_instance`, :941–966) removes the
in-manager deadlock but not the systemd ordering: the instance jobs still
wait for the manager's start job to COMPLETE.

**FIX (take the simple path — remove the reverse dependency; the manager
controls instance start itself and re-checks main health before spawning):**

```python
    "After=docker.service\n"
```

The manager already gates its own spawn on main-stack health
(`main_healthy`, the boot sequence), and instances only start when the
manager's `spawn_sequence`/control path tells them to — the `After=` was
belt-and-braces that turned out to be the deadlock itself. Keep
`Requires=docker.service`.

**Verification (this box, before merge):** cold-start integration check —
`systemctl stop` all instances + the manager, `systemctl start
bonsai2-sglang`, time to first healthy instance < boot_target_s (not
≈180 s), and the second instance's allocation-tier log line shows the
pool WITH the first instance's footprint subtracted. Add it as a
`@gpu` test (the hermetic mocks cannot see systemd ordering).

**Test.** Hermetic: fake systemd with ordering semantics asserts the
instance start job is no longer gated on manager READY; live: the
cold-start check above.

---

### F07 — Unit rewrites must reach systemd (daemon-reload) and the effective value must be verified

**Problem.** The drift watch and the quant fallback rewrite `--ctx-size`
/ `--model` in the unit file, then `systemctl restart`. Nothing runs
`systemctl daemon-reload`, so systemd restarts from its cached (old)
definition while health.json advertises the new value. Each recheck then
halves the context again (down to 8192) and the next reboot comes up at
that size.

**CURRENT — `bin/bonsai2-manager:1097–1135` (`rewrite_unit_ctx`):** ends
with the atomic write + log; the caller (`check_drift`,
`:1802–1818`) then restarts the unit without a reload. Same shape in
`rewrite_unit_model` (:1138–…).

**EXEMPLARY — make the rewrite atomic-with-reload and verify:**

```python
def rewrite_unit_ctx(unit_path: Path, new_ctx: int, current_ctx: int,
                     base: list[str] | None = None, *, log=_log) -> bool:
    ...  # (existing surgical rewrite, unchanged)
    # F07: a file on disk is not a unit definition — systemd keeps the
    # last daemon-reloaded copy in memory. Reload BEFORE the restart, or
    # the restart re-executes the old --ctx-size.
    r = subprocess.run((base or systemctl_base()) + ["daemon-reload"],
                       capture_output=True, text=True, check=False)
    if r.returncode != 0:
        log(f"drift watch: daemon-reload rc={r.returncode} ({r.stderr.strip()[-200:]})")
        return False
    return True
```

The caller must only restart the unit when `rewrite_unit_ctx` returned
True, and afterwards must verify the EFFECTIVE value (query the engine —
the fork exposes the live context on the server endpoint, or read the
unit with `systemctl show -p ExecStart` after reload) before marking
`ctx_reduced` in health.json. `rewrite_unit_model` gets the same reload.

**Test.** Hermetic: the restart seam records the sequence; assert
`daemon-reload` is called between the file write and `systemctl restart`
and that a failing reload defers the restart. Live: one drift-halve
cycle; `systemctl cat bonsai2-server@0` shows the new `--ctx-size`.

---

### F08 — Memory admission must not trust the main cgroup; unified byte units

**Problem.** Three interlocking defects:

1. `main_used_gb` prefers the main container's cgroup
   `memory.current` and the code calls that "sleep-accurate" — but the
   repo's own MAP 1.7 note says "do NOT meter via cgroup": on GB10 the
   CUDA allocations are outside cgroup accounting, so an awake main stack
   holding ~70 GB shows 11–14 GB, and the planner almost always grants
   every instance. A small cgroup reading cannot establish that main is
   asleep or that its pool is free.
2. The manager converts /proc/meminfo KiB by `/1e6` and calls it "decimal
   GB" while the installer's `compute_n` uses `1024**3` (GiB). The same
   128 GB input yields 2 instances at install time and 3 at manager boot.
3. The OS reserve (8 GB) is subtracted at planning time but nothing
   re-reserves it after allocations; the September bench recorded
   MemAvailable ≈ 2.43 GiB during a run — no OOM, but no guaranteed
   8 GB floor.

**CURRENT — `bin/bonsai2-manager:499–531` (`main_used_gb`):**

```python
    cid = _main_container_id(container)
    if cid:
        ...
                try:
                    return int(cand.read_text().strip()) / 1e9, "live cgroup"
```

**CURRENT — `bin/bonsai2-manager:419–421`:**

```python
    for line in Path("/proc/meminfo").read_text().splitlines():
        if line.startswith("MemAvailable:"):
            return int(line.split()[1]) / 1e6  # kB -> decimal GB
```

**FIX.**

- Byte-based internal accounting everywhere: read `/proc/meminfo` fields
  in kB, convert with `/1024/1024` to MiB, keep as int bytes; label units
  at presentation only. `bin/bonsai2_install.py` `compute_n` and the
  manager's planner must use the SAME formula (share it: move it into
  `bin/bonsai2_vram.py` and import from both).
- `main_used_gb` → `main_occupied_bytes`: on this unified-memory box use
  the MAX of (a) the recorded awake footprint (from
  `serving.yaml` `main_stack.footprint_gb`), and (b) a live probe. The
  live probe stays the cgroup read BUT is now a LOWER bound sanity value,
  never a "main is asleep" signal: if cgroup < 50 % of the recorded
  footprint, log `WARN_MAIN_MIGHT_BE_SLEEPING (cgroup {n} GB << recorded
  {m} GB — cgroup misses CUDA, MAP 1.7)` and still charge the recorded
  footprint against the pool (reserving main's wake-up growth), unless a
  main-healthy probe returns a sleep signal (the main repo's lifecycle
  exposes one; consume it, don't guess). This is conservative: it never
  under-reserves main.
- Global low-water guard: the allocation tier's free read already uses
  MemAvailable — add `LOW_WATER_MB` (default 8192, config key in
  `serving.yaml`): if free < low-water + instance need, deny the start
  (WARN_VRAM_SHORTFALL with the low-water detail) instead of granting.
  The drift watch keeps its existing per-container cap; this is the
  machine-level floor.

**Test.** Hermetic: planner gets total=121.6 GiB, main cgroup=12 GB,
recorded footprint=76.4 GB, reserve=8 GB → N computed against
121.6 − 76.4 − 8, NOT 121.6 − 12 − 8. Unit test: install-side and
manager-side N agree on a 128 GB input. Low-water: free = need + 4 GB
with low-water 8 GB → denied.

---

### F09 — `/run/bonsai2` ownership + boot recreation + enable the manager

**Problem.** The installer creates `results/dedup/bench/workspaces/control`
under `/run/bonsai2` as root and chowns only `results/` + `workspaces/`
to the invoking user; user-run `bonsai2-unload`/`bonsai2-reload` fail with
an uncaught PermissionError on `control/`, and dedup writes fail silently.
`/run` is tmpfs: after a reboot only the manager recreates the dir, as
root, with no subdirs — spawn/status fail until the installer runs again.
Runs 1–2 hit both; the operator's `sudo chown -R /run/bonsai2`
workaround is also what makes F15 (the root-ack symlink) exploitable.
The installer also never runs `systemctl enable`, so the pool does not
come up on boot even though the unit has `WantedBy=multi-user.target`.

**CURRENT — `bin/bonsai2_install.py:1446–1479` (`_make_dirs`):**

```python
def _make_dirs(health_dir: Path, invoke_uid: int | None = None) -> None:
    for sub in ("results", "dedup", "bench", "workspaces", "control"):
        (health_dir / sub).mkdir(parents=True, exist_ok=True)
    ...
        for sub in ("results", "workspaces"):
            ...
                    os.chown(p, invoke_uid, -1)
```

**FIX.**

1. Create the tree via a tmpfiles.d fragment instead of at install time:
   install writes `/etc/tmpfiles.d/bonsai2.conf`:

```
D /run/bonsai2              0755 root {root,blckwll} -
D /run/bonsai2/results      0755 blckwll - -
D /run/bonsai2/workspaces   0755 blckwll - -
D /run/bonsai2/dedup        0755 root blckwll - -
D /run/bonsai2/bench        0755 root blckwll - -
D /run/bonsai2/control      0755 root blckwll - -
```

   (group = the invoking user, so user commands can write their request
   files into `control/` and dedup; the manager runs as root and can do
   everything). Keep `_make_dirs` as the runtime fallback (the manager
   calls it at boot so a fresh reboot works even if tmpfiles hasn't run
   yet), but with group ownership on dedup/bench/control.
2. The manager's boot path creates the same tree (group 0755/0775 as
   above) — today it only recreates the base dir.
3. Installer step 7: `systemctl enable bonsai2-sglang.service` (and the
   instance templates' `WantedBy` already covers instances, but the
   manager is the one that spawns them — enable it). Verify with
   `systemctl is-enabled` in the install report.

**Test.** Hermetic: `_make_dirs` output checked with mocked `os.stat` —
dedup/bench/control carry the invoking group. Installer seams: `enable`
seam is called with the manager unit. Live (pre-merge, this box):
reboot → `systemctl is-enabled bonsai2-sglang` = enabled; user-run
`bonsai2-status` works without any chown.

---

### F10 — A fully busy instance must not fail its health probe (and get restarted with its tasks)

**Problem.** Healthy = `/v1/models` 200 + a 16-token completion round-trip
< 10 s. Under full load (8/8 slots busy) llama-server holds the probe's
completion until a slot frees, so the RT gate times out. After 5 failed
cycles the manager `systemctl restart`s the instance and kills every
in-flight completion; their retries fill the other instance, which then
fails the same way. The M7 saturation test and the skill's fan-out
advice both create exactly this load.

**CURRENT — `bin/bonsai2-manager:855–868` (completion RT gate) and
`:871–879` (`instance_healthy` = models 200 AND completion RT):**

```python
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            parsed = json.loads(resp.read())
    except (urllib.error.URLError, json.JSONDecodeError, OSError, TimeoutError):
        return False
    elapsed = time.monotonic() - t0
    if elapsed >= COMPLETION_RT_MAX_S:
        return False
```

**FIX.** Split the probe into two half-states with different meanings:

- **dead:** `/v1/models` fails (or returns the wrong alias). Counts as a
  probe failure (the 2-fail → degraded / 5-fail → restart escalation
  keeps working for real outages).
- **busy:** `/v1/models` is 200 but the completion RT exceeds the gate.
  The engine is up and simply queueing the probe behind real work — that
  is not "alive-but-stuck", it is "healthy and fully loaded". Mark the
  entry `state: busy` (a NEW health state; the harness's
  `ROUTABLE_STATES` gains `"busy"` so routing/claim can still use it,
  but a busy instance's slots are all in use anyway). `busy` does NOT
  accumulate probe fails; a `busy` entry that returns to a fast RT is
  simply `healthy` again.

```python
def instance_healthy(...) -> str:
    """'dead' | 'busy' | 'healthy' — see F10: a full-slot engine that
    still answers /v1/models is alive, not stuck."""
```

`check_escalation` only escalates on `dead`. A `busy` entry is left to
the drift watch and the harness's slot accounting (which already sees
the slots in use — the point of F01).

**Test.** Hermetic: probe returns models-200 + slow completion → state
`busy`, fails counter unchanged, no restart after 5 cycles. models-404 →
`dead`, escalation proceeds. A busy instance with 8 charged slots still
routes new tasks to the other instance (routing uses `slots_in_use`
from F01, not probe state).

---

### F11 — Unload must not acknowledge success while an instance is still up

**Problem.** The unload branch writes the ack with `state="ok"` even when
`systemctl stop` fails for some targets, and marks an instance
`bunloaded` even when `wait_down` reports False. Main then believes the
pool is down and starts expensive reasoning while workers are resident.

**CURRENT — `bin/bonsai2-manager:1945–1985` (unload branch):**

```python
                rc = stop(i, base)
                if rc != 0:
                    log(f"S3.2 unload: i={i} systemctl stop rc={rc} — keeping state={e.state} ...")
                    continue
                down = wait_down(_port(runtime.cfg, i))
                e.state = "bunloaded"
                e.fails = 0
                ...
                done.append(i)
...
            ack.update(unloaded=sorted(done), state="ok")
```

Note: the `rc != 0` path `continue`s (keeps state) but the ACK still says
`state="ok"` with the partial `done` list; and `wait_down == False` still
marks `bunloaded`.

**FIX.**

```python
            ack = {"type": "unload", "request_id": req_id,
                   "unloaded": sorted(done), "unresolved": []}
            ...
                rc = stop(i, base)
                if rc != 0:
                    ack["unresolved"].append({"i": i, "reason": f"stop rc={rc}"})
                    continue
                down = wait_down(_port(runtime.cfg, i))
                if not down:
                    # stop returned 0 but the listener did not close in
                    # the bounded window — the container is probably
                    # still draining. NOT unloaded: retry next pass.
                    e.fails += 1
                    ack["unresolved"].append({"i": i, "reason": "port still up after stop"})
                    continue
                e.state = "bunloaded"
                ...
            ack["state"] = "ok" if not ack["unresolved"] else "partial"
```

The command side (`cmd_unload`, harness :2795+) must surface
`state: partial` in its JSON (`"unresolved": [...]`) instead of treating
any ack as complete — main's phase order (unload → verify → heavy main
work) then has a machine-readable "release verified" gate: `state == "ok"`
AND `main_stack` memory recovered (the main repo's preflight principle).

**Test.** Hermetic: `stop` seam returns 1 for instance 1 of 2 → ack
`state: partial`, `unresolved[0].i == 1`, instance 0 unloaded; the
instance-1 entry keeps its prior state and stays in `runtime.spawned`.
`wait_down` False → same. Command JSON shows the unresolved list.

---

### F12 — Deadlines must stop running tools; cancellation must kill the tool's process group

**Problem.** Three layers:

- `_param_timeout` accepts any positive model-provided `timeout`
  (verified: `bin/bonsai2_tools.py:517–525` returns `float(t)`
  unclamped), so a 3600 s tool can outlive the task's deadline.
- The worker checks the task deadline only at the STEP boundary
  (`bin/bonsai2-taskworker.py:1120–1137`); a tool that starts just before
  the deadline runs to its own (unclamped) timeout, and the reaper then
  frees the slot while the tool still runs.
- Tools run in their own session (`start_new_session=True`), so the
  cancel path's process-group signals (harness `:2566–2577`) target the
  WORKER's group; the tool's group escapes. The worker's SIGTERM handler
  does have `_CURRENT_PROC` machinery (`bin/bonsai2_tools.py:139–143`)
  but only if the worker's handler actually fires before the slot is
  freed.

**CURRENT — `bin/bonsai2_tools.py:517–525`:**

```python
def _param_timeout(params: dict, ctx: dict) -> float:
    t = params.get("timeout")
    if isinstance(t, (int, float)) and not isinstance(t, bool) and t > 0:
        return float(t)
    return float(ctx.get("timeout_s", 60.0))
```

**FIX.**

1. The worker computes, before each tool execution,
   `remaining = deadline_at - time.monotonic()` and puts it in the tool
   ctx: `ctx["deadline_remaining_s"] = max(1.0, remaining)` (the tool
   ctx is built per step — `make_ctx` already takes `timeout_s`; add the
   key).
2. `_param_timeout` clamps:

```python
def _param_timeout(params: dict, ctx: dict) -> float:
    """F12: the effective tool timeout is min(model request, configured
    tool_timeout_s, remaining task deadline) — a tool may never outlive
    the task it belongs to (S3.4)."""
    t = params.get("timeout")
    requested = float(t) if isinstance(t, (int, float)) \
        and not isinstance(t, bool) and t > 0 \
        else float(ctx.get("timeout_s", 60.0))
    cap = float(ctx.get("timeout_s", 60.0))
    remaining = ctx.get("deadline_remaining_s")
    return max(1.0, min(requested, cap, float(remaining) if remaining else requested))
```

3. The worker's SIGTERM/SIGALRM handlers already call
   `T._kill_current_process_group()` (the `_CURRENT_PROC` seam); keep
   it, and make the reaper's deadline path ALSO issue the engine control
   POST (it does, F11-orphan-cancel, `:1211–1228`) plus a best-effort
   `pkill` on the worker's recorded pid — belt-and-braces for a tool
   group that outlived the worker.
4. Document the trust boundary honestly in the v2 skill: tools run with
   the operator's host rights; the deadline clamp is the guarantee, not
   isolation (isolation is F14).

**Test.** `{"cmd": "sleep 100000", "timeout": 100000}` with
`deadline_remaining_s=5` → the tool is capped at 5 s (timed_out=True,
exit 124 convention). Cancel a task mid-tool: the tool's process group
receives SIGKILL within the grace window (assert the group leader is
dead). A task whose deadline passes while a tool runs: reaper frees the
slot AND the engine control POST fires (mock port: expect the request).

---

### F13 — Token-capped output must not be reported as a complete answer

**Problem.** The tool loop treats `finish_reason == "length"` as a
failure only when the answer is blank
(`bin/bonsai2-taskworker.py:1411–1479` — the F2 guard: a nonempty answer
is trusted as a final answer). The tools-off completion path
(`:1846–1875`) accepts ANY ok response and marks `done` without checking
the finish reason. A 32,768-slot task that emits a partial answer then
hits the cap is collected as exit 20 / done / `partial=false` — main
integrates a truncated artifact.

**CURRENT — `bin/bonsai2-taskworker.py:1846–1874` (tools-off done):**

```python
        if res["ok"]:
            try:
                ...
            _set_state(rec, "done", None, None, d)
            _log(f"{rec['task_id']}: done (port {port})")
            return 0
```

`res["finish_reason"]` is available (the stream captures it, :300) but
unused here.

**FIX.**

- Tools-off path:

```python
        if res["ok"]:
            capped = res.get("finish_reason") == "length"
            ...  # (write the result file as today, plus:
            result_payload["partial"] = capped
            ...
            if capped:
                # A cap-cut answer is PARTIAL: the state stays `done`
                # (the engine did what it was told), but the record must
                # carry partial=true so collect and main's acceptance
                # step can distinguish it from a complete answer.
                _set_state(rec, "done", None,
                           "finish_reason=length — answer is capped, "
                           "treat as partial", d)
            else:
                _set_state(rec, "done", None, None, d)
```

- Tool-loop path: the F2 empty-answer guard stays; add a nonempty
  `length` branch that marks the record `partial: true` on the result
  file before `_set_state(done)` (a nonempty capped answer is a
  legitimate partial artifact — salvageable, not a failure).
- Harness `_collect_json` / `spawn_json`: surface `partial` (from the
  result file when terminal) in the collect payload; the closed exit
  code stays 20 for done+partial (acceptance is main's job — task
  execution success ≠ artifact acceptance, per the reviews).

**Test.** Mock engine: tools-off task, stream ends with
finish_reason="length", content nonempty → record `done`, result file
`partial: true`, collect JSON carries `partial: true`, exit 20. Empty
capped answer (existing F2 path): unchanged (nudge, then
ERR_TRUNCATED_NO_PROGRESS on the 2nd).

---

## 3. Security (tier 3)

### F14 — bash/python tools run with operator rights: state the boundary, scrub the env, clamp timeouts

**Problem.** The bash tool runs `/bin/sh -c <cmd>` and the python tool
runs the interpreter; the only restriction is `cwd`
(`bin/bonsai2_tools.py:392–433`). Nothing scrubs the environment, blocks
the network, or switches user; the worker inherits the caller's
environment. The current skill claims `--workdir` confines the tools —
false. A subagent reviewing a diff reads untrusted text; one injected
line in a reviewed file can make it `curl` `~/.ssh` out or write outside
the workdir. (SPEC S3.4 itself admits the host-side tools share "the
same trust domain as the main agent's own bash" — the skill's claim was
the overreach, but the env inheritance is still a real hole: `PYTHONPATH`,
`LD_PRELOAD`, `HTTP_PROXY`, shell init files.)

**CURRENT — `bin/bonsai2_tools.py:324–331` (`_run_capped` Popen):**

```python
    proc = subprocess.Popen(
        argv,
        cwd=str(cwd),
        stdin=subprocess.PIPE if input_bytes is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
```

No `env=` — full inheritance.

**FIX (pragmatic first step — isolation is a follow-up, see §7):**

1. Build a scrubbed env in `make_ctx` (host-side only):

```python
    # F14: the tool env is minimal — the model's commands must not see
    # operator shell state (PYTHONPATH/LD_PRELOAD/HTTP_PROXY/ssh-agent
    # sockets are the observed attack surface when a subagent reads
    # untrusted text). PATH stays (tools are found by name); HOME stays
    # (pip/cache); everything else is out.
    env = {
        "PATH": os.environ.get("PATH", "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"),
        "HOME": os.environ.get("HOME", "/root"),
        "LANG": os.environ.get("LANG", "C.UTF-8"),
        "TMPDIR": str(w / ".tmp"),
    }
    ctx["env"] = env
```

   and `_run_capped` passes `env=ctx.get("env")`. `TMPDIR` inside the
   workdir means scratch files are pruned by workdir rotation.
2. Disable shell init files for the bash tool: run
   `["/bin/sh", "--noeval", "-c", cmd]`? — no: `sh` (dash) has no
   `--noeval`; use `["/bin/sh", "-c", cmd]` with `env` scrubbed AND
   `SHELL=/bin/sh` in the env. (The real init-file vector is
   `~/.profile`/`~/.bashrc` read only by login/interactive shells —
   `sh -c` reads neither; the env scrub covers `ENV`/`BASH_ENV`.)
   Document the boundary: `bash` = non-interactive `sh -c`, no login,
   scrubbed env, cwd=workdir, process-group killed at timeout.
3. The v2 skill (F20) states the real boundary in one paragraph instead
   of claiming confinement.
4. **Follow-up (noted, separate PR):** bubblewrap/nsjail with
   workdir-only mounts + `--unshare-net` for the host-side tools. Until
   then the operator-risks are: a malicious subagent CAN read operator
   files (path check only applies to read_file/write_file, not to
   `bash cat ~/.ssh/id_ed25519`). The scrubbed env removes the
   passive-leak vectors; the active ones remain by design (trusted
   personal box).

**Test.** `bash` tool with `env {"PYTHONPATH": "/evil"}` pre-set in the
worker → the tool subprocess sees no `PYTHONPATH`. A `bash` call of
`env` prints only the allowlist vars. `python` tool: `print(os.environ)`
shows the same.

---

### F15 — Root manager: control-file acks must validate the request id and not follow symlinks

**Problem.** The manager runs as root. It reads every `*.json` in
`/run/bonsai2/control/`, takes `request_id` from the file UNCHECKED, and
writes `ack-<request_id>.json` through a `.tmp` file with
`Path.write_text` + `os.replace` — which follows symlinks. With the
`chown -R` workaround (pre-F09) any user process (including a subagent's
bash tool, F14) can plant `control/ack-x.tmp` as a symlink to any file
and drop a request with `request_id: x`; the root manager then overwrites
the symlink target with JSON.

**CURRENT — `bin/bonsai2-manager:2036–2047` (`_write_ack`):**

```python
def _write_ack(control_dir: Path, req_file: Path, ack: dict) -> None:
    req_id = ack.get("request_id") or req_file.stem
    ack_file = control_dir / f"ack-{req_id}.json"
    tmp = ack_file.with_suffix(".tmp")
    tmp.write_text(json.dumps(ack, indent=2) + "\n")
    os.replace(tmp, ack_file)
```

**FIX.**

```python
_REQ_ID_RE = re.compile(r"^[0-9a-f]{32}$")

def _validated_request_id(req_file: Path, parsed: dict) -> str:
    """F15: the request id is user-influenced input into a root-owned
    write path. Accept only the uuid4-hex shape the commands generate
    (harness _write_control_request, :2618) — anything else is an
    invalid request (the command's ack gets ERR_INVALID_REQUEST_ID)."""
    rid = parsed.get("request_id")
    if not isinstance(rid, str) or not _REQ_ID_RE.match(rid):
        raise ValueError(f"invalid request_id {rid!r}")
    return rid
```

- Validate at read time (before any write); on failure write
  `ack-<sanitized-stem>.json` with `{"state": "failed",
  "error": "ERR_INVALID_REQUEST_ID"}` where the sanitized stem is
  `sha256(req_file.name)[:16]` (never the user's string).
- Write the ack WITHOUT following symlinks: open the tmp with
  `os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o644)`, and
  re-check `os.path.islink(ack_file)` right before `os.replace`.
- The harness side already generates `uuid.uuid4().hex`
  (`:2618`) — keep that; this makes the contract explicit instead of
  accidental.

**Test.** Hermetic: request file with `request_id: "../../etc/passwd"`
→ no write outside control/ (mock fs: `ack-<16hex>.json` with the error
state, and `../` untouched). A pre-planted `ack-<valid-id>.json` symlink
to a sentinel file → after the pass the sentinel is unmodified, ack
written next to it.

---

### F16 — Engine-side tool execution: drop privileges and host networking

**Problem.** With `tool_execution: engine` (off by default) the worker
sends tool calls to the fork's `/tools` endpoint inside the instance
container; the container runs `--network host --ipc=host` with no
`--user`, the image sets no USER, and the worker applies no path checks
in this mode. A 20 s POST timeout leaves the command running.

**CURRENT — `bin/bonsai2_install.py:726–729` (the ExecStart the engine
tools inherit):**

```python
        "ExecStart=/usr/bin/docker run --rm --name bonsai2-server-%i "
        "--gpus all "
        f"--memory {budget_gb:g}g --memory-swap {budget_gb:g}g "
        "--shm-size 4g --network host --ipc=host "
```

**FIX.** The instance containers need host networking for the fixed
port scheme, so the fix is at the endpoint, not the container:

1. In `image/Dockerfile.runtime`: `USER bonsai2` (create the user; the
   baked model files world-readable at 0644, the llama-server binary
   owned by the user).
2. The worker's engine-tool path
   (`bin/bonsai2-taskworker.py:737–742`, `_execute_tool` engine branch):
   apply the SAME `resolve_path` containment check as the host-side
   read_file/write_file before posting (the engine receives a
   workdir-relative path; validate it host-side).
3. When the 20 s POST times out, the worker must mark the task
   `ERR_TOOL_TIMEOUT` (it does not today — the timeout is swallowed into
   a generic engine error and the loop continues).
4. Document: `tool_execution: engine` keeps `--network host` (port
   scheme); the engine user is unprivileged; path containment is
   host-checked. If either is unacceptable for your threat model, the
   mode stays off by default (it is).

**Test.** Hermetic: engine-tool call with `path: "../../etc/hosts"` →
rejected host-side before the POST. Dockerfile: `docker run --rm
<image> id` → uid = bonsai2 (live, pre-merge). Timeout: mock /tools
endpoint that sleeps 25 s → task record `failed`/`ERR_TOOL_TIMEOUT`.

---

### F17 — Explicit bind address + install-time API key

**Problem.** Every instance runs `--api-key bonsai2-local` from the
tracked `etc/subagent-engine.yaml:59` with no `--host` flag. Upstream
llama-server binds 127.0.0.1 by default (the fork is inferred to do the
same — submodule not checked out at review time; verify at first build).
With host networking the ports are reachable from the box only, but the
default is an inference and the key is static + committed.

**CURRENT — `bin/bonsai2_install.py:735`:**

```python
        f"--port {port} --api-key {api_key} "
```

**FIX.**

- `instance_exec_start`: add `--host 127.0.0.1` (explicit, not default):

```python
        f"--host 127.0.0.1 --port {port} --api-key {api_key} "
```

- Installer: generate the key at install time (`uuid.uuid4().hex`,
  32-hex — matches F15's request-id shape convention) and render it into
  `subagent-engine.yaml`; the committed template carries
  `api_key: __BONSAI2_GENERATED__` (a sentinel the installer replaces;
  if a re-run finds a non-sentinel value it keeps the existing key).
  The manager and harness read it from the YAML (they already do), so
  nothing else changes.

**Test.** Hermetic: rendered ExecStart contains `--host 127.0.0.1`; a
fresh install's `etc/subagent-engine.yaml` has a 32-hex api_key; a
re-run preserves it. Live: `ss -ltnp | grep 30101` binds 127.0.0.1.

---

## 4. Gate & tests (tier 5)

### F18 — The gate must run the hermetic test suite

**Problem.** `scripts/gate.sh` runs bash -n, shellcheck, ruff, a secret
scan, YAML parse, and the doctrine audit — never the 790-test hermetic
suite. A commit that breaks `cmd_spawn` still gets `[gate] PASS`, and PR
bodies record it as the gate result. Missing tools count as "skip"; YAML
errors only warn; `uvx ruff` is unpinned.

**CURRENT — `scripts/gate.sh:132–137`:**

```bash
if [ $FAIL -eq 0 ]; then
  echo "[gate] PASS"
else
  echo "[gate] FAIL"
fi
exit $FAIL
```

(no step 7; the suite is simply absent).

**FIX.** Add step 7, and tighten the softs:

```bash
# 7) hermetic test suite — THE gate (G2). Pinned interpreter: the project
#    venv's python if present, else python3. A missing interpreter is a
#    HARD failure from now on (baseline: the interpreter is on this box).
PY="$( [ -x .venv/bin/python ] && echo .venv/bin/python || echo python3 )"
echo "[gate] hermetic suite (python -m unittest discover)"
if HOME="$(mktemp -d)" "$PY" -m unittest discover -s tests -t . > /tmp/gate-tests.txt 2>&1; then
  echo "  ok    $(tail -1 /tmp/gate-tests.txt)"
else
  echo "  FAIL  hermetic suite:"; tail -25 /tmp/gate-tests.txt | sed 's/^/    /'
  FAIL=1
fi
```

- The `HOME=$(mktemp -d)` isolation is the review's recommended run
  shape (the suite must not touch the real `/run/bonsai2` or operator
  state).
- Pin ruff: replace `RUFF_CMD` detection with a versioned call
  (`uvx ruff@0.9.x` — pin the exact version observed passing today;
  record it in the PR body).
- YAML step: a parse failure on a tracked config that the code reads
  (`subagent-engine.yaml`, `serving.yaml`, `harness.yaml`) becomes a
  FAIL (the WARN-only stays for bench/docs yaml).
- The pre-commit hook (`.githooks/pre-commit`) calls the same step 7 on
  the staged subset — keep the hook fast by running only the test
  modules whose source files changed (map `bin/bonsai2_harness.py` →
  `tests/test_harness_*.py`, etc.; fall back to the full suite for
  `tests/` or `bench/` changes).

**Test.** Break a tracked test on a scratch branch → gate fails. Break
`cmd_spawn`'s happy path → gate fails. `uvx` absent + `ruff` absent →
gate still runs the suite and fails on the missing interpreter only if
python3 is also absent.

---

### F19 — The secret scan misses most secrets (pipefail + grep -q)

**Problem.** The scan ends in `grep -Eiq` inside a pipeline under
`set -uo pipefail`. On a large diff `grep -q` exits at the first match,
upstream stages die with SIGPIPE, pipefail gives status 141, so the
`if` treats a MATCH as no-match. The pattern also requires a QUOTED
16+ alphanumeric value: it never matches unquoted YAML values like the
repo's own `main_api_key:`, JSON keys, or `hf_`/`ghp_` tokens.

**CURRENT — `scripts/gate.sh:91–100`:**

```bash
echo "[gate] secret scan (diff)"
DIFF="$(git diff "origin/$BASE...HEAD" 2>/dev/null || git diff HEAD~1 2>/dev/null || true)"
if echo "$DIFF" | grep -nE '^\+' | grep -vE '^\+\+\+' \
   | grep -Eiq '(api[_-]?key|secret|password|token|bearer)\s*[:=]\s*["'\''][A-Za-z0-9]{16,}'; then
  echo "  WARN  possible secret literal in diff:"
  ...
  FAIL=1
fi
```

**FIX.** Capture the matches into a variable BEFORE testing, and widen
the pattern:

```bash
echo "[gate] secret scan (diff)"
DIFF_FILE="$(mktemp)"
git diff "origin/$BASE...HEAD" > "$DIFF_FILE" 2>/dev/null || git diff HEAD~1 > "$DIFF_FILE" 2>/dev/null || true
# Added lines only; the secret shape: a key-ish name, a separator, a 16+
# char alphanumeric value — QUOTED OR BARE (unquoted YAML/JSON values
# were invisible to the old pattern).
MATCHES="$(grep -E '^\+' "$DIFF_FILE" | grep -vE '^\+\+\+' \
  | grep -Ei '(api[_-]?key|secret|password|passwd|token|bearer|authorization)[[:space:]]*[:=][[:space:]]*"?[A-Za-z0-9_-]{16,}' \
  | grep -Eiv '([0-9a-f]{32}|[0-9a-f]{40}|[0-9a-f]{64})' || true)"
# (exclude lines that are obviously hashes/digests — the repo records
# sha256s; a value of exactly 32/40/64 hex is a digest, not a secret)
if [ -n "$MATCHES" ]; then
  echo "  FAIL  possible secret literal in diff:"
  echo "$MATCHES" | sed 's/^/    /'
  FAIL=1
fi
rm -f "$DIFF_FILE"
```

- The F17 generated-api-key sentinel (`__BONSAI2_GENERATED__`) and any
  committed example value must be added to the digest/exclude list or
  quoted in the YAML (the scan is a tripwire, not the gate — F18 makes
  the suite the gate; but a secret the scan can't see is a secret the
  PR body won't flag).
- Upgrade path (noted, separate PR): gitleaks with a repo config.

**Test.** Synthetic diff containing `main_api_key: sk-a3f9...` (unquoted,
16+ chars) → gate FAILs. Same diff with a quoted value → FAILs. Same
diff with only a sha256 digest → PASSes. (Run against a scratch branch
with the synthetic file.)

---

## 5. Skill (tier 6)

### F20 — Author `skills/bonsai2-harness/SKILL-v2.md` from scratch (do NOT patch the original)

**Problem.** `skills/bonsai2-harness/SKILL.md` is 953 lines / ~14–17 K
tokens loaded into main's context every session. ~25 % is dated incident
narrative. It contains 22 command/flag/exit-code mismatches against the
parsers (the worst: claims `--workdir` confines the tools; exit-8 advice
"re-spawn or raise priority" returns the stuck task or duplicates it;
exit-22 advice "cancel it" does nothing on a finished task and kills
nothing — collect is told to return 84 when it can't; `content`/
`reasoning_content` vs the actual `answer`/`reasoning`; a `--graceful`
flag the code ignores; R3/R6 point a `--tools off` task at files it
cannot open; the "stateful" branch contradicts SPEC's out-of-scope list;
the speed table labels an 8-stream total as single-task speed). Several
rules contradict each other (fan-out size; deadline "covers the whole
loop" vs "a spawn without a computed deadline is a guess"; prebuild
index/CSS while loaded vs no token-heavy main work while loaded).

**DIRECTIVE.** Write a NEW file `skills/bonsai2-harness/SKILL-v2.md`
(~120–160 lines) that:

- describes ONLY what the code (after F01–F19) actually supports —
  verified against `bin/bonsai2_harness.py`'s `COMMAND_EXIT_CODES`
  table, the real collect payload fields (`answer`, `reasoning`,
  `usage`, `tool_log`, `steps`, `workdir`, `partial`), and the real
  flags;
- keeps the one unambiguous batch workflow: plan/contracts on main while
  unloaded → spawn batch → poll via `status --tasks` / `collect` →
  `unload --all` and wait for the verified-release ack (F11) → main
  verifies after release;
- one task-contract template (task id, objective, allowed files,
  interface signatures, acceptance commands, budgets, result schema);
- the real trust boundary paragraph (F14: operator rights, scrubbed
  env, cwd=workdir, deadline-clamped tools — NOT "confined");
- exit-code table straight from `COMMAND_EXIT_CODES` (spawn {0,2,3,4,5,8,
  80,82,84}, collect {0,2,20,21,22}, etc.) with the post-F03 exit-8
  advice ("the manager dispatches on slot free; poll, don't re-spawn —
  re-spawn is dedup-safe now and returns the same task");
- cost figures labelled PROVISIONAL (bench unproven per §0): one table,
  no per-row bench citations (the doctrine_audit gate still runs on the
  v1 skill's claims.json — until the bench is re-run, v2's numbers are
  explicitly "to be re-anchored by the next live run");
- recipes: keep the 3 shapes the live runs actually proved
  (pre-seeded skeleton + specific modification; test batch with
  structured report; single-file code-gen), delete the rest until they
  are re-proven;
- the v2 file carries a header: `status: v2 — replaces SKILL.md (v1)
  from 2026-10-06; v1 is frozen, not deleted`.

Activation (which file the main model loads) is the user's call — see §7
step 2. The v1 skill + claims.json stay untouched on this branch.

**ACCEPTANCE.** Line count 120–160; every command/flag/exit-code in the
file is grep-verified against the code in the same PR; no
`[M#]`-citation table (or one, clearly marked provisional); the fresh
live task shape from run 3 (skeleton + modification) is reproducible
from the skill alone.

---

### F21 — `progress_report` must include terminal tasks (and the skill must not poll for disappearing rows)

**Problem.** `bin/bonsai2_harness.py:1375` (`progress_report`, used by
`status --tasks`) omits terminal tasks from its output. Run 3's poller
waited for terminal rows that disappeared instead. The proposed
`--include-terminal` flag from `fixes-third-practical-run.md` was never
implemented; the skill's advice ("poll `status --tasks`") points main at
a view that hides exactly the rows it needs.

**CURRENT — `bin/bonsai2_harness.py:1375` (signature; the body filters
to non-terminal states — see the function).**

**FIX.**

- `progress_report(d, task_ids=None, include_terminal=False)` — the
  filter becomes state-aware; default keeps the current behavior for
  back-compat, and `cmd_status` wires `--tasks --include-terminal` to it.
- The v2 skill (F20) mandates: poll with `--include-terminal`, stop
  polling when every tracked task id is terminal, then unload.
- (The reviews' larger proposal — a single deterministic
  `bonsai2-batch` wait-and-release command — is recorded as a
  FOLLOW-UP in §7 step 3, deliberately not in this branch: it depends on
  F03/F11/F13 landing first and on the manager owning dispatch.)

**Test.** Three tasks: done/failed/queued. `status --tasks` (default)
shows the queued row only (as today); `--include-terminal` shows all
three with their terminal states + error + partial flag.

---

## 6. Installer / ops robustness + smaller defects (tier 7)

### F22 — Main-only mode: the manager must stay in the probe loop, not exit

**Problem.** When no instance fits, `main()` sends READY and `return
EXIT_OK` — a clean exit under `Type=notify` + `Restart=on-failure`, so
the manager is GONE: health.json freezes, `bonsai2-reload` waits ~215 s
and returns 84, nothing reaps tasks. The docstring and SPEC S2.4 both
say the manager stays up in main-only mode.

**CURRENT — `bin/bonsai2-manager:2840–2843`:**

```python
            notify_sd("READY=1")
            return run_probe_loop(cfg, runtime, health_dir, args.settle, log=_log)
        notify_sd("READY=1")
        return EXIT_OK
```

(The settle>0 path is correct; the plain path returns.)

**FIX.**

```python
        notify_sd("READY=1")
        # F22: main-only is a RUNNING state, not a finished boot — the
        # probe loop owns the reload control files, the reaper, and the
        # (future) pool-dispatcher. A clean exit would leave health.json
        # frozen and the S3.2 control files unprocessed.
        run_probe_loop(cfg, runtime, health_dir, 0.0, log=_log)
        return EXIT_OK
```

**Test.** Hermetic: `spawn_sequence` returns 0 instances (allocation
tier denies all) → main() enters `run_probe_loop` (assert via the seam),
health.json carries `main_only_reason`, a reload control file dropped
during the loop is processed.

---

### F23 — The probe loop must survive any one bad pass; every subprocess gets a timeout

**Problem.** `run_probe_loop`'s `while True` body protects only 3 of its
passes with try/except: `record_bfailed_causes`, `process_control`,
`reap_tasks_once`. `main_healthy`, `_refresh_states`,
`check_escalation`, `check_drift`, and `write_health` are UNGUARDED
(`:2563–2617`): a non-HTTP listener on an instance port, a truncated
response body, a JSON list where a dict is expected, or a full `/run`
raises out of the loop and the manager dies. No `subprocess.run` in the
manager has a timeout, so a hung `docker inspect` or `systemctl` blocks
the loop forever. With F19-era health.json (no timestamp) nothing
notices.

**CURRENT — `bin/bonsai2-manager:2563–2611` (unprotected region):**

```python
    while True:
        now = time.monotonic()
        main_ok = main_healthy(cfg["main_endpoint"])
        running_snapshot = list(runtime.running)
        _refresh_states(runtime, running_snapshot, log, probe_timeout_s=probe_timeout_s)
        check_escalation(runtime, now, base=base, log=log)
        try:
            record_bfailed_causes(...)
        except Exception as exc:
            ...
```

**FIX.**

1. Wrap the five unprotected passes in per-pass try/except with the
   same log shape (a pass error logs and the cycle continues; the
   document write at cycle end is the one pass that MUST land — wrap it
   in a try/except that logs and RETRIES next cycle, but a failed write
   also stamps `health.json`'s staleness visible: the manager writes a
   `stale: true` marker file `health.json.failed` on write failure so
   the harness's F25 check can distinguish "manager alive but /run full"
   from "manager dead").
2. Every `subprocess.run` in the manager gets `timeout=` (docker
   inspect / stop / start / show: 30 s; systemctl show: 10 s). A
   `subprocess.TimeoutExpired` is the same as a non-zero rc for that
   pass (log + continue).
3. Unit: add `WatchdogSec=` is NOT appropriate (Type=notify without
   sd_notify keepalive) — instead add `Restart=always` to
   `units/bonsai2-sglang.service` (currently `on-failure`): a manager
   that dies any way gets restarted; combined with the F22 main-only
   loop the restart is cheap.

**Test.** Hermetic: `_refresh_states` seam raises (simulating a
truncated body) → the loop completes the cycle, writes health.json, and
survives 3 cycles. `subprocess.run` seam with `timeout=1` that sleeps →
the pass logs TimeoutExpired and continues. `write_health` seam fails
once → `health.json.failed` marker exists, next cycle recovers and
removes it.

---

### F24 — Restart guards, adoption, and reload math

**Problem (bundle — Fable M7):**

- **Double-restart guard baseline is per-process.** After one automatic
  restart the manager never restarts a later wedged engine
  (`:1703–1729`): the baseline timestamp is captured at process start,
  so the "no second restart within window" guard measures against the
  WRONG window.
- **No `PartOf=` on the instance units.** After a manager restart, the
  manager treats running instances as cold starts and checks them
  against free memory they already use; on a tight pool it exits 4 and
  loops until systemd's start-limit stops it.
- **Stopped-after-failures units stay "degraded".** The loop never reads
  `SubState`; a unit systemd has stopped after repeated failures keeps
  receiving tasks (`:1632–1640`).
- **Reload double-charge.** The reload path charges `in_pass_charge_gb`
  before verifying the start, then the `spawn_sequence`-equivalent
  re-read charges it again — a reload starts fewer instances than a boot
  with identical memory (`:1995–2021`).
- **`boot_target_s` feedback loop.** Each boot writes its measured
  duration back to `serving.yaml` with a 10 s floor and no ceiling
  (`write_boot_target_s`, `:2198–2234`) — a slow boot raises the next
  boot's deadline; the installer's full-text diff (F26) then sees
  "drift" and restarts the whole pool on an unchanged re-run.
- **`CPUQuota=64%` limits the docker client.** The unit's ExecStart is
  the `docker run` CLI; the container gets no CPU limit. Either enforce
  it at the container (`--cpus` in the ExecStart — the real intent) or
  drop the quota (decide, don't half-enforce).

**FIX.**

- Restart-guard window keyed to the LAST restart of that instance (store
  `last_restart_ts` on the runtime entry), not process start.
- Instance template gains `PartOf=bonsai2-sglang.service` — a manager
  stop/restart takes its instances with it (the manager's boot then
  adopts: on start, any running instance whose container is up is
  marked `healthy-pending-probe` and NOT re-charged against the pool —
  an "adoption pass" at boot, read-only).
- `check_escalation` reads `systemctl show -p SubState` for the unit:
  `stopped` → the entry is `bdown` (no new tasks; the reaper frees its
  slots) regardless of the probe.
- Reload: charge `in_pass_charge_gb` ONLY after a verified start (move
  `in_pass_charge_gb += need_gb` to the success branch of `start(i)`).
- `write_boot_target_s`: clamp to `[10, 300]` and write to
  `/run/bonsai2/boot-measure.json` (manager-owned, not the installer's
  serving.yaml) — this also unblocks F26's diff.
- CPUQuota: take the `--cpus` path — add `f"--cpus {cfg.get('cpus_per_instance', 4)} "`
  to the ExecStart docker run, and drop the unit's `CPUQuota` line (the
  container limit is the documented, inspectable one; `docker stats`
  will show it).

**Test.** Hermetic: (a) two wedged-engine restarts 20 s apart with
per-instance baseline → both restart; with process-start baseline → the
second is skipped (the bug, now inverted by the test). (b) manager
restart seam: boot sees 1 running container → adoption path, no pool
re-charge. (c) `SubState=stopped` → entry `bdown`. (d) reload with 2
targets and tight pool: exactly the same N as a boot (assert the charge
math). (e) boot-measure 400 s → clamped 300 in boot-measure.json,
serving.yaml byte-identical.

---

### F25 — Dead manager goes unnoticed: health.json needs a timestamp + staleness gate

**Problem.** `health.json` carries no timestamp and the manager never
removes it. The harness treats the manager as down only when the file is
MISSING. After a crash (pre-F23) or systemd stop-restart exhaustion,
spawn/status keep answering from the last good state and no reaper runs.
The repo's own `docs/two-red-gpu-tests-fix.md:218` records exactly this:
"the entire steady loop dies and health.json goes stale."

**FIX.**

- Manager: `build_runtime_health` stamps `"ts": time.time()` into
  health.json on every write (and on the first boot write).
- Harness: `load_health_doc` (the exit-84 gate at `:450–467`) gains a
  staleness check: if `now - ts > 3 * health_poll_s` (read from
  serving.yaml, default 30 s → 90 s) the document is treated as
  `HealthMissing` → exit 84. A missing `ts` field (old file) is treated
  as fresh-once: the harness logs `WARN_HEALTH_NO_TS` and allows one
  cycle (grace for in-flight manager upgrades).

**Test.** Hermetic: health doc with `ts = now - 120` (poll 30 s) →
`load_health_doc` raises `HealthMissing` → spawn exits 84. `ts` absent
→ first call passes with the warning, second call (still no ts, +60 s)
fails.

---

### F26 — Installer drift + re-run + rollback + fresh-box

**Problem (bundle):**

- **Unchanged re-run restarts the whole pool.** The installer compares
  the full deployed `serving.yaml` text against its template render
  (`:1779–1793`), but the manager rewrites `boot_target_s` in that file
  after every boot — so every re-run sees drift, rewrites the file,
  restarts the manager, and reloads both instances (run 3's ledger:
  ~11 min to healthy after a no-change re-run). (F24 moves the
  boot-measure out of serving.yaml — this fix makes the diff
  authoritative either way.)
- **Fresh box cannot install.** When the runtime image is missing,
  `bin/bonsai2_build_image.py` needs a local
  `bonsai2-server-build:121a` builder image and stops with ERR_CONFIG
  without it; nothing builds that image, and nothing initializes the
  `third_party/llama.cpp` submodule it is built from. Re-runs pass only
  because the digest already matches on this box.
- **Rollback leaves removed instances running.** `install()` stops the
  unit before deleting the unit file; `rollback()` only deletes the
  file (`:1211–1216` vs `:1839–1841`). Rollback 2→1 leaves
  `bonsai2-server@1` running, holding ~14.8 GB, with no unit file.
- **Smaller installer gaps.** No nvidia-container-toolkit check (SPEC
  S6.2 lists it as a prerequisite); model `revision` is `main` not the
  recorded commit; the free-disk check always reads `/opt` even with
  `--opt-root`; unit ExecStart built by unquoted interpolation of config
  values (a value with a space or `"` breaks the line).

**FIX.**

- Drift diff: compare the INSTALLER-OWNED KEYS only (`count`, `ctx`,
  `kv`, `budget`, `image.pin`, `boot_target_s` — last one only if still
  in serving.yaml pre-F24). The manager-owned keys are excluded from the
  diff (and recorded in the install report as `manager_owned: [...]`).
  A re-run with no installer-key change restarts NOTHING
  (S6.4's "an unchanged re-run restarts nothing").
- Fresh box: `bonsai2_build_image.py` init path:
  `git submodule update --init -- third_party/llama.cpp` → build
  `image/Dockerfile` (the builder) → build the runtime image from the
  pinned submodule commit. Prerequisite check in `install()` step 1:
  `nvidia-container-toolkit` present (`docker info` shows the runtime,
  or the binary exists) → else ERR_CONFIG with the install command in
  the detail.
- Rollback: stop the unit first (`systemctl stop bonsai2-server@<i>` +
  `wait_down`), then delete the file; report `stopped: [i]` in the
  rollback JSON. The rollback test asserts the container is gone
  (`docker inspect` fails), not just the file.
- `revision`: pin the commit hash (the one recorded next to it in the
  YAML comment) into the `revision:` field; the sha check still fails
  closed on content change.
- Free-disk check: `disk_usage(opt_root)` instead of hardcoded `/opt`.
- ExecStart interpolation: wrap each interpolated value in
  `shlex.quote`-equivalent (systemd's quoting: values containing
  whitespace get double-quoted) — the values today (port, key, model
  filename, ctx, kv, reasoning, alias) are safe, but the unit is
  rendered for a box whose config the installer controls; make the
  render defensive, not accidentally-safe.

**Test.** Hermetic: (a) serving.yaml on disk = render +
`boot_target_s: 213` (manager-rewritten) → re-run reports `drift: []`,
no restart seam called. (b) missing submodule + missing builder image →
build path calls `git submodule update` seam, then builder build, then
runtime build (assert order). (c) rollback 2→1: `stop` seam called for
instance 1 before the file-delete seam; `docker inspect` seam returns
not-found. (d) `revision` in the rendered engine yaml == the commit
string. (e) ExecStart with `alias: "my agent"` renders quoted.

---

### F27 — Smaller defects (C10 bundle) — one PR, five one-liners

All in `bin/bonsai2_harness.py` / `bin/bonsai2-taskworker.py` unless
noted. Implement as a single "harden the edges" PR:

1. **Text-JSON tool-call final-answer confusion**
   (`bin/bonsai2-taskworker.py:580–609`): a final answer that happens to
   be a JSON object naming a tool runs as a tool call. Guard: the
   text-JSON parse path (`_parse_text_tool_call`) accepts an object as a
   tool call ONLY when it carries an explicit `tool`/`tool_name`/`name`
   key whose value is in `_TOOL_NAMES` AND at least one of `cmd`/`code`/
   `path` — a bare `{"bash": "...", "python": "..."}` shape with no
   marker key is an answer.
2. **`--deadline nan` / `--deadline inf`**
   (`bin/bonsai2_harness.py:1704–1715`): reject non-finite deadline
   values (exit 2, `ERR_ARGS: --deadline must be a finite positive
   number`) — `nan`/`inf` currently pass validation and disable every
   deadline check.
3. **No top-level exception guard**
   (`bin/bonsai2_harness.py:2891–2916`): a missing PyYAML, a non-UTF-8
   prompt file, or `--ctx ²` exits 1 with no JSON (outside the closed
   exit-code set). Wrap `dispatch()` in a top-level handler: any
   uncaught exception → `{"error": "ERR_INTERNAL", "detail": ...}` +
   exit 2 (args/config class) or a new reserved code only if the closed
   set is extended deliberately (record it).
4. **Workspace rotation scope**
   (`bin/bonsai2_harness.py:581–593`): rotation deletes every
   subdirectory of `workspace_dir` untouched for 7 days, not only task
   directories. Only prune directories whose name matches the task-id
   shape (`^[0-9a-f]{8,}$`) or that carry a `.bonsai2-task` marker file
   (the spawn writes the marker; rotation prunes on the marker).
5. **Steer to a `--tools off` task** (`bin/bonsai2_harness.py:2383+`):
   steer returns success but the message never reaches the model (the
   tools-off path is a single completion — no step boundary to drain it
   at). Return exit 2 with
   `ERR_STEER_TOOLS_OFF: task was spawned --tools off; steer has no step
   boundary to land in` UNLESS the task is still `queued` (then steer
   is fine — the drain happens at the first step).

**Test per item.** (1) Final answer `{"bash": "echo hi", "python":
"print(1)"}` (no marker key) → done, answer preserved. (2) `--deadline
inf` → exit 2. (3) `import yaml` raised in a stubbed env → exit 2 +
valid JSON on stdout. (4) A 9-day-old non-task subdir under workspaces
survives rotation. (5) Steer to a running tools-off task → exit 2 with
the detail.

---

## 7. Directives — how the project proceeds on this branch

**Branch & merge model.** All work on `integration/consolidated-review-fixes`
(off `main` @ b17eed0). `main` stays untouched until you merge. Each
item F01–F27 lands as its own PR (one reviewable unit, own DOD per the
AGENTS.md PR-body contract, own `./scripts/gate.sh` + `review.sh`
round). Recommended landing order (each unblocks the next):

1. F18 + F19 (gate & secret scan) — from now on every PR is verified by
   the real suite; without this the rest of the branch is untested.
2. F01 + F04 + F05 (state machine core: charge, reaper CAS, cancel CAS) —
   the invariant set every later fix builds on.
3. F02 + F03 + F13 (admission + dispatch + completion semantics).
4. F06 + F07 + F08 + F09 + F10 + F11 (boot/lifecycle/memory as a set —
   they share the cold-start test).
5. F12 + F14 + F15 + F16 + F17 (deadline/tool security bundle).
6. F22 + F23 + F24 + F25 + F26 (manager robustness bundle).
7. F21 + F27 (progress visibility + edges).
8. F20 (the v2 skill) LAST — it documents the post-fix code.

**SPEC retirement (done on this branch).** `SPEC.md` moved to
`archive/SPEC.md` (git-mv, history preserved). A pointer file at the
root says: retired 2026-10-06, resurrect-able, the v2 skill + this
fixes file are the working documents until then. Per the
spec-amendment rule: every PR that would have amended a SPEC section now
records the delta in the PR body under `## Spec amendments` (before/after
+ the archive section it refers to) instead of editing the archive.
Resurrection = `git mv archive/SPEC.md SPEC.md` + one consistency pass
against the v2 skill.

**MVP safety rule still applies.** Load/test with exactly 1 instance
(`install.sh --instances 1`) until the end-to-end check below passes;
N=2 (and the F10 busy-instance path under real saturation) is the
integration-branch final test, not an intermediate one.

**VRAM audit rule still applies.** Before any @gpu test on this branch:
nvidia-smi + free + docker stats; commit or stash before stressing the
box.

**Definition of done for the BRANCH (the merge gate).** All of:

- [ ] F01–F27 merged on the branch, each with its PR-body DOD +
      spec-delta notes.
- [ ] The full hermetic suite (F18's step 7) passes, and every fix has
      at least one regression test that fails on b17eed0 (record the
      "fail-before" evidence in the PR body).
- [ ] Cold-start check (F06) on this box: manager+instances stopped →
      first healthy instance < boot_target_s; second instance's tier log
      shows the first's footprint subtracted.
- [ ] One live batch with 1 instance: 3 tasks (1 queued-beyond-spawn,
      1 engine-retried, 1 token-capped) → no stranded queue, no
      false-done on the capped task, retry counted in slots, unload ack
      `state: ok` only when the port is actually down.
- [ ] The v2 skill (F20) is the active skill and a fresh main session
      runs the run-3 task shape (skeleton + modification) from it alone.
- [ ] No `UNRESOLVED` markers without a disposition (per the AGENTS.md
      uncertainty rule; each PR carries its `## Uncertainties &
      decisions` section in the fixed 5-subsection shape).

**FOLLOW-UPS (deliberately NOT on this branch — record, don't build):**

- `bonsai2-batch` deterministic wait-and-release command (the reviews'
  biggest usability item): depends on F03 (manager dispatch) + F11
  (verified release) + F13 (partial semantics). Its own PR after merge.
- Bench re-run + rubric fixes (the excluded tier 4) → re-anchor the
  v2 skill's provisional cost table. The skill's speed/cost numbers are
  explicitly "to be re-anchored" until then.
- bubblewrap/nsjail tool isolation (the F14 follow-up).
- The post-MVP CI/CD proposal (AGENTS.md) gets this branch's review
  rounds as input.

**Review loop.** One reviewer (the pi reviewer on :30001), sequential
— this box has one SGLang engine; audits before spawning it. Reviewer
dispositions go in PR comments, never into source (the T3 finding: stop
embedding "G3 PR#18 r11 P1#4" in comments for new code — the existing
history stays, new code doesn't add the pattern).
