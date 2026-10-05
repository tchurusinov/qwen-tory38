"""Second-practical-run remediation (2026-10-04) — hermetic cases.

Companion to evidence-ledger-second-practical-run.md +
fix-proposals-second-practical-run.md (both in-repo). The 2026-10-04
live run lost 3 of 4 code-gen windows to the pin's 15-17K+ token
pre-tool REASONING block on a full-file write (six attempts, zero
tool calls, both cap+deadline pairings) and surfaced two infra
defects (install chown missed workspaces/; the manager reaper ran
without cfg). This module covers the hermetic half of the DOD:

  * reasoning OFF by default — the spawn record carries
    reasoning_off (the S3.6 task_reasoning_off default, the
    --reasoning on|off flag, the non-bool config gate -> exit 2, the
    config override); the tool-loop body + the pre-loop body carry
    `reasoning_effort: "none"` when the record has it and OMIT it for
    a record without the key (the first-run F1 byte-identical-wire
    discipline).
  * the F2 nudge is BOUNDED — two consecutive cap-cut empty
    completions fail the task ERR_TRUNCATED_NO_PROGRESS (collect 21)
    with the F4 salvage; the nudge message names the verify step; the
    streak resets on any content-carrying completion.
  * the worker self-cancel (the F11 gap) — the worker's OWN deadline
    exits issue the best-effort control POST for the latest in-flight
    completion id (gated on arm_reasoning_control + a live cmpl_id;
    fault-tolerant on a dead port; the tool-loop deadline path targets
    the tracked last_cmpl_id).
  * the progress probe — progress_report's fields + filter + the
    sidecar read; `bonsai2-status --tasks` emits the probe JSON
    (exit 0/2, no health.json requirement); no flag stays the pool
    status (byte-identical contract).
  * the manager reaper now passes cfg — cancel_on_deadline: false in
    the config the manager loads is honored (no control POST); the
    default path still cancels.
  * the install chowns workspaces/ like results/ (the second-run's
    INFRA DEFECT #2: user-scope spawn without --workdir EACCES'd on
    /run/bonsai2/workspaces).

Run: `python3 -m unittest discover -s tests -t .` (the full-suite
invocation). Absolute import-of-fixture form (top-level discovery sees
no package), so the module ALSO loads under `discover -s tests -t .`.

Hermetic: stdlib-only top-level imports (the suite's AST rule); the
fixture engines are the in-repo ToolEngine / MockEngine.
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
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
BIN_DIR = REPO_ROOT / "bin"
ENGINE_YAML = REPO_ROOT / "etc" / "subagent-engine.yaml"

from .fixtures.mock_engine import MockEngine, free_port
from .fixtures.tool_engine import ToolEngine


def _load_module(name: str, path: Path):
    """Load a bin/ tool as a module. The module must be registered in
    sys.modules BEFORE exec (the same convention as
    tests/test_remediation_first_practical_run.py)."""
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    assert spec and spec.loader, f"could not load {path}"
    mod = importlib.util.module_from_spec(spec)
    import sys as _sys

    _sys.modules[name] = mod
    try:
        loader.exec_module(mod)
    except BaseException:
        _sys.modules.pop(name, None)
        raise
    return mod


H = _load_module("bonsai2_harness", BIN_DIR / "bonsai2_harness.py")


def _load_taskworker(name: str = "bonsai2_taskworker_secondrun_test"):
    return _load_module(name, BIN_DIR / "bonsai2-taskworker.py")


def _load_installer(name: str = "bonsai2_install_secondrun_test"):
    return _load_module(name, BIN_DIR / "bonsai2_install.py")


def _load_manager(name: str = "bonsai2_manager_secondrun_test"):
    return _load_module(name, BIN_DIR / "bonsai2-manager")


W = _load_taskworker()


def _run_cmd(fn, argv: list[str], env: dict) -> tuple[int, dict, str]:
    """Run one S3.2 command in-process; (exit code, stdout JSON, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = fn(argv, env)
    return code, json.loads(out.getvalue()), err.getvalue()


def _run_spawn(argv: list[str], env: dict, poll_s: float = 0.05):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = H.cmd_spawn(argv, env, poll_s=poll_s)
    return code, json.loads(out.getvalue()), err.getvalue()


class _Base(unittest.TestCase):
    """The shared rig (same convention as the first-run remediation
    suite): temp tree, the S3.2 env overrides merged into os.environ,
    the engine YAML fixture pointed at the repo's etc/."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.health_dir = self.tmp / "run"
        self.results = self.tmp / "results"
        self.cfg = self.tmp / "harness.yaml"
        self.env: dict = {}
        self._saved_env = {
            k: os.environ.get(k)
            for k in (
                "BONSAI2_CONFIG",
                "BONSAI2_RESULTS_DIR",
                "BONSAI2_HEALTH_DIR",
                "BONSAI2_ENGINE_YAML",
                "SUDO_UID",
            )
        }

    def tearDown(self) -> None:
        for k, v in self._saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self._tmp.cleanup()

    def _write_cfg(self, **overrides) -> None:
        data = {
            "main_endpoint": "http://127.0.0.1:1",
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
        for k, v in self.env.items():
            os.environ[k] = v

    def _write_health(self, instances: list[dict], stack: str = "healthy") -> None:
        self.health_dir.mkdir(parents=True, exist_ok=True)
        doc = {
            "stack": stack,
            "instances": instances,
            "main_endpoint": "http://127.0.0.1:1",
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

    def _wait_state(
        self, d: dict, task_id: str, states: tuple, timeout_s: float = 20.0
    ) -> dict:
        deadline = time.monotonic() + timeout_s
        last = None
        while time.monotonic() < deadline:
            try:
                last = H.read_task(d, task_id)
            except H.TaskNotFound:
                time.sleep(0.05)
                continue
            if last.get("state") in states:
                return last
            time.sleep(0.05)
        raise AssertionError(f"task {task_id} never reached {states}: {last}")


# ======================================================================
# reasoning OFF by default — the spawn record
# ======================================================================


class TestSpawnRecordReasoningOff(_Base):
    """The record carries reasoning_off: the S3.6 task_reasoning_off
    default (True), the --reasoning on|off flag, the config override,
    and the boolean gate (non-bool -> exit 2)."""

    def _spawn_rec(self, argv_extra: list[str], **cfg_over) -> dict:
        self._write_cfg(**cfg_over)
        main = MockEngine(variant="ok")
        main.start()
        data = {
            "main_endpoint": f"http://127.0.0.1:{main.port}",
            "results_dir": str(self.results),
            "dedup_dir": str(self.health_dir / "dedup"),
            "dedup_ttl_h": 24,
            "queue_wait_max_s": 2,
            "workspace_dir": str(self.tmp / "workspaces"),
        }
        data.update(cfg_over)
        self.cfg.write_text("\n".join(f"{k}: {v}" for k, v in data.items()) + "\n")
        self.env["BONSAI2_CONFIG"] = str(self.cfg)
        os.environ["BONSAI2_CONFIG"] = str(self.cfg)
        p0 = free_port()
        self._write_health([self._inst(0, p0)])
        argv = ["-i", f"reasoning task {os.urandom(4).hex()}"] + argv_extra
        code, doc, _err = _run_spawn(argv, self.env)
        self.assertEqual(code, 0, f"spawn must be accepted: {doc}")
        main.stop()
        d = self._results()
        H.ensure_results_dirs(d)
        return H.read_task(d, doc["task_id"])

    def test_reasoning_off_default_on_record(self) -> None:
        rec = self._spawn_rec([])
        self.assertTrue(
            rec.get("reasoning_off"),
            "second-run: a record without --reasoning carries the "
            "task_reasoning_off default (True — reasoning off)",
        )

    def test_reasoning_on_flag_on_record(self) -> None:
        rec = self._spawn_rec(["--reasoning", "on"])
        self.assertFalse(
            rec.get("reasoning_off"),
            "second-run: --reasoning on is the explicit exception (record off=False)",
        )

    def test_reasoning_off_flag_on_record(self) -> None:
        rec = self._spawn_rec(["--reasoning", "off"])
        self.assertTrue(rec.get("reasoning_off"), "--reasoning off is the default")

    def test_reasoning_flag_bad_value_is_exit_2(self) -> None:
        self._write_cfg()
        main = MockEngine(variant="ok")
        main.start()
        p0 = free_port()
        self._write_health([self._inst(0, p0)])
        for bad in ("maybe", "ON", ""):
            code, doc, _err = _run_spawn(["-i", "x", "--reasoning", bad], self.env)
            self.assertEqual(code, 2, f"--reasoning {bad!r} must be exit 2: {doc}")
            self.assertIn("reasoning", doc.get("detail", doc.get("error", "")))
        main.stop()

    def test_reasoning_off_config_override_flips_the_default(self) -> None:
        # the cfg-level knob: task_reasoning_off: false makes the
        # default ON (the box opts into reasoning by default).
        rec = self._spawn_rec([], task_reasoning_off=False)
        self.assertFalse(
            rec.get("reasoning_off"),
            "the S3.6 task_reasoning_off override is honored at the record",
        )

    def test_reasoning_off_non_bool_config_is_exit_2(self) -> None:
        self._write_cfg()
        main = MockEngine(variant="ok")
        main.start()
        data = {
            "main_endpoint": f"http://127.0.0.1:{main.port}",
            "results_dir": str(self.results),
            "dedup_dir": str(self.health_dir / "dedup"),
            "dedup_ttl_h": 24,
            "queue_wait_max_s": 2,
            "workspace_dir": str(self.tmp / "workspaces"),
            "task_reasoning_off": 2,  # a non-bool value (YAML 1.1 parses
            # `yes` as a real True, so the non-bool must stay a real int)
        }
        self.cfg.write_text("\n".join(f"{k}: {v}" for k, v in data.items()) + "\n")
        self.env["BONSAI2_CONFIG"] = str(self.cfg)
        os.environ["BONSAI2_CONFIG"] = str(self.cfg)
        p0 = free_port()
        self._write_health([self._inst(0, p0)])
        code, _doc, _err = _run_spawn(["-i", "x"], self.env)
        self.assertEqual(
            code, 2, "a non-bool task_reasoning_off must be a ConfigError -> exit 2"
        )
        main.stop()


# ======================================================================
# reasoning_effort on the wire (tool-loop + pre-loop bodies)
# ======================================================================


class TestReasoningOffWire(_Base):
    """The completion bodies carry `reasoning_effort: "none"` when the
    record has reasoning_off and omit it for a record without the key
    (the first-run F1 byte-identical-wire discipline, applied to the
    new key)."""

    def _spawn_and_wait(
        self, responses: list, argv_extra: list[str] | None = None
    ) -> tuple[dict, ToolEngine]:
        self._write_cfg()
        main = MockEngine(variant="ok")
        main.start()
        data = {
            "main_endpoint": f"http://127.0.0.1:{main.port}",
            "results_dir": str(self.results),
            "dedup_dir": str(self.health_dir / "dedup"),
            "dedup_ttl_h": 24,
            "queue_wait_max_s": 2,
            "workspace_dir": str(self.tmp / "workspaces"),
        }
        self.cfg.write_text("\n".join(f"{k}: {v}" for k, v in data.items()) + "\n")
        self.env["BONSAI2_CONFIG"] = str(self.cfg)
        os.environ["BONSAI2_CONFIG"] = str(self.cfg)
        eng = ToolEngine(responses=list(responses))
        eng.start()
        try:
            self._write_health([self._inst(0, eng.port)])
            argv = ["-i", f"wire task {os.urandom(4).hex()}", "--deadline", "20"]
            argv += list(argv_extra or [])
            code, doc, _err = _run_spawn(argv, self.env)
            self.assertEqual(code, 0, f"spawn must be accepted: {doc}")
            d = self._results()
            H.ensure_results_dirs(d)
            rec = self._wait_state(d, doc["task_id"], ("done", "failed", "timeout"))
            return rec, eng
        finally:
            eng.stop()
            main.stop()

    def test_tool_loop_body_carries_reasoning_effort_none_by_default(self) -> None:
        rec, eng = self._spawn_and_wait(
            [
                {"kind": "tool", "tool": "bash", "params": {"cmd": "echo ok"}},
                {"kind": "final", "content": "all done"},
            ]
        )
        self.assertEqual(rec["state"], "done")
        self.assertTrue(rec.get("reasoning_off"), "the record carries the default")
        self.assertGreaterEqual(len(eng.request_log), 2)
        for i, body in enumerate(eng.request_log[:2]):
            self.assertEqual(
                body.get("reasoning_effort"),
                "none",
                f"second-run: completion {i} carries reasoning_effort "
                f'"none" by default',
            )

    def test_reasoning_on_spawn_omits_reasoning_effort(self) -> None:
        rec, eng = self._spawn_and_wait(
            [
                {"kind": "final", "content": "done"},
            ],
            ["--reasoning", "on"],
        )
        self.assertEqual(rec["state"], "done")
        self.assertFalse(rec.get("reasoning_off"))
        self.assertNotIn(
            "reasoning_effort",
            eng.request_log[0],
            "second-run: --reasoning on is the logged exception — the body "
            "OMITS the key (pre-second-run wire shape for this task)",
        )

    def test_legacy_record_without_reasoning_off_keeps_legacy_wire_shape(self) -> None:
        # a record WITHOUT the key (a pre-second-run record on disk)
        # must produce a tool-loop body WITHOUT reasoning_effort at all
        # (the F1 line-473 discipline: no null, no default).
        self._write_cfg()
        d = self._results()
        H.ensure_results_dirs(d)
        rec = {
            "task_id": "t-legacy-reasoning",
            "prompt": "legacy prompt",
            "result_file": str(self.results / "t-legacy-reasoning.json"),
            "state": "running",
            "tools": "on",
        }
        H.write_task(d, rec)
        with mock.patch.object(
            W,
            "_stream_chat",
            return_value={
                "ok": True,
                "answer": "legacy done",
                "reasoning": "",
                "tool_call": None,
                "usage": None,
                "finish_reason": "stop",
                "cmpl_id": None,
            },
        ) as mchat:
            W._run_tool_loop(rec, 30101, {"alias": "a", "api_key": "k"}, d, {})
        bodies = [c.args[2] for c in mchat.call_args_list]
        self.assertEqual(len(bodies), 1)
        self.assertNotIn(
            "reasoning_effort",
            bodies[0],
            "legacy: a record without reasoning_off must OMIT the key "
            "from the tool-loop body (the byte-identical payload)",
        )

    def test_record_with_reasoning_off_carries_it_in_tool_loop_body(self) -> None:
        self._write_cfg()
        d = self._results()
        H.ensure_results_dirs(d)
        rec = {
            "task_id": "t-off-wire",
            "prompt": "off prompt",
            "result_file": str(self.results / "t-off-wire.json"),
            "state": "running",
            "tools": "on",
            "reasoning_off": True,
        }
        H.write_task(d, rec)
        with mock.patch.object(
            W,
            "_stream_chat",
            return_value={
                "ok": True,
                "answer": "done",
                "reasoning": "",
                "tool_call": None,
                "usage": None,
                "finish_reason": "stop",
                "cmpl_id": None,
            },
        ) as mchat:
            W._run_tool_loop(rec, 30101, {"alias": "a", "api_key": "k"}, d, {})
        bodies = [c.args[2] for c in mchat.call_args_list]
        self.assertEqual(
            bodies[0].get("reasoning_effort"),
            "none",
            "a record that carries reasoning_off must put it on the wire",
        )

    def test_preloop_body_carries_reasoning_effort_when_record_arms_it(self) -> None:
        # the --tools off path (the PONG class) gets reasoning-off too.
        self._write_cfg()
        rec = {
            "task_id": "t-preloop-off",
            "prompt": "pong prompt",
            "result_file": str(self.results / "t-preloop-off.json"),
            "state": "running",
            "tools": "off",
            "reasoning_off": True,
        }
        with mock.patch.object(
            W,
            "_stream_chat",
            return_value={
                "ok": True,
                "answer": "PONG",
                "reasoning": "",
                "tool_call": None,
                "usage": None,
                "finish_reason": "stop",
                "cmpl_id": None,
            },
        ) as mchat:
            W._run_attempt(rec, 30101, {"alias": "a", "api_key": "k"})
        body = mchat.call_args_list[0].args[2]
        self.assertEqual(
            body.get("reasoning_effort"),
            "none",
            "the pre-loop body carries reasoning_effort when the record arms it",
        )

    def test_preloop_body_omits_reasoning_effort_for_a_legacy_record(self) -> None:
        self._write_cfg()
        rec = {
            "task_id": "t-preloop-legacy",
            "prompt": "pong prompt",
            "result_file": str(self.results / "t-preloop-legacy.json"),
            "state": "running",
            "tools": "off",
        }
        with mock.patch.object(
            W,
            "_stream_chat",
            return_value={
                "ok": True,
                "answer": "PONG",
                "reasoning": "",
                "tool_call": None,
                "usage": None,
                "finish_reason": "stop",
                "cmpl_id": None,
            },
        ) as mchat:
            W._run_attempt(rec, 30101, {"alias": "a", "api_key": "k"})
        body = mchat.call_args_list[0].args[2]
        self.assertNotIn("reasoning_effort", body, "legacy record -> legacy wire")


# ======================================================================
# the F2 nudge is BOUNDED (ERR_TRUNCATED_NO_PROGRESS)
# ======================================================================


class TestBoundedNudge(_Base):
    """Two consecutive cap-cut empty completions fail the task
    ERR_TRUNCATED_NO_PROGRESS (collect 21) with the F4 salvage; one
    empty cap-cut still gets the nudge (the F2 contract, unchanged);
    the streak resets on a content-carrying completion."""

    def _spawn_and_wait(
        self, responses: list, argv_extra: list[str] | None = None
    ) -> tuple[dict, ToolEngine, str]:
        self._write_cfg()
        main = MockEngine(variant="ok")
        main.start()
        data = {
            "main_endpoint": f"http://127.0.0.1:{main.port}",
            "results_dir": str(self.results),
            "dedup_dir": str(self.health_dir / "dedup"),
            "dedup_ttl_h": 24,
            "queue_wait_max_s": 2,
            "workspace_dir": str(self.tmp / "workspaces"),
        }
        self.cfg.write_text("\n".join(f"{k}: {v}" for k, v in data.items()) + "\n")
        self.env["BONSAI2_CONFIG"] = str(self.cfg)
        os.environ["BONSAI2_CONFIG"] = str(self.cfg)
        eng = ToolEngine(responses=list(responses))
        eng.start()
        try:
            self._write_health([self._inst(0, eng.port)])
            argv = ["-i", f"nudge task {os.urandom(4).hex()}", "--deadline", "20"]
            argv += list(argv_extra or [])
            code, doc, _err = _run_spawn(argv, self.env)
            self.assertEqual(code, 0, f"spawn must be accepted: {doc}")
            d = self._results()
            H.ensure_results_dirs(d)
            rec = self._wait_state(d, doc["task_id"], ("done", "failed", "timeout"))
            return rec, eng, doc["task_id"]
        finally:
            eng.stop()
            main.stop()

    def test_one_empty_cap_cut_still_gets_the_nudge(self) -> None:
        # the F2 contract is unchanged for the FIRST empty cap-cut: one
        # nudge, then the loop continues (a real final lands).
        rec, eng, _tid = self._spawn_and_wait(
            [
                {
                    "kind": "final",
                    "content": "",
                    "reasoning": "the reasoning that was cut",
                    "finish_reason": "length",
                },
                {"kind": "final", "content": "done-for-real"},
            ]
        )
        self.assertEqual(rec["state"], "done")
        self.assertGreaterEqual(
            len(eng.request_log), 2, "one nudge was fed; the loop continued"
        )
        # the nudge message now names the verify step (second-run).
        second_msgs = eng.request_log[1].get("messages", [])
        user_texts = [
            m.get("content", "") for m in second_msgs if m.get("role") == "user"
        ]
        self.assertTrue(
            any("cut off" in t for t in user_texts),
            f"the F2 nudge is present: {user_texts}",
        )
        self.assertTrue(
            any("definition of done" in t for t in user_texts),
            "second-run: the nudge names the verify step (run your DoD "
            "test, report the exit code, fix + re-run)",
        )

    def test_two_consecutive_empty_cap_cuts_fail_truncated_no_progress(self) -> None:
        rec, eng, tid = self._spawn_and_wait(
            [
                {
                    "kind": "final",
                    "content": "",
                    "reasoning": "first cut",
                    "finish_reason": "length",
                },
                {
                    "kind": "final",
                    "content": "",
                    "reasoning": "second cut",
                    "finish_reason": "length",
                },
            ]
        )
        self.assertEqual(
            rec["state"],
            "failed",
            "two consecutive empty cap-cuts must fail, not nudge a third time",
        )
        self.assertEqual(
            rec.get("error"),
            "ERR_TRUNCATED_NO_PROGRESS",
            "the new task-level error name (collect maps failed -> 21)",
        )
        self.assertEqual(
            len(eng.request_log),
            2,
            "the loop issued exactly two completions — no third nudge",
        )
        # the F4 salvage ran: the accumulated reasoning (two cuts' worth)
        # is tagged partial:true in the result file.
        rdata = json.loads((self.results / f"{tid}.json").read_text())
        self.assertTrue(rdata.get("partial"), "the salvage tagged the result partial")
        # the salvage writes the LATEST completion's accumulated text — the
        # second cut's reasoning (the first cut's was fed back as a nudge,
        # not accumulated onto the record).
        self.assertIn("second cut", rdata.get("reasoning") or "")

    def test_streak_resets_on_a_content_carrying_completion(self) -> None:
        # cut, then a content-carrying length-cut (trusted final, the
        # first-run F2 conservative half) -> done in two completions;
        # the streak never reached 2.
        rec, eng, _tid = self._spawn_and_wait(
            [
                {
                    "kind": "final",
                    "content": "",
                    "reasoning": "cut",
                    "finish_reason": "length",
                },
                {
                    "kind": "final",
                    "content": "visible partial answer",
                    "finish_reason": "length",
                },
            ]
        )
        self.assertEqual(rec["state"], "done")
        self.assertEqual(len(eng.request_log), 2)


# ======================================================================
# the worker self-cancel (the F11 gap proven live in the second run)
# ======================================================================


class TestWorkerSelfCancel(_Base):
    """The worker's OWN deadline exits issue the best-effort control
    POST for the latest in-flight completion id (gated on
    arm_reasoning_control + a live cmpl_id; fault-tolerant)."""

    def test_cancel_helper_posts_for_a_live_cmpl_id(self) -> None:
        eng = ToolEngine(responses=[])
        eng.start()
        eng.control_response = {"success": True}
        try:
            rec = {
                "task_id": "t-selfcancel-1",
                "arm_reasoning_control": True,
            }
            W._cancel_inflight_completion(rec, eng.port, "cmpl-self-1")
            self.assertEqual(len(eng.control_log), 1)
            self.assertEqual(eng.control_log[0].get("id"), "cmpl-self-1")
            self.assertEqual(eng.control_log[0].get("action"), "reasoning_end")
        finally:
            eng.stop()

    def test_cancel_helper_is_noop_without_a_cmpl_id(self) -> None:
        eng = ToolEngine(responses=[])
        eng.start()
        try:
            rec = {"task_id": "t-selfcancel-2", "arm_reasoning_control": True}
            W._cancel_inflight_completion(rec, eng.port, None)
            W._cancel_inflight_completion(rec, eng.port, "")
            self.assertEqual(eng.control_log, [], "no cmpl id -> no POST")
        finally:
            eng.stop()

    def test_cancel_helper_is_noop_when_disarmed(self) -> None:
        eng = ToolEngine(responses=[])
        eng.start()
        try:
            # arm_reasoning_control absent (legacy record) -> no POST:
            # the body that armed it was not sent, so a cancel is a
            # known no-op (success:false).
            rec = {"task_id": "t-selfcancel-3"}
            W._cancel_inflight_completion(rec, eng.port, "cmpl-self-3")
            self.assertEqual(eng.control_log, [], "disarmed -> no POST")
        finally:
            eng.stop()

    def test_cancel_helper_survives_a_dead_engine_port(self) -> None:
        # the fault-tolerance core: the engine is already refused — the
        # POST raises; the helper must swallow it (best-effort, never a
        # verdict).
        self._write_cfg()
        rec = {
            "task_id": "t-selfcancel-4",
            "arm_reasoning_control": True,
        }
        W._cancel_inflight_completion(rec, 1, "cmpl-self-4")  # port 1 = refused
        # a second call after the first proved the shape: still no raise.
        W._cancel_inflight_completion(rec, 1, "cmpl-self-4")

    def test_tool_loop_deadline_exit_cancels_the_tracked_completion(self) -> None:
        # the end-to-end shape the second run missed: the step's completion
        # (a 15-17K+ token reasoning bloat) lands AFTER the deadline -> the
        # worker's OWN late-result deadline exit fires: it salvages the
        # late reasoning (F4) AND issues the best-effort control POST for
        # THIS step's completion (the F11 gap — the manager reaper never
        # gets the chance when the worker's own deadline fires first).
        self._write_cfg()
        d = self._results()
        H.ensure_results_dirs(d)
        eng = ToolEngine(responses=[])
        eng.start()
        eng.control_response = {"success": True}
        try:
            rec = {
                "task_id": "t-selfcancel-5",
                "prompt": "deadline task",
                "result_file": str(self.results / "t-selfcancel-5.json"),
                "state": "running",
                "tools": "on",
                "arm_reasoning_control": True,
                "workdir": str(self.tmp / "wd"),
                "max_steps": 8,
                "deadline_s": 2.0,
                "port": eng.port,
                "instances_tried": [0],
            }
            (self.tmp / "wd").mkdir(parents=True, exist_ok=True)
            H.write_task(d, rec)
            with mock.patch.object(
                W,
                "_stream_chat",
                side_effect=_late_reasoning_stream,
            ):
                W._run_tool_loop(rec, eng.port, {"alias": "a", "api_key": "k"}, d, {})
            fresh = H.read_task(d, "t-selfcancel-5")
            self.assertEqual(fresh["state"], "timeout")
            self.assertEqual(
                len(eng.control_log),
                1,
                "the worker's own late-result deadline exit issued the control POST",
            )
            self.assertEqual(
                eng.control_log[0].get("id"),
                "cmpl-loop-5",
                "the cancel targeted this step's in-flight completion",
            )
            # the F4 salvage ran: the late reasoning is tagged partial:true.
            rdata = json.loads((self.results / "t-selfcancel-5.json").read_text())
            self.assertTrue(rdata.get("partial"))
            self.assertIn("late reasoning bloat", rdata.get("reasoning") or "")
        finally:
            eng.stop()


def _late_reasoning_stream(port: int, ident: dict, body: dict) -> dict:
    """The step's completion: block past the deadline, then land with the
    late reasoning + the tracked cmpl id (the second-run evidence shape)."""
    time.sleep(2.5)
    return {
        "ok": True,
        "answer": "",
        "reasoning": "late reasoning bloat",
        "tool_call": None,
        "usage": None,
        "finish_reason": "length",
        "cmpl_id": "cmpl-loop-5",
    }


# ======================================================================
# the progress probe (S3.2 --tasks; the F8-ordering mid-run signal)
# ======================================================================


class TestProgressProbe(_Base):
    """progress_report's fields + the bonsai2-status --tasks surface."""

    def _write_live_task(self, task_id: str, **over) -> dict:
        self._write_cfg()
        d = self._results()
        H.ensure_results_dirs(d)
        now = time.time()
        rec = {
            "task_id": task_id,
            "state": "running",
            "instance": 0,
            "port": 30101,
            "prompt": "p",
            "tools": "on",
            "created_ts": now - 100,
            "running_ts": now - 60,
            "deadline_s": 300,
            "worker_pid": os.getpid(),
            "result_file": str(self.results / f"{task_id}.json"),
            "instances_tried": [0],
            "updated_ts": now - 60,
        }
        rec.update(over)
        H.write_task(d, rec)
        return d

    def test_progress_report_fields(self) -> None:
        d = self._write_live_task("t-prog-1", steps=3, tool_log=[{}, {}, {}])
        W._write_cmpl_sidecar(d, {"task_id": "t-prog-1"}, 30101, "cmpl-prog-1")
        rows = H.progress_report(d)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["task_id"], "t-prog-1")
        self.assertEqual(row["state"], "running")
        self.assertEqual(row["steps"], 3)
        self.assertEqual(row["tool_calls"], 3)
        self.assertEqual(row["deadline_s"], 300)
        # deadline_remaining_s = running_ts + deadline_s - now ≈ 300 - 60
        self.assertIsNotNone(row["deadline_remaining_s"])
        self.assertTrue(200 < row["deadline_remaining_s"] < 260)
        self.assertEqual(
            row["cmpl_id"], "cmpl-prog-1", "the sidecar's cmpl id is surfaced"
        )

    def test_progress_report_skips_terminal_tasks(self) -> None:
        self._write_live_task("t-prog-2")
        d = self._results()
        H.write_task(
            d,
            {
                "task_id": "t-prog-done",
                "state": "done",
                "result_file": str(self.results / "t-prog-done.json"),
            },
        )
        rows = H.progress_report(d)
        self.assertEqual([r["task_id"] for r in rows], ["t-prog-2"])

    def test_progress_report_task_ids_filter(self) -> None:
        self._write_live_task("t-prog-3")
        d = self._results()
        H.write_task(
            d,
            {
                "task_id": "t-prog-4",
                "state": "running",
                "result_file": str(self.results / "t-prog-4.json"),
            },
        )
        rows = H.progress_report(d, task_ids=["t-prog-4"])
        self.assertEqual([r["task_id"] for r in rows], ["t-prog-4"])

    def test_progress_report_missing_sidecar_is_none_cmpl(self) -> None:
        d = self._write_live_task("t-prog-5")
        row = H.progress_report(d)[0]
        self.assertIsNone(row["cmpl_id"], "no sidecar -> cmpl_id None")

    def test_status_tasks_emits_probe_json_without_health(self) -> None:
        # the probe needs no health.json (works while the manager is
        # down) and no main-endpoint probe: exit 0, {"tasks": [...]}.
        self._write_live_task("t-prog-6")
        code, out, _err = _run_cmd(H.cmd_status, ["--tasks"], self.env)
        self.assertEqual(code, 0, f"--tasks must be exit 0: {out}")
        self.assertEqual(out.get("tasks", [{}])[0].get("task_id"), "t-prog-6")

    def test_status_tasks_with_explicit_ids(self) -> None:
        self._write_live_task("t-prog-7")
        code, out, _err = _run_cmd(H.cmd_status, ["--tasks", "t-prog-7"], self.env)
        self.assertEqual(code, 0)
        self.assertEqual(out["tasks"][0]["task_id"], "t-prog-7")

    def test_status_unknown_flag_is_exit_2(self) -> None:
        self._write_cfg()
        code, out, _err = _run_cmd(H.cmd_status, ["--bogus"], self.env)
        self.assertEqual(code, 2, "an unknown flag stays exit 2 (ERR_ARGS)")
        self.assertEqual(out.get("error"), "ERR_ARGS")

    def test_status_tasks_double_flag_is_exit_2(self) -> None:
        self._write_cfg()
        code, _out, _err = _run_cmd(H.cmd_status, ["--tasks", "a", "--tasks"], self.env)
        self.assertEqual(code, 2, "--tasks twice is malformed -> exit 2")


