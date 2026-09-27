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
