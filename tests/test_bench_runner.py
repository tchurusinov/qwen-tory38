"""tests/test_bench_runner.py — the S5.8 mock-endpoint run + dogfood
(MAP 4.8) + the S5.4 runner exit-code contract.

S5.8: "Mock-endpoint run: full M1+M2 against the S3.7 mock (canned
streaming responses) -> rows produced, timings sane, zero network/GPU."
and "Dogfood: the bench runs M7 ... and asserts its own runner produced
a parseable JSON row."

This drives ``bench.run.main`` end-to-end with
``tests.fixtures.bench_mock:mock_exec`` (canned responses through
``bench.exec.score_stream`` — real timing math, no sockets, no GPU):

  * full M1+M2: exit 0, the stdout JSON is parseable, BOTH rows are
    written to the results dir and parse, every task carries sane
    timing (elapsed > 0, a decode window, content chunks), every row
    carries the S5.2 box-state snapshot (S5.9 #1), and BOTH rubric
    verdicts pass (the canned answers are rubric-correct by
    construction).
  * dogfood M7: the runner's own M7 row is written + parses + the M7
    rubric passes (0 5xx + a positive t/s from the canned streams).
  * M6 control on main: --spotcheck 1.0 makes every M1-M5 rubric pass
    on the main endpoint; the control row carries a positive main_tps
    (the M8 guardrail baseline).
  * M8 guardrail: with a canned control + a canned under-load main
    measurement both from the same stream shape, the drop is 0% -> the
    M8 row passes.
  * the S5.4 exit codes: bad config -> 2; a held BONSAI2_BENCH_LOCK ->
    2 ("bench already running"); a completed run -> 0 even with row
    failures.

Hermetic: tmp bench.yaml + tmp engine/harness YAMLs (the S3.6/S1.6
env-var path hooks) + tmp results/summary dirs; the pool is N=1 (the
MVP load-time condition). No network, no GPU.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from bench import run as bench_run
from bench.rotate import BenchLock

_REPO_ROOT = Path(__file__).resolve().parents[1]
MOCK_SPEC = "tests.fixtures.bench_mock:mock_exec"


def _write_bench_yaml(tmp: Path, scenarios: list[str], results_dir: Path) -> str:
    p = tmp / "bench.yaml"
    p.write_text(
        f"scenarios: [{', '.join(scenarios)}]\n"
        f"quick: [{', '.join(scenarios)}]\n"
        f"results_dir: {results_dir}\n"
        "goldens_dir: bench/goldens\n"
        "main_guardrail_pct: 5\n"
    )
    return str(p)


def _engine_yaml(tmp: Path, count: int = 1) -> str:
    p = tmp / "subagent-engine.yaml"
    p.write_text(
        f"alias: qwen38-27b-subagent\napi_key: bonsai2-local\n"
        f"instances:\n  count: {count}\n  port_base: 30101\n"
    )
    return str(p)


def _harness_yaml(tmp: Path) -> str:
    p = tmp / "harness.yaml"
    p.write_text("main_endpoint: http://localhost:30001\n")
    return str(p)


def _env(tmp: Path) -> dict:
    return {
        "BONSAI2_ENGINE_YAML": _engine_yaml(tmp),
        "BONSAI2_CONFIG": _harness_yaml(tmp),
    }


def _rows_for(results_dir: Path, sid: str) -> list[dict]:
    rows = []
    for p in results_dir.glob(f"*_{sid}.json"):
        rows.append(json.loads(p.read_text()))
    return rows


class MockRunTestBase(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)
        self.results = self.tmp / "results"
        self.summary = self.tmp / "summary"

    def tearDown(self):
        self._td.cleanup()

    def _run_main(self, extra_args: list[str], cfg_path: str, env: dict):
        with (
            mock.patch.dict(os.environ, env, clear=False),
            mock.patch("sys.stdout", new_callable=__import__("io").StringIO) as out,
        ):
            rc = bench_run.main(
                [
                    "--config",
                    cfg_path,
                    "--summary-dir",
                    str(self.summary),
                    *extra_args,
                ]
            )
        return rc, out.getvalue()


class MockM1M2RunTest(MockRunTestBase):
    """The S5.8 mock-endpoint run: full M1+M2, rows produced, timings
    sane, zero network/GPU."""

    def test_full_m1_m2_mock_run(self):
        env = _env(self.tmp)
        cfg = _write_bench_yaml(self.tmp, ["M1", "M2"], self.results)
        rc, stdout = self._run_main(["--mock", MOCK_SPEC], cfg, env)
        self.assertEqual(rc, 0, stdout)

        # the S3.1 one-JSON-on-stdout contract (adapted to the bench)
        summary = json.loads(stdout)
        self.assertEqual(summary["status"], "done")
        self.assertEqual(summary["n_scenarios"], 2)
        self.assertEqual(summary["verdicts"], {"M1": "pass", "M2": "pass"}, summary)

        # rows produced + parse, verdicts pass
        for sid in ("M1", "M2"):
            rows = _rows_for(self.results, sid)
            self.assertEqual(len(rows), 1, f"{sid} row missing")
            row = rows[0]
            self.assertEqual(row["scenario"], sid)
            self.assertEqual(row["row_verdict"], "pass")
            self.assertIn("pass", row["verdict"])
            self.assertGreaterEqual(row["verdict"]["score"], 0.0)
            self.assertLessEqual(row["verdict"]["score"], 1.0)
            # the S5.2 box-state snapshot: every row carries it (S5.9 #1)
            snap = row["box_state"]
            for key in (
                "nvidia_smi",
                "docker_stats",
                "meminfo",
                "driver",
                "date",
                "epoch",
            ):
                self.assertIn(key, snap, f"{sid} row missing snapshot field {key}")

        # timings sane (the S5.8 assertion): every task has a real
        # stream — content chunks, a positive decode t/s, elapsed > 0
        for sid in ("M1", "M2"):
            row = _rows_for(self.results, sid)[0]
            self.assertGreater(len(row["tasks"]), 0, sid)
            for t in row["tasks"]:
                self.assertTrue(t["ok"], t.get("detail"))
                self.assertGreater(t["n_content_chunks"], 0, sid)
                self.assertGreater(t["elapsed_ms"], 0.0, sid)
                self.assertIsNotNone(t["decode_tps"])
                self.assertGreater(
                    t["decode_tps"], 0.0, f"{sid} decode t/s must be > 0"
                )
                self.assertEqual(t["prompt_tokens"], 32)  # the canned usage

        # the M2 row: 10 tasks -> 10/10 files covered (the rubric extra)
        m2 = _rows_for(self.results, "M2")[0]
        self.assertEqual(m2["n_tasks"], 10)
        self.assertEqual(m2["verdict"]["extra"]["n_files"], 10)

        # the committed summary (the S5.2 "bench-results/" citation
        # source) was written and carries both scenarios
        md = list(self.summary.glob("*.md"))
        self.assertEqual(len(md), 1, "summary markdown not written")
        text = md[0].read_text()
        self.assertIn("| M1 |", text)
        self.assertIn("| M2 |", text)
        self.assertIn("box-state", text.lower())


class DogfoodM7Test(MockRunTestBase):
    """S5.8 dogfood: the bench runs M7 and asserts its own runner
    produced a parseable JSON row."""

    def test_dogfood_m7_row_parses(self):
        env = _env(self.tmp)
        cfg = _write_bench_yaml(self.tmp, ["M7"], self.results)
        rc, stdout = self._run_main(["--mock", MOCK_SPEC], cfg, env)
        self.assertEqual(rc, 0, stdout)
        rows = _rows_for(self.results, "M7")
        self.assertEqual(len(rows), 1, "the dogfood M7 row must exist + parse")
        row = rows[0]
        self.assertEqual(row["scenario"], "M7")
        self.assertEqual(row["row_verdict"], "pass")
        # N=1 pool -> 8*N = 8 tasks
        self.assertEqual(row["n_tasks"], 8)
        # the M7 rubric: t/s at the ceiling + zero 5xx
        extra = row["verdict"]["extra"]
        self.assertGreater(extra["aggregate_tps"], 0.0)
        self.assertEqual(extra["n_5xx"], 0)

    def test_dogfood_m8_guardrail_passes_on_mock(self):
        """The M8 contention row: the canned control + the canned
        under-load main measurement share the same stream shape -> 0%
        drop -> the guardrail passes (the S5.9 #3 shape, hermetic)."""
        env = _env(self.tmp)
        cfg = _write_bench_yaml(self.tmp, ["M8"], self.results)
        rc, stdout = self._run_main(["--mock", MOCK_SPEC], cfg, env)
        self.assertEqual(rc, 0, stdout)
        rows = _rows_for(self.results, "M8")
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["row_verdict"], "pass", row["verdict"])
        self.assertGreater(row["m6_baseline_tps"], 0.0)
        self.assertGreater(row["m8_main_tps"], 0.0)
        self.assertEqual(row["verdict"]["extra"]["pct_drop"], 0.0)


