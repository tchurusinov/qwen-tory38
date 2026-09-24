#!/usr/bin/env python3
"""bonsai2_harness — MAP 3.1–3.3 / S3.1–S3.3: the harness control scripts' core.

One module owns the S3.1 output contract, the S3.6 config load, the S3.3
routing/queue model, and the task records that the S3.2 commands
(`bonsai2-status`, `bonsai2-list-instances`, `bonsai2-health`,
`bonsai2-spawn`) share. The per-command entry points live in the thin
deploy files in this same dir (`bin/bonsai2-status` & co — deployed to
/opt/bonsai2/bin/ by install.sh, the repo tree mirrors the host layout
1:1, MAP 0.1); each of them imports this module from its own dir (the
same convention `bonsai2-manager` uses for `bonsai2_vram`).

S3.1 output contract (locked, enforced in ONE place — `emit`):
  * stdout = exactly one JSON object (machine), one line;
  * stderr = human text (diagnostic only; never parsed);
  * the process exit code is ALWAYS one of the S3.2 closed set
    {0, 2, 3, 4, 5, 8, 20, 21, 22, 80, 82, 84} — `emit` raises on any
    other code, so a stray exit code (a harness bug, S3.8 #3) cannot be
    printed silently. Uncaught exceptions escape as Python's exit 1 with
    a traceback, which the S3.8 #3 closed-set suite is built to catch.

Config (S3.2/S3.6):
  * harness.yaml: /opt/bonsai2/etc/harness.yaml by default; the full path
    is overridable via the `BONSAI2_CONFIG` env var (the S3.2 hermetic
    testing hook). Missing/invalid harness.yaml = exit 2 (malformed
    config) for every command that loads it.
  * `BONSAI2_RESULTS_DIR` overrides `results_dir` (S3.2/S3.6).
  * the pool's identity (model alias + api_key the task workers send on
    the wire) comes from subagent-engine.yaml — the manager's own config
    in the same etc/ dir (default /opt/bonsai2/etc/subagent-engine.yaml,
    overridable via `BONSAI2_ENGINE_YAML`, the manager's documented test
    hook). Missing/invalid: the SPAWN accepts (the engine yaml is the
    manager's config, not the harness's — P1 #3, G3 PR#12 r1: a healthy
    pool + free slot is not blocked by it) and the WORKER fails its task
    at start with failed ERR_ENGINE_DOWN (the worker re-reads the file
    itself; the record stays collectable).
  * the health document: <BONSAI2_HEALTH_DIR or /run/bonsai2>/health.json
    (the S3.1 schema, written by the manager every probe cycle). The
    commands are read-only consumers of it; an absent/invalid document
    = the manager is down = exit 84 (S3.2).

Task records (S3.3/S3.4):
  * <results_dir>/tasks/<task_id>.json — the S3.1 spawn-JSON fields
    (task_id, state, instance, port, result_file, error, detail) plus
    bookkeeping (prompt, dedup_key, tools, ctx_need, priority, deadline_s,
    created_ts, worker_pid, instances_tried, updated_ts). The result file
    itself (<results_dir>/<task_id>.json by default, or --out FILE, S3.4)
    is written by the worker with {answer, reasoning, usage} (the
    S3.2 collect shape).
  * task ids: `t-YYYYmmdd-NNNN-<4 hex>` — the SPEC S3.1 example shape
    (`t-20260919-0001`) with a 4-hex uniqueness suffix (the example is a
    shape, not a grammar; parallel spawns must not collide).
  * slot accounting (S3.4 "a task occupies exactly one slot for its
    whole lifetime"): `slots_in_use` = queued + running records on that
    instance (what routing + the status table report); the ENGINE's free
    slot test is `running_count < slots_total` (max_num_seqs bounds only
    concurrent completions — the queue is what enforces it, S3.3).
  * the queue (S3.3): FIFO by created_ts with priority preemption
    (high before normal before low; within a priority, FIFO). A spawn
    that finds no free engine slot on any routing-eligible instance
    writes the record `state: queued` and polls: under a results lock,
    the head of the eligible queue for a slot-free instance is promoted
    to `running` and its worker started — possibly another task's, which
    is how queued work makes forward progress without a manager loop.
    On `queue_wait_max_s` expiry the spawn exits 8 with `state: "queued"`
    (S3.2: 8 = the manager-side wait budget, NOT the task's own
    deadline — S3.4); the task stays queued and a later spawn may
    promote it.
  * dedup (MAP 3.5, next item): the dedup key (prompt hash + tools/ctx/
    priority, NOT --out — S3.4) is computed and recorded here; the 24 h
    TTL lookup in <results_dir>/dedup/ lands with 3.5.

Exit-code mapping for THIS PR's commands (S3.2 closed set, per command):
  * status / list-instances / health: 0, 82 (main-endpoint probe fails),
    84 (health.json absent/invalid); health adds no main probe (it dumps
    the manager's raw document). status/load-config also 2 on malformed
    harness.yaml (the S3.2 "malformed config" case).
  * spawn: 0 (accepted — running, or promoted out of the queue in time),
    2 (malformed args / malformed config), 3 (routing candidate
    bfailed=ERR_PORT_IN_USE or bdown=ERR_INSTANCE_UNHEALTHY at
    submission — S4.5: wait one health cycle, re-spawn), 5 (no instance
    ctx fits the requested --ctx, ERR_CTX_TOO_LONG, S1.5), 8 (queue_wait
    expired, state "queued"), 80 (main-only mode: the S2.4
    {"reason": ...} JSON echoing health.json), 82 (main endpoint
    unhealthy), 84 (health.json absent/invalid).

The worker (`bin/bonsai2-taskworker.py`, not an S3.2 command — it is the
spawn's detached child):
  * 400 (request > ctx) -> failed ERR_CTX_TOO_LONG;
  * non-JSON / unparseable body -> failed ERR_PARSE (S3.7);
  * connection error / 429 / 5xx -> engine_retried ONCE on another
    instance (lowest-i healthy with ctx fit among the UNTRIED; when
    every healthy instance has already been tried the task fails —
    P0 #2, G3 PR#12 r1: the pre-fix least-loaded ANY-instance fallback
    could re-pick an already-tried one), second failure -> failed
    ERR_ENGINE_DOWN (S1.5 "engine crash mid-request");
  * deadline expiry -> timeout (the S3.4 22-path state; the manager-side
    enforcement half is MAP 3.4).
  The task record is the source of truth (the worker always exits 0;
  collect, MAP 3.6, maps terminal states to 20/21/22).
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import secrets
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

# --------------------------------------------------------------------------
# S3.2 closed set (the single source — S3.8 #3: any other exit code from a
# S3.2 script is a harness bug).
# --------------------------------------------------------------------------
EXIT_OK = 0
EXIT_ARGS = 2
EXIT_PORT = 3
EXIT_CTX = 5
EXIT_QUEUE_WAIT = 8
EXIT_MAIN_ONLY = 80
EXIT_MAIN_DOWN = 82
EXIT_MANAGER_DOWN = 84
EXIT_CLOSED_SET = frozenset({0, 2, 3, 4, 5, 8, 20, 21, 22, 80, 82, 84})

DEFAULT_CONFIG_PATH = "/opt/bonsai2/etc/harness.yaml"
DEFAULT_ENGINE_YAML = "/opt/bonsai2/etc/subagent-engine.yaml"
DEFAULT_HEALTH_DIR = "/run/bonsai2"

# S3.6 defaults (a missing key gets the default; a malformed VALUE is a
# ConfigError -> exit 2; S3.6 is the locked key set).
CONFIG_DEFAULTS: dict = {
    "main_endpoint": "http://localhost:30001",
    "main_endpoint_healthy_probe": "/v1/models",
    "results_dir": "/run/bonsai2/results",
    "dedup_dir": "/run/bonsai2/dedup",
    "dedup_ttl_h": 24,
    "queue_wait_max_s": 60,
    "instance_healthy_timeout_s": 30,
    "task_default_deadline_s": 600,
    "main_tps_guardrail_pct": 5,
}
_INT_KEYS = (
    "dedup_ttl_h",
    "queue_wait_max_s",
    "instance_healthy_timeout_s",
    "task_default_deadline_s",
    "main_tps_guardrail_pct",
)

# S3.4 priority ranks (lower = earlier; S3.3 "high jumps the queue").
PRIORITY_RANK = {"high": 0, "normal": 1, "low": 2}

# S3.1 task states (locked set, S3.1 schema).
TERMINAL_STATES = ("done", "failed", "timeout")
CHARGED_STATES = ("queued", "running")  # S3.4 whole-lifetime slot charge
ROUTABLE_STATES = ("healthy", "degraded")


class ConfigError(Exception):
    """Malformed/missing config (harness.yaml / subagent-engine.yaml) ->
    the commands' exit-2 path."""


