"""MAP 3.3: hermetic tests for bonsai2-spawn (the S3.2/S3.3 submission
flow) + the task worker's terminal states.

No GPU; the subagent instances are S3.7 mock engines on 127.0.0.1
fixture ports, the main endpoint is a fixture port too. Every exit
code THIS PR can emit is produced by a NAMED case (table-driven where
the shape allows) asserting the exact JSON + code:

  * 2  — malformed args (one case per bad-args shape, S3.2
         "malformed args / bad config").
  * 3  — routing candidate bfailed (ERR_PORT_IN_USE) and bdown
         (ERR_INSTANCE_UNHEALTHY) at submission (the S4.5 condition).
  * 5  — no instance ctx fits the requested --ctx (ERR_CTX_TOO_LONG,
         S1.5).
  * 8  — queue_wait_max_s expiry, state "queued" (the S3.2 manager-side
         wait budget; the task's own --deadline is NOT this).
  * 80 — main-only mode: the S2.4 {"reason": ...} JSON.
  * 82 — dead main endpoint (the S3.7 named case).
  * 84 — absent health dir (the S3.7 named case).
  * 0  — a free slot -> running; AND the S3.3 routing: least-loaded
         pick + tie-break lowest i (asserted on the JSON's instance
         field + the worker's request landing on that port) + priority
         preemption (a high task spawned while low tasks are queued is
         promoted first); AND the S1.5 engine-failure path through the
         real worker: 400 -> failed ERR_CTX_TOO_LONG; non-JSON body ->
         failed ERR_PARSE; 5xx -> engine_retried once on the other
         instance, second failure -> failed ERR_ENGINE_DOWN; slow
         engine past --deadline -> timeout.

Run: `python3 -m unittest discover -s tests -t .` (hermetic default).
"""

import importlib.machinery
import importlib.util
import io
import json
import os
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

# a short deadline the slow fixture exceeds (the S3.4 deadline-expiry
# case): the worker checks its deadline before AND after each attempt.
SLOW_DEADLINE_S = 0.5


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


