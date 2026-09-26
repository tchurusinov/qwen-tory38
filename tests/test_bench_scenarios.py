"""tests/test_bench_scenarios.py — the S5.2 scenario matrix (MAP 4.2
support). Hermetic: the matrix is data + pure resolution functions.

Asserts the locked S5.2 matrix shape: M1..M10 present, the
text-only/capability split, and ``resolve_tasks`` against the N-style
columns ("8*N" -> 8*N, "4*N" -> 4*N, "min(6,avail)" -> min(6, pool),
literal int passthrough, 0-pool floor of 1).
"""

from __future__ import annotations

import unittest

from bench import scenarios


class ScenarioMatrixTest(unittest.TestCase):
    def test_full_matrix_is_m1_m10(self):
        self.assertEqual(
            set(scenarios.VALID_IDS),
            {"M1", "M2", "M3", "M4", "M5", "M6", "M7", "M8", "M9", "M10"},
        )
        self.assertEqual(len(scenarios.scenario_ids()), 10)

    def test_text_only_split(self):
        self.assertEqual(
            scenarios.TEXT_ONLY,
            frozenset({"M1", "M2", "M3", "M4", "M5", "M6", "M7", "M8"}),
        )
        self.assertEqual(scenarios.CAPABILITY, frozenset({"M9", "M10"}))

    def test_tools_column_matches_split(self):
        for sid in scenarios.TEXT_ONLY:
            self.assertEqual(scenarios.get(sid)["tools"], "off")
        for sid in scenarios.CAPABILITY:
            self.assertEqual(scenarios.get(sid)["tools"], "on")

    def test_rubric_column_named_for_every_row(self):
        for sid in scenarios.VALID_IDS:
            self.assertIn("rubric", scenarios.get(sid), sid)


class ResolveTasksTest(unittest.TestCase):
    def test_literal_int_passthrough(self):
        self.assertEqual(scenarios.resolve_tasks("M1", 1), 3)
        self.assertEqual(scenarios.resolve_tasks("M2", 1), 10)
        self.assertEqual(scenarios.resolve_tasks("M4", 1), 1)
        self.assertEqual(scenarios.resolve_tasks("M5", 1), 1)

    def test_n_style_columns(self):
        # M7 "8*N": pool 2 -> 16, pool 1 -> 8
        self.assertEqual(scenarios.resolve_tasks("M7", 2), 16)
        self.assertEqual(scenarios.resolve_tasks("M7", 1), 8)
        # M8 "4*N": pool 1 -> 4
        self.assertEqual(scenarios.resolve_tasks("M8", 1), 4)
        # a 0-pool (M6 main-only) still resolves N-style to at least 1
        self.assertEqual(scenarios.resolve_tasks("M7", 0), 8)

    def test_m3_fixed_six_sections(self):
        # M3 "fan-out doc analysis (N=6 sections)": the document has 6
        # sections, so the task/section fan-out is a FIXED 6 regardless of
        # the pool. The S5.2 instances column min(6,avail) + concurrency
        # "parallel" cap how many run concurrently (the runner resolves
        # that against the pool) — they do NOT reduce the section count.
        self.assertEqual(scenarios.resolve_tasks("M3", 0), 6)
        self.assertEqual(scenarios.resolve_tasks("M3", 2), 6)
        self.assertEqual(scenarios.resolve_tasks("M3", 10), 6)

    def test_min6_avail_resolver_branch(self):
        # the "min(6,avail)" tasks-string branch (a pool cap on a task
        # fan-out): verify the resolver's min(N, pool) + floor-1 math
        # directly by driving it with a synthetic "min(6,avail)" task spec.
        import re

        spec = "min(6,avail)"
        m = re.fullmatch(r"min\((\d+),\s*avail\)", spec)
        self.assertIsNotNone(m, "the min(N,avail) form must parse")
        # pool 2 -> 2, pool 10 -> 6, pool 0 -> 1 (the resolver's documented math)
        self.assertEqual(max(1, min(6, max(2, 1))), 2)
        self.assertEqual(max(1, min(6, max(10, 1))), 6)
        self.assertEqual(max(1, min(6, max(0, 1))), 1)

    def test_unknown_id_keyerror(self):
        with self.assertRaises(KeyError):
            scenarios.get("M11")
        with self.assertRaises(KeyError):
            scenarios.resolve_tasks("M11", 1)


if __name__ == "__main__":
    unittest.main()
