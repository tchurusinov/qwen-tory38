"""M10 golden batch — file 002 (PASSING module).

Self-contained + deterministic (stdlib only). files-002: `is_even` reports
evenness.
"""

import unittest


class TestFiles002(unittest.TestCase):
    def test_even_zero(self):
        self.assertTrue(files_002_is_even(0))

    def test_even_seven(self):
        self.assertFalse(files_002_is_even(7))

    def test_even_eight(self):
        self.assertTrue(files_002_is_even(8))


def files_002_is_even(n):
    """True when n is even."""
    return n % 2 == 0


if __name__ == "__main__":
    unittest.main()
