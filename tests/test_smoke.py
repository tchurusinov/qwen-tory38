"""Hermetic test suite — no GPU, no network (Global testing discipline).

Default discover target: `python3 -m unittest discover -s tests -t .` from the
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
            p for p in root.rglob("*.py") if p.is_file() and "gpu" not in p.parts
        )
        self.assertGreaterEqual(len(files), 1, "hermetic suite has no test modules")
        allowed = set(sys.stdlib_module_names)
        # Documented edge-dep: `yaml` (PyYAML) may appear ONLY as a
        # LOCAL import (inside a function body) at the YAML read/parse
        # edge — the box dep the MAP 1.3/1.4/1.5 tooling shares (the
        # same convention as tests/test_model_fetch.py, which loads the
        # bin/ tools via importlib.util and keeps its own top-level
        # imports stdlib). A TOP-LEVEL `import yaml` in a hermetic test
        # module is still caught by the assertion below (it would break
        # the suite on a box without PyYAML).
        allowed_local = allowed | {"yaml"}
        for f in files:
            tree = ast.parse(f.read_text(), filename=str(f))
            top_level: list[str] = []
            local: list[str] = []

            def _nested_scope(node) -> bool:
                # A node introduces a NEW (non-module) scope iff it is a
                # function/class/lambda or a comprehension. G3c round-3
                # P2: module-scope control-flow blocks (try/if/while/for/
                # with/match) do NOT create a scope — an import under a
                # module-level `try: ... except ImportError:` still
                # executes at import time, so it must count as
                # TOP-LEVEL (and fail the stdlib-only assert), not "local".
                return isinstance(
                    node,
                    (
                        ast.FunctionDef,
                        ast.AsyncFunctionDef,
                        ast.ClassDef,
                        ast.Lambda,
                        ast.ListComp,
                        ast.SetComp,
                        ast.DictComp,
                        ast.GeneratorExp,
                        ast.comprehension,
                    ),
                )

            def walk(node, top: bool, top_level=top_level, local=local) -> None:
                # `top` = "node's DIRECT children execute at module scope".
                # It stays True through module-scope statements and their
                # control-flow containers, and flips False only when we
                # descend into a real nested scope (see _nested_scope).
                for child in ast.iter_child_nodes(node):
                    if isinstance(child, ast.Import):
                        for alias in child.names:
                            (top_level if top else local).append(alias.name)
                    elif isinstance(child, ast.ImportFrom):
                        # Relative imports (level > 0, e.g.
                        # `from .mock_engine import x`) resolve against
                        # the local package, never a top-level module —
                        # they cannot pull in a third-party dep, so the
                        # stdlib-only check skips them. This is what
                        # lets tests/fixtures/ (MAP P3) use conventional
                        # in-package relative imports (G3c round 11
                        # P1-3: the earlier `level == 0` assert would
                        # have blocked that structure). Only ABSOLUTE
                        # imports (level == 0) are checked against
                        # stdlib.
                        if child.level > 0:
                            continue
                        (top_level if top else local).append(child.module or "")
                    else:
                        walk(child, top and not _nested_scope(child))

            walk(tree, True)
            for n in top_level:
                top = n.split(".")[0]
                self.assertIn(
                    top,
                    allowed,
                    f"tests/{f.name}: TOP-LEVEL import of non-stdlib "
                    f"module {top!r} (hermetic suite = stdlib only, S6.6)",
                )
            for n in local:
                top = n.split(".")[0]
                self.assertIn(
                    top,
                    allowed_local,
                    f"tests/{f.name}: local import of non-stdlib "
                    f"module {top!r} outside the documented yaml "
                    f"edge-dep allowlist (hermetic suite = stdlib only, S6.6)",
                )


if __name__ == "__main__":
    unittest.main()
