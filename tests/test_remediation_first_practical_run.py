"""First-practical-run remediation (F1-F12, 2026-10-03) — hermetic cases.

Companion to remediation-report-first-practical-run.md +
evidence-ledger-for-first-practical-run.md (both in-repo). The 2026-10-03
live run lost 3 of 4 code-gen tasks to unbounded pre-tool reasoning
(9-21K reasoning tokens in the FIRST completion, no max_tokens on the
wire, a deadline shorter than the whole tool loop, no partial artifact
on timeout, orphaned engine completions after a reap, a boot
self-deadlock, and installer hygiene bugs). This module covers the
hermetic half of the DOD:

  * F1/F3a — the spawn record carries max_tokens (the S3.6 default
    16384, the --max-tokens override, the 1-32768 range gate -> exit 2)
    and the F3a tool-loop-aware DEFAULT deadline (tools on -> the
    task_default_deadline_s_tools_on default; tools off -> the bare
    task_default_deadline_s; an explicit --deadline always wins).
  * F1/F2/F11 — the tool-loop completion body carries max_tokens +
    reasoning_control (the F11 arm); a completion with
    finish_reason="length" + no answer content + no tool call is a
    TRUNCATION, not a final answer (the F2 guard feeds a nudge, the
    task does not false-succeed).
  * F4 — _salvage_partial_result writes the result file tagged
    partial:true with the accumulated answer/reasoning; a task whose
    completion lands after the deadline (the late-result path) carries
    the salvage on collect; the reaper's backstop reads the partial
    onto the record when the worker itself is gone.
  * F11 — _write_cmpl_sidecar / _read_cmpl_sidecar round-trip; the
    reaper's deadline pass issues the engine's control POST
    ({id, action: reasoning_end}) to the task's port, and a FAILED
    cancel never kills the reaper pass (fault tolerance).
  * F9 — systemctl_start_instance emits `systemctl start --no-block`
    (the boot self-deadlock fix) in its argv.
  * F10 — the rendered instance unit carries CPUQuota=64% (single);
    _deploy_bin_tree lands the executable set +x with a box-python
    shebang; _make_dirs chowns the results subtree to the invoking
    user; the manager unit template carries TimeoutStartSec=300.

Run: `python3 -m unittest discover -s . -t .` from the repo root
(the full-suite invocation; 730 tests at clean main). Absolute
import-of-fixture form (top-level discovery sees no package), so the
module ALSO loads under `discover -s tests -t .`.

Hermetic: stdlib-only top-level imports (the suite's AST rule); the
fixture engines are the in-repo ToolEngine / MockEngine.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import io
import json
import os
import stat
import tempfile
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
BIN_DIR = REPO_ROOT / "bin"
ENGINE_YAML = REPO_ROOT / "etc" / "subagent-engine.yaml"

from .fixtures.mock_engine import DeadMainEndpoint, MockEngine, free_port
from .fixtures.tool_engine import ToolEngine


def _load_module(name: str, path: Path):
    """Load a bin/ tool as a module. The module must be registered in
    sys.modules BEFORE exec: bonsai2_install.py is `from __future__
    import annotations` + @dataclass, which resolves string annotations
    via sys.modules[cls.__module__] at class-creation time (the same
    convention as tests/test_install_e2e.py)."""
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


def _load_taskworker(name: str = "bonsai2_taskworker_remediation_test"):
    """The worker as a separate module (same convention as
    tests/test_harness_spawn.py — the worker imports the harness as
    'bonsai2_harness'; a distinct name keeps discovery from colliding
    with other modules that load it under their own name)."""
    return _load_module(name, BIN_DIR / "bonsai2-taskworker.py")


def _load_installer(name: str = "bonsai2_install_remediation_test"):
    return _load_module(name, BIN_DIR / "bonsai2_install.py")


def _load_manager(name: str = "bonsai2_manager_remediation_test"):
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
    """The shared rig: temp tree, the S3.2 env overrides (merged into
    os.environ so any in-process / spawned code sees them), the engine
    YAML fixture pointed at the repo's etc/ (alias + api_key)."""

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
        self._saved_sudo_uid = os.environ.get("SUDO_UID")

    def tearDown(self) -> None:
        for k in self._saved_env:
            if k == "SUDO_UID":
                if self._saved_sudo_uid is None:
                    os.environ.pop("SUDO_UID", None)
                else:
                    os.environ["SUDO_UID"] = self._saved_sudo_uid
                continue
            v = self._saved_env[k]
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self._tmp.cleanup()

    # ------------------------------------------------------------------
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
# F1/F3a — the spawn record: max_tokens + the tool-loop-aware deadline
# ======================================================================


