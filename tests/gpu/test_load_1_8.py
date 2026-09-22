"""MAP 1.8 / S1.7 sustained load: 8 concurrent completions for 120 s.

Acceptance (S1.7 integration, S1.8 #3): with `--parallel 8` (the S1.3
lock), a 120 s window of 8-way concurrent completions must deliver
aggregate throughput >= 26.2 t/s (0.9 x the 29.16 t/s community
baseline — SPEC's `0.9 x community-baseline`) and zero 5xx responses.

Method (matches the live MAP 1.8 measurement, 2026-09-21: 50.7 t/s
aggregate, 384 ok, 0x 5xx on this box):
  * instance 0 cold-loads from the runtime image (S2.2 shape, baked
    CMD) — the SPEC's "cold-load one instance" half; readiness is
    polled on /v1/models (no 5xx here: /v1/models is not a completion)
  * ONE warm-up completion after readiness primes the first-decode
    path (CUDA kernels, first KV alloc) so the 120 s window measures
    sustained throughput, not the very first cold decode
  * 8 worker threads each loop short completions (32-token cap) for
    the full 120 s window; only responses that COMPLETE inside the
    window count
  * aggregate t/s = sum(completion_tokens) / 120 s
  * any HTTP 500-599 in the WINDOW fails the test (the SPEC's "zero
    5xx" applies to the sustained window; the load-time 503 "Loading
    model" belongs to the cold-load half, not the window)

Measurement notes:
  * completion tokens come from `usage.completion_tokens` in the
    non-streaming response — the engine's own accounting, not a client
    estimate.
  * With the measured ~50-62 t/s vs the 26.2 t/s floor the warm-up
    cannot sink the aggregate.
  * Transient non-5xx failures (connection reset, read timeout) are
    tallied in `err_other` and do NOT fail the run — the SPEC's
    acceptance is "zero 5xx"; a blip costs its tokens, not the test.
  * One box, one port 30101: the container is always torn down in
    `finally`.
"""

import json
import re
import shutil
import subprocess
import time
import unittest
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
ENGINE_YAML = REPO_ROOT / "etc" / "subagent-engine.yaml"
RUNTIME_IMAGE = "bonsai2-server:1.0"
CONTAINER = "bonsai2-test-i0-load"
PORT = 30101  # S1.3 instance 0 (the image's baked default)
API_KEY = "bonsai2-local"  # S1.3 --api-key (local-only)
ALIAS = "qwen38-27b-subagent"  # S1.3 --alias
# S2.2 --memory cap = S1.4 per_instance_budget_gb — read from the YAML
# at test time (same source as test_bringup_1_7, so the two @gpu tests
# can't drift if the template is re-anchored; the re-anchored value is
# 14.78).
CONCURRENT = 8  # S1.3 --parallel (max_num_seqs=8)
DURATION_S = 120  # S1.7: "for 120 s"
MIN_AGG_TPS = 26.2  # S1.7: 0.9 x 29.16 community baseline
READY_TIMEOUT_S = 180
READY_POLL_S = 2.0
PROMPT = "Count from 1 to 10, one number per line."
MAX_TOKENS = 32


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


def _gpu_compute_apps_mib() -> dict:
    """pid -> used MiB from nvidia-smi (the box's VRAM meter).

    `--query-gpu` memory fields are N/A on this GB10's unified memory;
    the cgroup under-charges device KV, so the meter is the
    baseline-subtracted compute-apps total (same convention as
    test_bringup_1_7).
    """
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
            continue  # header row / unexpected shape — skip, don't die
        out[int(pid_s)] = int(mib_s.split()[0])
    return out


def _gpu_delta_gb(new: dict, baseline: dict) -> float:
    """Baseline-subtracted footprint in GB (only NEW pids count).

    Inputs are MiB (nvidia-smi's unit); MiB / 1024 = GB (decimal, the
    S1.4 convention).
    """
    new_pids = set(new) - set(baseline)
    return sum(new[p] for p in new_pids) / 1024


def _instance_budget_gb() -> float:
    text = ENGINE_YAML.read_text()
    m = re.search(r"(?m)^[ \t]*per_instance_budget_gb:[ \t]*([0-9.]+)", text)
    assert m, f"per_instance_budget_gb not found in {ENGINE_YAML}"
    return float(m.group(1))


class _State:
    def __init__(self):
        self.tokens = 0
        self.ok = 0
        self.err_5xx = 0
        self.err_other = 0


