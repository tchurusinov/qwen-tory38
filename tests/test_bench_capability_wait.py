"""G3c round-2 P1 regression tests (no GPU, no network, no PyYAML):

* the H1 capability terminal-poll window (``bench.capability.
  default_wait_s``) EXCEEDS the in-flight step bound rather than
  equalling it (the worker re-checks the task's --deadline at every
  loop boundary + right after each chat step, so the task is terminal
  by ``deadline + one in-flight step``; the in-flight step is bounded
  by the tool/chat timeout = TOOL_TIMEOUT_S). The margin must be
  strictly greater than that bound so a legitimate deadline-boundary
  overrun can't trip "not terminal".
* the H13 pool-release warmup (``bench.run._warmup_instances``) primes
  the ports from the runner's ALREADY-RESOLVED engine dict, not a
  fresh ``endpoints.load_engine_identity()`` re-read (a divergent
  BONSAI2_ENGINE_YAML/default could warm the wrong ports).
"""

import unittest

from bench import capability
from bench import run as bench_run


class CapabilityWaitWindowTest(unittest.TestCase):
    """The H1 terminal-poll window's margin (G3c round-2 P1)."""

    def test_margin_exceeds_the_in_flight_step_bound(self):
        # wait > deadline + TOOL_TIMEOUT_S (the in-flight step bound).
        for dl in (300.0, 600.0, 900.0):
            wait = capability.default_wait_s(dl)
            self.assertGreater(
                wait,
                dl + capability.TOOL_TIMEOUT_S,
                f"wait {wait} must exceed the in-flight step bound "
                f"{dl + capability.TOOL_TIMEOUT_S} (not equal it)",
            )

    def test_margin_is_deadline_plus_bound_plus_slack(self):
        # the concrete shape: deadline + TOOL_TIMEOUT_S + a 30 s slack.
        self.assertEqual(capability.default_wait_s(600.0), 600.0 + 60.0 + 30.0)

    def test_tool_timeout_is_the_driver_step_bound(self):
        # the margin is derived from the SAME per-tool bound the driver
        # writes into the harness config it spawns with (single source
        # of truth, so the two can't drift).
        self.assertEqual(capability.TOOL_TIMEOUT_S, 60.0)


class WarmupEngineThreadingTest(unittest.TestCase):
    """The H13 warmup primes the resolved engine's ports (G3c round-2 P1)."""

    def test_main_only_warms_nothing_without_requests(self):
        # N=0 (main-only): the warmup returns True using the passed
        # engine dict alone — no requests import, no identity re-read.
        eng = {"instances": {"count": 0, "port_base": 30101}}
        self.assertTrue(bench_run._warmup_instances(eng))

    def test_warms_one_instance_from_the_passed_engine(self):
        # N=1: the warmup POSTs to the single port the PASSED engine
        # resolves (30101+0). A stubbed requests module records the hit
        # — proving the target came from the passed engine, not a
        # re-read of the BONSAI2_ENGINE_YAML/default identity.
        import sys
        import types

        hits: list = []
        fake = types.ModuleType("requests")

        class _R:
            status_code = 200

        def _post(url, **_kw):
            hits.append(url)
            return _R()

        fake.post = _post
        fake.RequestException = type("RequestException", (Exception,), {})
        sys.modules["requests"] = fake
        try:
            eng = {"instances": {"count": 1, "port_base": 30101}}
            self.assertTrue(bench_run._warmup_instances(eng))
        finally:
            sys.modules.pop("requests", None)
        self.assertEqual(len(hits), 1, "one warmup POST for one instance")
        self.assertIn("30101", hits[0], "the port came from the passed engine")


if __name__ == "__main__":
    unittest.main()
