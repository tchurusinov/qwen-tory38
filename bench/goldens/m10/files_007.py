"""M10 golden batch — file 007 (INTENTIONALLY FAILING module).

Self-contained + deterministic. files-007 deliberately asserts a wrong
value in one case, so `python -m unittest files_007` exits NON-zero. This
is the golden's second seeded FAIL (paired with files-003 in the golden
report).
"""

import unittest


class TestFiles007(unittest.TestCase):
    def test_min_nominal(self):
        self.assertEqual(files_007_min(4, 2), 2)

    def test_min_equal(self):
        # seeded failure: the min of two equal values is that value, not 1.
        self.assertEqual(files_007_min(6, 6), 1)


def files_007_min(a, b):
    """Return the smaller of a and b."""
    return min(a, b)


if __name__ == "__main__":
    unittest.main()
