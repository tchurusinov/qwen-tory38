"""MAP 1.10 / S1.7 edge: port collision — instance 0 ERR_PORT_IN_USE,
instance 1 up.

Spec text (S1.7, "Edge"): start a dummy listener on 30101 -> boot -> expect
instance 0 `ERR_PORT_IN_USE`, instance 1 up.

Method (docker-level, pre-manager — the P1 shape; S2's unit layer maps the
engine's fast bind failure onto the named code + exit 3, and the manager
skips that instance while the others proceed, S1.5):

  * a dummy listener (a host-side Python socket bound to 0.0.0.0:30101 in
    the test process) holds instance 0's port for the whole boot
  * instance 0 boots with the S1.3 command at --port 30101 -> the engine
    fails its HTTP bind and EXITS (probe 2026-09-22: exit 1, ~0.2 s,
    stderr "couldn't bind HTTP server socket" + "exiting due to HTTP
    server error"; the bind happens BEFORE model load, so the collision
    costs no VRAM and never reaches the KV allocation — the S1.5
    "instance skipped" shape)
  * instance 1 boots at the same time on 30102 with the same S2.2 shape
    -> /v1/models returns the alias (the "instance 1 up" half)
  * instance 0 NEVER serves: while the collision was held, its /v1/models
    never returned the alias, and its container has exited (not
    OOMKilled — a clean engine exit, the skip path, not the S1.5 OOM
    path)
  * after the test, the dummy listener is closed and both containers
    torn down (one box, two ports — never leave a listener behind)

Measurement notes (probed 2026-09-22 on this box):
  * The engine's own exit code on a bind collision is 1 (not the S1.5
    exit 3): the S1.5 exit-3 mapping is a UNIT-LAYER concern —
    bonsai2-server@i's ExecStart is the `docker run` wrapper, and the
    manager/skip logic keys off the unit's reported failure, not the
    engine's raw code. The @gpu test asserts what the CONTAINER level
    can show: the fast clean exit + the bind-error log line + the
    other-instance-healthy half.
  * The bind attempt precedes model load (stderr order, probe
    2026-09-22), so a collided instance consumes no VRAM and the test
    does not need the S1.4 allocation-tier bookkeeping. The engine's
    model-load log line is "loading model '<file>'" / "(startup)
    loading model" at the pinned commit (probed 2026-09-22 in the
    runtime image's .so's — the `load_model` symbol is C++ internals,
    not a log line).
"""

import json
import re
import shutil
import socket
import subprocess
import threading
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
# The collision window: how long instance 0 may spend failing to bind
# while the dummy listener holds 30101. Measured ~0.2 s (probe
# 2026-09-22) — the bind is before model load — so 30 s is generous
# without dragging the test.
COLLISION_WATCH_S = 30.0


def _drain_listener(listener: socket.socket, stop: "threading.Event") -> None:
    """Accept-and-close connections on the dummy listener so its accept
    queue does not accumulate the probe's SYNs (G3c round-1 P2: the
    listener only `bind`s + `listen`s, so probe connections would sit
    in the backlog for the whole window — harmless but not clean).
    Runs on a daemon thread until `stop` is set."""
    listener.settimeout(1.0)
    while not stop.is_set():
        try:
            conn, _ = listener.accept()
            conn.close()
        except TimeoutError:
            continue
        except OSError:
            break  # listener closed


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


def _instance_budget_gb() -> float:
    text = ENGINE_YAML.read_text()
    m = re.search(r"(?m)^[ \t]*per_instance_budget_gb:[ \t]*([0-9.]+)", text)
    assert m, f"per_instance_budget_gb not found in {ENGINE_YAML}"
    return float(m.group(1))


def _ctx_size() -> int:
    text = ENGINE_YAML.read_text()
    m = re.search(r"(?m)^[ \t]*ctx_size:[ \t]*([0-9.]+)", text)
    assert m, f"ctx_size not found in {ENGINE_YAML}"
    return int(m.group(1))


def _http_json(url: str, timeout: float):
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {API_KEY}"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def _wait_ready(port: int, deadline: float) -> bool:
    """Poll /v1/models for the alias; True if seen at any point before the
    deadline. Used both positively (instance 1 up) and negatively
    (instance 0 never served while the collision held its port)."""
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
        ):
            pass
        time.sleep(READY_POLL_S)
    return False


def _engine_cmd(port: int) -> list[str]:
    """The S1.3 per-instance serving command, verbatim, at --port <port>
    (the S2.2 unit shape: the full command — `docker run img --port X`
    replaces the baked CMD and dies under the nvidia entrypoint, probed
    2026-09-22)."""
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
    # No --tail: a collided/failed instance's log is short, and the
    # assertions (bind line present/absent, no model-load line) read the
    # WHOLE log.
    r = subprocess.run(
        ["docker", "logs", name],
        capture_output=True,
        text=True,
        check=False,
    )
    return r.stdout + "\n" + r.stderr