class HealthMissing(Exception):
    """health.json absent or unparseable -> the commands' exit-84 path
    (S3.2: 'manager itself down'; the manager writes the document every
    probe cycle, so its absence is the observable)."""


class TaskNotFound(Exception):
    """Unknown TASK_ID (the collect/steer/cancel exit-2 path, S3.2)."""


# --------------------------------------------------------------------------
# S3.1 output contract
# --------------------------------------------------------------------------
def emit(payload: dict, code: int, detail: str | None = None) -> int:
    """The S3.1 contract, enforced once: stdout = one JSON object (one
    line), stderr = human text, exit = a closed-set code. Raises
    ValueError on a non-closed-set code (the S3.8 #3 stray-code guard —
    better to crash loudly than print exit 1)."""
    if code not in EXIT_CLOSED_SET:
        raise ValueError(
            f"emit: exit code {code} is outside the S3.2 closed set {sorted(EXIT_CLOSED_SET)}"
        )
    sys.stdout.write(json.dumps(payload, separators=(",", ":")) + "\n")
    sys.stdout.flush()
    if detail:
        print(f"bonsai2: {detail}", file=sys.stderr, flush=True)
    return code


def spawn_json(rec: dict, detail: str | None = None) -> dict:
    """The S3.1 spawn/steer/cancel/collect JSON object (field order as in
    the SPEC example), from a task record (the record carries extras)."""
    return {
        "task_id": rec.get("task_id"),
        "state": rec.get("state"),
        "instance": rec.get("instance"),
        "port": rec.get("port"),
        "result_file": rec.get("result_file"),
        "error": rec.get("error"),
        "detail": rec.get("detail", detail),
    }


# --------------------------------------------------------------------------
# config loading (S3.6 + the manager's identity config)
# --------------------------------------------------------------------------
def _load_yaml_map(path: Path) -> dict:
    import yaml  # PyYAML: box dep, at the edge only (repo convention)

    if not path.is_file():
        raise ConfigError(f"config not found: {path}")
    try:
        text = path.read_text()
        data = yaml.safe_load(text)
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigError(f"config unreadable: {path}: {exc}") from exc
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise ConfigError(f"ERR_CONFIG: {path}: top level is not a mapping")
    return data


def load_config(env: dict | None = None) -> dict:
    """The S3.6 harness.yaml load with the S3.2 env overrides.

    `BONSAI2_CONFIG` overrides the config PATH; `BONSAI2_RESULTS_DIR`
    overrides `results_dir`. Missing keys fall back to CONFIG_DEFAULTS;
    a missing file or a malformed value raises ConfigError (exit 2).
    Returns a dict with every S3.6 key + `config_path` (the path read).
    """
    env = os.environ if env is None else env
    path = Path(env.get("BONSAI2_CONFIG", DEFAULT_CONFIG_PATH))
    data = _load_yaml_map(path)
    cfg: dict = dict(CONFIG_DEFAULTS)
    cfg["config_path"] = str(path)
    for key in (
        "main_endpoint",
        "main_endpoint_healthy_probe",
        "results_dir",
        "dedup_dir",
    ):
        if key in data:
            val = data[key]
            if not isinstance(val, str) or not val:
                raise ConfigError(
                    f"ERR_CONFIG: harness.yaml: {key} must be a non-empty string"
                )
            cfg[key] = val
    for key in _INT_KEYS:
        if key in data:
            val = data[key]
            if not isinstance(val, (int, float)) or isinstance(val, bool) or val <= 0:
                raise ConfigError(
                    f"ERR_CONFIG: harness.yaml: {key} must be a positive number"
                )
            cfg[key] = val
    results_env = env.get("BONSAI2_RESULTS_DIR")
    if results_env:
        cfg["results_dir"] = results_env
    return cfg


