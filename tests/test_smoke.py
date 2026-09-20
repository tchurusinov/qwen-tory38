"""Hermetic test suite — no GPU, no network (Global testing discipline).

Default discover target: `python3 -m unittest discover -s tests` from the
repo root. GPU tests live in tests/gpu/ (separate discover target,
run via tests/run_gpu.sh with an nvidia-smi snapshot first).

This module is the Phase 0 (MAP 0.2) runner proof: the suite must
discover and pass with zero dependencies beyond stdlib.
"""

import unittest


class TestSuiteSmoke(unittest.TestCase):
    """MAP 0.2 — test runner + conventions exist and work hermetic."""

    def test_suite_is_hermetic(self):
        # Discovery must find this test itself: the runner works.
        self.assertTrue(True)

    def test_no_gpu_network_imports(self):
        # stdlib-only import surface: requests/yaml/docker must not be
        # required by the hermetic suite (S6.6 dependency surface is
        # stdlib + requests at runtime, and yaml only for gate checks).
        import sys

        for mod in ("requests", "yaml"):
            self.assertNotIn(mod, sys.modules,
                             f"hermetic test imported {mod}")


if __name__ == "__main__":
    unittest.main()
