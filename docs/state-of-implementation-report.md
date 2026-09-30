# qwen-tory38 — State of Implementation Report

> **Dated snapshot (2026-09-25) — context, not a living doc.** As of this
> write the suite was 341 hermetic tests and the frontier was MAP P4.
> Current state: Phases 0–6 merged on main (730 hermetic cases green,
> 21 @gpu methods; one-command installer landed, PR #36). Read `MAP.md`
> for the live ledger; this file is kept for the component-by-component
> build narrative.

Date: 2026-09-25. Author: senior architect (Hermes session).
Scope: all code merged to main through PR #15 (MAP 0.1–3.8), plus the
parallel session's in-progress 3.9–3.12 treated as "as depicted in spec."
Machine state: no bonsai2 units installed on this box yet (Phase 6);
images `bonsai2-server-build:121a` and `bonsai2-server:1.0` present;
weights cached at `/opt/bonsai2/cache/` (both quants, SHA-verified).
Hermetic suite: 341 tests, OK, 54.9 s (verified this session).

---

## 1. What this project is, in one paragraph

A two-model agent stack on a single GB10 box (121.6 GiB unified
LPDDR5X). The main agent (qwen3.8-27b NVFP4, SGLang, :30001) is external
infrastructure this project consumes but never modifies. This project
builds the subagent side: N instances of Ternary-Bonsai-2-27B (ternary
weights, 7.21 GB PQ2_0 GGUF) served by the prism-ml llama.cpp fork
(pinned at `9a9394a8`), each instance a docker container under a systemd
unit. The main agent drives subagents through deterministic bash control
scripts (S3.2) governed by a doctrine skill (S4). A bench framework (S5)
measures where subagents actually win. Everything installs by one
command on a GB10 box (S6).

The VRAM budget: main stack ~76.4 GB, OS reserved 8.0 GB, leaving ~37.2 GB
for the bonsai pool. Per-instance footprint 14.08 GB (measured q4_0 KV),
budget 14.78 GB (+5% margin). N = floor(37.2 / 14.78) = 2 instances
default on this box.

---

## 2. What was built (by component)

### 2.1 Subagent inference engine (SPEC S1) — Phase 1

**What exists:**

- `third_party/llama.cpp/` — git submodule pinned at fork commit
  `9a9394a8` (prism branch tip 2026-09-18). The fork's ternary kernels
  compile unconditionally; no cmake toggles.
- `image/Dockerfile` — builds the fork in the CUDA 13.0.2-devel image
  (digest-pinned), aarch64, sm_121a. Produces `llama-server` + shared
  libs at `/opt/bonsai2/bin/` inside `bonsai2-server-build:121a`.
- `image/Dockerfile.runtime` — the runtime image `bonsai2-server:1.0`
  (digest `sha256:6b4cd79f…`): bundles the binary, both model quants
  (SHA-verified in-build), CUDA runtime, python3 (baked for the @gpu
  cgroup-charge holder test). The S1.3 CMD is the image default.
- `bin/bonsai2_model_fetch.py` — HuggingFace download with SHA256 + size
  verification, retry ×3 / 10 s backoff, `ERR_HASH_MISMATCH` exit 4.
- `bin/bonsai2_render_config.py` — the `subagent-engine.yaml` writer +
  schema validator (port math, ctx/parallel bounds, sampling ranges).
- `bin/bonsai2_vram.py` — the S1.4 VRAM formula as pure functions:
  `instance_footprint_gb`, `instance_count` (clamp [0,8]),
  `subagent_budget_gb`. No I/O.
- `bin/bonsai2_build_image.py` — orchestrates the runtime image build,
  cross-checks the build image's banner against the YAML pin, records
  the docker content digest as `image_pin`.
- `etc/subagent-engine.yaml` — template with all pins filled
  (engine commit, image digest, model SHAs, instance count 2,
  port_base 30101, ctx 262144, max_num_seqs 8, budget 14.78 GB,
  kv_cache q4_0, sampling from model card, alias, api_key).
- `units/bonsai2-server@.service` — per-instance unit template
  (docker run, host network, memory cap, restart policy, CPUQuota 64%).
- `@gpu` tests: `test_bringup_1_7.py`, `test_build_banner.py`,
  `test_runtime_image.py`, `test_load_1_8.py`, `test_oom_1_9.py`,
  `test_port_collision_1_10.py`, `test_kill_restart_1_11.py` — all
  green (17/17 of Phase 1's @gpu).

**What the engine can do as it stands:**

- Serve the Ternary-Bonsai-2-27B model as an OpenAI-compatible
  `/v1/chat/completions` endpoint on port `30101 + i`.
- 262144-token context window per instance (the model's hybrid-attention
  ceiling; 131072 effective per HF card, 262144 verified in the demo).
- 8 concurrent completion slots per instance (`--parallel 8`).
- q4_0 KV cache (the S1.4 re-anchor; measured 6.87 GB at 8×262144).
- Content/reasoning split via `--reasoning-format deepseek` (the
  `reasoning_content` field in the SSE stream).
- 120 s sustained load: 44.7–61.9 t/s aggregate at 8 concurrency
  (1.7–2.4× headroom over the 26.2 floor).

**What the engine cannot do (v1 design, locked):**

- No vision (mmproj not loaded; text-only).
- No multi-box (single GB10).
- No fine-tuning.
- No tool-calling protocol (the engine is a text model behind a
  chat-completions API; it does not execute tools itself).
- No stateful sessions (each completion is independent; the
  `--parallel 8` slots are concurrent streams, not conversation threads).
- No per-task context allocation (the engine's `--ctx-size` is fixed at
  boot; the harness routes tasks to instances whose live ctx fits).

### 2.2 Startup / serving lifecycle (SPEC S2) — Phase 2

**What exists:**

- `bin/bonsai2-manager` (2714 lines, Python, extensionless) — the
  instance manager. Runs from `bonsai2-sglang.service` (Type=notify).
  Implements:
  - S2.3 planning tier: `N' = max(0, min(N, floor(free_gb /
    per_instance_budget_gb)))`, with `free_gb` read from the main
    container's live cgroup `memory.current` (sleep-accurate), else the
    recorded `main_stack.footprint_gb`.
  - S1.4 allocation tier: the stricter pre-spawn gate
    `free ≥ max(instance_footprint_GB + 1.0, per_instance_budget_gb)`;
    5 s probe timeout + one retry, then treat free as 0.
  - S2.5 ordered spawn: start instance i, wait healthy (S2.2 probe),
    then i+1. One shared whole-boot deadline.
  - S2.2 health probe: every 10 s, `GET /v1/models` (200 + alias) AND
    16-token completion < 10 s. Two consecutive fails → `degraded`;
    five → restart attempt (defers to systemd if the unit already
    restarted since the streak began).
  - S2.4 N=0 / main-only: PTQ1_0 fallback retry (re-plan at the
    fallback footprint), ctx-halve retry on allocation-tier fail.
    `main_only_reason` ∈ {budget_zero, user_unloaded, ERR_VRAM_OOM}.
  - S2.5 drift watch: 5-minute recheck; cgroup pressure sustained > 30 s
    over the `--memory` cap → halve that instance's ctx + in-place
    `systemctl restart`. Never re-spawns `bunloaded` instances.
  - S3.2 control queue: `control/unload-*.json` / `reload-*.json` →
    `control/ack-<id>.json`. The manager is the sole pool actor.
  - S3.4 reaper: `reap_tasks_once()` runs every probe cycle; stale
    running records (dead worker pid) → `failed` / `ERR_ENGINE_DOWN`;
    past-deadline + grace → `timeout` / `ERR_DEADLINE`.
  - S2.6 measured boot: `write_boot_target_s` writes the measured
    cold-boot time back to `serving.yaml`.
- `units/bonsai2-sglang.service` — manager unit template (Type=notify,
  Wants=qwen38-sglang.service, After= network/docker).
- `etc/serving.yaml` — template (main_stack container/endpoint/
  footprint, os_reserved_gb, manager binary/health_dir/restart_burst/
  health_poll_s, boot_target_s).
- `@gpu` test: `test_lifecycle_2_11.py` (1220 lines) — all five S2.8
  acceptance criteria: cold boot ≤ 180 s, crash recovery, N=0
  end-to-end + PTQ1_0 fallback, drift cycle, unload/reload cycle.
  Green 2026-09-24 (PR #13 hotfix).
- Hermetic: `tests/test_manager.py` (2199 lines, 207+ cases).

**What the manager can do as it stands:**

- Boot N instances in order, each gated by the S1.4 allocation tier.
- Keep them alive: health probe, escalation restart, start-limit
  tracking (`bfailed` after 3 failed starts in 300 s).
- React to VRAM drift: halve context on a pressure event, in-place
  restart, no pool removal.
- Handle N=0: PTQ1_0 fallback, ctx-halve, main-only mode.
- Accept pool control commands (unload/reload) from the harness via
  the control/ queue.
- Reap stale task records (the S3.4 deadline backstop).
- Write `/run/bonsai2/health.json` every probe cycle (S3.1 schema).

**What the manager cannot do:**

- It does not dispatch tasks (that is the harness's job).
- It does not measure throughput or task success (that is the bench).
- It does not decide which tasks go to subagents vs main (that is the
  skill's job).
- It does not install anything (that is `install.sh`, Phase 6).
- It does not re-spawn instances the user explicitly unloaded.

### 2.3 Harness control scripts (SPEC S3) — Phase 3 (3.1–3.8 done)

**What exists:**

- `bin/bonsai2_harness.py` (2103 lines) — the core library. Owns:
  - The S3.1 output contract: stdout = one JSON object, stderr = human.
  - The closed-set exit codes: {0, 2, 3, 4, 5, 8, 20, 21, 22, 80, 82, 84}.
  - `load_config` (harness.yaml + env overrides), `load_health_doc`.
  - `route_target`: least-loaded, deadline-aware, ctx-fit routing;
    tie-break lowest i. Queue FIFO with priority preemption.
  - `claim_own_task` / `claim_slot`: the fast-path claim under the
    slot-claim lock (cross-process, stale-PID reclaim).
  - `start_worker`: spawns the detached taskworker child.
  - `reap_stale`: the S3.4 reaper (pid-dead + deadline-backstop).
  - `dedup_lookup` / `dedup_record`: the S3.5 24 h TTL spawn idempotency.
  - `cmd_status`, `cmd_list_instances`, `cmd_health`: the read family.
  - `cmd_spawn`: the full submission flow (arg parse → config → health
    → main probe → dedup → route → claim → worker start → queue-wait
    poll → exit).
  - `cmd_collect`: read result file; 20/21/22/0/2 mapping.
  - `cmd_steer`: advisory steer (record + optional `--wait SECS` → 8).
  - `cmd_cancel`: SIGTERM the worker's process group, SIGKILL after 5 s;
    the worker's handler marks the record `failed` / `ERR_CANCELLED`.
  - `cmd_unload` / `cmd_reload`: manager-mediated via the control/ queue.
- `bin/bonsai2-taskworker.py` (453 lines) — the detached child that runs
  one task. Streaming chat completion, result file write, one engine-down
  retry (S1.5, untried-only pool), deadline → timeout. Always exits 0
  (the record is the source of truth).
- Thin CLI dispatchers: `bonsai2-status`, `bonsai2-list-instances`,
  `bonsai2-health`, `bonsai2-spawn`, `bonsai2-collect`, `bonsai2-steer`,
  `bonsai2-cancel`, `bonsai2-unload`, `bonsai2-reload`.
- `etc/harness.yaml` — template (main_endpoint, probe, results_dir,
  dedup_dir, dedup_ttl_h, queue_wait_max_s, instance_healthy_timeout_s,
  task_default_deadline_s, main_tps_guardrail_pct).
- Hermetic tests: `test_harness_core.py` (579 lines),
  `test_harness_status.py` (447 lines), `test_harness_spawn.py`
  (1027 lines), `test_harness_34_38.py` (960 lines, 35 cases).
  `tests/fixtures/mock_engine.py` — threaded http.server fixture.

**What the harness can do as it stands:**

- Dispatch a task to the least-loaded healthy instance with a free
  engine slot. Queue (FIFO + priority) if no slot is free; exit 8 on
  `queue_wait_max_s` expiry.
- Track every task's state via JSON records in
  `/run/bonsai2/results/tasks/<task_id>.json`.
- Collect a task's result (answer, reasoning, usage) when terminal.
- Steer a running task (advisory: recorded on the task; the worker
  re-reads between attempts).
- Cancel a task (SIGTERM → SIGKILL after 5 s; slot freed at signal time).
- Unload/reload instances for VRAM management (manager-mediated).
- Report stack health: instance states, ctx, slots in use, main endpoint
  health, main-only reason.
- Enforce the closed-set exit code contract (any other code = harness
  bug, caught by the S3.8 #3 suite).
- Deduplicate same-prompt spawns within 24 h (no double slot charge).
- Enforce per-task deadlines (worker-side check + manager-side reaper
  backstop).

**What the harness cannot do (as of 3.8):**

- It cannot measure throughput (that is the bench, Phase 4).
- It cannot decide whether a task should go to a subagent or main
  (that is the skill, Phase 5).
- It cannot install or reconfigure the pool (that is `install.sh`,
  Phase 6).
- It cannot give a subagent a tool to execute (the subagent is a text
  model; the `--tools on|off` flag is recorded but its semantic effect
  is prompt-side, not harness-side).
- It cannot run a bench scenario (the `bonsai2-bench` command is Phase 4).
- The S3.8 #3 closed-set test, 3.10 mock engine hardening, 3.11
  table-driven suite, and 3.12 live acceptance are the remaining
  Phase 3 items (the parallel session is working on these).

### 2.4 What is NOT yet built

| Phase | Deliverable | State |
|---|---|---|
| 4 | Bench framework: `bench/run.py`, scorers, goldens, M1–M8, `bonsai2-bench` | Not started |
| 5 | Harness skill: `SKILL.md`, work-allocation table, doctrine audit, fresh-session test | Not started |
| 6 | `install.sh`: prereq checks, build-or-verify image, download model, detect footprint, write YAMLs, install units, start manager, health checks, stack-ready report; idempotency; `--instances N`; `--rollback`; `--refresh-main` | Not started |
| 7 | Global validation: cold boot, fresh-session workload, M1–M8 bench, install S6.8, 72 h soak, closeout | Not started |

---

## 3. Which component manages the subagents by the main agent?

There are three layers, and the distinction matters:

**Layer 1: The harness control scripts (S3.2) — the main-agent API.**
This is what the main agent actually calls. The nine commands:

```
bonsai2-spawn  [-p PROMPT_FILE | -i INLINE_PROMPT] [--tools on|off]
               [--ctx N] [--deadline SECS] [--out FILE]
               [--priority low|normal|high]
bonsai2-status
bonsai2-list-instances
bonsai2-health
bonsai2-collect  <TASK_ID>
bonsai2-steer    <TASK_ID> -m MESSAGE [--wait SECS]
bonsai2-cancel   <TASK_ID> [--graceful]
bonsai2-unload   [--all | --instances i[,i,...]]
bonsai2-reload   [--all | --instances i[,i,...]]
```

The main agent uses these to dispatch work, monitor progress, collect
results, steer (advisory), cancel, and manage pool VRAM. The closed-set
exit codes (12 codes) are the contract: the main agent's next action is
determined by the exit code, not by parsing stdout.

**Layer 2: The manager (bonsai2-manager) — the pool lifecycle brain.**
The main agent does not call the manager directly. The manager:
- Keeps the N instances alive (health probe, escalation restart).
- Handles VRAM drift (ctx halving).
- Processes pool control commands (unload/reload) from the harness.
- Reaps stale task records (the S3.4 deadline backstop).
- Writes `health.json` every 10 s (the harness reads this).

The manager is the reflexes; the harness is the hands; the main agent
is the brain.

**Layer 3: The skill (S4, Phase 5) — the doctrine.**
Not built yet. This will be a text skill (`bonsai2-harness`) that
encodes:
- WHEN to use subagents (the S4.2 work-allocation table).
- HOW to allocate work (batch spawns, session economy).
- The cost doctrine (dispatch iff `dispatch_cost < est_main_time_s`).
- The failure doctrine (one paragraph per exit code).
- The bench citations (every doctrine row cites an M-id from the Phase 4
  bench run).

Without the skill, the main agent has the API (harness) but no
doctrine: it doesn't know *when* to dispatch vs *when* to keep the task
on main, and it has no calibrated t/s constants for the cost model.

---

## 4. What capabilities does the main agent get with the subagents?

The main agent (qwen3.8-27b at :30001) gets:

1. **Parallel bulk text generation.** Spawn N independent tasks
   simultaneously (up to 8 per instance × N instances = 16 tasks in
   parallel at N=2). Each task is a single streaming completion.
   The main agent's turn count drops: instead of generating 6 file
   summaries sequentially, it spawns 6 in one turn and collects in
   the next.

2. **Context offloading.** A subagent task can consume up to 262K
   tokens of input without eating the main agent's 262K context.
   The main agent's context is reserved for planning, review, and
   cross-file coherence.

3. **Deterministic error handling.** The 12-code closed set means the
   main agent never parses free text to understand what went wrong.
   Exit 80 = main-only mode, run on main. Exit 5 = ctx too long,
   split the task. Exit 22 = deadline expired, re-spawn smaller.

4. **VRAM management.** Unload/reload lets the main agent (or the
   user) free VRAM for a main-stack operation and re-load subagents
   when it is done. The manager enforces the S1.4 tier on reload.

5. **Advisory steering.** `bonsai2-steer` records a message on a
   running task. The effect is bounded: the worker re-reads the record
   between attempts, so a steer affects the next retry, not the
   current attempt mid-stream. For a single-attempt task (the common
   case), the steer is a no-op unless the task fails and retries.

6. **Task idempotency.** Same-prompt + same-flags spawns within 24 h
   return the existing task_id (no double slot charge). This matters
   when the main agent re-spawns after a collect that returned a
   non-terminal state.

**What the main agent does NOT get from the subagents:**

- No subagent-to-subagent communication (locked: subagents never talk
  to each other; the main agent is the only orchestrator).
- No subagent-initiated tool calls (the subagent is a text model; it
  generates text, it does not execute commands).
- No subagent file access (the main agent reads/writes files; the
  subagent generates text that the main agent applies).
- No subagent browser access (no camoufox, no screen interaction).
- No subagent shell access (no `bash`, no `subprocess` inside the
  subagent's task).
- No multi-turn conversation within one task (fire-and-collect: one
  prompt in, one completion out; the task record tracks state but the
  engine is stateless per request).

---

## 5. Which component is responsible for subagent capabilities? What are they?

The subagent's capabilities are determined by three layers:

**The model (Ternary-Bonsai-2-27B).** This is the intelligence ceiling.
A 27B-parameter ternary-weight model (3-valued weights: -1, 0, +1).
The community reports ~98% of Qwen3.8-27B quality on standard
benchmarks. Text-only (no vision in v1). Its capabilities:
- Code generation (functions, test files, boilerplate).
- Text analysis (summaries, reviews, transforms).
- Long-context reading (up to 262K tokens per request).
- Reasoning (the `deepseek` format splits `content` from
  `reasoning_content`).
- What it lacks: tool-calling protocol, function calling, structured
  output enforcement (it generates free text; the main agent parses).

**The engine (llama.cpp fork, pinned).** This determines the serving
parameters:
- 262144 ctx (the model's hybrid-attention ceiling).
- 8 parallel slots (concurrent completion streams).
- q4_0 KV cache (the memory-efficient long-context path).
- OpenAI-compatible `/v1/chat/completions` API.
- What it determines but the main agent cannot change at task time:
  the sampling parameters (temperature 1.0, top_p 0.9, top_k 1,
  min_p 0.01, repeat_penalty 1.0, repeat_last_n 512, stop tokens
  248046/248044) are baked into the engine's config, not per-task.

**The harness (the task lifecycle).** This determines:
- Which instance gets the task (least-loaded routing).
- How long the task can run (`--deadline`, default 600 s).
- Whether the task is deduplicated (24 h TTL).
- How the result is delivered (the result file with
  `answer`/`reasoning`/`usage`).
- What happens on failure (one retry on another instance, then fail).

**The specific capabilities the user asked about:**

| Capability | Has it? | How |
|---|---|---|
| Computer use (GUI) | No | No screen, no mouse, no keyboard. The subagent is a text model. |
| Running Linux commands | No | No shell access. The subagent generates text (e.g., a script); the main agent runs it. |
| Managing/writing/creating/deleting files | No (directly) | The subagent generates file content as text. The main agent writes it to disk. The subagent cannot `open("file", "w")` itself. |
| Browser via camoufox | No | No browser in the subagent's environment. The camoufox venv at `~/prod/.venv` is available on the host, but the subagent's task is a single streaming completion against an HTTP endpoint; it has no Python runtime, no browser driver, no screen. |
| Multi-turn within a task | No | One prompt → one completion. The task record tracks state (queued/running/done/failed/timeout) but the engine does not maintain conversation state across requests. |
| Tool calling / function calling | No (protocol-level) | The `--tools on|off` flag is recorded on the task and is part of the dedup key, but its semantic effect is prompt-side: the main agent's prompt can tell the model "you have access to tool X, call it by outputting..." The model generates the tool call as text; the main agent (or a future tool-loop) interprets and executes it. The subagent itself does not execute the tool. |
| Reasoning | Yes | `--reasoning-format deepseek` splits `content` and `reasoning_content` in the SSE stream. The collect command returns both. |
| Long context | Yes | Up to 262K tokens per request. The `--ctx N` flag on spawn is a routing filter (the task only routes to instances whose live ctx ≥ N), not a per-task allocation. |
| Concurrency | Yes | 8 slots per instance. The harness queues when all slots are busy. |

**The key architectural point:** the subagent is deliberately a "dumb"
text generator. All agentic capabilities (file access, shell, browser,
tool execution, multi-turn reasoning, cross-file coherence) live in the
main agent. The subagent's job is to generate the text output fast and
cheaply in parallel; the main agent's job is to orchestrate, verify,
and apply. This is the "self-report" rule (S4.2): every subagent result
is an unverified claim that main must verify before use.

---

## 6. Open-ended decisions, poorly-scoped spec directives, and coherence evaluation

### 6.1 The `--tools on|off` flag is underspecified

The flag exists in the spawn API, is recorded on the task record, and is
part of the dedup key. But there is no spec text that says what "tools on"
does to the subagent's behavior. The worker sends the prompt as a single
user message to `/v1/chat/completions` with no system message and no
tools array. The semantic effect is entirely prompt-side: if the main
agent's prompt says "you have a calculator tool, output `{"tool":
"calc", "args": ...}`", the model generates that JSON as text. But:

- There is no tool-execution loop in the subagent.
- There is no system message injected when `--tools on`.
- The `--tools` value is not passed to the engine (the engine's
  sampling params are fixed at boot).
- The flag's only current effect is: (a) it is part of the dedup key
  (so `--tools on` and `--tools off` with the same prompt are
  different tasks), and (b) it is recorded on the task record (so the
  main agent can see it in the collect output).

**Recommendation:** either (a) remove the flag from the v1 API (it is
a no-op that adds surface area) or (b) document it explicitly as
"prompt-side convention: the main agent's prompt template changes based
on this flag; the harness records it for dedup and observability only."
Option (b) is the lesser change and matches the spec's "stateless, no
memory, no state" design (C-10).

### 6.2 Steer is advisory and its limitation is under-emphasized

The S3.2 spec says "steer-and-wait: block up to SECS for state change."
The implementation records the steer message on the task and the worker
re-reads the record between attempts. For a task that is mid-stream
(singly-attempt, the common case), the steer has zero effect until the
task completes and (if it failed) retries on another instance. The
`--wait SECS` polls for a *state change* (queued → running, running →
done/failed/timeout), not for the steer message to be "processed."

This is correct per the spec (the spec does not claim the steer affects
the current attempt), but the main agent's skill (S4.3 "instance
hygiene: prefer reusing a running task over re-spawning when the task
is >50% done (steer instead of cancel+spawn)") implies a stronger
capability than what exists. The steer is useful for:
- A queued task that gets steered before it starts (the worker reads
  the record at start; the steer message is in the record).
- A task that fails and retries (the retry reads the record; the steer
  message is there).
- The `--wait` use case: "I want to know when this task finishes, and
  while I'm waiting, here's a correction if it needs one."

But it is NOT useful for: "change the task's prompt mid-stream." That
would require the engine to support mid-stream prompt injection, which
llama.cpp's chat-completions API does not.

**Recommendation:** the S4.3 "steer instead of cancel+spawn" guidance
should be qualified: "steer is effective for queued tasks and for the
next attempt of a retrying task; for a task mid-stream on a single
attempt, cancel+spawn with the corrected prompt is the effective path."

### 6.3 The bench (S5) is the "reason this exists" but is not built

The entire value proposition of the project hangs on S5: "t/s first,
quality becomes apparent from task success" (C-13). Without the bench:
- The S4.2 work-allocation table has no M-id citations (every row
  says "[M#]" but the M-ids don't exist yet).
- The cost doctrine (S4.4) uses a-priori t/s constants (29 decode,
  4800 prefill) that are "the a-priori floor, not the final values."
- The M8 main-guardrail (main t/s within 5% of baseline at full
  subagent load) is unmeasured.
- The N=2 default is a "safe start" with no evidence that 2 is
  optimal (the S5.9 #3 "M8 guardrail passes at full load" condition
  for raising N is untested).

The bench is Phase 4 (10 MAP items: 4.1–4.10). It is the next
substantial work after Phase 3 closes (3.9–3.12).

### 6.4 install.sh (S6) is the reproducibility deliverable

Everything currently runs manually or via @gpu tests with
environment-variable hooks. The `install.sh` one-command installer
(Phase 6, 8 MAP items: 6.1–6.8) is what makes this reproducible on
a fresh GB10 box. Until it exists:
- The "one command on a fresh GB10 box → stack-ready report"
  acceptance (S6.8 #1) is unmet.
- The idempotency contract (S6.4: re-run → "unchanged") is untested.
- The rollback procedure (S6.5) is untested.
- The `--instances N` phased rollout (S0: build with 1, load to N)
  is not exercisable.

### 6.5 The main agent's capabilities with subagents are bounded by the prompt

Since the subagent is a text model (no tools, no files, no shell), the
quality of a subagent task is entirely determined by the main agent's
prompt. A well-crafted prompt ("here is the function signature, here
is the existing code context, write a test file with 3 test cases,
output only the file content between the markers") produces good
output. A vague prompt ("write some tests") produces vague output.

This means the main agent's prompt-engineering skill is the bottleneck
for subagent task quality. The S4 skill (Phase 5) should include
prompt templates for each S4.2 task shape (code-gen, analysis, diff
review, etc.) so the main agent has a proven prompt structure rather
than improvising.

### 6.6 The `--ctx N` flag is a routing filter, not a context allocation

The spawn `--ctx N` flag tells the harness "this task needs at least
N tokens of context." The routing then only considers instances whose
live ctx (post-drift-reduction) is ≥ N. This is a *filter*, not an
*allocation*: the engine's `--ctx-size` is fixed at boot (262144), and
the task does not get "N tokens reserved." If the task's actual input
exceeds the instance's ctx, the engine returns HTTP 400 and the task
fails with `ERR_CTX_TOO_LONG` (exit 5). The `--ctx` flag reduces the
probability of this by routing to an instance with sufficient ctx, but
it does not prevent it.

This is correct behavior (the spec says "never raise ctx on a per-task
basis in v1; instance-level only, S2.5"), but the flag name could be
confusing: `--ctx` sounds like "allocate N tokens of context to this
task" when it actually means "this task needs an instance with at
least N tokens of available context."

### 6.7 Coherence assessment

The architecture is coherent. The three layers (engine → manager →
harness → skill) have clean boundaries:
- The engine serves tokens.
- The manager keeps the engine instances alive and tracks VRAM.
- The harness dispatches tasks and tracks their lifecycle.
- The skill (when built) tells the main agent when and how to use
  the harness.

The spec is detailed and locked. The spec-follows-code discipline has
worked: every pin-reality delta (docker g = GiB, q4_0 KV re-anchor,
15 s → 30 s reload budget, exit-1 port collision, the 401 auth wall
on the main endpoint) got a recorded amendment, not a silent patch.

The main coherence risk is the gap between "we can run subagents"
(Phases 0–3, done) and "we know when to use them and whether it wins"
(Phases 4–5, not built). The project is 50% infrastructure and 50%
measurement/doctrine, and only the infrastructure half is done.

The code quality is good: 341 hermetic tests passing, the VRAM formula
module is pure and clean, the task record is the single source of truth,
the closed-set exit code contract is enforced in one place (`emit`).
The manager's 2714 lines are extensionless Python (outside the gate's
lint glob), which is a known debt flagged in the coherence report.

---

## 7. Spec amendments

The following items in SPEC.md need editing, clarification, addition,
or removal based on the implementation analysis:

### 7.1 S3.2: The `--tools on|off` flag needs a semantic definition

**Current text (S3.2):**
```
bonsai2-spawn  [-p PROMPT_FILE | -i INLINE_PROMPT] [--tools on|off]
```

**Problem:** No section of the spec defines what `--tools on` does to
the subagent's behavior. The implementation records it on the task
record and includes it in the dedup key, but its semantic effect is
prompt-side (the main agent's prompt template changes) or a no-op.

**Amendment:** Add a note to S3.2 (or S4.1):
> `--tools on|off` is a prompt-side convention flag. The harness
> records it on the task record and includes it in the dedup key (so
> `--tools on` and `--tools off` with the same prompt are distinct
> tasks). It does NOT change the engine's request: the worker sends
> the prompt as a single user message with no tools array and no
> system message. The semantic effect is on the main agent's prompt
> template: when `--tools on`, the main agent's prompt describes the
> available tools and instructs the model to output tool calls as
> structured JSON within the response. The main agent (or a future
> tool-loop, out of scope for v1) interprets and executes the tool
> calls. The subagent itself does not execute tools.

### 7.2 S4.3: Qualify the "steer instead of cancel+spawn" guidance

**Current text (S4.3, Instance hygiene):**
> prefer reusing a running task over re-spawning when the task is >50%
> done (steer instead of cancel+spawn)

**Problem:** The steer is advisory. For a task mid-stream on a single
attempt, the steer message is recorded but not consumed until the next
attempt (if any). The "prefer steer" guidance implies a capability that
only works for queued tasks and retrying tasks.

**Amendment:**
> prefer reusing a running task over re-spawning when the task is >50%
> done: steer the task (the steer message is consumed when the task
> retries on another instance; for a queued task, the worker reads the
> record at start and sees the steer). For a task mid-stream on a
> single attempt, the steer has no effect on the current attempt;
> cancel + spawn with the corrected prompt is the effective path.

### 7.3 S3.2: Clarify `--ctx N` semantics

**Current text (S3.2):**
```
bonsai2-spawn  [...] [--ctx N] [...]
```

**Problem:** The flag name suggests "allocate N tokens of context."
The actual behavior is "route to an instance with at least N tokens of
live context." This is a routing filter, not an allocation.

**Amendment:** Add a note to S3.2:
> `--ctx N` is a routing filter, not a context allocation. The harness
> routes the task to an instance whose live ctx (post-drift-reduction)
> is ≥ N. The engine's `--ctx-size` is fixed at boot; the task does
> not get N tokens "reserved." If the task's actual input exceeds the
> instance's ctx, the engine returns HTTP 400 and the task fails with
> `ERR_CTX_TOO_LONG` (exit 5). The flag reduces the probability of this
> by routing to an instance with sufficient ctx. See S3.3 (routing)
> and S1.5 (exit 5 = ctx too long).

### 7.4 S0: Add a "subagent capability boundary" note

**Problem:** The S0 non-goals list what is out of scope (LLM-to-LLM
orchestration, main-stack changes, vision, multi-box, fine-tuning,
stateful subagents). But the spec does not explicitly state that
subagents have no tool execution, no file access, no shell, no browser.
This is implied by the "fire-and-collect" design and the "self-report
rule" (S4.2), but a reader could reasonably ask "can a subagent run a
command?" and find no explicit answer.

**Amendment:** Add to S0 (after the "Division of labor" paragraph):
> **Subagent capability boundary (v1, locked):** a subagent task is a
> single streaming chat completion (prompt in, text out). The subagent
> has no tool execution, no file system access, no shell, no browser,
> and no multi-turn state within a task. The `--tools on|off` flag
> (S3.2) is a prompt-side convention: the main agent's prompt template
> changes based on the flag, but the subagent does not execute tools.
> All agentic capabilities (file I/O, command execution, browser,
> cross-file coherence, verification) live in the main agent. The
> subagent's output is a self-report (S4.2): unverified until main
> runs the named verification.

### 7.5 S4.4: The t/s constants need a "calibrated" marker

**Current text (S4.4):**
> `t/s_decode ≈ 29`, `t/s_prefill ≈ 4800` (rebenched at S5 on this
> exact box; numbers here are the a-priori floor, not the final values)

**Problem:** The "rebenched at S5" is a future-tense promise. Until S5
lands, these are uncalibrated. The skill (S4.1) should mark them as
such so the main agent knows the cost model is approximate.

**Amendment:** Change to:
> `t/s_decode ≈ 29`, `t/s_prefill ≈ 4800` (a-priori floor from the
> community GB10 bench; re-benched at S5 on this exact box. The S5
> run replaces these with measured values; until then, the cost model
> is approximate and the win rule (dispatch iff
> `dispatch_cost < est_main_time_s`) should be applied with a 20%
> margin on the dispatch side).

### 7.6 S1.3: Document that sampling params are engine-level, not per-task

**Problem:** The S1.6 config has a `sampling:` block (temperature, top_p,
top_k, min_p, repeat_penalty, repeat_last_n, stop_token_ids). These are
passed to the engine at boot (they are part of the `llama-server`
command line). The harness does not pass per-task sampling parameters
to the engine. The spec does not explicitly state this.

**Amendment:** Add to S1.3 (after the `--alias` line):
> Sampling parameters (S1.6 `sampling:` block) are engine-level: they
> are part of the `llama-server` command line and apply to all
> completions on that instance. The harness does not pass per-task
> sampling overrides. If a task needs different sampling (e.g.,
> lower temperature for deterministic output), the main agent's prompt
> should request it ("respond with the code only, no explanation")
> rather than the harness passing a `temperature` field (which the
> OpenAI-compatible API accepts but the engine may or may not honor
> at the pinned commit; the spec does not guarantee per-request
> sampling).

---

## 8. Capability roadmap: what is built, what is to come, what is extra

### 8.1 What is built (Phases 0–3)

| Capability | Status |
|---|---|
| Subagent inference (Ternary-Bonsai-2-27B, 262K ctx, 8 slots) | Built, tested (@gpu) |
| Pinned engine + runtime image | Built, tested |
| VRAM budget + two-tier gate (planning + allocation) | Built, tested |
| Instance lifecycle (spawn, health, restart, drift, unload/reload) | Built, tested |
| N=0 / main-only mode (PTQ1_0 fallback, ctx-halve) | Built, tested |
| Task dispatch (least-loaded routing, queue, priority, dedup) | Built, tested (hermetic) |
| Task lifecycle (spawn, collect, steer, cancel, reaper) | Built, tested (hermetic) |
| Closed-set exit codes (12 codes) | Built, enforced in `emit` |
| Health reporting (health.json, status, list-instances) | Built, tested |
| Hermetic test suite (341 tests) | Green |
| @gpu test suite (19 tests for S1.7–S2.8) | Green |

### 8.2 What is to come (Phases 4–7)

| Phase | Capability | MAP items |
|---|---|---|
| 4 | Bench framework: M1–M8 scenarios, runner, scorers, goldens, `bonsai2-bench`, re-bench policy | 4.1–4.10 |
| 5 | Harness skill: SKILL.md, work-allocation table (bench-cited), session economy, cost doctrine, failure doctrine, doctrine audit, fresh-session test | 5.1–5.7 |
| 6 | One-command install: prereq checks, image build/verify, model download, footprint detection, YAML writers, unit install, manager start, health checks, stack-ready report, idempotency, `--instances N`, rollback, `--refresh-main` | 6.1–6.8 |
| 7 | Global validation: cold boot ≤ 180 s, fresh-session workload, M1–M8 bench committed, install S6.8, 72 h soak, closeout, SPEC → active | 7.1–7.5 |

### 8.3 What is NOT in the spec (extra capabilities that could be added)

These are not in the spec (locked) but are natural extensions the user
asked about:

**Computer use / GUI interaction.**
Not in v1. The subagent is a text model. To add computer use:
- The subagent would need a screen (X11/Wayland) and a screenshot
  pipeline (capture → image → VLM or text description).
- The mmproj (vision) is not loaded in v1 (non-goal 3).
- A tool-loop would be needed: subagent outputs "click at (x,y)" →
  main agent executes → screenshot → subagent sees it.
- This is a "tool-loop" architecture, not fire-and-collect. It
  changes the task model from one-shot to multi-turn.
- **Verdict:** out of scope for v1 (locked: "stateless subagents").
  A v2 item.

**Running Linux commands.**
Not in v1 (as a subagent capability). The main agent runs commands.
To add subagent-side command execution:
- The subagent's task would need a sandbox (container, namespace,
  or chroot) with a shell.
- The task model would change from "one completion" to "tool-loop":
  the subagent outputs a command, the harness executes it, feeds the
  output back, the subagent outputs the next command, etc.
- This is a fundamentally different task model (agentic loop vs.
  fire-and-collect).
- **Verdict:** out of scope for v1. The current design is deliberate:
  the subagent is cheap and parallel; the main agent is the one that
  runs commands and verifies.

**File management (read/write/create/delete).**
Not in v1 (as a subagent capability). The main agent manages files.
To add subagent-side file access:
- The subagent's container would need a volume mount (the repo, or a
  scratch dir).
- The task model would need a file I/O tool (the subagent outputs
  "write file X with content Y", the harness applies it).
- **Verdict:** out of scope for v1. The "self-report" rule (S4.2)
  already implies the main agent verifies file changes; giving the
  subagent direct file access would weaken the verification boundary.

**Browser via camoufox.**
Not in v1. The camoufox venv at `~/prod/.venv` (camoufox 0.4.11,
with the browser binary cached at `~/.cache/camoufox/`) is available
on the host. To add subagent-side browser access:
- The subagent's task would need a browser execution environment
  (camoufox + Playwright/patchright).
- The task model would need a browser tool (the subagent outputs a
  browser action, the harness executes it, feeds the result back).
- The mmproj (vision) would likely be needed for screenshot analysis.
- **Verdict:** out of scope for v1. A v2 "agentic subagent" item.
  The current design: the main agent uses camoufox directly; the
  subagent generates the text (URLs, selectors, expected content)
  that the main agent feeds to the browser.

**Multi-turn / stateful subagents.**
Not in v1 (locked: "stateful subagents" is out of scope). Each task is
one completion. To add statefulness:
- The task record would carry conversation history.
- The worker would send the full history on each attempt.
- The engine's `--parallel 8` slots would be conversation threads.
- **Verdict:** out of scope for v1. The "stateless, no memory, no
  state" design (C-10) is a deliberate simplification.

**LLM-to-LLM orchestration (subagents calling subagents).**
Not in v1 (locked: "no LLM endpoint is ever used to schedule or
coordinate subagents"). The main agent is the only orchestrator.
**Verdict:** out of scope for v1.

**Vision (mmproj).**
Not in v1 (non-goal 3: "no vision serving"). The mmproj-Q8_0.gguf
(0.63 GB) is cached but not loaded. **Verdict:** out of scope for v1.

**Multi-box fan-out.**
Not in v1 (non-goal 4: "1 GB10 box"). **Verdict:** out of scope for v1.

**Fine-tuning.**
Not in v1 (non-goal 5). **Verdict:** out of scope for v1.

**LLM-judge bench mode.**
Not in v1 (out of scope). M3's spot-check is the human/main-judgment
seam. **Verdict:** out of scope for v1.

---

## 9. Machine state (this session, 2026-09-25)

- `main` branch: clean, up to date with `origin/main`.
- Latest commit: `ab7468f` (MAP correction, 2026-09-25).
- Open PRs on Gitea: none.
- The parallel session is working on MAP 3.9–3.12 (exit-code closed-set
  test, mock engine hardening, table-driven suite, S3.8 acceptance).
- No bonsai2 systemd units installed on this box yet (Phase 6).
- Images present: `bonsai2-server-build:121a`, `bonsai2-server:1.0`.
- Weights cached: `/opt/bonsai2/cache/` (PQ2_0 7.21 GB, PTQ1_0 5.95 GB).
- Main stack: `qwen38-sglang` container running, 7.5 GB cgroup
  (sleep_on_idle: main is asleep, ~76.4 GB at fresh load).
- GPU: nvidia-smi reports [N/A] for memory (the unified-memory driver
  does not expose a separate GPU memory pool; `free -h` shows
  121 Gi total, 75 Gi used, 46 Gi available).
- Camoufox: installed at `~/prod/.venv` (camoufox 0.4.11, browser
  binary cached at `~/.cache/camoufox/`). Not part of the bonsai2
  project (it is the host's anti-detect browser for the de-hex-er
  scraper and other tasks).