def _post_completion(st: _State, deadline: float) -> int:
    """One completion round-trip; tallies into st (only in-window).

    Returns the HTTP status (0 on a transport-level failure, which is
    tallied in err_other too).
    """
    body = json.dumps(
        {
            "model": ALIAS,
            "messages": [{"role": "user", "content": PROMPT}],
            "max_tokens": MAX_TOKENS,
            "stream": False,
        }
    ).encode()
    req = urllib.request.Request(
        f"http://127.0.0.1:{PORT}/v1/chat/completions",
        data=body,
        headers={
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30.0) as resp:
            parsed = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        if 500 <= e.code <= 599:
            st.err_5xx += 1
        else:
            st.err_other += 1
        return e.code
    except (urllib.error.URLError, OSError, TimeoutError):
        # Transient (server still loading, reset, read timeout): counted
        # in err_other, not fatal — the SPEC's acceptance is "zero 5xx".
        st.err_other += 1
        return 0
    if time.monotonic() > deadline:
        return 200  # completed after the window — not counted
    st.ok += 1
    usage = parsed.get("usage") or {}
    st.tokens += int(usage.get("completion_tokens", 0))
    return 200


def _worker(st: _State, deadline: float) -> None:
    while time.monotonic() < deadline:
        if st.err_5xx > 0:
            return
        _post_completion(st, deadline)


def _wait_ready(deadline: float) -> bool:
    """Poll /v1/models for the alias; True if seen before deadline."""
    while time.monotonic() < deadline:
        try:
            req = urllib.request.Request(
                f"http://127.0.0.1:{PORT}/v1/models",
                headers={"Authorization": f"Bearer {API_KEY}"},
            )
            with urllib.request.urlopen(req, timeout=5.0) as resp:
                models = json.loads(resp.read())
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


class TestSustainedLoad(unittest.TestCase):
    """S1.7/S1.8 #3: 8-way concurrent load, 120 s, >= 26.2 t/s, no 5xx."""

    @unittest.skipUnless(_image_present(), f"runtime image {RUNTIME_IMAGE} not present")
    def test_sustained_120s_8_concurrent(self):
        memory_cap_gb = _instance_budget_gb()
        # Pre-launch VRAM baseline (main-stack PIDs) so the post-window
        # footprint delta is this instance only (S1.8 #3 "VRAM delta
        # +/-10%"; the same baseline-subtracted meter as test_bringup_1_7).
        baseline = _gpu_compute_apps_mib()
        subprocess.run(
            [
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
                "4g",
                "--gpus",
                "all",
                "--ipc=host",
                RUNTIME_IMAGE,
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        try:
            # Cold-load half: boot the instance and poll /v1/models for
            # the alias (the load-time 503 "Loading model" responses are
            # this half's, not the sustained window's).
            load_start = time.monotonic()
            self.assertTrue(
                _wait_ready(load_start + READY_TIMEOUT_S),
                f"instance never ready within {READY_TIMEOUT_S}s "
                f"(alias {ALIAS!r} not seen on :{PORT})",
            )
            load_s = time.monotonic() - load_start
            print(
                f"\n[1.8] cold load ready in {load_s:.1f}s; "
                f"warm-up + {DURATION_S}s window follow",
                flush=True,
            )

            # One warm-up completion primes the first-decode path
            # (CUDA kernels, first KV alloc) so the window measures
            # SUSTAINED throughput.
            warm_st = _State()
            warm_status = _post_completion(warm_st, time.monotonic() + 60.0)
            self.assertLess(
                warm_status,
                500,
                f"warm-up completion failed with HTTP {warm_status} "
                f"(engine healthy at readiness?)",
            )

            # Sustained window: the SPEC's "8 concurrent completions
            # for 120 s -> t/s >= 26.2, zero 5xx".
            st = _State()
            window_start = time.monotonic()
            deadline = window_start + DURATION_S
            with ThreadPoolExecutor(max_workers=CONCURRENT) as pool:
                futures = [
                    pool.submit(_worker, st, deadline) for _ in range(CONCURRENT)
                ]
                for f in futures:
                    f.result()
            window_s = time.monotonic() - window_start
            # Denominator = measured wall-time (SPEC: "for 120 s"), not
            # the nominal constant: a stalled worker costs its tokens,
            # so the measured window is the honest time base.
            tps = st.tokens / window_s
            print(
                f"\n[1.8] {st.ok} completions, {st.tokens} tokens in "
                f"{window_s:.1f}s -> {tps:.1f} t/s aggregate "
                f"(5xx={st.err_5xx}, other_err={st.err_other})",
                flush=True,
            )
            self.assertGreater(st.ok, 0, "no completions recorded")
            self.assertEqual(
                st.err_5xx,
                0,
                f"{st.err_5xx} 5xx response(s) in the 120 s window",
            )
            self.assertGreaterEqual(
                tps,
                MIN_AGG_TPS,
                f"aggregate {tps:.1f} t/s < {MIN_AGG_TPS} t/s "
                f"(0.9 x 29.16 community baseline)",
            )

            # S1.8 #3 VRAM half: the loaded instance's footprint delta must
            # sit in the S1.4 +/-10% band. Same convention as
            # test_bringup_1_7 (baseline-subtracted compute-apps meter; floor
            # budget-anchored at per_instance_budget_gb, ceiling at the
            # measured 14.44 GB total) so the two @gpu tests can't drift.
            time.sleep(3.0)  # let the allocator settle after the window
            delta_gb = _gpu_delta_gb(_gpu_compute_apps_mib(), baseline)
            lo = memory_cap_gb * 0.9  # 13.30 GB (budget 14.78, -10%)
            hi = 14.44 * 1.1  # 15.88 GB (measured total, +10%)
            print(
                f"\n[1.8] VRAM delta {delta_gb:.2f} GB (band [{lo:.2f}, {hi:.2f}] GB)",
                flush=True,
            )
            self.assertGreaterEqual(
                delta_gb,
                lo,
                f"VRAM delta {delta_gb:.2f} GB < {lo:.2f} GB (S1.4 -10%)",
            )
            self.assertLessEqual(
                delta_gb,
                hi,
                f"VRAM delta {delta_gb:.2f} GB > {hi:.2f} GB (S1.4 +10%)",
            )
        finally:
            subprocess.run(
                ["docker", "rm", "-f", CONTAINER],
                capture_output=True,
                check=False,
            )


if __name__ == "__main__":
    unittest.main()
