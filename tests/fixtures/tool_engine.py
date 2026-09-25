"""The S3.4 tool-loop mock engine (MAP 3.14 tests; the subagent capability
layer, spec amendment 2026-09-25): a threaded http.server on 127.0.0.1
serving /v1/models, /v1/chat/completions from a SCRIPTABLE response
queue, and an optional /tools endpoint (the engine-side tool-execution
half, S3.6 `tool_execution: engine`).

NOT the subagent pool's real engine — it stands in for one instance the
same way tests/fixtures/mock_engine.py does, except its completions are
scripted per request: each queue entry describes one chat completion.

Spec shapes (response-queue entries, consumed in order):
  {"kind": "tool", "tool": "bash", "params": {"cmd": "..."},
   "id": "call_1", "native": True}   -> SSE with a native `tool_calls`
                                       delta (the A2 native-array shape)
  {..., "native": False}             -> SSE content carrying a fenced
                                       ```json {"tool": ..., "params": ...}
                                       ``` block (the A2 text-JSON
                                       fallback the taskworker also parses)
  {"kind": "final", "content": "..."}-> SSE content chunk (a final answer)
  {"sleep": SECS}                    -> optional: the handler sleeps
                                         before responding (a deterministic
                                         in-flight window for the dedup
                                         tests)

Queue exhaustion: the LAST entry repeats — deterministic overrun cases
(tool_invalid_max bad calls, --max-steps exhaustion).

`request_log` records every /v1/chat/completions body (tests assert WHAT
the taskworker sent: the tools array, the message-history growth).
`tools_log` records every /tools POST body (the engine-side execution
half; the test asserts the host->engine name mapping landed).
"""

from __future__ import annotations

import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# stdlib-only top level (the hermetic-suite AST check); typing.Self is
# stdlib on this box's Python (3.12) — the try/except keeps the module
# importable on a 3.11 box too.
try:
    from typing import Self
except ImportError:  # pragma: no cover (py3.11 boxes)

    class Self:  # typing.Self stand-in for annotations only
        ...


class ToolEngine:
    """One scripted instance: `ToolEngine(responses=[...])` then `.start()`
    -> `.port`. `.stop()` closes the server + accepts no more requests.

    `tools_response` (default {"success": True, "output": "ok"}) is the
    canned /tools POST answer for the `tool_execution: engine` cases."""

    def __init__(
        self,
        responses: list | None = None,
        alias: str = "qwen38-27b-subagent",
        api_key: str = "bonsai2-local",
        port: int = 0,
        tools_response: dict | None = None,
    ):
        self.responses = list(responses or [])
        self._idx = 0
        self.alias = alias
        self.api_key = api_key
        self.tools_response = tools_response or {"success": True, "output": "ok"}
        self.request_log: list[dict] = []
        self.tools_log: list[dict] = []
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self.port: int = port

    # ------------------------------------------------------------------
    def _next_spec(self) -> dict:
        """The next canned completion; the last entry REPEATS once the
        queue is exhausted (the overrun cases are single-entry queues)."""
        if not self.responses:
            return {"kind": "final", "content": "done"}
        self._idx = min(self._idx, len(self.responses) - 1)
        spec = self.responses[self._idx]
        if spec.get("kind") != "final":
            self._idx += 1
        return spec

    @staticmethod
    def _chunks(spec: dict) -> list[dict]:
        if spec.get("kind") == "tool":
            if spec.get("native", True):
                fn = {
                    "name": spec.get("tool", "bash"),
                    "arguments": json.dumps(spec.get("params", {})),
                }
                tc = {
                    "id": spec.get("id", "call_1"),
                    "type": "function",
                    "function": fn,
                }
                chunks = [{"choices": [{"delta": {"tool_calls": [tc]}}]}]
            else:
                inner: dict = {"tool": spec.get("tool", "bash")}
                if "params" in spec:
                    inner["params"] = spec["params"]
                block = "```json\n" + json.dumps(inner) + "\n```"
                chunks = [{"choices": [{"delta": {"content": block}}]}]
        else:  # kind == "final"
            chunks = [
                {"choices": [{"delta": {"content": spec.get("content", "done")}}]}
            ]
        chunks.append(
            {
                "choices": [{"delta": {}}],
                "usage": {"prompt_tokens": 3, "completion_tokens": 5},
            }
        )
        return chunks

    # ------------------------------------------------------------------
    def _make_handler(self):
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args, **kwargs):  # silence the default log
                pass

            def _send(self, code: int, payload: bytes) -> None:
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def do_GET(self):
                if self.path.rstrip("/") == "/v1/models":
                    self._send(
                        200, json.dumps({"data": [{"id": outer.alias}]}).encode()
                    )
                else:
                    self._send(404, b"{}")

            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length) if length else b""
                if self.path.rstrip("/") == "/v1/chat/completions":
                    self._completions(body)
                elif self.path.rstrip("/") == "/tools":
                    self._tools(body)
                else:
                    self._send(404, b"{}")

            def _completions(self, body: bytes) -> None:
                try:
                    req = json.loads(body)
                except json.JSONDecodeError:
                    req = {}
                outer.request_log.append(req if isinstance(req, dict) else {})
                spec = outer._next_spec()
                if spec.get("sleep"):
                    time.sleep(float(spec["sleep"]))
                payload = b""
                for chunk in outer._chunks(spec):
                    payload += b"data: " + json.dumps(chunk).encode() + b"\n\n"
                payload += b"data: [DONE]\n\n"
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def _tools(self, body: bytes) -> None:
                try:
                    parsed = json.loads(body)
                except json.JSONDecodeError:
                    parsed = None
                outer.tools_log.append(parsed)
                self._send(200, json.dumps(outer.tools_response).encode())

        return Handler

    def start(self) -> Self:
        self._server = ThreadingHTTPServer(
            ("127.0.0.1", self.port), self._make_handler()
        )
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
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
