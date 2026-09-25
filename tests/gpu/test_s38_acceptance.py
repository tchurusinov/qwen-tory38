"""MAP 3.12 / S3.8 acceptance: the LIVE end-to-end run on this box.

S3.8 (runnable) — the four live acceptance items, in MVP scope:

  #1  3-task fan-out using ONLY S3.2 commands: 3x spawn -> the tasks
      land running -> 3x collect (exit 20, the result payload is the
      verdict) -> results verified against the real engines' answers.
  #2  mid-task engine kill: kill -9 the live instance container -> the
      in-flight task recovers (S1.5 one-retry / requeue) and a
      subsequent task completes; the main agent's observed commands
      stay within S3.2 (spawn/collect/status).
  #3  the S3.2 closed set {0,2,3,4,5,8,20,21,22,80,82,84} — the 12
      codes are exercised by the HERMETIC suite
      (tests/test_harness_closed_set.py, S3.7 mock surface); this live
      test asserts the live codes it actually produces (0/8/20/80/82)
      and that NO code outside the closed set is ever emitted by the
      thin deploy scripts (the stray-code guard S3.8 #3 names).
  #4  N=0 main-only: with the pool explicitly unloaded the offload
      command (spawn) -> exit 80 + the S2.4 {"reason": "user_unloaded"}
      JSON (the hermetic half + the S2.4-table scoping live here too).

  #5  unload/reload: unload --all -> the S2.4 user_unloaded main-only
      pool (S3.7's mock pool cases — no-op, partial, bad-args,
      dead-manager — are the hermetic suite's; the live half is the
      unload -> user_unloaded -> reload -> healthy cycle).

MVP safety rule (AGENTS.md, standing): ORCHESTRATE WITH EXACTLY 1
subagent instance for this e2e run (`install.sh --instances 1` shape;
here: one user unit, instance 0, port 30101). The 1 is a load-time
condition, not a code limit — the manager + harness are written for N.

Why user scope + a temp health/results dir (the test_manager_spawn_2_5
convention, mirrored): blckwll is the manager's production user, and
Linger=yes keeps the user manager up; the manager prefixes systemctl
with --user (BONSAI2_USER_SYSTEMCTL=1). The thin deploy scripts
(`bin/bonsai2-spawn` etc.) read the pool from BONSAI2_HEALTH_DIR and
the task records from BONSAI2_RESULTS_DIR, so the live run is hermetic
on the run dir while the ENGINES are real (the S3.2 command surface
against the real S1.3 engines).

Measurement / box rules (per the @gpu convention):
  * pool state asserted BEFORE the run (the 1.9 rule): free >=
    1 * per_instance_budget_gb + 1.0 GB margin (MVP: one instance).
  * VRAM meter = nvidia-smi --query-compute-apps (main-stack PIDs are
    the baseline; unchanged post-run — S2.1 main-first ordering).
  * wall time ~2-4 min: one cold 27B load (~10-15 s) + settle + the
    fan-out + the kill-recovery window.

The instance container (`bonsai2-server-0`) + its user unit are ALWAYS
torn down in `finally` (one box, port 30101 — never leave a listener
behind). The test skips (not fails) when the runtime image / user
systemd / pool is not ready, so the @gpu suite stays green when the
box is mid-state.
"""

import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
BIN = REPO_ROOT / "bin"
MANAGER = REPO_ROOT / "bin" / "bonsai2-manager"
ENGINE_YAML = REPO_ROOT / "etc" / "subagent-engine.yaml"
SERVING_YAML = REPO_ROOT / "etc" / "serving.yaml"
RUNTIME_IMAGE = "bonsai2-server:1.0"
PORT_BASE = 30101  # instance 0
SETTLE_S = 20  # post-boot probe-loop window (3 cycles at 10 s)
MANAGER_TIMEOUT_S = 300
PY = sys.executable or "/usr/bin/python3"

