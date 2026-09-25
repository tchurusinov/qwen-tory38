"""MAP 3.9–3.11 / S3.2 / S3.7 / S3.8 #3–#5: the S3.2 exit-code closed
set, the manager stub, and the table-driven hermetic suite.

No GPU; the subagent instances are S3.7 mock engines on 127.0.0.1
fixture ports, the manager is the S3.7 MockManager stub. Every exit
code of the S3.2 closed set {0,2,3,4,5,8,20,21,22,80,82,84} is
produced by a NAMED case asserting the exact JSON + code; the table
(ALL_EXIT_CODES) is the single place that asserts the S3.8 #3 contract
("all 12 exit codes exercised … no code outside the closed set ever
emitted").

What each MAP item is proven by (the spec-named assertions):

  * 3.9 (exit-code closed set, S3.8 #3):
      - H.EXIT_CLOSED_SET is exactly the S3.2 literal (12 codes);
      - every S3.2 command's COMMAND_EXIT_CODES subset is a SUBSET of
        the closed set (no stray code a command may emit) and the union
        over the commands that can observe it covers the closed set —
        so no code outside the set can escape AND no code in the set is
        unexercised by some command's mapping;
      - a live dispatch of each named case returns exactly that code,
        and a stray-code guard asserts `emit` rejects 1/9/13 (the
        S3.8 #3 "stray exit 1 fails the suite" case).

  * 3.10 (mock engine fixture + manager stub):
      - the MockManager serves the S3.2 pool-control protocol
        (control/ request -> ack + health.json mutation), the S3.7
        84 case (a dead manager writes no ack) and the S2.4 main-only
        document (N=0 -> `main_only_reason`).

  * 3.11 (hermetic suite, table-driven; S3.7 named cases):
      - the ALL_EXIT_CODES table: one named case per closed-set code,
        exact JSON + code asserted (3 = bfailed port-conflict;
        4 = bfailed with an S1.4 engine cause; 5 = ctx too long;
        8 = queue_wait expiry; 20/21/22 = collect terminals;
        80 = main-only S2.4 JSON; 82 = dead main; 84 = manager down;
        0 = spawn accepted / collect non-terminal poll);
      - slot contention (S3.7): 2 instances x 2 slots, 5 spawns ->
        4 running + 1 queued (the 5th is still queued, exit 8);
      - failure injection (S3.7): engine killed mid-task ->
        engine_retried -> done on the other instance; non-JSON body ->
        collect exit 21 ERR_PARSE;
      - routing (S3.7): uneven fake payloads -> least-loaded pick;
        priority preemption (a high task spawned while a low one is
        queued is promoted first);
      - idempotency (S3.7): a same-prompt spawn with a live task is a
        no-op (the dedup entry is not re-created);
      - pool-control cases (S3.7): unload no-op, reload partial,
        bad-args, dead-manager — exact JSON + code.

Run: `/usr/bin/python3.12 -m unittest discover -s tests -t .`
(hermetic default; the box's plain `python3` lacks `yaml`).
"""

import importlib.machinery
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import ClassVar

REPO_ROOT = Path(__file__).resolve().parents[1]
BIN_DIR = REPO_ROOT / "bin"
ENGINE_YAML = REPO_ROOT / "etc" / "subagent-engine.yaml"

from .fixtures.mock_engine import MockEngine, free_port
from .fixtures.mock_manager import MockManager

PY = "/usr/bin/python3.12" if os.path.exists("/usr/bin/python3.12") else sys.executable


def _load_harness():
    loader = importlib.machinery.SourceFileLoader(
        "bonsai2_harness", str(BIN_DIR / "bonsai2_harness.py")
    )
    spec = importlib.util.spec_from_loader("bonsai2_harness", loader)
    assert spec, "could not load bin/bonsai2_harness.py"
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


H = _load_harness()

SLOW_DEADLINE_S = 0.5


class KilledEngine:
    """A S3.7 failure-injection engine: serves a completion stream with
    a MID-STREAM HOLD (a 1.2 s sleep before the response is written,
    so the worker's attempt is in flight), and after `.kill()` the
    in-flight connection is dropped without a response (the SIGKILL
    shape: the socket dies, no answer — the worker's urllib gets a
    connection error, the S1.5 one-retry picks another instance).

    Mirrors the MockEngine convention: `.start()`-like semantics live
    in `__init__` (binds 127.0.0.1:0), `.port`, `.kill()`, `.stop()`.
    """

    def __init__(self, port: int = 0):
        self.killed = threading.Event()
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a, **k):
                pass

            def do_GET(self):
                payload = b'{"data": [{"id": "qwen38-27b-subagent"}]}'
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                self.rfile.read(length)
                if outer.killed.is_set():
                    self.close_connection = True
                    return
                time.sleep(2.5)  # the mid-stream hold: the kill window
                if outer.killed.is_set():
                    # SIGKILL shape: drop the socket WITHOUT a response
                    self.close_connection = True
                    return
                payload = (
                    b'data: {"choices": [{"delta": {"reasoning_content": "k1 "}}]}\n\n'
                    b'data: {"choices": [{"delta": {"content": "kdone"}}]}\n\n'
                    b"data: [DONE]\n\n"
                )
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

        self._server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def kill(self) -> None:
        """The SIGKILL: every in-flight / future connection dies
        without a response."""
        self.killed.set()
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None

    def stop(self) -> None:
        if not self.killed.is_set():
            self.kill()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None


