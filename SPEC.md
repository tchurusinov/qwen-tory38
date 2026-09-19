---
title: qwen-tory38 — SPEC
status: proposed
supersedes: null
last_reviewed: 2026-09-19
method: spec-driven vibe coding (docs/transcripts/spec-driven-vibe-coding-in-action_1Fl94dPn2mU.md)
sources: idea.txt, docs/transcripts/*.md (3 videos), open-questions.md (locked 2026-09-19, deleted post-spec)
---

# qwen-tory38 — SPEC

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

**VRAM budget (this box, 2026-09-19 measurements — recompute at install):**

| component | footprint | source |
|---|---|---|
| qwen3.8-27b NVFP4 weights | ~27.9 GB | SGLang `weights_gb` at startup log |
| sglang KV pool (262144 ctx cap) | ~13.5 GB | SGLang `total_gpu_memory_gb=48.7` − weights |
| flash draft (qwen3.8-0.8B-NVFP4-dflash) | ~1.5 GB | image config |
| qwen38-sglang container overhead | ~3.5 GB | image `--memory` 10 GB − 48.7 GB device use, observed |
| **main stack total** | **~46.9 GB** | `docker stats qwen38-sglang` |
| per bonsai2 instance (PQ2_0, 262K ctx, 8 slots) | ~11.0–11.5 GB | 7.21 GB weights + 3.7–4.2 GB KV (community GB10 bench, same arch) |
| OS + system reserved | 12.0 GB | install-time policy, overridable |
| **install-time budget for bonsai** | **~62.7 GB** | 121.6 − 46.9 − 12.0 |

`N = floor(budget_for_bonsai / per_instance_footprint)` with the worked
example: `floor(62.7 / 11.5) = 5` instances, ~54.5 GB loaded (5 × 10.91),
~8 GB headroom. N=0 is a valid result (main-only mode; see S2.4).

**Unit convention (locked):** the 121.6 total is `/proc/meminfo`
(GiB); the main-stack ~46.9 and the 12.0 reserved are measured against
the same unified pool and subtracted in the same effective units, giving
the working budget `121.6 − 46.9 − 12.0 ≈ 62.7`. The residual GiB/GB
difference between `/proc/meminfo` and the tool-reported footprints is
inside the per-instance 5% margin (S1.4), so it does not change N on
this box; on other boxes the install recomputes from live `nvidia-smi`/
`/proc/meminfo` rather than this table.

**Component inventory.** S1 bonsai2 serving engine (pinned fork, one
process per instance). S2 lifecycle (systemd, health, budget, failover).
S3 harness control scripts (the main-agent API). S4 harness skill
(doctrine). S5 bench framework. S6 packaging (build image + install.sh;
no compose file in v1, S6.1).

**Non-goals (from idea.txt, locked).** (1) No LLM-orchestration of
subagents — only bash scripts. (2) No new main-model work — the sglang
qwen stack is external infrastructure this project consumes, never
modifies (S2.1, S6.4). (3) No vision serving (mmproj skipped; text-only
agents). (4) No multi-box fan-out in v1 (1 GB10 box; multi-box is a
bench scenario, not a feature). (5) No fine-tuning / training.
(6) No replacement of the senior-git-1.0 or existing Hermes workflows —
the skill sits alongside them.

**Existing-stack contract (S6.4).** :30001 (`qwen38-sglang` container,
systemd-managed, image `qwen38-dflash2:v1.2.3`) is the main-agent engine.
This project: reads its endpoint from `harness.yaml` (default
`http://localhost:30001`), never restarts/rebuilds it, and treats a
dead :30001 as external failure (harness exit 82), not its own.

---

## Section 1 — Subagent inference engine (bonsai2)

### 1.1 Artifacts

| artifact | size | role |
|---|---|---|
| `prism-ml/Ternary-Bonsai-2-27B-gguf` / `bonsai-2-27b-gguf-PQ2_0.gguf` | 7.21 GB | **v1 default** (fastest decode on Blackwell, per README; best prefill; ~461 t/s context handling) |
| same repo, `...-PTQ1_0.gguf` | 5.95 GB | lower-memory fallback (~18% smaller) when N comes out 0 or headroom is tight; slower decode on Blackwell |
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
  Build flags (from `PrismML-Eng/Bonsai-demo/scripts/build_cuda_linux.sh`,
  adapted): `cmake -B build -DGGML_CUDA=ON -DLLAMA_CURL=ON -DGGML_CUDA_F16=ON
  -DCMAKE_BUILD_TYPE=Release -DLLAMA_ACCELERATE=OFF
  -DCUDA_ARCH_NATIVE=OFF -DCUDA_ARCH_LIST="121a"` with the fork's
  ternary kernel toggles `LLAMA_BONSAI_LLAMA_CPP=ON`,
  `LLAMA_BONSAI_TERNARY_KERNELS_LLAMA_CPP=ON`,
  `LLAMA_BONSAI_TERNARY_KERNELS_CUDA_LLAMA_CPP=ON`. CUDA arch `121a` =
  Blackwell sm_121 (GB10). Requires driver ≥ 580 (box has 580.126.09 ✓),
  CUDA ≥ 12.8 (build container provides 13.0 ✓).
- **Binary location:** `/opt/bonsai2/bin/llama-server` inside the
  subagent image; one process per instance.
- **Version policy:** `engine_pin.commit` is bumped only via a PR that
  re-runs the S5 quick bench; no silent upgrades.

### 1.3 Per-instance serving command (locked)

```
/opt/bonsai2/bin/llama-server \
  --model /opt/bonsai2/models/bonsai-2-27b-gguf-PQ2_0.gguf \
  --ctx-size 262144 \
  --parallel 8 \
  --batch-size 2048 \
  --no-prompt \
  --port {port} \
  --api-key bonsai2-local \
  --reasoning-format deepseek_r1 \
  --alias "qwen38-27b-subagent"
```

- `--ctx-size 262144`: matches the model's 262K hybrid-attention ceiling
  (131072 effective per HF card; 262144 verified to work in the demo —
  the demo's `server.env` uses exactly `LLAMA_ARG_CTX_SIZE=262144`).
  8k is a valid `context` override in `subagent-engine.yaml` for
  memory-constrained installs (KV scales ~linearly, S1.4 formula).
- `--parallel 8`: 8 concurrent slots per instance, matching the demo's
  `LLAMA_ARG_PARALLEL=8`. `max_num_seqs` in `subagent-engine.yaml` is the
  override (1–64, default 8).
- `--reasoning-format deepseek_r1`: content/reasoning split, so the main
  agent can consume `reasoning_content` separately (the demo's exact
  choice).
- `--alias`: the OpenAI model id. All instances share the alias
  `qwen38-27b-subagent`; harness routes by port (S3.3).
- Ports: `30101 + i` for instance i (0-indexed), overridable per instance
  in `subagent-engine.yaml`. Collision at boot (port in use) =
  `ERR_PORT_IN_USE`, exit 3.

### 1.4 VRAM accounting (formula, locked)

```
instance_footprint_GB = weights_GB + kv_GB
kv_GB = slots * ctx_size * per_slot_per_token_GB
per_slot_per_token_GB = 0.00000176   # derived from the community GB10 bench
                                      # anchor: 3.7 GB KV at 8 slots x 262144 ctx
                                      # (8 x 262144 x 0.00000176 = 3.69 GB)
```

Sanity: 7.21 + 3.7 = 10.91 GB ≈ measured 10.8 GB in the community GB10
bench (bench reports 29.16 t/s decode @ ~4800 t/s prefill, same box
class, CUDA arch 121a). Per-instance budget in install: `11.5 GB`
(10.91 + 5% margin). `N = floor(budget_for_bonsai / 11.5)`, clamped to
[0, 8] (8 = 64 slots total, the demo's tested ceiling for one model).

**Boot-time OOM policy:** before spawning instance i, the lifecycle
(S2.3) checks `nvidia-smi`/cgroup free ≥ `instance_footprint_GB + 1.0 GB`
margin. If the check fails at i > 0: stop at i−1, log
`WARN_VRAM_SHORTFALL {i, free, need}`, proceed with the partial load,
exit 0 (a valid state). If it fails at i = 0 **while the S2.3 budget had
granted N' ≥ 1**: `ERR_VRAM_OOM`, manager exit 4, zero instances, main
continues alone — VRAM was lost *after* the budget ran (a surprise, not
the planned main-only mode). When S2.3 itself yields N' = 0, S2.4 takes
over (PTQ1_0 retry, then main-only): exit 0, no `ERR_VRAM_OOM`, unless
a live margin OOM defeats the fallback instance.

### 1.5 Error / edge cases (locked)

| condition | behavior | code |
|---|---|---|
| artifact missing/corrupt (hash mismatch, partial download) | retry download ×3 (10 s backoff); then fail boot of that instance only | `ERR_HASH_MISMATCH` / `ERR_DOWNLOAD`, exit 4 (process), systemd `Restart=on-failure` max 3 → `bfailed` |
| port in use at instance boot | instance skipped, others proceed | `ERR_PORT_IN_USE`, exit 3 |
| instance OOM at load | two observers, two *exclusive* paths, in priority order: (1) the S1.4 margin check is the pre-allocation gate and catches it first → the process exits 4 itself before the KV allocation; (2) only if the check passed and free memory moved in the gap (concurrent allocation) does the kernel OOM-kill mid-load instead — systemd sees SIGKILL (no process exit code), `Restart=on-failure` ×3 → `bfailed`, harness routes around it | `ERR_VRAM_OOM`, exit 4 (path 1, manager-observed) / unit-failure (path 2, systemd-observed) |
| engine crash mid-request | systemd `Restart=on-failure` (5 s delay); harness sees connection error → task `engine_retried` (one retry, next instance); second failure → task `failed` | `ERR_ENGINE_DOWN` |
| request > ctx-size | engine HTTP 400; harness surfaces `ERR_CTX_TOO_LONG` to main | exit 5 |
| instance unhealthy > 30 s | harness reports `degraded` status; instance marked `bdown` for routing | — |

### 1.6 Configuration — `subagent-engine.yaml`

```yaml
# /opt/bonsai2/etc/subagent-engine.yaml (written by install.sh)
engine:
  binary: /opt/bonsai2/bin/llama-server
  pin:
    repo: https://github.com/PrismML-Eng/llama.cpp
    commit: <pinned-sha>          # recorded at install
model:
  repo: prism-ml/Ternary-Bonsai-2-27B-gguf
  revision: main
  file: bonsai-2-27b-gguf-PQ2_0.gguf
  sha256: <computed-at-install>
  fallback_file: bonsai-2-27b-gguf-PTQ1_0.gguf   # used when N==0-quant-fallback (S2.4)
instances:
  count: 5
  port_base: 30101
  ctx_size: 262144
  max_num_seqs: 8
  per_instance_budget_gb: 11.5
sampling:                          # locked from model card
  temperature: 1.0
  top_p: 0.9
  top_k: 1
  min_p: 0.01
  repeat_penalty: 1.0
  repeat_last_n: 512
  stop_token_ids: [248046, 248044]
reasoning_format: deepseek_r1
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
  margin check fails → assert the last failure is `ERR_VRAM_OOM`, not a
  segfault, and prior instances stay healthy.
- **Edge:** port collision (start a dummy listener on 30101 → boot →
  expect instance 0 `ERR_PORT_IN_USE`, instance 1 up).

### 1.8 Acceptance criteria (runnable)

1. `install.sh` produces a `subagent-engine.yaml` with a valid SHA256 and
   pinned commit; `llama-server --version` output contains the fork's
   ternary banner.
2. N instances (N per S0 table = 5 on this box) all healthy within
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
systemd units — untouched by this project). Bonsai instances after: a
single `bonsai2-sglang.service` unit spawns the instance manager, which
spawns N `bonsai2-llama-server@i` transient units (systemd template
unit), in order, gated by the S1.4 margin check. Rationale: if the main
stack fails at boot, we don't want N bonsai instances eating 57 GB that
a main-stack fix might need.

### 2.2 Units (locked)

- `bonsai2-sglang.service` (Type=notify via the instance manager's sd_notify):
  runs `/opt/bonsai2/bin/bonsai2-manager` which: reads
  `subagent-engine.yaml`, runs the S1.4 boot check, spawns instances,
  watches each (restart policy below), emits health JSON on
  `/run/bonsai2/health.json` (schema S3.1).
- `bonsai2-llama-server@%i.service` (transient, started by the manager):
  `ExecStart=` the S1.3 command with `--port {port_base+i}`;
  `Restart=on-failure`, `RestartSec=5`, `StartLimitBurst=3` (then
  `bfailed`), `CPUQuota=64%` (a ceiling ≈ 12.8 of this 20-core box's
  cores — a bound on one instance's worst-case hog, not a reserved
  allocation; the N instances share the 20 cores by demand alongside
  the main stack, overridable), no `MemoryMax` (unified memory; cgroup v2
  `MemoryHigh` = per_instance_budget_gb). On this box's unified memory
  (S0), the instance's weights + KV live in the same LPDDR5X pool as
  the host: the unified-memory driver charges the instance's GPU
  allocations to its cgroup, so `MemoryHigh` bounds the instance's total
  unified-memory footprint. The S2.5 drift detector watches sustained
  over-`MemoryHigh` pressure as a budget guard (an "over its share"
  signal), not as an exact VRAM meter.
- **No docker for bonsai in v1** (C-05 locked: "i prefer this way, and
  its more efficient, and less of a risk to have it outside of docker"):
  the image builds the binary (S1.2), the install extracts binary+model
  to `/opt/bonsai2/`, systemd runs them. The main stack stays docker;
  two paradigms coexist deliberately — bonsai is lightweight
  (binary+file), main is a heavyweight pinned env (image).
- Health probe (per instance, every 10 s, from the manager):
  `GET /v1/models` with Bearer → healthy iff 200 + alias in body
  AND a 16-token completion round-trip < 10 s (catches alive-but-stuck).
  Two consecutive fails → `degraded`; five → restart attempt.

### 2.3 Boot-time budget check (locked)

At manager start: `free_gb = total − main_stack_used_gb − os_reserved_gb`
(where `main_stack_used_gb` is read live from `docker stats --no-stream
qwen38-sglang` when that container exists, else the S2.6
`main_stack.footprint_gb` default 46.9). Then N' = max(0, min(N,
floor(free_gb / per_instance_budget_gb))) — the `max(0, …)` clamps a
negative result (free_gb ≤ 0: main stack + reserved already consume the
pool) to main-only instead of a negative instance count. If 0 < N' < N:
log `WARN_VRAM_SHORTFALL`, proceed with N'. If N' == 0: main-only mode
(S2.4).

### 2.4 N=0 / main-only mode (locked)

**Entry: the S2.3 budget check only.** When the S2.3 formula yields
N' = 0 at the PQ2_0 budget, install retries once with the PTQ1_0
fallback (S1.1, ~18% smaller): recompute S1.4 with its footprint
(5.95 GB weights + the unchanged 3.7 GB KV at the same ctx/slots →
9.65 GB footprint; +5% margin → ~10.2 GB budget) and
record the quant in use in `subagent-engine.yaml`. Still 0 → main-only.
In main-only mode the
manager stays up, health.json reports `{"instances": 0, "mode":
"main-only"}`, harness scripts operate with all subagent commands
returning exit 80 (S3.4) with a JSON `{"reason":
"no_subagent_instances"}`. Install still succeeds (`install.sh` exits
0, prints the mode). Rationale: the box is usable; the install is not
a failure just because the main stack is fat that day. A live margin
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
  if an instance's `MemoryHigh` cgroup is thrashing (>30 s over
  high), it scales that instance's ctx down: the manager rewrites that
  instance's `--ctx-size` to half (rounded down to a multiple of 8192,
  floor 8192) and restarts that one process in place via its transient
  unit (`systemctl restart bonsai2-llama-server@i`). This is a
  *process*-level restart, not systemd re-plumbing: same unit name,
  same port, same model file, and `systemctl restart` resets the unit's
  start-limit counter so the drift handling itself cannot trip
  `bfailed`. Recorded in health.json as `ctx_reduced: true` + the new
  `ctx` value, and counted in that instance's restart budget (global #5).
  No instance is ever *retired* for drift (no pool removal, no N
  change) — only context shrinks. (Context-grow direction: never; v1 is
  conservative.)

### 2.6 Configuration — `serving.yaml` (written by install.sh)

```yaml
main_stack:
  container: qwen38-sglang
  endpoint: http://localhost:30001
  footprint_gb: 46.9            # live-read preferred; this is the fallback
os_reserved_gb: 12.0
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
- N=0 test: override main footprint to 110 GB → free_gb < 0, so this
  case exercises the S2.3 `max(0, …)` clamp → install → assert main-only
  mode, harness exit 80s, exit 0 from install.
- Drift test: cgroup-tune main container higher → assert ctx-reduce
  event in health.json with the halved `ctx`, the instance healthy
  again < 15 s, zero restarts on any *other* instance, N unchanged.

### 2.8 Acceptance (runnable)

1. Cold boot → main + N healthy ≤ 180 s (recorded T).
2. Single-instance crash → auto-recovered < 15 s; main unaffected;
   harness surfaces the event in `status`.
3. N=0 mode works end-to-end (install exit 0, harness 80s, main-only
   reported).
4. 5-minute drift simulation → ctx-reduce event, instance healthy at
   the halved ctx within 15 s, zero restarts on other instances, N
   unchanged.

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
   {"i": 0, "port": 30101, "state": "healthy|degraded|bdown|bfailed",
    "ctx": 262144, "ctx_reduced": false, "slots_in_use": 3, "slots_total": 8}],
 "main_endpoint": "http://localhost:30001", "main_healthy": true}
```

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
bonsai2-health                                   # raw /run/bonsai2/health.json, pretty
```

Exit codes (locked, complete set):

| code | meaning | main agent's next action (locked in S4.4) |
|---|---|---|
| 0 | success / queued | proceed; poll `status` or `collect` with deadline |
| 2 | malformed args / bad config | fix the call (human-readable detail on stderr) |
| 3 | port conflict (instance-level) | check `status`; usually self-heals after restart |
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
  observed by `collect`); 2 = malformed args; 3/4/5/80/82/84 = stack
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
summary on stdout. `--quick` = S5.5's 3-scenario smoke. GPU-exclusivity:
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
  one case per code, asserting the exact JSON + code).
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

### 3.8 Acceptance (runnable)

1. Main agent completes a 3-task fan-out using only S3.2 commands:
   3× spawn (parallel) → 3× collect (exit 20 each) → verify results.
2. Mid-task engine kill (kill -9 on a live instance) → task recovered
   via retry or requeue; main agent's only observed commands are from S3.2.
3. All 12 exit codes of the S3.2 closed set exercised by the hermetic
   suite; no code outside the closed set ever emitted (the suite asserts
   the closed set; a stray exit 1 or any other code fails it).
4. N=0 mode: every subagent command → exit 80 with the S2.4 JSON.

---

## Section 4 — Harness skill (the doctrine)

### 4.1 Shape (locked)

A skill named `bonsai2-harness` (Hermes skill dir, loaded on demand):
- **When to use:** any session where the main agent (this box's qwen3.8
  via :30001, or any agent pointed at `harness.yaml`) may offload work.
- **Commands:** exactly S3.2 (the skill never invents commands).
- **Doctrine:** S4.2–S4.5 below; every claim cites a S5 bench row id
  (e.g. `[B-07]`) — a doctrine number without a bench citation is a
  review defect.

### 4.2 Work-allocation table (v1 doctrine — recalibrated by S5, locked
as the starting point per C-02/C-04)

| task shape | allocation | why (bench-cited at activation) |
|---|---|---|
| Multi-step plan / architecture / cross-file refactor coherence | **main** | long-horizon + long-context + tool-chain coherence; subagent lacks the horizon |
| Single-file code-gen chunk (new function / test file / boilerplate) | **subagent** | bounded, self-contained, verify-by-compile [B-01] |
| Per-file / per-section analysis (review notes, doc summaries, data transforms) | **fan-out N** | independent units, embarrassingly parallel; N = min(files, instances×slots) [B-03] |
| Diff review of a PR-sized diff | **subagent draft → main verifies** | mirrors senior-git-1.0 reviewer/builder split; draft is a self-report (S4.2 rule) |
| Long-context reading of repo docs / transcripts (≥ ~30K tokens) | **main** | 262K ctx on main is free (already loaded); a subagent at the 8k ctx default loses the tail — M5 measures the 64k-ctx subagent cost, which is why the default allocation stays on main [B-05] |
| Test execution / lint / build-check | **subagent** | mechanical, parallelizable, output is a report not a decision [B-02] |
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
  assert the bench row it cites actually supports the allocation (the
  audit is a script: `bench.yaml` rows ↔ S4.2 table, citation check +
  win/lose recompute → a `doctrine-audit.json`; a failing audit blocks
  the skill PR).
- **Fresh-session test:** a new main-agent session (fresh pi/Hermes
  session, only the skill + S3.2 scripts available, mock engine) must
  complete the standard workload set (S5.6) with zero undocumented
  commands — asserted by the session transcript's command log.

### 4.7 Acceptance (runnable)

1. Doctrine audit passes: every S4.2 row cites an existing S5 row; the
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

### 6.1 Artifact form (locked — C-05: systemd + binary, not docker)

- `bonsai2-image`: a build-only Docker image (`Dockerfile.build`) that
  compiles the pinned fork (S1.2 flags) for aarch64 and bundles
  `llama-server` + a CUDA 13.0 runtime. Used to **build**, not to run
  (C-05: efficient outside docker).
- `install.sh` (the one command): downloads/extracts the built binary
  (or builds in-place if no prebuilt), downloads the model (S1.1, hash
  check), writes the four config files (S1.6/S2.6/S3.6/S5.7) from
  detected box state, installs the S2.2 units, starts the manager, runs
  the S2.7 health checks, prints the stack-ready report.
- No compose file in v1 (nothing runs in docker besides the pre-existing
  main stack, which install.sh never touches).

### 6.2 Prerequisites (verified on this box, R-09 — locked as the
supported matrix)

| prereq | this box | install check |
|---|---|---|
| DGXOS / Ubuntu 24.04.4 LTS, kernel `6.17.0-1021-nvidia` | ✓ | `/etc/os-release` + `uname -r` (warn if not 24.04) |
| aarch64 | ✓ | `uname -m == aarch64` (else `ERR_ARCH`, exit 3) |
| NVIDIA driver ≥ 580 | 580.126.09 ✓ | `nvidia-smi` parses, version ≥ 580 |
| nvidia-container-toolkit | ✓ (main stack uses it) | presence check only (build stage) |
| docker (build stage only) | 27.5.1 ✓ | `docker version` |
| unified memory ≥ 96 GiB | 121.6 GiB ✓ | `/proc/meminfo` MemTotal ≥ 103 GB decimal (= 96 GiB; the S0 table and this column use GiB). Below 96 GiB is outside the supported matrix: N is still computed by S1.4/S2.3 (a ~96 GiB box with the 46.9 GB main stack computes N = 3 at PQ2_0); only below ~70 GiB does N come out 0 → main-only mode (S2.4) |
| python 3.12 (bench runner) | 3.12.3 ✓ | `python3 --version` ≥ 3.10 |
| free disk ≥ 20 GB | ~100 GB ✓ | `df /opt` ≥ 20 GB — a floor, not an expectation: a clean install writes ~8 GB (model + binary + configs), the build stage peaks higher; ~100 GB free is normal on a fresh GB10 |

### 6.3 The one command (locked, ordered)

```
sudo ./install.sh          # idempotent; see S6.4
```

Steps: (1) prereq checks (S6.2 table, any fail → named ERR, exit ≠ 0,
nothing installed). (2) build/extract binary to `/opt/bonsai2/bin` (skip
if present + version matches the pin — idempotency). (3) download model
(S1.1, skip if SHA matches). (4) detect main-stack footprint live
(`docker stats qwen38-sglang`; probe fails → main stack dead, fall back
to the 46.9 GB default, log `WARN_MAIN_STACK_DEAD`, install continues
per S6.4's main-only fallback) → compute N via S1.4 → write the four
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

- Re-run: binary/model skipped if pinned-and-verified; YAMLs
  regenerated only if detected state changed (N changed, main footprint
  drifted > 5 GB — a policy constant, set below the per-instance budget
  of 11.5 GB so a drift this size can already change N; the S6.7
  +10 GB test exercises it) — else `--force-config` to regenerate; units
  re-installed without restart if unchanged (`systemctl daemon-reload`
  only on diff).
- **Never touches the main stack:** no `docker` command against
  `qwen38-sglang` except `stats` (read-only) and `inspect` (read-only).
  The main stack dies? install continues (main-only fallback), and the
  report says so.

### 6.5 Upgrade procedure (locked — C-06: "whenever a new version of
the main agent or subagent model engine is released")

- **Main stack (qwen38-dflash2 / sglang):** owned by the dgx-spark-qwen38
  repo, NOT this one. This project's upgrade path: a PR in
  dgx-spark-qwen38 (their lane) → after their image is up, this project
  re-runs `install.sh --refresh-main` (re-detects footprint, recomputes
  N, re-runs quick bench M1/M2/M7). No in-place main-stack surgery here.
- **Subagent engine (llama.cpp fork pin):** PR bumps
  `engine_pin.commit` → CI/build stage recompiles → `install.sh` picks up
  the new binary (S6.4 step 2) → **quick bench gates the PR** (S5.5,
  >10% t/s regression or auto-rubric regression = PR blocked).
- **Subagent model (revision/quant):** same PR flow; full bench (S5.5)
  when the quant or revision changes; the doctrine audit (S4.6) re-runs
  in the same PR.
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
  reinstall-over (S6.4 idempotency: second run touches nothing, reports
  "unchanged").
- **Drift test:** main-stack footprint +10 GB between two installs →
  N recomputes down, report says why.
- **Prereq-fail tests:** each S6.2 row faked-failed (fake uname, fake
  driver) → the named ERR, exit ≠ 0, nothing in `/opt/bonsai2` created
  (atomic: check-all before touch-any).

### 6.8 Acceptance (runnable)

1. One command on a fresh GB10 box → stack-ready report, all S2.8
   checks pass, N auto-computed and correct per S1.4.
2. Re-run → "unchanged" (zero restarts, zero downloads).
3. Rollback to the previous state file → stack healthy at the previous N.
4. All S6.2 prereq-fail cases emit their named ERR with exit ≠ 0 and a
   clean filesystem.

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

LLM-orchestration, main-stack changes, vision (mmproj), multi-box v1,
fine-tuning, LLM-judge bench mode, instance kill-for-drift, docker-run
bonsai, stateful subagents.
