"""The S3.7 mock engine fixture: a threaded http.server on 127.0.0.1
serving canned `/v1/models` and `/v1/chat/completions` (streaming +
non-streaming, 200/400/429/500 variants).

Used by the Phase-3 hermetic tests (MAP 3.3+). NOT the subagent pool's
real engine: it stands in for one instance (a port + an alias + the
OpenAI-compat shape the pinned llama-server fork speaks, S1.3). The
fixture is a plain class (no unittest coupling) so a test can hold
several "instances" on different ports, kill one mid-task (the S3.7
failure-injection case), and inspect what the harness actually sent.

Canned behavior (set per-port via `variant`):
  * "ok"      -> 200; completions stream 3 SSE chunks (+[DONE]) with
                 content + reasoning split (the S1.3 deepseek reasoning
                 shape); a non-stream request gets the plain OpenAI
                 completion body. `/v1/models` carries the alias.
  * "400"     -> 400 on completions (the request > ctx shape, S1.5)
  * "429"     -> 429 on completions (overloaded)
  * "500"     -> 500 on completions
  * "badjson" -> 200 but a NON-JSON body (the S3.7 ERR_PARSE case)
  * "slow"    -> 200, streams 3 chunks; the response is written ~1.35 s
                 after the request (0.45 s before + 0.9 s after) — the
                 S3.4 deadline-expiry case with a short --deadline
  * "authwall"-> /v1/models 401 (up-but-auth-walled: the manager's
                 S2.1 probe semantics treat 401 as UP — the
                 main_healthy exit-82 gate case)
  * "models_only" -> /v1/models 200 but completions 500 (an
                  alive-but-stuck shape; liveness fixtures)

`port` (default 0 = ephemeral): bind a NAMED 127.0.0.1 port so the
tests' health documents can point at it (the spawn round-trip rig).

The harness's main-endpoint probe (S3.6 main_endpoint_healthy_probe)
is satisfied by any fixture port configured as main_endpoint — the
fixture answers GET /v1/models with 200 + a body.
"""

from __future__ import annotations

import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# stdlib-only top level (the hermetic-suite AST check): typing.Self is
# stdlib on this box's Python (3.12); the try/except keeps the module
# importable on a 3.11 box without typing_extensions too.
try:
    from typing import Self
except ImportError:  # pragma: no cover (py3.11 boxes)

    class Self:  # typing.Self stand-in for annotations only
        ...


class MockEngine:
    """One canned instance: `MockEngine(alias=..., variant="ok")` then
    `.start()` -> `.port` (binds 127.0.0.1:0, a fresh port). `.stop()`
    closes the server + accepts no more connections.

    `request_log` (list of (method, path, body_dict_or_None)) records
    every request — tests assert WHAT the harness sent (the model id,
    the stream flag, the auth header shape lives in `auth_log`).
    """

    def __init__(
        self,
        alias: str = "qwen38-27b-subagent",
        variant: str = "ok",
        api_key: str = "bonsai2-local",
        port: int = 0,
    ):
        self.alias = alias
        self.variant = variant
        self.api_key = api_key
        self.request_log: list[dict] = []
        self.auth_log: list[str] = []
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self.port: int = port

    # ------------------------------------------------------------------
    def _make_handler(self):
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args, **kwargs):  # silence the default stderr log
                pass

            def _record(self, body: bytes | None):
                try:
                    parsed = json.loads(body) if body else None
                except json.JSONDecodeError:
                    parsed = None
                outer.request_log.append(
                    {
                        "method": self.command,
                        "path": self.path,
                        "body": parsed,
                        "raw": body,
                    }
                )
                outer.auth_log.append(self.headers.get("Authorization", ""))

            def do_GET(self):
                self._record(None)
                if self.path.rstrip("/") == "/v1/models":
                    if outer.variant == "authwall":
                        self._send(401, b'{"error": "unauthorized"}')
                    else:
                        self._send(
                            200, json.dumps({"data": [{"id": outer.alias}]}).encode()
                        )
                else:
                    self._send(404, b"{}")

            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length) if length else b""
                self._record(body)
                if self.path.rstrip("/") == "/v1/chat/completions":
                    self._handle_completions(body)
                else:
                    self._send(404, b"{}")

            def _handle_completions(self, body: bytes):
                v = outer.variant
                if v in ("400", "429", "500", "models_only"):
                    code = 400 if v == "400" else (429 if v == "429" else 500)
                    self._send(
                        code,
                        json.dumps({"error": {"message": f"canned {code}"}}).encode(),
                    )
                    return
                if v == "badjson":
                    self._send(200, b"this-is-not-json")
                    return
                try:
                    req = json.loads(body)
                except json.JSONDecodeError:
                    self._send(400, b'{"error": {"message": "bad request json"}}')
                    return
                if req.get("stream"):
                    if v == "slow":
                        time.sleep(0.45)
                    if v == "hold":
                        # S3.7 slot-contention / priority-promotion: a
                        # multi-second hold BEFORE the response, so the
                        # worker's attempt stays in flight and its slot
                        # stays charged for as long as the test needs
                        # (deterministic — no reliance on response
                        # timing).
                        time.sleep(3.0)
                    chunks = [
                        {"choices": [{"delta": {"reasoning_content": "ok "}}]},
                        {"choices": [{"delta": {"content": "done"}}]},
                        {
                            "choices": [{"delta": {}}],
                            "usage": {"prompt_tokens": 7, "completion_tokens": 3},
                        },
                    ]
                    payload = b""
                    for c in chunks:
                        payload += b"data: " + json.dumps(c).encode() + b"\n\n"
                    if v == "slow":
                        time.sleep(0.9)
                    payload += b"data: [DONE]\n\n"
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
                else:
                    msg = {
                        "role": "assistant",
                        "content": "done",
                        "reasoning_content": "ok ",
                    }
                    self._send(
                        200,
                        json.dumps(
                            {
                                "choices": [{"message": msg}],
                                "usage": {"prompt_tokens": 7, "completion_tokens": 3},
                            }
                        ).encode(),
                    )

            def _send(self, code: int, payload: bytes):
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

        return Handler

    def start(self) -> Self:
        self._server = ThreadingHTTPServer(
            ("127.0.0.1", self.port), self._make_handler()
        )
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        # wait until the port accepts (serve_forever is async)
        for _ in range(100):
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(0.05)
                if s.connect_ex(("127.0.0.1", self.port)) == 0:
                    return self
        return self

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None

    def __enter__(self) -> Self:
        return self.start()

    def __exit__(self, *exc) -> None:
        self.stop()


class DeadMainEndpoint:
    """A port that REFUSES connections (the S3.7 exit-82 case: the
    main-endpoint probe's connection-refused path). `start()` binds +
    closes so the port is free-but-refused; nothing runs on it."""

    def __init__(self) -> None:
        self.port: int = 0

    def start(self) -> DeadMainEndpoint:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            self.port = s.getsockname()[1]
        return self

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"


def free_port() -> int:
    """A port with no listener (bind + close; good enough for fixtures —
    the tests run sequentially on one box, hermetic)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
