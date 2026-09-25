# bin/ — source tree for /opt/bonsai2/bin/ (SPEC S3.2, S1.6, S2.2)

Everything in this directory is deployed by install.sh to
/opt/bonsai2/bin/ on the host (layout MAP 0.1). The repo tree mirrors
the host layout 1:1 so source and deploy paths never diverge in naming.

Landed so far:

- `bonsai2_vram.py` — the S1.4 VRAM accounting formula as pure
  functions (MAP 1.6, S1.4/S1.7 "unit half").
- `bonsai2_model_fetch.py` — weights download + SHA/size verification
  against `etc/subagent-engine.yaml` (MAP 1.3, S1.1/S1.5).
- `bonsai2_render_config.py` — subagent-engine.yaml writer + schema
  validation (MAP 1.5, S1.6/S1.7).
- `bonsai2_build_image.py` — the `bonsai2-server:<tag>` runtime image
  builder + image.pin recorder (MAP 1.4, S1.6/S6.1).
- `bonsai2-manager` — the instance manager (MAP 2.1–2.11, S2.1–S2.5):
  config load, S2.3 boot tier, S1.4 allocation tier, S2.5 ordered
  spawn, S3.1 health.json, S2.2 escalation, S2.4 PTQ1_0/ctx-halve
  fallback, drift watch, S3.2 control queue. Runs from the
  `bonsai2-sglang.service` unit (Type=notify via its own sd_notify).
- `bonsai2_harness.py` — the S3.1 core library for the S3.2 control
  scripts: one-JSON-on-stdout/one-stderr contract, closed-set exit
  codes, S3.6 config loader, health.json read, main-endpoint probe,
  slot accounting + least-loaded routing, queue + task records,
  `reap_stale` (the S3.4 reaper: stale running records + the deadline
  backstop, MAP 3.4), the S3.4 dedup entry API (MAP 3.5), and the
  task/pool commands: collect / steer / cancel / unload / reload.
- `bonsai2-status`, `bonsai2-list-instances`, `bonsai2-health` — the
  S3.2 read family (MAP 3.2): status (S3.1 status schema, live
  main-probe, recomputed slots), list-instances (human table), health
  (raw health.json). Exit 0/82/84 mapping.
- `bonsai2-spawn` + `bonsai2-taskworker.py` — S3.2 spawn command +
  detached worker (MAP 3.3): least-loaded deadline-aware routing,
  queue wait (exit 8), S3.7 result file, S1.5 one-retry engine-down
  path. The worker is not itself an S3.2 command; it is the child the
  spawn command detaches.
- `bonsai2-collect` (MAP 3.6) — read one task's result: task JSON +
  `answer`/`reasoning`/`usage` from the result file. Exit 20/21/22 =
  the task's terminal state (done/failed/timeout); 0 = non-terminal
  (poll); 2 = unknown task.
- `bonsai2-steer` + `bonsai2-cancel` (MAP 3.7) — steer-and-wait
  (`--wait SECS` -> exit 8 on no state change; the steer is advisory in
  v1, recorded on the task) and cancel (SIGTERM the worker's process
  group, SIGKILL after 5 s; the worker's own handler marks the record
  failed/ERR_CANCELLED so the slot frees at signal time, S3.4).
- `bonsai2-unload` + `bonsai2-reload` (MAP 3.8) — pool control,
  manager-mediated through the S3.2 control/ queue: unload stops the
  targeted instance units (instant VRAM release, `bunloaded`,
  zero-running -> main-only `user_unloaded`), reload re-spawns through
  the S1.4 tier + S2.2 start + health re-probe (partial ->
  `"state": "partial"`, exit 0). Dead manager -> exit 84.

Lands here (later MAP items on this phase / later phases):

- `bonsai2-steer`, `bonsai2-cancel` (MAP 3.7), `bonsai2-collect`
  (MAP 3.6) — S3.2 commands; the slot model + deadline enforcement
  (MAP 3.4) and spawn dedup (MAP 3.5) land in the core library, not as
  new commands.
- `bonsai2-bench`, `bonsai2-unload`, `bonsai2-reload` — S3.2 commands
  (MAP 3.8/4.7).
- `llama-server` — NOT in the repo: it is baked into the
  `bonsai2-server:<tag>` image (S1.4/S6.1); a host copy lives at
  /opt/bonsai2/bin/llama-server for ops (S1.2, MAP 1.2).

Tests: hermetic suite lives in `tests/` (run
`python3 -m unittest discover -s tests -t .` from the repo root; no
GPU, no network, stdlib only). @gpu tests live in `tests/gpu/`, run
via `tests/run_gpu.sh`.
