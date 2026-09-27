"""M10 golden batch — file 009 (PASSING module).

Self-contained + deterministic (stdlib only). files-009: `is_upper`
reports whether a string is all uppercase.
"""

import unittest


class TestFiles009(unittest.TestCase):
    def test_upper(self):
        self.assertTrue(files_009_is_upper("ABC"))

    def test_mixed(self):
        self.assertFalse(files_009_is_upper("AbC"))

    def test_lower(self):
        self.assertFalse(files_009_is_upper("abc"))


def files_009_is_upper(s):
    """True when s is non-empty and all of its letters are uppercase."""
    return bool(s) and s == s.upper() and any(c.isalpha() for c in s)


if __name__ == "__main__":
    unittest.main()