# ======================================================================
# the manager reaper now passes cfg (the cancel_on_deadline gate)
# ======================================================================


class TestManagerReaperCfg(_Base):
    """reap_tasks_once loads the harness config: a cfg with
    cancel_on_deadline: false is honored on the manager path (no
    control POST); the default path still cancels."""

    def _deadline_record(self, task_id: str, port: int) -> dict:
        # the manager's reaper reads only BONSAI2_RESULTS_DIR (the
        # tasks dir must exist) + BONSAI2_CONFIG (load_config); no
        # health.json required on the reaper path.
        d = H.results_dirs(
            {
                "results_dir": str(self.results),
            }
        )
        H.ensure_results_dirs(d)
        now = time.time()
        rec = {
            "task_id": task_id,
            "state": "running",
            "port": port,
            "instance": 0,
            "prompt": "p",
            "tools": "on",
            "created_ts": now - 100,
            "running_ts": now - 2000,  # well past deadline_s + grace
            "deadline_s": 60,
            "worker_pid": os.getpid(),  # ALIVE -> the deadline backstop
            "result_file": str(self.results / f"{task_id}.json"),
            "instances_tried": [0],
            "updated_ts": now - 2000,
        }
        H.write_task(d, rec)
        return d

    def test_manager_reaper_honors_cancel_on_deadline_false(self) -> None:
        eng = ToolEngine(responses=[])
        eng.start()
        eng.control_response = {"success": True}
        try:
            d = self._deadline_record("t-mgr-cfg-1", eng.port)
            W._write_cmpl_sidecar(d, {"task_id": "t-mgr-cfg-1"}, eng.port, "cmpl-mgr-1")
            # the manager reads cfg via BONSAI2_CONFIG — set it to a
            # config that opts OUT of the cancel.
            data = {
                "results_dir": str(self.results),
                "dedup_dir": str(self.health_dir / "dedup"),
                "dedup_ttl_h": 24,
                "workspace_dir": str(self.tmp / "workspaces"),
                "cancel_on_deadline": False,
            }
            self.cfg.write_text("\n".join(f"{k}: {v}" for k, v in data.items()) + "\n")
            os.environ["BONSAI2_RESULTS_DIR"] = str(self.results)
            os.environ["BONSAI2_CONFIG"] = str(self.cfg)
            os.environ["BONSAI2_ENGINE_YAML"] = str(ENGINE_YAML)
            mgr = _load_manager()
            reaped = mgr.reap_tasks_once(log=lambda m: None)
            self.assertEqual(reaped, ["t-mgr-cfg-1"], "the reaping still happened")
            self.assertEqual(
                eng.control_log,
                [],
                "second-run: cancel_on_deadline:false in the config the "
                "manager loads is honored (no control POST)",
            )
        finally:
            eng.stop()

    def test_manager_reaper_default_path_still_cancels(self) -> None:
        eng = ToolEngine(responses=[])
        eng.start()
        eng.control_response = {"success": True}
        try:
            d = self._deadline_record("t-mgr-cfg-2", eng.port)
            W._write_cmpl_sidecar(d, {"task_id": "t-mgr-cfg-2"}, eng.port, "cmpl-mgr-2")
            # no cancel_on_deadline in the cfg -> the default-True gate.
            data = {
                "results_dir": str(self.results),
                "dedup_dir": str(self.health_dir / "dedup"),
                "dedup_ttl_h": 24,
                "workspace_dir": str(self.tmp / "workspaces"),
            }
            self.cfg.write_text("\n".join(f"{k}: {v}" for k, v in data.items()) + "\n")
            os.environ["BONSAI2_RESULTS_DIR"] = str(self.results)
            os.environ["BONSAI2_CONFIG"] = str(self.cfg)
            os.environ["BONSAI2_ENGINE_YAML"] = str(ENGINE_YAML)
            mgr = _load_manager()
            reaped = mgr.reap_tasks_once(log=lambda m: None)
            self.assertEqual(reaped, ["t-mgr-cfg-2"])
            self.assertEqual(
                len(eng.control_log),
                1,
                "the default (cancel_on_deadline on) manager path still cancels",
            )
            self.assertEqual(eng.control_log[0].get("id"), "cmpl-mgr-2")
        finally:
            eng.stop()