class M6ControlTest(MockRunTestBase):
    def test_m6_control_valid_on_mock(self):
        """M6 on main: --spotcheck 1.0 makes every M1-M5 rubric pass ->
        the control row is valid (main_tps > 0 + all shapes pass)."""
        env = _env(self.tmp)
        cfg = _write_bench_yaml(self.tmp, ["M6"], self.results)
        rc, stdout = self._run_main(
            ["--mock", MOCK_SPEC, "--spotcheck", "1.0"], cfg, env
        )
        self.assertEqual(rc, 0, stdout)
        rows = _rows_for(self.results, "M6")
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["row_verdict"], "pass", row["verdict"])
        self.assertGreater(row["main_tps"], 0.0)
        self.assertEqual(row["verdict"]["extra"]["all_pass"], True)
        self.assertEqual(set(row["per_shape"]), {"M1", "M2", "M3", "M4", "M5"})


class M9M10PendingTest(MockRunTestBase):
    def test_m9_m10_rows_run_with_pending_verdict(self):
        """MAP 4.9 lands the capability scorers; until then a selected
        M9/M10 row still RUNS its task (the t/s + box state are
        recorded) with a named pending verdict (the S5.4 'failure is
        data' rule)."""
        env = _env(self.tmp)
        cfg = _write_bench_yaml(self.tmp, ["M9", "M10"], self.results)
        rc, stdout = self._run_main(["--mock", MOCK_SPEC], cfg, env)
        self.assertEqual(rc, 0, stdout)
        for sid in ("M9", "M10"):
            rows = _rows_for(self.results, sid)
            self.assertEqual(len(rows), 1, sid)
            row = rows[0]
            self.assertEqual(row["row_verdict"], "fail")  # pending, not an error
            self.assertEqual(row["verdict"]["extra"]["pending"], "4.9")
            self.assertGreater(len(row["tasks"]), 0)
            self.assertTrue(row["tasks"][0]["ok"])
            self.assertIn("box_state", row)


