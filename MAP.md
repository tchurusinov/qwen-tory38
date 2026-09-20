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

- PQ2_0 weights not on the box yet (since 2026-09-20): local copy is
  PTQ1_0 (fallback quant, 5.95 GB) + mmproj only. SPEC default PQ2_0
  (7.21 GB) downloads at install (MAP 1.3) — not a blocker, note for
  VRAM audits and first-run expectations.
- shellcheck + ruff not installed (since 2026-09-20): gate.sh +
  pre-commit treat both as soft until installed (install in Phase 0,
  provenance-checked, aarch64-safe).

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
- [ ] 1.3 Model download + SHA256 verification: `PQ2_0` (default) +
      `PTQ1_0` (fallback); retry ×3 / 10 s backoff; `ERR_HASH_MISMATCH`
      exit 4 path (S1.1, S1.5).
- [ ] 1.4 Build `bonsai2-server:<tag>` runtime image: binary + model files
      + CUDA runtime; record docker content digest as `image_pin`
      (S1.6, S6.1).
- [ ] 1.5 `subagent-engine.yaml` writer + YAML schema validation: port
      math, ctx/parallel bounds, sampling ranges (S1.6, S1.7 unit half).
- [ ] 1.6 VRAM formula as a pure function: `instance_footprint_GB`,
      `N = floor(budget/11.5)` clamped [0,8], N=0 and N>8 cases (S1.4,
      S1.7 unit half).
- [ ] 1.7 Manual single-instance bring-up: `docker run` per S1.3
      (verbatim flags, `--port 30101`), host networking, memory cap;
      `/v1/models` returns `qwen38-27b-subagent`; 16-token completion
      round-trip < 10 s; `nvidia-smi` delta matches S1.4 formula ±10 %
      (S1.7 integration).
- [ ] 1.8 Sustained load: 8 concurrent completions (max_num_seqs=8) for
      120 s → t/s ≥ 26.2 (0.9 × 29.16 baseline), zero 5xx (S1.7, S1.8 #3).
- [ ] 1.9 OOM-ceiling load: add instances until the S1.4 allocation tier
      fails → last failure is `ERR_VRAM_OOM`, not a segfault; prior
      instances stay healthy (S1.7).
- [ ] 1.10 Port collision: dummy listener on 30101 → instance 0
      `ERR_PORT_IN_USE` (exit 3) skipped, instance 1 up (S1.5, S1.7 edge).
- [ ] 1.11 Kill -9 of the instance process → auto-restart < 15 s
      (docker-level, pre-manager) (S1.8 #4).

### Phase 2 — Manager + systemd lifecycle (SPEC S2)

Exit: S2.8 acceptance (all five, runnable on this box).

- [ ] 2.1 `bonsai2-manager` skeleton: Type=notify via sd_notify; reads
      `subagent-engine.yaml` + `serving.yaml`; writes
      `/run/bonsai2/health.json` in the S3.1 schema (S2.2).
- [ ] 2.2 Boot-time budget check (S2.3): `free_gb` from the main
      container's live cgroup `memory.current` (sleep-accurate) else the
      recorded fresh-load `footprint_gb`; `N' = max(0, min(N, floor(...)))`;
      `WARN_VRAM_SHORTFALL` on 0 < N' < N.
- [ ] 2.3 Allocation tier (S1.4, manager-run): stricter pre-spawn gate
      `free ≥ instance_footprint_GB + 1.0`; probe timeout 5 s + one
      retry, then treat free as 0; i > 0 → partial load exit 0; i = 0
      with granted N' ≥ 1 → exit 4 `ERR_VRAM_OOM`; N' = 0 → S2.4 handoff.
- [ ] 2.4 Unit files: `bonsai2-sglang.service` + `bonsai2-server@i.service`
      verbatim main-stack shape — `docker run` flags per S2.2,
      `ExecStartPre= docker rm -f`, `Restart=on-failure`, `RestartSec=5`,
      `StartLimitBurst=3` → `bfailed`, `CPUQuota=64 %`; `Wants=` (not
      `Requires=`) on the main stack (S2.1, S2.2).
- [ ] 2.5 Spawn path: manager starts granted instances in order via
      `systemctl start bonsai2-server@i`; waits each to healthy before the
      next; boot order: main stack first (S2.1).
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

<!-- Newest entry on top. One entry per implementation session, appended by
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
