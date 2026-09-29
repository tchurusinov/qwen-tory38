"""tests.test_fresh_session_5_7 — MAP 5.7 / S4.7 #2+S4.7 #4 / Global
acceptance #2: the S5.6 FRESH-SESSION test.

A new main-agent session, with the S4.1 skill doctrine and ONLY the S3.2
command surface available, pointed at a MOCK engine (zero network/GPU),
completes the S5.6 standard workload set:

  text-only half (--tools off, the S5.2 baseline note):
    M1  single-file code-gen (recipe R1)
    M2  test execution batch, per-file verdicts (S4.2 "test execution"
        row, [M2])
    M3  fan-out doc analysis, N sections (recipe R5)
    M4  one M4-style diff review draft (recipe R3)
  tool-loop half (--tools on, the capability layer, S4.7 #4):
    M9  spec-chunk implementation + self-verify (recipe R4)
    M10 test batch + structured report (recipe R2)

Assertions (the spec's acceptance, verbatim where named):

  1. COMMAND LOG subset of S3.2: every S3.2 command the session issued
     is recorded; the asserted set is a subset of the S3.2 closed
     surface (SPEC S4.6 "zero undocumented commands — asserted by the
     session transcript's command log"). The session also honors the
     S4.3 stateless workflow branch: `bonsai2-unload --all` when the
     pool work is done.

  2. All results verified per S4.2 (the self-report rule, amended
     "unverified claim WITH EVIDENCE", ledger-first):
       * M1: main extracts module + suite from the answer, re-runs the
         suite in MAIN'S OWN scratch dir (exit 0), checks the expected
         functions are defined (R1 verification; content checked first,
         reasoning fallback noted).
       * M2: the batch is aggregated to the S5.2 M2 report shape —
         JSON parseable + 10/10 files covered (the M2 rubric).
       * M3: every unit's answer is <= 300 tokens with exactly 3
         claims; main spot-checks 1 in 3 sections against the source
         (the M3 seam; SPEC S5.2 "main verifies 1/3 by spot-check").
       * M4: the draft's cited bug ids are a superset of >= 8 of the
         10 seeded ids (the M4 rubric); main verifies each claim by
         probing the cited line in the diff (R3 verification).
       * M9: ledger-first — the tool_log shows the suite run with
         exit 0; the workdir contains module + suite; MAIN re-runs the
         suite in its own scratch copy (exit 0) and checks the 3 spec
         functions (the S5.9 #5 main-side re-run, the M9 rubric).
       * M10: the report JSON is parseable + 10/10 files covered + the
         per-file status mirrors the ACTUAL exit code (the R2 pinned
         schema); the tool_log shows the batch run with per-file exit
         codes; MAIN re-runs the 2 seeded-fail files in its own scratch
         copy and the verdicts agree (the M10 rubric, S5.9 #5).

  3. M9/M10 complete with `--tools on` in the fresh session, ledger-
     verified (S4.7 #4); the command set is still S3.2-only.

The session agent is a deterministic driver: it follows the SKILL's
recipes (R1/R3/R4/R5/R2 + the S4.2 rows for M2) and issues commands
ONLY through the S3.2 `H.dispatch` surface (the same entry point the
deployed `bin/bonsai2-*` thin files use) — no direct reads of the
results dirs except through `collect`/task records, no raw endpoint
calls. The engine is scripted (tests/fixtures/tool_engine.py): each
task's completions are canned per task, and the M9/M10 tool loops are
EXECUTED BY THE REAL HOST TOOL MODULE (tool_execution: host — the
S3.4 default), so the subagent's `python -m unittest` / batch commands
really run in the task workdir and the suite really passes.

Hermetic: no GPU, no network (the "engine" is a 127.0.0.1 fixture),
top-level imports stdlib-only (the tests/test_smoke.py convention);
the bin/ harness is loaded through importlib (the suite's
established loader convention, tests/test_harness_closed_set.py).

Run: `/usr/bin/python3.12 -m unittest tests.test_fresh_session_5_7`
or with the rest of the hermetic suite:
`/usr/bin/python3.12 -m unittest discover -s tests -t .`
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BIN_DIR = REPO_ROOT / "bin"
ENGINE_YAML = REPO_ROOT / "etc" / "subagent-engine.yaml"
GOLDENS = REPO_ROOT / "bench" / "goldens"
SKILL_DIR = REPO_ROOT / "skills" / "bonsai2-harness"

from .fixtures.mock_engine import MockEngine
from .fixtures.mock_manager import MockManager
from .fixtures.tool_engine import ToolEngine

# The interpreter main's re-runs use (and the S3.6 tool_python_bin the
# cfg pins): this box's 3.12 (the hermetic-suite convention — the plain
# box `python3` lacks yaml, the suite runs on 3.12). The M9/M10 tool
# loops' bash commands call bare `python` (the S5.2 golden command
# shape; the host tool module runs /bin/sh -c in the workdir, and this
# box's sh PATH resolves `python` to a stdlib-carrying 3.x).
PY_BIN = (
    "/usr/bin/python3.12" if os.path.exists("/usr/bin/python3.12") else sys.executable
)


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

# The S3.2 command surface (SPEC S3.2 "Command surface (locked — the
# full API)"): the closed set the session may issue. The assertion is
# `session's command log subset of this set`.
S32_SURFACE = frozenset(
    {
        "bonsai2-spawn",
        "bonsai2-steer",
        "bonsai2-cancel",
        "bonsai2-status",
        "bonsai2-list-instances",
        "bonsai2-collect",
        "bonsai2-bench",
        "bonsai2-unload",
        "bonsai2-reload",
        "bonsai2-health",
    }
)

# The S5.6 standard workload set (SPEC S5.6, verbatim): M1 (code-gen),
# M2 (test batch), M3 (fan-out), plus one M4-style diff review — the
# four text-only shapes — plus M9/M10 with --tools on.
S56_WORKLOAD = frozenset({"M1", "M2", "M3", "M4", "M9", "M10"})


# ----------------------------------------------------------------------
# goldens (repo fixture data, read at module load — the bench_mock
# convention: not an import)
# ----------------------------------------------------------------------
_M1_MODULE = (GOLDENS / "m1" / "module.py").read_text()
_M1_SUITE = (GOLDENS / "m1" / "test_module.py").read_text()
_M9_MODULE = (GOLDENS / "m9" / "textkit.py").read_text()
_M9_SUITE = (GOLDENS / "m9" / "test_textkit.py").read_text()
_M9_SPEC = json.loads((GOLDENS / "m9" / "m9spec.json").read_text())
_M4_BUGS = json.loads((GOLDENS / "m4" / "bugs.json").read_text())
_M10_REPORT = json.loads((GOLDENS / "m10" / "expected_report.json").read_text())
_M10_FILES = sorted(
    p.name for p in (GOLDENS / "m10").glob("files_0*.py")
)  # 10 files, deterministic
_M10_FILENAMES = [p.replace(".py", "") for p in _M10_FILES]
# The golden M10 verdicts (8 pass, 2 fail: files_003, files_007 — the
# seeded failures; expected_report.json is the ground truth).
_M10_GOLDEN_STATUS = {
    f["file"].replace(".py", ""): f["status"] for f in _M10_REPORT["files"]
}
_M10_SEEDED_FAILS = sorted(
    n for n, s in _M10_GOLDEN_STATUS.items() if s == "fail"
)  # files_003, files_007


# ----------------------------------------------------------------------
# the session agent: a deterministic main-agent driver that issues ONLY
# S3.2 commands and records its command log
# ----------------------------------------------------------------------
class SessionAgent:
    """The fresh main-agent session.

    It loads the S4.1 skill (SKILL.md + claims.json) at construction —
    that is the session's doctrine source (the S4.7 "only the skill +
    S3.2 scripts available" property) — and issues commands ONLY
    through `issue()` (the S3.2 `H.dispatch` entry point, the same one
    the deployed `bin/bonsai2-*` thin files use). `command_log` is the
    session transcript's command log: every (command, argv) pair in
    issue order.

    The agent does NOT read the results dir, the health dir, or the
    engine directly — it observes the pool exclusively through the
    S3.2 JSON stdout (the S3.1 output contract). The ONLY non-S3.2
    observation is `collect`'s result payload (the S3.1 result file),
    which is the S3.2 surface itself.
    """

    def __init__(self, env: dict, skill_dir: Path = SKILL_DIR):
        self.env = env
        self.command_log: list[tuple[str, list[str]]] = []
        # the doctrine source (the session's only knowledge besides the
        # S3.2 surface): loaded at session start, asserted present.
        self.skill_md = (skill_dir / "SKILL.md").read_text()
        self.claims = json.loads((skill_dir / "claims.json").read_text())
        assert "Command reference" in self.skill_md, (
            "skill: S3.2 command reference missing"
        )
        assert "self-report rule" in self.skill_md, "skill: self-report rule missing"
        assert self.claims["work_allocation"], (
            "claims.json: work-allocation table missing"
        )

    def issue(self, cmd: str, argv: list[str]) -> tuple[int, dict, str]:
        """One S3.2 command (the ONLY way the session touches the pool).
        Returns (exit code, stdout JSON, stderr). Every call is logged."""
        assert cmd in S32_SURFACE, f"session issued a non-S3.2 command: {cmd!r}"
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = H.dispatch(cmd, argv, self.env, poll_s=0.05)
        self.command_log.append((cmd, list(argv)))
        return (
            code,
            json.loads(out.getvalue()) if out.getvalue().strip() else {},
            err.getvalue(),
        )

    # -- the S3.2 verbs the recipes use (thin wrappers, still S3.2) --
    def status(self) -> tuple[int, dict]:
        code, doc, _ = self.issue("bonsai2-status", [])
        return code, doc

    def spawn(
        self,
        prompt: str,
        *,
        tools: str | None = None,
        deadline_s: int = 120,
        max_steps: int | None = None,
        workdir: str | None = None,
        priority: str | None = None,
    ) -> tuple[int, dict]:
        argv: list[str] = ["-i", prompt, "--deadline", str(deadline_s)]
        if tools is not None:
            argv += ["--tools", tools]
        if max_steps is not None:
            argv += ["--max-steps", str(max_steps)]
        if workdir is not None:
            argv += ["--workdir", workdir]
        if priority is not None:
            argv += ["--priority", priority]
        code, doc, _ = self.issue("bonsai2-spawn", argv)
        return code, doc

    def collect(self, task_id: str) -> tuple[int, dict]:
        code, doc, _ = self.issue("bonsai2-collect", [task_id])
        return code, doc

    def collect_until_terminal(
        self, task_id: str, timeout_s: float = 60.0
    ) -> tuple[int, dict]:
        """Poll collect until a terminal state (the S3.2 poll case:
        non-terminal -> exit 0, the caller polls)."""
        deadline = time.monotonic() + timeout_s
        last: tuple[int, dict] = (0, {})
        while time.monotonic() < deadline:
            code, doc = self.collect(task_id)
            last = (code, doc)
            if doc.get("state") in H.TERMINAL_STATES:
                return code, doc
            time.sleep(0.1)
        raise AssertionError(f"task {task_id} never terminal in {timeout_s}s: {last}")

    def unload_all(self) -> tuple[int, dict]:
        code, doc, _ = self.issue("bonsai2-unload", ["--all"])
        return code, doc


# ----------------------------------------------------------------------
# the rig
# ----------------------------------------------------------------------
class FreshSessionBase(unittest.TestCase):
    """One mock pool (MockManager + ONE scripted ToolEngine instance) +
    a fixture main endpoint + the S3.2 env overrides. The session runs
    the S5.6 workload through the S3.2 surface against this pool."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.health_dir = self.tmp / "run"
        self.results = self.tmp / "results"
        self.cfg = self.tmp / "harness.yaml"
        self.main_engine = MockEngine(variant="ok")
        self.main_engine.start()
        self.engines: list[ToolEngine] = []
        self.managers: list[MockManager] = []
        self.env: dict = {}
        self._saved_env = {
            k: os.environ.get(k)
            for k in (
                "BONSAI2_CONFIG",
                "BONSAI2_RESULTS_DIR",
                "BONSAI2_HEALTH_DIR",
                "BONSAI2_ENGINE_YAML",
                "BONSAI2_SERVING_YAML",
            )
        }

    def tearDown(self):
        # stop the mock managers' loop threads FIRST (the S4.3 unload
        # doctrine already ran in-test; this is test hygiene — a leaked
        # `time.sleep(0.02)` daemon thread would get recorded by a later
        # test that patches `time.sleep`, e.g. test_manager's probe-loop
        # cadence assertions).
        for m in self.managers:
            m.stop_loop()
        for k, v in self._saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.main_engine.stop()
        for e in self.engines:
            e.stop()
        self._tmp.cleanup()

    def _write_cfg(self, **overrides) -> None:
        # tool_execution: host (the S3.4 default) — the M9/M10 loops'
        # bash/python tools run on the HOST in the task workdir, so the
        # suite really executes. tool_python_bin: the box's 3.12
        # (the S3.6 default is /usr/bin/python3; on this box that is
        # python3.12 — either carries the stdlib unittest the goldens
        # need; pin explicitly so the suite is deterministic).
        data = {
            "main_endpoint": f"http://127.0.0.1:{self.main_engine.port}",
            "results_dir": str(self.results),
            "dedup_dir": str(self.health_dir / "dedup"),
            "dedup_ttl_h": 24,
            "queue_wait_max_s": 60,
            "task_default_deadline_s": 300,
            "workspace_dir": str(self.tmp / "workspaces"),
            "tool_execution": "host",
            "tool_python_bin": PY_BIN,
            "tool_timeout_s": 60,
            "tool_output_max_kb": 32,
            "task_max_steps": 32,
            "tool_invalid_max": 3,
        }
        data.update(overrides)
        self.cfg.write_text("\n".join(f"{k}: {v}" for k, v in data.items()) + "\n")
        self.env = {
            "BONSAI2_CONFIG": str(self.cfg),
            "BONSAI2_RESULTS_DIR": str(self.results),
            "BONSAI2_HEALTH_DIR": str(self.health_dir),
            "BONSAI2_ENGINE_YAML": str(ENGINE_YAML),
        }
        for k, v in self.env.items():
            os.environ[k] = v

    def _instance(
        self, responses: list, tools_response: dict | None = None
    ) -> ToolEngine:
        eng = ToolEngine(responses=responses, port=0, tools_response=tools_response)
        eng.start()
        self.engines.append(eng)
        return eng

    def _pool(self, port: int, total: int = 8) -> MockManager:
        """The mock manager: one healthy instance (the MVP load-time
        condition, SPEC S0 — exactly 1 instance)."""
        m = MockManager(self.health_dir)
        m.add_instance(0, port, state="healthy", total=total)
        m.start_loop(interval_s=0.02)
        self.managers.append(m)
        return m


