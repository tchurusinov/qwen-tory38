---
name: bonsai2-harness
description: "Use when offloading work to the bonsai2 subagent pool — S3.2 commands, allocation doctrine, calibrated costs, bench-validated recipes."
---

# bonsai2-harness — delegation doctrine for the subagent pool

This skill is the doctrine layer between the harness control scripts
(SPEC S3 — the commands) and the bench results (SPEC S5 — where subagents
actually win). It is **harness-agnostic** by form: it may be used by any
agent runtime that loads text skills and can run bash (Hermes, pi, or
equivalent), pointed at ANY pool that exposes the S3.2 command surface.
The box-specific facts live in the single **"This box"** section below;
everything else is written against the S3.2 surface. A different box
updates "This box" only — the doctrine, the command reference, the
verification protocol, and the recipes stay the same.

## When to use

Any session where the main agent may offload work to the subagent pool.
Loaded on demand. If the pool is absent or explicitly unloaded, the
commands still exist — they answer with exit 80 (main-only mode) and the
work goes to main (the failure doctrine below covers every code).

## This box

The only box-specific section. A different box updates this section only.

- **Box:** GB10-class unified-memory machine, ~121.6 GiB LPDDR5X, aarch64.
- **Main stack (external infrastructure):** SGLang endpoint
  `http://localhost:30001` (qwen3.8-27b NVFP4, the main agent's own
  model). Fresh-load footprint ~76.4 GB. Key-walled: completions need
  `main_api_key` from the harness config; the health probe is keyless
  ("401-walled is still UP" convention). Consume, never modify.
- **Subagent pool:** N = 2 instances (computed at install by the S1.4
  formula from the main-stack footprint). Per-instance budget 14.78 GB
  (14.08 GB measured q4_0 footprint + 5 % margin). Ports 30101/30102.
  Model: Ternary-Bonsai-2-27B, PQ2_0 quant (7.21 GB, PTQ1_0 5.95 GB
  fallback). Engine: prism-ml llama.cpp fork, pin `9a9394a8` (prism
  branch — the stock build refuses the PQ2_0 packing). Launch shape:
  ctx 262144, parallel 8, q4_0 KV (`--cache-type-k/v q4_0`), deepseek
  reasoning format. Per-slot context = ctx/parallel = 32,768 tokens.
- **Calibrated t/s constants (2026-09-28, post-hotfix run @ commit
  b2cbe98, this pin):** see the cost doctrine section — single-task
  decode 44.7–61.9 t/s (MAP 1.8 @gpu live runs 2026-09-22; the S1.8 #3
  load-band floor is 26.2 = 0.9 × 29.16 community baseline), 16-wide
  contention 3.7–19.3 per-task, tool-loop
  M9 413 s / 5 tool steps, M10 108 s / 10 tool steps, main idle 8.52 t/s,
  main-under-load planning number = halved (user observation 30 → 15;
  the M8 bench 0.00 is a timing artifact).
- **Layout:** pre-install = this repo (`bin/` commands + `etc/*.yaml`
  templates + `bench/` framework); installed = `/opt/bonsai2/bin/`
  (commands), `/opt/bonsai2/etc/` (the four rendered configs),
  `/run/bonsai2/` (runtime: health.json, results/, dedup/, bench/,
  workspaces/). The commands below are the installed names; pre-install
  the same scripts run from the repo's `bin/` with
  `BONSAI2_CONFIG`/`BONSAI2_RESULTS_DIR` overrides.
- **Observed healthy steady state:** CPU 4–20 %, GPU ~96 %, page cache
  ~99 % (reclaimable — judge `MemAvailable`, not cache %), VRAM 100 →
  116 GiB box-wide during the 2-instance bench run, staying within the
  per-instance S1.4 tier.

## Command reference (exactly S3.2 — the skill never invents commands)

The pool exposes this closed surface. `stdout` = one JSON object
(machine), `stderr` = human text (diagnostic only, never parsed).
Config from the harness config file; env overrides
`BONSAI2_CONFIG`, `BONSAI2_RESULTS_DIR` (hermetic testing hook).

```
bonsai2-spawn  [-p PROMPT_FILE | -i INLINE_PROMPT] [--tools on|off]
               [--ctx N] [--deadline SECS] [--max-steps N] [--workdir DIR]
               [--out FILE] [--priority low|normal|high]
               -> JSON. Submits to the least-loaded healthy instance.
               --tools defaults ON (the agentic tool loop); --tools off =
               single completion, byte-identical to pre-loop behavior.
               --max-steps bounds the tool loop (range 1-128).
               --workdir DIR confines the toolset to that directory (must
               exist, writable); when omitted the harness creates
               <workspace_dir>/<task_id>.
bonsai2-steer  <TASK_ID> -m MESSAGE [--wait SECS]   # steer-and-wait: block
                                                    # up to SECS for state change
bonsai2-cancel <TASK_ID> [--graceful]               # SIGTERM then SIGKILL after 5 s
bonsai2-status                                       # JSON: stack + per-instance state
bonsai2-list-instances                               # human table; exit 0
bonsai2-collect  <TASK_ID>                           # JSON: task + {answer, reasoning,
                                                     # usage, tool_log, steps, workdir}
                                                     # (tool_log is [] for --tools off)
bonsai2-bench    [--scenario NAME] [--quick]         # the bench runner (S3.5)
bonsai2-unload   [--all | --instances i[,i,...]]     # instant VRAM release; no-op when
                                                     # already unloaded
bonsai2-reload   [--all | --instances i[,i,...]]     # re-spawn through the allocation
                                                     # tier + health re-probe
bonsai2-health                                   # raw health.json, pretty
```