class TestSpawnRecordRemediation(_Base):
    """F1 (max_tokens on the record) + F3a (the F3a tool-loop-aware
    default deadline) + the --max-tokens range gate (exit 2)."""

    def _spawn_rec(self, argv_extra: list[str], **cfg_over) -> dict:

        self._write_cfg(**cfg_over)
        main = MockEngine(variant="ok")
        main.start()
        # rewrite the main endpoint now that the fixture has a real port
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
        argv = ["-i", f"mt task {os.urandom(4).hex()}"] + argv_extra
        code, doc, _err = _run_spawn(argv, self.env)
        self.assertEqual(code, 0, f"spawn must be accepted: {doc}")
        main.stop()
        d = self._results()
        H.ensure_results_dirs(d)
        return H.read_task(d, doc["task_id"])

    def test_max_tokens_default_on_record(self) -> None:
        rec = self._spawn_rec([])
        self.assertEqual(
            rec["max_tokens"],
            16384,
            "F1: a record without --max-tokens carries the S3.6 task_default_max_tokens",
        )

    def test_max_tokens_flag_override_on_record(self) -> None:
        rec = self._spawn_rec(["--max-tokens", "4096"])
        self.assertEqual(
            rec["max_tokens"], 4096, "F1: --max-tokens N is carried on the record"
        )

    def test_max_tokens_out_of_range_low_is_exit_2(self) -> None:

        self._write_cfg()
        main = MockEngine(variant="ok")
        main.start()
        p0 = free_port()
        self._write_health([self._inst(0, p0)])
        for bad in ("0", "-1", "40000", "abc", "1.5"):
            code, doc, _err = _run_spawn(["-i", "x", "--max-tokens", bad], self.env)
            self.assertEqual(code, 2, f"--max-tokens {bad!r} must be exit 2: {doc}")
            self.assertIn("max-tokens", doc.get("detail", doc.get("error", "")))
        main.stop()

    def test_max_tokens_zero_leading_is_exit_2(self) -> None:

        self._write_cfg()
        main = MockEngine(variant="ok")
        main.start()
        p0 = free_port()
        self._write_health([self._inst(0, p0)])
        code, doc, _err = _run_spawn(["-i", "x", "--max-tokens", "007"], self.env)
        self.assertEqual(code, 2, f"'007' must be rejected (not a positive int): {doc}")
        main.stop()

    def test_tools_on_default_deadline_is_tools_on_default(self) -> None:
        # F3a: --tools on (the default) without --deadline -> the
        # task_default_deadline_s_tools_on default (1800 s default in the
        # CONFIG_DEFAULTS; the test cfg leaves it unset, so 1800).
        rec = self._spawn_rec([])
        self.assertEqual(
            rec["deadline_s"],
            1800.0,
            "F3a: a tools-on task without --deadline gets the tools-on default",
        )

    def test_tools_off_default_deadline_is_bare_default(self) -> None:
        # F3a: --tools off without --deadline -> the BARE
        # task_default_deadline_s (600 s default) — the off path keeps
        # its pre-remediation deadline.
        rec = self._spawn_rec(["--tools", "off"])
        self.assertEqual(
            rec["deadline_s"],
            600.0,
            "F3a: a tools-off task keeps task_default_deadline_s",
        )

    def test_explicit_deadline_wins_over_both_defaults(self) -> None:
        rec = self._spawn_rec(["--deadline", "777"])
        self.assertEqual(rec["deadline_s"], 777.0, "an explicit --deadline always wins")

    def test_tools_on_default_deadline_from_config_override(self) -> None:
        # the cfg-level knob: task_default_deadline_s_tools_on in the
        # harness.yaml is what a box tunes the tools-on default to.
        rec = self._spawn_rec([], task_default_deadline_s_tools_on=1234)
        self.assertEqual(rec["deadline_s"], 1234.0)


# ======================================================================
# F1/F2/F11 — the tool-loop wire body: max_tokens + reasoning_control +
# the F2 truncation guard
# ======================================================================


