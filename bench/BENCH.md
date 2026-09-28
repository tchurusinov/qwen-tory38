# bench/ — how to run the M1–M10 bench on this box (pre-install convention)

Read this BEFORE touching anything. The first full live run cost ~40 min of
investigation to launch because none of this was written down. Everything
below is what a run actually needs on THIS box, verified live
(2026-09-27 pre-prod run + 2026-09-28 post-hotfix full run, branch
`phase4-m1-m10-live-rerun`, PR #23).

## What this bench is

The S5 scenario matrix (M1–M10) measuring where the Bonsai subagents
actually win vs the main agent. M6 is the main-only control (runs first),
M8 is the main-t/s guardrail vs M6 (runs last), M9/M10 are the capability
layer (real harness tool loop, BOTH `tool_execution` modes — host and
engine — in one row each, plus a host-vs-engine comparison). Runner:
`bench/run.py`, thin wrapper `bin/bonsai2-bench` (exit codes 0/2; 0 =
completed even with scenario-level failures, 2 = bad config / bench lock
held).

## The one thing that surprises every run: the install layout does not exist

Phase 6 (`install.sh`) is not on this box. Concretely:

- No `/opt/bonsai2/{bin,etc}` — only `/opt/bonsai2/cache/` (the model
  weights, already SHA-verified).
- No `/run/bonsai2` — and `/run` is a root-owned tmpfs, so it cannot be
  `mkdir`'d as the user (no passwordless sudo on this box).
- No bonsai2 systemd units installed system-wide.

Therefore every live run needs:
1. Explicit env overrides (below) so the runner + harness scripts find
   their configs.
2. A user-scope systemd manager (below) so the H13 pool-release flags
   (`--release-between` / `--release-after`) are genuinely
   manager-mediated instead of `skipped (84)`.
3. Writable scratch dirs under `/tmp` (or home) for health/results/dedup/
   workspaces.

## Step 0 — VRAM audit (AGENTS.md rule, before ANY GPU load)

```bash
nvidia-smi --query-gpu=memory.used,memory.total --format=csv,noheader
free -h | sed -n '1,2p'
docker stats --no-stream --format 'table {{.Name}}\t{{.MemUsage}}'
```

Decision: free pool = 121.6 GiB − main-stack live usage (the main
container may be asleep via sleep_on_idle — read its cgroup, don't assume
76.4) − other containers − 8.0 reserved. Each instance needs
`footprint + 1.0 GB` margin (the S1.4 allocation tier: 14.08 + 1.0 =
15.08 GB for PQ2_0). No uncommitted changes on the box before a run.

## Step 1 — the subagent instances (user-scope systemd, 2 instances)

Pre-install the manager cannot manage system-scope units (no sudo). The
@gpu tests establish the convention: user-scope units +
`BONSAI2_USER_SYSTEMCTL=1`.

The units already exist at `~/.config/systemd/user/bonsai2-server@{0,1}.service`
from the @gpu tests, BUT without `--tools` — for a tools-on launch
(recommended: it's how the subagents are used, and M9/M10's engine mode
needs the fork's `--tools` set) re-render them:

```bash
# from repo root; renders the S2.2 unit shape + --tools {S1.6 7-tool set}
# for instance i, port 30101+i, from etc/subagent-engine.yaml
~/prod/.venv/bin/python <<'EOF'
import yaml
eng = yaml.safe_load(open('etc/subagent-engine.yaml'))
inst, tools, m = eng['instances'], eng['tools'], eng['model']
tool_args = ','.join(tools['set'])
for i in (0, 1):
    port = inst['port_base'] + i
    exec_start = (
        "ExecStart=/usr/bin/docker run --rm --name bonsai2-server-%i "
        "--gpus all "
        f"--memory {inst['per_instance_budget_gb']}g --memory-swap {inst['per_instance_budget_gb']}g "
        "--shm-size 4g --network host --ipc=host "
        f"{eng['image']['pin']['tag']} "
        f"/opt/bonsai2/bin/llama-server "
        f"--model /opt/bonsai2/models/{m['file']} "
        f"--ctx-size {inst['ctx_size']} --parallel {inst['max_num_seqs']} --batch-size 2048 "
        f"--cache-type-k {eng['kv_cache']} --cache-type-v {eng['kv_cache']} "
        f"--port {port} --api-key {eng['api_key']} "
        f"--reasoning-format {eng['reasoning_format']} --alias {eng['alias']} "
        f"--tools {tool_args}"
    )
    text = (
        "[Unit]\n"
        f"Description=bonsai2 subagent instance %i (llama-server container)\n"
        "After=docker.service\n"
        "StartLimitIntervalSec=300\n"
        "StartLimitBurst=3\n\n"
        "[Service]\n"
        "ExecStartPre=/usr/bin/docker rm -f bonsai2-server-%i\n"
        f"{exec_start}\n"
        "Restart=on-failure\nRestartSec=5\nCPUQuota=64%\n\n"
        "[Install]\nWantedBy=multi-user.target\n"
    )
    open(f'/tmp/bonsai2-live-run/bonsai2-server@{i}.service','w').write(text)
print('units rendered to /tmp/bonsai2-live-run/')
EOF
cp /tmp/bonsai2-live-run/bonsai2-server@{0,1}.service ~/.config/systemd/user/
systemctl --user daemon-reload
```

Pitfall history: a bare `docker run img --flag` dies under the nvidia
entrypoint — the unit must carry the FULL llama-server command (the image's
baked CMD is not reusable that way). User-scope units have no
`docker.service` unit: the template's `Requires=docker.service` is the
documented @gpu-test deviation (drop it, keep `After=docker.service`);
docker is running system-wide regardless.

## Step 2 — scratch layout + rendered harness.yaml

```bash
R=/tmp/bonsai2-live-run   # any user-writable root; keep the run's logs there
mkdir -p $R/health $R/results $R/dedup $R/workspaces
```

Render `harness.yaml` from the repo template (`etc/harness.yaml`) with
THREE changes (the pre-install deltas — H3 + H11):

```python
# ~/prod/.venv/bin/python  (the prod venv has PyYAML; the box python3.12
# also does — either works)
import yaml
h = yaml.safe_load(open('etc/harness.yaml'))
h['main_api_key'] = '<main-stack key>'    # H3: the live main is KEY-WALLED
h['results_dir']  = '/tmp/bonsai2-live-run/results'
h['dedup_dir']    = '/tmp/bonsai2-live-run/dedup'
h['workspace_dir']= '/tmp/bonsai2-live-run/workspaces'
open('/tmp/bonsai2-live-run/harness.yaml','w').write(
    yaml.dump(h, default_flow_style=False, sort_keys=False))
```

The main-stack key: the `qwen38-sglang` container runs `--api-key <k>` —
read it with `docker inspect qwen38-sglang --format '{{json .Config.Cmd}}'`
(keyless completions 401; the keyless `/v1/models` probe still returns
401 = "up", which is the health-probe convention — the key is for
COMPLETIONS, i.e. M6/M8). Without `main_api_key` the M6 control row 401s
into a 0.0 and M8 has no guardrail baseline (the pre-prod run's failure).

## Step 3 — start the manager (user-scope, steady loop)

```bash
BONSAI2_USER_SYSTEMCTL=1 \
BONSAI2_HEALTH_DIR=/tmp/bonsai2-live-run/health \
BONSAI2_ENGINE_YAML=$PWD/etc/subagent-engine.yaml \
BONSAI2_SERVING_YAML=$PWD/etc/serving.yaml \
~/prod/.venv/bin/python bin/bonsai2-manager    # boots i=0..i=N, then probes forever
```

Watch `manager.log`/stderr for: `i=0 healthy=True` → `i=1 healthy=True` →
`full load: 2 instance(s) spawned`. ~20–50 s (S2.5 ordered spawn, waits
each healthy before the next). Verify:

```bash
jq -r '.stack, (.instances | to_entries[] | "\(.key): \(.value.state)")' \
  /tmp/bonsai2-live-run/health/health.json
curl -s http://localhost:30101/v1/models -H 'Authorization: Bearer bonsai2-local'
```

The manager writes `boot_target_s` back into the tracked
`etc/serving.yaml` (S2.6 measured-boot write-back — that is a MODIFIED
tracked file at the end of the run; restore from git or commit it as data).
`--settle N` is the exit-variant; for a live run the manager stays in the
steady loop (that is what H13's `--release-*` flags need: unload/reload
requests are manager-mediated via the control/ queue, acked per cycle).

## Step 4 — launch the bench

```bash
BONSAI2_CONFIG=/tmp/bonsai2-live-run/harness.yaml \
BONSAI2_ENGINE_YAML=$PWD/etc/subagent-engine.yaml \
BONSAI2_HEALTH_DIR=/tmp/bonsai2-live-run/health \
~/prod/.venv/bin/python -m bench.run \
  --results-dir /tmp/bonsai2-live-run/results \
  --deadline-s 600 \
  --release-between --release-after \
  > /tmp/bonsai2-live-run/runner-stdout.log 2>&1
```

Flag notes:
- `--release-between` (H13): at each scenario boundary — unload --all →
  reload + a 16-token warmup per reloaded instance (keeps cold-prefill out
  of the next scenario's timing, matters for M7's saturation numbers).
- `--release-after` (H13): after all rows + the summary — UNLOAD-ONLY by
  design; the pool is LEFT main-only/user_unloaded. If you want the pool
  back up afterwards, run the manager-mediated reload yourself
  (`bin/bonsai2-reload --all` with the same env) or restart the manager.
- Both flags are no-ops under `--mock` (recorded as such in the run JSON).
  If the manager is down at a boundary, the record says `skipped (84)`
  and the run continues (release is an optimization, not a gate).
- The prod venv (`~/prod/.venv/bin/python`) is the launch interpreter —
  it has PyYAML and is what the taskworker gets Popen'd with
  (`sys.executable` of the runner).
- The H13 release calls run `bin/bonsai2-unload`/`bin/bonsai2-reload` via
  their OWN shebang (`/usr/bin/python3` — which HAS yaml on this box),
  not the bench's python.

Execution order inside the run: M6 first (guardrail baseline), then M1,
M2, M3, M4, M5, M7, M8, M9, M10. Expect roughly 40–90 min total
(pre-prod: M1–M7 ≈ 25 min; M9/M10 ×2 modes are tool-loop tasks up to the
600 s deadline each, the slow half).

## Step 5 — M3's spot-check seam (manual, by design)

M3 is semi-auto: auto shape ×0.7 + spot-check ×0.3. The spot-check is
THE MAIN AGENT'S JUDGMENT — the bench cannot drive it. After the run:

1. Open the M3 row in the results dir (`<ts>_M3.json`); each ok task's
   `answer` is one section `{summary, claims}`.
2. Read 1/3 of the sections against the source doc; judge claim accuracy
   (0..1; the rubric threshold is ≥ 2/3).
3. Re-run M3 alone WITH the judgment:

```bash
BONSAI2_CONFIG=/tmp/bonsai2-live-run/harness.yaml \
BONSAI2_ENGINE_YAML=$PWD/etc/subagent-engine.yaml \
BONSAI2_HEALTH_DIR=/tmp/bonsai2-live-run/health \
~/prod/.venv/bin/python -m bench.run \
  --results-dir /tmp/bonsai2-live-run/results \
  --scenario M3 --spotcheck <0..1>
```

   (The pool must be up for this — reload it first if `--release-after`
   already freed it.) The re-run row supersedes the first M3 row in
   bench-results/.
4. An undriven seam is recorded `spotcheck: "unjudged"` (H5) — NOT a
   silent 0.0. A blend below 0.75 with auto 6/6 means the seam was the
   gap, not the model.

## Step 6 — commit the rows + the rebench baseline

The rows in the results dir are the 4.10 record: copy them into
`bench-results/` (force-track: `git add -f bench-results/<file>` — the
dir is git-ignored for local rotation; the committed set is the record),
plus the runner's stdout log + a README entry in `bench-results/README.md`
(dates, launch flags, row-by-row verdicts + observations). Then, from the
run's quick-set rows (M1/M2/M7) the S5.5 baseline:

```bash
~/prod/.venv/bin/python -m bench.rebench save-baseline \
  /tmp/bonsai2-live-run/results \
  --config etc/bench.yaml \
  --engine etc/subagent-engine.yaml \
  --baseline bench-results/rebench-baseline.json
```

(H9: the baseline carries the wall-clock aggregate t/s + the comparability
triple {n_tasks, concurrency, N}; a like-with-like mismatch in a later
gate is "not comparable", not a silent compare.)

## Step 7 — teardown

- `bin/bonsai2-unload --all` (env: `BONSAI2_HEALTH_DIR` +
  `BONSAI2_CONFIG` pointing at the scratch harness.yaml) or just kill the
  manager + `systemctl --user stop bonsai2-server@{0,1}` +
  `docker ps -a | grep bonsai2` to confirm no stray containers.
- `/tmp/bonsai2-live-run/` can be pruned (keep manager.log +
  runner-stdout.log until the rows are committed).
- Restore `etc/serving.yaml` if the manager rewrote `boot_target_s`
  (or commit the new value — it is a measured S2.6 constant).

## Known failure modes (from the two live runs)

| symptom | cause | fix |
|---|---|---|
| M6 401 on all main completions | main stack key-walled, `main_api_key` empty | step 2 (H3) |
| M1 fails with empty content, 1 content token | pin's deepseek reasoning format dumped the answer into `reasoning_content`; pre-H4 rubric measured an empty artifact | H4's content-only M1 prompt; if it recurs, check the prompt survived the rebase |
| M9/M10 `CapabilityError: not terminal within Ns` | driver poll window < task deadline | H1: `wait_s` default = deadline + 90 s; an explicit `wait_s` overrides |
| A capability crash aborts the whole run | H2's pre-state: CapabilityError propagated to the top-level catch | H2: row verdict (fail, named detail), run continues |
| `aggregate_tps` looks tiny (0.25) | pre-H9 serial-sum denominator | H9: `aggregate_tps_wall` is the headline; the row carries the comparability triple |
| M5 miss on "M1, M2, M7" vs "M1, M2, and M7" | strict containment | H8: tolerant matcher (ordered whole-token subsequence) |
| `ERR_CONFIG: config not found: /opt/bonsai2/etc/...` | missing env overrides (manager AND runner need them) | steps 3–4 |
| `results_dir ... does not exist yet` WARN at runner start | the tracked template's default `/run/bonsai2/bench` (pre-install) | harmless; the `--results-dir` override wins |
| M9 workdir leaked in /tmp | pre-H6: driver only cleaned `_tmp`, not the staged mkdtemp | H6: `finally` removes the staged workdir |

## The launch, in one block (copy-paste, from repo root)

```bash
R=/tmp/bonsai2-live-run; mkdir -p $R/health $R/results $R/dedup $R/workspaces
# 1) units (tools-on) — see Step 1 for the render script; then:
systemctl --user daemon-reload
# 2) harness.yaml — see Step 2; set main_api_key from:
#    docker inspect qwen38-sglang --format '{{json .Config.Cmd}}'
# 3) manager (background, steady loop):
BONSAI2_USER_SYSTEMCTL=1 BONSAI2_HEALTH_DIR=$R/health \
BONSAI2_ENGINE_YAML=$PWD/etc/subagent-engine.yaml \
BONSAI2_SERVING_YAML=$PWD/etc/serving.yaml \
nohup ~/prod/.venv/bin/python bin/bonsai2-manager > $R/manager.log 2>&1 &
# 4) wait for "full load: 2 instance(s) spawned" in $R/manager.log, then:
BONSAI2_CONFIG=$R/harness.yaml BONSAI2_ENGINE_YAML=$PWD/etc/subagent-engine.yaml \
BONSAI2_HEALTH_DIR=$R/health \
nohup ~/prod/.venv/bin/python -m bench.run --results-dir $R/results \
  --deadline-s 600 --release-between --release-after > $R/runner-stdout.log 2>&1 &
# 5) on completion: M3 spot-check re-run (Step 5), commit rows (Step 6),
#    teardown (Step 7).
```