Schemas and mechanics (S3.1/S3.3/S3.4, abridged — the lock is in the spec):

- `spawn`/`steer`/`cancel`/`collect` JSON carries `task_id`, `state`
  (`queued|running|done|failed|timeout|engine_retried`), `instance`,
  `port`, `result_file`, `error`, `detail`.
- `status` JSON carries `stack` (`healthy|degraded|main-only`), the
  per-instance `state` (`healthy|degraded|bdown|bfailed|bunloaded`),
  `slots_in_use`/`slots_total`, `main_healthy`, and `main_only_reason`
  when instances = 0 (`bunloaded` = the user explicitly unloaded — not a
  failure state; routing skips it, the drift watch does not re-spawn it).
- **Routing:** least-loaded, deadline-aware (fewest slots in use,
  tie-break lowest i). A task occupies exactly one slot for its whole
  lifetime. All slots busy → queue (FIFO, priority preempts) up to
  `queue_wait_max_s` (default 60 s); expiry → exit 8.
- **Dedup:** a `spawn` whose prompt hash + flags match a live task within
  24 h is a no-op returning the existing task_id (no double slot charge).
- **Tool loop (`--tools on`):** the task runs a bounded loop inside its
  worker: send history → tool call (validated against the 4-tool schema:
  `bash(cmd, timeout)` / `read_file(path)` / `write_file(path, content)`
  / `python(code, timeout)`; up to 3 consecutive bad calls → task fails
  `ERR_TOOL_INVALID_CALL`) → host-side execution confined to the
  workdir → append result → repeat; final answer → `done`. Per-tool
  timeout 60 s; `--max-steps` (default 32) bounds completions; the
  result file carries `tool_log` (one entry per tool call: step, tool,
  redacted params, exit_code, truncated output, duration_ms) and
  `steps`. `tool_log` is the ledger main verifies against (the
  self-report rule below).
- **Pool control:** `unload` SIGTERMs the targeted instance units
  (instant VRAM release) → main-only mode; `reload` re-spawns through the
  allocation tier + health probe. Both manager-mediated; a dead manager
  → exit 84. Neither touches running tasks on other instances.

### Exit codes (closed set, complete — any other code is a harness bug)

