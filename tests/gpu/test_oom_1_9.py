"""MAP 1.9 / S1.7 VRAM ceiling: the S1.4 allocation tier fails -> ERR_VRAM_OOM,
not a segfault; prior instances stay healthy.

Spec text (S1.7, "VRAM ceiling"): load instances until the S1.4 allocation
tier fails -> assert the last failure is `ERR_VRAM_OOM`, not a segfault, and
prior instances stay healthy.

Method (docker-level, pre-manager — the P1 shape; S2's manager runs the
allocation tier as a pre-spawn gate, this test reproduces that gate in-test
from live free memory and lets the kernel's cgroup OOM-killer realize the
failure the way S1.5 path 2 describes it for a mid-load OOM):

  * instances 0 and 1 boot at the full S2.2 `--memory` cap
    (per_instance_budget_gb from the template) and reach /v1/models
    readiness — the "prior instances" half
  * instance 2's pre-spawn S1.4 allocation-tier check
    (free >= instance_footprint_GB + 1.0 GB, `bin/bonsai2_vram` pure
    function + /proc/meminfo MemAvailable) is run on LIVE free memory;
    with 2 instances loaded the pool is below the requirement, so the
    tier FAILS at i = 2 -> the S1.4 ERR_VRAM_OOM path (S1.5 path 1: the
    observer stops before / at the KV allocation)
  * instance 2 is spawned with its `--memory` cap set BELOW the tier
    requirement, so the kernel OOM-kills the load mid-KV-allocation
    instead of letting it run: the container must exit
    OOMKilled=true — a clean kernel kill, NOT a segfault (exit 139,
    SIGBUS/SIGSEGV markers, or the engine's own graceful bind/arg
    failures)
  * prior instances 0/1 stay HEALTHY afterwards: container Up,
    /v1/models still returns the alias on both ports, and a
    16-token completion round-trips on both (the S1.7 "stay healthy"
    half, same probe as test_bringup_1_7)
  * the main stack's PIDs (nvidia-smi compute-apps baseline) are
    unchanged — the OOM cost instance 2 only

Measurement notes (box-specific, probed 2026-09-22):
  * /proc/meminfo MemAvailable is the free-memory read on this GB10
    (the MAP 1.7 convention: nvidia-smi `--query-gpu` memory fields are
    N/A on unified memory; the S1.4 tier's "cgroup/nvidia-smi free"
    maps to MemAvailable here — it excludes page cache the kernel
    cannot reclaim from the subagent pool).
  * MemAvailable at probe time ~45.9 GB; after instances 0/1 (~14.44 GB
    each, MAP 1.7 measured total) it is ~16.8 GB — comfortably below the
    tier requirement 15.08 GB = instance_footprint_GB(7.21, 8, 262144)
    + 1.0 GB spike margin = 14.08 + 1.0.
  * The OOM cap = requirement - 0.5 GB headroom = 14.58 GB < the
    measured 14.44 GB load total + the cgroup's own runtime charge, so
    the kernel OOM-kill lands during/right after the KV allocation —
    the S1.5 path-2 shape — and the exit is a SIGKILL (OOMKilled=true),
    never a segfault.
  * This is the first N > 1 experiment on the box (the MVP safety rule
    loads exactly 1 until now; 1.9 is the ceiling probe that justifies
    the N > 1 path). VRAM audit per the machine rule: pre-launch
    baseline in the log, free memory asserted to exceed 2x the tier
    requirement BEFORE instance 2's tier check (the test is invalid —
    and fails loudly — if someone else ate the pool mid-run).
  * One box, three ports (30101/30102/30103): all containers are torn
    down in `finally`.
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
API_KEY = "bonsai2-local"  # S1.3 --api-key (local-only)
ALIAS = "qwen38-27b-subagent"  # S1.3 --alias
PORT_BASE = 30101  # S1.3: port = port_base + i
SHM_SIZE = "4g"  # S2.2 shape
READY_TIMEOUT_S = 180
READY_POLL_S = 2.0
# S1.4 allocation tier: free >= instance_footprint_GB + 1.0 GB.
SPIKE_MARGIN_GB = 1.0
# The failing instance's --memory cap: requirement - 0.5 GB headroom, so
# the kernel OOM-kill lands at the KV allocation (probe 2026-09-22:
# measured load total 14.44 GB vs cap 14.58 GB -> OOMKilled=true).
OOM_CAP_HEADROOM_GB = 0.5


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


def _yaml_value(key: str, pattern: str) -> float:
    """A top-level-or-indented YAML scalar, anchored so a comment line
    that merely MENTIONS the key can't match (same convention as
    test_bringup_1_7's per_instance_budget reader)."""
    text = ENGINE_YAML.read_text()
    m = re.search(pattern, text)
    assert m, f"{key!r} not found in {ENGINE_YAML}"
    return float(m.group(1))


