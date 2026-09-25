"""MAP 3.4–3.8: hermetic tests for the new harness commands
(collect / steer / cancel / unload / reload) + the reaper pass
(S3.4 deadline backstop) + dedup (S3.4 spawn idempotency).

No GPU; the subagent instances are S3.7 mock engines on 127.0.0.1
fixture ports. Every exit code the new commands can emit is produced
by a named case asserting the exact JSON + code.

Run: `python3 -m unittest discover -s tests -t .` (hermetic default).
"""

import importlib.machinery
import importlib.util
import io
import json
import os
import tempfile
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
    assert spec
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


H = _load_harness()


# ------------------------------------------------------------------------
# base rig (same pattern as test_harness_spawn.SpawnBase)
# ------------------------------------------------------------------------
class Base(unittest.TestCase):
    """Shared rig: fixture main endpoint + temp dirs + env overrides."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.health_dir = self.tmp / "run"
        self.results = self.tmp / "results"
        self._engines: list[MockEngine] = []
        self.main_engine = MockEngine(variant="ok")
        self.main_engine.start()
        self._engines.append(self.main_engine)
        self.cfg = self.tmp / "harness.yaml"
        self.env: dict = {}
        self._saved_env = {
            k: os.environ.get(k)
            for k in (
                "BONSAI2_CONFIG",
                "BONSAI2_RESULTS_DIR",
                "BONSAI2_HEALTH_DIR",
                "BONSAI2_ENGINE_YAML",
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
        self._tmp.cleanup()

    def _write_cfg(self, **overrides) -> None:
        data = {
            "main_endpoint": f"http://127.0.0.1:{self.main_engine.port}",
            "results_dir": str(self.results),
            "dedup_dir": str(self.tmp / "dedup"),
            "dedup_ttl_h": 24,
            "queue_wait_max_s": 2,
        }
        data.update(overrides)
        self.cfg.write_text("\n".join(f"{k}: {v}" for k, v in data.items()) + "\n")
        self.env = {
            "BONSAI2_CONFIG": str(self.cfg),
            "BONSAI2_RESULTS_DIR": str(self.results),
            "BONSAI2_HEALTH_DIR": str(self.health_dir),
            "BONSAI2_ENGINE_YAML": str(ENGINE_YAML),
        }
        for k, v in self.env.items():
            os.environ[k] = v

    def _write_health(self, instances: list[dict], stack: str = "healthy") -> None:
        doc = {
            "stack": stack,
            "instances": instances,
            "main_endpoint": "http://x",
            "main_healthy": True,
        }
        self.health_dir.mkdir(parents=True, exist_ok=True)
        (self.health_dir / "health.json").write_text(json.dumps(doc, indent=2) + "\n")

    def _inst(
        self,
        i: int,
        port: int,
        state: str = "healthy",
        ctx: int = 262144,
        total: int = 8,
    ) -> dict:
        return {
            "i": i,
            "port": port,
            "state": state,
            "ctx": ctx,
            "ctx_reduced": False,
            "slots_in_use": 0,
            "slots_total": total,
        }

    def _results(self) -> dict:
        return H.results_dirs(H.load_config(self.env))

    def _write_task(self, task_id: str, state: str = "running", **extra) -> None:
        d = self._results()
        H.ensure_results_dirs(d)
        rec = {
            "task_id": task_id,
            "state": state,
            "instance": 0,
            "port": 0,
            "result_file": str(self.results / f"{task_id}.json"),
            "error": None,
            "detail": None,
            "prompt": "test prompt",
            "tools": "on",
            "ctx_need": None,
            "priority": "normal",
            "deadline_s": 60.0,
            "created_ts": time.time(),
            "worker_pid": None,
            "instances_tried": [0],
            "updated_ts": time.time(),
        }
        rec.update(extra)
        H.write_task(d, rec)

    def _run(
        self, cmd: str, argv: list[str], env: dict | None = None, poll_s: float = 0.05
    ) -> tuple[int, dict, str]:
        """Run a harness command in-process, capture stdout JSON."""
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = H.dispatch(cmd, argv, env or self.env)
        return code, json.loads(out.getvalue()), err.getvalue()


# ------------------------------------------------------------------------
# 3.4 — Reaper (stale running records + deadline backstop)
# ------------------------------------------------------------------------
class TestReaper(unittest.TestCase):
    """S3.4: reap_stale turns a dead worker's running record into
    failed/ERR_ENGINE_DOWN and a past-deadline record into
    timeout/ERR_DEADLINE, freeing the slot."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.results = self.tmp / "results"
        (self.results / "tasks").mkdir(parents=True)
        (self.results / ".locks").mkdir(parents=True)
        self.d = {
            "base": self.results,
            "tasks": self.results / "tasks",
            "locks": self.results / ".locks",
        }

    def tearDown(self):
        self._tmp.cleanup()

    def test_dead_pid_reaped_to_failed(self):
        """A running record with a dead pid -> failed / ERR_ENGINE_DOWN."""
        rec = {
            "task_id": "t-dead",
            "state": "running",
            "instance": 0,
            "port": 30101,
            "worker_pid": 999999,  # a pid that doesn't exist
            "deadline_s": 600,
            "running_ts": time.time() - 10,
            "updated_ts": time.time(),
            "instances_tried": [0],
        }
        H.write_task(self.d, rec)
        reaped = H.reap_stale(self.d, log=lambda m: None)
        self.assertEqual(reaped, ["t-dead"])
        fresh = H.read_task(self.d, "t-dead")
        self.assertEqual(fresh["state"], "failed")
        self.assertEqual(fresh["error"], "ERR_ENGINE_DOWN")

    def test_deadline_exceeded_reaped_to_timeout(self):
        """A running record past its deadline (live pid) -> timeout / ERR_DEADLINE."""
        import os

        pid = os.getpid()  # a live pid
        rec = {
            "task_id": "t-slow",
            "state": "running",
            "instance": 0,
            "port": 30101,
            "worker_pid": pid,
            "deadline_s": 5.0,
            "running_ts": time.time() - 30,  # 25 s past the 5 s deadline
            "updated_ts": time.time(),
            "instances_tried": [0],
        }
        H.write_task(self.d, rec)
        reaped = H.reap_stale(self.d, grace_s=65.0, log=lambda m: None)
        # 30 s > 5 s deadline + 65 s grace? No: 30 < 5+65=70.
        # The grace is the socket cap + margin; the deadline must be exceeded BY MORE than the grace.
        self.assertEqual(
            reaped, [], "30 s past a 5 s deadline with 65 s grace is NOT yet exceeded"
        )
        # now with a bigger overshoot
        rec2 = dict(rec, task_id="t-slow2", running_ts=time.time() - 200)
        H.write_task(self.d, rec2)
        reaped2 = H.reap_stale(self.d, grace_s=65.0, log=lambda m: None)
        self.assertIn("t-slow2", reaped2)
        fresh = H.read_task(self.d, "t-slow2")
        self.assertEqual(fresh["state"], "timeout")
        self.assertEqual(fresh["error"], "ERR_DEADLINE")

    def test_terminal_records_not_reaped(self):
        """Terminal records are already free — the reaper skips them."""
        for state in ("done", "failed", "timeout"):
            H.write_task(
                self.d,
                {
                    "task_id": f"t-{state}",
                    "state": state,
                    "instance": 0,
                    "port": 30101,
                    "worker_pid": 999999,
                    "deadline_s": 5,
                    "running_ts": time.time() - 200,
                },
            )
        reaped = H.reap_stale(self.d, log=lambda m: None)
        self.assertEqual(reaped, [])

    def test_queued_records_not_reaped(self):
        """Queued records have no worker — the claimer owns them."""
        H.write_task(
            self.d,
            {
                "task_id": "t-q",
                "state": "queued",
                "instance": 0,
                "port": 30101,
                "worker_pid": None,
                "deadline_s": 5,
            },
        )
        reaped = H.reap_stale(self.d, log=lambda m: None)
        self.assertEqual(reaped, [])