class TestPortCollision(unittest.TestCase):
    """S1.7 edge: dummy listener on 30101 -> instance 0 fails to bind
    (ERR_PORT_IN_USE shape, S1.5), instance 1 up."""

    @unittest.skipUnless(_image_present(), f"runtime image {RUNTIME_IMAGE} not present")
    def test_collision_skips_i0_keeps_i1(self):
        # The "dummy listener on 30101": a host-side socket in this test
        # process (the S1.7 text shape). 0.0.0.0 so it collides with the
        # engine's 127.0.0.1 bind too (host networking).
        listener = socket.socket()
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("0.0.0.0", PORT_BASE + 0))
        listener.listen(5)
        # Drain the accept queue in the background (G3c round-1 P2):
        # without it, the probe's SYNs sit in the backlog for the
        # whole window.
        drain_stop = threading.Event()
        drain = threading.Thread(
            target=_drain_listener, args=(listener, drain_stop), daemon=True
        )
        drain.start()

        i0, i1 = "bonsai2-test-i0-collide", "bonsai2-test-i1-collide"
        cap_g = f"{_instance_budget_gb():.1f}g"
        subprocess.run(["docker", "rm", "-f", i0], capture_output=True, check=False)
        subprocess.run(["docker", "rm", "-f", i1], capture_output=True, check=False)
        try:
            # Both instances boot with the dummy listener holding
            # instance 0's port for the whole window (the S1.7 edge:
            # "start a dummy listener on 30101 -> boot").
            _spawn(i0, PORT_BASE + 0, cap_g)
            _spawn(i1, PORT_BASE + 1, cap_g)

            # Instance 1 (30102): up — the "instance 1 up" half.
            self.assertTrue(
                _wait_ready(PORT_BASE + 1, time.monotonic() + READY_TIMEOUT_S),
                "instance 1 never ready on 30102 (the 'instance 1 up' half)",
            )

            # Instance 0 (30101): the engine fails its HTTP bind fast
            # (before model load, probe 2026-09-22). Watch for its exit
            # while the listener holds the port.
            deadline = time.monotonic() + COLLISION_WATCH_S
            exited = False
            while time.monotonic() < deadline:
                st = _container_state(i0)
                if not st["running"]:
                    exited = True
                    break
                time.sleep(1.0)
            self.assertTrue(
                exited,
                f"instance 0 still running {COLLISION_WATCH_S}s after boot "
                "with 30101 held — the collision did not stop it",
            )
            st = _container_state(i0)
            self.assertFalse(
                st["oom_killed"],
                "instance 0 exited OOMKilled — that's the S1.5 OOM path, "
                "not the port-collision skip",
            )
            logs = _container_logs(i0)
            self.assertIn(
                "couldn't bind HTTP server socket",
                logs,
                f"instance 0 logs lack the bind-collision line — the exit "
                f"was something else: {logs[-800:]}",
            )
            self.assertNotIn(
                "(startup) loading model",
                logs,
                "instance 0 reached model load — the collision should fail "
                "it before (the pinned engine's model-load line is "
                "'(startup) loading model', probed 2026-09-22 in the "
                "runtime image)",
            )
            print(
                f"\n[1.10] instance 0: exited (code {st['exit_code']}) on the "
                f"30101 bind collision (ERR_PORT_IN_USE shape, S1.5); "
                f"instance 1 up on 30102",
                flush=True,
            )

            # While the listener held 30101, instance 0's own port never
            # served the alias (its engine had already exited; the
            # listener answered on 30101, and it has no /v1/models).
            # G3c round-2 P2: the 10 s window is a negative check whose
            # soundness rests on the exit proven ABOVE (the exited
            # container's engine no longer serves the alias; the dummy
            # listener's accept-and-close drain never returns a
            # /v1/models payload) — so no fresh serving engine could
            # appear in this window, and a single alias miss at the end
            # is conclusive for the "skipped, not serving" half.
            self.assertFalse(
                _wait_ready(PORT_BASE + 0, time.monotonic() + 10.0),
                "instance 0's port served the alias while the collision "
                "was held — the collided instance should be skipped, not "
                "serving",
            )
        finally:
            drain_stop.set()
            subprocess.run(["docker", "rm", "-f", i0], capture_output=True, check=False)
            subprocess.run(["docker", "rm", "-f", i1], capture_output=True, check=False)
            listener.close()


if __name__ == "__main__":
    unittest.main()