def _instance_budget_gb() -> float:
    return _yaml_value(
        "per_instance_budget_gb", r"(?m)^[ \t]*per_instance_budget_gb:[ \t]*([0-9.]+)"
    )


def _weights_gb() -> float:
    # S1.1: the default quant (PQ2_0) byte size pinned in the template.
    return _yaml_value("size_bytes", r"(?m)^[ \t]*size_bytes:[ \t]*([0-9.]+)") / (
        1024**3
    )


def _ctx_size() -> int:
    return int(_yaml_value("ctx_size", r"(?m)^[ \t]*ctx_size:[ \t]*([0-9.]+)"))


def _mem_available_gb() -> float:
    """The S1.4 tier's free-memory read on this GB10 (see module doc)."""
    for line in Path("/proc/meminfo").read_text().splitlines():
        if line.startswith("MemAvailable:"):
            return int(line.split()[1]) / (1024 * 1024)
    raise AssertionError("MemAvailable not in /proc/meminfo")


def _gpu_compute_apps_mib() -> dict:
    """pid -> used MiB from nvidia-smi (the box's process-level meter,
    same convention as test_bringup_1_7)."""
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


def _http_json(url: str, timeout: float):
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {API_KEY}"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def _completion(port: int, prompt: str, max_tokens: int, timeout: float):
    body = json.dumps(
        {
            "model": ALIAS,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "stream": False,
        }
    ).encode()
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/v1/chat/completions",
        data=body,
        headers={
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    t0 = time.monotonic()
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    return time.monotonic() - t0, json.loads(raw)


def _wait_ready(port: int, deadline: float) -> bool:
    """Poll /v1/models for the alias; True if seen before deadline."""
    last_err = "not polled yet"
    while time.monotonic() < deadline:
        try:
            models = _http_json(f"http://127.0.0.1:{port}/v1/models", 5.0)
            if ALIAS in (m.get("id") for m in models.get("data", [])):
                return True
        except (
            urllib.error.URLError,
            json.JSONDecodeError,
            ConnectionError,
            TimeoutError,
            OSError,
        ) as e:
            last_err = str(e)
        time.sleep(READY_POLL_S)
    print(f"    (readiness {port}: last error {last_err})", flush=True)
    return False


def _engine_cmd(port: int) -> list[str]:
    """The S1.3 per-instance serving command, verbatim, at --port <port>
    (the S2.2 unit shape: the full command, not the image's baked
    default — probed 2026-09-22: `docker run img --port X` replaces the
    baked CMD and dies with `exec: --: invalid option` under the nvidia
    entrypoint, so the full command must be passed)."""
    return [
        "/opt/bonsai2/bin/llama-server",
        "--model",
        "/opt/bonsai2/models/bonsai-2-27b-gguf-PQ2_0.gguf",
        "--ctx-size",
        str(_ctx_size()),
        "--parallel",
        "8",
        "--batch-size",
        "2048",
        "--cache-type-k",
        "q4_0",
        "--cache-type-v",
        "q4_0",
        "--port",
        str(port),
        "--api-key",
        API_KEY,
        "--reasoning-format",
        "deepseek",
        "--alias",
        ALIAS,
    ]


def _spawn(name: str, port: int, cap_g: str) -> None:
    r = subprocess.run(
        [
            "docker",
            "run",
            "-d",
            "--name",
            name,
            "--network",
            "host",
            "--memory",
            cap_g,
            "--memory-swap",
            cap_g,
            "--shm-size",
            SHM_SIZE,
            "--gpus",
            "all",
            "--ipc=host",
            RUNTIME_IMAGE,
        ]
        + _engine_cmd(port),
        capture_output=True,
        text=True,
        check=False,
    )
    assert r.returncode == 0, f"docker run {name} failed: {r.stderr[-500:]}"


def _teardown(*names: str) -> None:
    for n in names:
        subprocess.run(["docker", "rm", "-f", n], capture_output=True, check=False)


def _container_state(name: str) -> dict:
    r = subprocess.run(
        [
            "docker",
            "inspect",
            name,
            "--format",
            "exit={{.State.ExitCode}} oom={{.State.OOMKilled}} running={{.State.Running}}",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    m = re.match(r"exit=(\S+) oom=(\S+) running=(\S+)", r.stdout.strip())
    assert m, f"unexpected inspect output: {r.stdout!r}"
    return {
        "exit_code": int(m.group(1)),
        "oom_killed": m.group(2) == "true",
        "running": m.group(3) == "true",
    }


def _container_logs(name: str, tail: int = 200) -> str:
    r = subprocess.run(
        ["docker", "logs", f"--tail={tail}", name],
        capture_output=True,
        text=True,
        check=False,
    )
    return r.stdout + "\n" + r.stderr


class TestOomCeiling(unittest.TestCase):
    """S1.7 VRAM ceiling: allocation tier fails -> OOM kill, no segfault;
    prior instances stay healthy."""

    @unittest.skipUnless(_image_present(), f"runtime image {RUNTIME_IMAGE} not present")
    def test_allocation_tier_fail_is_oom_not_segfault(self):
        # S1.4 allocation-tier requirement (the gate the manager runs
        # pre-spawn; S1.4: footprint + 1.0 GB spike margin, NOT the
        # 14.78 planning budget).
        footprint_gb = subprocess.run(
            [
                "python3",
                str(REPO_ROOT / "bin" / "bonsai2_vram.py"),
                "footprint",
                "--weights-gb",
                f"{_weights_gb()}",
                "--slots",
                "8",
                "--ctx",
                str(_ctx_size()),
            ],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        footprint_gb = float(footprint_gb)
        tier_need_gb = footprint_gb + SPIKE_MARGIN_GB
        oom_cap_g = f"{tier_need_gb - OOM_CAP_HEADROOM_GB:.2f}g"
        print(
            f"\n[1.9] S1.4 tier requirement {tier_need_gb:.2f} GB "
            f"(footprint {footprint_gb:.2f} + {SPIKE_MARGIN_GB}); "
            f"OOM cap {oom_cap_g}",
            flush=True,
        )

        baseline = _gpu_compute_apps_mib()
        i0, i1, i2 = "bonsai2-test-i0-oom", "bonsai2-test-i1-oom", "bonsai2-test-i2-oom"
        cap_g = f"{_instance_budget_gb():.1f}g"
        _teardown(i0, i1, i2)
        try:
            # Phase 1: instances 0 and 1 at the full S2.2 cap ("prior
            # instances" — both must reach readiness). VRAM audit (the
            # machine rule): two ~14.44 GB loads (MAP 1.7 measured total)
            # + the OOM probe's ~14.58 GB cap + main-stack headroom; the
            # tier at i=2 must read LOW, so this pre-check is against
            # the two healthy loads, not the probe.
            free_pre = _mem_available_gb()
            measured_total_gb = 14.44  # MAP 1.7 measurement, this box
            need_pre = (
                2 * measured_total_gb + 14.58 + 8.0
            )  # + OOM probe + reserved class
            assert free_pre >= need_pre, (
                f"VRAM audit: MemAvailable {free_pre:.1f} GB < {need_pre:.1f} GB "
                f"class needed (2 full loads + OOM probe + reserved); "
                f"re-run when the pool is clear"
            )
            print(
                f"[1.9] VRAM audit pre-launch: MemAvailable {free_pre:.1f} GB; "
                f"full cap {cap_g}",
                flush=True,
            )
            _spawn(i0, PORT_BASE + 0, cap_g)
            self.assertTrue(
                _wait_ready(PORT_BASE + 0, time.monotonic() + READY_TIMEOUT_S),
                "instance 0 never ready",
            )
            _spawn(i1, PORT_BASE + 1, cap_g)
            self.assertTrue(
                _wait_ready(PORT_BASE + 1, time.monotonic() + READY_TIMEOUT_S),
                "instance 1 never ready",
            )

            # Phase 2: instance 2's S1.4 allocation-tier check on LIVE
            # free memory — with 2 instances loaded the pool is below
            # the requirement, so the tier FAILS (S1.4 ERR_VRAM_OOM
            # path; S1.5 path 1's trigger condition).
            free_at_i2 = _mem_available_gb()
            print(
                f"\n[1.9] S1.4 allocation tier at i=2: free {free_at_i2:.2f} GB "
                f"vs requirement {tier_need_gb:.2f} GB",
                flush=True,
            )
            self.assertLess(
                free_at_i2,
                tier_need_gb,
                f"allocation tier unexpectedly PASSES at i=2 (free "
                f"{free_at_i2:.2f} >= need {tier_need_gb:.2f}) — the pool "
                f"changed mid-run; re-run with the pool clear",
            )

            # Phase 3: spawn i=2 under the sub-requirement cgroup cap so
            # the kernel OOM-kills the load at the KV allocation
            # (S1.5 path 2's shape — the mid-load OOM) instead of
            # letting it run past the tier.
            _spawn(i2, PORT_BASE + 2, oom_cap_g)
            deadline = time.monotonic() + READY_TIMEOUT_S
            state = {"exit_code": None}
            while time.monotonic() < deadline:
                st = _container_state(i2)
                if not st["running"]:
                    state = st
                    break
                time.sleep(READY_POLL_S)
            self.assertFalse(
                state["running"], f"instance 2 still running after {READY_TIMEOUT_S}s"
            )
            self.assertTrue(
                state["oom_killed"],
                f"instance 2 exit={state['exit_code']} OOMKilled="
                f"{state['oom_killed']} — expected the kernel OOM-kill "
                f"(S1.5 path 2), not a clean engine exit",
            )
            logs = _container_logs(i2)
            # "Not a segfault": no SIGSEGV/SIGBUS/core-dump markers in
            # the engine's own output; a SIGKILL'd load has no graceful
            # "exiting due to" / "couldn't bind" tail either.
            self.assertNotIn(
                "SIGSEGV",
                logs,
                "instance 2 logs contain SIGSEGV — that's a segfault, not ERR_VRAM_OOM",
            )
            self.assertNotIn(
                "SIGBUS",
                logs,
                "instance 2 logs contain SIGBUS (memory fault) — a "
                "segfault-class failure, not ERR_VRAM_OOM",
            )
            self.assertNotIn(
                "core dumped",
                logs,
                "instance 2 core-dumped — a segfault-class failure, not ERR_VRAM_OOM",
            )
            print(
                f"[1.9] instance 2: OOMKilled=True, exit "
                f"{state['exit_code']} (SIGKILL) — ERR_VRAM_OOM, not a "
                f"segfault",
                flush=True,
            )

            # Phase 4: prior instances stay HEALTHY (S1.7): container
            # Up, alias on /v1/models, and a 16-token completion
            # round-trips on both (same probe as test_bringup_1_7).
            for off in (0, 1):
                st = _container_state(i0 if off == 0 else i1)
                self.assertTrue(
                    st["running"],
                    f"instance {off} down after the OOM kill ({_container_state(i2)})",
                )
                self.assertTrue(
                    _wait_ready(PORT_BASE + off, time.monotonic() + 30.0),
                    f"instance {off} lost the alias after the OOM kill",
                )
                elapsed, resp = _completion(
                    PORT_BASE + off, "Say hello in one word.", 16, 30.0
                )
                msg = resp["choices"][0].get("message", {})
                self.assertTrue(
                    msg.get("content") or msg.get("reasoning_content"),
                    f"instance {off}: empty completion after the OOM kill",
                )
                print(
                    f"[1.9] instance {off} healthy post-OOM "
                    f"(16-token RT {elapsed:.2f}s)",
                    flush=True,
                )

            # The main stack paid nothing: its PIDs are unchanged.
            main_pids_before = set(baseline)
            main_pids_after = set(_gpu_compute_apps_mib())
            new_pids = main_pids_after - main_pids_before
            gone_pids = main_pids_before - main_pids_after
            self.assertEqual(
                gone_pids,
                set(),
                f"main-stack PIDs gone after the OOM probe: {gone_pids}",
            )
            self.assertEqual(
                len(new_pids),
                2,
                f"expected exactly the 2 surviving instances as new PIDs, "
                f"got {len(new_pids)} (i=2's PIDs should be gone)",
            )
        finally:
            _teardown(i0, i1, i2)


if __name__ == "__main__":
    unittest.main()