class Base(unittest.TestCase):
    """Shared rig: fixture main endpoint + temp dirs + env overrides
    (the S3.2 hermetic hooks, same pattern as the 3.3/3.4–3.8 suites)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.health_dir = self.tmp / "run"
        self.results = self.tmp / "results"
        self._engines: list[MockEngine] = []
        self.main_engine = MockEngine(variant="ok")
        self.main_engine.start()
        self._engines.append(self.main_engine)
        self.engines: list[object] = []
        self._last_task_id: str | None = None
        self.cfg = self.tmp / "harness.yaml"
        self.env: dict = {}
        self._saved_env = {
            k: os.environ.get(k)
            for k in (
                "BONSAI2_CONFIG",
                "BONSAI2_RESULTS_DIR",
                "BONSAI2_HEALTH_DIR",
                "BONSAI2_ENGINE_YAML",
                "BONSAI2_SERVING_YAML",
            )
        }

    def tearDown(self):
        for k, v in self._saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        for e in self._engines:
            e.stop()
        for e in self.engines:
            e.stop()
        self._tmp.cleanup()

    def _instance(self, port: int, variant: str = "ok") -> MockEngine:
        m = MockEngine(variant=variant, port=port)
        m.start()
        self.engines.append(m)
        return m

    def _write_cfg(self, **overrides) -> None:
        data = {
            "main_endpoint": f"http://127.0.0.1:{self.main_engine.port}",
            "results_dir": str(self.results),
            "dedup_dir": str(self.health_dir / "dedup"),
            "queue_wait_max_s": 1,
        }
        data.update(overrides)
        self.cfg.write_text("\n".join(f"{k}: {v}" for k, v in data.items()) + "\n")
        self.env = {
            "BONSAI2_CONFIG": str(self.cfg),
            "BONSAI2_RESULTS_DIR": str(self.results),
            "BONSAI2_HEALTH_DIR": str(self.health_dir),
            "BONSAI2_ENGINE_YAML": str(ENGINE_YAML),
        }
        # a tight pool wait budget: the unload/reload ack-wait reads the
        # manager's serving.yaml (boot_target_s + 2*health_poll_s + 15).
        # A short budget keeps the dead-manager 84 case fast.
        srv = self.tmp / "serving.yaml"
        srv.write_text("boot_target_s: 2\nmanager:\n  health_poll_s: 1\n")
        self.env["BONSAI2_SERVING_YAML"] = str(srv)
        for k, v in self.env.items():
            os.environ[k] = v

    def _write_health(self, instances: list[dict], stack: str = "healthy") -> None:
        doc: dict = {
            "stack": stack,
            "instances": instances,
            "main_endpoint": "http://x",
            "main_healthy": True,
        }
        if not instances:
            doc["main_only_reason"] = "budget_zero"
        self.health_dir.mkdir(parents=True, exist_ok=True)
        (self.health_dir / "health.json").write_text(json.dumps(doc, indent=2) + "\n")

    def _write_health_doc(self, doc: dict) -> None:
        """Write a COMPLETE S3.1 document verbatim (the N=0 / main-only
        cases carry `main_only_reason` + the stack string, which the
        instances-list `_write_health` would re-derive)."""
        self.health_dir.mkdir(parents=True, exist_ok=True)
        (self.health_dir / "health.json").write_text(json.dumps(doc, indent=2) + "\n")

    def _inst(
        self,
        i: int,
        port: int,
        state: str = "healthy",
        ctx: int = 262144,
        total: int = 8,
        bfailed_reason: str | None = None,
    ) -> dict:
        inst = {
            "i": i,
            "port": port,
            "state": state,
            "ctx": ctx,
            "ctx_reduced": False,
            "slots_in_use": 0,
            "slots_total": total,
        }
        if bfailed_reason:
            inst["bfailed_reason"] = bfailed_reason
        return inst

    def _killed_instance(self, port: int = 0) -> KilledEngine:
        k = KilledEngine(port)
        self.engines.append(k)
        return k

    def _results(self) -> dict:
        return H.results_dirs(H.load_config(self.env))

    def _reset_row(self) -> None:
        """Fresh per-row state: stop the previous row's engines and
        wipe the results + dedup dirs (the table runs 12 rows in one
        test method, and the slot-charge / dedup reads glob the
        results dir — a hog record from row N must not charge row N+1
        or dedupe its prompt)."""
        for e in self.engines:
            e.stop()
        self.engines = []
        shutil.rmtree(self.results, ignore_errors=True)
        shutil.rmtree(self.health_dir / "dedup", ignore_errors=True)
        # a stale health.json from row N would make row N+1's
        # manager-down (84) case spawn against the previous pool —
        # remove it so each row starts from its own setup.
        (self.health_dir / "health.json").unlink(missing_ok=True)
        shutil.rmtree(self.health_dir / "control", ignore_errors=True)
        self._last_task_id = None

    def _run(
        self, cmd: str, argv: list[str], poll_s: float = 0.05
    ) -> tuple[int, dict, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = H.dispatch(cmd, argv, self.env, poll_s)
        return code, json.loads(out.getvalue()), err.getvalue()

    def _spawn(self, argv: list[str], poll_s: float = 0.05) -> tuple[int, dict, str]:
        """cmd_spawn with the stdout capture the setup helpers need
        (cmd_spawn returns the exit CODE; the task JSON goes to
        stdout)."""
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = H.cmd_spawn(argv, self.env, poll_s=poll_s)
        return code, json.loads(out.getvalue()), err.getvalue()

    def _run_cmd(self, cmd: str, argv: list[str]) -> tuple[int, dict]:
        """Run a S3.2 command the way the main agent does (the thin
        deploy file, a real subprocess) — the S3.8 #1/#3 shape."""
        r = subprocess.run(
            [PY, str(BIN_DIR / cmd), *argv],
            capture_output=True,
            text=True,
            env=dict(os.environ, **{k: str(v) for k, v in self.env.items()}),
            check=False,
            timeout=120,
        )
        return r.returncode, json.loads(r.stdout) if r.stdout.strip() else {}

    def _wait_state(
        self, d: dict, task_id: str, states: tuple, timeout_s: float = 15.0
    ) -> dict:
        deadline = time.monotonic() + timeout_s
        last = None
        while time.monotonic() < deadline:
            last = H.read_task(d, task_id)
            if last.get("state") in states:
                return last
            time.sleep(0.05)
        raise AssertionError(f"task {task_id} never reached {states}: {last}")