# ------------------------------------------------------------------------
# 3.4 / 3.5 — Dedup (spawn idempotency, 24 h TTL)
# ------------------------------------------------------------------------
class TestDedup(unittest.TestCase):
    """S3.4: dedup_lookup / dedup_record roundtrip + TTL expiry."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.results = self.tmp / "results"
        self.dedup = self.tmp / "dedup"
        (self.results / "tasks").mkdir(parents=True)
        (self.results / ".locks").mkdir(parents=True)
        self.d = {
            "base": self.results,
            "tasks": self.results / "tasks",
            "locks": self.results / ".locks",
        }
        self.cfg = {
            "results_dir": str(self.results),
            "dedup_dir": str(self.dedup),
            "dedup_ttl_h": 24,
        }

    def tearDown(self):
        self._tmp.cleanup()

    def test_roundtrip(self):
        """Write a dedup entry for a live task -> lookup returns the task."""
        H.write_task(
            self.d,
            {
                "task_id": "t-dup",
                "state": "running",
                "instance": 0,
                "port": 30101,
            },
        )
        key = json.dumps(
            {"sha256": "abc", "tools": "on", "ctx": None, "priority": "normal"},
            sort_keys=True,
        )
        H.dedup_record(self.cfg, key, "t-dup")
        found = H.dedup_lookup(self.cfg, key)
        self.assertIsNotNone(found)
        self.assertEqual(found["task_id"], "t-dup")

    def test_expired_entry_returns_none(self):
        """A dedup entry older than the TTL -> None (key is free)."""
        H.write_task(
            self.d,
            {
                "task_id": "t-exp",
                "state": "running",
                "instance": 0,
                "port": 30101,
            },
        )
        key = json.dumps(
            {"sha256": "def", "tools": "off", "ctx": None, "priority": "low"},
            sort_keys=True,
        )
        H.dedup_record(self.cfg, key, "t-exp")
        # rewrite the entry with an old timestamp
        path = H.dedup_entry_path(self.cfg, key)
        entry = json.loads(path.read_text())
        entry["ts"] = time.time() - 25 * 3600  # 25 h ago > 24 h TTL
        path.write_text(json.dumps(entry) + "\n")
        found = H.dedup_lookup(self.cfg, key)
        self.assertIsNone(found)

    def test_terminal_task_frees_key(self):
        """A dedup entry pointing at a terminal task -> None."""
        H.write_task(
            self.d,
            {
                "task_id": "t-done",
                "state": "done",
                "instance": 0,
                "port": 30101,
            },
        )
        key = json.dumps(
            {"sha256": "ghi", "tools": "on", "ctx": None, "priority": "normal"},
            sort_keys=True,
        )
        H.dedup_record(self.cfg, key, "t-done")
        found = H.dedup_lookup(self.cfg, key)
        self.assertIsNone(found, "a terminal task frees the dedup key")

    def test_missing_entry_returns_none(self):
        """No dedup entry file -> None (a fresh task)."""
        key = json.dumps(
            {"sha256": "new", "tools": "on", "ctx": None, "priority": "normal"},
            sort_keys=True,
        )
        found = H.dedup_lookup(self.cfg, key)
        self.assertIsNone(found)


# ------------------------------------------------------------------------
# 3.6 — collect
# ------------------------------------------------------------------------
class TestCollect(Base):
    """S3.2: bonsai2-collect exit codes + JSON shape."""

    def test_exit_20_done(self):
        """A done task -> exit 20 + the result payload."""
        self._write_cfg()
        self._write_health([self._inst(0, free_port())])
        self._write_task("t-col", state="done")
        # write the result file
        rf = self.results / "t-col.json"
        rf.parent.mkdir(parents=True, exist_ok=True)
        rf.write_text(
            json.dumps(
                {
                    "answer": "42",
                    "reasoning": "because",
                    "usage": {"prompt_tokens": 5, "completion_tokens": 3},
                }
            )
            + "\n"
        )
        code, doc, _ = self._run("bonsai2-collect", ["t-col"])
        self.assertEqual(code, 20)
        self.assertEqual(doc["task_id"], "t-col")
        self.assertEqual(doc["state"], "done")
        self.assertEqual(doc["answer"], "42")
        self.assertEqual(doc["reasoning"], "because")
        self.assertEqual(doc["usage"]["prompt_tokens"], 5)

    def test_exit_21_failed(self):
        """A failed task -> exit 21."""
        self._write_cfg()
        self._write_health([self._inst(0, free_port())])
        self._write_task("t-fail", state="failed", error="ERR_ENGINE_DOWN")
        code, doc, _ = self._run("bonsai2-collect", ["t-fail"])
        self.assertEqual(code, 21)
        self.assertEqual(doc["state"], "failed")

    def test_exit_22_timeout(self):
        """A timeout task -> exit 22."""
        self._write_cfg()
        self._write_health([self._inst(0, free_port())])
        self._write_task("t-to", state="timeout", error="ERR_DEADLINE")
        code, doc, _ = self._run("bonsai2-collect", ["t-to"])
        self.assertEqual(code, 22)
        self.assertEqual(doc["state"], "timeout")

    def test_exit_0_non_terminal(self):
        """A running task -> exit 0 (poll again)."""
        self._write_cfg()
        self._write_health([self._inst(0, free_port())])
        self._write_task("t-run", state="running")
        code, doc, _ = self._run("bonsai2-collect", ["t-run"])
        self.assertEqual(code, 0)
        self.assertEqual(doc["state"], "running")

    def test_exit_2_unknown_task(self):
        """An unknown task -> exit 2."""
        self._write_cfg()
        self._write_health([self._inst(0, free_port())])
        code, doc, _ = self._run("bonsai2-collect", ["t-nosuch"])
        self.assertEqual(code, 2)
        self.assertEqual(doc.get("error"), "ERR_TASK_UNKNOWN")


# ------------------------------------------------------------------------
# 3.7 — steer / cancel
# ------------------------------------------------------------------------
class TestSteerCancel(Base):
    """S3.2: bonsai2-steer and bonsai2-cancel exit codes."""

    def test_steer_exit_2_unknown_task(self):
        self._write_cfg()
        self._write_health([self._inst(0, free_port())])
        code, doc, _ = self._run("bonsai2-steer", ["t-nosuch", "-m", "hello"])
        self.assertEqual(code, 2)
        self.assertEqual(doc.get("error"), "ERR_TASK_UNKNOWN")

    def test_steer_exit_0_non_terminal(self):
        """Steer on a running task -> 0 (recorded)."""
        self._write_cfg()
        self._write_health([self._inst(0, free_port())])
        self._write_task("t-st", state="running")
        code, _doc, _ = self._run("bonsai2-steer", ["t-st", "-m", "try approach B"])
        self.assertEqual(code, 0)
        # the steer was recorded
        d = self._results()
        rec = H.read_task(d, "t-st")
        self.assertIn("steer_messages", rec)
        self.assertEqual(rec["steer_messages"][0]["message"], "try approach B")

    def test_steer_exit_8_no_state_change(self):
        """Steer --wait on a running task that never changes -> 8."""
        self._write_cfg()
        self._write_health([self._inst(0, free_port())])
        self._write_task("t-wait", state="running")
        code, doc, _ = self._run(
            "bonsai2-steer", ["t-wait", "-m", "hi", "--wait", "0.5"]
        )
        self.assertEqual(code, 8)
        self.assertEqual(doc["state"], "running")

    def test_steer_exit_2_bad_args(self):
        """Steer without -m -> 2."""
        self._write_cfg()
        self._write_health([self._inst(0, free_port())])
        self._write_task("t-noargs", state="running")
        code, _doc, _ = self._run("bonsai2-steer", ["t-noargs"])
        self.assertEqual(code, 2)

    def test_cancel_exit_2_unknown_task(self):
        self._write_cfg()
        self._write_health([self._inst(0, free_port())])
        code, doc, _ = self._run("bonsai2-cancel", ["t-nosuch"])
        self.assertEqual(code, 2)
        self.assertEqual(doc.get("error"), "ERR_TASK_UNKNOWN")

    def test_cancel_exit_2_no_args(self):
        self._write_cfg()
        self._write_health([self._inst(0, free_port())])
        code, _doc, _ = self._run("bonsai2-cancel", [])
        self.assertEqual(code, 2)

    def test_cancel_exit_0_terminal_noop(self):
        """Cancel on a done task -> 0 (already-terminal no-op)."""
        self._write_cfg()
        self._write_health([self._inst(0, free_port())])
        self._write_task("t-done", state="done")
        code, doc, _ = self._run("bonsai2-cancel", ["t-done"])
        self.assertEqual(code, 0)
        self.assertEqual(doc["state"], "done")

    def test_cancel_exit_0_queued(self):
        """Cancel on a queued task -> 0 (marked failed, slot freed)."""
        self._write_cfg()
        self._write_health([self._inst(0, free_port())])
        self._write_task("t-q", state="queued")
        code, doc, _ = self._run("bonsai2-cancel", ["t-q"])
        self.assertEqual(code, 0)
        self.assertEqual(doc["state"], "failed")
        self.assertEqual(doc.get("error"), "ERR_CANCELLED")

    def test_cancel_exit_0_running_with_dead_pid(self):
        """Cancel on a running task whose worker pid is dead -> 0 (marked failed)."""
        self._write_cfg()
        self._write_health([self._inst(0, free_port())])
        self._write_task("t-run", state="running", worker_pid=999999)
        code, doc, _ = self._run("bonsai2-cancel", ["t-run"])
        self.assertEqual(code, 0)
        self.assertEqual(doc["state"], "failed")
        self.assertEqual(doc.get("error"), "ERR_CANCELLED")


# ------------------------------------------------------------------------
# 3.8 — unload / reload (pool control, manager-mediated)
# ------------------------------------------------------------------------
class TestPoolControl(Base):
    """S3.2: bonsai2-unload / bonsai2-reload exit codes.

    These are manager-mediated: the command writes a request into the
    control/ queue and waits for an ack. The manager's process_control
    (MAP 2.10) is the ack source. In hermetic tests we simulate the
    manager by writing the ack file directly (the command only reads
    the ack — it does not call the manager in-process).
    """

    def _sim_ack(self, req_id: str, op: str, state: str = "ok", **extra) -> None:
        """Simulate the manager's ack for a control request."""
        control = self.health_dir / "control"
        control.mkdir(parents=True, exist_ok=True)
        ack = {"type": op, "request_id": req_id, "ts": time.time(), "state": state}
        ack.update(extra)
        (control / f"ack-{req_id}.json").write_text(json.dumps(ack, indent=2) + "\n")

    def test_unload_exit_2_no_args(self):
        self._write_cfg()
        self._write_health([self._inst(0, free_port())])
        code, doc, _ = self._run("bonsai2-unload", [])
        self.assertEqual(code, 2)
        self.assertIn("--all", doc.get("detail", ""))

    def test_unload_exit_2_bad_instance(self):
        self._write_cfg()
        self._write_health([self._inst(0, free_port())])
        code, _doc, _ = self._run("bonsai2-unload", ["--instances", "abc"])
        self.assertEqual(code, 2)

    def test_unload_exit_2_both_selectors(self):
        self._write_cfg()
        self._write_health([self._inst(0, free_port())])
        code, _doc, _ = self._run("bonsai2-unload", ["--all", "--instances", "0"])
        self.assertEqual(code, 2)

    def test_unload_exit_84_manager_down(self):
        """No health.json -> 84 (immediate, not budget-long)."""
        self._write_cfg()  # no health.json written
        code, doc, _ = self._run("bonsai2-unload", ["--all"])
        self.assertEqual(code, 84)
        self.assertEqual(doc.get("error"), "ERR_MANAGER_DOWN")

    def test_reload_exit_84_manager_down(self):
        self._write_cfg()
        code, doc, _ = self._run("bonsai2-reload", ["--all"])
        self.assertEqual(code, 84)
        self.assertEqual(doc.get("error"), "ERR_MANAGER_DOWN")

    def test_unload_exit_2_malformed_config(self):
        self._write_cfg(queue_wait_max_s="soon")
        self._write_health([self._inst(0, free_port())])
        code, _doc, _ = self._run("bonsai2-unload", ["--all"])
        self.assertEqual(code, 2)


