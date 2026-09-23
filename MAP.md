# qwen-tory38 — MAP (implementation ledger)

This file is the **agent's ledger of what is done and what is left** for
implementing `SPEC.md` end to end. SPEC.md is the *what* (locked design);
this map is the *how far along* (mutable, updated as work lands).

- A `[x]` item is done **only** when its spec-named assertions pass on this
  box (tests run, exit codes observed, acceptance criteria verified).
  Never check an item from memory or from a previous session's claim —
  re-verify at resume if cheap.
- The map is not one session's work. Resume is safe because: (1) the first
  unchecked item is the frontier, (2) every session leaves a log entry at
  the bottom, (3) spec deltas are backported to SPEC.md, never held here.
- If implementation contradicts SPEC.md, fix SPEC.md (spec follows code)
  and record the delta in the session log entry.

## Resume protocol (do this, in order, at session start)

1. Read this whole file. It is short on purpose; SPEC.md is the detail.
2. Read the **newest** session-log entry (bottom of file): machine state,
   open blockers, where the last agent stopped. Skim older entries only
   for the phases they touched.
3. Probe live state: `git log --oneline -3`, `ls /opt/bonsai2 2>/dev/null`,
   `systemctl status bonsai2-sglang --no-pager 2>/dev/null`,
   `bonsai2-status 2>/dev/null` (if scripts exist). Reconcile against the
   checked boxes; uncheck anything the probe disproves and say why in your
   session entry.
4. Work the **first unchecked item** (top-down, in listed order). Within a
   phase items are ordered by dependency unless marked independent.
5. As you complete items: mark `[x]`, keep the spec ref, note surprises.
6. **Before ending the session, append a session-log entry** (template
   below). No entry, no session — the next agent inherits nothing.
7. Stop cleanly when: the frontier item is blocked (record why), the
   session budget is spent, or a phase gate is reached. Never leave a
   half-written item unmarked; add a one-line `<!-- in progress: ... -->`
   comment under it.

## Out of scope (locked — do not build these, even when tempted)

LLM-to-LLM subagent orchestration · main-stack changes (consume :30001,
never touch it) · vision/mmproj · multi-box v1 · fine-tuning · LLM-judge
bench mode · kill-for-drift instance retirement · stateful subagents.
(See SPEC.md "Out of scope".)

## Open items / standing blockers

<!-- maintained by agents; one line each: what is blocked, on what, since when.
     Move resolved lines into Decisions (or delete). Empty = no blockers. -->
<!-- none as of 2026-09-21:
- (was: PQ2_0 weights not on the box) resolved — both quants are in
  /opt/bonsai2/cache, SHA256 + size match the YAML pins: PQ2_0
  3907dc16… (7,206,168,928 B), PTQ1_0 53107f53… (5,946,648,928 B);
  the MAP 1.4 build consumed them and the digest landed in
  image.pin (PR #7).
- (was: shellcheck + ruff not installed) resolved — ruff 0.16.8 via
  uvx; shellcheck 0.9.0 candidate from noble/universe arm64 (Ubuntu
  ports, provenance-checked; machine rule: no bare upgrade, install
  the single package when running the gate). Gate's soft-skip stays
  until they are on PATH.
-->

## Phases

Order is the build order: each phase is testable on its own and the next
phase consumes the previous one's surface.

### Phase 0 — Repo scaffolding & layout

Exit: repo layout decided and recorded; toolchain verified; test runner works hermetic.

- [x] 0.0 Repo guidance docs in place: `AGENTS.md` (project target, MVP
      single-instance safety rule, senior-git-1.0 workflow, VRAM audit
      rule, uncertainty rule, pre-commit policy, fork/external-deps
      policy, post-MVP CI/CD proposal, machine rules) + `ARTIFACTS.md`
      (trackability ledger for everything outside the repo; created
      2026-09-20, pre-implementation — its "created by this project"
      table fills in as phases land).
- [x] 0.1 Decide + record repo layout (decided 2026-09-20, branch
      `phase0-scaffolding`): the repo mirrors the host `/opt/bonsai2/`
      layout 1:1 so source and deploy paths never diverge in naming;
      `install.sh` (Phase 6) is the deployer.
      ```
      install.sh                  # S6.3 one-command installer (Phase 6)
      bin/                        # -> /opt/bonsai2/bin/ (S3.2 scripts, S2.1
                                  #   manager; MAP P2/P3; llama-server NOT
                                  #   here — baked into the image, S1.2)
      etc/                        # -> /opt/bonsai2/etc/ TEMPLATES; install.sh
      |   subagent-engine.yaml    #   renders real YAMLs (S1.6/S2.6/S3.6/S5.7)
      |   serving.yaml
      |   harness.yaml
      |   bench.yaml
      units/                      # -> /etc/systemd/system/ (S2.2; MAP 2.4
      |   bonsai2-sglang.service  #   renders/installs the concrete units)
      |   bonsai2-server@.service #   (template syntax, one per instance i)
      bench/                      # S5 framework source (run.py, score/,
      |   goldens/                #   goldens + M4 seeded-bug fixture; MAP P4)
      tests/                      # hermetic default: python3 -m unittest
      |   test_smoke.py           #   discover -s tests (no GPU/no network,
      |   fixtures/               #   stdlib unittest; S3.7 mock engine +
                                  #   manager stub lands at MAP P3)
      |   run_gpu.sh              # @gpu runner: runs tests/gpu/ ONLY, with
      |   gpu/                    #   nvidia-smi + /proc/meminfo snapshots
                                  #   first — @gpu tests live ONLY here
      third_party/llama.cpp/      # fork submodule pinned (MAP 1.1)
      scripts/                    # repo workflow tooling (gate.sh, review.sh,
                                  #   install-hooks.sh; not host-installed)
      .githooks/pre-commit        # MAP 0.4 (core.hooksPath=.githooks)
      ```
      Notes: `bench/` is repo-source only — runtime results go to
      `/run/bonsai2/bench/` and committed summaries to `bench-results/`
      (S5.2); `etc/` holds TEMPLATES with `<placeholder>` values — the
      four runtime YAMLs are written by install.sh with computed
      pins/hashes/N; the `tests/` split encodes the Global testing
      discipline (hermetic default vs `@gpu` explicit).
- [x] 0.2 Test runner + conventions (2026-09-20): stdlib `unittest`
      only (zero deps, S6.6-safe); hermetic default =
      `python3 -m unittest discover -s tests` from repo root — verified
      green (2 tests, 0.000s, no GPU/network). `@gpu` label = separate
      `tests/gpu/` discover target, run via `tests/run_gpu.sh` which
      snapshots `nvidia-smi` (driver/memory/utilization + `free -h`)
      into `test-logs/gpu-<ts>.log` before discovery (Global testing
      discipline). `tests/test_smoke.py` asserts the suite is hermetic
      (std-only imports; no requests/yaml leaked in).
- [x] 0.3 Verify build toolchain (probed 2026-09-20 on this box, S1.2/
      S6.2/S6.6 matrix):
      - arch: aarch64 ✓ (uname -m)
      - driver 580.159.03 ✓ (≥ 580; newer than the 580.126.09 recorded
        at spec time — box was re-imaged since, still in-matrix)
      - python 3.12.3 ✓ (≥ 3.10)
      - docker 29.2.1 (client + server) ✓ with BuildKit
      - nvidia-container-toolkit 1.19.1 ✓ (`nvidia-ctk --version`)
      - CUDA build container: `nvidia/cuda:13.0.2-devel-ubuntu24.04`
        — pulled + manifest-verified arm64+aarch64 (digest
        `nvidia/cuda@sha256:5dc1bca23d05bd37b011be68ec470c03b403a5da07ec3a86e41af9470e9d0cc6`);
        inside it: nvcc 13.0 (cuda_13.0.r13.0), gcc/g++ present;
        cmake + ninja MISSING from the base image — install via
        `apt-get install -y cmake ninja-build` (Ubuntu 24.04 repos,
        aarch64 ✓; verified: cmake 3.28.3, ninja 1.11.1 in-container).
        install.sh's build stage (MAP 1.2/6.2) must pin this image
        digest + the apt line.
      - /opt free: 353 GB ✓ (≥ 20 GB floor)
      - host linters: shellcheck + ruff still not installed (soft in
        gate/pre-commit; Open items).
- [x] 0.4 Pre-commit enforcement (2026-09-20): `.githooks/pre-commit`
      (staged-only: bash -n + shellcheck-soft on .sh; ruff-soft on .py;
      secret scan of staged diff — same heuristic as gate.sh;
      trailing-whitespace/CRLF/no-staged-file>50MB; then full
      `./scripts/gate.sh`) + `scripts/install-hooks.sh` (sets
      `core.hooksPath=.githooks`, idempotent — ran twice, no drift).
      Enabled in this clone. Verified with deliberate probes: dirty
      staged .sh (trailing whitespace) → hook exit 1 (BLOCKED); staged
      .sh carrying a 16-char string after `token = "` → secret-scan
      FAIL exit 1; clean staged file → real `git commit` ran the hook
      to "pre-commit: ok" + gate PASS (test commit reverted after). `git commit --no-verify`
      remains allowed with a one-line PR-body reason.

### Phase 1 — Subagent inference engine + image (SPEC S1)

Exit: one instance runs by hand from the pinned image and passes S1.7
integration. S1.8 #2–#5 complete at end of Phase 6/7 (they need N ≥ 1
under the manager or the full stack).

- [x] 1.1 Pin the fork commit: `PrismML-Eng/llama.cpp` branch `prism`,
      pinned at `9a9394a895b96003ca842a6041cb28ac49a108f7` (prism tip
      2026-09-18, checked via `git ls-remote` 2026-09-20 — same SHA the
      2026-09-20 merge-status check recorded; no drift). Recorded in
      `etc/subagent-engine.yaml` `engine_pin` (rendered to
      `/opt/bonsai2/etc/` by install.sh, S1.2). **Fork-vendoring decided:
      git submodule** at `third_party/llama.cpp` (the 0.1 layout
      `third_party/llama.cpp/` line; `.gitmodules` tracks `prism`), in
      repo, project-wide, never system-wide. Re-verify-at-first-build
      still owed at 1.2: attempt a stock-build load of PQ2_0, expect
      refusal (Q1_0 merged upstream; generic ternary Q2_0/g64 on
      mainline; PQ2_0/PTQ1_0 packing still fork-only — re-checked 2026-09-20).
      Spec delta (2026-09-20, backported per the document conventions):
      at this pin the fork's ternary kernels compile unconditionally;
      no `LLAMA_BONSAI_*` cmake toggles exist (verified: zero matches in
      any CMake file at the pin) — S1.2 build flags now match the
      Bonsai-demo script at the pin (`-G Ninja`,
      `-DCMAKE_CUDA_ARCHITECTURES="121a"`, no F16/CURL/ACCELERATE
      flags). Version policy: bump only via a PR with S5 quick bench
      (S1.2, S5.5).
- [x] 1.2 Build pipeline (2026-09-20, this box): `image/Dockerfile`
      builds the pinned fork source in the MAP 0.3 CUDA image
      (`nvidia/cuda@sha256:5dc1bca2...`, digest-pinned FROM), S1.2
      cmake flags at the pin, `-DCMAKE_CUDA_ARCHITECTURES="121a"`,
      `-DLLAMA_BUILD_COMMIT=<engine_pin prefix>` (build context has no
      git metadata: COPY strips .git, so the commit line is supplied
      from the pin; CMake falls back to git-derived BUILD_COMMIT only
      when LLAMA_BUILD_COMMIT is unset). Artifact: aarch64 ELF
      `llama-server` + all its shared libs side by side in
      `/opt/bonsai2/bin/` (RUNPATH `$ORIGIN`, patchelf; one `lib*.so*`
      glob so new libs like libllama-server-impl.so can't drift out of
      the copy). In-build assertions: sm_121a in
      compile_commands.json, aarch64 ELF, banner carries the pin prefix
      ("version: 0.2.0-dev (build 0, commit 9a9394a8)"),
      libggml-cuda linked. Verified: build exit 0; banner + sm_121a in
      the build log (test-logs/build-*.log, rotated); @gpu suite
      tests/gpu/test_build_banner.py 3/3 OK (pin in banner,
      libggml-cuda link, aarch64 ELF). Note for 1.4: the build log's
      "ggml_cuda_init: no CUDA-capable device is detected" is the
      expected GPU-less build-container outcome, not a failure; the
      runtime image gets `--gpus all`. Build-image local tag:
      `bonsai2-server-build:121a` (build pipeline artifact, not the
      S1.6 image_pin — that is the runtime image, MAP 1.4).
- [x] 1.3 Model download + SHA256 verification: `PQ2_0` (default) +
      `PTQ1_0` (fallback); retry ×3 / 10 s backoff; `ERR_HASH_MISMATCH`
      exit 4 path (S1.1, S1.5). (2026-09-21, PR #5 merged as `ea87132`,
      7 review rounds; tool = `bin/bonsai2_model_fetch.py`, hermetic
      suite 20 tests; live 7.21 GB + 5.95 GB download runs at install,
      MAP 6.2 step 3.)
