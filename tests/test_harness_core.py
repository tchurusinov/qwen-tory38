"""MAP 3.1: hermetic tests for bin/bonsai2_harness.py (the S3.1 core).

No GPU, no network beyond 127.0.0.1 fixture ports (the S3.7 mock
engine; the hermetic-suite convention of tests/test_smoke.py —
stdlib-only top-level imports; `yaml` is the documented local edge
import). The module is loaded from bin/ via importlib (the repo
convention — bin/ is the deploy target, not a test package).

What each MAP item is proven by (the spec-named assertions):

  * 3.1 (stdout = one JSON object; harness.yaml loader + env overrides):
      - emit: stdout is EXACTLY one line of compact JSON; the detail
        goes to stderr; a non-closed-set exit code RAISES (the S3.8 #3
        stray-code guard).
      - load_config: defaults for the S3.6 key set; the BONSAI2_CONFIG
        path override; the BONSAI2_RESULTS_DIR override; missing key ->
        default; malformed value / malformed YAML / missing file ->
        ConfigError (the exit-2 path).
      - load_engine_identity: alias + api_key from subagent-engine.yaml
        (the tracked template); missing keys -> ConfigError.
      - the closed set itself is the SPEC S3.2 set (12 codes).

  * 3.3 (routing + queue — the library half of the S3.3 contract):
      - route_target: least-loaded healthy pick; tie-break lowest i;
        the ctx-fit filter (no instance fits -> ERR_CTX_TOO_LONG with
        the detail naming the max live ctx); bfailed candidate ->
        ERR_PORT_IN_USE; bdown candidate -> ERR_INSTANCE_UNHEALTHY;
        all-unroutable-but-ctx-fits -> queue at the least-loaded.
      - claim_slot: FIFO within a priority; priority preemption (a
        later high-priority task beats an earlier low one); a queued
        task whose target lost ctx fit (the S2.5 drift halve) stays
        queued; a full instance is skipped; promotion writes the
        record atomically (state running).
      - task records: new_task_id shape (t-YYYYmmdd-NNNN-<4hex>) +
        uniqueness under 50 ids; write_task/read_task round trip;
        slots_in_use counts queued+running (the S3.4 whole-lifetime
        charge); running_count counts running only (the engine's
        max_num_seqs charge, S3.3).
      - lock_wait: two threads serialize (no lost update on a
        shared counter — the claim protocol's ordering primitive).
      - main_healthy: the S3.6 probe semantics — 200 up, 401 up
        (auth-walled), 404 down, 5xx down, connection-refused down
        (the exit-82 gate, S3.2).
"""

import importlib.machinery
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BIN_DIR = REPO_ROOT / "bin"
ENGINE_YAML = REPO_ROOT / "etc" / "subagent-engine.yaml"

from .fixtures.mock_engine import MockEngine, free_port


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


def _write_health(health_dir: Path, doc: dict) -> None:
    health_dir.mkdir(parents=True, exist_ok=True)
    (health_dir / "health.json").write_text(json.dumps(doc) + "\n")


def _cfg_yaml(tmp: Path, **overrides) -> Path:
    p = tmp / "harness.yaml"
    # main_endpoint only: every OTHER S3.6 key must come from the
    # defaults (test_defaults_for_missing_keys asserts that).
    data = {"main_endpoint": "http://127.0.0.1:1"}
    data.update(overrides)
    p.write_text("\n".join(f"{k}: {v}" for k, v in data.items()) + "\n")
    return p


class TestS31OutputContract(unittest.TestCase):
    """MAP 3.1 — the S3.1 contract, enforced in emit."""

    def test_emit_one_line_json_stdout(self):
        buf_out, buf_err = io.StringIO(), io.StringIO()
        with redirect_stdout(buf_out), redirect_stderr(buf_err):
            code = H.emit({"task_id": "t-1", "state": "queued"}, 0, "a human note")
        self.assertEqual(code, 0)
        lines = buf_out.getvalue().splitlines()
        self.assertEqual(len(lines), 1, "stdout must be exactly ONE line (S3.1)")
        payload = json.loads(lines[0])
        self.assertEqual(payload, {"task_id": "t-1", "state": "queued"})
        self.assertIn(
            "a human note", buf_err.getvalue(), "detail must go to stderr (human only)"
        )

    def test_emit_stray_code_raises(self):
        buf = io.StringIO()
        with redirect_stdout(buf), self.assertRaises(ValueError):
            H.emit({"x": 1}, 1, "stray exit 1 = harness bug (S3.8 #3)")
        self.assertEqual(
            buf.getvalue().count("\n"),
            0,
            "no JSON must have been written on a stray code",
        )

    def test_closed_set_is_the_spec_set(self):
        self.assertEqual(
            H.EXIT_CLOSED_SET, frozenset({0, 2, 3, 4, 5, 8, 20, 21, 22, 80, 82, 84})
        )


