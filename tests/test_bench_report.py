"""tests/test_bench_report.py — the S5.4 reporter (MAP 4.3).

Hermetic: builds rows from in-memory inputs and writes them to tmp dirs.

Asserts:
  * build_row carries the S5.2 box-state snapshot + the S5.3 verdict +
    the row verdict (pass/fail) + the commit pin.
  * write_row writes a parseable <ts>_<scenario>.json (atomic).
  * write_summary writes the bench-results/<date>_<commit>.md with one
    table row per scenario (the S5.2 committed summary, the doctrine's
    citation source) + the box-state fields.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from bench import report
from bench.exec import TaskResult

_SNAP = {
    "nvidia_smi": "580.126.09, 100 MiB",
    "docker_stats": "qwen38-sglang 70GiB / 121GiB",
    "meminfo": "MemTotal=124000000 kB MemAvailable=40000000 kB",
    "driver": "580.126.09",
    "date": "2026-09-26T00:00:00Z",
    "epoch": 1760000000,
}

_CFG = {
    "config_path": "/opt/bonsai2/etc/bench.yaml",
    "results_dir": "/run/bonsai2/bench",
    "repetitions": 3,
}


def _task(ok: bool = True, tokens: int = 100, elapsed: float = 1000.0) -> TaskResult:
    return TaskResult(
        ok=ok,
        answer="done",
        completion_tokens=tokens,
        elapsed_ms=elapsed,
        decode_tps=tokens / (elapsed / 1000.0),
        prompt_tokens=32,
        error=None if ok else "engine",
        detail=None if ok else "HTTP 500",
    )


class BuildRowTest(unittest.TestCase):
    def test_row_carries_snapshot_and_verdict(self):
        verdict = {"pass": True, "score": 1.0, "detail": "ok", "extra": {}}
        row = report.build_row("M1", [_task()], verdict, _SNAP, _CFG, commit="abc1234")
        self.assertEqual(row["scenario"], "M1")
        self.assertEqual(row["commit"], "abc1234")
        self.assertEqual(row["row_verdict"], "pass")
        self.assertEqual(row["verdict"]["score"], 1.0)
        self.assertEqual(row["box_state"], _SNAP)
        self.assertEqual(len(row["tasks"]), 1)
        self.assertTrue(row["tasks"][0]["ok"])

    def test_row_verdict_fails_when_verdict_fails(self):
        verdict = {"pass": False, "score": 0.0, "detail": "nope", "extra": {}}
        row = report.build_row(
            "M2", [_task(ok=False, tokens=0, elapsed=10.0)], verdict, _SNAP, _CFG
        )
        self.assertEqual(row["row_verdict"], "fail")

    def test_row_serializes_to_json(self):
        verdict = {"pass": True, "score": 1.0, "detail": "ok", "extra": {"k": 1}}
        row = report.build_row("M7", [_task()], verdict, _SNAP, _CFG)
        json.dumps(
            row
        )  # must not raise (the S5.9 #1 "every row carries the box-state snapshot")


class WriteRowTest(unittest.TestCase):
    def test_write_row_is_parseable(self):
        verdict = {"pass": True, "score": 1.0, "detail": "ok", "extra": {}}
        row = report.build_row("M1", [_task()], verdict, _SNAP, _CFG)
        with tempfile.TemporaryDirectory() as td:
            p = report.write_row(row, td, "M1")
            self.assertTrue(p.is_file())
            self.assertTrue(p.name.endswith("_M1.json"))
            back = json.loads(p.read_text())
            self.assertEqual(back, row)

    def test_no_leftover_tmp_files(self):
        verdict = {"pass": True, "score": 1.0, "detail": "ok", "extra": {}}
        row = report.build_row("M1", [_task()], verdict, _SNAP, _CFG)
        with tempfile.TemporaryDirectory() as td:
            report.write_row(row, td, "M1")
            leftovers = [p for p in Path(td).iterdir() if p.name.endswith(".tmp")]
            self.assertEqual(leftovers, [])


class WriteSummaryTest(unittest.TestCase):
    def _rows(self) -> list:
        pass_v = {"pass": True, "score": 1.0, "detail": "ok", "extra": {}}
        fail_v = {"pass": False, "score": 0.5, "detail": "7/10 found", "extra": {}}
        return [
            report.build_row(
                "M1",
                [_task(tokens=100, elapsed=1000.0)],
                pass_v,
                _SNAP,
                _CFG,
                commit="abc1234",
            ),
            report.build_row(
                "M4",
                [_task(tokens=200, elapsed=2000.0)],
                fail_v,
                _SNAP,
                _CFG,
                commit="abc1234",
            ),
        ]

    def test_summary_table_has_one_row_per_scenario(self):
        with tempfile.TemporaryDirectory() as td:
            p = report.write_summary(self._rows(), td, _CFG, commit="abc1234")
            self.assertTrue(p.is_file())
            self.assertTrue(p.name.endswith("_abc1234.md"))
            text = p.read_text()
            self.assertIn("| M1 |", text)
            self.assertIn("| M4 |", text)
            self.assertIn("pass", text)
            self.assertIn("fail", text)
            # the S5.2 snapshot fields in the table (driver + the
            # MemAvailable value, the S5.2 pool physics the T5 doctrine names)
            self.assertIn("580.126.09", text)
            self.assertIn("40000000 kB", text)

    def test_summary_dir_created(self):
        with tempfile.TemporaryDirectory() as td:
            nested = Path(td) / "a" / "b"
            p = report.write_summary(self._rows(), nested, _CFG, commit="abc1234")
            self.assertTrue(p.is_file())


if __name__ == "__main__":
    unittest.main()