# ------------------------------------------------------------------------
# 3.9 — the S3.2 exit-code closed set (S3.8 #3)
# ------------------------------------------------------------------------
class TestClosedSet(Base):
    """The S3.2 closed set + the per-command mapping, asserted as a
    table (the S3.8 #3 'a test asserts no stray code can be emitted')."""

    SPEC_S32_CLOSED_SET: ClassVar[set[int]] = {0, 2, 3, 4, 5, 8, 20, 21, 22, 80, 82, 84}

    def test_closed_set_is_the_s32_literal(self):
        self.assertEqual(
            set(H.EXIT_CLOSED_SET),
            self.SPEC_S32_CLOSED_SET,
            "H.EXIT_CLOSED_SET must be exactly the S3.2 12-code set",
        )

    def test_per_command_mapping_is_a_subset(self):
        for cmd, codes in H.COMMAND_EXIT_CODES.items():
            self.assertTrue(
                set(codes).issubset(self.SPEC_S32_CLOSED_SET),
                f"{cmd} may emit a code outside the closed set: {sorted(codes)}",
            )

    def test_every_command_has_a_mapping(self):
        for cmd in (
            "bonsai2-spawn",
            "bonsai2-steer",
            "bonsai2-cancel",
            "bonsai2-status",
            "bonsai2-list-instances",
            "bonsai2-health",
            "bonsai2-collect",
            "bonsai2-unload",
            "bonsai2-reload",
        ):
            with self.subTest(cmd=cmd):
                allowed = H.command_allowed_codes(cmd)
                self.assertTrue(set(allowed).issubset(self.SPEC_S32_CLOSED_SET))

    def test_no_stray_code_can_be_emitted(self):
        # the emit() guard: a code outside the closed set RAISES (the
        # S3.1 contract), so a stray exit 1 / 9 / 13 cannot escape.
        for stray in (1, 9, 13, 100):
            with (
                self.subTest(stray=stray),
                self.assertRaises(ValueError),
                redirect_stdout(io.StringIO()),
            ):
                H.emit({"x": 1}, stray, "stray")

    def test_the_table_covers_the_closed_set(self):
        # the union of the named-case table (ALL_EXIT_CODES below) IS
        # the closed set — S3.8 #3: every code in the set is produced by
        # at least one named case, and no code outside it is.
        self.assertEqual(
            {row[0] for row in ALL_EXIT_CODES},
            self.SPEC_S32_CLOSED_SET,
            "the named-case table must cover exactly the 12 closed-set codes",
        )
        for code, cmd, argv, _json, _setup in ALL_EXIT_CODES:
            self.assertTrue(
                code in H.COMMAND_EXIT_CODES[cmd],
                f"table case for exit {code} on {cmd} — the S3.2 mapping does not allow it",
            )


# ------------------------------------------------------------------------
# the table-driven exit-code cases (3.9 + 3.11 + S3.8 #3)
# ------------------------------------------------------------------------
def _setup_healthy_pool(self: Base) -> list[dict]:
    p0, p1 = free_port(), free_port()
    self._write_cfg()
    self._instance(p0)
    self._instance(p1)
    self._write_health([self._inst(0, p0), self._inst(1, p1)])
    return [self._inst(0, p0), self._inst(1, p1)]


def _setup_healthy_pool_4(self: Base) -> None:
    p0 = free_port()
    p1 = free_port()
    self._write_cfg()
    self._instance(p0)
    self._instance(p1)
    self._write_health([self._inst(0, p0, state="bfailed"), self._inst(1, p1, total=1)])
    d = self._results()
    H.ensure_results_dirs(d)
    H.write_task(
        d, {"task_id": "t-fill", "state": "running", "instance": 1, "port": p1}
    )