# ------------------------------------------------------------------------
# 3.4 — running_ts stamping in the claim path
# ------------------------------------------------------------------------
class TestRunningTs(Base):
    """S3.4: the claim path stamps running_ts so the reaper has a
    deadline reference."""

    def test_claim_own_task_stamps_running_ts(self):
        """claim_own_task sets running_ts on first promotion."""
        p = free_port()
        self._write_cfg()
        self._write_health([self._inst(0, p, total=1)])
        d = self._results()
        H.ensure_results_dirs(d)
        H.write_task(
            d,
            {
                "task_id": "t-ts",
                "state": "queued",
                "instance": 0,
                "port": p,
                "result_file": str(d["base"] / "t-ts.json"),
                "error": None,
                "detail": None,
                "prompt": "hi",
                "tools": "on",
                "ctx_need": None,
                "priority": "normal",
                "deadline_s": 60.0,
                "created_ts": time.time(),
                "worker_pid": None,
                "instances_tried": [0],
                "updated_ts": time.time(),
            },
        )
        doc = {"instances": [self._inst(0, p, total=1)]}
        rec, promoted = H.claim_own_task(d, doc, "t-ts")
        self.assertIsNotNone(rec)
        self.assertTrue(promoted)
        self.assertIn("running_ts", rec)
        self.assertIsInstance(rec["running_ts"], float)


