"""MAP 1.9 / S1.7 VRAM ceiling: load instances until the S1.4 allocation
tier fails -> the last failure is ERR_VRAM_OOM, not a segfault; prior
instances stay healthy.

Spec text (S1.7, "VRAM ceiling"): load instances until the S1.4
allocation tier fails -> assert the last failure is `ERR_VRAM_OOM`, not a
segfault, and prior instances stay healthy.

Method (docker-level, pre-manager — the P1 shape; S2's manager IS this
loop, this test reproduces it from live free memory):

  * instances 0 and 1 boot sequentially at the full S2.2 `--memory` cap
    (per_instance_budget_gb from the template) and each reach /v1/models
    readiness — the "prior instances". The S1.4 allocation tier runs
    BEFORE each spawn (free >= instance_footprint_GB + 1.0 GB spike
    margin; the footprint comes from `bin/bonsai2_vram` so it cannot
    drift from the formula; free = /proc/meminfo MemAvailable, the
    S1.4 "cgroup/nvidia-smi free" read on this GB10 — see the
    measurement notes). It passes at i = 0/1 and FAILS at i = 2 (live
    free ~14.8 GB vs the 15.08 GB requirement, probed 2026-09-22) ->
    the recorded last failure is the S1.4 `ERR_VRAM_OOM` named
    condition, the load stops — S1.5 path 1: the pre-allocation gate
    catches it before the instance's KV allocation
  * "not a segfault": the recorded failure is the NAMED gate condition
    (a clean, deterministic reject with free/need in the log), and no
    spawned container's log contains SIGSEGV/SIGBUS/core-dump markers
  * a path-2 demonstration spawn follows at i = 2 with its cgroup cap
    set BELOW the tier requirement (6.0 GB < 15.08 GB — S1.5 path 2's
    "the tier passed and free memory moved in the gap" shape, forced):
    if the kernel OOM-kills the load (OOMKilled, exit 137-class) that's
    clean; if the warm page cache lets the load split across host/CUDA
    instead (probed 2026-09-22: file-backed mmap'd weights are
    reclaimable, so a sub-requirement cap does NOT guarantee an OOM
    kill on this box — the engine logs "layer 0 is assigned to device
    CPU" and serves) that is the path-2 "kernel manages it" shape and
    is accepted. Both outcomes assert the segfault-marker check.
  * prior instances 0/1 stay HEALTHY afterwards: containers Up,
    /v1/models still returns the alias on both ports, and a
    16-token completion round-trips on both (the S1.7 "stay healthy"
    half, same probe as test_bringup_1_7)
  * the main stack's PIDs (nvidia-smi compute-apps baseline) are
    unchanged — the ceiling cost instance 2's tier, not the main stack

Measurement notes (box-specific, probed 2026-09-22):
  * /proc/meminfo MemAvailable is the free-memory read on this GB10
    (the MAP 1.7 convention: nvidia-smi `--query-gpu` memory fields are
    N/A on unified memory). Probe numbers: ~43.7 GB base; ~29.2 GB
    after one 14.78 g-capped instance; ~14.8 GB after two (each load
    nets ~14.44 GB, the MAP 1.7 measured total incl. overhead). The
    tier requirement 15.08 GB = instance_footprint_GB(7.21 GB,
    8 slots, 262144) + 1.0 GB = 14.08 + 1.0.
  * The tier fails at i=2 by only ~0.27 GB — the test asserts the pool
    state (free in a sane band [5 GB, need) at i=2) so a drift in
    main-stack usage fails it loudly instead of changing its meaning.
  * A cgroup cap below the S1.4 requirement does NOT by itself force an
    OOM kill here: the engine's GGUF weights are mmap'd file pages
    (reclaimable under cgroup pressure; probe: a 6.0 g cap still
    loads, splitting the fused-ops layers to CPU), and the KV is
    device-allocated. The kernel OOM path (S1.5 path 2) fires on
    concurrent allocation in the gap after the tier — the manager's
    5-min recheck (S2.5) is where it lands in P2; at the P1 @gpu level
    the tier gate IS the named ERR_VRAM_OOM, and the path-2 spawn is
    the demonstration, asserted in both outcomes.
  * Sequential boot (i=0 ready, then i=1): two concurrent cold loads
    from the same image are ~30 s slower each on this box (page-cache
    contention) and can trip a readiness window — the S2.5 "waits each
    to healthy before the next" order, not a tuned constant.
  * This is the first N > 1 experiment on the box (the MVP safety rule
    loads exactly 1 until now; 1.9 is the ceiling probe that justifies
    the N > 1 path). VRAM audit per the machine rule: pre-launch
    baseline in the log; free memory asserted >= ~37 GB before the two
    full-cap loads (the test is invalid — and fails loudly — if
    someone else ate the pool mid-run).
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
# S1.4 allocation tier: free >= instance_footprint_GB + 1.0 GB (the
# true footprint + the 1.0 GB spike margin, NOT the 14.78 planning
# budget — S1.4 verbatim).
SPIKE_MARGIN_GB = 1.0
# Path-2 demonstration cap at i=2 (probe 2026-09-22: sub-requirement
# cap -> the kernel manages the split; OOM-kill or not, no segfault).
PATH2_CAP_G = "6.0g"
# Sane-band assertions on the live pool (the probe numbers above):
# before the two full-cap loads free must cover them plus headroom;
# at i=2 the tier must read low but not absurdly (0 GB free would
# mean something else ate the pool and the test is invalid).
MIN_FREE_BEFORE_LOADS_GB = 37.0
TIER_FREE_BAND_LO_GB = 5.0
# S1.4's bounded free read: 5 s timeout per probe, one retry, then
# treat free as 0 (S1.4 verbatim; /proc/meminfo reads are instant —
# the bound shapes the readiness poll's urlopen timeout below).
FREE_PROBE_TIMEOUT_S = 5.0


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
    """Poll /v1/models for the alias; True if seen before deadline.
    This doubles as the S1.4 bounded-read shape: the 5 s urlopen
    timeout is the per-probe bound, the 2 s poll the retry rhythm."""
    last_err = "not polled yet"
    while time.monotonic() < deadline:
        try:
            models = _http_json(
                f"http://127.0.0.1:{port}/v1/models", FREE_PROBE_TIMEOUT_S
            )
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


def _container_logs(name: str) -> str:
    # No --tail: the segfault-marker assertions read the whole log.
    r = subprocess.run(
        ["docker", "logs", name],
        capture_output=True,
        text=True,
        check=False,
    )
    return r.stdout + "\n" + r.stderr


def _allocation_tier(free_gb: float, footprint_gb: float) -> tuple[bool, str]:
    """The S1.4 pre-spawn allocation tier (the manager's gate, S1.4
    verbatim): free >= instance_footprint_GB + 1.0 GB. Returns
    (pass, log_line); a fail carries the S1.4 ERR_VRAM_OOM named
    condition with free/need filled in (the WARN_VRAM_SHORTFALL
    shape)."""
    need = footprint_gb + SPIKE_MARGIN_GB
    if free_gb >= need:
        return True, f"tier PASS: free {free_gb:.2f} GB >= need {need:.2f} GB"
    return (
        False,
        f"ERR_VRAM_OOM (S1.4 allocation tier): free {free_gb:.2f} GB < need {need:.2f} GB",
    )


SEGFAULT_MARKERS = ("SIGSEGV", "SIGBUS", "core dumped", "Segmentation fault")


class TestOomCeiling(unittest.TestCase):
    """S1.7 VRAM ceiling: load until the S1.4 allocation tier fails ->
    ERR_VRAM_OOM, not a segfault; prior instances stay healthy."""

    @unittest.skipUnless(_image_present(), f"runtime image {RUNTIME_IMAGE} not present")
    def test_allocation_tier_fail_is_oom_not_segfault(self):
        # S1.4 allocation-tier requirement (the gate the manager runs
        # pre-spawn; S1.4: footprint + 1.0 GB spike margin, NOT the
        # 14.78 planning budget). The footprint comes from
        # bin/bonsai2_vram so the gate and the formula can't drift.
        footprint_gb = float(
            subprocess.run(
                [
                    "python3",
                    str(REPO_ROOT / "bin" / "bonsai2_vram.py"),
                    "footprint",
                    "--weights-gb",
                    f"{_weights_gb():.6f}",
                    "--slots",
                    "8",
                    "--ctx",
                    str(_ctx_size()),
                ],
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()
        )
        need_gb = footprint_gb + SPIKE_MARGIN_GB
        print(
            f"\n[1.9] S1.4 allocation tier: need {need_gb:.2f} GB "
            f"(footprint {footprint_gb:.2f} + {SPIKE_MARGIN_GB} GB spike margin)",
            flush=True,
        )

        baseline = _gpu_compute_apps_mib()  # main-stack PIDs, pre-launch
        i0, i1, i2 = (
            "bonsai2-test-i0-oom",
            "bonsai2-test-i1-oom",
            "bonsai2-test-i2-oom",
        )
        cap_g = f"{_instance_budget_gb():.2f}g"
        _teardown(i0, i1, i2)
        try:
            # VRAM audit (the machine rule): two ~14.44 GB loads
            # (MAP 1.7 measured total) + the path-2 probe + main-stack
            # headroom must fit BEFORE anything boots.
            free_pre = _mem_available_gb()
            print(
                f"[1.9] VRAM audit pre-launch: MemAvailable {free_pre:.1f} GB; "
                f"full cap {cap_g}",
                flush=True,
            )
            self.assertGreaterEqual(
                free_pre,
                MIN_FREE_BEFORE_LOADS_GB,
                f"MemAvailable {free_pre:.1f} GB < {MIN_FREE_BEFORE_LOADS_GB} GB "
                f"— the pool is not clear for the two full-cap loads; re-run "
                f"when it is (the VRAM audit rule)",
            )

            # Phase 1: the load loop. i=0 and i=1: the tier passes and
            # the instance reaches readiness (sequential boot — the
            # S2.5 "waits each to healthy before the next" order).
            for i in (0, 1):
                free = _mem_available_gb()
                ok, line = _allocation_tier(free, footprint_gb)
                self.assertTrue(
                    ok,
                    f"allocation tier unexpectedly FAILED at i={i}: {line} "
                    f"— the pool drifted, re-run with it clear",
                )
                print(f"[1.9] i={i}: {line}", flush=True)
                _spawn(i0 if i == 0 else i1, PORT_BASE + i, cap_g)
                self.assertTrue(
                    _wait_ready(PORT_BASE + i, time.monotonic() + READY_TIMEOUT_S),
                    f"instance {i} never ready (the load loop's "
                    f"healthy-before-next gate, S2.5 shape)",
                )

            # Phase 2: i=2 — the tier runs on LIVE free memory (two
            # instances now loaded) and FAILS: the recorded last
            # failure is the S1.4 ERR_VRAM_OOM named condition.
            free_at_i2 = _mem_available_gb()
            ok, line = _allocation_tier(free_at_i2, footprint_gb)
            print(f"[1.9] i=2: {line}", flush=True)
            self.assertFalse(
                ok,
                f"allocation tier unexpectedly PASSES at i=2 "
                f"(free {free_at_i2:.2f} GB >= need {need_gb:.2f} GB) — "
                f"the pool changed mid-run; re-run with it clear",
            )
            self.assertGreaterEqual(
                free_at_i2,
                TIER_FREE_BAND_LO_GB,
                f"free {free_at_i2:.2f} GB at i=2 is below the sane band "
                f"[{TIER_FREE_BAND_LO_GB} GB, {need_gb:.2f} GB) — something "
                f"else ate the pool; the failure is valid but the run "
                f"is not comparable, re-run",
            )
            self.assertIn("ERR_VRAM_OOM", line)  # the named condition

            # Phase 3: the path-2 demonstration spawn at i=2 — the tier
            # was bypassed (S1.5 path 2: "the tier passed and free
            # memory moved in the gap"), with a cgroup cap below the
            # requirement. Two acceptable outcomes, one rejected one:
            #   * OOMKilled=True  -> the kernel OOM-kill (exit
            #     137-class): clean
            #   * still running   -> the kernel managed the split
            #     (file-backed weights reclaimable — probe 2026-09-22):
            #     clean, the engine serves at reduced placement
            #   * exited without OOMKilled -> the engine's own error:
            #     the segfault-marker check below is what keeps it
            #     from passing silently
            # "Not a segfault" = no SIGSEGV/SIGBUS/core-dump markers in
            # any spawned container's log, in either outcome.
            _spawn(i2, PORT_BASE + 2, PATH2_CAP_G)
            deadline = time.monotonic() + READY_TIMEOUT_S
            state = None
            while time.monotonic() < deadline:
                st = _container_state(i2)
                if not st["running"]:
                    state = st
                    break
                time.sleep(READY_POLL_S)
            if state is None:
                state = _container_state(i2)  # still running: the split shape
            print(
                f"[1.9] i=2 path-2 spawn: OOMKilled={state['oom_killed']}, "
                f"running={state['running']}, exit={state['exit_code']}",
                flush=True,
            )
            for n in (i0, i1, i2):
                logs = _container_logs(n)
                for marker in SEGFAULT_MARKERS:
                    self.assertNotIn(
                        marker,
                        logs,
                        f"{n}: '{marker}' in the log — a segfault-class "
                        f"failure, not ERR_VRAM_OOM",
                    )
            if not state["running"]:
                self.assertTrue(
                    state["oom_killed"],
                    f"i=2 exited code {state['exit_code']} without "
                    f"OOMKilled — the engine's own error, not the "
                    f"kernel OOM path: {_container_logs(i2)[-400:]}",
                )
            else:
                # The split shape: the path-2 instance still serves
                # (that is the point of the demonstration — the kernel
                # managed the under-budget load, it did not crash).
                self.assertTrue(
                    _wait_ready(PORT_BASE + 2, time.monotonic() + 60.0),
                    "i=2 running but its port never served the alias "
                    "(the split shape should still be a serving engine)",
                )
                print("[1.9] i=2 split shape: serving at the 6.0 g cap", flush=True)

            # Phase 4: prior instances stay HEALTHY (S1.7): containers
            # Up, alias on /v1/models, and a 16-token completion
            # round-trips on both (same probe as test_bringup_1_7).
            for off, n in ((0, i0), (1, i1)):
                st = _container_state(n)
                self.assertTrue(
                    st["running"], f"instance {off} down after the ceiling probe"
                )
                self.assertTrue(
                    _wait_ready(PORT_BASE + off, time.monotonic() + 30.0),
                    f"instance {off} lost the alias after the ceiling probe",
                )
                elapsed, resp = _completion(
                    PORT_BASE + off, "Say hello in one word.", 16, 30.0
                )
                msg = resp["choices"][0].get("message", {})
                self.assertTrue(
                    msg.get("content") or msg.get("reasoning_content"),
                    f"instance {off}: empty completion after the ceiling probe",
                )
                print(
                    f"[1.9] instance {off} healthy post-probe "
                    f"(16-token RT {elapsed:.2f}s)",
                    flush=True,
                )

            # The main stack paid nothing: its PIDs are unchanged.
            self.assertEqual(
                set(baseline),
                set(_gpu_compute_apps_mib()),
                "main-stack PIDs changed during the ceiling probe "
                "(the OOM should cost instance 2's tier only)",
            )
        finally:
            _teardown(i0, i1, i2)


if __name__ == "__main__":
    unittest.main()
