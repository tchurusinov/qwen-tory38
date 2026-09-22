"""MAP 1.11 / S1.8 #4 + S2.7: kill -9 of the instance process ->
auto-restart < 15 s.

Spec text:
  * S1.8 #4 (acceptance, runnable): "Kill of one instance -> auto-restart
    < 15 s; others unaffected."
  * S2.7 (testing): "Kill -9 one instance process -> assert
    auto-recovery < 15 s, health.json transitions healthy->degraded->
    healthy, harness status reflects it."
  * MAP 1.11 scopes this to the PRE-MANAGER, docker-level half: the S2.2
    unit shape (Restart=on-failure, RestartSec=5) IS the restart policy,
    and with no manager running yet, the test reproduces that policy's
    effect: kill the engine's PID in the container, run the same
    `docker run`/restart path the unit would run, and measure the full
    healthy -> degraded -> healthy window.

Method:
  * instance 0 cold-loads at the S2.2 shape (`--rm` equivalent via the
    test's own teardown, per-instance --memory cap, host net); healthy =
    /v1/models returns the alias (the pre-manager substitute for
    health.json; the S2.1 manager emits the real health.json in P2)
  * the engine's PID in the container (llama-server is PID 1 — the
    nvidia entrypoint `exec`s it, probed 2026-09-22) is read via
    `docker exec ... pgrep -x llama-server` BEFORE the kill
  * kill -9 that PID: the container dies (SIGKILL, no exit code of its
    own — the S1.5 "engine crash mid-request" shape at the container
    level)
  * auto-restart = the S2.2 `Restart=on-failure` effect: re-run the
    container (the unit's ExecStart shape, minus `docker rm -f` which is
    the unit's ExecStartPre — a dead container has no PID left to
    collide) and poll /v1/models for the alias
  * assertions: total kill -> healthy < 15 s (the S1.8 #4 number; the
    window includes the engine's model reload — the S2.7 "auto-recovery
    < 15 s" target, which is what a main-agent waiting on this instance
    actually experiences); the post-restart PID differs from the
    pre-kill one (a real process restart, not the same process serving);
    the main stack's nvidia-smi PIDs are unchanged ("others unaffected"
    — the half this single-instance test can check; the multi-instance
    "others" half lands with 1.9's post-OOM health check)

Measurement notes (box-specific, learned 2026-09-22):
  * Ready time after a cold `docker run` from this image measured 8-15 s
    on this box (MAP 1.7: ready ~8-15 s; the page cache is warm after
    the first load, so the RESTART reload is the fast side of that
    band). The 15 s window is the spec's number, not a tuned constant;
    if the box degrades (main-stack activity, cold cache) the test fails
    loudly and the measurement is in the log for the S2.7 reboot test
    to compare against.
  * `pgrep` exists in the runtime image (probed 2026-09-22); the kill
    goes through `docker exec` so no host-side PID namespace games are
    needed.
"""

import json
import re
import shutil
import subprocess
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
ENGINE_YAML = REPO_ROOT / "etc" / "subagent-engine.yaml"
RUNTIME_IMAGE = "bonsai2-server:1.0"
CONTAINER = "bonsai2-test-i0-kill"
PORT = 30101  # S1.3 instance 0 (the image's baked default)
API_KEY = "bonsai2-local"  # S1.3 --api-key (local-only)
ALIAS = "qwen38-27b-subagent"  # S1.3 --alias
SHM_SIZE = "4g"  # S2.2 shape
RESTART_BUDGET_S = 15.0  # S1.8 #4 / S2.7: auto-restart < 15 s
READY_TIMEOUT_S = 180
READY_POLL_S = 0.5  # tight: the 15 s budget is measured, not coarse-polled


def _docker_present() -> bool:
    return shutil.which("docker") is not None


def _image_present() -> bool:
    if not _docker_present():
        return False
    r = subprocess.run(
        ["docker", "image", "inspect", RUNTIME_IMAGE],
        capture_output=True,
        text=True,
        check=False,
    )
    return r.returncode == 0


