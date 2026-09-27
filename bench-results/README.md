# bench-results/ — live bench run output

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