def load_engine_identity(env: dict | None = None) -> dict:
    """The task worker's wire identity from subagent-engine.yaml (the
    manager's config, same etc/ dir): `alias` (the OpenAI model id all
    instances share, S1.3) + `api_key` (S1.3 --api-key). Path override:
    the manager's documented `BONSAI2_ENGINE_YAML` hook. Missing file /
    missing or non-string keys -> ConfigError (exit 2)."""
    env = os.environ if env is None else env
    path = Path(env.get("BONSAI2_ENGINE_YAML", DEFAULT_ENGINE_YAML))
    data = _load_yaml_map(path)
    out = {"engine_yaml": str(path)}
    for key in ("alias", "api_key"):
        val = data.get(key)
        if not isinstance(val, str) or not val:
            raise ConfigError(f"ERR_CONFIG: {path}: {key} missing/not a string")
        out[key] = val
    port_base = data.get("instances", {}).get("port_base")
    if isinstance(port_base, int) and not isinstance(port_base, bool):
        out["port_base"] = port_base
    return out


# --------------------------------------------------------------------------
# the health document (S3.1; written by the manager, read by the commands)
# --------------------------------------------------------------------------
def health_doc_path(health_dir: str | None = None, env: dict | None = None) -> Path:
    env = os.environ if env is None else env
    base = health_dir or env.get("BONSAI2_HEALTH_DIR") or DEFAULT_HEALTH_DIR
    return Path(base) / "health.json"


def load_health_doc(health_dir: str | None = None, env: dict | None = None) -> dict:
    """Read /run/bonsai2/health.json (S3.1 schema). Raises HealthMissing
    (the exit-84 path) when the document is absent, not valid JSON, or
    missing its `stack` + `instances` shape."""
    path = health_doc_path(health_dir, env)
    if not path.is_file():
        raise HealthMissing(f"health.json absent: {path} (manager down?)")
    try:
        doc = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise HealthMissing(f"health.json unreadable: {path}: {exc}") from exc
    if (
        not isinstance(doc, dict)
        or not isinstance(doc.get("instances"), list)
        or not isinstance(doc.get("stack"), str)
    ):
        raise HealthMissing(f"health.json malformed (missing stack/instances): {path}")
    return doc


def main_healthy(cfg: dict, timeout_s: float = 5.0) -> bool:
    """The S3.6 `main_endpoint_healthy_probe` liveness GET — the exit-82
    gate. Same semantics as the manager's S2.1 probe (shared convention):
    2xx = up; 401 + other 4xx (not 404/410) = up (the server answered;
    the harness does not hold the main stack's key); 404/410 = the route
    moved/removed = down; 5xx / refused / timeout = down."""
    url = cfg["main_endpoint"].rstrip("/") + cfg["main_endpoint_healthy_probe"]
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            resp.read(64)
        return True
    except urllib.error.HTTPError as e:
        return (200 <= e.code < 500) and e.code not in (404, 410)
    except (urllib.error.URLError, OSError, TimeoutError, ValueError):
        return False


# --------------------------------------------------------------------------
# task records + slot accounting
# --------------------------------------------------------------------------
def results_dirs(cfg: dict) -> dict:
    """The results layout (mkdir'd on demand): tasks/ holds the records,
    the result files live at results_dir/<task_id>.json (the S3.1
    example: /run/bonsai2/results/t-....json) unless --out moved them,
    .locks/ holds the claim locks, .task_counter the id sequence."""
    base = Path(cfg["results_dir"])
    return {"base": base, "tasks": base / "tasks", "locks": base / ".locks"}


def ensure_results_dirs(d: dict) -> None:
    for p in (d["base"], d["tasks"], d["locks"]):
        p.mkdir(parents=True, exist_ok=True)


def task_path(d: dict, task_id: str) -> Path:
    return d["tasks"] / f"{task_id}.json"


def read_task(d: dict, task_id: str) -> dict:
    p = task_path(d, task_id)
    if not p.is_file():
        raise TaskNotFound(f"unknown task: {task_id} (no {p})")
    try:
        rec = json.loads(p.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise TaskNotFound(f"task record unreadable: {p}: {exc}") from exc
    if not isinstance(rec, dict) or rec.get("task_id") != task_id:
        raise TaskNotFound(f"task record malformed: {p}")
    return rec


def write_task(d: dict, rec: dict) -> None:
    """Atomic record write (tmp + rename) — a reader (status, another
    spawn's claim pass, collect) never sees a partial record."""
    p = task_path(d, rec["task_id"])
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(rec, indent=2) + "\n")
    os.replace(tmp, p)