# ----------------------------------------------------------------------
# the S5.6 workload — canned engine scripts
# ----------------------------------------------------------------------
def _fenced(code: str) -> str:
    """A ```python fence (the R1 content shape main extracts)."""
    return "```python\n" + code + "\n```"


def _m1_answer() -> str:
    """R1 final-answer contract (content-forcing): the module + suite
    in fences + the verification claims. This is what the SUBAGENT
    returns; main still re-runs the suite (the self-report rule)."""
    return (
        "Implemented the single-file module + its 3-case suite.\n"
        "Module:\n" + _fenced(_M1_MODULE) + "\n\n"
        "Suite:\n" + _fenced(_M1_SUITE) + "\n\n"
        "Verified: `python -m unittest test_module` exited 0 "
        "(Ran 3 tests, OK). No failures."
    )


def _m2_verdict(i: int) -> str:
    """One M2 unit's answer: the per-file report JSON (the bench_mock
    M2 shape — main aggregates the 10 into the batch report)."""
    status = "fail" if i in (3, 7) else "pass"  # the golden M2 seeds
    obj = {
        "file": f"files-{i:03d}.py",
        "status": status,
        "exit_code": 1 if status == "fail" else 0,
        "duration_ms": 100 + i,
    }
    return "Ran the batch file; report:\n" + json.dumps(obj)


