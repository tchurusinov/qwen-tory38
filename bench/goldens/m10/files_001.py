"""M10 golden batch — file 001 (PASSING module).

Self-contained + deterministic (stdlib only): the S5.3 main-side re-run
re-runs these modules in a subprocess and checks the exit code.
files-001: `double` doubles a number.
"""

import unittest


class TestFiles001(unittest.TestCase):
    def test_double_zero(self):
        self.assertEqual(files_001_double(0), 0)

    def test_double_positive(self):
        self.assertEqual(files_001_double(7), 14)

    def test_double_negative(self):
        self.assertEqual(files_001_double(-4), -8)


def files_001_double(n):
    """Return 2 * n."""
    return 2 * n


if __name__ == "__main__":
    unittest.main()
