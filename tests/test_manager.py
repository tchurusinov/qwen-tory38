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
import os
import re
import shutil
import tempfile
import time
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
# G3 PR #10 round-5 P2 (note-only): loading the manager here runs its
# module-level `sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))`
# (bin/), so `import bonsai2_vram` inside the manager resolves against the
# DEPLOY bin/ dir — which is the deploy layout by design (the manager is
# deployed to /opt/bonsai2/bin/, next to bonsai2_vram.py). A future
# `bin/bonsai2_vram.py` shadow in the test CWD would silently win; accepted
# because bin/ is the single source of truth and tests read from it.
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

    def test_fallback_size_bytes_is_validated_and_surfaced(self):
        """G3 round-4 P1: model.fallback_size_bytes (the S2.4 PTQ1_0
        fallback quant, 5.95 GB vs PQ2_0 7.21 GB) must be validated at
        config-load and carried into the normalized cfg, so MAP 2.7's
        N=0 quant-fallback can plan against it."""
        cfg = mgr.load_config(ENGINE_YAML, SERVING_YAML)
        self.assertEqual(cfg["fallback_size_bytes"], 5946648928)

    def test_bad_fallback_size_bytes_is_err_config(self):
        with tempfile.TemporaryDirectory() as td:
            good = ENGINE_YAML.read_text().replace(
                "fallback_size_bytes: 5946648928",
                "fallback_size_bytes: notanumber",
            )
            bad = Path(td) / "subagent-engine.yaml"
            bad.write_text(good)
            with self.assertRaises(ValueError) as ctx:
                mgr.load_config(bad, SERVING_YAML)
            self.assertIn("ERR_CONFIG", str(ctx.exception))

    def test_non_numeric_instance_key_is_err_config(self):
        """G3 round-8 P2: a hand-edited YAML with a non-numeric int key
        (e.g. `count: abc`) must fail at config-load with ERR_CONFIG, not
        as a cryptic `int()` ValueError mid-boot in main()."""
        with tempfile.TemporaryDirectory() as td:
            good = ENGINE_YAML.read_text().replace("count: 2", "count: abc")
            bad = Path(td) / "subagent-engine.yaml"
            bad.write_text(good)
            with self.assertRaises(ValueError) as ctx:
                mgr.load_config(bad, SERVING_YAML)
            self.assertIn("ERR_CONFIG", str(ctx.exception))
            self.assertIn("instances.count", str(ctx.exception))

    def test_float_budget_is_accepted(self):
        """The per_instance_budget_gb key accepts int OR float (S1.4 works
        in decimal GB; the YAML template ships 14.78 as a float)."""
        with tempfile.TemporaryDirectory() as td:
            good = ENGINE_YAML.read_text().replace(
                "per_instance_budget_gb: 14.78", "per_instance_budget_gb: 12"
            )
            ok = Path(td) / "subagent-engine.yaml"
            ok.write_text(good)
            cfg = mgr.load_config(ok, SERVING_YAML)
            self.assertEqual(cfg["budget_gb"], 12)

    def test_kv_cache_q4_0_feeds_the_s1_4_constant(self):
        """G3 round-13 P1: the S1.6 `kv_cache` key now feeds the S1.4
        footprint — q4_0 (the template default, the S1.4 re-anchor) must
        load and carry its LOCKED measured constant."""
        cfg = mgr.load_config(ENGINE_YAML, SERVING_YAML)
        self.assertEqual(cfg["kv_cache"], "q4_0")
        self.assertAlmostEqual(cfg["per_slot_per_token_gb"], 0.000003277)

    def test_kv_cache_missing_is_err_config(self):
        """G3 round-13 P1: an edited-away `kv_cache` key must fail at
        config-load (ERR_CONFIG, exit 2), not silently gate on the q4_0
        constant mid-boot."""
        with tempfile.TemporaryDirectory() as td:
            bad = Path(td) / "subagent-engine.yaml"
            bad.write_text(ENGINE_YAML.read_text().replace("kv_cache: q4_0", ""))
            with self.assertRaises(ValueError) as ctx:
                mgr.load_config(bad, SERVING_YAML)
            self.assertIn("kv_cache missing", str(ctx.exception))

    def test_kv_cache_unknown_dtype_is_err_config(self):
        """G3 round-13 P1: a dtype with no LOCKED S1.4 constant (e.g. f16)
        must be an ERR_CONFIG at load — there is no measured FP16 value in
        the spec to fall back to, so guessing at boot is forbidden."""
        with tempfile.TemporaryDirectory() as td:
            bad = Path(td) / "subagent-engine.yaml"
            bad.write_text(
                ENGINE_YAML.read_text().replace("kv_cache: q4_0", "kv_cache: f16")
            )
            with self.assertRaises(ValueError) as ctx:
                mgr.load_config(bad, SERVING_YAML)
            self.assertIn("no locked S1.4", str(ctx.exception))

    def test_allocation_need_gb_honours_the_kv_constant(self):
        """G3 round-13 P1: allocation_need_gb threads
        per_slot_per_token_gb into the footprint — a hypothetical larger
        KV constant must raise the need (the default-constant call is
        unchanged, so existing callers keep their value)."""
        default = mgr.allocation_need_gb(7.21, 8, 262144, 14.78)
        self.assertAlmostEqual(default, 15.08, places=2)
        # ~2x constant (an FP16-ish value) must raise the need past the
        # margin term — the cap floor (14.78) no longer dominates.
        bigger = mgr.allocation_need_gb(
            7.21, 8, 262144, 14.78, per_slot_per_token_gb=0.0000070
        )
        self.assertGreater(bigger, default)


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

    def test_oom_document_carries_err_vram_oom_reason(self):
        """G3 round-4 P0: the i=0 OOM exit-4 path writes health.json with
        main_only_reason = 'ERR_VRAM_OOM' (a 0-instance doc whose cause is
        OOM, not budget_zero/user_unloaded). The S3.1 schema is open to
        new reason values (S2.4 amendment 2026-09-23)."""
        cfg = self._cfg()
        doc = mgr.build_health(cfg, [], main_ok=True, main_only_reason="ERR_VRAM_OOM")
        self.assertEqual(doc["stack"], "main-only")
        self.assertEqual(doc["instances"], [])
        self.assertEqual(doc["main_only_reason"], "ERR_VRAM_OOM")


