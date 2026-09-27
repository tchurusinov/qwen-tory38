# qwen-tory38 — State of Implementation (post first live run)

Date: 2026-09-27. Author: senior architect (Hermes session).
Scope: everything merged to `main` through PR #21 (`f7c768d`), which carries
the MAP 4.9 capability layer + 4.10 pre-prod live M1-M10 run + 4.11 re-bench
gate. Phases 0-4 are claimed done in `MAP.md`; 5, 6, 7 are the remaining
phases. This report reconciles the ledger against the tree, the code, and the
first live run's observed reality (PR #21 comments 3-4 + `bench-results/`).

Machine state at report time:
- Hermetic suite: `Ran 583 tests in 152s, OK`, exit 0 (`/usr/bin/python3.12`,
  repo root, `-t .`). `@gpu` suite present (25 tests in `tests/gpu/`).
- Images: `bonsai2-server-build:121a` (7.99 GB), `bonsai2-server:1.0` (16.1 GB).
- Weights: `/opt/bonsai2/cache/` holds PQ2_0 + PTQ1_0 (SHA-verified at build).
- `/opt/bonsai2` exists but is ONLY `cache/` — no `bin/`, `etc/`, `run/`
  (Phase 6 install not run). No bonsai2 systemd units installed. No bonsai2
  containers running at report time.
- Unified memory: 121 GiB total; `MemAvailable` ~51 GiB at rest (main stack
  asleep in its cgroup, 1.18 GiB live). No subagent instances up now.
- Main stack (:30001) is **key-walled**: no-key `POST /v1/chat/completions`
  -> 401 (confirmed live). This is the root of the M6/M8 first-run failures.
- Pre-prod live run rows committed at `bench-results/` (M6, M1, M2, M3, M4,
  M5, M7 + `runner-stdout.log`). M8, M9, M10 rows are absent — the run
  crashed at M9 (see §4).

The single most important thing in this report: the pre-prod run proved the
components work together, but it exposed a set of Phase-4 seams that make a
clean full M1-M10 run impossible today. Those seams, and the exact hotfix
that plugs them, are §4 and §5.

---

## 1. What this project is, in one paragraph

A two-model agent stack on a single GB10 box (121.6 GiB unified LPDDR5X).
The main agent (qwen3.8-27b NVFP4, SGLang, :30001) is external
infrastructure the project consumes but never modifies. The project builds
the subagent side: N instances of Ternary-Bonsai-2-27B (ternary weights,
PQ2_0 GGUF) served by the pinned prism-ml llama.cpp fork, each a docker
container under a systemd unit. The main agent drives subagents through
deterministic bash control scripts (S3.2) governed by a doctrine skill (S4).
A bench framework (S5) measures where subagents actually win. Everything is
installed by one command on a GB10 box (S6). VRAM budget: main ~76.4 GB,
OS reserved 8.0 GB, leaving ~37.2 GB for the bonsai pool; per-instance
footprint 14.08 GB, budget 14.78 GB (+5% margin), N = floor(37.2/14.78) = 2.

---

## 2. Components and current state

Percentages are against the SPEC section each component implements. "Functions
as designed" means the code path is verified by its hermetic and/or `@gpu`
tests AND, where a live run touched it, by that run. A component can be
"100% built" yet "not on the box" because install (Phase 6) has not run.

### Phase 0 — scaffolding & layout: 100%, functions

- Repo layout mirrors `/opt/bonsai2/` 1:1. `bin/`, `etc/` (templates),
  `units/`, `bench/`, `tests/`, `third_party/llama.cpp` (submodule),
  `scripts/`, `.githooks/` all present and in use.
- Test runner: hermetic default `python3 -m unittest discover -s tests -t .`
  (583 tests green). `@gpu` split under `tests/gpu/` driven by
  `tests/run_gpu.sh` with a pre-discovery nvidia-smi + /proc/meminfo snapshot.
- Pre-commit hook (`.githooks/pre-commit`) + `scripts/gate.sh` +
  `scripts/review.sh` all installed and run on every commit (the G2 gate is
  the formal check; the hook is the tripwire).
- State: done. Nothing left.

### Phase 1 — subagent inference engine + image: 100% built, functions; on-box only the weights

- `third_party/llama.cpp` pinned at fork commit `9a9394a8` (prism tip).
- `image/Dockerfile` (build) + `image/Dockerfile.runtime` (runtime image
  `bonsai2-server:1.0`, digest recorded in `etc/subagent-engine.yaml`).
- `bin/bonsai2_model_fetch.py` (HuggingFace + SHA256/size + retry,
  `ERR_HASH_MISMATCH` exit 4).
- `bin/bonsai2_render_config.py` (`subagent-engine.yaml` writer + S1.6/S1.7
  validator: port math, ctx/parallel bounds, sampling ranges).
- `bin/bonsai2_vram.py` (S1.4 formula as pure functions; the 2026-09-22
  re-anchor to the q4_0-KV constant 0.000003277 / 14.78 GB budget / N=2 is
  reflected).
- `bin/bonsai2_build_image.py` (runtime-image orchestrator; cross-checks the
  build image banner against the engine pin).
- Verified live (MAP 1.7-1.11 `@gpu`): single-instance bring-up, sustained
  8-concurrent load (44-61 t/s, 0x5xx), OOM-ceiling (clean `ERR_VRAM_OOM`,
  prior instances healthy), port-collision (`ERR_PORT_IN_USE`), kill-9
  auto-restart < 15 s.
- Left: S1.8 #2-#5 complete only at Phase 6/7 (they need N>=1 under the
  manager + the full stack). State: built + verified in-test; the runtime
  image is baked, the binary is not yet deployed to `/opt/bonsai2/bin`
  (Phase 6).

### Phase 2 — manager + systemd lifecycle: 100% built, functions in-test; not on the box

- `bin/bonsai2-manager` (sd_notify, two-YAML loader, `health.json` writer,
  S3.1 document).
- Boot-time budget check (S2.3, live cgroup `memory.current`), escalation +
  N=0 / PTQ1_0 / ctx-halve fallbacks, drift watch, mock manager, reaper +
  deadline, spawn dedup, collect, steer/cancel, unload/reload (S3.2 closed
  set asserted).
