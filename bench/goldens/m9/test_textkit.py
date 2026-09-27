"""M9 golden — the 3-case test suite for the M9 spec-chunk fixture.

The S5.2 M9 fixture is "a small library spec with 3 functions + a 3-case
suite". This is the 3-case suite: three deterministic cases against the
spec functions in ``textkit.py`` (``slugify``, ``title_case``,
``truncate``) — one per function, the shape the subagent is asked to
co-produce and the suite the M9 rubric re-runs on main's side (the S5.9
#5 "main-side re-run": exit 0 = the suite the subagent claimed to have
passed actually passes).

The cases are the 3 edges the spec pins per function — small +
deterministic (no I/O, no network).
"""

import unittest

from textkit import slugify, title_case, truncate


class TestM9Golden(unittest.TestCase):
    def test_slugify(self):
        self.assertEqual(slugify("Hello, World!"), "hello-world")
        self.assertEqual(slugify("  A  B  "), "a-b")
        self.assertEqual(slugify("!!!"), "")

    def test_title_case(self):
        self.assertEqual(title_case("a plan a canal"), "A Plan A Canal")
        self.assertEqual(title_case("already"), "Already")
        self.assertEqual(title_case(""), "")

    def test_truncate(self):
        self.assertEqual(truncate("abcdef", 6), "abcdef")
        self.assertEqual(truncate("abcdef", 5), "ab...")
        self.assertEqual(truncate("abcdef", 2), "")


if __name__ == "__main__":
    unittest.main()