def _setup_engine_bfailed(self: Base, cause: str) -> None:
    p0, p1 = free_port(), free_port()
    self._write_cfg()
    self._instance(p0)
    self._instance(p1)
    self._write_health(
        [
            self._inst(0, p0, state="bfailed", bfailed_reason=cause),
            self._inst(1, p1, total=1),
        ]
    )
    d = self._results()
    H.ensure_results_dirs(d)
    H.write_task(
        d, {"task_id": "t-fill", "state": "running", "instance": 1, "port": p1}
    )


def _setup_ctx_too_long(self: Base) -> None:
    p0, p1 = free_port(), free_port()
    self._write_cfg()
    self._instance(p0)
    self._instance(p1)
    self._write_health([self._inst(0, p0, ctx=8192), self._inst(1, p1, ctx=16384)])


def _setup_queue_expired(self: Base) -> None:
    p0, p1 = free_port(), free_port()
    self._write_cfg(queue_wait_max_s=1)
    self._instance(p0)
    self._instance(p1)
    self._write_health([self._inst(0, p0, total=1), self._inst(1, p1, total=1)])
    d = self._results()
    H.ensure_results_dirs(d)
    H.write_task(
        d, {"task_id": "t-hog-0", "state": "running", "instance": 0, "port": p0}
    )
    H.write_task(
        d, {"task_id": "t-hog-1", "state": "running", "instance": 1, "port": p1}
    )


def _setup_main_only(self: Base) -> None:
    self._write_cfg()
    self._write_health([], stack="main-only")


def _setup_dead_main(self: Base) -> None:
    dead_port = free_port()
    p0 = free_port()
    self._write_cfg(main_endpoint=f"http://127.0.0.1:{dead_port}")
    self._instance(p0)
    self._write_health([self._inst(0, p0, total=1)])


def _setup_manager_down(self: Base) -> None:
    self._write_cfg()  # no health.json written
    self._instance(free_port())


def _setup_done(self: Base) -> dict:
    p0 = free_port()
    self._write_cfg()
    self._instance(p0)
    self._write_health([self._inst(0, p0, total=1)])
    code, doc, _ = self._spawn(["-i", "fan-out A"], poll_s=0.05)
    assert code == 0, doc
    d = self._results()
    H.ensure_results_dirs(d)
    rec = self._wait_state(d, doc["task_id"], ("done", "failed"))
    assert rec["state"] == "done", rec
    self._last_task_id = doc["task_id"]
    return doc


def _setup_failed(self: Base, variant: str = "badjson") -> dict:
    p0 = free_port()
    self._write_cfg()
    self._instance(p0, variant=variant)
    self._write_health([self._inst(0, p0, total=1)])
    code, doc, _ = self._spawn(["-i", "failing task"], poll_s=0.05)
    assert code == 0, doc
    d = self._results()
    H.ensure_results_dirs(d)
    self._wait_state(d, doc["task_id"], ("failed",), timeout_s=20.0)
    self._last_task_id = doc["task_id"]
    return doc


def _setup_timeout(self: Base) -> dict:
    p0 = free_port()
    self._write_cfg()
    self._instance(p0, variant="slow")
    self._write_health([self._inst(0, p0, total=1)])
    code, doc, _ = self._spawn(
        ["-i", "slow task", "--deadline", str(SLOW_DEADLINE_S)], poll_s=0.05
    )
    assert code == 0, doc
    d = self._results()
    H.ensure_results_dirs(d)
    self._wait_state(d, doc["task_id"], ("timeout",), timeout_s=20.0)
    self._last_task_id = doc["task_id"]
    return doc


def _setup_non_terminal(self: Base) -> dict:
    p0 = free_port()
    self._write_cfg()
    self._instance(p0, variant="slow")
    self._write_health([self._inst(0, p0, total=1)])
    code, doc, _ = self._spawn(["-i", "long task", "--deadline", "60"], poll_s=0.05)
    assert code == 0, doc
    time.sleep(0.3)  # the worker is mid-attempt (the slow fixture sleeps)
    self._last_task_id = doc["task_id"]
    return doc


