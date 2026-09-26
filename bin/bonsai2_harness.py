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

# Exit-code mapping for THIS PR's commands (S3.2 closed set, per command):
#  * status / list-instances / health: 0, 82 (main-endpoint probe fails),
#    84 (health.json absent/invalid); health adds no main probe (it dumps
#    the manager's raw document). status/load-config also 2 on malformed
#    harness.yaml (the S3.2 "malformed config" case).
#  * spawn: 0 (accepted — running, or promoted out of the queue in time),
#    2 (malformed args / malformed config), 3 (routing candidate
#    bfailed=ERR_PORT_IN_USE or bdown=ERR_INSTANCE_UNHEALTHY at
#    submission — S4.5: wait one health cycle, re-spawn), 4 (routing
#    candidate bfailed with an S1.4 engine-level bfailed_reason
#    [ERR_VRAM_OOM / ERR_HASH* / ERR_DOWNLOAD*] at submission — the S1.5
#    engine-level failure, S4.4: requeue on another or run on main),
#    5 (no instance ctx fits the requested --ctx, ERR_CTX_TOO_LONG, S1.5),
#    8 (queue_wait expired, state "queued"), 80 (main-only mode: the S2.4
#    {"reason": ...} JSON echoing health.json), 82 (main endpoint
#    unhealthy), 84 (health.json absent/invalid).

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
import re
import secrets
import shutil
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

# --------------------------------------------------------------------------
# S3.2 closed set (the single source — S3.8 #3: any other exit code from a
# S3.2 script is a harness bug). The set is LOCKED at exactly these 12
# values (S3.2 'Closed set: {0, 2, 3, 4, 5, 8, 20, 21, 22, 80, 82, 84} —
# 12 codes'); EXIT_CLOSED_SET is asserted against that literal by the
# closed-set suite (MAP 3.9 / S3.8 #3) so a stray code cannot be added
# here unnoticed, and `emit` enforces it at every emit site.
# --------------------------------------------------------------------------
EXIT_OK = 0
EXIT_ARGS = 2
EXIT_PORT = 3
EXIT_ENGINE = 4
EXIT_CTX = 5
EXIT_QUEUE_WAIT = 8
EXIT_MAIN_ONLY = 80
EXIT_MAIN_DOWN = 82
EXIT_MANAGER_DOWN = 84
EXIT_POOL_WAIT = 84  # S3.2 unload/reload: manager down == its own 84 (same code, alias for readability)
EXIT_CLOSED_SET = frozenset({0, 2, 3, 4, 5, 8, 20, 21, 22, 80, 82, 84})

# S3.2 per-command exit mapping (the locked "Per-command exit mapping"
# block under the S3.2 exit-code table). This is the DECLARATIVE record of
# which closed-set codes each S3.2 command may emit — the single source the
# MAP 3.9 closed-set test asserts against (S3.8 #3: "a test asserts no
# stray code can be emitted"):
#   * the closed set is exactly the S3.2 literal (no drift — asserted);
#   * every command's allowed set is a SUBSET of the closed set (no stray
#     code);
#   * every S3.2 command has a mapping (the 9 commands dispatch() implements
#     today; `bonsai2-bench`'s 0/2 subset is S3.5/S5.4 and lands in P4/MAP
#     4.7 — declared there, not wired in dispatch() yet).
# `emit` still enforces the GLOBAL closed set at every emit site (a stray
# code anywhere is a ValueError, not a silent exit-1); this table is what
# pins the PER-COMMAND subset so a command cannot grow a code its S3.2 row
# does not allow.
COMMAND_EXIT_CODES: dict[str, frozenset[int]] = {
    # 0 accepted, 2 args/config, 3 bfailed(port)/bdown, 4 bfailed(engine),
    # 5 ctx too long, 8 queue_wait, 80 main-only, 82 main down, 84 mgr down
    "bonsai2-spawn": frozenset({0, 2, 3, 4, 5, 8, 80, 82, 84}),
    # 0 accepted/state-change, 2 unknown task/args, 8 --wait no state change
    "bonsai2-steer": frozenset({0, 2, 8}),
    # 0 accepted/terminal no-op, 2 unknown task/args
    "bonsai2-cancel": frozenset({0, 2}),
    # 0 ok, 2 args/config, 82 main down, 84 health missing
    "bonsai2-status": frozenset({0, 2, 82, 84}),
    "bonsai2-list-instances": frozenset({0, 2, 82, 84}),
    # 0 ok, 2 args, 84 health missing (no main probe — the raw doc dump)
    "bonsai2-health": frozenset({0, 2, 84}),
    # 20 done, 21 failed, 22 timeout, 0 non-terminal poll, 2 unknown task
    "bonsai2-collect": frozenset({0, 2, 20, 21, 22}),
    # 0 done (incl. no-op / partial), 2 args, 84 manager down
    "bonsai2-unload": frozenset({0, 2, 84}),
    "bonsai2-reload": frozenset({0, 2, 84}),
}


def command_allowed_codes(command: str) -> frozenset[int]:
    """The closed-set codes `command` may emit (the S3.2 per-command
    mapping). KeyError when the command has no S3.2 mapping (a non-S3.2
    command, or a new command the mapping has not been extended for —
    the closed-set test uses this to catch the latter)."""
    if command not in COMMAND_EXIT_CODES:
        raise KeyError(f"no S3.2 exit-code mapping for {command!r}")
    return COMMAND_EXIT_CODES[command]


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
    # --- subagent capability layer (S3.4 tool loop; the 2026-09-25
    # spec amendment's S3.6 block, MAP 3.15) ---
    "tool_execution": "host",  # host | engine (S3.4 dual-mode)
    "tool_python_bin": "/usr/bin/python3",  # the python tool's interpreter
    "tool_timeout_s": 60,  # per-tool subprocess timeout
    "tool_output_max_kb": 32,  # tool output truncation (S3.1/S3.4)
    "task_max_steps": 32,  # default --max-steps (range 1-128)
    "tool_invalid_max": 3,  # consecutive bad calls -> ERR_TOOL_INVALID_CALL
    "workspace_dir": "/run/bonsai2/workspaces",  # per-task scratch workdirs
}
_INT_KEYS = (
    "dedup_ttl_h",
    "queue_wait_max_s",
    "instance_healthy_timeout_s",
    "task_default_deadline_s",
    "main_tps_guardrail_pct",
    "tool_timeout_s",
    "tool_output_max_kb",
    "task_max_steps",
    "tool_invalid_max",
)

# S3.4 priority ranks (lower = earlier; S3.3 "high jumps the queue").
PRIORITY_RANK = {"high": 0, "normal": 1, "low": 2}

# S3.1 task states (locked set, S3.1 schema).
TERMINAL_STATES = ("done", "failed", "timeout")
CHARGED_STATES = ("queued", "running")  # S3.4 whole-lifetime slot charge
ROUTABLE_STATES = ("healthy", "degraded")

# S3.4 reaper: the states a worker OWNS (it has started; a pid exists). The
# reaper only touches these — the claimer only touches `queued`, so the two
# never race on the same record. A terminal record is already free.
REAPABLE_STATES = ("running", "engine_retried")