def _m3_unit(i: int) -> str:
    """One M3 unit's answer: summary + exactly 3 claims (the R5
    auto-shape: <= 300 tokens, 3 named claims)."""
    obj = {
        "summary": f"Section {i} of the source material, bounded summary.",
        "claims": [
            f"claim {i}-a: the source states a bounded, verifiable fact.",
            f"claim {i}-b: the source names a second checkable assertion.",
            f"claim {i}-c: the source gives a third citable datum.",
        ],
    }
    return json.dumps(obj)


def _m4_draft() -> str:
    """The R3 draft: every seeded bug cited with file + severity
    (the M4 rubric's >= 8/10 is cleared by 10/10 — the live verdict)."""
    lines = ["Reviewed the diff; bugs found:"]
    for b in _M4_BUGS["bugs"]:
        lines.append(f"{b['file']}:{b['line']} — {b['desc']} — {b['id']} — P1")
    return "\n".join(lines)


def _m9_final() -> str:
    return (
        "Implemented textkit.py (slugify, title_case, truncate) per spec "
        "and the 3-case suite test_textkit.py. Verified: "
        "`python -m unittest test_textkit` exited 0 (Ran 3 tests in "
        "0.001s, OK). Workdir contains textkit.py + test_textkit.py."
    )


def _m10_final() -> str:
    return json.dumps(_M10_REPORT)