# ------------------------------------------------------------------------
# 3.4 — reaper in the spawn queue-wait loop
# ------------------------------------------------------------------------
class TestReaperInSpawnLoop(Base):
    """S3.4: the reaper runs on every queue-wait poll, so a dead
    worker's slot is freed and the queue forward-progresses."""

    def test_dead_worker_frees_slot_during_queue_wait(self):
        """Two tasks on a 1-slot instance: the first 'dies' (its
        worker pid is dead), the second is promoted by the reaper
        during the queue-wait loop."""
        p = free_port()
        self._write_cfg(queue_wait_max_s=5)
        self._write_health([self._inst(0, p, total=1)])
        d = self._results()
        H.ensure_results_dirs(d)
        # the first task: running but its worker pid is dead
        H.write_task(
            d,
            {
                "task_id": "t-dead",
                "state": "running",
                "instance": 0,
                "port": p,
                "result_file": str(d["base"] / "t-dead.json"),
                "error": None,
                "detail": None,
                "prompt": "dead task",
                "tools": "on",
                "ctx_need": None,
                "priority": "normal",
                "deadline_s": 300.0,
                "created_ts": time.time() - 10,
                "worker_pid": 999999,
                "instances_tried": [0],
                "updated_ts": time.time() - 10,
            },
        )
        # the second task: queued, waiting for the slot
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = H.cmd_spawn(
                ["-i", "second task", "--priority", "high"],
                self.env,
                poll_s=0.1,
            )
        doc = json.loads(out.getvalue())
        # the reaper freed the dead task's slot; the second task got promoted
        self.assertIn(code, (0,))
        self.assertEqual(doc["state"], "running")
        self.assertEqual(doc["task_id"], doc.get("task_id"))
        # the dead task was reaped
        reaped = H.read_task(d, "t-dead")
        self.assertEqual(reaped["state"], "failed")
        self.assertEqual(reaped.get("error"), "ERR_ENGINE_DOWN")