# S3.4 deadline backstop grace: the worker's per-chunk socket cap (60 s) + a
# margin. The reaper marks an ALIVE worker's record timed out only when the
# task's own --deadline is exceeded by MORE than this — the worker is the
# deadline authority between its attempts; the reaper is the backstop for a
# single hung attempt (S3.4 "manager enforces; on expiry the instance gets an
# abort, task -> timeout, slot freed").
REAP_DEADLINE_GRACE_S = 65.0


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
    the SPEC example), from a task record (the record carries extras).
    `workdir` is the S3.2/S3.1 capability-layer field (the 2026-09-25
    amendment): the resolved workdir the task runs in — present for
    post-capability-layer records, absent for older ones (the S3.1
    schema is open to new fields)."""
    out = {
        "task_id": rec.get("task_id"),
        "state": rec.get("state"),
        "instance": rec.get("instance"),
        "port": rec.get("port"),
        "result_file": rec.get("result_file"),
        "error": rec.get("error"),
        "detail": rec.get("detail", detail),
    }
    # G3 PR#18 r6 P1: emit `workdir` only for `--tools on` records, so a
    # `--tools off` spawn JSON matches the pre-loop schema byte-for-byte
    # (the amendment's "byte-identical to the pre-loop behavior"
    # constraint — the S3.1 wire/result surface). The RECORD still stores
    # workdir for off tasks (it is part of the S3.4 dedup key — "same
    # prompt in two workdirs is two different tasks" holds regardless of
    # the loop flag), and collect on an off task reports workdir=null
    # (the S3.1 amendment: "tool_log is [] for --tools off tasks").
    # G3 PR#18 r7 P2 (noted): this creates an asymmetric surface on purpose
    # — the off-task SPAWN JSON omits workdir (pre-loop schema), while the
    # off-task COLLECT payload returns it (read_result defaults to
    # rec.get("workdir"), which is set for off tasks; collect is the
    # S3.1 "verification entry point" and the amendment's collect payload
    # is {answer, reasoning, usage, tool_log, steps, workdir} — the
    # off-task workdir is the harness-created scratch dir, useful info,
    # not part of the pre-loop wire surface). No fix: both sides are
    # consistent with their own spec clauses.
    if rec.get("tools") == "on" and rec.get("workdir") is not None:
        out["workdir"] = rec["workdir"]
    return out


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
        "tool_python_bin",
        "workspace_dir",
    ):
        if key in data:
            val = data[key]
            if not isinstance(val, str) or not val:
                raise ConfigError(
                    f"ERR_CONFIG: harness.yaml: {key} must be a non-empty string"
                )
            cfg[key] = val
    # S3.4 dual-mode flag: a string with a LOCKED value set (same
    # discipline as the manager's kv_cache, S1.6 delta 2026-09-23).
    if "tool_execution" in data:
        val = data["tool_execution"]
        if not isinstance(val, str) or val not in ("host", "engine"):
            raise ConfigError(
                f"ERR_CONFIG: harness.yaml: tool_execution must be host|engine "
                f"(got {val!r})"
            )
        cfg["tool_execution"] = val
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


# S3.2/S3.6 (the 2026-09-25 spec amendment): per-task scratch workdirs are
# "rotated with the 7-day results window". Rotation = prune
# <workspace_dir>/<task_id> subdirs whose mtime is older than 7 days.
# Best-effort (a failed prune never blocks a spawn; the dir is scratch).
WORKSPACE_ROTATION_AGE_S = 7 * 24 * 3600


def _newest_mtime_under(p: Path, now: float) -> float:
    """The most-recent mtime among `p` and all its descendants. Returns 0.0
    if any stat fails (best-effort; the caller treats 0.0 as "unknown ->
    keep"). Used by rotate_workspaces so a workdir whose FILES are fresh but
    whose top-level dir mtime is stale (e.g. a file written in place, which
    does not bump the parent dir's mtime) is still kept."""
    newest = 0.0
    try:
        newest = p.stat().st_mtime
    except OSError:
        return 0.0
    try:
        for sub in p.rglob("*"):
            try:
                m = sub.stat().st_mtime
            except OSError:
                continue
            newest = max(newest, m)
    except (OSError, ValueError):
        pass
    return newest


def rotate_workspaces(
    workspace_dir, env: dict | None = None, now: float | None = None
) -> int:
    """Prune per-task workdirs older than the 7-day window. Returns the
    number of subdirs removed. A missing/empty dir is a no-op (0). Only
    DIRECTORY children are candidates (stray files are left alone); a
    subdir whose most-recent FILE mtime (any descendant) is within the
    window is kept. Best-effort: an OSError on any candidate is skipped
    (never raised).

    G3 PR#18 r4 P0: the original implementation keyed off the top-level
    dir's OWN mtime only. In-place file writes do not bump a directory's
    mtime (verified: modifying a file leaves the parent's mtime unchanged),
    so a workdir whose files were written at creation time and then only
    updated in place would carry a stale dir mtime and be pruned after 7
    days even though its files were fresh — contradicting this docstring's
    "most-recent file mtime" promise. Now the newest mtime over the whole
    subtree decides.
    """
    root = Path(workspace_dir)
    if not root.is_dir():
        return 0
    now = time.time() if now is None else now
    removed = 0
    try:
        children = list(root.iterdir())
    except OSError:
        return 0
    # G3 PR#18 r5 P2 (noted): _newest_mtime_under runs rglob("*") per
    # candidate workdir, which is O(total files in the workspace tree) per
    # spawn. It runs ONCE per cmd_spawn invocation (at the top, outside any
    # lock — see G3 PR#18 r4 P1), so the cost is a one-shot full-tree stat
    # scan per spawn, not per tool step. On a busy workspace_dir this is a
    # per-spawn I/O cost; if it ever shows up in a load test, the next
    # optimization is to throttle to once-per-process (cache the last
    # rotation ts) rather than once-per-spawn. No behavior change.
    for child in children:
        if not child.is_dir():
            continue
        newest = _newest_mtime_under(child, now)
        if newest == 0.0:
            # could not stat anything — leave it alone (best-effort)
            continue
        if now - newest > WORKSPACE_ROTATION_AGE_S:
            try:
                shutil.rmtree(child, ignore_errors=True)
                removed += 1
            except OSError:
                continue
    return removed


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
      * ERR_ENGINE_OOM -> the routing candidate is bfailed with
        `bfailed_reason` an S1.4 engine-level cause (OOM/download/hash —
        the S3.2 exit-4 condition at submission);
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
        reason = cand.get("bfailed_reason")
        if isinstance(reason, str) and reason.startswith(
            ("ERR_VRAM_OOM", "ERR_HASH", "ERR_DOWNLOAD")
        ):
            # S1.5: the engine-level bfailed causes (OOM/download/hash)
            # are the S3.2 exit-4 condition at submission — the instance
            # is bfailed AND the manager recorded WHY (an S1.4 engine
            # cause), so the main agent's S4.4 4-path applies: check
            # status, requeue on another or run on main.
            return (
                cand,
                "ERR_ENGINE_OOM",
                (
                    f"routing candidate i={cand.get('i')} is bfailed ({reason} — "
                    "engine-level failure: OOM/download/hash, S1.5 — check status, "
                    "requeue on another instance or run on main)"
                ),
            )
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
        rec["running_ts"] = time.time()
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
            # S3.4 reaper: the task's running clock starts on first
            # promotion (the whole-lifetime --deadline, S3.4 — a retry
            # keeps the original start, so the deadline spans retries).
            head.setdefault("running_ts", time.time())
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
# S3.4 reaper (stale running records + the deadline backstop)
# --------------------------------------------------------------------------
def _pid_alive(pid) -> bool:
    """`os.kill(pid, 0)` liveness. A dead/missing pid is False; the
    reaper treats pid=None (a record that was running before the worker
    was started, or a pre-3.4 record) as a live-but-unverifiable pid
    ONLY for the deadline backstop — the pid-dead rule needs a pid."""
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # pid exists, not ours
    except OSError:
        return False
    return True


def reap_stale(
    d: dict, *, grace_s: float = REAP_DEADLINE_GRACE_S, log=print
) -> list[str]:
    """S3.4 reaper: one pass over the task records that the WORKER owns.

    Two rules, both freeing the slot (S3.4 "one slot per task for its
    lifetime; cancel/deadline/terminal frees it"):
      * pid-dead: a REAPABLE record (`running` / `engine_retried`) whose
        `worker_pid` is missing or dead -> the worker died without
        updating its record -> `failed` / `ERR_ENGINE_DOWN` + slot freed.
        This is the "stale running record" the worker docstring defers to
        the Phase-3 reaper ("a crashed worker leaves its record
        running... the Phase-3 reaper (MAP 3.4) turns a stale running
        record into failed/ERR_ENGINE_DOWN").
      * deadline backstop: a REAPABLE record whose worker pid is ALIVE but
        `running_ts + deadline_s + grace_s < now` -> the worker is stuck in
        a single attempt past the task's own --deadline beyond the
        per-chunk socket cap + grace -> `timeout` / `ERR_DEADLINE` + slot
        freed (S3.4 "manager enforces; on expiry the instance gets an
        abort, task -> timeout, slot freed").

    `queued` records are NOT reaped here (the claimer owns them — a queued
    record has no worker and its own queue_wait budget is the spawn's,
    S3.3). Terminal records are free already. Each write is the atomic
    tmp+rename (`write_task`). Idempotent: a record already reaped is
    terminal and skipped. Returns the reaped task_ids (order = record
    order). The pass is ONE iter_tasks scan (the round-2/3 O(N_tasks)
    scaling-hazard fix, named for MAP 3.4: the reaper must not re-glob +
    re-parse the task dir per record).
    """
    now = time.time()
    reaped: list[str] = []
    for rec in iter_tasks(d):
        if rec.get("state") not in REAPABLE_STATES:
            continue
        pid = rec.get("worker_pid")
        deadline_s = rec.get("deadline_s")
        running_ts = rec.get("running_ts")
        pid_gone = pid is not None and not _pid_alive(pid)
        deadline_gone = (
            running_ts is not None
            and isinstance(deadline_s, (int, float))
            and not isinstance(deadline_s, bool)
            and now > (float(running_ts) + float(deadline_s) + float(grace_s))
        )
        if not pid_gone and not deadline_gone:
            continue
        prior = rec.get("state")
        rec["state"] = "timeout" if deadline_gone else "failed"
        rec["error"] = "ERR_DEADLINE" if deadline_gone else "ERR_ENGINE_DOWN"
        rec["detail"] = (
            f"reaper: worker pid {pid} dead — record left {prior!r}; slot freed (S3.4)"
            if pid_gone and not deadline_gone
            else f"reaper: deadline {deadline_s} s exceeded by more than the {grace_s:.0f} s grace — hung attempt; slot freed (S3.4)"
        )
        rec["updated_ts"] = now
        write_task(d, rec)
        reaped.append(rec["task_id"])
        log(f"reaper: {rec['task_id']} -> {rec['state']} ({rec['error']})")
    return reaped


# --------------------------------------------------------------------------
# S3.4 spawn idempotency (dedup, 24 h TTL)
# --------------------------------------------------------------------------
def _dedup_key_hash(dedup_key: str) -> str:
    """A filesystem-safe, fixed-width name for the dedup entry (the
    dedup key is a JSON string — hash it to one sha256 hex token)."""
    return hashlib.sha256(dedup_key.encode("utf-8")).hexdigest()


def dedup_entry_path(cfg: dict, dedup_key: str) -> Path:
    """The S3.4 24 h TTL dedup entry path (S3.6 `dedup_dir`):
    `<dedup_dir>/<sha256(dedup_key)>.json`. The entry is
    `{task_id, ts, dedup_key}` — the LIVE task the key maps to."""
    return Path(cfg["dedup_dir"]) / f"{_dedup_key_hash(dedup_key)}.json"


def dedup_lookup(cfg: dict, dedup_key: str, env: dict | None = None) -> dict | None:
    """The S3.4 dedup lookup: a LIVE task within the TTL whose key matches.

    Returns the referenced task record when the entry exists, is fresh
    (`now - ts < dedup_ttl_h`), and the referenced task is still non-terminal
    (queued/running/engine_retried) — else None (a terminal or expired task
    frees the key; the caller creates a fresh task). A missing/unreadable
    entry is None (the dedup dir is scratch under /run — a miss is not an
    error, just a new task). Read-only (the write happens after the caller
    creates the record, so two same-key spawns under the per-key lock each
    see the other's task and the second no-ops)."""
    path = dedup_entry_path(cfg, dedup_key)
    if not path.is_file():
        return None
    try:
        entry = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(entry, dict):
        return None
    ts = entry.get("ts")
    ttl_h = float(cfg["dedup_ttl_h"])
    if not isinstance(ts, (int, float)) or isinstance(ts, bool):
        return None
    if time.time() - float(ts) >= ttl_h * 3600.0:
        return None
    task_id = entry.get("task_id")
    if not isinstance(task_id, str) or not task_id:
        return None
    d = results_dirs(cfg)
    try:
        rec = read_task(d, task_id)
    except TaskNotFound:
        return None
    if rec.get("state") in TERMINAL_STATES:
        return None
    return rec


def dedup_record(cfg: dict, dedup_key: str, task_id: str) -> None:
    """Write the S3.4 dedup entry for a freshly-created task (the caller
    holds the per-key lock, so a same-key spawn cannot slip in between the
    lookup and this write).

    The dedup dir is SCRATCH under /run (S3.4: 'a miss is not an error,
    just a new task') — so an unwritable/absent dir degrades to a missed
    dedup, not a failed spawn. The task record is already written by the
    time this runs; sinking the whole submission over a bookkeeping entry
    would be the wrong trade. An OSError here is swallowed (the next
    same-key spawn simply creates a fresh task instead of no-op-ing)."""
    path = dedup_entry_path(cfg, dedup_key)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        entry = {"task_id": task_id, "ts": time.time(), "dedup_key": dedup_key}
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(entry, indent=2) + "\n")
        os.replace(tmp, path)
    except OSError:
        # scratch dir unavailable (e.g. /run/bonsai2 not yet created on a
        # cold boot, or the dir is unwritable) — the task proceeds without
        # the idempotency entry. Never raises: a spawn must not fail here.
        return


# --------------------------------------------------------------------------
# S3.2 task-result read (collect's answer/reasoning/usage)
# --------------------------------------------------------------------------
def read_result(rec: dict) -> dict:
    """The S3.2 collect payload fields `answer` / `reasoning` / `usage` +
    the S3.1 capability-layer fields `tool_log` / `steps` / `workdir`
    (the 2026-09-25 amendment's result-file shape: the taskworker writes
    {answer, reasoning, usage, tool_log, steps, workdir}). Absent/invalid
    file -> the answer/reasoning/usage are null AND tool_log/steps are
    the S3.2 no-file defaults (`tool_log is [] for --tools off tasks`,
    `steps` null when the file is absent) — a failed/timeout task may not
    have written it; collect still returns the task's terminal state +
    code. `workdir` comes from the RECORD (the S3.1 'main's verification
    entry point') when the record carries it; the result file echoes it
    for post-capability-layer records."""
    out = {
        "answer": None,
        "reasoning": None,
        "usage": None,
        "tool_log": [],
        "steps": None,
        "workdir": rec.get("workdir"),
    }
    rf = rec.get("result_file")
    if not isinstance(rf, str) or not rf:
        return out
    try:
        data = json.loads(Path(rf).read_text())
    except (OSError, json.JSONDecodeError):
        return out
    if isinstance(data, dict):
        for k in ("answer", "reasoning", "usage", "tool_log", "steps", "workdir"):
            if k in data:
                out[k] = data[k]
    return out


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
    [--deadline SECS] [--max-steps N] [--workdir DIR]
    [--out FILE] [--priority low|normal|high]

    `--tools` defaults ON (the S3.4 agentic loop; the 2026-09-25 spec
    amendment). `--max-steps` defaults to the S3.6 `task_max_steps`
    (range 1-128, enforced here). `--workdir` must exist + be writable
    (validated at spawn; when omitted the harness creates
    <workspace_dir>/<task_id> at claim time, S3.2/S3.6)."""
    args = {
        "prompt": None,
        "prompt_inline": False,
        "tools": "on",
        "ctx": None,
        "deadline": None,
        "max_steps": None,
        "workdir": None,
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
        if a == "--max-steps":
            # G3 PR#18 r3 P0: `isdigit()` accepts leading zeros ("007" ->
            # 7) and "000" -> 0 (range check rejects it) — inconsistent
            # with the sibling --ctx/numeric handling. Use a strict
            # `^[1-9][0-9]*$` so "007" is rejected outright, not silently
            # coerced to 7.
            tok = argv[i + 1] if i + 1 < len(argv) else ""
            if not re.fullmatch(r"[1-9][0-9]*", tok):
                return "ERR_ARGS: --max-steps must be a positive integer"
            v = int(tok)
            # G3 PR#18 r4 P2 (noted): a huge value ("999999999999") passes
            # the positive-integer gate and is rejected by the 1-128 range
            # check below with "must be in 1-128" — that message is the
            # accurate one (the value IS in the right shape, just too
            # large), so the two branches stay distinct on purpose.
            if not (1 <= v <= 128):
                return "ERR_ARGS: --max-steps must be in 1-128 (S3.4)"
            args["max_steps"] = v
            i += 2
            continue
        if a == "--workdir":
            if i + 1 >= len(argv):
                return "ERR_ARGS: --workdir needs a DIR"
            args["workdir"] = Path(argv[i + 1]).expanduser()
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
    0 accepted, 2 malformed args/config, 3 candidate bfailed(bport)/bdown,
    4 candidate bfailed with an S1.4 engine-level cause (S1.5),
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
    # S3.2/S3.6 (the 2026-09-25 spec amendment): rotate the per-task
    # workdirs with the 7-day window. G3 PR#18 r4 P1: this was originally
    # called INSIDE the per-key dedup lock, holding that hot lock across an
    # O(N) filesystem scan of the whole workspace tree on every spawn. It
    # only prunes subdirs older than 7 days, so it is safe to run once
    # here, before any lock is taken — never holds the dedup lock during I/O.
    # Best-effort (a failed prune never blocks the spawn).
    try:
        rotate_workspaces(cfg["workspace_dir"])
    except Exception:  # noqa: BLE001, S110 — rotation is best-effort
        pass
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
    # Resolve the prompt CONTENT now (the S3.4 dedup key is the content
    # hash — for -p that is the file content, not the path argument).
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
    # MAP 3.5 / S3.4 spawn idempotency (dedup): the dedup key is the prompt
    # content hash + --tools/--ctx/--priority (NOT --out — it only selects
    # where the result file is written). The CLAIM + record happen under a
    # per-key lock at record creation (below); after releasing it, the
    # entry is RE-READ: if a concurrent same-key spawn's entry replaced it
    # in the gap, this spawn no-ops to that task (no double slot).
    dedup_key = json.dumps(
        {
            "sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
            "tools": parsed["tools"],
            "ctx": parsed["ctx"],
            "priority": parsed["priority"],
            # S3.4 (the 2026-09-25 spec amendment, A4): --workdir is part of
            # "everything that determines what the task will do" — same
            # prompt in two workdirs is two different tasks.
            "workdir": str(parsed["workdir"])
            if parsed["workdir"] is not None
            else None,
        },
        sort_keys=True,
    )
    target, err, detail = route_target(doc, d, parsed["ctx"])
    if err:
        if err == "ERR_CTX_TOO_LONG":
            code = EXIT_CTX
        elif err == "ERR_ENGINE_OOM":
            code = (
                EXIT_ENGINE  # S1.5: bfailed with an engine-level cause -> S3.2 exit 4
            )
        else:
            code = EXIT_PORT
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
    # G3 PR#12 r3 P2: a NON-numeric string port must not escape as an
    # uncaught ValueError (Python exit 1, outside the S3.2 closed set) —
    # it is a malformed MANAGER document, so exit 84 (the
    # HealthMissing family), not 2 (2 = the harness's own config).
    port = target.get("port", ident.get("port_base", 30101) + instance_i)
    try:
        port = int(port)
    except (TypeError, ValueError):
        return emit(
            {
                "error": "ERR_MANAGER_DOWN",
                "detail": (
                    f"health.json: instance i={instance_i} port {port!r} is not "
                    "numeric (malformed document)"
                ),
            },
            EXIT_MANAGER_DOWN,
            f"health.json port for i={instance_i} not numeric: {port!r}",
        )
    # G3 PR#15 r1 P1-1: validate/CREATE the --out target BEFORE taking
    # the per-key dedup lock — an exit-2 from inside the lock released
    # the dedup lock while the aborted spawn was still unfixed, letting
    # a same-key spawn create a second record in the gap.
    out_path: Path | None = None
    if parsed["out"] is not None:
        op: Path = parsed["out"].expanduser().resolve()
        if op.parent.is_file():
            return emit(
                {
                    "error": "ERR_ARGS",
                    "detail": f"--out {op} : parent is a file",
                },
                EXIT_ARGS,
                f"--out {op}: parent is a file",
            )
        # P1 #5 (G3 PR#12 r1): validate/CREATE the --out parent at
        # spawn time (the worker used to mkdir it at write time, so
        # a --out under an impossible deep path was only discovered
        # mid-task). An OSError here -> a spawn-time rejection, exit 2.
        try:
            op.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            return emit(
                {"error": "ERR_ARGS", "detail": f"--out {op}: {exc}"},
                EXIT_ARGS,
                f"--out {op}: {exc}",
            )
        out_path = op
    # S3.2 (the 2026-09-25 spec amendment, A4): --workdir must exist and
    # be writable at SPAWN time (the tool loop's confinement root; an
    # impossible dir is a spawn-time rejection, exit 2 — same discipline
    # as the --out parent above). When omitted, the harness creates
    # <workspace_dir>/<task_id> at record-creation time (S3.6
    # workspace_dir, default /run/bonsai2/workspaces/).
    workdir_explicit: Path | None = None
    if parsed["workdir"] is not None:
        wd = parsed["workdir"].expanduser().resolve()
        if not wd.is_dir():
            return emit(
                {
                    "error": "ERR_ARGS",
                    "detail": f"--workdir {wd}: not a directory (must exist, S3.2)",
                },
                EXIT_ARGS,
                f"--workdir {wd}: not a directory",
            )
        # G3 PR#18 r3 P2 (noted, no behavior change): this validates the
        # explicit --workdir under the CURRENT uid at spawn, and the
        # taskworker runs as that same uid — so the check is exactly right
        # for the writer. (Asymmetry vs the auto-workdir, which the harness
        # creates 0o700 owner-only: an explicit --workdir may be owned by
        # another user but is writable by us, which is all the tools need.)
        if not os.access(wd, os.W_OK):
            return emit(
                {"error": "ERR_ARGS", "detail": f"--workdir {wd}: not writable"},
                EXIT_ARGS,
                f"--workdir {wd}: not writable",
            )
        workdir_explicit = wd
    # S3.4 / MAP 3.5: claim a task id + record + dedup entry under the
    # per-key lock so two concurrent same-key spawns cannot both create a
    # record (the one that loses the lock writes its record, then re-reads
    # the entry after releasing — and no-ops to the winner's live task).
    dedup_lock_name = "dedup-" + _dedup_key_hash(dedup_key)
    task_id: str | None = None
    with lock_wait(d["base"], dedup_lock_name):
        dup = dedup_lookup(cfg, dedup_key)
        if dup is not None:
            dup_winner = dup
        else:
            # dup is None — create the record + entry under the lock.
            # (A terminal entry under the key cannot reach the else side:
            # dedup_lookup filters terminal records out, so the re-read
            # below is the race detector — see G3 PR#15 r1 P1-3.)
            dup_winner = None
            task_id = new_task_id(d)
            if out_path is None:
                result_file = str(d["base"] / f"{task_id}.json")
            else:
                result_file = str(out_path)
            deadline_s = (
                parsed["deadline"]
                if parsed["deadline"] is not None
                else float(cfg["task_default_deadline_s"])
            )
            # S3.2/S3.6 (the 2026-09-25 spec amendment): the task's
            # workdir. Explicit --workdir is validated above; when
            # omitted the harness creates <workspace_dir>/<task_id>
            # NOW, under the per-key dedup lock (a concurrent same-key
            # spawn no-ops to this task and shares the dir — the key
            # already includes the workdir term, so two distinct
            # workdirs can never dedup onto each other).
            if workdir_explicit is not None:
                workdir = str(workdir_explicit)
            else:
                auto_wd = Path(cfg["workspace_dir"]) / task_id
                try:
                    # parents=True creates the workspace_dir root too; no
                    # separate parent mkdir (G3 PR#18 r2 P1: the explicit
                    # parent.mkdir was dead code — the child mkdir's
                    # parents=True already creates it). G3 PR#18 r3 P1:
                    # chmod the leaf 0o700 so a task's workdir is
                    # owner-only (defense-in-depth — every task here
                    # shares the runner uid, so this is belt-and-suspenders
                    # for the shared workspace_dir).
                    auto_wd.mkdir(parents=True, exist_ok=True)
                    os.chmod(auto_wd, 0o700)
                except OSError as exc:
                    # emit inside the lock is fine (the with-block releases
                    # it on return): no record exists yet, so the key is
                    # free for the caller's corrected spawn.
                    return emit(
                        {
                            "error": "ERR_ARGS",
                            "detail": f"cannot create workdir {auto_wd}: {exc}",
                        },
                        EXIT_ARGS,
                        f"workdir creation failed: {exc}",
                    )
                workdir = str(auto_wd)
            # S3.4 --max-steps: the flag value, else the S3.6
            # task_max_steps default (range 1-128, enforced at parse).
            # (rotate_workspaces was called here, inside the lock, until
            # G3 PR#18 r4 P1 moved it to the top of cmd_spawn — it no
            # longer runs under the per-key dedup lock, so a spawn no
            # longer holds that hot lock across an O(N) I/O scan of the
            # whole workspace tree.)
            max_steps = (
                parsed["max_steps"]
                if parsed["max_steps"] is not None
                else int(cfg["task_max_steps"])
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
                "workdir": workdir,
                "max_steps": max_steps,
                "created_ts": now,
                "worker_pid": None,
                "instances_tried": [],
                "updated_ts": now,
            }
            write_task(d, rec)
            dedup_record(cfg, dedup_key, task_id)
    # S3.4 / MAP 3.5: after the per-key lock, two outcomes:
    #  1. dup_winner is not None  -> a live task was found (or was created
    #     by a concurrent same-key spawn that held the lock first) -> return
    #     it, no new slot.
    #  2. dup_winner is None      -> we created the record under the lock;
    #     re-read the entry to detect a RACE (another spawn's entry replaced
    #     ours in the gap): if the key now maps to a different live task,
    #     no-op to that task.
    if dup_winner is not None:
        dup_fresh = read_task(d, dup_winner["task_id"])
        return emit(
            spawn_json(dup_fresh),
            EXIT_OK,
            f"dedup: duplicate of live task {dup_fresh['task_id']} "
            f"(same prompt hash + --tools/--ctx/--priority within {cfg['dedup_ttl_h']} h) — no new slot",
        )
    dup2 = dedup_lookup(cfg, dedup_key)
    if dup2 is not None and dup2.get("task_id") != task_id:
        # G3 PR#15 r1 P1-3: our entry was replaced by a concurrent
        # same-key spawn's (dedup_record's os.replace is not
        # compare-and-swap — a writer that lost the stale-lock race can
        # overwrite the entry in the gap after release). The entry now
        # points at the winner; OUR record is an orphan duplicate (both
        # would be charged = a double slot, S3.4). Two outcomes:
        #   * winner LIVE    -> the prompt is executing under the winner;
        #     ours is the loser: mark it failed/ERR_DUPLICATE (slot freed)
        #     and return the winner. The entry already points at the
        #     winner — leave it.
        #   * winner TERMINAL -> the entry is stale (its task just ended);
        #     OURS is the live task for this prompt: re-point the entry
        #     at us so the key tracks the live one (otherwise the next
        #     same-key spawn would create a third record) and return
        #     ours.
        winner = read_task(d, dup2["task_id"])
        if winner.get("state") not in TERMINAL_STATES:
            with lock_wait(d["base"], f"task-{task_id}"):
                ours = read_task(d, task_id)
                if ours.get("state") not in TERMINAL_STATES:
                    ours["state"] = "failed"
                    ours["error"] = "ERR_DUPLICATE"
                    ours["detail"] = (
                        f"dedup race: key now owned by live task "
                        f"{winner['task_id']!r}; this record is the loser"
                    )
                    ours["updated_ts"] = time.time()
                    write_task(d, ours)
            return emit(
                spawn_json(winner),
                EXIT_OK,
                f"dedup: duplicate of live task {winner['task_id']!r} "
                f"(raced a concurrent same-key spawn) — no new slot",
            )
        with lock_wait(d["base"], f"task-{task_id}"):
            ours = read_task(d, task_id)
            if ours.get("state") not in TERMINAL_STATES:
                ours["detail"] = (
                    f"dedup race: entry pointed at terminal task "
                    f"{winner['task_id']!r}; this task {task_id!r} "
                    "owns the key"
                )
                ours["updated_ts"] = time.time()
                write_task(d, ours)
        dedup_record(cfg, dedup_key, task_id)
        fresh = read_task(d, task_id)
        return emit(
            spawn_json(fresh),
            EXIT_OK,
            f"dedup: raced a concurrent same-key spawn; winner {winner['task_id']!r} "
            f"terminal — task {task_id} owns the key",
        )
    assert task_id is not None  # created under the lock (both return paths above exit)
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
    # MAP 3.4: the reaper runs every poll — a dead worker's slot is freed
    # and its queue position drained, so the queue actually forward-
    # progresses.
    wait_deadline = time.monotonic() + float(cfg["queue_wait_max_s"])
    while True:
        try:
            doc = load_health_doc(env=env)
        except HealthMissing:
            pass  # the manager dropped the document mid-wait — keep the last good one
        reap_stale(d, log=lambda m: print(f"bonsai2: {m}", file=sys.stderr))
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
# S3.2 command surface: collect / steer / cancel (MAP 3.6, 3.7)
# --------------------------------------------------------------------------
def _collect_json(rec: dict) -> dict:
    """The S3.2 collect payload: the task JSON (spawn shape) + the
    result-file fields `answer` / `reasoning` / `usage` (S3.2: 'read
    result file; task JSON + answer/reasoning/usage'). A terminal task
    with a result file carries the worker's written values; a missing
    result file (failed/timeout before the write, or the write target
    was moved) carries nulls — the state + error are the verdict."""
    out = spawn_json(rec)
    out.update(read_result(rec))
    return out


def cmd_collect(argv: list[str], env: dict | None = None) -> int:
    """`bonsai2-collect <TASK_ID>` (S3.2; MAP 3.6).

    Exit codes (the S3.2 per-command mapping, locked):
      * 20 = the task is `done` — the result payload is the verdict;
      * 21 = `failed`; 22 = `timeout` (the task's terminal state);
      * 0  = non-terminal (`queued`/`running`/`engine_retried`) — the
             current-state JSON is returned; the caller polls (S3.2:
             '0 = task non-terminal, current-state JSON returned
             (caller polls)');
      * 2  = TASK_ID unknown (S3.2: '2 = TASK_ID unknown').
    The command never touches the engine — it reads the task record +
    the result file, so it works on a dead stack (the record is the
    source of truth, S3.1)."""
    try:
        cfg = load_config(env)
    except ConfigError as exc:
        return emit({"error": "ERR_CONFIG", "detail": str(exc)}, EXIT_ARGS, str(exc))
    if len(argv) != 1:
        return emit(
            {"error": "ERR_ARGS", "detail": "usage: bonsai2-collect <TASK_ID>"},
            EXIT_ARGS,
            "usage: bonsai2-collect <TASK_ID>",
        )
    task_id = argv[0]
    d = results_dirs(cfg)
    # MAP 3.4 (the PLAN's collect half of the reaper wiring): reap BEFORE
    # the read — a stale `running` record (dead worker pid) is turned
    # terminal here, so collect maps it to 21/22 instead of polling 0
    # forever. Idempotent; terminal records are skipped.
    reap_stale(d, log=lambda m: print(f"bonsai2: {m}", file=sys.stderr))
    try:
        rec = read_task(d, task_id)
    except TaskNotFound as exc:
        return emit(
            {
                "task_id": task_id,
                "state": None,
                "error": "ERR_TASK_UNKNOWN",
                "detail": str(exc),
            },
            EXIT_ARGS,
            str(exc),
        )
    payload = _collect_json(rec)
    state = rec.get("state")
    if state == "done":
        return emit(payload, 20, None)
    if state == "failed":
        return emit(payload, 21, None)
    if state == "timeout":
        return emit(payload, 22, None)
    return emit(payload, EXIT_OK, f"task {task_id} state {state!r} — poll again")


def cmd_steer(argv: list[str], env: dict | None = None, poll_s: float = 0.05) -> int:
    """`bonsai2-steer <TASK_ID> -m MESSAGE [--wait SECS]` (S3.2; MAP 3.7).

    Steering a subagent task: the v1 engine is a stateless fire-and-
    collect stream (S1.3), so the steer is ADVISORY — it is recorded on
    the task (`steer_messages`, append-only) and its effect is bounded
    by what the worker observes: the worker re-reads its record between
    attempts, and a `queued` record that gets steered keeps its queue
    position (steering does not move slots, S3.4). The steer-and-wait
    window is the S3.2 exit-8 shape: no state change within SECS -> 8
    (the task is still running/queued; the manager-side wait budget).

    Exit codes (the S3.2 per-command mapping, locked):
      * 0  = operation accepted (a non-terminal task got the steer
             recorded; with `--wait`, a state change was observed
             within the window);
      * 8  = `--wait SECS` expired with no state change (the task is
             still running or queued — S3.2: '8 = no state change
             within SECS');
      * 2  = TASK_ID unknown / malformed args."""
    try:
        cfg = load_config(env)
    except ConfigError as exc:
        return emit({"error": "ERR_CONFIG", "detail": str(exc)}, EXIT_ARGS, str(exc))
    parsed = _parse_steer_args(argv)
    if isinstance(parsed, str):
        return emit({"error": "ERR_ARGS", "detail": parsed}, EXIT_ARGS, parsed)
    task_id, message, wait_s = parsed
    d = results_dirs(cfg)
    try:
        rec = read_task(d, task_id)
    except TaskNotFound as exc:
        return emit(
            {
                "task_id": task_id,
                "state": None,
                "error": "ERR_TASK_UNKNOWN",
                "detail": str(exc),
            },
            EXIT_ARGS,
            str(exc),
        )
    now = time.time()
    with lock_wait(d["base"], f"task-{task_id}"):
        rec = read_task(
            d, task_id
        )  # re-read under the lock (a worker may be mid-write)
        if rec.get("state") in TERMINAL_STATES:
            # G3 PR#15 r1 P1-3: S3.2 lists exit 0 as "steer on a
            # NON-TERMINAL task" — a terminal steer is the same family as
            # "unknown task" (2): the operation has no effect, the caller
            # should collect instead. (Previously: recorded + exit 0 with
            # a "already done" detail.)
            return emit(
                {
                    "task_id": task_id,
                    "state": rec.get("state"),
                    "error": "ERR_TASK_TERMINAL",
                    "detail": (
                        f"task {task_id} is already {rec.get('state')!r} — "
                        "steer applies to a non-terminal task (S3.2)"
                    ),
                },
                EXIT_ARGS,
                f"steer on terminal task {task_id}",
            )
        rec.setdefault("steer_messages", []).append({"message": message, "ts": now})
        rec["updated_ts"] = now
        write_task(d, rec)
    if wait_s is None:
        return emit(
            spawn_json(rec), EXIT_OK, f"steer recorded on {task_id} (no --wait)"
        )
    # steer-and-wait: poll until the state changes or the window closes.
    start_state = rec.get("state")
    deadline = time.monotonic() + float(wait_s)
    while time.monotonic() < deadline:
        time.sleep(poll_s)
        try:
            fresh = read_task(d, task_id)
        except TaskNotFound:
            break
        if fresh.get("state") != start_state:
            return emit(
                spawn_json(fresh),
                EXIT_OK,
                f"steer: {task_id} {start_state!r} -> {fresh.get('state')!r} within {wait_s} s",
            )
    try:
        fresh = read_task(d, task_id)
    except TaskNotFound:
        # G3 PR#15 r1 P2-2: the record vanished mid-window (cancel +
        # reaper raced the poll) — the task is unknown now; exit 2, not
        # an uncaught TaskNotFound (exit 1, outside the S3.2 closed set).
        return emit(
            {
                "task_id": task_id,
                "state": None,
                "error": "ERR_TASK_UNKNOWN",
                "detail": f"task {task_id} vanished during the steer wait window",
            },
            EXIT_ARGS,
            f"steer --wait: task {task_id} gone",
        )
    return emit(
        spawn_json(fresh),
        EXIT_QUEUE_WAIT,
        f"steer: no state change on {task_id} within {wait_s} s (still {fresh.get('state')!r})",
    )


def cmd_cancel(argv: list[str], env: dict | None = None) -> int:
    """`bonsai2-cancel <TASK_ID> [--graceful]` (S3.2; MAP 3.7).

    SIGTERM the task's worker, SIGKILL after 5 s if it did not exit
    (the S3.2 mapping: 'cancel SIGTERM -> SIGKILL after 5 s'). A
    terminal record is an already-done no-op (S3.2: '0 = ... an
    already-terminal no-op'); a `queued` record (no worker yet) is
    marked `failed` / `ERR_CANCELLED` directly (nothing to signal).
    CANCEL FREES THE SLOT (S3.4) — the record leaves the charged
    states, so the claim pass can promote the next queued task.

    Exit codes: 0 = accepted (signal sent / already terminal);
    2 = TASK_ID unknown / malformed args."""
    try:
        cfg = load_config(env)
    except ConfigError as exc:
        return emit({"error": "ERR_CONFIG", "detail": str(exc)}, EXIT_ARGS, str(exc))
    if not argv:
        return emit(
            {
                "error": "ERR_ARGS",
                "detail": "usage: bonsai2-cancel <TASK_ID> [--graceful]",
            },
            EXIT_ARGS,
            "usage: bonsai2-cancel <TASK_ID> [--graceful]",
        )
    if argv[0] not in ("--graceful",):
        task_id = argv[0]
    else:
        return emit(
            {
                "error": "ERR_ARGS",
                "detail": "usage: bonsai2-cancel <TASK_ID> [--graceful]",
            },
            EXIT_ARGS,
            "usage: bonsai2-cancel <TASK_ID> [--graceful]",
        )
    d = results_dirs(cfg)
    try:
        rec = read_task(d, task_id)
    except TaskNotFound as exc:
        return emit(
            {
                "task_id": task_id,
                "state": None,
                "error": "ERR_TASK_UNKNOWN",
                "detail": str(exc),
            },
            EXIT_ARGS,
            str(exc),
        )
    state = rec.get("state")
    if state in TERMINAL_STATES:
        return emit(spawn_json(rec), EXIT_OK, f"{task_id} already {state!r} — no-op")
    now = time.time()
    pid = rec.get("worker_pid")
    if state == "queued" or not isinstance(pid, int) or pid <= 0 or not _pid_alive(pid):
        # no live worker to signal: mark the record failed directly
        # (the slot frees — S3.4 "cancel frees the slot").
        with lock_wait(d["base"], f"task-{task_id}"):
            rec = read_task(d, task_id)
            if rec.get("state") not in TERMINAL_STATES:
                rec["state"] = "failed"
                rec["error"] = "ERR_CANCELLED"
                rec["detail"] = (
                    f"cancelled before its worker started (was {state!r}) — slot freed (S3.4)"
                )
                rec["updated_ts"] = now
                write_task(d, rec)
        return emit(
            spawn_json(rec), EXIT_OK, f"cancelled queued/unstarted task {task_id}"
        )
    _signal_process_tree(pid, signum=signal.SIGTERM)
    deadline = time.monotonic() + CANCEL_KILL_GRACE_S
    while time.monotonic() < deadline:
        if not _pid_alive(pid):
            break
        time.sleep(0.05)
    # G3 PR#15 r1 P2-4: the post-grace SIGKILL goes to the WHOLE GROUP,
    # unconditionally — the wait loop above only tracks the leader pid,
    # so a child that outlived the leader (within the group) still gets
    # the SIGKILL here. On a fully-dead group the killpg is a
    # ProcessLookupError = no-op (the success case).
    _signal_process_tree(pid, signum=signal.SIGKILL)
    time.sleep(0.1)
    # the worker's SIGTERM handler marks the record itself when it can;
    # this is the belt-and-braces path for a worker that died between
    # its handler running and the record landing (or one that ignored
    # the signal entirely). Under the lock so a racing write cannot
    # clobber the terminal state.
    with lock_wait(d["base"], f"task-{task_id}"):
        rec = read_task(d, task_id)
        if rec.get("state") not in TERMINAL_STATES:
            rec["state"] = "failed"
            rec["error"] = "ERR_CANCELLED"
            rec["detail"] = (
                f"cancelled: worker pid {pid} signalled ({rec.get('state')!r} -> failed) — slot freed (S3.4)"
            )
            rec["updated_ts"] = time.time()
            write_task(d, rec)
    return emit(
        spawn_json(rec), EXIT_OK, f"cancelled {task_id} (worker pid {pid} signalled)"
    )


# --------------------------------------------------------------------------
# S3.2 command surface: unload / reload (pool control, MAP 3.8)
# --------------------------------------------------------------------------
def _control_dir() -> Path:
    """The manager's control-request queue: /run/bonsai2/control/
    (the manager's process_control reads it; S3.2 'Both are
    manager-mediated'). BONSAI2_HEALTH_DIR redirects the base (the
    hermetic hook — the manager writes health.json next to control/)."""
    return Path(os.environ.get("BONSAI2_HEALTH_DIR") or DEFAULT_HEALTH_DIR) / "control"


def _write_control_request(rtype: str, instances: list[int] | str) -> tuple[str, Path]:
    """Write one control-request file (the manager's S3.2 shape:
    `{"type": ..., "instances": ..., "request_id": ...}`). Returns
    (request_id, path)."""
    import uuid

    control = _control_dir()
    control.mkdir(parents=True, exist_ok=True)
    req_id = uuid.uuid4().hex
    req = {"type": rtype, "instances": instances, "request_id": req_id}
    path = control / f"{rtype}-{req_id}.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(req, indent=2) + "\n")
    os.replace(tmp, path)
    return req_id, path


def _wait_control_ack(
    req_id: str, timeout_s: float, poll_s: float = 0.05
) -> dict | None:
    """Poll for the manager's ack (`ack-<request_id>.json`). The manager
    processes control requests once per health cycle (the S2.2
    `health_poll_s` cadence); a dead manager writes no ack -> None
    (the S3.2 exit-84 path). A malformed ack (not a dict) is treated as
    no ack yet (the manager rewrites it next cycle — actually it renames
    the request .done after the ack, so a bad ack is sticky: the caller
    surfaces the error, no infinite loop)."""
    control = _control_dir()
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        ack_path = control / f"ack-{req_id}.json"
        if ack_path.is_file():
            try:
                ack = json.loads(ack_path.read_text())
            except (OSError, json.JSONDecodeError):
                time.sleep(poll_s)
                continue
            if isinstance(ack, dict) and ack.get("request_id") == req_id:
                return ack
        time.sleep(poll_s)
    return None


def _parse_pool_args(argv: list[str]) -> tuple[list[int] | str | None, str | None]:
    """Parse `--all` / `--instances i[,i,...]` for unload/reload.
    Returns (targets, error): targets is 'all' or [int, ...] and error
    is None on success; targets is None and error a message on a
    malformed call (S3.2: '2 = malformed args (unknown instance index,
    both selectors given)')."""
    all_flag = False
    inst: list[int] | None = None
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--all":
            all_flag = True
            i += 1
        elif a == "--instances":
            if i + 1 >= len(argv):
                return None, "ERR_ARGS: --instances needs a value (i[,i,...])"
            raw = argv[i + 1]
            parts = [p.strip() for p in raw.split(",") if p.strip()]
            if not parts:
                return None, "ERR_ARGS: --instances got an empty list"
            for p in parts:
                if not p.isdigit():
                    return (
                        None,
                        f"ERR_ARGS: unknown instance index {p!r} (need non-negative ints)",
                    )
                inst = inst or []
                inst.append(int(p))
            i += 2
        else:
            return (
                None,
                f"ERR_ARGS: unknown argument {a!r} (expected --all | --instances i[,i,...])",
            )
    if all_flag and inst is not None:
        return None, "ERR_ARGS: both --all and --instances given (pick one)"
    if all_flag:
        return "all", None
    if inst is None:
        return None, "ERR_ARGS: need --all or --instances i[,i,...]"
    return inst, None


DEFAULT_SERVING_YAML = "/opt/bonsai2/etc/serving.yaml"


def _pool_wait_budget(cfg: dict) -> float:
    """The unload/reload wait budget: boot_target_s + 2 health cycles +
    15 s slack. The manager processes the request once per health cycle;
    the reload path inside the cycle includes the S2.2 health probe up
    to boot_target_s, so the budget must comfortably exceed it. The
    values come from serving.yaml (the manager's own config — the env
    override BONSAI2_SERVING_YAML redirects it, the hermetic hook); a
    missing/malformed file falls back to the defaults (180 s boot +
    10 s poll) and the budget is still a safe over-estimate."""
    boot, poll = 180.0, 10.0
    try:
        srv = Path(os.environ.get("BONSAI2_SERVING_YAML") or DEFAULT_SERVING_YAML)
        if srv.is_file():
            data = _load_yaml_map(srv)
            bt = data.get("boot_target_s", boot)
            if isinstance(bt, (int, float)) and not isinstance(bt, bool) and bt > 0:
                boot = float(bt)
            hp = (data.get("manager") or {}).get("health_poll_s", poll)
            if isinstance(hp, (int, float)) and not isinstance(hp, bool) and hp > 0:
                poll = float(hp)
    except (OSError, ValueError, KeyError, ConfigError) as exc:
        # the budget is a heuristic, not a verdict: a malformed
        # serving.yaml falls back to the defaults (stderr note).
        print(
            f"bonsai2: pool wait budget: serving.yaml unreadable ({exc}); using defaults",
            file=sys.stderr,
            flush=True,
        )
    return boot + 2 * poll + 15.0


def _cmd_pool(
    op: str, argv: list[str], env: dict | None = None, poll_s: float = 0.05
) -> int:
    """The shared S3.2 unload/reload flow (MAP 3.8): manager-mediated
    via the control/ queue. Exit codes: 0 = operation done (incl. the
    no-op case and a partial reload, which reports "state": "partial");
    2 = malformed args; 84 = manager down (no ack within the budget —
    'a dead manager -> exit 84')."""
    try:
        cfg = load_config(env)
    except ConfigError as exc:
        return emit({"error": "ERR_CONFIG", "detail": str(exc)}, EXIT_ARGS, str(exc))
    targets, parse_err = _parse_pool_args(argv)
    if parse_err is not None:
        return emit({"error": "ERR_ARGS", "detail": parse_err}, EXIT_ARGS, parse_err)
    assert targets is not None  # parse_err None => targets is 'all' or a list
    # a dead manager never writes an ack — check the document first so
    # the 84 path is immediate, not budget-long (S3.2: 'a dead manager
    # -> exit 84').
    try:
        load_health_doc(env=env)
    except HealthMissing as exc:
        return emit(
            {"error": "ERR_MANAGER_DOWN", "detail": str(exc), "type": op},
            EXIT_MANAGER_DOWN,
            str(exc),
        )
    req_id, _path = _write_control_request(op, targets)
    budget = _pool_wait_budget(cfg)
    ack = _wait_control_ack(req_id, budget, poll_s=poll_s)
    if ack is None:
        return emit(
            {
                "error": "ERR_MANAGER_DOWN",
                "detail": f"no ack within {budget:.0f} s — the manager is not processing control/{op} (S3.2: manager down)",
                "type": op,
                "request_id": req_id,
            },
            EXIT_MANAGER_DOWN,
            f"control ack timeout ({budget:.0f} s) for {op}",
        )
    state = ack.get("state")
    if state == "error":
        return emit(
            {
                "error": "ERR_ARGS",
                "detail": f"manager rejected the {op}: {ack.get('detail')}",
                "type": op,
            },
            EXIT_ARGS,
            f"manager rejected {op}: {ack.get('detail')}",
        )
    payload = {"type": op, "request_id": req_id, "state": state}
    if op == "unload":
        payload["unloaded"] = ack.get("unloaded", [])
        return emit(
            payload, EXIT_OK, f"unloaded {payload['unloaded']} (state {state!r})"
        )
    payload["reloaded"] = ack.get("reloaded", [])
    if ack.get("detail"):
        payload["detail"] = ack.get("detail")
    return emit(payload, EXIT_OK, f"reloaded {payload['reloaded']} (state {state!r})")


def cmd_unload(argv: list[str], env: dict | None = None, poll_s: float = 0.05) -> int:
    """`bonsai2-unload [--all | --instances i[,i,...]]` (S3.2; MAP 3.8).

    The manager SIGTERMs the targeted instance units (instant VRAM
    release), marks them `bunloaded` in health.json. Zero running
    instances -> main-only `user_unloaded` (S2.4). Idempotent: an
    already-unloaded index is a no-op for that index. JSON carries
    {"unloaded": [i...]}. Exit: 0 / 2 (args) / 84 (manager down)."""
    return _cmd_pool("unload", argv, env, poll_s=poll_s)


def cmd_reload(argv: list[str], env: dict | None = None, poll_s: float = 0.05) -> int:
    """`bonsai2-reload [--all | --instances i[,i,...]]` (S3.2; MAP 3.8).

    Re-spawns the targeted indexes through the S1.4 allocation tier +
    S2.2 unit start + health re-probe. A tier fail at a target leaves
    it `bunloaded`; the others proceed; the JSON carries
    {"reloaded": [i...], "state": "partial"} + a WARN_VRAM_SHORTFALL
    detail (exit 0 — 'a partial reload ... is still a valid state').
    Exit: 0 / 2 (args) / 84 (manager down)."""
    return _cmd_pool("reload", argv, env, poll_s=poll_s)


# --------------------------------------------------------------------------
# S3.2 task-signal helper (cancel's SIGTERM/SIGKILL)
# --------------------------------------------------------------------------
CANCEL_KILL_GRACE_S = 5.0  # S3.2: 'cancel SIGTERM -> SIGKILL after 5 s'


def _signal_process_tree(pid: int, signum: int) -> None:
    """SIGTERM/SIGKILL the worker's PROCESS GROUP (start_worker uses
    start_new_session=True, so the worker is a group leader: signalling
    the group reaches its children too). A ProcessLookupError (the
    worker already exited between our checks) is the success case."""
    try:
        os.killpg(pid, signum)
    except ProcessLookupError:
        return
    except PermissionError:
        try:
            os.kill(pid, signum)
        except OSError:
            return
    except OSError:
        try:
            os.kill(pid, signum)
        except OSError:
            return


def _parse_steer_args(argv: list[str]) -> tuple[str, str, float | None] | str:
    """Parse `bonsai2-steer <TASK_ID> -m MESSAGE [--wait SECS]`.
    Returns (task_id, message, wait_s|None) or an error string."""
    task_id: str | None = None
    message: str | None = None
    wait_s: float | None = None
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "-m":
            if i + 1 >= len(argv):
                return "ERR_ARGS: -m needs a MESSAGE value"
            message = argv[i + 1]
            i += 2
        elif a == "--wait":
            if i + 1 >= len(argv):
                return "ERR_ARGS: --wait needs a SECS value"
            try:
                wait_s = float(argv[i + 1])
            except ValueError:
                return f"ERR_ARGS: --wait needs a number, got {argv[i + 1]!r}"
            if wait_s < 0:
                return "ERR_ARGS: --wait SECS must be >= 0"
            i += 2
        elif a.startswith("--wait="):
            try:
                wait_s = float(a.split("=", 1)[1])
            except ValueError:
                return f"ERR_ARGS: --wait= needs a number, got {a!r}"
            i += 1
        else:
            if task_id is None:
                task_id = a
                i += 1
            else:
                return f"ERR_ARGS: unexpected argument {a!r} (usage: bonsai2-steer <TASK_ID> -m MESSAGE [--wait SECS])"
    if task_id is None:
        return "ERR_ARGS: missing TASK_ID (usage: bonsai2-steer <TASK_ID> -m MESSAGE [--wait SECS])"
    if message is None:
        return "ERR_ARGS: -m MESSAGE is required"
    return task_id, message, wait_s


# --------------------------------------------------------------------------
# entry-point dispatch (the thin deploy files call this)
# --------------------------------------------------------------------------
def dispatch(
    name: str, argv: list[str], env: dict | None = None, poll_s: float = 0.25
) -> int:
    if name == "bonsai2-status":
        return cmd_status(argv, env)
    if name == "bonsai2-list-instances":
        return cmd_list_instances(argv, env)
    if name == "bonsai2-health":
        return cmd_health(argv, env)
    if name == "bonsai2-spawn":
        return cmd_spawn(argv, env, poll_s=poll_s)
    if name == "bonsai2-collect":
        return cmd_collect(argv, env)
    if name == "bonsai2-steer":
        return cmd_steer(argv, env, poll_s=poll_s)
    if name == "bonsai2-cancel":
        return cmd_cancel(argv, env)
    if name == "bonsai2-unload":
        return cmd_unload(argv, env, poll_s=poll_s)
    if name == "bonsai2-reload":
        return cmd_reload(argv, env, poll_s=poll_s)
    raise SystemExit(f"bonsai2_harness.dispatch: unknown command {name!r}")


if __name__ == "__main__":
    sys.exit(dispatch(Path(sys.argv[0]).name, sys.argv[1:]))