# --------------------------------------------------------------------------
# MAP 2.1–2.5 — main() exit-code contract (boot-tier read failures + OOM)
# --------------------------------------------------------------------------
class TestMainExitCodes(unittest.TestCase):
    """main() maps its boot-time failures to the documented exit codes:
    2 = ERR_CONFIG (incl. a /proc/meminfo read failure), 4 = ERR_VRAM_OOM
    (the i=0 allocation-tier defeat, no READY=1)."""

    def _env(self, td):
        env = {
            "BONSAI2_ENGINE_YAML": str(ENGINE_YAML),
            "BONSAI2_SERVING_YAML": str(SERVING_YAML),
            "BONSAI2_HEALTH_DIR": td,
        }
        return env

    def test_mem_total_read_failure_is_err_config_exit_2(self):
        """G3 round-4 P2: read_total_gb() raising OSError (MemTotal absent)
        maps to ERR_CONFIG exit 2, not an uncaught traceback (exit 1)."""
        cfg = mgr.load_config(ENGINE_YAML, SERVING_YAML)
        notified: list[str] = []
        with tempfile.TemporaryDirectory() as td:
            with (
                mock.patch.dict(os.environ, self._env(td), clear=False),
                mock.patch.object(mgr, "load_config", return_value=cfg),
                mock.patch.object(
                    mgr, "read_total_gb", side_effect=OSError("MemTotal missing")
                ),
                mock.patch.object(
                    mgr, "notify_sd", side_effect=lambda s: notified.append(s)
                ),
            ):
                rc = mgr.main([])
            self.assertEqual(rc, 2, "MemTotal read failure = ERR_CONFIG exit 2")
        # G3 round-10 P2: a pre-READY exit sends STOPPING= so a stop issued
        # during the still-"starting" window has Type=notify bookkeeping.
        self.assertIn("STOPPING=1", notified)

    def test_i0_oom_exits_4_writes_health_no_ready(self):
        """G3 round-4 P0: when spawn_sequence returns exit 4 (i=0
        allocation OOM), main() writes health.json (reason ERR_VRAM_OOM),
        does NOT send READY=1, and returns 4 — so systemd under
        Type=notify sees a FAILED START -> Restart=on-failure."""
        cfg = mgr.load_config(ENGINE_YAML, SERVING_YAML)
        written: dict = {}
        notified: list[str] = []

        def fake_write_health(health_dir, doc):
            written["doc"] = doc

        def fake_notify(state):
            notified.append(state)

        td = tempfile.mkdtemp()
        try:
            with (
                mock.patch.dict(os.environ, self._env(td), clear=False),
                mock.patch.object(mgr, "load_config", return_value=cfg),
                mock.patch.object(mgr, "read_total_gb", return_value=121.6),
                mock.patch.object(
                    mgr, "main_used_gb", return_value=(76.4, "recorded footprint")
                ),
                mock.patch.object(mgr, "main_healthy", return_value=True),
                mock.patch.object(
                    mgr,
                    "spawn_sequence",
                    return_value=(
                        mgr.EXIT_VRAM_OOM,
                        [],
                        "ERR_VRAM_OOM: free 5.00 GB < need 15.08 GB",
                        {},
                    ),
                ),
                mock.patch.object(mgr, "write_health", side_effect=fake_write_health),
                mock.patch.object(mgr, "notify_sd", side_effect=fake_notify),
            ):
                rc = mgr.main([])
        finally:
            shutil.rmtree(td, ignore_errors=True)

        self.assertEqual(rc, mgr.EXIT_VRAM_OOM, "i=0 allocation OOM = exit 4")
        self.assertEqual(written["doc"]["main_only_reason"], "ERR_VRAM_OOM")
        self.assertEqual(written["doc"]["stack"], "main-only")
        self.assertEqual(written["doc"]["instances"], [])
        self.assertFalse(
            any(s.startswith("READY=") for s in notified),
            "exit-4 boot must NOT send READY=1",
        )
        # G3 round-10 P2: a pre-READY exit sends STOPPING= so a stop issued
        # during the still-"starting" window has Type=notify bookkeeping
        # instead of falling through to the TimeoutStopSec main-PID path.
        self.assertIn(
            "STOPPING=1", notified, "exit-4 boot must send STOPPING= (pre-READY)"
        )

    def test_i0_oom_health_document_uses_the_reprobed_main_state(self):
        """G3 round-15 P2: the S2.1 main_ok is probed BEFORE the spawn
        sequence; the exit-4 health document must carry a FRESH probe, so
        a main that dies during the tier rejection is not recorded as
        healthy in the final (terminal) OOM document."""
        cfg = mgr.load_config(ENGINE_YAML, SERVING_YAML)
        written: dict = {}
        main_calls: list[int] = []
        side = [True, False]  # S2.1 probe: up; exit-4 re-probe: down

        def fake_main_healthy(endpoint, timeout=5.0):
            main_calls.append(1)
            return side[min(len(main_calls) - 1, len(side) - 1)]

        with (
            tempfile.TemporaryDirectory() as td,
            mock.patch.dict(os.environ, self._env(td), clear=False),
            mock.patch.object(mgr, "load_config", return_value=cfg),
            mock.patch.object(mgr, "read_total_gb", return_value=121.6),
            mock.patch.object(
                mgr, "main_used_gb", return_value=(76.4, "recorded footprint")
            ),
            mock.patch.object(mgr, "main_healthy", side_effect=fake_main_healthy),
            mock.patch.object(
                mgr,
                "spawn_sequence",
                return_value=(
                    mgr.EXIT_VRAM_OOM,
                    [],
                    "ERR_VRAM_OOM: free 5.00 GB < need 15.08 GB",
                    {},
                ),
            ),
            mock.patch.object(
                mgr,
                "write_health",
                side_effect=lambda d, doc: written.update(doc=doc),
            ),
            mock.patch.object(mgr, "notify_sd"),
        ):
            rc = mgr.main([])
        self.assertEqual(rc, mgr.EXIT_VRAM_OOM)
        self.assertGreaterEqual(
            len(main_calls), 2, "the exit-4 path must re-probe main after spawn"
        )
        self.assertIs(
            written["doc"]["main_healthy"],
            False,
            "the OOM document must carry the RE-PROBED main state, not the "
            "pre-spawn one",
        )

    def test_boot_deadline_starts_before_the_main_probe(self):
        """G3 round-14 P1: the S2.8 #1 shared whole-boot deadline starts
        BEFORE the S2.1 main_healthy probe — the probe is part of
        'main + N healthy <= boot_target_s', so its (up to 5 s) cost
        lives INSIDE the budget, not on top of it. Pin the ordering:
        record the monotonic() time inside the mocked main_healthy; the
        boot_deadline_s passed to spawn_sequence must satisfy
        deadline - boot_target_s <= probe_time (i.e. it was computed
        first)."""
        cfg = mgr.load_config(ENGINE_YAML, SERVING_YAML)
        probe_time: list[float] = []
        deadline_seen: list[float] = []

        def fake_main_healthy(endpoint, timeout=5.0):
            probe_time.append(time.monotonic())
            return True

        def fake_spawn_sequence(*args, **kwargs):
            deadline_seen.append(float(kwargs["boot_deadline_s"]))
            return (mgr.EXIT_OK, [0], "full load: 1 instance(s) spawned", {0: 0})

        with (
            tempfile.TemporaryDirectory() as td,
            mock.patch.dict(os.environ, self._env(td), clear=False),
            mock.patch.object(mgr, "load_config", return_value=cfg),
            mock.patch.object(mgr, "read_total_gb", return_value=121.6),
            mock.patch.object(
                mgr, "main_used_gb", return_value=(76.4, "recorded footprint")
            ),
            mock.patch.object(mgr, "main_healthy", side_effect=fake_main_healthy),
            mock.patch.object(
                mgr,
                "spawn_sequence",
                side_effect=fake_spawn_sequence,
            ),
            mock.patch.object(mgr, "write_health"),
            mock.patch.object(mgr, "notify_sd"),
            # run_probe_loop re-probes main every cycle (it would add
            # extra probe_time entries); the settle path is out of
            # scope for this ordering pin.
            mock.patch.object(mgr, "run_probe_loop", return_value=mgr.EXIT_OK),
            # MAP 2.9: the measured-boot write-back targets the serving
            # yaml — patch it so the tracked template is not rewritten
            # by this hermetic ordering pin.
            mock.patch.object(mgr, "write_boot_target_s", return_value=True),
        ):
            rc = mgr.main([])
        self.assertEqual(rc, mgr.EXIT_OK)
        self.assertEqual(len(probe_time), 1)
        self.assertEqual(len(deadline_seen), 1)
        start_of_budget = deadline_seen[0] - cfg["boot_target_s"]
        self.assertLessEqual(
            start_of_budget,
            probe_time[0],
            "the shared boot deadline must start BEFORE the main probe "
            "(S2.8 #1: 'main + N healthy' — the probe is inside the "
            "budget, not after it)",
        )

    def test_settle_run_sends_ready_1_before_settle_window(self):
        """G3 round-5 P1: a --settle boot (instances or main-only) exits 0
        after the settle window — but it still sends READY=1 BEFORE the
        window. Under a user-scope Type=notify start an exit-0 with no
        prior READY=1 is a FAILED START in systemd notify semantics.
        G3 round 9 P2: also verify the S3.1 document the first health.json
        write carries (states correct, not just the READY ordering)."""
        cfg = mgr.load_config(ENGINE_YAML, SERVING_YAML)
        notified: list[str] = []
        health_docs: list[dict] = []

        def fake_notify(state):
            notified.append(state)

        def fake_write_health(health_dir, doc):
            health_docs.append(doc)

        td = tempfile.mkdtemp()
        try:
            with (
                mock.patch.dict(os.environ, self._env(td), clear=False),
                mock.patch.object(mgr, "load_config", return_value=cfg),
                mock.patch.object(mgr, "read_total_gb", return_value=121.6),
                mock.patch.object(
                    mgr, "main_used_gb", return_value=(76.4, "recorded footprint")
                ),
                mock.patch.object(mgr, "main_healthy", return_value=True),
                mock.patch.object(
                    mgr,
                    "spawn_sequence",
                    return_value=(
                        mgr.EXIT_OK,
                        [0],
                        "full load: 1 instance(s) spawned",
                        {0: 0},
                    ),
                ),
                mock.patch.object(mgr, "instance_healthy", return_value=True),
                mock.patch.object(mgr, "write_health", side_effect=fake_write_health),
                mock.patch.object(mgr, "notify_sd", side_effect=fake_notify),
                mock.patch.object(mgr, "run_probe_loop", return_value=0),
                # MAP 2.9: the measured-boot write-back targets the
                # serving yaml — patch it so the tracked template is not
                # rewritten by this hermetic settle test.
                mock.patch.object(mgr, "write_boot_target_s", return_value=True),
            ):
                rc = mgr.main(["--settle", "0.1"])
        finally:
            shutil.rmtree(td, ignore_errors=True)

        self.assertEqual(rc, 0, "settle run exits 0 after the window")
        self.assertIn("READY=1", notified, "settle boot must send READY=1")
        # READY=1 must come AFTER the spawn/booted status, not before it.
        ready_idx = notified.index("READY=1")
        booted_idx = next(i for i, s in enumerate(notified) if "booted" in s)
        self.assertGreater(ready_idx, booted_idx, "READY=1 after the booted STATUS=")
        # G3 round 9 P2: the first health.json must carry the S3.1 states
        # for the spawned instance (healthy — the mocked probe + clean
        # start_rc), stack=healthy, and no main_only_reason (S2.4: only at
        # zero instances).
        self.assertTrue(health_docs, "settle boot must write health.json")
        doc = health_docs[0]
        self.assertEqual(doc["stack"], "healthy")
        self.assertEqual(
            [inst["state"] for inst in doc["instances"]],
            ["healthy"],
            "S3.1: first health.json reflects the spawn outcome",
        )
        self.assertNotIn("main_only_reason", doc)
        self.assertIs(doc["main_healthy"], True)


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
        fallback path (the live cgroup read is the @gpu test's probe).
        The returned source is the log label main() must use — never
        inferred from the value (a container sitting exactly at the
        recorded footprint would mislabel as the fallback path)."""
        with mock.patch.object(mgr, "_main_container_id", return_value=None):
            used, source = mgr.main_used_gb("qwen38-sglang", 76.4)
        self.assertEqual(used, 76.4)
        self.assertEqual(source, "recorded footprint")

    def test_main_used_gb_live_cgroup_source_label(self):
        """The live-read path labels itself 'live cgroup' even when the
        value happens to equal the fallback (the P1 edge case that
        value-comparison hid)."""
        fake_bytes = str(int(76.4e9))  # EXACTLY the fallback value in bytes
        first_cand = Path("/sys/fs/cgroup/system.slice/docker-abc.scope/memory.current")

        def is_file(self):
            return self == first_cand

        with (
            mock.patch.object(mgr, "_main_container_id", return_value="/abc"),
            mock.patch.object(Path, "is_file", is_file),
            mock.patch.object(Path, "read_text", lambda self: fake_bytes),
        ):
            used, source = mgr.main_used_gb("qwen38-sglang", 76.4)
        self.assertAlmostEqual(used, 76.4)
        self.assertEqual(source, "live cgroup")


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
        # The need for the default config IS footprint + 1.0 floored at
        # the S2.2 cap (S1.4 as amended 2026-09-23): 7.206168928
        # weights + 6.872367104 KV = 14.078536032 footprint + 1.0, and
        # max(_, 14.78) keeps the margin term (15.08 > 14.78). `vram` =
        # bin/bonsai2_vram.py loaded at module level above (the manager's
        # own S1.4 source).

        footprint = vram.instance_footprint_gb(7206168928 / 1e9, 8, 262144)
        need = footprint + TIER_MARGIN_GB
        self.assertAlmostEqual(need, footprint + 1.0)
        # and the manager's gate term matches that (allocation_need_gb —
        # the max() floor is a no-op on this box, no drift between the
        # formula module and the tier).
        gate = mgr.allocation_need_gb(7206168928 / 1e9, 8, 262144, 14.78)
        self.assertAlmostEqual(gate, need)
        granted, _, _ = mgr.allocation_tier(need, lambda: need)
        self.assertTrue(granted)
        granted2, _, _ = mgr.allocation_tier(need, lambda: need - 0.5)
        self.assertFalse(granted2)

    def test_need_floors_at_the_s2_2_cap(self):
        """S1.4/S2.2 reconciliation (2026-09-23): when the per-instance
        cap (footprint + 5% margin) exceeds footprint + 1.0, the gate
        term is the cap — a free pool between the two must be rejected
        (it would pass the margin term but not fit the container)."""
        # Synthetic: footprint 10.0, margin term 11.0, cap 12.0 ->
        # gate = 12.0 (the cap binds, not the margin term).
        gate = mgr.allocation_need_gb(
            10.0, 0, 0, 12.0
        )  # slots=0 -> kv = 0, footprint = weights
        self.assertAlmostEqual(gate, 12.0)
        # And the tier rejects free = 11.5 (between margin and cap).
        granted, _, _ = mgr.allocation_tier(gate, lambda: 11.5)
        self.assertFalse(granted)
        # ... but grants free = 12.0.
        granted, _, _ = mgr.allocation_tier(gate, lambda: 12.0)
        self.assertTrue(granted)

    def test_tier_pre_charge_reduces_available(self):
        """G3 PR#11 r1 P1: the S3.2 reload loop charges each in-pass start
        against the shared pool (pre_charge_gb), the way spawn_sequence's
        post-spawn re-read does at boot. A static pool that is enough for
        ONE instance must reject the second target once the first start
        is charged against it."""
        need = 15.08
        # Pool is enough for exactly one instance.
        granted, _, _ = mgr.allocation_tier(need, lambda: 15.10, pre_charge_gb=0.0)
        self.assertTrue(granted, "first target: free >= need")
        # After the first start is charged, the pool drops below need.
        granted2, _, detail = mgr.allocation_tier(
            need, lambda: 15.10, pre_charge_gb=need
        )
        self.assertFalse(granted2, "second target: free - in-pass charge < need")
        self.assertIn("in-pass charge", detail, "the detail names the in-pass charge")

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

    def test_mem_available_gb_bounded_raises_timeout_on_wedged_reader(self):
        """G3 round-14 P0: the S1.4 '5 s timeout per probe' is ENFORCED,
        not nominal — a wedged reader must raise TimeoutError at the cap
        (the pre-r14 docstring admitted the bound was nominal; a wedged
        /proc read would have deadlocked the boot, violating S1.4's
        'wedged probe must never deadlock the manager')."""
        import threading as _threading

        gate = _threading.Event()

        def wedged():
            gate.wait(60)  # simulates a stuck /proc read

        real = mgr._mem_available_gb
        old_cap = mgr.FREE_PROBE_TIMEOUT_S
        mgr._mem_available_gb = wedged
        mgr.FREE_PROBE_TIMEOUT_S = 0.2  # short for the hermetic run
        try:
            t0 = time.monotonic()
            with self.assertRaises(TimeoutError):
                mgr._mem_available_gb_bounded()
            elapsed = time.monotonic() - t0
        finally:
            mgr._mem_available_gb = real
            mgr.FREE_PROBE_TIMEOUT_S = old_cap
            gate.set()  # release the wedged thread
        self.assertLessEqual(elapsed, 2.0, "the cap must fire, not wait for the reader")

    def test_read_free_gb_timeout_counts_as_a_failed_probe(self):
        """G3 round-14 P0: a TimeoutError from the bounded reader goes
        through the SAME retry-once-then-zero path as an OSError —
        one probe + one retry, then free = 0 (S1.4)."""
        calls = []

        def wedged():
            calls.append(1)
            raise TimeoutError("wedged (injected)")

        real = mgr._mem_available_gb
        mgr._mem_available_gb = wedged
        try:
            free = mgr.read_free_gb()
        finally:
            mgr._mem_available_gb = real
        self.assertEqual(free, 0.0, "second timeout must treat free as 0")
        self.assertEqual(len(calls), 2, "one probe + one retry, no more")


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
        self.assertIn("StartLimitIntervalSec=300", text)  # S2.2 (300 s window)
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

        def fake_wait(i, cfg, deadline):
            order.append(f"wait-{i}")
            return True

        exit_code, spawned, _, start_rc = mgr.spawn_sequence(
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
        self.assertEqual(start_rc, {0: 0, 1: 0})

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

        exit_code, spawned, detail, _ = mgr.spawn_sequence(
            self._cfg(),
            2,
            15.08,
            free_reader=free_reader,
            systemctl_start=fake_start,
            wait_healthy=lambda i, cfg, deadline: True,
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

        exit_code, spawned, detail, _ = mgr.spawn_sequence(
            self._cfg(),
            2,
            15.08,
            free_reader=lambda: 5.0,  # free < need on the very first probe
            systemctl_start=fake_start,
            wait_healthy=lambda i, cfg, deadline: True,
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
        exit_code, spawned, _, _ = mgr.spawn_sequence(
            self._cfg(),
            0,
            15.08,
            free_reader=lambda: 5.0,
            systemctl_start=lambda i, base=None: started.append(i) or 0,
            wait_healthy=lambda i, cfg, deadline: True,
            log=lambda *_: None,
        )
        self.assertEqual(exit_code, 0)
        self.assertEqual(spawned, [])
        self.assertEqual(started, [])

    def test_failed_unit_start_is_recorded_and_loop_continues(self):
        """A unit that fails to start (docker gone, un-clearable port
        collision) is recorded, and i+1 still attempts (S2.1: the
        instances are independent engines). The failed index's healthy
        wait is SKIPPED (S1.5 bfailed — polling a dead engine's port
        for up to boot_target_s is wasted time)."""
        order = []
        waits = []

        def fake_start(i, base=None):
            order.append(f"start-{i}")
            return 1 if i == 0 else 0

        def fake_wait(i, cfg, deadline):
            waits.append(i)
            return True

        exit_code, spawned, _, start_rc = mgr.spawn_sequence(
            self._cfg(),
            2,
            15.08,
            free_reader=lambda: 100.0,
            systemctl_start=fake_start,
            wait_healthy=fake_wait,
            log=lambda *_: None,
        )
        self.assertEqual(exit_code, 0)
        self.assertEqual(spawned, [0, 1], "the failed i=0 is recorded")
        self.assertEqual(order, ["start-0", "start-1"])
        # The failed start surfaces its rc so main() can reflect bfailed in
        # health.json directly (S1.5 routing — no re-derivation by probe).
        self.assertEqual(start_rc, {0: 1, 1: 0})
        # i=0's wait is skipped (bfailed), i=1's still happens.
        self.assertEqual(waits, [1], "failed-start index must not be waited on")

    def test_steady_loop_does_not_mask_later_instance_after_failed_start(self):
        """G3 round-10 P1 (refuted): with a failed i=0 start, the steady
        probe loop probes _port(cfg, i) = port_base + i for the REAL index
        i, so a later healthy instance keeps its true health — the failed
        index's dead port cannot mask a neighbour. Pin the invariant:
        main()'s first-health.json build marks i=0 bfailed from start_rc
        (not by probing), and _refresh_states over several cycles keeps
        i=1 'healthy' while i=0 STAYS 'bfailed' (a probe miss preserves
        the prior state — only a fresh healthy probe clears it; the
        skeleton never silently degrades a bfailed unit, and MAP 2.6's
        restart is what transitions it — G3 PR #10 r12 P1#2 + r12 P2#3
        docstring correction)."""
        cfg = self._cfg()
        port_base = int(cfg["port_base"])
        start_rc = {0: 1, 1: 0}
        spawned = [0, 1]
        # main()'s first-health.json state build (initial_states):
        # start_rc != 0 -> bfailed, else a probe.
        with mock.patch.object(
            mgr,
            "instance_healthy",
            side_effect=lambda port, a, k: port == port_base + 1,
        ):
            states = mgr.initial_states(spawned, start_rc, cfg)
        self.assertEqual(states, ["bfailed", "healthy"])
        self.assertEqual(mgr.stack_summary(states), "degraded")
        # i=0's port dead, i=1's port healthy: run several cycles on the
        # steady-loop Runtime (MAP 2.6 replaced the skeleton's
        # `states`-list refresh with per-instance entries BY INDEX).
        runtime = mgr.Runtime(cfg, spawned, states)
        with mock.patch.object(
            mgr,
            "instance_healthy",
            side_effect=lambda port, a, k: port == port_base + 1,
        ):
            for _ in range(3):
                mgr._refresh_states(runtime, list(runtime.running))
        self.assertEqual(
            runtime.entries[1].state,
            "healthy",
            "a healthy instance after a failed i=0 start must NOT be masked",
        )
        self.assertEqual(
            runtime.entries[0].state,
            "bfailed",
            "a bfailed start stays bfailed on probe misses (only a fresh "
            "healthy probe clears it; MAP 2.6 restarts it)",
        )
        self.assertEqual(mgr.stack_summary(runtime.states_for_health()), "degraded")

    def test_main_healthy_4xx_semantics(self):
        """G3 round-11 P1: main_healthy treats a moved/removed /v1/models
        route as DOWN, not up. 2xx + 401 = up (the engine is answering,
        auth-walled); 404/410 = down (route gone); other 4xx (405 etc.) =
        up (a route-level error, server alive); 5xx = down. This feeds
        health.json `main_healthy` -> the harness exit-82 gate (S3.1)."""
        import urllib.error

        cfg = self._cfg()
        ep = cfg["main_endpoint"]

        def http(code):
            return urllib.error.HTTPError(
                ep + "/v1/models",
                code,
                "x",
                {},
                None,  # type: ignore[arg-type]
            )

        # 401 (auth-walled, expected healthy shape) -> up
        with mock.patch.object(mgr.urllib.request, "urlopen", side_effect=http(401)):
            self.assertTrue(mgr.main_healthy(ep), "401 = up")
        # 404 (route moved/removed) -> down (the r11 P1 fix)
        with mock.patch.object(mgr.urllib.request, "urlopen", side_effect=http(404)):
            self.assertFalse(mgr.main_healthy(ep), "404 = down")
        # 410 (route removed) -> down
        with mock.patch.object(mgr.urllib.request, "urlopen", side_effect=http(410)):
            self.assertFalse(mgr.main_healthy(ep), "410 = down")
        # 405 (route-level error, server alive) -> up
        with mock.patch.object(mgr.urllib.request, "urlopen", side_effect=http(405)):
            self.assertTrue(mgr.main_healthy(ep), "405 = up")
        # 503 (server error) -> down
        with mock.patch.object(mgr.urllib.request, "urlopen", side_effect=http(503)):
            self.assertFalse(mgr.main_healthy(ep), "503 = down")

        # 2xx handled by urlopen returning normally -> up
        class _Resp:
            def read(self, n):
                return b""

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        with mock.patch.object(mgr.urllib.request, "urlopen", return_value=_Resp()):
            self.assertTrue(mgr.main_healthy(ep), "2xx = up")

    def test_wait_instance_healthy_shared_boot_deadline(self):
        """G3 round-11 P2: a shared boot_deadline_s caps the whole spawn
        sequence at boot_target_s, so N instances cannot each burn the
        full per-instance budget (S2.8 #1: 'Cold boot → main + N healthy
        ≤ 180 s'). When the deadline has already passed, the wait returns
        immediately (last probe) instead of looping until a fresh
        per-instance boot_target_s. None falls back to per-instance."""
        cfg = self._cfg()
        # A probe that is always down; with a shared deadline in the past,
        # the wait must return quickly (last=False), not sleep a full
        # boot_target_s.
        with mock.patch.object(mgr, "instance_healthy", return_value=False):
            out = mgr.wait_instance_healthy(
                0,
                cfg,
                boot_deadline_s=0.0,  # already passed
            )
        self.assertFalse(out)
        # None -> per-instance deadline (returns the last probe, which was
        # down) — the behaviour the hermetic test can observe without a GPU.
        with mock.patch.object(mgr, "instance_healthy", return_value=True):
            out2 = mgr.wait_instance_healthy(0, cfg)
        self.assertTrue(out2, "a healthy probe with no deadline returns True")

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
        # first live-run bug that class). places=6 (not 9): the guard is
        # against formula DRIFT (a constant change), not float noise —
        # 1e-6 GB = ~1 MiB of slack, still 3+ orders of magnitude below
        # any realistic constant change.
        self.assertAlmostEqual(need, 15.078536032, places=6)


# --------------------------------------------------------------------------
# MAP 2.6 — S2.2 escalation (two-fail -> degraded, five-fail -> restart)
# --------------------------------------------------------------------------
class TestEscalation(unittest.TestCase):
    """2.6 / S2.2: `Two consecutive fails -> degraded; five -> restart
    attempt.` The steady-loop per-instance state machine drives it."""

    def _cfg(self):
        return mgr.load_config(ENGINE_YAML, SERVING_YAML)

    def _runtime(self, cfg):
        return mgr.Runtime(cfg, [0], ["healthy"])

    def test_two_consecutive_fails_degrades(self):
        """A single miss does NOT degrade (the S2.2 two-consecutive rule);
        a second consecutive miss does (health -> degraded)."""
        cfg = self._cfg()
        rt = self._runtime(cfg)
        with mock.patch.object(mgr, "instance_healthy", return_value=False):
            mgr._refresh_states(rt, [0])  # miss 1
        self.assertEqual(rt.entries[0].state, "healthy")
        self.assertEqual(rt.entries[0].fails, 1)
        with mock.patch.object(mgr, "instance_healthy", return_value=False):
            mgr._refresh_states(rt, [0])  # miss 2
        self.assertEqual(rt.entries[0].state, "degraded")
        self.assertEqual(rt.entries[0].fails, 2)

    def test_healthy_probe_resets_the_fail_counter(self):
        """A healthy probe resets `fails` to 0 (the rule counts
        CONSECUTIVE fails; a recovering instance never reaches the streak)."""
        cfg = self._cfg()
        rt = self._runtime(cfg)
        with mock.patch.object(mgr, "instance_healthy", return_value=False):
            mgr._refresh_states(rt, [0])
        self.assertEqual(rt.entries[0].fails, 1)
        with mock.patch.object(mgr, "instance_healthy", return_value=True):
            mgr._refresh_states(rt, [0])
        self.assertEqual(rt.entries[0].state, "healthy")
        self.assertEqual(rt.entries[0].fails, 0)

    def test_five_fails_issues_a_restart(self):
        """fails >= RESTART_FAILS (5) + no systemd-side restart -> the
        manager issues `systemctl restart bonsai2-server@i` and counts it
        in the instance's restart budget (S2.5 / global #5)."""
        cfg = self._cfg()
        rt = self._runtime(cfg)
        rt.entries[0].fails = 5
        rt.entries[0].state = "degraded"
        restarts = []
        with (
            mock.patch.object(mgr, "instance_healthy", return_value=False),
            mock.patch.object(mgr, "systemctl_base", return_value=["systemctl"]),
        ):
            mgr.check_escalation(
                rt,
                1000.0,
                base=["systemctl"],
                unit_show=lambda *a, **k: mock.Mock(stdout="NRestarts=0\n"),
                restart=lambda i, base: restarts.append(i) or 0,
                log=lambda *a: None,
            )
        self.assertEqual(restarts, [0], "the five-fail restart is issued")
        self.assertEqual(rt.entries[0].restarts, 1)
        self.assertEqual(rt.entries[0].state, "degraded")

    def test_systemd_side_restart_is_deferred(self):
        """If the unit already restarted on ITS OWN since the streak began
        (NRestarts incremented, the unit's Restart=on-failure already acted),
        the manager defers — no double restart, the streak resets."""
        cfg = self._cfg()
        rt = self._runtime(cfg)
        rt.entries[0].fails = 5
        rt.entries[0].state = "degraded"
        rt.entries[0].last_nrestarts = 0  # baseline at streak start
        restarts = []
        with (
            mock.patch.object(mgr, "instance_healthy", return_value=False),
            mock.patch.object(mgr, "systemctl_base", return_value=["systemctl"]),
        ):
            mgr.check_escalation(
                rt,
                1000.0,
                base=["systemctl"],
                # systemd itself bumped NRestarts to 1 since the baseline
                unit_show=lambda *a, **k: mock.Mock(stdout="NRestarts=1\n"),
                restart=lambda i, base: restarts.append(i) or 0,
                log=lambda *a: None,
            )
        self.assertEqual(restarts, [], "deferred — no manager restart")
        self.assertEqual(rt.entries[0].restarts, 0)
        self.assertEqual(rt.entries[0].fails, 0)

    def test_baseline_frozen_after_streak_start(self):
        """G3 PR#11 r1 P1: the NRestarts baseline is captured ONCE at the
        streak start (fails 0 -> 1) and NOT re-advanced on later
        sub-threshold cycles. A systemd-side restart that lands while
        fails is still < RESTART_FAILS must still be caught by the defer
        check at the threshold — so the baseline stays at the streak-start
        value, not the post-restart value."""
        cfg = self._cfg()
        rt = self._runtime(cfg)
        restarts = []

        def restart(i, base):
            restarts.append(i)
            return 0

        # streak start: fails 0 -> 1, baseline -1 -> captured (NRestarts=7)
        rt.entries[0].fails = 1
        with mock.patch.object(mgr, "systemctl_base", return_value=["systemctl"]):
            mgr.check_escalation(
                rt,
                1000.0,
                base=["systemctl"],
                unit_show=lambda *a, **k: mock.Mock(stdout="NRestarts=7\n"),
                restart=restart,
                log=lambda *a: None,
            )
        self.assertEqual(
            rt.entries[0].last_nrestarts, 7, "baseline captured at streak start"
        )

        # later sub-threshold cycle: baseline must NOT be re-read/advanced
        rt.entries[0].fails = 3
        with mock.patch.object(mgr, "systemctl_base", return_value=["systemctl"]):
            mgr.check_escalation(
                rt,
                1010.0,
                base=["systemctl"],
                unit_show=lambda *a, **k: mock.Mock(stdout="NRestarts=8\n"),
                restart=restart,
                log=lambda *a: None,
            )
        self.assertEqual(
            rt.entries[0].last_nrestarts,
            7,
            "baseline frozen at the streak-start value (not advanced mid-streak)",
        )
        # at the threshold: systemd already restarted (8 > 7) -> defer
        rt.entries[0].fails = 5
        with mock.patch.object(mgr, "systemctl_base", return_value=["systemctl"]):
            mgr.check_escalation(
                rt,
                1020.0,
                base=["systemctl"],
                unit_show=lambda *a, **k: mock.Mock(stdout="NRestarts=8\n"),
                restart=restart,
                log=lambda *a: None,
            )
        self.assertEqual(restarts, [], "systemd-side restart mid-streak is deferred")


# --------------------------------------------------------------------------
# MAP 2.7 — S2.4 N=0 PTQ1_0 fallback + ctx-halve retry + bookkeeping
# --------------------------------------------------------------------------
class TestFallback(unittest.TestCase):
    """2.7 / S2.4: `install retries once with the PTQ1_0 fallback` at its
    footprint; a ctx-halve retry on an allocation-tier fail; the quant +
    reduced ctx recorded in subagent-engine.yaml."""

    def _cfg(self):
        return mgr.load_config(ENGINE_YAML, SERVING_YAML)

    def test_fallback_budget_is_ptq1_footprint_plus_5pct(self):
        """S2.4 verbatim: the PTQ1_0 budget is the formula footprint
        (PTQ1_0 weights + the unchanged q4_0 KV at the same ctx/slots) +
        the S1.4 5% margin — ~13.46 GB, NOT floored at the PQ2_0 cap."""
        cfg = self._cfg()
        fb_fp = vram.instance_footprint_gb(
            cfg["fallback_size_bytes"] / 1e9,
            int(cfg["slots"]),
            int(cfg["ctx_size"]),
            mgr.KV_PER_SLOT_PER_TOKEN_GB[cfg["kv_cache"]],
        )
        self.assertAlmostEqual(mgr.fallback_footprint_gb(cfg), fb_fp)
        self.assertAlmostEqual(mgr.fallback_budget_gb(cfg), fb_fp * 1.05, places=6)
        # the fallback budget is strictly smaller than the PQ2_0 cap
        self.assertLess(mgr.fallback_budget_gb(cfg), float(cfg["budget_gb"]))

    def test_fallback_quant_available(self):
        cfg = self._cfg()
        self.assertTrue(mgr.fallback_quant_available(cfg))
        cfg2 = dict(cfg)
        cfg2["fallback_size_bytes"] = None
        self.assertFalse(mgr.fallback_quant_available(cfg2))

    def _tmp_engine_yaml(self):
        import tempfile

        fd, path = tempfile.mkstemp(suffix=".yaml")
        os.close(fd)
        Path(path).write_text(ENGINE_YAML.read_text())
        return Path(path)

    def test_record_quant_in_use_is_surgical(self):
        p = self._tmp_engine_yaml()
        try:
            self.assertTrue(mgr.record_quant_in_use(p, "PTQ1_0", log=lambda *a: None))
            text = p.read_text()
            self.assertIn("quant_in_use: PTQ1_0", text)
            # idempotent + replaces the previous marker, not a duplicate
            self.assertTrue(mgr.record_quant_in_use(p, "PTQ1_0", log=lambda *a: None))
            self.assertEqual(p.read_text().count("quant_in_use:"), 1)
        finally:
            p.unlink(missing_ok=True)

    def test_record_ctx_in_use_rewrites_the_instances_key(self):
        p = self._tmp_engine_yaml()
        try:
            self.assertTrue(mgr.record_ctx_in_use(p, 131072, log=lambda *a: None))
            text = p.read_text()
            self.assertIn("ctx_size: 131072", text)
            # still valid YAML with the new value
            import yaml

            data = yaml.safe_load(text)
            self.assertEqual(data["instances"]["ctx_size"], 131072)
        finally:
            p.unlink(missing_ok=True)

    def test_record_ctx_in_use_matches_unspaced_key(self):
        """G3 PR#11 r1 P2: `ctx_size:262144` (no space after the colon,
        valid YAML) must be matched by the replace path — otherwise the
        replace misses and the insert path appends a DUPLICATE ctx_size
        key under instances:."""
        import tempfile

        d = Path(tempfile.mkdtemp())
        try:
            p = d / "eng.yaml"
            p.write_text("instances:\n  ctx_size:262144\n  count: 2\n")
            self.assertTrue(mgr.record_ctx_in_use(p, 131072, log=lambda *a: None))
            text = p.read_text()
            self.assertEqual(
                text.count("ctx_size:"), 1, f"duplicate ctx_size key: {text!r}"
            )
            import yaml

            self.assertEqual(yaml.safe_load(text)["instances"]["ctx_size"], 131072)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_rewrite_unit_model_and_memory_are_surgical(self):
        import tempfile

        d = Path(tempfile.mkdtemp())
        try:
            unit = d / "u.service"
            unit.write_text(
                "[Service]\n"
                "ExecStart=/usr/bin/docker run --rm "
                "--memory 14.78g --memory-swap 14.78g "
                "/opt/bonsai2/bin/llama-server "
                "--model /opt/bonsai2/models/bonsai-2-27b-gguf-PQ2_0.gguf "
                "--ctx-size 262144 \n"
            )
            self.assertTrue(
                mgr.rewrite_unit_model(
                    unit, "bonsai-2-27b-gguf-PTQ1_0.gguf", log=lambda *a: None
                )
            )
            self.assertTrue(
                mgr.rewrite_unit_memory(unit, 14.78, 13.46, log=lambda *a: None)
            )
            text = unit.read_text()
            self.assertIn(
                "--model /opt/bonsai2/models/bonsai-2-27b-gguf-PTQ1_0.gguf",
                text,
            )
            self.assertNotIn("PQ2_0", text)
            self.assertIn("--memory 13.46g", text)
            self.assertIn("--memory-swap 13.46g", text)
            self.assertNotIn("--memory 14.78g", text)
            # the ctx-size flag is untouched by these two rewrites
            self.assertIn("--ctx-size 262144", text)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_fallback_retry_spawns_at_default_ctx(self):
        """When the S1.4 tier grants the fallback instance at the default
        ctx, fallback_retry returns 0 + [0] and records the quant."""
        cfg = self._cfg()
        p = self._tmp_engine_yaml()
        spawned_calls = []

        def fake_spawn(c, n, need, **kw):
            spawned_calls.append((n, round(need, 3)))
            return (
                mgr.EXIT_OK,
                [0],
                "full load: 1 instance(s) spawned",
                {0: 0},
            )

        try:
            with (
                mock.patch.object(mgr, "_instance_unit_path", return_value=p),
                mock.patch.object(mgr, "rewrite_unit_model", return_value=True),
                mock.patch.object(mgr, "rewrite_unit_memory", return_value=True),
            ):
                rc, spawned, _detail, _start_rc, ctx = mgr.fallback_retry(
                    cfg, p, ["systemctl"], 1000.0, spawn=fake_spawn, log=lambda *a: None
                )
        finally:
            p.unlink(missing_ok=True)
        self.assertEqual(rc, 0)
        self.assertEqual(spawned, [0])
        self.assertEqual(ctx, int(cfg["ctx_size"]))
        self.assertEqual(len(spawned_calls), 1)

    def test_fallback_retry_ctx_halves_on_tier_fail(self):
        """S2.4: a tier fail at the default ctx -> the ctx is halved
        (multiple of 8192, floor 8192) and the tier re-run; a grant at the
        reduced ctx returns 0 + [0] with ctx_reduced and records the ctx."""
        cfg = self._cfg()
        p = self._tmp_engine_yaml()
        needs = []

        def fake_spawn(c, n, need, **kw):
            needs.append(round(need, 3))
            if len(needs) == 1:
                # first attempt (default ctx) defeats the tier at i=0
                return (mgr.EXIT_VRAM_OOM, [], "ERR_VRAM_OOM: short", {})
            # second attempt (halved ctx) grants
            return (mgr.EXIT_OK, [0], "full load: 1 instance(s)", {0: 0})

        try:
            with (
                mock.patch.object(mgr, "_instance_unit_path", return_value=p),
                mock.patch.object(mgr, "rewrite_unit_model", return_value=True),
                mock.patch.object(mgr, "rewrite_unit_memory", return_value=True),
                mock.patch.object(mgr, "rewrite_unit_ctx", return_value=True),
            ):
                rc, spawned, _detail, _start_rc, ctx = mgr.fallback_retry(
                    cfg, p, ["systemctl"], 1000.0, spawn=fake_spawn, log=lambda *a: None
                )
        finally:
            text = p.read_text() if p.exists() else ""
            p.unlink(missing_ok=True)
        self.assertEqual(rc, 0)
        self.assertEqual(spawned, [0])
        self.assertEqual(ctx, mgr.halve_ctx(int(cfg["ctx_size"])))
        self.assertEqual(len(needs), 2)
        # the reduced ctx was recorded in the engine yaml
        self.assertIn(f"ctx_size: {mgr.halve_ctx(int(cfg['ctx_size']))}", text)

    def test_fallback_retry_exit_4_when_both_ctx_fail(self):
        """If BOTH the default and the halved ctx are defeated by the tier,
        the S1.4 ERR_VRAM_OOM exit 4 applies (S2.4 live-margin OOM)."""
        cfg = self._cfg()
        p = self._tmp_engine_yaml()
        calls = []

        def fake_spawn(c, n, need, **kw):
            calls.append(1)
            return (mgr.EXIT_VRAM_OOM, [], "ERR_VRAM_OOM: short", {})

        try:
            with (
                mock.patch.object(mgr, "_instance_unit_path", return_value=p),
                mock.patch.object(mgr, "rewrite_unit_model", return_value=True),
                mock.patch.object(mgr, "rewrite_unit_memory", return_value=True),
                mock.patch.object(mgr, "rewrite_unit_ctx", return_value=True),
            ):
                rc, spawned, _detail, _start_rc, _ctx = mgr.fallback_retry(
                    cfg, p, ["systemctl"], 1000.0, spawn=fake_spawn, log=lambda *a: None
                )
        finally:
            p.unlink(missing_ok=True)
        self.assertEqual(rc, mgr.EXIT_VRAM_OOM)
        self.assertEqual(spawned, [])
        self.assertEqual(len(calls), 2, "default + halved ctx, then give up")


# --------------------------------------------------------------------------
# MAP 2.8 — S2.5 drift watch (30 s sustained cgroup pressure -> ctx halve)
# --------------------------------------------------------------------------
class TestDriftWatch(unittest.TestCase):
    """2.8 / S2.5: cgroup pressure sustained > 30 s over the --memory cap
    -> halve that instance's ctx + restart in place; bunloaded never
    re-spawned; no context-grow."""

    def _cfg(self):
        return mgr.load_config(ENGINE_YAML, SERVING_YAML)

    def _runtime(self, cfg):
        return mgr.Runtime(cfg, [0], ["healthy"])

    def test_sustained_pressure_halves_and_restarts(self):
        """Two rechecks over cap, spaced > DRIFT_PRESSURE_S, halve the ctx,
        rewrite the unit, restart in place, set ctx_reduced, count a
        restart; the cap is decimal-GB * 1e9 bytes (the S2.2 --memory cap)."""
        cfg = self._cfg()
        rt = self._runtime(cfg)
        cap = mgr._drift_cap_bytes(float(cfg["budget_gb"]))
        rewrites = []
        restarts = []
        over = int(float(cfg["budget_gb"]) * 1e9) + 1  # strictly over the cap

        def fake_cgroup_read(path):
            return over

        def fake_cgroup_path(name):
            return Path("/sys/fs/cgroup/fake")

        with mock.patch.object(
            mgr, "_instance_unit_path", return_value=Path("/u.service")
        ):
            # recheck 1: over cap -> start the pressure clock (no halve
            # yet — the 30 s sustained window has just opened)
            mgr.check_drift(
                rt,
                1000.0,
                cap,
                base=["systemctl"],
                cgroup_path=fake_cgroup_path,
                cgroup_read=fake_cgroup_read,
                rewrite=lambda u, nc, cc, log=None: rewrites.append(nc) or True,
                restart=lambda i, base: restarts.append(i) or 0,
                log=lambda *a: None,
            )
            self.assertIsNotNone(rt.entries[0].pressure_start)
            self.assertEqual(rt.entries[0].ctx, int(cfg["ctx_size"]))  # not yet
            # recheck 2 (> 30 s later): still over cap -> the halve fires
            mgr.check_drift(
                rt,
                1000.0 + mgr.DRIFT_PRESSURE_S + 1,
                cap,
                base=["systemctl"],
                cgroup_path=fake_cgroup_path,
                cgroup_read=fake_cgroup_read,
                rewrite=lambda u, nc, cc, log=None: rewrites.append(nc) or True,
                restart=lambda i, base: restarts.append(i) or 0,
                log=lambda *a: None,
            )
        self.assertEqual(rt.entries[0].ctx, mgr.halve_ctx(int(cfg["ctx_size"])))
        self.assertTrue(rt.entries[0].ctx_reduced)
        self.assertEqual(restarts, [0])
        self.assertEqual(len(rewrites), 1)

    def test_pressure_below_cap_resets_the_clock(self):
        """A reading at/below the cap resets the pressure clock — the
        `> 30 s sustained` rule needs the over-cap condition CONTINUOUS."""
        cfg = self._cfg()
        rt = self._runtime(cfg)
        cap = mgr._drift_cap_bytes(float(cfg["budget_gb"]))
        under = int(float(cfg["budget_gb"]) * 1e9) - 1
        with mock.patch.object(
            mgr, "_instance_unit_path", return_value=Path("/u.service")
        ):
            # start over cap...
            mgr.check_drift(
                rt,
                1000.0,
                cap,
                base=["systemctl"],
                cgroup_path=lambda n: Path("/x"),
                cgroup_read=lambda p: under + 2,
                rewrite=lambda *a, **k: True,
                restart=lambda *a, **k: 0,
                log=lambda *a: None,
            )
            self.assertIsNotNone(rt.entries[0].pressure_start)
            # ...then drop back under cap -> the clock resets
            mgr.check_drift(
                rt,
                1000.0 + 5,
                cap,
                base=["systemctl"],
                cgroup_path=lambda n: Path("/x"),
                cgroup_read=lambda p: under,
                rewrite=lambda *a, **k: True,
                restart=lambda *a, **k: 0,
                log=lambda *a: None,
            )
        self.assertIsNone(rt.entries[0].pressure_start)
        self.assertEqual(rt.entries[0].ctx, int(cfg["ctx_size"]))

    def test_bunloaded_instance_is_not_rechecked(self):
        """`bunloaded` instances are not in runtime.running — the drift
        watch never re-spawns / re-checks an explicitly-unloaded index."""
        cfg = self._cfg()
        rt = self._runtime(cfg)
        rt.entries[0].state = "bunloaded"
        cgroup_calls = []
        with mock.patch.object(
            mgr, "_instance_unit_path", return_value=Path("/u.service")
        ):
            mgr.check_drift(
                rt,
                1000.0,
                1,
                base=["systemctl"],
                cgroup_path=lambda n: cgroup_calls.append(n) or Path("/x"),
                cgroup_read=lambda p: 99999999999,
                rewrite=lambda *a, **k: True,
                restart=lambda *a, **k: 0,
                log=lambda *a: None,
            )
        self.assertEqual(cgroup_calls, [], "bunloaded index not cgroup-read")

    def test_drift_at_ctx_floor_is_a_noop_no_restart(self):
        """G3 PR#11 r1 P2: when the ctx is already at the floor, the
        sustained pressure window fires but the halve is a no-op — no
        unit rewrite, no in-place restart (no restart-budget count
        burned); the pressure clock still resets."""
        cfg = self._cfg()
        rt = self._runtime(cfg)
        rt.entries[0].ctx = mgr.CTX_FLOOR  # already at the floor
        cap = mgr._drift_cap_bytes(float(cfg["budget_gb"]))
        over = int(float(cfg["budget_gb"]) * 1e9) + 1
        rewrites = []
        restarts = []
        with mock.patch.object(
            mgr, "_instance_unit_path", return_value=Path("/u.service")
        ):
            mgr.check_drift(
                rt,
                1000.0,
                cap,
                base=["systemctl"],
                cgroup_path=lambda n: Path("/x"),
                cgroup_read=lambda p: over,
                rewrite=lambda u, nc, cc, log=None: rewrites.append(nc) or True,
                restart=lambda i, base: restarts.append(i) or 0,
                log=lambda *a: None,
            )
            # > 30 s later, still over cap: the window fires but the
            # floor makes the halve a no-op.
            mgr.check_drift(
                rt,
                1000.0 + mgr.DRIFT_PRESSURE_S + 1,
                cap,
                base=["systemctl"],
                cgroup_path=lambda n: Path("/x"),
                cgroup_read=lambda p: over,
                rewrite=lambda u, nc, cc, log=None: rewrites.append(nc) or True,
                restart=lambda i, base: restarts.append(i) or 0,
                log=lambda *a: None,
            )
        self.assertEqual(rewrites, [], "no ctx rewrite at the floor")
        self.assertEqual(restarts, [], "no in-place restart at the floor")
        self.assertEqual(rt.entries[0].restarts, 0, "no restart-budget count")
        self.assertEqual(rt.entries[0].ctx, mgr.CTX_FLOOR)
        self.assertIsNone(rt.entries[0].pressure_start, "the pressure clock reset")


# --------------------------------------------------------------------------
# MAP 2.9 — S2.6 serving.yaml writer (measured boot_target_s write-back)
# --------------------------------------------------------------------------
class TestServingWriter(unittest.TestCase):
    """2.9 / S2.6: `boot_target_s` is measured, not guessed — written back
    to serving.yaml from the measured boot (the S2.7 reboot test's T)."""

    def test_write_boot_target_s_rewrites_the_key(self):
        import tempfile

        d = Path(tempfile.mkdtemp())
        try:
            sy = d / "serving.yaml"
            sy.write_text(
                "main_stack:\n  container: qwen38-sglang\n"
                "  endpoint: http://localhost:30001\n  footprint_gb: 76.4\n"
                "os_reserved_gb: 8.0\nboot_target_s: 180   # measured\n"
            )
            self.assertTrue(mgr.write_boot_target_s(sy, 61.4, log=lambda *a: None))
            text = sy.read_text()
            self.assertIn("boot_target_s: 61", text)
            self.assertNotIn("boot_target_s: 180", text)
            # idempotent + no duplicate key
            mgr.write_boot_target_s(sy, 62.0, log=lambda *a: None)
            self.assertEqual(sy.read_text().count("boot_target_s:"), 1)
            import yaml

            data = yaml.safe_load(sy.read_text())
            self.assertEqual(data["boot_target_s"], 62)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_write_boot_target_s_missing_file_is_not_fatal(self):
        self.assertFalse(
            mgr.write_boot_target_s(
                Path("/nonexistent/bonsai2/serving.yaml"), 50.0, log=lambda *a: None
            )
        )


# --------------------------------------------------------------------------
# MAP 2.10 — S3.2 unload/reload manager-level semantics (control/ queue)
# --------------------------------------------------------------------------
class TestControlQueue(unittest.TestCase):
    """2.10 / S3.2: the manager is the sole pool actor — it drains the
    control/ request queue (unload -> stop + bunloaded + user_unloaded;
    reload -> S1.4 tier + start + health re-probe, partial on a tier
    fail) and writes a timestamped ack the command polls."""

    def _cfg(self):
        return mgr.load_config(ENGINE_YAML, SERVING_YAML)

    def _req(self, d, rtype, targets, rid="req1"):
        f = d / f"{rid}.json"
        f.write_text(
            json.dumps({"type": rtype, "instances": targets, "request_id": rid})
        )
        return f

    def test_unload_all_stops_and_marks_bunloaded(self):
        import tempfile

        cfg = self._cfg()
        rt = mgr.Runtime(cfg, [0, 1], ["healthy", "healthy"])
        d = Path(tempfile.mkdtemp())
        try:
            (d / "control").mkdir()
            self._req(d / "control", "unload", "all", "u1")
            stopped = []
            with mock.patch.object(mgr, "systemctl_base", return_value=["systemctl"]):
                mgr.process_control(
                    d / "control",
                    rt,
                    1000.0,
                    base=["systemctl"],
                    stop=lambda i, base: stopped.append(i) or 0,
                    wait_down=lambda *a, **k: True,
                    log=lambda *a: None,
                )
            self.assertEqual(sorted(stopped), [0, 1])
            self.assertEqual(rt.entries[0].state, "bunloaded")
            self.assertEqual(rt.entries[1].state, "bunloaded")
            self.assertEqual(rt.spawned, [])
            self.assertEqual(rt.main_only_reason, "user_unloaded")
            ack = json.loads((d / "control" / "ack-u1.json").read_text())
            self.assertEqual(sorted(ack["unloaded"]), [0, 1])
            self.assertEqual(ack["state"], "ok")
            # the request was marked done
            self.assertTrue((d / "control" / "u1.json.done").exists())
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_reload_grants_through_the_tier_and_reprobes(self):
        import tempfile

        cfg = self._cfg()
        rt = mgr.Runtime(cfg, [0, 1], ["healthy", "healthy"])
        # unload both first
        for i in (0, 1):
            rt.entries[i].state = "bunloaded"
        rt.spawned = []
        d = Path(tempfile.mkdtemp())
        try:
            (d / "control").mkdir()
            self._req(d / "control", "reload", "all", "r1")
            started = []
            with mock.patch.object(mgr, "systemctl_base", return_value=["systemctl"]):
                mgr.process_control(
                    d / "control",
                    rt,
                    1000.0,
                    base=["systemctl"],
                    free_reader=lambda: 100.0,
                    start=lambda i, base: started.append(i) or 0,
                    wait_healthy=lambda i, c, dl: True,
                    log=lambda *a: None,
                )
            self.assertEqual(sorted(started), [0, 1])
            self.assertEqual(rt.entries[0].state, "healthy")
            self.assertIsNone(rt.main_only_reason)
            ack = json.loads((d / "control" / "ack-r1.json").read_text())
            self.assertEqual(sorted(ack["reloaded"]), [0, 1])
            self.assertEqual(ack["state"], "ok")
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_reload_partial_on_tier_fail_stays_bunloaded(self):
        import tempfile

        cfg = self._cfg()
        rt = mgr.Runtime(cfg, [0, 1], ["healthy", "healthy"])
        for i in (0, 1):
            rt.entries[i].state = "bunloaded"
        rt.spawned = []
        d = Path(tempfile.mkdtemp())
        try:
            (d / "control").mkdir()
            self._req(d / "control", "reload", "all", "rp")
            # free is enough for ONE instance (need ~15 GB); the real
            # cgroup would show less free after i=0 starts, so model a
            # decreasing read (16.0 grants i=0, 10.0 rejects i=1).
            free_vals = [16.0, 10.0]

            def _free():
                return free_vals.pop(0) if free_vals else 0.0

            with mock.patch.object(mgr, "systemctl_base", return_value=["systemctl"]):
                mgr.process_control(
                    d / "control",
                    rt,
                    1000.0,
                    base=["systemctl"],
                    free_reader=_free,
                    start=lambda i, base: 0,
                    wait_healthy=lambda i, c, dl: True,
                    log=lambda *a: None,
                )
            # only i=0 is reloaded; i=1 stays bunloaded (the S1.4 partial)
            self.assertEqual(rt.entries[0].state, "healthy")
            self.assertEqual(rt.entries[1].state, "bunloaded")
            ack = json.loads((d / "control" / "ack-rp.json").read_text())
            self.assertEqual(ack["state"], "partial")
            self.assertEqual(ack.get("detail"), "WARN_VRAM_SHORTFALL")
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_bad_request_gets_an_error_ack(self):
        import tempfile

        cfg = self._cfg()
        rt = mgr.Runtime(cfg, [0], ["healthy"])
        d = Path(tempfile.mkdtemp())
        try:
            (d / "control").mkdir()
            f = d / "control" / "bad.json"
            f.write_text(
                json.dumps({"type": "bogus", "instances": "all", "request_id": "b1"})
            )
            with mock.patch.object(mgr, "systemctl_base", return_value=["systemctl"]):
                mgr.process_control(
                    d / "control", rt, 1000.0, base=["systemctl"], log=lambda *a: None
                )
            ack = json.loads((d / "control" / "ack-b1.json").read_text())
            self.assertEqual(ack["state"], "error")
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_list_of_all_strings_and_bare_int_are_error_acks(self):
        """G3 PR#11 r1 P0: the mixed-operator validation let a list like
        ["all"] through (crashing on int("all") after the ack was
        decided) and rejected a bare int target. The explicit
        validation: valid = the string "all" OR a list of ints;
        everything else gets an error ack, the request is marked done,
        no crash."""
        import tempfile

        cfg = self._cfg()
        rt = mgr.Runtime(cfg, [0, 1], ["healthy", "healthy"])
        d = Path(tempfile.mkdtemp())
        try:
            (d / "control").mkdir()
            for rid, targets in (("la", ["all"]), ("bi", 0)):
                f = d / "control" / f"{rid}.json"
                f.write_text(
                    json.dumps(
                        {"type": "unload", "instances": targets, "request_id": rid}
                    )
                )
            with mock.patch.object(mgr, "systemctl_base", return_value=["systemctl"]):
                mgr.process_control(
                    d / "control", rt, 1000.0, base=["systemctl"], log=lambda *a: None
                )
            for rid in ("la", "bi"):
                ack = json.loads((d / "control" / f"ack-{rid}.json").read_text())
                self.assertEqual(ack["state"], "error", f"{rid}: {ack}")
                self.assertTrue((d / "control" / f"{rid}.json.done").exists())
            # nothing was touched
            self.assertEqual(rt.entries[0].state, "healthy")
            self.assertEqual(rt.entries[1].state, "healthy")
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_unload_unspawned_index_is_a_noop(self):
        """G3 PR#11 r1 P0: an index the manager never spawned (not in
        runtime.spawned) is a no-op for that index — no systemctl stop,
        no state change, not in the ack's unloaded list (the next reload
        must not have to re-admit an index the manager did not manage)."""
        import tempfile

        cfg = self._cfg()
        # only i=0 was spawned; i=1 is configured but not running
        rt = mgr.Runtime(cfg, [0], ["healthy"])
        d = Path(tempfile.mkdtemp())
        try:
            (d / "control").mkdir()
            self._req(d / "control", "unload", "all", "u2")
            stopped = []
            with mock.patch.object(mgr, "systemctl_base", return_value=["systemctl"]):
                mgr.process_control(
                    d / "control",
                    rt,
                    1000.0,
                    base=["systemctl"],
                    stop=lambda i, base: stopped.append(i) or 0,
                    wait_down=lambda *a, **k: True,
                    log=lambda *a: None,
                )
            self.assertEqual(stopped, [0], "only the spawned index is stopped")
            self.assertEqual(rt.entries[1].state, "bdown", "unspawned index untouched")
            ack = json.loads((d / "control" / "ack-u2.json").read_text())
            self.assertEqual(ack["unloaded"], [0])
            self.assertEqual(ack["state"], "ok")
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_unload_failed_stop_keeps_prior_state(self):
        """G3 PR#11 r1 P1: a non-zero stop rc means the container may
        still be running — the manager keeps the prior state (the probe
        loop re-observes it) instead of believing a failed stop; the
        index stays in spawned so a later pass can retry."""
        import tempfile

        cfg = self._cfg()
        rt = mgr.Runtime(cfg, [0, 1], ["healthy", "healthy"])
        d = Path(tempfile.mkdtemp())
        try:
            (d / "control").mkdir()
            self._req(d / "control", "unload", [0], "u3")
            with mock.patch.object(mgr, "systemctl_base", return_value=["systemctl"]):
                mgr.process_control(
                    d / "control",
                    rt,
                    1000.0,
                    base=["systemctl"],
                    stop=lambda i, base: 1,  # the stop did not land
                    wait_down=lambda *a, **k: True,
                    log=lambda *a: None,
                )
            self.assertEqual(
                rt.entries[0].state, "healthy", "state kept on failed stop"
            )
            self.assertIn(0, rt.spawned, "index stays spawned for the retry")
            ack = json.loads((d / "control" / "ack-u3.json").read_text())
            self.assertNotIn(
                0, ack.get("unloaded", []), "failed stop not reported unloaded"
            )
            self.assertEqual(ack["state"], "ok", "the pass itself completes")
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_reload_static_pool_charges_in_pass_starts(self):
        """G3 PR#11 r1 P1: a reload pass gates each target on the SAME
        shared pool — a static free reading that fits only ONE instance
        must grant i=0 and reject i=1 (the in-pass charge), even though
        the pool is not re-read between targets. (The pre-charge test in
        TestAllocationTier pins the tier half; this pins the reload
        loop's use of it end-to-end.)"""
        import tempfile

        cfg = self._cfg()
        rt = mgr.Runtime(cfg, [0, 1], ["healthy", "healthy"])
        for i in (0, 1):
            rt.entries[i].state = "bunloaded"
        rt.spawned = []
        d = Path(tempfile.mkdtemp())
        try:
            (d / "control").mkdir()
            self._req(d / "control", "reload", "all", "r2")
            started = []
            with mock.patch.object(mgr, "systemctl_base", return_value=["systemctl"]):
                mgr.process_control(
                    d / "control",
                    rt,
                    1000.0,
                    base=["systemctl"],
                    free_reader=lambda: 15.10,  # static: fits exactly one
                    start=lambda i, base: started.append(i) or 0,
                    wait_healthy=lambda i, c, dl: True,
                    log=lambda *a: None,
                )
            self.assertEqual(started, [0], "i=0 reloaded; i=1 rejected by the charge")
            self.assertEqual(rt.entries[1].state, "bunloaded")
            ack = json.loads((d / "control" / "ack-r2.json").read_text())
            self.assertEqual(ack["reloaded"], [0])
            self.assertEqual(ack["state"], "partial")
            self.assertEqual(ack.get("detail"), "WARN_VRAM_SHORTFALL")
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_wait_port_down_default_is_bounded_at_5s(self):
        """G3 PR#11 r1 P2: the unload drain defaults to 5 s (the stop is
        `docker rm -f`, near-instant); a port that never frees must
        return False around the 5 s cap, not 30 s — the 10 s probe
        cadence cannot stall 30 s x N behind the drain."""
        import time as _time

        calls = []

        def slow_check(port):
            calls.append(_time.monotonic())
            return False

        t0 = _time.monotonic()
        out = mgr.wait_port_down(30101, check=slow_check)
        elapsed = _time.monotonic() - t0
        self.assertFalse(out)
        # ~5 s cap: the drain polled until the deadline (poll 0.5 s), not
        # the old 30 s default.
        self.assertLess(elapsed, 7.0, f"drain took {elapsed:.1f} s — cap moved?")
        self.assertGreaterEqual(elapsed, 4.5, f"drain exited early at {elapsed:.1f} s")


if __name__ == "__main__":
    unittest.main()