# ======================================================================
# the install chowns workspaces/ like results/ (INFRA DEFECT #2)
# ======================================================================


class TestInstallChownsWorkspaces(_Base):
    """_make_dirs chowns BOTH user-scope subtrees (results/ AND
    workspaces/) to the invoking uid — the second run's PONG probe
    EACCES'd on /run/bonsai2/workspaces with the results-only chown."""

    def test_make_dirs_chowns_results_and_workspaces(self) -> None:
        inst = _load_installer()
        health_dir = self.tmp / "run"
        inst._make_dirs(health_dir, invoke_uid=1000)  # must not raise
        for sub in ("results", "dedup", "bench", "workspaces", "control"):
            self.assertTrue((health_dir / sub).is_dir())
        if os.geteuid() == 0:
            # the chown-landed assertions (root): BOTH user-scope
            # subtrees carry the invoking uid.
            self.assertEqual(os.stat(health_dir / "results").st_uid, 1000)
            self.assertEqual(
                os.stat(health_dir / "workspaces").st_uid,
                1000,
                "second-run: workspaces/ is chowned like results/ (INFRA DEFECT #2)",
            )
            self.assertEqual(
                os.stat(health_dir / "dedup").st_uid, 0
            )  # dedup stays root (manager scope)
            self.assertEqual(
                os.stat(health_dir / "bench").st_uid, 0
            )  # bench stays root (manager scope)
            self.assertEqual(
                os.stat(health_dir / "control").st_uid, 0
            )  # control stays root (manager scope)


if __name__ == "__main__":
    unittest.main()