class TestToolLoopWireBody(_Base):
    """The tool-loop completion body carries max_tokens (F1) +
    reasoning_control (the F11 arm); F2: a completion cut at the cap
    with no answer content is a nudge, not a false-success."""

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

    def test_tool_loop_body_carries_max_tokens_and_reasoning_control(self) -> None:
        rec, eng = self._spawn_and_wait(
            [
                {"kind": "tool", "tool": "bash", "params": {"cmd": "echo ok"}},
                {"kind": "final", "content": "all done"},
            ]
        )
        self.assertEqual(rec["state"], "done")
        self.assertGreaterEqual(len(eng.request_log), 2)
        body0 = eng.request_log[0]
        self.assertEqual(
            body0.get("max_tokens"),
            16384,
            "F1: the tool-loop body carries the record's max_tokens (the default)",
        )
        self.assertIs(
            body0.get("reasoning_control"),
            True,
            "F11: arm_reasoning_control on the record (default) arms the body",
        )
        # the SECOND completion (the final-answer step) carries the same
        # caps — the cap is per-completion, not one-shot.
        body1 = eng.request_log[1]
        self.assertEqual(body1.get("max_tokens"), 16384)
        self.assertIs(body1.get("reasoning_control"), True)

    def test_f2_length_without_answer_is_nudge_not_done(self) -> None:
        # F2: the first completion is cut at max_tokens — the pin's
        # shape: finish_reason="length" with EMPTY content (the
        # reasoning bloat cut before it emitted any visible answer).
        # The guard must NOT mark done; it feeds a nudge and the loop
        # continues; the second completion (a real final) lands.
        rec, eng = self._spawn_and_wait(
            [
                {
                    "kind": "final",
                    "content": "",
                    "reasoning": "the 9-21K pre-tool reasoning that was cut",
                    "finish_reason": "length",
                },
                {"kind": "final", "content": "done-for-real"},
            ]
        )
        self.assertEqual(
            rec["state"], "done", "the nudged loop still lands on a real final"
        )
        self.assertGreaterEqual(
            len(eng.request_log),
            2,
            "F2: the truncation fed a nudge — a SECOND completion was issued",
        )
        # the nudge is the second request's user message.
        second_msgs = eng.request_log[1].get("messages", [])
        user_texts = [
            m.get("content", "") for m in second_msgs if m.get("role") == "user"
        ]
        self.assertTrue(
            any("cut off" in t for t in user_texts),
            f"F2: the nudge message is in the second request's history: {user_texts}",
        )
        # and the SECOND body still carries the caps (the guard did not
        # drop the F1/F11 wire fields).
        self.assertEqual(eng.request_log[1].get("max_tokens"), 16384)
        self.assertIs(eng.request_log[1].get("reasoning_control"), True)

    def test_f2_content_carrying_completion_is_trusted_final(self) -> None:
        # the conservative half of the F2 guard: a completion cut at the
        # cap that DID emit answer content is trusted as a final answer
        # (no nudge) — the guard only fires on EMPTY content. This keeps
        # the loop bounded (max_steps still caps it) and avoids the
        # infinite-nudge trap on a content-carrying truncation.
        rec, eng = self._spawn_and_wait(
            [
                {
                    "kind": "final",
                    "content": "partial but visible answer",
                    "finish_reason": "length",
                },
            ]
        )
        self.assertEqual(
            rec["state"],
            "done",
            "F2: a content-carrying length-truncation is trusted as final (one completion)",
        )
        self.assertEqual(
            len(eng.request_log),
            1,
            "F2: no nudge for a content-carrying truncation — exactly one completion",
        )

    def test_f1_legacy_record_without_max_tokens_keeps_legacy_wire_shape(self) -> None:
        # G3 PR#37 round-1 P2: the S3.4 F1 delta promises "a record
        # WITHOUT the key keeps the legacy byte-identical payload" for
        # BOTH the pre-loop completion and each tool-loop step. The
        # pre-loop path gates on isinstance(rec.get("max_tokens"), int);
        # the tool-loop path must not emit `"max_tokens": null` for a
        # pre-remediation record. Unit-level: a record with the key
        # stripped (as a pre-F1 record on disk) must produce a tool-loop
        # body WITHOUT the max_tokens key at all.
        self._write_cfg()
        d = self._results()
        H.ensure_results_dirs(d)
        rec = {
            "task_id": "t-legacy-wire",
            "prompt": "legacy prompt",
            "result_file": str(self.results / "t-legacy-wire.json"),
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
            "max_tokens",
            bodies[0],
            "F1 legacy: a record without the key must OMIT max_tokens from "
            "the tool-loop body (no null, no default) — the legacy "
            "byte-identical payload",
        )
        self.assertEqual(rec["state"], "done")

    def test_f1_record_with_max_tokens_carries_it_in_tool_loop_body(self) -> None:
        # the symmetric half: a record that DOES carry max_tokens (every
        # post-remediation record does) must still put it on the wire —
        # the gate must not silently swallow the cap.
        self._write_cfg()
        d = self._results()
        H.ensure_results_dirs(d)
        rec = {
            "task_id": "t-capped-wire",
            "prompt": "capped prompt",
            "result_file": str(self.results / "t-capped-wire.json"),
            "state": "running",
            "tools": "on",
            "max_tokens": 4096,
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
        self.assertEqual(len(bodies), 1)
        self.assertEqual(
            bodies[0].get("max_tokens"),
            4096,
            "F1: a record that carries max_tokens must put it on the wire",
        )


# ======================================================================
# F4 — partial-artifact salvage
# ======================================================================


class TestPartialSalvage(_Base):
    """F4: the worker's late-result salvage + the reaper's backstop +
    the collect surfacing."""

    def test_salvage_writes_partial_result_file(self) -> None:
        # unit level: _salvage_partial_result writes the result file
        # tagged partial:true with the accumulated answer/reasoning +
        # the capability fields (tool_log / steps / workdir / usage).
        rf = self.results / "t-salv.json"
        rec = {
            "task_id": "t-salv",
            "result_file": str(rf),
            "state": "running",
        }
        W._salvage_partial_result(
            rec,
            tool_log=[{"step": 0, "tool": "write_file", "exit_code": 0}],
            steps=1,
            workdir="/run/bonsai2/workspaces/t-salv",
            answer="half-written answer text",
            reasoning="the long in-flight reasoning that was cut",
            usage={"prompt_tokens": 10, "completion_tokens": 4096},
        )
        self.assertTrue(rf.is_file(), "the salvage wrote the result file")
        data = json.loads(rf.read_text())
        self.assertTrue(data.get("partial"), "the file is tagged partial:true")
        self.assertEqual(data["answer"], "half-written answer text")
        self.assertEqual(data["reasoning"], "the long in-flight reasoning that was cut")
        self.assertEqual(
            data["usage"], {"prompt_tokens": 10, "completion_tokens": 4096}
        )
        self.assertEqual(
            data["tool_log"], [{"step": 0, "tool": "write_file", "exit_code": 0}]
        )
        self.assertEqual(data["steps"], 1)
        self.assertEqual(data["workdir"], "/run/bonsai2/workspaces/t-salv")

    def test_salvage_is_noop_when_both_empty(self) -> None:
        rf = self.results / "t-empty.json"
        W._salvage_partial_result(
            {"task_id": "t-empty", "result_file": str(rf)},
            tool_log=[],
            steps=0,
            workdir=None,
            answer="",
            reasoning="",
            usage=None,
        )
        self.assertFalse(rf.exists(), "nothing to salvage -> the file is NOT written")

    def test_late_result_salvage_survives_on_collect(self) -> None:
        # round-trip: a --tools on task whose completion lands AFTER the
        # deadline (the late-result path) -> timeout ERR_DEADLINE with
        # the salvage on the result file; collect surfaces partial +
        # the partial answer/reasoning.

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
        eng = ToolEngine(responses=[{"kind": "final", "content": "late", "sleep": 6.0}])
        eng.start()
        try:
            self._write_health([self._inst(0, eng.port)])
            argv = [
                "-i",
                f"late task {os.urandom(4).hex()}",
                "--deadline",
                "3",
                "--max-steps",
                "2",
            ]
            code, doc, _err = _run_spawn(argv, self.env)
            self.assertEqual(code, 0, f"spawn must be accepted: {doc}")
            d = self._results()
            H.ensure_results_dirs(d)
            rec = self._wait_state(d, doc["task_id"], ("timeout",), timeout_s=15.0)
            self.assertEqual(rec["state"], "timeout")
            self.assertEqual(rec["error"], "ERR_DEADLINE")
            # the salvage is on the result file (the worker's own
            # late-result path wrote it BEFORE marking the record).
            rdata = json.loads(Path(rec["result_file"]).read_text())
            self.assertTrue(
                rdata.get("partial"), "F4: the late result was salvaged (partial:true)"
            )
            self.assertEqual(rdata.get("answer"), "late")
            # collect surfaces it.
            code_c, cdoc, _e = _run_cmd(H.cmd_collect, [doc["task_id"]], self.env)
            self.assertEqual(code_c, 22, "collect: timeout = 22")
            self.assertTrue(
                cdoc.get("partial"), "F4: collect surfaces the partial marker"
            )
            self.assertEqual(
                cdoc.get("answer"), "late", "F4: collect surfaces the salvaged answer"
            )
        finally:
            eng.stop()
            main.stop()

    def test_reaper_backstop_carries_partial_onto_record(self) -> None:
        # the reaper's F4 backstop: the worker is gone (pid dead) but the
        # result file already carries a partial (e.g. the worker wrote
        # it on its late-result path then died before persisting the
        # record) -> the reaper marks the record AND carries the partial
        # onto it; collect then returns the salvageable text.
        self._write_cfg()
        d = self._results()
        H.ensure_results_dirs(d)
        task_id = "t-backstop-1"
        rf = self.results / f"{task_id}.json"
        rf.parent.mkdir(parents=True, exist_ok=True)
        rf.write_text(
            json.dumps(
                {
                    "partial": True,
                    "answer": "salvaged by the worker before it died",
                    "reasoning": "the in-flight reasoning",
                    "tool_log": [],
                    "steps": 0,
                }
            )
            + "\n"
        )
        now = time.time()
        rec = {
            "task_id": task_id,
            "state": "running",
            "port": 30101,
            "instance": 0,
            "prompt": "p",
            "tools": "on",
            "created_ts": now - 1000,
            "running_ts": now - 2000,
            "deadline_s": 60,
            "worker_pid": 4194000,  # a pid that is (almost certainly) dead
            "result_file": str(rf),
            "instances_tried": [0],
            "updated_ts": now - 2000,
        }
        H.write_task(d, rec)
        reaped = H.reap_stale(d, log=lambda m: None, cfg=H.load_config(self.env))
        self.assertEqual(reaped, [task_id])
        got = H.read_task(d, task_id)
        self.assertEqual(got["state"], "timeout")
        self.assertEqual(got["error"], "ERR_DEADLINE")
        self.assertEqual(
            got.get("partial_answer"),
            "salvaged by the worker before it died",
            "F4: the reaper carried the partial answer onto the record",
        )
        self.assertEqual(got.get("partial_reasoning"), "the in-flight reasoning")
        # collect surfaces it.
        code_c, cdoc, _e = _run_cmd(H.cmd_collect, [task_id], self.env)
        self.assertEqual(code_c, 22)
        self.assertEqual(
            cdoc.get("partial_answer"), "salvaged by the worker before it died"
        )


# ======================================================================
# F11 — the cmpl sidecar round-trip + the reaper's orphan cancel
# ======================================================================


class TestOrphanCancel(_Base):
    """F11: the worker's cmpl sidecar + the reaper's best-effort engine
    control POST on a deadline reaping (fault-tolerant: a failed cancel
    never kills the reaper pass)."""

    def _make_deadline_record(self, task_id: str, port: int, **over) -> dict:
        self._write_cfg()
        d = self._results()
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
            "worker_pid": os.getpid(),  # ALIVE: the deadline (not pid) fires
            "result_file": str(self.results / f"{task_id}.json"),
            "instances_tried": [0],
            "updated_ts": now - 2000,
        }
        rec.update(over)
        H.write_task(d, rec)
        return d

    def test_sidecar_round_trip(self) -> None:
        self._write_cfg()
        d = self._results()
        H.ensure_results_dirs(d)
        rec = {"task_id": "t-side-1"}
        W._write_cmpl_sidecar(d, rec, 30101, "cmpl-abc-123")
        got = H._read_cmpl_sidecar(Path(d["tasks"]), "t-side-1")
        self.assertEqual(
            got, {"cmpl_id": "cmpl-abc-123", "port": 30101, "ts": got["ts"]}
        )
        # a missing sidecar -> None (the reaper treats it as a no-op).
        self.assertIsNone(H._read_cmpl_sidecar(Path(d["tasks"]), "t-missing"))
        # an invalid sidecar -> None (fault tolerance).
        (Path(d["tasks"]) / "t-bad.cmpl.json").write_text("not json")
        self.assertIsNone(H._read_cmpl_sidecar(Path(d["tasks"]), "t-bad"))
        # a None cmpl_id leaves the previous sidecar in place.
        W._write_cmpl_sidecar(d, {"task_id": "t-side-1"}, 30101, None)
        self.assertEqual(
            H._read_cmpl_sidecar(Path(d["tasks"]), "t-side-1")["cmpl_id"],
            "cmpl-abc-123",
        )

    def test_reaper_issues_control_post_on_deadline_reaping(self) -> None:

        eng = ToolEngine(responses=[])
        eng.start()
        eng.control_response = {"success": True}
        try:
            d = self._make_deadline_record("t-cancel-1", eng.port)
            W._write_cmpl_sidecar(
                d, {"task_id": "t-cancel-1"}, eng.port, "cmpl-orphan-1"
            )
            reaped = H.reap_stale(d, log=lambda m: None, cfg=H.load_config(self.env))
            self.assertEqual(reaped, ["t-cancel-1"])
            self.assertEqual(
                len(eng.control_log),
                1,
                "F11: the reaper issued exactly one control POST",
            )
            body = eng.control_log[0]
            self.assertEqual(
                body.get("id"),
                "cmpl-orphan-1",
                "the POST targets the sidecar's cmpl id",
            )
            self.assertEqual(
                body.get("action"),
                "reasoning_end",
                "the action is reasoning_end (the pin's control-endpoint verb)",
            )
        finally:
            eng.stop()

    def test_reaper_cancel_is_fault_tolerant_on_failed_cancel(self) -> None:

        eng = ToolEngine(responses=[])
        eng.start()
        eng.control_response = {
            "success": False,
            "error": "no active completion for this id",
        }
        try:
            d = self._make_deadline_record("t-cancel-2", eng.port)
            W._write_cmpl_sidecar(
                d, {"task_id": "t-cancel-2"}, eng.port, "cmpl-orphan-2"
            )
            reaped = H.reap_stale(d, log=lambda m: None, cfg=H.load_config(self.env))
            self.assertEqual(
                reaped, ["t-cancel-2"], "a failed cancel does not stop the reaping"
            )
            got = H.read_task(d, "t-cancel-2")
            self.assertEqual(got["state"], "timeout")
            self.assertEqual(len(eng.control_log), 1)
        finally:
            eng.stop()

    def test_reaper_cancel_is_noop_when_sidecar_missing(self) -> None:
        # no sidecar (the worker never recorded a cmpl id — a legacy /
        # mock engine that omits the SSE id) -> no control POST; the
        # reaping still happens.

        eng = ToolEngine(responses=[])
        eng.start()
        try:
            d = self._make_deadline_record("t-cancel-3", eng.port)
            reaped = H.reap_stale(d, log=lambda m: None, cfg=H.load_config(self.env))
            self.assertEqual(reaped, ["t-cancel-3"])
            self.assertEqual(eng.control_log, [], "no sidecar -> no control POST")
        finally:
            eng.stop()

    def test_reaper_cancel_is_gated_on_cancel_on_deadline(self) -> None:
        # cfg cancel_on_deadline: false -> bookkeeping-only reaping (the
        # pre-remediation behavior, for a box that opts out).

        eng = ToolEngine(responses=[])
        eng.start()
        try:
            d = self._make_deadline_record("t-cancel-4", eng.port)
            W._write_cmpl_sidecar(
                d, {"task_id": "t-cancel-4"}, eng.port, "cmpl-orphan-4"
            )
            cfg = H.load_config(self.env)
            cfg["cancel_on_deadline"] = False
            reaped = H.reap_stale(d, log=lambda m: None, cfg=cfg)
            self.assertEqual(reaped, ["t-cancel-4"])
            self.assertEqual(eng.control_log, [], "gated off -> no control POST")
        finally:
            eng.stop()

    def test_reaper_cancel_survives_a_dead_engine_port(self) -> None:
        # the fault-tolerance core: the engine's port is already refused
        # (the instance died between the record's running_ts and the
        # reaping) -> the control POST raises URLError; the reaper must
        # swallow it (a failed cancel NEVER kills the reaper pass).

        dead = DeadMainEndpoint().start()  # a refused port, nothing on it
        d = self._make_deadline_record("t-cancel-5", dead.port)
        W._write_cmpl_sidecar(d, {"task_id": "t-cancel-5"}, dead.port, "cmpl-orphan-5")
        reaped = H.reap_stale(d, log=lambda m: None, cfg=H.load_config(self.env))
        self.assertEqual(
            reaped, ["t-cancel-5"], "the reaping landed despite the dead port"
        )
        got = H.read_task(d, "t-cancel-5")
        self.assertEqual(got["state"], "timeout")
        self.assertEqual(got["error"], "ERR_DEADLINE")


