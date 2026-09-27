"""bench.score — the S5.3/S5.4 per-rubric scorer plugins.

One module per rubric: ``m1`` (compiles + its own 3-case suite), ``m2``
(report JSON parseable + 10/10 files covered), ``m4`` (seeded 10-bug
fixture diff, >= 80% found), ``m5`` (5 QA questions, golden-file binary
scoring), ``m7`` (t/s at ceiling + zero 5xx), ``m8`` (main t/s within
``main_guardrail_pct`` of M6 -> pass/fail), ``m3`` (the S5.3 semi-auto:
auto shape score * 0.7 + main spot-check * 0.3), ``m6`` (the
main-only control of M1-M5, same rubrics as the subagent rows), ``m9``
(the S5.2 tool-loop spec-chunk: main-side re-run exit 0 + tool_log shows
the test run with exit 0 + the spec's 3 functions exist in the workdir)
and ``m10`` (the S5.2 tool-loop test batch: report JSON parseable +
10/10 files covered + main re-runs 2/10 files, verdicts agree).

Each ``score(...)`` is a PURE function: given the task execution data
(the runner's TaskResults) + the goldens (loaded from
``bench/goldens/`` via :mod:`bench.score.goldens`) it returns a verdict
dict ``{"pass": bool, "score": float, "detail": str, "extra": dict}``.
The S5.8 "rubric tests" are golden in/out pairs: a canned input + the
expected verdict, hermetic (no endpoints). The M4 seeded 10-bug diff and
the M5 QA golden are versioned in ``bench/goldens/`` (repo fixtures, the
S5.3 "golden files in bench/goldens/" rule).

The verdict ``pass`` is the S5.2 row verdict (a guardrail breach or a
rubric miss flips the row to ``fail`` while the run still exits 0 —
S5.4). The ``score`` is a [0,1] rubric score (the M3 semi-auto blend
lives here, S5.3).
"""

from __future__ import annotations

from . import _common, goldens, m1, m2, m3, m4, m5, m6, m7, m8, m9, m10

__all__ = [
    "SCORES",
    "_common",
    "dispatch",
    "goldens",
    "m1",
    "m2",
    "m3",
    "m4",
    "m5",
    "m6",
    "m7",
    "m8",
    "m9",
    "m10",
]

# The S5.3 rubric-name -> scorer-module mapping (the scenarios' "rubric"
# column is this key). m6 is the control's dispatch (the runner routes
# M6's M1-M5 sub-tasks through the SAME rubric modules with the main
# endpoint's TaskResults). m9/m10 are the capability-layer rubrics (MAP
# 4.9): their payload is the tool-loop capability result (workdir +
# tool_log + answer + the S5.2 fixture ids), scored by the S5.9 #5
# main-side re-run.
SCORES: dict = {
    "M1": m1,
    "M2": m2,
    "M3": m3,
    "M4": m4,
    "M5": m5,
    "M7": m7,
    "M8": m8,
    "M9": m9,
    "M10": m10,
}


def dispatch(rubric: str, *args, **kwargs):
    """Route a rubric name to its scorer's ``score``. ``rubric`` must be
    in the S5.3 set (M1/M2/M3/M4/M5/M7/M8/M9/M10 — M6 is the runner's
    control, routed through these per sub-task). KeyError for an unknown
    rubric (the runner's config-validation path is upstream)."""
    mod = SCORES[rubric]
    return mod.score(*args, **kwargs)
