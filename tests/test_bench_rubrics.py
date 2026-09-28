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
from bench.score import goldens, m1, m2, m3, m4, m5, m6, m7, m8, m9, m10

# The MAP 4.9 canned capability result (the M9/M10 hermetic seam) — a
# RELATIVE import (the hermetic-suite fixture convention, S6.6; the smoke
# stdlib-only check skips relative imports, and bench_mock's top-level
# import chain is stdlib-only: the `bench` dep is local inside _run).
from .fixtures.bench_mock import mock_cap as _mock_cap


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

    def test_comma_tokenization_is_comma_placement_independent(self):
        # G3c round-2 P2 (H8 hardening): the golden "M1, M2, M7" used to
        # tokenize with the comma attached ("m1," / "m2," / "m7"), so the
        # token-set half only matched when the answer used the EXACT same
        # comma placement. The live regression was saved only because the
        # conjunction was "and" and the tokens happened to align. Now the
        # H8 half extracts alnum/underscore runs, so a comma-FREE or
        # differently-punctuated answer still matches, and a genuinely
        # missing fact still fails.
        golden = "M1, M2, M7"
        self.assertTrue(m5._match("M1 M2 M7", golden), "comma-free variant")
        self.assertTrue(m5._match("M1, M2, and M7", golden), "conjunction")
        self.assertTrue(
            m5._match("the quick bench is M1, M2, and M7", golden), "padded"
        )
        self.assertFalse(m5._match("M1, M2", golden), "a missing fact fails")

    def test_multi_value_golden_requires_order(self):
        # G3c round-6 P1: the multi-value H8 half is an ordered
        # subsequence, not a bare set-subset. A reordered fact list
        # ("M7, M2, M1") must NOT pass the golden "M1, M2, M7"; extra
        # words / stop-particles BETWEEN the facts still pass
        # ("tolerant of phrasing"); a missing fact still fails.
        golden = "M1, M2, M7"
        self.assertFalse(m5._match("M7, M2, M1", golden), "reordered fails")
        self.assertFalse(m5._match("M2, M1, M7", golden), "partial reorder fails")
        self.assertTrue(
            m5._match("M1 and M2 and finally M7", golden),
            "stop-words between keep order",
        )
        self.assertFalse(m5._match("M1 M7", golden), "missing middle fact fails")

    def test_multi_token_golden_rejects_prefix_substring(self):
        # G3c round-15 P1: the round-2 bare `g in a` fast path let the
        # golden be a strict-PREFIX substring of the answer — "M1, M2"
        # vs "M1, M22" -> "m1, m2" in "m1, m22" -> the last golden token
        # ("m2") substring-matched a longer answer word ("m22") and the
        # question falsely passed. The ordered whole-token subsequence is
        # the matcher now: the golden's content tokens must each appear
        # as a WHOLE token, in golden order.
        golden = "M1, M2"
        self.assertFalse(m5._match("M1, M22", golden), "prefix-substring fails")
        self.assertFalse(m5._match("M1 M22", golden), "prefix-substring, no comma")
        self.assertTrue(m5._match("M1, M2", golden), "exact passes")
        self.assertTrue(m5._match("M1, M2 exactly", golden), "padded passes")
        self.assertTrue(m5._match("M1 M2 M77", golden), "extra fact tolerated")
        self.assertTrue(m5._match("M1 and M2", golden), "stop-word tolerated")

    def test_bare_stop_word_golden_matches_whole_word_only(self):
        # G3c round-7 P1: a golden that is itself a single stop word
        # ("a", "the", "an") has _token_seq(g) == [] (the stop word is
        # filtered), so the pre-fix post-filter token count misrouted it
        # to the `g in a` substring fast-path and "banana" matched
        # golden "a". The fix routes on the RAW (pre-filter) token count,
        # so a single stop-word golden gets the whole-word matcher:
        # "banana" does NOT match "a", "a cat" does.
        self.assertFalse(m5._match("banana", "a"), "substring false positive")
        self.assertTrue(m5._match("a cat", "a"), "word-boundary match")
        self.assertFalse(m5._match("mother", "the"), "substring false positive")
        self.assertTrue(m5._match("the cat", "the"), "word-boundary match")
        self.assertFalse(m5._match("another", "an"), "substring false positive")
        self.assertTrue(m5._match("an apple", "an"), "word-boundary match")

    def test_mis_sized_golden_fails_named(self):
        # the S5.2 "5 QA questions" rubric contract: a golden with a
        # different question count fails NAMED instead of the rubric
        # silently adapting (G3 round-2 P2: the 5-way contract must be
        # asserted, not inferred from the golden).
        import json
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "m5").mkdir()
            qa = dict(goldens.m5_qa())
            qa["questions"] = qa["questions"][:3]
            qa["answers"] = qa["answers"][:3]
            (root / "m5" / "qa.json").write_text(json.dumps(qa))
            v = m5.score({"answers": list(qa["answers"])}, root=str(root))
            self.assertFalse(v["pass"])
            self.assertIn("expects 5", v["detail"])


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