# ======================================================================
# F9 — the manager's non-blocking start (the boot self-deadlock)
# ======================================================================


class TestNoBlockStart(unittest.TestCase):
    """F9: systemctl_start_instance emits `systemctl start --no-block`
    in its argv (the boot self-deadlock fix — the manager is
    Type=notify and sends READY=1 only after spawn_sequence returns, so
    a BLOCKING start deadlocks against the instance units'
    After=bonsai2-sglang.service)."""

    def test_start_argv_carries_no_block(self) -> None:
        mgr = _load_manager()
        calls: list[list[str]] = []

        class _FakeCompleted:
            returncode = 0
            stderr = ""
            stdout = ""

        def fake_run(cmd, **kw):
            calls.append(list(cmd))
            return _FakeCompleted()

        orig = mgr.subprocess.run
        mgr.subprocess.run = fake_run
        try:
            rc = mgr.systemctl_start_instance(0)
            self.assertEqual(rc, 0)
            self.assertEqual(len(calls), 1)
            argv = calls[0]
            self.assertIn("start", argv)
            self.assertIn("--no-block", argv, "F9: the start is non-blocking")
            self.assertIn("bonsai2-server@0", argv)
            # the --user prefix (the @gpu convention) still lands when the
            # base carries it.
            calls.clear()
            rc = mgr.systemctl_start_instance(1, base=["systemctl", "--user"])
            self.assertEqual(rc, 0)
            self.assertEqual(calls[0][0], "systemctl")
            self.assertEqual(calls[0][1], "--user")
            self.assertIn("--no-block", calls[0])
            self.assertIn("bonsai2-server@1", calls[0])
        finally:
            mgr.subprocess.run = orig

    def test_manager_unit_template_carries_timeout_start_sec(self) -> None:
        text = (REPO_ROOT / "units" / "bonsai2-sglang.service").read_text()
        self.assertIn(
            "TimeoutStartSec=300",
            text,
            "F9: the manager unit gets a 300 s start budget",
        )
        self.assertIn("Type=notify", text)