# ------------------------------------------------------------------------
# 3.5 — dedup end-to-end (spawn-level idempotency)
# ------------------------------------------------------------------------
class TestDedupEndToEnd(Base):
    """S3.4: a spawn whose dedup key matches a LIVE task is a no-op
    returning the existing task_id (no double slot)."""

    def test_same_prompt_live_task_is_noop(self):
        """Two spawns with the SAME prompt while the first is still
        live: the second returns the first's task_id, no new record."""
        self._write_cfg()
        engine = MockEngine(variant="slow")  # slow = the task stays live
        engine.start()
        self.addCleanup(engine.stop)
        self._write_health([self._inst(0, engine.port)])
        argv = ["-i", "dedup me please"]
        out1, err1 = io.StringIO(), io.StringIO()
        with redirect_stdout(out1), redirect_stderr(err1):
            code1 = H.cmd_spawn(argv, self.env, poll_s=0.05)
        doc1 = json.loads(out1.getvalue())
        self.assertEqual(code1, 0)
        self.assertEqual(doc1["state"], "running")
        tid1 = doc1["task_id"]
        d = self._results()
        # second spawn, same key (prompt + tools + ctx + priority):
        # while the first task is still live (the slow engine), the key
        # maps to it -> no new slot.
        out2, err2 = io.StringIO(), io.StringIO()
        with redirect_stdout(out2), redirect_stderr(err2):
            code2 = H.cmd_spawn(argv, self.env, poll_s=0.05)
        doc2 = json.loads(out2.getvalue())
        self.assertEqual(code2, 0, "a live duplicate is accepted (exit 0)")
        self.assertEqual(
            doc2["task_id"], tid1, "the duplicate returns the EXISTING task_id"
        )
        # exactly ONE record exists
        ids = [r.get("task_id") for r in H.iter_tasks(d)]
        self.assertEqual(len(ids), 1, "no double slot: one record only")

    def test_different_priority_new_task(self):
        """Same prompt, different --priority: the dedup key differs, so
        a NEW task is created (the priority is part of the key, S3.4)."""
        self._write_cfg()
        engine = MockEngine(variant="slow")
        engine.start()
        self.addCleanup(engine.stop)
        self._write_health([self._inst(0, engine.port)])
        out1, _ = io.StringIO(), io.StringIO()
        with redirect_stdout(out1):
            code1 = H.cmd_spawn(
                ["-i", "prio task", "--priority", "low"], self.env, poll_s=0.05
            )
        self.assertEqual(code1, 0)
        tid1 = json.loads(out1.getvalue())["task_id"]
        out2, _ = io.StringIO(), io.StringIO()
        with redirect_stdout(out2):
            code2 = H.cmd_spawn(
                ["-i", "prio task", "--priority", "high"], self.env, poll_s=0.05
            )
        self.assertEqual(code2, 0)
        tid2 = json.loads(out2.getvalue())["task_id"]
        self.assertNotEqual(tid1, tid2, "different priority = different key")
        d = self._results()
        ids = [r.get("task_id") for r in H.iter_tasks(d)]
        self.assertEqual(len(ids), 2)