# The S3.2 closed set, one named case per code. Each row:
# (code, command, argv, the asserted stdout JSON (or a checker fn), setup)
ALL_EXIT_CODES: list[tuple[int, str, list[str], "dict | None", "object"]] = [
    # 0 — spawn accepted (a free slot)
    (
        0,
        "bonsai2-spawn",
        ["-i", "hi"],
        lambda doc: doc.get("state") in ("running", "queued"),
        _setup_healthy_pool,
    ),
    # 2 — malformed args
    (
        2,
        "bonsai2-spawn",
        ["--bogus"],
        {"error": "ERR_ARGS"},
        _setup_healthy_pool,
    ),
    # 3 — routing candidate bfailed (port-conflict family, S1.5)
    (
        3,
        "bonsai2-spawn",
        ["-i", "hi"],
        {"error": "ERR_PORT_IN_USE"},
        _setup_healthy_pool_4,
    ),
    # 4 — routing candidate bfailed with an S1.4 engine-level cause
    (
        4,
        "bonsai2-spawn",
        ["-i", "hi"],
        {"error": "ERR_ENGINE_OOM"},
        lambda self: _setup_engine_bfailed(self, "ERR_VRAM_OOM"),
    ),
    # 5 — no instance's live ctx fits the requested --ctx
    (
        5,
        "bonsai2-spawn",
        ["-i", "hi", "--ctx", "262144"],
        {"error": "ERR_CTX_TOO_LONG"},
        _setup_ctx_too_long,
    ),
    # 8 — queue_wait_max_s expired, the task is still "queued"
    (
        8,
        "bonsai2-spawn",
        ["-i", "hi"],
        lambda doc: doc.get("state") == "queued",
        _setup_queue_expired,
    ),
    # 20 — task done (collect)
    (
        20,
        "bonsai2-collect",
        lambda self: [self._last_task_id],
        lambda doc: doc.get("state") == "done",
        _setup_done,
    ),
    # 21 — task failed (engine answered error/parse fail)
    (
        21,
        "bonsai2-collect",
        lambda self: [self._last_task_id],
        lambda doc: doc.get("state") == "failed",
        _setup_failed,
    ),
    # 22 — task timed out mid-run
    (
        22,
        "bonsai2-collect",
        lambda self: [self._last_task_id],
        lambda doc: doc.get("state") == "timeout",
        _setup_timeout,
    ),
    # 80 — main-only mode (the S2.4 {"reason": ...} JSON)
    (
        80,
        "bonsai2-spawn",
        ["-i", "hi"],
        {"reason": "budget_zero"},
        _setup_main_only,
    ),
    # 82 — main endpoint unhealthy
    (
        82,
        "bonsai2-spawn",
        ["-i", "hi"],
        {"error": "ERR_MAIN_DOWN"},
        _setup_dead_main,
    ),
    # 84 — manager down (health.json absent)
    (
        84,
        "bonsai2-spawn",
        ["-i", "hi"],
        {"error": "ERR_MANAGER_DOWN"},
        _setup_manager_down,
    ),
]


class TestExitCodeTable(Base):
    """The table-driven suite: every closed-set code by a named case
    asserting the exact JSON + code (S3.7 / S3.8 #3)."""

    def _run_case(self, row: tuple) -> None:
        code, cmd, argv, expected, setup = row
        with self.subTest(code=code, cmd=cmd):
            self._reset_row()  # fresh state for this row
            setup(self)
            out, err = io.StringIO(), io.StringIO()
            argv_actual = argv(self) if callable(argv) else argv
            with redirect_stdout(out), redirect_stderr(err):
                rc = H.dispatch(cmd, argv_actual, self.env, poll_s=0.05)
            self.assertEqual(
                rc,
                code,
                f"exit code: want {code}, got {rc} (cmd={cmd} argv={argv_actual})",
            )
            payload = json.loads(out.getvalue())
            if callable(expected):
                self.assertTrue(expected(payload), f"JSON check failed: {payload}")
            elif isinstance(expected, dict):
                for k, v in expected.items():
                    self.assertEqual(
                        payload.get(k),
                        v,
                        f"JSON field {k!r}: want {v!r}, got {payload.get(k)!r} ({payload})",
                    )

    def test_all_exit_codes_table_driven(self):
        for row in ALL_EXIT_CODES:
            self._run_case(row)

    def test_exit_4_engine_causes_table(self):
        # the S1.4 engine-level causes (OOM/download/hash) all map to 4;
        # each cause names the S1.4 error in the detail (S3.2 4-case).
        for cause in ("ERR_VRAM_OOM", "ERR_HASH_MISMATCH", "ERR_DOWNLOAD"):
            with self.subTest(cause=cause):
                _setup_engine_bfailed(self, cause)
                out, _ = io.StringIO(), io.StringIO()
                with redirect_stdout(out), redirect_stderr(io.StringIO()):
                    rc = H.dispatch("bonsai2-spawn", ["-i", "hi"], self.env)
                self.assertEqual(rc, 4, f"{cause} must map to exit 4")
                payload = json.loads(out.getvalue())
                self.assertEqual(payload["error"], "ERR_ENGINE_OOM")
                self.assertIn(cause, payload["detail"])

    def test_exit_4_not_a_port_conflict(self):
        # the S3.2 distinction: bfailed WITHOUT an S1.4 cause is the
        # 3-case (port-conflict family), not 4.
        _setup_healthy_pool_4(self)
        out, _ = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(io.StringIO()):
            rc = H.dispatch("bonsai2-spawn", ["-i", "hi"], self.env)
        self.assertEqual(rc, 3)
        self.assertEqual(json.loads(out.getvalue())["error"], "ERR_PORT_IN_USE")

    def test_exit_0_collect_non_terminal_poll(self):
        # collect on a running task -> 0 (the caller polls, S3.2).
        doc = _setup_non_terminal(self)
        out, _ = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(io.StringIO()):
            rc = H.dispatch("bonsai2-collect", [doc["task_id"]], self.env)
        self.assertEqual(rc, 0, "a non-terminal task's collect is the poll case (0)")
        self.assertEqual(json.loads(out.getvalue())["state"], "running")


