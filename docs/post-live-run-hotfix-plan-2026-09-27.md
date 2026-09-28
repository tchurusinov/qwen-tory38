# qwen-tory38 — Post-Live-Run Hotfix: Action Plan

Date: 2026-09-27. Author: senior architect.
Status: PLAN (approved decisions recorded in §0; execution follows §11's DOD).
Inputs: `docs/post-live-run-state-report-2026-09-27.md` (the analysis) + the
post-report discussion with the admin (2026-09-27, decisions logged in §0).
Base: `main @ f7c768d` (post-PR #21). Branch: `hotfix-post-live-run`.

Goal: one cumulative hotfix PR that makes the NEXT live M1–M10 run clean —
M6/M8 measurable, M1/M3/M5 scorable as intended, M7's metric sound,
M9/M10 reaching terminal without aborting the run, and the run leaving no
leaked workdirs or stranded VRAM — plus the mechanism that later becomes the
main agent's own release lever (H13, two tiers).

---

## 0. Decision log (post-report discussion, 2026-09-27 — canonical)

1. **M1/M2 reasoning seam = Option A only.** The task prompt forces a
   content-only fenced answer; the rubric stays strict; no config flag /
   Option B. (Admin: "1a".)
2. **M7 metric.** Wall-clock t/s is the headline "ceiling" figure; the
   serial-sum figure is also recorded. The S5.5 baseline row carries
   `{n_tasks, concurrency, N}` and the re-bench gate compares like-with-like
   (mismatch → "not comparable" flag, not a silent compare). The engine's
   `--parallel 8` cap is constant; the load the bench applies varies per M
   (M2 = 8-wide, M7 = 16, M8 = 8, M1/M4/M5 sequential) — so comparability is
   a recorded property, not an assumption.
3. **"Release" has two meanings (admin question: "release model+kv or only
   kv? is this technically possible?")** — verified against the pinned fork
   (`9a9394a8`), see H13 §1: **yes**. Tier 1 = KV only (idle slots free
   their per-slot KV; weights stay resident, re-prefill is fast). Tier 2 =
   model + KV (`POST /models/unload` exists on the pin; reload re-loads
   weights, 8–30 s). Both are given to the main model via the skill once it
   exists (Phase 5); the bench exposes them now as `--release-after` /
   `--release-between` (tier 2) for the proof-of-concept run.
4. **Release between every M: yes** — the bench releases the pool at every
   scenario boundary (PoC for the stateless doctrine: "unload between each M
   is a strong proof of concept for the real thing" — admin). Each M then
   starts from the same clean pool, so per-M footprints and M7 saturation
   timing are comparable, and the admin's "why did VRAM climb?" question
   gets an empirical answer (intra-M growth vs between-M accumulation —
   H12's snapshots are the instrumentation).
5. **M3 spot-check: driven live** (two-step operator procedure, see §7).
6. **Quant runs: one run per quant after the hotfix PR** — formal PQ2_0
   full M1–M10 first, then a PTQ1_0 comparison run (footprint + M1/M2/M7),
   feeding the S2.4 fallback / N decision with measured data.
7. **Q6 (main t/s 30→15 under load):** precursor handled by the H13
   mechanism now; the doctrine rule ("delegate grunt work, keep critical
   thinking on main") lands in Phase 5 with the measured numbers.

---

## 1. H1 — capability driver wait window covers the task deadline (P0, the run-stopper)

- **Why:** `bench/capability.py:312` `run_mode(wait_s=120.0)` gives up at
  120 s while the task's `--deadline` is 300 s (M9/M10 default) or 600 s
  (launch). Live M9 crash: `not terminal within 120.0s (last state:
  running)` — the taskworker was still running.
- **What:** the driver's terminal-poll window = task deadline + 90 s poll
  margin (60 s tool-timeout bound + a 30 s slack so the window EXCEEDS
  the in-flight-step bound — G3c round-2 P1 spec-follows-code: the plan
  originally sketched "deadline + 60 s"; the implemented value is
  `deadline_s + TOOL_TIMEOUT_S + 30.0`).
- **Where:** `bench/capability.py:312` (signature), the wait loop at
  `:453-465`; call site `bench/run.py:715` already passes `deadline_s`, so
  it flows through.
- **How:** `wait_s: float | None = None`; inside: `wait_s = wait_s if wait_s
  is not None else deadline_s + TOOL_TIMEOUT_S + 30.0` (the implemented
  90 s = 60 s tool bound + 30 s slack, G3c round-2 P1). Remove the 120.0
  literal. Hermetic
  test: a mock capability task that is not terminal by 120 s but is by
  deadline+margin passes the row; update `tests/test_harness_tools_loop.py`
  + `tests/test_bench_runner.py` for the new default.
- **Spec:** S5.2/S5.9 #5 — the capability row's driver wait = the S5.2
  ctx-budget deadline + poll margin.

## 2. H2 — a capability-row failure is a ROW verdict, not a run crash (P1)

- **Why:** today `CapabilityError` propagates out of the `for sid` loop in
  `bench/run.py:464` to the top-level catch → `{"status": "crash"}` → M8,
  M9/M10 all dropped. Text-only rows already treat scenario-level fail as a
  row verdict (S5.4 "failure is data"); the capability path does not.
- **Where:** `bench/run.py:_run_capability` (635-681), the per-mode loop.
- **How:** in the `for mode in modes` loop catch `CapabilityError` and build
  a degraded `_capability_mode_row` (`row_verdict: "fail"`, `detail` = error
  + last task state, `score: 0.0`); the host-vs-engine comparison records
  the crashed mode honestly; the run continues to M8 and the remaining
  modes and writes all rows + the summary. Hermetic test: a `mock_cap` that
  raises for one mode → two-row comparison with the crashed mode `fail`,
  run still writes all rows + summary.
- **Spec:** S5.4 — "exit 0 even on scenario-level failures" extends to the
  capability path explicitly.

## 3. H3 — main endpoint `api_key` hook (P1, unblocks M6 + M8)

- **Why:** `bench/endpoints.py:106` hardcodes `api_key: ""` for main; the
  live main stack is key-walled (no-key completions → 401, confirmed). M6 →
  0.0, M8 → no baseline. The "401-walled is still UP" convention is for the
  HEALTH PROBE only; completions need the key.
- **Where:** `bench/endpoints.py:89-108` (`main_endpoint` reads
  `harness.get("main_api_key", "")` into the endpoint dict's `api_key`);
  `etc/harness.yaml` (add `main_api_key:` with comment; default empty →
  keyless mains keep working); `etc/bench.yaml` mirror; `bench/config.py`
  schema. The executor already sends `Bearer` when the key is non-empty
  (`bench/exec.py:234`) — no executor change.
- **How:** hermetic test: a key-walled main (mock: 401 without key, 200
  with) → M6 measures a positive `main_tps`; M8's guardrail baseline valid.
- **Spec:** S3.6 — the `harness.yaml` main-endpoint contract gains
  `main_api_key` (optional; empty = keyless).

## 4. H4 — M1 prompt forces a content-only answer (P1; decision §0.1 = A only)

- **Why:** the pin's deepseek reasoning format put the entire M1 answer in
  `reasoning_content` (1 content token each task); the rubric compiled an
  empty artifact → 0/3.
- **Where:** `bench/run.py:100-108` (M1 prompt: add "Your final answer must
  be EXACTLY the two ```python fences — no prose, no reasoning text outside
  the fences."), `bench/run.py:379-381` (payload builder keeps the
  prefer-content-then-reasoning fallback as a last resort), `bench/scenarios
  .py` M1 shape note.
- **How:** hermetic test: a reasoning-only M1 payload still fails the strict
  rubric when content is empty (documents A's behavior); a content-carried
  fence pair passes (existing golden). No flag, no Option B.
- **Spec:** S5.2 M1 — at the pinned engine the model's answer lands in
  `reasoning_content`; the M1 prompt forces content-only; the rubric measures
  the content artifact.

## 5. H5 — M3 spot-check seam: record "unjudged", never silent 0.0 (P1)

- **Why:** without `--spotcheck`, M3 fails at the 2/3 threshold by
  construction (live: blend 0.70) and the row does not say the seam was not
  driven.
- **Where:** `bench/score/m3.py:79-98` (distinguish `spot is None` →
  `spotcheck: "unjudged"` + named detail `M3: spot-check seam not driven
  (supply --spotcheck 0..1)` from `spot == 0.0` → judged zero; the numeric
  blend still computes at 0.0 weight for the unjudged case),
  `bench/run.py:404` / `:438-442` plumbing unchanged.
- **How:** hermetic test: `spotcheck=None` → detail contains "not driven",
  `score == auto * 0.7`; `spotcheck=0.0` → detail says judged zero.
- **Spec:** S5.3 M3 — the spot-check is a live main-agent input; an undriven
  seam is recorded `unjudged`.

## 6. H6 — M9/M10 workdir leak (P2, confirmed live)

- **Where:** `bench/capability.py:367` (workdir), `:484-487` (finally).
- **How:** `shutil.rmtree(workdir, ignore_errors=True)` in the same
  `finally` after `_tmp.cleanup()`. Hermetic test: after `run_mode` the
  staged workdir path no longer exists.
- **Spec:** none (the S5.2 workdir-contract docstring becomes true).

## 7. H7 — M9 tool_log scorer: no early-return on a non-zero match (P2)

- **Why:** `bench/score/m9.py:111-138` returns `False` on the FIRST
  suite-mentioning entry with non-zero `exit_code` instead of continuing;
  failed-then-successful runs are mis-scored "no successful run".
- **How:** `continue` on non-zero match; return `False` only after the loop.
  Hermetic test: `[failed-run, successful-run]` tool_log passes the
  sub-condition.
- **Spec:** none (S5.9 #5 "tool_log shows the test run with exit 0" = "there
  EXISTS a successful run").

## 8. H8 — M5 scorer: tolerate conjunctions / word boundaries (P2)

- **Why:** live miss: golden "M1, M2, M7" vs answer "M1, M2, and M7";
  `bench/score/m5.py:59` containment does not strip the conjunction.
- **How:** drop stop tokens (`and`, `or`, `the`, `a`, `an`) in
  `_normalize`; `_match` passes when ordered containment holds OR every
  golden token is present in the answer's token set (strict containment
  remains the special case). Hermetic tests: "M1, M2, M7" vs "M1, M2, and
  M7" → pass; "M1, M2" vs "M1, M2, M7" → fail.
- **Spec:** S5.3 M5 — the matcher is tolerant of conjunctions/word-boundary
  phrasing, strict on missing facts.

## 9. H9 — M7 metric: wall-clock headline + recorded comparability (P2; §0.2)

- **Why:** `bench/exec.py:353` `aggregate_tps = total_tokens /
  sum(per-task-elapsed)` = 370 / 1484 s serial = 0.25; the batch wall was
  ~161 s. The S5.5 baseline is built on this.
- **What:** the aggregate records BOTH: `aggregate_tps_wall` (total tokens /
  batch wall-clock — the "ceiling" figure, becomes the S5.5 baseline) and
  `aggregate_tps_serial` (the decode-throughput invariant). `aggregate_tps`
  stays as the wall alias for backward compatibility.
- **Where:** `bench/exec.py:322-364` (`run_concurrent` measures the batch
  wall around the pool block; `total_wall_s` + both tps values in `agg`),
  `bench/score/m7.py` (rubric reads the wall value; records serial in
  `extra`), `bench/rebench.py` (gate reads wall).
- **Comparability (decision §0.2):** the M7 row and the S5.5 baseline carry
  `{n_tasks, concurrency, N}`. The gate: if any of the three differs from
  the baseline → status `not_comparable` (named, not silent; exit code
  documented in the gate section), otherwise the >10% t/s rule applies.
  Hermetic test: two 2-task concurrent runs → wall tps computed from batch
  wall; a baseline with different `N` → `not_comparable`.
- **Spec:** S5.1/S5.5 — "t/s at the ceiling" = total tokens / batch
  wall-clock at the scenario's concurrency; the serial sum is recorded as
  `aggregate_tps_serial`; the baseline carries `{n_tasks, concurrency, N}`
  and like-with-like is enforced. Before/after: live M7 figure 0.25 (serial)
  → ~2.3 (wall) on this run.

## 10. H10 — rebench `_config_value`: don't swallow a malformed `--config` (P2)

- **Why:** `bench/rebench.py:394-409` falls back to defaults on a bad
  `--config`, hiding the documented exit-2 "bad input" path.
- **How:** "file absent" → default (as today); "file present but
  unparseable" → exit-2 bad input. Hermetic test: malformed `--config` →
  exit 2.
- **Spec:** S5.7/S5.5 — a present-but-malformed bench.yaml is a named
  bad-input error.

## 11. H11 — bench pre-install usability (P2, docs + WARN clarity)

- **Why:** default `results_dir: /run/bonsai2/bench` assumes the installed
  layout; pre-install runs needed `--results-dir` (pre-run item 1).
- **How:** keep the default (correct post-install); make the "does not exist
  yet" WARN name the flag; document the pre-install convention (`BONSAI2_
  CONFIG` + `BONSAI2_ENGINE_YAML` + `--results-dir`) in
  `bench-results/README.md` as THE path until Phase 6 lands.
- **Spec:** S5.2/S5.7 — the pre-install live-run convention is documented.

## 12. H12 — box-state snapshot: reclaimable memory + per-scenario snapshots (P2)

- **Why:** the live run's VRAM climbed 100 → 116 and cache sat at ~99%; the
  current snapshot (`bench/boxstate.py`) records only `MemTotal` /
  `MemAvailable` — not the reclaimable pool, and not a trend. This is the
  instrumentation that answers "what caused the climb?" (§0.4) and lets a
  reader distinguish resident from reclaimable memory.
- **How:** extend `meminfo()` (`bench/boxstate.py:104-110`) to `MemTotal`,
  `MemAvailable`, `MemFree`, `Cached`, `Shmem`, `SReclaimable`. The runner
  already snapshots per row (so each M row is one sample of the trend —
  committed rows are the time series); add ONE extra post-run snapshot to
  the run JSON / summary so end-of-run state is explicit. Hermetic test:
  the snapshot dict carries the new fields.
- **Spec:** S5.2 — the box-state snapshot carries the reclaimable-memory
  fields; the committed per-row snapshots form the run's VRAM time series.

## 13. H13 — pool release mechanism: two tiers, bench flags, main-agent-ready (P1; §0.3-§0.4, §0.7)

### 13.1 The two tiers — what they mean, verified at the pin

The admin's question: "can release mean release model+KV, or only KV? is
this technically possible?" — **yes, both exist on the pinned fork
(`9a9394a8`)**, verified in the fork source this session:

- **Tier 2 (model + KV):** `POST /models/unload` — the fork's model
  router exposes load/unload (`third_party/llama.cpp/tools/server/server
  .cpp:228-229` → `server-models.cpp:2039 post_router_models_unload`;
  handler: body `{"model": <name>}`, `models.unload(...)`, success when the
  model was running). Unloads the weights (PQ2_0 ~7.21 GB) AND the KV;
  reload re-loads everything.
- **Tier 1 (KV only):** the slots/KV layer — slots are the engine's
  per-request contexts (8 per instance, `--parallel 8`); each slot's KV
  cache is per-task, and with the default `cache_prompt=false` the KV is
  discarded when the task ends (`server-context.cpp:3101` gates cache
  reuse on `cache_prompt`). No explicit HTTP endpoint clears an idle
  slot's KV at this pin — Tier 1 is achieved by the slot lifecycle itself
  (task done → slot idle → KV gone) plus the `cache_prompt` request flag
  for intra-session reuse.
- **What the live-run climb actually was (the mechanism):** between-M, the
  engine's CUDA allocator retains already-allocated KV/device buffers for
  reuse (released only when the process unloads — that is the tier-2
  boundary), plus host page cache (the ~99% buff/cache — reclaimable, not
  resident), plus transient host-tool subprocesses during M9. Within one M,
  growth is allocator retention across concurrent slots. **The release
  between M's is therefore the tier-2 boundary**, and H12's snapshots
  measure which of the three components each contributes.

### 13.2 Giving it to the main model (Phase 5 shape, designed now)

Once the skill exists, the main agent's S3.2 surface gains two documented
capabilities (the skill is the interface; the mechanism is what this PR
builds):

- **"release pool" (tier 2):** `bonsai2-unload --all` → manager SIGTERMs the
  instance units (instant VRAM release, the S3.2 closed set) → stack enters
  main-only mode. The main agent's t/s returns to baseline; re-delegation
  triggers `bonsai2-reload` (boot ~8-15 s warm, 21.5 s observed under
  contention; the S2.8 reload budget is 30 s post-amendment).
- **"keep warm, KV only" (tier 1):** no unload — the instance stays up,
  weights resident, and idle slots carry no KV by construction (default
  `cache_prompt=false`). The main agent picks this when it expects to
  re-delegate to the same instance within the KV-reuse window; it costs
  nothing to ask for (it is the engine's default), which is why the skill
  documents it as a *decision* (what to expect from VRAM), not a command.
- The skill's stateless branch (default) = tier 2 between delegations; the
  stateful branch = tier 1 (instances stay loaded, KV per-task). The main
  model decides tier and timing against the doctrine's cost constants
  (reload 8-30 s vs the held-VRAM ~2×14.08 GB at N=2) — the doctrine
  (Phase 5) teaches the trade; this PR makes the trade measurable and
  exercised.

### 13.3 Bench flags (this PR)

- `--release-after` (default OFF): after the run writes all rows + the
  summary, shell out to `bin/bonsai2-unload --all` (manager-mediated; the
  bench does not touch docker directly — S3.2 surface only). Records
  `release_after: {unloaded, exit}` in the run JSON.
- `--release-between` (default OFF): at each scenario boundary, `bonsai2-
  unload --all` → `bonsai2-reload` (the S3.2 pair; wait budget = the S2.8
  30 s reload budget, polled on health) → a 16-token warmup completion per
  reloaded instance (discarded, same pattern as the MAP 1.7 bring-up check —
  prevents cold-prefill poisoning M7's timing) → next scenario. Records a
  LIST of per-boundary records in the run JSON — one entry per boundary,
  each `{kind: "between", precedes: <next sid>, follows: <prev sid>,
  unload_exit, reload_exit, reload_s, warmup_s, ...}` (G3c round-8
  spec-follows-code amendment: this sketched `{scenario, ...}`; the
  implemented shape uses `precedes`/`follows` because a boundary sits
  BETWEEN two scenarios, so a single `scenario` key would be ambiguous —
  the SPEC S4.3 / S3.5 stdout schema carry the implemented shape).
- Honest failure path: unload/reload are manager-mediated; if the manager is
  down (S3.2 exit 84) the flag logs `release: skipped (84)` in the row and
  the run CONTINUES (release is an optimization, not a correctness gate).
  Under `--mock` both flags are no-ops (recorded as such).
- **Re-run runbook (post-hotfix, §7):** hand-start the manager first
  (pre-prod step, not code) so the mechanism is genuinely exercised; run
  with BOTH flags ON.
- Hermetic tests: with `--mock`, flags are recorded as no-ops; a mocked
  unload-84 → run continues + `skipped (84)` recorded; `--release-between`
  ordering (rows written for scenario N before the boundary release; scenario
  N+1 rows after).
- **Spec:** S4.2/S4.3 — the stateless branch releases (tier 2) after the
  task; tier 1 (KV-only, instances warm) is the stateful branch's shape; the
  bench exposes `--release-after` / `--release-between`. Marked borderline:
  the DOCTRINE half is Phase 5; this item is the MECHANISM + the PoC data.

## 14. H14 — the post-hotfix live re-run runbook (the 4.10 clean half)

Not code in the hotfix PR — the `@gpu` step it enables. Recorded here so the
run is reproducible:

1. VRAM audit (AGENTS.md rule): main asleep cgroup + 2 instances × 14.78 +
   1.0 margin; commit/stash uncommitted work first.
2. Hand-start the manager (`units/bonsai2-sglang.service` + manager, pre-
   prod shape) so H13's flags are manager-mediated, not skipped.
3. Launch: `--release-between --release-after`, PQ2_0, `--deadline-s 600`,
   prod venv, `BONSAI2_CONFIG`/`BONSAI2_ENGINE_YAML`/`--results-dir` per the
   pre-install convention (H11).
4. **M3, two steps (decision §0.5):** run the bench; M3's row carries the 6
   generated sections. The main agent reads 1/3 of the sections against the
   source doc, judges claim accuracy (0..1), then the M3 row is re-run via
   `--scenario M3 --spotcheck <value>` — the existing single-scenario path,
   no new code. The re-run row supersedes the first M3 row in
   `bench-results/`.
5. Commit `bench-results/` (M6, M1-M10, M9/M10 ×2 modes + host-vs-engine
   comparison + the summary + per-row `box_state` time series + the
   `release_between` records).
6. **PTQ1_0 comparison run (decision §0.6):** footprint check at N=2, then
   M1/M2/M7 only (the quick bench set) — enough for the S2.4 fallback / N
   decision.
7. S5.5 baseline: `bench/rebench.py save-baseline` from the PQ2_0 quick
   rows (wall tps + `{n_tasks, concurrency, N}` per H9) →
   `bench-results/rebench-baseline.json`.

## 15. Out of the hotfix PR's DOD (their own PRs)

- Phase 5 (the doctrine skill + the two workflow branches; consumes H13's
  PoC data and §14's numbers).
- Phase 6 (`install.sh`; H11 documents the pre-install path until then).
- Phase 7 (soak, CI/CD, closeout).
- The live re-run itself (§14) — its own `@gpu` step after merge.

---

## PR-body contract (for the hotfix PR that executes §1-§13)

- **`## Definition of Done`:** every item H1-H13 has a named hermetic test
  that passes; H1+H2 proven by a `--mock-cap` run where a slow / raising
  capability task no longer aborts the run and all rows + the summary land;
  the full hermetic suite green; `./scripts/gate.sh` PASS; mock `--quick`
  M1+M2+M7 run writes rows + summary; H13 flags recorded as no-ops under
  `--mock`. NOT claimed: a live M1-M10 re-run (§14 is the follow-up `@gpu`
  step) and the PTQ1_0 comparison.
- **`## Spec amendments`:** S3.6 (`main_api_key`), S5.2 M1 (reasoning/content
  seam), S5.3 M3 ("unjudged"), S5.1/S5.5 (M7 wall metric + baseline
  comparability fields), S5.4 (capability path = row verdicts), S5.2/S5.9 #5
  (driver wait = deadline + margin), S5.7 (malformed bench.yaml = exit 2),
  S5.2 (snapshot fields + per-row time series), S4.2/S4.3 (release tiers +
  bench flags). Each with before/after where a value or default moves.
- **`## Uncertainties & decisions`:** the two forks, fixed 5-subsection
  shape: (a) H13 tier choice for the re-run (both flags on = tier 2 at every
  boundary — taken; the alternative "tier 1 between, tier 2 only at run
  end" is what the H12 data will arbitrate next time); (b) H4 content-only
  prompt vs rubric tolerance (A taken, per admin §0.1; B recorded as the
  fallback if the prompt under-delivers).
- **Checks + residual risk:** exact hermetic count + gate result; the
  `@gpu` steps NOT in this PR (§14 + the PTQ1_0 comparison); residual:
  Phase-5/6/7 still unbuilt; H13 exercised live only at §14.
