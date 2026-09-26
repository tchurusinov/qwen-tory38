"""M1 golden — the 3-case test suite the M1 rubric scores against.

"S5.2 M1: compiles + passes its own 3-case test suite (auto)". This is
the 3-case suite: three deterministic cases against the ``module.py``
golden (clamp, sum_of_squares, is_palindrome). The M1 scorer (bench.
score.m1) re-runs an analogous suite against a SUBAGENT's generated
module in a subprocess and asserts exit 0 (the S5.2 "compiles + passes
its own 3-case test suite" rubric).

The cases are the 3 edges the golden pins: one nominal, one boundary, one
exception per function family — small + deterministic (no I/O).
"""

import unittest

from module import clamp, is_palindrome, sum_of_squares


class TestM1Golden(unittest.TestCase):
    def test_clamp_nominal_and_edges(self):
        self.assertEqual(clamp(5, 0, 10), 5)
        self.assertEqual(clamp(-1, 0, 10), 0)
        self.assertEqual(clamp(99, 0, 10), 10)

    def test_sum_of_squares(self):
        self.assertEqual(sum_of_squares([1, 2, 3]), 14)
        self.assertEqual(sum_of_squares([]), 0)
        with self.assertRaises(TypeError):
            sum_of_squares([1, "x"])

    def test_is_palindrome_edges(self):
        self.assertTrue(is_palindrome("A man, a plan, a canal: Panama"))
        self.assertTrue(is_palindrome(""))
        self.assertFalse(is_palindrome("no"))


if __name__ == "__main__":
    unittest.main()