# ------------------------------------------------------------------------
# 3.11 — S3.7 named cases
# ------------------------------------------------------------------------
class TestSlotContention(Base):
    """S3.7 slot contention: 2 instances x 2 slots, 5 spawns ->
    4 running + 1 queued."""

    def test_five_spawns_four_slots(self):
        p0, p1 = free_port(), free_port()
        self._write_cfg(queue_wait_max_s=1)
        self._instance(p0, variant="hold")  # the hold keeps a slot charged
        self._instance(p1, variant="hold")
        self._write_health([self._inst(0, p0, total=2), self._inst(1, p1, total=2)])
        d = self._results()
        H.ensure_results_dirs(d)
        # 4 real workers fill the 4 slots; the hold variant keeps each
        # attempt in flight ~3 s, so the slots stay charged past the
        # 5th spawn's 1 s queue-wait.
        for k in range(4):
            code, doc, _ = self._spawn(
                ["-i", f"worker {k}", "--deadline", "30"], poll_s=0.05
            )
            self.assertEqual(code, 0, f"spawn {k} must be accepted")
        # the 5th: no slot free on either instance -> queued, exit 8
        code, doc, _ = self._spawn(["-i", "the 5th"], poll_s=0.05)
        self.assertEqual(code, 8, "5th spawn with 0 free slots -> exit 8 (queue_wait)")
        self.assertEqual(doc["state"], "queued")
        # 4 running + 1 queued on disk
        running = [r for r in H.iter_tasks(d) if r.get("state") == "running"]
        queued = [r for r in H.iter_tasks(d) if r.get("state") == "queued"]
        self.assertEqual(len(running), 4, "4 tasks are running")
        self.assertEqual(len(queued), 1, "1 task is queued")


class TestFailureInjection(Base):
    """S3.7 failure injection: kill mid-task -> engine_retried -> done;
    non-JSON body -> 21 ERR_PARSE."""

    def test_kill_mid_task_retried_then_done(self):
        p0 = free_port()
        p1 = free_port()
        self._write_cfg()
        killer = self._killed_instance(p0)  # instance 0: the kill target
        self._instance(p1)  # instance 1: the healthy retry target
        self._write_health([self._inst(0, p0, total=1), self._inst(1, p1, total=1)])
        # spawn a task on instance 0 (least-loaded tie-break picks i=0);
        # the engine holds the stream mid-way, so we can kill it
        # mid-task.
        code, doc, _ = self._spawn(
            ["-i", "kill-me-mid-task", "--deadline", "60"], poll_s=0.05
        )
        self.assertEqual(code, 0)
        self.assertEqual(doc["instance"], 0)
        d = self._results()
        H.ensure_results_dirs(d)
        # wait until the task is running on i=0 (the worker's attempt is
        # in flight inside the engine's mid-stream hold)
        rec = self._wait_state(d, doc["task_id"], ("running",))
        self.assertEqual(rec["state"], "running")
        time.sleep(0.8)  # the engine is inside its 1.2 s hold
        # kill the engine mid-task (the S3.7 "kill mid-task" case): the
        # in-flight connection dies without a response -> the worker's
        # attempt fails (connection error) -> the S1.5 one-retry -> i=1.
        killer.kill()
        rec = self._wait_state(
            d, doc["task_id"], ("engine_retried", "done", "failed"), timeout_s=30.0
        )
        self.assertEqual(
            rec["state"],
            "engine_retried",
            "the S1.5 one-retry: the engine-down mid-task lands engine_retried",
        )
        # the retry completes on the other instance
        rec = self._wait_state(d, doc["task_id"], ("done", "failed"), timeout_s=30.0)
        self.assertEqual(rec["state"], "done", "the retry on i=1 completes")
        self.assertEqual(
            rec["instance"], 1, "the record's instance moved to the retry target"
        )
        self.assertIn(1, rec.get("instances_tried", []))

    def test_nonjson_body_is_21(self):
        p0 = free_port()
        self._write_cfg()
        self._instance(p0, variant="badjson")
        self._write_health([self._inst(0, p0, total=1)])
        code, doc, _ = self._spawn(["-i", "non-json task"], poll_s=0.05)
        self.assertEqual(code, 0)
        d = self._results()
        H.ensure_results_dirs(d)
        rec = self._wait_state(d, doc["task_id"], ("failed",), timeout_s=20.0)
        self.assertEqual(rec["error"], "ERR_PARSE", "S3.7: non-JSON body -> ERR_PARSE")
        out, _ = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(io.StringIO()):
            rc = H.dispatch("bonsai2-collect", [doc["task_id"]], self.env)
        self.assertEqual(rc, 21, "collect of a failed task -> 21")