class RunnerExitCodeTest(MockRunTestBase):
    def test_bad_config_exit_2(self):
        env = _env(self.tmp)
        p = self.tmp / "bad.yaml"
        p.write_text("scenarios: [M1, M11]\n")
        with (
            mock.patch.dict(os.environ, env, clear=False),
            mock.patch("sys.stderr", new_callable=__import__("io").StringIO),
        ):
            rc = bench_run.main(["--config", str(p)])
        self.assertEqual(rc, 2)

    def test_unknown_scenario_flag_exit_2(self):
        env = _env(self.tmp)
        cfg = _write_bench_yaml(self.tmp, ["M1"], self.results)
        with (
            mock.patch.dict(os.environ, env, clear=False),
            mock.patch("sys.stderr", new_callable=__import__("io").StringIO),
        ):
            rc = bench_run.main(["--config", cfg, "--scenario", "M99"])
        self.assertEqual(rc, 2)

    def test_concurrent_bench_exit_2(self):
        """The S3.5 flock (MAP 4.3): a held BONSAI2_BENCH_LOCK makes the
        runner exit 2 with the named 'bench already running' detail."""
        env = _env(self.tmp)
        cfg = _write_bench_yaml(self.tmp, ["M1"], self.results)
        lock_path = self.tmp / ".bench.lock"
        held = BenchLock(lock_path)
        self.assertTrue(held.acquire())
        try:
            with (
                mock.patch.dict(
                    os.environ,
                    {**env, "BONSAI2_BENCH_LOCK": str(lock_path)},
                    clear=False,
                ),
                mock.patch("sys.stderr", new_callable=__import__("io").StringIO) as err,
            ):
                rc = bench_run.main(["--config", cfg, "--mock", MOCK_SPEC])
            self.assertEqual(rc, 2)
            self.assertIn("bench already running", err.getvalue())
        finally:
            held.release()

    def test_exit_0_even_with_row_failures(self):
        """The S5.4 'exit 0 even on scenario-level failures' rule: a row
        that FAILS its rubric (the mock's M6 without a spot-check — M3's
        spot-check is 0.0 -> blend < 0.5) still completes the run with
        exit 0 and a parseable row."""
        env = _env(self.tmp)
        cfg = _write_bench_yaml(self.tmp, ["M6"], self.results)
        rc, stdout = self._run_main(["--mock", MOCK_SPEC], cfg, env)
        self.assertEqual(rc, 0, stdout)
        rows = _rows_for(self.results, "M6")
        self.assertEqual(len(rows), 1)
        # M3's spot-check is the S5.3 main-judgment seam: with no
        # --spotcheck the M6 control's M3 sub-verdict fails -> the
        # control row fails, but the run still exits 0.
        self.assertEqual(rows[0]["row_verdict"], "fail")
        self.assertEqual(rows[0]["per_shape"]["M3"], False)

    def test_runner_crash_surfaces_in_stdout_json(self):
        """The S5.4 closed set keeps bonsai2-bench in 0/2 (S5.9 #3), so an
        unexpected runner crash still exits 0 — but the S3.1 'one JSON
        object on stdout' must then carry status='crash' + the named
        error (G3 round-2 P2: a crash is not a successful bench run, and
        must not be silently reported as 'done')."""
        env = _env(self.tmp)
        cfg = _write_bench_yaml(self.tmp, ["M1"], self.results)
        with (
            mock.patch.dict(os.environ, env, clear=False),
            mock.patch.object(bench_run, "run", side_effect=RuntimeError("boom")),
            mock.patch("sys.stdout", new_callable=__import__("io").StringIO) as out,
            mock.patch("sys.stderr", new_callable=__import__("io").StringIO),
        ):
            rc = bench_run.main(["--config", cfg, "--mock", MOCK_SPEC])
        self.assertEqual(rc, 0)
        obj = json.loads(out.getvalue())
        self.assertEqual(obj["status"], "crash")
        self.assertIn("boom", obj["error"])


if __name__ == "__main__":
    unittest.main()
