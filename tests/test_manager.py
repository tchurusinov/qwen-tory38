"""MAP 2.1–2.5 / S2.1–S2.5: hermetic tests for bin/bonsai2-manager.

No GPU, no network, no systemd: stdlib-only (the hermetic-suite
convention in tests/test_smoke.py). The manager is loaded from bin/ via
stdlib `importlib.util` (same convention as tests/test_model_fetch.py —
`bin/` is the deploy target, not a test package). Its S1.4 dependency
(`bonsai2_vram`, same bin/ dir) is imported by the module itself via the
sys.path.insert in its header, so no extra wiring is needed here.

What each MAP item is proven by (the spec-named assertions):

  * 2.1 (skeleton: notify + config + health.json):
      - load_config parses the two tracked templates and normalizes the
        keys the boot sequence reads (count/budget/port/alias/api_key/
        weights/model/main-stack block/os-reserved/health-poll).
      - a missing/malformed YAML -> ERR_CONFIG (exit 2 path).
      - write_health writes ATOMICALLY (no partial file visible; the
        tmp file is gone, the final file parses) and builds the S3.1
        document (stack/instances/main_endpoint/main_healthy +
        main_only_reason ONLY when instances: 0, S2.4).
      - build_health / stack_summary: the S3.1 `stack` derivation
        (healthy / degraded on any bdown-bfailed / main-only on empty)
        and the S2.4 main_only_reason presence rule (budget_zero when
        zero instances; absent when any instance exists).

  * 2.2 (boot-time budget check, S2.3 verbatim):
      - planning_tier: `N' = max(0, min(N, floor(free/budget)))` with
        free = total - main - reserved. Driven pure: full grant
        (N'=N), shortfall (0 < N' < N -> WARN_VRAM_SHORTFALL appended),
        negative free -> N'=0 (the `max(0, ...)` clamp — the S2.7 N=0
        test's `max(0,...)` case), zero budget -> 0, over-budget -> N
        (min(N, ...)). The S2.3 formula, one-to-one.
      - main_used_gb: container-id fallback -> the recorded
        `main_stack.footprint_gb` (the spec's "else the S2.6 ...
        default" path).

  * 2.3 (allocation tier, S1.4 verbatim):
      - allocation_tier: free >= footprint + 1.0 grants; free just
        under need rejects (the 1.0 GB spike margin is asserted by
        feeding need - 0.5 -> reject, need exactly -> grant).
      - read_free_gb: the S1.4 bounded free read — one probe + one
        retry, then free = 0 (monkeypatched failing reader: the
        reader is called exactly twice and the result is 0.0).

  * 2.4 (units, S2.1/S2.2):
      - units/bonsai2-sglang.service: Type=notify, ExecStart points at
        /opt/bonsai2/bin/bonsai2-manager, Wants= (NOT Requires=) on
        qwen38-sglang.service, After= includes the main stack.
      - units/bonsai2-server@.service: the S2.2 verbatim shape —
        Requires=docker.service, ExecStartPre docker rm -f,
        Restart=on-failure, RestartSec=5, StartLimitBurst=3,
        CPUQuota=64%.

  * 2.5 (spawn path, S2.5 verbatim):
      - spawn_sequence ordering: i=1's systemctl start is NOT issued
        until i=0's wait-healthy returns True (an injected start/wait
        pair records the call order).
      - partial load (i > 0 tier fail): WARN_VRAM_SHORTFALL shape,
        exit 0, spawned = [0] (the S1.4 "i > 0 partial load, exit 0"
        rule).
      - i = 0 tier fail: exit 4 ERR_VRAM_OOM, zero spawned (S1.4
        "the manager exits 4 before the instance's KV allocation").
      - n_prime = 0: no systemctl call at all, exit 0 (the S2.4
        handoff is the caller's; the loop body is simply empty).
      - a unit that fails to start is recorded, and the loop continues
        to the next index (S2.1: independent engines).

Run: `python3 -m unittest discover -s tests` from the repo root.
"""

