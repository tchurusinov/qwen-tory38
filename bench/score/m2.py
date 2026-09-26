"""bench.score.m2 — the M2 rubric: "report JSON parseable + 10/10 files
covered (auto)".

Input (the runner's test-batch payload for one M2 task):
  {"report": <str JSON>}
  ``report`` — the structured test-batch report the subagent produced. The
  golden shape (``bench/goldens/m2/expected_report.json``) is the
  10-file report: a top-level object whose ``files`` is a list of 10
  per-file entries, each with a ``file`` name + a ``status`` verdict.

Scoring (auto, binary — S5.3 "machine-scored"):
  1. PARSE: ``json.loads`` the report. A parse failure is a fail (the
     S5.2 "report JSON parseable" half).
  2. COVERAGE: the report covers all 10 files — the ``files`` list has 10
     distinct ``file`` entries, each with a pass/fail ``status`` (the
     S5.2 "10/10 files covered" half). Coverage, not pass-rate, is what is
     scored (a report that covered all 10 with 2 fails still passes the
     rubric — the rubric is about coverage).
  ``pass`` = parsed AND 10/10 covered. ``score`` = covered/10 (so a
  partial report scores proportionally; the S5.3 rubric score is [0,1]).

The hermetic S5.8 golden in/out pair: the golden report -> pass; a report
with 9 files -> score 0.9, fail (not 10/10).

Stdlib-only: ``json`` + the shared verdict helper.
"""

from __future__ import annotations

import json

from bench.score._common import verdict

# The S5.2 M2 batch size: 10 files.
EXPECTED_FILES = 10
_VALID_STATUS = ("pass", "fail")


def _covered_files(report: dict) -> int:
    """The number of DISTINCT files the report covers with a pass/fail
    status (the S5.2 "10/10 files covered" metric). A file counts when it
    has a non-empty ``file`` name AND a recognized ``status``."""
    files = report.get("files")
    if not isinstance(files, list):
        return 0
    names: set[str] = set()
    for entry in files:
        if not isinstance(entry, dict):
            continue
        name = entry.get("file")
        status = entry.get("status")
        if isinstance(name, str) and name and status in _VALID_STATUS:
            names.add(name)
    return len(names)


def score(payload: dict, root=None) -> dict:
    """Score one M2 test-batch report. See the module docstring for the
    payload shape."""
    report = payload.get("report")
    if not isinstance(report, str) or not report.strip():
        return verdict(False, 0.0, "M2: no report JSON in payload")
    # 1) parse — the S5.2 "report JSON parseable" half.
    try:
        parsed = json.loads(report)
    except json.JSONDecodeError as exc:
        return verdict(False, 0.0, f"M2: report JSON not parseable: {exc}")
    if not isinstance(parsed, dict):
        return verdict(False, 0.0, "M2: report JSON is not an object")
    # 2) coverage — the S5.2 "10/10 files covered" half.
    covered = _covered_files(parsed)
    ok = covered >= EXPECTED_FILES
    frac = covered / EXPECTED_FILES
    return verdict(
        ok,
        frac,
        f"M2: report parsed, {covered}/{EXPECTED_FILES} files covered "
        f"({'10/10' if ok else 'incomplete'})",
        {"n_files": covered, "n_expected": EXPECTED_FILES, "parseable": True},
    )