class TestRouting(Base):
    """S3.7 routing: uneven fake payloads -> least-loaded; priority
    preemption (high jumps the queue)."""

    def test_least_loaded_routing(self):
        p0, p1 = free_port(), free_port()
        self._write_cfg()
        self._instance(p0)
        self._instance(p1)
        self._write_health([self._inst(0, p0), self._inst(1, p1)])
        d = self._results()
        H.ensure_results_dirs(d)
        # uneven load: i=0 busy with 3, i=1 empty.
        for k in range(3):
            H.write_task(
                d,
                {
                    "task_id": f"t-busy-{k}",
                    "state": "running",
                    "instance": 0,
                    "port": p0,
                },
            )
        code, doc, _ = self._spawn(["-i", "least-loaded"], poll_s=0.05)
        self.assertEqual(code, 0)
        self.assertEqual(
            doc["instance"], 1, "least-loaded routing picks the empty instance"
        )

    def test_priority_preemption(self):
        p0, p1 = free_port(), free_port()
        self._write_cfg(queue_wait_max_s=30)
        self._instance(p0)
        self._instance(p1)
        self._write_health([self._inst(0, p0, total=1), self._inst(1, p1, total=1)])
        d = self._results()
        H.ensure_results_dirs(d)
        # Fill both instances so no slot is free yet.
        H.write_task(
            d, {"task_id": "t-fill-0", "state": "running", "instance": 0, "port": p0}
        )
        H.write_task(
            d, {"task_id": "t-fill-1", "state": "running", "instance": 1, "port": p1}
        )
        # Two QUEUED tasks both targeting i=0: a low created first, a
        # high created LATER. claim_slot promotes the queue head by
        # (priority rank, created_ts, task_id) — so the high one must
        # win the freed slot even though it was enqueued after the low.
        now = time.time()
        H.write_task(
            d,
            {
                "task_id": "t-low",
                "state": "queued",
                "instance": 0,
                "port": p0,
                "priority": "low",
                "ctx_need": None,
                "created_ts": now,
            },
        )
        H.write_task(
            d,
            {
                "task_id": "t-high",
                "state": "queued",
                "instance": 0,
                "port": p0,
                "priority": "high",
                "ctx_need": None,
                "created_ts": now + 1.0,
            },
        )
        # Free the slot on i=0 (the running task completes).
        H.write_task(
            d, {"task_id": "t-fill-0", "state": "done", "instance": 0, "port": p0}
        )
        doc = H.load_health_doc(env=self.env)
        claimed = H.claim_slot(d, doc)
        self.assertIsNotNone(claimed, "a free slot on i=0 must be claimed")
        self.assertEqual(
            claimed["task_id"],
            "t-high",
            "the high-priority queued task jumps the low-priority one "
            "(S3.3 'high jumps the queue, low waits')",
        )
        self.assertEqual(
            H.read_task(d, "t-high")["state"],
            "running",
            "the promoted high task is running",
        )
        self.assertEqual(
            H.read_task(d, "t-low")["state"],
            "queued",
            "the low task stays queued behind the high one",
        )


class TestIdempotency(Base):
    """S3.7 idempotency: a same-prompt spawn with a live task is a
    no-op (the dedup entry is not re-created)."""

    def test_same_prompt_live_task_is_noop(self):
        p0, p1 = free_port(), free_port()
        self._write_cfg()
        self._instance(p0)
        self._instance(p1)
        self._write_health([self._inst(0, p0)])
        code, doc, _ = self._spawn(
            ["-i", "dedup prompt", "--deadline", "60"], poll_s=0.05
        )
        self.assertEqual(code, 0)
        tid = doc["task_id"]
        # a second spawn of the same prompt: the live task is the
        # idempotent no-op — same task id, not a new one.
        code2, doc2, _ = self._spawn(
            ["-i", "dedup prompt", "--deadline", "60"], poll_s=0.05
        )
        self.assertEqual(code2, 0)
        self.assertEqual(
            doc2["task_id"], tid, "same prompt -> same task id (idempotent)"
        )


# ------------------------------------------------------------------------
# 3.12 / S3.8 #4 — N=0 main-only mode: spawn -> exit 80 + the S2.4 JSON
# ------------------------------------------------------------------------
class TestMainOnly(Base):
    """S3.8 #4 (the hermetic half): at N=0 the subagent OFFLOAD command
    (`spawn`) exits 80 with the S2.4 `{"reason": ...}` JSON, echoing
    the health.json `main_only_reason` (the box is usable — the task
    runs on main). This is the S2.4 headline + the S3.2 closed-set row
    for 80.

    Scoping note (recorded for the PR 'Uncertainties & decisions'):
    S2.4 / S3.8 #4 say 'every subagent command -> exit 80', but the
    LOCKED S3.2 per-command table is more specific — it lists 80 ONLY
    under `spawn`, and pins `status`/`health`/`list-instances` at 0
    (with `main_only_reason` echoed, the S3.1 document) and
    `collect`/`steer`/`cancel`/`unload`/`reload` on their own code sets
    (they operate on a task id / the pool, not on the pool being
    off). The precise S3.2-table reading is what the code implements and
    what the green status suite already asserts; this class proves the
    spawn-80 + `{"reason": ...}` contract the two S2.4/S3.8 passages
    point at, for both S2.4 reasons (`budget_zero`, `user_unloaded`).
    """

    def test_spawn_80_user_unloaded(self):
        self._write_cfg()
        # a user-unloaded pool carries the S2.4 user_unloaded reason
        self._write_health_doc(
            {
                "stack": "main-only",
                "instances": [],
                "main_endpoint": "http://x",
                "main_healthy": True,
                "main_only_reason": "user_unloaded",
            }
        )
        code, doc, _ = self._spawn(["-i", "run on main"], poll_s=0.05)
        self.assertEqual(code, 80, "N=0 -> spawn exits 80 (main-only, S2.4)")
        self.assertEqual(
            doc,
            {"reason": "user_unloaded"},
            'the S2.4 JSON: exactly {"reason": ...}, echoing the reason',
        )

    def test_spawn_80_budget_zero(self):
        self._write_cfg()
        self._write_health_doc(
            {
                "stack": "main-only",
                "instances": [],
                "main_endpoint": "http://x",
                "main_healthy": True,
                "main_only_reason": "budget_zero",
            }
        )
        code, doc, _ = self._spawn(["-i", "run on main"], poll_s=0.05)
        self.assertEqual(code, 80)
        self.assertEqual(doc, {"reason": "budget_zero"})

    def test_spawn_80_default_reason_when_absent(self):
        # S3.2/S3.1: a main-only document with no explicit
        # main_only_reason still echoes the default (budget_zero).
        self._write_cfg()
        self._write_health_doc(
            {
                "stack": "main-only",
                "instances": [],
                "main_endpoint": "http://x",
                "main_healthy": True,
            }
        )
        code, doc, _ = self._spawn(["-i", "run on main"], poll_s=0.05)
        self.assertEqual(code, 80)
        self.assertEqual(doc, {"reason": "budget_zero"})

    def test_status_reports_main_only_not_80(self):
        # The S3.2 table pins `status` at 0 (not 80) at N=0, echoing
        # main_only_reason in the S3.1 document — the precise reading
        # (the 8 is reserved for the offload command; status is a read).
        self._write_cfg()
        self._write_health_doc(
            {
                "stack": "main-only",
                "instances": [],
                "main_endpoint": "http://x",
                "main_healthy": True,
                "main_only_reason": "user_unloaded",
            }
        )
        code, doc, _ = self._run("bonsai2-status", [])
        self.assertEqual(code, 0, "status at N=0 -> 0 (S3.2 table), main-only is data")
        self.assertEqual(doc.get("stack"), "main-only")
        self.assertEqual(doc.get("main_only_reason"), "user_unloaded")
        self.assertEqual(doc.get("instances"), [])