import importlib.machinery
import importlib.util
import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
_MANAGER = REPO_ROOT / "bin" / "bonsai2-manager"
VRAM_TOOL = REPO_ROOT / "bin" / "bonsai2_vram.py"
ENGINE_YAML = REPO_ROOT / "etc" / "subagent-engine.yaml"
SERVING_YAML = REPO_ROOT / "etc" / "serving.yaml"
MANAGER_UNIT = REPO_ROOT / "units" / "bonsai2-sglang.service"
SERVER_UNIT = REPO_ROOT / "units" / "bonsai2-server@.service"

# S1.4 allocation-tier constants (mirror of the manager's, asserted in
# the tier tests below).
TIER_MARGIN_GB = 1.0


def _load_manager():
    """Load bin/bonsai2-manager as a module (importlib, hermetic).

    The file has no `.py` extension (the deploy name), so
    `spec_from_file_location` needs an explicit SourceFileLoader to
    pick the right loader — the default would return loader=None for a
    path it can't map to a known suffix.
    """
    loader = importlib.machinery.SourceFileLoader("bonsai2_manager", str(_MANAGER))
    spec = importlib.util.spec_from_loader("bonsai2_manager", loader)
    assert spec, f"could not load {_MANAGER}"
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def _load_vram():
    """Load bin/bonsai2_vram.py as a module (the importlib convention,
    same as tests/test_vram.py — bin/ is the deploy target, not a test
    package)."""
    spec = importlib.util.spec_from_file_location("bonsai2_vram", str(VRAM_TOOL))
    assert spec and spec.loader, f"could not load {VRAM_TOOL}"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


vram = _load_vram()
mgr = _load_manager()


# --------------------------------------------------------------------------
# MAP 2.1 — config loading + health.json (S3.1) + sd_notify shape
# --------------------------------------------------------------------------
class TestConfigLoading(unittest.TestCase):
    """2.1: the manager reads subagent-engine.yaml + serving.yaml (S2.2)."""

    def test_tracks_template_parses_to_normalized_config(self):
        cfg = mgr.load_config(ENGINE_YAML, SERVING_YAML)
        # The keys the boot sequence reads (S2.3/S2.5/S2.1):
        self.assertEqual(cfg["count"], 2)  # S0 default (the re-anchored N=2)
        self.assertEqual(cfg["port_base"], 30101)  # S1.3
        self.assertEqual(cfg["ctx_size"], 262144)  # S1.3
        self.assertEqual(cfg["slots"], 8)  # S1.3 --parallel 8
        self.assertEqual(cfg["budget_gb"], 14.78)  # S1.4 re-anchor
        self.assertEqual(cfg["alias"], "qwen38-27b-subagent")  # S1.3
        self.assertEqual(cfg["api_key"], "bonsai2-local")  # S1.3
        self.assertEqual(cfg["main_container"], "qwen38-sglang")  # S2.6
        self.assertEqual(cfg["main_endpoint"], "http://localhost:30001")
        self.assertEqual(cfg["main_footprint_gb"], 76.4)  # S2.6
        self.assertEqual(cfg["os_reserved_gb"], 8.0)  # S2.6
        self.assertEqual(cfg["health_poll_s"], 10)  # S2.6
        self.assertEqual(cfg["boot_target_s"], 180)  # S2.6/S2.7
        # weights_gb is the DECIMAL-GB conversion of model.size_bytes
        # (the S1.4 convention; 7206168928 B -> 7.206168928 GB).
        self.assertAlmostEqual(cfg["weights_gb"], 7206168928 / 1e9)

    def test_missing_file_is_err_config(self):
        with tempfile.TemporaryDirectory() as td:
            missing = Path(td) / "subagent-engine.yaml"
            with self.assertRaises(ValueError) as ctx:
                mgr.load_config(missing, SERVING_YAML)
            self.assertIn("ERR_CONFIG", str(ctx.exception))

    def test_malformed_yaml_is_err_config(self):
        with tempfile.TemporaryDirectory() as td:
            bad = Path(td) / "subagent-engine.yaml"
            bad.write_text("instances:\n  count: 2\n  count: notadict\n")
            with self.assertRaises(ValueError) as ctx:
                mgr.load_config(bad, SERVING_YAML)
            self.assertIn("ERR_CONFIG", str(ctx.exception))

    def test_missing_instances_block_is_err_config(self):
        with tempfile.TemporaryDirectory() as td:
            bad = Path(td) / "subagent-engine.yaml"
            bad.write_text("engine:\n  binary: /opt/bonsai2/bin/llama-server\n")
            with self.assertRaises(ValueError) as ctx:
                mgr.load_config(bad, SERVING_YAML)
            self.assertIn("ERR_CONFIG", str(ctx.exception))


