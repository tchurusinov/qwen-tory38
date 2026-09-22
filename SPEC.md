---
title: qwen-tory38 — SPEC
status: proposed
supersedes: null
last_reviewed: 2026-09-19
method: spec-driven vibe coding (docs/transcripts/spec-driven-vibe-coding-in-action_1Fl94dPn2mU.md)
sources: idea.txt, docs/transcripts/*.md (3 videos), open-questions.md (locked 2026-09-19, deleted post-spec)
---

# qwen-tory38 — SPEC

## Contents

- [Document conventions (locked)](#document-conventions-locked)
- [S0 — Project overview (VRAM budget, division of labor, non-goals, existing-stack contract)](#project-overview-s0)
- [Section 1 — Subagent inference engine (bonsai2)](#section-1--subagent-inference-engine-bonsai2)
  [1.1 Artifacts](#11-artifacts) · [1.2 Serving engine (locked — research R-02 complete)](#12-serving-engine-locked--research-r-02-complete) · [1.3 Per-instance serving command (locked)](#13-per-instance-serving-command-locked) · [1.4 VRAM accounting (formula, locked)](#14-vram-accounting-formula-locked) · [1.5 Error / edge cases (locked)](#15-error--edge-cases-locked) · [1.6 Configuration — `subagent-engine.yaml`](#16-configuration--subagent-engineyaml) · [1.7 Testing](#17-testing) · [1.8 Acceptance criteria (runnable)](#18-acceptance-criteria-runnable)
- [Section 2 — Startup / serving lifecycle](#section-2--startup--serving-lifecycle)
  [2.1 Order (locked)](#21-order-locked) · [2.2 Units (locked)](#22-units-locked) · [2.3 Boot-time budget check (locked)](#23-boot-time-budget-check-locked) · [2.4 N=0 / main-only mode (locked)](#24-n0--main-only-mode-locked) · [2.5 Failure policy (locked)](#25-failure-policy-locked) · [2.6 Configuration — `serving.yaml` (written by install.sh)](#26-configuration--servingyaml-written-by-installsh) · [2.7 Testing](#27-testing) · [2.8 Acceptance (runnable)](#28-acceptance-runnable)
- [Section 3 — Harness control scripts (the main-agent API)](#section-3--harness-control-scripts-the-main-agent-api)
  [3.1 Output contract (locked)](#31-output-contract-locked) · [3.2 Command surface (locked — the full API; every flag specced)](#32-command-surface-locked--the-full-api-every-flag-specced) · [3.3 Instance routing (locked — C-03 "i am not sure, which ever one will be more efficient... you choose")](#33-instance-routing-locked--c-03-i-am-not-sure-which-ever-one-will-be-more-efficient-you-choose) · [3.4 Slot model (locked)](#34-slot-model-locked) · [3.5 Bench runner wiring (S3.5)](#35-bench-runner-wiring-s35) · [3.6 Configuration — `harness.yaml`](#36-configuration--harnessyaml) · [3.7 Testing (hermetic-first)](#37-testing-hermetic-first) · [3.8 Acceptance (runnable)](#38-acceptance-runnable)
- [Section 4 — Harness skill (the doctrine)](#section-4--harness-skill-the-doctrine)
  [4.1 Shape (locked)](#41-shape-locked) · [4.2 Work-allocation table (v1 doctrine — recalibrated by S5, locked as the starting point per C-02/C-04)](#42-work-allocation-table-v1-doctrine--recalibrated-by-s5-locked-as-the-starting-point-per-c-02c-04) · [4.3 Session economy (locked — C-04: "keep subagent instances and sessions in check, we have limited instances, one sglang engine")](#43-session-economy-locked--c-04-keep-subagent-instances-and-sessions-in-check-we-have-limited-instances-one-sglang-engine) · [4.4 Cost doctrine (v1 numbers — calibrated by S5; C-11: "t/s first, quality becomes apparent from task success")](#44-cost-doctrine-v1-numbers--calibrated-by-s5-c-11-ts-first-quality-becomes-apparent-from-task-success) · [4.5 Failure doctrine (locked, per S3.2 exit code)](#45-failure-doctrine-locked-per-s32-exit-code) · [4.6 Testing](#46-testing) · [4.7 Acceptance (runnable)](#47-acceptance-runnable)
- [Section 5 — Bench framework](#section-5--bench-framework)
  [5.1 Scope (locked — C-13: "t/s first, quality becomes apparent from task success")](#51-scope-locked--c-13-ts-first-quality-becomes-apparent-from-task-success) · [5.2 Scenario matrix (locked, from R-08; v1 = M1–M8)](#52-scenario-matrix-locked-from-r-08-v1--m1m8) · [5.3 Success rubrics (locked, named)](#53-success-rubrics-locked-named) · [5.4 Runner (locked)](#54-runner-locked) · [5.5 Re-bench policy (locked — feeds S6.5 upgrade procedure)](#55-re-bench-policy-locked--feeds-s65-upgrade-procedure) · [5.6 Standard workload set (used by S4.6's fresh-session test)](#56-standard-workload-set-used-by-s46s-fresh-session-test) · [5.7 Configuration — `bench.yaml`](#57-configuration--benchyaml) · [5.8 Testing](#58-testing) · [5.9 Acceptance (runnable)](#59-acceptance-runnable)
- [Section 6 — GB10 packaging (one-command install)](#section-6--gb10-packaging-one-command-install)
  [6.1 Artifact form (locked — main-stack parity: pinned docker image under systemd)](#61-artifact-form-locked--main-stack-parity-pinned-docker-image-under-systemd) · [6.2 Prerequisites (verified on this box, R-09 — locked as the supported matrix)](#62-prerequisites-verified-on-this-box-r-09--locked-as-the-supported-matrix) · [6.3 The one command (locked, ordered)](#63-the-one-command-locked-ordered) · [6.4 Idempotency + main-stack contract (locked)](#64-idempotency--main-stack-contract-locked) · [6.5 Upgrade procedure (locked — C-06: "whenever a new version of the main agent or subagent model engine is released")](#65-upgrade-procedure-locked--c-06-whenever-a-new-version-of-the-main-agent-or-subagent-model-engine-is-released) · [6.6 aarch64 pin list (locked — ARM64-only rule, every dep has an aarch64 build)](#66-aarch64-pin-list-locked--arm64-only-rule-every-dep-has-an-aarch64-build) · [6.7 Testing](#67-testing) · [6.8 Acceptance (runnable)](#68-acceptance-runnable)
- [Global testing discipline](#global-testing-discipline)
- [Global acceptance criteria (project-level, runnable)](#global-acceptance-criteria-project-level-runnable)
- [Out of scope (locked, from S0 non-goals)](#out-of-scope-locked-from-s0-non-goals)

## Document conventions (locked)

- Status lifecycle via YAML frontmatter: `proposed` / `active` / `superseded`
  (superseded docs name their replacement). This doc moves to `active` when
  every section has been through review and no open question remains.
- Every component section carries: **Configuration** (concrete schema +
  worked example), **Testing** (what/how, hermetic where possible),
  **Acceptance criteria** (verifiable, runnable).
- No "or similar": every error path names its exit code, exception, or
  emitted message. Every config value is concrete or derived by a named
  formula.
- This SPEC.md is the master. If a section outgrows the doc, it splits into
  `docs/specs/NN-slug.md`; this file keeps a one-paragraph pointer + its
  acceptance criteria.
- Implementation findings that contradict this spec get backported HERE
  (spec follows code when the code was right), never the other way around.
- One command surface, one schema: machine output is JSON on stdout
  (schema in S3.1); stderr is human-only. No project script ever writes
  machine state as free text.

## Project overview (S0)

**What this is.** A two-model agent stack for this GB10-class box
(121.6 GiB unified LPDDR5X): a **main agent** on qwen3.8-27b NVFP4 served
by the pre-existing sglang stack at :30001, plus **subagents** on
Ternary-Bonsai-2-27B (ternary weights, 7.21 GB PQ2_0 GGUF) served as N
instances of the prism-ml llama.cpp fork in the remaining VRAM. The main
agent drives subagents **only** through deterministic bash control
scripts (S3) governed by a harness skill (S4) that encodes
work-allocation doctrine. A bench framework (S5) measures where the
subagents actually win. Everything installs by one command on a GB10 box
(S6).

**Division of labor (locked).** Main = smarter, longer horizon, longer
context, planning, review, verification, anything needing tool chains or
cross-file coherence. Subagents = cheap parallel bulk work: code-gen
chunks, per-file/section analysis, data transformation, draft writing,
test execution, diff summarization — tasks whose output main verifies
before use. Subagent output is a **self-report**: main never trusts it
unverified. The intelligence gap is a design input: main's job grows as
verification overhead grows, and the doctrine (S4) is recalibrated from
bench results (S5), not from vibes.

**VRAM budget (this box, 2026-09-19/20 measurements — recompute at install):**

| component | footprint | source |
|---|---|---|
| qwen3.8-27b NVFP4 weights | ~27.9 GB | SGLang startup log (`weights_gb`) |
| sglang KV pool (262144 ctx, 360510 tokens, fp8 + bf16) | ~17.9 GB | SGLang log "KV Cache is allocated": 5.50 + 5.50 + 3.44 + 3.44 |
| mamba SSM/conv state + intermediate cache pool | ~17.4 GB | SGLang log "Mamba Cache is allocated": 0.27 + 6.82 + 10.12 + 0.15 |
| flash draft (qwen3.8-0.8B-NVFP4-dflash) | ~1.5 GB | image config (draft model) |
| CUDA ctx / runtime / cuda-graph overhead | ~11.7 GB | derived residual so rows sum to the observed total (76.4 − 27.9 − 17.9 − 17.4 − 1.5) |
| **main stack total** | **~76.4 GB** | SGLang log at fresh load: 124.546 GB pool − 48.18 GB `available_gpu_mem` (authoritative for the budget) |
| per bonsai2 instance (PQ2_0, 262K ctx, 8 slots, q4_0 KV) | ~14.0–14.5 GB | 7.21 GB weights + ~6.9 GB q4_0 KV + CUDA overhead (measured 14.44 GB on this box, MAP 1.7) |
| OS + system reserved | 8.0 GB | install-time policy, overridable (host OS, the other resident containers ~2 GB, Xorg, page-cache floor) |
| **install-time budget for bonsai** | **~37.2 GB** | 121.6 − 76.4 − 8.0 |

`N = floor(budget_for_bonsai / per_instance_footprint)` with the worked
example: `floor(37.2 / 14.78) = 2` instances, ~28.8 GB loaded (2 × 14.44),
~8.4 GB headroom. N=0 is a valid result (main-only mode; see S2.4).
Default N = 2 (safe start; the 2026-09-21 S1.4 re-anchor dropped it from
3 — the measured q4_0 footprint is 14.44 GB, not the original 10.91 GB
estimate); whether N is raised
later is decided by evidence: M8 guardrail passes at full load (S5.9 #3)
+ headroom to spare (S6.5).

**Fresh-load vs idle (locked):** the main stack runs SGLang with
`sleep_on_idle=True`: at idle it releases weights/KV/draft to RAM, so
`docker stats` may show the main container at 8–15 GB — a dashboard read
of "83.6 / 121.6 GB used" is exactly that state (main asleep + host +
the other containers), *not* the budgeting state. The budget is built
from the fresh-load footprint, because bonsai must coexist with the main
stack *awake*. Install records the fresh-load footprint from the SGLang
startup log (S6.3 step 4); the S2.5 drift watch compares the live cgroup
against that recorded value.

**Unit convention (locked):** the 121.6 total is `/proc/meminfo`
(GiB); SGLang reports the same pool as 124.546 GB (MiB-based, the
nvidia-smi-visible capacity). The main-stack ~76.4 is measured against
the same unified pool at fresh load; the 8.0 reserved is a policy
constant against the same pool. Working budget:
`121.6 − 76.4 − 8.0 = 37.2`. The residual GiB/GB difference between
`/proc/meminfo` and the tool-reported footprints is inside the
per-instance 5% margin (S1.4), so it does not change N on this box;
on other boxes the install recomputes from live `nvidia-smi`/
`/proc/meminfo` rather than this table.

**Subagent-pool floor + rollout (locked, from idea.txt):** idea.txt
sets the floor "at least 40 gbs vram for bonsai subagents"; the
measured fresh-load headroom here is 37.2 GB, so N = 2 loads ~28.8 GB —
deliberately *below* the floor line, a safe start; N is raised only on
evidence (see the line above). Rollout is phased: build and verify with
**1 instance** (`install.sh --instances 1`, S6.3), then load up to N by
re-running `install.sh` without the flag (or `--instances 3`) — N is
recomputed via S1.4, the YAML rewrites, and the remaining instances
boot through the S1.4 allocation tier (S6.3/S6.5).

**Component inventory.** S1 bonsai2 serving engine (pinned fork; one
docker container per instance under systemd — main-stack parity, S2.2).
S2 lifecycle (systemd, health, budget, failover). S3 harness control
scripts (the main-agent API). S4 harness skill (doctrine). S5 bench
framework. S6 packaging (pinned bonsai image + install.sh; no compose
file in v1, S6.1).

**Non-goals (from idea.txt, locked).** (1) No LLM-to-LLM orchestration
of subagents — no LLM endpoint is ever used to schedule or coordinate
subagents, and subagents never talk to each other; the main agent
*does* orchestrate, exclusively through the deterministic S3.2 bash
commands + the S4 doctrine skill (that is the design; what is banned is
LLMs calling LLMs to coordinate). (2) No new main-model work — the
sglang
qwen stack is external infrastructure this project consumes, never
modifies (S2.1, S6.4). (3) No vision serving (mmproj skipped; text-only
agents). (4) No multi-box fan-out in v1 (1 GB10 box; multi-box is a
bench scenario, not a feature). (5) No fine-tuning / training.
(6) No replacement of the senior-git-1.0 or existing Hermes workflows —
the skill sits alongside them.

**Existing-stack contract (locked; install-time rules in S6.4).** :30001
(`qwen38-sglang` container, systemd-managed, image `qwen38-dflash2:v1.2.3`)
is the main-agent engine.
This project: reads its endpoint from `harness.yaml` (default
`http://localhost:30001`), never restarts/rebuilds it, and treats a
dead :30001 as external failure (harness exit 82), not its own.
Bonsai runs in its own containers (`bonsai2-server@i`, S2.2) — the
main stack and the bonsai pool are separate container families;
install only ever reads from the main container (S6.4).

---

## Section 1 — Subagent inference engine (bonsai2)

### 1.1 Artifacts

| artifact | size | role |
|---|---|---|
| `prism-ml/Ternary-Bonsai-2-27B-gguf` / `Ternary-Bonsai-2-27B-PQ2_0.gguf` (→ disk `bonsai-2-27b-gguf-PQ2_0.gguf`) | 7.21 GB | **v1 default** (fastest decode on Blackwell, per README; best prefill; ~461 t/s context handling) |
| same repo, `Ternary-Bonsai-2-27B-PTQ1_0.gguf` (→ disk `bonsai-2-27b-gguf-PTQ1_0.gguf`) | 5.95 GB | lower-memory fallback (~18% smaller) when N comes out 0 or headroom is tight; slower decode on Blackwell |
| `prism-ml/Ternary-Bonsai-2-27B-mmproj` / `mmproj-Q8_0.gguf` | 0.63 GB | optional, **not loaded in v1** (non-goal 3) |

Pinning: artifact + exact download URL + SHA256 recorded in
`subagent-engine.yaml` at install time (install.sh computes the hash
post-download; a mismatch = install error `ERR_HASH_MISMATCH`, exit 4).
HuggingFace `^` revision pinning in the config: `revision: "main"` in
v1, re-pinned on re-bench (S5.5).

### 1.2 Serving engine (locked — research R-02 complete)

- **Engine:** llama.cpp `llama-server` (OpenAI-compatible), built from the
  prism-ml fork `PrismML-Eng/llama.cpp` at a **pinned commit** recorded in
  `subagent-engine.yaml` (`engine_pin: {repo, commit}`). Upstream
  llama.cpp has no ternary kernels — the fork is the only v1 path
  (confirmed in the model README's compatibility note).
- **Build:** source build for aarch64 GB10 (prebuilt CUDA binaries are
  x86_64-only — verified against the demo repo's `download_binaries.sh`).
  Build flags (per `PrismML-Eng/Bonsai-demo/scripts/build_cuda_linux.sh`
  at pin `9a9394a8`, delta 2026-09-20): `cmake -B build -G Ninja
  -DGGML_CUDA=ON -DCMAKE_BUILD_TYPE=Release
  -DCMAKE_CUDA_ARCHITECTURES="121a"` — the demo's generic multi-arch list
  (`80;86;89;90;[100;]120a`) is replaced by this box's sm_121. The fork's
  ternary kernels compile unconditionally into the build at this commit;
  no `LLAMA_BONSAI_*` cmake toggles exist at `9a9394a8` (verified: zero
  matches in any CMake file at the pin). CUDA arch `121a` = Blackwell
  sm_121 (GB10). Requires driver ≥ 580 (box has 580.126.09 ✓), CUDA ≥ 12.8
  (build container provides 13.0 ✓).
- **Binary location:** `/opt/bonsai2/` on the host (installed from the
  pinned image, S2.2/S6.1); the same binary ships inside
  `bonsai2-server:v1` and the per-instance containers run from the
  image (main-stack parity — the main stack also runs from a pinned
  image under a systemd unit, S2.2). One container per instance.
- **Version policy:** `engine_pin.commit` is bumped only via a PR that
  re-runs the S5 quick bench; no silent upgrades.

### 1.3 Per-instance serving command (locked)

```
/opt/bonsai2/bin/llama-server \
  --model /opt/bonsai2/models/bonsai-2-27b-gguf-PQ2_0.gguf \
  --ctx-size 262144 \
  --parallel 8 \
  --batch-size 2048 \
  --cache-type-k q4_0 \
  --cache-type-v q4_0 \
  --port {port} \
  --api-key bonsai2-local \
  --reasoning-format deepseek \
  --alias "qwen38-27b-subagent"
```

This is the container's `CMD` — each instance runs inside its
`bonsai2-server@i` container (S2.2), host networking (parity with the
main stack's `--network host`), the model baked into the image at
`/opt/bonsai2/models/`, the S2.2 memory cap applied at the container
level (`--memory` + cgroup `MemoryHigh`).

> Pin reality (delta 2026-09-21, S1.7 bring-up, spec follows code):
> at the pinned fork commit the two earlier flags do not exist.
> `--no-prompt` is not a valid `llama-server` argument (the server is
> stateless per request; the demo's launcher never sets it) and
> `--reasoning-format` accepts only `none | auto | deepseek |
> deepseek-legacy` — `deepseek_r1` is rejected at arg-parse. This
> command was measured live on the box and is the working set.
>
> `--cache-type-k/v q4_0` (delta 2026-09-21, S1.4 re-anchor): the KV
> cache dtype is not part of the weights. The default (FP16) KV at
> 8×262144 reserves ~16 GiB, giving a measured footprint of ~24 GiB —
> far above the S1.4 per-instance budget and infeasible at N ≥ 2 on the
> 37.2 GB pool. q4_0 KV (the demo's documented long-context-on-tight-
> memory path, `BONSAI_KV4=1`) measures ~6.4 GiB KV and a total
> footprint of 13.45 GiB (14.44 GB), which is what the re-anchored S1.4
> constant and the N computation are built on. Decode is marginally
> slower than FP16 KV (a memory choice, not a speed one).

- `--ctx-size 262144`: matches the model's 262K hybrid-attention ceiling
  (131072 effective per HF card; 262144 verified to work in the demo —
  the demo's `server.env` uses exactly `LLAMA_ARG_CTX_SIZE=262144`).
  8k is a valid `context` override in `subagent-engine.yaml` for
  memory-constrained installs (KV scales ~linearly, S1.4 formula).
- `--parallel 8`: 8 concurrent slots per instance, matching the demo's
  `LLAMA_ARG_PARALLEL=8`. `max_num_seqs` in `subagent-engine.yaml` is the
  override (1–64, default 8).
- `--cache-type-k/v q4_0`: 4-bit KV cache (the S1.4 re-anchor; see the
  pin-reality note). Without it the KV defaults to FP16 and the instance
  footprint doubles.
- `--reasoning-format deepseek`: content/reasoning split, so the main
  agent can consume `reasoning_content` separately (the pin's
  content/reasoning value; `deepseek-legacy` would instead leave raw
  `think` tags in `message.content`).
- `--alias`: the OpenAI model id. All instances share the alias
  `qwen38-27b-subagent`; harness routes by port (S3.3).
- Ports: `30101 + i` for instance i (0-indexed), overridable per instance
  in `subagent-engine.yaml`. Collision at boot (port in use) =
  `ERR_PORT_IN_USE`, exit 3.

### 1.4 VRAM accounting (formula, locked)

```
instance_footprint_GB = weights_GB + kv_GB
kv_GB = slots * ctx_size * per_slot_per_token_GB
per_slot_per_token_GB = 0.000003277  # measured q4_0 KV on this box (MAP 1.7):
                                     # 6.87 GB KV at 8 slots x 262144 ctx
                                     # (8 x 262144 x 0.000003277 = 6.87 GB).
                                     # S1.3 pins --cache-type-k/v q4_0; the old
                                     # 0.00000176 (3.7 GB KV) was the FP16 /
                                     # community-bench anchor and under-predicted
                                     # the full-ctx footprint on this box.
```

Sanity: 7.21 + 6.87 = 14.08 GB formula footprint ≈ **measured 14.44 GB
(13.45 GiB)** total (incl. ~0.36 GB CUDA ctx/runtime overhead) for the
q4_0 instance, MAP 1.7 @gpu on this box. Per-instance budget in install:
`14.78 GB` (14.08 + 5% margin). `N = floor(budget_for_bonsai / 14.78)`,
clamped to [0, 8] (8 = 64 slots total, the demo's tested ceiling for one
model); S0's default on this box is N = 2 (worked example:
`floor(37.2 / 14.78) = 2`).

**Boot-time OOM policy (two-tier check, actor = the instance manager):**
S2.3's budget (`floor(free_gb / per_instance_budget_gb)`) is a *planning*
tier that decides N' before anything boots. On top of it, before the
manager actually spawns instance i, it runs a *stricter allocation* tier:
`nvidia-smi`/cgroup free ≥ `instance_footprint_GB + 1.0 GB` (the true
footprint + a 1.0 GB spike margin, not the 14.78 GB planning budget).
The free-memory read is bounded: 5 s timeout per probe, one retry; on
a second timeout the tier treats free as 0 for that spawn (the i > 0
partial-load rule applies; the value is re-read on the S2.5 5-min
recheck and on any reload) — a wedged probe must never deadlock the
manager.
- If the allocation tier fails at i > 0: stop at i−1, log
  `WARN_VRAM_SHORTFALL {i, free, need}`, proceed with the partial load,
  exit 0 (a valid state).
- If it fails at i = 0 **while S2.3's planning tier had granted N' ≥ 1**:
  the manager (not the process) exits 4 (`ERR_VRAM_OOM`), zero instances,
  main continues alone. This is the "granted at plan time, short at
  allocation time" case: VRAM was lost *after* the budget ran (a
  surprise, not the planned main-only mode). S2.3's "proceed with N'"
  stands for the planning tier; the allocation tier is the only one that
  can turn a granted N' ≥ 1 into an install-level exit 4.
- When S2.3's planning tier itself yields N' = 0, the manager hands off to
  S2.4 (PTQ1_0 retry, then main-only): exit 0, no `ERR_VRAM_OOM`, unless
  the allocation tier defeats the fallback instance.

### 1.5 Error / edge cases (locked)

| condition | behavior | code |
|---|---|---|
| artifact missing/corrupt (hash mismatch, partial download) | retry download ×3 (10 s backoff); then fail boot of that instance only | `ERR_HASH_MISMATCH` / `ERR_DOWNLOAD`, exit 4 (process), systemd `Restart=on-failure` max 3 → `bfailed` |
| port in use at instance boot | instance skipped, others proceed | `ERR_PORT_IN_USE`, exit 3 |
| instance OOM at load | two observers, two *exclusive* paths, in priority order: (1) the S1.4 allocation tier (pre-allocation gate, run by the manager) catches it first → the manager exits 4 before the instance's KV allocation; (2) only if the tier passed and free memory moved in the gap (concurrent allocation) does the kernel OOM-kill mid-load instead — systemd sees SIGKILL (no process exit code), `Restart=on-failure` ×3 → `bfailed`, harness routes around it | `ERR_VRAM_OOM`, exit 4 (path 1, manager) / unit-failure (path 2, systemd) |
| engine crash mid-request | systemd `Restart=on-failure` (5 s delay); harness sees connection error → task `engine_retried` (one retry, next instance); second failure → task `failed` | `ERR_ENGINE_DOWN` |
| request > ctx-size | engine HTTP 400; harness surfaces `ERR_CTX_TOO_LONG` to main | exit 5 |
| instance unhealthy > 30 s (S2.2 probe: `instance_healthy_timeout_s`) | harness reports `degraded`, the instance is marked `bdown` for routing; the emitted status field is the named condition `ERR_INSTANCE_UNHEALTHY` (no exit code — this is a routing/health state, not a command result) | `ERR_INSTANCE_UNHEALTHY` (status, `bdown`) |

### 1.6 Configuration — `subagent-engine.yaml`

```yaml
# /opt/bonsai2/etc/subagent-engine.yaml (written by install.sh)
engine:
  binary: /opt/bonsai2/bin/llama-server   # as baked into the image below
  pin:
    repo: https://github.com/PrismML-Eng/llama.cpp
    commit: <pinned-sha>          # recorded at install
image:
  pin:
    tag: bonsai2-server:<tag>     # recorded at install (S2.2)
    sha256: <docker-content-digest at install>   # image_pin: {tag, sha256}
model:
  repo: prism-ml/Ternary-Bonsai-2-27B-gguf
  revision: main
  # On-disk names (used by the S1.3 serving command /opt/bonsai2/models/):
  file: bonsai-2-27b-gguf-PQ2_0.gguf
  fallback_file: bonsai-2-27b-gguf-PTQ1_0.gguf   # used when N==0-quant-fallback (S2.4)
  # HuggingFace source objects (the real filenames in the repo, S1.1):
  source_object: Ternary-Bonsai-2-27B-PQ2_0.gguf
  fallback_source_object: Ternary-Bonsai-2-27B-PTQ1_0.gguf
  sha256: <pinned-at-install>
  size_bytes: <bytes>
  fallback_sha256: <pinned-at-install>
  fallback_size_bytes: <bytes>
instances:
  count: 2                  # S0 default on this box (worked example: floor(37.2/14.78) = 2; the 2026-09-21 S1.4 re-anchor dropped N from 3)
  port_base: 30101
  ctx_size: 262144
  max_num_seqs: 8
  per_instance_budget_gb: 14.78   # S1.4: 14.08 GB measured q4_0 footprint + 5% margin
kv_cache: q4_0                   # S1.3 --cache-type-k/v (the S1.4 re-anchor; FP16 default doubles the footprint)
sampling:                          # locked from model card
  temperature: 1.0
  top_p: 0.9
  top_k: 1
  min_p: 0.01
  repeat_penalty: 1.0
  repeat_last_n: 512
  stop_token_ids: [248046, 248044]
reasoning_format: deepseek
alias: qwen38-27b-subagent
api_key: bonsai2-local
```

### 1.7 Testing

- **Unit (hermetic, no GPU):** YAML schema validation of
  `subagent-engine.yaml` (port math, ctx/parallel bounds, sampling ranges);
  the VRAM formula as a pure function (given weights/kv constants → N,
  including N=0 and N>8 clamp cases).
- **Integration (GPU, this box):** cold-load one instance → assert
  `/v1/models` returns `qwen38-27b-subagent`, assert `nvidia-smi` delta
  matches S1.4 formula ±10%. Sustained: 8 concurrent completions
  (max_num_seqs=8) for 120 s → assert t/s ≥ 0.9 × community-baseline
  (29.16) and zero 5xx. VRAM ceiling: load instances until the S1.4
  allocation tier fails → assert the last failure is `ERR_VRAM_OOM`, not a
  segfault, and prior instances stay healthy.
- **Edge:** port collision (start a dummy listener on 30101 → boot →
  expect instance 0 `ERR_PORT_IN_USE`, instance 1 up).

> Pin reality (delta 2026-09-22, S1.7 edge test, spec follows code): at
> the pinned fork commit a bind collision makes the engine exit **1** in
> ~0.2 s ("couldn't bind HTTP server socket … exiting due to HTTP server
> error"), and the bind attempt happens BEFORE `load_model` — a collided
> instance costs no VRAM and never reaches the KV allocation. The S1.5
> `ERR_PORT_IN_USE` exit 3 is the UNIT-LAYER contract: `bonsai2-server@i`'s
> ExecStart is the `docker run` wrapper, and the manager's skip logic
> (S1.5 "instance skipped, others proceed") keys off the unit's reported
> failure, not the engine's raw code. The @gpu edge test
> (`tests/gpu/test_port_collision_1_10.py`, MAP 1.10) asserts the
> container-level half (fast clean exit, no OOMKilled, bind line in the
> log, instance 1 up); the exit-3 mapping itself lands with the S2
> units.

### 1.8 Acceptance criteria (runnable)

1. `install.sh` produces a `subagent-engine.yaml` with a valid model
   SHA256, pinned engine commit, and a pinned image digest
   (`image_pin`); the `llama-server --version` output inside the
   container contains the fork's ternary banner — concretely at pin
   `9a9394a8`: the commit-carrying build line
   `version: 0.2.0-dev (build <N>, commit <engine_pin prefix>)` (the
   pin only exists on the fork's prism branch; the fork has no
   separate ternary banner line) plus the CUDA backend linked
   (`libggml-cuda` resolvable from the binary, S1.2 `GGML_CUDA=ON`).
2. N instances (N per S0 table = 2 on this box; the 2026-09-21 S1.4
   re-anchor dropped it from 3) all healthy within
   120 s of boot; `/v1/models` on each port returns the alias.
3. Concurrent load test passes (t/s ≥ 26.2, zero 5xx, VRAM delta ±10%).
4. Kill of one instance → auto-restart < 15 s; others unaffected.
5. Main endpoint (:30001) t/s within 5% of its recorded pre-project
   baseline while all N instances are at working load (baseline measured
   in S5; 5% = this box's S0 headroom, overridable in `harness.yaml`).

---

## Section 2 — Startup / serving lifecycle

### 2.1 Order (locked)

Main sglang first (pre-existing, `qwen38-sglang` + `qwen38-keepalive`
systemd units — untouched by this project). Bonsai instances after: the
`bonsai2-server@i` units are installed by `install.sh` (S2.2, same
paradigm as the main stack's unit), but only the ones the S1.4
allocation tier grants are started, in order, by the
`bonsai2-sglang.service` manager. Rationale: if the main
stack fails at boot, we don't want N bonsai instances eating ~33 GB
that a main-stack fix might need.

### 2.2 Units (locked)

- `bonsai2-sglang.service` (Type=notify via the instance manager's sd_notify):
  runs `/opt/bonsai2/bin/bonsai2-manager` which: reads
  `subagent-engine.yaml`, runs the S1.4 boot check, spawns instances,
  watches each (restart policy below), emits health JSON on
  `/run/bonsai2/health.json` (schema S3.1).
- `bonsai2-server@%i.service` (persistent template unit; one per
  instance i): runs `docker run` for that instance, **verbatim the same
  configuration shape as the main stack's `qwen38-sglang.service`**
  (user decision 2026-09-20: the main stack is robust and reliable the
  way it is set up — subagents run exactly the same way):

  ```
  docker run --rm --name bonsai2-server-i \
    --gpus all \
    --memory {per_instance_budget_gb}g --memory-swap {per_instance_budget_gb}g \
    --shm-size 4g \
    --network host --ipc=host \
    bonsai2-server:<pinned-tag> \
      /opt/bonsai2/bin/llama-server {S1.3 flags, --port {port_base+i}}
  ```

  The image `bonsai2-server:<tag>` is pinned in `subagent-engine.yaml`
  (`image_pin: {tag, sha256}` — the docker content digest, recorded at
  install, same pinning discipline as the main stack's
  `qwen38-dflash2:v1.2.3`); it bundles the S1.2-built binary + the
  model files + the CUDA runtime. `--network host` → the instance port
  is a host port directly (30101 + i), no publishing. The container's
  memory cap is `--memory` = the S1.4 per-instance budget (the
  unified-memory driver charges the instance's GPU allocations to its
  cgroup, so this bounds the instance's total footprint; `--memory-swap`
  = same value, no swap escape, matching the main stack's pattern).
- Per-instance unit hardening (same class as the main unit's
  `Requires=docker.service`): `ExecStartPre=` `docker rm -f
  bonsai2-server-i` (clean stale container, verbatim the main unit's
  pattern); `Restart=on-failure`, `RestartSec=5`,
  `StartLimitBurst=3` (then `bfailed`), `CPUQuota=64%` (a ceiling ≈
  12.8 of this 20-core box's cores — a bound on one instance's
  worst-case hog, not a reserved allocation; the N instances share the
  20 cores by demand alongside the main stack, overridable). The
  S2.5 drift detector watches sustained over-`--memory` pressure on
  the instance's cgroup as a budget guard (an "over its share"
  signal), not as an exact VRAM meter.
- **Manager ↔ units:** the manager spawns instance i by `systemctl
  start bonsai2-server@i` (the units are installed by `install.sh`,
  not transient — one unit file per instance, same paradigm as the
  main stack's one-unit-per-stack); unload (`bonsai2-unload`, S3.2) =
  `systemctl stop bonsai2-server@i` (+ `docker rm -f`), reload =
  `systemctl start` through the S1.4 allocation tier first. A
  restarted unit after a ctx reduction (S2.5) keeps the same unit
  name/port/model, and `systemctl restart` resets the unit's
  start-limit counter so drift handling itself cannot trip `bfailed`.
- Health probe (per instance, every 10 s, from the manager):
  `GET /v1/models` with Bearer → healthy iff 200 + alias in body
  AND a 16-token completion round-trip < 10 s (catches alive-but-stuck).
  Two consecutive fails → `degraded`; five → restart attempt.

### 2.3 Boot-time budget check (locked)

At manager start: `free_gb = total − main_stack_used_gb − os_reserved_gb`
(where `main_stack_used_gb` is the main container's cgroup
`memory.current`, read live when the container exists — sleep-accurate,
so it reflects the main stack's *current* pool usage rather than the
76.4 GB fresh-load default (the value install recorded, S6.3 step 4) —
else the S2.6
`main_stack.footprint_gb` default 76.4). Then N' = max(0, min(N,
floor(free_gb / per_instance_budget_gb))) — the `max(0, …)` clamps a
negative result (free_gb ≤ 0: main stack + reserved already consume the
pool) to main-only instead of a negative instance count. If 0 < N' < N:
log `WARN_VRAM_SHORTFALL`, proceed with N'. If N' == 0: main-only mode
(S2.4).

**Budgeting vs current (locked):** the S0 table sizes the *default* N
from the fresh-load footprint (76.4 GB) so the pool fits with main
awake. At run time the S1.4 allocation tier is the gate per instance,
so a main stack that happens to be asleep (lower `memory.current`)
simply passes the tier faster — it never *reduces* the default N below
S0's value on this box; N only recomputes at install time (S6.3 step 4)
and on drift (S2.5/S6.4).

### 2.4 N=0 / main-only mode (locked)

**Entry: the S2.3 budget check, after this section's PTQ1_0 retry.**
When the S2.3 formula yields N' = 0 at the PQ2_0 budget, install
retries once with the PTQ1_0
fallback (S1.1, ~18% smaller): recompute S1.4 with its footprint
(5.95 GB weights + the unchanged 6.87 GB q4_0 KV at the same ctx/slots →
12.82 GB formula footprint; +5% margin → ~13.46 GB budget) and
record the quant in use in `subagent-engine.yaml`. Still 0 → main-only.
In main-only mode the
manager stays up, health.json reports `{"instances": 0, "mode":
"main-only", "reason": "budget_zero"}` — `reason` names the cause:
`"budget_zero"` (the S2.3 formula came out 0, possibly after the PTQ1_0
retry) or `"user_unloaded"` (all instances explicitly unloaded via
`bonsai2-unload`, S3.2 — a mode the user asked for, not a budget
outcome). harness scripts operate with all subagent commands
returning exit 80 (S3.4) with a JSON `{"reason": ...}` echoing the
health.json reason. Install still succeeds (`install.sh` exits
0, prints the mode). Rationale: the box is usable; the install is not
a failure just because the main stack is fat that day.

**Fallback boot path (locked — S1.4 cross-ref):** the PTQ1_0 retry can
yield a *granted* fallback instance whose allocation tier still fails
at the default `--ctx-size 262144` (S1.3; KV is the dominant term in a
thin pool). In that case the manager retries that one instance with the
ctx halved (multiple of 8192, floor 8192 — the same reduction as the
S2.5 drift rule) and re-runs the allocation tier on the reduced
footprint; only if that also fails does the S1.4 `ERR_VRAM_OOM` exit 4
apply. The reduced ctx is recorded in `subagent-engine.yaml`
(`instances.ctx_size` for that quant) so restarts stay consistent.

**Live-margin OOM (locked):** a live margin
OOM that defeats an instance the budget granted (N' ≥ 1 at S2.3,
including a fallback-granted one) is the S1.4 `ERR_VRAM_OOM` path,
exit 4 — zero instances but not main-only mode.

### 2.5 Failure policy (locked)

- Instance down: manager restarts (S2.2 limits); harness routes around
  (S3.3); main agent sees `status` show `bfailed`/`bdown` and the skill
  (S4.4) tells it to requeue or run on main.
- Manager down: systemd restarts the manager (max 5/10 min); all
  instances get SIGTERM + respawn.
- Whole-box reboot: systemd starts main (existing units) then
  `bonsai2-sglang.service` (Wants=, not Requires=, so a dead main stack
  doesn't block bonsai — they're independent engines). Target: all healthy
  ≤ 180 s after `multi-user.target` (measured at install; T recorded in
  `serving.yaml`).
- VRAM drift (main stack grows over time): manager re-checks every 5 min;
  if an instance's cgroup memory pressure is sustained (>30 s over its
  `--memory` cap, S2.2), it scales that instance's ctx down: the manager rewrites that
  instance's `--ctx-size` to half (rounded down to a multiple of 8192,
  floor 8192) in its unit's `docker run` flags and restarts that one
  container in place (`systemctl restart bonsai2-server@i`). This is a
  *container*-level restart, not systemd re-plumbing: same unit name,
  same port, same image, and `systemctl restart` resets the unit's
  start-limit counter so the drift handling itself cannot trip
  `bfailed`. Recorded in health.json as `ctx_reduced: true` + the new
  `ctx` value, and counted in that instance's restart budget (global #5).
  No instance is ever *retired* for drift (no pool removal, no N
  change) — only context shrinks. (Context-grow direction: never; v1 is
  conservative.) **Mid-session effect:** a task whose `--ctx` exceeds
  the instance's post-reduction ctx hits `ERR_CTX_TOO_LONG` (exit 5,
  S1.5) when its request lands after the reduction; S4.5's 5-path is
  the recovery flow (split/trim, re-spawn — least-loaded routing may
  place the re-spawn on a different, non-reduced instance). The drift
  event is surfaced in `status` (the instance's `ctx_reduced: true` +
  reduced `ctx`) so the main agent can see it before choosing task ctx.
  The drift watch acts on instances that are *running*;
  it never re-spawns instances the user explicitly unloaded
  (`bunloaded`, S3.2) — an explicit `bonsai2-unload` beats the drift
  watch; reload is only a command (`bonsai2-reload`), a manager restart,
  or a re-run of install.

### 2.6 Configuration — `serving.yaml` (written by install.sh)

```yaml
main_stack:
  container: qwen38-sglang
  endpoint: http://localhost:30001
  footprint_gb: 76.4            # fresh-load footprint (SGLang log); live cgroup read preferred (S2.3)
os_reserved_gb: 8.0
manager:
  binary: /opt/bonsai2/bin/bonsai2-manager
  health_dir: /run/bonsai2
  restart_burst: 3
  health_poll_s: 10
boot_target_s: 180              # measured, not guessed; updated by install test
```

### 2.7 Testing

- Kill -9 one instance process → assert auto-recovery < 15 s, health.json
  transitions `healthy→degraded→healthy`, harness `status` reflects it.
- Reboot test (this box, scratch namespace): `reboot` → both stacks
  healthy ≤ `boot_target_s`; T measured and written back to serving.yaml.
- N=0 test: override main footprint to 120 GB → free_gb < 0, so this
  case exercises the S2.3 `max(0, …)` clamp → install → assert main-only
  mode, harness exit 80s, exit 0 from install.
- Drift test: cgroup-tune main container higher → assert ctx-reduce
  event in health.json with the halved `ctx`, the instance healthy
  again < 15 s, zero restarts on any *other* instance, N unchanged.
- Unload/reload test: `bonsai2-unload --all` → assert VRAM release
  (instance cgroups gone), `status` shows `bunloaded` +
  `main_only_reason: "user_unloaded"`, spawn → exit 80; the S2.5 5-min
  recheck does NOT re-spawn; `bonsai2-reload --all` → healthy again
  within `boot_target_s`.

### 2.8 Acceptance (runnable)

1. Cold boot → main + N healthy ≤ 180 s (recorded T).
2. Single-instance crash → auto-recovered < 15 s; main unaffected;
   harness surfaces the event in `status`.
3. N=0 mode works end-to-end (install exit 0, harness 80s, main-only
   reported).
4. 5-minute drift simulation → ctx-reduce event, instance healthy at
   the halved ctx within 15 s, zero restarts on other instances, N
   unchanged.
5. Unload/reload cycle (S2.7 test): `unload --all` → main-only with
   `main_only_reason: "user_unloaded"`, no auto-reload on the drift
   cycle; `reload --all` → N healthy within `boot_target_s`.

---

## Section 3 — Harness control scripts (the main-agent API)

### 3.1 Output contract (locked)

Every script: **stdout = one JSON object** (machine), **stderr = human
text** (diagnostic only; never parsed). Schemas:

```jsonc
// spawn / steer / cancel / collect
{"task_id": "t-20260919-0001", "state": "queued|running|done|failed|timeout|engine_retried",
 "instance": 2, "port": 30103, "result_file": "/run/bonsai2/results/t-....json",
 "error": null | "ERR_ENGINE_DOWN", "detail": "human text, optional"}
// status
{"stack": "healthy|degraded|main-only", "instances": [
   {"i": 0, "port": 30101, "state": "healthy|degraded|bdown|bfailed|bunloaded",
    "ctx": 262144, "ctx_reduced": false, "slots_in_use": 3, "slots_total": 8}],
 "main_endpoint": "http://localhost:30001", "main_healthy": true,
 "main_only_reason": "budget_zero"   // present when instances: 0 (S2.4)
}
```

Note: `bunloaded` = the user explicitly unloaded that instance
(`bonsai2-unload`); it is not a failure state, routing skips it, and the
drift watch does not re-spawn it (S2.5).

### 3.2 Command surface (locked — the full API; every flag specced)

All under `/opt/bonsai2/bin/`, config from
`/opt/bonsai2/etc/harness.yaml` (env overrides: `BONSAI2_CONFIG`,
`BONSAI2_RESULTS_DIR` — hermetic testing hook):

```
bonsai2-spawn  [-p PROMPT_FILE | -i INLINE_PROMPT] [--tools on|off]
               [--ctx N] [--deadline SECS] [--out FILE] [--priority low|normal|high]
               -> JSON (3.1). Submits to the least-loaded healthy instance.
bonsai2-steer  <TASK_ID> -m MESSAGE [--wait SECS]  # steer-and-wait: block up to SECS for state change
bonsai2-cancel <TASK_ID> [--graceful]              # SIGTERM then SIGKILL after 5 s
bonsai2-status                                   # JSON (3.1)
bonsai2-list-instances                           # human table; exit 0
bonsai2-collect  <TASK_ID>                       # -> JSON; reads result file, returns task JSON + {"answer": ..., "reasoning": ..., "usage": {...}}
bonsai2-bench    [--scenario NAME] [--quick]      # invokes S5 runner (S3.5)
bonsai2-unload   [--all | --instances i[,i,...]]  # manager SIGTERMs the
                                                  # targeted instance units (instant VRAM release);
                                                  # no-op on already-unloaded; JSON {"unloaded": [i...]}
bonsai2-reload   [--all | --instances i[,i,...]]  # re-spawn through the S1.4 allocation
                                                  # tier + health re-probe; JSON {"reloaded": [i...], "state": ...}
bonsai2-health                                   # raw /run/bonsai2/health.json, pretty
```

`bonsai2-unload` / `bonsai2-reload` (pool control):
- Unload: the manager stops the targeted `bonsai2-server@i` units
  (`systemctl stop` → the unit's `docker rm -f`, same signal path as
  `cancel`), releases the VRAM immediately, and marks the instance
  `bunloaded` in health.json. Routing (S3.3) skips `bunloaded`
  instances; with zero running instances the stack enters main-only
  mode with `main_only_reason: "user_unloaded"` (S2.4), so spawn/collect
  return exit 80 while the pool is explicitly off. Idempotent: unload of
  an already-unloaded (or `bdown`/`bfailed`) instance is a no-op for
  that index. `--all` = every non-running index in `instances.count`.
- Reload: re-spawns the targeted indexes through the normal path — S1.4
  allocation tier, S2.2 unit start, S2.2 health probe (healthy ≤
  `boot_target_s`). If the allocation tier fails at a target index,
  that index stays `bunloaded`, the others proceed, and the JSON carries
  `"state": "partial"` + a `WARN_VRAM_SHORTFALL` detail (exit 0; an
  unload/reload that partially succeeds is still a valid state — the
  S1.4 i > 0 partial-load rule, not the i = 0 exit-4 case).
- Both are manager-mediated (like every pool operation); a dead manager
  → exit 84. Neither touches running tasks on other instances;
  in-flight tasks on a target at unload finish their SIGTERM drain (the
  5 s SIGKILL backstop applies, as in `cancel`).

Exit codes (locked, complete set):

| code | meaning | main agent's next action (locked in S4.4) |
|---|---|---|
| 0 | success / queued | proceed; poll `status` or `collect` with deadline |
| 2 | malformed args / bad config | fix the call (human-readable detail on stderr) |
| 3 | port conflict (instance-level) | check `status`; if the instance is still `bdown`, wait one health cycle (30 s) and re-spawn (S4.5); the manager clears the collision on the instance restart (S1.3) |
| 4 | engine-level failure (OOM/download/hash) | check `status`; if instance `bfailed` → requeue on another or run on main |
| 5 | ctx too long for the request | split task or reduce context |
| 8 | deadline expired, task still running | `cancel` + re-spawn smaller, or run on main |
| 20 | task done (collect) | use the result; verify before trusting (S4.2) |
| 21 | task failed (engine answered error/parse fail) | read detail; re-spawn or run on main |
| 22 | task timed out mid-run | `cancel`; re-spawn with tighter scope |
| 80 | no subagent instances (main-only mode) | run on main; note in session log |
| 82 | main endpoint unhealthy | check main stack (external); don't spawn subagent work that needs main |
| 84 | manager itself down | run `systemctl status bonsai2-sglang`; retry in 10 s ×3, then escalate |

Closed set: {0, 2, 3, 4, 5, 8, 20, 21, 22, 80, 82, 84} — 12 codes. Any
other exit code from a S3.2 script is a harness bug; the S3.8 #3 suite
asserts exactly this set, so a stray exit 1 (or any other code) fails
it.

Per-command exit mapping (the table above + these; closed set unchanged):
- `spawn`: 0 = accepted/queued (the task's later terminal state is
  observed by `collect`); 2 = malformed args; 3 = the routing candidate
  is in `ERR_PORT_IN_USE` at submission time (the manager is mid-clear;
  check `status`, wait 30 s, re-spawn — S4.5); 4/5/80/82/84 = stack
  conditions at submission time; **8 = the task is still in the queue
  when `queue_wait_max_s` expires** (S3.3: the task never got a slot, so
  it is not the task's own deadline).
- `steer` / `cancel`: 0 = operation accepted (`steer` on a non-terminal
  task; `cancel` on a running/queued task, signal sent, or an
  already-terminal no-op); 2 = TASK_ID unknown. `steer --wait SECS`:
  8 = no state change within SECS (task still running).
- `collect`: 20/21/22 = the task's terminal state (done / failed /
  timeout); 0 = task non-terminal, current-state JSON returned (caller
  polls); 2 = TASK_ID unknown.
- `unload` / `reload`: 0 = operation done (including the no-op case and
  a partial reload, which reports `"state": "partial"`); 2 = malformed
  args (unknown instance index, both selectors given); 84 = manager
  down (the pool is manager-mediated; S4.5's 84 path applies).

Exit 8 vs 22 (both "deadline-related", different owners): **8** is a
*manager-side wait budget* — the queue wait at `spawn` (S3.3) or the
`steer --wait` window — where the task is still running or still queued.
**22** is the *task's own* `--deadline` (S3.4) expiring mid-run, so the
manager aborts it. A task that never started (queue) → 8; a task that
started but ran past its deadline → 22.
- `status` / `list-instances` / `health`: 0; 82/84 when the probed
  stack condition is bad.
- `bench`: 0/2 per S5.4.

### 3.3 Instance routing (locked — C-03 "i am not sure, which ever one
will be more efficient... you choose")

**Least-loaded, deadline-aware:** pick the healthy instance with the
fewest slots in use; tie-break by lowest i. A task occupies exactly one
slot (its whole lifetime, S3.4). If all slots on all instances are busy:
queue (FIFO, priority preempts: high jumps the queue, low waits) up to
`queue_wait_max_s` (default 60, `harness.yaml`); on expiry → exit 8 with
`state: "queued"` in the JSON. No round-robin (ignores load), no pinned
(stateless model, pinning buys nothing — C-10: "stateless, no memory, no
state" locked).

### 3.4 Slot model (locked)

- Slot = one concurrent completion stream on one instance (1 of
  max_num_seqs). A spawn takes 1 slot on 1 instance for its whole
  lifetime. Steering does not move slots. Cancel frees the slot.
- Deadline (per task, `--deadline`): manager enforces; on expiry the
  instance gets an abort, task → `timeout` (exit 22), slot freed.
- Idempotency: `spawn` whose **dedup key** matches a live task within 24 h
  is a no-op returning the existing task_id. The dedup key is the prompt
  hash (of the `-p` file content or the `-i` inline text) plus the
  `--tools`, `--ctx`, and `--priority` flags — everything that determines
  what the task will do. `--out FILE` is **not** part of the key (it only
  selects where the result file is written), so dedup is well-defined with
  or without `--out`. Keys live in `/run/bonsai2/dedup/` with a 24 h TTL
  (`dedup_ttl_h`). No double slot charge, no re-download (S3.5).

### 3.5 Bench runner wiring (S3.5)

`bonsai2-bench` is a thin wrapper over the S5 runner: it reads
`bench.yaml`, picks the scenario, runs it against the live stack, writes
`/run/bonsai2/bench/<timestamp>_<scenario>.json` + prints the one-line
summary on stdout. Exit code = the runner's (S5.4): 0 even on
scenario-level failure (the verdict is data, S5.2), 2 = bench config
invalid. `--quick` = S5.5's 3-scenario smoke. GPU-exclusivity:
the runner takes the `BONSAI2_BENCH_LOCK` flock so a bench never runs
while another bench is active; it may run alongside normal subagent
traffic (bench traffic is marked `user: "bench"` in instance logs and
excluded from doctrine stats).

### 3.6 Configuration — `harness.yaml`

```yaml
main_endpoint: http://localhost:30001
main_endpoint_healthy_probe: /v1/models
results_dir: /run/bonsai2/results
dedup_dir: /run/bonsai2/dedup
dedup_ttl_h: 24
queue_wait_max_s: 60
instance_healthy_timeout_s: 30
task_default_deadline_s: 600
main_tps_guardrail_pct: 5      # S1.8 #5 / S0 guardrail
# boot_target_s lives in serving.yaml (S2.6), not here: it is a
# boot-lifecycle measurement owned by install/manager
```

### 3.7 Testing (hermetic-first)

- **Mock engine** (no GPU): a Python http.server fixture returning
  canned `/v1/models` + `/v1/chat/completions` (streaming + non-streaming,
  200/400/429/500 variants). Full script surface runs against it:
  every exit code above is produced by a named test case (table-driven,
  one case per code, asserting the exact JSON + code). The subagent mock
  does not itself serve the main endpoint, so the two main-stack codes
  get their own cases: **82** = `spawn`/`status` against a dead/stopped
  main-endpoint fixture (the harness's `main_endpoint_healthy_probe`
  fails → `main_healthy: false`, exit 82); **84** = the same commands
  with the health.json/manager socket absent (`/run/bonsai2` missing) →
  exit 84. Codes 3/4/5/8/20/21/22/80 are produced by the subagent
  mock + the manager stub; 2 by a bad-args case.
- Slot contention: mock with 2 instances × 2 slots; spawn 5 tasks →
  assert 4 running + 1 queued → complete one → assert the queued one
  starts and no slot count exceeds 2 per instance (asserted via a mock
  `slots_in_use` counter the fixture tracks).
- Failure injection: kill mock instance mid-task → assert
  `engine_retried` once on another instance, then `failed` on second
  kill. Malformed response (non-JSON body) → exit 21 with `ERR_PARSE`.
- Idempotency: double-spawn same content-hash → same task_id, one slot.
- Routing: fake `status` payloads with uneven load → assert least-loaded
  pick + priority preemption.
- Pool control: mock manager stub tracks instance state; `unload --all`
  → every index `bunloaded`, `status` reports `main_only_reason:
  "user_unloaded"`, `spawn` → exit 80; unload again → no-op exit 0;
  `reload --all` → healthy; partial case: force the S1.4 allocation
  tier to fail at index 1 → `reload` JSON `"state": "partial"`, exit 0,
  index 1 still `bunloaded`. Bad args (unknown index; both `--all` and
  `--instances`) → exit 2. Dead manager → exit 84.

### 3.8 Acceptance (runnable)

1. Main agent completes a 3-task fan-out using only S3.2 commands:
   3× spawn (parallel) → 3× collect (exit 20 each) → verify results.
2. Mid-task engine kill (kill -9 on a live instance) → task recovered
   via retry or requeue; main agent's only observed commands are from S3.2.
3. All 12 exit codes of the S3.2 closed set exercised by the hermetic
   suite — the S3.7 mock-engine surface (spawn/steer/cancel/collect/
   status) covers every code; `bonsai2-bench` exercises the 0/2 subset
   per S5.4; no code outside the closed set ever emitted (the suite
   asserts the closed set; a stray exit 1 or any other code fails it).
4. N=0 mode: every subagent command → exit 80 with the S2.4 JSON.
5. Unload/reload: `unload --all` → exit 80 pool with the S2.4
   `user_unloaded` JSON; `reload --all` → all N healthy; the mock pool
   cases from S3.7 (no-op, partial, bad-args, dead-manager) all assert
   the exact JSON + code.

---

## Section 4 — Harness skill (the doctrine)

### 4.1 Shape (locked)

A skill named `bonsai2-harness` (standard SKILL.md text skill —
frontmatter + body — installable into Hermes, pi, or any agent runtime
that loads text skills and can run bash; loaded on demand):
- **When to use:** any session where the main agent (this box's qwen3.8
  via :30001, or any agent pointed at `harness.yaml`) may offload work.
- **Commands:** exactly S3.2 (the skill never invents commands).
- **Doctrine:** S4.2–S4.5 below; every claim cites a S5.2 scenario row id
  (e.g. `[M7]`) — a doctrine number without a bench citation is a
  review defect.

### 4.2 Work-allocation table (v1 doctrine — recalibrated by S5, locked
as the starting point per C-02/C-04)

| task shape | allocation | why (bench-cited at activation) |
|---|---|---|
| Multi-step plan / architecture / cross-file refactor coherence | **main** | long-horizon + long-context + tool-chain coherence; subagent lacks the horizon |
| Single-file code-gen chunk (new function / test file / boilerplate) | **subagent** | bounded, self-contained, verify-by-compile [M1] |
| Per-file / per-section analysis (review notes, doc summaries, data transforms) | **fan-out N** | independent units, embarrassingly parallel; N = min(files, instances×slots) [M3] |
| Diff review of a PR-sized diff | **subagent draft → main verifies** | mirrors senior-git-1.0 reviewer/builder split; draft is a self-report (S4.2 rule) |
| Long-context reading of repo docs / transcripts (≥ ~30K tokens) | **main** | 262K ctx on main is free (already loaded); a subagent at the 8k ctx default loses the tail — M5 measures the 64k-ctx subagent cost, which is why the default allocation stays on main [M5] |
| Test execution / lint / build-check | **subagent** | mechanical, parallelizable, output is a report not a decision [M2] |
| Draft writing (docs, summaries, commit messages at scale) | **subagent** | quality bar is lower-verification; main edits the draft |
| Anything requiring live tool feedback loops (iterative debugging, interactive exploration) | **main** | feedback latency; subagent is fire-and-collect, no mid-task steering value here |
| Verification of a subagent's claimed success (file exists? test passed? diff applies?) | **main, always** | self-report rule (4.2); never trust on the word |

**The self-report rule (locked):** every subagent result is treated as an
unverified claim. Before use, main runs the named verification (build,
diff-apply, file-read, re-run test). Verification cost is part of the
task's true cost in S4.4 — a task that costs main 60 s to verify but
saves 90 s on main is not a win; it goes to main.

### 4.3 Session economy (locked — C-04: "keep subagent instances and
sessions in check, we have limited instances, one sglang engine")

- **One engine is shared:** main-agent turns and subagent decodes queue
  on their own engines (main=sglang, sub=llama-server), so the economy
  is about **slots**, not tokens: keep ≤ 6 of 8 slots busy per instance
  (2 free for deadline-sensitive spawns). This is a doctrine cap on the
  main agent's own spawn count, not a harness rule: S3.3's least-loaded
  routing does not enforce it, so an over-subscribed round is handled by
  the `queue_wait_max_s` backstop, not by a queue rejection.
- **Batch spawns:** when fanning out, spawn all N in one turn (one tool
  block), then one `collect` loop — never spawn-collect-spawn-collect
  ping-pong (doubles main's turn count).
- **Short main turns while subagents run:** main's inter-spawn turns
  should not call :30001 with >2K-token prompts (its own prefill queues
  nothing — separate engine — but short turns keep the session cache hot
  and the loop tight).
- **Instance hygiene:** prefer reusing a running task over re-spawning
  when the task is >50% done (steer instead of cancel+spawn); cancel is
  for wrong-shape tasks, not impatience.

### 4.4 Cost doctrine (v1 numbers — calibrated by S5; C-11: "t/s first,
quality becomes apparent from task success")

- Per-task dispatch cost estimate (main-side, for the win/lose call):
  `dispatch_cost = prefill_tokens / t/s_prefill + output_tokens /
  t/s_decode + verification_cost_main_s` (verification is inside
  dispatch_cost, not added twice), with v1 constants from the
  community GB10 bench: `t/s_decode ≈ 29`, `t/s_prefill ≈ 4800`
  (rebenched at S5 on this exact box; numbers here are the a-priori
  floor, not the final values).
- **Win rule:** dispatch iff `dispatch_cost < est_main_time_s(task)`
  (est_main_time_s = the time the same task would take on main,
  including main's own verification of the outcome) AND the task
  matches a S4.2 row that is bench-confirmed. Two consecutive bench-confirmed losses on a task
  shape flip that row to main until re-benched (the doctrine is
  self-correcting, not opinion-locked).
- **Instance cost:** an idle instance costs nothing (no license, no
  clock); the only real cost is VRAM (S1.4) and decode contention with
  the bench (S3.5 lock). No charge-backs in v1.

### 4.5 Failure doctrine (locked, per S3.2 exit code)

- **0/20:** proceed; verify per 4.2.
- **2 (malformed):** fix args; if twice in a row, re-read the skill's
  command reference (skill self-heal: the skill names its own section).
- **3/4:** `status` → if the instance is `bfailed`, requeue elsewhere or
  main; if `bdown`, wait one health cycle (30 s) then requeue.
- **5:** split the task (smaller ctx) or trim input; never raise ctx on a
  per-task basis in v1 (instance-level only, S2.5).
- **8/22 (time):** shrink scope; if the task keeps timing out at 2×
  deadline, it's a main task (move it, note it for S4.2 re-calibration).
- **21 (failed):** read `detail`; re-spawn once with a tightened prompt;
  second failure → main.
- **80:** main-only mode; proceed on main, log the mode (a session note,
  not an error).
- **82:** main endpoint sick — stop spawning, check the main stack
  (`systemctl status qwen38-sglang`), this is external but the session
  depends on it.
- **84:** manager down — `systemctl status bonsai2-sglang`, retry ×3/10 s,
  escalate to the user via the session log.

### 4.6 Testing

- **Doctrine-vs-reality audit:** run the S5 matrix; for each S4.2 row,
  assert the S5.2 scenario row it cites (the `[M#]` id) actually supports
  the allocation (the audit is a script: `bench.yaml` scenario rows ↔
  S4.2 table, citation check +
  win/lose recompute → a `doctrine-audit.json`; a failing audit blocks
  the skill PR).
- **Fresh-session test:** a new main-agent session in any agent runtime
  that loads the S4.1 skill (pi, Hermes, or equivalent — fresh
  session, only the skill + S3.2 scripts available, mock engine) must
  complete the standard workload set (S5.6) with zero undocumented
  commands — asserted by the session transcript's command log.

### 4.7 Acceptance (runnable)

1. Doctrine audit passes: every S4.2 row cites an existing S5.2 scenario
   (an M-id that is in `bench.yaml`); the
   win/lose recompute matches the table (or the table is updated in the
   same PR).
2. Fresh-session standard workload: complete, all results verified per
   4.2, command set ⊆ S3.2.
3. Two synthetic "loss" scenarios (a task shape bench-confirmed as
   main-wins) → the audit flags the stale row.

---

## Section 5 — Bench framework

### 5.1 Scope (locked — C-13: "t/s first, quality becomes apparent from
task success")

The bench measures **both** axes, t/s-primary: (1) raw throughput
(t/s decode, t/s prefill, task wall-time) at the S0 task shapes, and
(2) **task success** with a named rubric per task type (S5.3) — because
per C-02 ("i need to make sure that i can make the subagent more
efficient than the main agent") the whole project's value claim hangs on
the success-rate gap at equal or better cost.

### 5.2 Scenario matrix (locked, from R-08; v1 = M1–M8)

| id | shape | instances | concurrency | ctx | success rubric |
|---|---|---|---|---|---|
| M1 | single-file code-gen (3 tasks) | 1 | 1 | 8k | compiles + passes its own 3-case test suite (auto) |
| M2 | test execution batch (10 tasks) | 1 | 8 | 8k | report JSON parseable + 10/10 files covered (auto) |
| M3 | fan-out doc analysis (N=6 sections) | min(6,avail) | parallel | 8k | each section: summary ≤ 300 tokens + 3 named claims each; main verifies 1/3 by spot-check (rubric: claim accuracy ≥ 2/3) |
| M4 | diff review (1 PR-sized diff) | 1 | 1 | 32k | draft finds ≥ 80% of a seeded 10-bug fixture (auto, seeded) |
| M5 | long-context doc read (60K tokens) | 1 | 1 | 64k | 5 QA questions, each binary-scored by a golden file (auto) |
| M6 | main-only control of M1–M5 | — | 1 | — | same rubrics; the control (idea.txt capability comparison) |
| M7 | saturation: all instances, all slots | N | 8×N | 8k | t/s at the ceiling + zero 5xx (auto) |
| M8 | contention: main at working load + subagents at working load | N | 4×N | 8k | main t/s guardrail: within `main_tps_guardrail_pct` of M6 (auto). The runner records the verdict per scenario row as `pass`/`fail` (e.g. `{"M8": "pass"}`); a guardrail breach flips the row to `fail` while the run still exits 0 (S5.4) |

Reproducible: `bench.yaml` pins model revision + engine commit + box
state snapshot (driver, nvidia-smi, docker stats, /proc/meminfo,
date) recorded per run. The runner captures the snapshot via stdlib
`subprocess` (no extra dep): `nvidia-smi --query-gpu=driver_version,memory.used --format=csv,noheader`,
`docker stats --no-stream --format '{{.Name}} {{.MemUsage}}'` (main
stack container, read-only, per S6.4), and a `MemTotal`/`MemAvailable`
read of `/proc/meminfo`; results are stored as strings in the run
row. Scenario ctx (the `ctx` column) is a runner-enforced token
budget, not a server setting: the runner keeps each scenario's input
+ output within the column's tokens (prompt shaping + a completion
cap), records actual tokens used in the row, and the engine serves at
its boot ctx (S1.3 `--ctx-size`); a scenario that wants the full
budget is a bench v2 item. Bench run JSONs rotate with the same 7-day
window as the results (S3.1): the runner prunes
`/run/bonsai2/bench/*.json` older than 7 days on each start, and
additionally caps the bench dir at 1 GB (keep newest, delete oldest),
so the global #5 disk budget (2 GB) covers them. Results dir:
`/run/bonsai2/bench/` + committed
summary `bench-results/<date>_<commit>.md` (the doctrine's citation
source).

### 5.3 Success rubrics (locked, named)

- **Auto-rubrics (M1, M2, M4, M5, M7, M8):** machine-scored, golden
  files in `bench/goldens/` (the M4 seeded 10-bug diff is versioned in
  the repo — a fixture, not a live diff).
- **Semi-auto (M3):** auto-scored shape (token budget, claim count) +
  main spot-checks 1/3 of sections (claim accuracy); a run's M3 score
  is `auto_score * 0.7 + spotcheck * 0.3`.
- **No LLM-judge in v1** (no judge = no cost, no variance); M3's
  spot-check is the human/main-judgment seam. (An LLM-judge mode is a
  bench v2 item, parked.)

### 5.4 Runner (locked)

`bench/run.py` (Python 3: the dependency surface is exactly stdlib +
`requests` — `requests` is the sole non-stdlib dep and is on the S6.6
pin list; no heavy deps, aarch64-safe; the S5.2 box-state snapshot needs
only stdlib `subprocess` plus the already-required `nvidia-smi` and
`docker` CLIs, S6.2):
scenario loader (bench.yaml), endpoint resolver
(subagent ports from `subagent-engine.yaml`, main from `harness.yaml`),
task executor (streaming chat completions, token timing from stream
timestamps), box-state snapshot capture (S5.2, stdlib `subprocess` +
`/proc/meminfo` reads), scorer (per-rubric plugins in `bench/score/`),
reporter (JSON row + markdown summary). Flock per S3.5. Exit 0 even on
scenario-level failures (the failure is data: each scenario row carries
a `pass`/`fail` verdict, including the M8 guardrail, S5.2); exit 2 =
bench config invalid — the S3.2 code 2 (malformed args / bad config),
in the closed set; `bonsai2-bench` is a S3.2 command and follows the
same exit-code contract.

### 5.5 Re-bench policy (locked — feeds S6.5 upgrade procedure)

- **Quick bench (M1+M2+M7, `--quick`):** run on every engine-pin bump,
  every model re-download with changed SHA, and weekly (cron, optional).
  Regression > 10% t/s or any auto-rubric regression → the PR that
  bumped the pin is blocked until re-resolved.
- **Full bench (M1–M8):** on model revision changes, quant fallback
  switches, or when a S4.2 row flips. Output feeds the doctrine audit
  (S4.6) in the same PR.

### 5.6 Standard workload set (used by S4.6's fresh-session test)

M1 (code-gen), M2 (test batch), M3 (fan-out), plus one M4-style diff
review — the four shapes a real session actually does. A fresh session
completing these with S3.2-only commands + 4.2-verified results = the
skill works.

### 5.7 Configuration — `bench.yaml`

```yaml
scenarios: [M1, M2, M3, M4, M5, M6, M7, M8]
quick: [M1, M2, M7]
repetitions: 3
regression_tps_pct: 10
results_dir: /run/bonsai2/bench
goldens_dir: bench/goldens
main_guardrail_pct: 5          # mirrors harness.yaml main_tps_guardrail_pct
```

### 5.8 Testing

- **Dogfood:** the bench runs M7 (itself a throughput scenario) and
  asserts its own runner produced a parseable JSON row (bench tests the
  bench, per the spec-driven method).
- **Rubric tests:** each scorer plugin has a golden in/out pair (hermetic,
  no endpoints).
- **Config test:** `bench.yaml` schema (scenario ids, reps, dir
  resolvability); invalid scenario id → exit 2.
- **Mock-endpoint run:** full M1+M2 against the S3.7 mock (canned
  streaming responses) → rows produced, timings sane, zero network/GPU.

### 5.9 Acceptance (runnable)

1. One full M1–M8 run on this box, `bench-results/` committed, every row
   carries the box-state snapshot.
2. Every S4.2 doctrine row cites an M-row from that run (audit clean).
3. M8 main-guardrail passes (≤ 5% main-t/s regression at full subagent
   load; asserted from the M8 row's `pass`/`fail` verdict in the
   committed bench JSON, S5.2) — or the S0 table's N is reduced and
   re-asserted.
4. Dogfood M7 row parses; mock-run M1+M2 passes hermetic.

---

## Section 6 — GB10 packaging (one-command install)

### 6.1 Artifact form (locked — main-stack parity: pinned docker image
under systemd)

- `bonsai2-server:<tag>`: the **runtime image** for every subagent
  instance (S2.2). Bundles the S1.2-built `llama-server` binary, the
  model files (S1.1, hash-verified before baking), and the CUDA 13.0
  runtime. It is the bonsai equivalent of the main stack's
  `qwen38-dflash2:v1.2.3` — the user decision 2026-09-20: subagent
  inference runs exactly the way the main agent's does, because that
  configuration is robust and reliable on this box. Pinning: tag +
  docker content digest recorded in `subagent-engine.yaml`
  (`image_pin`, S1.6) at install — same discipline as the main image.
- Build stage: `install.sh` builds the image in a docker build stage
  (aarch64 + CUDA 13.0, the S1.2 toolchain — the same pipeline class
  as the main image's build in the dgx-spark-qwen38 repo) unless a
  prebuilt image with a matching pin is already present locally. The
  fork commit (S1.2) + model SHA (S1.1) are the build inputs; a change
  to either forces a rebuild, recorded in the new image's digest.
- `install.sh` (the one command): build-or-verify the image + record
  its digest, downloads the model with the S1.1 hash check (skipped if
  the baked image already carries the matching SHA), writes the four
  config files (S1.6/S2.6/S3.6/S5.7) from detected box state, installs
  the S2.2 units (manager + one `bonsai2-server@i` per instance),
  starts the manager, runs the S2.7 health checks, prints the
  stack-ready report.
- No compose file in v1: nothing besides the two container families
  (the pre-existing main stack, which install.sh never touches, and the
  bonsai pool) runs in docker, and the bonsai containers are driven by
  systemd units exactly like the main stack's container is.

### 6.2 Prerequisites (verified on this box, R-09 — locked as the
supported matrix)

| prereq | this box | install check |
|---|---|---|
| DGXOS / Ubuntu 24.04.4 LTS, kernel `6.17.0-1021-nvidia` | ✓ | `/etc/os-release` + `uname -r` (warn if not 24.04) |
| aarch64 | ✓ | `uname -m == aarch64` (else `ERR_ARCH`, exit 3) |
| NVIDIA driver ≥ 580 | 580.126.09 ✓ | `nvidia-smi` parses, version ≥ 580 |
| nvidia-container-toolkit | ✓ (main stack uses it) | presence check only (build stage) |
| docker (runtime + build) | 27.5.1 ✓ | `docker version` (runtime: the main stack and the `bonsai2-server@i` containers, S2.2; build: the image stage, S6.1) |
| unified memory ≥ 96 GiB | 121.6 GiB ✓ | `/proc/meminfo` MemTotal ≥ 103 GB decimal (= 96 GiB; the S0 table and this column use GiB). **Warn, not fail:** at/above 96 GiB the install proceeds normally; below 96 GiB it logs `WARN_MEMORY_BELOW_MATRIX` and still computes N via S1.4/S2.3 (a ~96 GiB box with the 76.4 GB main stack computes N = 1; below ~95.9 GiB N comes out 0 → main-only mode (S2.4)), still exit 0 |
| python 3.12 (bench runner) | 3.12.3 ✓ | `python3 --version` ≥ 3.10 |
| free disk ≥ 20 GB | ~100 GB ✓ | `df /opt` ≥ 20 GB — a floor, not an expectation: a clean install writes ~8 GB (model + binary + configs), the build stage peaks higher; ~100 GB free is normal on a fresh GB10 |

### 6.3 The one command (locked, ordered)

```
sudo ./install.sh          # idempotent; see S6.4
```

Steps: (1) prereq checks (S6.2 table; a **hard-fail** row → named ERR,
exit ≠ 0, nothing installed. Rows marked "warn" in S6.2 — currently the
unified-memory row — proceed with a logged warning and are not a
failure). (2) build-or-verify the bonsai image and record its digest
(S1.6 `image_pin`; skip the build if a local image matches the pin —
idempotency). (3) download model
(S1.1, skip if SHA matches). (4) detect the main-stack fresh-load
footprint: read the main container's SGLang startup log
(`docker logs qwen38-sglang`, `available_gpu_mem` + pool size lines,
read-only) and compute pool − available; if the log is unavailable,
fall back to the main container's cgroup `memory.current` while the
main stack is awake; if the main stack is dead, fall back to the
S2.6 default 76.4 GB, log `WARN_MAIN_STACK_DEAD`, install continues
per S6.4's main-only fallback. Record the detected value in
`serving.yaml` as `main_stack.footprint_gb` (the drift-watch baseline,
S6.4). `--instances N` overrides `instances.count` for this install
(the S0 phased-rollout flag: `--instances 1` to build/verify, then
re-run without it, or `--instances 3`, to load to default N). Compute
N via S1.4 → write the four
YAMLs. (5) install units (S2.2), start manager. (6) health checks
(S2.7 probes, 180 s budget) — in main-only mode (S2.4) these cover the
main endpoint only; in a partial load (S1.4 `WARN_VRAM_SHORTFALL` at
i > 0) they cover the loaded instances. (7) print the stack-ready
report (JSON + human table: N instances, ports, t/s guardrail, config
paths, the command surface summary). Exit 0 on full or main-only (S2.4)
success; exit 4 on live-margin OOM (S1.4: the budget granted N' ≥ 1 but
instance 0 could not boot); exit 3 on arch mismatch (a port collision
at install is not a failure: that instance is skipped per S1.5, others
proceed); exit 2 on config.

### 6.4 Idempotency + main-stack contract (locked)

- Re-run: image skipped if the recorded digest matches the local image;
  model skipped if SHA-matched; YAMLs
  regenerated only if detected state changed (N changed, main fresh-load
  footprint (the recorded `main_stack.footprint_gb`, S6.3 step 4)
  drifted > 5 GB — a policy constant, set below the per-instance budget
  of 14.78 GB so a drift this size can already change N; the S6.7
  +10 GB test exercises it) — else `--force-config` to regenerate; units
  re-installed without restart if unchanged (`systemctl daemon-reload`
  only on diff).
- **Never touches the main stack:** no `docker` command against
  `qwen38-sglang` except `logs` / `stats` / `inspect` / cgroup reads
  (all read-only). The main stack dies? install continues (main-only
  fallback), and the report says so.

### 6.5 Upgrade procedure (locked — C-06: "whenever a new version of
the main agent or subagent model engine is released")

- **Main stack (qwen38-dflash2 / sglang):** owned by the dgx-spark-qwen38
  repo, NOT this one. This project's upgrade path: a PR in
  dgx-spark-qwen38 (their lane) → after their image is up, this project
  re-runs `install.sh --refresh-main` (re-detects footprint, recomputes
  N, re-runs quick bench M1/M2/M7). No in-place main-stack surgery here.
- **Subagent engine (llama.cpp fork pin):** PR bumps
  `engine_pin.commit` → the docker build stage recompiles the fork into
  a new `bonsai2-server` image → `install.sh` picks up the new image
  (S6.4 step 2, digest re-recorded) → **quick bench gates the PR**
  (S5.5,
  >10% t/s regression or auto-rubric regression = PR blocked).
- **Subagent model (revision/quant):** same PR flow; full bench (S5.5)
  when the quant or revision changes; the doctrine audit (S4.6) re-runs
  in the same PR.
- **Raising N (pool expansion, S0 default N = 2 is a safe start):**
  after a full M1–M8 bench pass with M8 guardrail green (S5.9 #3) and
  headroom to spare, re-run `install.sh --instances N'` (S6.3 step 4)
  with N' recomputed from the live S1.4 budget; the new instances boot
  through the S1.4 allocation tier as usual. A PR that raises N must
  carry the fresh M8 row (no guardrail regression) or it is blocked,
  exactly like an engine-pin bump.
- **Rollback:** `install.sh --rollback <previous-state-file>` (state
  files: `/opt/bonsai2/etc/state-<timestamp>.yaml`, one per install,
  last 5 kept) — restores configs + restarts the manager; binary/model
  rollback = re-point the pin (old artifacts stay cached in
  `/opt/bonsai2/cache`).

### 6.6 aarch64 pin list (locked — ARM64-only rule, every dep has an
aarch64 build)

- Python 3.12 (system), `requests` (wheel: aarch64 ✓), cmake + ninja +
  CUDA 13.0 toolkit (image build stage, aarch64 ✓ — same toolchain the
  community GB10 bench used).
- No golang/JS at runtime (scripts are bash + the Python bench runner
  only). The four YAMLs are the only runtime state.

### 6.7 Testing

- **Clean-box test:** this box, scratch namespace (`/opt/bonsai2-<ts>`):
  full S6.3 flow, then the S2.8 + S3.8 acceptances end-to-end, then
  reinstall-over. This exercises S6.4's no-change branch specifically:
  the second run on the same box with no drift must report "unchanged"
  (zero restarts, zero downloads, no YAML rewrite). The drift branch
  (S6.4 step 4: N changed or footprint drifted > 5 GB → regenerate YAMLs)
  is the separate S6.7 drift test below.
- **Drift test:** main-stack footprint +10 GB between two installs →
  N recomputes down, report says why.
- **Prereq-fail tests:** each S6.2 **hard-fail** row faked-failed (fake
  uname, fake driver, ...) → the named ERR, exit ≠ 0, nothing in
  `/opt/bonsai2` created (atomic: check-all before touch-any). The one
  warn row (unified memory) faked-low → `WARN_MEMORY_BELOW_MATRIX`,
  exit 0, install continues (N recomputed per S1.4/S2.3).

### 6.8 Acceptance (runnable)

1. One command on a fresh GB10 box → stack-ready report, all S2.8
   checks pass, N auto-computed and correct per S1.4.
2. Re-run → "unchanged" (zero restarts, zero downloads).
3. Rollback to the previous state file → stack healthy at the previous N.
4. All S6.2 hard-fail prereq cases emit their named ERR with exit ≠ 0
   and a clean filesystem; the warn row (unified memory) emits
   `WARN_MEMORY_BELOW_MATRIX` and continues (exit 0).

---

## Global testing discipline

- Hermetic where possible: S3.7 (scripts vs mock engine), S5.8 (scorers
  vs goldens, bench vs mock), S1.7 unit half, S6.7 prereq fakes — no GPU,
  no network, deterministic.
- GPU-touching tests are labeled (`@gpu`), never run in parallel with a
  live review/subagent session (one-box constraint), and always preceded
  by a `nvidia-smi` snapshot in the test log.
- Every test asserts against a named acceptance criterion above — no
  vibes, no "should be faster".
- The doctrine audit (S4.6) is the cross-section invariant: skill claims
  ↔ bench rows ↔ script exit codes, one PR, one audit.

## Global acceptance criteria (project-level, runnable)

1. Cold boot → main + N bonsai instances healthy ≤ 180 s (S2.8).
2. A fresh main-agent session completes the standard workload set
   (S5.6) using only S3.2 commands, every result verified per S4.2
   (S4.7).
3. Full M1–M8 bench run committed; every S4.2 row cites a bench row;
   M8 main-guardrail ≤ 5% (S5.9).
4. One-command install on a fresh GB10 box: S6.8 all four.
5. 72 h soak: all N instances up (restart count per instance ≤ 2), no
   main-stack t/s regression beyond the 5% guardrail, `/run/bonsai2`
   disk < 2 GB (results rotation: keep 7 days, S3.1 results_dir; bench
   run JSONs: 7 days, S5.2).

## Out of scope (locked, from S0 non-goals)

LLM-to-LLM orchestration of subagents (the main agent orchestrates via
S3.2 bash + the S4 skill — that is the design; LLMs calling LLMs to
coordinate is what's out), main-stack changes, vision (mmproj),
multi-box v1,
fine-tuning, LLM-judge bench mode, instance kill-for-drift, stateful
subagents.
