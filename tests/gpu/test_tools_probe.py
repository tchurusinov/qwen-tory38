"""@gpu — the S3.4 wire-protocol probe (MAP 3.14; the subagent capability
layer, spec amendment 2026-09-25, A2).

THE QUESTION this probe answers (the pin-reality delta the amendment
names): does the Bonsai 2 chat template at the pin (9a9394a8) emit
tool calls via the NATIVE `tool_calls` array in a /v1/chat/completions
response when a `tools` array is sent, or does it fall back to a
TEXT-JSON convention (a fenced/bare JSON object in the content)?

The taskworker's loop handles BOTH halves hermetically (the
tests/test_harness_tools_loop.py cases) — this probe decides which
half the LIVE pin takes, so the M9/M10 goldens (MAP 4.9) and the
harness skill can be tuned against observed behavior, not guesswork.

Method (one-box constraint: one instance, the MVP N=1 condition):
  * `docker run` the pinned `bonsai2-server:1.0` image (the model is
    baked in; S1.4 tier sizing, a reduced ctx 8192 — the probe only
    needs a short conversation),
  * send ONE chat completion with the four-tool `tools` array
    (bin/bonsai2_tools.py TOOLS_JSON, the exact wire shape the loop
    sends) and a prompt that unambiguously requires a tool call,
  * inspect the response: native `choices[0].message.tool_calls`
    vs a fenced/bare JSON tool object in the content,
  * record the verdict to test-logs/tools-probe-<ts>.json AND stderr
    (the pin-reality delta entry; MAP session log picks it up).

Exit discipline (the @gpu convention): the instance is ALWAYS stopped
in tearDown (docker rm -f, best-effort). The test PASSES when a
verdict is reached (either wire shape is a valid pin-reality
observation — the loop's dual parsing is the design answer, both
halves work); it FAILS only when the probe cannot reach a verdict
(instance down on time, no response, both halves absent).

Run: `tests/run_gpu.sh tests.gpu.test_tools_probe` (never parallel
with a live session/review, Global testing discipline).
"""

from __future__ import annotations

import json
import re
import subprocess
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
BIN_DIR = REPO_ROOT / "bin"
LOG_DIR = REPO_ROOT / "test-logs"

# the pinned image (S1.6; the model is baked in — verified baked at
# the 2026-09-25 session: ls /opt/bonsai2/models inside the image).
RUNTIME_IMAGE = "bonsai2-server:1.0"
PROBE_PORT = 30170  # outside the pool's 30101+ range (no collision)
API_KEY = "bonsai2-local"
ALIAS = "qwen38-27b-subagent"
CONTAINER = "bonsai2-tools-probe"
START_TIMEOUT_S = 300.0  # the 27B load on this box is well under 5 min
REASONING_FORMAT = "deepseek"

# the prompt: unambiguously requires a tool call (the model has nothing
# else to do — no file content is given in the prompt itself).
PROBE_PROMPT = (
    "Use the bash tool to run the command: echo probe-12345 > /tmp/probe.txt "
    "&& cat /tmp/probe.txt\n"
    "Then report the exact output of that command."
)

_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)
_TOOL_KEYS = ("tool", "name")


def _load_tools_module():
    import importlib.machinery
    import importlib.util

    loader = importlib.machinery.SourceFileLoader(
        "bonsai2_tools_probe", str(BIN_DIR / "bonsai2_tools.py")
    )
    spec = importlib.util.spec_from_loader("bonsai2_tools_probe", loader)
    assert spec
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def _port_free(port: int) -> bool:
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.25)
        return s.connect_ex(("127.0.0.1", port)) != 0


def _stop_container() -> None:
    subprocess.run(
        ["docker", "rm", "-f", CONTAINER],
        capture_output=True,
        check=False,
    )


