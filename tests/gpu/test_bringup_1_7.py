"""MAP 1.7 / S1.7 integration: manual single-instance bring-up, automated.

Cold-loads instance 0 from the `bonsai2-server:1.0` runtime image the way
install.sh will (S2.2 shape: host networking, per-instance `--memory` cap,
`--gpus all`, `--ipc=host`, `--shm-size 4g`) and asserts the S1.7
integration acceptance:

  * `/v1/models` returns the alias `qwen38-27b-subagent` (S1.7 integration)
  * a 16-token completion round-trips in < 10 s (S1.7 integration)
  * the nvidia-smi footprint delta matches the S1.4 formula within 10 %
    (S1.7 integration)

Measurement notes (box-specific, learned MAP 1.7, 2026-09-21):
  * VRAM meter = `nvidia-smi --query-compute-apps=pid,used_memory`
    MINUS the pre-launch baseline (the main-stack PIDs). `--query-gpu`
    memory fields are N/A on this GB10's unified memory, and the cgroup
    under-charges device KV (scope peak ~7.6 GiB vs ~13.45 GiB true
    footprint) — do NOT meter via cgroup.
  * The S1.4 formula gives the expected footprint (14.08 GB at the
    re-anchored constant 0.000003277, 8 slots x 262144 ctx); the measured
    total on this box is 14.44 GB (13.45 GiB) including ~0.36 GB CUDA
    ctx/runtime overhead. The 10 % band is around the MEASURED total.
  * The 16-token RT target of 10 s measured 0.83 s on this box (q4_0 KV,
    warm engine); the target is spec, not a tuned constant.

The instance container is `bonsai2-test-i0` and is always torn down in
`finally` (one box, one port 30101 — never leave a listener behind).
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
CONTAINER = "bonsai2-test-i0"
PORT = 30101  # S1.3 instance 0 (the image's baked default)
API_KEY = "bonsai2-local"  # S1.3 --api-key (local-only)
ALIAS = "qwen38-27b-subagent"  # S1.3 --alias
# S2.2 --memory = S1.4 per_instance_budget_gb — read from the YAML at
# test time (the same source as the VRAM band, so the two can't drift
# if the template is edited; the re-anchored value is 14.78).
SHM_SIZE = "4g"  # S2.2 shape
READY_TIMEOUT_S = 180
READY_POLL_S = 2.0


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
    """instances.per_instance_budget_gb from the template (S1.4 re-anchor).

    Anchored at line start (indentation + exact key name), so a comment
    line that merely MENTIONS the key can't match; the value itself is
    the first token after the colon (a trailing comment is fine).
    """
    text = ENGINE_YAML.read_text()
    m = re.search(r"(?m)^[ \t]*per_instance_budget_gb:[ \t]*([0-9.]+)", text)
    assert m, f"per_instance_budget_gb not found in {ENGINE_YAML}"
    return float(m.group(1))


def _http_json(url: str, timeout: float):
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {API_KEY}"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def _completion(prompt: str, max_tokens: int, stream: bool, timeout: float):
    """POST /v1/chat/completions; returns (elapsed_s, parsed_response)."""
    body = json.dumps(
        {
            "model": ALIAS,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "stream": stream,
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
    t0 = time.monotonic()
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        if stream:
            raw = b"".join(resp)
        else:
            raw = resp.read()
    return time.monotonic() - t0, json.loads(raw)


def _gpu_compute_apps_mib() -> dict:
    """pid -> used MiB from nvidia-smi (the box's VRAM meter, see module doc)."""
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
    S1.4 convention — the spec's 14.44 GB figure is 13.45 GiB + the
    spec's own GiB->GB rounding).
    """
    new_pids = set(new) - set(baseline)
    return sum(new[p] for p in new_pids) / 1024


class TestBringUp(unittest.TestCase):
    """S1.7 integration: one cold instance, alias + RT + VRAM delta."""

    @unittest.skipUnless(_image_present(), f"runtime image {RUNTIME_IMAGE} not present")
    def test_instance_0_brings_up_and_meets_s1_7(self):
        # Pre-launch baseline: the main-stack PIDs are subtracted out so
        # the delta is this instance only.
        baseline = _gpu_compute_apps_mib()
        memory_cap_gb = _instance_budget_gb()
        # S2.2 shape: host net, memory cap, gpus, ipc, shm. The image's
        # baked CMD carries the S1.3 serving flags (no --entrypoint: a
        # bare `docker run img --flag` breaks under the nvidia
        # entrypoint, which treats the first -- as an exec option).
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
            # 1) readiness: /v1/models -> 200 + alias (S1.7 integration).
            deadline = time.monotonic() + READY_TIMEOUT_S
            alias_seen = False
            last_err = "not polled yet"
            while time.monotonic() < deadline:
                try:
                    models = _http_json(f"http://127.0.0.1:{PORT}/v1/models", 5.0)
                    names = [m.get("id") for m in models.get("data", [])]
                    if ALIAS in names:
                        alias_seen = True
                        break
                except (
                    urllib.error.URLError,
                    json.JSONDecodeError,
                    ConnectionError,
                    TimeoutError,
                    OSError,
                ) as e:
                    last_err = str(e)
                time.sleep(READY_POLL_S)
            self.assertTrue(
                alias_seen,
                f"/v1/models never returned {ALIAS!r} within "
                f"{READY_TIMEOUT_S}s (last error: {last_err})",
            )

            # 2) 16-token completion round-trip < 10 s (S1.7
            #    integration). Under --reasoning-format deepseek a
            #    trivial prompt burns its 16 tokens in
            #    reasoning_content (content: "" with finish_reason
            #    length) — the acceptance is the ROUND-TRIP, not the
            #    text; either content or reasoning_content populated
            #    proves the model actually answered (probe 2026-09-22:
            #    "Say hello" -> reasoning only, "What color is the
            #    sky" -> content 'Blue').
            elapsed, resp = _completion("Say hello in one word.", 16, False, 60.0)
            self.assertLess(
                elapsed,
                10.0,
                f"16-token completion took {elapsed:.2f}s (> 10 s acceptance)",
            )
            self.assertIn("choices", resp)
            msg = resp["choices"][0].get("message", {})
            self.assertTrue(
                msg.get("content") or msg.get("reasoning_content"),
                f"empty completion (no content, no reasoning_content): {msg!r}",
            )

            # 3) VRAM delta vs the S1.4 formula, 10 % band (S1.7
            #    integration). Band = [budget*0.9, measured*1.1] =
            #    [13.30, 15.88] GB around the re-anchored budget
            #    (14.78 = formula footprint 14.08 + 5 % install margin)
            #    and the measured 14.44 GB total on this box. The floor
            #    is deliberately BUDGET-anchored (not footprint-anchored
            #    at 14.08*0.9 = 12.67): the measured delta includes the
            #    instance's real overhead, and anchoring the floor on
            #    the budget keeps the band honest for the S2.2
            #    --memory cap (which IS the budget). A q4_0 -> FP16 KV
            #    regression (~24.6 GiB) still fails the ceiling loudly.
            #    (User-approved band, 2026-09-22; G3c round-2 P1-2
            #    disposition = document, not tighten.)
            time.sleep(3.0)  # let the allocator settle after the request
            delta_gb = _gpu_delta_gb(_gpu_compute_apps_mib(), baseline)
            formula_gb = _instance_budget_gb()  # 14.78 (includes the 5 % margin)
            measured_total_gb = 14.44  # MAP 1.7 measurement on this box
            lo = formula_gb * 0.9
            hi = measured_total_gb * 1.1
            self.assertGreaterEqual(
                delta_gb,
                lo,
                f"VRAM delta {delta_gb:.2f} GB < {lo:.2f} GB "
                f"(S1.4 formula {formula_gb} GB, -10 %)",
            )
            self.assertLessEqual(
                delta_gb,
                hi,
                f"VRAM delta {delta_gb:.2f} GB > {hi:.2f} GB "
                f"(measured total {measured_total_gb} GB, +10 %)",
            )
        finally:
            subprocess.run(
                ["docker", "rm", "-f", CONTAINER],
                capture_output=True,
                check=False,
            )


if __name__ == "__main__":
    unittest.main()