def iter_tasks(d: dict) -> list[dict]:
    out: list[dict] = []
    for p in sorted(d["tasks"].glob("*.json")):
        try:
            rec = json.loads(p.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(rec, dict) and rec.get("task_id"):
            out.append(rec)
    return out


def slots_in_use(d: dict, i: int) -> int:
    """The S3.4 whole-lifetime charge on instance i: queued + running
    records (what routing and the status table report)."""
    return sum(
        1
        for r in iter_tasks(d)
        if r.get("instance") == i and r.get("state") in CHARGED_STATES
    )


def running_count(d: dict, i: int) -> int:
    """The ENGINE's concurrent-completion charge on instance i (the
    max_num_seqs bound, S3.3): running records only."""
    return sum(
        1
        for r in iter_tasks(d)
        if r.get("instance") == i and r.get("state") == "running"
    )


def _instance_loads(d: dict) -> dict:
    """{i: (slots_in_use, running_count)} from ONE iter_tasks pass
    (P1 #6, G3 PR#12 r1): routing + the fast path need both counters
    for every instance — iterating the task records N_instances × N
    times is O(N × M) I/O for no reason. Instances with no records are
    absent; callers use .get(i, (0, 0))."""
    loads: dict = {}
    for r in iter_tasks(d):
        i = r.get("instance")
        if r.get("state") not in CHARGED_STATES:
            continue
        entry = loads.setdefault(i, [0, 0])
        entry[0] += 1
        if r.get("state") == "running":
            entry[1] += 1
    return {i: (v[0], v[1]) for i, v in loads.items()}


@contextlib.contextmanager
def lock_wait(base_dir: Path, name: str, timeout_s: float = 2.0):
    """Best-effort O_EXCL lock file. `name` is the lock IDENTITY — the
    file is {name}.lock, so concurrent holders contend on the SAME path
    (G3 PR#12 r1 P0 #1: a {name}.{pid} naming meant every process
    created its own file and the lock never actually serialized across
    processes). The PID suffix is the STALE-DETECTION key: a holder
    whose PID no longer exists left a dead lock, which is reclaimed
    (the O_EXCL pattern is best-effort, so a crashed holder must not
    wedge the queue — P2 #7's follow-through). Released by unlink.
    A timeout raises TimeoutError (the caller retries its poll loop —
    the lock is advisory ordering, not a resource)."""
    lock_dir = base_dir / ".locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    path = lock_dir / f"{name}.lock"
    deadline = time.monotonic() + timeout_s
    while True:
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            with os.fdopen(fd, "w") as f:
                f.write(str(os.getpid()))
            break
        except FileExistsError:
            # Stale-lock reclaim: a holder crashed without the unlink
            # leaving {name}.lock behind. If the recorded PID is gone,
            # the lock is dead — take it (the O_EXCL pattern is
            # best-effort, so a dead holder must not wedge the queue).
            # An EMPTY file is either a live holder mid-write or a
            # holder that died between create and write — give it a
            # 0.5 s grace before reclaiming.
            stale = False
            first_empty = None
            try:
                content = path.read_text().strip()
                if content:
                    holder = int(content)
                    if holder <= 0:
                        stale = True
                    else:
                        try:
                            os.kill(holder, 0)
                        except OSError:
                            stale = True
                else:
                    now = time.monotonic()
                    if first_empty is None:
                        first_empty = now
                    elif now - first_empty > 0.5:
                        stale = True
            except (OSError, ValueError):
                stale = True
            if stale:
                try:
                    path.unlink()
                except OSError:
                    pass
                continue
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    f"lock {name!r} busy for {timeout_s:.1f} s"
                ) from None
            time.sleep(0.01)
    try:
        yield
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def new_task_id(d: dict) -> str:
    """`t-YYYYmmdd-NNNN-<4hex>`: the SPEC S3.1 example shape + a 4-hex
    uniqueness suffix. The counter is a monotonically-increasing file
    under the results lock (concurrent spawns cannot reuse a sequence
    number; the suffix covers a same-number race anyway)."""
    date = time.strftime("%Y%m%d")
    counter_file = d["base"] / ".task_counter"
    with lock_wait(d["base"], "results"):
        n = 1
        try:
            n = int(counter_file.read_text().strip() or "0") + 1
        except (OSError, ValueError):
            pass
        counter_file.write_text(str(n))
    return f"t-{date}-{n:04d}-{secrets.token_hex(2)}"


# --------------------------------------------------------------------------
# S3.3 routing + queue
# --------------------------------------------------------------------------
def _inst_total(entry: dict) -> int:
    val = entry.get("slots_total")
    return val if isinstance(val, int) and not isinstance(val, bool) and val > 0 else 0


def _inst_ctx(entry: dict) -> int | None:
    val = entry.get("ctx")
    return val if isinstance(val, int) and not isinstance(val, bool) else None