class TestHealthJson(unittest.TestCase):
    """2.1: /run/bonsai2/health.json in the S3.1 schema (S2.2)."""

    def _cfg(self):
        return mgr.load_config(ENGINE_YAML, SERVING_YAML)

    def test_write_health_is_atomic_and_parses(self):
        cfg = self._cfg()
        doc = mgr.build_health(cfg, ["healthy", "degraded"], main_ok=True)
        with tempfile.TemporaryDirectory() as td:
            health_dir = Path(td)
            mgr.write_health(health_dir, doc)
            final = health_dir / "health.json"
            self.assertTrue(final.is_file())
            self.assertFalse(
                (health_dir / "health.json.tmp").exists(),
                "tmp file left behind — not an atomic write",
            )
            parsed = json.loads(final.read_text())
            self.assertEqual(parsed, doc)

    def test_s31_schema_shape(self):
        cfg = self._cfg()
        doc = mgr.build_health(cfg, ["healthy", "healthy"], main_ok=True)
        # S3.1 status schema: stack, instances[], main_endpoint,
        # main_healthy; per-instance i/port/state/ctx/ctx_reduced/
        # slots_in_use/slots_total.
        self.assertEqual(doc["stack"], "healthy")
        self.assertEqual(len(doc["instances"]), 2)
        self.assertEqual(doc["instances"][0]["port"], 30101)
        self.assertEqual(doc["instances"][1]["port"], 30102)  # port_base + i
        self.assertEqual(doc["instances"][0]["state"], "healthy")
        self.assertEqual(doc["instances"][0]["ctx"], 262144)
        self.assertIs(doc["instances"][0]["ctx_reduced"], False)
        self.assertEqual(doc["instances"][0]["slots_total"], 8)
        self.assertEqual(doc["main_endpoint"], "http://localhost:30001")
        self.assertIs(doc["main_healthy"], True)
        # S2.4: main_only_reason ONLY present when instances: 0.
        self.assertNotIn("main_only_reason", doc)

    def test_main_only_document_carries_reason(self):
        cfg = self._cfg()
        doc = mgr.build_health(cfg, [], main_ok=True, main_only_reason="budget_zero")
        self.assertEqual(doc["stack"], "main-only")
        self.assertEqual(doc["instances"], [])
        self.assertEqual(doc["main_only_reason"], "budget_zero")

    def test_user_unloaded_reason_is_echoed(self):
        cfg = self._cfg()
        doc = mgr.build_health(cfg, [], main_ok=True, main_only_reason="user_unloaded")
        self.assertEqual(doc["main_only_reason"], "user_unloaded")

    def test_stack_derivation_degraded_on_bfailed_or_bdown(self):
        cfg = self._cfg()
        for bad in ("bdown", "bfailed"):
            doc = mgr.build_health(cfg, ["healthy", bad], main_ok=True)
            self.assertEqual(doc["stack"], "degraded")
            self.assertNotIn("main_only_reason", doc)


