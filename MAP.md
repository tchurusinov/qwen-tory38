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

- fork-vendoring (since 2026-09-20, top-2 open per AGENTS.md uncertainty
  rule): third_party/llama.cpp as pinned git submodule (chosen default)
  vs plain clone + pin recorded in ARTIFACTS.md. Decide at MAP 1.1;
  either way: in-repo, project-wide, never system-wide.
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
- [ ] 0.1 Decide + record repo layout: which source lives where — `install.sh`,
      `bonsai2-manager` (or its source), the S3.2 harness scripts, unit files,
      `bench/` (runner, `score/`, `goldens/`), `tests/`. Host paths are
      fixed by spec: `/opt/bonsai2/{bin,etc,cache}` (S1.6, S2.2, S3.2);
      write the layout decision into this map under 0.1 when done.
- [ ] 0.2 Test runner + conventions: hermetic tests runnable with no GPU/no
      network; `@gpu` label for GPU tests; `nvidia-smi` snapshot into the
      test log before GPU tests (Global testing discipline).
- [ ] 0.3 Verify build toolchain: aarch64 + CUDA 13.0 build container,
      cmake/ninja, driver ≥ 580 — the S1.2/S6.6 matrix on this box.
- [ ] 0.4 Pre-commit enforcement (AGENTS.md "Code quality"):
      `.githooks/pre-commit` (bash -n + shellcheck on .sh, ruff on .py,
      secret scan on staged diff, whitespace/CRLF/size checks, then full
      `./scripts/gate.sh`) + `scripts/install-hooks.sh` (sets
      `core.hooksPath=.githooks`, idempotent); install it in this clone
      and verify with a deliberate dirty-file commit (must block) + a
      clean commit (must pass).

### Phase 1 — Subagent inference engine + image (SPEC S1)

Exit: one instance runs by hand from the pinned image and passes S1.7
integration. S1.8 #2–#5 complete at end of Phase 6/7 (they need N ≥ 1
under the manager or the full stack).

- [ ] 1.1 Pin the fork commit: `PrismML-Eng/llama.cpp` branch `prism`,
      pick SHA; record `engine_pin: {repo, commit}` (S1.2). Merge
      status checked 2026-09-20 (Q1_0 merged upstream; generic ternary
      Q2_0/g64 on mainline; **PQ2_0/PTQ1_0 packing still needs the
      fork** — re-verify at first build: attempt a stock-build load of
      PQ2_0, expect refusal). Also decide fork-vendoring (Open items:
      submodule vs clone+pin). Version policy: bump only via a PR with
      S5 quick bench (S1.2, S5.5).
- [ ] 1.2 Build pipeline: source build aarch64, S1.2 cmake flags, arch
      `121a`, ternary kernel toggles; assert fork ternary banner in
      `llama-server --version` (S1.2, S1.8 #1).
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
