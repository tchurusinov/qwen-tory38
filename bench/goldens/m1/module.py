"""M1 golden — a small single-file module with three functions.

The M1 scenario (S5.2) is "single-file code-gen (3 tasks)"; the rubric is
"compiles + passes its own 3-case test suite (auto)". This file is the
GOLDEN shape of the code a subagent is asked to generate: a small library
module with three functions (``clamp``, ``sum_of_squares``,
``is_palindrome``) and the 3-case test suite in ``test_module.py``. The
M1 scorer (bench.score.m1) scores a subagent's generated module against
the SAME 3-case suite shape: the generated file must (a) parse, and (b)
pass the 3-case suite when the runner re-runs it.

The functions are deliberately tiny + deterministic (no I/O, no
network): a hermetic scorer can execute them in a subprocess and assert
the exit code without a GPU or a network.
"""


def clamp(value: float, lo: float, hi: float) -> float:
    """Clamp ``value`` into [lo, hi]. lo > hi raises ValueError."""
    if lo > hi:
        raise ValueError(f"lo {lo} > hi {hi}")
    if value < lo:
        return lo
    if value > hi:
        return hi
    return value


def sum_of_squares(items) -> int:
    """The sum of the squares of the numeric items in an iterable.

    Non-numeric items raise TypeError (the S5.2 code-gen contract: the
    golden is a specific, testable shape, not a permissive one).
    """
    total = 0
    for x in items:
        if not isinstance(x, (int, float)) or isinstance(x, bool):
            raise TypeError(f"non-numeric item: {x!r}")
        total += int(x * x)
    return total


def is_palindrome(text: str) -> bool:
    """True when ``text`` (case-folded, alphanumeric-only) is a palindrome.

    "A man, a plan, a canal: Panama" -> True. Empty -> True (the vacuous
    palindrome, the 3-case suite pins this edge).
    """
    s = "".join(ch for ch in text.casefold() if ch.isalnum())
    return s == s[::-1]
