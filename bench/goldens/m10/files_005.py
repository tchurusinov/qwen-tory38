"""M10 golden batch — file 005 (PASSING module).

Self-contained + deterministic (stdlib only). files-005: `first_char`
returns the first character of a non-empty string (empty -> '').
"""

import unittest


class TestFiles005(unittest.TestCase):
    def test_first(self):
        self.assertEqual(files_005_first_char("hello"), "h")

    def test_first_single(self):
        self.assertEqual(files_005_first_char("x"), "x")

    def test_first_empty(self):
        self.assertEqual(files_005_first_char(""), "")


def files_005_first_char(s):
    """Return the first character of s, or '' when s is empty."""
    return s[0] if s else ""


if __name__ == "__main__":
    unittest.main()
