"""M10 golden batch — file 003 (INTENTIONALLY FAILING module).

Self-contained + deterministic. files-003 deliberately asserts a wrong
value in one case, so `python -m unittest files_003` exits NON-zero. This
is the golden's seeded FAIL (the S5.3 rubric scores COVERAGE, not pass
rate — a failing file is still a covered file, and the main-side re-run
must OBSERVE the fail, matching the subagent's claimed verdict).
"""

import unittest


class TestFiles003(unittest.TestCase):
    def test_max_nominal(self):
        self.assertEqual(files_003_max(3, 9), 9)

    def test_max_equal(self):
        # seeded failure: the max of two equal values is that value, not -1.
        self.assertEqual(files_003_max(5, 5), -1)


def files_003_max(a, b):
    """Return the larger of a and b."""
    return max(a, b)


if __name__ == "__main__":
    unittest.main()
