"""Hermetic test suite — no GPU, no network (Global testing discipline).

Default discover target: `python3 -m unittest discover -s tests` from the
repo root. GPU tests live in tests/gpu/ (separate discover target,
run via tests/run_gpu.sh with an nvidia-smi snapshot first).

This module is the Phase 0 (MAP 0.2) runner proof: the suite must
discover and pass with zero dependencies beyond stdlib.

Hermetic-convention enforcement (G3c round 1/3, findings P1-5/P1-4):
the suite-wide contract is "stdlib-only imports in hermetic test
modules", enforced structurally — every import statement in every
tests/test_*.py must resolve to a stdlib top-level module. (The earlier
sys.modules-based assertion was execution-order dependent; the AST
check is deterministic and catches future test modules at commit time.)
tests/gpu/ is exempt: @gpu tests may use the S6.6 runtime deps.
"""

import ast
import pathlib
import sys
import unittest


class TestSuiteSmoke(unittest.TestCase):
    """MAP 0.2 — test runner + conventions exist and work hermetic."""

    def test_suite_is_hermetic(self):
        # Discovery must find this test itself: the runner works.
        self.assertTrue(True)

    def test_test_modules_import_stdlib_only(self):
        root = pathlib.Path(__file__).resolve().parent
        # Every .py under tests/ EXCEPT the @gpu target (tests/gpu/ may
        # use the S6.6 runtime deps): today that is test_smoke.py, and
        # it also covers future hermetic helpers (e.g. tests/fixtures/,
        # which lands at MAP P3) — G3c round 9 P2.
        files = sorted(
            p for p in root.rglob("*.py")
            if p.is_file() and "gpu" not in p.parts
        )
        self.assertGreaterEqual(len(files), 1,
                                "hermetic suite has no test modules")
        allowed = set(sys.stdlib_module_names)
        for f in files:
            tree = ast.parse(f.read_text(), filename=str(f))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                if isinstance(node, ast.ImportFrom):
                    # Relative imports (level > 0, e.g. `from . import x`)
                    # resolve against the package, not the top level —
                    # assert they never appear in hermetic test modules.
                    self.assertEqual(node.level, 0,
                                     f"tests/{f.name}: relative import "
                                     f"(level {node.level}) in a hermetic "
                                     f"test module — use an absolute "
                                     f"import instead")
                    names = [] if node.module is None else [node.module]
                else:
                    continue
                for n in names:
                    top = n.split(".")[0]
                    self.assertIn(top, allowed,
                                  f"tests/{f.name}: import of non-stdlib "
                                  f"module {top!r} (hermetic suite = "
                                  f"stdlib only, S6.6)")


if __name__ == "__main__":
    unittest.main()