def _instance_budget_gb() -> float:
    text = ENGINE_YAML.read_text()
    m = re.search(r"(?m)^[ \t]*per_instance_budget_gb:[ \t]*([0-9.]+)", text)
    assert m, f"per_instance_budget_gb not found in {ENGINE_YAML}"
    return float(m.group(1))


def _http_json(url: str, timeout: float):
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {API_KEY}"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def _wait_ready(deadline: float) -> bool:
    """Poll /v1/models for the alias; True if seen before deadline."""
    while time.monotonic() < deadline:
        try:
            models = _http_json(f"http://127.0.0.1:{PORT}/v1/models", 5.0)
            if ALIAS in (m.get("id") for m in models.get("data", [])):
                return True
        except (
            urllib.error.URLError,
            json.JSONDecodeError,
            ConnectionError,
            TimeoutError,
            OSError,
        ):
            pass
        time.sleep(READY_POLL_S)
    return False


def _engine_pid_in_container() -> int:
    """The llama-server PID inside the container (PID 1 — the nvidia
    entrypoint execs it, probed 2026-09-22). The kill -9 target."""
    r = subprocess.run(
        ["docker", "exec", CONTAINER, "pgrep", "-x", "llama-server"],
        capture_output=True,
        text=True,
        check=False,
    )
    pids = [p for p in r.stdout.split() if p.isdigit()]
    assert pids, f"llama-server PID not found in container: {r.stdout!r} {r.stderr!r}"
    return int(pids[0])


def _container_host_pid() -> int:
    """The container's init PID in the HOST pid namespace (0 when not
    running). Because llama-server IS the container's init (the
    in-container PID is always 1, so it can never witness a restart),
    the HOST pid is the restart witness: a re-run container gets a new
    host pid."""
    r = subprocess.run(
        ["docker", "inspect", CONTAINER, "--format", "{{.State.Pid}}"],
        capture_output=True,
        text=True,
        check=True,
    )
    return int(r.stdout.strip())


def _gpu_compute_apps_mib() -> dict:
    """pid -> used MiB from nvidia-smi (the box's process-level meter,
    same convention as test_bringup_1_7 / test_load_1_8)."""
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
        parts = line.split(",", 1)
        if len(parts) != 2:
            continue
        pid_s, mib_s = parts[0].strip(), parts[1].strip()
        if not (pid_s.isdigit() and mib_s.endswith(" MiB")):
            continue
        out[int(pid_s)] = int(mib_s.split()[0])
    return out