- `units/bonsai2-sglang.service` + `units/bonsai2-server@.service` present.
- Verified by `@gpu` (`tests/gpu/test_manager_spawn_2_5.py`,
  `test_lifecycle_2_11.py`).
- Left: not installed on this box (no `/run/bonsai2`, no units). That is
  Phase 6's job, not a Phase-2 defect.

### Phase 3 — harness control scripts + main-agent API: 100% built, functions; not on the box

- `bin/bonsai2_harness.py` (2560 lines) is the single source of truth for the
  S3.2 surface: `spawn`, `status`, `list-instances`, `health`, `cancel`,
  `collect`, `steer`, `unload`, `reload`, `vram`, plus the thin deploy
  wrappers `bin/bonsai2-{spawn,status,...}`.
- S3.1 status family (the `health.json` schema), S3.2 exit-code closed set
  (asserted per command), S3.3 spawn/taskworker, S3.4 capability layer
  (the 7-tool set, host + engine tool execution, workdir + spawn flags,
  `--reasoning-format deepseek` wire).
- `bin/bonsai2-taskworker.py` (1534 lines): the tool-loop engine (`rec
  ["tools"] == "on"` path: stream -> tool-call parse -> host `bonsai2_tools
  .execute` or engine `POST /tools`), the `_tool_log_entry` ledger shape
  (single ledger for both modes), the `--deadline` backstop.
- `bin/bonsai2_tools.py` (651 lines): the 7 host tools
  (`read_file`, `file_glob_search`, `grep_search`,
  `exec_shell_command`, `write_file`, `edit_file`, `get_info`).
- Verified: `@gpu` probe of the engine `/tools` endpoint on the pin
  (`tests/gpu/test_tools_probe.py`); the live pre-prod run confirmed
  `POST /tools {tool:get_info}` -> 200 JSON on both instances, so engine
  mode is real on the pin, not just wired.
- Left: not installed on the box (needs `/run/bonsai2` + the rendered
  `harness.yaml`, Phase 6). The first live run was launched with documented
  env overrides (`BONSAI2_CONFIG`, `BONSAI2_ENGINE_YAML`, `--results-dir`)
  precisely because the install layout did not exist.

### Phase 4 — bench framework: ~90%. Code is complete; the live run (4.10) is the open piece and it exposed the seams

This is the phase the first live run stress-tested, so the detail is here.

- **4.1-4.8 (runner core, scorers, goldens, S5.8 tests): done, functions.**
  `bench/run.py` (1009 lines, the runner), `bench/config.py` (bench.yaml +
  S5.7 schema), `bench/endpoints.py` (endpoint resolver), `bench/exec.py`
  (streaming executor + `score_stream` timing core + `run_concurrent`),
  `bench/boxstate.py` (per-run box snapshot), `bench/scenarios.py` (S5.2
  matrix M1-M10), `bench/report.py` + `bench/rotate.py` (JSON rows + 7-day
  rotation + flock), `bench/score/` (m1-m10 + `_common` + `goldens`),
  `bin/bonsai2-bench` (thin wrapper). 116 S5.8 hermetic tests +
  `tests/fixtures/bench_mock.py` (mock-endpoint executor).
- **4.9 (M9/M10 capability goldens + scorers + driver): done at code.**
  `bench/goldens/m9/` + `bench/goldens/m10/`, `bench/score/m9.py` +
  `bench/score/m10.py` (main-side re-run rubrics), `bench/capability.py`
  (511 lines, the M9/M10 driver through the REAL harness tool-loop seam),
  runner integration (`bench/run.py:_run_capability` routes M9/M10 through
  both `tool_execution` modes, records host-vs-engine comparison),
  `--mock-cap` hermetic seam, tests.
- **4.10 (live full M1-M10 run): PARTIAL.** One pre-prod run executed
  (2 instances, `--tools` launch, 14.78g pool cap each, PQ2_0, q4_0 KV,
  ctx 262144, ports 30101/30102, `--deadline-s 600`). Rows M6, M1, M2, M3,
  M4, M5, M7 landed and are committed. The run **crashed at M9** (driver
  120 s poll window vs 600 s task deadline), so M8, M9, M10 rows never
  landed. No `write_summary` ran (the crash pre-empted it). This is the
  "first live run" the user references; its conclusions are §4.
- **4.11 (S5.5 re-bench policy): done at code.** `bench/rebench.py`
  (531 lines; engine-identity key, quick-bench regression gate, full-bench
  due-detection, baseline save/load; CLI gate/due/save-baseline, exit 0/2/3),
  `rebench_baseline` in `bench/config.py` + `etc/bench.yaml`, 28 hermetic
  tests in `tests/test_rebench.py`. Not yet exercised by a real pin-bump.

Left to make Phase 4 "complete": (a) a clean full M1-M10 run (the hotfix,
§5, then re-run), (b) committing the M8/M9/M10 rows + the host-vs-engine
comparison + the S5.9 #5 verdicts, (c) exercising 4.11 on a real
engine-identity change.

### Phase 5 — harness skill / the doctrine: 0% built. The user's "stateful vs stateless" workflow lives here

Not started. This is the biggest unbuilt component and the one the first
live run's lessons most directly inform:
- `SKILL.md` skeleton (S4.1): when-to-use + command reference = exactly S3.2
  + the prompt templates (workdir contract, definition of done = test
  command + expected exit code, final-answer contract).
- Work-allocation table (S4.2): every row `[M#]`-cited from the Phase-4 run,
  self-report rule amended to "unverified claim WITH EVIDENCE"
  (ledger-first verification: claimed suite ran exit 0, claimed files exist,
  then main re-runs the named check), the tool-loop rows (M9, M10,
  browser-via-python) included.
- Session economy (S4.3) with the re-benched t/s constants replacing the
  a-priori 29/4800 floors + the `--tools on` dispatch-cost term + win rule +
  two-consecutive-loss flip.
- Failure doctrine (S4.5): one paragraph per S3.2 exit code.
- Doctrine audit script (S4.6) + fresh-session test (S4.7) + S4.7 acceptance.