def _load_taskworker(name: str = "bonsai2_taskworker_under_test"):
    """The worker as a separate module (the suite loads the harness as
    'bonsai2_harness'; the worker is its own top-level module that
    imports it — keep the name distinct so unittest discovery sees two
    real modules)."""
    loader = importlib.machinery.SourceFileLoader(
        name, str(BIN_DIR / "bonsai2-taskworker.py")
    )
    spec = importlib.util.spec_from_loader(name, loader)
    assert spec
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def _run_spawn(
    argv: list[str], env: dict, poll_s: float = 0.1
) -> tuple[int, dict, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = H.cmd_spawn(argv, env, poll_s=poll_s)
    return code, json.loads(out.getvalue()), err.getvalue()


def _wait_state(d: dict, task_id: str, states: tuple, timeout_s: float = 10.0) -> dict:
    deadline = time.monotonic() + timeout_s
    last = None
    while time.monotonic() < deadline:
        last = H.read_task(d, task_id)
        if last.get("state") in states:
            return last
        time.sleep(0.05)
    raise AssertionError(f"task {task_id} never reached {states}: {last}")


class SpawnBase(unittest.TestCase):
    """The shared rig: fixture main endpoint + fixture subagent
    instances on distinct ports + a hand-written health document
    pointing at those ports + a temp results dir + the S3.2 env
    overrides (BONSAI2_CONFIG / BONSAI2_RESULTS_DIR /
    BONSAI2_HEALTH_DIR / BONSAI2_ENGINE_YAML). The env is also merged
    into os.environ for the duration of the test: the detached task
    worker (a real subprocess) inherits os.environ, and its identity
    read (BONSAI2_ENGINE_YAML) must see the same fixture values the
    in-process loads do."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.health_dir = self.tmp / "run"
        self.results = self.tmp / "results"
        self._engines: list[MockEngine] = []
        self.main_engine = MockEngine(variant="ok")
        self.main_engine.start()
        self._engines.append(self.main_engine)
        self.engines: list[MockEngine] = []
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
            "queue_wait_max_s": 2,
            # MAP 3.15: per-task workdirs live under the rig's temp tree
            # (the S3.6 default /run/bonsai2/workspaces/ is root-owned on
            # this box — spawn's auto-workdir creation must not hit it).
            "workspace_dir": str(self.tmp / "workspaces"),
        }
        data.update(overrides)
        self.cfg.write_text("\n".join(f"{k}: {v}" for k, v in data.items()) + "\n")
        self.env = {
            "BONSAI2_CONFIG": str(self.cfg),
            "BONSAI2_RESULTS_DIR": str(self.results),
            "BONSAI2_HEALTH_DIR": str(self.health_dir),
            "BONSAI2_ENGINE_YAML": str(ENGINE_YAML),
        }
        for k, v in self.env.items():  # the worker subprocess sees the same
            os.environ[k] = v

    def _write_health(
        self,
        instances: list[dict],
        stack: str = "healthy",
        main_only_reason: str | None = None,
    ) -> None:
        doc: dict = {
            "stack": stack,
            "instances": instances,
            "main_endpoint": "http://x",
            "main_healthy": True,
        }
        if main_only_reason is not None:
            doc["main_only_reason"] = main_only_reason
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


class TestSpawnExitCodes(SpawnBase):
    """The S3.2 per-command exit mapping — one named case per code."""

    # --- 2: malformed args (the bad-args case, S3.7) ---
    def test_exit_2_no_prompt(self):
        self._write_cfg()
        self._write_health([self._inst(0, self._instance(0).port)])
        code, doc, _ = _run_spawn([], self.env)
        self.assertEqual(code, 2)
        self.assertEqual(doc["error"], "ERR_ARGS")

    def test_exit_2_unknown_flag(self):
        self._write_cfg()
        code, doc, _ = _run_spawn(["-i", "hi", "--bogus"], self.env)
        self.assertEqual(code, 2)
        self.assertIn("--bogus", doc["detail"])

    def test_exit_2_bad_priority(self):
        self._write_cfg()
        code, _doc, _ = _run_spawn(["-i", "hi", "--priority", "urgent"], self.env)
        self.assertEqual(code, 2)

    def test_exit_2_bad_ctx(self):
        self._write_cfg()
        code, _doc, _ = _run_spawn(["-i", "hi", "--ctx", "zero"], self.env)
        self.assertEqual(code, 2)

    def test_exit_2_both_prompt_sources(self):
        p = self.tmp / "prompt.txt"
        p.write_text("hi")
        self._write_cfg()
        code, doc, _ = _run_spawn(["-p", str(p), "-i", "inline"], self.env)
        self.assertEqual(code, 2)
        self.assertIn("exactly one", doc["detail"])

    def test_exit_2_missing_prompt_file(self):
        self._write_cfg()
        self._write_health([self._inst(0, self._instance(0).port)])
        code, doc, _ = _run_spawn(["-p", str(self.tmp / "nope.txt")], self.env)
        self.assertEqual(
            code, 2, "the prompt file is read at submission (the -p source)"
        )
        self.assertIn("nope.txt", doc["detail"])

    def test_exit_2_malformed_config(self):
        self._write_cfg(queue_wait_max_s="soon")
        self._write_health([self._inst(0, self._instance(0).port)])
        code, _doc, _ = _run_spawn(["-i", "hi"], self.env)
        self.assertEqual(code, 2, "malformed harness.yaml -> 2 (S3.2 'bad config')")

    # --- 84: absent health dir (the S3.7 named case) ---
    def test_exit_84_absent_health_dir(self):
        self._write_cfg()  # no health.json written
        code, doc, _ = _run_spawn(["-i", "hi"], self.env)
        self.assertEqual(
            code, 84, "health.json absent -> 84 (S3.2 'a dead manager -> exit 84')"
        )
        self.assertEqual(doc["error"], "ERR_MANAGER_DOWN")

    # --- 82: dead main endpoint (the S3.7 named case) ---
    def test_exit_82_dead_main_endpoint(self):
        p0 = free_port()
        self._write_cfg()  # main_endpoint = a refused port
        self._write_health([self._inst(0, self._instance(0).port)])
        # point the config at a dead main port
        self._write_cfg(main_endpoint=f"http://127.0.0.1:{p0}")
        code, doc, _ = _run_spawn(["-i", "hi"], self.env)
        self.assertEqual(code, 82, "dead main endpoint -> 82 (S3.2 spawn mapping)")
        self.assertEqual(doc["error"], "ERR_MAIN_DOWN")

    # --- 80: main-only mode (the S2.4 {"reason": ...} JSON) ---
    def test_exit_80_main_only(self):
        self._write_cfg()
        self._write_health([], stack="main-only", main_only_reason="user_unloaded")
        code, doc, _ = _run_spawn(["-i", "hi"], self.env)
        self.assertEqual(code, 80, "0 instances -> 80 (S3.2 'no subagent instances')")
        self.assertEqual(doc, {"reason": "user_unloaded"}, "the S2.4 JSON, exact shape")

    # --- 3: routing candidate bfailed / bdown (the S4.5 condition) ---
    def test_exit_3_bfailed_candidate(self):
        p0, p1 = free_port(), free_port()
        self._write_cfg()
        self._write_health(
            [self._inst(0, p0, state="bfailed"), self._inst(1, p1, total=1)]
        )
        # fill i=1 so the only routable instance is the bfailed i=0
        d = self._results()
        H.ensure_results_dirs(d)
        for k in range(1):
            H.write_task(
                d,
                {
                    "task_id": f"t-fill-{k}",
                    "state": "running",
                    "instance": 1,
                    "port": p1,
                },
            )
        code, doc, _ = _run_spawn(["-i", "hi"], self.env)
        self.assertEqual(
            code, 3, "bfailed candidate at submission -> 3 (S3.2 spawn mapping)"
        )
        self.assertEqual(doc["error"], "ERR_PORT_IN_USE")
        self.assertEqual(doc["instance"], 0)

    def test_exit_3_bdown_candidate(self):
        p0, p1 = free_port(), free_port()
        self._write_cfg()
        d = self._results()
        H.ensure_results_dirs(d)
        H.write_task(
            d, {"task_id": "t-fill", "state": "running", "instance": 1, "port": p1}
        )
        self._write_health(
            [self._inst(0, p0, state="bdown"), self._inst(1, p1, total=1)]
        )
        code, doc, _ = _run_spawn(["-i", "hi"], self.env)
        self.assertEqual(code, 3)
        self.assertEqual(
            doc["error"],
            "ERR_INSTANCE_UNHEALTHY",
            "bdown = the S1.5 named condition (S4.5: wait one health cycle)",
        )

    # --- 5: no instance ctx fits (S1.5 ERR_CTX_TOO_LONG) ---
    def test_exit_5_ctx_too_long(self):
        p0, p1 = free_port(), free_port()
        self._write_cfg()
        self._write_health([self._inst(0, p0, ctx=8192), self._inst(1, p1, ctx=16384)])
        code, doc, _ = _run_spawn(["-i", "hi", "--ctx", "262144"], self.env)
        self.assertEqual(code, 5, "S1.5: request > every instance's live ctx -> 5")
        self.assertEqual(doc["error"], "ERR_CTX_TOO_LONG")
        self.assertIn("16384", doc["detail"])

    # --- 8: queue_wait expiry (the S3.2 manager-side wait budget) ---
    def test_exit_8_queue_wait_expired(self):
        p0, p1 = free_port(), free_port()
        self._write_cfg(queue_wait_max_s=1)
        self._write_health([self._inst(0, p0, total=1), self._inst(1, p1, total=1)])
        d = self._results()
        H.ensure_results_dirs(d)
        H.write_task(
            d, {"task_id": "t-hog-0", "state": "running", "instance": 0, "port": p0}
        )
        H.write_task(
            d, {"task_id": "t-hog-1", "state": "running", "instance": 1, "port": p1}
        )
        t0 = time.monotonic()
        code, doc, _ = _run_spawn(["-i", "hi"], self.env, poll_s=0.05)
        self.assertEqual(
            code, 8, "queue_wait_max_s expiry -> 8 (S3.2: NOT the task's own deadline)"
        )
        self.assertEqual(doc["state"], "queued", "the S3.2 'state: queued' JSON")
        self.assertEqual(doc["instance"], 0)
        self.assertGreaterEqual(
            time.monotonic() - t0, 0.8, "it actually waited the queue_wait budget"
        )
        # the task stays queued (a later spawn may promote it — S3.3)
        rec = H.read_task(d, doc["task_id"])
        self.assertEqual(rec["state"], "queued")

    # --- 0: accepted (free slot) + least-loaded routing + tie-break ---
    def test_exit_0_and_least_loaded_routing(self):
        p0, p1 = free_port(), free_port()
        self._write_cfg()
        self._write_health([self._inst(0, p0), self._inst(1, p1)])
        d = self._results()
        H.ensure_results_dirs(d)
        for k in range(3):  # i=0 busy with 3, i=1 free
            H.write_task(
                d,
                {
                    "task_id": f"t-busy-{k}",
                    "state": "running",
                    "instance": 0,
                    "port": p0,
                },
            )
        code, doc, _ = _run_spawn(["-i", "hi"], self.env)
        self.assertEqual(
            code, 0, "a free slot -> 0 accepted (S3.2: '0 = accepted/queued')"
        )
        self.assertEqual(doc["instance"], 1, "S3.3 least-loaded: i=1, not i=0")
        self.assertEqual(doc["state"], "running")
        self.assertRegex(
            doc["task_id"], r"^t-\d{8}-\d{4}-[0-9a-f]{4}$", "the S3.1 task-id shape"
        )
        self.assertEqual(doc["port"], p1)
        self.assertEqual(doc["result_file"], str(d["base"] / f"{doc['task_id']}.json"))
        self.assertIsNone(doc["error"])

    def test_tie_break_lowest_i(self):
        p0, p1 = free_port(), free_port()
        self._write_cfg()
        self._write_health([self._inst(0, p0), self._inst(1, p1)])
        code, doc, _ = _run_spawn(["-i", "hi"], self.env)
        self.assertEqual(code, 0)
        self.assertEqual(doc["instance"], 0, "S3.3 tie-break: lowest i")

    def test_out_flag_selects_result_file(self):
        p0 = free_port()
        self._write_cfg()
        self._write_health([self._inst(0, p0)])
        out = self.tmp / "custom" / "result.json"
        code, doc, _ = _run_spawn(["-i", "hi", "--out", str(out)], self.env)
        self.assertEqual(code, 0)
        self.assertEqual(
            doc["result_file"],
            str(out),
            "--out selects where the result is written (S3.4: NOT part of the dedup key)",
        )

    def test_prompt_file_source(self):
        p0 = free_port()
        self._write_cfg()
        self._write_health([self._inst(0, p0)])
        p = self.tmp / "prompt.txt"
        p.write_text("file-sourced prompt")
        code, doc, _ = _run_spawn(["-p", str(p)], self.env)
        self.assertEqual(code, 0)
        rec = H.read_task(self._results(), doc["task_id"])
        self.assertEqual(rec["prompt"], "file-sourced prompt")


class TestSpawnWorkerRoundTrip(SpawnBase):
    """spawn -> the detached real worker -> terminal record + result
    file (the S3.2 collect shape, the S3.1 state set, the S1.5
    error names — all asserted on the task record)."""

    def _spawn_ok(
        self, *spawn_argv, variant="ok", ctx=262144, total=8, deadline=None
    ) -> tuple[int, dict, dict]:
        p0 = free_port()
        self._write_cfg()
        self._instance(p0, variant=variant)
        self._write_health([self._inst(0, p0, ctx=ctx, total=total)])
        argv = list(spawn_argv) if spawn_argv else ["-i", "hello task"]
        if deadline is not None:
            argv += ["--deadline", str(deadline)]
        code, doc, _ = _run_spawn(argv, self.env, poll_s=0.05)
        self.assertEqual(code, 0, "spawn must be accepted")
        d = self._results()
        H.ensure_results_dirs(d)
        return code, doc, d

    def test_done_with_result_file(self):
        _, doc, d = self._spawn_ok()
        rec = _wait_state(d, doc["task_id"], ("done", "failed"))
        self.assertEqual(rec["state"], "done", "the mock engine answered: done")
        self.assertIsNone(rec["error"])
        result = json.loads(Path(rec["result_file"]).read_text())
        self.assertEqual(result["answer"], "done")
        self.assertEqual(result["reasoning"], "ok ")
        self.assertEqual(
            result["usage"],
            {"prompt_tokens": 7, "completion_tokens": 3},
            "usage from the stream-final chunk",
        )

    def test_400_is_ctx_too_long(self):
        _, doc, d = self._spawn_ok(variant="400")
        rec = _wait_state(d, doc["task_id"], ("failed",))
        self.assertEqual(rec["state"], "failed")
        self.assertEqual(
            rec["error"], "ERR_CTX_TOO_LONG", "S1.5: engine HTTP 400 = request > ctx"
        )

    def test_nonjson_body_is_parse(self):
        _, doc, d = self._spawn_ok(variant="badjson")
        rec = _wait_state(d, doc["task_id"], ("failed",))
        self.assertEqual(rec["state"], "failed")
        self.assertEqual(
            rec["error"],
            "ERR_PARSE",
            "S3.7: malformed response (non-JSON body) -> ERR_PARSE",
        )

    def test_engine_down_retried_then_failed(self):
        p0, p1 = free_port(), free_port()
        self._write_cfg()
        self._instance(p0, variant="500")
        self._instance(p1, variant="ok")
        self._write_health([self._inst(0, p0), self._inst(1, p1)])
        code, doc, _ = _run_spawn(["-i", "hi"], self.env, poll_s=0.05)
        # the least-loaded tie-break picks i=0 (both empty): the bad one
        self.assertEqual(code, 0)
        self.assertEqual(doc["instance"], 0)
        d = self._results()
        H.ensure_results_dirs(d)
        rec = _wait_state(d, doc["task_id"], ("done", "failed"), timeout_s=15.0)
        self.assertEqual(
            rec["state"], "done", "the S1.5 one-retry: the SECOND instance completed it"
        )
        self.assertIn(1, rec.get("instances_tried", []), "the retry target is recorded")
        self.assertEqual(
            rec["instance"], 1, "the record's instance moved to the retry target"
        )
        self.assertIsNotNone(rec["worker_pid"])

    def test_engine_down_both_failed(self):
        p0, p1 = free_port(), free_port()
        self._write_cfg()
        self._instance(p0, variant="500")
        self._instance(p1, variant="429")
        self._write_health([self._inst(0, p0), self._inst(1, p1)])
        code, doc, _ = _run_spawn(["-i", "hi"], self.env, poll_s=0.05)
        self.assertEqual(code, 0)
        d = self._results()
        H.ensure_results_dirs(d)
        rec = _wait_state(d, doc["task_id"], ("failed",), timeout_s=20.0)
        self.assertEqual(rec["state"], "failed", "S1.5: second failure -> failed")
        self.assertEqual(rec["error"], "ERR_ENGINE_DOWN")
        self.assertEqual(sorted(rec.get("instances_tried", [])), [0, 1])
        self.assertIn(
            "retried once", rec.get("detail", ""), "the retry is named in the detail"
        )

    def test_deadline_expiry_is_timeout(self):
        _, doc, d = self._spawn_ok(variant="slow", deadline=SLOW_DEADLINE_S)
        rec = _wait_state(d, doc["task_id"], ("timeout", "done"), timeout_s=20.0)
        self.assertEqual(
            rec["state"],
            "timeout",
            "S3.4: the task's own --deadline expired mid-run -> timeout",
        )
        self.assertEqual(rec["error"], "ERR_DEADLINE")


class TestSpawnQueuePromotion(SpawnBase):
    """S3.3 queue + priority preemption, live through two spawns: a
    low-priority task holds the only slot, a high-priority spawn queues
    and gets the slot the moment the running task finishes — the
    PROMOTION happens on a later spawn's claim pass (the S3.3 queue
    protocol: forward progress without a manager loop)."""

    def test_high_priority_jumps_the_queue(self):
        p0 = free_port()
        self._write_cfg(queue_wait_max_s=3)
        self._instance(p0, variant="ok")
        self._write_health([self._inst(0, p0, total=1)])
        d = self._results()
        H.ensure_results_dirs(d)
        # a task occupies the ONLY slot — written directly, no worker:
        # the reaper thread marks it done mid-wait, freeing the slot.
        hog = H.new_task_id(d)
        H.write_task(
            d,
            {
                "task_id": hog,
                "state": "running",
                "instance": 0,
                "port": p0,
                "created_ts": time.time(),
                "priority": "normal",
            },
        )
        # two queued tasks: low (older) then high (newer but preempts)
        low = H.new_task_id(d)
        H.write_task(
            d,
            {
                "task_id": low,
                "state": "queued",
                "instance": 0,
                "port": p0,
                "created_ts": time.time() - 10,
                "priority": "low",
                "prompt": "low",
                "result_file": str(self.results / (low + ".json")),
            },
        )
        high = H.new_task_id(d)
        H.write_task(
            d,
            {
                "task_id": high,
                "state": "queued",
                "instance": 0,
                "port": p0,
                "created_ts": time.time() - 5,
                "priority": "high",
                "prompt": "high",
                "result_file": str(self.results / (high + ".json")),
            },
        )

        def finish_hog():
            time.sleep(0.5)
            try:
                rec = H.read_task(d, hog)
                if rec.get("state") == "running":
                    rec["state"] = "done"
                    H.write_task(d, rec)
            except H.TaskNotFound:
                pass

        reaper = threading.Thread(target=finish_hog, daemon=True)
        reaper.start()
        # When the hog finishes, the claim pass must promote the HIGH
        # task (priority preemption, S3.3), not the older low one. The
        # slot then frees again and this spawn's normal task may run
        # before the 3 s budget — so code 0 or 8 are both valid; the
        # preemption is asserted on the records.
        code, doc, _ = _run_spawn(["-i", "third"], self.env, poll_s=0.05)
        reaper.join(timeout=5)
        self.assertIn(code, (0, 8), "accepted, or queued-wait-expired")
        self.assertEqual(doc["state"], "running" if code == 0 else "queued")
        high_rec = H.read_task(d, high)
        self.assertIn(
            high_rec["state"],
            ("running", "done"),
            "the high-priority task was promoted into the freed slot (S3.3 'high jumps the queue')",
        )
        self.assertEqual(
            H.read_task(d, low)["state"],
            "queued",
            "the low task is still behind it",
        )

    def test_queued_task_promoted_by_later_spawn(self):
        p0 = free_port()
        self._write_cfg(queue_wait_max_s=1)
        # the instance is SLOW (~1.35 s per completion): a promoted task
        # HOLDS the slot past the spawn's 1 s queue_wait budget, so the
        # promotion order is observable — the hog's slot goes to the
        # OLDER queued task, not to the spawn that is waiting on it.
        self._instance(p0, variant="slow")
        self._write_health([self._inst(0, p0, total=1)])
        d = self._results()
        H.ensure_results_dirs(d)
        H.write_task(
            d, {"task_id": "t-hog", "state": "running", "instance": 0, "port": p0}
        )
        # a pre-existing queued task, OLDER than the spawn we are about
        # to run (the FIFO-within-priority order the claim protocol must
        # honor)
        old_queued = H.new_task_id(d)
        H.write_task(
            d,
            {
                "task_id": old_queued,
                "state": "queued",
                "instance": 0,
                "port": p0,
                "created_ts": time.time() - 5,
                "priority": "normal",
                "prompt": "pre-existing queued task",
                "result_file": str(self.results / (old_queued + ".json")),
            },
        )

        def finish_hog():
            time.sleep(0.3)
            try:
                rec = H.read_task(d, "t-hog")
                if rec.get("state") == "running":
                    rec["state"] = "done"
                    H.write_task(d, rec)
            except H.TaskNotFound:
                pass

        reaper = threading.Thread(target=finish_hog, daemon=True)
        reaper.start()
        # the hog's slot frees mid-wait; the spawn's claim pass must
        # promote the OLDER queued task (FIFO), not this spawn's task —
        # so this spawn waits out its budget -> exit 8, state queued
        code, doc, _ = _run_spawn(
            ["-i", "newer-than-the-queued-one"], self.env, poll_s=0.05
        )
        reaper.join(timeout=5)
        self.assertEqual(
            code,
            8,
            "the slot went to the OLDER queued task (FIFO): this spawn waits out its budget -> 8",
        )
        tid = doc["task_id"]
        self.assertEqual(doc["state"], "queued")
        self.assertIn(
            H.read_task(d, old_queued)["state"],
            ("running", "done"),
            "the older queued task was promoted first (S3.3 FIFO) — it took the slot",
        )
        self.assertEqual(
            H.read_task(d, tid)["state"],
            "queued",
            "the newer task stayed queued — the slot did not go to it",
        )
        # a later spawn: the slot is held by the promoted task's worker
        # (or freed just as its claim runs — FIFO promotes tid first
        # either way), so it queues too and exits 8
        code2, doc2, _ = _run_spawn(["-i", "triggers-promotion"], self.env, poll_s=0.05)
        self.assertEqual(
            code2,
            8,
            "the later spawn's task queues behind the promotion (FIFO)",
        )
        self.assertEqual(doc2["state"], "queued")
        self.assertNotEqual(
            doc2["task_id"],
            tid,
            "the later spawn made its own task — it did not steal the promotion",
        )
        self.assertIn(
            H.read_task(d, tid)["state"],
            ("queued", "running", "done"),
            "tid: still queued, or promoted once the slow slot freed (FIFO order)",
        )

    def test_two_concurrent_spawns_do_not_double_claim(self):
        """P0 #1 regression (G3 PR#12 r1): the spawn free-slot fast path
        is a CLAIM under the slot-claim lock (claim_own_task) — two
        concurrent spawns must NEVER promote the SAME record to running
        (the pre-fix fast path checked running_count with no lock held,
        so a concurrent claim could promote the same record in between:
        two start_workers, one task). Here: one engine slot, two spawns
        racing via a barrier; each gets its OWN task id, and both
        eventually run (the second is promoted by the first task's
        slot-freeing on the SLOW engine)."""
        p0 = free_port()
        self._write_cfg(queue_wait_max_s=3)
        self._instance(p0, variant="slow")
        self._write_health([self._inst(0, p0, total=1)])
        barrier = threading.Barrier(2)
        results: list = []
        result_lock = threading.Lock()
        errors: list = []

        # Per-thread routed streams: redirect_stdout/redirect_stderr swap
        # the GLOBAL sys.stdout/sys.stderr, so two racing spawns must not
        # each swap (thread B's JSON could land in thread A's buffer ->
        # json.loads blows up). One global swap to a stream that routes
        # every write to the CURRENT thread's buffer is race-free. The
        # out/err streams route to SEPARATE buffers (stderr diagnostic
        # text must not corrupt the stdout JSON).
        tls = threading.local()

        def _routed(attr: str) -> io.StringIO:
            buf = getattr(tls, attr, None)
            if buf is None:
                buf = io.StringIO()
                setattr(tls, attr, buf)
            return buf

        class _ThreadRoutedOut(io.StringIO):
            def write(self, s):
                return _routed("out").write(s)

            def flush(self):
                pass

            def close(self):
                pass

        class _ThreadRoutedErr(io.StringIO):
            def write(self, s):
                return _routed("err").write(s)

            def flush(self):
                pass

            def close(self):
                pass

        routed_out, routed_err = _ThreadRoutedOut(), _ThreadRoutedErr()
        saved_out, saved_err = sys.stdout, sys.stderr

        def runner():
            barrier.wait()
            # a DISTINCT prompt per thread: the S3.4 dedup key is the
            # prompt hash, and two SAME-key spawns correctly no-op the
            # second (that is S3.5, tested separately) — this test is the
            # claim-lock race, so each spawn must be its own task.
            # `--tools off` pins this test to the pre-loop single-completion
            # path it was written to verify (the P0 #1 claim-lock race,
            # G3 PR#12 r1) — not the S3.4 tool loop's extra per-step record
            # writes, which would race the detached "slow" worker against
            # tearDown's rmtree of the rig (the tool loop is covered by the
            # dedicated 3.14 cases, MAP 3.13/3.14).
            ident = threading.get_ident()
            with redirect_stdout(routed_out), redirect_stderr(routed_err):
                code = H.cmd_spawn(
                    ["-i", f"race task {ident}", "--tools", "off"],
                    self.env,
                    poll_s=0.05,
                )
            raw = getattr(tls, "out", io.StringIO()).getvalue()
            tls.out = None
            try:
                doc = json.loads(raw)
            except json.JSONDecodeError as exc:
                with result_lock:
                    errors.append(f"thread {threading.get_ident()}: {exc}: {raw!r}")
                return
            with result_lock:
                results.append((code, doc))

        try:
            sys.stdout, sys.stderr = routed_out, routed_err
            ts = [threading.Thread(target=runner) for _ in range(2)]
            for t in ts:
                t.start()
            for t in ts:
                t.join(timeout=20)
        finally:
            sys.stdout, sys.stderr = saved_out, saved_err
        self.assertEqual(errors, [], f"spawn stdout lost to a sibling thread: {errors}")
        self.assertEqual(len(results), 2)
        codes = [c for c, _ in results]
        self.assertIn(
            codes,
            [
                [0, 0],
                [0, 8],
                [8, 0],
            ],
            f"each spawn accepted (0) or queued-out (8): {codes}",
        )
        ids = [doc["task_id"] for _c, doc in results]
        self.assertEqual(len(set(ids)), 2, "two DISTINCT task ids — no shared record")
        d = self._results()
        # G3 PR#12 r2 P2: only RUNNING records carry a worker_pid (a spawn
        # that queued out on exit 8 has none — asserting exactly-2 pids was
        # timing-fragile). The no-double-claim invariant: no SINGLE worker
        # may serve two records.
        pids = [
            r.get("worker_pid")
            for r in (H.read_task(d, tid) for tid in ids)
            if r.get("worker_pid")
        ]
        self.assertEqual(
            len(pids),
            len(set(pids)),
            "no worker serves two records (the double-claim defect would start two workers on one task, or one on two)",
        )


class TestTaskWorkerRetry(unittest.TestCase):
    """The worker's S1.5 one-retry target, unit level: the P0 #2 pool
    semantics — a retry NEVER lands back on an already-tried instance."""

    def setUp(self):
        self.W = _load_taskworker()
        self.tmp = Path(tempfile.mkdtemp())
        self.d = {
            "base": self.tmp,
            "tasks": self.tmp / "tasks",
            "locks": self.tmp / ".locks",
        }
        (self.tmp / "tasks").mkdir(parents=True)

    def tearDown(self):
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def _doc(self, instances):
        return {"stack": "healthy", "instances": instances}

    def _inst(self, i, state="healthy", ctx=262144):
        return {"i": i, "port": 30101 + i, "state": state, "ctx": ctx}

    def test_retry_never_retries_a_tried_instance(self):
        # P0 #2 regression: both instances healthy, both already tried,
        # one of them carries all the load — the pre-fix least-loaded
        # ANY-instance fallback would have re-picked the tried i=0.
        rec = {
            "task_id": "t-1",
            "instances_tried": [0, 1],
            "ctx_need": None,
            "result_file": str(self.tmp / "r1.json"),
        }
        doc = self._doc([self._inst(0), self._inst(1)])
        H.write_task(self.d, {"task_id": "t-other", "state": "running", "instance": 0})
        ni, _port = self.W._pick_retry_instance(doc, rec, self.d)
        self.assertIsNone(
            ni,
            "every healthy instance already tried -> no retry target -> fail (S1.5 one retry, next instance)",
        )

    def test_retry_picks_lowest_i_untried(self):
        rec = {"task_id": "t-1", "instances_tried": [0], "ctx_need": None}
        doc = self._doc([self._inst(0), self._inst(1), self._inst(2)])
        ni, port = self.W._pick_retry_instance(doc, rec, self.d)
        self.assertEqual(ni, 1, "lowest-i healthy with ctx fit among the UNTRIED")
        self.assertEqual(port, 30102)

    def test_retry_skips_broken_untried(self):
        rec = {"task_id": "t-1", "instances_tried": [0], "ctx_need": None}
        doc = self._doc([self._inst(0), self._inst(1, state="bdown"), self._inst(2)])
        ni, _port = self.W._pick_retry_instance(doc, rec, self.d)
        self.assertEqual(ni, 2, "bdown i=1 is skipped even though it is untried")


class TestRound1P1P2Fixes(SpawnBase):
    """The round-1 P1 #3 / P1 #5 / P2 #8 / P2 #9 fixes, each pinned by a
    named case against its pre-fix shape."""

    def test_spawn_missing_engine_yaml_is_accepted(self):
        """P1 #3: the engine yaml is the manager's config, not the
        harness's — a healthy pool + free slot is NOT blocked by it.
        Pre-fix: spawn exit 2. Now: accepted; the worker's own re-read
        fails the task with ERR_ENGINE_DOWN (the record stays
        collectable)."""
        p0 = free_port()
        self._write_cfg()
        self._instance(p0, variant="ok")
        self._write_health([self._inst(0, p0)])
        # point BOTH the in-process load and the worker's env at a
        # missing file
        self.env["BONSAI2_ENGINE_YAML"] = str(self.tmp / "absent-engine.yaml")
        os.environ["BONSAI2_ENGINE_YAML"] = self.env["BONSAI2_ENGINE_YAML"]
        code, doc, _ = _run_spawn(["-i", "hi"], self.env, poll_s=0.05)
        self.assertEqual(
            code,
            0,
            "healthy pool + free slot -> accepted despite the missing engine yaml",
        )
        self.assertEqual(doc["state"], "running")
        d = self._results()
        rec = _wait_state(d, doc["task_id"], ("failed",), timeout_s=10.0)
        self.assertEqual(
            rec["error"],
            "ERR_ENGINE_DOWN",
            "the worker's re-read fails it at start (the coupling, documented in the module docstring)",
        )
        self.assertIn("worker-start", rec.get("detail", ""))

    def test_out_parent_is_created_at_spawn(self):
        """P1 #5: the --out parent deep path is validated/created at
        spawn time — a crash mid-task can no longer be the first place
        an uncreatable --out dir is discovered."""
        p0 = free_port()
        self._write_cfg()
        self._instance(p0, variant="ok")
        self._write_health([self._inst(0, p0)])
        out = self.tmp / "a" / "b" / "c" / "deep-out.json"
        code, doc, _ = _run_spawn(
            ["-i", "hi", "--out", str(out)], self.env, poll_s=0.05
        )
        self.assertEqual(code, 0)
        self.assertEqual(doc["result_file"], str(out))
        self.assertTrue(
            out.parent.is_dir(), "the --out parent was created at spawn time"
        )
        d = self._results()
        rec = _wait_state(d, doc["task_id"], ("done", "failed"), timeout_s=10.0)
        self.assertEqual(rec["state"], "done")

    def test_out_parent_under_a_file_is_exit_2(self):
        """P1 #5: an --out whose PARENT PATH COMPONENT is a file fails at
        spawn (exit 2, the malformed-args path), not mid-task."""
        p0 = free_port()
        self._write_cfg()
        self._instance(p0, variant="ok")
        self._write_health([self._inst(0, p0)])
        blocker = self.tmp / "blocker"
        blocker.write_text("i am a file in the --out path")
        out = blocker / "result.json"
        code, doc, _ = _run_spawn(
            ["-i", "hi", "--out", str(out)], self.env, poll_s=0.05
        )
        self.assertEqual(code, 2, "uncreatable --out parent -> exit 2 at spawn")
        self.assertEqual(doc["error"], "ERR_ARGS")

    def test_string_port_in_health_doc_is_coerced(self):
        """P2 #9: a string port in health.json is coerced to int at spawn
        time (the S3.1 numeric port schema) — the spawn JSON must not
        emit a string port."""
        p0 = free_port()
        self._write_cfg()
        self._instance(p0, variant="ok")
        inst = self._inst(0, p0)
        inst["port"] = str(p0)  # the malformed document
        self._write_health([inst])
        code, doc, _ = _run_spawn(["-i", "hi"], self.env, poll_s=0.05)
        self.assertEqual(code, 0)
        self.assertIsInstance(
            doc["port"], int, "spawn JSON port is numeric (S3.1 schema)"
        )
        self.assertEqual(doc["port"], p0)

    def test_zero_deadline_is_immediate_timeout(self):
        """P2 #8: deadline_s == 0.0 is NOT the 3600 s default — a
        zero-deadline task times out immediately (pre-fix: `0.0` is
        falsy, so the worker silently used the 3600 s default)."""
        W = _load_taskworker(name="bonsai2_taskworker_zero")

        p0 = free_port()
        self._write_cfg()
        self._instance(p0, variant="ok")
        self._write_health([self._inst(0, p0)])
        d = self._results()
        H.ensure_results_dirs(d)
        tid = H.new_task_id(d)
        rec = {
            "task_id": tid,
            "state": "running",
            "instance": 0,
            "port": p0,
            "prompt": "deadline zero",
            "deadline_s": 0.0,
            "result_file": str(d["base"] / f"{tid}.json"),
            "created_ts": time.time(),
        }
        H.write_task(d, rec)
        argv_backup = list(sys.argv)
        W.sys.argv = ["bonsai2-taskworker.py", str(d["tasks"] / f"{tid}.json")]
        try:
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                rc = W.main()
        finally:
            sys.argv = argv_backup
        self.assertEqual(rc, 0, "the worker always exits 0 — the record carries state")
        rec = H.read_task(d, tid)
        self.assertEqual(
            rec["state"],
            "timeout",
            "deadline 0.0 = already expired -> timeout (not the 3600 s default)",
        )
        self.assertEqual(rec["error"], "ERR_DEADLINE")


if __name__ == "__main__":
    unittest.main()
