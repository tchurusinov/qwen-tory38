"""MAP 1.6 / S1.4: hermetic tests for the VRAM formula
(bin/bonsai2_vram.py) — "the VRAM formula as a pure function (given
weights/kv constants -> N, including N=0 and N>8 clamp cases)" (S1.7).

No GPU, no network, stdlib-only (the hermetic-suite convention in
tests/test_smoke.py). The tool is loaded from bin/ via
importlib.util (same convention as tests/test_model_fetch.py: bin/ is
the deploy target, not a test package).

Run: `python3 -m unittest discover -s tests` from the repo root.
"""

import importlib.util
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
_TOOL = REPO_ROOT / "bin" / "bonsai2_vram.py"


def _load_tool():
    spec = importlib.util.spec_from_file_location("bonsai2_vram", str(_TOOL))
    assert spec and spec.loader, f"could not load {_TOOL}"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


vram = _load_tool()

# S1.1 sizes + S1.3 defaults + S1.4 constants (the spec is the source).
PQ2_WEIGHTS_GB = 7.21
PTQ1_WEIGHTS_GB = 5.95
SLOTS = 8
CTX = 262144
PER_SLOT = vram.PER_SLOT_PER_TOKEN_GB  # 0.00000176
BUDGET_GB = 11.5


class TestInstanceFootprint(unittest.TestCase):
    """S1.4: instance_footprint_GB = weights_GB + slots*ctx*const."""

    def test_spec_worked_example_kv_constant(self):
        # S1.4 anchor: 8 x 262144 x 0.00000176 = 3.69 GB (the spec's
        # "3.7 GB KV" community-bench number, derived).
        self.assertAlmostEqual(SLOTS * CTX * PER_SLOT, 3.69, delta=0.01)

    def test_spec_sanity_footprint(self):
        # S1.4 "Sanity: 7.21 + 3.7 = 10.91 GB".
        fp = vram.instance_footprint_gb(PQ2_WEIGHTS_GB, SLOTS, CTX)
        self.assertAlmostEqual(fp, 10.91, delta=0.01)

    def test_ptq1_fallback_footprint(self):
        # S1.1: the fallback quant is ~18% smaller on weights; the KV
        # term is identical (same slots/ctx/constant).
        fp = vram.instance_footprint_gb(PTQ1_WEIGHTS_GB, SLOTS, CTX)
        self.assertAlmostEqual(fp, 5.95 + 3.69, delta=0.01)

    def test_ctx_scaling_linear(self):
        # S1.3 note: "KV scales ~linearly" — halving the ctx halves the
        # KV term.
        full = vram.instance_footprint_gb(1.0, 8, 16384)
        half = vram.instance_footprint_gb(1.0, 8, 8192)
        self.assertAlmostEqual(
            full - half, half - vram.instance_footprint_gb(1.0, 8, 0), delta=1e-9
        )

    def test_zero_slots_and_ctx(self):
        self.assertEqual(vram.instance_footprint_gb(7.21, 0, 262144), 7.21)
        self.assertEqual(vram.instance_footprint_gb(7.21, 8, 0), 7.21)

    def test_negative_slots_rejected(self):
        with self.assertRaises(ValueError):
            vram.instance_footprint_gb(7.21, -1, 262144)
        with self.assertRaises(ValueError):
            vram.instance_footprint_gb(7.21, 8, -1)

    def test_constant_is_the_spec_anchor(self):
        # The S1.4 constant itself (the derived-from-bench value).
        self.assertEqual(PER_SLOT, 0.00000176)


class TestInstanceCount(unittest.TestCase):
    """S1.4: N = floor(budget/footprint), clamped [0, 8]."""

    def test_s0_worked_example_n3(self):
        # S0: floor(37.2 / 11.5) = 3.
        self.assertEqual(vram.instance_count(37.2, BUDGET_GB), 3)

    def test_default_pool_inputs_give_n3(self):
        # The S0 table via the budget function (121.6 - 76.4 - 8.0).
        budget = vram.subagent_budget_gb(121.6, 76.4, 8.0)
        self.assertAlmostEqual(budget, 37.2, delta=1e-9)
        self.assertEqual(vram.instance_count(budget, BUDGET_GB), 3)

    def test_exact_boundary_is_inclusive(self):
        # budget == 3 * 11.5 -> exactly 3 (floor of an exact integer).
        self.assertEqual(vram.instance_count(3 * BUDGET_GB, BUDGET_GB), 3)

    def test_just_below_boundary(self):
        self.assertEqual(vram.instance_count(3 * BUDGET_GB - 0.001, BUDGET_GB), 2)

    def test_max_clamp_n8(self):
        # N>8 clamp case (S1.7): a pool that fits more than 8 instances
        # is clamped to 8 (64 slots total, the demo ceiling, S1.4).
        self.assertEqual(vram.instance_count(8 * BUDGET_GB + 100.0, BUDGET_GB), 8)
        self.assertEqual(vram.instance_count(1000.0, BUDGET_GB), 8)

    def test_n0_when_budget_zero(self):
        self.assertEqual(vram.instance_count(0.0, BUDGET_GB), 0)

    def test_n0_when_budget_negative(self):
        # S2.3's `max(0, ...)`: main stack + reserved already consume
        # the pool -> main-only, not a negative instance count.
        self.assertEqual(vram.instance_count(-5.0, BUDGET_GB), 0)
        self.assertEqual(
            vram.instance_count(vram.subagent_budget_gb(121.6, 130.0, 8.0), BUDGET_GB),
            0,
        )

    def test_n0_when_budget_below_one_footprint(self):
        self.assertEqual(vram.instance_count(11.49, BUDGET_GB), 0)

    def test_n0_when_footprint_non_positive(self):
        # ZeroDivision guard: a non-positive footprint is "no instance
        # can be allocated", not an exception (S1.7 N=0 cases).
        self.assertEqual(vram.instance_count(37.2, 0.0), 0)
        self.assertEqual(vram.instance_count(37.2, -1.0), 0)

    def test_one_instance_minimum(self):
        self.assertEqual(vram.instance_count(BUDGET_GB + 0.01, BUDGET_GB), 1)

    def test_budget_is_the_budget_not_the_footprint(self):
        # S1.4 divides the pool by the 11.5 GB *budget* (footprint +
        # 5% margin), not by the raw 10.91 footprint: a pool of
        # 37.2/10.91 = 3.41 would also floor to 3, but at 3*11.5 +
        # 10.9 the two denominators diverge — the spec says budget.
        pool = 3 * BUDGET_GB + 10.9  # 45.4
        self.assertEqual(vram.instance_count(pool, BUDGET_GB), 3)  # 45.4/11.5 = 3.94


class TestSubagentBudget(unittest.TestCase):
    def test_s0_subtraction(self):
        self.assertAlmostEqual(
            vram.subagent_budget_gb(121.6, 76.4, 8.0), 37.2, delta=1e-9
        )

    def test_negative_allowed(self):
        # The budget itself may be negative (the S2.3 clamp is the
        # instance_count's job, not this one's).
        self.assertAlmostEqual(
            vram.subagent_budget_gb(121.6, 128.0, 8.0), -14.4, delta=1e-9
        )


if __name__ == "__main__":
    unittest.main()
