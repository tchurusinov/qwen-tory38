"""bench.score.m10 — the M10 rubric: the S5.2 tool-loop test batch, scored as
the S5.9 #5 "main-side re-run".

The M10 scenario (S5.2) is "tool-loop test batch: run a 10-file test batch,
produce a structured report (per-file pass/fail, durations)". The S5.2
success rubric, verbatim:

  "report JSON parseable + 10/10 files covered + main re-runs 2/10 files
   to confirm the pass/fail verdicts agree (auto, 2/10 spot)"

Input (the runner's capability payload for one M10 tool-loop task):
  {
    "report": <str>,   # the subagent's final answer (the structured
                       #   report JSON, emitted on a line by itself)
  }

Scoring (auto, the S5.9 #5 main-side re-run — S5.3 "machine-scored", no
LLM-judge). Three independent sub-conditions:
  1. PARSE: the answer carries a JSON object that parses (the S5.2
     "report JSON parseable" half).
  2. COVERAGE: the report covers all 10 batch files with a per-file
     pass/fail verdict (the S5.2 "10/10 files covered" half; coverage,
     not pass-rate, is the rubric — the same convention as the M2
     scorer).
  3. RERUN-AGREE: MAIN re-runs 2/10 files in a scratch dir (the 2/10
     spot) and the observed verdicts AGREE with the report's claimed
     verdicts for those files (the S5.9 #5 "verdicts agree"). The spot
     is the two SEEDED FAIL files of the golden batch (files_003.py,
     files_007.py — bench/goldens/m10, the goldens loader's documented
     spot set): re-running the fails is what catches a report that
     claims a pass where the module fails (the pass verdicts are
     confirmed by the coverage half).
  ``pass`` = all three. ``score`` = the blend of the three halves
  ([0,1]: parse is 0/1, coverage is covered/10, agree is
  agreed/2 — the S5.3 rubric-score shape).

The hermetic S5.8 golden in/out pair: the golden report
(bench/goldens/m10/expected_report.json — 8 pass, files_003/007 fail)
-> pass (1.0); a report claiming files_003 passed -> the re-run observes
the fail, the verdicts disagree -> 2/3; a 9-file report -> the coverage
half scores 0.9.

Stdlib-only: ``json`` + ``subprocess`` + ``tempfile`` + the goldens
loader + the shared verdict helper.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from bench.score import goldens
from bench.score._common import verdict
from bench.score.m2 import _VALID_STATUS, EXPECTED_FILES

# The S5.9 #5 2/10 spot: the golden batch's two SEEDED FAIL modules (the
# goldens loader's documented spot set — bench/goldens/m10 files_003 /
# files_007; re-running the fails is what catches a report that claims a
# pass where the module fails).
SPOT_FILES = ("files_003.py", "files_007.py")


def _default_python() -> str:
    # the box python (the hermetic-suite interpreter convention: /usr/bin/
    # python3 on this box). Fallback: the interpreter running the bench.
    p = os.environ.get("BONSAI2_PYTHON_BIN")
    if p:
        return p
    return sys.executable


def _parse_report(report) -> tuple[dict | None, str]:
    """Parse the subagent's report (the S5.2 "report JSON parseable"
    half). ``report`` is the answer string; the report is the first
    balanced ``{...}`` object in it (the S5.2 contract: "emit ONE JSON
    object on a line by itself"). Returns (parsed | None, detail)."""
    if not isinstance(report, str) or not report.strip():
        return None, "M10: no report in payload"
    import re  # local: the extraction is a parse detail, not an import surface

    m = re.search(r"\{.*\}", report, re.DOTALL)
    candidate = m.group(0) if m else report
    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError as exc:
        return None, f"M10: report JSON not parseable: {exc}"
    if not isinstance(parsed, dict):
        return None, "M10: report JSON is not an object"
    return parsed, "M10: report JSON parsed"


def _covered_files(report: dict) -> int:
    """The number of DISTINCT files the report covers with a pass/fail
    status (the S5.2 "10/10 files covered" metric — the M2 scorer's
    convention)."""
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


def _claimed_status(report: dict, name: str) -> str | None:
    """The verdict the report claims for one batch file (or None when the
    file is not covered with a recognized status)."""
    files = report.get("files")
    if not isinstance(files, list):
        return None
    for entry in files:
        if (
            isinstance(entry, dict)
            and entry.get("file") == name
            and entry.get("status") in _VALID_STATUS
        ):
            return entry["status"]
    return None


def _main_rerun_spot(
    root, python_bin: str | None
) -> tuple[dict[str, str | None], str, int]:
    """The S5.9 #5 main-side re-run (the 2/10 spot): copy the SPOT_FILES
    golden batch modules to a scratch dir and run each through
    ``python -m unittest``; the observed verdict per file is
    exit 0 -> "pass", non-zero -> "fail". Returns (observed per file,
    detail, n_rerun). A missing golden is a named fail (the goldens
    loader raises FileNotFoundError)."""
    py = python_bin or _default_python()
    observed: dict[str, str | None] = {name: None for name in SPOT_FILES}
    try:
        sources = goldens.m10_batch_sources(root)
    except FileNotFoundError as exc:
        return observed, f"M10: golden batch missing: {exc}", 0
    n_rerun = 0
    tail = ""
    with tempfile.TemporaryDirectory(prefix="bonsai2-m10-") as td:
        tdir = Path(td)
        for name in SPOT_FILES:
            (tdir / name).write_text(sources[name])
            try:
                p = subprocess.run(
                    [py, "-m", "unittest", name.removesuffix(".py")],
                    cwd=str(tdir),
                    capture_output=True,
                    text=True,
                    timeout=60,
                    check=False,
                )
            except (subprocess.TimeoutExpired, OSError) as exc:
                tail = f"{name}: error {exc!r}"
                continue
            observed[name] = "pass" if p.returncode == 0 else "fail"
            n_rerun += 1
            tail = f"{name}: exit {p.returncode} ({'pass' if p.returncode == 0 else 'fail'})"
    return (
        observed,
        f"M10: main re-ran {n_rerun}/{len(SPOT_FILES)} spot files; last: {tail}",
        n_rerun,
    )


def score(payload: dict, root=None, python_bin: str | None = None) -> dict:
    """Score one M10 tool-loop capability result. See the module
    docstring for the payload shape. The three sub-conditions (parse,
    coverage, re-run agreement) are scored independently; ``pass`` = all
    three, and ``score`` = the blend of the three halves."""
    # 1) the report JSON is parseable (the S5.2 "report JSON parseable").
    parsed, parse_detail = _parse_report(payload.get("report"))
    parse_ok = parsed is not None
    # 2) 10/10 files covered (the S5.2 "10/10 files covered" half).
    covered = _covered_files(parsed) if parsed is not None else 0
    cover_ok = covered >= EXPECTED_FILES
    cover_frac = min(covered / EXPECTED_FILES, 1.0)
    # 3) main re-runs 2/10 and the verdicts agree (the S5.9 #5 half).
    observed, rerun_detail, n_rerun = _main_rerun_spot(root, python_bin)
    agree = 0
    per_file: dict[str, dict] = {}
    for name in SPOT_FILES:
        claim = _claimed_status(parsed, name) if parsed is not None else None
        obs = observed.get(name)
        ok = obs is not None and claim is not None and claim == obs
        agree += 1 if ok else 0
        per_file[name] = {
            "claimed": claim,
            "observed": obs,
            "agree": bool(ok),
        }
    agree_ok = n_rerun == len(SPOT_FILES) and agree == len(SPOT_FILES)
    agree_frac = agree / len(SPOT_FILES)

    blend = (
        (1.0 if parse_ok else 0.0) + cover_frac + (agree_frac if n_rerun else 0.0)
    ) / 3.0
    ok = parse_ok and cover_ok and agree_ok
    detail = (
        f"M10: {'all' if ok else 'NOT all'} sub-conditions "
        f"({parse_detail}; {covered}/{EXPECTED_FILES} files covered; "
        f"{agree}/{len(SPOT_FILES)} spot verdicts agree: {rerun_detail})"
    )
    return verdict(
        ok,
        blend,
        detail,
        {
            "parse": {"pass": parse_ok, "detail": parse_detail},
            "coverage": {
                "pass": cover_ok,
                "n_files": covered,
                "n_expected": EXPECTED_FILES,
            },
            "rerun": {
                "pass": agree_ok,
                "n_rerun": n_rerun,
                "n_agree": agree,
                "spot_files": per_file,
            },
        },
    )