# ------------------------------------------------------------------------
# 3.10 + 3.11 — pool-control cases via the MockManager stub (S3.7)
# ------------------------------------------------------------------------
class TestPoolControlStub(Base):
    """The S3.7 pool-control cases through the MockManager stub:
    unload no-op, reload partial, bad-args, dead-manager — exact
    JSON + code (S3.8 #5). The stub services the control/ queue in a
    background loop (the manager's steady-loop model: `process_control`
    runs once per health cycle, and the command blocks on the ack-wait
    — so the service pass must run concurrently)."""

    def _manager(self) -> MockManager:
        self._write_cfg()
        m = MockManager(self.health_dir)
        m.add_instance(0, free_port(), state="healthy")
        m.add_instance(1, free_port(), state="healthy")
        m.start_loop()
        return m

    def tearDown(self):
        try:
            for m in getattr(self, "_mgrs", []):
                m.stop_loop()
        finally:
            super().tearDown()

    def setUp(self):
        super().setUp()
        self._mgrs: list[MockManager] = []

    def _mgr(self) -> MockManager:
        m = self._manager()
        self._mgrs.append(m)
        return m

    def test_unload_all_ok(self):
        m = self._mgr()
        code, doc, _ = self._run("bonsai2-unload", ["--all"])
        self.assertEqual(code, 0, "unload --all -> 0")
        self.assertEqual(doc["type"], "unload")
        self.assertEqual(sorted(doc["unloaded"]), [0, 1])
        # health.json now shows both bunloaded (the S2.4 user_unloaded
        # document follows: zero running instances)
        health = m.health_doc()
        for inst in health["instances"]:
            self.assertEqual(inst["state"], "bunloaded")

    def test_unload_noop_already_unloaded(self):
        m = self._mgr()
        m.set_state(0, "bunloaded")
        code, doc, _ = self._run("bonsai2-unload", ["--instances", "0"])
        self.assertEqual(code, 0)
        self.assertEqual(
            doc.get("unloaded"),
            [],
            "an already-unloaded index is a no-op for that index (S3.2)",
        )
        self.assertEqual(doc.get("state"), "ok")

    def test_reload_partial(self):
        m = self._mgr()
        m.set_state(0, "bunloaded")
        m.set_state(1, "bunloaded")
        m.grant_reload(1, False)  # the S1.4 tier rejects i=1
        code, doc, _ = self._run("bonsai2-reload", ["--all"])
        self.assertEqual(code, 0, "a partial reload is still a valid state (exit 0)")
        self.assertEqual(doc.get("state"), "partial")
        self.assertEqual(doc.get("reloaded"), [0], "only the granted index reloaded")
        self.assertIn("WARN_VRAM_SHORTFALL", doc.get("detail", ""))
        health = m.health_doc()
        by_i = {inst["i"]: inst for inst in health["instances"]}
        self.assertEqual(by_i[0]["state"], "healthy")
        self.assertEqual(by_i[1]["state"], "bunloaded")

    def test_bad_args_both_selectors(self):
        self._mgr()
        code, doc, _ = self._run("bonsai2-unload", ["--all", "--instances", "0"])
        self.assertEqual(code, 2, "both selectors -> 2 (malformed args)")
        self.assertEqual(doc["error"], "ERR_ARGS")

    def test_bad_args_unknown_index(self):
        self._mgr()
        code, doc, _ = self._run("bonsai2-reload", ["--instances", "abc"])
        self.assertEqual(code, 2)
        self.assertEqual(doc["error"], "ERR_ARGS")

    def test_dead_manager_84(self):
        m = self._mgr()
        m.set_dead()  # a dead manager writes no ack
        code, doc, _ = self._run("bonsai2-unload", ["--all"])
        self.assertEqual(code, 84, "a dead manager -> exit 84 (S3.2)")
        self.assertEqual(doc["error"], "ERR_MANAGER_DOWN")


if __name__ == "__main__":
    unittest.main()
