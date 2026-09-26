"""bench.score.m1 — the M1 rubric: "compiles + passes its own 3-case test
suite (auto)".

Input (the runner's generated-artifact payload for one M1 task):
  {"module": <str source>, "suite": <str source | None>}
  * ``module`` — the code the subagent generated (the M1 single-file
    code-gen output).
  * ``suite``  — the 3-case test suite the subagent was asked to co-generate
    (the S5.2 "its own 3-case test suite"). The golden shape is
    ``bench/goldens/m1/test_module.py``.

Scoring (auto, no LLM-judge — S5.3):
  1. COMPILE: ``py_compile`` the generated module (a syntax error is a fail).
  2. RUN THE SUITE: write the module + suite to a scratch dir and run
     ``python -m unittest`` (the box python) against them; exit 0 = the
     suite passes. A non-zero exit or a compile failure is a fail.
  ``pass`` = compiled AND suite exit 0. ``score`` = 1.0 when both hold,
  0.0 otherwise (a binary auto-rubric, the S5.3 "machine-scored" shape).

The hermetic S5.8 golden in/out pair: the golden module + suite (bench.
goldens.m1_*) -> pass; a module with a syntax error -> fail.

Stdlib-only: ``py_compile`` + ``subprocess`` + ``pathlib`` + ``tempfile``.
"""

from __future__ import annotations

import py_compile
import subprocess
import sys
import tempfile
from pathlib import Path

from bench.score._common import verdict


def _default_python() -> str:
    # the box python (the hermetic-suite interpreter convention: a python3
    # that passes `import yaml` / has the box deps; /usr/bin/python3.12
    # on this box). Fallback: the interpreter running the bench.
    import os

    p = os.environ.get("BONSAI2_PYTHON_BIN")
    if p:
        return p
    return sys.executable


def score(
    payload: dict,
    python_bin: str | None = None,
    root: str | Path | None = None,
) -> dict:
    """Score one M1 generated artifact. See the module docstring for the
    payload shape. ``root`` is the goldens dir override (used only to
    locate the golden suite when ``payload['suite']`` is None — the
    runner always supplies the suite; the golden is the hermetic-test
    default)."""
    module = payload.get("module")
    suite = payload.get("suite")
    if not isinstance(module, str) or not module.strip():
        return verdict(False, 0.0, "M1: no generated module in payload")
    # a missing suite falls back to the golden 3-case suite (the S5.2 "its
    # own 3-case test suite" — the golden shape the subagent is asked to
    # match; the runner supplies the subagent's own suite when it co-gen).
    if not isinstance(suite, str) or not suite.strip():
        try:
            from bench.score import goldens

            suite = goldens.m1_test_source(root)
        except FileNotFoundError as exc:
            return verdict(False, 0.0, f"M1: no suite and golden missing: {exc}")

    with tempfile.TemporaryDirectory(prefix="bonsai2-m1-") as td:
        tdir = Path(td)
        mod_path = tdir / "module.py"
        test_path = tdir / "test_module.py"
        mod_path.write_text(module)
        test_path.write_text(suite)
        # 1) compile the module (syntax check) — the S5.2 "compiles" half.
        try:
            py_compile.compile(str(mod_path), doraise=True)
        except py_compile.PyCompileError as exc:
            return verdict(False, 0.0, f"M1: module does not compile: {exc.msg}")
        # 2) run the 3-case suite against the module — the S5.2 "passes its
        # own 3-case test suite" half.
        py = python_bin or _default_python()
        try:
            p = subprocess.run(
                [py, "-m", "unittest", "test_module"],
                cwd=str(tdir),
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
        except (subprocess.TimeoutExpired, OSError) as exc:
            return verdict(False, 0.0, f"M1: suite run error: {exc!r}")
        ok = p.returncode == 0
        tail = (p.stdout + p.stderr).strip().splitlines()[-1:] or [""]
        return verdict(
            ok,
            1.0 if ok else 0.0,
            f"M1: suite {'passed' if ok else 'failed'} (exit {p.returncode}): {tail[0]}",
            {"exit_code": p.returncode, "suite_tail": tail[0]},
        )
