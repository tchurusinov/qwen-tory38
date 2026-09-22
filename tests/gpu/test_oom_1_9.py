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
    spawned container's log contains SIGSEGV/SIGBUS/core-dump markers.
    The failure is forced with a host-side memory holder (a touched
    bytearray cutting MemAvailable to ~4 GB below the requirement),
    because MemAvailable counts reclaimable page cache and the pool is
    not guaranteed below the requirement after two loads on its own
  * a path-2 demonstration spawn follows at i = 2 with its cgroup cap
    set BELOW the tier requirement (6.0 GB < 15.08 GB), started while
    the holder still holds free down — S1.5 path 2's "free memory
    moved in the gap" shape, forced to be deterministic. The holder
    is released as soon as the spawn has started, and the outcome is
    measured in steady state on the loose pool (G3c round-1 P1: a
    mid-teardown outcome races the pool transient). If the kernel
    OOM-kills the load (OOMKilled, exit 137-class) that's clean; if
    the warm page cache lets the load split across host/CUDA instead
    (probed 2026-09-22: file-backed mmap'd weights are reclaimable,
    so a sub-requirement cap does NOT guarantee an OOM kill on this
    box — the engine logs "layer 0 is assigned to
    device CPU" and serves) that is the path-2 "kernel manages it"
    shape and is accepted. Every outcome asserts the segfault-marker
    check and the exit-code guard (no 135/139 signal deaths).
  * prior instances 0/1 stay HEALTHY afterwards: containers Up,
    /v1/models still returns the alias on both ports, and a
    16-token completion round-trips on both (the S1.7 "stay healthy"
    half, same probe as test_bringup_1_7)
  * the main stack's PIDs (nvidia-smi compute-apps baseline) are
    unchanged — the ceiling cost instance 2's tier, not the main stack

Measurement notes (box-specific, probed 2026-09-22):
  * /proc/meminfo MemAvailable is the free-memory read on this GB10
    (the MAP 1.7 convention: nvidia-smi `--query-gpu` memory fields are
    N/A on unified memory), read as decimal GB like the S1.4 formula
    (kB / 1e6). Probe numbers: ~45.9 base; ~31.5 after one 14.78 g
    capped instance; ~16.8 after two (each load nets ~14.44, the MAP
    1.7 measured total incl. overhead). The S1.4 formula is decimal-GB
    throughout (the spec's 7.21 GB = 7206168928 / 1e9): the tier
    requirement 15.08 GB = instance_footprint_GB(7.206 GB, 8 slots,
    262144) + 1.0 GB = 14.08 + 1.0 — the weights are read from the
    template's pinned size_bytes in decimal GB so the tier and the
    formula can't drift in units.
  * The tier fails at i=2 by ~0.27 GB when the pool is in its probe
    state — the test asserts the pool state (free in a sane band
    [5 GB, need) at i=2) so a drift in main-stack usage fails it
    loudly instead of changing its meaning.
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
import sys
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
# Sane-band assertions on the live pool (the probe numbers above,
# decimal GB): before the two full-cap loads free must cover them
# plus headroom (probe: ~50.9 base; the holder sized from the live
# read absorbs the variance, so this is a pool-CLEARED check, not a
# precise budget); at i=2 the tier must read low but not absurdly
# (0 GB free would mean something else ate the pool and the test is
# invalid).
MIN_FREE_BEFORE_LOADS_GB = 33.0
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
    # S1.1: the default quant (PQ2_0) byte size pinned in the template,
    # in DECIMAL GB (1e9) — the S1.4 formula is decimal-GB throughout
    # (its worked example: 7.21 GB weights = 7206168928 / 1e9; 14.08 =
    # 7.21 + 6.87). A GiB division would under-size the footprint.
    return _yaml_value("size_bytes", r"(?m)^[ \t]*size_bytes:[ \t]*([0-9.]+)") / 1e9


def _ctx_size() -> int:
    return int(_yaml_value("ctx_size", r"(?m)^[ \t]*ctx_size:[ \t]*([0-9.]+)"))


def _mem_available_gb() -> float:
    """The S1.4 tier's free-memory read on this GB10, in DECIMAL GB
    (kB / 1e6) so it is directly comparable with the S1.4 formula's
    decimal-GB requirement (see module doc)."""
    for line in Path("/proc/meminfo").read_text().splitlines():
        if line.startswith("MemAvailable:"):
            return int(line.split()[1]) / 1e6
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


def _probe_serving_once(port: int) -> bool:
    """A single /v1/models check (True if the alias is served right now).
    Used inside the path-2 outcome poll to detect the split shape's
    "now serving" transition without committing to the full _wait_ready
    window (G3c round-1 P1: the path-2 outcome is a steady-state
    measurement, re-evaluated each poll on the loose pool)."""
    try:
        models = _http_json(f"http://127.0.0.1:{port}/v1/models", FREE_PROBE_TIMEOUT_S)
        return ALIAS in (m.get("id") for m in models.get("data", []))
    except (
        urllib.error.URLError,
        json.JSONDecodeError,
        ConnectionError,
        TimeoutError,
        OSError,
    ):
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


SEGFAULT_MARKERS = ("SIGSEGV", "SIGBUS", "core dumped")
# (G3c round-1 P1: the literal "Segmentation fault" phrase is NOT a
# marker here — the container's PID 1 is the engine (the nvidia
# entrypoint execs it), so no shell prints the phrase, and the pinned
# engine's libs contain no such string (probed 2026-09-22: 0 hits in
# all .so's). A real SIGSEGV dies with exit 139 / SIGBUS with 135,
# which the exit-code guard below catches.)


def _start_memory_holder(size_gb: float):
    """S1.5 path 2's "free memory moved in the gap", made deterministic:
    a host-side process that allocates + touches `size_gb` GB of
    anonymous memory (bytearray zero-fill touches every page) and holds
    it. It reduces /proc/meminfo MemAvailable the way a concurrent
    allocation would — the exact condition the S1.4 tier protects
    against — and is killed after the path-2 demonstration. Returns the
    Popen handle.

    NOTE (probe 2026-09-22): MemAvailable counts reclaimable page cache,
    so a `size_gb` allocation drops MemAvailable by LESS than size_gb
    (the kernel reclaims clean page cache to satisfy part of it — ~4.9
    GB reclaimed on this box after two 7 GB model loads). Size the
    holder with a guard large enough to exceed that reclamation."""
    return subprocess.Popen(
        [
            sys.executable,
            "-c",
            (
                "import time, sys; "
                "b = bytearray(int(sys.argv[1])); "
                "print(len(b), flush=True); time.sleep(3600)"
            ),
            str(int(size_gb * 1e9)),  # decimal GB, matching _mem_available_gb
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _wait_free_below(target_gb: float, timeout_s: float) -> float:
    """Poll MemAvailable until it drops below target_gb (the holder's
    pages are committed once; MemAvailable then settles). Returns the
    final free reading. Bounded so a wedged pool can't hang the test
    (S1.4's "bounded free read" discipline)."""
    deadline = time.monotonic() + timeout_s
    free = _mem_available_gb()
    while time.monotonic() < deadline and free >= target_gb:
        time.sleep(2.0)
        free = _mem_available_gb()
    return free


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

            # Phase 2: i=2 — the tier runs on LIVE free memory and
            # FAILS: the recorded last failure is the S1.4 ERR_VRAM_OOM
            # named condition. The failure is DETERMINISTIC, not
            # pool-state luck: MemAvailable on this box counts
            # reclaimable page cache (probe 2026-09-22: ~19.9 still
            # "available" after two 14.44 GB loads), so the pool is not
            # guaranteed below the requirement on its own — a host-side
            # memory holder cuts free to ~4 GB below it (the S1.5
            # path-2 "free memory moved in the gap" condition, forced).
            free_unheld = _mem_available_gb()
            # Holder sizing: start from the NOMINAL cut (holder =
            # free_unheld - target). The kernel may reclaim clean page
            # cache to satisfy part of it (probe 2026-09-22: ~4.9 GB
            # reclaimed out of a 9.4 GB holder), which would leave
            # free above the target — so poll, and if the nominal cut
            # didn't reach it, top up with an additional holder for
            # the shortfall. At most 3 rounds: a pool that needs more
            # than that of touching memory has drifted, fail loudly.
            target_free_gb = need_gb - 3.0  # land 3 GB below the need
            holders = []
            for round_no in range(3):
                free_now = _mem_available_gb()
                if free_now < target_free_gb:
                    break
                cut = free_now - target_free_gb
                holders.append(_start_memory_holder(cut))
                time.sleep(2.0)  # let the pages commit
                free_settled = _wait_free_below(target_free_gb, 20.0)
                if round_no == 0:
                    print(
                        f"[1.9] i=2 holder round 1: cut {cut:.1f} GB "
                        f"-> free {free_settled:.1f} GB "
                        f"(reclaimed {cut - (free_unheld - free_settled):.1f} GB)",
                        flush=True,
                    )
            free_at_i2 = _mem_available_gb()
            ok, line = _allocation_tier(free_at_i2, footprint_gb)
            print(
                f"[1.9] i=2 ({len(holders)} holder(s), "
                f"{free_unheld - free_at_i2:.1f} GB held): {line} "
                f"(free unheld {free_unheld:.2f} GB)",
                flush=True,
            )
            self.assertFalse(
                ok,
                f"allocation tier unexpectedly PASSES at i=2 "
                f"(free {free_at_i2:.2f} GB >= need {need_gb:.2f} GB) — "
                f"holder sizing hypothesis failed: {len(holders)} holder(s) "
                f"touching {free_unheld - free_at_i2:.1f} GB of anonymous "
                f"memory did not cut free below the target "
                f"{target_free_gb:.2f} GB — page-cache reclamation absorbed "
                f"the touched memory (the holder is the kernel's "
                f"least-protected allocation); re-run with the pool in "
                f"its probe state",
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
            # just failed, but the manager's stricter gate only holds
            # until the next recheck (S1.4: "the value is re-read on
            # the S2.5 5-min recheck") — so the spawn in the gap is
            # S1.5 path 2, with a cgroup cap below the requirement
            # while the holder(s) still hold free down. Three observed
            # outcomes, all clean (S1.7: "not a segfault") — which one
            # appears depends on how tight the pool is (probe
            # 2026-09-22, the same spawn at three pool states):
            #   * OOMKilled=True (exit 137, free ~11.0 GB) -> the
            #     kernel's cgroup OOM-kill: the clean ERR_VRAM_OOM
            #     kernel path
            #   * running + serving (observed at free ~14.1 GB and,
            #     holder-driven, at ~12.1 GB)            -> the
            #     kernel managed the split (file-backed weights
            #     reclaimable): clean, serves at reduced placement
            #   * exited without OOMKilled (exit 133
            #     SIGTRAP, free ~10.6 GB)                  -> the
            #     engine's own clean error on the under-budget
            #     allocation: clean, "not a segfault"
            # Measurement shape (G3c round-1 P1: no mid-teardown
            # outcome): the spawn starts while the holder holds the
            # pool tight (that IS the "free memory moved in the gap"
            # condition — a cgroup OOM-kill, if it is going to
            # happen, lands in this window), then the holder is
            # released and the OUTCOME is polled in steady state on
            # the loose pool for the full 180 s spawn budget: a cold
            # 6.0 g load is slow (~114 s, probe 2026-09-22), so the
            # outcome may not be settled by the time the holder goes.
            # The one rejected outcome — a segfault-class crash — is
            # caught by the marker loop + exit-code guard below
            # across ALL spawned containers (no SIGSEGV/SIGBUS/
            # core-dump log markers; no 135/139 signal deaths; a
            # clean exit 0 under budget would mean the spawn
            # "succeeded", not the path-2 failure).
            print(
                f"[1.9] i=2 path-2: holder(s) up (MemAvailable "
                f"{_mem_available_gb():.1f} GB), spawning at {PATH2_CAP_G}",
                flush=True,
            )
            _spawn(i2, PORT_BASE + 2, PATH2_CAP_G)
            # Settle in the tight pool first (the cgroup limit is in
            # effect and the load is allocating — the OOM-kill window).
            time.sleep(15.0)
            # Release the holder(s) and let the pool settle, so the
            # outcome and phase 4 run on a normal pool (G3c round-1
            # P1 + P2: no mid-teardown outcome; a short settle so the
            # pool reading below is not still falling).
            for h in holders:
                if h.returncode is None:
                    h.terminate()
                    h.wait(timeout=30)
            time.sleep(5.0)
            pool_after = _mem_available_gb()
            print(
                f"[1.9] i=2 path-2: holder(s) released (MemAvailable "
                f"{pool_after:.1f} GB); polling outcome in steady state "
                f"(budget {READY_TIMEOUT_S:.0f}s)",
                flush=True,
            )
            t_outcome = time.monotonic()
            served = False
            deadline = t_outcome + READY_TIMEOUT_S
            while time.monotonic() < deadline:
                st = _container_state(i2)
                if not st["running"]:
                    break
                if _probe_serving_once(PORT_BASE + 2):
                    served = True
                    break
                time.sleep(READY_POLL_S)
            state = _container_state(i2)
            print(
                f"[1.9] i=2 path-2 outcome after "
                f"{time.monotonic() - t_outcome:.0f}s: "
                f"OOMKilled={state['oom_killed']}, "
                f"running={state['running']}, exit={state['exit_code']}, "
                f"served={served}",
                flush=True,
            )
            for n in (i0, i1, i2):
                st_n = _container_state(n)
                logs = _container_logs(n)
                for marker in SEGFAULT_MARKERS:
                    self.assertNotIn(
                        marker,
                        logs,
                        f"{n}: '{marker}' in the log — a segfault-class "
                        f"failure, not ERR_VRAM_OOM",
                    )
                # Exit-code guard (G3c round-1 P1): a real SIGSEGV/
                # SIGBUS dies 139/135; the accepted non-zero exits
                # are 137 (cgroup OOM) and the engine's own clean
                # error (observed 133/SIGTRAP at the pinned commit).
                if not st_n["running"]:
                    self.assertNotIn(
                        st_n["exit_code"],
                        (135, 139),
                        f"{n}: exit {st_n['exit_code']} — a "
                        f"SIGBUS/SIGSEGV signal death, not "
                        f"ERR_VRAM_OOM",
                    )
            if not state["running"]:
                # Exited: either the kernel's cgroup OOM-kill
                # (OOMKilled=True, exit 137) or the engine's own
                # clean error on the under-budget allocation
                # (non-zero exit, OOMKilled=False — observed as
                # 133/SIGTRAP at free ~10.6 GB, probe 2026-09-22).
                # Both are the path-2 demonstration; a zero exit
                # (clean stop, no failure) is not.
                self.assertTrue(
                    state["oom_killed"] or state["exit_code"] != 0,
                    f"i=2 exited {state['exit_code']} without OOMKilled "
                    f"or an error exit — the spawn cleanly 'succeeded' "
                    f"under the path-2 cap; pool was looser than the "
                    f"tier gate implied, re-run: "
                    f"{_container_logs(i2)[-400:]}",
                )
                print(
                    f"[1.9] i=2 path-2 failure shape: "
                    f"{'kernel cgroup OOM-kill' if state['oom_killed'] else 'engine clean error'} "
                    f"(exit {state['exit_code']}, OOMKilled={state['oom_killed']})",
                    flush=True,
                )
            else:
                # The split shape: i2 survived and is a SERVING
                # engine at the 6.0 g cap, measured in steady state
                # on the loose pool (the kernel managed the
                # under-budget load — it did not crash).
                self.assertTrue(
                    served,
                    f"i=2 running but its port never served the alias "
                    f"within the {READY_TIMEOUT_S:.0f}s steady-state "
                    f"outcome window (the split shape should still "
                    f"be a serving engine); log tail: "
                    f"{_container_logs(i2)[-300:]}",
                )
                print(
                    f"[1.9] i=2 split shape: serving at the 6.0 g cap "
                    f"(outcome after {time.monotonic() - t_outcome:.0f}s)",
                    flush=True,
                )

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

            # The main stack paid nothing: its PIDs are all still in
            # the compute-apps listing (a subset, not equality — the
            # spawned instances' PIDs are legitimately ADDED, and
            # nvidia-smi can lag reaping a dead container's entry,
            # probe 2026-09-22: a torn-down container's PID lingered
            # in the listing for minutes).
            after_pids = _gpu_compute_apps_mib()
            lost = set(baseline) - set(after_pids)
            self.assertEqual(
                lost,
                set(),
                f"main-stack PIDs lost from the compute-apps listing "
                f"during the ceiling probe: {lost}",
            )
        finally:
            _teardown(i0, i1, i2)


if __name__ == "__main__":
    unittest.main()
