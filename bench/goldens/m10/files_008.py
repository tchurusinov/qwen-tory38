"""M10 golden batch — file 008 (PASSING module).

Self-contained + deterministic (stdlib only). files-008: `add` adds two
numbers.
"""

import unittest


class TestFiles008(unittest.TestCase):
    def test_add_positive(self):
        self.assertEqual(files_008_add(2, 3), 5)

    def test_add_negative(self):
        self.assertEqual(files_008_add(-2, -3), -5)

    def test_add_zero(self):
        self.assertEqual(files_008_add(0, 0), 0)


def files_008_add(a, b):
    """Return a + b."""
    return a + b


if __name__ == "__main__":
    unittest.main()