class M9RubricTest(unittest.TestCase):
    """MAP 4.9 M9 rubric golden in/out pairs (hermetic)."""

    def _payload(self, workdir, tool_log, funcs=None):
        from bench.score import goldens

        if funcs is None:
            funcs = [
                f["function"] if isinstance(f, dict) else str(f)
                for f in goldens.m9_spec().get("spec_functions", [])
            ]
        return {
            "workdir": str(workdir),
            "tool_log": tool_log,
            "answer": "Suite passed: Ran 3 tests, OK (exit 0).",
            "module_name": "textkit",
            "suite_file": "test_textkit.py",
            "run_command": "python -m unittest test_textkit",
            "spec_functions": funcs,
        }

    def _suite_run_entry(self, exit_code=0):
        return {
            "step": 3,
            "tool": "bash",
            "params": {"cmd": "python -m unittest test_textkit"},
            "exit_code": exit_code,
            "output": "Ran 3 tests in 0.001s\nOK",
            "duration_ms": 40,
        }

    def _workdir_with_module(self, module_src):
        import tempfile
        from pathlib import Path

        wd = Path(tempfile.mkdtemp(prefix="bonsai2-m9-test-"))
        (wd / "textkit.py").write_text(module_src)
        (wd / "test_textkit.py").write_text(goldens.m9_suite_source())
        return wd

    def test_golden_capability_result_passes(self):
        res = _mock_cap("M9", "host")
        v = m9.score(bench_mock_mod_payload("M9", res))
        self.assertTrue(v["pass"], v)
        self.assertEqual(v["score"], 1.0)
        self.assertTrue(v["extra"]["main_rerun"]["pass"], v)
        self.assertTrue(v["extra"]["tool_log"]["pass"], v)
        self.assertTrue(v["extra"]["functions"]["pass"], v)

    def test_crashed_state_skips_functions_sub_check(self):
        # G3c round-8 P1: when the driver raises (state != "done"), the
        # staged workdir was already rmtree'd — the functions sub-check
        # must be SKIPPED (not scored against a cleaned path), and the
        # sub-condition records skipped: true + a named reason.
        p = self._payload("/nonexistent/cleaned-workdir", [self._suite_run_entry()])
        p["state"] = "crashed"
        v = m9.score(p)
        self.assertFalse(v["pass"], v)
        self.assertFalse(v["extra"]["functions"]["pass"], v)
        self.assertTrue(v["extra"]["functions"]["skipped"], v)
        self.assertIn("SKIPPED", v["extra"]["functions"]["detail"], v)
        self.assertIn("state 'crashed'", v["extra"]["functions"]["detail"], v)

    def test_multi_stop_word_golden_degenerate_path(self):
        # G3c round-8 P2: a golden of 2+ raw tokens that are ALL stop
        # words (e.g. "a, an") has no content token (_token_seq(g) == []),
        # so the ordered-subsequence half is vacuous (empty needle ->
        # _is_subseq True for ANY answer — the degenerate pinned below).
        # It resolves via SUBSTRING containment of the literal instead
        # (the round-15 degenerate branch, `g in a`). No M5 golden today
        # is multi-stop-word, so this path is untested by the goldens —
        # pin the OBSERVABLE behavior: the literal "a, an" and any answer
        # that SUBSTRING-contains it match; an answer without the literal
        # (even with both stop-words present separately) does not.
        self.assertTrue(m5._match("a, an", "a, an"), "exact literal")
        self.assertTrue(m5._match("there is a, an error", "a, an"), "substring-padded")
        self.assertFalse(
            m5._match("a an error", "a, an"), "comma-less differs (no literal)"
        )
        self.assertTrue(
            m5._match("a, another one", "a, an"),
            "substring 'a, an' present in 'a, another one' -> True",
        )

    def test_missing_suite_run_in_tool_log_scores_2_3(self):
        import tempfile
        from pathlib import Path

        wd = Path(tempfile.mkdtemp(prefix="bonsai2-m9-test-"))
        (wd / "textkit.py").write_text(goldens.m9_module_source())
        v = m9.score(
            self._payload(
                wd,
                [
                    {
                        "step": 1,
                        "tool": "write_file",
                        "params": {"path": "x"},
                        "exit_code": 0,
                        "output": "ok",
                    }
                ],
            )
        )
        self.assertFalse(v["pass"])
        self.assertAlmostEqual(v["score"], 2.0 / 3.0, places=5)
        self.assertFalse(v["extra"]["tool_log"]["pass"])
        # the suite is NOT in the workdir -> the functions check fails too
        # (workdir has no test_textkit.py), but the module does -> 2/3
        # is main_re-run + functions; re-run is always the golden.
        self.assertTrue(v["extra"]["main_rerun"]["pass"])

    def test_nonzero_suite_run_scores_2_3(self):
        import tempfile
        from pathlib import Path

        wd = Path(tempfile.mkdtemp(prefix="bonsai2-m9-test-"))
        (wd / "textkit.py").write_text(goldens.m9_module_source())
        (wd / "test_textkit.py").write_text(goldens.m9_suite_source())
        v = m9.score(self._payload(wd, [self._suite_run_entry(exit_code=1)]))
        self.assertFalse(v["pass"])
        self.assertAlmostEqual(v["score"], 2.0 / 3.0, places=5)
        self.assertFalse(v["extra"]["tool_log"]["pass"])

    def test_workdir_missing_a_function_scores_2_3(self):
        import tempfile
        from pathlib import Path

        wd = Path(tempfile.mkdtemp(prefix="bonsai2-m9-test-"))
        # the golden module minus one spec function (drop truncate)
        src = goldens.m9_module_source()
        lines = []
        skipping = False
        for line in src.splitlines(keepends=True):
            if line.startswith("def truncate("):
                skipping = True
                continue
            if skipping and line.startswith("def "):
                skipping = False
            if not skipping:
                lines.append(line)
        (wd / "textkit.py").write_text("".join(lines))
        (wd / "test_textkit.py").write_text(goldens.m9_suite_source())
        v = m9.score(self._payload(wd, [self._suite_run_entry()]))
        self.assertFalse(v["pass"])
        self.assertFalse(v["extra"]["functions"]["pass"])
        self.assertEqual(v["extra"]["functions"]["n_present"], 2)
        self.assertEqual(v["extra"]["functions"]["n_expected"], 3)
        self.assertAlmostEqual(v["score"], 2.0 / 3.0, places=5)