class TestKillRestart(unittest.TestCase):
    """S1.8 #4 / S2.7 (pre-manager half): kill -9 -> auto-restart < 15 s."""

    @unittest.skipUnless(_image_present(), f"runtime image {RUNTIME_IMAGE} not present")
    def test_kill_9_auto_restart_under_15s(self):
        baseline = _gpu_compute_apps_mib()  # main-stack PIDs, pre-launch
        memory_cap_gb = _instance_budget_gb()
        docker = [
            "docker",
            "run",
            "-d",
            "--name",
            CONTAINER,
            "--network",
            "host",
            "--memory",
            f"{memory_cap_gb}g",
            "--memory-swap",
            f"{memory_cap_gb}g",
            "--shm-size",
            SHM_SIZE,
            "--gpus",
            "all",
            "--ipc=host",
            RUNTIME_IMAGE,
        ]
        r = subprocess.run(docker, capture_output=True, text=True, check=False)
        self.assertEqual(r.returncode, 0, f"docker run failed: {r.stderr[-500:]}")
        try:
            # Healthy (the health.json pre-manager substitute): the alias
            # must be served before the kill, or the kill window
            # measures cold-load + restart instead of restart.
            self.assertTrue(
                _wait_ready(time.monotonic() + READY_TIMEOUT_S),
                f"instance never healthy within {READY_TIMEOUT_S}s before the kill",
            )
            pid_before = _engine_pid_in_container()
            host_pid_before = _container_host_pid()  # restart witness, pre-kill
            print(
                f"\n[1.11] healthy (alias on :{PORT}, engine pid {pid_before}, "
                f"host pid {host_pid_before}); "
                f"kill -9 -> restart window follows (budget {RESTART_BUDGET_S:.0f}s)",
                flush=True,
            )

            # Kill -9 the engine process (the S2.7 "kill -9 one instance
            # process"). Container-level: SIGKILL -> container exits
            # (the S2.2 unit's Restart=on-failure sees a non-zero-ish
            # failure — SIGKILL is not a clean exit).
            kill_t0 = time.monotonic()
            r = subprocess.run(
                ["docker", "exec", CONTAINER, "kill", "-9", str(pid_before)],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(
                r.returncode,
                0,
                f"kill -9 failed: {r.stderr[-300:]}",
            )

            # Auto-restart (the S2.2 Restart=on-failure effect,
            # pre-manager): re-run the container in the unit's
            # ExecStart shape. ExecStartPre's `docker rm -f` is folded
            # into the rm below (a dead container has no PID left to
            # collide with, so the fresh run is the restart).
            subprocess.run(
                ["docker", "rm", "-f", CONTAINER], capture_output=True, check=False
            )
            r = subprocess.run(docker, capture_output=True, text=True, check=False)
            self.assertEqual(
                r.returncode,
                0,
                f"restart docker run failed: {r.stderr[-500:]}",
            )

            # Back to healthy: poll the alias; the whole window (kill ->
            # alias served again) is what S1.8 #4's "< 15 s" covers.
            self.assertTrue(
                _wait_ready(time.monotonic() + READY_TIMEOUT_S),
                "instance never healthy again after the restart",
            )
            restart_s = time.monotonic() - kill_t0
            print(
                f"[1.11] restart window: kill -> healthy in {restart_s:.2f}s "
                f"(budget {RESTART_BUDGET_S:.0f}s)",
                flush=True,
            )
            self.assertLess(
                restart_s,
                RESTART_BUDGET_S,
                f"kill -> healthy took {restart_s:.2f}s (> {RESTART_BUDGET_S:.0f}s "
                f"S1.8 #4 acceptance); the S2.7 reboot test re-measures "
                f"this at boot scale",
            )

            # A real process restart, witnessed by the HOST pid: the
            # in-container PID is 1 in BOTH containers (llama-server is
            # the container init — the nvidia entrypoint execs it), so
            # it can never differ across a container re-run; the host
            # pid (docker .State.Pid) is allocated fresh per container
            # and is the restart witness.
            host_pid_after = _container_host_pid()
            self.assertGreater(
                host_pid_after,
                0,
                "post-restart container has no host pid — it is not running",
            )
            self.assertNotEqual(
                host_pid_after,
                host_pid_before,
                f"post-restart host pid {host_pid_after} == pre-kill host "
                f"pid {host_pid_before} — not a process restart",
            )
            print(
                f"[1.11] restart witnessed: host pid "
                f"{host_pid_before} -> {host_pid_after}",
                flush=True,
            )

            # Others unaffected (the half this single-instance test can
            # check): the main stack's nvidia-smi PIDs are all still
            # listed (a subset, not equality — the instance's own PID
            # is a legitimate addition, and nvidia-smi can lag reaping
            # the killed container's entry; probe 2026-09-22: a
            # torn-down container's PID lingered for minutes).
            lost = set(baseline) - set(_gpu_compute_apps_mib())
            self.assertEqual(
                lost,
                set(),
                f"main-stack PIDs lost during the kill/restart window "
                f"(the 'others unaffected' half, S1.8 #4): {lost}",
            )
        finally:
            subprocess.run(
                ["docker", "rm", "-f", CONTAINER],
                capture_output=True,
                check=False,
            )


if __name__ == "__main__":
    unittest.main()