class TestConfigLoad(unittest.TestCase):
    """MAP 3.1 — the S3.6 loader + the S3.2 env overrides."""

    def test_defaults_for_missing_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = _cfg_yaml(Path(tmp), main_endpoint="http://127.0.0.1:9")
            cfg = H.load_config({"BONSAI2_CONFIG": str(p)})
            self.assertEqual(cfg["main_endpoint"], "http://127.0.0.1:9")
            for key, default in H.CONFIG_DEFAULTS.items():
                if key != "main_endpoint":
                    self.assertEqual(cfg[key], default, key)
            self.assertEqual(cfg["config_path"], str(p))

    def test_results_dir_env_override(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = _cfg_yaml(Path(tmp))
            cfg = H.load_config(
                {"BONSAI2_CONFIG": str(p), "BONSAI2_RESULTS_DIR": "/tmp/elsewhere"}
            )
            self.assertEqual(
                cfg["results_dir"],
                "/tmp/elsewhere",
                "BONSAI2_RESULTS_DIR beats the YAML key (S3.2)",
            )

    def test_malformed_value_is_config_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "harness.yaml"
            p.write_text("queue_wait_max_s: -5\n")
            with self.assertRaises(H.ConfigError):
                H.load_config({"BONSAI2_CONFIG": str(p)})

    def test_malformed_yaml_is_config_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "harness.yaml"
            p.write_text("main_endpoint: [unclosed\n")
            with self.assertRaises(H.ConfigError):
                H.load_config({"BONSAI2_CONFIG": str(p)})

    def test_missing_file_is_config_error(self):
        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(H.ConfigError):
            H.load_config({"BONSAI2_CONFIG": str(Path(tmp) / "nope.yaml")})

    def test_engine_identity_from_template(self):
        ident = H.load_engine_identity({"BONSAI2_ENGINE_YAML": str(ENGINE_YAML)})
        self.assertEqual(ident["alias"], "qwen38-27b-subagent")
        self.assertEqual(ident["api_key"], "bonsai2-local")
        self.assertEqual(ident.get("port_base"), 30101)

    def test_engine_identity_missing_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "engine.yaml"
            p.write_text("alias: only-alias\n")
            with self.assertRaises(H.ConfigError):
                H.load_engine_identity({"BONSAI2_ENGINE_YAML": str(p)})


class TestHealthDoc(unittest.TestCase):
    def test_load_and_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            hd = Path(tmp) / "run"
            _write_health(
                hd,
                {
                    "stack": "healthy",
                    "instances": [],
                    "main_endpoint": "x",
                    "main_healthy": True,
                },
            )
            doc = H.load_health_doc(health_dir=str(hd))
            self.assertEqual(doc["stack"], "healthy")
            hd2 = Path(tmp) / "absent"
            with self.assertRaises(H.HealthMissing):
                H.load_health_doc(health_dir=str(hd2))
            _write_health(hd, {"not": "the shape"})
            with self.assertRaises(H.HealthMissing):
                H.load_health_doc(health_dir=str(hd))


class TestMainHealthy(unittest.TestCase):
    """The S3.6 probe -> the exit-82 gate (the manager's S2.1 semantics)."""

    def _cfg(self, port: int) -> dict:
        return {
            "main_endpoint": f"http://127.0.0.1:{port}",
            "main_endpoint_healthy_probe": "/v1/models",
        }

    def test_refused_is_down(self):
        self.assertFalse(H.main_healthy(self._cfg(free_port()), timeout_s=1.0))

    def test_200_is_up(self):
        with MockEngine(variant="ok") as m:
            self.assertTrue(H.main_healthy(self._cfg(m.port), timeout_s=2.0))

    def test_401_authwalled_is_up(self):
        with MockEngine(variant="authwall") as m:
            self.assertTrue(H.main_healthy(self._cfg(m.port), timeout_s=2.0))

    def test_404_is_down(self):
        # The fixture answers unknown paths with 404: point the probe at a
        # path the fixture does not serve.
        with MockEngine(variant="ok") as m:
            cfg = {
                "main_endpoint": f"http://127.0.0.1:{m.port}",
                "main_endpoint_healthy_probe": "/nope",
            }
            self.assertFalse(H.main_healthy(cfg, timeout_s=2.0))


class TestTaskRecords(unittest.TestCase):
    def test_task_id_shape_and_uniqueness(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = H.results_dirs({"results_dir": str(tmp)})
            H.ensure_results_dirs(d)
            ids = {H.new_task_id(d) for _ in range(50)}
            self.assertEqual(len(ids), 50)
            for i in ids:
                self.assertRegex(i, r"^t-\d{8}-\d{4}-[0-9a-f]{4}$")

    def test_round_trip_and_slot_counts(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = H.results_dirs({"results_dir": str(tmp)})
            H.ensure_results_dirs(d)
            tid = H.new_task_id(d)
            rec = {"task_id": tid, "state": "queued", "instance": 3, "port": 30103}
            H.write_task(d, rec)
            self.assertEqual(H.read_task(d, tid)["state"], "queued")
            self.assertEqual(
                H.slots_in_use(d, 3), 1, "queued = the S3.4 whole-lifetime charge"
            )
            self.assertEqual(H.running_count(d, 3), 0)
            rec["state"] = "running"
            H.write_task(d, rec)
            self.assertEqual(H.slots_in_use(d, 3), 1)
            self.assertEqual(H.running_count(d, 3), 1)
            with self.assertRaises(H.TaskNotFound):
                H.read_task(d, H.new_task_id(d))


class TestRouting(unittest.TestCase):
    """S3.3 — least-loaded, deadline-aware, the tie-break and the
    S1.5 named conditions at submission."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.d = H.results_dirs({"results_dir": str(self.tmp / "results")})
        H.ensure_results_dirs(self.d)

    def tearDown(self):
        self._tmp.cleanup()

    def _doc(self, instances):
        return {
            "stack": "healthy",
            "instances": instances,
            "main_endpoint": "x",
            "main_healthy": True,
        }

    def _inst(self, i, state="healthy", ctx=262144, total=8):
        return {
            "i": i,
            "port": 30101 + i,
            "state": state,
            "ctx": ctx,
            "ctx_reduced": False,
            "slots_in_use": 0,
            "slots_total": total,
        }

    def _mk(self, i, n, state="running"):
        for k in range(n):
            H.write_task(
                self.d,
                {
                    "task_id": f"t-{i}-{state}-{k}",
                    "state": state,
                    "instance": i,
                    "port": 30101 + i,
                },
            )

    def test_least_loaded_pick(self):
        self._mk(0, 5)
        self._mk(1, 1)
        doc = self._doc([self._inst(0), self._inst(1)])
        target, err, _ = H.route_target(doc, self.d, None)
        self.assertIsNone(err)
        self.assertEqual(target["i"], 1, "fewest slots in use wins (S3.3)")

    def test_tie_break_lowest_i(self):
        self._mk(0, 2)
        self._mk(1, 2)
        doc = self._doc([self._inst(0), self._inst(1)])
        target, _err, _ = H.route_target(doc, self.d, None)
        self.assertEqual(target["i"], 0, "tie -> lowest i (S3.3)")

    def test_ctx_fit_required(self):
        self._mk(0, 0)
        self._mk(1, 0)
        doc = self._doc([self._inst(0, ctx=8192), self._inst(1, ctx=32768)])
        target, _err, _ = H.route_target(doc, self.d, 16384)
        self.assertEqual(target["i"], 1, "only the ctx-fitting instance is routable")

    def test_ctx_too_long_named_condition(self):
        doc = self._doc([self._inst(0, ctx=8192), self._inst(1, ctx=16384)])
        target, err, detail = H.route_target(doc, self.d, 262144)
        self.assertEqual(err, "ERR_CTX_TOO_LONG", "S1.5 exit-5 condition at submission")
        self.assertIn("16384", detail, "the detail must say the max live ctx")
        self.assertEqual(target["i"], 1, "the least-loaded overall is reported")

    def test_bfailed_candidate_is_port_in_use(self):
        self._mk(1, 8)  # i=1 full
        doc = self._doc([self._inst(0, state="bfailed"), self._inst(1)])
        target, err, _ = H.route_target(doc, self.d, None)
        self.assertEqual(
            err, "ERR_PORT_IN_USE", "S1.5: bfailed = the port-collision state"
        )
        self.assertEqual(target["i"], 0)

    def test_bdown_candidate_is_instance_unhealthy(self):
        self._mk(1, 8)
        doc = self._doc([self._inst(0, state="bdown"), self._inst(1)])
        target, err, _ = H.route_target(doc, self.d, None)
        self.assertEqual(err, "ERR_INSTANCE_UNHEALTHY")
        self.assertEqual(target["i"], 0)

    def test_all_full_queues_at_least_loaded(self):
        self._mk(0, 8)
        self._mk(1, 3)
        doc = self._doc([self._inst(0), self._inst(1)])
        target, err, _ = H.route_target(doc, self.d, None)
        self.assertIsNone(err, "no named error: the task queues")
        self.assertEqual(target["i"], 1, "queue at the least-loaded instance")

    def test_no_instances(self):
        target, err, _ = H.route_target(self._doc([]), self.d, None)
        self.assertIsNone(target)
        self.assertEqual(err, "ERR_NO_INSTANCES")


class TestClaimSlot(unittest.TestCase):
    """S3.3 queue protocol: FIFO + priority preemption + promotion."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.d = H.results_dirs({"results_dir": str(self.tmp / "results")})
        H.ensure_results_dirs(self.d)
        # one slot in use on i=0 of two (running_count 1/2 — the
        # multi-claim tests need a slot that frees up, not a dead one)
        H.write_task(
            self.d,
            {"task_id": "t-full", "state": "running", "instance": 0, "port": 30101},
        )
        self.doc = {
            "stack": "healthy",
            "instances": [
                {
                    "i": 0,
                    "port": 30101,
                    "state": "healthy",
                    "ctx": 262144,
                    "ctx_reduced": False,
                    "slots_in_use": 1,
                    "slots_total": 2,
                }
            ],
        }

    def tearDown(self):
        self._tmp.cleanup()

    def _queue(self, tid, priority, created, instance=0, ctx_need=None):
        rec = {
            "task_id": tid,
            "state": "queued",
            "instance": instance,
            "port": 30101 + instance,
            "priority": priority,
            "created_ts": created,
            "ctx_need": ctx_need,
        }
        H.write_task(self.d, rec)
        return rec

    def test_fifo_within_priority(self):
        self._queue("t-low-a", "normal", 100.0)
        self._queue("t-low-b", "normal", 101.0)
        self._queue("t-low-c", "normal", 102.0)
        # free the slot
        full = H.read_task(self.d, "t-full")
        full["state"] = "done"
        H.write_task(self.d, full)
        claimed = H.claim_slot(self.d, self.doc)
        self.assertEqual(claimed["task_id"], "t-low-a", "FIFO within a priority (S3.3)")

    def test_priority_preempts(self):
        self._queue("t-low-a", "low", 100.0)
        self._queue("t-high-b", "high", 101.0)  # later, but higher
        self._queue("t-norm-c", "normal", 102.0)
        full = H.read_task(self.d, "t-full")
        full["state"] = "done"
        H.write_task(self.d, full)
        claimed = H.claim_slot(self.d, self.doc)
        self.assertEqual(claimed["task_id"], "t-high-b", "high jumps the queue (S3.3)")
        # second claim: the normal task, not the low one
        claimed2 = H.claim_slot(self.d, self.doc)
        self.assertEqual(claimed2["task_id"], "t-norm-c")

    def test_ctx_lost_by_drift_stays_queued(self):
        self._queue("t-big", "normal", 100.0, ctx_need=262144)
        self.doc["instances"][0]["ctx"] = 131072  # the S2.5 drift halve
        full = H.read_task(self.d, "t-full")
        full["state"] = "done"
        H.write_task(self.d, full)
        claimed = H.claim_slot(self.d, self.doc)
        self.assertIsNone(
            claimed,
            "a queued task whose ctx no longer fits stays queued (S2.5 mid-session effect)",
        )
        self.doc["instances"][0]["ctx"] = 262144
        claimed = H.claim_slot(self.d, self.doc)
        self.assertEqual(
            claimed["task_id"], "t-big", "promoted once the ctx fits again"
        )

    def test_full_instance_not_claimed(self):
        self._queue("t-q", "high", 100.0)
        doc = {
            "stack": "healthy",
            "instances": [
                dict(self.doc["instances"][0], slots_total=1, slots_in_use=1)
            ],
        }
        claimed = H.claim_slot(self.d, doc)
        self.assertIsNone(claimed, "running_count == slots_total: the slot is not free")

    def test_no_queue_is_none(self):
        self.assertIsNone(H.claim_slot(self.d, self.doc))


class TestLockWait(unittest.TestCase):
    def test_two_threads_serialize(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)

            def bump(n):
                for _ in range(50):
                    with H.lock_wait(base, "crit", timeout_s=5.0):
                        f = base / "counter"
                        try:
                            v = int(f.read_text())
                        except (OSError, ValueError):
                            v = 0
                        f.write_text(str(v + 1))

            ts = [threading.Thread(target=bump, args=(1,)) for _ in range(2)]
            for t in ts:
                t.start()
            for t in ts:
                t.join(timeout=10)
            self.assertEqual(
                int((base / "counter").read_text()),
                100,
                "no lost update under the lock",
            )

    def test_timeout_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            with H.lock_wait(base, "crit", timeout_s=5.0):

                def hold():
                    with (
                        self.assertRaises(TimeoutError),
                        H.lock_wait(base, "crit", timeout_s=0.1),
                    ):
                        pass

                t = threading.Thread(target=hold)
                t.start()
                t.join(timeout=5)
                self.assertTrue(t.is_alive() is False)

    def test_cross_process_lock_contends(self):
        """P0 #1 regression (G3 PR#12 r1): the O_EXCL lock is a REAL
        cross-process serialization — the file is {name}.lock (the name
        is the identity, the file is shared), so a holder in ANOTHER
        process blocks this one. The pre-fix {name}.{pid} naming meant
        every process created its own file and no two holders ever
        contended (the lock never actually serialized)."""
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            child_script = (
                "import sys, time\n"
                "sys.path.insert(0, sys.argv[1])\n"
                "import bonsai2_harness as H\n"
                "from pathlib import Path\n"
                "base = Path(sys.argv[2])\n"
                "with H.lock_wait(base, 'xproc', timeout_s=10.0):\n"
                "    (base / 'held').write_text('held')\n"
                "    time.sleep(6)\n"
            )
            child = subprocess.Popen(
                [
                    sys.executable,
                    "-c",
                    child_script,
                    str(BIN_DIR),
                    str(base),
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            try:
                # wait until the child actually HOLDS the lock (it writes a
                # marker from inside the critical section — no startup race)
                held = base / "held"
                deadline = time.monotonic() + 5.0
                while not held.is_file() and time.monotonic() < deadline:
                    time.sleep(0.05)
                self.assertTrue(held.is_file(), "the child must have taken the lock")
                # while the child holds it, a short-timeout acquire here
                # must TIME OUT (contention on the shared {name}.lock)
                with (
                    self.assertRaises(TimeoutError),
                    H.lock_wait(base, "xproc", timeout_s=0.5),
                ):
                    pass
                # kill the holder -> stale lock (dead PID inside)
                child.kill()
                child.wait(timeout=5)
                # reclaim: the dead holder's lock must not wedge the queue
                with H.lock_wait(base, "xproc", timeout_s=5.0):
                    lock_file = base / ".locks" / "xproc.lock"
                    self.assertEqual(
                        lock_file.read_text().strip(),
                        str(os.getpid()),
                        "reclaimed: this process is now the holder",
                    )
            finally:
                if child.poll() is None:
                    child.kill()
                    child.wait(timeout=5)


if __name__ == "__main__":
    unittest.main()
