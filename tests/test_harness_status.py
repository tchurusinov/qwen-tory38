"""MAP 3.2: hermetic tests for the status family (bonsai2-status,
bonsai2-list-instances, bonsai2-health) — the S3.2 per-command exit
mapping: 0 / 82 (main-endpoint probe fails) / 84 (health.json absent),
+ 2 on a malformed harness.yaml.

The commands run in-process via H.dispatch (the same code path the thin
deploy files use — argv[0] dispatch is covered by the subprocess smoke
tests below). The health document is a hand-written file under a temp
BONSAI2_HEALTH_DIR; the main endpoint is a fixture port (the S3.7 mock
engine, 127.0.0.1).

What each assertion proves (spec-named):
  * status: the S3.1 status schema VERBATIM — stack/instances (each with
    i/port/state/ctx/ctx_reduced/slots_in_use/slots_total)/
    main_endpoint/main_healthy; main_healthy is LIVE-probed (a healthy
    document + a dead probe port -> main_healthy false, exit 82, the
    document still emitted on stdout); main_only_reason present ONLY
    when instances: 0 (the S2.4 contract) with the document's reason
    value echoed; slots_in_use recomputed from task records (the
    manager's 0 is not trusted — the S3.1 note 'the harness owns live
    task slots').
  * health: the raw document pretty-printed, exit 0/84 only (no main
    probe: a dead main port must NOT make health exit 82).
  * list-instances: a human TABLE on stdout (columns, one row per
    instance, the main-only annotation when the set is empty), exit
    0/82/84 like status.
  * exit 2: malformed harness.yaml (the S3.2 "malformed config").
  * exit 84: absent health dir (S3.2 'a dead manager -> exit 84').
"""

import importlib.machinery
import importlib.util
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BIN_DIR = REPO_ROOT / "bin"

from .fixtures.mock_engine import MockEngine


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


def _run(dispatch_name: str, argv: list[str], env: dict) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = H.dispatch(dispatch_name, argv, env)
    return code, out.getvalue(), err.getvalue()


class StatusFamilyBase(unittest.TestCase):
    NAME = "bonsai2-status"

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.health_dir = self.tmp / "run"
        self.results = self.tmp / "results"
        self._engines = []
        # a fixture main endpoint that answers the S3.6 probe
        self.main_engine = MockEngine(variant="ok")
        self.main_engine.start()
        self._engines.append(self.main_engine)
        self.env = {
            "BONSAI2_CONFIG": str(self._write_cfg()),
            "BONSAI2_RESULTS_DIR": str(self.results),
            "BONSAI2_HEALTH_DIR": str(self.health_dir),
            "BONSAI2_ENGINE_YAML": str(REPO_ROOT / "etc" / "subagent-engine.yaml"),
        }

    def tearDown(self):
        for e in self._engines:
            e.stop()
        self._tmp.cleanup()

    def _write_cfg(self) -> Path:
        p = self.tmp / "harness.yaml"
        p.write_text(
            f"main_endpoint: http://127.0.0.1:{self.main_engine.port}\nresults_dir: {self.results}\n"
        )
        return p

    def _write_health(self, doc: dict) -> None:
        self.health_dir.mkdir(parents=True, exist_ok=True)
        (self.health_dir / "health.json").write_text(json.dumps(doc, indent=2) + "\n")

    def _inst(self, i, state="healthy", ctx=262144, total=8):
        return {
            "i": i,
            "port": 30101 + i,
            "state": state,
            "ctx": ctx,
            "ctx_reduced": False,
            "slots_in_use": 0,  # the manager's value; the harness recomputes
            "slots_total": total,
        }


