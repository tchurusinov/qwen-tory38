"""tests.test_s47_acceptance — MAP 5.8 / SPEC S4.7 (all four clauses): the
runnable S4.7 acceptance.

S4.7 is the Section-4 acceptance block ("runnable"). The pieces are
proven elsewhere in the suite:

  * #1 (audit passes) — enforced by gate.sh step 6 (bench/
    doctrine_audit.py check, MAP 5.6) and unit-tested in
    tests/test_doctrine_audit.py;
  * #2 + #4 (fresh session, S3.2-only, S5.6 workload incl. the M9/M10
    tool-loop half ledger-verified) — tests/test_fresh_session_5_7.py
    (MAP 5.7);
  * #3 (two synthetic loss scenarios -> audit flags the stale row) —
    tests/test_doctrine_audit.py CliTest.

What was missing is the acceptance ITSELF: one test module that asserts
S4.7 #1–#4 as a set, against the live repo state (the tracked
etc/bench.yaml + skills/bonsai2-harness/claims.json + committed
bench-results/ rows), composing the pieces instead of re-proving them.
That is this module — the S4.7 acceptance runner:

  1. **S4.7 #1 — the doctrine audit passes on the live repo state:**
     run_audit over the tracked claims.json + bench.yaml +
     bench-results/ rows returns pass=True, no missing citations, no
     stale rows; the three two-loss rows (M1/M9/M10) are the
     documented win_rule.exceptions (the S4.7 #1 "table updated in the
     same PR" clause — the exceptions are on record in claims.json,
     this test proves the recompute agrees with the table); every
     other cited row recomputes to `match`. Also: the real CLI
     (`bench/doctrine_audit.py check` with the DEFAULT paths — the
     gate's one-liner) exits 0 on the live state.
  2. **S4.7 #2 — fresh-session standard workload, complete:** a FRESH
     session agent (this test constructs its own — new agent, new
     session) completes the S5.6 set (M1, M2, M3, M4 + the M9/M10
     tool-loop half) against the mock pool, every result verified per
     S4.2 by the 5.7 driver's ledger-first assertions (inherited
     _run_m*), and the session's command log is a subset of the S3.2
     closed surface with the S5.6-minimum verb set (the
     acceptance-level re-assertion, not the unit test's).
  3. **S4.7 #3 — two synthetic loss scenarios:** two task shapes
     bench-confirmed as main-wins (each scenario carries 2 consecutive
     bench losses, no documented exception) -> the audit flags BOTH
     rows stale, exit 1. Exercised through the audit's real CLI
     (doctrine_audit.main) with synthetic inputs, under the no-yaml
     stdlib-fallback branch (the gate's bare-box-python3 path — the
     test_doctrine_audit S4.7-#3 convention).
  4. **S4.7 #4 — tool-loop doctrine:** the S4.2 tool-loop rows
     (M9/M10-cited) complete in a FRESH session with `--tools on`,
     every result ledger-verified per the amended S4.2 self-report
     rule (ledger entry with the real exit code, claimed files
     present, main-side re-run on main's own scratch copy), and the
     command set is still S3.2-only. The ledger re-verification here
     reads the SESSION'S OWN task records (results_dir/tasks/
     <task_id>.json, through the harness's iter_tasks/read_result —
     the S3.1 surface), not the driver's in-memory collect payloads.

Hermetic: no GPU, no network (the pool is the 5.7 mock rig —
MockManager + scripted ToolEngine + the 127.0.0.1 mock main
endpoint); the audit half is pure I/O over the tracked repo files +
temp synthetic inputs. Top-level imports are stdlib + the repo's own
modules (the test_smoke.py convention); the bin/ harness is loaded
through importlib (the 5.7 loader convention).

Run: `/usr/bin/python3.12 -m unittest tests.test_s47_acceptance`
or with the rest of the hermetic suite:
`/usr/bin/python3.12 -m unittest discover -s tests -t .`
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from bench import doctrine_audit

from . import test_fresh_session_5_7 as fs57

# ----------------------------------------------------------------------
# the 5.7 rig + driver (the S4.7 #2/#4 half runs on the 5.7 session
# agent: same S3.2 surface, same S5.6 canned scripts, same S4.2
# ledger-first verification — composed, not re-proven)
# ----------------------------------------------------------------------
PY_BIN = fs57.PY_BIN
GOLDENS = fs57.GOLDENS
SessionAgent = fs57.SessionAgent
S32_SURFACE = fs57.S32_SURFACE
S56_WORKLOAD = fs57.S56_WORKLOAD
M9_LOOP = fs57._M9_LOOP
M10_LOOP = fs57._M10_LOOP
M9_SPEC = fs57._M9_SPEC
M10_SEEDED_FAILS = fs57._M10_SEEDED_FAILS
M10_GOLDEN_STATUS = fs57._M10_GOLDEN_STATUS

REPO_ROOT = Path(__file__).resolve().parents[1]
BIN_DIR = REPO_ROOT / "bin"


def _load_harness():
    loader = importlib.machinery.SourceFileLoader(
        "bonsai2_harness", str(BIN_DIR / "bonsai2_harness.py")
    )
    spec = importlib.util.spec_from_loader("bonsai2_harness", loader)
    assert spec, "could not load bin/bonsai2_harness.py"
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


H = _load_harness()

# The live repo state S4.7 #1 runs on (the tracked inputs — the same
# paths gate.sh step 6 and the committed audit use: the audit module's
# DEFAULT_BENCH_YAML / DEFAULT_CLAIMS / DEFAULT_RESULTS_DIR, repo-rooted).
BENCH_YAML = REPO_ROOT / doctrine_audit.DEFAULT_BENCH_YAML
CLAIMS = REPO_ROOT / doctrine_audit.DEFAULT_CLAIMS
RESULTS_DIR = REPO_ROOT / doctrine_audit.DEFAULT_RESULTS_DIR

# The live audit's expected recompute (asserted, not assumed): the three
# documented-exception two-loss rows + the cited rows that must
# recompute to `match` against the committed bench-results/ rows.
EXCEPTION_SCENARIOS = {"M1", "M9", "M10"}
MATCH_SCENARIOS = {"M2", "M3", "M4", "M5"}


# ----------------------------------------------------------------------
# S4.7 #1 — the doctrine audit passes on the live repo state
# ----------------------------------------------------------------------
class S47Clause1AuditTest(unittest.TestCase):
    """The S4.7 #1 acceptance: the audit's recomputation agrees with the
    tracked S4.2 table, on the live repo state (no synthetic inputs)."""

    def _live_audit(self) -> dict:
        scen = doctrine_audit.load_bench_scenarios(BENCH_YAML)
        claims = doctrine_audit.load_claims(CLAIMS)
        rows = doctrine_audit.load_rows_by_scenario(RESULTS_DIR)
        return doctrine_audit.run_audit(claims, scen, rows)

    def test_live_audit_passes_no_missing_no_stale(self) -> None:
        res = self._live_audit()
        self.assertTrue(
            res["pass"],
            f"S4.7 #1: the live doctrine audit must pass: {res['reasons']}",
        )
        self.assertEqual(res["missing_citations"], [])
        self.assertEqual(res["stale_rows"], [])

    def test_live_recompute_agrees_with_the_table(self) -> None:
        # The S4.7 #1 "table updated in the same PR" clause, proven: the
        # win/lose recompute matches the tracked table — the exception-
        # named two-loss rows are documented-exception (not stale), and
        # every other cited row is a match.
        res = self._live_audit()
        claims = doctrine_audit.load_claims(CLAIMS)
        exceptions = {e["scenario"] for e in claims["win_rule"]["exceptions"]}
        self.assertEqual(
            exceptions,
            EXCEPTION_SCENARIOS,
            "claims.json win_rule.exceptions names the M1/M9/M10 two-loss rows",
        )
        statuses = {
            r["bench"]: r["status"] for r in res["work_allocation"] if r["bench"]
        }
        for sid in EXCEPTION_SCENARIOS:
            self.assertEqual(
                statuses.get(sid),
                "documented-exception",
                f"[{sid}]: 2 consecutive losses + a documented exception -> "
                "documented-exception (not stale): "
                f"{statuses.get(sid)}",
            )
        for sid in MATCH_SCENARIOS:
            self.assertEqual(
                statuses.get(sid),
                "match",
                f"[{sid}]: the recompute supports the allocation: {statuses.get(sid)}",
            )

    def test_live_cli_check_defaults_exit_0(self) -> None:
        # The S4.7 #1 acceptance through the real CLI at the gate's
        # paths (the DEFAULT paths = the tracked repo state; the same
        # one-liner gate.sh step 6 runs): exit 0, pass=true.
        out = subprocess.run(
            [sys.executable, "bench/doctrine_audit.py", "check"],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(
            out.returncode,
            0,
            f"S4.7 #1: the live CLI audit must pass: {out.stdout}{out.stderr}",
        )
        doc = json.loads(out.stdout)
        self.assertTrue(doc["pass"])
        self.assertEqual(doc["stale_rows"], [])
        self.assertEqual(doc["missing_citations"], [])


# ----------------------------------------------------------------------
# S4.7 #2 — the fresh-session standard workload is complete
# ----------------------------------------------------------------------
class S47Clause2FreshSessionTest(fs57.FreshSessionWorkload):
    """S4.7 #2 on a FRESH session: the 5.6 standard workload set
    completes, every result verified per S4.2 (the 5.7 driver's
    inherited ledger-first assertions), command set subset of S3.2."""

    def test_fresh_session_standard_workload_complete_s32_only(self) -> None:
        # a FRESH session agent (a new session — not the 5.7 unit
        # test's agent): same S5.6 canned scripts, same S3.2 surface.
        engine = self._instance(
            responses=[
                # text-only half (single completions, in task order):
                {"kind": "final", "content": fs57._m1_answer()},  # M1
                *[
                    {"kind": "final", "content": fs57._m2_verdict(i)} for i in range(10)
                ],  # M2 x10
                *[
                    {"kind": "final", "content": fs57._m3_unit(i)} for i in range(6)
                ],  # M3 x6
                {"kind": "final", "content": fs57._m4_draft()},  # M4
                # tool-loop half (the scripts run the real host tools):
                *M9_LOOP,  # M9
                *M10_LOOP,  # M10
            ]
        )
        mgr = self._pool(engine.port, total=8)
        self._write_cfg()

        agent = SessionAgent(env=self.env)

        # session start: the pool state (the S4.5 first act).
        code, doc = agent.status()
        self.assertEqual(code, 0, f"status must be 0 at session start: {doc}")

        # the S5.6 workload, S4.2-verified per shape (the 5.7 driver's
        # inherited ledger-first verification — a failure anywhere in a
        # _run_m* is a S4.7 #2 failure here):
        self._run_m1(agent)
        self._run_m2(agent)
        self._run_m3(agent)
        self._run_m4(agent)
        self._run_m9(agent)
        self._run_m10(agent)

        # S4.3 stateless branch: unload when the pool work is done.
        code, doc = agent.unload_all()
        self.assertEqual(code, 0, f"unload --all must be accepted: {doc}")
        health = mgr.health_doc()
        for inst in health["instances"]:
            self.assertEqual(inst["state"], "bunloaded")

        # ----------------------------------------------------------------
        # the S4.7 #2 acceptance assertions (the acceptance's own — not
        # the 5.7 unit test's)
        # ----------------------------------------------------------------
        # 1. COMMAND LOG subset of S3.2 (the S4.7 #2 tail: "command set
        #    subset of S3.2" — the session transcript's command log).
        used = {cmd for cmd, _ in agent.command_log}
        self.assertTrue(
            used.issubset(S32_SURFACE),
            f"S4.7 #2: the session issued non-S3.2 commands: {used - S32_SURFACE}",
        )
        # the S5.6-minimum verb set: the workload needs exactly these
        # verbs (no undocumented lifecycle verbs, no raw endpoint calls).
        self.assertEqual(
            used,
            {"bonsai2-spawn", "bonsai2-status", "bonsai2-collect", "bonsai2-unload"},
            f"S4.7 #2: the session's verb set is the S5.6 minimum: {used}",
        )
        # 2. the session's workload coverage: all six S5.6 shapes ran.
        self.assertEqual(S56_WORKLOAD, {"M1", "M2", "M3", "M4", "M9", "M10"})
        # 3. the skill was the session's doctrine source (the S4.7
        #    "only the skill + S3.2 scripts available" property): every
        #    S5.6 shape is bench-cited in the skill's S4.2 table.
        bench_cites = {
            r["bench"] for r in agent.claims["work_allocation"] if r["bench"]
        }
        for sid in S56_WORKLOAD:
            self.assertIn(
                sid,
                bench_cites,
                f"S4.7 #2: the S5.6 shape {sid} is bench-cited in the "
                "skill's S4.2 table",
            )


# ----------------------------------------------------------------------
# S4.7 #3 — two synthetic loss scenarios -> the audit flags the stale row
# ----------------------------------------------------------------------
class S47Clause3SyntheticLossTest(unittest.TestCase):
    """S4.7 #3: two task shapes bench-confirmed as main-wins (2
    consecutive bench losses each, no documented exception) -> the
    audit flags BOTH rows stale, exit 1. Real CLI path
    (doctrine_audit.main), synthetic inputs, under the no-yaml
    stdlib-fallback branch (the gate's bare-box-python3 path — the
    test_doctrine_audit S4.7-#3 convention)."""

    def test_two_synthetic_loss_scenarios_flag_both_stale_exit_1(self) -> None:
        d = Path(tempfile.mkdtemp(prefix="s47-synth-"))
        by = d / "bench.yaml"
        by.write_text("scenarios: [M1, M2, M4, M5, M9, M10]\n")
        claims = {
            "work_allocation": [
                # a task shape bench-confirmed as main-wins: the bench
                # says the shape lost twice in a row; the table still
                # allocates it subagent-side; no exception on record.
                {
                    "task_shape": "Synthetic loss shape A",
                    "allocation": "subagent",
                    "bench": "M4",
                },
                {
                    "task_shape": "Synthetic loss shape B",
                    "allocation": "subagent",
                    "bench": "M5",
                },
            ]
        }
        cl = d / "claims.json"
        cl.write_text(json.dumps(claims))
        rd = d / "results"
        rd.mkdir()
        for i, sid in enumerate(["M4", "M4", "M5", "M5"]):
            row = {
                "scenario": sid,
                "generated_ts": f"20260929T00000{i}Z",
                "row_verdict": "fail",
            }
            (rd / f"20260929T00000{i}Z_{sid}.json").write_text(json.dumps(row))

        saved_yaml = sys.modules.get("yaml")
        sys.modules.pop("yaml", None)  # force the stdlib-fallback branch
        try:
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
        finally:
            if saved_yaml is None:
                sys.modules.pop("yaml", None)
            else:
                sys.modules["yaml"] = saved_yaml
        self.assertEqual(
            rc,
            1,
            "S4.7 #3: two synthetic loss scenarios -> a failing audit (exit 1)",
        )

        # the flagged rows: BOTH are stale (the "two synthetic loss
        # scenarios" is plural by construction — one flagged row would
        # be a half-audit).
        res = doctrine_audit.run_audit(
            doctrine_audit.load_claims(cl),
            doctrine_audit.load_bench_scenarios(by),
            doctrine_audit.load_rows_by_scenario(rd),
        )
        self.assertFalse(res["pass"])
        flagged = {r["bench"] for r in res["stale_rows"]}
        self.assertEqual(
            flagged,
            {"M4", "M5"},
            f"S4.7 #3: the audit flags both synthetic-loss rows stale: {flagged}",
        )
        for r in res["stale_rows"]:
            self.assertEqual(r["consecutive_losses"], 2)
            self.assertEqual(r["status"], "stale")


# ----------------------------------------------------------------------
# S4.7 #4 — the tool-loop doctrine (M9/M10, --tools on, ledger-verified)
# ----------------------------------------------------------------------
class S47Clause4ToolLoopTest(fs57.FreshSessionWorkload):
    """S4.7 #4 on a FRESH session: the S4.2 tool-loop rows (M9/M10)
    complete the tool-loop half of the standard workload set with
    `--tools on`, every result ledger-verified per the amended S4.2
    self-report rule; the command set is still S3.2-only."""

    def test_tool_loop_rows_fresh_session_ledger_verified_s32_only(self) -> None:
        # a FRESH session agent; the mock pool: one scripted instance
        # serves the two tool-loop tasks sequentially (MVP load-time
        # condition, SPEC S0 — exactly 1 instance). The M9/M10 tool
        # loops are EXECUTED BY THE REAL HOST TOOL MODULE
        # (tool_execution: host — the S3.4 default), so the suites
        # really run in the task workdirs.
        engine = self._instance(responses=[*M9_LOOP, *M10_LOOP])
        mgr = self._pool(engine.port, total=8)
        self._write_cfg()

        agent = SessionAgent(env=self.env)
        code, doc = agent.status()
        self.assertEqual(code, 0, f"status must be 0 at session start: {doc}")

        # the tool-loop half of the S5.6 set — the S4.2 tool-loop rows
        # (M9/M10-cited): the 5.7 driver's ledger-first verification of
        # every result (inherited _run_m9/_run_m10: the M9 ledger
        # entry + workdir files + main-side suite re-run; the M10
        # report schema + status-mirrors-exit-code + the 2 seeded-fail
        # main-side re-runs).
        self._run_m9(agent)
        self._run_m10(agent)

        code, doc = agent.unload_all()
        self.assertEqual(code, 0, f"unload --all must be accepted: {doc}")
        health = mgr.health_doc()
        for inst in health["instances"]:
            self.assertEqual(inst["state"], "bunloaded")

        # ----------------------------------------------------------------
        # the S4.7 #4 acceptance assertions
        # ----------------------------------------------------------------
        # 1. the command set is still S3.2-only (the S4.7 #4 tail).
        used = {cmd for cmd, _ in agent.command_log}
        self.assertTrue(
            used.issubset(S32_SURFACE),
            f"S4.7 #4: the tool-loop session issued non-S3.2 commands: "
            f"{used - S32_SURFACE}",
        )
        self.assertEqual(
            used,
            {"bonsai2-spawn", "bonsai2-status", "bonsai2-collect", "bonsai2-unload"},
            f"S4.7 #4: the tool-loop session's verb set is S3.2-only: {used}",
        )
        # 2. every spawn is a tool-loop spawn: `--tools on` (the S4.7 #4
        #    "with --tools on"), no invented flag.
        allowed_flags = {"-i", "--tools", "--deadline", "--workdir"}
        for cmd, argv in agent.command_log:
            if cmd == "bonsai2-spawn":
                self.assertIn(
                    "--tools",
                    argv,
                    f"S4.7 #4: every tool-loop spawn carries --tools: {argv}",
                )
                self.assertEqual(
                    argv[argv.index("--tools") + 1],
                    "on",
                    f"S4.7 #4: the tool-loop spawns run with --tools on: {argv}",
                )
                flags = {a for a in argv[1:] if a.startswith("-")}
                self.assertTrue(
                    flags.issubset(allowed_flags),
                    f"S4.7 #4: the spawn argv carries only S3.2 flags: "
                    f"{flags - allowed_flags} ({argv})",
                )
        # 3. ledger-first verification of every result (the amended S4.2
        #    self-report rule) — re-asserted on the SESSION'S OWN task
        #    records (results_dir/tasks/<task_id>.json, through the
        #    harness's iter_tasks/read_result — the S3.1 surface), not
        #    the driver's in-memory collect payloads: the M9 suite ran
        #    with ledger exit 0 + the main-side re-run passed; the M10
        #    report mirrors the exit codes + the seeded-fail verdicts
        #    agree with main's re-runs.
        recs = H.iter_tasks(H.results_dirs(H.load_config()))
        self.assertEqual(len(recs), 2, "S4.7 #4: the session ran exactly 2 tasks")
        m9_rec = next(
            r
            for r in recs
            if "textkit" in json.dumps(H.read_result(r).get("tool_log", []))
        )
        self.assertEqual(m9_rec["state"], "done")
        m9_res = H.read_result(m9_rec)
        m9_run = [
            e
            for e in m9_res["tool_log"]
            if e["tool"] in ("bash", "python")
            and "unittest test_textkit" in json.dumps(e.get("params", {}))
        ]
        self.assertEqual(len(m9_run), 1, "S4.7 #4: the M9 ledger shows the suite run")
        self.assertEqual(
            m9_run[0]["exit_code"], 0, "S4.7 #4: the M9 suite run's ledger exit is 0"
        )
        m9_wd = Path(m9_rec["workdir"])
        self.assertTrue(
            (m9_wd / "textkit.py").is_file(),
            "S4.7 #4: the M9 workdir carries textkit.py",
        )
        self.assertTrue(
            (m9_wd / "test_textkit.py").is_file(),
            "S4.7 #4: the M9 workdir carries test_textkit.py",
        )
        for fn in M9_SPEC["spec_functions"]:
            self.assertIn(
                f"def {fn}(",
                (m9_wd / "textkit.py").read_text(),
                f"S4.7 #4: the M9 workdir module defines {fn}",
            )
        scratch = self.tmp / "m9-accept-scratch"
        scratch.mkdir()
        shutil.copy(m9_wd / "textkit.py", scratch / "textkit.py")
        shutil.copy(m9_wd / "test_textkit.py", scratch / "test_textkit.py")
        r = subprocess.run(
            [PY_BIN, "-m", "unittest", "test_textkit"],
            cwd=str(scratch),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(
            r.returncode,
            0,
            f"S4.7 #4: the M9 main-side acceptance re-run must pass: "
            f"{r.stdout}{r.stderr}",
        )

        m10_rec = next(
            r
            for r in recs
            if "report" in json.dumps(H.read_result(r).get("tool_log", []))
        )
        m10_res = H.read_result(m10_rec)
        report = json.loads(m10_res["answer"])
        files = {f["file"].replace(".py", ""): f for f in report["files"]}
        self.assertEqual(len(files), 10, "S4.7 #4: the M10 report covers 10/10 files")
        for name, f in files.items():
            self.assertEqual(
                f["status"],
                "pass" if f["exit_code"] == 0 else "fail",
                f"S4.7 #4: M10 {name} status mirrors the exit code",
            )
            self.assertEqual(
                f["status"],
                M10_GOLDEN_STATUS[name],
                f"S4.7 #4: M10 {name} verdict agrees with the seed truth",
            )
        # the ledger shows the batch run with per-file exit codes.
        batch_entries = [
            e
            for e in m10_res["tool_log"]
            if e["tool"] == "bash" and "unittest" in json.dumps(e.get("params", {}))
        ]
        self.assertEqual(
            len(batch_entries),
            1,
            f"S4.7 #4: the M10 ledger shows the batch run: {m10_res['tool_log']}",
        )
        # main re-runs the 2 seeded-fail files in its own scratch copy;
        # the verdicts agree (the M10 rubric's re-run half).
        scratch10 = self.tmp / "m10-accept-scratch"
        scratch10.mkdir()
        for p in (GOLDENS / "m10").glob("files_0*.py"):
            shutil.copy(p, scratch10 / p.name)
        for name in M10_SEEDED_FAILS:
            r10 = subprocess.run(
                [PY_BIN, "-m", "unittest", name],
                cwd=str(scratch10),
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotEqual(
                r10.returncode,
                0,
                f"S4.7 #4: M10 {name} main re-run: the seeded fail really fails: "
                f"{r10.stdout}{r10.stderr}",
            )
            self.assertEqual(
                files[name]["status"],
                "fail",
                f"S4.7 #4: M10 {name}: main's observed verdict agrees with the report",
            )


if __name__ == "__main__":
    unittest.main()