def route_target(
    doc: dict, d: dict, ctx_need: int | None
) -> tuple[dict | None, str | None, str | None]:
    """S3.3 least-loaded, deadline-aware routing, one submission pass.

    Returns (target_entry, error_name, detail):
      * error None + a target with a free engine slot -> run there;
      * error None + a target without one -> queue there (the caller's
        queue-wait loop owns the rest);
      * ERR_CTX_TOO_LONG -> no instance's live ctx fits ctx_need (the
        S1.5 exit-5 condition at submission);
      * ERR_PORT_IN_USE -> the routing candidate is bfailed (S1.5: port
        collision at boot, the manager is mid-clear);
      * ERR_INSTANCE_UNHEALTHY -> the routing candidate is bdown (S1.5:
        unhealthy > 30 s — S4.5: wait one health cycle, re-spawn).

    `bunloaded` never appears in the health document (the manager drops
    it, S3.1 note), so it is skipped by construction. Routing is over
    ctx-FIT instances (when ctx_need is set): the least-loaded among
    them, tie-break lowest i; if no instance fits, the least-loaded
    overall is reported with ERR_CTX_TOO_LONG (the detail says which)
    — EVEN when a slot is free: a free slot on a ctx-shrunk instance is
    not a slot for this task (S1.5 exit 5, not a blind queue).

    P1 #6 (G3 PR#12 r1): the slot counters are built from ONE
    iter_tasks pass (`_instance_loads`) — the per-instance
    slots_in_use/running_count calls each re-globbed + re-parsed every
    record, O(N_instances × N_tasks) I/O per spawn.
    """
    instances = doc.get("instances", [])
    if not instances:
        return None, "ERR_NO_INSTANCES", "main-only mode (0 instances in health.json)"
    loads = _instance_loads(d)
    charged = {e.get("i"): loads.get(e.get("i"), (0, 0))[0] for e in instances}
    running = {e.get("i"): loads.get(e.get("i"), (0, 0))[1] for e in instances}
    fit = [e for e in instances if ctx_need is None or (_inst_ctx(e) or 0) >= ctx_need]
    pool = fit if fit else instances
    routable = [e for e in pool if e.get("state") in ROUTABLE_STATES]
    free = [e for e in routable if running[e.get("i")] < _inst_total(e)]
    if free:
        target = min(free, key=lambda e: (charged.get(e.get("i"), 0), e.get("i", 0)))
        if not fit:
            # report the instance with the LARGEST live ctx (tie: lowest
            # i) — the one most likely to fit after a ctx bump/re-anchor
            best = min(instances, key=lambda e: (-(_inst_ctx(e) or 0), e.get("i", 0)))
            return (
                best,
                "ERR_CTX_TOO_LONG",
                (
                    f"requested --ctx {ctx_need} exceeds the live ctx of every "
                    f"instance (max live ctx {max((_inst_ctx(e) or 0) for e in instances)})"
                ),
            )
        return target, None, None
    cand = min(pool, key=lambda e: (charged.get(e.get("i"), 0), e.get("i", 0)))
    if not fit:
        return (
            cand,
            "ERR_CTX_TOO_LONG",
            (
                f"requested --ctx {ctx_need} exceeds the live ctx of every "
                f"instance (max live ctx {max((_inst_ctx(e) or 0) for e in instances)})"
            ),
        )
    if cand.get("state") == "bfailed":
        return (
            cand,
            "ERR_PORT_IN_USE",
            (
                f"routing candidate i={cand.get('i')} is bfailed (port conflict at boot, "
                "the manager is mid-clear — S4.5: check status, wait one health cycle, re-spawn)"
            ),
        )
    if cand.get("state") in ("bdown", "degraded"):
        return (
            cand,
            "ERR_INSTANCE_UNHEALTHY" if cand.get("state") == "bdown" else None,
            (
                f"routing candidate i={cand.get('i')} is {cand.get('state')} "
                "(S4.5: wait one health cycle, re-spawn)"
            ),
        )
    return cand, None, None


def claim_own_task(d: dict, doc: dict, task_id: str) -> tuple[dict | None, bool]:
    """The fast-path claim, under the SAME slot-claim lock as
    claim_slot (G3 PR#12 r1 P0 #1: the old fast path checked
    running_count and promoted this record with no lock held, so a
    concurrent spawn's claim_slot could promote the same record in
    between — two start_workers, one task. Now the check + promotion +
    ownership are one critical section).

    Returns (rec, promoted):
      * rec None -> the slot is not free for this task (running full,
        or the instance no longer routable / the ctx no longer fits —
        the S2.5 drift halve): the caller drops into the queue-wait
        loop;
      * promoted True -> THIS call marked the record running; the
        caller owns starting its worker;
      * promoted False -> the record is already running/terminal,
        owned by ANOTHER spawn (its claim pass got here first): the
        caller's task is running — emit it, start no second worker.
    """
    with lock_wait(d["base"], "slot-claim"):
        try:
            rec = read_task(d, task_id)
        except TaskNotFound:
            return None, False
        if rec.get("state") in ("running", *TERMINAL_STATES):
            return rec, False
        i = rec.get("instance")
        entry = next(
            (e for e in doc.get("instances", []) if e.get("i") == i),
            None,
        )
        if entry is None or entry.get("state") not in ROUTABLE_STATES:
            return None, False
        if (
            rec.get("ctx_need") is not None
            and (_inst_ctx(entry) or 0) < rec["ctx_need"]
        ):
            return None, False
        loads = _instance_loads(d)
        running = loads.get(i, (0, 0))[1]
        if running >= _inst_total(entry):
            return None, False
        rec["state"] = "running"
        rec["updated_ts"] = time.time()
        write_task(d, rec)
        return rec, True


def claim_slot(d: dict, doc: dict) -> dict | None:
    """One slot-claim pass under the results lock (the S3.3 queue
    protocol): for each instance with a free engine slot (running <
    slots_total, routable state), promote the HEAD of that instance's
    eligible queue (priority rank, then created_ts, then task_id —
    "high jumps the queue, low waits") to `running` and return the
    claimed record (possibly ANOTHER spawn's task — that is how queued
    work moves without a manager loop). None when nothing is eligible.
    A record whose target instance has since gone un-routable or
    lost its ctx fit (the S2.5 drift halve) stays queued."""
    with lock_wait(d["base"], "slot-claim"):
        queued = [r for r in iter_tasks(d) if r.get("state") == "queued"]
        if not queued:
            return None
        doc_ctx = {e.get("i"): _inst_ctx(e) for e in doc.get("instances", [])}
        doc_state = {e.get("i"): e.get("state") for e in doc.get("instances", [])}
        doc_total = {e.get("i"): _inst_total(e) for e in doc.get("instances", [])}
        by_inst: dict = {}
        for r in queued:
            by_inst.setdefault(r.get("instance"), []).append(r)
        for i in sorted(
            doc_state, key=lambda x: (0 if isinstance(x, int) else 1, x or 0)
        ):
            if doc_state.get(i) not in ROUTABLE_STATES:
                continue
            if running_count(d, i) >= doc_total.get(i, 0):
                continue
            eligible = [
                r
                for r in by_inst.get(i, [])
                if (
                    r.get("ctx_need") is None
                    or (doc_ctx.get(i) or 0) >= r.get("ctx_need")
                )
            ]
            if not eligible:
                continue
            head = min(
                eligible,
                key=lambda r: (
                    PRIORITY_RANK.get(r.get("priority"), 1),
                    r.get("created_ts", 0.0),
                    r.get("task_id", ""),
                ),
            )
            head["state"] = "running"
            head["updated_ts"] = time.time()
            write_task(d, head)
            return head
    return None


