"""M9 golden — the reference solution for the M9 spec-chunk fixture.

The M9 scenario (S5.2) is "tool-loop spec-chunk: implement a bounded spec
chunk (fixture: a small library spec with 3 functions + a 3-case suite)".
This file is the GOLDEN solution: the 3 spec functions
(``slugify``, ``title_case``, ``truncate``) implemented exactly as the
spec in ``m9spec.json`` describes. The M9 scorer (bench.score.m9) does
NOT diff against this file — it re-runs the SUBAGENT's generated module +
suite (the S5.9 #5 "main-side re-run") and checks the three spec
functions exist in the workdir. This file is the hermetic reference: the
S5.8 mock tool-loop emits ITS tool calls (write this module + the 3-case
suite, run the suite) so the bench's capability row is rubric-correct by
construction, the way the M1 golden drives the M1 mock.
"""

import re


def slugify(text):
    """Return a lowercase, hyphen-joined slug of ``text``.

    Keep only alphanumeric characters; replace runs of spaces /
    non-alphanumeric characters with a single hyphen; strip leading and
    trailing hyphens. Empty / all-non-alphanumeric input -> ''.
    """
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").casefold())
    return s.strip("-")


def title_case(text):
    """Title-case ``text``: first letter of each word upper, rest lower."""
    words = (text or "").split(" ")
    out = []
    for w in words:
        letters = "".join(c for c in w if c.isalpha())
        if letters:
            out.append(letters[0].upper() + letters[1:].lower())
        else:
            out.append(w)
    return " ".join(out)


def truncate(text, max_len):
    """Truncate ``text`` to ``max_len`` characters, appending an ellipsis.

    Unchanged when len(text) <= max_len. When longer and max_len >= 3:
    the first (max_len - 3) characters + '...' (exactly max_len long).
    max_len < 3 -> ''.
    """
    if max_len < 3:
        return ""
    t = text or ""
    if len(t) <= max_len:
        return t
    return t[: max_len - 3] + "..."