class TestToolsWireProtocolProbe(unittest.TestCase):
    """The single probe case: verdict = native | text-json (either is a
    PASS — the pin-reality delta is the OBSERVATION, recorded to a file)."""

    @classmethod
    def setUpClass(cls) -> None:
        # VRAM audit is the RUNNER's job (tests/run_gpu.sh snapshots
        # nvidia-smi pre-run); this class only enforces the port.
        if not _port_free(PROBE_PORT):
            raise unittest.SkipTest(f"port {PROBE_PORT} in use — probe later")
        _stop_container()
        cls.proc = subprocess.Popen(
            [
                "docker",
                "run",
                # no --rm: the explicit docker rm -f in _stop_container
                # owns the teardown, so docker logs survives for the
                # early-death assertion below (with --rm the container
                # vanishes before the error can be read).
                "--name",
                CONTAINER,
                "--gpus",
                "all",
                "--memory",
                "16g",
                "--memory-swap",
                "16g",
                "--shm-size",
                "4g",
                "--network",
                "host",
                "--ipc=host",
                RUNTIME_IMAGE,
                "/opt/bonsai2/bin/llama-server",
                "--model",
                "/opt/bonsai2/models/bonsai-2-27b-gguf-PQ2_0.gguf",
                "--ctx-size",
                "8192",
                "--parallel",
                "1",
                "--batch-size",
                "512",
                "--cache-type-k",
                "q4_0",
                "--cache-type-v",
                "q4_0",
                "--port",
                str(PROBE_PORT),
                "--api-key",
                API_KEY,
                "--reasoning-format",
                REASONING_FORMAT,
                "--alias",
                ALIAS,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        cls.deadline = time.monotonic() + START_TIMEOUT_S
        while time.monotonic() < cls.deadline:
            if cls.proc.poll() is not None:
                tail = subprocess.run(
                    ["docker", "logs", "--tail", "30", CONTAINER],
                    capture_output=True,
                    check=False,
                )
                log_tail = tail.stderr.decode("utf-8", "replace")[-1500:]
                _stop_container()
                raise AssertionError(
                    f"probe instance exited early (rc {cls.proc.returncode}) — "
                    f"the image/model load failed. docker logs tail:\n{log_tail}"
                )
            if not _port_free(PROBE_PORT):
                break
            time.sleep(0.5)
        else:
            raise AssertionError(
                f"probe instance port {PROBE_PORT} never came up within "
                f"{START_TIMEOUT_S:.0f} s"
            )
        # /v1/models answers (the health shape; auth-walled main semantics
        # do not apply — the key is the instance's own).
        for _ in range(40):
            try:
                with urllib.request.urlopen(
                    urllib.request.Request(
                        f"http://127.0.0.1:{PROBE_PORT}/v1/models",
                        headers={"Authorization": f"Bearer {API_KEY}"},
                    ),
                    timeout=5,
                ) as resp:
                    if resp.status == 200:
                        return
            except (urllib.error.URLError, OSError):
                time.sleep(1.0)
        raise AssertionError(
            f"/v1/models never answered on {PROBE_PORT} — instance up but "
            "not serving (the @gpu log carries the container's state)"
        )

    @classmethod
    def tearDownClass(cls) -> None:
        _stop_container()

    def test_probe_verdict_is_recorded(self) -> None:
        T = _load_tools_module()
        body = {
            "model": ALIAS,
            "messages": [{"role": "user", "content": PROBE_PROMPT}],
            "tools": T.TOOLS_JSON,
            "stream": False,
        }
        req = urllib.request.Request(
            f"http://127.0.0.1:{PROBE_PORT}/v1/chat/completions",
            data=json.dumps(body).encode(),
            headers={
                "Authorization": f"Bearer {API_KEY}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=300.0) as resp:
                data = json.loads(resp.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            raise AssertionError(
                f"probe completion HTTP {e.code}: "
                f"{e.read(2048).decode('utf-8', 'replace')}"
            )

        choice = (data.get("choices") or [{}])[0]
        msg = choice.get("message") or {}
        content = msg.get("content") or ""
        native = msg.get("tool_calls")

        verdict: str
        detail: str
        if isinstance(native, list) and native:
            verdict = "native"
            detail = (
                f"native tool_calls array: "
                f"{json.dumps(native[0].get('function', {}), default=str)[:200]}"
            )
        else:
            # the text-JSON half (the taskworker's _parse_text_tool_call
            # convention): a fenced or bare object naming one of the four tools
            verdict, detail = "none", "no native tool_calls in the response"
            m = _FENCE_RE.search(content)
            candidates = [m.group(1)] if m else []
            if content.lstrip().startswith("{"):
                candidates.append(content.lstrip())
            for cand in candidates:
                try:
                    obj = json.loads(cand)
                except json.JSONDecodeError:
                    continue
                if not isinstance(obj, dict):
                    continue
                name = None
                for k in _TOOL_KEYS:
                    v = obj.get(k)
                    if isinstance(v, str):
                        name = v
                        break
                if name in T._TOOL_NAMES:
                    verdict, detail = (
                        "text-json",
                        f"text-JSON tool object: {cand[:200]}",
                    )
                    break
            if verdict == "none":
                detail += (
                    f"; content head: {content[:300]!r}; "
                    f"finish_reason={choice.get('finish_reason')}"
                )

        record = {
            "probe": "tools-wire-protocol (MAP 3.14, S3.4 A2)",
            "pin": "9a9394a895b96003ca842a6041cb28ac49a108f7",
            "image": RUNTIME_IMAGE,
            "tools_sent": [t["function"]["name"] for t in T.TOOLS_JSON],
            "prompt": PROBE_PROMPT,
            "verdict": verdict,
            "detail": detail,
            "usage": data.get("usage"),
            "ts": time.time(),
        }
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        out = LOG_DIR / f"tools-probe-{time.strftime('%Y%m%d-%H%M%S')}.json"
        out.write_text(json.dumps(record, indent=2) + "\n")
        print(
            f"[tools-probe] VERDICT: {verdict} — {detail} (recorded: {out})",
            flush=True,
        )
        # EITHER wire shape is a PASS (the loop's dual parsing is the
        # design answer); no verdict = the probe could not observe.
        self.assertIn(
            verdict,
            ("native", "text-json"),
            f"probe reached no verdict (both halves absent): {detail}",
        )


if __name__ == "__main__":
    unittest.main()