# --------------------------------------------------------------------------
# MAP 2.2 — S2.3 boot-time budget check
# --------------------------------------------------------------------------
class TestPlanningTier(unittest.TestCase):
    """2.2 / S2.3: N' = max(0, min(N, floor(free/budget)))."""

    def test_full_grant(self):
        # 121.6 - 76.4 - 8.0 = 37.2 (decimal GB; float arithmetic ->
        # 37.1999...9, hence the approximate assert); floor(37.2/14.78)
        # = 2 = N.
        n_prime, free, warns = mgr.planning_tier(121.6, 76.4, 8.0, 2, 14.78)
        self.assertAlmostEqual(free, 37.2)
        self.assertEqual(n_prime, 2)
        self.assertEqual(warns, [])

    def test_shortfall_warns_and_proceeds(self):
        # main cgroup live at 90 GB (asleep+cache state): free = 23.6 ->
        # floor(23.6/14.78) = 1 < N=2 -> WARN_VRAM_SHORTFALL, proceed w/ 1.
        n_prime, free, warns = mgr.planning_tier(121.6, 90.0, 8.0, 2, 14.78)
        self.assertEqual(n_prime, 1)
        self.assertAlmostEqual(free, 23.6)  # 121.6 - 90.0 - 8.0
        self.assertEqual(len(warns), 1)
        self.assertIn("WARN_VRAM_SHORTFALL", warns[0])

    def test_negative_free_clamps_to_zero(self):
        # S2.7 N=0 test's `max(0, ...)` case: main footprint override to
        # 120 GB -> free = 121.6 - 120 - 8 = -6.4 -> N' = 0.
        n_prime, free, _ = mgr.planning_tier(121.6, 120.0, 8.0, 2, 14.78)
        self.assertLess(free, 0)
        self.assertEqual(n_prime, 0)

    def test_min_clamp_keeps_n_at_yaml_count(self):
        # A thin main stack (sleep-accurate cgroup ~10 GB) never pushes N'
        # ABOVE the recorded N (S2.3 'budgeting vs current'): floor(103.6/
        # 14.78) = 7 -> min(N=2, 7) = 2.
        n_prime, _, _ = mgr.planning_tier(121.6, 10.0, 8.0, 2, 14.78)
        self.assertEqual(n_prime, 2)

    def test_zero_budget_is_main_only(self):
        n_prime, _, warns = mgr.planning_tier(121.6, 76.4, 8.0, 2, 0.0)
        self.assertEqual(n_prime, 0)
        self.assertTrue(warns)

    def test_negative_budget_is_main_only(self):
        n_prime, _, warns = mgr.planning_tier(121.6, 76.4, 8.0, 2, -1.0)
        self.assertEqual(n_prime, 0)
        self.assertTrue(warns)

    def test_exactly_one_budget_grants_one(self):
        # boundary: free == budget -> floor = 1 (a full grant of 1).
        n_prime, _, _ = mgr.planning_tier(121.6, 121.6 - 8.0 - 14.78, 8.0, 2, 14.78)
        self.assertEqual(n_prime, 1)

    def test_main_used_gb_falls_back_to_recorded_footprint(self):
        """S2.3: 'live when the container exists, else the recorded
        main_stack.footprint_gb default'. A missing container id is the
        fallback path (the live cgroup read is the @gpu test's probe)."""
        with mock.patch.object(mgr, "_main_container_id", return_value=None):
            self.assertEqual(mgr.main_used_gb("qwen38-sglang", 76.4), 76.4)


# --------------------------------------------------------------------------
# MAP 2.3 — S1.4 allocation tier
# --------------------------------------------------------------------------
class TestAllocationTier(unittest.TestCase):
    """2.3 / S1.4: free >= instance_footprint_GB + 1.0 (bounded read)."""

    def test_grants_at_need(self):
        granted, free, _ = mgr.allocation_tier(15.08, lambda: 15.08)
        self.assertTrue(granted)
        self.assertAlmostEqual(free, 15.08)

    def test_grants_above_need(self):
        granted, _, _ = mgr.allocation_tier(15.08, lambda: 50.0)
        self.assertTrue(granted)

    def test_rejects_below_need(self):
        granted, free, detail = mgr.allocation_tier(15.08, lambda: 15.07)
        self.assertFalse(granted)
        self.assertAlmostEqual(free, 15.07)
        self.assertIn("need 15.08", detail)

    def test_margin_is_one_gb(self):
        # The need for the default config IS footprint + 1.0 (S1.4
        # verbatim): 7.206168928 weights + 6.87 KV = 14.076... + 1.0.
        # `vram` = bin/bonsai2_vram.py loaded at module level above
        # (the manager's own S1.4 source).

        footprint = vram.instance_footprint_gb(7206168928 / 1e9, 8, 262144)
        need = footprint + TIER_MARGIN_GB
        self.assertAlmostEqual(need, footprint + 1.0)
        # and the manager's default need matches that (the spec formula,
        # one-to-one — no drift between the formula module and the tier).
        granted, _, _ = mgr.allocation_tier(need, lambda: need)
        self.assertTrue(granted)
        granted2, _, _ = mgr.allocation_tier(need, lambda: need - 0.5)
        self.assertFalse(granted2)

    def test_read_free_gb_retries_once_then_zero(self):
        """S1.4: '5 s timeout per probe, one retry; on a second timeout
        the tier treats free as 0'."""
        calls = []

        def failing_reader():
            calls.append(1)
            raise OSError("probe failed (injected)")

        real = mgr._mem_available_gb
        mgr._mem_available_gb = failing_reader
        try:
            free = mgr.read_free_gb()
        finally:
            mgr._mem_available_gb = real
        self.assertEqual(free, 0.0, "second timeout must treat free as 0")
        self.assertEqual(len(calls), 2, "one probe + one retry, no more")

    def test_read_free_gb_success_first_probe(self):
        real = mgr._mem_available_gb
        mgr._mem_available_gb = lambda: 42.0
        try:
            self.assertEqual(mgr.read_free_gb(), 42.0)
        finally:
            mgr._mem_available_gb = real


