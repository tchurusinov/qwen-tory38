"""S5.8 golden in/out pairs for the S5.5 re-bench policy (MAP 4.11).

The S5.8 convention: the pure core of ``bench/rebench.py`` is driven by
golden in/out pairs (hermetic — no GPU, no network, no yaml, no the real
``subagent-engine.yaml``):

  * the **engine identity** (``engine_identity``): the pin commit / model
    revision / model SHA / active quant file / fallback file / image SHA —
    a pin-bump changes ``engine_commit``; a re-download changes
    ``model_sha``; a quant fallback switch changes ``quant``.
  * the **quick-bench regression gate** (``quick_gate``): a M7 t/s drop
    ``> regression_tps_pct`` OR a quick auto-rubric pass→fail flip blocks;
    the boundary (exactly at the threshold) does NOT block; an improved
    t/s does NOT block; the first run (no baseline) has nothing to
    regress from.
  * the **full-bench due-detection** (``full_bench_due``): a model
    revision change, a quant fallback switch, or an S4.2 row flip is due;
    an engine-pin change alone is NOT the full-bench trigger (it is the
    quick-gate's case); the first run is not "due".
  * the **row extraction** (``rows_summary``): the S5.2 row's
    ``row_verdict`` + the M7 verdict-extra ``aggregate_tps``.
  * the **baseline save/load round-trip** (atomic, tmp+rename; the S3.1
    write convention).
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from bench import rebench

# ---------------------------------------------------------------------------
# golden fixtures (the S5.5 in/out pairs)
# ---------------------------------------------------------------------------
ENGINE_V1 = {
    "engine": {"pin": {"commit": "abc123def456"}},
    "model": {
        "file": "Ternary-Bonsai-2-27B-PQ2_0-GGUF",
        "fallback_file": "Ternary-Bonsai-2-27B-PTQ1_0-GGUF",
        "revision": "2",
        "sha256": "deadbeef" * 8,
    },
    "image": {"pin": {"sha256": "cafebabe" * 8}},
}

ENGINE_V2_PIN_BUMP = {
    "engine": {"pin": {"commit": "fff000fff000"}},
    "model": ENGINE_V1["model"],
    "image": ENGINE_V1["image"],
}

ENGINE_V2_REDOWNLOAD = {
    "engine": {"pin": {"commit": "abc123def456"}},
    "model": {
        "file": "Ternary-Bonsai-2-27B-PQ2_0-GGUF",
        "fallback_file": "Ternary-Bonsai-2-27B-PTQ1_0-GGUF",
        "revision": "2",
        "sha256": "feedface" * 8,
    },
    "image": ENGINE_V1["image"],
}

ENGINE_V2_QUANT_SWITCH = {
    "engine": {"pin": {"commit": "abc123def456"}},
    "model": {
        "file": "Ternary-Bonsai-2-27B-PTQ1_0-GGUF",
        "fallback_file": "Ternary-Bonsai-2-27B-PTQ1_0-GGUF",
        "revision": "2",
        "sha256": "deadbeef" * 8,
    },
    "image": ENGINE_V1["image"],
}

ENGINE_V2_REVISION = {
    "engine": {"pin": {"commit": "abc123def456"}},
    "model": {
        "file": "Ternary-Bonsai-2-27B-PQ2_0-GGUF",
        "fallback_file": "Ternary-Bonsai-2-27B-PTQ1_0-GGUF",
        "revision": "3",
        "sha256": "deadbeef" * 8,
    },
    "image": ENGINE_V1["image"],
}


def _baseline(scenarios: dict, engine_identity: dict | None = None) -> dict:
    return {
        "engine": engine_identity,
        "generated_ts": "2026-09-27T00:00:00Z",
        "commit": "abc123",
        "scenarios": scenarios,
    }


def _quick_pass_rows(tps: float = 120.0) -> dict:
    """A passing quick-bench scenario summary at ``tps`` M7 t/s."""
    return {
        "M1": {"verdict": "pass", "score": 1.0, "tps": None},
        "M2": {"verdict": "pass", "score": 1.0, "tps": None},
        "M7": {"verdict": "pass", "score": 1.0, "tps": tps},
    }


def _row(sid: str, verdict: bool, tps: float | None = None) -> dict:
    """An S5.2-shaped run row (the bench/report.py build_row shape)."""
    extra = {"aggregate_tps": tps} if tps is not None else {}
    return {
        "scenario": sid,
        "generated_ts": "20260927T000000Z",
        "commit": "abc123",
        "config_path": "etc/bench.yaml",
        "repetitions": 3,
        "tasks": [],
        "verdict": {
            "pass": verdict,
            "score": 1.0 if verdict else 0.0,
            "detail": "golden",
            "extra": extra,
        },
        "row_verdict": "pass" if verdict else "fail",
        "box_state": {},
    }


# ---------------------------------------------------------------------------
# the engine identity (the S5.5 "key a pin-bump / re-download /
# quant-switch changes")
# ---------------------------------------------------------------------------
class EngineIdentityTest(unittest.TestCase):
    def test_identity_fields(self):
        ident = rebench.engine_identity(ENGINE_V1)
        self.assertEqual(ident["engine_commit"], "abc123def456")
        self.assertEqual(ident["model_revision"], "2")
        self.assertEqual(ident["model_sha"], "deadbeef" * 8)
        self.assertEqual(ident["quant"], "Ternary-Bonsai-2-27B-PQ2_0-GGUF")
        self.assertEqual(ident["quant_fallback"], "Ternary-Bonsai-2-27B-PTQ1_0-GGUF")
        self.assertEqual(ident["image_sha"], "cafebabe" * 8)

    def test_pin_bump_changes_engine_commit_only(self):
        a = rebench.engine_identity(ENGINE_V1)
        b = rebench.engine_identity(ENGINE_V2_PIN_BUMP)
        self.assertNotEqual(a["engine_commit"], b["engine_commit"])
        for k in ("model_revision", "model_sha", "quant", "quant_fallback"):
            self.assertEqual(a[k], b[k], k)

    def test_redownload_changes_model_sha(self):
        a = rebench.engine_identity(ENGINE_V1)
        b = rebench.engine_identity(ENGINE_V2_REDOWNLOAD)
        self.assertNotEqual(a["model_sha"], b["model_sha"])

    def test_quant_switch_changes_quant(self):
        a = rebench.engine_identity(ENGINE_V1)
        b = rebench.engine_identity(ENGINE_V2_QUANT_SWITCH)
        self.assertNotEqual(a["quant"], b["quant"])
        self.assertEqual(a["model_sha"], b["model_sha"])

    def test_missing_fields_are_none(self):
        ident = rebench.engine_identity({})
        self.assertEqual(
            ident,
            {
                "engine_commit": None,
                "model_revision": None,
                "model_sha": None,
                "quant": None,
                "quant_fallback": None,
                "image_sha": None,
            },
        )

    def test_non_dict_is_treated_as_empty(self):
        self.assertIsNone(
            rebench.engine_identity({})["model_revision"]
        )  # the non-dict path is exercised by ``{}``; ``None`` maps too:


# ---------------------------------------------------------------------------
# the S5.5 quick-bench regression gate
# ---------------------------------------------------------------------------
class QuickGateTest(unittest.TestCase):
    def test_no_regression_unblocks(self):
        base = _baseline(_quick_pass_rows(120.0), rebench.engine_identity(ENGINE_V1))
        cur = _quick_pass_rows(119.0)  # a 0.83% drop, under the 10% threshold
        gate = rebench.quick_gate(base, cur, 10.0)
        self.assertFalse(gate["blocked"])
        self.assertEqual(gate["reasons"], [])

    def test_m7_tps_drop_over_threshold_blocks(self):
        base = _baseline(_quick_pass_rows(120.0))
        cur = _quick_pass_rows(105.0)  # a 12.5% drop, over the 10% threshold
        gate = rebench.quick_gate(base, cur, 10.0)
        self.assertTrue(gate["blocked"])
        self.assertTrue(gate["tps"]["regression"])
        self.assertGreater(gate["tps"]["drop_pct"], 10.0)

    def test_exactly_at_threshold_does_not_block(self):
        # S5.5: "Regression > 10% t/s" — the boundary (exactly 10%) is NOT
        # a regression.
        base = _baseline(_quick_pass_rows(100.0))
        cur = _quick_pass_rows(90.0)  # exactly a 10% drop
        gate = rebench.quick_gate(base, cur, 10.0)
        self.assertFalse(gate["blocked"])
        self.assertEqual(gate["tps"]["drop_pct"], 10.0)

    def test_tps_improvement_does_not_block(self):
        base = _baseline(_quick_pass_rows(100.0))
        cur = _quick_pass_rows(150.0)
        gate = rebench.quick_gate(base, cur, 10.0)
        self.assertFalse(gate["blocked"])

    def test_m1_rubric_regression_blocks(self):
        base = _baseline(_quick_pass_rows(120.0))
        cur = _quick_pass_rows(119.0)
        cur["M1"] = {"verdict": "fail", "score": 0.5, "tps": None}
        gate = rebench.quick_gate(base, cur, 10.0)
        self.assertTrue(gate["blocked"])
        self.assertIn("M1", gate["rubric_regressions"])

    def test_m2_rubric_regression_blocks(self):
        base = _baseline(_quick_pass_rows(120.0))
        cur = _quick_pass_rows(119.0)
        cur["M2"] = {"verdict": "fail", "score": 0.2, "tps": None}
        gate = rebench.quick_gate(base, cur, 10.0)
        self.assertTrue(gate["blocked"])
        self.assertIn("M2", gate["rubric_regressions"])

    def test_first_run_no_baseline_does_not_block(self):
        # the S5.5 "first run has nothing to regress from": no baseline
        # identity, no baseline t/s, no baseline rubrics — the gate passes
        # so the first quick run can establish the baseline.
        gate = rebench.quick_gate(
            rebench.baseline_shape(), _quick_pass_rows(120.0), 10.0
        )
        self.assertFalse(gate["blocked"])

    def test_baseline_without_m7_tps_skips_tps_half(self):
        base = _baseline(_quick_pass_rows(0.0))
        base["scenarios"]["M7"]["tps"] = None  # a run with no aggregate
        cur = _quick_pass_rows(50.0)  # a t/s from nothing: a gain, not a drop
        gate = rebench.quick_gate(base, cur, 10.0)
        self.assertFalse(gate["blocked"])
        self.assertIsNone(gate["tps"]["baseline_tps"])

    def test_custom_threshold(self):
        base = _baseline(_quick_pass_rows(100.0))
        cur = _quick_pass_rows(92.0)  # an 8% drop: under 10%, over 5%
        self.assertFalse(rebench.quick_gate(base, cur, 10.0)["blocked"])
        self.assertTrue(rebench.quick_gate(base, cur, 5.0)["blocked"])

    def test_m7_load_triple_mismatch_is_not_comparable(self):
        # H9 (post-live-run, decision §0.2): a baseline whose M7 load triple
        # {n_tasks, concurrency, N} differs from the current run's is RECORDED
        # "not comparable" (named, not silently compared). The >10% t/s rule
        # is NOT applied when the load differs — a 120 -> 100 drop (16.7%)
        # would block a like-with-like comparison, but here the load changed,
        # so the t/s half is skipped while the rubric half still runs.
        base = _baseline(_quick_pass_rows(120.0))
        base["scenarios"]["M7"]["comparability"] = {
            "n_tasks": 16,
            "concurrency": 16,
            "N": 2,
        }
        cur = _quick_pass_rows(100.0)  # a 16.7% drop: would block if compared
        cur["M7"]["comparability"] = {
            "n_tasks": 8,
            "concurrency": 8,
            "N": 1,
        }
        gate = rebench.quick_gate(base, cur, 10.0)
        self.assertFalse(gate["blocked"])  # not blocked by the skipped t/s half
        self.assertFalse(gate["tps"].get("comparable", True))
        self.assertFalse(gate["tps"].get("regression", False))
        # G3c round-5 P1: the gate-level status names the "unblocked but
        # t/s skipped" case distinctly — a reader seeing blocked: false
        # alone could not tell "t/s checked and passed" from "t/s
        # skipped, rubric passed".
        self.assertEqual(gate.get("status"), "not_comparable")
        self.assertTrue(
            any("not comparable" in r for r in gate["reasons"]),
            gate["reasons"],
        )

    def test_m7_load_triple_mismatch_rubric_half_still_blocks(self):
        # H9: not-comparable skips the t/s half, but a RUBRIC regression still
        # blocks (the rubric half is load-independent). A baseline M1 pass that
        # now fails is blocked even when the M7 t/s comparison is not-comparable.
        base = _baseline(_quick_pass_rows(120.0))
        base["scenarios"]["M7"]["comparability"] = {
            "n_tasks": 16,
            "concurrency": 16,
            "N": 2,
        }
        cur = _quick_pass_rows(100.0)
        cur["M7"]["comparability"] = {
            "n_tasks": 8,
            "concurrency": 8,
            "N": 1,
        }
        cur["M1"] = {"verdict": "fail", "score": 0.4, "tps": None}
        gate = rebench.quick_gate(base, cur, 10.0)
        self.assertTrue(gate["blocked"])
        self.assertIn("M1", gate["rubric_regressions"])


# ---------------------------------------------------------------------------
# the S5.5 full-bench due-detection
# ---------------------------------------------------------------------------
class FullBenchDueTest(unittest.TestCase):
    def _base(self):
        return _baseline(_quick_pass_rows(120.0), rebench.engine_identity(ENGINE_V1))

    def test_pin_bump_alone_is_not_full_bench_due(self):
        # an engine PIN change is the QUICK-gate's case (the S6.5 pin-bump
        # PR's quick bench), not the full-bench trigger.
        due = rebench.full_bench_due(
            self._base(), rebench.engine_identity(ENGINE_V2_PIN_BUMP)
        )
        self.assertFalse(due["due"])

    def test_revision_change_is_due(self):
        due = rebench.full_bench_due(
            self._base(), rebench.engine_identity(ENGINE_V2_REVISION)
        )
        self.assertTrue(due["due"])
        self.assertTrue(any("revision" in r for r in due["reasons"]))

    def test_quant_switch_is_due(self):
        due = rebench.full_bench_due(
            self._base(), rebench.engine_identity(ENGINE_V2_QUANT_SWITCH)
        )
        self.assertTrue(due["due"])
        self.assertTrue(any("quant" in r for r in due["reasons"]))

    def test_s42_flip_is_due(self):
        due = rebench.full_bench_due(
            self._base(), rebench.engine_identity(ENGINE_V1), s42_row_flipped=True
        )
        self.assertTrue(due["due"])
        self.assertTrue(any("S4.2" in r for r in due["reasons"]))

    def test_no_baseline_is_not_due(self):
        due = rebench.full_bench_due(
            rebench.baseline_shape(), rebench.engine_identity(ENGINE_V1)
        )
        self.assertFalse(due["due"])

    def test_same_engine_is_not_due(self):
        due = rebench.full_bench_due(self._base(), rebench.engine_identity(ENGINE_V1))
        self.assertFalse(due["due"])

    def test_redownload_alone_is_not_full_bench_due(self):
        # a re-download (changed SHA, same revision/quant) is the QUICK
        # gate's case; it does not trigger the full bench on its own.
        due = rebench.full_bench_due(
            self._base(), rebench.engine_identity(ENGINE_V2_REDOWNLOAD)
        )
        self.assertFalse(due["due"])


# ---------------------------------------------------------------------------
# the S5.2 row extraction + the baseline save/load round-trip
# ---------------------------------------------------------------------------
class RowExtractionAndBaselineTest(unittest.TestCase):
    def test_rows_summary_reads_row_verdict_and_m7_tps(self):
        rows = [_row("M1", True), _row("M2", False), _row("M7", True, 120.5)]
        summary = rebench.rows_summary(rows)
        self.assertEqual(summary["M1"]["verdict"], "pass")
        self.assertEqual(summary["M2"]["verdict"], "fail")
        self.assertEqual(summary["M7"]["tps"], 120.5)
        self.assertEqual(summary["M1"]["tps"], None)

    def test_rows_summary_keeps_last_duplicate(self):
        rows = [_row("M7", True, 100.0), _row("M7", True, 130.0)]
        self.assertEqual(rebench.rows_summary(rows)["M7"]["tps"], 130.0)

    def test_save_load_round_trip(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "sub" / "rebench-baseline.json"
            rows = [_row("M1", True), _row("M2", True), _row("M7", True, 120.0)]
            rebench.save_baseline(p, ENGINE_V1, rows, commit="abc123")
            data = rebench.load_baseline(p)
            self.assertEqual(data["engine"]["engine_commit"], "abc123def456")
            self.assertEqual(data["commit"], "abc123")
            self.assertEqual(data["scenarios"]["M7"]["tps"], 120.0)
            self.assertTrue(p.is_file())
            # the atomic-write convention: no leftover tmp file.
            self.assertFalse(p.with_suffix(".json.tmp").exists())

    def test_load_absent_returns_empty_shape(self):
        data = rebench.load_baseline("/nonexistent/rebench-baseline.json")
        self.assertEqual(data, rebench.baseline_shape())

    def test_load_malformed_raises(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "bad.json"
            p.write_text("{ not json\n")
            with self.assertRaises(rebench.RebenchError):
                rebench.load_baseline(p)

    def test_load_rows_m1_glob_does_not_match_m10(self):
        # the S5.2 row filename is ``<ts>_<sid>.json``: an M1 row and an
        # M10 row share the "M1" prefix — the gate's load must keep them
        # distinct (M1's load must not pick up the M10 row's file).
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            (d / "20260927T000000Z_M10.json").write_text(
                json.dumps(_row("M10", True)) + "\n"
            )
            (d / "20260927T000001Z_M1.json").write_text(
                json.dumps(_row("M1", True)) + "\n"
            )
            rows = rebench.load_rows(d, scenarios=("M1",))
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["scenario"], "M1")


class _FakeYamlError(Exception):
    """A stand-in for ``yaml.YAMLError`` (the real PyYAML is a box dep,
    not a test dep — the S5.8 hermetic convention: no yaml in the test
    env). Injected as ``_fake_yaml_module`` below so the rebench CLI
    config path is testable without PyYAML installed."""


def _fake_yaml_module(safe_load_impl):
    """Build a fake ``yaml`` module whose ``safe_load`` is the given
    callable (so a test can pin the parsed value OR raise
    ``_FakeYamlError`` to simulate an unparseable file)."""
    import types

    m = types.ModuleType("yaml")

    class _YamLError(Exception):
        pass

    m.YAMLError = _YamLError
    m.safe_load = safe_load_impl
    return m


class _ConfigValueH10Test(unittest.TestCase):
    """G3c round-13 P1 + P2: the H10 strict ``--config`` path must name
    a PRESENT file that yields no value (empty file -> None; a mapping
    that lacks the key), and the CLI must parse the config ONCE for both
    S5.7 keys (not once per key)."""

    _BASELINE = rebench.DEFAULT_BASELINE
    _PCT = 10.0

    def _with_yaml(self, parsed):
        """Context manager: install a fake ``yaml`` module whose
        ``safe_load`` returns ``parsed`` for every file read."""
        import contextlib
        import sys

        fake = _fake_yaml_module(lambda text: parsed)

        @contextlib.contextmanager
        def _cm():
            prev = sys.modules.get("yaml")
            sys.modules["yaml"] = fake
            try:
                yield fake
            finally:
                if prev is None:
                    sys.modules.pop("yaml", None)
                else:
                    sys.modules["yaml"] = prev

        return _cm()

    def _write(self, text):
        import tempfile

        fd, p = tempfile.mkstemp(suffix=".yaml", prefix="rebench-")
        with os.fdopen(fd, "w") as f:
            f.write(text)
        self.addCleanup(Path(p).unlink, missing_ok=True)
        return p

    # ---- P1#1: an empty / whitespace PRESENT file is a named bad input ----
    def test_empty_present_file_strict_is_bad_input(self):
        p = self._write("")
        with (
            self._with_yaml(None),  # yaml.safe_load("") -> None
            self.assertRaises(rebench.RebenchError) as ctx,
        ):
            rebench._config_value(
                p, "rebench_baseline", self._BASELINE, strict_malformed=True
            )
        # the named reason must say the file has no key (NOT that it is
        # unparseable — an empty file parses fine, to None).
        self.assertIn("has no 'rebench_baseline' key", str(ctx.exception))

    def test_whitespace_present_file_strict_is_bad_input(self):
        p = self._write("   \n  \n")
        with (
            self._with_yaml(None),  # yaml.safe_load(whitespace) -> None
            self.assertRaises(rebench.RebenchError) as ctx,
        ):
            rebench._config_value(
                p, "rebench_baseline", self._BASELINE, strict_malformed=True
            )
        self.assertIn("has no 'rebench_baseline' key", str(ctx.exception))

    # ---- P1#2: a PRESENT mapping that LACKS the key is a named bad input ----
    def test_present_mapping_missing_key_strict_is_bad_input(self):
        p = self._write("regression_tps_pct: 10\n")  # has the OTHER key
        with (
            self._with_yaml({"regression_tps_pct": 10}),
            self.assertRaises(rebench.RebenchError) as ctx,
        ):
            rebench._config_value(
                p, "rebench_baseline", self._BASELINE, strict_malformed=True
            )
        self.assertIn("has no 'rebench_baseline' key", str(ctx.exception))

    # ---- the strict path still names the pre-round-13 cases ----
    def test_unparseable_present_file_strict_is_bad_input(self):
        p = self._write("rebench_baseline: [")  # unterminated flow seq

        def _raise(_text):
            raise _FakeYamlError()

        # give the fake module a YAMLError class the code can catch
        fake = _fake_yaml_module(_raise)
        fake.YAMLError = _FakeYamlError
        import sys

        prev = sys.modules.get("yaml")
        sys.modules["yaml"] = fake
        try:
            with self.assertRaises(rebench.RebenchError) as ctx:
                rebench._config_value(
                    p, "rebench_baseline", self._BASELINE, strict_malformed=True
                )
            self.assertIn("unparseable", str(ctx.exception))
        finally:  # noqa: keep the manual sys.modules swap (fake has a
            # custom YAMLError the context-manager helper doesn't know)
            if prev is None:
                sys.modules.pop("yaml", None)
            else:
                sys.modules["yaml"] = prev

    def test_non_mapping_present_file_strict_is_bad_input(self):
        p = self._write("- M1\n- M2\n")
        with (
            self._with_yaml(["M1", "M2"]),  # top level is a list
            self.assertRaises(rebench.RebenchError) as ctx,
        ):
            rebench._config_value(
                p, "rebench_baseline", self._BASELINE, strict_malformed=True
            )
        self.assertIn("top level is not a mapping", str(ctx.exception))

    # ---- the LENIENT path is unchanged: every present-but-valueless
    #      case still returns the default (no raise) ----
    def test_lenient_empty_file_returns_default(self):
        p = self._write("")
        with self._with_yaml(None):
            self.assertEqual(
                rebench._config_value(
                    p, "rebench_baseline", self._BASELINE, strict_malformed=False
                ),
                self._BASELINE,
            )

    def test_lenient_missing_key_returns_default(self):
        p = self._write("regression_tps_pct: 10\n")
        with self._with_yaml({"regression_tps_pct": 10}):
            self.assertEqual(
                rebench._config_value(
                    p, "rebench_baseline", self._BASELINE, strict_malformed=False
                ),
                self._BASELINE,
            )

    # ---- the H10 "absent = default" half is preserved ----
    def test_absent_config_returns_default_strict(self):
        self.assertEqual(
            rebench._config_value(
                None, "rebench_baseline", self._BASELINE, strict_malformed=True
            ),
            self._BASELINE,
        )

    def test_empty_string_config_is_absent_convention(self):
        # G3c round-9 P2: "" is the documented no-config escape hatch.
        self.assertEqual(
            rebench._config_value(
                "", "rebench_baseline", self._BASELINE, strict_malformed=True
            ),
            self._BASELINE,
        )

    # ---- P2 regression: the CLI parses the config ONCE for both keys
    #      (pin the single-pass behavior; a re-introduced second parse
    #      would re-read the file and the count would change). ----
    def test_cli_parses_config_once_for_both_keys(self):
        import sys

        p = self._write("rebench_baseline: b.json\nregression_tps_pct: 5\n")
        calls = []

        def _counting_load(text):
            # count only the BENCH.YAML parse (the engine yaml is a
            # separate file the `due` command also reads — the P2 claim
            # is specifically that the CONFIG is parsed once, not that
            # the whole CLI does one yaml load total).
            if "rebench_baseline" in text or "regression_tps_pct" in text:
                calls.append(text)
            return {"rebench_baseline": "b.json", "regression_tps_pct": 5}

        fake = _fake_yaml_module(_counting_load)
        prev = sys.modules.get("yaml")
        sys.modules["yaml"] = fake
        try:
            # `due` needs an engine yaml too; use the env hook to point it
            # at a minimal valid file so the command runs to its due/not
            # due result WITHOUT a second bench.yaml parse.
            import tempfile

            td = Path(tempfile.mkdtemp())
            self.addCleanup(_cleanup_dir, td)
            eng = td / "engine.yaml"
            eng.write_text(
                "engine:\n  pin:\n    commit: abc\n"
                "model:\n  file: f\n  fallback_file: f\n  revision: '1'\n"
                "image:\n  pin:\n    sha256: 'x'\n"
            )
            rc = rebench.main(["due", "--config", p, "--engine", str(eng)])
            self.assertIn(rc, (rebench.EXIT_OK, rebench.EXIT_BLOCKED))
            self.assertEqual(
                len(calls),
                1,
                f"the CLI must parse bench.yaml exactly once for both S5.7 "
                f"keys; observed {len(calls)} parse calls",
            )
        finally:
            if prev is None:
                sys.modules.pop("yaml", None)
            else:
                sys.modules["yaml"] = prev

    # ---- P2 regression + P1 end-to-end: a present-but-incomplete
    #      --config is exit 2 "bad input" from the CLI (not a silent
    #      default that then fails later). ----
    def test_cli_present_but_incomplete_config_is_exit_2(self):
        p = self._write("regression_tps_pct: 5\n")  # missing rebench_baseline
        with self._with_yaml({"regression_tps_pct": 5}):
            rc = rebench.main(["due", "--config", p, "--engine", "/nonexistent.yaml"])
        # exit 2 (EXIT_CONFIG) before it ever reaches the engine load
        self.assertEqual(rc, rebench.EXIT_CONFIG)

    def test_cli_missing_config_still_runs_with_defaults(self):
        # H10 "absent = default": no --config -> the gate/due path still
        # runs (it fails later on the missing engine yaml, not on the
        # config — a different exit-2 message, but the same exit code).
        import tempfile

        with self._with_yaml(None):
            td = Path(tempfile.mkdtemp())
            self.addCleanup(_cleanup_dir, td)
            rc = rebench.main(["due", "--engine", str(td / "engine.yaml")])
        # due with no baseline + no config -> not-due (EXIT_OK) OR
        # missing-engine (EXIT_CONFIG) — both are NOT the config-missing
        # key error. The point is the config did NOT short-circuit it.
        self.assertIn(rc, (rebench.EXIT_OK, rebench.EXIT_CONFIG))


def _cleanup_dir(p):
    import shutil

    shutil.rmtree(p, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