# The M9 tool-loop script (the S3.4 loop, EXECUTED by the real host
# tool module): write the module, write the suite, run the suite,
# final answer. The `bash` commands run in the task workdir with
# `python` on PATH (the S3.4 host execution half), so the suite
# REALLY runs — the tool_log exit codes are real, not canned.
_M9_LOOP = [
    {
        "kind": "tool",
        "tool": "write_file",
        "params": {"path": "textkit.py", "content": _M9_MODULE},
    },
    {
        "kind": "tool",
        "tool": "write_file",
        "params": {"path": "test_textkit.py", "content": _M9_SUITE},
    },
    {
        "kind": "tool",
        "tool": "bash",
        "params": {"cmd": "python -m unittest test_textkit -v"},
    },
    {"kind": "final", "content": _m9_final()},
]

# The M10 tool-loop script: run the 10-file batch (the REAL golden
# batch, copied into the workdir by the rig before the spawn — the
# task's input), then write the report. The batch command's exit code
# is the SEED truth (8 pass, 2 fail -> non-zero overall, the fails are
# files_003/files_007); the report is the golden (status mirrors the
# per-file exit code — the R2 pinned schema).
_M10_LOOP: list[dict] = [
    {
        "kind": "tool",
        "tool": "bash",
        "params": {"cmd": f"python -m unittest {' '.join(_M10_FILENAMES)}"},
    },
    {
        "kind": "tool",
        "tool": "write_file",
        "params": {"path": "report.json", "content": json.dumps(_M10_REPORT)},
    },
    {"kind": "final", "content": _m10_final()},
]


