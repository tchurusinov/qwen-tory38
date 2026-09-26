"""bench.score.m4 — the M4 rubric: "draft finds >= 80% of a seeded 10-bug
fixture (auto, seeded)".

Input (the runner's diff-review payload for one M4 task):
  {"draft": <str | list>}
  * ``draft`` as a ``list`` — the bug ids the subagent cited (the clean
    case; the runner parses the subagent's answer into ids when it can).
  * ``draft`` as a ``str``  — the subagent's raw review text; the scorer
    extracts every ``BUG-NN`` token it cites (the S5.2 "draft finds" half:
    the subagent cites the seeded ids it found).

Scoring (auto, binary threshold — S5.3 "machine-scored"):
  * the ground truth is the 10 seeded ids in ``bench/goldens/m4/bugs.json``
    (the S5.2 "seeded 10-bug fixture" — a repo fixture, not a live diff).
  * ``n_found`` = the size of the intersection of the subagent's cited ids
    with the 10 seeded ids.
  * ``pass`` = ``n_found >= 8`` (>= 80% of 10 — the S5.2 rubric threshold).
  * ``score`` = ``n_found / 10`` (the S5.3 rubric score, [0,1]).

The hermetic S5.8 golden in/out pair: a draft citing all 10 -> pass (1.0);
a draft citing 7 -> fail (0.7). A draft that cites ids NOT in the seed is
not penalized (the intersection is the metric — extra findings are noise,
not a miss).

Stdlib-only: ``re`` + the goldens loader + the shared verdict helper.
"""

from __future__ import annotations

import re

from bench.score import goldens
from bench.score._common import verdict

# The S5.2 M4 threshold: >= 80% of the seeded 10.
THRESHOLD_PCT = 0.8
BUG_ID_RE = re.compile(r"BUG-\d{2}")


def _cited_ids(draft) -> set:
    """The set of BUG-NN ids the subagent cited. A list is treated as the
    ids directly; a string is scanned for BUG-NN tokens (case-insensitive
    — the subagent may cite them in any case)."""
    if isinstance(draft, (list, tuple, set)):
        return {str(x).upper() for x in draft if str(x).strip()}
    if isinstance(draft, str):
        return {m.upper() for m in BUG_ID_RE.findall(draft)}
    return set()


def score(payload: dict, root=None) -> dict:
    """Score one M4 diff-review draft. See the module docstring for the
    payload shape. ``root`` is the goldens dir override (the hermetic
    S5.8 test points at a tmp copy)."""
    draft = payload.get("draft")
    cited = _cited_ids(draft)
    try:
        bugs = goldens.m4_bugs(root)
    except FileNotFoundError as exc:
        return verdict(False, 0.0, f"M4: golden bugs missing: {exc}")
    seeded = {b.get("id", "").upper() for b in bugs if isinstance(b, dict)}
    seeded.discard("")
    n_seeded = len(seeded)
    found = sorted(cited & seeded)
    n_found = len(found)
    if n_seeded == 0:
        return verdict(False, 0.0, "M4: no seeded bugs in golden", {"n_found": 0})
    threshold = round(THRESHOLD_PCT * n_seeded)
    ok = n_found >= threshold
    return verdict(
        ok,
        n_found / n_seeded,
        f"M4: found {n_found}/{n_seeded} seeded bugs "
        f"({'>= 80%' if ok else '< 80%'}, threshold {threshold})",
        {
            "n_found": n_found,
            "n_seeded": n_seeded,
            "threshold": threshold,
            "found": found,
            "cited": sorted(cited),
        },
    )
