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
| bench goldens | `bench/goldens/` | versioned in repo | MAP 4.4 |

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
- systemd units `bonsai2-sglang.service` + `bonsai2-server@i.service` —
  MAP 2.4
- docker image `bonsai2-server:<tag>` — digest recorded in
  `subagent-engine.yaml` `image_pin` — MAP 1.4
- `/opt/bonsai2-<ts>/` — clean-box test scratch namespace (removed after) — MAP 6.7
