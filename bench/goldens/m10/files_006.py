"""M10 golden batch — file 006 (PASSING module).

Self-contained + deterministic (stdlib only). files-006: `count_chars`
counts the occurrences of a character in a string.
"""

import unittest


class TestFiles006(unittest.TestCase):
    def test_count_present(self):
        self.assertEqual(files_006_count_chars("banana", "a"), 3)

    def test_count_absent(self):
        self.assertEqual(files_006_count_chars("banana", "z"), 0)

    def test_count_empty(self):
        self.assertEqual(files_006_count_chars("", "a"), 0)


def files_006_count_chars(s, ch):
    """Count the occurrences of ch in s."""
    return s.count(ch)


if __name__ == "__main__":
    unittest.main()