class TestStatus(StatusFamilyBase):
    def test_status_schema_and_zero(self):
        self._write_health(
            {
                "stack": "healthy",
                "instances": [self._inst(0), self._inst(1)],
                "main_endpoint": "http://localhost:30001",
                "main_healthy": True,
            }
        )
        # one live task on i=1: slots_in_use must be recomputed (1), not
        # the manager's 0 (the S3.1 'the harness owns live task slots')
        H.ensure_results_dirs(H.results_dirs(H.load_config(self.env)))
        tid = H.new_task_id(H.results_dirs(H.load_config(self.env)))
        H.write_task(
            H.results_dirs(H.load_config(self.env)),
            {"task_id": tid, "state": "running", "instance": 1, "port": 30102},
        )
        code, out, _err = _run(self.NAME, [], self.env)
        self.assertEqual(code, 0, "main healthy + a valid document = exit 0")
        doc = json.loads(out)
        self.assertEqual(
            set(doc),
            {"stack", "instances", "main_endpoint", "main_healthy"},
            "S3.1 status schema, exact key set",
        )
        self.assertEqual(doc["stack"], "healthy")
        self.assertTrue(
            doc["main_healthy"], "live probe: the fixture main endpoint is up"
        )
        self.assertNotIn(
            "main_only_reason", doc, "instances: 2 -> no main_only_reason (S2.4)"
        )
        self.assertEqual(len(doc["instances"]), 2)
        row0, row1 = doc["instances"]
        self.assertEqual(row0["i"], 0)
        self.assertEqual(
            set(row0),
            {"i", "port", "state", "ctx", "ctx_reduced", "slots_in_use", "slots_total"},
            "S3.1 instance schema",
        )
        self.assertEqual(row0["slots_in_use"], 0)
        self.assertEqual(
            row1["slots_in_use"],
            1,
            "recomputed from the live task record (S3.4 whole-lifetime charge)",
        )
        self.assertEqual(row1["slots_total"], 8)

    def test_main_healthy_is_live_not_stale(self):
        self._write_health(
            {
                "stack": "healthy",
                "instances": [self._inst(0)],
                "main_endpoint": "http://x",
                "main_healthy": True,
            }
        )
        self.main_engine.stop()  # the document says healthy; the probe says down
        code, out, _err = _run(self.NAME, [], self.env)
        doc = json.loads(out)
        self.assertEqual(
            code, 82, "dead main endpoint -> exit 82 (S3.2 status mapping)"
        )
        self.assertFalse(
            doc["main_healthy"], "the LIVE probe value, not the document's stale True"
        )
        self.assertEqual(
            doc["stack"],
            "healthy",
            "the pool document is still the data — the document is emitted",
        )

    def test_main_only_reason_present_only_when_zero(self):
        self._write_health(
            {
                "stack": "main-only",
                "instances": [],
                "main_endpoint": "http://x",
                "main_healthy": True,
                "main_only_reason": "user_unloaded",
            }
        )
        code, out, _ = _run(self.NAME, [], self.env)
        doc = json.loads(out)
        self.assertEqual(code, 0)
        self.assertEqual(
            doc["main_only_reason"],
            "user_unloaded",
            "the S2.4 reason is echoed from the document",
        )
        self.assertEqual(doc["instances"], [])

    def test_exit_2_malformed_config(self):
        self.env["BONSAI2_CONFIG"] = str(self._write_bad_cfg())
        self._write_health(
            {
                "stack": "healthy",
                "instances": [self._inst(0)],
                "main_endpoint": "x",
                "main_healthy": True,
            }
        )
        code, out, _err = _run(self.NAME, [], self.env)
        self.assertEqual(
            code,
            2,
            "malformed harness.yaml -> exit 2 (S3.2 'malformed args / bad config')",
        )
        self.assertIn("ERR_ARGS", json.loads(out)["error"])

    def _write_bad_cfg(self) -> Path:
        p = self.tmp / "bad.yaml"
        p.write_text("queue_wait_max_s: abc\n")
        return p

    def test_exit_84_absent_health(self):
        code, out, _err = _run(self.NAME, [], self.env)  # health dir never written
        self.assertEqual(
            code, 84, "health.json absent -> exit 84 (S3.2 'a dead manager -> exit 84')"
        )
        self.assertEqual(json.loads(out)["error"], "ERR_MANAGER_DOWN")

    def test_no_arguments_only(self):
        self._write_health(
            {
                "stack": "healthy",
                "instances": [self._inst(0)],
                "main_endpoint": "x",
                "main_healthy": True,
            }
        )
        code, _out, _ = _run(self.NAME, ["--bogus"], self.env)
        self.assertEqual(code, 2, "status takes no arguments -> exit 2")


class TestHealth(StatusFamilyBase):
    NAME = "bonsai2-health"

    def test_raw_document_pretty(self):
        doc = {
            "stack": "degraded",
            "instances": [self._inst(0, state="degraded")],
            "main_endpoint": "http://x",
            "main_healthy": True,
        }
        self._write_health(doc)
        self.main_engine.stop()  # health must NOT probe: dead main still 0
        code, out, _ = _run(self.NAME, [], self.env)
        self.assertEqual(
            code, 0, "bonsai2-health dumps the raw document — no main probe (no 82)"
        )
        self.assertEqual(json.loads(out), doc, "the raw document, unmodified")
        self.assertIn('  "stack"', out, "pretty-printed (indent)")

    def test_exit_84(self):
        code, _out, _ = _run(self.NAME, [], self.env)
        self.assertEqual(code, 84)


