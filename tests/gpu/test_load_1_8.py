"""MAP 1.8 / S1.7 sustained load: 8 concurrent completions for 120 s.

Acceptance (S1.7 integration, S1.8 #3): with `--parallel 8` (the S1.3
lock), a 120 s window of 8-way concurrent completions must deliver
aggregate throughput >= 26.2 t/s (0.9 x the 29.16 t/s community
baseline — SPEC's `0.9 x community-baseline`) and zero 5xx responses.

Method (matches the live MAP 1.8 measurement, 2026-09-21: 50.7 t/s
aggregate, 384 ok, 0x 5xx on this box):
  * instance 0 cold-loads from the runtime image (S2.2 shape, baked CMD)
  * 8 worker threads each loop short completions (32-token cap) until
    the 120 s window closes; only responses that COMPLETE inside the
    window count
  * aggregate t/s = sum(completion_tokens) / window seconds
  * any HTTP 500-599 response fails the test immediately

Measurement notes:
  * completion tokens come from `usage.completion_tokens` in the
    non-streaming response — the engine's own accounting, not a client
    estimate.
  * The first completion of the window pays warm-up (CUDA kernels,
    first KV alloc); with the measured 50.7 t/s vs the 26.2 t/s floor
    the warm-up cannot sink the aggregate.
  * One box, one port 30101: the container is always torn down in
    `finally`.
"""

import json
import shutil
import subprocess
import time
import unittest
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

RUNTIME_IMAGE = "bonsai2-server:1.0"
CONTAINER = "bonsai2-test-i0-load"
PORT = 30101  # S1.3 instance 0 (the image's baked default)
API_KEY = "bonsai2-local"  # S1.3 --api-key (local-only)
ALIAS = "qwen38-27b-subagent"  # S1.3 --alias
MEMORY_CAP_GB = 14.78  # S2.2 --memory = S1.4 per_instance_budget_gb
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


class _LoadError(Exception):
    """A non-5xx failure inside a worker (connection reset, timeout)."""


class _State:
    def __init__(self):
        self.tokens = 0
        self.ok = 0
        self.err_5xx = 0
        self.err_other = 0


def _post_completion(st: _State, deadline: float) -> None:
    """One completion round-trip; tallies into st (only in-window)."""
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
        return
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        st.err_other += 1
        raise _LoadError(str(e))
    if time.monotonic() > deadline:
        return  # completed after the window — not counted
    st.ok += 1
    usage = parsed.get("usage") or {}
    st.tokens += int(usage.get("completion_tokens", 0))


def _worker(st: _State, deadline: float) -> None:
    while time.monotonic() < deadline:
        if st.err_5xx > 0:
            return
        _post_completion(st, deadline)


def _wait_ready(deadline: float) -> None:
    while time.monotonic() < deadline:
        try:
            req = urllib.request.Request(
                f"http://127.0.0.1:{PORT}/v1/models",
                headers={"Authorization": f"Bearer {API_KEY}"},
            )
            with urllib.request.urlopen(req, timeout=5.0) as resp:
                models = json.loads(resp.read())
            if ALIAS in (m.get("id") for m in models.get("data", [])):
                return
        except (
            urllib.error.URLError,
            json.JSONDecodeError,
            ConnectionError,
            TimeoutError,
            OSError,
        ):
            pass
        time.sleep(READY_POLL_S)
    raise AssertionError(f"instance not ready within {READY_TIMEOUT_S}s")


class TestSustainedLoad(unittest.TestCase):
    """S1.7/S1.8 #3: 8-way concurrent load, 120 s, >= 26.2 t/s, no 5xx."""

    @unittest.skipUnless(_image_present(), f"runtime image {RUNTIME_IMAGE} not present")
    def test_sustained_120s_8_concurrent(self):
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
                f"{MEMORY_CAP_GB}g",
                "--memory-swap",
                f"{MEMORY_CAP_GB}g",
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
            _wait_ready(time.monotonic() + READY_TIMEOUT_S)

            st = _State()
            deadline = time.monotonic() + DURATION_S
            with ThreadPoolExecutor(max_workers=CONCURRENT) as pool:
                futures = [
                    pool.submit(_worker, st, deadline) for _ in range(CONCURRENT)
                ]
                for f in futures:
                    f.result()  # raises _LoadError on a non-5xx worker failure

            elapsed = DURATION_S  # the measured window (spec, not wall-clock drift)
            tps = st.tokens / elapsed
            print(
                f"\n[1.8] {st.ok} completions, {st.tokens} tokens in "
                f"{DURATION_S}s -> {tps:.1f} t/s aggregate "
                f"(5xx={st.err_5xx}, other_err={st.err_other})",
                flush=True,
            )
            self.assertEqual(
                st.err_5xx,
                0,
                f"{st.err_5xx} 5xx response(s) in the 120 s window",
            )
            self.assertGreater(st.ok, 0, "no completions recorded")
            self.assertGreaterEqual(
                tps,
                MIN_AGG_TPS,
                f"aggregate {tps:.1f} t/s < {MIN_AGG_TPS} t/s "
                f"(0.9 x 29.16 community baseline)",
            )
        finally:
            subprocess.run(
                ["docker", "rm", "-f", CONTAINER],
                capture_output=True,
                check=False,
            )


if __name__ == "__main__":
    unittest.main()