The user's post-run note (PR #21 comment 3) wants the skill to carry **two
identical workflow branches — stateful (subagent stays loaded + retains
context) and stateless (subagent loaded on demand, unloaded after the
task, default = stateless, faster)** — plus the doctrine that the main
agent, whose t/s halved under subagent load, should **plan work into large
clearly-predefined chunks, delegate the grunt work, and do the
dependency-critical thinking while the subagents run.** That is Phase-5
content, and the live run is its evidence.

### Phase 6 — one-command install: 0% built. This is why the live run needed env overrides

Not started. `install.sh` (S6.3): prerequisite checks (S6.2 table),
build-or-verify image + record digest, download model (skip on SHA match),
detect main-stack fresh-load footprint from the SGLang log, compute N via
S1.4, write the four YAMLs (with the S3.4 tool-loop keys), install units,
start the manager, health checks (180 s budget), stack-ready report. Exit
codes 0/4/3/2. Plus `--instances N`, idempotency + main-stack contract,
state files + `--rollback`, `--refresh-main`, the S6.7 clean-box tests, S6.8
acceptance. Until this lands there is no `/opt/bonsai2/{bin,etc,run}`, no
`/run/bonsai2`, no units — so every pre-install live run needs the documented
env overrides + `--results-dir` (pre-run items 1-3).

### Phase 7 — global validation & closeout: 0% built

- Global testing discipline in force (hermetic default, `@gpu` labeled,
  never parallel with a live session, nvidia-smi snapshot first, every test
  asserts a named acceptance).
- Global acceptance #1-#4 (cold boot <= 180 s; fresh session completes the
  standard workload set S3.2-only; full M1-M10 committed with S4.2 rows
  citing bench rows and M8 <= 5%; one-command install S6.8 x4).
- Global acceptance #5 — 72 h soak (all N up, restarts/instance <= 2, no
  main t/s regression beyond 5%, `/run/bonsai2` < 2 GB).
- Post-MVP CI/CD proposal (enable only what the e2e run's observed pain
  justifies; Gitea 1.27.1 self-hosted).
- Closeout: SPEC `status: active`, clear Open-items, final session log, MAP
  fully `[x]`.

---

## 3. Capability-boundary table (what the stack can / cannot do today)

