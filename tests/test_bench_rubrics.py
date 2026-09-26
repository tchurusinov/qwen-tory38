"""tests/test_bench_rubrics.py — the S5.8 rubric tests (MAP 4.4/4.5/4.6).

S5.8: "each scorer plugin has a golden in/out pair (hermetic, no
endpoints)." One golden in/out pair per scorer, driven with in-memory
payloads (the scorers are pure on payload + the repo goldens):

  * M1 — the golden module + golden suite -> pass; a module with a
    syntax error -> fail.
  * M2 — the golden 10-file report -> pass; a 9-file report -> score
    0.9, fail.
  * M4 — a draft citing all 10 seeded ids -> pass (1.0); 7 -> fail
    (0.7).
  * M5 — the 5 golden answers -> pass (1.0); 4 of 5 -> fail (0.8).
  * M7 — an aggregate with 0 errors + a positive t/s -> pass; one 5xx
    task -> fail.
  * M8 — m8_main_tps == m6_tps -> pass (0% drop); 0.90 * m6 -> fail
    (10% drop > the 5% guardrail).
  * M3 — all sections shape-correct + spot-check 1.0 -> pass; a mix ->
    the documented blend.
  * M6 — main_tps > 0 + every M1-M5 verdict passes -> pass; no t/s ->
    fail.

No endpoints, no network, no GPU: the goldens live in bench/goldens/
(the S5.3 "golden files in bench/goldens/" rule) and the M1 pair runs
the suite through the same subprocess path the live rubric uses.
"""

from __future__ import annotations

import unittest

from bench.exec import TaskResult
from bench.score import goldens, m1, m2, m3, m4, m5, m6, m7, m8


class M1RubricTest(unittest.TestCase):
    def test_golden_module_and_suite_pass(self):
        payload = {
            "module": goldens.m1_module_source(),
            "suite": goldens.m1_test_source(),
        }
        v = m1.score(payload)
        self.assertTrue(v["pass"], v)
        self.assertEqual(v["score"], 1.0)

    def test_synthesis_broken_module_fails(self):
        payload = {
            "module": "def clamp(:\n    pass\n",
            "suite": goldens.m1_test_source(),
        }
        v = m1.score(payload)
        self.assertFalse(v["pass"])
        self.assertEqual(v["score"], 0.0)
        self.assertIn("does not compile", v["detail"])

    def test_failing_suite_fails(self):
        payload = {
            "module": goldens.m1_module_source(),
            "suite": (
                "import unittest\n"
                "from module import clamp\n\n"
                "class T(unittest.TestCase):\n"
                "    def test_wrong(self):\n"
                "        self.assertEqual(clamp(5, 0, 10), 6)\n\n"
                "if __name__ == '__main__':\n"
                "    unittest.main()\n"
            ),
        }
        v = m1.score(payload)
        self.assertFalse(v["pass"])

    def test_missing_module_is_fail(self):
        v = m1.score({"module": "", "suite": goldens.m1_test_source()})
        self.assertFalse(v["pass"])
        self.assertIn("no generated module", v["detail"])


class M2RubricTest(unittest.TestCase):
    def test_golden_report_passes(self):
        import json

        v = m2.score(
            {"report": json.dumps(goldens.load_json("m2", "expected_report.json"))}
        )
        self.assertTrue(v["pass"], v)
        self.assertEqual(v["score"], 1.0)
        self.assertEqual(v["extra"]["n_files"], 10)

    def test_9_files_scores_0_9_and_fails(self):
        import json

        report = goldens.load_json("m2", "expected_report.json")
        report["files"] = report["files"][:9]
        v = m2.score({"report": json.dumps(report)})
        self.assertFalse(v["pass"])
        self.assertAlmostEqual(v["score"], 0.9, places=5)

    def test_unparseable_report_fails(self):
        v = m2.score({"report": "{not json"})
        self.assertFalse(v["pass"])
        self.assertEqual(v["score"], 0.0)

    def test_non_object_report_fails(self):
        v = m2.score({"report": "[1, 2, 3]"})
        self.assertFalse(v["pass"])