class M10RubricTest(unittest.TestCase):
    """MAP 4.9 M10 rubric golden in/out pairs (hermetic)."""

    def test_golden_report_passes(self):
        import json

        v = m10.score({"report": json.dumps(goldens.m10_expected_report())})
        self.assertTrue(v["pass"], v)
        self.assertEqual(v["score"], 1.0)
        self.assertTrue(v["extra"]["parse"]["pass"])
        self.assertEqual(v["extra"]["coverage"]["n_files"], 10)
        self.assertEqual(v["extra"]["rerun"]["n_agree"], 2)

    def test_wrong_spot_claim_scores_2_3(self):
        """A report that claims files_003 passed when the golden module
        fails -> the 2/10 main re-run observes the fail, the verdicts
        disagree -> the re-run half drops (the S5.9 #5 spot catch)."""
        import json

        report = goldens.m10_expected_report()
        for entry in report["files"]:
            if entry["file"] == "files_003.py":
                entry["status"] = "pass"
                entry["exit_code"] = 0
        v = m10.score({"report": json.dumps(report)})
        self.assertFalse(v["pass"])
        self.assertFalse(v["extra"]["rerun"]["pass"])
        self.assertEqual(v["extra"]["rerun"]["n_agree"], 1)

    def test_9_file_report_scores_0_9667(self):
        import json

        report = goldens.m10_expected_report()
        report["files"] = report["files"][:9]
        v = m10.score({"report": json.dumps(report)})
        self.assertFalse(v["pass"])
        self.assertFalse(v["extra"]["coverage"]["pass"])
        # the M10 score is the BLEND of the three halves (S5.3 rubric-
        # score shape): parse 1.0 + coverage 0.9 + re-run agreement
        # 1.0 (the 2/10 spot files are still covered, the spot is
        # files_003/007 — the dropped 10th is not in the spot set) ->
        # (1 + 0.9 + 1) / 3 = 0.9667 (the coverage HALF is 0.9; the
        # blend is the documented score).
        self.assertAlmostEqual(v["score"], (1.0 + 0.9 + 1.0) / 3.0, places=5)

    def test_unparseable_report_fails(self):
        v = m10.score({"report": "{not json"})
        self.assertFalse(v["pass"])
        self.assertFalse(v["extra"]["parse"]["pass"])


def bench_mock_mod_payload(sid, res):
    """The M9/M10 runner payload from a canned capability result (mirrors
    ``bench.run._capability_payload`` without importing the runner)."""
    from bench.score import goldens as g

    if sid == "M9":
        return {
            "workdir": str(res.get("workdir")),
            "tool_log": res.get("tool_log") or [],
            "answer": res.get("answer") or "",
            "module_name": "textkit",
            "suite_file": "test_textkit.py",
            "run_command": "python -m unittest test_textkit",
            "spec_functions": [
                f["function"] if isinstance(f, dict) else str(f)
                for f in g.m9_spec().get("spec_functions", [])
            ],
        }
    return {"report": res.get("answer") or ""}


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