def start_worker(d: dict, rec: dict, log=print) -> int:
    """Spawn the detached task worker for a record that is `running`
    (or being promoted to it). The worker is this dir's
    bonsai2-taskworker.py (deployed next to it, the MAP 0.1 1:1
    layout); the record carries everything it needs (the wire identity
    is re-read from subagent-engine.yaml itself, so a config re-render
    between spawn and worker start is picked up). Returns the pid;
    records worker_pid + updated_ts on the record."""
    bin_dir = Path(__file__).resolve().parent
    worker = bin_dir / "bonsai2-taskworker.py"
    proc = subprocess.Popen(
        [sys.executable, str(worker), str(task_path(d, rec["task_id"]))],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    rec["worker_pid"] = proc.pid
    rec["updated_ts"] = time.time()
    write_task(d, rec)
    log(
        f"worker started for {rec['task_id']} (pid {proc.pid}, i={rec.get('instance')})"
    )
    return proc.pid


# --------------------------------------------------------------------------
# commands (MAP 3.2 + 3.3)
# --------------------------------------------------------------------------
def _recomputed_instances(doc: dict, d: dict) -> list[dict]:
    """The S3.1 status `instances` list with slots_in_use RECOMPUTED from
    the task records (the manager's own document carries the field for
    the pool's accounting but the harness owns the live task slots —
    the manager writes 0, the S3.1 note 'the harness owns live task
    slots'). All other fields are the manager's, verbatim."""
    out = []
    for e in doc.get("instances", []):
        entry = dict(e)
        entry["slots_in_use"] = slots_in_use(d, e.get("i"))
        out.append(entry)
    return out


def cmd_status(argv: list[str], env: dict | None = None) -> int:
    """bonsai2-status — the S3.1 status schema, exit 0/82/84 (+2 on a
    malformed harness.yaml). main_healthy is LIVE-probed (the document
    value may lag by up to a probe cycle); main_only_reason is present
    ONLY when instances: 0 (the S2.4 contract, from the document)."""
    if argv:
        return emit(
            {
                "error": "ERR_ARGS",
                "detail": f"status takes no arguments (got: {argv!r})",
            },
            EXIT_ARGS,
            "status takes no arguments",
        )
    try:
        cfg = load_config(env)
    except ConfigError as exc:
        return emit({"error": "ERR_ARGS", "detail": str(exc)}, EXIT_ARGS, str(exc))
    try:
        doc = load_health_doc(env=env)
    except HealthMissing as exc:
        return emit(
            {"error": "ERR_MANAGER_DOWN", "detail": str(exc)},
            EXIT_MANAGER_DOWN,
            str(exc),
        )
    ok = main_healthy(cfg)
    d = results_dirs(cfg)
    ensure_results_dirs(d)
    out = {
        "stack": doc["stack"],
        "instances": _recomputed_instances(doc, d),
        "main_endpoint": cfg["main_endpoint"],
        "main_healthy": ok,
    }
    if not out["instances"]:
        out["main_only_reason"] = doc.get("main_only_reason", "budget_zero")
    return emit(
        out,
        0 if ok else EXIT_MAIN_DOWN,
        None if ok else "main endpoint probe failed (S3.6 main_endpoint_healthy_probe)",
    )


def cmd_list_instances(argv: list[str], env: dict | None = None) -> int:
    """bonsai2-list-instances — HUMAN TABLE on stdout (the S3.2 named
    exception to the one-JSON-object rule; diagnostic text still goes to
    stderr), exit 0/82/84."""
    if argv:
        return emit(
            {
                "error": "ERR_ARGS",
                "detail": f"list-instances takes no arguments (got: {argv!r})",
            },
            EXIT_ARGS,
            "list-instances takes no arguments",
        )
    try:
        cfg = load_config(env)
    except ConfigError as exc:
        return emit({"error": "ERR_ARGS", "detail": str(exc)}, EXIT_ARGS, str(exc))
    try:
        doc = load_health_doc(env=env)
    except HealthMissing as exc:
        return emit(
            {"error": "ERR_MANAGER_DOWN", "detail": str(exc)},
            EXIT_MANAGER_DOWN,
            str(exc),
        )
    ok = main_healthy(cfg)
    d = results_dirs(cfg)
    ensure_results_dirs(d)
    rows = []
    for e in _recomputed_instances(doc, d):
        rows.append(
            (
                str(e.get("i")),
                str(e.get("port")),
                str(e.get("state")),
                str(e.get("ctx")),
                "yes" if e.get("ctx_reduced") else "no",
                f"{e.get('slots_in_use', 0)}/{e.get('slots_total', 0)}",
            )
        )
    header = ("INSTANCE", "PORT", "STATE", "CTX", "CTX_REDUCED", "SLOTS")
    widths = [
        max(len(h), *(len(r[c]) for r in rows)) if rows else len(h)
        for c, h in enumerate(header)
    ]
    sys.stdout.write(
        "  ".join(h.ljust(w) for h, w in zip(header, widths)).rstrip() + "\n"
    )
    for r in rows:
        sys.stdout.write(
            "  ".join(v.ljust(w) for v, w in zip(r, widths)).rstrip() + "\n"
        )
    if not rows:
        sys.stdout.write(
            f"(no running instances — main-only, reason: {doc.get('main_only_reason', 'budget_zero')})\n"
        )
    sys.stdout.flush()
    if not ok:
        print("bonsai2: main endpoint probe failed (exit 82)", file=sys.stderr)
    return 0 if ok else EXIT_MAIN_DOWN


def cmd_health(argv: list[str], env: dict | None = None) -> int:
    """bonsai2-health — the RAW health.json, pretty-printed (S3.2),
    exit 0/84 only: no main probe (the document is the manager's report,
    dumped as-is)."""
    if argv:
        return emit(
            {
                "error": "ERR_ARGS",
                "detail": f"health takes no arguments (got: {argv!r})",
            },
            EXIT_ARGS,
            "health takes no arguments",
        )
    try:
        doc = load_health_doc(env=env)
    except HealthMissing as exc:
        return emit(
            {"error": "ERR_MANAGER_DOWN", "detail": str(exc)},
            EXIT_MANAGER_DOWN,
            str(exc),
        )
    sys.stdout.write(json.dumps(doc, indent=2) + "\n")
    sys.stdout.flush()
    return 0


def _parse_spawn_args(argv: list[str]) -> dict | str:
    """The S3.2 spawn surface, hand-rolled (the exit-2 JSON must still
    be one stdout object, argparse's usage-to-stderr shape cannot
    emit it). Returns the parsed dict, or the detail string on failure.

    [-p PROMPT_FILE | -i INLINE_PROMPT] [--tools on|off] [--ctx N]
    [--deadline SECS] [--out FILE] [--priority low|normal|high]
    """
    args = {
        "prompt": None,
        "prompt_inline": False,
        "tools": "off",
        "ctx": None,
        "deadline": None,
        "out": None,
        "priority": "normal",
    }
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "-p":
            if i + 1 >= len(argv):
                return "ERR_ARGS: -p needs a PROMPT_FILE"
            if args["prompt"] is not None:
                return (
                    "ERR_ARGS: both -p and -i given (exactly one prompt source)"
                    if args["prompt_inline"]
                    else "ERR_ARGS: -p given twice"
                )
            args["prompt"] = argv[i + 1]
            i += 2
            continue
        if a == "-i":
            if i + 1 >= len(argv):
                return "ERR_ARGS: -i needs an INLINE_PROMPT"
            if args["prompt"] is not None or args["prompt_inline"]:
                return "ERR_ARGS: both -p and -i given (exactly one prompt source)"
            args["prompt_inline"] = True
            args["prompt"] = argv[i + 1]
            i += 2
            continue
        if a == "--tools":
            if i + 1 >= len(argv) or argv[i + 1] not in ("on", "off"):
                return "ERR_ARGS: --tools must be on|off"
            args["tools"] = argv[i + 1]
            i += 2
            continue
        if a == "--ctx":
            if i + 1 >= len(argv) or not argv[i + 1].isdigit() or int(argv[i + 1]) <= 0:
                return "ERR_ARGS: --ctx must be a positive integer"
            args["ctx"] = int(argv[i + 1])
            i += 2
            continue
        if a == "--deadline":
            if i + 1 >= len(argv):
                return "ERR_ARGS: --deadline needs SECS"
            try:
                v = float(argv[i + 1])
            except ValueError:
                return "ERR_ARGS: --deadline must be a number"
            if v <= 0:
                return "ERR_ARGS: --deadline must be > 0"
            args["deadline"] = v
            i += 2
            continue
        if a == "--out":
            if i + 1 >= len(argv):
                return "ERR_ARGS: --out needs a FILE"
            args["out"] = Path(argv[i + 1])
            i += 2
            continue
        if a == "--priority":
            if i + 1 >= len(argv) or argv[i + 1] not in PRIORITY_RANK:
                return "ERR_ARGS: --priority must be low|normal|high"
            args["priority"] = argv[i + 1]
            i += 2
            continue
        return f"ERR_ARGS: unknown argument {a!r}"
    if args["prompt"] is None:
        return "ERR_ARGS: exactly one of -p PROMPT_FILE / -i INLINE_PROMPT is required"
    if not args["prompt"]:
        return "ERR_ARGS: empty prompt"
    return args


def cmd_spawn(argv: list[str], env: dict | None = None, poll_s: float = 0.25) -> int:
    """bonsai2-spawn (S3.2/S3.3) — the full submission flow documented in
    the module docstring; the S3.2 per-command exit mapping:
    0 accepted, 2 malformed args/config, 3 candidate bfailed/bdown,
    5 ctx too long, 8 queue_wait expired (state "queued"), 80 main-only
    (the S2.4 {"reason": ...} JSON), 82 main unhealthy, 84 manager
    down / health.json absent."""
    parsed = _parse_spawn_args(argv)
    if isinstance(parsed, str):
        return emit({"error": "ERR_ARGS", "detail": parsed}, EXIT_ARGS, parsed)
    try:
        cfg = load_config(env)
    except ConfigError as exc:
        return emit({"error": "ERR_ARGS", "detail": str(exc)}, EXIT_ARGS, str(exc))
    # P1 #3 (G3 PR#12 r1): the wire identity is only used for the
    # port_base fallback below — the worker re-reads subagent-engine.yaml
    # itself at start (its own load_engine_identity), so a missing/malformed
    # engine yaml must NOT block spawn acceptance on a healthy pool + free
    # slot. Fall back to the default port base here; a real config problem
    # then surfaces as the worker's failed ERR_ENGINE_DOWN at run time
    # (the record is still collectable), not a spawn exit 2.
    try:
        ident = load_engine_identity(env)
    except ConfigError:
        ident = {"engine_yaml": None, "port_base": 30101}
    try:
        doc = load_health_doc(env=env)
    except HealthMissing as exc:
        return emit(
            {"error": "ERR_MANAGER_DOWN", "detail": str(exc)},
            EXIT_MANAGER_DOWN,
            str(exc),
        )
    if not main_healthy(cfg):
        return emit(
            {
                "error": "ERR_MAIN_DOWN",
                "detail": "main endpoint unhealthy (S3.6 probe failed)",
            },
            EXIT_MAIN_DOWN,
            "main endpoint unhealthy — check the main stack (external, S2.1)",
        )
    if not doc.get("instances"):
        return emit(
            {"reason": doc.get("main_only_reason", "budget_zero")},
            EXIT_MAIN_ONLY,
            "main-only mode — no subagent instances (S2.4); run the task on main",
        )
    d = results_dirs(cfg)
    ensure_results_dirs(d)
    target, err, detail = route_target(doc, d, parsed["ctx"])
    if err:
        code = EXIT_CTX if err == "ERR_CTX_TOO_LONG" else EXIT_PORT
        return emit(
            {
                "task_id": None,
                "state": "failed",
                "instance": target.get("i") if target else None,
                "port": target.get("port") if target else None,
                "result_file": None,
                "error": err,
                "detail": detail,
            },
            code,
            detail,
        )
    instance_i = target["i"]
    # P2 #9 (G3 PR#12 r1): a string port in health.json would otherwise
    # be emitted as a string, violating the S3.1 numeric port schema.
    port = int(target.get("port", ident.get("port_base", 30101) + instance_i))
    task_id = new_task_id(d)
    if parsed["out"] is None:
        result_file = str(d["base"] / f"{task_id}.json")
    else:
        out_path = parsed["out"].expanduser().resolve()
        if out_path.parent.is_file():
            return emit(
                {"error": "ERR_ARGS", "detail": f"--out {out_path} : parent is a file"},
                EXIT_ARGS,
                f"--out {out_path}: parent is a file",
            )
        # P1 #5 (G3 PR#12 r1): validate/CREATE the --out parent at spawn
        # time (the worker used to mkdir it at write time, so a --out under
        # an impossible deep path was only discovered mid-task). An OSError
        # here (parent path uncreatable) is a spawn-time rejection, exit 2.
        try:
            out_path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            return emit(
                {"error": "ERR_ARGS", "detail": f"--out {out_path}: {exc}"},
                EXIT_ARGS,
                f"--out {out_path}: {exc}",
            )
        result_file = str(out_path)
    prompt = parsed["prompt"]
    if not parsed["prompt_inline"]:
        try:
            prompt = Path(parsed["prompt"]).expanduser().resolve().read_text()
        except OSError as exc:
            return emit(
                {"error": "ERR_ARGS", "detail": f"-p {parsed['prompt']}: {exc}"},
                EXIT_ARGS,
                str(exc),
            )
    # S3.4 dedup key (MAP 3.5 owns the 24 h TTL lookup; the key is
    # recorded now: prompt hash + tools/ctx/priority — NOT --out).
    dedup_key = json.dumps(
        {
            "sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
            "tools": parsed["tools"],
            "ctx": parsed["ctx"],
            "priority": parsed["priority"],
        },
        sort_keys=True,
    )
    deadline_s = (
        parsed["deadline"]
        if parsed["deadline"] is not None
        else float(cfg["task_default_deadline_s"])
    )
    now = time.time()
    rec = {
        "task_id": task_id,
        "state": "queued",
        "instance": instance_i,
        "port": port,
        "result_file": result_file,
        "error": None,
        "detail": None,
        "prompt": prompt,
        "dedup_key": dedup_key,
        "tools": parsed["tools"],
        "ctx_need": parsed["ctx"],
        "priority": parsed["priority"],
        "deadline_s": deadline_s,
        "created_ts": now,
        "worker_pid": None,
        "instances_tried": [],
        "updated_ts": now,
    }
    write_task(d, rec)
    # P0 #1 (G3 PR#12 r1): the fast path is a CLAIM, so it runs under
    # the slot-claim lock (claim_own_task). A None result = the slot
    # was taken by a concurrent claim (or the instance drifted
    # un-routable mid-spawn) -> the queue-wait loop below.
    claimed_rec, promoted = claim_own_task(d, doc, task_id)
    if claimed_rec is not None:
        if promoted:
            start_worker(
                d, claimed_rec, log=lambda m: print(f"bonsai2: {m}", file=sys.stderr)
            )
        fresh = read_task(d, task_id)
        if fresh.get("state") == "running":
            return emit(
                spawn_json(fresh),
                EXIT_OK,
                f"task {task_id} running on i={fresh.get('instance')}",
            )
    # The S3.3 queue-wait loop: poll the claim protocol until this task
    # is running or queue_wait_max_s expires (S3.2: 8 = the manager-side
    # wait budget; the task's own --deadline is the worker's, S3.4).
    # P1 #4 (G3 PR#12 r1): the health document is RE-READ every poll —
    # the manager may have re-written it mid-wait (an instance bdown,
    # ctx halved, slots_total changed), and the claim pass must route
    # on the live document, not the one from spawn entry.
    wait_deadline = time.monotonic() + float(cfg["queue_wait_max_s"])
    while True:
        try:
            doc = load_health_doc(env=env)
        except HealthMissing:
            pass  # the manager dropped the document mid-wait — keep the last good one
        claimed = claim_slot(d, doc)
        if claimed is not None:
            start_worker(
                d, claimed, log=lambda m: print(f"bonsai2: {m}", file=sys.stderr)
            )
        fresh = read_task(d, task_id)
        if fresh.get("state") == "running":
            return emit(
                spawn_json(fresh),
                EXIT_OK,
                f"task {task_id} running on i={fresh.get('instance')}",
            )
        if time.monotonic() >= wait_deadline:
            fresh["detail"] = (
                f"queue wait ({cfg['queue_wait_max_s']} s) expired — no free slot on i={instance_i}"
            )
            write_task(d, fresh)
            return emit(spawn_json(fresh), EXIT_QUEUE_WAIT, fresh["detail"])
        time.sleep(poll_s)


# --------------------------------------------------------------------------
# entry-point dispatch (the thin deploy files call this)
# --------------------------------------------------------------------------
def dispatch(name: str, argv: list[str], env: dict | None = None) -> int:
    if name == "bonsai2-status":
        return cmd_status(argv, env)
    if name == "bonsai2-list-instances":
        return cmd_list_instances(argv, env)
    if name == "bonsai2-health":
        return cmd_health(argv, env)
    if name == "bonsai2-spawn":
        return cmd_spawn(argv, env)
    raise SystemExit(f"bonsai2_harness.dispatch: unknown command {name!r}")


if __name__ == "__main__":
    sys.exit(dispatch(Path(sys.argv[0]).name, sys.argv[1:]))