# ======================================================================
# F10 — the installer hygiene (exec bits, shebang, ownership, CPUQuota)
# ======================================================================


class TestInstallHygiene(_Base):
    """F10: the installer lands the command surface +x with a
    box-python shebang, chowns the results subtree to the invoking
    user, and renders a valid CPUQuota."""

    def test_rendered_instance_unit_has_single_percent_cpu_quota(self) -> None:
        inst = _load_installer()
        unit = inst.render_instance_unit(
            0,
            budget_gb=14.78,
            ctx=262144,
            parallel=8,
            kv="q4_0",
            port_base=30101,
            image_tag="img",
            model_file="f.gguf",
            api_key="k",
            reasoning="deepseek",
            alias="a",
        )
        self.assertIn(
            "CPUQuota=64%\n",
            unit,
            "F10: the rendered unit carries CPUQuota=64% (single)",
        )
        self.assertNotIn(
            "CPUQuota=64%%", unit, "the render bug (double percent) is gone"
        )

    def test_deploy_bin_tree_exec_bits_and_shebang(self) -> None:
        inst = _load_installer()
        # a fake repo bin/ tree: the source wrappers are NOT +x and
        # carry the env-python shebang (the pre-remediation clone shape
        # that produced the ledger's EACCES / PyYAML-less-python
        # failures).
        repo = self.tmp / "repo"
        bin_src = repo / "bin"
        bin_src.mkdir(parents=True)
        bench_src = repo / "bench"
        bench_src.mkdir(parents=True)
        (bench_src / "run.py").write_text("# bench\n")
        wrapper_body = "import sys\nprint('cmd')\n"
        wrappers = [
            "bonsai2-manager",
            "bonsai2-status",
            "bonsai2-list-instances",
            "bonsai2-health",
            "bonsai2-spawn",
            "bonsai2-collect",
            "bonsai2-steer",
            "bonsai2-cancel",
            "bonsai2-unload",
            "bonsai2-reload",
            "bonsai2-bench",
        ]
        for name in wrappers:
            p = bin_src / name
            p.write_text("#!/usr/bin/env python3\n" + wrapper_body)
            p.chmod(0o644)  # the clone shape: NOT +x
        # a module file (imported, not executed) stays a non-wrapping
        # name in BIN_DEPLOY — it must NOT get a shebang rewrite
        # (it has no shebang to begin with) but is copied verbatim.
        (bin_src / "bonsai2_harness.py").write_text("# a module, no shebang\n")
        (bin_src / "bonsai2_harness.py").chmod(0o644)
        # a fake seams object: only repo_root matters here.
        seams = inst.Seams()
        seams.repo_root = repo
        opt = self.tmp / "opt"
        inst._deploy_bin_tree(seams, opt, python="/usr/bin/python3")
        bin_dst = opt / "bin"
        for name in wrappers:
            p = bin_dst / name
            self.assertTrue(p.is_file(), f"{name} deployed")
            self.assertEqual(
                stat.S_IMODE(p.stat().st_mode),
                0o755,
                f"F10: {name} lands +x (0o755)",
            )
            first = p.read_text().splitlines()[0]
            self.assertEqual(
                first,
                "#!/usr/bin/python3",
                f"F10: {name}'s shebang is rewritten to the box python: {first!r}",
            )
        # the module file: copied, no shebang invented.
        mod = bin_dst / "bonsai2_harness.py"
        self.assertTrue(mod.is_file())
        self.assertEqual(mod.read_text(), "# a module, no shebang\n")

    def test_deploy_bin_tree_is_idempotent_on_reinstall(self) -> None:
        # a re-install over an already-pinned tree must not double-
        # rewrite (the shebang stays a single #! line; the content after
        # it is untouched).
        inst = _load_installer()
        repo = self.tmp / "repo"
        bin_src = repo / "bin"
        bin_src.mkdir(parents=True)
        (repo / "bench").mkdir(parents=True)
        (repo / "bench" / "run.py").write_text("# bench\n")
        for name in ("bonsai2-manager", "bonsai2-status"):
            (bin_src / name).write_text("#!/usr/bin/env python3\nprint('x')\n")
        seams = inst.Seams()
        seams.repo_root = repo
        opt = self.tmp / "opt"
        inst._deploy_bin_tree(seams, opt, python="/usr/bin/python3")
        inst._deploy_bin_tree(seams, opt, python="/usr/bin/python3")  # re-run
        p = opt / "bin" / "bonsai2-manager"
        self.assertEqual(
            p.read_text(),
            "#!/usr/bin/python3\nprint('x')\n",
            "re-install is byte-stable",
        )

    def test_invoke_uid_reads_sudo_uid(self) -> None:
        inst = _load_installer()
        os.environ["SUDO_UID"] = "1000"
        self.assertEqual(inst._invoke_uid(), 1000)
        os.environ["SUDO_UID"] = "0"  # root invoker -> no chown needed
        self.assertIsNone(inst._invoke_uid())
        os.environ.pop("SUDO_UID")
        self.assertIsNone(inst._invoke_uid())

    def test_make_dirs_chowns_results_subtree(self) -> None:
        # F10.3: _make_dirs(health_dir, invoke_uid) chowns the results/
        # subtree to the invoking user so user-scope commands can mkdir
        # task dirs without EACCES. (os.chown needs root — the box runs
        # the installer under sudo; when the test is non-root, the
        # best-effort path swallows the ENOTPERM and the call still
        # returns cleanly: the assertion is on the no-throw contract +
        # the dir creation, and the chown-landed assertion only when
        # the process can actually chown.)
        inst = _load_installer()
        health_dir = self.tmp / "run"
        inst._make_dirs(health_dir, invoke_uid=1000)  # must not raise
        for sub in ("results", "dedup", "bench", "workspaces", "control"):
            self.assertTrue((health_dir / sub).is_dir())
        if os.geteuid() == 0:
            self.assertEqual(os.stat(health_dir / "results").st_uid, 1000)
            self.assertEqual(
                os.stat(health_dir / "dedup").st_uid, 0
            )  # dedup stays root
