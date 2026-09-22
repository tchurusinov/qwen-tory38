"""MAP 2.1–2.5 / S2.2+S2.5: live @gpu test of the manager spawn path.

The hermetic suite (tests/test_manager.py) proves the manager's LOGIC
(boot-tier math, allocation tier, spawn ordering, exit codes, unit shape).
This @gpu test proves the REAL PATH end-to-end on this box, the way
install.sh will wire it (S2.2/S2.5):

  * the S2.2 `bonsai2-server@i` unit shape is installed (here in USER
    scope — blckwll is the manager's production user; the unit files are
    byte-identical to the S2.2 shape, only the scope differs)
  * `bonsai2-sglang.service`'s ExecStart target (`bonsai2-manager`) runs
    the S2.3 boot check + S1.4 allocation tier + S2.5 ordered spawn
    against the LIVE pool
  * two real 27B instances come up healthy (S2.2 health probe:
    /v1/models + 16-token RT), in order, under the S2.2 `--memory` cap
  * /run/bonsai2/health.json (here: a temp dir) is written in the S3.1
    schema with stack=healthy and both instances

Why user scope (and why that is a documented deviation, not a bug): the
production units are SYSTEM units (MAP 2.4's templates land in
/etc/systemd/system/ at install, S2.2). This box's blckwll user is not
a system-scope manager (sudo needs a password; polkit not configured),
and the S2.2 unit's `Requires=docker.service` is FATAL in user scope
("Unit docker.service not found" — probed 2026-09-22; the user manager
has no docker unit). So the test units drop the `Requires=` line (kept
in the system template, where it is S2.2-verbatim) and the manager
prefixes systemctl with `--user` (BONSAI2_USER_SYSTEMCTL=1, the
manager's documented test hook). Everything else — ExecStartPre, the
verbatim docker-run flags, Restart=on-failure, RestartSec=5,
StartLimitBurst=3, CPUQuota=64% — is the S2.2 shape byte-for-byte.

Measurement notes (box-specific, per the @gpu convention):
  * pool state is asserted BEFORE the run (the 1.9 test's rule — a
    test that loads 2x14.78 GB into a tight pool is not a valid test):
    free >= 2 * per_instance_budget_gb + 1.0 GB margin.
  * VRAM meter = nvidia-smi --query-compute-apps (the main-stack PIDs
    are the baseline; unchanged post-run).
  * wall time ~60-90 s: two cold 27B loads (~10-15 s each, sequential —
    S2.5 waits each to healthy before the next) + a 20 s settle.

The two instance containers (`bonsai2-server-0/1`) and the two user
units are ALWAYS torn down in `finally` (one box, ports 30101/30102 —
never leave a listener behind).
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
MANAGER = REPO_ROOT / "bin" / "bonsai2-manager"
ENGINE_YAML = REPO_ROOT / "etc" / "subagent-engine.yaml"
SERVING_YAML = REPO_ROOT / "etc" / "serving.yaml"
RUNTIME_IMAGE = "bonsai2-server:1.0"
PORT_BASE = 30101
API_KEY = "bonsai2-local"
ALIAS = "qwen38-27b-subagent"
MAIN_CONTAINER = "qwen38-sglang"
SETTLE_S = 20  # post-boot probe-loop window (3 cycles at 10 s)
MANAGER_TIMEOUT_S = 300


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
    """True iff the user-scope systemd manager is running. blckwll has
    Linger=yes on this box (probed 2026-09-22), so the user manager is
    up at boot and stays up; `systemctl --user status` reaching the
    manager (rc 0) is the liveness test."""
    r = subprocess.run(
        ["systemctl", "--user", "status"],
        capture_output=True,
        text=True,
        check=False,
    )
    return r.returncode == 0


def _port_free(port: int) -> bool:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
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


def _gpu_compute_apps_mib() -> dict:
    r = subprocess.run(
        ["nvidia-smi", "--query-compute-apps=pid,used_memory", "--format=csv"],
        capture_output=True,
        text=True,
        check=True,
    )
    out = {}
    for line in r.stdout.splitlines():
        line = line.strip()
        if not line.endswith(" MiB") or "," not in line:
            continue
        pid_s, mib_s = line.split(",", 1)
        if pid_s.strip().isdigit():
            out[int(pid_s.strip())] = int(mib_s.split()[0])
    return out


def _instance_unit_text(
    i: int, image: str, budget_gb: float, ctx: int, parallel: int, kv: str, port: int
) -> str:
    """The S2.2 unit shape, rendered for instance i (the S2.2 verbatim
    docker-run flags; user scope: the `Requires=docker.service` line is
    the documented deviation — see the module doc)."""
    exec_start = (
        "ExecStart=/usr/bin/docker run --rm --name bonsai2-server-%i "
        "--gpus all "
        f"--memory {budget_gb}g --memory-swap {budget_gb}g "
        "--shm-size 4g --network host --ipc=host "
        f"{image} "
        f"/opt/bonsai2/bin/llama-server "
        f"--model /opt/bonsai2/models/bonsai-2-27b-gguf-PQ2_0.gguf "
        f"--ctx-size {ctx} --parallel {parallel} --batch-size 2048 "
        f"--cache-type-k {kv} --cache-type-v {kv} "
        f"--port {port} --api-key {API_KEY} "
        f"--reasoning-format deepseek --alias {ALIAS}"
    )
    return (
        "[Unit]\n"
        f"Description=bonsai2 subagent instance %i (llama-server container, @gpu test)\n"
        "After=docker.service\n"
        "StartLimitIntervalSec=300\n"
        "StartLimitBurst=3\n"
        "\n"
        "[Service]\n"
        f"ExecStartPre=/usr/bin/docker rm -f bonsai2-server-%i\n"
        f"{exec_start}\n"
        "Restart=on-failure\n"
        "RestartSec=5\n"
        "CPUQuota=64%%\n"
        "\n"
        "[Install]\n"
        "WantedBy=multi-user.target\n"
    )


def _ctx_kv_from_template() -> tuple[int, str, int]:
    text = ENGINE_YAML.read_text()

    def _grab(pattern: str) -> str:
        m = re.search(pattern, text)
        assert m, f"pattern {pattern!r} not found in the template"
        return m.group(1)

    ctx = int(_grab(r"(?m)^[ \t]*ctx_size:[ \t]*(\d+)"))
    kv = _grab(r"(?m)^[ \t]*kv_cache:[ \t]*(\S+)")
    par = int(_grab(r"(?m)^[ \t]*max_num_seqs:[ \t]*(\d+)"))
    return ctx, kv, par


class TestManagerSpawnLive(unittest.TestCase):
    """S2.2+S2.5 live: the manager spawns N real instances in order."""

    @unittest.skipUnless(_image_present(), f"runtime image {RUNTIME_IMAGE} not present")
    @unittest.skipUnless(_user_systemctl_available(), "user-scope systemd unavailable")
    def test_manager_spawns_granted_instances_in_order(self):
        # ---- preconditions (the 1.9 pool-state rule) ----
        budget = _instance_budget_gb()
        free_gb = _mem_available_gb()
        need_total = 2 * budget + 1.0  # two instances + the tier margin
        self.assertGreaterEqual(
            free_gb,
            need_total,
            f"pool not clear: free {free_gb:.1f} GB < {need_total:.1f} GB "
            f"(2 x {budget} + 1.0 margin) — run the VRAM audit and clear",
        )
        self.assertTrue(
            _port_free(PORT_BASE) and _port_free(PORT_BASE + 1),
            "ports 30101/30102 busy — leftover container?",
        )
        self.assertTrue(_no_bonsai_containers(), "stale bonsai2-server containers")
        baseline_pids = set(_gpu_compute_apps_mib())

        ctx, kv, par = _ctx_kv_from_template()
        user_unit_dir = Path.home() / ".config" / "systemd" / "user"
        unit_files = []
        try:
            # ---- install the two S2.2-shaped user units ----
            user_unit_dir.mkdir(parents=True, exist_ok=True)
            for i in (0, 1):
                uf = user_unit_dir / f"bonsai2-server@{i}.service"
                uf.write_text(
                    _instance_unit_text(
                        i, RUNTIME_IMAGE, budget, ctx, par, kv, PORT_BASE + i
                    )
                )
                unit_files.append(uf)
            subprocess.run(
                ["systemctl", "--user", "daemon-reload"],
                check=True,
                capture_output=True,
            )

            # ---- run the manager (S2.3 + S1.4 tier + S2.5 spawn) ----
            with tempfile.TemporaryDirectory(prefix="bonsai2-health-") as td:
                env = dict(os.environ)
                env["BONSAI2_USER_SYSTEMCTL"] = "1"
                env["BONSAI2_HEALTH_DIR"] = td
                env["BONSAI2_ENGINE_YAML"] = str(ENGINE_YAML)
                env["BONSAI2_SERVING_YAML"] = str(SERVING_YAML)
                t0 = time.monotonic()
                r = subprocess.run(
                    [sys.executable, str(MANAGER), "--settle", str(SETTLE_S)],
                    capture_output=True,
                    text=True,
                    env=env,
                    check=False,
                    timeout=MANAGER_TIMEOUT_S,
                )
                boot_s = time.monotonic() - t0
                self.assertEqual(
                    r.returncode,
                    0,
                    f"manager exit {r.returncode} (want 0 = full load / "
                    f"partial / main-only).\nSTDERR:\n{r.stderr[-2000:]}",
                )

                # ---- S2.3: the boot tier ran on the LIVE pool --------
                # main stack asleep (cgroup ~2 GB, not the 76.4 fresh-load
                # default) -> free well above the pool -> N'=min(N=2,
                # floor(free/budget)) = 2. The manager log records it.
                self.assertIn("S2.3:", r.stderr)
                self.assertIn("N'=2", r.stderr)

                # ---- S1.4: the allocation tier ran, at the formula
                #      need (footprint + 1.0) -------------------------
                self.assertIn("S1.4 allocation tier need", r.stderr)

                # ---- S2.5: ordered spawn, both instances -------------
                self.assertIn("i=0", r.stderr)
                self.assertIn("i=1", r.stderr)
                self.assertIn("full load: 2 instance(s) spawned", r.stderr)

                # ---- S2.2: both containers running under the cap -----
                for i in (0, 1):
                    name = f"bonsai2-server-{i}"
                    ins = json.loads(
                        subprocess.run(
                            ["docker", "inspect", name],
                            capture_output=True,
                            text=True,
                            check=True,
                        ).stdout
                    )[0]
                    self.assertTrue(
                        ins["State"]["Running"],
                        f"{name} not running: {ins['State']}",
                    )
                    # S2.2: --memory = the S1.4 per-instance budget.
                    # Pin-reality delta (2026-09-22, S2.2): docker parses
                    # the `g` suffix as GiB (2^30), not decimal GB, and
                    # TRUNCATES the float parse to bytes (probed: 14.78g
                    # -> 15869904158 = int(14.78 * 2^30) = int(
                    # 15869904158.72)) — so the cap lands ~0.91% above
                    # the S1.4 decimal-GB budget (the spec formula and
                    # the tier stay decimal-GB end-to-end; the cap is a
                    # hair looser, which can only help the allocation
                    # tier, never hurt it).
                    cap_bytes = int(budget * 2**30)
                    self.assertEqual(
                        ins["HostConfig"]["Memory"],
                        cap_bytes,
                        f"{name} memory cap != {budget}g as docker parses it "
                        f"(S2.2 verbatim flag; {cap_bytes} bytes)",
                    )
                    self.assertEqual(
                        ins["HostConfig"]["MemorySwap"],
                        cap_bytes,
                        f"{name} memory-swap != {budget}g (S2.2: no swap escape)",
                    )
                    self.assertEqual(ins["HostConfig"]["NetworkMode"], "host")
                    self.assertEqual(ins["HostConfig"]["IpcMode"], "host")

                # ---- S2.2/S3.1: health.json --------------------------
                health = json.loads((Path(td) / "health.json").read_text())
                self.assertEqual(
                    health["stack"],
                    "healthy",
                    f"health.json: {json.dumps(health)[:400]}",
                )
                self.assertEqual(len(health["instances"]), 2)
                for i, inst in enumerate(health["instances"]):
                    self.assertEqual(inst["i"], i)
                    self.assertEqual(inst["port"], PORT_BASE + i)
                    self.assertEqual(
                        inst["state"],
                        "healthy",
                        f"instance {i} not healthy after settle: {json.dumps(inst)}",
                    )
                self.assertIs(health["main_healthy"], True)
                self.assertNotIn("main_only_reason", health)  # S2.4: only at 0

                # ---- S2.5: main stack unaffected (the S2.1 ordering) -
                after_pids = _gpu_compute_apps_mib()
                lost = baseline_pids - set(after_pids)
                self.assertFalse(
                    lost,
                    f"main-stack PIDs lost from the compute-apps listing: {lost}",
                )
                print(
                    f"\n[2.5 live] manager boot+spawn+settle in {boot_s:.1f} s "
                    f"(free {free_gb:.1f} GB; 2 instances healthy; "
                    f"health.json stack=healthy)"
                )
        finally:
            for i in (0, 1):
                subprocess.run(
                    ["systemctl", "--user", "stop", f"bonsai2-server@{i}"],
                    capture_output=True,
                    check=False,
                )
                subprocess.run(
                    ["docker", "rm", "-f", f"bonsai2-server-{i}"],
                    capture_output=True,
                    check=False,
                )
            for uf in unit_files:
                uf.unlink(missing_ok=True)
            subprocess.run(
                ["systemctl", "--user", "daemon-reload"],
                capture_output=True,
                check=False,
            )


if __name__ == "__main__":
    unittest.main()
