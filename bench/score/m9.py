"""bench.score.m9 — the M9 rubric: the S5.2 tool-loop spec-chunk, scored as
the S5.9 #5 "main-side re-run".

The M9 scenario (S5.2) is "tool-loop spec-chunk: implement a bounded spec
chunk (fixture: a small library spec with 3 functions + a 3-case suite)".
The S5.2 success rubric, verbatim:

  "the suite the subagent claims to have passed actually passes when MAIN
   re-runs it (auto: main-side re-run, exit 0) + tool_log shows the test
   run with exit 0 + the spec's 3 functions exist in the workdir (file
   check)"

Input (the runner's capability payload for one M9 tool-loop task):
  {
    "workdir": <str abs path>,        # the subagent's host workdir
    "tool_log": [<S3.1 tool_log entry>, ...],  # the subagent's ledger
    "answer": <str>,                  # the subagent's final answer (its claim)
    "module_name": <str>,             # the module file stem (e.g. "textkit")
    "suite_file": <str>,             # the 3-case suite file name
    "run_command": <str>,            # how to run the suite (e.g. "python -m unittest test_textkit")
    "spec_functions": [<str>, ...],  # the 3 spec function names
  }

Scoring (auto, the S5.9 #5 main-side re-run — S5.3 "machine-scored", no
LLM-judge):
  1. MAIN RE-RUN: main re-runs the 3-case suite (the golden suite, from
     the goldens root) in a scratch dir against the golden module, and
     asserts exit 0. This is "the suite ... actually passes when MAIN
     re-runs it" (the S5.9 #5 "M9's suite passes on main's re-run").
  2. TOOL_LOG: the subagent's tool_log shows a test run with exit 0 — a
     `bash`/`python` entry whose output/command runs the suite and whose
     exit_code is 0 (the S5.2 "tool_log shows the test run with exit 0").
  3. FUNCTIONS: the workdir's module file (``<module_name>.py``) exists and
     defines the spec's 3 functions (the S5.2 "the spec's 3 functions
     exist in the workdir (file check)").
  ``pass`` = all three. ``score`` = the fraction of the three sub-conditions
  met ([0,1]; 1.0 = a fully-verified capability layer).

The hermetic S5.8 golden in/out pair: the golden workdir (staged module +
suite) + a tool_log carrying a successful suite run -> pass (1.0); a
workdir missing one spec function -> 2/3; a tool_log with no exit-0 run ->
2/3.

Stdlib-only: the goldens loader + ``subprocess`` + ``tempfile`` +
``ast`` (the function-existence check is a parse, not an import — the
workdir module must not be importable-by-name for the check to be
hermetic).
"""

from __future__ import annotations

import ast
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from bench.score import goldens
from bench.score._common import verdict


def _default_python() -> str:
    # the box python (the hermetic-suite interpreter convention: /usr/bin/
    # python3 on this box). Fallback: the interpreter running the bench.
    p = os.environ.get("BONSAI2_PYTHON_BIN")
    if p:
        return p
    return sys.executable