class M4RubricTest(unittest.TestCase):
    def test_all_10_cited_passes(self):
        bugs = goldens.m4_bugs()
        ids = [b["id"] for b in bugs]
        v = m4.score({"draft": "found: " + " ".join(ids)})
        self.assertTrue(v["pass"], v)
        self.assertEqual(v["score"], 1.0)
        self.assertEqual(v["extra"]["n_found"], 10)

    def test_7_cited_fails_at_0_7(self):
        bugs = goldens.m4_bugs()
        ids = [b["id"] for b in bugs][:7]
        v = m4.score({"draft": " ".join(ids)})
        self.assertFalse(v["pass"])
        self.assertAlmostEqual(v["score"], 0.7, places=5)
        self.assertEqual(v["extra"]["n_found"], 7)

    def test_extra_ids_are_not_penalized(self):
        bugs = goldens.m4_bugs()
        ids = [b["id"] for b in bugs]
        v = m4.score({"draft": " ".join(ids) + " BUG-99 BUG-100"})
        self.assertTrue(v["pass"])
        self.assertEqual(v["extra"]["n_found"], 10)

    def test_no_citation_fails(self):
        v = m4.score({"draft": "the diff looks fine"})
        self.assertFalse(v["pass"])
        self.assertEqual(v["extra"]["n_found"], 0)


class M5RubricTest(unittest.TestCase):
    def test_all_five_correct_passes(self):
        v = m5.score({"answers": goldens.m5_qa()["answers"]})
        self.assertTrue(v["pass"], v)
        self.assertEqual(v["score"], 1.0)

    def test_four_of_five_fails_at_0_8(self):
        answers = list(goldens.m5_qa()["answers"])
        answers[3] = "M1, M2, M9"  # wrong quick-bench answer
        v = m5.score({"answers": answers})
        self.assertFalse(v["pass"])
        self.assertAlmostEqual(v["score"], 0.8, places=5)

    def test_padded_answers_still_match_by_containment(self):
        answers = list(goldens.m5_qa()["answers"])
        answers[0] = "The fallback quant is PTQ_1_0 as documented."
        v = m5.score({"answers": answers})
        self.assertTrue(v["pass"], v)


class M7RubricTest(unittest.TestCase):
    def _agg(self, tps: float, n: int = 8) -> dict:
        return {
            "n_tasks": n,
            "n_ok": n,
            "n_errors": 0,
            "total_completion_tokens": n * 100,
            "total_elapsed_s": n * 100 / tps if tps else 0.0,
            "aggregate_tps": tps,
        }

    def test_zero_5xx_and_positive_tps_passes(self):
        results = [
            TaskResult(ok=True, completion_tokens=100, elapsed_ms=1000.0)
            for _ in range(8)
        ]
        v = m7.score({"agg": self._agg(30.0), "results": results})
        self.assertTrue(v["pass"], v)
        self.assertEqual(v["score"], 1.0)
        self.assertEqual(v["extra"]["aggregate_tps"], 30.0)

    def test_one_5xx_task_fails(self):
        results = [TaskResult(ok=True) for _ in range(7)] + [
            TaskResult(ok=False, error="engine", detail="HTTP 500")
        ]
        v = m7.score({"agg": self._agg(30.0), "results": results})
        self.assertFalse(v["pass"])
        self.assertEqual(v["extra"]["n_5xx"], 1)

    def test_ctx_errors_do_not_count_as_5xx(self):
        results = [TaskResult(ok=True) for _ in range(7)] + [
            TaskResult(ok=False, error="ctx", detail="HTTP 400")
        ]
        v = m7.score({"agg": self._agg(30.0), "results": results})
        # a 400 (ctx) is the request shape, not the engine — the zero-5xx
        # half still holds.
        self.assertTrue(v["pass"])

    def test_zero_tps_fails(self):
        v = m7.score({"agg": self._agg(0.0), "results": []})
        self.assertFalse(v["pass"])


class M8RubricTest(unittest.TestCase):
    def test_at_the_control_passes(self):
        v = m8.score({"m6_tps": 30.0, "m8_main_tps": 30.0, "main_guardrail_pct": 5.0})
        self.assertTrue(v["pass"], v)
        self.assertEqual(v["score"], 1.0)
        self.assertEqual(v["extra"]["pct_drop"], 0.0)

    def test_10pct_drop_beyond_5pct_guardrail_fails(self):
        v = m8.score({"m6_tps": 30.0, "m8_main_tps": 27.0, "main_guardrail_pct": 5.0})
        self.assertFalse(v["pass"])
        self.assertAlmostEqual(v["extra"]["pct_drop"], 10.0, places=5)
        self.assertEqual(v["score"], 0.0)

    def test_at_the_guardrail_edge_scores_zero_but_passes(self):
        # 5% drop == the guardrail edge: within (the S5.2 "within"), score
        # 0.0 (the continuous proximity measure at the edge).
        v = m8.score({"m6_tps": 100.0, "m8_main_tps": 95.0, "main_guardrail_pct": 5.0})
        self.assertTrue(v["pass"])
        self.assertEqual(v["score"], 0.0)

    def test_no_baseline_fails(self):
        v = m8.score({"m6_tps": 0.0, "m8_main_tps": 30.0, "main_guardrail_pct": 5.0})
        self.assertFalse(v["pass"])
        self.assertIn("no M6 control baseline", v["detail"])