- [x] 1.4 Build `bonsai2-server:<tag>` runtime image: binary + model files
      + CUDA runtime; record docker content digest as `image_pin`
      (S1.6, S6.1). (2026-09-21, PR #7 merged as `a203f79`, 4 review
      rounds: `image/Dockerfile.runtime`
      + `bin/bonsai2_build_image.py` orchestrator; base =
      `nvidia/cuda:13.0.2-runtime-ubuntu24.04` digest-pinned; binary via
      `COPY --from=bonsai2-server-build:121a` (MAP 1.2 artifact; BuildKit
      named contexts unavailable on this docker driver — probed, so the
      `--from=image:tag` form; the orchestrator cross-checks that image's
      banner against the YAML engine pin before building); both quants
      baked with in-build sha256+size assertions vs the YAML pins
      (S1.1 "hash-verified before baking"); + two system libs copied
      from the build image that the CUDA runtime base lacks
      (libgomp.so.1 OpenMP runtime, libcuda.so.1 driver stub — probed
      by diffing the binary's needed-lib list against the base's ld
      cache; the nvidia runtime shadows the baked libcuda with the
      host's real one under --gpus all); tag `bonsai2-server:1.0`,
      digest recorded in `etc/subagent-engine.yaml` `image.pin`
      (surgical, comment-preserving writer). Hermetic:
      tests/test_image_build.py; @gpu: tests/gpu/test_runtime_image.py
      (banner pin, aarch64 ELF, libggml-cuda + CUDA-runtime ld-cache,
      models at pinned sizes, S1.3 default CMD).)
- [x] 1.5 `subagent-engine.yaml` writer + YAML schema validation: port
      math, ctx/parallel bounds, sampling ranges (S1.6, S1.7 unit half).
      (2026-09-21, PR #7 merged as `a203f79`, 4 review
      rounds: `bin/bonsai2_render_config.py` —
      `validate_engine_config` (S1.6 shape + S1.7 bounds: presence, pin
      formats, port math incl. overflow + collision via optional
      per-instance `ports` overrides, ctx 8192..262144 multiple of 8192,
      parallel 1..64, count 0..8, sampling ranges, stop ids) returning
      (errors, warnings) — the template's unrecorded image pin is a
      warning, not an error, so the tracked template validates pre-build;
      `render_config` + `main()` = the install.sh writer (S6.3: fill
      placeholders, write the rendered YAML, --check for validation).
      Hermetic: tests/test_render_config.py (60+ cases, incl. the
      tracked template + the MAP 1.4 render flow).)
- [x] 1.6 VRAM formula as a pure function: `instance_footprint_GB`,
      `N = floor(budget/11.5)` clamped [0,8], N=0 and N>8 cases (S1.4,
      S1.7 unit half). (2026-09-21, PR #7 merged as `a203f79`, 4 review
      rounds: `bin/bonsai2_vram.py`
      — `instance_footprint_gb` (S1.4 verbatim, 0.00000176 constant),
      `instance_count` (clamp [0,8]; total on all floats: N=0 on
      zero/negative budget, N=0 on non-positive footprint instead of a
      ZeroDivision, N=8 clamp on over-budget), `subagent_budget_gb`
      (S0 table: 121.6 − 76.4 − 8.0 = 37.2) + CLI; hermetic:
      tests/test_vram.py incl. both spec worked examples (10.91 GB
      footprint; N=3 at 37.2/11.5).) <!-- 2026-09-22 delta (MAP 1.7): the
      S1.4 constant re-anchored to 0.000003277 (measured q4_0 KV) and the
      budget to 14.78 GB; the worked example is now 14.08 GB footprint,
      N=2 at 37.2/14.78. The body text above is the PR #7 historical
      record at the original FP16-era 0.00000176 / 11.5 GB values. -->
- [x] 1.7 Manual single-instance bring-up: `docker run` per S1.3
      (verbatim flags, `--port 30101`), host networking, memory cap;
      `/v1/models` returns `qwen38-27b-subagent`; 16-token completion
      round-trip < 10 s; `nvidia-smi` delta matches S1.4 formula ±10 %
      (S1.7 integration). (2026-09-22, PR #8 merged as `a567b09`, 4
      review rounds: `tests/gpu/test_bringup_1_7.py` — cold load from
      `bonsai2-server:1.0` in S2.2 shape; live 2026-09-22: ready ~8–15 s,
      16-token RT ~0.8 s, footprint delta 13.4–13.51 GB in the band
      [13.30, 15.88] GB. Band floor intentionally budget-anchored
      (14.78*0.9 = 13.30), user-approved 2026-09-22, documented in-test.)
- [x] 1.8 Sustained load: 8 concurrent completions (max_num_seqs=8) for
      120 s → t/s ≥ 26.2 (0.9 × 29.16 baseline), zero 5xx (S1.7, S1.8 #3).
      (2026-09-22, PR #8 merged as `a567b09`: `tests/gpu/test_load_1_8.py`
      — 8 concurrent × 120 s, wall-time denominator, zero-5xx, plus the
      VRAM-delta ±10 % half asserted in the same band (wired `03521ac`).
      Live runs 2026-09-22: 44.7–61.9 t/s aggregate (5376–7424 tokens),
      0× 5xx — PASS with ~1.7–2.4× headroom over the 26.2 floor.)
- [x] 1.9 OOM-ceiling load: add instances until the S1.4 allocation tier
      fails → last failure is `ERR_VRAM_OOM`, not a segfault; prior
      instances stay healthy (S1.7). (2026-09-22, `tests/gpu/test_oom_1_9.py`:
      S1.4 tier gate on live MemAvailable + host-side memory holder to force
      deterministic rejection; path-2 spawn at 6.0 g cap under the gap.
      Accepts three clean shapes: kernel cgroup OOM-kill (137/OOMKilled),
      engine clean error (non-zero exit, observed 133/SIGTRAP), split/running
      (kernel managed the under-budget load). Rejects segfault-class markers
      (SIGSEGV/SIGBUS/core-dumped/Segmentation fault) across all spawned
      containers + a clean exit-0 "success" under the cap. Prior instances
      stay healthy (16-token RT + alias). Live 2026-09-22: i=0 tier PASS 51.4 GB,
      i=1 tier PASS 36.2 GB, i=2 ERR_VRAM_OOM at free 12.02 GB (need 15.08),
      path-2 split shape serving at 6.0 g cap, instances 0/1 healthy post-probe,
      main-stack PIDs unchanged.)
- [x] 1.10 Port collision: dummy listener on 30101 → instance 0
      `ERR_PORT_IN_USE` skipped, instance 1 up (S1.5, S1.7 edge).
      (2026-09-22, `tests/gpu/test_port_collision_1_10.py`: dummy SO_REUSEADDR
      listener on 30101 → engine exits 1 in ~0.2 s with "couldn't bind HTTP
      server socket" (S1.7 pin-reality delta: exit-1 fast, bind before model
      load, log on stderr — SPEC S1.5 exit-3 mapping is the unit-layer
      concern, not the engine's). Instance 1 up on 30102. Live 2026-09-22:
      instance 0 exited (code 1) on the 30101 bind collision; instance 1 up.)
- [x] 1.11 Kill -9 of the instance process → auto-restart < 15 s
      (docker-level, pre-manager) (S1.8 #4). (2026-09-22,
      `tests/gpu/test_kill_restart_1_11.py`: kill -9 the engine PID in-container
      (llama-server is PID 1 via nvidia entrypoint), re-run the container in the
      S2.2 ExecStart shape. Restart witnessed by host PID (docker .State.Pid —
      the in-container PID is 1 in both containers). Live 2026-09-22: kill →
      healthy in 3.14 s (budget 15 s); host PID 2508937 → 2509315; main-stack
      PIDs unchanged (no baseline PID lost from the compute-apps listing).)

### Phase 2 — Manager + systemd lifecycle (SPEC S2)

Exit: S2.8 acceptance (all five, runnable on this box).

- [x] 2.1 `bonsai2-manager` skeleton: Type=notify via sd_notify; reads
      `subagent-engine.yaml` + `serving.yaml`; writes
      `/run/bonsai2/health.json` in the S3.1 schema (S2.2).
      (2026-09-22, branch `phase2-manager-skeleton`:
      `bin/bonsai2-manager` — hand-rolled sd_notify (AF_UNIX datagram,
      no systemd-python dep), the two-YAML loader (ERR_CONFIG exit 2),
      atomic `health.json` write (tmp+rename), the S3.1 document builder
      (stack/instances/main_endpoint/main_healthy + `main_only_reason`
      ONLY at 0 instances, S2.4). Hermetic: `tests/test_manager.py`
      (config load + ERR_CONFIG paths, S3.1 schema + atomicity, stack
      derivation, main-only reason echo). @gpu:
      `tests/gpu/test_manager_spawn_2_5.py`.)
- [x] 2.2 Boot-time budget check (S2.3): `free_gb` from the main
      container's live cgroup `memory.current` (sleep-accurate) else the
      recorded fresh-load `footprint_gb`; `N' = max(0, min(N, floor(...)))`;
      `WARN_VRAM_SHORTFALL` on 0 < N' < N.
      (2026-09-22, same branch: `planning_tier` (the S2.3 formula verbatim,
      pure + total: full grant, shortfall->warn+proceed, negative free ->
      `max(0,...)` clamp to 0, min(N,...) clamp) + `main_used_gb`
      (live cgroup `memory.current` via the `system.slice/docker-<id>.scope`
      shape this box uses — probed 2026-09-22; falls back to
      `main_stack.footprint_gb` when the container/cgroup is gone). Live:
      the @gpu test read the main cgroup at ~1.97 GB (sleep-accurate —
      SGLang asleep, device KV not cgroup-charged, the known MAP 1.7
      finding) and computed N'=2 off the live pool.)
- [x] 2.3 Allocation tier (S1.4, manager-run): stricter pre-spawn gate
      `free ≥ instance_footprint_GB + 1.0`; probe timeout 5 s + one
      retry, then treat free as 0; i > 0 → partial load exit 0; i = 0
      with granted N' ≥ 1 → exit 4 `ERR_VRAM_OOM`; N' = 0 → S2.4 handoff.
      (2026-09-22, same branch: `allocation_tier` (need = `bonsai2_vram.
      instance_footprint_gb` + 1.0 — the formula module is the source, no
      hardcoded need) + `read_free_gb` (one probe + one retry, then 0 —
      the bounded shape the hermetic test drives with a failing reader:
      exactly 2 calls, then 0.0). Hermetic: grant-at-need / reject-below /
      the 1.0 GB margin / retry-then-zero. Live: @gpu tier ran at need
      15.08 GB — i=0 PASS @47.4 GB, i=1 PASS @34.6 GB.)
- [x] 2.4 Unit files: `bonsai2-sglang.service` + `bonsai2-server@i.service`
      verbatim main-stack shape — `docker run` flags per S2.2,
      `ExecStartPre= docker rm -f`, `Restart=on-failure`, `RestartSec=5`,
      `StartLimitBurst=3` → `bfailed`, `CPUQuota=64 %`; `Wants=` (not
      `Requires=`) on the main stack (S2.1, S2.2).
      (2026-09-22, same branch: both `units/*.service` templates carry the
      S2.1/S2.2 shape — manager: `Type=notify` + `Wants=qwen38-sglang`
      (not `Requires=`) + `After=` incl. the main stack; server:
      `Requires=docker.service` + `ExecStartPre= docker rm -f` +
      `Restart=on-failure`/`RestartSec=5`/`StartLimitBurst=3`/`CPUQuota=
      64%` — hermetic-asserted in `tests/test_manager.py::TestUnitFiles`;
      the server template's comment now names the full-command docker-run
      (probed 2026-09-22: bare `docker run img --flag` dies under the
      nvidia entrypoint) + that the S1.4 tier is what admits a start.
      `docker rm -f` on an absent container returns rc=0 (probed), so
      `ExecStartPre` is safe on first start.)
- [x] 2.5 Spawn path: manager starts granted instances in order via
      `systemctl start bonsai2-server@i`; waits each to healthy before the
      next; boot order: main stack first (S2.1).
      (2026-09-22, same branch: `spawn_sequence` — tier gate ->
      `systemctl start bonsai2-server@i` -> S2.2 health probe wait
      (models + 16-token RT, `boot_target_s`) BEFORE the next index;
      i>0 tier fail -> WARN_VRAM_SHORTFALL + exit 0 partial; i=0 tier
      fail -> exit 4 ERR_VRAM_OOM with no start issued; a failed unit
      start is recorded, loop continues (S2.1 independent engines).
      Hermetic: the ordering (i=1's start only after i=0's wait returns
      True), all four exit/outcome branches. Live (@gpu
      `test_manager_spawn_2_5.py`, 2026-09-22): the manager booted from
      the tracked templates, read the live pool, and spawned BOTH real
      27B instances in order — 46.9 s boot+spawn+settle, both healthy,
      health.json `stack=healthy`, main PIDs unchanged; teardown left no
      containers. NOTE for install.sh (6.2): the unit's `Requires=
      docker.service` is system-scope only (user scope has no docker
      unit — the @gpu test's documented deviation; `BONSAI2_USER_SYSTEMCTL`
      is the manager's user-scope hook).)
- [ ] 2.6 Health probe loop: every 10 s, `GET /v1/models` (200 + alias)
      AND 16-token completion < 10 s; two consecutive fails → `degraded`,
      five → restart attempt (S2.2).
- [ ] 2.7 N=0 / main-only mode (S2.4): PTQ1_0 retry at its footprint;
      ctx-halve retry (multiple of 8192, floor 8192) on allocation-tier
      fail; health.json `{"instances": 0, "mode": "main-only", "reason":
      "budget_zero" | "user_unloaded"}`; install stays exit 0.
- [ ] 2.8 Drift watch (S2.5): 5-min recheck; cgroup pressure > 30 s over
      `--memory` → halve that instance's ctx, `systemctl restart
      bonsai2-server@i` in place (start-limit counter reset),
      `ctx_reduced: true` + new ctx in health.json, counted in restart
      budget; never re-spawn `bunloaded` instances; no context-grow.
- [ ] 2.9 `serving.yaml` writer (S2.6), including `boot_target_s` written
      back from measured boot (S2.7 reboot test).
- [ ] 2.10 S2.7 test suite: kill -9 recovery (< 15 s,
      healthy→degraded→healthy in health.json + `status`), N=0 clamp
      test (override main footprint 120 GB), drift test, unload/reload
      manager-level semantics.
- [ ] 2.11 S2.8 acceptance run: cold boot ≤ 180 s (record T), crash
      recovery, N=0 end-to-end, drift cycle, unload/reload cycle.

### Phase 3 — Harness control scripts, the main-agent API (SPEC S3)

Exit: hermetic closed-set suite green + S3.8 acceptance (all five).

- [ ] 3.1 Core library: stdout = one JSON object (S3.1 schemas), stderr
      human-only; `harness.yaml` loader (S3.6) + env overrides
      `BONSAI2_CONFIG`, `BONSAI2_RESULTS_DIR` (S3.2).
- [ ] 3.2 `bonsai2-status`, `bonsai2-list-instances`, `bonsai2-health`
      (S3.2, S3.1 status schema incl. `main_only_reason`).
- [ ] 3.3 `bonsai2-spawn`: least-loaded deadline-aware routing, tie-break
      lowest i; queue FIFO with priority preemption; `queue_wait_max_s`
      expiry → exit 8 `state: "queued"` (S3.3, S3.2).
- [ ] 3.4 Slot model + deadline enforcement: one slot per task for its
      lifetime; `--deadline` expiry → abort, task `timeout` (exit 22),
      slot freed (S3.4).
- [ ] 3.5 spawn idempotency: dedup key = prompt hash + `--tools/--ctx/
      --priority` (not `--out`), 24 h TTL, `/run/bonsai2/dedup/`; live
      duplicate → existing task_id, no double slot (S3.4).
- [ ] 3.6 `bonsai2-collect`: read result file; task JSON + `answer /
      reasoning / usage`; 20/21/22 terminal, 0 = non-terminal poll, 2 =
      unknown TASK_ID (S3.2).
- [ ] 3.7 `bonsai2-steer` / `bonsai2-cancel`: steer-and-wait → exit 8 on
      no state change in SECS; cancel SIGTERM → SIGKILL after 5 s;
      engine-down mid-task → `engine_retried` once on next instance,
      second failure → `failed` (S3.2, S1.5).
- [ ] 3.8 `bonsai2-unload` / `bonsai2-reload`: manager-mediated;
      `bunloaded` state; zero-running → main-only
      `user_unloaded`; idempotent no-op; partial reload →
      `"state": "partial"` exit 0; dead manager → 84 (S3.2).
- [ ] 3.9 Exit-code closed set {0,2,3,4,5,8,20,21,22,80,82,84}:
      per-command mapping per S3.2 implemented; a test asserts no stray
      code can be emitted (S3.8 #3).
- [ ] 3.10 Mock engine fixture: Python http.server, canned `/v1/models` +
      `/v1/chat/completions` (streaming + non-streaming, 200/400/429/500),
      plus a manager stub; the 82 (dead main-endpoint fixture) and 84
      (absent `/run/bonsai2`) cases (S3.7).
- [ ] 3.11 Hermetic suite, table-driven: every exit code by a named case
      asserting exact JSON + code; slot contention (2×2, 5 spawns → 4
      running + 1 queued); failure injection (kill mid-task →
      `engine_retried` → `failed`; non-JSON body → 21 `ERR_PARSE`);
      idempotency; routing (uneven fake payloads → least-loaded +
      priority preemption); pool-control cases (S3.7).
- [ ] 3.12 S3.8 acceptance: live 3-task fan-out (3× spawn → 3× collect
      exit 20, results verified); live kill -9 of a running instance →
      task recovered; all 12 codes exercised by the suite; N=0 → every
      subagent command exit 80 with S2.4 JSON; unload/reload mock cases.

### Phase 4 — Bench framework (SPEC S5)

Exit: one full M1–M8 run committed with S5.9 acceptance (all four).

- [ ] 4.1 `bench.yaml` + schema validation (S5.7); invalid scenario id →
      exit 2 (S5.8 config test).
- [ ] 4.2 Runner core `bench/run.py`: scenario loader; endpoint resolver
      (subagent ports from `subagent-engine.yaml`, main from
      `harness.yaml`); task executor (streaming chat completions, token
      timing from stream timestamps); box-state snapshot per run
      (`nvidia-smi` query, `docker stats` read-only, `/proc/meminfo`,
      driver, date) via stdlib subprocess (S5.4, S5.2).
- [ ] 4.3 Reporter: JSON row per scenario + `bench-results/<date>_<commit>.md`
      summary; 7-day rotation + 1 GB cap (keep newest); `BONSAI2_BENCH_LOCK`
      flock; exit 0 even on scenario-level fail, exit 2 on bad config
      (S5.4, S5.2, S3.5).
- [ ] 4.4 Auto scorers + goldens in `bench/goldens/`: M1 (compiles + own
      3-case suite), M2 (report JSON parseable + 10/10 coverage), M4
      (seeded 10-bug fixture diff, ≥ 80 % found), M5 (5 QA questions,
      golden-file binary scoring), M7 (t/s at ceiling + zero 5xx), M8
      (main t/s within `main_guardrail_pct` of M6 → `pass`/`fail` verdict
      recorded in the row) (S5.2, S5.3).
- [ ] 4.5 M3 semi-auto scorer: auto shape (≤ 300 tokens, 3 claims per
      section) + main spot-check of 1/3 sections (claim accuracy ≥ 2/3);
      score = `auto*0.7 + spotcheck*0.3` (S5.3).
- [ ] 4.6 M6 main-only control of M1–M5, same rubrics (S5.2).
- [ ] 4.7 `bonsai2-bench` thin wrapper (`--scenario`, `--quick`); exit
      code = runner's (0/2) (S3.5).
- [ ] 4.8 S5.8 tests: dogfood (M7 row parses), rubric golden in/out pairs
      per scorer (hermetic), mock-endpoint full M1+M2 run (zero
      network/GPU, timings sane).
- [ ] 4.9 Live full M1–M8 run on this box; commit `bench-results/`; every
      row carries the box-state snapshot; M8 verdict recorded (S5.9 #1).
- [ ] 4.10 Re-bench policy wired (S5.5): quick bench (M1+M2+M7, `--quick`)
      on every engine-pin bump / model re-download / weekly optional
      cron; > 10 % t/s or auto-rubric regression blocks the pin-bump PR;
      full bench on revision/quant change or S4.2 row flip.

### Phase 5 — Harness skill, the doctrine (SPEC S4)

Exit: S4.7 acceptance (all three). Skill is installed for Hermes/pi as
`bonsai2-harness` (S4.1).

- [ ] 5.1 `SKILL.md` skeleton: frontmatter, when-to-use, command
      reference = exactly S3.2 (the skill never invents commands) (S4.1).
- [ ] 5.2 Work-allocation table (S4.2): every row carries a `[M#]`
      citation from the Phase 4 run; self-report rule (verification cost
      is part of task cost).
- [ ] 5.3 Session economy (S4.3) + cost doctrine (S4.4) with the
      re-benched t/s constants from Phase 4 replacing the a-priori 29/4800
      floors; win rule + two-consecutive-loss flip.
- [ ] 5.4 Failure doctrine (S4.5): one paragraph per S3.2 exit code.
- [ ] 5.5 Doctrine audit script: `bench.yaml` scenario rows ↔ S4.2
      citations + win/lose recompute → `doctrine-audit.json`; failing
      audit blocks the skill PR (S4.6).
- [ ] 5.6 Fresh-session test: new main-agent session, skill + S3.2
      commands only (mock engine), completes the S5.6 standard workload
      set (M1, M2, M3 + one M4-style diff review); command log ⊆ S3.2,
      all results verified per S4.2 (S4.6, S5.6).
- [ ] 5.7 S4.7 acceptance: audit passes (citations exist, win/lose
      recomputed or table updated in same PR); fresh-session workload
      complete; two synthetic loss scenarios → audit flags the stale row.

### Phase 6 — GB10 packaging: one-command install (SPEC S6)

Exit: S6.8 acceptance (all four). This phase glues Phases 1–5 together;
the pieces are already testable by hand — `install.sh` is the deliverable.

- [ ] 6.1 Prerequisite checks (S6.2 table): hard-fail rows → named ERR,
      exit ≠ 0, atomic (check-all before touch-any, nothing in
      `/opt/bonsai2`); warn row (unified memory < 96 GiB) →
      `WARN_MEMORY_BELOW_MATRIX`, exit 0, N recomputed.
- [ ] 6.2 `install.sh` ordered steps (S6.3): prereq checks → build-or-verify
      image + record digest → download model (skip on SHA match) → detect
      main-stack fresh-load footprint from SGLang startup log
      (`available_gpu_mem` + pool, read-only; fallbacks: live cgroup,
      then 76.4 default + `WARN_MAIN_STACK_DEAD`) → compute N via S1.4
      (with S2.4 PTQ1_0/ctx-halve fallbacks) → write the four YAMLs
      (S1.6/S2.6/S3.6/S5.7) → install units → start manager → health
      checks (180 s budget, main-only/partial variants) → stack-ready
      report (JSON + human table). Exit codes: 0 full or main-only, 4
      live-margin OOM, 3 arch mismatch, 2 config.
- [ ] 6.3 `--instances N` override (phased rollout: `--instances 1` to
      build/verify, then re-run to N) (S6.3 step 4, S0).
- [ ] 6.4 Idempotency + main-stack contract (S6.4): re-run with no drift →
      "unchanged" (zero restarts, zero downloads, no YAML rewrite);
      drift > 5 GB footprint or N change → regenerate; `--force-config`;
      units re-installed without restart when unchanged; only read-only
      docker commands against `qwen38-sglang` (`logs/stats/inspect`/cgroup).
- [ ] 6.5 State files + `--rollback <state-<ts>.yaml>` (last 5 kept in
      `/opt/bonsai2/etc/`); restores configs + restarts manager; binary/
      model rollback = re-point pin (cache kept) (S6.5).
- [ ] 6.6 `--refresh-main` flow: re-detect footprint, recompute N, re-run
      quick bench M1/M2/M7 after a main-stack upgrade (S6.5).
- [ ] 6.7 S6.7 tests: clean-box test in scratch namespace
      (`/opt/bonsai2-<ts>`): full S6.3 flow → S2.8 + S3.8 end-to-end →
      reinstall-over must report unchanged; drift test (main footprint
      +10 GB between two installs → N recomputes down, report explains);
      each hard-fail prereq faked-fail (fake uname/driver/…) → named ERR,
      clean filesystem; warn row faked-low → continues exit 0.
- [ ] 6.8 S6.8 acceptance: one command → stack-ready report + S2.8 checks
      + N correct per S1.4; re-run "unchanged"; rollback → healthy at
      previous N; all prereq cases emit named ERR / warn as specced.

### Phase 7 — Global validation & closeout

Exit: all Global acceptance criteria (project-level, SPEC bottom) pass;
SPEC.md `status` → `active`.

- [ ] 7.1 Global testing discipline in force: hermetic where possible;
      `@gpu` tests labeled, never parallel with a live session, preceded
      by `nvidia-smi` snapshot; every test asserts a named acceptance
      criterion (Global testing discipline).
- [ ] 7.2 Global acceptance #1–#4: cold boot ≤ 180 s; fresh session
      completes the standard workload set with S3.2-only commands, all
      results S4.2-verified; full M1–M8 committed with every S4.2 row
      citing a bench row and M8 ≤ 5 %; one-command install S6.8 ×4.
- [ ] 7.3 Global acceptance #5 — 72 h soak: all N instances up (restarts
      per instance ≤ 2), no main-stack t/s regression beyond the 5 %
      guardrail, `/run/bonsai2` < 2 GB (7-day results + bench rotation
      verified) (Global acceptance #5).
- [ ] 7.4 Post-MVP CI/CD proposal (AGENTS.md "After MVP e2e"): write up
      what the e2e run actually hit (failure points, slow spots, review
      rounds) → propose the tuned set (post-commit gate.sh+hermetic
      tests, pytest for the bench runner, one self-hosted Gitea Actions
      runner, reviewer as required PR status) — enable only what the
      observed pain justifies; verify current Gitea 1.27.1 self-hosted
      feature set before proposing (Issues/PRs/labels/Actions are
      self-hosted; paid = cloud scale + support, not features we need).
      Track as a decision item, not inline tooling.
- [ ] 7.5 Closeout: SPEC.md frontmatter `status: active` (every section
      through review, no open question); clear the Open-items section;
      final session log entry summarizing the whole effort + any spec
      deltas; this map fully `[x]`.

## Session log

<!-- Newest entry on top.
 One entry per implementation session, appended by
     the agent BEFORE ending the session. Template:

### <YYYY-MM-DD> — <one-line title>
- agent/model: (e.g. Hermes qwen3.8-27b / session id or host)
- phase(s): (phases touched)
- items checked: (item numbers; "none" if blocked)
- machine state: (what is installed/running now: /opt/bonsai2 contents,
  systemctl units, running containers, N instances, any env vars/flags
  the next session must know — e.g. "image built, not pinned yet",
  "manager running with --instances 1")
- next: (exact next item, and anything that must happen first)
- blockers/intricacies: (what bit, spec refs, what the next agent should
  know to avoid re-learning it; "none" if clean)
- spec deltas: (SPEC.md changes made this session + why; "none")
-->

### 2026-09-23 — G3 loop PR #10: round 14 reviewed — 1 P0/1 P1/3 P2; P0 (free-probe 5 s cap nominal → ENFORCED via daemon thread + join(timeout)) + P1 (shared boot deadline started AFTER main_healthy → now BEFORE) REAL + fixed + pinned, P2s closed (commit 51fa6ae)
- G3 PR #10 (MAP 2.1–2.5). Round 14: 1 P0 / 1 P1 / 3 P2 (raw: /tmp/review-phase2-manager-skeleton-r14.txt; stdout /tmp/review-10-stdout-r14.txt, review.sh rc=0).
  - P0 the S1.4 "5 s timeout per probe" was NOMINAL (docstring admitted it): a wedged /proc/meminfo read would deadlock the boot, violating "wedged probe must never deadlock the manager". REAL — fixed: new `_mem_available_gb_bounded()` = daemon thread + join(FREE_PROBE_TIMEOUT_S) → genuine TimeoutError at the cap; TimeoutError feeds the SAME retry-once-then-zero path as OSError (S1.4). 2 new hermetic tests (wedged reader → TimeoutError ≤ cap; TimeoutError counts as failed probe).
  - P1 shared S2.8 #1 boot deadline computed AFTER main_healthy() → the main probe's up-to-5 s was outside the 180 s whole-boot budget (S2.8 #1 "main + N healthy ≤ 180 s"). REAL — fixed: `boot_deadline_s = time.monotonic() + cfg["boot_target_s"]` now computed BEFORE the S2.1 main probe (the probe is inside the budget, not on top of it). New hermetic pin `test_boot_deadline_starts_before_the_main_probe` (records monotonic() inside the mocked main_healthy; asserts deadline − boot_target_s ≤ probe_time).
  - P2#1 footprint computed but need_gb recomputed independently → single source of truth: `need_gb = max(footprint + TIER_MARGIN_GB, budget_gb)` derived from the SAME footprint the log prints (allocation_need_gb stays for pure-formula tests).
  - P2#2 @gpu pool precondition guaranteed 2 instances but didn't assert template count==2 → a count bump to ≥3 would drift the "full load: 2 instance(s)" assertion silently → new `_count_from_template()` + precondition assertEqual(count, 2).
  - P2#3 size_bytes required int while per_instance_budget_gb accepted int-or-float (inconsistent numeric edge) → uniform: int OR positive float, bool excluded, normalized to int at load; same edge for fallback_size_bytes.
  - Verified: 185/185 OK; ruff check+format clean; gate PASS; commit 51fa6ae pushed. @gpu r14: first run FAILED on the documented transient-PID flake ({1153957} lost from compute-apps — a transient CUDA process captured at baseline, close to the test's own pid 1154086, now gone; main stack 5581/12262 both still alive) — re-run (r14b) to confirm. Disposition → PR #10 comment (pending).
  - P1/P0 trend: r14 = first round with a P0 since r4 (both real contract gaps, not vestiges). P2 trend: 7→1→3→2→4→3→4→3 (r1–r13).

### 2026-09-23 — G3 loop PR #10: round 13 reviewed — 0 P0/2 P1/4 P2; P1#1 kv_cache-never-feeds-S1.4 REAL + fixed (ERR_CONFIG on unvalidated dtype), P1#2 None-deadline-TypeError refuted by probe, P2s closed (commit 061706c)
- G3 PR #10 (MAP 2.1–2.5). Round 13: 0 P0 / 2 P1 / 4 P2 (raw: /tmp/review-phase2-manager-skeleton-r13.txt; stdout /tmp/review-10-stdout-r13.txt, review.sh rc=0).
  - P1#1 `kv_cache` (S1.6 key) never read by load_config → S1.4 footprint hard-coded the q4_0 constant; a future FP16 edit would silently over-admit. REAL — fixed: `KV_PER_SLOT_PER_TOKEN_GB = {"q4_0": 0.000003277}` (S1.4 locks ONLY q4_0; the reviewer's suggested fp16→0.00000176 is the old under-predicting anchor the re-anchor replaced, so unknown dtype = ERR_CONFIG at load, not a guessed constant at boot); load_config validates + exposes `kv_cache`/`per_slot_per_token_gb`; `allocation_need_gb` 5th param (default = module constant); main() threads cfg value into both footprint calls + tier log prints `kv_cache=`. 4 new hermetic tests.
  - P1#2 explicit `boot_deadline_s=None` → `else: deadline = None` → `time.monotonic() < None` TypeError — REFUTED: source order is `if boot_deadline_s is None:` FIRST; /tmp/probe_r13_p1b.py: `wait_instance_healthy(0, cfg, None)` → False in ~2s (no TypeError); end-to-end `spawn_sequence(..., boot_deadline_s=None)` rc=1 start → `rc=0 spawned=[0] start_rc={0:1}`.
  - P2#1 allocation_tier log marker: gate is `free >= need` but marker printed `<=` when granted — the OPPOSITE for every strict grant (worse than the reported edge case) → pinned `{'>=' if granted else '<'}`.
  - P2#2 completion timeout docstring: kwarg is the urlopen SOCKET cap; the S2.2 '<10 s' gate is measured elapsed (timer pre-urlopen). Clarified in docstring.
  - P2#3 @gpu `--batch-size 2048` hardcoded: S1.3 locked value, NOT an S1.6 key — comment at the site (drift would be silent; probes don't check batch size).
  - P2#4 intent file "31 cases" stale → now "44 cases at r13; 31 at r1 — the count grew with each review round's pins".
  - Verified: 182/182 OK; ruff check+format clean; gate PASS; commit 061706c pushed. @gpu r13: 50.7 s (2 instances healthy, stack=healthy) — first run with the kv_cache-validated S1.4 path live. Disposition posted → PR #10 comment 664.
  - P2 trend: 7(r1) → 1(r4) → 3(r5) → 2(r6) → 4(r11) → 3(r12) → 4(r13). P1s since r9: r11=1 real, r12=2 (1 vestige/1 refuted), r13=2 (1 real/1 refuted). Round 14 = confirmation on the new surface (kv_cache load path + log marker + 4 new tests).

### 2026-09-23 — G3 loop PR #10: round 12 reviewed — 0 P0/2 P1/3 P2; P1#1 vestigial (fixed), P1#2 refuted by probe + pin tightened, P2s closed (commit 0f0cf4e)
- G3 PR #10 (MAP 2.1–2.5). Round 12: 0 P0 / 2 P1 / 3 P2 (raw: /tmp/review-phase2-manager-skeleton-r12.txt; stdout /tmp/review-10-stdout-r12.txt, review.sh rc=0).
  - P1#1 `stack_summary` tuple second always None → `build_health` `elif reason:` dead (behavior already correct: `main()` sets `main_only_reason` only in the n_prime==0 branch). REAL vestige — fixed: bare-str return, branch removed, S3.1 contract stated in both docstrings; hermetic callers updated.
  - P1#2 "permanently-bfailed unit flips to degraded on the next cycle" — REFUTED by /tmp/probe_r12_p1b.py: i=0 stays `bfailed` across 3 `_refresh_states` cycles (cycle 1–3: `['bfailed','healthy']`; VERDICT both True). Reviewer's "first miss → degraded" applies only to `healthy`-on-entry (the documented r9 skeleton over-report); non-healthy states are preserved on a miss. r10 pin tightened: `states[0] == "bfailed"` (was `in ("bfailed","degraded")`) + docstring corrected.
  - P2#1 401 auth-wall indistinguishable from healthy → accepted limitation documented in `main_healthy` docstring (S2.1: main = independent infra; pool probes carry the key); keyed probe = MAP 2.6 candidate if the exit-82 gate under-fires.
  - P2#2 @gpu test hardcoded model/alias/api_key/reasoning → new `_model_alias_key_reasoning_from_template()` reads them LIVE from subagent-engine.yaml; `_instance_unit_text` now 11-arg; stale API_KEY/ALIAS/MAIN_CONTAINER module constants deleted (PORT_BASE stays, asserted vs unit at install).
  - P2#3 r10-pin docstring said "i=0 settles to degraded over-report" (wrong) → corrected to "i=0 STAYS bfailed (probe miss preserves prior state; only a fresh healthy probe clears it; MAP 2.6 restarts it)".
  - Verified: 178/178 OK; ruff check+format clean; gate PASS; commit 0f0cf4e pushed. @gpu r12: 39.3 s (2 instances healthy, stack=healthy) — first run with YAML-derived unit text. Disposition posted → PR #10 comment 659 (PATCH-corrected probe quote, verified via /issues/10/comments).
  - P2 trend: 7(r1) → 1(r4) → 3(r5) → 2(r6) → 4(r11) → 3(r12). P1s since r9: r11 = 1 (4xx main_healthy, real), r12 = 2 (1 vestigial fixed / 1 refuted+pinned). Round 13 = confirmation pass on the new surface (bare-str stack_summary + YAML-sourced @gpu unit).

### 2026-09-23 — G3 loop PR #10: round 11 reviewed — 0 P0/1 P1/4 P2; P1 (main_healthy 4xx) + P2#1 (S2.8 #1 shared boot deadline) REAL + FIXED + pinned, P2#2-4 closed, P2#5 naming (commit ed273d3) + @gpu re-verify green (branch `phase2-manager-skeleton`)
- agent/model: Hermes qwen3.8-27b (session: Phase 2)
- phase(s): P2 (G3 review loop for MAP 2.1–2.5)
- round 11 (commit ed273d3, disposition comment 650 + @gpu append):
  0 P0 + 1 P1 + 4 P2. P1 (main_healthy classified all 4xx as up; a
  404/410 on a moved/removed /v1/models misreports healthy, contradicting
  S3.1's exit-82 liveness contract) CONFIRMED + FIXED — semantics
  tightened: 2xx = up; 401 = up (auth-walled, expected healthy shape);
  404/410 = DOWN (route moved/removed; health.json main_healthy feeds
  the exit-82 gate); other 4xx = up (route-level error, server alive);
  5xx/refused/timeout = down. Pinned with test_main_healthy_4xx_semantics
  (6 codes). P2#1 (per-instance boot_target_s deadline → N×180 s worst
  case vs S2.8 #1's 180 s WHOLE-boot acceptance) CONFIRMED + FIXED —
  wait_instance_healthy now takes an optional boot_deadline_s; main()
  sets ONE shared boot_deadline_s = monotonic()+boot_target_s before the
  spawn sequence (the whole boot is capped at boot_target_s, not N × it);
  None → per-instance. Pinned with test_wait_instance_healthy_shared_boot_
  deadline. P2#2 (build_health main-only positional main_only_reason
  brittle to reorder) — the n_prime==0 write_health call now uses the
  explicit main_only_reason= kwarg. P2#3 (unreachable return exit_code
  after the infinite probe loop) — now `return EXIT_OK` with the why
  (exit-4 path already returned; r6 annotation retained). P2#4 (@gpu cap
  assertion is a docker-parse drift detector but the test name doesn't
  say so) — extracted to _assert_memory_cap_is_gib_truncation(ins,
  budget, name) whose docstring carries the pin-reality delta +
  drift-detector contract; the test calls the named helper (kept in the
  one @gpu test, not a 2nd — a standalone cap test would re-boot 2 real
  27B instances to re-inspect containers the first already inspects).
  Hermetic 178/178 OK (+2 new tests). @gpu green post-fix (43.3 s, 2
  healthy, stack=healthy, branch tip ed273d3 — this run now exercises
  the shared S2.8 #1 boot-deadline path live).
- next: round-12 review (convergence pass #3); P1 + P2#1 confirmed +
  fixed + pinned, P2s all closed — expect REVIEW CLEAN, then G4 human
  merge.
- spec deltas: none new (S2.8 #1's 180 s whole-boot budget is now ENFORCED
  by the shared deadline, not amended; S3.1's main-endpoint probe
  contract is now IMPLEMENTED by main_healthy's 404/410-down semantics,
  not changed).

### 2026-09-23 — G3 loop PR #10: round 10 reviewed — 0 P0/1 P1/2 P2; P1 REFUTED by probe + pinned, P2 STOPPING= fixed + pinned (commit 0a83c06) + @gpu re-verify green (branch `phase2-manager-skeleton`)
- agent/model: Hermes qwen3.8-27b (session: Phase 2)
- phase(s): P2 (G3 review loop for MAP 2.1–2.5)
- round 10 (commit 0a83c06, disposition comment 644 + @gpu append 645):
  0 P0 + 1 P1 + 2 P2. P1 (failed i=0 start "permanently masks instance 1's
  health in health.json") REFUTED BY PROBE — ran the real
  spawn_sequence + main() first-health.json build + _refresh_states over
  3 cycles with i=0's start rc=5 and i=1 healthy: spawned=[0,1],
  start_rc={0:5,1:0}, first health.json ['bfailed','healthy'], after 3
  cycles i=1 stays 'healthy' (probes port_base+1 independently; i=0's
  dead port drives only i=0's own state; the reviewer's own round-10 walk
  observed the same ['bfailed','healthy'] outcome). Pinned with new
  hermetic test test_steady_loop_does_not_mask_later_instance_after_
  failed_start (asserts i=1 healthy over 3 cycles + stack=degraded per
  S3.1). P2#1 (exit-4 returns before any STOPPING= → Type=notify stop of
  the still-"starting" unit deadlocks until TimeoutStopSec) FIXED — all
  3 pre-READY exits (exit-4 ERR_VRAM_OOM + both ERR_CONFIG paths: config
  ValueError, MemTotal OSError) now emit STOPPING=1 after STATUS=; module
  docstring exit-4 section records it; pinned in both exit-code tests
  (assert STOPPING=1 in notified). P2#2 (dead `return exit_code` after
  the infinite probe loop) already annotated in round 6 — no change.
  Hermetic 176/176 OK (+1). @gpu green post-fix (68.2 s, 2 healthy,
  stack=healthy, branch tip 0a83c06).
- next: round-11 review (convergence pass #2); P1 refuted + pinned, P2s
  closed/pinned — expect REVIEW CLEAN, then G4 human merge.
- spec deltas: none new (STOPPING= is an implementation detail of the
  pre-READY exits, not a spec value; S2.2/S1.4/S1.5/S2.4 carry).

### 2026-09-23 — G3 loop PR #10: round 9 reviewed — 0 P0/0 P1/3 P2 (reviewer self-downgraded 2 P1s to P2) + P2 pass + @gpu re-verify green (branch `phase2-manager-skeleton`)
- agent/model: Hermes qwen3.8-27b (session: Phase 2)
- phase(s): P2 (G3 review loop for MAP 2.1–2.5)
- round 9 (commit d80264c, disposition comment 639 + @gpu append):
  0 P0 + 0 P1 + 3 P2. The two items the reviewer first raised as P1
  both self-downgraded to P2 in its own reasoning ('Net: P2' / 'Weak
  test, not a defect. P2'). DOD met: no P0 open, no P1 open. P2 pass:
  (a) P2#1 unit_files teardown — note-only (the finally already unlinks
  every unit before any late assert; preconditions re-check pool/port/
  stray per run; systemctl stop on a never-loaded unit is a no-op).
  (b) P2#2 settle test weak — FIXED: test_settle_run_sends_ready_1_before_
  settle_window now captures write_health via side_effect and asserts the
  S3.1 document of the first health.json write (stack=healthy, instance
  state=healthy, main_healthy=True, main_only_reason absent per S2.4) —
  the test now guards states, not just the READY ordering.
  (c) P2#3 _refresh_states single-miss -> degraded over-reports vs S2.2
  two-consecutive — CONFIRMED docstring/code/spec gap; old docstring said
  'keeps the last state on a probe miss' while the code flips
  healthy->degraded on the FIRST miss. Fixed the docstring to state the
  exact skeleton miss-mapping (marked over-report vs S2.2's locked rule;
  MAP 2.6 replaces with the 2-fail counter + 5-fail -> restart) + pinned
  the same intent in the branch comment.
  (d) P2#4 spawn_sequence slow-load left permanently degraded — note-only;
  pinned restart-path ownership to MAP 2.6 (S2.2 five-fail escalation).
  (e) P2#5 cap_bytes assertEqual brittleness — disposed as documented
  intent (the exact-equal IS the docker-g-parse drift detector; integer
  budgets make the truncation a no-op so the brittleness doesn't fire).
  Hermetic 175/175 OK. @gpu green post-P2-pass (73.3 s, 2 healthy,
  stack=healthy, branch tip d80264c).
- next: round-10 review (convergence pass); P0/P1 clean since round 5,
  P2 trend 3 (all notes/hardening) — expect REVIEW CLEAN, then G4 human
  merge.
- spec deltas: none new this session (S2.2/S1.4/S1.5/S2.4 carry from
  rounds 1–8; round 9 is docstring + test-hardening + notes, no
  locked-value spec moves).

### 2026-09-23 — G3 loop PR #10: round 8 reviewed + 3 findings disposed (1 P1/2 P2) + @gpu re-verify green (branch `phase2-manager-skeleton`)
- agent/model: Hermes qwen3.8-27b (session: Phase 2)
- phase(s): P2 (G3 review loop for MAP 2.1–2.5)
- round 8 (commit f6a3be1, disposition comment 634 + @gpu append):
  0 P0 + 1 P1 + 2 P2. P1 (@gpu docstring overstates 'byte-identical to
  S2.2 shape') FIXED — diffed the @gpu user-scope unit against the system
  template line-by-line; the reviewer was right the claim was overstated.
  Beyond the Requires= drop, the @gpu unit ALSO shortens After= (user
  units can't order against system units; the test's manager subprocess
  handles S2.1 main-first ordering) and adds a `, @gpu test` Description
  marker. Module docstring now ENUMERATES the exact 3 user-scope deltas +
  lists everything else that stays byte-for-byte — the shape claim is now
  checkable line-by-line. P2 (load_config coerces non-numeric instance
  key to cryptic int()/float() ValueError mid-boot) FIXED — numeric
  instance keys type-validated at config-load (count/port_base/ctx_size/
  max_num_seqs int, per_instance_budget_gb int-or-float, bool excluded)
  -> named ERR_CONFIG exit 2; range checks stay the S1.7 render-config
  tool's. +2 hermetic tests. P2 (baseline_pids timing) DISPOSED by
  comment — the assertion targets S2.1 'main stack unaffected' (spawn
  didn't kill/replace the main stack); 'not lost' is the right direction,
  a pid appearing after capture is out of scope by construction; comment
  documents the scope. Hermetic 175/175 OK. @gpu green post-fix
  (64.2 s, 2 healthy, stack=healthy, branch tip f6a3be1).
- next: round-9 review (launching); P0/P1 have been the convergence target
  — no P0 open, no P1 open now (r8's P1 was the last). Expect CLEAN or
  only P2 notes, then G4 human merge.
- spec deltas: none new this session (S2.2/S1.4/S1.5/S2.4 carry from
  rounds 1–7; round 8 is docstring + config-validation + a test-scope
  comment, no locked-value spec moves).

### 2026-09-23 — G3 loop PR #10: round 7 reviewed + 1 P2 disposed (0 P0/0 P1, docstring-precision fix) + @gpu re-verify green (branch `phase2-manager-skeleton`)
- agent/model: Hermes qwen3.8-27b (session: Phase 2)
- phase(s): P2 (G3 review loop for MAP 2.1–2.5)
- round 7 (commit f22cad0, disposition comment pending):
  0 P0 + 0 P1 + 1 P2. Loop has converged — P0/P1 clean since round 5,
  P2 trend 7 (r6) -> 1 (r7). The lone P2 re-flags the `systemctl_state`
  forward-hook site (first surfaced r6 P2): the r6 docstring said 'maps a
  unit to the S3.1 instance state', but the function returns the RAW
  `systemctl show -p SubState -p Result` values (SubState='exited'/
  'running', Result='success'); the SubState/Result -> S3.1 string mapping
  is applied by the consumer (MAP 2.6 restart escalation), where the
  forward-hook is wired. Fixed the docstring to state exactly that
  (docstring-only; no behavior delta). Hermetic 173/173 OK. @gpu green
  on branch tip.
- next: final @gpu on f22cad0 + post disposition; a final reviewer pass
  is expected to return REVIEW CLEAN (no P0/P1 open, P2s all annotated/
  disposed), then G4 human merge.
- spec deltas: none new this session (S2.2/S1.4/S1.5/S2.4 carry from
  rounds 1–6; round 7 is a docstring-precision fix, not a spec change).

### 2026-09-23 — G3 loop PR #10: round 6 reviewed + 7 P2s disposed (0 P0/0 P1) + @gpu re-verify green (branch `phase2-manager-skeleton`)
- agent/model: Hermes qwen3.8-27b (session: Phase 2)
- phase(s): P2 (G3 review loop for MAP 2.1–2.5)
- round 6 (commit 6e9dc8b, disposition comment pending):
  0 P0 + 0 P1 + 7 P2 (all "minor, note for later"). DOD met on P0/P1.
  All 7 P2s disposed (fixed, not just noted): removed 3 vestigial params
  (`wait_instance_healthy` base, `run_probe_loop` main_ok,
  `_refresh_states` main_ok — main is re-probed per-cycle in the loop),
  merged the double failed-start log into one line, annotated
  `systemctl_state` as a MAP-2.6 forward-hook + fixed its return
  annotation (`-> dict`, was `-> str`), annotated the unreachable
  non-settle `return exit_code` (SIGTERM is the real path). P2#6
  (`StartLimitIntervalSec=300` not in S2.2 spec): the reviewer
  misread it as @gpu-only — the SYSTEM unit ships it (line 39), so S2.2
  is amended to record it (spec-follows-code, a 300 s window for 3
  restarts so a slow two-27 B-load boot doesn't exhaust the burst) +
  pinned in `test_server_unit_shape`. Hermetic 173/173 OK. @gpu green
  post-fix (43.3 s, 2 healthy, stack=healthy).
- next: round-7 review (launching); expect CLEAN (0 P0/0 P1, P2s now all
  annotated/disposed), then G4 human merge.
- spec deltas: S2.2 (record StartLimitIntervalSec=300; spec-follows-code,
  G3 PR #10 round 6 P2). No locked-value moves.

### 2026-09-23 — G3 loop PR #10: round 5 reviewed + 4 findings disposed (1 P1 fixed, 3 P2 doc-amended/note-only) + @gpu re-verify green (branch `phase2-manager-skeleton`)
- agent/model: Hermes qwen3.8-27b (session: Phase 2)
- phase(s): P2 (G3 review loop for MAP 2.1–2.5)
- round 5 (commit f84b0f0, disposition comment 619 + @gpu append):
  0 P0 + 1 P1 + 3 P2. P1 (settle paths skip READY=1) FIXED — both
  --settle branches (instances + main-only budget_zero) now notify
  READY=1 BEFORE the settle window; under a user-scope Type=notify
  start an exit-0 with no prior READY=1 is a FAILED START in systemd
  notify semantics, so a --settle run (a normal ready boot) would have
  reported failed. New hermetic test asserts rc=0, READY=1 present,
  and READY=1 ordered AFTER the 'booted N instance(s)' STATUS= line.
  P2#1 (probe-loop 5 s urlopen blocks single-threaded loop) — note-only,
  documented in main_healthy docstring (single hard 5 s cap, revisit if
  MAP 2.6 adds concurrent probes). P2#2 (4xx broader than docstring)
  FIXED by docstring — any 4xx = up (liveness, S2.1), incl. a moved
  /v1/models route; behavior unchanged. P2#3 (test import shadowing) —
  note-only, comment at the module-level load site (bin/ is the deploy
  layout; accepted). Hermetic 173/173 OK. @gpu green post-fix
  (63.6 s, 2 healthy).
- next: round-6 review (launching); loop until clean, then G4 human merge.
- spec deltas: none new this session (S2.2/S1.4/S1.5/S2.4 carry from
  rounds 1–2; round-5 changes are behavior/doc clarifications, not spec).

### 2026-09-23 — G3 loop PR #10: rounds 3–4 reviewed + disposed (round 3: 1 P1/2 P2, both P2 refuted-by-read + 1 P1 refuted-by-probe, cap_bytes documented; round 4: 1 P0/2 P1/1 P2, 3 fixed + 1 refuted-by-probe) + @gpu re-verify green x2 (branch `phase2-manager-skeleton`)
- agent/model: Hermes qwen3.8-27b (session: Phase 2)
- phase(s): P2 (G3 review loop for MAP 2.1–2.5)
- round 3 (commit 11f12c3, disposition comment 611): isolated reviewer
  1 P1 + 2 P2. P1 (stale free-read across instances) REFUTED by probe —
  `allocation_tier` calls `free_reader()` inside the per-i loop, so free is
  re-read every spawn; need_gb is a constant threshold, correctly computed
  once. P2 (stale main_healthy) REFUTED by read — `run_probe_loop` re-calls
  `main_healthy()` at the top of every cycle. P2 (cap_bytes brittleness)
  DISPOSED — documented as the deliberate unit-vs-spec drift detector.
  @gpu green (57.7 s, 2 healthy).
- round 4 (commit 064493b, disposition comment 613 + @gpu append):
  1 P0 + 2 P1 + 1 P2. P0 (exit-4/Type=notify fall-through) FIXED — explicit
  `if exit_code == EXIT_VRAM_OOM` block writes health.json
  main_only_reason="ERR_VRAM_OOM", emits STATUS=, returns 4 before READY=1
  (Restart=on-failure is the intended transient-retry path). P1#1
  (fallback_size_bytes unused) FIXED — validated at config-load (ERR_CONFIG
  if not a positive int), carried into cfg, surfaced in the S2.4 N=0
  handoff log for MAP 2.7. P1#2 (cap off-by-2) REFUTED by probe —
  int(float('14.78')*2**30)=15869904158 == spec pinned == docker-probed cap;
  the review's 15869904160 mis-truncated. P2 (read_total_gb OSError uncaught)
  FIXED — try/except OSError -> ERR_CONFIG exit 2. +5 hermetic tests
  (172/172 OK). @gpu green post-fix (59.9 s, 2 healthy).
- next: round-5 review (launching); loop until clean, then G4 human merge.
- spec deltas: none new this session (S2.2/S1.4/S1.5/S2.4 carry from
  rounds 1–2; the ERR_VRAM_OOM reason value is within the S2.4/S3.1
  amendment's open schema).

### 2026-09-23 — G3 loop PR #10: round 2 reviewed, 6 findings (1 P0/2 P1/3 P2) disposed + fixed + @gpu re-verify green x2 (branch `phase2-manager-skeleton`)
- agent/model: Hermes qwen3.8-27b (session: Phase 2)
- phase(s): P2 (G3 review loop for MAP 2.1–2.5)
- items checked: none (review round; spec amendments S1.4/S1.5/S2.4 recorded)
- machine state:
  * branch `phase2-manager-skeleton` @ `a6fbe0e` (G3r2 fixes on top of
    `cbd0022`), pushed to origin
  * round-2 raw review + disposition = comment 607 on PR #10
  * hermetic 167/167 OK (+1 cap-floor case); gate PASS; ruff clean;
    @gpu re-verify green twice (57.3 s / 57.7 s, 2 instances healthy,
    stack=healthy, clean teardown; both runs under the amended
    `max(footprint+1.0, cap)` gate)
- next: G3 round 3 review; loop until clean, then G4 human merge
- blockers/intricacies: none. Round-2 findings (all disposed, see
  comment 607 for the full disposition):
  * P0 bfailed-record: DISPOSITIONED (design is spec-correct — S3.1
    state vocabulary + S1.5 routing + S3.2 reload all key off the
    recorded bfailed index; dropping it would make it invisible to
    status). S1.5 port-collision row amended to remove the ambiguity.
  * P1 tier-vs-cap: FIXED — new `allocation_need_gb() =
    max(footprint + 1.0, per_instance_budget_gb)`; S1.4 boot-OOM
    policy amended with the pin-reality delta (no-op on this box:
    15.08 > 14.78; binds where the 5% margin > 1.0 GB).
  * P1 @gpu stray unit: FIXED — precondition asserts no user-scope
    bonsai2-sglang.service + finally unlinks it.
  * P2 skip-wait-on-failed-start: FIXED — spawn_sequence skips the
    healthy wait when start_rc[i] != 0.
  * P2 budget_zero/fallback_zero: S2.4 amended (v1 shares
    budget_zero; fallback_zero is a MAP 2.7 candidate).
  * P2 places=9: relaxed to places=6 (drift guard, not float noise).
- spec deltas: S1.4 (gate term = max(footprint + 1.0, cap), pin-
  reality delta 2026-09-23), S1.5 (port-collision row: bfailed-record
  semantics + reload re-runs the tier), S2.4 (budget_zero shared
  with the post-PTQ1_0-retry zero; fallback_zero candidate for
  MAP 2.7). No locked acceptance number moved.

### 2026-09-23 — G3 loop PR #10: round 1 reviewed + all 4 fixed + @gpu re-verify green (branch `phase2-manager-skeleton`)
- agent/model: Hermes qwen3.8-27b (session: Phase 2)
- phase(s): P2 (G3 review loop for MAP 2.1–2.5)
- items checked: none (review round; no new MAP items)
- machine state:
  * branch `phase2-manager-skeleton` @ `d91f49f` (G3r1 fixes on top of
    `a9e11a2` the 2.1–2.5 implementation commit), pushed to origin
  * PR #10 open (http://localhost:3001/blckwell/qwen-tory38/pulls/10),
    full PR-body contract at open time; round-1 raw review + disposition
    = comment 603
  * hermetic 166/166 OK (165 + 1 new: main_used_gb live-cgroup source
    label at a value EQUAL to the fallback — the P1 edge); gate PASS;
    ruff clean; @gpu re-verify green (56.1 s, 2 instances healthy,
    stack=healthy, clean teardown)
- next: G3 round 2 review (review.sh re-run after d91f49f); loop until
  clean, then G4 human merge
- blockers/intricacies: none. Round-1 findings: 0 P0 / 1 P1 / 3 P2,
  all fixed in d91f49f:
  * P1 main_used_gb: source was inferred by value comparison — a
    container exactly at the recorded value mislabeled; now returns
    (used_gb, source) and main() logs the path actually taken
  * P2 read_free_gb: docstring overstated the 5 s bound as enforced;
    it is the S1.4 contract on the probe, nominal for the instant
    /proc reader (docstring corrected)
  * P2 spawn rc: spawn_sequence now returns start_rc (index ->
    systemctl start rc) and main() writes bfailed DIRECTLY in the
    first health.json for a failed start (S1.5 routing) instead of
    re-deriving by probing a possibly-dead unit; hermetic assertion
    added on the rc surface
  * P2 @gpu unit template: CPUQuota=64%% reached systemd verbatim
    (plain string, no format applied) -> now CPUQuota=64%
- spec deltas: none (round-1 findings were code/doc, no spec change;
  the S2.2 docker-giB delta was already recorded at PR open)

### 2026-09-22 — Phase 2: MAP 2.1–2.5 implemented + green (branch `phase2-manager-skeleton`, G3 pending)
- agent/model: Hermes qwen3.8-27b (session: Phase 2, branch `phase2-manager-skeleton`)
- phase(s): P2 (2.1 manager skeleton + 2.2 boot-tier + 2.3 allocation tier + 2.4 units + 2.5 spawn path; G3 review pending)
- items checked: 2.1, 2.2, 2.3, 2.4, 2.5 (flipped to [x] in this entry;
  hermetic 165/165 OK + @gpu live spawn PASS)
- machine state:
  * branch `phase2-manager-skeleton` off `origin/main` @ `ecb895a`
  * NEW: `bin/bonsai2-manager` (the S2 manager: hand-rolled sd_notify,
    two-YAML loader, S2.3 planning tier + live cgroup read, S1.4
    allocation tier, S2.5 ordered spawn, S3.1 health.json writer,
    steady probe loop) + `tests/test_manager.py` (31 hermetic cases)
    + `tests/gpu/test_manager_spawn_2_5.py` (@gpu live spawn) +
    SPEC S2.2 pin-reality delta + units/bonsai2-server@.service comment
  * hermetic `Ran 165 tests OK` (was 134; +31 manager cases); gate PASS;
    ruff clean on all three new files (the manager is extensionless,
    outside the gate's `*.py` glob — linted manually)
  * @gpu live run (pool 46.2 GB free, box quiet, main stack asleep):
    manager boot+spawn+settle 46.9 s — S2.3 read the main cgroup LIVE
    (1.97 GB, sleep-accurate) -> N'=2; S1.4 tier need 15.08 GB, i=0
    PASS @47.4 GB -> start -> healthy, i=1 PASS @34.6 GB -> start ->
    healthy; both 27B instances up under the S2.2 `--memory 14.78g` cap
    (docker parses g as GiB + truncates -> 15869904158 B, the spec
    delta); health.json `stack=healthy`, 2 instances healthy,
    main PIDs unchanged; teardown left zero containers
  * no /opt/bonsai2 yet (install = Phase 6); main stack untouched
- next (exact order, on `phase2-manager-skeleton`):
  1. `./scripts/gate.sh` (PASS), commit, Gitea PR, `./scripts/review.sh`
     loop (G3), G4 human merge
  2. after G4: next MAP item = 2.6 (health probe loop: the skeleton has
     the re-probe + health.json refresh; MAP 2.6 owns the
     two-fail->degraded / five-fail->restart escalation — the marked
     hooks are in `_refresh_states`)
- blockers/intricacies:
  * cgroup v2 on this box: docker containers live at
    `/sys/fs/cgroup/system.slice/docker-<id>.scope/` (NOT
    `/sys/fs/cgroup/docker/<id>/`) — `main_used_gb` tries both; the
    cgroup only counts HOST memory (device KV is not cgroup-charged,
    the MAP 1.7 finding) — sleep-accurate means "the main container's
    cgroup is small when SGLang sleeps", exactly what S2.3 wants.
  * user-scope systemd: `Requires=docker.service` is fatal there ("Unit
    docker.service not found") — the @gpu test installs USER units with
    `After=` only (documented deviation; system units keep the S2.2
    verbatim shape). blckwll has Linger=yes so `systemctl --user` works.
  * docker `--memory 14.78g` = GiB + truncation (15869904158 B), ~0.91%
    looser than the decimal-GB budget — SPEC S2.2 delta recorded.
  * main endpoint probe: `GET /v1/models` on the main stack returns 401
    (up, wants its api key) — the manager treats 2xx/4xx as healthy,
    5xx/refused as down (it does not hold the main's key).
- spec deltas:
  * S2.2 pin-reality delta (2026-09-22): docker's `g` suffix = GiB
    (2^30) + float truncation; the S1.4 formula + tier stay decimal-GB;
    no budget value moved.
  * (no other SPEC changes; the S2.3/S1.4 behavior was implemented
    verbatim, no amendment needed)

### 2026-09-22 — G4 merge: PR #9 merged as `deb16fa`, MAP 1.9/1.10/1.11 done
- agent/model: Hermes qwen3.8-27b (session: G4 merge, branch `main` @ `deb16fa`)
- phase(s): P1 (1.9 + 1.10 + 1.11 @gpu tests merged; the three Phase-1
  integration tests are now on main)
- human gate: APPROVED (comment 595: "approved, no need for round 4
  disposition, feel free to merge"). Round-4 review = 0 P0 / 0 P1 / 5 P2
  (notes-of-taste, recorded on the G4 comment 596 — none blocks the DOD).
- review loop (4 rounds, one isolated reviewer): 0 P0 throughout; P1
  3 → 2 → 1 → 0 (converged, each round a different P1, not the same one
  bouncing). Full per-round disposition on the PR (557/563, 572/577,
  584/588, 593/595) + the G4 record + merge result (comment 596).
- merge: Gitea API `do=merge` at 2026-09-22 18:14:46Z → merge commit
  `deb16fa` is the `origin/main` tip. Branch `phase1-edge-oom-kill`
  deleted (remote + local). `./scripts/gate.sh` on main @ `deb16fa`:
  PASS. Hermetic `Ran 134 tests OK`.
- diff scope (6 files): tests/gpu/test_oom_1_9.py,
  test_port_collision_1_10.py, test_kill_restart_1_11.py (new),
  tests/gpu/README.md, SPEC.md (S1.7 "Edge" pin-reality delta), MAP.md.
- items checked: 1.9, 1.10, 1.11 (flipped to [x] in the round-3 entry;
  now merged to main)
- machine state: no subagent instances running; ports 30101–30103 free;
  image `bonsai2-server:1.0` unchanged; main stack untouched.
- next: see the round-3 entry's "next" (it is the active frontier) —
  1.9/1.10/1.11 are now merged, so the frontier moves to the next MAP
  item after them.
- blockers/intricacies: the round-4 5 P2s (notes-of-taste) are recorded
  on the G4 comment (596) — candidate for a follow-up issue, not a gate
  blocker. @gpu-vs-live-session contention note still stands (1.8/1.11
  are the two most timing-sensitive tests; keep the session quiet during
  an @gpu run).
- spec deltas: S1.7 "Edge" pin-reality delta (the engine exits 1 fast on
  a port collision; the S1.5 exit-3 mapping is a unit-layer concern) —
  now on main. No locked value moved.

### 2026-09-22 — G3 loop PR #9: round-3 review posted, 0 P0 / 1 P1 / 3 P2, all 4 fixed, @gpu re-verify green (branch `phase1-edge-oom-kill`)
- agent/model: Hermes qwen3.8-27b (this session)
- phase(s): G3 (PR #9 review loop, round 3)
- reviewer: isolated pi on :30001, diff `origin/main...phase1-edge-oom-kill`
  at `071194f`. Raw output posted to the PR as comment 584 (round 3,
  verbatim, pre-reconcile).
- findings: 0 P0, 1 P1, 3 P2 — all 4 actionable, none waived (commit
  1a19610):
  * P1 1.11 S1.8 #4 budget included the rm-f tear-down guard -> FIXED:
    the acceptance number now measures the reload/ready window AFTER the
    restart `docker run` returns; the teardown guard + rm + run is a
    separately-measured, logged prefix (`teardown_s`). Re-verified live:
    kill → healthy 8.78 s total = teardown+rm+run 5.73 s + reload/ready
    3.05 s; the 15 s S1.8 #4 assert is on the reload/ready. (The 5.73 s
    teardown prefix is the exit-poll waiting for the container's exit to
    propagate — it no longer hides inside the acceptance number.)
  * P2 1.11/1.9/1.10 redundant except-tuple sub-classes -> FIXED:
    OSError subsumes URLError/ConnectionError/TimeoutError; tuples now
    `(json.JSONDecodeError, OSError)` in all four _wait_ready/_probe
    sites.
  * P2 1.9 fixed 15 s tight-pool settle could let a cgroup OOM-kill land
    after the holder release -> FIXED: the tight-pool window is now
    POLLED (break on i2 exit, 60 s bound); the holder releases the
    moment i2 has settled, so a kill cannot be misattributed to the
    split/engine-error shapes. If i2 is still loading at the 60 s mark
    the holder releases and the steady-state poll continues (a late kill
    is accepted — the OOMKilled/137 shape is clean either way).
  * P2 1.9 SIGINT/assert mid-Phase-2 could leak multi-GB holders until
    their 3600 s sleep expires -> FIXED: every holder is registered via
    `self.addCleanup(_release_holder, holder)`; release is idempotent
    and runs on any test exit (failure, interrupt, normal).
- re-verify (clean pool 49.3 GB, box quiet, post-fixes `1a19610`):
  `Ran 3 tests in 112.4 s OK` — 1.9 PASS (tier fail at free 11.97 GB
  deterministic via 7.1 GB holder; path-2 final state
  running=True exit=0 served=True, split shape serving at the 6.0 g cap
  in steady state; instance 0/1 RT 0.94/0.89 s), 1.10 PASS (instance 0
  exit 1 on the bind collision), 1.11 PASS (numbers above).
- next: (SUPERSEDED by the G4 merge entry above — 1.9/1.10/1.11 are
  merged to main as `deb16fa`; the frontier moves to the next MAP item
  after 1.11)
- blockers/intricacies: same @gpu-vs-live-session contention note.
- spec deltas: none.

### 2026-09-22 — G3 loop PR #9: round-2 review posted, 0 P0 / 2 P1 / 3 P2, all 5 fixed, @gpu re-verify green (branch `phase1-edge-oom-kill`)
- agent/model: Hermes qwen3.8-27b (this session)
- phase(s): G3 (PR #9 review loop, round 2)
- reviewer: isolated pi on :30001, diff `origin/main...phase1-edge-oom-kill`
  at `e5f4c51` (round-1 fixes + session log). Raw output posted to the PR
  as comment 572 (round 2, verbatim, pre-reconcile).
- findings: 0 P0, 2 P1, 3 P2 — 4 fixed, 1 waived:
  * P1 1.11 teardown poll inside the S1.8 #4 window -> FIXED: exit-poll
    tightened to 0.2 s rhythm / 5 s bound (on the clean re-run the guard
    itself was < 1 s; SIGKILL'd containers die in milliseconds) — commit
    68ae93a.
  * P1 1.9 clean-exit-0 after the poll could be accepted as the split
    shape -> PROBED + FIXED: most of it was the reviewer conflating a
    RUNNING container's reported exit 0 with "exited cleanly 0" (running
    reports exit 0 by construction — that is the accepted split shape);
    the real sliver (i2 exiting cleanly in the gap after the poll loop)
    is closed by a FINAL state re-read that the branch decision + the
    clean-exit-0 rejection use (68ae93a).
  * P2 1.9 holder 3-round cap's "fail loudly" deferred to the tier
    re-eval -> FIXED: explicit reclamation-defeated log signal when the
    cap is exhausted and free is still above target (68ae93a).
  * P2 1.10 never-served negative check "weaker than intended" -> FIXED
    (annotated, not reworked): the check's soundness rests on the exit
    proven above (the exited container's engine no longer serves the
    alias; the drain thread never returns a /v1/models payload) — no
    fresh serving engine can appear in the window (68ae93a).
  * P2 1.9 `_weights_gb` `size_bytes/1e9` precision -> WAIVED (false
    positive at the pin): the pinned template stores full-precision int
    bytes `size_bytes: 7206168928` (etc/subagent-engine.yaml, HF
    source-object size, verified 2026-09-20); no rounding/truncation in
    the template, so no drift at the pin. Fragile-to-future-YAML-rewrite
    concern noted in the disposition, not in the test.
- re-verify (clean pool 49.4 GB, box quiet, post-fixes `68ae93a`): the
  three touched @gpu tests re-ran green, `Ran 3 tests in 73.0 s OK`:
  1.9 PASS (tier fail at free 11.66 GB deterministic via 7.5 GB holder;
  path-2 FINAL state after 0 s: running=True exit=0 served=True — split
  shape serving at the 6.0 g cap, steady-state; instance 0/1 RT
  1.07/0.86 s; main-stack PIDs unchanged), 1.10 PASS (instance 0 exit 1
  on the bind collision; the real '(startup) loading model' marker
  asserted absent), 1.11 PASS (kill → healthy 8.78 s, budget 15 s; host
  PID 3076735 → 3077606; exit-poll guard < 1 s).
- next: (SUPERSEDED by the G4 merge entry above — the round-2
  disposition + round-3 review both happened; see the round-3 entry)
- blockers/intricacies: same @gpu-vs-live-session contention note as
  the round-1 entry.
- spec deltas: none.

### 2026-09-22 — G3 loop PR #9: round-1 review posted, 0 P0 / 3 P1 / 8 P2, all 11 fixed, @gpu re-run green (branch `phase1-edge-oom-kill`)
- agent/model: Hermes qwen3.8-27b (this session)
- phase(s): G3 (PR #9 review loop, round 1 of N)
- reviewer: isolated pi on :30001 via `./scripts/review.sh phase1-edge-oom-kill`
  (fresh `--no-session --no-tools`, `--thinking off`), diff
  `origin/main...phase1-edge-oom-kill` at `8dafebc`. Raw output posted to
  the PR as comment 557 (round 1, verbatim, pre-reconcile).
- findings: 0 P0, 3 P1, 8 P2 — ALL 11 actionable, none waived:
  * P1 1.9 path-2 outcome measured mid-teardown (holders still up) + the
    split branch's second 180 s ready-wait could race i2 -> FIXED: holders
    released right after the tight-pool spawn settle (the cgroup OOM-kill
    window), outcome then polled in steady state on the loose pool for the
    full 180 s spawn budget; split shape asserts served-at-cap, no wasted
    second wait (commit 02870b0).
  * P1 1.9 segfault marker "Segmentation fault" could false-positive on a
    non-fatal diagnostic -> PROBED: the pinned engine's .so's contain 0
    hits for the literal phrase (no shell in the container prints it; the
    engine's own fatal path is GGML_ASSERT/abort). FIXED: markers narrowed
    to SIGSEGV/SIGBUS/core dumped + an exit-code guard rejecting 135/139
    signal deaths (02870b0).
  * P1/P2 1.9 holder-size assertion message misnamed the cause -> FIXED:
    names the reclamation hypothesis explicitly (02870b0).
  * P2 1.11 `docker rm -f` racing the killed container's exit -> FIXED:
    bounded 10 s exit-poll + rc assert (02870b0).
  * P2 1.11 subset-only "others unaffected" could mask a nvidia-smi
    lagging the main stack -> FIXED: lingering killed host-PID is now a
    logged warning (02870b0).
  * P2 1.10 dummy listener's accept queue never drained -> FIXED: background
    accept-and-close thread (02870b0).
  * P2 1.10 `assertNotIn("load_model")` brittle (C++ symbol, not a log
    line) -> PROBED in the runtime image: the pinned engine logs
    "(startup) loading model" / "loading model '<file>'". FIXED: marker is
    now the real log line (02870b0).
  * P2 MAP session log lacked the per-module @gpu count -> FIXED: 1.7/1.8/
    1.9/1.10/1.11 = 5 integration cases + build-banner 3 + runtime-image 5
    = 13 (05f3aa3).
  * P2 gpu/README wall-time undercounted the split-shape branch -> FIXED:
    ~15-20 min clear pool, up to ~25-30 min worst case (05f3aa3).
- re-run (clean pool, post-fixes): full suite
  `test-logs/gpu-20260922-201840.log` — 11/13 OK: 1.9 restructured
  PASS (outcome after 0s: running=True exit=0 served=True — split shape
  serving at the 6.0 g cap in steady state; instance 0/1 RT 0.88/0.83 s;
  main-stack PIDs unchanged), 1.10 PASS, 1.7 PASS, hermetic image checks
  PASS. Two pre-existing tests failed ON THAT RUN due to GPU contention
  with the live Hermes session (box at 96% util mid-suite — the
  "one heavy thing at a time" discipline): 1.8 aggregate 24.2 t/s
  (< 26.2 floor) and 1.11 restart 18.23 s (> 15 s). Clean re-run of just
  those two (box idle): 1.8 31.9 t/s OK, 1.11 13.95 s OK, exit 0
  (2026-09-22 20:5x). Effective state: @gpu 13/13 green across the
  re-run pair.
- next:
  1. G3c disposition comment on the PR (this entry) — then round-2
     `./scripts/review.sh` on `05f3aa3` (the full diff is unchanged in
     scope; the fixes are in 02870b0/05f3aa3).
  2. G4 human merge gate.
- blockers/intricacies:
  * @gpu runs contend with the main Hermes session's own turns on the
    same SGLang engine / box; keep the session quiet during the run or
    expect 1.8/1.11 to drift (they are the two most timing-sensitive
    tests in the suite).
- spec deltas: none (the S1.7 "Edge" delta from the opening commit
  stands).

### 2026-09-22 — Phase 1: MAP 1.9/1.10/1.11 @gpu tests implemented + green (branch `phase1-edge-oom-kill`, G3 pending)
- agent/model: Hermes qwen3.8-27b (session id 20260922_140725_09d424)
- phase(s): P1 (1.9 OOM-ceiling + 1.10 port-collision + 1.11 kill-9/restart
  @gpu tests implemented, measured live, all green; G3 review pending)
- items checked: 1.9, 1.10, 1.11 (flipped to [x] in this entry; the three
  @gpu tests pass under the full suite)
- machine state:
  * branch `phase1-edge-oom-kill` off `main` @ `b4fd056`; 7 commits
    (58ef48a initial tests + S1.7 delta, c49cfc4 OOM re-base, 11cbbf4
    decimal-GB + holder, 4fdb3da G3r1, 213d7bf G3r2, 836d86f G3r3,
    f35706d G3r4)
  * image `bonsai2-server:1.0` unchanged (re-baked in PR #8, digest
    sha256:ed6068ae…)
  * hermetic 134/134 OK; @gpu 13/13 OK
    (test-logs/gpu-20260922-175630.log; per-module: 1.7 bring-up 1,
    1.8 sustained 1, 1.9 OOM-ceiling 1, 1.10 port-collision 1,
    1.11 kill-9/restart 1 — 5 @gpu integration cases; the other 8 are
    the hermetic image checks that run in the same suite: build-banner
    3, runtime-image 5); gate PASS on branch tip
  * no probe/test containers left; ports 30101–30103 free; main stack
    untouched (PIDs 5581 + 12262 baseline unchanged)
- LIVE MEASUREMENTS (acceptance evidence, final green run
  test-logs/gpu-20260922-175630.log):
  * 1.9 OOM-ceiling (test_oom_1_9.py): S1.4 tier need 15.08 GB
    (footprint 14.08 + 1.0 GB spike margin); i=0 tier PASS free 51.4 GB,
    i=1 tier PASS free 36.2 GB, i=2 ERR_VRAM_OOM at free 12.02 GB
    (deterministic via 9.0 GB host-side memory holder, 2 rounds);
    path-2 spawn at 6.0 g cap → split shape serving at the 6.0 g cap
    (kernel managed the under-budget load, file-backed weights
    reclaimable); instances 0/1 healthy post-probe (16-token RT ~1.1 s /
    ~0.8 s); main-stack PIDs unchanged
  * 1.10 port-collision (test_port_collision_1_10.py): dummy SO_REUSEADDR
    listener on 30101 → instance 0 exited code 1 on the 30101 bind
    collision (ERR_PORT_IN_USE shape, S1.5), instance 1 up on 30102
  * 1.11 kill-9/restart (test_kill_restart_1_11.py): kill -9 engine PID
    (in-container PID 1, witnessed by host PID 2508937) → healthy in
    3.14 s (budget 15 s), host PID 2508937 → 2509315; main-stack PIDs
    unchanged (no baseline PID lost from the compute-apps listing)
- KEY FINDINGS (why the tests assert what they assert):
  1. S1.4 tier is DECIMAL-GB end-to-end (worked example 7.21 GB weights
     = 7206168928 / 1e9; 14.08 = 7.21 + 6.87). All test-side memory
     reads/holdings are decimal (kB / 1e6). A GiB mix-up made the tier
     need 14.58 instead of 15.08 — caught by the first live run.
  2. MemAvailable on this GB10 counts reclaimable page cache, so two
     7 GB loads leave ~19.9 GB "available" — the pool is NOT
     guaranteed below the requirement on its own. The OOM test forces
     a deterministic tier reject with a host-side memory holder that
     touches anonymous memory and is sized to land free ~3 GB below the
     need (reclamation guard: the kernel reclaims clean page cache to
     satisfy part of the holder's allocation, ~4.9 GB on this box; the
     holder is sized in up to 3 rounds until free settles below target).
  3. The path-2 spawn at a 6.0 g cgroup cap (below the 14.08 GB
     requirement) is NOT a guaranteed kernel OOM-kill: file-backed
     weight pages are reclaimable, so the kernel splits layers between
     host and GPU and the engine may still come up (the split shape) —
     it only OOM-kills when the pool is tight enough. The test accepts
     the three clean shapes (cgroup OOM-kill 137, engine clean error
     non-zero exit / observed 133 SIGTRAP, split/running) and rejects
     only segfault-class markers + a clean exit-0 "success".
  4. The engine's port-collision behavior at the pinned commit: exit 1
     in ~0.2 s with "couldn't bind HTTP server socket" — the bind
     attempt happens BEFORE model load, the log goes to stderr. This is
     the S1.7 pin-reality delta (recorded in SPEC S1.7 "Edge"); the
     S1.5 exit-3 mapping is a unit-layer concern, not the engine's.
  5. llama-server is the container init (the nvidia entrypoint execs
     "$@"), so its in-container PID is 1 in BOTH the killed and the
     restarted container. The kill-9 restart witness must be the HOST
     PID (docker .State.Pid), which is allocated fresh per container.
- next (exact order, on `phase1-edge-oom-kill`):
  1. `./scripts/gate.sh` (PASS), Gitea PR, `./scripts/review.sh` loop
     (G3), G4 human merge
  2. after G4: this entry stands as the session record; MAP 1.9/1.10/1.11
     are already [x]
- blockers/intricacies:
  * nvidia-smi on this GB10: `--query-gpu` memory fields N/A; use
    /proc/meminfo MemAvailable for host-pool VRAM probes.
  * The OOM test's memory holder is anonymous host memory — the
    kernel's least-protected allocation — so it can itself be OOM-killed
    if the pool is tighter than the test models; the 3-round cap fails
    loudly rather than silently allocating more.
  * Probe containers must be torn down before @gpu re-runs (port
    30101 collision); the three tests use ports 30101–30103 and the
    suite runs them sequentially.
- spec deltas (on this branch):
  * S1.7 "Edge" pin-reality delta (2026-09-22): the engine exits 1 fast
    on a port collision ("couldn't bind HTTP server socket", bind before
    model load, log on stderr); the S1.5 exit-3 ERR_PORT_IN_USE mapping
    is the unit-layer concern. Recorded in SPEC S1.7 "Edge". No locked
    value moved.

### 2026-09-22 — G4 merge: PR #8 merged as `a567b09`, MAP 1.7/1.8 checked
- agent/model: Hermes qwen3.8-27b (session: G4 merge, branch `main` @ `a567b09`)
- phase(s): P1 (1.7 + 1.8 done; frontier now 1.9)
- items checked: 1.7 (`tests/gpu/test_bringup_1_7.py`, S1.7 integration:
  cold load S2.2 shape, alias, 16-token RT < 10 s, VRAM delta band),
  1.8 (`tests/gpu/test_load_1_8.py`, S1.8 #3: 8×120 s ≥ 26.2 t/s, zero
  5xx + VRAM delta ±10 % now asserted), S1.3 re-bake + image.pin
  re-stamp (`sha256:ed6068ae…`), S1.4 re-anchor (0.000003277 / 14.78 GB /
  N=2, carried from the prior session's uncommitted delta)
- machine state: branch `phase1-bringup-load` merged (server-side,
  Gitea API POST /pulls/8/merge, do=merge) and deleted local+remote;
  `main` @ `a567b09` (local synced); `etc/subagent-engine.yaml` carries
  the re-anchored values (count 2, budget 14.78, kv_cache q4_0, image
  pin ed6068ae…); /opt/bonsai2 still absent (install = Phase 6).
  No probe containers left; port 30101 free; main stack untouched.
- review loop (PR #8, 4 rounds): R1 527/530 → `6f8e7b9`; R2 532/534 →
  `75ecc3d`; R3 535/542 → `399163c` + `03521ac` (P0 review.sh
  word-split→array; P1 #214 footprint helpers + call sites; P1 #260
  WAIVED — probe: the 3 s settle sleep was already in the tree at the
  reviewed head; 3 P2 noted/deferred); R4 539/547 → 0 P0, P1 + 1 P2
  re-statements of user-waived round-2 items, 1 P2 noted/deferred
  (existing-PID growth → S2.5 domain). Missed post step this session:
  R3-disposition (542) landed after R4-raw (539); 542 carries the
  sequencing note. G4 gate comment: 549. User approval: 546.
- post-merge @gpu re-run (on `03521ac`, the first full run after the
  delta-assert wiring): 10/10 OK in 159.6 s
  (`test-logs/gpu-20260922-135803.log`) — load test: 168 completions,
  5376 tokens in 120.2 s → 44.7 t/s aggregate, 0× 5xx, VRAM delta
  13.51 GB (band [13.30, 15.88]); bring-up ok. Hermetic 134/134 OK;
  gate PASS on `96b6149` (the merge head).
- next: 1.9 OOM-ceiling load (S1.7: add instances until the S1.4
  allocation tier fails → last failure ERR_VRAM_OOM, not a segfault;
  prior instances stay healthy). Requires ≥2 instances under a manager
  or by hand — per the MVP safety rule this is the first N>1
  experiment; VRAM audit before each load (the 14.78 GB budget × 2 +
  margin fits: 37.2 GB pool).
- blockers/intricacies: none new. Lesson recorded: verify the assert
  CALL SITE of a "test doesn't assert Y" finding, not just that helpers
  exist; post the G3c-post before firing the next round's G3b.
- spec deltas: none this session (all deltas landed on the branch in
  the prior session: S1.3 pin-reality, S1.4 re-anchor, S0 table row,
  S2.4 fallback, S6.4 drift threshold).

### 2026-09-22 — PR #8 G3 loop continued: round-3 disposition posted + round-4 review posted (pending disposition)
- agent/model: Hermes qwen3.8-27b (session: PR #8 review-loop continuation)
- phase(s): P1 (1.7/1.8 review loop; no new items)
- items checked: none (1.7/1.8 still open until G4 merge)
- machine state:
  * branch `phase1-bringup-load` head `03521ac` (pushed): `399163c`
    (G3c r3: P0 review.sh EXCLUDE_SPECS word-split -> EXCLUDE_ARGS array;
    P1 load-test footprint helpers) + `03521ac` (P1 #214 wiring: the
    meter helpers are now CALLED in test_load_1_8.py — pre-launch
    baseline before docker run, post-window delta asserted in the S1.4
    band [13.30, 15.88] GB, same convention as test_bringup_1_7)
  * PR #8 comment sequence: 527 R1-raw, 530 R1-disposition, 532 R2-raw,
    534 R2-disposition, 535 R3-raw, 539 R4-raw (head 399163c,
    AGENTS.md+README.md excluded via REVIEW_EXCLUDE), 542 R3-disposition
    (posted post-hoc with a sequencing note; covers all 6 R3 findings)
  * hermetic 134/134 OK; gate PASS on 03521ac; image
    bonsai2-server:1.0 unchanged (digest ed6068ae…)
- R3 dispositions (comment 542): P0 review.sh word-split FIXED
  (399163c, live pathspec probe); P1 #214 FIXED in two commits
  (helpers 399163c + call sites 03521ac); P1 #260 WAIVED (probe: the
  3s settling sleep was already in the tree at the reviewed head —
  reviewer read past it; band = user-approved); P2 thread-locks NOTED
  (GIL-atomic, deferred); P2 _completion HTTPError NOTED (real,
  deferred to @gpu hardening); P2 kv_cache flag-set consistency NOTED
  (belongs at S6.3/S2.2 install cross-check, deferred)
- R4 findings (comment 539, head 399163c): 0 P0, 1 P1, 2 P2. All three
  are re-statements of round-2 user-approved items (P1 = band floor
  budget-anchored, already waived-by-user R2; P2 wall-time denominator,
  already fixed+documented R2; P2 = existing-PID growth skew in
  _gpu_delta_gb — a genuinely new note, P2, follow-up candidate).
  R4 disposition PENDING. Note: R4 ran on 399163c, BEFORE the 03521ac
  wiring commit; the P1 #214 class of finding is closed in 03521ac
  regardless.
- next (exact order, on `phase1-bringup-load`):
  1. post the G3c round-4 disposition (comment 539's 3 findings: P1
     waived-by-user band anchor + cite comment 534; P2 wall-time
     waived-by-user + cite 534; P2 existing-PID-growth NOTED/deferred
     — check whether the user wants it wired: _gpu_delta_gb could
     instead sum new-total minus baseline-total, but that would count
     main-stack growth against the instance; document or change at
     user call)
  2. if clean: G4-comment + human merge gate; then flip MAP 1.7/1.8
     with the measurement numbers, re-run tests/run_gpu.sh once for the
     full 10-test green including the new delta assert
- blockers/intricacies:
  * the two footprint helpers landed in 399163c DEFINED but not CALLED
    (half of R3 P1 #214) — the @gpu load test would have passed without
    ever asserting the VRAM half. Fixed in 03521ac. Lesson: when a
    review finding says "test X doesn't assert Y", verify the assert
    CALL SITE, not just that the helpers exist.
  * PR comment ordering: R3-disposition (542) landed after R4-raw (539)
    because the round-4 review was posted before the R3 disposition;
    542 carries an explicit sequencing note so the G4 reader knows the
    timeline.
- spec deltas: none (this session is review-loop mechanics + one test
  assert; no SPEC.md change)

### 2026-09-22 — Phase 1: MAP 1.7/1.8 implemented + measured, S1.3 image re-baked (branch `phase1-bringup-load`, G3 pending)
- agent/model: Hermes qwen3.8-27b (session: continue of 2026-09-21
  mid-flight 1.7/1.8 session)
- phase(s): P1 (1.7 + 1.8 measured + automated; G3 review pending)
- items checked: NONE yet (1.7/1.8 measured green, code on branch;
  flip at G4 merge)
- machine state:
  * branch `phase1-bringup-load` (off `main` @ `c5ccecf`); the previous
    session's 7 uncommitted re-anchor files + this session's image
    re-bake + 2 new @gpu tests, all committed
  * image `bonsai2-server:1.0` RE-BAKED with the corrected S1.3 CMD
    (q4_0 KV, deepseek reasoning, no --no-prompt); new digest
    `sha256:ed6068ae…` stamped in `etc/subagent-engine.yaml` image.pin
    via `bin/bonsai2_build_image.py` (build log
    test-logs/build-runtime-20260922-112836.log)
  * hermetic 134/134 OK; @gpu 10/10 OK (test-logs/gpu-20260922-114519.log)
  * gate PASS; no probe containers left; main stack untouched
- LIVE MEASUREMENTS (acceptance evidence, re-run under the new tests):
  * 1.7 bring-up (test_bringup_1_7.py): `/v1/models` 200 + alias
    `qwen38-27b-subagent` (cold load, host net, --memory 14.78g,
    --gpus all, --ipc=host, --shm-size 4g); 16-token completion
    round-trip < 10 s (measured ~0.8 s); nvidia-smi baseline-subtracted
    footprint delta in the S1.4 ±10 % band (this run: ~13.4 GiB pid
    delta — the known 14.44 GB measurement; the ±10 % band is
    [13.3, 15.88] GB around the 14.78 budget / 14.44 measured)
  * 1.8 load (test_load_1_8.py): 8 concurrent x 120 s -> 59.7-61.9 t/s
    aggregate (two runs: 7168/7424 tokens), 0x 5xx, 224-232 ok
    (target >= 26.2 = 0.9 x 29.16) -> PASS, ~2.3x headroom
- KEY FINDINGS (why the test asserts what it asserts):
  1. `--reasoning-format deepseek` + `max_tokens=16` on a trivial
     prompt ("Say hello") puts ALL 16 tokens in `reasoning_content`
     (`content: ""`, `finish_reason: length`). The S1.7 16-token
     acceptance is the ROUND-TRIP (< 10 s), not the text; the test
     accepts content OR reasoning_content populated (probe 2026-09-22:
     "What color is the sky?" -> `content: 'Blue'`, finish_reason
     stop, 32 tokens split across both fields).
  2. VRAM meter on this GB10 = `nvidia-smi
     --query-compute-apps=pid,used_memory` MINUS the main-stack
     baseline (PIDs 5581 + 12262). The `--query-gpu` memory fields are
     N/A; the cgroup under-charges device KV. The delta function must
     convert MiB -> GB correctly (first run had a /1048576 MiB->TiB
     bug — caught by the band assert, 0.013 GB vs the 13.3 floor).
  3. The image's baked CMD is now the S1.3 verbatim (re-anchored)
     command; the S2.2 unit will override `--port` per instance at
     docker-run time.
- next (exact order, on `phase1-bringup-load`):
  1. commit (done this session), `./scripts/gate.sh` (PASS), Gitea PR,
     `./scripts/review.sh` loop (G3), G4 human merge
  2. after G4: flip MAP 1.7/1.8 checkboxes with the numbers above;
     add the S1.4 re-anchor note to the stale MAP 1.6 body text
     (0.00000176 -> 0.000003277, 10.91 -> 14.08, N=3 -> N=2); copy
     /tmp measurement artifacts into test-logs/ (gitignored) if not
     already there
- blockers/intricacies:
  * nvidia-smi on this GB10: `--query-gpu` memory fields N/A;
    `--query-compute-apps=pid,used_memory` is the working meter
    (baseline-subtracted).
  * Under `--reasoning-format deepseek`, short prompts can be
    reasoning-only; don't assert on `content` alone for a small
    max_tokens cap.
  * Probe containers must be torn down before @gpu re-runs (port
    30101 collision).
- spec deltas (on this branch, uncommitted -> commit this session):
  * S1.3: dropped `--no-prompt`, `deepseek_r1` -> `deepseek`, added
    `--cache-type-k q4_0 --cache-type-v q4_0` + a "pin reality" delta
    note (all in the image re-bake commit)
  * S1.4: `per_slot_per_token_GB` 0.00000176 -> 0.000003277 (measured
    q4_0 KV); S0 table row ~14.0-14.5 GB measured 14.44 GB; worked
    example floor(37.2/14.78)=2; S2.4 PTQ1_0 fallback recompute; S6.4
    drift-threshold budget 11.5 -> 14.78
  * etc/subagent-engine.yaml: count 3->2, per_instance_budget_gb
    11.5->14.78, new `kv_cache: q4_0` top-level key,
    reasoning_format deepseek_r1->deepseek, image.pin re-stamped to
    sha256:ed6068ae…
  * bin/bonsai2_vram.py: PER_SLOT_PER_TOKEN_GB 0.000003277,
    DEFAULT_BUDGET_GB 14.78, docstrings re-anchored
  * bin/bonsai2_render_config.py: new `kv_cache` validation (pin-
    supported --cache-type-k/v allowlist)
  * tests: test_vram.py + test_render_config.py re-anchored (134 total);
    tests/gpu/test_bringup_1_7.py + test_load_1_8.py NEW;
    test_runtime_image.py CMD assertion updated to the re-anchored flags

### 2026-09-21 — Phase 1: MAP 1.7/1.8 measured live + S1.3/S1.4 re-anchored (branch `phase1-bringup-load`, mid-flight)
- agent/model: Hermes qwen3.8-27b (session id 20260921_201742_4d9b75)
- phase(s): P1 (1.7 + 1.8 measured; implementation mid-flight, uncommitted)
- items checked: NONE yet (1.7/1.8 still open — measurements done,
  code/tests not committed; see next)
- machine state:
  * branch `phase1-bringup-load` (created off `main` @ `c5ccecf`), 6 files
    modified UNCOMMITTED: SPEC.md, bin/bonsai2_vram.py,
    bin/bonsai2_render_config.py, etc/subagent-engine.yaml,
    tests/test_vram.py, tests/test_render_config.py
  * hermetic suite 134/134 OK (was 130; +4 kv_cache validator tests)
  * image `bonsai2-server:1.0` STILL BAKES THE OLD S1.3 CMD (`--no-prompt`
    + `deepseek_r1`, no `--cache-type-k/v`) — needs re-bake; the probe
    runs used the corrected flags via CMD override
  * probe containers all removed; main stack only (PIDs 5581 ~1.4 GiB +
    12262 ~64.5 GiB sglang); MemAvailable ~44 GiB baseline; port 30101 free
- LIVE MEASUREMENTS (the acceptance evidence; artifacts in /tmp, not yet
  copied to test-logs/):
  * 1.7 bring-up: probe `bonsai2-probe-i0` (corrected flags, `--memory
    11.5g` era, host net, :30101) → `/v1/models` 200 `qwen38-27b-subagent`
    in ~10–15 s; 16-token completion round-trip **0.83 s** (< 10 s
    acceptance), single-stream decode 27.8 t/s, `reasoning_content`
    populated under `--reasoning-format deepseek`
  * 1.7 VRAM delta (meter: `nvidia-smi
    --query-compute-apps=pid,used_memory`, baseline-subtracted): FP16 KV
    (literal old S1.3) **24.59 GiB**; q4_0 KV (`--cache-type-k/v q4_0`)
    **13.45 GiB = 14.44 GB** (stable across 2 runs); weights+overhead
    only 7.05 GiB → KV component ≈ 6.40 GiB; cgroup scope peak only
    ~7.6 GiB (device KV not fully cgroup-charged — do NOT meter via cgroup)
  * 1.8 load (8 concurrent × 120 s on the q4_0 instance): **50.7 t/s
    aggregate, 384 ok, 0× 5xx** (target ≥ 26.2 = 0.9 × 29.16) → PASS,
    2× headroom
- KEY FINDINGS (why the spec moved — "spec follows code"):
  1. The literal S1.3 command CANNOT BOOT at pin 9a9394a8:
     `--no-prompt` is rejected at arg-parse (absent in the fork's
     `common/arg.cpp`; also absent in upstream + the Bonsai-demo
     launcher); `--reasoning-format deepseek_r1` rejected (the pin's
     `common_reasoning_format_from_name` in `common/chat.cpp` accepts
     `none|auto|deepseek|deepseek-legacy`).
  2. The old S1.4 constant (0.00000176, "3.7 GB KV") corresponds to
     q4_0-class KV; the old S1.3 had no `--cache-type-k/v` flags, so the
     server ran FP16 KV (24.59 GiB measured) — the 10.91 GB/11.5 GB
     budget was never achievable with the literal S1.3 command.
  3. User-approved Option A ("a, go ahead"): lock S1.3 to q4_0 KV and
     re-anchor S1.4 to the live q4_0 measurement. Consequence: default
     N = floor(37.2 / 14.78) = **2** (was 3); measured instance
     footprint 14.44 GB; budget figure 14.78 GB (14.08 + ~5% margin);
     2 instances load ~28.8 GB, ~8.4 GB headroom; idea.txt 40 GB floor
     still respected as a safe start.
- next (exact order, all on `phase1-bringup-load`):
  1. re-bake the runtime image: `image/Dockerfile.runtime` CMD (line
     ~215–223) still has `--no-prompt` + `deepseek_r1` and lacks
     `--cache-type-k q4_0 --cache-type-v q4_0` → update CMD, re-run
     `bin/bonsai2_build_image.py`, stamp the new digest into
     `etc/subagent-engine.yaml` image.pin (write_image_pin flow)
  2. `tests/gpu/test_runtime_image.py::test_default_cmd_is_s1_3_verbatim`
     (line ~199–239) asserts the OLD flags — update expected list
  3. write the two new @gpu tests:
     * tests/gpu/test_bringup_1_7.py — launch instance 0 from the image
       per S1.3 (host net, `--memory {per_instance_budget_gb}g` → now
       14.78, `--memory-swap` same, `--shm-size 4g --gpus all --ipc=host`),
       readiness poll `/v1/models` → 200 + `qwen38-27b-subagent`, 16-token
       completion RT < 10 s, nvidia-smi delta vs S1.4 formula ±10%
       (band ~13.3–15.5 GB around the 14.44 GB measured)
     * tests/gpu/test_load_1_8.py — 8 concurrent completions × 120 s →
       aggregate ≥ 26.2 t/s, zero 5xx; follow tests/gpu/README.md
       conventions; run hermetic + @gpu sequentially (one GPU rule)
  4. run `python3 -m unittest discover -s tests` (expect 134 OK) +
     `tests/run_gpu.sh` (expect old 8 + new tests OK)
  5. commit, `./scripts/gate.sh`, Gitea PR, `./scripts/review.sh` loop,
     G4 human merge; then flip MAP 1.7/1.8 checkboxes with the
     measurement numbers above + copy /tmp artifacts into test-logs/
     (gitignored)
  6. refresh the stale 1.6 body text in MAP.md (says "0.00000176
     constant", "10.91 GB footprint; N=3 at 37.2/11.5") — historical
     record, low priority
- blockers/intricacies:
  * VRAM meter on this GB10 = `nvidia-smi --query-compute-apps=pid,used_memory`
    MINUS the main-stack baseline (cgroup under-charges device memory:
    scope peak ~7.6 GiB vs ~13.45 GiB true footprint). `--query-gpu`
    memory fields are N/A on unified memory.
  * bare `docker run img --flag v` with the nvidia entrypoint breaks
    (`exec "$@"` treats the first `--` token as an exec option) — use
    the image's baked CMD (binary path first) or `--entrypoint` with the
    full command.
  * `--memory` cap: S1.7's "memory cap" = S2.2's `--memory` =
    per_instance_budget_gb, which is now **14.78** (the 11.5g probes
    predate the re-anchor).
  * /tmp helpers: measure_17_18.py, measure_kv4.py, bonsai-start.sh,
    base.txt (baseline PIDs). Baseline taken at session start; re-take
    before new measurements (PIDs change across reboots).
- spec deltas (all UNCOMMITTED, on `phase1-bringup-load`):
  * S1.3: dropped `--no-prompt`, `deepseek_r1` → `deepseek`, added
    `--cache-type-k q4_0 --cache-type-v q4_0` + a "pin reality" delta note
  * S1.4: `per_slot_per_token_GB` 0.00000176 → 0.000003277 (measured
    q4_0 KV, MAP 1.7); S0 table row → ~14.0–14.5 GB measured 14.44 GB;
    worked example → floor(37.2 / 14.78) = 2, ~28.8 GB loaded, ~8.4 GB
    headroom; S2.4 PTQ1_0 fallback recompute (12.82 GB / ~13.46 budget);
    S6.4 drift-threshold note budget 11.5 → 14.78
  * etc/subagent-engine.yaml: count 3→2, per_instance_budget_gb 11.5→14.78,
    new `kv_cache: q4_0` top-level key, reasoning_format deepseek_r1→deepseek
  * bin/bonsai2_vram.py: PER_SLOT_PER_TOKEN_GB 0.000003277,
    DEFAULT_BUDGET_GB 14.78, docstrings re-anchored
  * bin/bonsai2_render_config.py: new `kv_cache` validation — allowlist
    taken from the pinned fork's `kv_cache_types` list
    (third_party/llama.cpp/common/arg.cpp:304: f32, f16, bf16, q8_0,
    q4_0, q4_1, iq4_nl, q5_0, q5_1), case-insensitive
  * tests: test_vram.py re-anchored (constant, N=2, 14.78 budget, 6.87 GB
    KV worked example); test_render_config.py fixture (count 2, 14.78,
    deepseek, kv_cache q4_0) + 4 new kv_cache cases → 134 tests

### 2026-09-21 — Phase 1: MAP 1.4/1.5/1.6 merged (PR #7, `a203f79`), 4-round review loop
- agent/model: Hermes qwen3.8-27b (session: G4 merge, branch `main` @ `a203f79`)
- phase(s): P1 (1.4 + 1.5 + 1.6 done; frontier now 1.7)
- items checked: 1.4 (`image/Dockerfile.runtime` + `bin/bonsai2_build_image.py`
  orchestrator + buildx timeout + `write_image_pin`), 1.5
  (`bin/bonsai2_render_config.py` validator + writer, `copy.deepcopy`),
  1.6 (`bin/bonsai2_vram.py`), hermetic suites 130 tests OK,
  @gpu `tests/run_gpu.sh` 8/8 against `bonsai2-server:1.0`.
- machine state: branch `phase1-runtime-image-vram` deleted local +
  remote; `etc/subagent-engine.yaml` `image.pin` stamped with the live
  digest (`sha256:0fff95f5…` = `docker image inspect
  bonsai2-server:1.0 .Id`); /opt/bonsai2 still absent (install = Phase 6).
- review loop (senior-git-1.0, PR #7 comments 506–521): round 1 → buildx
  `subprocess.run` had no timeout (P1, fixed `f8b2d55`); round 2 →
  collision map dropped out-of-range port overrides (P1, fixed
  `3ca5fdc`) + 2 P2 documented; round 3 → `write_image_pin` hardcoded
  4-space indent (P1, fixed `a85c9a2`) + exit routing substring match
  (P1, fixed) + 3 P2 (2 fixed: `json` deep-copy → `copy.deepcopy`,
  test_smoke import-walk module-scope; 1 noted); round 4 → P1
  (`write_image_pin` target: tracked template vs deploy target) WAIVED on
  user approval — design-level, deferred to the S6.4 install.sh item
  (the rebuild-skip check must decide which file is the authoritative
  `image.pin`; if the deploy-target write is preferred later, the change
  is localized to `main()` via `write_image_pin(..., out_path=…)`).
- next: 1.7 manual single-instance bring-up (`docker run` per S1.3,
  `--gpus all`, `--port 30101`); the @gpu test covers artifacts +
  library resolution only — inference is 1.7's job.
- blockers/intricacies: none new. PR #7 G4 merge gate comment 520.
- spec deltas: none new this session.

### 2026-09-21 — Phase 1: MAP 1.3 merged (PR #5, `ea87132`), 7-round review loop
- agent/model: Hermes qwen3.8-27b (session: G4 merge, branch `main` @ `ea87132`)
- phase(s): P1 (1.3 done; frontier now 1.4)
- items checked: 1.3 (tool + config + hermetic tests; live download deferred
  to install, MAP 6.2 step 3)
- machine state: `bin/bonsai2_model_fetch.py` (412 L, stdlib-only) +
  `tests/test_model_fetch.py` (18 tests) + pinned `etc/subagent-engine.yaml`
  model block on main; branch `phase1-model-fetch` deleted local + remote;
  /opt/bonsai2 still absent (install = Phase 6).
- next: 1.4 build `bonsai2-server:<tag>` runtime image (S1.6 image_pin) —
  reuses the MAP 1.2 build pipeline (`image/Dockerfile`, local tag
  `bonsai2-server-build:121a`); the runtime image bakes the fork binary
  + model files + CUDA runtime and records its content digest.
- blockers/intricacies: none new. Gitea 1.27.1 merge route quirk:
  `POST /pulls/5` returns 405; the working route is
  `POST /api/v1/repos/<o>/<r>/pulls/5/merge` with `{"do":"merge",
  "merge_commit_message": ..., "delete_source_branch": false}` (422 with
  "Required: do" was the discoverer). G3b/G3c round-7 comments were missed
  in the round-7 post step; backfilled as PR #5 comments 500/501 alongside
  the G4 record (502).
- spec deltas: none new this session (S1.1/S1.6 object-name delta landed
  on the branch with the PR).

### 2026-09-20 — Phase 1: build pipeline landed (item 1.2), fork binary built at pin
- agent/model: Hermes qwen3.8-27b (session: Phase 1, branch `phase1-build-pipeline`)
- phase(s): P1 (1.2 done; G3 pending on this branch)
- items checked: 1.2
- machine state: `image/Dockerfile` (build pipeline) + `.dockerignore` +
  `tests/gpu/test_build_banner.py` (@gpu banner test, 3 cases) on branch
  `phase1-build-pipeline` (cut from origin/main 1f515a4, i.e. post-1.1
  merge). LOCAL docker image `bonsai2-server-build:121a` built (not
  pushed; build pipeline artifact — the S1.6 image_pin is the MAP 1.4
  runtime image, a different thing). Inside: aarch64 ELF
  /opt/bonsai2/bin/llama-server + all shared libs (RUNPATH $ORIGIN,
  patchelf). No /opt/bonsai2 host dir yet (that is install/1.4+ work).
  Gitea API: the session token (from the 2026-09-20 session) still
  valid.
- next: 1.3 model download + SHA256 (PQ2_0 default + PTQ1_0 fallback,
  retry x3/10s, ERR_HASH_MISMATCH exit 4). Note: local PTQ1_0 already on
  the box (LM Studio copy, 5.95 GB) — the 1.3 download is PQ2_0 (7.21 GB)
  to /opt/bonsai2/cache + the fallback SHA check; disk /opt has 353 GB.
- blockers/intricacies: (1) ggml-cuda links the CUDA driver API (VMM:
  cuMemCreate & co) to libcuda.so.1; the CUDA image only ships the
  toolkit STUB libcuda.so (SONAME libcuda.so.1, filename mismatch), so
  the exe link fails with undefined cuMem* refs — fixed by apt
  `libnvidia-compute-580` in the build image (library-only, no kernel/GL
  deps; provenance: NVIDIA apt repo, machine rule) + -rpath-link to the
  stubs dir as belt-and-braces. (2) The build context has no git metadata
  (COPY strips .git; the submodule .git is a gitlink FILE, not a usable
  repo), so the build-info commit line is supplied via
  -DLLAMA_BUILD_COMMIT=<engine_pin prefix> (CMake only falls back to
  git-derived BUILD_COMMIT when the var is unset — verified in
  third_party/llama.cpp/CMakeLists.txt:159). LLAMA_BUILD_COMMIT/
  LLAMA_BUILD_NUMBER are the documented override knobs. (3) This fork
  links libllama-server-impl.so as a separate shared lib — a hardcoded
  lib copy list would miss it; use a `lib*.so*` glob. (4) --version does
  NOT print the "CUDA1 = 1" system-info line (that is a model-load
  banner); it runs ggml_cuda_init anyway, which in the GPU-less build
  container logs "no CUDA-capable device is detected" — expected, not a
  failure. CUDA backend assertion = `ldd | grep libggml-cuda`. SPEC S1.8
  #1 was made concrete accordingly (commit-carrying build line +
  libggml-cuda linked). (5) sm_121a check: grep compile_commands.json
  (nvcc command lines), asserted in-build.
- spec deltas: S1.8 #1 made concrete at the pin (banner = commit-carrying
  build line + libggml-cuda linked; no separate ternary banner line in
  this fork). No other SPEC changes.
- G3c review loop (same session, continued): 5 isolated review rounds
  on PR #4 (blckwell/qwen-tory38, Gitea), all raw-output + disposition
  comments posted. R1 (9d2b72f): 1 P0, 2 P1, 2 P2 -> fixed in 0ebcc5d
  (.dockerignore dedupe, env -i transitive-lib check, token prefix
  dropped from this log). R2 (0ebcc5d): 0 P0, 4 P1, 3 P2 -> fixed in
  4b45764 (exact-arch assert, unmasked libggml-cuda ldd, --gpus all on
  the banner test, pin-parse docstrings). R3 (4b45764): 1 P0, 2 P1,
  2 P2 -> fixed in 4480fa3 (arch positive+exhaustive asserts on
  compile_commands.json, nproc-capped -j, boundary-anchored pin parse,
  root-docs named instead of blanket *.md so fork .md stays in context,
  LD_LIBRARY_PATH scope note). R4 (4480fa3): 0 P0, 1 P1, 2 P2 -> fixed
  in 8b75332: the P1 was the pin being ARG-hardcoded AND in the YAML
  (silent-drift risk) -- now the pin is read from
  etc/subagent-engine.yaml in-build (S1.6 source of truth), the
  LLAMA_PIN_SHORT build-arg only cross-checks it (ERR_PIN_DRIFT exit 4,
  negative-tested with a fake arg in 0.1 s); patchelf loop now skips
  versioned symlinks deliberately and fails loud on a real .so
  (ERR_RPATH); .git ignore rule documented as form-agnostic
  (submodule gitlink file OR plain-clone dir). R5 (8b75332): 0 P0,
  1 P1, 3 P2 -- posted raw-only per user instruction, NOT yet
  dispositioned/fixed: P1 = anchor the banner check to the full
  `version:` line rather than a `commit <8hex>` substring; P2s =
  broken-symlink robustness, pin helper erroring when the template is
  absent (fresh clone), exhaustive-arch check's file-path assumption.
  Branch head 8b75332; gate PASS; @gpu 3/3; last rebuild exit 0 with
  all in-build assertions (banner commit 9a9394a8, sm_121a-only,
  aarch64, libggml-cuda, env -i banner + ldd-from-own-dir).
  Next: fix R5 (or user disposition), final review pass, G4 merge gate;
  then MAP 1.3 (model download + SHA256).

### 2026-09-20 — Phase 1 start: fork pinned at 9a9394a8, vendored as submodule (item 1.1)
- agent/model: Hermes qwen3.8-27b (session: Phase 1, branch `phase1-fork-pin`)
- phase(s): P1 (1.1 done; G3 pending on this branch)
- items checked: 1.1
- machine state: branch `phase1-fork-pin` cut from origin/main (371747a);
  `third_party/llama.cpp` submodule added at `9a9394a8`
  (PrismML-Eng/llama.cpp `prism` tip, 2026-09-18 commit "build: fix Ubuntu
  ARM64 and Windows Vulkan release jobs"); `.gitmodules` tracks the
  `prism` branch; `etc/subagent-engine.yaml` `engine_pin.commit` filled
  with the SHA; ARTIFACTS.md in-repo row updated; SPEC S1.2 build flags
  backported to the pin's reality. No /opt/bonsai2, no bonsai units/images
  (unchanged). No uncommitted work on the box.
- next: 1.2 build pipeline (docker build stage: nvidia/cuda:13.0.2-devel
  image + apt cmake/ninja + cmake flags now per the updated S1.2, arch
  `121a`), including the 1.1-owed stock-build refusal probe: build a
  stock llama.cpp and attempt a PQ2_0 load, expect refusal, record the
  result in the session log. Note the stock probe is cheap to skip if the
  fork build (1.2 proper) lands first and the 1.1 re-verify can piggyback
  on the fork's load test at 1.7 instead — say so in the PR.
- blockers/intricacies: (1) Spec delta S1.2: `LLAMA_BONSAI_*` cmake
  toggles do not exist at the pin (grep -r "BONSAI" over CMakeLists.txt +
  cmake/ = zero hits); the fork compiles its ternary kernels
  unconditionally. S1.2 now cites the demo script AT THE PIN; the MAP 1.2
  flags changed accordingly (Ninja generator,
  `-DCMAKE_CUDA_ARCHITECTURES="121a"`). (2) Fork-vendoring decision:
  submodule chosen (the 0.1 layout line, the AGENTS.md "chosen default");
  the alternative (plain clone + pin in ARTIFACTS.md) noted in the PR as
  the open decision per the uncertainty rule. (3) The submodule is a real
  clone of a large repo: `git submodule update --init` on re-clone pulls
  the whole fork history; acceptable, and a `.gitmodules` `branch = prism`
  means `git submodule update --remote` tracks the right ref when the pin
  bumps via PR.
- spec deltas: S1.2 build-flags paragraph rewritten to match the fork at
  `9a9394a8` (no `LLAMA_BONSAI_*` toggles; demo-script flags at the pin;
  arch list replaced by `121a`). Backport per the document conventions
  (implementation finding contradicted the spec; code/pin was right).

### 2026-09-20 — Phase 0 review loop closed: round-14 lint-alignment + session log (items 0.1–0.4, G3b rounds 1–14)
- agent/model: Hermes qwen3.8-27b (session: Phase 0, branch `phase0-scaffolding`)
- phase(s): P0 (0.1–0.4 done; G3b independent-review loop closed at round 14)
- items checked: 0.1, 0.2, 0.3, 0.4 (unchanged this session — all already
  [x]; this session closed the review loop + aligned the lint contract)
- machine state: no /opt/bonsai2, no bonsai2 units/images (Phase 0 is
  repo-only). Pre-commit hook ENABLED in this clone (core.hooksPath=
  .githooks). NEW contract alignment (G3c round 14 P1): `scripts/gate.sh`
  now runs shellcheck HARD when installed (was WARN-only) and a NEW ruff
  step (format --check + check, HARD when `ruff` or `uvx ruff` is
  available) — the pre-commit tripwire and the formal G2 gate now run the
  SAME lint contract. Tree made ruff-clean: `scripts/diagram_qwen38_stack.py`
  + `tests/test_smoke.py` reformatted + 2 ruff issues fixed (import order,
  unused `fill`→`_fill`) + the diagram script chmod +x (EXE001). ruff is
  uv-managed on this box (not on the bare PATH), so the gate/hook detect
  it via `uvx ruff`. Gate PASS, hermetic suite OK (2 tests), hook green.
- next: Phase 1, item 1.1 — pin the fork commit (PrismML-Eng/llama.cpp
  `prism` branch; check tip SHA + the fork-vendoring decision: submodule
  vs clone+pin; add `third_party/llama.cpp` per the 0.1 layout). Note the
  gate baseline is now "ruff available → hard, shellcheck not installed →
  soft-skip": a fresh session must not read the old "shellcheck soft" note
  as still authoritative.
- blockers/intricacies: (1) round-14 P1 was a real contract gap: the
  tripwire checked `.py` at all and treated shellcheck as hard-when-
  installed, while the formal gate treated shellcheck as WARN-only and had
  no ruff step — fixed by aligning gate.sh UP to the documented contract
  (not the hook down), so the tripwire cannot block what the gate allows.
  (2) ruff availability axis: `command -v ruff` is false on this box (uv
  manages it), so both gate.sh and the hook now fall back to `uvx ruff`;
  if a future session installs ruff on the bare PATH, the `ruff` branch
  wins and uvx is not used. (3) `scripts/diagram_qwen38_stack.py` lives
  OUTSIDE tests/, so its ruff reformat did NOT trip the stdlib-import
  AST check — that scanner walks tests/ only (the hermetic suite root),
  while the gate's ruff step covers every tracked .py.
- spec deltas: none (round-14 findings were tripwire/gate contract, not
  SPEC content; no SPEC.md change).

### 2026-09-20 — Phase 0 scaffolding: layout, test runner, toolchain, pre-commit (items 0.1–0.4)
- agent/model: Hermes qwen3.8-27b (session: Phase 0, branch `phase0-scaffolding`)
- phase(s): P0 (0.1–0.4 done; Phase 0 exit criteria met)
- items checked: 0.1, 0.2, 0.3, 0.4
- machine state: repo now carries the full Phase 0 layout (bin/, etc/
  templates ×4, units/ placeholders ×2, tests/ + tests/gpu/,
  .githooks/pre-commit, scripts/install-hooks.sh). Pre-commit hook
  ENABLED in this clone (core.hooksPath=.githooks — a fresh clone must
  run `./scripts/install-hooks.sh` once). No /opt/bonsai2 yet, no
  bonsai units/images. NEW local docker image:
  `nvidia/cuda:13.0.2-devel-ubuntu24.04` (digest in ARTIFACTS.md;
  build-stage toolchain for MAP 1.2 — note cmake+ninja must be apt'd
  inside it). Hermetic suite green: `python3 -m unittest discover -s
  tests` (2 tests). Gate PASS on the branch tip.
- next: Phase 1, item 1.1 — pin the fork commit (PrismML-Eng/llama.cpp
  `prism` branch; check tip SHA + the fork-vendoring decision: submodule
  vs clone+pin, Open items; add `third_party/llama.cpp` per the 0.1
  layout) → 1.2 build pipeline (uses the pinned CUDA image from 0.3).
- blockers/intricacies: (1) driver on this box is now 580.159.03 —
  SPEC S1.2/S6.2 recorded 580.126.09; still in-matrix (≥ 580), no spec
  delta needed, but the spec's "this box" column is stale. (2) The
  CUDA base image ships WITHOUT cmake/ninja (verified in-container):
  the 1.2 build stage = image + `apt-get install -y cmake ninja-build`
  + cmake S1.2 flags; don't assume a stock toolchain inside. (3)
  nvidia-smi on this GB10 returns `[N/A]` for memory.used/mem.total
  via --query-gpu (unified-memory driver quirk) — use
  `nvidia-smi -q -d MEMORY` or /proc/meminfo for VRAM probes in
  S1.4/S2.3 code; recorded in run_gpu.sh's pre-snapshot.
- spec deltas: none (0.3's driver version note is a re-measurement,
  the spec's "≥ 580" requirement is the binding one).

### 2026-09-20 — AGENTS.md + ARTIFACTS.md created; fork merge status checked
- agent/model: Hermes qwen3.8-27b (session: guidance docs, no implementation)
- phase(s): P0 (0.0 done)
- items checked: 0.0
- machine state: docs now in place: SPEC.md (master, 1269 lines), MAP.md
  (ledger), AGENTS.md (agent guidance), ARTIFACTS.md (external-artifact
  ledger). scripts/gate.sh + scripts/review.sh installed; gate baseline
  PASS (exit 0, 2026-09-20). No /opt/bonsai2, no bonsai2 units, no images.
  Main stack up 26h (qwen38-dflash2:v1.2.3). Local weights: PTQ1_0
  (5.95 GB) + mmproj in ~/.lmstudio/models/prism-ai/...PTQ1_0-GGUF/;
  PQ2_0 (SPEC default) not yet downloaded. No shellcheck/ruff installed
  (soft in gate/pre-commit until installed).
- next: Phase 0 items 0.1–0.4 (layout, test runner, toolchain,
  pre-commit hook + install-hooks.sh).
- blockers/intricacies: three open items in MAP "Open items" (fork
  vendoring top-2 open; PQ2_0 download pending; linters uninstalled).
  Fork merge status verified 2026-09-20 via PrismML docs + ls-remote:
  `prism` branch active at 9a9394a8 (tip at check time); PQ2_0/PTQ1_0
  packing still fork-only — re-verify by stock-build load test at 1.1.
  Gitea Actions runner is OFF on this box (1.27.1) — relevant to the
  post-MVP CI/CD proposal (7.4).
- spec deltas: none (AGENTS.md/ARTIFACTS.md are new planning docs, not
  spec changes; MVP single-instance safety rule restates S0's
  `--instances 1` rollout, no delta).

### 2026-09-20 — Map created from SPEC.md
- agent/model: Hermes qwen3.8-27b (session: map creation, no implementation)
- phase(s): none (planning)
- items checked: none
- machine state: fresh repo, spec only — `SPEC.md` (1269 lines, all sections
  locked, G4-approved via 58ae21f), `idea.txt`, 3 transcripts, `scripts/`
  (review.sh, gate.sh, diagram_qwen38_stack.py). No `/opt/bonsai2`, no
  bonsai2 units, no images. Main stack running at :30001 (external, untouched).
- next: Phase 0, item 0.1 (decide repo layout).
- blockers/intricacies: none yet. Note for all sessions: this is a single
  GB10 box — GPU tests must not run parallel with a live main-agent session
  (Global testing discipline). N default is 3 but roll out phased:
  `--instances 1` first (S0).
- spec deltas: none.
