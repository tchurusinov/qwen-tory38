"""tests.test_doctrine_audit — S4.6 doctrine-vs-reality audit (MAP 5.6).

Hermetic (no GPU, no network, no the real bench, no PyYAML in the test
env). The S4.6 audit is a NAMED tool (``bench/doctrine_audit.py``):

  * **Citation existence** — every S4.2 row / recipe / prompt-template
    ``[M#]`` citation names a scenario that is in ``bench.yaml`` (S5.7).
  * **Win/lose recompute** — for each subagent-side S4.2 row, count the
    CONSECUTIVE losses (newest-first) on the cited scenario's bench rows;
    >= 2 consecutive losses (the S4.4 flip threshold) with NO documented
    exception = STALE (the audit flags it, exit 1; S4.7 #3 "two synthetic
    loss scenarios -> the audit flags the stale row").
  * A failing audit blocks the skill PR (S4.6; wired into gate.sh).

These are the S5.8 "golden in/out pairs" for the audit's PURE core
(``consecutive_losses`` / ``audit_row`` / ``run_audit`` — no I/O, no
yaml) + a thin set of I/O-edge tests (``load_claims`` /
``load_rows_by_scenario`` are stdlib-json; ``load_bench_scenarios`` uses
the documented fake-yaml-module edge, same convention as
``test_rebench.py``).
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from bench import doctrine_audit


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _row(scenario: str, ts: str, verdict: str) -> dict:
    """A minimal S5.2 run row (the audit reads ``scenario`` /
    ``generated_ts`` / ``row_verdict``)."""
    return {
        "scenario": scenario,
        "generated_ts": ts,
        "row_verdict": verdict,
        "verdict": {
            "pass": verdict == "pass",
            "score": 1.0 if verdict == "pass" else 0.0,
        },
    }


def _fake_yaml_module(safe_load_impl):
    """A fake ``yaml`` module (no PyYAML in the hermetic env — the
    documented test_rebench.py convention)."""
    import types

    m = types.ModuleType("yaml")

    class _YamLError(Exception):
        pass

    m.YAMLError = _YamLError
    m.safe_load = safe_load_impl
    return m


class _YamlEdge:
    """Context helper: run a body with a fake ``yaml`` module installed."""

    def __init__(self, parsed):
        self._parsed = parsed

    def __enter__(self):
        import sys

        self._prev = sys.modules.get("yaml")
        self._fake = _fake_yaml_module(lambda _t: self._parsed)
        sys.modules["yaml"] = self._fake
        return self._fake

    def __exit__(self, *exc):
        import sys

        if self._prev is None:
            sys.modules.pop("yaml", None)
        else:
            sys.modules["yaml"] = self._prev
        return False


# --------------------------------------------------------------------------
# pure core: consecutive_losses
# --------------------------------------------------------------------------
class ConsecutiveLossesTest(unittest.TestCase):
    def test_no_rows_is_zero(self):
        self.assertEqual(doctrine_audit.consecutive_losses([]), 0)

    def test_newest_pass_resets_to_zero(self):
        # newest first: pass, fail, fail -> streak 0
        rows = [
            _row("M1", "3", "pass"),
            _row("M1", "2", "fail"),
            _row("M1", "1", "fail"),
        ]
        self.assertEqual(doctrine_audit.consecutive_losses(rows), 0)

    def test_two_consecutive_losses(self):
        rows = [_row("M1", "2", "fail"), _row("M1", "1", "fail")]
        self.assertEqual(doctrine_audit.consecutive_losses(rows), 2)

    def test_streak_stops_at_a_pass(self):
        # newest first: fail, fail, pass, fail -> streak 2 (the two newest)
        rows = [
            _row("M1", "4", "fail"),
            _row("M1", "3", "fail"),
            _row("M1", "2", "pass"),
            _row("M1", "1", "fail"),
        ]
        self.assertEqual(doctrine_audit.consecutive_losses(rows), 2)

    def test_sorts_by_timestamp_not_input_order(self):
        # input order is oldest-first, but the streak is counted newest-first
        rows = [
            _row("M1", "1", "pass"),
            _row("M1", "2", "fail"),
            _row("M1", "3", "fail"),
        ]
        self.assertEqual(doctrine_audit.consecutive_losses(rows), 2)

    def test_missing_timestamp_sorts_oldest_first(self):
        # a missing ts sorts oldest (empty string), safe tie-break
        rows = [_row("M1", "", "fail"), _row("M1", "1", "fail")]
        self.assertEqual(doctrine_audit.consecutive_losses(rows), 2)


# --------------------------------------------------------------------------
# pure core: is_subagent_side
# --------------------------------------------------------------------------
class IsSubagentSideTest(unittest.TestCase):
    def test_main_side(self):
        self.assertFalse(doctrine_audit.is_subagent_side("main"))
        self.assertFalse(doctrine_audit.is_subagent_side("main, always"))

    def test_subagent_side(self):
        self.assertTrue(doctrine_audit.is_subagent_side("subagent"))
        self.assertTrue(doctrine_audit.is_subagent_side("fan-out N"))
        self.assertTrue(
            doctrine_audit.is_subagent_side("subagent draft -> main verifies")
        )
        self.assertTrue(doctrine_audit.is_subagent_side("subagent, --tools on"))
        self.assertTrue(
            doctrine_audit.is_subagent_side(
                "main, or subagent --tools on via the python tool"
            )
        )

    def test_empty_is_not_subagent(self):
        self.assertFalse(doctrine_audit.is_subagent_side(""))


# --------------------------------------------------------------------------
# pure core: audit_row (the win/lose recompute per S4.2 row)
# --------------------------------------------------------------------------
BENCH = {"M1", "M2", "M3", "M4", "M5", "M6", "M7", "M8", "M9", "M10"}


class AuditRowTest(unittest.TestCase):
    def test_no_citation(self):
        rec = doctrine_audit.audit_row("Planning", "main", None, BENCH, {}, [])
        self.assertEqual(rec["status"], "no-citation")
        self.assertIsNone(rec["citation_exists"])

    def test_missing_citation_is_a_review_defect(self):
        # cites a scenario not in bench.yaml -> missing-citation
        rec = doctrine_audit.audit_row("X", "subagent", "M99", BENCH, {}, [])
        self.assertEqual(rec["status"], "missing-citation")
        self.assertFalse(rec["citation_exists"])

    def test_no_data(self):
        # valid scenario, no rows yet -> no-data (cannot recompute; not a failure)
        rec = doctrine_audit.audit_row("X", "subagent", "M7", BENCH, {}, [])
        self.assertEqual(rec["status"], "no-data")
        self.assertEqual(rec["n_rows"], 0)

    def test_subagent_match_zero_losses(self):
        rows = {"M2": [_row("M2", "1", "pass")]}
        rec = doctrine_audit.audit_row("Tests", "subagent", "M2", BENCH, rows, [])
        self.assertEqual(rec["status"], "match")
        self.assertEqual(rec["consecutive_losses"], 0)

    def test_subagent_one_loss_is_still_match(self):
        rows = {"M5": [_row("M5", "1", "fail")]}
        rec = doctrine_audit.audit_row("QA", "subagent", "M5", BENCH, rows, [])
        self.assertEqual(rec["status"], "match")
        self.assertEqual(rec["consecutive_losses"], 1)

    def test_subagent_two_losses_no_exception_is_stale(self):
        rows = {"M9": [_row("M9", "2", "fail"), _row("M9", "1", "fail")]}
        rec = doctrine_audit.audit_row(
            "Spec chunk", "subagent, --tools on", "M9", BENCH, rows, []
        )
        self.assertEqual(rec["status"], "stale")
        self.assertEqual(rec["consecutive_losses"], 2)
        self.assertTrue(rec["is_subagent_side"])

    def test_subagent_two_losses_with_exception_is_documented(self):
        rows = {"M9": [_row("M9", "2", "fail"), _row("M9", "1", "fail")]}
        ex = [{"scenario": "M9", "reason": "known rubric seam"}]
        rec = doctrine_audit.audit_row(
            "Spec chunk", "subagent, --tools on", "M9", BENCH, rows, ex
        )
        self.assertEqual(rec["status"], "documented-exception")
        self.assertTrue(rec["documented_exception"])

    def test_main_side_with_losses_is_match(self):
        # a main-side row cannot "flip to main" — losses are supporting evidence
        rows = {"M5": [_row("M5", "2", "fail"), _row("M5", "1", "fail")]}
        rec = doctrine_audit.audit_row("Long-context", "main", "M5", BENCH, rows, [])
        self.assertEqual(rec["status"], "match")
        self.assertFalse(rec["is_subagent_side"])

    def test_exception_does_not_apply_below_threshold(self):
        # 1 loss + an exception: the exception is NOT consulted (only >= threshold)
        rows = {"M1": [_row("M1", "1", "fail")]}
        ex = [{"scenario": "M1", "reason": "x"}]
        rec = doctrine_audit.audit_row("Code-gen", "subagent", "M1", BENCH, rows, ex)
        self.assertEqual(rec["status"], "match")
        self.assertFalse(rec["documented_exception"])


# --------------------------------------------------------------------------
# pure core: audit_citation_record (recipes + templates)
# --------------------------------------------------------------------------
class AuditCitationRecordTest(unittest.TestCase):
    def test_null_bench_is_provisional(self):
        rec = doctrine_audit.audit_citation_record(
            {"id": "T3", "bench": None, "note": "no row yet"}, BENCH
        )
        self.assertEqual(rec["citation_status"], "provisional")
        self.assertIsNone(rec["citation_exists"])

    def test_valid_bench_is_ok(self):
        rec = doctrine_audit.audit_citation_record({"id": "R1", "bench": "M1"}, BENCH)
        self.assertEqual(rec["citation_status"], "ok")
        self.assertTrue(rec["citation_exists"])

    def test_missing_bench_flags(self):
        rec = doctrine_audit.audit_citation_record({"id": "R7", "bench": "M99"}, BENCH)
        self.assertEqual(rec["citation_status"], "missing")
        self.assertFalse(rec["citation_exists"])


# --------------------------------------------------------------------------
# pure core: run_audit (the S4.7 #3 synthetic-loss acceptance)
# --------------------------------------------------------------------------
def _claims(rows_shapes):
    """A minimal claims.json shape: work_allocation rows + no recipes/templates."""
    return {"work_allocation": rows_shapes, "recipes": [], "prompt_templates": []}


class RunAuditTest(unittest.TestCase):
    def test_clean_passes(self):
        claims = _claims(
            [
                {"task_shape": "Plan", "allocation": "main", "bench": None},
                {"task_shape": "Tests", "allocation": "subagent", "bench": "M2"},
            ]
        )
        rows = {"M2": [_row("M2", "1", "pass")]}
        out = doctrine_audit.run_audit(claims, BENCH, rows)
        self.assertTrue(out["pass"])
        self.assertEqual(out["missing_citations"], [])
        self.assertEqual(out["stale_rows"], [])

    def test_two_synthetic_loss_scenarios_flag_both(self):
        # S4.7 #3: two task shapes each benched as a distinct scenario, each
        # with 2 consecutive losses, no documented exception -> both stale.
        claims = _claims(
            [
                {"task_shape": "Shape A", "allocation": "subagent", "bench": "M4"},
                {"task_shape": "Shape B", "allocation": "subagent", "bench": "M5"},
            ]
        )
        rows = {
            "M4": [_row("M4", "2", "fail"), _row("M4", "1", "fail")],
            "M5": [_row("M5", "2", "fail"), _row("M5", "1", "fail")],
        }
        out = doctrine_audit.run_audit(claims, BENCH, rows)
        self.assertFalse(out["pass"])
        self.assertEqual(
            sorted(r["task_shape"] for r in out["stale_rows"]), ["Shape A", "Shape B"]
        )

    def test_missing_citation_fails_audit(self):
        claims = _claims(
            [{"task_shape": "X", "allocation": "subagent", "bench": "M99"}]
        )
        out = doctrine_audit.run_audit(claims, BENCH, {})
        self.assertFalse(out["pass"])
        self.assertEqual(len(out["missing_citations"]), 1)
        self.assertTrue(out["reasons"])

    def test_documented_exception_keeps_pass(self):
        claims = _claims(
            [
                {
                    "task_shape": "Spec",
                    "allocation": "subagent, --tools on",
                    "bench": "M9",
                }
            ]
        )
        claims["win_rule"] = {"exceptions": [{"scenario": "M9", "reason": "seam"}]}
        rows = {"M9": [_row("M9", "2", "fail"), _row("M9", "1", "fail")]}
        out = doctrine_audit.run_audit(claims, BENCH, rows)
        self.assertTrue(out["pass"])
        self.assertEqual(out["stale_rows"], [])

    def test_main_side_losses_do_not_fail(self):
        claims = _claims([{"task_shape": "QA", "allocation": "main", "bench": "M5"}])
        rows = {"M5": [_row("M5", "2", "fail"), _row("M5", "1", "fail")]}
        out = doctrine_audit.run_audit(claims, BENCH, rows)
        self.assertTrue(out["pass"])

    def test_recipe_and_template_citations_are_checked(self):
        claims = {
            "work_allocation": [],
            "recipes": [{"id": "R1", "bench": "M99"}],
            "prompt_templates": [{"id": "T1", "bench": "M9"}],
        }
        out = doctrine_audit.run_audit(claims, BENCH, {})
        self.assertFalse(out["pass"])
        # R1 (M99) missing; T1 (M9) ok
        self.assertEqual(len(out["missing_citations"]), 1)
        self.assertEqual(out["missing_citations"][0].get("id"), "R1")


# --------------------------------------------------------------------------
# I/O edge: load_claims / load_rows_by_scenario (stdlib json)
# --------------------------------------------------------------------------
class LoadClaimsTest(unittest.TestCase):
    def test_load_valid(self):
        d = tempfile.mkdtemp(prefix="da-claims-")
        self.addCleanup(lambda: Path(d).rmdir() if not os.listdir(d) else None)
        p = Path(d) / "claims.json"
        p.write_text(json.dumps({"work_allocation": []}))
        self.assertEqual(doctrine_audit.load_claims(p), {"work_allocation": []})

    def test_load_missing_raises(self):
        with self.assertRaises(doctrine_audit.AuditError):
            doctrine_audit.load_claims("/nonexistent/claims.json")

    def test_load_malformed_raises(self):
        d = tempfile.mkdtemp(prefix="da-claims-")
        p = Path(d) / "claims.json"
        p.write_text("{ not json")
        with self.assertRaises(doctrine_audit.AuditError):
            doctrine_audit.load_claims(p)

    def test_load_non_object_raises(self):
        d = tempfile.mkdtemp(prefix="da-claims-")
        p = Path(d) / "claims.json"
        p.write_text("[1, 2, 3]")
        with self.assertRaises(doctrine_audit.AuditError):
            doctrine_audit.load_claims(p)


class LoadRowsByScenarioTest(unittest.TestCase):
    def _write(self, name, obj):
        d = self._shared_dir()
        (d / name).write_text(json.dumps(obj))
        return d

    def _shared_dir(self):
        if getattr(self, "_rows_dir", None) is None:
            self._rows_dir = Path(tempfile.mkdtemp(prefix="da-rows-"))
        return self._rows_dir

    def test_groups_by_internal_scenario(self):
        # an engine-mode row named _M9_engine.json still carries scenario M9
        d = self._write(
            "20260928T000000Z_M9.json", _row("M9", "20260928T000000Z", "fail")
        )
        self._write(
            "20260928T000001Z_M9_engine.json", _row("M9", "20260928T000001Z", "fail")
        )
        out = doctrine_audit.load_rows_by_scenario(d)
        self.assertEqual(len(out["M9"]), 2)

    def test_skips_non_row_files(self):
        d = Path(tempfile.mkdtemp(prefix="da-rows-"))
        (d / "M1.json").write_text(json.dumps(_row("M1", "1", "pass")))
        (d / "rebench-baseline.json").write_text(
            json.dumps({"engine": None, "scenarios": {}})
        )
        out = doctrine_audit.load_rows_by_scenario(d)
        self.assertEqual(list(out), ["M1"])

    def test_absent_dir_is_empty_not_error(self):
        self.assertEqual(doctrine_audit.load_rows_by_scenario("/nonexistent-dir"), {})

    def test_skips_malformed_json(self):
        d = Path(tempfile.mkdtemp(prefix="da-rows-"))
        (d / "bad.json").write_text("{ broken")
        (d / "good.json").write_text(json.dumps(_row("M2", "1", "pass")))
        out = doctrine_audit.load_rows_by_scenario(d)
        self.assertEqual(list(out), ["M2"])


# --------------------------------------------------------------------------
# I/O edge: load_bench_scenarios (the yaml edge, fake module)
# --------------------------------------------------------------------------
class LoadBenchScenariosTest(unittest.TestCase):
    def test_reads_scenario_set(self):
        d = Path(tempfile.mkdtemp(prefix="da-bench-"))
        p = d / "bench.yaml"
        p.write_text("scenarios: [M1, M2]\n")
        with _YamlEdge({"scenarios": ["M1", "M2", "M3"]}):
            self.assertEqual(doctrine_audit.load_bench_scenarios(p), {"M1", "M2", "M3"})

    def test_missing_raises(self):
        with self.assertRaises(doctrine_audit.AuditError):
            doctrine_audit.load_bench_scenarios("/nonexistent/bench.yaml")

    def test_unparseable_raises(self):
        import sys

        d = Path(tempfile.mkdtemp(prefix="da-bench-"))
        p = d / "bench.yaml"
        p.write_text("scenarios: [M1,")

        def _raise(_t):
            raise sys.modules["yaml"].YAMLError()

        fake = _fake_yaml_module(_raise)
        prev = sys.modules.get("yaml")
        sys.modules["yaml"] = fake
        try:
            with self.assertRaises(doctrine_audit.AuditError):
                doctrine_audit.load_bench_scenarios(p)
        finally:
            if prev is None:
                sys.modules.pop("yaml", None)
            else:
                sys.modules["yaml"] = prev

    def test_no_scenario_key_is_empty_set(self):
        d = Path(tempfile.mkdtemp(prefix="da-bench-"))
        p = d / "bench.yaml"
        p.write_text("quick: [M1]\n")
        with _YamlEdge({"quick": ["M1"]}):
            self.assertEqual(doctrine_audit.load_bench_scenarios(p), set())

    def test_non_mapping_raises(self):
        d = Path(tempfile.mkdtemp(prefix="da-bench-"))
        p = d / "bench.yaml"
        p.write_text("- M1\n- M2\n")
        with _YamlEdge(["M1", "M2"]), self.assertRaises(doctrine_audit.AuditError):
            doctrine_audit.load_bench_scenarios(p)


class _NoYaml:
    """Context helper: force `import yaml` to raise ImportError (the hermetic
    box python3 / the gate interpreter has no PyYAML — the stdlib-fallback
    path)."""

    def __enter__(self):
        import sys

        self._prev = sys.modules.get("yaml")
        sys.modules["yaml"] = None  # `import yaml` -> ImportError

    def __exit__(self, *exc):
        import sys

        if self._prev is None:
            sys.modules.pop("yaml", None)
        else:
            sys.modules["yaml"] = self._prev
        return False


class StdlibFallbackTest(unittest.TestCase):
    """The S4.6 gate runs on the bare box `python3` (no PyYAML); these prove
    the stdlib fallback reads the tracked template's flow-sequence
    `scenarios:` line, and names a parse error for other shapes."""

    def _bench(self, text):
        d = Path(tempfile.mkdtemp(prefix="da-fb-"))
        p = d / "bench.yaml"
        p.write_text(text)
        return p

    def test_flow_sequence_without_yaml(self):
        p = self._bench("scenarios: [M1, M2, M3]\nquick: [M1, M7]\n")
        with _NoYaml():
            self.assertEqual(doctrine_audit.load_bench_scenarios(p), {"M1", "M2", "M3"})

    def test_flow_sequence_with_trailing_comment(self):
        # the tracked template shape: a scenario-set line + inline comment
        p = self._bench("# bench.yaml\nscenarios: [M1, M2, M3, M10]   # S5.2 matrix\n")
        with _NoYaml():
            self.assertEqual(
                doctrine_audit.load_bench_scenarios(p), {"M1", "M2", "M3", "M10"}
            )

    def test_non_flow_shape_without_yaml_raises(self):
        # a block-sequence scenario set is NOT the supported no-yaml shape
        p = self._bench("scenarios:\n  - M1\n  - M2\n")
        with _NoYaml(), self.assertRaises(doctrine_audit.AuditError):
            doctrine_audit.load_bench_scenarios(p)

    def test_no_scenarios_key_without_yaml_is_empty(self):
        p = self._bench("quick: [M1]\n")
        with _NoYaml():
            self.assertEqual(doctrine_audit.load_bench_scenarios(p), set())

    def test_parse_helper_direct(self):
        self.assertEqual(
            doctrine_audit._parse_bench_yaml_scenarios("scenarios: [M9, M10]\n"),
            {"M9", "M10"},
        )
        # no scenarios: line at all -> empty set (NOT a parse error)
        self.assertEqual(
            doctrine_audit._parse_bench_yaml_scenarios("quick: [M1]\n"), set()
        )
        # a scenarios: line that is NOT flow-sequence (block shape) -> None
        # (the parse-error sentinel the caller names)
        self.assertIsNone(
            doctrine_audit._parse_bench_yaml_scenarios("scenarios:\n  - M1\n")
        )


# --------------------------------------------------------------------------
# CLI: exit codes (the S4.6 0/1/2 contract)
# --------------------------------------------------------------------------
class CliTest(unittest.TestCase):
    def _bench_yaml(self, scenarios=("M1", "M2", "M9", "M10")):
        d = Path(tempfile.mkdtemp(prefix="da-cli-"))
        p = d / "bench.yaml"
        p.write_text("scenarios: [M1, M2, M9, M10]\n")
        self.addCleanup(lambda: None)
        return p

    def _with_yaml(self, parsed):
        return _YamlEdge(parsed)

    def _claims_file(self, shapes, exceptions=None):
        d = Path(tempfile.mkdtemp(prefix="da-cli-"))
        p = d / "claims.json"
        claims = {"work_allocation": shapes}
        if exceptions:
            claims["win_rule"] = {"exceptions": exceptions}
        p.write_text(json.dumps(claims))
        return p

    def _results_dir(self, rows):
        d = Path(tempfile.mkdtemp(prefix="da-cli-"))
        for i, r in enumerate(rows):
            (d / f"20260928T00000{i}Z_{r['scenario']}.json").write_text(json.dumps(r))
        return d

    def test_check_clean_exits_0(self):
        by = self._bench_yaml()
        cl = self._claims_file(
            [{"task_shape": "T", "allocation": "subagent", "bench": "M2"}]
        )
        rd = self._results_dir([_row("M2", "1", "pass")])
        with self._with_yaml({"scenarios": ["M1", "M2", "M9", "M10"]}):
            rc = doctrine_audit.main(
                [
                    "check",
                    "--bench-yaml",
                    str(by),
                    "--claims",
                    str(cl),
                    "--results-dir",
                    str(rd),
                ]
            )
        self.assertEqual(rc, 0)

    def test_check_stale_exits_1(self):
        by = self._bench_yaml()
        cl = self._claims_file(
            [{"task_shape": "T", "allocation": "subagent", "bench": "M9"}]
        )
        rd = self._results_dir([_row("M9", "2", "fail"), _row("M9", "1", "fail")])
        with self._with_yaml({"scenarios": ["M1", "M2", "M9", "M10"]}):
            rc = doctrine_audit.main(
                [
                    "check",
                    "--bench-yaml",
                    str(by),
                    "--claims",
                    str(cl),
                    "--results-dir",
                    str(rd),
                ]
            )
        self.assertEqual(rc, 1)

    def test_check_exception_keeps_0(self):
        by = self._bench_yaml()
        cl = self._claims_file(
            [{"task_shape": "T", "allocation": "subagent", "bench": "M9"}],
            exceptions=[{"scenario": "M9", "reason": "seam"}],
        )
        rd = self._results_dir([_row("M9", "2", "fail"), _row("M9", "1", "fail")])
        with self._with_yaml({"scenarios": ["M1", "M2", "M9", "M10"]}):
            rc = doctrine_audit.main(
                [
                    "check",
                    "--bench-yaml",
                    str(by),
                    "--claims",
                    str(cl),
                    "--results-dir",
                    str(rd),
                ]
            )
        self.assertEqual(rc, 0)

    def test_missing_claims_exits_2(self):
        by = self._bench_yaml()
        with self._with_yaml({"scenarios": ["M1", "M2", "M9", "M10"]}):
            rc = doctrine_audit.main(
                [
                    "check",
                    "--bench-yaml",
                    str(by),
                    "--claims",
                    "/nonexistent/claims.json",
                ]
            )
        self.assertEqual(rc, 2)

    def test_audit_writes_output(self):
        by = self._bench_yaml()
        cl = self._claims_file(
            [{"task_shape": "T", "allocation": "subagent", "bench": "M2"}]
        )
        rd = self._results_dir([_row("M2", "1", "pass")])
        out = Path(tempfile.mkdtemp(prefix="da-out-")) / "doctrine-audit.json"
        with self._with_yaml({"scenarios": ["M1", "M2", "M9", "M10"]}):
            rc = doctrine_audit.main(
                [
                    "audit",
                    "--bench-yaml",
                    str(by),
                    "--claims",
                    str(cl),
                    "--results-dir",
                    str(rd),
                    "--output",
                    str(out),
                ]
            )
        self.assertEqual(rc, 0)
        self.assertTrue(out.is_file())
        data = json.loads(out.read_text())
        self.assertTrue(data["pass"])
        self.assertIn("generated_ts", data)


if __name__ == "__main__":
    unittest.main()
