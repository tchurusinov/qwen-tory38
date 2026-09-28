# bench-results/

Committed bench summary artifacts (S5.2/S5.7). Per-row raw JSON lives under
the install layout's `results_dir` (7-day rotation, 1 GB cap); this directory
carries the committed, readable summaries + the S5.5 rebench baseline.

## Pre-install live-run convention (until Phase 6's install.sh lands)

Before install.sh exists, the bench has to point at its inputs explicitly,
because the defaults assume the installed layout (`/run/bonsai2/bench`,
`/opt/bonsai2/etc/harness.yaml`). A pre-install live run is:

- `BONSAI2_CONFIG=<path to harness.yaml>` (the S3.2 hook; default is
  `etc/harness.yaml` in the repo)
- `BONSAI2_ENGINE_YAML=<path to subagent-engine.yaml>` (the engine identity
  the runner reads for the pool ports + the quant/revision it records)
- `--results-dir <writable dir>` (overrides the template's
  `results_dir: /run/bonsai2/bench`; the runner creates it at run start)

The default `results_dir` stays the installed path. Running pre-install
without `--results-dir` is still fine (the dir is created at run time), but
the config loader emits a WARN that names the flag: `results_dir: ... does
not exist yet (will be created at run time) (override with --results-dir;
see the pre-install convention in bench-results/README.md)`.

A pre-install run of the full bench looks like:

```bash
BONSAI2_CONFIG=$PWD/etc/harness.yaml \
BONSAI2_ENGINE_YAML=$PWD/etc/subagent-engine.yaml \
python3 -m bench.run --results-dir /tmp/bonsai2-bench-preinstall
```

(The prod venv is the box Python; `python3 -m bench.run` from the repo root
is equivalent to `bin/bonsai2-bench` minus the install-time path fixes.)

The `bench/run.py` results dir (the S5.2 row JSONs + the summary .md the
runner writes). Git-ignored by default (local, rotated: 7-day prune +
1 GB cap, S5.2/S3.5); **this run's files are force-tracked** (`git add -f`)
as the 4.10 pre-production record — the repo is localhosted and the rows
are the evidence for PR #21 (the S5.9 #1 "committed bench-results" item).

## 20260927 live full M1-M10 run (PR #21, pre-production)

- Date: 2026-09-27 16:37-16:53 EEST (~16 min to the M9 crash).
- Launch: 2 instances (`--tools` 7-tool S1.6 set, 14.78g pool cap, PQ2_0,
  q4_0 KV, ctx 262144, ports 30101/30102) — the user-directed "launch with
  tools" pre-prod run (the way subagents will be used). Runner: prod venv
  python, `--results-dir` override, `--deadline-s 600`.
- Outcome: M6/M1/M2/M3/M4/M5/M7 rows written; run crashed at M9/host
  (`CapabilityError: not terminal within 120.0s` — the driver's
  `wait_s=120` poll window vs the task's 600 s deadline). M8/M9/M10 rows
  not written (the crash aborts the run before `write_summary`, so no
  summary .md either).
- Rows: M6 fail (main 401 wall, no M8 baseline) · M1 fail (all output in
  reasoning_content, 1 content token) · M2 pass · M3 fail (spot-check
  seam unsupplied) · M4 pass · M5 fail 4/5 · M7 pass (aggregate 0.25 t/s,
  zero 5xx).
- Full observations + hurdles: PR #21 comment 1038.
- `runner-stdout.log` = the runner's stdout/stderr for the (crashing) run.

## 20260928 live full M1-M10 run (post-hotfix, PR #23, MAP 4.10 clean half)

- Date: 2026-09-28 14:54–16:13 EEST. Launch: 2 instances, `--tools` 7-tool
  S1.6 set, PQ2_0, q4_0 KV, ctx 262144, ports 30101/30102 — user-scope
  systemd manager (steady loop) + user-scope units, scratch layout under
  `/tmp/bonsai2-live-run/`, rendered `harness.yaml` with `main_api_key`
  (H3). Runner: prod venv, `--results-dir` override, `--deadline-s 600`,
  `--release-between --release-after` (H13, manager-mediated).
- Outcome: ALL 10 scenario rows written (the pre-prod run stopped at M9's
  crash) + summary + the M3 spot-check re-run row. Verdicts: M2/M4/M5/M7
  pass; M3 pass (spot-check re-run, 0.88); M6/M1/M8/M9/M10 fail.
  Per-hotfix outcomes: H3 ✓ (M6 main_tps 8.52, no 401s) · H4 ✗ (M1 still
  reasoning-only: 1 content token, ~4.5k reasoning chars) · H5 ✓ (M3
  unjudged row named, judged re-run supersedes) · H7/H8 ✓ (M5 now 5/5) ·
  H9 ✓ (M7 headline = wall 2.08 t/s + serial 0.215 + comparability
  {16,16,2}) · H1/H2 ✓ (M9 no crash; capability failures = row verdicts,
  run reached M10/M8) · H13 ~ (flags exercised, but `bin/bonsai2-unload`
  / `-reload` are mode 100644 in git → `skipped (unload not launchable:
  PermissionError)` at every boundary — design degradation path, run
  continued; pool NOT actually released between scenarios).
- New live findings (follow-up PRs, per the PR-#23 DOD "NOT claimed"):
  1. M8 main-side measurement 0.0 t/s (guardrail "breach"): the main's
     one-sentence summary went to reasoning_content (same class as M1;
     decode_tps None → 0.0). M8 is a measurement-seam failure, not a
     contention result — the subagent half ran fine (decode 3.5–7.6 t/s,
     8/8 ok) at MemAvailable 2.55 GB (no OOM).
  2. M9 both modes 2/3: the failing sub-condition is "workdir module
     textkit.py not found" — H6's `finally: rmtree(workdir)` in
     `bench/capability.py run_mode` runs BEFORE the scorer's file check.
     The subagent DID write textkit.py + test_textkit.py and the suite
     ran exit 0 (tool_log: "Ran 3 tests in 0.000s OK") — M9's 0.67 is a
     scorer-ordering artifact, not a subagent failure.
  3. M10 engine mode: final answer EMPTY (len 0, state done) → coverage
     0/10; host mode: report parsed + 10/10 covered but re-run 0/2 agree
     (`claimed: null` — the subagent's per-file status entries don't
     match the scorer's `_VALID_STATUS` shape).
  4. Thin S3.2 scripts `bin/bonsai2-{unload,reload,cancel,collect,steer}`
     are 100644 in git (no exec bit) → pre-install direct-path launches
     hit PermissionError (the install-time chmod never ran).
- Rows: the 20260928T*.json set in this dir (12 full-run rows + the M3
  spot-check re-run row `20260928T132112480024Z_M3.json` which supersedes
  the first M3 row, §14 step 4). `rebench-baseline.json` = the S5.5
  baseline from this run's quick rows (M7 wall 2.0794 t/s @
  {n_tasks:16, concurrency:16, N:2}). `runner-stdout-20260928-full.log`
  + `manager-log-20260928.txt` = the run's runner/manager records;
  `runner-stdout-20260928-m3-spot.log` = the M3 re-run.
- Summary: `2026-09-28_b2cbe98.md` (the M3 row in it is the JUDGED
  re-run row; the runner's own write carried the unjudged one until the
  post-run re-summary).