class M3RubricTest(unittest.TestCase):
    def _good_section(self, i: int) -> dict:
        return {
            "summary": f"Section {i} summary within the token budget.",
            "claims": [f"claim {i}-1", f"claim {i}-2", f"claim {i}-3"],
        }

    def test_all_shape_correct_plus_perfect_spotcheck_passes(self):
        payload = {
            "sections": [self._good_section(i) for i in range(6)],
            "spotcheck": 1.0,
        }
        v = m3.score(payload)
        self.assertTrue(v["pass"], v)
        self.assertEqual(v["score"], 1.0)

    def test_perfect_shape_with_weak_spotcheck_fails(self):
        # the S5.2 "claim accuracy >= 2/3" threshold: 0.5 spot-check is
        # below it — shape alone cannot rescue the row.
        payload = {
            "sections": [self._good_section(i) for i in range(6)],
            "spotcheck": 0.5,
        }
        v = m3.score(payload)
        self.assertFalse(v["pass"])
        self.assertAlmostEqual(v["score"], 0.7 * 1.0 + 0.3 * 0.5, places=5)

    def test_bad_shape_sections_score_the_auto_half_down(self):
        bad = {"summary": "x" * 4000, "claims": ["only one"]}
        payload = {
            "sections": [self._good_section(0), bad, self._good_section(2)],
            "spotcheck": 1.0,
        }
        v = m3.score(payload)
        # the rubric rounds `auto` to 4 places in extra (0.6667)
        self.assertAlmostEqual(v["extra"]["auto"], 2.0 / 3.0, places=3)

    def test_no_sections_fails(self):
        v = m3.score({"sections": [], "spotcheck": 1.0})
        self.assertFalse(v["pass"])


class M6RubricTest(unittest.TestCase):
    def _pass_verdict(self) -> dict:
        return {"pass": True, "score": 1.0, "detail": "ok", "extra": {}}

    def test_valid_control_passes(self):
        v = m6.score(
            {
                "main_tps": 40.0,
                "verdicts": {s: self._pass_verdict() for s in m6.CONTROL_SHAPES},
            }
        )
        self.assertTrue(v["pass"], v)
        self.assertEqual(v["score"], 1.0)
        self.assertEqual(v["extra"]["all_pass"], True)

    def test_missing_main_tps_fails(self):
        v = m6.score(
            {
                "main_tps": 0.0,
                "verdicts": {s: self._pass_verdict() for s in m6.CONTROL_SHAPES},
            }
        )
        self.assertFalse(v["pass"])
        self.assertIn("no main endpoint t/s", v["detail"])

    def test_one_shape_verdict_failure_fails_the_control(self):
        verdicts = {s: self._pass_verdict() for s in m6.CONTROL_SHAPES}
        verdicts["M4"] = {"pass": False, "score": 0.7, "detail": "7/10", "extra": {}}
        v = m6.score({"main_tps": 40.0, "verdicts": verdicts})
        self.assertFalse(v["pass"])
        self.assertEqual(v["extra"]["per_shape"]["M4"], False)


class VerdictShapeTest(unittest.TestCase):
    """The shared S5.3 verdict shape (the contract every scorer emits)."""

    def test_verdict_shape_and_clamping(self):
        from bench.score import _common

        v = _common.verdict(True, 1.5, "detail")
        self.assertEqual(set(v), {"pass", "score", "detail", "extra"})
        self.assertTrue(v["pass"])
        self.assertEqual(v["score"], 1.0)  # clamped
        self.assertEqual(v["extra"], {})
        v2 = _common.verdict(False, -0.5, "d", extra={"k": 1})
        self.assertEqual(v2["score"], 0.0)
        self.assertEqual(v2["extra"], {"k": 1})


if __name__ == "__main__":
    unittest.main()