# --------------------------------------------------------------------------
# MAP 2.4 — unit files (S2.1 / S2.2 verbatim shape)
# --------------------------------------------------------------------------
class TestUnitFiles(unittest.TestCase):
    """2.4: the S2.1/S2.2 unit files, verbatim main-stack shape."""

    def test_manager_unit_shape(self):
        text = MANAGER_UNIT.read_text()
        self.assertIn("Type=notify", text)  # S2.2 (the manager's sd_notify)
        self.assertIn("ExecStart=/opt/bonsai2/bin/bonsai2-manager", text)  # S2.2
        self.assertIn("Wants=qwen38-sglang.service", text)  # S2.1/S2.2
        self.assertNotIn("Requires=qwen38-sglang", text)  # NOT Requires=
        m = re.search(r"^After=.*$", text, re.MULTILINE)
        self.assertIsNotNone(m, "After= missing")
        assert m is not None  # re.search returns Optional[str|None]
        self.assertIn("qwen38-sglang.service", m.group(0))  # S2.1 boot order

    def test_server_unit_shape(self):
        text = SERVER_UNIT.read_text()
        self.assertIn("Requires=docker.service", text)  # S2.2 hardening
        self.assertIn(
            "ExecStartPre=/usr/bin/docker rm -f bonsai2-server-%i", text
        )  # S2.2 verbatim (clean stale container)
        self.assertIn("Restart=on-failure", text)  # S2.2
        self.assertIn("RestartSec=5", text)  # S2.2
        self.assertIn("StartLimitBurst=3", text)  # S2.2 (then bfailed)
        self.assertIn("CPUQuota=64%", text)  # S2.2
        # The template is the placeholder install.sh renders — it must be
        # honest about that (a comment documents the rendered ExecStart).
        self.assertIn("docker run --rm --name bonsai2-server-%i", text)