| code | meaning |
|---|---|
| 0 | success / queued / operation accepted (incl. no-op) |
| 2 | malformed args / bad config / unknown TASK_ID |
| 3 | port conflict (instance-level, at submission) |
| 4 | engine-level failure (OOM / download / hash) |
| 5 | ctx too long for the request |
| 8 | deadline/queue-wait expired while still queued or waiting (manager-side wait budget) |
| 20 | task done (collect) |
| 21 | task failed (engine answered error / parse fail / tool-loop error) |
| 22 | task timed out mid-run (the task's own `--deadline` or step budget) |
| 80 | no subagent instances (main-only mode) |
| 82 | main endpoint unhealthy |
| 84 | manager itself down |

Exit 8 vs 22 (both deadline-related, different owners): **8** is the
*manager-side wait budget*, with exactly two owners: (a) `spawn` — the
task is still in the queue when `queue_wait_max_s` expires; (b)
`steer --wait` — no state change within the SECS window (the task is
still running). `collect` never returns 8. **22** is the *task's own*
`--deadline` (or step budget) expiring mid-run, so the manager aborts
it. A task that never started (queue) → 8; a task that started but ran
past its deadline → 22.

Per-command mapping (closed set unchanged): `spawn` 0 = accepted/queued
(the terminal state comes via `collect`); 3 = routing candidate
`bdown`-mid-clear at submission (check `status`, wait 30 s, re-spawn);
4/5/80/82/84 = stack conditions at submission; 8 = still queued when
`queue_wait_max_s` expires. `steer`/`cancel` 0 = accepted; 2 = unknown
TASK_ID; `steer --wait` 8 = no state change in the window. `collect`
20/21/22 = terminal; 0 = non-terminal (poll). `unload`/`reload` 0 = done
(incl. no-op and partial, which reports `"state": "partial"`); 2 = bad
args; 84 = manager down. `status`/`list-instances`/`health` 0; 82/84 when
the probed condition is bad. `bench` 0/2.

## Work allocation (S4.2 — every row bench-cited)

The table says *what* to delegate; the recipes say *how*. The verdicts
are the 2026-09-28 post-hotfix run (this pin) — the citation is the
bench row; the verdict is data, not a verdict on the allocation.

| task shape | allocation | bench | live verdict (2026-09-28) |
|---|---|---|---|
| Multi-step plan / architecture / cross-file refactor coherence | **main** | — (main by construction) | long-horizon + long-context + tool-chain coherence; a subagent lacks the horizon |
| Single-file code-gen chunk (new function / test file / boilerplate) | **subagent** | [M1] | fail 0/3 at the pin: reasoning-only output (the H4 content-forcing prompt landed; the rubric seam is open). The shape itself is verify-by-compile; recipe R1 forces content-only |
| Per-file / per-section analysis (review notes, doc summaries, data transforms) | **fan-out N** (N = min(files, instances×slots)) | [M3] | pass 0.88 (auto 5/6 + spot-check 1.00). Independent units work; the spot-check is main's job (recipe R5 drives it) |
| Diff review of a PR-sized diff | **subagent draft → main verifies** | [M4] | pass 10/10 seeded bugs. The subagent finds bugs reliably; the draft is still a self-report — main verifies the claim |
| Long-context reading of repo docs / transcripts (≥ ~30K tokens) | **main** | [M5] | pass 5/5 QA (the live M5 golden doc is ~475 tokens, `ctx_cap` recorded 8192 — the bench validates the QA-on-doc shape, NOT the 60K shape). The row stays **main** because main's 262K ctx is free (already loaded) and a subagent at the 8k default loses the tail — that reasoning, not M5, supports the 60K allocation. Subagent is viable for QA on long docs only when the doc fits one slot (`--ctx` is the pre-submission gate value, S3.4; the real cap is the per-slot 32,768 = ctx/parallel; a 60K doc does NOT fit one slot → split R5-style or run on main; ctx sizing per recipe R6) |
| Test execution / lint / build-check | **subagent** | [M2] | pass 10/10 coverage. Mechanical, parallelizable, output is a report not a decision |
| Draft writing (docs, summaries, commit messages at scale) | **subagent** | [M3] | pass 0.88. Quality bar is lower-verification; main edits the draft |
| Anything requiring live tool feedback loops (iterative debugging, interactive exploration) | **main** | — (main by construction) | feedback latency; fire-and-collect gives no mid-task steering value here |
| Verification of a subagent's claimed success (file exists? test passed? diff applies?) | **main, always** | — (the self-report rule below) | never trust on the word |
| Spec-chunk implementation (bounded, self-contained, with a test suite to run) | **subagent, --tools on** | [M9] | fail (2/3 sub-conditions met): main re-run passed (exit 0) + tool_log shows the suite run — but the workdir file check fails systematically (driver cleanup-ordering defect, H6: the workdir is rmtree'd before the scorer looks). Capability layer works; recipe R4 makes main's own-copy re-run the authoritative check |
| Test execution batch with a report (run N tests, structure the results) | **subagent, --tools on** | [M10] | fail 0/2 spot verdicts agree: the batch ran correctly (10/10 covered) but the report's per-file `status` did not mirror the exit code (status semantics defect). Recipe R2 pins the report schema |
| Anything that must see a rendered page / browser state (camoufox) | **main, or subagent --tools on via the python tool** | — (no S5.2 row yet; the template is provisional) | the venv is host-side; a subagent drives it through the python/bash tools; a multi-step interactive session with state main must keep = main |

### The self-report rule (locked — amended "unverified claim WITH EVIDENCE")

Every subagent result is treated as an **unverified claim, WITH
EVIDENCE**: a `--tools on` result carries the `tool_log` ledger, and
main's named verification is **ledger-first**:

1. Check the claimed suite ran — a `tool_log` entry for the test command
   with `exit_code: 0`.
2. Check the claimed files exist — read the workdir path on main's own
   side (the result JSON's `workdir` field is main's verification entry
   point).
3. Re-run the named check on main's own side (main's own scratch copy),
   and trust that result, not the claim.

The ledger lowers the verification cost term in the cost doctrine — it
does not remove it. Verification cost is part of the task's true cost:
a task that costs main 60 s to verify but saves 90 s on main is not a
win; it goes to main.

## Workflow branches + session economy (S4.3)

### Two workflow branches (structurally identical, different pool lifecycle)

Same S3.2 commands, same verification protocol. Differ only in when the
pool is loaded and how context is retained:

- **Stateless (DEFAULT):** understand → load (reload if needed) →
  delegate a bounded, clearly-defined task → `collect` → **`bonsai2-unload
  --all`** → verify at full main t/s → re-load only if more work is
  needed. No context retention between delegations (a new task is a new
  `spawn`, not a `steer` of the old task). Default because load is
  relatively instantaneous (warm boot ~8–15 s; 21.5 s observed under
  contention; reload budget 30 s) and the unload is what restores the
  main's full t/s between delegations.
- **Stateful:** load once, keep the instance loaded across related steps;
  the subagent retains context from previous steps and the main `steer`s
  between steps instead of re-spawning. Costs: the instance's VRAM stays
  allocated (main's t/s stays halved for the whole window) and the task's
  context grows with each step. Use only for multi-step work where the
  subagent must remember earlier steps (e.g. a debugging session where
  step 3 depends on step 1's findings).

Branch choice is per-task; the recipes each name their branch.

### Slot economy (one engine is shared — the economy is about slots, not tokens)

- Keep ≤ 6 of 8 slots busy per instance (2 free for deadline-sensitive
  spawns). This is a doctrine cap on main's own spawn count, not a
  harness rule: routing does not enforce it; an over-subscribed round
  is handled by the `queue_wait_max_s` backstop (exit 8), not a queue
  rejection.
- **Batch spawns:** when fanning out, spawn all N in one turn (one tool
  block), then one `collect` loop — never spawn-collect-spawn-collect
  ping-pong (doubles main's turn count).
- **Short main turns while subagents run:** main's inter-spawn turns
  should not call its own endpoint with >2K-token prompts — short turns
  keep the session cache hot and the loop tight.
- **Instance hygiene:** prefer reusing a running task over re-spawning
  when the task is >50 % done. Steer a `--tools on` task: the message is
  injected at the loop's next step boundary (effective mid-loop). Steer
  a `--tools off` task: advisory only — consumed at the next attempt
  (a retry), not the in-flight single completion; for a single-attempt
  task mid-stream, `cancel` + `spawn` with the corrected prompt is the
  effective path. Cancel is for wrong-shape tasks, not impatience.

### Main t/s halving under a loaded pool (live-run doctrine, 2026-09-28)

While subagent instances are loaded and busy, the main agent's effective
t/s falls to roughly half (observed 30 → 15; the M8 bench guardrail is
meant to measure this — its 0.00 is a timing artifact, the user's
observation is the planning number). The doctrine: plan the work so main
sends subagents **LARGE CLEARLY-PREDEFINED chunks** and then works on the
next **intelligence-critical** task — planning, architecture,
verification of subagent output — that does not suffer from the reduced
t/s. Never do main-side token-heavy generation while the pool is loaded;
let the grunts do the grunt work.

### VRAM climb + release (live-run doctrine, 2026-09-28)

Per-task VRAM climbs during a run as the KV cache fills (observed
100 → 116 GiB box-wide on the 2-instance live run, staying within the
per-instance 14.78 GB tier — the climb is KV growth, not a leak; it
stays under the allocation tier by construction). After a task/batch the
pool's VRAM does NOT release on its own (observed 117 GiB post-run with
instances still loaded). Doctrine: `bonsai2-unload --all` when the pool
is done. In the stateless branch the unload is part of the normal loop;
in the stateful branch it happens at task completion; after a bench run,
check `bonsai2-health` / `nvidia-smi` and unload if VRAM is still high
(the bench's `--release-after` / `--release-between` are the same
operation in the bench context).

### Reading the box meters (live-run doctrine, 2026-09-28)

- High page-cache % (~99 % with main + 2 instances) is the OS
  reclaimable cache, not a problem — it is freed under memory pressure.
  Judge headroom by `MemAvailable` (the reclaimable-pool fields:
  MemFree, Cached, Shmem, SReclaimable), not cache %.
- CPU at 4–20 % with GPU at ~96 % is the healthy steady state: decode is
  GPU/memory-bandwidth-bound on the unified LPDDR5X pool. Squeezing the
  CPU further does not raise subagent decode t/s; it only speeds
  prefill, which is already fast.

## Cost doctrine (S4.4 — re-benched constants, 2026-09-28)

The a-priori floors (t/s_decode ≈ 29, t/s_prefill ≈ 4800, community GB10
bench, UNCALIBRATED) are replaced where the Phase 4 run measured; the
calibrated values are in "This box".

**Calibrated constants (this pin, 2026-09-28):**

| constant | value | source |
|---|---|---|
| subagent decode t/s, single task, no contention | 44.7–61.9 | MAP 1.8 @gpu live runs 2026-09-22 (8-concurrent sustained, 44.7–61.9 t/s aggregate; band floor 26.2) |
| subagent decode t/s, under 16-wide contention | 3.7–19.3 per task | [M7] (16 tasks, 2 instances) |
| aggregate t/s, 16-wide, 2 instances | 2.08 wall / 0.215 serial | [M7] — the headline is the wall |
| subagent decode t/s, tool-loop | 0.27 effective (M9: 111 completion tokens / 413 s) · 2.33 (M10: 250 / 108 s) | [M9]/[M10] — tool execution dominates, not decode |
| main t/s, idle (no subagents) | 8.52 | [M6] control |
| main t/s, while subagents loaded | halved (planning number; user observation 30 → 15) | live run; [M8] 0.00 = timing artifact |
| t/s_prefill | ≈ 4800 (a-priori, UNCALIBRATED at the first full bench) | keep the 20 % dispatch-side margin |

The key calibration: the a-priori 29 t/s was a *single-task,
no-contention* number. Under realistic multi-task load (16 tasks,
2 instances, 8 slots each), per-task decode drops to 3.7–19.3 t/s. The
win/lose call uses the **contention-adjusted** number, not the
single-task number. Tool-loop tasks have a different cost profile
entirely: the wall time is dominated by tool execution (bash commands,
file writes, test runs) + step-boundary prefill of the growing history,
not decode — M9's single task ran 413 s with only 111 completion tokens
(0.27 effective t/s) because its tool steps took the bulk of the time;
M10 ran 108 s for 250 tokens (2.33 effective).

**Per-task dispatch cost estimate (main-side, for the win/lose call):**

```
dispatch_cost = prefill_tokens / t/s_prefill
              + output_tokens / t/s_decode        (contention-adjusted when N in flight)
              + verification_cost_main_s          (inside dispatch_cost, not added twice)

--tools on additionally:
dispatch_cost += steps_est * tool_mean_duration_s + prefill_of_tool_results
```

Calibrated per-step wall cost from the run — the WHOLE per-step term
(`tool_mean_duration_s + prefill_of_tool_results`, S4.4), measured as
wall time per tool step: M9-class spec-chunk (bash unittest + file
writes) ≈ 413 s / 5 tool steps ≈ 82.6 s per step; M10-class test batch
(bash unittest × 10) ≈ 108 s / 10 tool steps ≈ 10.8 s per step.
("tool steps" = the loop's tool-call executions; that is NOT the S3.4
result field `steps` (total completions issued) — in these runs
tool steps ≈ completions, since each completion carries one tool call
except the final answer.) `tool_mean_duration_s` alone is capped at
the 60 s tool timeout; what dominates the per-step wall is the
tool execution + the step-boundary completion + the re-prefill of the
growing history (M9: 413 s for only 111 completion tokens = 0.27
effective t/s). Estimate `steps_est` from the task (the recipe's
definition of done names the expected tool calls).

**Win rule:** dispatch iff `dispatch_cost < est_main_time_s(task)`
(est_main_time_s = the time the same task would take on main, including
main's own verification of the outcome — use the main-t/s-halved number
while the pool is loaded) AND the task matches a S4.2 row that is
bench-confirmed. **Two consecutive bench-confirmed losses on a task
shape flip that row to main until re-benched** (the doctrine is
self-correcting, not opinion-locked). Note the M1 nuance from the
2026-09-28 run: M1 failed twice (pre-run + post-run), but the [M6]
control shows main fails the M1 shape too — the loss is engine-format
(reasoning-only content at the pin), not an allocation loss, so the row
stays subagent with the content-forcing recipe until the rubric seam is
closed and re-benched.

**Instance cost:** an idle instance costs nothing (no license, no
clock); the only real cost is VRAM (the allocation tier) and decode
contention with the bench. No charge-backs in v1.

## Failure doctrine (S4.5 — one paragraph per S3.2 exit code)

- **0 (success / queued / accepted):** proceed. For `spawn`, the task's
  later terminal state is observed by `collect` — poll with the deadline
  in view. For `steer`/`cancel`/`unload`/`reload`, the operation is
  done (including no-ops and partial reloads, which report
  `"state": "partial"` — a valid state, not an error).
- **2 (malformed args / bad config / unknown TASK_ID):** fix the call —
  the human-readable detail is on stderr. If twice in a row, re-read
  this skill's command reference section (skill self-heal: it names its
  own section). Unknown TASK_ID on `collect`/`steer`/`cancel` usually
  means a stale id from a rotated/finished task — re-`status`.
- **3 (port conflict, instance-level):** check `status`; if the instance
  is still `bdown`, wait one health cycle (30 s) and re-spawn — the
  manager clears the collision on the instance restart. If `healthy`,
  the collision was mid-clear; re-spawn goes to the next least-loaded
  instance anyway.
- **4 (engine-level failure: OOM / download / hash):** check `status`;
  if the instance is `bfailed` → requeue on another instance or run on
  main. An OOM at grant time that the allocation tier should have caught
  is a budget drift — note it in the session log.
- **5 (ctx too long for the request):** split the task (smaller chunks)
  or trim the input. Never raise ctx on a per-task basis in v1
  (instance-level only, via the failure policy). Remember the per-slot
  cap: ctx/parallel = 32,768 tokens on this box's launch shape.
- **8 (deadline/queue-wait expired, still queued or waiting):**
  manager-side wait budget — the task never got a slot (or the
  `steer --wait` window lapsed with no state change). Check `status`
  for load; if the pool is saturated, shrink the fan-out to the ≤ 6-of-8
  doctrine cap and re-spawn; if a single deadline-sensitive task keeps
  losing the queue race, give it `--priority high`.
- **20 (task done, collect):** use the result — and verify it before
  trusting it (the self-report rule). A 20 with an empty `answer` and a
  long `reasoning` is the pin's reasoning-only artifact (M1): check
  `content` first, fall back to `reasoning_content` if `content` is
  empty, and note it.
- **21 (task failed: engine answered error / parse fail / tool-loop
  error):** read `detail` (and the `tool_log` ledger up to the failure
  — the ledger is diagnostic data, not a verdict). Re-spawn once with a
  tightened prompt; second failure → run on main. `ERR_TOOL_INVALID_CALL`
  (3 consecutive bad tool calls) usually means the task shape is not
  tool-loop-friendly → main. `ERR_CTX_TOO_LONG` mid-loop → tighter
  scope or `--max-steps`.
- **22 (task timed out mid-run: the task's own deadline or step budget
  expired):** `cancel` if not already reaped; re-spawn with tighter
  scope (smaller `--deadline` is not the fix — smaller *task*). If the
  task keeps timing out at 2× its deadline, it is a main task (move it,
  note it for S4.2 re-calibration).
- **80 (no subagent instances — main-only mode):** proceed on main; log
  the mode (a session note, not an error). The pool is explicitly
  off (`user_unloaded`) or N = 0 (`budget_zero`). Decide:
  `bonsai2-reload --all` if the work is delegable and VRAM allows, or
  just run main-only.
- **82 (main endpoint unhealthy):** stop spawning — the session depends
  on the main stack even if the work is subagent-shaped. Check the main
  stack (`systemctl status qwen38-sglang`); it is external infrastructure
  (consume, never modify), but the session is blocked while it is down.
- **84 (manager down):** `systemctl status bonsai2-sglang`; retry in
  10 s × 3, then escalate to the user via the session log. The pool is
  manager-mediated — while the manager is down, every pool operation
  (spawn/collect/unload/reload) answers 84.

## Prompt templates (S4.1 — one proven template per S4.2 tool-loop row)

The subagent is a text model with a toolset, not a second main agent:
its quality is bounded by the prompt. The main agent substitutes the
`{placeholders}` with task specifics and does NOT improvise the
structure. A template always carries: the **workdir contract**, the
**definition of done** (the test command + expected exit code), and the
**final-answer contract** (one paragraph: what was done, what was
verified — exact commands + exit codes, what failed).

### T1 — spec-chunk implementation (S4.2 tool-loop row, [M9])

```
You are implementing a spec chunk.
Workdir: {workdir} (all paths are relative to it; do not look outside it).

Spec: {spec_json}
  (functions: {function_list}; module: {module_name}; suite: {suite_name};
   run command: {run_command})

Task:
1. Implement the module at {module_path} with the spec's functions.
2. Write the test suite at {suite_path} with {n} test cases (one per function).
3. Run the suite: {run_command}
4. Confirm the suite passes (exit 0).

Definition of done:
- The module compiles and defines all spec functions.
- The suite passes: {run_command} exits 0.
- The workdir contains {module_name} and {suite_name}.

Final answer contract (your last message, no tool calls after this):
One paragraph: what you implemented, what you verified (the exact
commands you ran and their exit codes), what failed (if anything).
```

### T2 — test batch with structured report (S4.2 tool-loop row, [M10])

```
You are running a test batch and generating a report.
Workdir: {workdir} (all paths are relative to it; do not look outside it).

Task: Run each of the {N} files matching {file_pattern} with:
  {run_command_pattern}
Record the exit code and duration for each file.

Definition of done:
- All {N} files have been run exactly once.
- The report is written to {report_path} as JSON with this schema:
  {"files": [{{"file": "<name>", "status": "pass"|"fail", "exit_code": <int>, "duration_ms": <int>}}]}
- The `status` field must reflect the ACTUAL exit code (0 → "pass",
  non-zero → "fail"), not the batch verdict.

Final answer contract:
One paragraph: how many files passed, how many failed, which files
failed (by name), and the exact commands you ran.
```

### T3 — browser task via the python tool (S4.2 row, provisional —
no S5.2 bench row yet; the citation rule applies when it is benched)

```
You are driving a headless browser through the python tool.
Workdir: {workdir} (all paths are relative to it; do not look outside it).

Task: {browser_task} (e.g. load {url}, wait for {selector}, extract
{data} into {out_path}).

Environment: the browser venv is at {venv_python} (host-side; call it
through the python tool's interpreter path when the tool needs a
specific python). Screenshot/page-state evidence goes to {workdir}/evidence/.

Definition of done:
- {concrete_done} (e.g. {out_path} exists and contains {expected_shape}).
- Evidence: one screenshot or DOM dump per visited state, in
  {workdir}/evidence/.

Final answer contract:
One paragraph: what you did, the exact commands you ran (with their
exit codes), what failed (if anything), the evidence file names.
```

## Recipes (S4.1 delta 2026-09-28 — worked examples)

Each recipe: task shape + bench citation + prompt template + main's
verification protocol + observed failure modes + workflow branch. A
recipe without a bench citation is a review defect (the S4.1 citation
rule applies to recipes, not just to the table). Sources: (a) the
practical lessons of the 2026-09-27/28 live runs (M1 reasoning-only
content, M9 workdir-cleanup ordering, M10 report status semantics, main
t/s halving under a loaded pool, KV growth / pool release) and (b)
external research on orchestrator-worker delegation (task boundaries,
output contracts, effort scaling — the source list is in
`docs/phase5-skill-preliminary-analysis-20260928.md` §9). The
orchestrator-worker research in one line: subagents win on
*read-only, independent units* where the context compression (they read
6K tokens, return ~400) beats the token cost; they lose on
*write-heavy, shared-artifact* work (the main agent's repo is the shared
artifact — every recipe below is single-writer-per-workdir).

### R1 — single-file code-gen ([M1])

- **Task shape:** generate a single-file module + its test suite
  (the S4.2 "single-file code-gen chunk" row).
- **Bench:** [M1] — fail 0/3 at the pin (reasoning-only output:
  4.5k reasoning chars, 1 content token; H4's content-forcing prompt
  landed but the rubric seam is open).
- **Prompt template (text-only, `--tools off`):**

```
You are implementing a single-file Python module.
Workdir: {workdir} (all paths are relative to it; do not look outside it).

Task: {task_description}

Definition of done:
- The module file is at {module_path} and compiles:
  `python -c "import {module_name}"` exits 0.
- The test suite is at {suite_path} and passes:
  `python -m unittest {suite_name}` exits 0.

Final answer contract (your last message, no tool calls after this):
One paragraph: what you did, what you verified (the exact commands you
ran and their exit codes), what failed (if anything).
Do NOT use reasoning. Write your answer directly.
```

- **Verification:** main re-runs the module compile + suite in its own
  scratch dir; checks the module defines the expected functions. Check
  `content` first; if `content` is empty, fall back to
  `reasoning_content` (the pin's reasoning-only artifact — then note it
  for the M1 rubric seam).
- **Failure modes:** reasoning-only output (above) — the "Do NOT use
  reasoning" preamble is the mitigation. Compile-pass-but-
  suite-fail artifacts: the rubric gates on BOTH compile and suite,
  so a passing compile claim is not a done claim.
- **Workflow:** stateless (single task, no context retention needed).

### R2 — test batch with report ([M10], uses template T2)

- **Task shape:** run N test files, structure the results into a report
  JSON (the S4.2 "test execution batch with a report" row, `--tools on`).
- **Bench:** [M10] — fail 0/2 spot verdicts agree: the subagent ran the
  batch correctly (10/10 covered) but the report's per-file `status`
  field did not mirror the exit code (the live report said "pass" for
  the seeded fails → claimed None vs observed fail).
- **Prompt template:** T2, with `{N}`, `{file_pattern}`,
  `{run_command_pattern}`, `{report_path}` filled.
- **Verification (ledger-first):** main parses the report JSON; checks
  N/N files covered; checks the ledger (`tool_log`) shows the batch run
  with per-file exit codes; re-runs 2 of N files (prefer the
  claimed-fails — that is where status semantics broke live) and checks
  the claimed verdicts agree with observed. The re-run is the
  authoritative check.
- **Failure modes:** status semantics (above) — the template says "status
  must reflect the actual exit code, not the batch verdict". Skipped
  files (coverage < N) → re-spawn with the missing file list.
- **Workflow:** stateless (batch task, no context retention needed).

### R3 — diff review ([M4])

- **Task shape:** review a PR-sized diff for bugs
  (the S4.2 "diff review" row — subagent draft → main verifies).
- **Bench:** [M4] — pass 10/10 seeded bugs: the subagent finds bugs
  reliably. The draft is still a self-report (it may miss bugs or claim
  false positives); main's verification is the real review — the
  reviewer-proposes/builder-verifies split.
- **Prompt template (text-only, `--tools off`):**

```
You are reviewing a code diff for bugs.
Workdir: {workdir} (all paths are relative to it; do not look outside it).

Task: Review the diff at {diff_path} for bugs.
The diff is against {base_ref}...{head_ref}.
Look for: off-by-one, null-pointer, race condition, resource leak,
incorrect API usage, missing error handling.

Definition of done:
- You have read the entire diff.
- You have identified all bugs (if any).
- Your answer lists each bug with: file, line number, bug description,
  severity (P0/P1/P2).

Final answer contract:
One paragraph per bug: file:line — description — severity.
If no bugs: "No bugs found. I reviewed N files, M hunks."
```

- **Verification:** main reads the diff itself; for each claimed bug,
  reads the file at the cited line and checks the claim is real
  (line numbers drift between the subagent's view and main's — probe
  before classifying); main checks no obvious bug was missed (main's
  own review is the floor, the draft is the ceiling candidate).
- **Failure modes:** false positives (the draft over-claims) — waive
  with the probe evidence, do not "fix" correct code to appease the
  draft. Missed bugs — the draft is a candidate list, not a verdict.
- **Workflow:** stateless (single review task; main keeps the diff in
  context for verification).

### R4 — spec-chunk implementation ([M9], uses template T1)

- **Task shape:** implement a bounded spec chunk (a small library with
  N functions + an N-case test suite) — the S4.2 "spec-chunk
  implementation" row, `--tools on`.
- **Bench:** [M9] — fail (2/3 sub-conditions met): main re-run passed (exit 0)
  + tool_log shows the suite run — but the workdir file check fails
  systematically in live runs (the driver's cleanup-ordering defect,
  H6: the workdir is rmtree'd before the scorer looks for the files).
- **Prompt template:** T1, with the spec JSON + run command filled.
- **Verification (ledger-first):** main re-runs the suite in its own
  scratch copy (the authoritative check); checks the `tool_log` shows
  the suite run with exit 0; checks the workdir contains module + suite
  — but treat the workdir check as the one sub-condition that depends on
  the driver's cleanup ordering: if the files are absent yet the
  ledger + re-run both pass, the capability is proven; note the
  ordering defect, do not re-run the task for it.
- **Failure modes:** the workdir-ordering artifact (above). Tool-loop
  overruns: `--max-steps` exhaustion (exit 22) → smaller spec chunk.
- **Workflow:** stateless (single spec chunk; no context retention
  needed). For a multi-chunk spec where chunk 2 depends on chunk 1's
  module: stateful (keep the instance loaded, steer between chunks).

### R5 — fan-out analysis ([M3])

- **Task shape:** analyze N independent files/sections in parallel
  (the S4.2 "per-file / per-section analysis" row, fan-out N).
- **Bench:** [M3] — pass 0.88 post-spot-check re-run: independent units
  work; the spot-check seam must be driven by main (the live run drove
  it by hand; an undriven seam records "unjudged", not silent 0.0).
- **Prompt template (per unit, text-only `--tools off`):**

```
You are analyzing a single file/section.
Workdir: {workdir} (all paths are relative to it; do not look outside it).

Task: Analyze {file_or_section} and produce a summary.
{analysis_brief}

Definition of done:
- Your answer is <= 300 tokens.
- Your answer contains exactly 3 claims about the file/section.
- Each claim is factual and verifiable.

Final answer contract:
3 claims, one per line. No preamble. No summary. Just the 3 claims.
```

- **Verification:** main spot-checks 1 in every 3 sections (reads the
  section itself, checks the claims are accurate) — the M3 seam (SPEC
  S5.2: "main verifies 1/3 by spot-check"). The per-section
  claim-accuracy gate is ≥ 2/3 — i.e. ≥ 2 of that section's 3 named
  claims must be accurate (the rubric threshold is per section, not
  per batch).
- **Failure modes:** summary too long (>300 tokens) or more than 3
  claims — the template caps both; a unit that ignores the cap gets one
  tightened re-spawn, then main absorbs it.
- **Workflow:** stateless, batch-spawn: spawn all N units in one turn
  (one tool block), then one `collect` loop. N = min(files,
  instances×slots), capped by the ≤ 6-of-8 slots doctrine.

### R6 — long-context QA ([M5])

- **Task shape:** answer QA questions about a long document
  (≥ ~30K tokens). The S4.2 row says **main** (main's 262K ctx is free,
  already loaded; a subagent at the 8k default loses the tail) — M5
  proves the subagent works, so this recipe offers both: main by
  default; the subagent alternative when main's window is too small to
  hold the document + the questions. **Ctx sizing (this box's launch
  shape):** `--ctx N` is a pre-submission gate on the REQUESTED ctx
  only (S3.4) — it does not grant a bigger window. The real constraint
  is the per-slot cap = ctx_size/parallel = 262144/8 = 32,768 tokens:
  the doc + questions + margin must fit under THAT, or the task's
  sequence overruns the slot mid-run and fails `ERR_CTX_TOO_LONG`
  (exit 21). So a "60K-token" doc does NOT fit one 32,768 slot on this
  box — split it (one section per unit, R5-style) or run it on main.
  Use `--ctx 65536` only as the gate value that lets a ≤32,768-token
  request through; it is not the window size.
- **Bench:** [M5] — pass 5/5 QA. **Citation honesty:** the live M5
  golden doc is ~475 tokens (`ctx_cap` recorded 8192 in the row), so
  [M5] validates the *QA-on-a-document shape*, not the 60K case. The
  60K allocation to main is supported by main's free-ctx reasoning
  (main's 262K ctx is already loaded; a subagent at the 8k default
  loses the tail), not by M5. The 2026-09-28 matcher fix made the M5
  rubric tolerant of conjunctions (the pre-fix row had lost 1/5 to
  "M1, M2, M7" vs "M1, M2, and M7").
- **Prompt template (if delegating to the subagent):**

```
You are answering questions about a long document.
Workdir: {workdir} (all paths are relative to it; do not look outside it).

Document: {doc_path} ({N} tokens)
Questions: {question_list}

Definition of done:
- You have read the entire document.
- Each answer is <= 50 tokens.
- Each answer is factual and verifiable in the document.

Final answer contract:
One answer per question, labeled. No preamble.
```

- **Verification:** main re-reads the relevant section of the document;
  checks each answer is accurate. For the subagent path: check the
  `--ctx` actually fits (doc + questions + margin < the slot's 32,768
  per-slot cap on this box's launch shape — if not, split the doc).
- **Failure modes:** the 8k-default tail loss (the reason the row stays
  main by default). Matcher strictness (word-boundary particles) —
  main's re-read tolerates what the rubric will not.
- **Workflow:** stateless (single QA task; no context retention needed).

### R7 — saturation / high-concurrency fan-out ([M7])

- **Task shape:** run N independent tasks at full pool concurrency
  (the stress/throughput shape; also the pattern for any
  embarrassingly-parallel batch that is wider than the ≤ 6-of-8
  doctrine cap — e.g. a re-bench or a large analysis batch).
- **Bench:** [M7] — pass: 2.08 t/s wall at 16-wide (2 instances, 8
  slots each), zero 5xx. Per-task t/s 3.7–19.3 (variance = slot
  sharing). The aggregate is the headline, not the per-task.
- **Prompt template:** the per-unit template of the task shape
  (R5-style for analysis, R2-style for batches — the shape defines the
  template, this recipe defines the *load*).
- **Verification:** main checks the aggregate result; checks zero 5xx
  (the M7 rubric); checks per-task t/s is within the expected range
  (3.7–19.3 under 16-wide on this box — outside it = contention or a
  wedged instance, check `status`).
- **Failure modes:** per-task t/s collapse under contention is EXPECTED
  (that is the calibration, not a failure); a hard 5xx is not — it is an
  engine/slot fault, isolate the instance (`status`), let routing route
  around it.
- **Workflow:** stateless, batch-spawn all N in one turn, one collect
  loop. For pure throughput runs this is the stateful exception to the
  "≤ 6 of 8" cap: the pool is saturated by design; release it after
  (`bonsai2-unload --all`).

### Recipe selection guide

| task shape | S4.2 allocation | recipe |
|---|---|---|
| Single-file code-gen | subagent | R1 |
| Test batch + report | subagent --tools on | R2 |
| Diff review | subagent draft → main verifies | R3 |
| Spec-chunk implementation | subagent --tools on | R4 |
| Fan-out analysis (per-file) | fan-out N | R5 |
| Long-context QA | main (or subagent at 64k ctx) | R6 |
| High-concurrency fan-out | fan-out N | R7 |
| Multi-step plan / architecture | main | (no recipe — main does it) |
| Live tool feedback loop | main | (no recipe — main does it) |

Not in the table = main does it (the default is main; a shape without a
recipe or a bench-confirmed row is not delegable until it is benched).

## Install / refresh notes for other boxes

A different box updates "This box" only: instance count + ports,
per-instance budget + ctx/parallel/KV shape, model + quant + engine pin,
the calibrated constants (re-bench — the S5.5 quick bench M1/M2/M7 is
the regression gate; the full M1–M10 on revision/quant change), the
layout paths, and the observed steady-state meters. The doctrine, the
command reference (S3.2 is the surface contract — if the pool's surface
differs, it is not an S3.2 pool), the verification protocol, and the
recipes carry over; only the numbers in the cost doctrine and the
per-slot ctx math need recomputing.