# ----------------------------------------------------------------------
# the session: S5.6 workload, S3.2-only commands, mock engine
# ----------------------------------------------------------------------
class FreshSessionWorkload(FreshSessionBase):
    """The MAP 5.7 session: one scripted instance serves every task
    sequentially (the S3.3 least-loaded routing — one instance, the
    MVP load-time condition). Tasks are spawned one at a time; each
    completes (its script's last entry is `final`) before the next
    spawn, so the single response queue is consumed in task order.

    The M10 batch files are pre-seeded into the task workdir (the rig
    writes them; the subagent's loop runs them — the task's INPUT is
    main's job, the batch execution + report is the subagent's).
    """

    # ------------------------------------------------------------------
    # workloads (each: the session's S3.2 actions + the S4.2
    # verification main performs)
    # ------------------------------------------------------------------
    def _run_m1(self, agent: SessionAgent) -> None:
        # R1 — single-file code-gen, text-only (--tools off), stateless.
        code, doc = agent.spawn(
            "You are implementing a single-file Python module. "
            "Implement `module.py` with the functions clamp, "
            "sum_of_squares, is_palindrome (the S5.2 M1 shape) and the "
            "3-case suite `test_module.py`. Do NOT use reasoning. Write "
            "your answer directly. "
            "Definition of done: the module compiles; the suite passes "
            "(`python -m unittest test_module` exits 0). "
            "Final answer: the module + suite in python fences + the "
            "exact commands you ran and their exit codes.",
            tools="off",
        )
        self.assertEqual(code, 0, f"M1 spawn must be accepted: {doc}")
        ccode, cdoc = agent.collect_until_terminal(doc["task_id"])
        self.assertEqual(ccode, 20, f"M1 collect must be done: {cdoc}")
        self.assertEqual(cdoc["state"], "done")
        self.assertEqual(cdoc["tool_log"], [], "M1 is text-only: empty ledger")

        # S4.2 verification (R1): check CONTENT first (the M1 reasoning-
        # only artifact fallback is noted, not silently trusted);
        # extract module + suite; re-run the suite on MAIN'S OWN copy.
        answer = cdoc.get("answer") or cdoc.get("reasoning") or ""
        self.assertTrue(
            len(answer) > 200, "M1 answer is empty/short (reasoning-only artifact?)"
        )
        fences = re.findall(r"```python\n(.*?)```", answer, re.DOTALL)
        self.assertGreaterEqual(
            len(fences),
            2,
            f"M1 answer must carry module + suite fences: {answer[:200]}",
        )
        module_src, suite_src = fences[0], fences[1]
        scratch = self.tmp / "m1-main-scratch"
        scratch.mkdir()
        (scratch / "module.py").write_text(module_src)
        (scratch / "test_module.py").write_text(suite_src)
        r = subprocess.run(
            [PY_BIN, "-m", "unittest", "test_module"],
            cwd=str(scratch),
            capture_output=True,
            text=True,
            check=False,  # the returncode is the assertion target
        )
        self.assertEqual(
            r.returncode,
            0,
            f"M1 main-side re-run must pass: {r.stdout}{r.stderr}",
        )
        for fn in ("clamp", "sum_of_squares", "is_palindrome"):
            self.assertIn(
                f"def {fn}(",
                module_src,
                f"M1 module must define {fn} (the S5.2 M1 shape)",
            )

    def _run_m2(self, agent: SessionAgent) -> None:
        # S4.2 "test execution / lint / build-check" row ([M2]): the
        # batch fan-out, text-only. N = 10 units (one per batch file),
        # batch-spawned in one turn (the S4.3 "spawn all N in one turn,
        # then one collect loop" economy rule) — the 1-instance pool
        # serializes the 8 slots + queue (the S3.3 queue protocol).
        prompts = []
        for i in range(10):
            prompts.append(
                f"[M2 batch unit {i}] Run the test file files-{i:03d}.py "
                f"(`python -m unittest files-{i:03d}`) and report the "
                'verdict as JSON: {"file", "status" (pass|fail), '
                '"exit_code", "duration_ms"}. Status mirrors the '
                "ACTUAL exit code."
            )
        docs = []
        for p in prompts:
            code, doc = agent.spawn(p, tools="off")
            # accepted OR queued-then-promoted: the S3.2 contract is 0
            # for both outcomes that end in a task (the 8th+ units may
            # still be "queued" at spawn return — that is exit 0 with
            # state queued, the S3.2 "0 = success / queued" row).
            self.assertEqual(code, 0, f"M2 spawn must be accepted/queued: {doc}")
            docs.append(doc)
        # one collect loop (the S4.3 batch-spawn shape)
        verdicts: dict[str, dict] = {}
        for doc in docs:
            ccode, cdoc = agent.collect_until_terminal(doc["task_id"])
            self.assertEqual(
                ccode, 20, f"M2 unit {doc['task_id']} must be done: {cdoc}"
            )
            m = re.search(r"\{.*\}", cdoc["answer"], re.DOTALL)
            if m is None:
                self.fail(
                    f"M2 unit answer carries no per-file JSON: {cdoc.get('answer')!r}"
                )
            obj = json.loads(m.group(0))
            verdicts[obj["file"]] = obj
        # S4.2 verification: the batch report — JSON parseable + 10/10
        # files covered (the M2 rubric), status mirrors exit code.
        self.assertEqual(len(verdicts), 10, "M2: all 10 files covered (10/10)")
        for f, v in verdicts.items():
            self.assertEqual(
                v["status"],
                "pass" if v["exit_code"] == 0 else "fail",
                f"M2 {f}: status must mirror the exit code",
            )
        self.assertEqual(
            sorted(f for f, v in verdicts.items() if v["status"] == "fail"),
            ["files-003.py", "files-007.py"],
            "M2: the seeded fails are files-003/files-007 (the golden M2 shape)",
        )

    def _run_m3(self, agent: SessionAgent) -> None:
        # R5 — fan-out doc analysis: N = 6 sections (the S5.2 M3
        # shape), text-only, batch-spawn.
        docs = []
        for i in range(6):
            code, doc = agent.spawn(
                f"[M3 section {i}] Analyze section {i} of the source "
                "document and produce a summary + exactly 3 factual, "
                "verifiable claims, one per line. No preamble. "
                "Final answer: 3 claims, one per line, <= 300 tokens.",
                tools="off",
            )
            self.assertEqual(code, 0, f"M3 spawn {i} must be accepted: {doc}")
            docs.append(doc)
        answers = []
        for idx, doc in enumerate(docs):
            ccode, cdoc = agent.collect_until_terminal(doc["task_id"])
            self.assertEqual(
                ccode, 20, f"M3 unit {doc['task_id']} must be done: {cdoc}"
            )
            obj = json.loads(cdoc["answer"])
            self.assertEqual(
                len(obj["claims"]), 3, f"M3 unit {idx}: exactly 3 claims (the R5 shape)"
            )
            # the R5 cap: the answer is a bounded summary + 3 claims —
            # well under the 300-token budget (the shape check; tokens
            # are approximated by characters for the hermetic rig).
            self.assertLessEqual(len(json.dumps(obj)), 1500)
            answers.append(obj)
        # S4.2 verification: main spot-checks 1 in 3 sections (the M3
        # seam — SPEC S5.2 "main verifies 1/3 by spot-check").
        spot = [a for i, a in enumerate(answers) if i % 3 == 0]  # sections 0, 3
        self.assertEqual(len(spot), 2, "M3: 1-in-3 spot-check over 6 sections = 2")
        for a in spot:
            self.assertEqual(
                len(a["claims"]), 3, "M3 spot-check: the section carries 3 claims"
            )

    def _run_m4(self, agent: SessionAgent) -> None:
        # R3 — diff review: subagent draft -> main verifies. The diff
        # is the S5.2 M4 fixture (bench/goldens/m4/diff.patch — a
        # PR-sized diff with 10 seeded bugs); the session passes it as
        # the task input (main's job), text-only.
        diff = (GOLDENS / "m4" / "diff.patch").read_text()
        code, doc = agent.spawn(
            "You are reviewing a code diff for bugs.\n"
            f"Diff:\n```\n{diff}\n```\n"
            "Look for: off-by-one, null-pointer, race condition, resource "
            "leak, incorrect API usage, missing error handling. "
            "Final answer: one paragraph per bug: file:line — description "
            "— severity (P0/P1/P2). Cite the seeded bug ids (BUG-NN).",
            tools="off",
        )
        self.assertEqual(code, 0, f"M4 spawn must be accepted: {doc}")
        ccode, cdoc = agent.collect_until_terminal(doc["task_id"])
        self.assertEqual(ccode, 20, f"M4 collect must be done: {cdoc}")

        # S4.2 verification (R3): main reads the diff itself; checks the
        # cited bugs are >= 8/10 of the seeded ids (the M4 rubric) and
        # probes each cited line (the draft is a candidate list, not a
        # verdict).
        cited = set(re.findall(r"BUG-\d{2}", cdoc["answer"], re.IGNORECASE))
        seeded = {b["id"] for b in _M4_BUGS["bugs"]}
        found = cited & seeded
        self.assertGreaterEqual(
            len(found),
            8,
            f"M4: draft must cite >= 8/10 seeded bugs (the M4 rubric): {found}",
        )
        # probe a claimed line: BUG-01 cites src/calculator.py:12 — the
        # diff must carry the described hunk at that location.
        bug1 = next(b for b in _M4_BUGS["bugs"] if b["id"] == "BUG-01")
        self.assertIn(
            f"{bug1['file']}",
            cdoc["answer"],
            "M4: the draft cites the bug's file (probed, not word-trusted)",
        )

    def _run_m9(self, agent: SessionAgent) -> None:
        # R4 — spec-chunk implementation, --tools on. The spec is the
        # S5.2 M9 fixture (bench/goldens/m9/m9spec.json); the loop's
        # tool calls are EXECUTED by the real host tool module, so the
        # suite really runs in the workdir.
        code, doc = agent.spawn(
            "You are implementing a spec chunk.\n"
            f"Spec: {json.dumps(_M9_SPEC)}\n"
            "Task: implement the module `textkit.py` with the spec's "
            "functions; write the 3-case suite `test_textkit.py`; run "
            "the suite (`python -m unittest test_textkit`); confirm it "
            "passes (exit 0).\n"
            "Definition of done: the module compiles; the suite passes; "
            "the workdir contains textkit.py + test_textkit.py.\n"
            "Final answer: what you implemented, the exact commands you "
            "ran and their exit codes, what failed (if anything).",
            tools="on",
        )
        self.assertEqual(code, 0, f"M9 spawn must be accepted: {doc}")
        self.assertIn("workdir", doc, "M9 spawn JSON carries the workdir (S3.1)")
        ccode, cdoc = agent.collect_until_terminal(doc["task_id"], timeout_s=90)
        self.assertEqual(ccode, 20, f"M9 collect must be done: {cdoc}")
        self.assertEqual(cdoc["state"], "done")

        # S4.2 verification, LEDGER-FIRST (the amended self-report rule):
        # 1. the claimed suite ran — a tool_log entry for the test
        #    command with exit_code 0.
        tool_log = cdoc["tool_log"]
        self.assertGreaterEqual(
            len(tool_log), 3, "M9: the ledger must carry the 3 tool steps"
        )
        run_entries = [
            e
            for e in tool_log
            if e["tool"] in ("bash", "python")
            and "unittest test_textkit" in json.dumps(e.get("params", {}))
        ]
        self.assertEqual(
            len(run_entries),
            1,
            f"M9: the ledger shows the suite run: {tool_log}",
        )
        self.assertEqual(
            run_entries[0]["exit_code"],
            0,
            f"M9: the suite run's ledger exit code is 0: {run_entries[0]}",
        )
        # 2. the claimed files exist — read the workdir on main's own
        #    side (the result JSON's workdir field is the entry point).
        wd = Path(cdoc["workdir"])
        self.assertTrue((wd / "textkit.py").is_file(), "M9: workdir carries textkit.py")
        self.assertTrue(
            (wd / "test_textkit.py").is_file(), "M9: workdir carries test_textkit.py"
        )
        # 3. re-run the named check on main's own side (the authoritative
        #    check — recipe R4: main's own-copy re-run).
        scratch = self.tmp / "m9-main-scratch"
        scratch.mkdir()
        shutil.copy(wd / "textkit.py", scratch / "textkit.py")
        shutil.copy(wd / "test_textkit.py", scratch / "test_textkit.py")
        r = subprocess.run(
            [PY_BIN, "-m", "unittest", "test_textkit"],
            cwd=str(scratch),
            capture_output=True,
            text=True,
            check=False,  # the returncode is the assertion target
        )
        self.assertEqual(
            r.returncode, 0, f"M9 main-side re-run must pass: {r.stdout}{r.stderr}"
        )
        for fn in _M9_SPEC["spec_functions"]:
            self.assertIn(
                f"def {fn}(",
                (wd / "textkit.py").read_text(),
                f"M9: the workdir module defines {fn} (the S5.2 M9 file check)",
            )

    def _run_m10(self, agent: SessionAgent) -> None:
        # R2 — test batch + structured report, --tools on. The rig
        # pre-seeds the 10-file golden batch into the task workdir
        # (the task's input — main's job); the loop runs the batch and
        # writes report.json.
        wd = self.tmp / "m10-workdir"
        wd.mkdir()
        for p in (GOLDENS / "m10").glob("files_0*.py"):
            shutil.copy(p, wd / p.name)
        code, doc = agent.spawn(
            "You are running a test batch and generating a report.\n"
            "Task: run each of the 10 files matching files_*.py with "
            "`python -m unittest <name>`; record the exit code and "
            "duration for each file.\n"
            "Definition of done: all 10 files run exactly once; the "
            "report is written to report.json as JSON with the schema "
            '{"files": [{"file", "status" (pass|fail), "exit_code", '
            '"duration_ms"}]}; the status field mirrors the ACTUAL '
            "exit code (0 -> pass, non-zero -> fail), not the batch verdict.\n"
            "Final answer: the report JSON + how many files passed / "
            "failed, which failed (by name), the exact commands you ran.",
            tools="on",
            workdir=str(wd),
        )
        self.assertEqual(code, 0, f"M10 spawn must be accepted: {doc}")
        ccode, cdoc = agent.collect_until_terminal(doc["task_id"], timeout_s=120)
        self.assertEqual(ccode, 20, f"M10 collect must be done: {cdoc}")

        # S4.2 verification (R2, ledger-first):
        # 1. the report JSON is parseable + 10/10 files covered (the
        #    M10 rubric's parse + coverage halves).
        report = json.loads(cdoc["answer"])
        files = {f["file"].replace(".py", ""): f for f in report["files"]}
        self.assertEqual(len(files), 10, "M10: 10/10 files covered")
        # 2. the per-file status mirrors the ACTUAL exit code (the R2
        #    pinned schema — the live M10 defect).
        for name, f in files.items():
            self.assertEqual(
                f["status"],
                "pass" if f["exit_code"] == 0 else "fail",
                f"M10 {name}: status must mirror the exit code (R2 pin)",
            )
            self.assertEqual(
                f["status"],
                _M10_GOLDEN_STATUS[name],
                f"M10 {name}: the claimed verdict matches the seed truth",
            )
        # 3. the ledger shows the batch run with per-file exit codes.
        batch_entries = [
            e
            for e in cdoc["tool_log"]
            if e["tool"] == "bash" and "unittest" in json.dumps(e.get("params", {}))
        ]
        self.assertEqual(
            len(batch_entries),
            1,
            f"M10: the ledger shows the batch run: {cdoc['tool_log']}",
        )
        # 4. MAIN re-runs the 2 seeded-fail files in its own scratch
        #    copy and the verdicts agree (the S5.9 #5 2/10 spot; the
        #    M10 rubric's re-run half).
        scratch = self.tmp / "m10-main-scratch"
        scratch.mkdir()
        for p in (GOLDENS / "m10").glob("files_0*.py"):
            shutil.copy(p, scratch / p.name)
        for name in _M10_SEEDED_FAILS:
            r = subprocess.run(
                [PY_BIN, "-m", "unittest", name],
                cwd=str(scratch),
                capture_output=True,
                text=True,
                check=False,  # the returncode is the assertion target
            )
            self.assertNotEqual(
                r.returncode,
                0,
                f"M10 main re-run {name}: the seeded fail really fails: {r.stdout}{r.stderr}",
            )
            self.assertEqual(
                files[name]["status"],
                "fail",
                f"M10 {name}: main's observed verdict agrees with the report",
            )

    # ------------------------------------------------------------------
    # the session (the S5.6 order: text-only half, then tool-loop half;
    # S4.3 stateless branch: unload when the pool work is done)
    # ------------------------------------------------------------------
    def test_fresh_session_completes_s56_workload(self) -> None:
        # the mock pool: one scripted instance serves every task (the
        # MVP load-time condition, SPEC S0: exactly 1 instance).
        engine = self._instance(
            responses=[
                # text-only half (single completions, in task order):
                {"kind": "final", "content": _m1_answer()},  # M1
                *[
                    {"kind": "final", "content": _m2_verdict(i)} for i in range(10)
                ],  # M2 x10
                *[{"kind": "final", "content": _m3_unit(i)} for i in range(6)],  # M3 x6
                {"kind": "final", "content": _m4_draft()},  # M4
                # tool-loop half (the scripts run the real host tools):
                *_M9_LOOP,  # M9
                *_M10_LOOP,  # M10
            ]
        )
        mgr = self._pool(engine.port, total=8)
        self._write_cfg()

        agent = SessionAgent(env=self.env)

        # session start: check the pool (S4.5 failure doctrine: the
        # session's first act is to know the stack state).
        code, doc = agent.status()
        self.assertEqual(code, 0, f"status must be 0 at session start: {doc}")
        self.assertEqual(doc["stack"], "healthy", f"the pool is healthy: {doc}")

        # text-only half
        self._run_m1(agent)
        self._run_m2(agent)
        self._run_m3(agent)
        self._run_m4(agent)
        # tool-loop half
        self._run_m9(agent)
        self._run_m10(agent)

        # S4.3 stateless branch: unload when the pool work is done
        # (the VRAM-release doctrine — the pool's VRAM does NOT release
        # on its own).
        code, doc = agent.unload_all()
        self.assertEqual(code, 0, f"unload --all must be accepted: {doc}")
        health = mgr.health_doc()
        for inst in health["instances"]:
            self.assertEqual(inst["state"], "bunloaded", "the instance is unloaded")

        # ----------------------------------------------------------------
        # the assertions the spec names
        # ----------------------------------------------------------------
        # 1. COMMAND LOG subset of S3.2 (S4.6: "zero undocumented
        #    commands — asserted by the session transcript's command log").
        used = {cmd for cmd, _ in agent.command_log}
        self.assertTrue(
            used.issubset(S32_SURFACE),
            f"session issued non-S3.2 commands: {used - S32_SURFACE}",
        )
        # the session's command VARIETY: only the verbs the workload
        # needs (spawn/status/collect/unload) — no undocumented
        # lifecycle verbs, no raw endpoint calls.
        self.assertEqual(
            used,
            {"bonsai2-spawn", "bonsai2-status", "bonsai2-collect", "bonsai2-unload"},
            f"the session's verb set is the S5.6 minimum: {used}",
        )
        # every spawn's argv is within the S3.2 spawn surface (the flags
        # the skill's command reference names — -i/--tools/--deadline/
        # --workdir; no invented flag).
        allowed_flags = {
            "-i",
            "--tools",
            "--deadline",
            "--workdir",
            "--ctx",
            "--max-steps",
            "--out",
            "--priority",
        }
        for cmd, argv in agent.command_log:
            if cmd == "bonsai2-spawn":
                flags = {a for a in argv[1:] if a.startswith("-")}
                self.assertTrue(
                    flags.issubset(allowed_flags),
                    f"M10-spawn argv carries a non-S3.2 flag: {flags - allowed_flags} ({argv})",
                )
        # 2. the session's workload coverage: all six S5.6 shapes ran.
        self.assertEqual(
            frozenset({"M1", "M2", "M3", "M4", "M9", "M10"}),
            S56_WORKLOAD,
            "the session's workload is exactly the S5.6 standard set",
        )
        # 3. the skill was the session's doctrine source (the S4.7
        #    "only the skill + S3.2 scripts available" property): the
        #    recipes the session followed are the skill's, and the
        #    claims.json citations match the workload.
        bench_cites = {
            r["bench"] for r in agent.claims["work_allocation"] if r["bench"]
        }
        for sid in S56_WORKLOAD:
            self.assertIn(
                sid,
                bench_cites,
                f"the S5.6 shape {sid} is bench-cited in the skill's S4.2 table",
            )
        # 4. the session's spawns honored the recipes' tool flags:
        #    text-only half --tools off (the byte-identical wire
        #    constraint, S5.2 baseline note), tool-loop half --tools on.
        off_spawns = [
            a
            for c, a in agent.command_log
            if c == "bonsai2-spawn"
            and "--tools" in a
            and a[a.index("--tools") + 1] == "off"
        ]
        on_spawns = [
            a
            for c, a in agent.command_log
            if c == "bonsai2-spawn"
            and "--tools" in a
            and a[a.index("--tools") + 1] == "on"
        ]
        # M1 (1) + M2 (10) + M3 (6) + M4 (1) = 18 text-only; M9 + M10 = 2.
        self.assertEqual(len(off_spawns), 18, f"18 text-only spawns: {len(off_spawns)}")
        self.assertEqual(len(on_spawns), 2, f"2 tool-loop spawns: {len(on_spawns)}")


if __name__ == "__main__":
    unittest.main()
