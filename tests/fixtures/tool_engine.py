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
(tool_invalid_max bad calls, --max-steps exhaustion). A `final` entry
that is NOT last advances to the next entry after it is served (MAP 5.7
multi-task queue: one scripted instance serves a SEQUENCE of tasks —
one task's final answer is followed by the next task's entries,
consumed in task order, one completion per request). The last entry's
repeat semantics are unchanged (it has no successor).

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
        # MAP 5.7: the queue cursor is read/modified per request, and the
        # ThreadingHTTPServer serves CONCURRENT requests (the M2/M3
        # fan-out: 8 slots, 10 tasks). Without this lock two in-flight
        # requests would race on `self._idx` — one verdict served twice,
        # one skipped. The lock makes the next-entry hand-off atomic.
        self._lock = threading.Lock()
        self.alias = alias
        self.api_key = api_key
        self.tools_response = tools_response or {"success": True, "output": "ok"}
        self.request_log: list[dict] = []
        self.tools_log: list[dict] = []
        # F11 (2026-10-03 first-practical-run remediation): the engine's
        # real-time completion-control endpoint. `control_response` is the
        # canned answer (default {"success": True}); `control_log` records
        # every POST body the reaper's cancel sent (tests assert the
        # {id, action} shape + that the reaper addressed the RIGHT port).
        self.control_response: dict = {"success": True}
        self.control_log: list[dict] = []
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self.port: int = port

    # ------------------------------------------------------------------
    def _next_spec(self) -> dict:
        """The next canned completion; the last entry REPEATS once the
        queue is exhausted (the overrun cases are single-entry queues).
        G3 PR#18 r8 P2 (noted): a spec carrying a `sleep` key repeats it
        on EVERY replay once the queue is exhausted (the repeat re-uses
        the same spec dict, so the sleep fires again). No current test
        uses a `sleep` key, so the overrun cases (single-entry, no
        sleep) never hit the repeat path — the documented hazard is
        latent. If a future test adds `sleep` to a single-entry spec that
        must overrun, expect the suite to stall for N*sleep seconds.

        MAP 5.7 (multi-task queue): a `final` entry that is NOT last
        advances to the next entry after it is served — one scripted
        instance serves a SEQUENCE of tasks (one task's final answer is
        followed by the next task's entries, consumed in task order,
        one completion per request). The last entry's repeat semantics
        are unchanged (it has no successor)."""
        if not self.responses:
            return {"kind": "final", "content": "done"}
        idx = min(self._idx, len(self.responses) - 1)
        with self._lock:
            # re-read under the lock (the caller's clamp above may be
            # stale after a concurrent hand-off); atomically take the
            # entry at the CURRENT cursor and advance it.
            idx = min(self._idx, len(self.responses) - 1)
            spec = self.responses[idx]
            # a tool step or a NON-LAST final advances off the cursor
            # (the next task's script starts / the next step of the same
            # loop); the LAST final stays (the repeat semantics for the
            # overrun cases — single-entry queues, or a task that keeps
            # requesting after its final).
            if spec.get("kind") != "final" or idx < len(self.responses) - 1:
                self._idx = idx + 1
        return spec

    @staticmethod
    def _chunks(spec: dict) -> list[dict]:
        # F11/F2 (2026-10-03 first-practical-run remediation): the spec
        # may carry `id` (the engine's completion id — the SSE `id`
        # field the worker captures for the F11 control-endpoint
        # cancel) and `finish_reason` (the SSE `choices[0].finish_reason`
        # the worker's F2 truncation guard reads). Absent -> the chunks
        # carry neither (the pre-remediation wire shape; legacy specs
        # keep their byte-identical behavior).
        emit_id = isinstance(spec.get("id"), str) and bool(spec.get("id"))
        fr = spec.get("finish_reason")
        chunks = []
        for c in ToolEngine._base_chunks(spec):
            c = dict(c)
            if emit_id:
                c["id"] = spec["id"]
            if isinstance(fr, str) and fr:
                choices = c.get("choices")
                if isinstance(choices, list) and choices:
                    c["choices"] = [dict(choices[0], finish_reason=fr)]
            chunks.append(c)
        return chunks

    @staticmethod
    def _base_chunks(spec: dict) -> list[dict]:
        if spec.get("kind") == "tool":
            if spec.get("fragmented", False):
                # G3 PR#18 r9: emulate the OpenAI/llama.cpp streaming
                # contract where ONE tool call arrives as FRAGMENTS:
                # chunk 1 = id + name, later chunks = arguments
                # fragments. Exercises the worker's per-index
                # accumulation (the r9 P1 fix) end to end.
                fn_name = spec.get("tool", "bash")
                args = json.dumps(spec.get("params", {}))
                mid = len(args) // 2
                chunks = [
                    {
                        "choices": [
                            {
                                "delta": {
                                    "tool_calls": [
                                        {
                                            "index": 0,
                                            "id": spec.get("id", "call_1"),
                                            "type": "function",
                                            "function": {"name": fn_name},
                                        }
                                    ]
                                }
                            }
                        ]
                    },
                    {
                        "choices": [
                            {
                                "delta": {
                                    "tool_calls": [
                                        {
                                            "index": 0,
                                            "function": {"arguments": args[:mid]},
                                        }
                                    ]
                                }
                            }
                        ]
                    },
                    {
                        "choices": [
                            {
                                "delta": {
                                    "tool_calls": [
                                        {
                                            "index": 0,
                                            "function": {"arguments": args[mid:]},
                                        }
                                    ]
                                }
                            }
                        ]
                    },
                ]
            elif spec.get("native", True):
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
            # F2 (first-practical-run remediation): the pin's
            # deepseek-format completion can carry reasoning_content
            # deltas (the 9-21K pre-tool reasoning bloat). A spec's
            # `reasoning` key emits a reasoning_content chunk BEFORE the
            # content chunk — a length-cut completion (finish_reason=
            # "length", empty content) that still carries reasoning is
            # what reaches the worker's F2 truncation guard; a fully
            # empty stream never does (it fails the pre-existing
            # parse guard first, ERR_PARSE). Absent key -> legacy
            # byte-identical shape.
            chunks = []
            if spec.get("reasoning"):
                chunks.append(
                    {"choices": [{"delta": {"reasoning_content": spec["reasoning"]}}]}
                )
            chunks.append(
                {"choices": [{"delta": {"content": spec.get("content", "done")}}]}
            )
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
                elif self.path.rstrip("/") == "/v1/chat/completions/control":
                    # F11: the engine's real-time completion-control
                    # endpoint (the reaper's orphan-cancel lever). Record
                    # the body the reaper sent; answer with the canned
                    # control_response (default success).
                    try:
                        parsed = json.loads(body)
                    except json.JSONDecodeError:
                        parsed = None
                    outer.control_log.append(parsed)
                    self._send(200, json.dumps(outer.control_response).encode())
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
