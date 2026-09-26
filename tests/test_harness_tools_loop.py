"""MAP 3.14 / 3.15 hermetic cases — the taskworker tool loop and the
workdir/spawn-flag surface (the subagent capability layer; spec
amendment 2026-09-25, S3.4/S3.2).

3.14 (the loop, host + engine execution):
  * a tool call step + a final answer -> done; the result file carries
    tool_log / steps / workdir (the S3.1 capability fields);
  * the native `tool_calls` array AND the text-JSON convention both
    drive the loop (the A2 wire-protocol duality — the @gpu probe
    decides which the pin honors; both halves must work hermetically);
  * the four-tool `tools` array is sent with every loop request;
    `--tools off` sends NO tools array (the byte-identical wire
    constraint);
  * tool execution against the host module (default) AND against the
    instance /tools endpoint (`tool_execution: engine`, including the
    host->engine name mapping and the error-response shape);
  * the message history grows with the assistant tool_calls + the tool
    result between steps;
  * loop bounds: tool_invalid_max consecutive bad calls ->
    failed/ERR_TOOL_INVALID_CALL (collect 21); a tool step overrun ->
    failed/ERR_TOOL_TIMEOUT (collect 21, exit_code 124 in the log);
    --max-steps without a final answer -> timeout/ERR_DEADLINE
    (collect 22);
  * steer is drained at the NEXT STEP BOUNDARY (S4.3 amendment
    semantics);
  * cancel mid-step -> failed/ERR_CANCELLED (the SIGTERM handler's
    kill_current + record mark).

3.15 (workdir + spawn flags):
  * `--workdir DIR` (must exist + writable at spawn, else exit 2),
    `--max-steps N` (1-128, else exit 2);
  * no `--workdir` -> the harness creates <workspace_dir>/<task_id>;
  * `workdir` is part of the dedup key: same prompt, two workdirs =
    two tasks; same prompt + same workdir = dedup no-op;
  * the spawn/collect JSON carries workdir; collect on a failed tool
    task carries the tool_log up to the failure;
  * rotate_workspaces(): prunes per-task workdirs past the 7-day
    window, keeps fresh ones + stray files.

Run: `python3 -m unittest discover -s tests -t .` (hermetic default).
"""

from __future__ import annotations

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

from .fixtures.mock_engine import MockEngine
from .fixtures.tool_engine import ToolEngine


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


