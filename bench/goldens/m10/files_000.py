"""M10 golden batch — file 000.

The M10 scenario (S5.2) is "tool-loop test batch: run a 10-file test batch,
produce a structured report (per-file pass/fail, durations)". This file is
one of the 10 self-contained unittest modules the subagent's tool loop
executes (bash `python -m unittest files_000`). files-000 is a PASSING
module: every assertion holds, so `python -m unittest files_000` exits 0.

Self-contained + deterministic (no I/O, no network, no imports beyond the
stdlib): the S5.3 rubric's main-side re-run (the S5.9 #5 "main re-runs
2/10 files to confirm the verdicts agree") re-runs these modules in a
subprocess and asserts the exit code without a GPU or a network.
"""

import unittest


class TestFiles000(unittest.TestCase):
    def test_add_one_zero(self):
        self.assertEqual(files_000_check(0), 1)

    def test_add_one_positive(self):
        self.assertEqual(files_000_check(5), 6)

    def test_add_one_negative(self):
        self.assertEqual(files_000_check(-3), -2)


def files_000_check(n):
    """Return n + 1 (the one operation this module pins)."""
    return n + 1


if __name__ == "__main__":
    unittest.main()
