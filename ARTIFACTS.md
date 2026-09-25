# qwen-tory38 — ARTIFACTS.md

Where every external thing lives. Rule: anything created outside the
repo (files, binaries, images, systemd units, tools, caches) MUST appear
here with path + pin + the MAP item that created it. No orphaned
artifacts. Update this file in the same PR that creates/moves/removes
an artifact.

Legend: P = pinned (version/SHA recorded), C = created by this project,
E = external (pre-existing, consumed read-only).

## In-repo

| artifact | location | pin | created by |
|---|---|---|---|
| llama.cpp fork source (prism branch) | `third_party/llama.cpp` (submodule, added 2026-09-20) | fork commit `9a9394a8` (prism tip at MAP 1.1; 2026-09-18), recorded in `etc/subagent-engine.yaml` `engine_pin` (rendered to `/opt/bonsai2/etc/` by install.sh) | MAP 1.1 (vendoring decided: submodule — in-repo, project-wide, never system-wide) |
| systemd unit templates | `units/bonsai2-sglang.service` (manager, Type=notify) + `units/bonsai2-server@.service` (per-instance template; install.sh renders ExecStart per granted instance) | S2.1/S2.2 shape hermetic-asserted in `tests/test_manager.py::TestUnitFiles` | MAP 2.4 (templates only — the concrete units are rendered/installed by install.sh, MAP 6.2) |
| bench goldens | `bench/goldens/` | versioned in repo | MAP 4.4 |
| subagent capability layer — host-side 4-tool module | `bin/bonsai2_tools.py` (deployed to `/opt/bonsai2/bin/` by install.sh; imported by the taskworker's tool loop, S3.4) | stdlib-only; tools: bash(cmd, timeout) / read_file(path) / write_file(path, content) / python(code, timeout); workdir containment (escape above the workdir root rejected — `/`-rooted and `..`-crossing paths; symlinks in allowed, symlinks out rejected at open); process-group kill at `tool_timeout_s`; output truncation at `tool_output_max_kb` | MAP 3.13 (S3.4 tool loop; spec amendment 2026-09-25) |

## This box (pre-existing, external — consumed read-only)

| artifact | location | note |
|---|---|---|
| Main agent engine | container `qwen38-sglang` (systemd `qwen38-sglang` + `qwen38-keepalive`), endpoint `http://localhost:30001` | image `qwen38-dflash2:v1.2.3`; fresh-load footprint ~76.4 GB; sleep_on_idle — idle cgroup reads 8–15 GB, that is NOT the budgeting state |
| Main-stack source (their lane, not ours) | `/home/blckwll/prod/dgx-spark-qwen38/` | install.sh / switch-model.sh / bench.sh / cockpit :30090 |
| Bonsai PTQ1_0 weights | `/home/blckwll/.lmstudio/models/prism-ai/Ternary-Bonsai-2-27B-PTQ1_0-GGUF/Ternary-Bonsai-2-27B-PTQ1_0.gguf` | 5.95 GB, PTQ1_0 = SPEC S1.1 fallback quant; LM Studio copy — do not move |
| Bonsai mmproj (optional, NOT loaded in v1) | `/home/blckwll/.lmstudio/models/prism-ai/Ternary-Bonsai-2-27B-PTQ1_0-GGUF/Ternary-Bonsai-2-27B-mmproj-Q8_0.gguf` | 0.63 GB; vision is non-goal (text-only v1) |
| Gitea | `blckwell/qwen-tory38`, api `http://localhost:3001`, ssh `gitea-local:2222` | v1.27.1; PR/merge gate for senior-git-1.0 |

## Created by this project (fill in as MAP phases complete)

<!-- format: | artifact | path | pin (version/SHA/digest) | MAP item | created | -->

| artifact | path | pin | created by |
|---|---|---|---|
| CUDA 13.0 build container (local image) | docker `nvidia/cuda:13.0.2-devel-ubuntu24.04` | `nvidia/cuda@sha256:5dc1bca23d05bd37b011be68ec470c03b403a5da07ec3a86e41af9470e9d0cc6` (multi-arch manifest; aarch64 verified) | MAP 0.3 (build stage for MAP 1.2; needs `apt-get install cmake ninja-build` inside — see MAP 0.3) |
| bonsai2 fork build pipeline (local image) | docker `bonsai2-server-build:121a` (repo `image/Dockerfile`), binary at `/opt/bonsai2/bin/llama-server` inside | build inputs: fork pin `9a9394a8` + CUDA image digest above + `libnvidia-compute-580` (driver lib, NVIDIA apt repo); image ID recorded in the build log | MAP 1.2 (builds the S1.2 binary for the MAP 1.4 runtime image `bonsai2-server:<tag>`; rebuild on any pin change) |
| bonsai2 model fetcher (repo source) | `bin/bonsai2_model_fetch.py` (deployed to `/opt/bonsai2/bin/` by install.sh) | stdlib-only + PyYAML config read; pins from `etc/subagent-engine.yaml` `model` block (SHA256 + size) | MAP 1.3 (S1.1 download + SHA256 verify; retry ×3 / 10 s backoff; `ERR_HASH_MISMATCH`/`ERR_DOWNLOAD` → exit 4; hermetic tests `tests/test_model_fetch.py`) |
| bonsai2 PQ2_0 + PTQ1_0 weights (model cache) | `/opt/bonsai2/cache/bonsai-2-27b-gguf-PQ2_0.gguf` (7.21 GB) + `bonsai-2-27b-gguf-PTQ1_0.gguf` (5.95 GB) | pinned: PQ2_0 sha256 `3907dc16…ec1` (7206168928 B), PTQ1_0 sha256 `53107f53…3ee3` (5946648928 B); HF source objects `Ternary-Bonsai-2-27B-{PQ2_0,PTQ1_0}.gguf`, revision `main` @ `6ed5e12b` (2026-09-20) | MAP 1.3 (downloaded + SHA-verified; S1.3 serving command reads the on-disk names from here, not the HF object names) |
| CUDA 13.0 runtime base (local image) | docker `nvidia/cuda:13.0.2-runtime-ubuntu24.04` | `nvidia/cuda@sha256:6a0e31b59e70890446f2c17356d6efc0d54260090a8c63d6ca7c2ad049db95d2` (multi-arch manifest; aarch64 entry verified 2026-09-21) | MAP 1.4 (S6.1 "CUDA 13.0 runtime" for the runtime image — the RUNTIME variant, not MAP 1.2's devel image) |
| bonsai2 runtime image | docker `bonsai2-server:1.0` (repo `image/Dockerfile.runtime` + `bin/bonsai2_build_image.py`) | build inputs: MAP 1.2 `bonsai2-server-build:121a` (binary) + MAP 1.3 cache files (in-build sha256+size check) + runtime base above; content digest recorded in `etc/subagent-engine.yaml` `image.pin`; **re-baked 2026-09-24 with python3 baked in (the S2.8 #4 @gpu cgroup-charge holder — the CUDA runtime base ships no python; in-build assertion) and re-pinned: digest `sha256:6b4cd79f…` (build log `test-logs/build-runtime-20260924-132403.log`; prior digest `sha256:ed6068ae…` from the MAP 1.7/1.8 re-bake)** | MAP 1.4 (S1.6 image_pin: {tag, sha256}; S1.3 verbatim CMD); re-bake in the PR #13 hotfix (docs/two-red-gpu-tests-fix.md) |
| bonsai2 runtime-image build orchestrator (repo source) | `bin/bonsai2_build_image.py` (deployed to `/opt/bonsai2/bin/` by install.sh) | stdlib + PyYAML config read; pins from `etc/subagent-engine.yaml` (engine.pin.commit, model block); exit 0/2/4 (ERR_CONFIG / ERR_HASH_MISMATCH) | MAP 1.4 (S6.1 build stage: cache pre-verify → docker buildx → digest read-back → image.pin write; hermetic tests `tests/test_image_build.py`, @gpu `tests/gpu/test_runtime_image.py`) |
| bonsai2 engine-config writer + validator (repo source) | `bin/bonsai2_render_config.py` | stdlib validation core + PyYAML edge; S1.6 shape + S1.7 bounds (port math, ctx/parallel, sampling ranges); exit 0/2 | MAP 1.5 (the install.sh YAML writer, S6.3; hermetic tests `tests/test_render_config.py`) |
| bonsai2 VRAM formula (repo source) | `bin/bonsai2_vram.py` | stdlib-only pure functions: `instance_footprint_gb`, `instance_count` (S1.4 clamp [0,8]), `subagent_budget_gb` (S0 table) | MAP 1.6 (S1.4/S1.7 unit half; hermetic tests `tests/test_vram.py`) |
| bonsai2 instance manager (repo source) | `bin/bonsai2-manager` (deployed to `/opt/bonsai2/bin/bonsai2-manager` by install.sh; runs from the `bonsai2-sglang.service` unit, S2.2) | stdlib core + PyYAML edge; imports `bonsai2_vram` for the S1.4 tier need; exit 0 (full/partial/main-only) / 4 (ERR_VRAM_OOM at i=0) / 2 (ERR_CONFIG) | created at MAP 2.1 (S2.2 skeleton + S3.1 health.json), extended through MAP 2.6–2.11 (escalation backstop, N=0/PTQ1_0 fallback, drift watch, measured-boot writeback, S3.2 control queue); S2.3 boot tier + S1.4 allocation tier + S2.5 ordered spawn; hermetic `tests/test_manager.py`, @gpu `tests/gpu/test_manager_spawn_2_5.py` + `tests/gpu/test_lifecycle_2_11.py` (S2.8 acceptance, green 2026-09-24 after the PR #13 hotfix) |
| bonsai2 harness core library (repo source) | `bin/bonsai2_harness.py` (deployed to `/opt/bonsai2/bin/` by install.sh; imported by the S3.2 command files) | stdlib core + PyYAML config edge; S3.1 one-JSON-on-stdout contract, closed-set exit codes {0,2,3,4,5,8,20,21,22,80,82,84}, S3.6 config loader + `BONSAI2_CONFIG`/`BONSAI2_RESULTS_DIR` env overrides, slot accounting + least-loaded routing, queue + task records, `reap_stale` hook | MAP 3.1 (hermetic `tests/test_harness_core.py`; fixture: `tests/fixtures/mock_engine.py`, threaded http.server, no :30001 dependency) |
| bonsai2 status family (repo source) | `bin/bonsai2-status`, `bin/bonsai2-list-instances`, `bin/bonsai2-health` (deployed to `/opt/bonsai2/bin/` by install.sh) | thin dispatchers into `bonsai2_harness.py`; S3.1 status schema incl. `main_only_reason` (instances: 0, S2.4); exit 0 / 82 (main probe down) / 84 (health.json absent) | MAP 3.2 (hermetic `tests/test_harness_status.py`) |
| bonsai2 spawn + taskworker (repo source) | `bin/bonsai2-spawn` (command) + `bin/bonsai2-taskworker.py` (detached worker child) (deployed to `/opt/bonsai2/bin/` by install.sh) | spawn: full S3.2 flag surface, least-loaded deadline-aware routing + tie-break lowest i, queue wait → exit 8 `state: "queued"`; task record `t-YYYYmmdd-NNNN-<hex>`; worker: streaming chat-completions, result file `<results>/results/<task_id>.json`, 400 → `ERR_CTX_TOO_LONG`, one engine-down retry (S1.5), deadline → `timeout`. `UNRESOLVED: spawn-dedup` at the spawn site (MAP 3.5 closes it) | MAP 3.3 (hermetic `tests/test_harness_spawn.py`: exit 0/2/3/5/8/80/82/84 by named case + spawn→worker→done round trip against the mock-engine fixture) |

Planned host locations (fixed by SPEC — do not relocate without a spec
delta):

- `/opt/bonsai2/bin/llama-server` — engine binary (baked into the image;
  host copy for ops) — MAP 1.2
- `/opt/bonsai2/bin/bonsai2-manager` — instance manager — MAP 2.1
- `/opt/bonsai2/bin/bonsai2-*` — harness control scripts (S3.2) — MAP P3
- `/opt/bonsai2/etc/{subagent-engine,serving,harness,bench}.yaml` — the
  four runtime configs, written by install.sh — MAP 1.5/2.9/P3/P4
- `/opt/bonsai2/cache/` — model downloads (incl. PQ2_0 default quant,
  7.21 GB, SHA-verified) — MAP 1.3
- `/opt/bonsai2/etc/state-<timestamp>.yaml` — install state, last 5
  kept — MAP 6.5
- `/run/bonsai2/{health.json,results/,dedup/,bench/}` — runtime state
  (tmpfs; bench + results rotate 7 days, bench dir capped 1 GB) — MAP P2/P3
- `/run/bonsai2/workspaces/` — per-task scratch workdirs for the S3.4
  tool loop (parent created by install.sh; per-task subdirs
  `workspaces/<task_id>` created by the harness at task time, 7-day
  rotation) — MAP 3.15
- `/opt/bonsai2/bin/bonsai2_tools.py` — host-side 4-tool module (the
  ONLY toolset a subagent sees, S3.4) — MAP 3.13
- systemd units `bonsai2-sglang.service` + `bonsai2-server@i.service` —
  MAP 2.4
- docker image `bonsai2-server:<tag>` — digest recorded in
  `subagent-engine.yaml` `image_pin` — MAP 1.4
- `/opt/bonsai2-<ts>/` — clean-box test scratch namespace (removed after) — MAP 6.7