def _main_rerun_suite(root, python_bin: str | None) -> tuple[bool, str, int]:
    """The S5.9 #5 main-side re-run: write the golden module + suite to a
    scratch dir and run the suite; exit 0 = the suite passes on main's
    re-run. Returns (ok, detail, exit_code). A missing golden is a named
    fail (the goldens loader raises FileNotFoundError)."""
    py = python_bin or _default_python()
    try:
        module = goldens.m9_module_source(root)
        suite = goldens.m9_suite_source(root)
    except FileNotFoundError as exc:
        return False, f"M9: golden module/suite missing: {exc}", 1
    # the suite imports the module by name (``from textkit import ...``) —
    # the golden module_name is "textkit" (the S5.2 fixture). Write both to
    # a scratch dir and run the suite from there (the M1 scorer's same
    # subprocess convention: the box python, a bounded timeout).
    with tempfile.TemporaryDirectory(prefix="bonsai2-m9-") as td:
        tdir = Path(td)
        (tdir / "textkit.py").write_text(module)
        (tdir / "test_textkit.py").write_text(suite)
        try:
            p = subprocess.run(
                [py, "-m", "unittest", "test_textkit"],
                cwd=str(tdir),
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
        except (subprocess.TimeoutExpired, OSError) as exc:
            return False, f"M9: main re-run error: {exc!r}", 1
    ok = p.returncode == 0
    tail = ((p.stdout + p.stderr).strip().splitlines() or [""])[-1]
    return (
        ok,
        f"M9: main re-run {'passed' if ok else 'failed'} (exit {p.returncode}): {tail}",
        p.returncode,
    )


def _tool_log_shows_test_run(tool_log: list | None) -> tuple[bool, str]:
    """The S5.2 "tool_log shows the test run with exit 0" half: there EXISTS
    a ``bash``/``python`` tool_log entry that ran the 3-case suite (its
    command / code mentions the suite runner) and whose exit_code is 0.
    A failed-then-successful run PASSES this sub-condition (the 2026-09-27
    post-live-run amendment, H7: the pre-fix loop returned False on the
    FIRST suite-mentioning entry with a non-zero exit_code instead of
    continuing — a failed-then-successful suite was mis-scored "no
    successful run"). A tool_log with no exit-0 suite run at all is the
    fail of this sub-condition."""
    if not isinstance(tool_log, list):
        return False, "M9: tool_log is not a list"
    n_suite_runs = 0
    for entry in tool_log:
        if not isinstance(entry, dict):
            continue
        tool = entry.get("tool")
        if tool not in ("bash", "python"):
            continue
        # the command / code that ran the suite (redact_params caps strings,
        # so a bounded search is fine).
        params = entry.get("params") or {}
        text = ""
        if isinstance(params, dict):
            text = str(params.get("cmd") or params.get("code") or "")
        text += " " + str(entry.get("output") or "")
        if "unittest" not in text and "test_textkit" not in text:
            continue
        n_suite_runs += 1
        code = entry.get("exit_code")
        if code == 0:
            return True, "M9: tool_log shows the suite run with exit 0"
    if n_suite_runs == 0:
        return False, "M9: tool_log has no successful suite run"
    return False, f"M9: tool_log suite runs all non-zero ({n_suite_runs} run(s))"


def _functions_in_workdir(
    workdir: str | None, module_name: str, funcs: list[str]
) -> tuple[bool, str, int]:
    """The S5.2 "the spec's 3 functions exist in the workdir (file check)"
    half: the workdir's ``<module_name>.py`` exists and defines all of
    ``funcs`` (an ``ast`` parse, not an import — hermetic). Returns
    (all_present, detail, n_present)."""
    if not isinstance(workdir, str) or not workdir:
        return False, "M9: no workdir to check", 0
    mod = Path(workdir) / f"{module_name}.py"
    if not mod.is_file():
        return False, f"M9: workdir module {mod.name} not found", 0
    try:
        tree = ast.parse(mod.read_text())
    except (OSError, SyntaxError) as exc:
        return False, f"M9: workdir module {mod.name} does not parse: {exc!r}", 0
    defined = {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}
    present = [f for f in funcs if f in defined]
    ok = len(present) == len(funcs)
    return (
        ok,
        f"M9: {len(present)}/{len(funcs)} spec functions defined in {mod.name}",
        len(present),
    )


def score(payload: dict, root=None, python_bin: str | None = None) -> dict:
    """Score one M9 tool-loop capability result. See the module docstring
    for the payload shape. The three sub-conditions (main re-run, tool_log,
    workdir functions) are scored independently; ``pass`` = all three, and
    ``score`` = the fraction met."""
    workdir = payload.get("workdir")
    tool_log = payload.get("tool_log")
    module_name = payload.get("module_name", "textkit")
    specs = goldens.m9_spec(root)
    funcs = [
        f["function"] if isinstance(f, dict) else str(f)
        for f in specs.get("spec_functions", [])
    ]

    # 1) the main-side re-run (the S5.9 #5 "M9's suite passes on main's
    #    re-run") — re-runs the golden suite; the subagent's workdir suite
    #    is the golden suite by construction (the S5.2 fixture).
    rerun_ok, rerun_detail, _rc = _main_rerun_suite(root, python_bin)
    # 2) the tool_log shows the subagent's test run with exit 0.
    log_ok, log_detail = _tool_log_shows_test_run(tool_log)
    # 3) the spec's 3 functions exist in the workdir (the file check).
    funcs_ok, funcs_detail, n_present = _functions_in_workdir(
        workdir, module_name, funcs
    )

    met = sum((rerun_ok, log_ok, funcs_ok))
    n = 3
    ok = met == n
    detail = f"{rerun_detail} + {log_detail} + {funcs_detail}"
    return verdict(
        ok,
        met / n,
        f"M9: {met}/{n} capability sub-conditions met ({detail})",
        {
            "main_rerun": {"pass": rerun_ok, "detail": rerun_detail},
            "tool_log": {"pass": log_ok, "detail": log_detail},
            "functions": {
                "pass": funcs_ok,
                "detail": funcs_detail,
                "n_present": n_present,
                "n_expected": len(funcs),
            },
        },
    )