class TestListInstances(StatusFamilyBase):
    NAME = "bonsai2-list-instances"

    def test_human_table(self):
        self._write_health(
            {
                "stack": "healthy",
                "instances": [self._inst(0, ctx=131072), self._inst(1)],
                "main_endpoint": "http://x",
                "main_healthy": True,
            }
        )
        H.ensure_results_dirs(H.results_dirs(H.load_config(self.env)))
        H.write_task(
            H.results_dirs(H.load_config(self.env)),
            {"task_id": "t-x", "state": "queued", "instance": 0, "port": 30101},
        )
        code, out, _ = _run(self.NAME, [], self.env)
        self.assertEqual(code, 0)
        lines = out.splitlines()
        self.assertEqual(len(lines), 3, "header + 2 instance rows")
        header = lines[0]
        for col in ("INSTANCE", "PORT", "STATE", "CTX", "CTX_REDUCED", "SLOTS"):
            self.assertIn(col, header)
        self.assertIn("30101", lines[1])
        self.assertIn("30102", lines[2])
        self.assertIn(
            "0/8",
            lines[1] + lines[2],
            "the recomputed slot column (0 on i=1, 1 on i=0)",
        )
        self.assertIn("1/8", lines[1] + lines[2])

    def test_main_only_annotation(self):
        self._write_health(
            {
                "stack": "main-only",
                "instances": [],
                "main_endpoint": "http://x",
                "main_healthy": True,
                "main_only_reason": "budget_zero",
            }
        )
        code, out, _ = _run(self.NAME, [], self.env)
        self.assertEqual(code, 0)
        self.assertIn("main-only", out)
        self.assertIn("budget_zero", out)

    def test_exit_82_and_84(self):
        self._write_health(
            {
                "stack": "healthy",
                "instances": [self._inst(0)],
                "main_endpoint": "http://x",
                "main_healthy": True,
            }
        )
        self.main_engine.stop()
        code, _out, _ = _run(self.NAME, [], self.env)
        self.assertEqual(code, 82, "list-instances carries the same 82 gate")
        self._write_health({})  # invalid: no stack
        code, _out, _ = _run(self.NAME, [], self.env)
        self.assertEqual(code, 84)


class TestDeployFilesSmoke(unittest.TestCase):
    """The thin deploy files: subprocess smoke (they exist, they run
    python, they dispatch through the shared core). BONSAI2_HEALTH_DIR
    points at a temp dir with a hand-written document; the main
    endpoint is a fixture port so the real 82/84/0 mapping is exercised
    end to end (the argv[0] dispatch the S3.2 surface relies on)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.health_dir = self.tmp / "run"
        self.results = self.tmp / "results"
        self.main_engine = MockEngine(variant="ok")
        self.main_engine.start()
        self.cfg = self.tmp / "harness.yaml"
        self.cfg.write_text(
            f"main_endpoint: http://127.0.0.1:{self.main_engine.port}\nresults_dir: {self.results}\n"
        )
        self.env = {
            **os.environ,
            "BONSAI2_CONFIG": str(self.cfg),
            "BONSAI2_RESULTS_DIR": str(self.results),
            "BONSAI2_HEALTH_DIR": str(self.health_dir),
            "BONSAI2_ENGINE_YAML": str(REPO_ROOT / "etc" / "subagent-engine.yaml"),
        }

    def tearDown(self):
        self.main_engine.stop()
        self._tmp.cleanup()

    def _write_health(self, doc: dict):
        self.health_dir.mkdir(parents=True, exist_ok=True)
        (self.health_dir / "health.json").write_text(json.dumps(doc) + "\n")

    def _run_bin(self, name: str, *argv: str):
        import subprocess

        return subprocess.run(
            [sys_executable(), str(BIN_DIR / name), *argv],
            capture_output=True,
            text=True,
            env=self.env,
            check=False,
        )

    def test_status_zero_json(self):
        self._write_health(
            {
                "stack": "healthy",
                "instances": [
                    {
                        "i": 0,
                        "port": 30101,
                        "state": "healthy",
                        "ctx": 262144,
                        "ctx_reduced": False,
                        "slots_in_use": 0,
                        "slots_total": 8,
                    }
                ],
                "main_endpoint": "http://x",
                "main_healthy": True,
            }
        )
        r = self._run_bin("bonsai2-status")
        self.assertEqual(r.returncode, 0, r.stderr)
        doc = json.loads(r.stdout)
        self.assertTrue(doc["main_healthy"])

    def test_status_84(self):
        r = self._run_bin("bonsai2-status")
        self.assertEqual(
            r.returncode, 84, "absent health.json -> 84 through the real deploy file"
        )
        self.assertEqual(json.loads(r.stdout)["error"], "ERR_MANAGER_DOWN")

    def test_health_0(self):
        self._write_health(
            {
                "stack": "healthy",
                "instances": [],
                "main_endpoint": "x",
                "main_healthy": True,
                "main_only_reason": "budget_zero",
            }
        )
        r = self._run_bin("bonsai2-health")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout)["main_only_reason"], "budget_zero")

    def test_spawn_malformed_args_exit_2(self):
        r = self._run_bin("bonsai2-spawn")
        self.assertEqual(
            r.returncode, 2, "no prompt source -> 2 (before any config/health read)"
        )
        payload = json.loads(r.stdout)
        self.assertEqual(payload["error"], "ERR_ARGS")


def sys_executable():
    import sys

    return sys.executable


if __name__ == "__main__":
    unittest.main()