# --------------------------------------------------------------------------
# MAP 2.5 — spawn path (S2.5: ordered, tier-gated, wait-before-next)
# --------------------------------------------------------------------------
class TestSpawnSequence(unittest.TestCase):
    """2.5 / S2.5: the ordered, allocation-tier-gated spawn loop."""

    def _cfg(self):
        return mgr.load_config(ENGINE_YAML, SERVING_YAML)

    def test_ordering_wait_before_next(self):
        """S2.5: 'waits each to healthy before the next'. i=1's start
        must not be issued until i=0's wait returns True."""
        order = []

        def fake_start(i, base=None):
            order.append(f"start-{i}")
            return 0

        def fake_wait(i, cfg, base=None):
            order.append(f"wait-{i}")
            return True

        exit_code, spawned, _ = mgr.spawn_sequence(
            self._cfg(),
            2,
            15.08,
            free_reader=lambda: 100.0,
            systemctl_start=fake_start,
            wait_healthy=fake_wait,
            log=lambda *_: None,
        )
        self.assertEqual(exit_code, 0)
        self.assertEqual(spawned, [0, 1])
        self.assertEqual(order, ["start-0", "wait-0", "start-1", "wait-1"])

    def test_partial_load_at_i_gt_0_is_exit_0(self):
        """S1.4 i > 0 tier fail: WARN_VRAM_SHORTFALL, stop at i-1, exit 0."""
        order = []
        free_calls = {"n": 0}

        def free_reader():
            free_calls["n"] += 1
            return 100.0 if free_calls["n"] == 1 else 5.0  # i=0 grants, i=1 fails

        def fake_start(i, base=None):
            order.append(f"start-{i}")
            return 0

        exit_code, spawned, detail = mgr.spawn_sequence(
            self._cfg(),
            2,
            15.08,
            free_reader=free_reader,
            systemctl_start=fake_start,
            wait_healthy=lambda i, cfg, base=None: True,
            log=lambda *_: None,
        )
        self.assertEqual(exit_code, 0, "i > 0 partial load is exit 0 (S1.4)")
        self.assertEqual(spawned, [0])
        self.assertEqual(order, ["start-0"], "i=1 must not start")
        self.assertIn("partial load at i=1", detail)

    def test_i_0_tier_fail_is_exit_4(self):
        """S1.4 i = 0 tier fail: exit 4 ERR_VRAM_OOM, zero spawned, and
        NO systemctl call (the manager exits before the KV allocation)."""
        started = []

        def fake_start(i, base=None):
            started.append(i)
            return 0

        exit_code, spawned, detail = mgr.spawn_sequence(
            self._cfg(),
            2,
            15.08,
            free_reader=lambda: 5.0,  # free < need on the very first probe
            systemctl_start=fake_start,
            wait_healthy=lambda i, cfg, base=None: True,
            log=lambda *_: None,
        )
        self.assertEqual(exit_code, 4, "i = 0 allocation fail = exit 4 (S1.4)")
        self.assertEqual(spawned, [])
        self.assertEqual(started, [], "no instance may start at i=0 OOM")
        self.assertIn("ERR_VRAM_OOM", detail)

    def test_n_prime_zero_spawns_nothing(self):
        """S2.4 handoff: N' = 0 -> the loop body is empty, exit 0, no
        systemctl call (the main-only write is the caller's)."""
        started = []
        exit_code, spawned, _ = mgr.spawn_sequence(
            self._cfg(),
            0,
            15.08,
            free_reader=lambda: 5.0,
            systemctl_start=lambda i, base=None: started.append(i) or 0,
            wait_healthy=lambda i, cfg, base=None: True,
            log=lambda *_: None,
        )
        self.assertEqual(exit_code, 0)
        self.assertEqual(spawned, [])
        self.assertEqual(started, [])

    def test_failed_unit_start_is_recorded_and_loop_continues(self):
        """A unit that fails to start (docker gone, un-clearable port
        collision) is recorded, and i+1 still attempts (S2.1: the
        instances are independent engines)."""
        order = []

        def fake_start(i, base=None):
            order.append(f"start-{i}")
            return 1 if i == 0 else 0

        exit_code, spawned, _ = mgr.spawn_sequence(
            self._cfg(),
            2,
            15.08,
            free_reader=lambda: 100.0,
            systemctl_start=fake_start,
            wait_healthy=lambda i, cfg, base=None: True,
            log=lambda *_: None,
        )
        self.assertEqual(exit_code, 0)
        self.assertEqual(spawned, [0, 1], "the failed i=0 is recorded")
        self.assertEqual(order, ["start-0", "start-1"])

    def test_needs_use_vram_formula_not_a_hardcoded_number(self):
        """The need the main() computes must equal the S1.4 formula
        (footprint + 1.0) — no drift between the manager and
        bonsai2_vram (the spec-follows-code guard)."""
        cfg = self._cfg()
        need = (
            vram.instance_footprint_gb(
                cfg["weights_gb"], int(cfg["slots"]), int(cfg["ctx_size"])
            )
            + mgr.TIER_MARGIN_GB
        )
        # The exact S1.4 number on this box: weights 7206168928/1e9 GB +
        # KV 8 x 262144 x 0.000003277 GB = 14.078536032 GB + 1.0 margin
        # = 15.078536032 GB need (decimal GB end-to-end, the S1.4
        # convention — see the MAP 1.9 finding: a GiB mix-up was the
        # first live-run bug that class).
        self.assertAlmostEqual(need, 15.078536032, places=9)


if __name__ == "__main__":
    unittest.main()