Capability | Has it? | How / boundary
--- | --- | ---
Single subagent instance up + serving | yes (built+tested) | `@gpu` bring-up; not installed on-box yet (Phase 6)
N instances (N=2) under the manager | yes (built+tested) | S2.2/S2.4; exercised at N=2 in the live run
Subagent text completions | yes | M2/M4/M5 live-pass; S1.3 alias + api_key wire
Subagent tool loop (host mode) | yes (built) | S3.4; `bonsai2_tools` 7 tools; verified `@gpu`; live M9/M10 blocked by driver timeout (§4 H1)
Subagent tool loop (engine mode) | yes (built+probed) | `POST /tools` 200 on the pin (live-confirmed); live M9/M10 same driver-timeout block
Main-only control of M1-M5 (M6) | built, NOT live-passing | blocked by key-walled main + no `main_api_key` hook (§4 H3)
Main t/s guardrail (M8) | built, NOT live-reached | needs a valid M6 baseline (H3) + the run to reach M8 (H2)
Long-context read (M5) | yes, 4/5 live | scorer too strict on conjunctions (§4 H8)
Saturation t/s (M7) | yes, live-pass | metric denominator is sum-of-serial not batch-wall (§4 H9)
Capability spec-chunk (M9) + test-batch (M10) | built, blocked live | driver wait window < task deadline (§4 H1); whole-run abort on capability crash (§4 H2)
Re-bench regression gate (S5.5) | built, unexercised | `bench/rebench.py`; not run against a real pin change
Doctrine skill (S4) | not built | Phase 5
One-command install (S6) | not built | Phase 6
Stateful vs stateless subagent workflow | not built | Phase 5 (the user's post-run direction)

---

## 4. Post-flight: what the first live run concluded

The run is authoritative over the SPEC where they disagree (the user's
canonical rule). Read PR #21 comments 3-4 as the primary source; this
section distills it and adds the code-level root causes I verified.

### 4.1 What actually happened (row by row)

| row | verdict | what the live run showed | root cause (verified in code) |
|-----|---------|--------------------------|-------------------------------|
| M6 (main control, first) | fail 0.0 | `main_tps=0.0`; all 21 main completions 401 | `bench/endpoints.py:106` hardcodes `api_key: ""` for main; no `main_api_key` hook in `harness.yaml`/`bench.yaml`; main stack is key-walled |
| M1 (3 code-gen) | fail 0.0 | 3x `ok=True` but `completion_tokens=1`, `content=""`; the whole 4.3-4.5k answer went to `reasoning_content`; rubric compiled an empty artifact | the pin's deepseek reasoning format dumps the answer into `reasoning_content`; `bench/run.py:381` falls back to reasoning as the M1 artifact; `bench/score/m1.py` is binary-strict (compile+suite), so reasoning-only fails by construction |
| M2 (10-task batch) | pass 1.0 | 10/10 correct JSON reports | works |
| M3 (6-section fan-out) | fail 0.7 | auto 6/6 (1.00) but spot-check 0.00; blend 0.70 < 0.75 | `--spotcheck` seam was NOT supplied in the live run; `bench/run.py:404` `cfg["_spotcheck"]` None -> 0.0; M3 is semi-auto and fails at construction without the main's judgment |
| M4 (diff review, 32k) | pass 1.0 | all 10 BUG ids found, decode_tps 21.7 | works |
| M5 (60k doc, 5 QA) | fail 0.8 | 4/5; the miss: golden "M1, M2, M7" vs answer "M1, M2, and M7" | `bench/score/m5.py:59` containment `g in a` is strict — does not tolerate the conjunction "and"; word-boundary / token-set gap |
| M7 (saturation 16 tasks) | pass 1.0 | zero 5xx; per-task 4.5-14.3 t/s; **`aggregate_tps 0.25`** | `bench/exec.py:353` `aggregate_tps = total_tokens / sum(per-task-elapsed)` — that is 370 tokens / 1484 s of *serial* time, not the ~161 s batch wall. The "ceiling t/s" feeds the S5.5 baseline with the wrong denominator |
| M8 | not reached | crashed at M9 first | run order M6,M1,M2,M3,M4,M5,M7,M8,M9,M10; M9 crash aborted the run |
| M9 | crash | `CapabilityError: task not terminal within 120.0s (last state: running)` | `bench/capability.py:312` `run_mode(wait_s=120.0)` default vs the task `--deadline 300` (M9) / 600 (launch); `bench/run.py:715` calls `run_mode` WITHOUT passing `wait_s`, so the driver gives up at 120 s while the taskworker is still running |
| M10 | not reached | M9 crashed first (both modes run M9-host -> M9-engine -> M10-host -> M10-engine) | same as M9 |

### 4.2 The conclusions (the valuable, actionable lessons)

1. **The pieces work together; the capability layer has a driver-timeout defect
   that is the P0 of the run.** `run_mode(wait_s=120)` vs task `--deadline
   600` — any capability task needing > 120 s kills the whole bench run.
   This was the one finding that stops the run; the P2 review items were all
   "note for later."

2. **A capability-row crash aborts the WHOLE run, not just the row.**
   `bench/run.py:464`'s `for` loop lets `CapabilityError` propagate to the
   top-level catch, which maps it to `{"status":"crash"}` and drops M8,
   M9/M10 (both modes). This violates the S5.4 "failure is data" rule, which
   the text-only rows respect (a scenario-level fail is a row verdict, not a
   run crash). The capability path needs the same treatment.

3. **The main endpoint is key-walled but the bench's main endpoint has no
   `api_key`.** M6 and therefore M8 (the S5.9 #1 guardrail) are unmeasurable
   until the bench carries a `main_api_key` hook. The "401-walled is still
   UP" convention is for the HEALTH PROBE, not for completions — the probe is
   fine; the completions 401.

4. **M1 measures content, but the model burns everything into
   `reasoning_content` at this pin.** Either the M1 prompt must force a
   content-only answer, or the rubric must tolerate reasoning-only output.
   This is a SPEC-level seam, not a code bug. It will bite M2/M3-style
   "emit structured output" rows the same way.

5. **M3's semi-auto seam needs a live main-judgment input.** The `--spotcheck`
   flag exists but a live run must drive it (the main reads 1/3 of the
   sections and supplies 0..1). Without it M3 is shape-only and fails at the
   2/3 spot-check threshold by construction. The doctrine (Phase 5) must
   name this as the operator/main step.

6. **M5's scorer is stricter than the doc's phrasing.** The 4/5 was a
   conjunction ("and") the containment check does not tolerate. Long-context
   read itself worked (scattered facts recovered). This is a scorer
   robustness gap, not a model gap.

7. **M7's headline number is the wrong denominator.** `aggregate_tps` divides
   total tokens by the SUM of serial per-task elapsed, not the batch wall.
   The S5.5 re-bench baseline is built on this, so the metric must be
   defined (batch-wall = saturation ceiling) before the gate is trusted.

8. **The engine `/tools` endpoint is real on the pin** (`get_info` -> 200
   JSON on both instances) — S1.3 "engine mode runs the 7-tool set inside
   the instance's cgroup" is confirmed, not just wired.

9. **VRAM climbed 100 -> 116-117 GiB during the run and stayed 117 post-run
   (4.8 GiB free).** Two issues: (a) the in-run climb (KV-cache growth + host
   tool subprocesses + the main agent's own context under the 16-wide M7) and
   (b) no teardown — the instances were not unloaded at run end. The release
   mechanism exists (`bonsai2-unload`, manager-mediated) but neither the bench
   nor a doctrine tells the main agent to use it. The user wants this baked
   into the skill.

10. **Cache memory was ~99% with main + 2 instances.** On a unified-memory
    GB10 that is the reclaimable page-cache / shared-memory pool
    (`buff/cache` in `/proc/meminfo`, `Shmem`). It is not a leak by itself,
    but at 99% with the pool fully loaded it is the headroom the kernel can
    reclaim; the box-state snapshot should record `MemAvailable` + `Shmem`
    trend so a run can distinguish reclaimable from resident.

11. **The main agent's t/s fell 30 -> 15 under subagent load (expected).**
    This is exactly what the M8 guardrail (5%) is for, and it is the reason
    the skill must teach the main agent to delegate large, clearly-predefined
    chunks and do the dependency-critical thinking while the subagents run
    (so the halved t/s hits grunt work, not critical-path thinking).

12. **CPU was 4-30%, GPU 96%.** The engine is GPU-bound; CPU headroom means we
    are not CPU-limited, so raising concurrency/parallelism (not CPU) is the
    lever. More CPU does not directly raise t/s here.

### 4.3 What is a design boundary (locked non-goal) vs a not-yet-built hole

To keep the hotfix honest, the "holes" split into:
- **Design boundary (locked, do not build):** stateful subagents,
  LLM-to-LLM orchestration, main-stack changes, vision/mmproj, multi-box v1,
  fine-tuning, LLM-judge bench mode, kill-for-drift, dedicated browser tool.
- **Not-yet-built (Phase 5/6/7, their own PRs):** the doctrine skill,
  `install.sh`, the 72 h soak, the CI/CD proposal.
- **In-scope hotfix (Phase-4 seams the live run exposed):** §5. These are the
  "obvious glaring holes" that block a clean full M1-M10 run today.

---

## 5. Proposed action plan: the hotfix patch PR

One PR, cumulative, on a `hotfix-post-live-run` branch off `main @ f7c768d`.
Goal: make the NEXT live M1-M10 run clean — M6/M8 measurable, M1/M3/M5
scorable, M7's metric sound, M9/M10 reaching terminal without aborting the
run, and the run leaving no leaked workdirs or stranded instances. Every item
is "why / what / where / how" and maps to a spec amendment where one is
needed (spec follows code; the live run is canonical).

The items are ordered by the payoff of the fix and the risk of not doing it.
H1-H5 are the P0/P1 that block a clean run; H6-H12 are the P2 that make the
run's data trustworthy and repeatable.

### H1 — M9/M10 driver wait window must cover the task deadline (P0, the run-stopper)

- **Why:** `run_mode(wait_s=120.0)` gives up at 120 s while the task's
  `--deadline` is 300 s (M9/M10) or 600 s (launch). The live M9 crash is this
  exact mismatch. This is the single defect that stopped the whole run.
- **What:** the driver's terminal-poll window must be `task deadline + a poll
  margin`, not a hardcoded 120 s.
- **Where:** `bench/capability.py:312` (`wait_s` default) and the call site
  `bench/run.py:715` (`_capability_live` -> `bcap.run_mode(...)`).
- **How:** change the `run_mode` signature to derive the wait from the
  deadline: `wait_s: float | None = None` and inside
  `wait_s = wait_s if wait_s is not None else deadline_s + 60.0` (a 60 s
  poll margin over the task's own backstop). Remove the 120.0 literal.
  `bench/run.py:_capability_live` already passes `deadline_s`, so it will flow
  through. Add a hermetic test: a `mock_cap` that is not terminal by 120 s but
  is by 360 s must PASS the M9/M10 row (not crash). Update
  `tests/test_harness_tools_loop.py` + `tests/test_bench_runner.py` for the new
  default.
- **Spec:** S5.2/S5.9 — the capability row's driver wait = the S5.2 ctx-budget
  deadline + poll margin. (Amendment note, not a locked-value move.)

### H2 — a capability-row failure is a ROW verdict, not a run crash (P1)

- **Why:** today a `CapabilityError` aborts the entire run (M8, M9/M10 all
  dropped). The text-only rows already treat a scenario-level fail as a row
  verdict (S5.4 "failure is data"); the capability path does not.
- **What:** wrap the capability execution so a `CapabilityError` produces a
  failed capability row (row verdict `fail`, `status` stays `ok`, `detail`
  carries the error + last task state) and the run continues to M8 and the
  remaining modes.
- **Where:** `bench/run.py:_run_capability` (635-681) and the per-mode loop.
- **How:** in the `for mode in modes` loop, catch `CapabilityError` (and the
  non-terminal case) and build a degraded `_capability_mode_row` with
  `row_verdict="fail"` + the error text, instead of raising. Preserve the
  host-vs-engine comparison (a crashed mode records `score: 0.0` + the error
  in the comparison). Add a hermetic test: a `mock_cap` that raises for one
  mode must yield a two-row comparison where the crashed mode is `fail` and
  the run still writes all rows + the summary.
- **Spec:** S5.4 — extend the "exit 0 even on scenario-level failures" rule to
  the capability path explicitly.

### H3 — main endpoint `api_key` hook (P1, unblocks M6 + M8)

- **Why:** `bench/endpoints.py:106` hardcodes `api_key: ""` for the main
  endpoint; the live main stack is key-walled (401 on no-key completions).
  M6 -> 0.0, M8 -> no baseline. The "401-walled is UP" convention is for the
  HEALTH PROBE only; completions need the key.
- **What:** a `main_api_key` key in `harness.yaml` (and mirrored in
  `bench.yaml` if the bench reads it) that `main_endpoint()` carries on the
  resolved endpoint; the executor already sends `Bearer` when the key is
  non-empty (`bench/exec.py:234`), so no executor change is needed.
- **Where:** `bench/endpoints.py:89-108` (`main_endpoint`),
  `etc/harness.yaml` (add `main_api_key:` with a comment; default empty so a
  keyless main still works), `etc/bench.yaml` (mirror), `bench/config.py`
  (schema).
- **How:** `main_endpoint()` reads `harness.get("main_api_key", "")` and puts
  it on the returned dict's `api_key`. Back-compatible: empty key = no
  Authorization header (today's behavior). Add a hermetic test: a key-walled
  main (mock returning 401 without key, 200 with) -> M6 control measures a
  positive `main_tps` and M8's guardrail baseline is valid.
- **Spec:** S3.6 — the `harness.yaml` main-endpoint contract gains
  `main_api_key` (optional; empty = keyless). Document that the health probe
  stays 401-tolerant while completions use the key.

### H4 — M1 reasoning/content seam (P1, a SPEC-level decision)

- **Why:** the pin's deepseek reasoning format puts the answer in
  `reasoning_content` and emits 1 content token; the M1 rubric (binary
  compile+suite) measured the reasoning monologue and failed 0/3. This will
  recur on any "emit structured output" row.
- **What (decision — A only, per admin §0.1 "1a"; B recorded as fallback, not implemented):**
  - Option A (recommended): **prompt-side** — the M1/M2-style task prompt
    explicitly says "your final answer must be EXACTLY the fenced blocks, no
    prose and no reasoning in the answer," and the runner's M1 payload keeps
    the current prefer-content-then-reasoning fallback. This keeps the rubric
    strict (it should: a real artifact must compile).
  - Option B: **rubric-side** — the M1 rubric tolerates reasoning-only output
    when the content is < 2 tokens and the reasoning carries a parseable
    fence. Looser, risks rewarding non-artifacts.
  - **A only** (the admin decision, §0.1 "1a"): A is what is implemented in
    this PR — the content-only prompt, the rubric stays strict, **no config
    flag**. B is recorded as the FALLBACK (the uncertainty rule: both
    surfaced so the user decides against observed results) but is **NOT
    implemented** — no `m1_allow_reasoning_only` flag in `bench.yaml` /
    `bench/score/m1.py` ships in this diff. If the live re-run shows the
    content-only prompt under-delivers, B is the follow-up lever.
- **Where (A, as implemented):** `bench/run.py:100-108` (M1 prompt),
  `bench/run.py:379-381` (M1 payload builder keeps the
  prefer-content-then-reasoning fallback as a last resort),
  `bench/scenarios.py` (M1 shape note). B (fallback, NOT implemented)
  would be: `etc/bench.yaml` (the flag) + `bench/score/m1.py` (a branch
  that, when the flag is set and `n_content_chunks < 2`, parses the fence
  out of `reasoning` instead of failing).
- **How (A):** tighten the M1 prompt to "answer = exactly two ```python
  fences"; keep the `t.answer or t.reasoning` fallback. Hermetic: a
  reasoning-only M1 payload still fails the strict rubric when content is
  empty (documents A's behavior); a content-carried fence pair passes
  (the existing golden). (B, if taken as the follow-up, would add its own
  hermetic test.)
- **Spec:** S5.2 M1 — record the seam: at the pinned engine the model's
  answer lands in `reasoning_content`; the rubric measures the content
  artifact; the M1 prompt forces content-only. Before/after note if the
  default moves.

### H5 — M3 spot-check seam: name it, and stop failing silently (P1)

- **Why:** M3 is semi-auto; without `--spotcheck` it is shape-only and fails
  at the 2/3 threshold by construction (the live run got 0.70). Today the
  failure detail does not say "the seam was not driven" — it just blends to
  0.70.
- **What:** when `--spotcheck` is None, record `spotcheck: "unjudged"` (not
  0.0) and a named detail `M3: spot-check seam not driven (supply --spotcheck
  0..1)`; the blend still computes but the row's `detail` makes the operator
  step explicit. This is the minimal robust fix; the DOCTRINE (Phase 5) then
  names the main-agent step that drives `--spotcheck`.
- **Where:** `bench/score/m3.py:79-98` (the spot-check handling),
  `bench/run.py:404` (`cfg["_spotcheck"]`), `bench/run.py:438-442`
  (`--spotcheck` plumbing).
- **How:** in `bench/score/m3.py`, when `spot is None` (not a number), set
  `spot_display = "unjudged"` and keep the numeric blend (0.0 weight) but add
  the named detail. Distinguish "not driven" (None) from "judged 0.0"
  (0.0). Hermetic: an M3 payload with `spotcheck=None` -> detail contains
  "not driven", score == auto*0.7.
- **Spec:** S5.3 M3 — the spot-check is a live main-agent input; an undriven
  seam is recorded as "unjudged," not silently 0.0.

### H6 — M9/M10 workdir leak (P2, confirmed live)

- **Why:** `run_mode`'s `finally` only calls `_tmp.cleanup()` (the harness
  temp), not the `stage_workdir` `mkdtemp` — every live capability call leaks
  a `/tmp/bonsai2-m{9,10}-*` workdir. The docstring claims "the driver cleans
  it on the live path." Confirmed in the live run (the staged M9 workdir
  survived the crash).
- **Where:** `bench/capability.py:367` (workdir created), `:484-487`
  (`try/return/finally`).
- **How:** track the workdir and `shutil.rmtree(workdir, ignore_errors=True)`
  in the same `finally` (after `_tmp.cleanup()`), or make `stage_workdir`
  return a context-managed object. Add a hermetic test: after a `run_mode`
  call the staged workdir path no longer exists.
- **Spec:** none (code-only; the S5.2 workdir-contract docstring becomes true).

### H7 — M9 tool_log scorer: don't early-return on a non-zero match (P2)

- **Why:** `_tool_log_shows_test_run` returns `False` on the FIRST
  `bash`/`python` entry that mentions the suite if its `exit_code` is
  non-zero, instead of continuing to later entries. A failed-then-successful
  suite run is mis-scored as "no successful run."
- **Where:** `bench/score/m9.py:111-138` (the loop's `return False` at 137).
- **How:** remove the early `return False`; on a non-zero match, `continue` to
  the next entry; only return `False` after the loop (no entry ran the suite
  with exit 0). Hermetic: a tool_log with [failed-suite-run, successful-run]
  -> pass the sub-condition.
- **Spec:** none (S5.9 #5 "tool_log shows the test run with exit 0" — the
  fix makes the scorer actually mean "there EXISTS a successful run").

### H8 — M5 scorer: tolerate conjunctions / word boundaries (P2)

- **Why:** the live miss was golden "M1, M2, M7" vs answer "M1, M2, and M7";
  `bench/score/m5.py:59` containment `g in a` does not strip the conjunction.
- **What:** a tolerant matcher: normalize both, strip common conjunction
  particles (`and`, `or`, `the`, `a`, `an`), then score by token-set overlap
  with a threshold (e.g., all golden tokens present in the answer = pass;
  containment is the strict special case). This keeps "exact match is a
  special case of containment" but stops failing on "and."
- **Where:** `bench/score/m5.py:42-59` (`_normalize` + `_match`).
- **How:** add `_STOP = frozenset({"and","or","the","a","an"})`; in
  `_normalize` drop those tokens; in `_match` require every golden token to
  appear in the answer's token set (ordered containment still wins when it
  holds). Hermetic: "M1, M2, M7" vs "M1, M2, and M7" -> pass; "M1, M2" vs
  "M1, M2, M7" -> fail (missing M7).
- **Spec:** S5.3 M5 — the matcher is tolerant of conjunctions/word-boundary
  phrasing, strict on missing facts.

### H9 — M7 aggregate t/s: record the batch wall, define the metric (P2)

- **Why:** `bench/exec.py:353` computes `aggregate_tps = total_tokens /
  sum(per-task-elapsed)` — 370 tokens / 1484 s of SERIAL time = 0.25, not the
  ~161 s batch wall (370 / 161 = 2.3). The S5.5 re-bench baseline is built on
  this, so the "ceiling t/s" must be defined.
- **What:** record BOTH in the aggregate: `aggregate_tps_serial` (total
  tokens / sum-serial — the decode-throughput invariant) AND
  `aggregate_tps_wall` (total tokens / batch wall-clock). Declare which one
  is the S5.5 baseline. Recommended baseline = `aggregate_tps_wall` (the
  "t/s at the concurrency ceiling" is a wall-clock saturation figure).
- **Where:** `bench/exec.py:351-364` (`run_concurrent` aggregate),
  `bench/score/m7.py` (the rubric's `aggregate_tps` read), `bench/rebench.py`
  (the gate's baseline field).
- **How:** measure the batch wall in `run_concurrent` (the `with
  ThreadPoolExecutor` block's real elapsed), add
  `total_wall_s` + `aggregate_tps_wall` to `agg`; keep
  `aggregate_tps` as an alias for the wall value (or rename and update the
  readers). Update the M7 rubric + the S5.5 gate to read the wall value.
  Hermetic: a 2-task concurrent run -> `aggregate_tps_wall` is computed from
  the batch wall, not the sum.
- **Spec:** S5.1/S5.5 — "t/s at the ceiling" = total tokens / batch
  wall-clock at the S5.2 concurrency column; the serial sum is recorded as
  `aggregate_tps_serial` for the decode-throughput comparison. Before/after
  on the recorded M7 figure (0.25 serial -> ~2.3 wall on this run).

### H10 — rebench `_config_value`: don't swallow a malformed `--config` (P2)

- **Why:** `bench/rebench.py:394-409` silently falls back to the default on a
  bad/absent `--config`, so a typo'd `bench.yaml` does not surface the
  documented exit-2 "bad input" path.
- **Where:** `bench/rebench.py:394` (`_config_value`).
- **How:** distinguish "file absent" (use default, as today) from "file
  present but unparseable" (return a sentinel the caller turns into exit-2
  "bad input"). Add a hermetic test: a malformed `--config` -> exit 2.
- **Spec:** S5.7/S5.5 — a present-but-malformed bench.yaml is a named bad-input
  error, not a silent default.

### H11 — bench `results_dir` pre-install usability (P2)

- **Why:** the default `results_dir: /run/bonsai2/bench` assumes the installed
  layout; pre-install live runs need `--results-dir` (pre-run item 1).
- **Where:** `etc/bench.yaml`, `bench/config.py` (validation), `bench/run.py`
  (the `results_dir.mkdir(parents=True, exist_ok=True)` at 443).
- **How:** keep the default (it is correct post-install) but (a) make the
  "does not exist yet" WARN actionable (state the flag), and (b) document
  `--results-dir` in `bench-results/README.md` + the pre-prod handoff as the
  pre-install path. No behavior change; a docs + WARN-clarity item.
- **Spec:** S5.2/S5.7 — the pre-install live-run convention (env overrides +
  `--results-dir`) is the documented path until `install.sh` (Phase 6) lands.

### H12 — box-state snapshot: record reclaimable + VRAM-climb signal (P2)

- **Why:** the run's VRAM climbed 100 -> 116 and the cache was ~99%; the
  current snapshot (`bench/boxstate.py`) records nvidia-smi, docker stats,
  `MemTotal`/`MemAvailable` — not `Shmem`/`Cached` (the reclaimable pool) and
  not a trend. A row should let a later reader distinguish resident from
  reclaimable memory.
- **Where:** `bench/boxstate.py:104-110` (`meminfo`), `:112-134`
  (`snapshot`).
- **How:** extend `meminfo()` to include `Cached`, `Shmem`, `SReclaimable`;
  add `meminfo_trend` is overkill for v1 — instead record a second snapshot
  at run END (the runner already snapshots per row; add a final post-run
  snapshot field to the run JSON) so a climb is visible in the committed
  summary. Hermetic: the snapshot dict carries the new fields.
- **Spec:** S5.2 — the box-state snapshot carries the reclaimable-memory
  fields + a post-run snapshot (data, not a verdict).

### H13 (borderline Phase-5/6, include if cheap) — post-run instance teardown option

- **Why:** post-run VRAM stayed 117/121 because the instances were not
  unloaded. The user wants a release mechanism, and wants the skill to teach
  the main agent to unload after delegating (stateless default).
- **What (hotfix half):** a bench `--unload-after` flag (default off) that,
  after the run writes all rows + the summary, shells out to
  `bin/bonsai2-unload --all` (manager-mediated) so a live run leaves no
  stranded instances. The DOCTRINE (Phase 5) then makes unload-after the
  main-agent's default in the stateless branch.
- **Where:** `bench/run.py` (the flag + the post-`write_summary` teardown),
  `bin/bonsai2-bench` (the flag passthrough).
- **How:** add `--unload-after` (store_true); after `report.write_summary`,
  if set, run `bin/bonsai2-unload --all` (subprocess, read-only docker
  against the manager) and record the result in the run JSON. Hermetic: with
  `--mock` the teardown is a no-op; the flag is recorded. Keep it OFF by
  default (it is a state change the operator may not want mid-experiment).
- **Spec:** S4.2/S4.3 — the stateless branch unloads after the task; the
  bench exposes `--unload-after` for the same. Marked borderline because the
  *doctrine* half is Phase 5; this item is only the *mechanism*.

### What the hotfix PR does NOT touch (out of its DOD, left to their own PRs)

- Phase 5 (the doctrine skill + the stateful/stateless branches) — the hotfix
  H13 mechanism feeds it, but the skill is a separate PR.
- Phase 6 (`install.sh`) — H11 documents the pre-install path; the installer
  is its own PR.
- Phase 7 (soak, CI/CD, closeout).
- The re-run of the live M1-M10 itself (4.10's "clean" half) — that happens
  AFTER the hotfix merges, as its own `@gpu` step that lands the M8/M9/M10
  rows + the summary.

### PR-body contract for the hotfix (pre-written, per AGENTS.md)

- **`## Definition of Done`:** every H1-H12 (and H13 if included) has a
  named hermetic test that passes; the capability path (H1+H2) is proven by
  a `--mock-cap` run where a slow/non-terminal capability task no longer
  aborts the run; the full hermetic suite is green; `./scripts/gate.sh`
  PASS; the mock `--quick` M1+M2+M7 run writes rows + a summary. NOT claimed:
  a live M1-M10 re-run (that is the follow-up `@gpu` step).
- **`## Spec amendments`:** H3 (S3.6 `main_api_key`), H4 (S5.2 M1
  reasoning/content seam), H5 (S5.3 M3 "unjudged" seam), H9 (S5.1/S5.5 M7
  ceiling denominator), H10 (S5.7 malformed-config exit), H12 (S5.2 snapshot
  fields). Each with the before/after where a value or default moves.
- **`## Uncertainties & decisions`:** H4 (option A vs B behind a flag, both
  tested, reported) and H9 (which M7 metric is the S5.5 baseline) are the two
  forks to record in the fixed 5-subsection shape.
- **Checks + residual risk:** exact hermetic count, gate PASS, the `@gpu`
  steps that are NOT in this PR (the live re-run), and the residual
  (Phase-5/6/7 still unbuilt).

---

## 6. Spec amendments this analysis forces (recorded, to land in the hotfix PR)

Per the spec-amendment rule (spec follows code; the live run is canonical):

1. **S3.6** — `harness.yaml` main-endpoint contract gains `main_api_key`
   (optional; empty = keyless; health probe stays 401-tolerant, completions
   use the key). *Expected:* main had no key in the bench. *Real:* the live
   main is key-walled. *Now:* the hook exists.
2. **S5.2 M1** — at the pinned engine the model's answer lands in
   `reasoning_content`; the M1 rubric measures the content artifact; the M1
   prompt forces a content-only fenced answer (Option A only, per admin
   §0.1 — the rubric-side Option B, a `m1_allow_reasoning_only` relax
   flag, is recorded as the fallback but NOT implemented in this PR).
   *Expected:* the answer is in `content`. *Real:*
   it is in `reasoning_content`, 1 content token. *Now:* seam documented.
3. **S5.3 M3** — the spot-check is a live main-agent input; an undriven seam
   is recorded `unjudged`, not silently 0.0. *Expected:* the seam is driven
   by the live main. *Real:* the live run did not drive it. *Now:* named.
4. **S5.1/S5.5 M7** — "t/s at the ceiling" = total tokens / batch wall-clock;
   the serial sum is recorded as `aggregate_tps_serial`. *Expected:* the
   aggregate was the ceiling. *Real:* it was 0.25 (serial), not ~2.3 (wall).
   *Now:* wall is the S5.5 baseline.
5. **S5.4** — "exit 0 even on scenario-level failures" extends to the
   capability path (a `CapabilityError` is a row verdict, not a run crash).
6. **S5.2/S5.9 #5** — the capability row's driver wait = the S5.2 ctx-budget
   deadline + a poll margin (not a hardcoded 120 s).
7. **S5.7** — a present-but-malformed `bench.yaml` is a named exit-2 bad-input
   error, not a silent default.
8. **S5.2** — the box-state snapshot carries the reclaimable-memory fields
   (`Cached`/`Shmem`/`SReclaimable`) + a post-run snapshot.

---

## 7. Capability roadmap (what is built vs what is to come)

- **Built (Phases 0-4):** engine + image (S1), manager + lifecycle (S2),
  harness control + capability layer (S3), bench framework + capability
  rows + re-bench gate (S5). All verified in-test; S1-S3 not yet installed on
  the box (Phase 6).
- **To come (Phases 5-7):**
  - **Phase 5 (doctrine skill):** the `bonsai2-harness` skill — command
    reference = S3.2, the work-allocation table with `[M#]` citations from the
    Phase-4 run, session economy with the re-benched t/s constants, the
    failure doctrine, the doctrine audit + fresh-session test, AND the two
    stateful/stateless workflow branches (stateless default: load subagent ->
    delegate large predefined chunk -> unload -> main verifies at full t/s ->
    re-spawn as needed; the main plans the critical-path thinking while the
    subagents do the grunt work).
  - **Phase 6 (install):** `install.sh` one-command deploy (prereqs, image,
    model, N, the four YAMLs, units, manager, health, stack-ready report,
    idempotency, rollback, `--refresh-main`, clean-box tests, S6.8).
  - **Phase 7 (closeout):** global acceptance #1-#5, the 72 h soak, the
    post-MVP CI/CD proposal (enable only what the e2e run's observed pain
    justifies), SPEC `status: active`.
- **Extra capabilities that would need adding (locked non-goals, with cost):**
  stateful subagents (context retention across tasks — currently a locked
  non-goal; cost: per-task context management + the stateful branch),
  LLM-to-LLM orchestration (the main driving subagents through an LLM
  interface rather than bash — cost: a new control surface beyond S3.2),
  vision/mmproj (loading the 0.63 GB mmproj-Q8_0 — cost: a vision path +
  the S1.3 shape change), a dedicated browser tool (cost: an 8th tool + the
  camoufox venv wiring), LLM-judge bench mode (cost: a judge endpoint + a new
  rubric class), multi-box v1 (cost: a distribution layer).

---

## 8. Machine-state snapshot (what is installed/running/cached now)

- `/opt/bonsai2/cache/`: PQ2_0 + PTQ1_0 (SHA-verified at image build).
- Images: `bonsai2-server:1.0` (16.1 GB, digest in
  `etc/subagent-engine.yaml`), `bonsai2-server-build:121a` (7.99 GB).
- No `/opt/bonsai2/{bin,etc,run}` (Phase 6 not run); no
  `/run/bonsai2`; no bonsai2 systemd units; no bonsai2 containers running.
- Main stack: `qwen38-sglang` up 3 days, 1.18 GiB live (asleep in cgroup),
  key-walled (:30001, no-key completions -> 401).
- Unified memory: 121 GiB total, ~51 GiB `MemAvailable` at rest.
- Live-run rows committed at `bench-results/` (M6, M1, M2, M3, M4, M5, M7 +
  `runner-stdout.log`); M8/M9/M10 absent (the run crashed at M9).
- Hermetic suite: 583 tests OK; `@gpu` suite: 25 tests (not run this session;
  no instances up).

---

## 9. Open uncertainties to bring to the user (the closing list)

1. **H4 default:** is the M1/M2-style "answer must be content-only" (Option
   A) acceptable, or should the rubric tolerate reasoning-only (Option B,
   behind a flag)? Both are implemented + tested in the hotfix; the default
   is A.
2. **H9 baseline:** is the S5.5 re-bench baseline the batch-WALL t/s
   (recommended, "ceiling") or the serial-sum t/s? The live M7 figure moves
   from 0.25 (serial) to ~2.3 (wall).
3. **H13 scope:** include `--unload-after` in the hotfix (mechanism only,
   default off), or leave it entirely to Phase 5/6? Recommended: include it,
   cheap + directly answers the "we need a release mechanism" observation.
4. **M3 spot-check in the live re-run:** who drives `--spotcheck` in the next
   live run — the main agent reads 1/3 of the sections and supplies 0..1
   (the intended S5.3 seam), or do we record `unjudged` and treat M3 as
   shape-only for the pre-prod pass? The hotfix makes "unjudged" explicit
   either way.
5. **The 7.21 GB PQ2_0 vs 5.95 GB PTQ1_0 choice:** the live run used PQ2_0
   (the SPEC default). If the post-run VRAM (117/121) is too tight at N=2,
   PTQ1_0 is the S2.4 fallback (smaller footprint). Flag for the Phase-6 N
   computation; not a hotfix item.
6. **The main t/s 30 -> 15 under load:** expected, and it is the M8 guardrail's
   job to bound it. The Phase-5 doctrine must turn this into the
   "delegate-the-grunt-work, keep-the-critical-thinking" planning rule. No
   hotfix code; doctrine content.
