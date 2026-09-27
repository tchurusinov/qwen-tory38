"""M10 golden batch — file 004 (PASSING module).

Self-contained + deterministic (stdlib only). files-004: `abs_value`
returns the absolute value.
"""

import unittest


class TestFiles004(unittest.TestCase):
    def test_abs_positive(self):
        self.assertEqual(files_004_abs_value(3), 3)

    def test_abs_negative(self):
        self.assertEqual(files_004_abs_value(-3), 3)

    def test_abs_zero(self):
        self.assertEqual(files_004_abs_value(0), 0)


def files_004_abs_value(n):
    """Return the absolute value of n."""
    return n if n >= 0 else -n


if __name__ == "__main__":
    unittest.main()