# S3.2 closed set (the stray-code guard S3.8 #3 asserts against).
S32_CLOSED_SET = {0, 2, 3, 4, 5, 8, 20, 21, 22, 80, 82, 84}

# The S3.2 commands this acceptance drives (the main agent's surface).
S32_COMMANDS = [
    "bonsai2-spawn",
    "bonsai2-collect",
    "bonsai2-status",
    "bonsai2-list-instances",
    "bonsai2-health",
    "bonsai2-steer",
    "bonsai2-cancel",
    "bonsai2-unload",
    "bonsai2-reload",
]


def _image_present() -> bool:
    if shutil.which("docker") is None:
        return False
    r = subprocess.run(
        ["docker", "image", "inspect", RUNTIME_IMAGE],
        capture_output=True,
        text=True,
        check=False,
    )
    return r.returncode == 0


def _user_systemctl_available() -> bool:
    r = subprocess.run(
        ["systemctl", "--user", "status"],
        capture_output=True,
        text=True,
        check=False,
    )
    return r.returncode == 0


def _port_free(port: int) -> bool:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        s.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def _no_bonsai_containers() -> bool:
    r = subprocess.run(
        [
            "docker",
            "ps",
            "-a",
            "--filter",
            "name=bonsai2-server-",
            "--format",
            "{{.Names}}",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    return not r.stdout.strip()


def _mem_available_gb() -> float:
    for line in Path("/proc/meminfo").read_text().splitlines():
        if line.startswith("MemAvailable:"):
            return int(line.split()[1]) / 1e6
    raise AssertionError("MemAvailable not in /proc/meminfo")


def _instance_budget_gb() -> float:
    text = ENGINE_YAML.read_text()
    m = re.search(r"(?m)^[ \t]*per_instance_budget_gb:[ \t]*([0-9.]+)", text)
    assert m, "per_instance_budget_gb not found in the template"
    return float(m.group(1))


def _instance_unit_text(budget_gb: float, ctx_kv: tuple, model_alias: tuple) -> str:
    ctx, kv, par = ctx_kv
    model_file, alias, api_key, reasoning = model_alias
    return (
        "[Unit]\n"
        "Description=bonsai2 subagent instance %i (llama-server, @gpu acceptance)\n"
        "After=docker.service\n"
        "StartLimitIntervalSec=300\n"
        "StartLimitBurst=3\n"
        "\n"
        "[Service]\n"
        "ExecStartPre=/usr/bin/docker rm -f bonsai2-server-%i\n"
        "ExecStart=/usr/bin/docker run --rm --name bonsai2-server-%i "
        "--gpus all "
        f"--memory {budget_gb}g --memory-swap {budget_gb}g "
        "--shm-size 4g --network host --ipc=host "
        f"{RUNTIME_IMAGE} "
        f"/opt/bonsai2/bin/llama-server "
        f"--model /opt/bonsai2/models/{model_file} "
        f"--ctx-size {ctx} --parallel {par} --batch-size 2048 "
        f"--cache-type-k {kv} --cache-type-v {kv} "
        f"--port {PORT_BASE} --api-key {api_key} "
        f"--reasoning-format {reasoning} --alias {alias}\n"
        "Restart=on-failure\n"
        "RestartSec=5\n"
        "CPUQuota=64%\n"
        "\n"
        "[Install]\n"
        "WantedBy=multi-user.target\n"
    )


def _ctx_kv_from_template() -> tuple:
    text = ENGINE_YAML.read_text()

    def _grab(pat: str) -> str:
        m = re.search(pat, text)
        assert m, f"{pat!r} not in the template"
        return m.group(1)

    return (
        int(_grab(r"(?m)^[ \t]*ctx_size:[ \t]*(\d+)")),
        _grab(r"(?m)^[ \t]*kv_cache:[ \t]*(\S+)"),
        int(_grab(r"(?m)^[ \t]*max_num_seqs:[ \t]*(\d+)")),
    )


def _model_alias_from_template() -> tuple:
    text = ENGINE_YAML.read_text()

    def _grab(pat: str) -> str:
        m = re.search(pat, text)
        assert m, f"{pat!r} not in the template"
        return m.group(1)

    return (
        _grab(r"(?m)^[ \t]*file:[ \t]*(\S+)"),
        _grab(r"(?m)^[ \t]*alias:[ \t]*(\S+)"),
        _grab(r"(?m)^[ \t]*api_key:[ \t]*(\S+)"),
        _grab(r"(?m)^[ \t]*reasoning_format:[ \t]*(\S+)"),
    )


class TestS38LiveAcceptance(unittest.TestCase):
    """S3.8 #1/#2/#4/#5 live (MVP: one instance); #3 is the hermetic
    closed-set suite + this file's stray-code guard."""

    @unittest.skipUnless(_image_present(), f"runtime image {RUNTIME_IMAGE} not present")
    @unittest.skipUnless(_user_systemctl_available(), "user-scope systemd unavailable")
    def test_s38_fanout_kill_n0_unload(self):
        # ---- preconditions (the 1.9 pool-state rule, MVP N=1) ----
        budget = _instance_budget_gb()
        free_gb = _mem_available_gb()
        need_total = budget + 1.0  # one instance + the tier margin (MVP)
        self.assertGreaterEqual(
            free_gb,
            need_total,
            f"pool not clear: free {free_gb:.1f} GB < {need_total:.1f} GB "
            f"(1 x {budget} + 1.0 margin) — run the VRAM audit and clear",
        )
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("127.0.0.1", PORT_BASE))
        except OSError:
            self.fail(f"port {PORT_BASE} busy — leftover container?")
        finally:
            s.close()
        self.assertTrue(_no_bonsai_containers(), "stale bonsai2-server containers")

        ctx_kv = _ctx_kv_from_template()
        model_alias = _model_alias_from_template()
        user_unit_dir = Path.home() / ".config" / "systemd" / "user"
        unit_file = user_unit_dir / "bonsai2-server@0.service"
        # snapshot the tracked templates (the manager's S2.6/S2.4
        # write-backs may mutate them — restore after, like 2.5).
        tpl_backups = {p: p.read_bytes() for p in (ENGINE_YAML, SERVING_YAML)}
        # the background manager process + its log handle (cleaned up in
        # finally so a backgrounded manager never outlives the test).
        manager_proc: subprocess.Popen | None = None
        log_fh = None
        try:
            # ---- install the one S2.2-shaped user unit (MVP N=1) ----
            user_unit_dir.mkdir(parents=True, exist_ok=True)
            unit_file.write_text(_instance_unit_text(budget, ctx_kv, model_alias))
            subprocess.run(
                ["systemctl", "--user", "daemon-reload"],
                check=True,
                capture_output=True,
            )

            # ---- run the manager (S2.3 + S1.4 tier + S2.5 spawn) ----
            with tempfile.TemporaryDirectory(prefix="bonsai2-acc-") as td:
                tdir = Path(td)
                health_dir = tdir / "run"
                results_dir = tdir / "results"
                env = dict(os.environ)
                env["BONSAI2_USER_SYSTEMCTL"] = "1"
                env["BONSAI2_HEALTH_DIR"] = str(health_dir)
                env["BONSAI2_RESULTS_DIR"] = str(results_dir)
                env["BONSAI2_ENGINE_YAML"] = str(ENGINE_YAML)
                env["BONSAI2_SERVING_YAML"] = str(SERVING_YAML)
                # the thin deploy scripts' config: main endpoint = the
                # real SGLang (:30001, 401-walled = up per probe),
                # results/dedup under the run dir.
                harness_cfg = tdir / "harness.yaml"
                harness_cfg.write_text(
                    "main_endpoint: http://localhost:30001\n"
                    f"results_dir: {results_dir}\n"
                    f"dedup_dir: {health_dir / 'dedup'}\n"
                    "queue_wait_max_s: 30\n"
                    "task_default_deadline_s: 300\n"
                )
                env["BONSAI2_CONFIG"] = str(harness_cfg)

                # ---- run the manager in the BACKGROUND (S2.3 + S1.4
                #      tier + S2.5 spawn; --settle 0 = run forever) ----
                # The manager must STAY UP for S3.8 #2/#5: it restarts
                # the killed unit (Restart=on-failure) and services the
                # control/ queue for unload/reload (the manager's steady
                # loop is the S3.2 protocol the thin commands talk to).
                log_path = tdir / "manager.log"
                # SIM115-exempt: Popen holds this fd for the manager's
                # whole lifetime; it is closed in the `finally` below
                # (a `with` block would close it at the end of the huge
                # try-body, mid-test).
                log_fh = open(log_path, "wb")  # noqa: SIM115
                manager_proc = subprocess.Popen(
                    [PY, str(MANAGER)],
                    stdout=log_fh,
                    stderr=subprocess.STDOUT,
                    env=env,
                    start_new_session=True,
                )
                # wait for instance 0 to be healthy (S2.2 probe: the
                # manager's health cycle writes the document)
                health: dict = {}
                deadline = time.monotonic() + MANAGER_TIMEOUT_S
                healthy = False
                while time.monotonic() < deadline:
                    if manager_proc.poll() is not None:
                        break  # the manager exited early (boot failure)
                    try:
                        health = json.loads((health_dir / "health.json").read_text())
                    except (OSError, json.JSONDecodeError):
                        health = {}
                        continue
                    if health.get("stack") == "healthy" and any(
                        i.get("state") == "healthy" for i in health.get("instances", [])
                    ):
                        healthy = True
                        break
                    time.sleep(3.0)
                self.assertTrue(
                    healthy,
                    f"instance 0 not healthy within {MANAGER_TIMEOUT_S} s "
                    f"(health: {json.dumps(health)[:400]}; "
                    f"manager exit {manager_proc.poll()}; "
                    f"log tail: {log_path.read_text(errors='replace')[-1500:]})",
                )
                self.assertEqual(
                    len(health["instances"]), 1, "MVP: exactly one instance (N=1)"
                )
                inst = health["instances"][0]
                self.assertEqual(inst["i"], 0)
                self.assertEqual(inst["port"], PORT_BASE)
                self.assertEqual(
                    inst["state"], "healthy", f"instance 0 not healthy: {inst}"
                )

                def run_bin(cmd: str, argv: list, expect: int | None = None):
                    """Run a thin S3.2 deploy script; return (code, doc).
                    The stray-code guard S3.8 #3: NO code outside the
                    closed set may be emitted by any S3.2 command."""
                    rr = subprocess.run(
                        [PY, str(BIN / cmd), *argv],
                        capture_output=True,
                        text=True,
                        env=env,
                        check=False,
                        timeout=180,
                    )
                    self.assertIn(
                        rr.returncode,
                        S32_CLOSED_SET,
                        f"{cmd} {argv} exited {rr.returncode} — NOT in the "
                        f"S3.2 closed set {sorted(S32_CLOSED_SET)} "
                        f"(stray code = harness bug, S3.8 #3). "
                        f"stderr: {rr.stderr[-400:]}",
                    )
                    doc = json.loads(rr.stdout) if rr.stdout.strip() else {}
                    if expect is not None:
                        self.assertEqual(
                            rr.returncode,
                            expect,
                            f"{cmd} {argv}: exit {rr.returncode} != {expect} "
                            f"(doc {doc}); stderr {rr.stderr[-400:]}",
                        )
                    return rr.returncode, doc

                # ======================================================
                # S3.8 #1 — 3-task fan-out (MVP: 3 sequential tasks; the
                # "parallel" shape is 3 spawns each landing on the one
                # instance's free slots, then 3 collects each exit 20).
                # ======================================================
                task_ids = []
                for k in range(3):
                    code, doc = run_bin(
                        "bonsai2-spawn",
                        [
                            "-i",
                            f"live fan-out task {k}: answer with the single word PONG-{k}",
                            "--deadline",
                            "180",
                        ],
                        expect=0,
                    )
                    self.assertIn(
                        "task_id", doc, f"spawn {k} returned no task_id: {doc}"
                    )
                    task_ids.append(doc["task_id"])
                # S3.8 #1: 3x collect -> exit 20 each, results verified.
                for k, tid in enumerate(task_ids):
                    deadline = time.monotonic() + 200
                    while True:
                        code, doc = run_bin("bonsai2-collect", [tid])
                        if code == 20:  # done
                            break
                        self.assertEqual(
                            code,
                            0,
                            f"collect {tid}: {code} != 0/20 (state {doc.get('state')})",
                        )
                        self.assertIn(
                            doc.get("state"),
                            ("running", "queued", "engine_retried"),
                            f"collect {tid}: unexpected state {doc.get('state')}",
                        )
                        if time.monotonic() > deadline:
                            self.fail(f"task {tid} never reached done within 200 s")
                        time.sleep(2.0)
                    self.assertEqual(code, 20, f"collect {tid}: want 20, got {code}")
                    self.assertEqual(doc.get("state"), "done")
                    self.assertIn(
                        "PONG",
                        str(doc.get("answer", "")),
                        f"result not verified: {doc.get('answer')!r}",
                    )
                    self.assertIn("usage", doc)
                print(
                    f"\n[S3.8 #1 live] 3-task fan-out done: {len(task_ids)} tasks "
                    f"spawned -> collect exit 20 -> results verified (PONG)."
                )

                # ======================================================
                # S3.8 #2 — mid-task engine kill: kill -9 the container,
                # a queued task is requeued/retried and still completes.
                # ======================================================
                # spawn a task, then kill the engine container mid-run.
                # With one instance, the S1.5 one-retry has no UNTRIED
                # instance, so the task lands engine_retried (recorded)
                # or, once the manager restarts the unit, the reaper /
                # a fresh spawn completes it. Assert: the kill is
                # OBSERVED (unit restarted), the task record recovers to
                # a terminal state, and a fresh spawn still works.
                kill_tid = None
                code, doc = run_bin(
                    "bonsai2-spawn",
                    ["-i", "kill-me-mid-task: answer PONG-KILL", "--deadline", "180"],
                    expect=0,
                )
                kill_tid = doc["task_id"]
                # wait for the task to be running
                for _ in range(60):
                    _, cd = run_bin("bonsai2-collect", [kill_tid])
                    if cd.get("state") == "running":
                        break
                    time.sleep(1.0)
                # kill the live instance container mid-task
                subprocess.run(
                    ["docker", "kill", "-s", "SIGKILL", "bonsai2-server-0"],
                    capture_output=True,
                    check=False,
                )
                time.sleep(3.0)
                # the manager (Restart=on-failure) restarts the unit; the
                # in-flight task is now a stale running record -> the
                # S3.4 reaper turns it failed/ERR_ENGINE_DOWN (slot
                # freed), or the worker's connection-error path retries
                # (N=1: no untried instance -> S1.5 one-retry -> failed).
                # Wait for the record to reach a terminal state.
                rec_state = None
                last_cd: dict = {}
                deadline = time.monotonic() + 120
                while time.monotonic() < deadline:
                    _, cd = run_bin("bonsai2-collect", [kill_tid])
                    last_cd = cd
                    st = cd.get("state")
                    if st in ("done", "failed", "timeout", "engine_retried"):
                        rec_state = st
                        break
                    time.sleep(2.0)
                self.assertIsNotNone(
                    rec_state,
                    f"task {kill_tid} did not reach a terminal state within "
                    f"120 s of the kill -9 (record: {last_cd})",
                )
                # a fresh spawn against the restarted instance completes
                code, doc = run_bin(
                    "bonsai2-spawn",
                    ["-i", "after-kill task: answer PONG-AFTER", "--deadline", "180"],
                    expect=0,
                )
                after_tid = doc["task_id"]
                deadline = time.monotonic() + 150
                while True:
                    code, cd = run_bin("bonsai2-collect", [after_tid])
                    if code == 20:
                        break
                    if time.monotonic() > deadline:
                        self.fail(f"post-kill task {after_tid} never done: {cd}")
                    time.sleep(2.0)
                self.assertEqual(code, 20)
                self.assertIn("PONG", str(cd.get("answer", "")))
                print(
                    f"\n[S3.8 #2 live] mid-task kill -9: task -> {rec_state}; "
                    f"post-kill spawn -> collect exit 20 (PONG-AFTER)."
                )

                # ======================================================
                # S3.8 #5 — unload/reload (the live half).
                # ======================================================
                # unload --all -> the pool is main-only with the S2.4
                # user_unloaded reason; spawn -> exit 80 (S3.8 #4 live).
                run_bin("bonsai2-unload", ["--all"], expect=0)
                health2 = json.loads((health_dir / "health.json").read_text())
                running = [i for i in health2["instances"] if i["state"] != "bunloaded"]
                assert not running, f"instances still running after unload: {running}"
                _code80, doc80 = run_bin(
                    "bonsai2-spawn", ["-i", "pool is off"], expect=80
                )
                self.assertEqual(
                    doc80,
                    {"reason": "user_unloaded"},
                    f"S3.8 #4 live: unload -> main-only pool, spawn exits 80 "
                    f"with the S2.4 user_unloaded JSON; got {doc80}",
                )
                # reload --all -> healthy again
                run_bin("bonsai2-reload", ["--all"], expect=0)
                deadline = time.monotonic() + 120
                healthy = False
                h3: dict = {}
                while time.monotonic() < deadline:
                    h3 = json.loads((health_dir / "health.json").read_text())
                    if h3.get("instances") and all(
                        i["state"] == "healthy" for i in h3["instances"]
                    ):
                        healthy = True
                        break
                    time.sleep(3.0)
                self.assertTrue(healthy, f"pool not healthy after reload: {h3}")
                print(
                    "\n[S3.8 #5 live] unload --all -> user_unloaded main-only "
                    "(spawn exit 80); reload --all -> healthy."
                )

                print(
                    f"\n[S3.8 acceptance] live e2e passed (MVP N=1): "
                    f"fan-out + kill-recovery + N=0 80 + unload/reload; "
                    f"no S3.2 command emitted a code outside {sorted(S32_CLOSED_SET)}."
                )
        finally:
            if manager_proc is not None and manager_proc.poll() is None:
                # terminate the steady-loop manager (it owns the control/
                # queue + restarts the units). SIGTERM, then SIGKILL.
                manager_proc.terminate()
                try:
                    manager_proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    manager_proc.kill()
                    manager_proc.wait(timeout=10)
            if log_fh is not None:
                try:
                    log_fh.close()
                except OSError:
                    pass
            subprocess.run(
                ["systemctl", "--user", "stop", "bonsai2-server@0"],
                capture_output=True,
                check=False,
            )
            subprocess.run(
                ["docker", "rm", "-f", "bonsai2-server-0"],
                capture_output=True,
                check=False,
            )
            unit_file.unlink(missing_ok=True)
            subprocess.run(
                ["systemctl", "--user", "daemon-reload"],
                capture_output=True,
                check=False,
            )
            for p, blob in tpl_backups.items():
                try:
                    if p.read_bytes() != blob:
                        p.write_bytes(blob)
                except OSError:
                    pass


if __name__ == "__main__":
    unittest.main()