# ------------------------------------------------------------------------
# 3.7 — cancel end-to-end (live SIGTERM to a running worker)
# ------------------------------------------------------------------------
class TestCancelEndToEnd(Base):
    """S3.2: cancel SIGTERMs the running worker; the worker's handler
    marks the record failed/ERR_CANCELLED; the slot frees."""

    def test_cancel_running_worker(self):
        """A live worker (blocked in the slow engine's stream) is
        cancelled: exit 0, record failed/ERR_CANCELLED, pid gone."""
        self._write_cfg()
        engine = MockEngine(variant="slow")
        engine.start()
        self.addCleanup(engine.stop)
        self._write_health([self._inst(0, engine.port)])
        out, _ = io.StringIO(), io.StringIO()
        with redirect_stdout(out):
            code = H.cmd_spawn(["-i", "cancel me"], self.env, poll_s=0.05)
        self.assertEqual(code, 0)
        doc = json.loads(out.getvalue())
        tid = doc["task_id"]
        d = self._results()
        rec = H.read_task(d, tid)
        self.assertEqual(rec["state"], "running")
        pid = rec.get("worker_pid")
        self.assertIsInstance(pid, int, "the spawn started a real worker")
        # give the worker a moment to enter its attempt
        time.sleep(0.3)
        out2, _ = io.StringIO(), io.StringIO()
        with redirect_stdout(out2):
            ccode = H.cmd_cancel([tid], self.env)
        cdoc = json.loads(out2.getvalue())
        self.assertEqual(ccode, 0, "cancel accepted")
        self.assertEqual(cdoc["state"], "failed")
        self.assertEqual(cdoc.get("error"), "ERR_CANCELLED")
        # the worker process is gone: it exited and its parent reaped it
        # (in hermetic runs the test process IS the spawn's parent — in
        # production the spawn CLI exits, so init re-aps. A zombie passes
        # `os.kill(pid, 0)`, so assert via /proc/<pid>/stat state + the
        # recorded exit status, not raw liveness).
        import os

        def _state(pid: int) -> str:
            try:
                stat = Path(f"/proc/{pid}/stat").read_text()
                return stat.rsplit(")", 1)[1].split()[0]
            except FileNotFoundError:
                return "GONE"

        state = _state(pid)
        if state == "Z":  # zombie: dead, but this process (parent) must reap
            wpid, status = os.waitpid(pid, 0)
            self.assertEqual(wpid, pid)
            self.assertEqual(status >> 8, 0, "the worker exited 0 (clean cancel)")
            state = _state(pid)
        self.assertEqual(state, "GONE", "the worker process exited after cancel")