def _run_cmd(fn, argv: list[str], env: dict) -> tuple[int, dict, str]:
    """Run one S3.2 command in-process; (exit code, stdout JSON, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = fn(argv, env)
    return code, json.loads(out.getvalue()), err.getvalue()


class LoopBase(unittest.TestCase):
    """The tool-loop rig: fixture main endpoint + a SCRIPTED subagent
    instance (tests/fixtures/tool_engine.py) on its own port + a
    hand-written health document + temp results dir + the S3.2 env
    overrides (merged into os.environ for the detached worker, the same
    convention as the spawn rig)."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.health_dir = self.tmp / "run"
        self.results = self.tmp / "results"
        self.cfg = self.tmp / "harness.yaml"
        self.main_engine = MockEngine(variant="ok")
        self.main_engine.start()
        self.engines: list[ToolEngine] = []
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

    def tearDown(self) -> None:
        for k, v in self._saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.main_engine.stop()
        for e in self.engines:
            e.stop()
        self._tmp.cleanup()

    # ------------------------------------------------------------------
    def _write_cfg(self, **overrides) -> None:
        data = {
            "main_endpoint": f"http://127.0.0.1:{self.main_engine.port}",
            "results_dir": str(self.results),
            "dedup_dir": str(self.health_dir / "dedup"),
            "dedup_ttl_h": 24,
            "queue_wait_max_s": 2,
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
        for k, v in self.env.items():  # the worker subprocess inherits
            os.environ[k] = v

    def _instance(
        self, port: int, responses: list, tools_response: dict | None = None
    ) -> ToolEngine:
        eng = ToolEngine(responses=responses, port=port, tools_response=tools_response)
        eng.start()
        self.engines.append(eng)
        return eng

    def _write_health(self, instances: list[dict]) -> None:
        self.health_dir.mkdir(parents=True, exist_ok=True)
        doc = {
            "stack": "healthy",
            "instances": instances,
            "main_endpoint": "http://x",
            "main_healthy": True,
        }
        (self.health_dir / "health.json").write_text(json.dumps(doc) + "\n")

    def _inst(self, i: int, port: int, total: int = 8) -> dict:
        return {
            "i": i,
            "port": port,
            "state": "healthy",
            "ctx": 262144,
            "ctx_reduced": False,
            "slots_in_use": 0,
            "slots_total": total,
        }

    def _results(self) -> dict:
        return H.results_dirs(H.load_config(self.env))

    def _spawn(self, argv: list[str]) -> tuple[int, dict]:
        code, doc, _ = _run_cmd(
            H.cmd_spawn,
            argv,
            self.env,
        )
        return code, doc

    def _wait_terminal(self, d: dict, task_id: str, timeout_s: float = 20.0) -> dict:
        deadline = time.monotonic() + timeout_s
        last = None
        while time.monotonic() < deadline:
            last = H.read_task(d, task_id)
            if last.get("state") in H.TERMINAL_STATES:
                return last
            time.sleep(0.05)
        raise AssertionError(f"task {task_id} never terminal: {last}")

    def _collect(self, task_id: str) -> tuple[int, dict]:
        code, doc, _ = _run_cmd(H.cmd_collect, [task_id], self.env)
        return code, doc


class TestToolLoopHostExecution(LoopBase):
    """3.14 — the step loop against the host tool module (the default,
    `tool_execution: host`)."""

    def _rig(self, responses: list, **cfg_over) -> tuple[int, dict, int, ToolEngine]:
        p0 = 0
        self._write_cfg(**cfg_over)
        eng = self._instance(p0, responses)
        self._write_health([self._inst(0, eng.port)])
        code, doc = self._spawn(["-i", "loop task", "--deadline", "30"])
        assert code == 0, doc
        return 0, doc, eng.port, eng

    def test_tool_call_then_final_answer_is_done(self) -> None:
        _x, doc, _port, _eng = self._rig(
            [
                {
                    "kind": "tool",
                    "tool": "bash",
                    "params": {"cmd": "echo made > made.txt"},
                },
                {"kind": "final", "content": "all good"},
            ]
        )
        d = self._results()
        H.ensure_results_dirs(d)
        rec = self._wait_terminal(d, doc["task_id"])
        self.assertEqual(rec["state"], "done", f"got {rec}")
        code, cdoc = self._collect(doc["task_id"])
        self.assertEqual(code, 20, "collect: done = 20")
        self.assertEqual(cdoc["answer"], "all good")
        # the S3.1 capability fields (the amendment's collect payload):
        self.assertEqual(len(cdoc["tool_log"]), 1)
        entry = cdoc["tool_log"][0]
        self.assertEqual(entry["tool"], "bash")
        self.assertEqual(entry["step"], 1)
        self.assertEqual(entry["exit_code"], 0)
        self.assertEqual(cdoc["steps"], 2, "two completions: tool step + final")
        self.assertIsInstance(cdoc["workdir"], str)
        # the tool actually ran in the workdir (the confinement payoff):
        self.assertTrue(Path(cdoc["workdir"], "made.txt").is_file())
        # the result file on disk carries the same fields:
        rf = json.loads(Path(rec["result_file"]).read_text())
        self.assertEqual(rf["tool_log"], cdoc["tool_log"])
        self.assertEqual(rf["steps"], 2)
        self.assertEqual(rf["workdir"], cdoc["workdir"])

    def test_native_tools_array_is_sent_on_loop_requests(self) -> None:
        _x, doc, _port, eng = self._rig(
            [
                {"kind": "tool", "tool": "read_file", "params": {"path": "nope.txt"}},
                {"kind": "final", "content": "ok"},
            ]
        )
        d = self._results()
        H.ensure_results_dirs(d)
        self._wait_terminal(d, doc["task_id"])
        req = eng.request_log[0]
        self.assertIn("tools", req, "the loop sends the four-tool array")
        self.assertEqual(
            [t["function"]["name"] for t in req["tools"]],
            ["bash", "read_file", "write_file", "python"],
        )
        # the tool result was fed back: the 2nd request's history carries
        # the assistant tool_calls + a role=tool message
        req2 = eng.request_log[1]
        roles = [m.get("role") for m in req2["messages"]]
        self.assertIn("tool", roles)
        self.assertIn("assistant", roles)

    def test_text_json_tool_call_fallback(self) -> None:
        # the A2 probe fallback: no native tool_calls array, the answer
        # carries a fenced ```json {"tool": ...} block — the loop must
        # still execute it (the @gpu probe decides which the pin honors;
        # both halves work).
        _x, doc, _port, _eng = self._rig(
            [
                {
                    "kind": "tool",
                    "tool": "write_file",
                    "params": {"path": "t.txt", "content": "x"},
                    "native": False,
                },
                {"kind": "final", "content": "textjson ok"},
            ]
        )
        d = self._results()
        H.ensure_results_dirs(d)
        rec = self._wait_terminal(d, doc["task_id"])
        self.assertEqual(rec["state"], "done")
        # the text-JSON request carried NO native tools delta: the mock
        # emitted a content chunk instead (assert the mock honored the
        # spec shape it was asked to)
        code, cdoc = self._collect(doc["task_id"])
        self.assertEqual(code, 20)
        self.assertEqual(cdoc["tool_log"][0]["tool"], "write_file")
        # the write_file tool call actually wrote "x" to <workdir>/t.txt
        # (G3 PR#18 r2 P0: the old `cdoc["workdir"] and "x"` was a tautology
        # — workdir is a non-empty path string, so the left side was always
        # the literal "x" and never checked the file's real content).
        self.assertEqual(
            "x",
            Path(cdoc["workdir"], "t.txt").read_text(),
            "write_file (text-JSON fallback) wrote the content to the workdir",
        )

    def test_invalid_calls_exhaust_tool_invalid_max(self) -> None:
        # one malformed call (bash with no cmd) repeats until
        # tool_invalid_max consecutive bad calls -> failed.
        self._write_cfg(tool_invalid_max=2)
        p0 = 0
        eng = self._instance(p0, [{"kind": "tool", "tool": "bash", "params": {}}])
        self._write_health([self._inst(0, eng.port)])
        code, doc = self._spawn(["-i", "bad calls", "--deadline", "30"])
        self.assertEqual(code, 0)
        d = self._results()
        H.ensure_results_dirs(d)
        rec = self._wait_terminal(d, doc["task_id"])
        self.assertEqual(rec["state"], "failed")
        self.assertEqual(rec["error"], "ERR_TOOL_INVALID_CALL")
        self.assertEqual(len(eng.request_log), 2, "one bad call per step, two steps")
        code_c, cdoc = self._collect(doc["task_id"])
        self.assertEqual(code_c, 21, "collect: failed = 21")
        self.assertEqual(cdoc["error"], "ERR_TOOL_INVALID_CALL")
        self.assertEqual(len(cdoc["tool_log"]), 2, "the ledger up to the failure")

    def test_tool_step_overrun_fails_err_tool_timeout(self) -> None:
        # a step that overruns tool_timeout_s -> failed ERR_TOOL_TIMEOUT
        # (collect 21); the log entry carries exit_code 124.
        self._write_cfg(tool_timeout_s=1)
        p0 = 0
        eng = self._instance(
            p0, [{"kind": "tool", "tool": "bash", "params": {"cmd": "sleep 20"}}]
        )
        self._write_health([self._inst(0, eng.port)])
        code, doc = self._spawn(["-i", "slow tool", "--deadline", "30"])
        self.assertEqual(code, 0)
        d = self._results()
        H.ensure_results_dirs(d)
        rec = self._wait_terminal(d, doc["task_id"], timeout_s=15.0)
        self.assertEqual(rec["state"], "failed")
        self.assertEqual(rec["error"], "ERR_TOOL_TIMEOUT")
        code_c, cdoc = self._collect(doc["task_id"])
        self.assertEqual(code_c, 21)
        self.assertEqual(cdoc["tool_log"][0]["exit_code"], 124)

    def test_max_steps_exhausted_is_timeout(self) -> None:
        # --max-steps without a final answer -> timeout / ERR_DEADLINE
        # (collect 22), not failed.
        p0 = 0
        eng = self._instance(
            p0, [{"kind": "tool", "tool": "bash", "params": {"cmd": "true"}}]
        )
        self._write_cfg()
        self._write_health([self._inst(0, eng.port)])
        code, doc = self._spawn(
            ["-i", "never finishes", "--max-steps", "3", "--deadline", "30"]
        )
        self.assertEqual(code, 0)
        d = self._results()
        H.ensure_results_dirs(d)
        rec = self._wait_terminal(d, doc["task_id"], timeout_s=15.0)
        self.assertEqual(rec["state"], "timeout")
        self.assertEqual(rec["error"], "ERR_DEADLINE")
        self.assertEqual(len(eng.request_log), 3, "exactly --max-steps completions")
        code_c, _cdoc = self._collect(doc["task_id"])
        self.assertEqual(code_c, 22, "collect: timeout = 22")

    def test_steer_drained_at_next_step_boundary(self) -> None:
        # S4.3 amendment: steer on a --tools on task is injected as the
        # next user message at the loop's NEXT STEP BOUNDARY.
        p0 = 0
        eng = self._instance(
            p0,
            [
                {"kind": "tool", "tool": "bash", "params": {"cmd": "sleep 3"}},
                {"kind": "final", "content": "steered"},
            ],
        )
        self._write_cfg()
        self._write_health([self._inst(0, eng.port)])
        code, doc = self._spawn(["-i", "steer me", "--deadline", "30"])
        self.assertEqual(code, 0)
        d = self._results()
        H.ensure_results_dirs(d)
        # wait for the step-1 tool to be IN FLIGHT (rec steps==1 is
        # written before the execution; the 3 s sleep is the window)
        rec = H.read_task(d, doc["task_id"])
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            rec = H.read_task(d, doc["task_id"])
            if rec.get("steps", 0) >= 1:
                break
            time.sleep(0.05)
        self.assertGreaterEqual(rec.get("steps", 0), 1, "step 1 must have started")
        code_s, _sdoc, _ = _run_cmd(
            H.cmd_steer, [doc["task_id"], "-m", "injected note"], self.env
        )
        self.assertEqual(code_s, 0, "steer accepted")
        self._wait_terminal(d, doc["task_id"])
        # the 2nd request's history carries the injected user message:
        req2 = eng.request_log[1]
        msgs = [m.get("content") for m in req2["messages"] if m.get("role") == "user"]
        self.assertIn("injected note", msgs, f"steer not drained: {msgs}")

    def test_cancel_mid_tool_step_marks_err_cancelled(self) -> None:
        # S3.4 cancel: SIGTERM mid-step -> the handler kills the in-flight
        # tool's process group, the record goes failed/ERR_CANCELLED
        # (the slot frees at signal time), collect 21.
        # G3 PR#18 r5 P2 (noted): the poll below breaks once
        # worker_pid is set AND steps >= 1 — a narrow window exists
        # between the record marking the pid and the tool subprocess
        # actually spawning (the sleep 20 has not yet started). If cancel
        # lands in that window, the handler's kill-current-tool is a
        # no-op (no _CURRENT_PROC) and the REAPER is what reclaims the
        # slot. The assertion (state=failed, error=ERR_CANCELLED,
        # collect 21) holds either way, so the test does not distinguish
        # handler-kill from reaper-kill — that is acceptable for v1
        # (ERR_CANCELLED is the contract; WHICH reaper ran is
        # implementation detail). To pin the window, assert on a tool
        # subprocess that is definitely alive (e.g. pgrep the sleep pid);
        # deferred to the test-overhaul phase the user announced.
        p0 = 0
        eng = self._instance(
            p0, [{"kind": "tool", "tool": "bash", "params": {"cmd": "sleep 20"}}]
        )
        self._write_cfg()
        self._write_health([self._inst(0, eng.port)])
        code, doc = self._spawn(["-i", "cancel me", "--deadline", "30"])
        self.assertEqual(code, 0)
        d = self._results()
        H.ensure_results_dirs(d)
        rec = H.read_task(d, doc["task_id"])
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            rec = H.read_task(d, doc["task_id"])
            if rec.get("worker_pid") and rec.get("steps", 0) >= 1:
                break
            time.sleep(0.05)
        self.assertIsNotNone(rec.get("worker_pid"), "the worker must be running")
        code_c, _cdoc, _ = _run_cmd(H.cmd_cancel, [doc["task_id"]], self.env)
        self.assertEqual(code_c, 0, "cancel accepted")
        rec_f = self._wait_terminal(d, doc["task_id"])
        self.assertEqual(rec_f["state"], "failed")
        self.assertEqual(rec_f["error"], "ERR_CANCELLED")
        code_col, _ = self._collect(doc["task_id"])
        self.assertEqual(code_col, 21)


class TestToolLoopEngineExecution(LoopBase):
    """3.14 — the engine-side execution half (`tool_execution: engine`):
    the taskworker POSTs the instance's /tools endpoint instead of
    running the host module; the host->engine name mapping must land."""

    def test_engine_side_execution_maps_names(self) -> None:
        p0 = 0
        eng = self._instance(
            p0,
            [
                {"kind": "tool", "tool": "bash", "params": {"cmd": "echo eng"}},
                {"kind": "final", "content": "engine done"},
            ],
            tools_response={
                "success": True,
                "plain_text_response": "engine-out",
                "exit_code": 0,
            },
        )
        self._write_cfg(tool_execution="engine")
        self._write_health([self._inst(0, eng.port)])
        code, doc = self._spawn(["-i", "engine tools", "--deadline", "30"])
        self.assertEqual(code, 0)
        d = self._results()
        H.ensure_results_dirs(d)
        rec = self._wait_terminal(d, doc["task_id"])
        self.assertEqual(rec["state"], "done", f"got {rec}")
        # the /tools endpoint got the MAPPED name + params:
        self.assertEqual(len(eng.tools_log), 1)
        treq = eng.tools_log[0]
        self.assertEqual(treq["tool"], "exec_shell_command")
        self.assertEqual(treq["params"]["command"], "echo eng")
        # G3 PR#18 r2 P2: the engine-side path must NOT force the host
        # workdir as `cwd` — the instance container has no shared volume
        # (units/bonsai2-server@.service, no -v), so the host workdir does
        # not exist inside it; a chdir into a missing path would fail.
        self.assertNotIn(
            "cwd",
            treq["params"],
            "engine /tools body must not carry the host workdir as cwd",
        )
        code_c, cdoc = self._collect(doc["task_id"])
        self.assertEqual(code_c, 20)
        self.assertEqual(cdoc["tool_log"][0]["output"], "engine-out")

    def test_engine_side_error_feeds_back_and_continues(self) -> None:
        # a /tools error is an error RESULT (fed back to the model), not
        # a task failure — the loop continues to the final answer.
        p0 = 0
        eng = self._instance(
            p0,
            [
                {"kind": "tool", "tool": "read_file", "params": {"path": "a.txt"}},
                {"kind": "final", "content": "recovered"},
            ],
            tools_response={"error": "boom"},
        )
        self._write_cfg(tool_execution="engine")
        self._write_health([self._inst(0, eng.port)])
        code, doc = self._spawn(["-i", "engine err", "--deadline", "30"])
        self.assertEqual(code, 0)
        d = self._results()
        H.ensure_results_dirs(d)
        rec = self._wait_terminal(d, doc["task_id"])
        self.assertEqual(rec["state"], "done")
        code_c, cdoc = self._collect(doc["task_id"])
        self.assertEqual(code_c, 20)
        self.assertIn("ERR_ENGINE_TOOL", cdoc["tool_log"][0]["output"])

    def test_tools_off_sends_no_tools_array(self) -> None:
        # the byte-identical wire constraint: --tools off is the single
        # completion WITHOUT a tools array (the pre-loop path).
        p0 = 0
        eng = self._instance(p0, [{"kind": "final", "content": "done"}])
        self._write_cfg()
        self._write_health([self._inst(0, eng.port)])
        code, doc = self._spawn(
            ["-i", "plain task", "--tools", "off", "--deadline", "30"]
        )
        self.assertEqual(code, 0)
        d = self._results()
        H.ensure_results_dirs(d)
        rec = self._wait_terminal(d, doc["task_id"])
        self.assertEqual(rec["state"], "done")
        req = eng.request_log[0]
        self.assertNotIn("tools", req, "--tools off must not send the tools array")
        # G3 PR#18 r6 P1: a `--tools off` spawn JSON must match the
        # pre-loop S3.1 schema byte-for-byte — the capability-layer
        # `workdir` field is NOT part of the off-task spawn surface
        # (the RECORD still stores it: the S3.4 dedup key carries the
        # workdir term regardless of the loop flag, so two off-tasks in
        # different workdirs stay distinct).
        self.assertNotIn(
            "workdir", doc, "--tools off spawn JSON omits workdir (pre-loop schema)"
        )
        self.assertIsNotNone(
            rec.get("workdir"), "the off-task RECORD still stores workdir (dedup key)"
        )
        code_c, cdoc = self._collect(doc["task_id"])
        self.assertEqual(code_c, 20)
        self.assertEqual(cdoc["tool_log"], [], "no-file/off default: empty ledger")
        self.assertIsNone(
            cdoc["steps"], "no file written on the off path -> steps null"
        )


class TestSpawnFlagsAndWorkdir(LoopBase):
    """3.15 — the --workdir/--max-steps surface, the dedup-key workdir
    term, and the 7-day workdir rotation."""

    def test_defaults_tools_on_workdir_created(self) -> None:
        p0 = 0
        eng = self._instance(p0, [{"kind": "final", "content": "done"}])
        self._write_cfg()
        self._write_health([self._inst(0, eng.port)])
        code, doc = self._spawn(["-i", "defaults task", "--deadline", "30"])
        self.assertEqual(code, 0)
        d = self._results()
        H.ensure_results_dirs(d)
        rec = H.read_task(d, doc["task_id"])
        self.assertEqual(rec["tools"], "on", "tools default ON (S3.2)")
        self.assertEqual(rec["max_steps"], 32, "task_max_steps default")
        self.assertTrue(
            Path(rec["workdir"]).is_dir(), "the harness created the workdir"
        )
        # G3 PR#18 r3 P1: the auto-created workdir is 0o700 owner-only
        # (defense-in-depth for the shared workspace_dir).
        self.assertEqual(
            Path(rec["workdir"]).stat().st_mode & 0o777,
            0o700,
            "auto-workdir is 0o700 owner-only",
        )
        # the accepted JSON carries the workdir field:
        self.assertEqual(doc["workdir"], rec["workdir"])

    def test_explicit_workdir_used_verbatim(self) -> None:
        p0 = 0
        eng = self._instance(p0, [{"kind": "final", "content": "done"}])
        self._write_cfg()
        self._write_health([self._inst(0, eng.port)])
        custom = self.tmp / "custom-wd"
        custom.mkdir()
        code, doc = self._spawn(
            ["-i", "explicit wd", "--workdir", str(custom), "--deadline", "30"]
        )
        self.assertEqual(code, 0)
        d = self._results()
        H.ensure_results_dirs(d)
        rec = H.read_task(d, doc["task_id"])
        self.assertEqual(rec["workdir"], str(custom.resolve()))

    def test_workdir_missing_is_exit_2(self) -> None:
        p0 = 0
        eng = self._instance(p0, [{"kind": "final", "content": "done"}])
        self._write_cfg()
        self._write_health([self._inst(0, eng.port)])
        code, doc, _ = _run_cmd(
            H.cmd_spawn,
            ["-i", "missing wd", "--workdir", str(self.tmp / "no-such-dir")],
            self.env,
        )
        self.assertEqual(code, 2)
        self.assertEqual(doc["error"], "ERR_ARGS")
        self.assertIn("workdir", doc["detail"])

    def test_max_steps_flag_stored(self) -> None:
        p0 = 0
        eng = self._instance(p0, [{"kind": "final", "content": "done"}])
        self._write_cfg()
        self._write_health([self._inst(0, eng.port)])
        code, doc = self._spawn(["-i", "ms", "--max-steps", "5", "--deadline", "30"])
        self.assertEqual(code, 0)
        d = self._results()
        H.ensure_results_dirs(d)
        self.assertEqual(H.read_task(d, doc["task_id"])["max_steps"], 5)

    def test_max_steps_out_of_range_is_exit_2(self) -> None:
        # "007" is the G3 PR#18 r3 P0: isdigit() accepted leading-zero
        # tokens and silently stored int("007")=7; the strict
        # ^[1-9][0-9]*$ regex now rejects it as a positive-integer violation.
        self._write_cfg()
        for bad in ("0", "-1", "129", "007", "000"):
            code, doc, _ = _run_cmd(
                H.cmd_spawn, ["-i", "bad ms", "--max-steps", bad], self.env
            )
            self.assertEqual(code, 2, f"--max-steps {bad}")
            self.assertEqual(doc["error"], "ERR_ARGS")

    def test_workdir_is_part_of_the_dedup_key(self) -> None:
        # same prompt, two DIFFERENT workdirs = two tasks (A4: the workdir
        # is "everything that determines what the task will do");
        # same prompt + same workdir = a dedup no-op.
        p0, p1 = 0, 0
        eng0 = self._instance(
            p0, [{"kind": "tool", "tool": "bash", "params": {"cmd": "sleep 3"}}]
        )
        eng1 = self._instance(p1, [{"kind": "final", "content": "done"}])
        self._write_cfg()
        self._write_health([self._inst(0, eng0.port), self._inst(1, eng1.port)])
        wd_a = self.tmp / "wd-a"
        wd_a.mkdir()
        code_a, doc_a = self._spawn(
            ["-i", "dedup probe", "--workdir", str(wd_a), "--deadline", "30"]
        )
        self.assertEqual(code_a, 0)
        d = self._results()
        H.ensure_results_dirs(d)
        # wait for task A to be live (running): the dedup no-op below
        # requires a LIVE winner
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if H.read_task(d, doc_a["task_id"])["state"] == "running":
                break
            time.sleep(0.05)
        # same prompt + SAME workdir -> dedup onto A (the accepted JSON
        # is A's record; the S3.2 detail may carry the dedup note)
        code_b, doc_b = self._spawn(["-i", "dedup probe", "--workdir", str(wd_a)])
        self.assertEqual(code_b, 0)
        self.assertEqual(doc_b["task_id"], doc_a["task_id"], "dedup no-op -> same task")
        # same prompt + a DIFFERENT workdir -> a distinct task
        wd_c = self.tmp / "wd-c"
        wd_c.mkdir()
        code_c, doc_c = self._spawn(["-i", "dedup probe", "--workdir", str(wd_c)])
        self.assertEqual(code_c, 0)
        self.assertNotEqual(
            doc_c["task_id"], doc_a["task_id"], "different workdir = different task"
        )

    def test_collect_failed_tool_task_carries_tool_log(self) -> None:
        # S3.1: "a failed task's result file still carries tool_log up to
        # the failure" — the ledger is diagnostic, not the verdict.
        p0 = 0
        eng = self._instance(
            p0, [{"kind": "tool", "tool": "bash", "params": {"cmd": "sleep 20"}}]
        )
        self._write_cfg(tool_timeout_s=1)
        self._write_health([self._inst(0, eng.port)])
        code, doc = self._spawn(["-i", "fail log", "--deadline", "30"])
        self.assertEqual(code, 0)
        d = self._results()
        H.ensure_results_dirs(d)
        rec = self._wait_terminal(d, doc["task_id"], timeout_s=15.0)
        self.assertEqual(rec["state"], "failed")
        code_c, cdoc = self._collect(doc["task_id"])
        self.assertEqual(code_c, 21)
        self.assertEqual(len(cdoc["tool_log"]), 1)
        self.assertEqual(cdoc["tool_log"][0]["tool"], "bash")
        self.assertEqual(cdoc["workdir"], rec["workdir"])


class TestWorkspaceRotation(LoopBase):
    """3.15 — rotate_workspaces(): the 7-day window (best-effort, never
    blocks a spawn). Unit-level: no engine needed."""

    def test_stale_workdirs_pruned_fresh_kept(self) -> None:
        ws = self.tmp / "ws"
        ws.mkdir()
        old = ws / "t-old"
        old.mkdir()
        (old / "f.txt").write_text("x")
        new = ws / "t-new"
        new.mkdir()
        (new / "f.txt").write_text("x")
        (ws / "stray.txt").write_text("x")  # a stray FILE is not a workdir
        old_time = time.time() - 8 * 24 * 3600
        os.utime(old, (old_time, old_time))
        os.utime(old / "f.txt", (old_time, old_time))

        removed = H.rotate_workspaces(str(ws))
        self.assertEqual(removed, 1)
        self.assertFalse(old.exists(), "the stale workdir was pruned")
        self.assertTrue(new.exists(), "the fresh workdir survives")
        self.assertTrue((ws / "stray.txt").exists(), "stray files are untouched")

    def test_file_mtime_newer_than_dir_kept(self) -> None:
        # G3 PR#18 r4 P0: in-place file writes do not bump the parent
        # dir's mtime. A workdir whose DIR mtime is stale (8 days old) but
        # whose FILE was touched 1h ago must be KEPT — the pre-fix
        # impl pruned on the dir mtime and would have rmtree'd a live
        # workdir. (The sibling test above sets dir+file to the same old
        # time, so it cannot distinguish dir-mtime from file-mtime.)
        ws = self.tmp / "ws-mixed"
        ws.mkdir()
        old = ws / "t-old-dir-fresh-file"
        old.mkdir()
        f = old / "live.txt"
        f.write_text("x")
        old_time = time.time() - 8 * 24 * 3600
        fresh_time = time.time() - 3600
        # dir mtime old, file mtime fresh
        os.utime(old, (old_time, old_time))
        os.utime(f, (fresh_time, fresh_time))
        removed = H.rotate_workspaces(str(ws))
        self.assertEqual(removed, 0, "fresh file inside an old-mtime dir keeps it")
        self.assertTrue(old.exists(), "the workdir with a fresh file survives")

    def test_missing_dir_is_a_noop(self) -> None:
        self.assertEqual(H.rotate_workspaces(str(self.tmp / "no-such-ws")), 0)


if __name__ == "__main__":
    unittest.main()