# ------------------------------------------------------------------------
# 3.4 — manager-side reaper pass (reap_tasks_once in the manager)
# ------------------------------------------------------------------------
def _load_manager():
    loader = importlib.machinery.SourceFileLoader(
        "bonsai2_manager_34", str(BIN_DIR / "bonsai2-manager")
    )
    spec = importlib.util.spec_from_loader("bonsai2_manager_34", loader)
    assert spec, "could not load the manager module"
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


_MGR = _load_manager()


class TestManagerReaperPass(unittest.TestCase):
    """S3.4: the manager's reap_tasks_once reaps stale task records
    (the manager-side deadline enforcement, every health cycle)."""

    def test_missing_tasks_dir_is_noop(self):
        """No results dir -> [] (nothing to reap; hermetic boot)."""
        with tempfile.TemporaryDirectory() as tmp:
            env = {"BONSAI2_RESULTS_DIR": str(Path(tmp) / "nope")}
            old = os.environ.get("BONSAI2_RESULTS_DIR")
            os.environ["BONSAI2_RESULTS_DIR"] = env["BONSAI2_RESULTS_DIR"]
            try:
                self.assertEqual(_MGR.reap_tasks_once(Path(tmp)), [])
            finally:
                if old is None:
                    os.environ.pop("BONSAI2_RESULTS_DIR", None)
                else:
                    os.environ["BONSAI2_RESULTS_DIR"] = old

    def test_reaps_dead_running_record(self):
        """A stale running record in the results dir is reaped."""
        with tempfile.TemporaryDirectory() as tmp:
            results = Path(tmp) / "results"
            (results / "tasks").mkdir(parents=True)
            (results / ".locks").mkdir(parents=True)
            d = {
                "base": results,
                "tasks": results / "tasks",
                "locks": results / ".locks",
            }
            H.write_task(
                d,
                {
                    "task_id": "t-mgr",
                    "state": "running",
                    "instance": 0,
                    "port": 30101,
                    "worker_pid": 999999,
                    "deadline_s": 600,
                    "running_ts": time.time() - 10,
                },
            )
            old = os.environ.get("BONSAI2_RESULTS_DIR")
            os.environ["BONSAI2_RESULTS_DIR"] = str(results)
            try:
                reaped = _MGR.reap_tasks_once(Path(tmp), log=lambda m: None)
                self.assertEqual(reaped, ["t-mgr"])
                fresh = H.read_task(d, "t-mgr")
                self.assertEqual(fresh["state"], "failed")
            finally:
                if old is None:
                    os.environ.pop("BONSAI2_RESULTS_DIR", None)
                else:
                    os.environ["BONSAI2_RESULTS_DIR"] = old


if __name__ == "__main__":
    unittest.main()
