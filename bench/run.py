#!/usr/bin/env python3
"""bench.run — the S5.4 bench runner: ``bench/run.py``.

The S5.4 runner component composition (SPEC S5.4, locked):

  * **scenario loader** — ``bench.config.load_bench_config`` (the S5.7
    bench.yaml, S5.8 schema; invalid scenario id -> exit 2) + the S5.2
    scenario matrix (``bench.scenarios``).
  * **endpoint resolver** — ``bench.endpoints`` (subagent ports from
    ``subagent-engine.yaml``, main from ``harness.yaml``).
  * **task executor** — ``bench.exec`` (streaming chat completions, token
    timing from stream timestamps; ``run_concurrent`` for the S5.2
    concurrency column).
  * **box-state snapshot** — ``bench.boxstate`` (S5.2, stdlib subprocess +
    /proc; "every row carries the box-state snapshot", S5.9 #1).
  * **scorer** — ``bench.score`` (the per-rubric plugins, S5.3).
  * **reporter** — ``bench.report`` (JSON row + ``bench-results/``
    markdown summary).

Exit-code contract (S5.4 / S3.5): **0 even on scenario-level failures**
(a failure is data: each scenario row carries a ``pass``/``fail`` verdict,
including the M8 guardrail, S5.2); **2 = bench config invalid** (the S3.2
code 2, malformed config, in the closed set) and **2 = bench already
running** (the S3.5 flock, MAP 4.3 ``BONSAI2_BENCH_LOCK``). ``bonsai2-bench``
(S3.2, MAP 4.7) is a thin wrapper whose exit code IS the runner's.

Usage:
  python3 bench/run.py                  # full bench (bench.yaml scenarios)
  python3 bench/run.py --quick          # the S5.5 quick bench (M1+M2+M7)
  python3 bench/run.py --scenario M1   # one scenario
  python3 bench/run.py --mock MOD:FN   # the S5.8 mock-endpoint run (hermetic)

The S5.8 mock-endpoint run: ``--mock <module>:<fn>`` points the runner at a
canned task executor (``fn(prompt, endpoint) -> TaskResult``) instead of the
live ``bench.exec.stream_chat`` — a full M1+M2 run against the S3.7 mock
(canned streaming responses), zero network/GPU, timings sanity-checked by
the S5.8 test (the canned executor builds TaskResults through
``bench.exec.score_stream`` over canned (ts, chunk) pairs — the S5.8
"timings sane" assertion).

M9/M10 (the S5.2 capability-layer rows, MAP 4.9): they run through the
REAL subagent tool loop (``bench.capability``) in BOTH ``tool_execution``
modes (host + engine — the S5.2 "run in both modes; the run records the
mode in the row"), score via the S5.9 #5 main-side re-runs
(``bench.score.m9`` / ``.m10``), and record the host-vs-engine comparison in
the row. The hermetic seam is ``--mock-cap MODULE:FN`` (canned capability
results, ``tests.fixtures.bench_mock:mock_cap`` — zero network/GPU, the
S5.8 "timings sane" convention). The box-state snapshot + rotation are
shared (S5.2); exit 0 even on scenario-level failures (S5.4).

H13 (post-live-run, 2026-09-27): the pool-release mechanism —
``--release-between`` (tier 2 at every scenario boundary:
``bonsai2-unload --all`` -> ``bonsai2-reload`` + a 16-token warmup
completion per reloaded instance, polled on health within the S2.8 30 s
reload budget) and ``--release-after`` (tier 2 after the run writes all
rows + the summary). Both default OFF; both are no-ops (recorded as
such) under ``--mock``; a manager-down (S3.2 exit 84) boundary is
recorded ``release: skipped (84)`` and the run CONTINUES (release is an
optimization, not a correctness gate).
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import re
import sys
import time
from pathlib import Path

# Make the bench package importable when run as a script (python3 bench/
# run.py) — the bin/bonsai2-bench wrapper does the same path insert for
# the repo-root import.
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from bench import boxstate, config, endpoints, report, scenarios
from bench import capability as bcap
from bench import exec as bexec
from bench import score as bscore
from bench.exec import TaskResult
from bench.rotate import BenchLock
from bench.rotate import rotate as rotate_dir
from bench.score import goldens

EXIT_OK = 0
EXIT_CONFIG = 2

# M9/M10 are the S5.2 capability-layer rows (MAP 4.9): they run through
# the REAL subagent tool loop in both tool_execution modes and score via
# the S5.9 #5 main-side re-runs (the capability path, _run_capability).
# The set is also the _prompts_for fallback for the canned M9/M10 prompt
# shape (the S5.2 task shapes).
_CAPABILITY = ("M9", "M10")
# The S5.2 "M8 main t/s guardrail" needs the M6 control baseline; M6 is
# ordered before M8 in every run that carries both.
_M6_BEFORE = "M6"
_M8_AFTER = "M8"


# --------------------------------------------------------------------------
# prompts (the S5.2 task shapes, as the prompts the executor sends)
# --------------------------------------------------------------------------
def _prompts_for(sid: str, n_tasks: int) -> list[str]:
    """The task prompts for one scenario (one streaming chat completion
    each, the S5.2 task shapes). Prompt markers are stable (``[M1 task
    0]`` ...) — the S5.8 mock executor keys canned responses off them."""
    if sid == "M1":
        return [
            f"[M1 task {i}] Generate a small single-file Python module with "
            "exactly 3 functions (clamp(x, lo, hi), sum_of_squares(seq), "
            "is_palindrome(s)) and its 3-case unittest suite (one test per "
            "function). Return the module source in one ```python fence and "
            "the suite in a second ```python fence. The module must compile "
            "and pass its own 3-case suite. Your final answer must be "
            "EXACTLY the two ```python fences — no prose, no reasoning "
            "text outside the fences."
            for i in range(max(1, n_tasks))
        ]
    if sid == "M2":
        return [
            f"[M2 task {i}] Run the test batch's file {i:02d} (a 3-test unittest "
            "module in the workdir) and emit ONE JSON object on a line by "
            'itself: {"file": <name>, "status": "pass"|"fail", '
            '"exit_code": <int>, "duration_ms": <int>}.'
            for i in range(max(1, n_tasks))
        ]
    if sid == "M3":
        return [
            f"[M3 section {i}] Analyze document section {i} and return ONE "
            'JSON object: {"summary": <at most 300 tokens>, "claims": '
            "<exactly 3 named claims, each a string>}."
            for i in range(max(1, n_tasks))
        ]
    if sid == "M4":
        return [
            (
                "[M4] Review the seeded diff fixture inlined below and list the "
                "bugs you find, citing each by its BUG-NN id.\n\n"
                f"<diff-fixture>\n{_m4_diff_text()}\n</diff-fixture>"
            )
        ]
    if sid == "M5":
        qa = _m5_qa()
        doc = qa.get("doc", "")
        questions = qa.get("questions", [])
        return [
            (
                "[M5] Read the document inlined below, then answer the 5 QA "
                "questions. Return a JSON array of exactly 5 short answers, in "
                "question order.\n\n<document>\n"
                f"{doc}\n</document>\n\n<questions>\n"
                f"{'\n'.join(f'Q{n}. {q}' for n, q in enumerate(questions, 1))}"
                "</questions>"
            )
        ]
    if sid == "M6":
        return ["[M6] Control: complete the task."]
    if sid in _CAPABILITY:
        shape = scenarios.get(sid).get("shape", sid)
        return ["[M9] " + shape if sid == "M9" else "[M10] " + shape]
    # M7 / M8: the throughput shapes (short, repeatable tasks).
    return [
        f"[{sid} task {i}] Short completion: summarize the bench task shape "
        "in one sentence."
        for i in range(max(1, n_tasks))
    ]


def _goldens_root(cfg: dict) -> str | None:
    """The goldens dir from the bench config (S5.7 ``goldens_dir``),
    resolved against the repo root when relative."""
    g = cfg.get("goldens_dir")
    if not g:
        return None
    p = Path(g)
    if not p.is_absolute():
        p = _REPO_ROOT / p
    if not p.is_dir():
        return None
    return str(p)


def _m4_diff_text() -> str:
    try:
        return goldens.m4_diff()
    except (FileNotFoundError, OSError):
        return "(diff fixture unavailable)"


def _m5_qa() -> dict:
    try:
        return goldens.m5_qa()
    except (FileNotFoundError, OSError):
        return {
            "doc": "(document unavailable)",
            "questions": [f"Q{i}" for i in range(5)],
            "answers": [],
        }


# --------------------------------------------------------------------------
# the executor wiring
# --------------------------------------------------------------------------
def _make_exec(mock, ctx_cap, deadline_s):
    """The per-task callable (the S5.4 "streaming chat completions, token
    timing from stream timestamps" half). ``mock`` (the S5.8 canned
    executor) wins when set."""

    def exec_one(prompt: str, endpoint: dict):
        if mock is not None:
            return mock(prompt, endpoint)
        return bexec.stream_chat(
            endpoint,
            prompt,
            max_tokens=ctx_cap,
            deadline_s=deadline_s,
        )

    return exec_one


def _load_mock_executor(spec: str):
    """The S5.8 mock-endpoint executor: import ``<module>:<fn>`` from the
    repo root. ``fn(prompt, endpoint) -> TaskResult`` (canned responses —
    zero network/GPU)."""
    if ":" not in spec:
        raise config.ConfigError(f"--mock needs a module:fn spec, got {spec!r}")
    mod_name, fn_name = spec.split(":", 1)
    mod = importlib.import_module(mod_name)
    fn = getattr(mod, fn_name)
    if not callable(fn):
        raise config.ConfigError(f"--mock {spec!r} is not callable")
    return fn


def _load_mock_cap(spec: str):
    """The S5.8/4.9 canned capability result: import ``<module>:<fn>`` from
    the repo root. ``fn(sid, tool_execution, root=None) -> dict`` (the
    ``bench.capability.run_mode`` output shape — the runner's capability
    row consumes it identically; canned tool-loop results, zero
    network/GPU, the S5.8 "timings sane" convention)."""
    if ":" not in spec:
        raise config.ConfigError(f"--mock-cap needs a module:fn spec, got {spec!r}")
    mod_name, fn_name = spec.split(":", 1)
    mod = importlib.import_module(mod_name)
    fn = getattr(mod, fn_name)
    if not callable(fn):
        raise config.ConfigError(f"--mock-cap {spec!r} is not callable")
    return fn


# --------------------------------------------------------------------------
# rubric payloads from task results (the S5.3 scorer inputs)
# --------------------------------------------------------------------------
def _first_answer(tasks) -> str:
    for t in tasks:
        if t.ok and (t.answer or t.reasoning):
            return t.answer or t.reasoning
    return ""


def _parse_m1_artifacts(answer: str) -> tuple[str, str | None]:
    """(module, suite) from an M1 answer: the ```python fences in order
    (module first, suite second). One fence -> (module, None) (the scorer
    falls back to the golden suite). No fence -> the raw answer as module."""
    blocks = re.findall(r"```(?:python)?\s*\n(.*?)```", answer, re.DOTALL)
    blocks = [b for b in blocks if b.strip()]
    if len(blocks) >= 2:
        return blocks[0], blocks[1]
    if len(blocks) == 1:
        return blocks[0], None
    return answer, None


def _build_m2_report(tasks) -> dict:
    """Aggregate the M2 10-task batch into the S5.2 report shape (the
    m2 scorer's input): one entry per task, per-file pass/fail. A task
    that emitted a parseable JSON object with file+status wins; anything
    else is an entry with status 'fail' (the file was covered, the
    verdict is fail — coverage is the rubric, not pass-rate)."""
    files = []
    n_pass = 0
    for i, t in enumerate(tasks):
        entry = None
        if t.ok:
            m = re.search(r"\{.*\}", t.answer or "", re.DOTALL)
            if m:
                try:
                    obj = json.loads(m.group(0))
                    if isinstance(obj, dict) and "status" in obj:
                        entry = {
                            "file": str(obj.get("file") or f"batch_file_{i:02d}"),
                            "status": "pass" if obj.get("status") == "pass" else "fail",
                            "exit_code": obj.get("exit_code"),
                            "duration_ms": obj.get("duration_ms"),
                        }
                except json.JSONDecodeError:
                    entry = None
        if entry is None:
            entry = {"file": f"batch_file_{i:02d}", "status": "fail"}
        if entry["status"] == "pass":
            n_pass += 1
        files.append(entry)
    return {
        "files": files,
        "summary": {
            "n_files": len(files),
            "n_pass": n_pass,
            "n_fail": len(files) - n_pass,
            "covered": len(files) == bscore.m2.EXPECTED_FILES,
        },
    }


def _m5_answers(tasks) -> list[str]:
    """The M5 QA answers: the first ok task's answer parsed as a JSON
    array of 5 strings (the S5.2 "5 QA questions" shape); the raw answer
    as a one-element fallback."""
    ans = _first_answer(tasks)
    m = re.search(r"\[.*\]", ans, re.DOTALL)
    if m:
        try:
            arr = json.loads(m.group(0))
            if isinstance(arr, list):
                return [str(x) for x in arr]
        except json.JSONDecodeError:
            pass
    return [ans] if ans else []


def _m3_sections(tasks) -> list[dict]:
    """The M3 sections: each ok task's answer as one section (the S5.2
    "fan-out doc analysis (N=6 sections)"). The answer is expected to be
    a JSON object {summary, claims} (the M3 shape); a non-JSON answer is
    a shape-fail section (the rubric scores 0 for its auto half)."""
    sections = []
    for t in tasks:
        if not t.ok:
            continue
        ans = t.answer or ""
        m = re.search(r"\{.*\}", ans, re.DOTALL)
        if m:
            try:
                obj = json.loads(m.group(0))
                if isinstance(obj, dict):
                    sections.append(obj)
                    continue
            except json.JSONDecodeError:
                pass
        sections.append({"summary": ans, "claims": []})
    return sections


def _aggregate_tps(tasks, wall_time: float | None = None) -> dict:
    """The M7/M8 aggregate over task results (the run_concurrent agg
    shape, computed over pre-collected results). H9 (post-live-run):
    carries BOTH t/s figures — ``aggregate_tps_serial`` (total tokens /
    the SERIAL per-task elapsed sum, the decode-throughput invariant)
    and ``aggregate_tps_wall`` (total tokens / the batch WALL clock,
    the S5.1 "t/s at the ceiling" saturation figure; ``aggregate_tps``
    aliases the wall value, the S5.5 baseline's read). ``wall_time``
    is the batch wall-clock seconds when the caller measured one
    (``run_concurrent``'s aggregate); None keeps the pre-amendment
    serial shape for the legacy call sites."""
    ok = [t for t in tasks if t.ok]
    total_tokens = sum(t.completion_tokens or 0 for t in ok)
    total_elapsed = sum(t.elapsed_ms or 0.0 for t in ok) / 1000.0
    agg = {
        "n_tasks": len(tasks),
        "n_ok": len(ok),
        "n_errors": len(tasks) - len(ok),
        "total_completion_tokens": total_tokens,
        "total_elapsed_s": total_elapsed,
        "aggregate_tps_serial": total_tokens / total_elapsed
        if total_elapsed > 0
        else 0.0,
    }
    if isinstance(wall_time, (int, float)) and wall_time > 0:
        agg["total_wall_s"] = wall_time
        agg["aggregate_tps_wall"] = total_tokens / wall_time
        agg["aggregate_tps"] = total_tokens / wall_time
    else:
        # a legacy call site without a measured batch wall keeps the
        # pre-amendment shape (the serial figure).
        agg["aggregate_tps"] = (
            total_tokens / total_elapsed if total_elapsed > 0 else 0.0
        )
    return agg


# --------------------------------------------------------------------------
# shape scoring (the S5.3 rubric routing, shared by the subagent rows and
# the M6 control — the S5.2 "same rubrics")
# --------------------------------------------------------------------------
def _score_shape(
    sid: str,
    tasks: list,
    cfg: dict,
    agg: dict | None = None,
) -> dict:
    """Score ONE scenario's task fan-out with its S5.3 rubric. A shape
    failure is a verdict (pass/score/detail), not an exception (the S5.4
    "the failure is data" rule). M7's zero-5xx half reads the per-task
    error names (the exec's ctx/parse/engine mapping). The M3 spot-check
    value is the single source of truth in ``cfg["_spotcheck"]`` (stashed
    by ``run`` when the ``--spotcheck`` flag is set; None -> the scorer's
    0.0 default). ``agg`` (H9 post-live-run) is the scenario's measured
    ``run_concurrent`` aggregate (the batch WALL clock + the serial sum +
    both t/s figures) when the caller has one — M7 scores from it; a None
    falls back to the pre-amendment serial aggregate over the tasks."""
    root = _goldens_root(cfg)
    if sid == "M1":
        per_task = []
        for t in tasks:
            module, suite = _parse_m1_artifacts(t.answer or t.reasoning or "")
            payload = {"module": module, "suite": suite}
            v = bscore.m1.score(payload, root=root)
            per_task.append(v)
        if not per_task:
            return bscore._common.verdict(False, 0.0, "M1: no tasks to score")
        score = sum(v["score"] for v in per_task) / len(per_task)
        ok = all(v["pass"] for v in per_task)
        n_pass = sum(1 for v in per_task if v["pass"])
        return bscore._common.verdict(
            ok,
            score,
            f"M1: {n_pass}/{len(per_task)} generated artifacts pass the compile+suite rubric",
            {"per_task": per_task},
        )
    if sid == "M2":
        report = _build_m2_report(tasks)
        return bscore.m2.score({"report": json.dumps(report)})
    if sid == "M3":
        # the spot-check value (the S5.3 main/human-judgment seam) is the
        # single source of truth in cfg["_spotcheck"] (None -> m3.score's
        # 0.0 default) — no shadowing param.
        return bscore.m3.score(
            {"sections": _m3_sections(tasks), "spotcheck": cfg.get("_spotcheck")}
        )
    if sid == "M4":
        return bscore.m4.score({"draft": _first_answer(tasks)}, root=root)
    if sid == "M5":
        return bscore.m5.score({"answers": _m5_answers(tasks)}, root=root)
    if sid == "M7":
        # H9 (post-live-run): score from the MEASURED aggregate (the
        # batch wall clock — the S5.1 "t/s at the ceiling" saturation
        # figure). A None agg keeps the legacy serial shape.
        if agg is None:
            agg = _aggregate_tps(tasks)
        return bscore.m7.score({"agg": agg, "results": tasks})
    return bscore._common.verdict(False, 0.0, f"no rubric for shape {sid!r}")


# --------------------------------------------------------------------------
# the run
# --------------------------------------------------------------------------
def run(
    cfg: dict,
    quick: bool = False,
    scenario: str | None = None,
    mock_spec: str | None = None,
    mock_cap_spec: str | None = None,
    deadline_s: float = 600.0,
    max_tokens: int | None = None,
    spotcheck: float | None = None,
    release_after: bool = False,
    release_between: bool = False,
) -> dict:
    """Run the bench (the S5.4 runner). Returns the run report (dict: the
    per-scenario rows + the summary path + the rotation report). The exit
    code is decided by ``main`` (0 on completion even with row failures,
    2 on bad config / a concurrent bench).

    quick: the S5.5 quick bench (M1+M2+M7).
    scenario: run ONE scenario id (a single-row re-run / the S5.8 dogfood).
    mock_spec: the S5.8 ``module:fn`` canned executor (hermetic).
    mock_cap_spec: the S5.8/4.9 ``module:fn`` canned capability result
    (hermetic M9/M10: ``fn(sid, tool_execution, root=None) -> dict``).
    spotcheck: the M3 spot-check value (the S5.3 main/human-judgment seam;
    the live main agent reads 1/3 of the sections and supplies this).
    release_after / release_between: the H13 pool-release flags (the
    S4.2/S4.3 tier-2 release — manager-mediated via the S3.2 scripts;
    both are no-ops recorded as such under ``--mock``).
    """
    if spotcheck is not None:
        cfg["_spotcheck"] = spotcheck
    results_dir = Path(cfg["results_dir"])
    results_dir.mkdir(parents=True, exist_ok=True)
    # S5.2 rotation + 1 GB cap on start (MAP 4.3: 7-day prune, keep newest).
    rotation_report = rotate_dir(results_dir)
    # S5.4 "flock per S3.5" / MAP 4.3 BONSAI2_BENCH_LOCK (one bench at a
    # time on the box — the machine rule; a concurrent bench exits 2).
    lock_path = Path(
        os.environ.get("BONSAI2_BENCH_LOCK") or str(results_dir.parent / ".bench.lock")
    )
    lock = BenchLock(lock_path)
    if not lock.acquire():
        raise config.ConfigError(f"bench already running (flock held: {lock_path})")
    try:
        sids = _select_scenarios(cfg, quick, scenario)
        mock = _load_mock_executor(mock_spec) if mock_spec else None
        mock_cap = _load_mock_cap(mock_cap_spec) if mock_cap_spec else None
        engine = endpoints.load_engine_identity()
        harness = endpoints.load_harness_main()
        n_pool, _ = endpoints.subagent_ports(engine)
        rows: list[dict] = []
        m6_baseline: float | None = None
        # H13: the pool-release records (S4.2/S4.3). ``release_between``
        # fires at each scenario BOUNDARY (rows for scenario N are
        # written before the boundary release; scenario N+1's rows are
        # after — the ordering the hermetic test asserts). A no-op under
        # --mock, recorded as such (the flag is exercised through the
        # record, not the pool).
        release_between_records: list[dict] = []
        unload_bin, reload_bin = _pool_release_flags_available()
        ordered_sids = _order_for_m6(sids)
        for n, sid in enumerate(ordered_sids):
            if release_between and n > 0:
                release_between_records.append(
                    _pool_release(
                        "between",
                        unload_bin,
                        reload_bin,
                        mock is not None,
                    )
                )
            row = _run_scenario(
                sid,
                cfg,
                engine,
                harness,
                n_pool,
                mock,
                mock_cap,
                deadline_s,
                max_tokens,
                m6_baseline,
            )
            rows.append(row)
            report.write_row(row, results_dir, sid)
            # the capability row (S5.2 both modes) carries the second-mode
            # row: write it too (the `<ts>_<sid>_engine.json` sibling) so
            # the host-vs-engine comparison is in the run JSON on disk,
            # not only inline (S5.9 #5).
            cap_row = row.get("capability_comparison_engine_row")
            if isinstance(cap_row, dict):
                report.write_row(cap_row, results_dir, f"{sid}_engine")
            if sid == "M6":
                m6_baseline = row.get("main_tps")
        summary_path = report.write_summary(
            rows,
            cfg.get("_summary_dir", "bench-results"),
            cfg,
            None,
            box_root=_REPO_ROOT,
        )
        out = {
            "n_scenarios": len(rows),
            "rows": rows,
            "summary_path": str(summary_path),
            "rotation": rotation_report,
        }
        # H12 (post-live-run): ONE extra post-run box-state snapshot so
        # the END-OF-RUN state is explicit (the per-row snapshots are the
        # run's VRAM time series — this one is the last sample, written
        # into the run report so a reader never has to hunt the newest
        # row file for it).
        out["box_state_post_run"] = boxstate.snapshot()
        if release_after:
            out["release_after"] = _pool_release(
                "after", unload_bin, reload_bin, mock is not None
            )
        if release_between:
            out["release_between"] = release_between_records
        return out
    finally:
        lock.release()


def _select_scenarios(cfg: dict, quick: bool, scenario: str | None) -> list[str]:
    if scenario:
        if scenario not in scenarios.VALID_IDS:
            raise config.ConfigError(f"unknown scenario id: {scenario!r}")
        return [scenario]
    if quick:
        return list(cfg.get("quick", ["M1", "M2", "M7"]))
    return list(cfg.get("scenarios", scenarios.scenario_ids()))


def _order_for_m6(sids: list[str]) -> list[str]:
    """Move M6 before M8 in the run order (the S5.2 guardrail baseline:
    M8's main t/s is checked against M6's). All else in the given order."""
    if _M6_BEFORE in sids and _M8_AFTER in sids:
        out = [s for s in sids if s not in (_M6_BEFORE, _M8_AFTER)]
        out.insert(0, _M6_BEFORE)
        out.append(_M8_AFTER)
        return out
    return sids


def _ctx_cap(sid: str, max_tokens_override: int | None) -> int | None:
    """The S5.2 "runner-enforced token budget" completion cap for a
    scenario (the ctx column -> a max_tokens cap on the completion).
    ``max_tokens_override`` (the --max-tokens CLI flag) wins when set."""
    if max_tokens_override is not None:
        return max_tokens_override
    ctx = scenarios.get(sid).get("ctx", 0)
    if ctx and ctx > 0:
        # a rough completion cap: a fraction of the ctx budget (the prompt
        # is a bounded shape; the cap keeps output within the column).
        return max(64, ctx // 8)
    return None


def _concurrency(sid: str, n_pool: int) -> int | None:
    """The S5.2 concurrency column for a scenario (the thread-pool bound):
    M2=8, M7=8*N, M8=4*N, M10=8, M3=the task count (parallel), else 1."""
    c = scenarios.get(sid).get("concurrency", 1)
    if c == "parallel":
        return None  # the caller uses the task count
    if isinstance(c, str):
        m = re.fullmatch(r"(\d*)\**\s*N", c)
        if m:
            mult = int(m.group(1)) if m.group(1) else 1
            return max(1, mult * max(1, n_pool))
        return 1
    return max(1, int(c))


# --------------------------------------------------------------------------
# the capability-layer path (M9/M10, MAP 4.9): both tool_execution modes
# --------------------------------------------------------------------------
def _capability_payload(sid: str, res: dict, root: str | None) -> dict:
    """The S5.9 #5 main-side re-run payload for one capability result (the
    scorer's input shape — the workdir + the tool-loop ledger + the
    subagent's claim)."""
    if sid == "M9":
        return {
            "workdir": str(res.get("workdir")),
            "tool_log": res.get("tool_log") or [],
            "answer": res.get("answer") or "",
            "module_name": "textkit",
            "suite_file": "test_textkit.py",
            "run_command": "python -m unittest test_textkit",
            "spec_functions": [
                f["function"] if isinstance(f, dict) else str(f)
                for f in bscore.goldens.m9_spec(root).get("spec_functions", [])
            ],
        }
    return {"report": res.get("answer") or ""}


def _capability_mode_row(sid: str, mode: str, res: dict, snap: dict, cfg: dict) -> dict:
    """Build + score ONE capability task (M9/M10) in ONE tool_execution
    mode into the S5.2 row shape (the S5.4 "one JSON row per scenario"
    contract, extended with the tool_execution mode). The S5.2 "the run
    records the mode in the row" half: ``tool_execution_mode``. The box-
    state snapshot is shared (S5.2); scoring is the S5.9 #5 main-side
    re-run (the m9/m10 scorer plugins, ``bench.score``)."""
    root = _goldens_root(cfg)
    verdict = _score_capability(sid, res, root)
    timing = res.get("timing") or {}
    task = TaskResult(
        ok=bool(res.get("state") == "done"),
        answer=res.get("answer") or "",
        reasoning=res.get("reasoning") or "",
        usage=res.get("usage") or {},
        prompt_tokens=timing.get("prompt_tokens"),
        completion_tokens=timing.get("completion_tokens"),
        time_to_first_token_ms=timing.get("time_to_first_token_ms"),
        elapsed_ms=timing.get("elapsed_ms"),
        n_content_chunks=timing.get("n_content_chunks") or (res.get("steps") or 0),
        decode_tps=timing.get("decode_tps"),
        error=res.get("error"),
        detail=None,
    )
    row = report.build_row(
        sid,
        [task],
        verdict,
        snap,
        cfg,
        None,
        box_root=_REPO_ROOT,
    )
    row["tool_execution_mode"] = mode
    row["tool_execution"] = "on"
    row["capability"] = {
        "task_id": res.get("task_id"),
        "state": res.get("state"),
        "error": res.get("error"),
        "tool_log": res.get("tool_log") or [],
        "steps": res.get("steps"),
        "workdir": str(res.get("workdir") or ""),
    }
    return row


def _score_capability(sid: str, res: dict, root: str | None) -> dict:
    """Score one capability task with its S5.3 rubric (the S5.9 #5
    main-side re-run: m9 / m10)."""
    payload = _capability_payload(sid, res, root)
    if sid == "M9":
        return bscore.m9.score(payload, root=root)
    if sid == "M10":
        return bscore.m10.score(payload, root=root)
    return bscore._common.verdict(False, 0.0, f"no capability rubric for {sid!r}")


def _run_capability(
    sid: str,
    cfg: dict,
    engine: dict,
    harness: dict,
    n_pool: int,
    mock_cap,
    deadline_s: float,
    snap: dict,
) -> dict:
    """Run the M9/M10 capability row (S5.2 "run in both tool_execution
    modes (host and engine); the run records the mode in the row"):
    execute ONE capability task in EACH mode (the real harness tool loop
    via ``bench.capability.run_mode``, or the ``mock_cap`` canned results
    for the S5.8 hermetic run), score each via the S5.9 #5 main-side
    re-runs, and record the host-vs-engine comparison in the row
    (S5.9 #5). Both mode rows carry the box-state snapshot (S5.2).

    Returns the canonical row (the host-mode row) with the second-mode
    verdict + the comparison stashed under ``capability_comparison``; the
    caller writes BOTH rows to the results dir (the host row is the
    canonical one, the engine row is written as
    ``<ts>_<sid>_engine.json``)."""
    modes = ("host", "engine")
    rows = []
    for mode in modes:
        try:
            if mock_cap is not None:
                res = mock_cap(sid, mode, _goldens_root(cfg))
            else:
                res = _capability_live(
                    sid, cfg, engine, harness, n_pool, mode, deadline_s
                )
            rows.append(_capability_mode_row(sid, mode, res, snap, cfg))
        except bcap.CapabilityError as exc:
            # H2 (post-live-run): a capability-row failure is a ROW
            # verdict, not a run crash (the live first run's M9/host
            # CapabilityError propagated out of the mode loop to the
            # top-level catch -> status "crash" -> M8/M9/M10 all
            # dropped; S5.4 "failure is data" extends to the capability
            # path explicitly — the run continues to the remaining
            # modes + scenarios and writes all rows + the summary).
            # The degraded result is an EMPTY capability result in the
            # run_mode output shape: the scorer's sub-conditions fail
            # honestly (the main re-run still runs the golden suite; the
            # empty workdir / empty tool_log fail their halves), and the
            # row carries the error + the last state in its detail.
            degraded = {
                "task_id": None,
                "tool_execution": mode,
                "state": "crashed",
                "error": f"{exc.__class__.__name__}: {exc}",
                "answer": "",
                "reasoning": "",
                "usage": {},
                "tool_log": [],
                "steps": None,
                # G3c round-1 P2 (degraded workdir): the real staged dir was
                # already rmtree'd in the driver's finally, so pointing at
                # the shared tempdir (which still holds OTHER tasks' debris)
                # would send a reader looking for this task's artifact in
                # the wrong place. A named placeholder is honest: the
                # workdir was cleaned; the scorer's file-check half fails
                # against it by construction.
                "workdir": "<staged-workdir: cleaned>",
                "timing": {
                    "ok": False,
                    "elapsed_ms": 0.0,
                    "completion_tokens": None,
                    "decode_tps": None,
                },
            }
            row = _capability_mode_row(sid, mode, degraded, snap, cfg)
            row["verdict"]["detail"] = (
                f"{row['verdict']['detail']} — capability driver: {degraded['error']}"
            )
            row["capability"]["error"] = degraded["error"]
            rows.append(row)
    host, engine_row = rows[0], rows[1]
    # S5.9 #5 "the host-vs-engine tool_execution comparison is recorded in
    # the run JSON": both verdicts + whether they agree + the per-mode t/s.
    comparison = {
        "host": {"row_verdict": host["row_verdict"], "score": host["verdict"]["score"]},
        "engine": {
            "row_verdict": engine_row["row_verdict"],
            "score": engine_row["verdict"]["score"],
        },
        "agree": host["row_verdict"] == engine_row["row_verdict"],
        "host_tps": host["tasks"][0].get("decode_tps"),
        "engine_tps": engine_row["tasks"][0].get("decode_tps"),
    }
    host["capability_comparison"] = comparison
    host["capability_comparison_engine_row"] = engine_row
    return host


def _engine_port_base(engine: dict) -> int:
    """The subagent pool's port_base (S1.3; the S5.4 endpoint resolver's
    convention, defaulted to 30101). The 0-pool capability fallback
    routes to the first pool port."""
    inst = engine.get("instances", {})
    base = inst.get("port_base", 30101)
    return base if isinstance(base, int) and not isinstance(base, bool) else 30101


def _capability_live(
    sid: str,
    cfg: dict,
    engine: dict,
    harness: dict,
    n_pool: int,
    mode: str,
    deadline_s: float,
) -> dict:
    """One capability task in ONE mode through the REAL harness tool loop
    (``bench.capability.run_mode`` — cmd_spawn + the Popen'd
    bonsai2-taskworker). The instance port is the pool's first (S5.2
    M9/M10: 1 instance); a 0-pool falls back to the engine's port_base
    (the S2.4 main-only convention: the loop still needs an instance port
    to route to, so the first pool port stands in)."""
    eps = endpoints.resolve_for_scenario(sid, engine, harness, n_pool)
    if eps and eps[0].get("kind") == "subagent":
        port = eps[0]["port"]
        main_ep = endpoints.main_endpoint(harness)["url"]
    else:
        port = _engine_port_base(engine)
        main_ep = None
    return bcap.run_mode(
        sid,
        mode,
        root=_goldens_root(cfg),
        deadline_s=deadline_s,
        instance_port=port,
        main_endpoint=main_ep,
        engine_yaml=os.environ.get("BONSAI2_ENGINE_YAML"),
    )


def _run_scenario(
    sid: str,
    cfg: dict,
    engine: dict,
    harness: dict,
    n_pool: int,
    mock,
    mock_cap,
    deadline_s: float,
    max_tokens: int | None,
    m6_baseline: float | None,
) -> dict:
    """Run ONE scenario: resolve endpoints + tasks, execute (the S5.2
    concurrency column), score (the S5.3 rubric), build the row (with the
    S5.2 box-state snapshot). A scenario-level failure is a row verdict
    (fail), not an error (the S5.4 "exit 0 even on scenario-level
    failures" rule). M9/M10 take the capability path (the S5.2 dual
    tool_execution modes, MAP 4.9)."""
    snap = boxstate.snapshot()
    if sid in scenarios.CAPABILITY:
        return _run_capability(
            sid, cfg, engine, harness, n_pool, mock_cap, deadline_s, snap
        )
    if sid == "M6":
        return _run_m6_control(cfg, harness, mock, deadline_s, snap)
    if sid == "M8":
        return _run_m8(
            cfg, engine, harness, n_pool, mock, deadline_s, snap, m6_baseline
        )
    n_tasks = scenarios.resolve_tasks(sid, n_pool)
    ctx_cap = _ctx_cap(sid, max_tokens)
    eps = endpoints.resolve_for_scenario(sid, engine, harness, n_pool)
    prompts = _prompts_for(sid, n_tasks)
    conc = _concurrency(sid, n_pool)
    if conc is None:
        # "parallel" (M3): all available instances, one section each —
        # the S5.2 instances column min(6, avail): the 6 sections are
        # the fixed shape; the pool size caps the PARALLEL fan-out (a
        # 0-pool main-only fallback runs them sequentially).
        conc = min(len(prompts), max(n_pool, 1)) if len(prompts) > 1 else 1
        if n_pool == 0:
            conc = 1
    exec_one = _make_exec(mock, ctx_cap, deadline_s)
    # round-robin the prompts over the endpoint pool (the S5.2 "all
    # instances, all slots" for M7; single-endpoint shapes use eps[0]).
    ep_by_prompt = {
        p: (eps[i % len(eps)] if eps else endpoints.main_endpoint(harness))
        for i, p in enumerate(prompts)
    }

    def exec_prompt(prompt: str):
        return exec_one(prompt, ep_by_prompt[prompt])

    tasks, agg = bexec.run_concurrent(prompts, conc, exec_prompt)
    verdict = _score_shape(sid, tasks, cfg, agg=agg)
    row = report.build_row(sid, tasks, verdict, snap, cfg, None, box_root=_REPO_ROOT)
    row["concurrency"] = conc
    row["n_tasks"] = len(tasks)
    row["ctx_cap"] = ctx_cap
    row["tool_execution"] = "on" if sid in scenarios.CAPABILITY else "off"
    if sid == "M7":
        # H9 (post-live-run, decision §0.2): the M7 row carries the
        # COMPARABILITY triple {n_tasks, concurrency, N} — the S5.5
        # baseline row carries the same triple, and the re-bench gate
        # compares like-with-like (a mismatch -> "not comparable", not a
        # silent compare: the engine's --parallel 8 cap is constant, but
        # the load the bench applies varies per M, so comparability is a
        # RECORDED property, not an assumption).
        row["comparability"] = {
            "n_tasks": len(tasks),
            "concurrency": conc,
            "N": n_pool,
        }
        # the measured aggregate (the S5.1 t/s-at-the-ceiling inputs:
        # total_wall_s + both t/s figures) is on the row for the reader
        # + the S5.5 baseline capture (bench/rebench.py save-baseline).
        # G3c round-1 P2 (authoritative-figure guidance): this dict carries
        # BOTH the serial and the wall t/s; the row's per-row
        # total_elapsed_s / timing can disagree with them after a scenario
        # retry, so name which is which for a reader — the S5.1 headline
        # "t/s at the ceiling" is `aggregate_tps_wall` (== `aggregate_tps`,
        # the S5.5 baseline read), and `aggregate_tps_serial` is the
        # decode-throughput invariant recorded for comparison only.
        row["aggregate"] = agg
    return row


def _run_m6_control(cfg, harness, mock, deadline_s, snap) -> dict:
    """The M6 control (S5.2 "main-only control of M1-M5, same rubrics"):
    run the M1-M5 task shapes on the MAIN endpoint and score each with the
    SAME rubric modules. The control's main_tps is the M8 guardrail
    baseline. Recorded as the M6 row (a valid control when main_tps > 0
    and every M1-M5 rubric passes on main)."""
    main_ep = endpoints.main_endpoint(harness)
    exec_one = _make_exec(mock, _ctx_cap("M6", None), deadline_s)
    verdicts: dict = {}
    total_tokens = 0
    total_elapsed = 0.0
    all_tasks: list = []
    for shape in bscore.m6.CONTROL_SHAPES:
        n = scenarios.resolve_tasks(shape, 1)
        prompts = _prompts_for(shape, n)
        for prompt in prompts:
            t = exec_one(prompt, main_ep)
            all_tasks.append(t)
            if t.ok:
                total_tokens += t.completion_tokens or 0
                total_elapsed += (t.elapsed_ms or 0.0) / 1000.0
        # the "same rubrics" half: score the shape's tasks (the ones just
        # appended, most-recent-last) with its own rubric module (the S5.2
        # "same rubrics; the control").
        verdicts[shape] = _score_shape(shape, _last_n(all_tasks, len(prompts)), cfg)
    main_tps = total_tokens / total_elapsed if total_elapsed > 0 else 0.0
    control_verdict = bscore.m6.score({"main_tps": main_tps, "verdicts": verdicts})
    row = report.build_row(
        "M6", all_tasks, control_verdict, snap, cfg, None, box_root=_REPO_ROOT
    )
    row["main_tps"] = main_tps
    row["per_shape"] = control_verdict.get("extra", {}).get("per_shape", {})
    row["n_tasks"] = len(all_tasks)
    row["concurrency"] = 1
    row["ctx_cap"] = _ctx_cap("M6", None)
    row["tool_execution"] = "off"
    return row


def _last_n(tasks: list, n: int) -> list:
    """The last ``n`` tasks of ``tasks`` (the current shape's fan-out,
    appended most-recent-last)."""
    return list(tasks)[-n:] if n > 0 else []


# --------------------------------------------------------------------------
# H13 (post-live-run): the pool-release mechanism (S4.2/S4.3, the two
# tiers; bench flags + the manager-mediated S3.2 surface only)
# --------------------------------------------------------------------------
def _pool_release_flags_available() -> tuple[str | None, str | None]:
    """The repo's ``bin/bonsai2-unload`` / ``bin/bonsai2-reload`` (the
    S3.2 manager-mediated surface; the bench does NOT touch docker or
    the instances directly). None when the script is absent (a
    pre-install checkout without bin/ — the flag then records
    ``skipped (no bin)`` and the run continues)."""
    d = _REPO_ROOT / "bin"
    unload = d / "bonsai2-unload"
    reload_ = d / "bonsai2-reload"
    return (
        str(unload) if unload.is_file() else None,
        str(reload_) if reload_.is_file() else None,
    )


def _pool_release(
    kind: str,
    unload_bin: str | None,
    reload_bin: str | None,
    mock: bool,
) -> dict:
    """One tier-2 pool release (S4.2 "stateless branch: unload between
    each M"; S4.3 "tier 2 = model + KV"). Manager-mediated through the
    S3.2 scripts; the bench records, never enforces (release is an
    optimization, not a correctness gate):

    * ``--mock`` — a no-op, recorded as such (the hermetic run never
      touches the pool; the flag is still EXERCISED through the record
      so the next live run's run JSON shows the boundary fired).
    * unload/reload absent — ``skipped (no bin)``.
    * unload exit 84 (manager down, S3.2) — ``skipped (84)``; the run
      CONTINUES (an 84 boundary must not abort the bench: the next
      scenario still runs against whatever pool state exists).
    * reload exit != 0 — ``partial`` (recorded; the warmup still runs
      against the instances that came up).

    ``kind`` is ``between`` (a scenario boundary: unload -> reload ->
    warmup) or ``after`` (the run's end: unload only). Returns the
    record dict the run JSON carries (``release_between`` /
    ``release_after``)."""
    rec: dict = {"kind": kind, "tier": 2}
    if mock:
        rec.update({"mock": True, "note": "no-op under --mock (H13)"})
        return rec
    if not unload_bin:
        rec.update({"note": "skipped (no bin)"})
        return rec
    import subprocess

    t0 = time.monotonic()
    try:
        # G3c round-1 P1: invoke the S3.2 script by its OWN
        # shebang/interpreter, not the bench's python. Pre-install the
        # script is a python3-shebang .py; a Phase-6 install may ship it
        # as a bash wrapper (S3.2), and [sys.executable, unload_bin]
        # would run that bash wrapper under the bench's python and crash
        # the release path (the pre-install .py path only "worked" by
        # accident). Executing the path directly hands it to its shebang.
        p = subprocess.run(
            [unload_bin, "--all"],
            capture_output=True,
            text=True,
            timeout=90,
            check=False,
        )
    except OSError as exc:
        # a present-but-not-launchable script (e.g. exec bit stripped) is
        # a named skip, not an abort (H13: release is an optimization,
        # not a correctness gate).
        rec.update(
            {
                "note": f"skipped (unload not launchable: {exc.__class__.__name__})",
                "unload_exit": None,
                "s": round(time.monotonic() - t0, 3),
            }
        )
        return rec
    unload_exit = p.returncode
    if unload_exit == 84:
        rec.update(
            {
                "note": "skipped (84)",
                "unload_exit": unload_exit,
                "s": round(time.monotonic() - t0, 3),
            }
        )
        return rec
    if unload_exit != 0:
        rec.update(
            {
                "note": f"unload exit {unload_exit}",
                "unload_exit": unload_exit,
                "s": round(time.monotonic() - t0, 3),
            }
        )
        return rec
    rec["unload_exit"] = 0
    if kind == "between" and reload_bin:
        t0 = time.monotonic()
        try:
            # G3c round-1 P1 (reload side, same class as the unload side):
            # invoke the S3.2 script by its own shebang/interpreter, not
            # the bench's python (pre-install .py vs Phase-6 bash wrapper).
            p = subprocess.run(
                [reload_bin, "--all"],
                capture_output=True,
                text=True,
                timeout=90,
                check=False,
            )
        except OSError as exc:
            # a not-launchable reload script is a named partial (the
            # instances the manager already has keep serving; the bench
            # records, never enforces).
            rec.update(
                {
                    "note": f"partial (reload not launchable: {exc.__class__.__name__})",
                    "reload_exit": None,
                    "reload_s": round(time.monotonic() - t0, 3),
                }
            )
            return rec
        reload_exit = p.returncode
        reload_s = round(time.monotonic() - t0, 3)
        # S2.8 30 s reload budget (post-amendment): the reload is
        # manager-mediated and the manager re-probes health; the bench
        # does not re-implement the probe — it records the exit + wall
        # and WARNS (data, not a gate) when the S2.8 budget is blown.
        rec.update(
            {
                "reload_exit": reload_exit,
                "reload_s": reload_s,
                "reload_budget_s": 30.0,
                "budget_exceeded": reload_s > 30.0,
            }
        )
        if reload_exit != 0:
            rec["note"] = f"partial (reload exit {reload_exit})"
            return rec
        # the 16-token warmup completion per reloaded instance (the MAP
        # 1.7 bring-up-check pattern): a cold prefill on the FIRST real
        # task would poison M7's saturation timing, so the bench spends
        # 16 tokens on a discarded completion before the next scenario.
        # It goes at the instance ports the S1.6 pool owns — the bench
        # has already resolved them for this run; the hermetic/mock path
        # never reaches here, so the real HTTP is the only transport.
        w0 = time.monotonic()
        warmup_ok = _warmup_instances()
        rec["warmup_s"] = round(time.monotonic() - w0, 3)
        rec["warmup_ok"] = warmup_ok
    rec["note"] = rec.get("note", "done")
    return rec


def _warmup_instances() -> bool:
    """The 16-token warmup completion per reloaded instance (H13). The
    subagent pool's endpoints come from the engine yaml (the S1.6
    ports); the warmup is a DISCARDED completion (its tokens are not
    the scenario's) — the point is to prime the allocator / prefill
    path so the next scenario's first task does not pay a cold-start
    inside the measured window. Any non-2xx per instance is a named
    WARN (data, not a gate): a warmup miss must not fail the bench.
    Returns True when every instance's warmup completed 2xx."""
    import requests  # lazy: the warmup is I/O (the S5.4 dep surface)

    from bench import endpoints

    try:
        engine = endpoints.load_engine_identity()
    except (OSError, ValueError, FileNotFoundError):
        return False
    n, eps = endpoints.subagent_ports(engine)
    if n == 0:
        return True  # main-only: nothing to warm
    ok_all = True
    for ep in eps:
        try:
            r = requests.post(
                f"{ep['url'].rstrip('/')}/v1/chat/completions",
                json={
                    "model": ep["alias"],
                    "messages": [
                        {"role": "user", "content": "warmup (H13, discarded)"}
                    ],
                    "max_tokens": 16,
                    "stream": False,
                },
                headers=(
                    {"Authorization": f"Bearer {ep['api_key']}"}
                    if ep.get("api_key")
                    else {}
                ),
                timeout=30,
            )
            ok = 200 <= r.status_code < 300
        except requests.RequestException:
            ok = False
        ok_all = ok_all and ok
    return ok_all


def _run_m8(
    cfg,
    engine,
    harness,
    n_pool,
    mock,
    deadline_s,
    snap,
    m6_baseline: float | None,
) -> dict:
    """The M8 contention row (S5.2 "main at working load + subagents at
    working load"): run the 4*N subagent load AND one main-side timed
    completion CONCURRENTLY (one shared pool — the main t/s measured
    UNDER the subagent load), then score the guardrail: main t/s within
    ``main_guardrail_pct`` of the M6 control. The runner records the
    verdict per scenario row as pass/fail; a guardrail breach flips the
    row to fail while the run still exits 0 (S5.4)."""
    sub_eps = endpoints.resolve_for_scenario("M8", engine, harness, n_pool)
    main_ep = endpoints.main_endpoint(harness)
    n_tasks = scenarios.resolve_tasks("M8", n_pool)
    prompts = _prompts_for("M8", n_tasks)
    conc = _concurrency("M8", n_pool)
    ctx_cap = _ctx_cap("M8", None)
    exec_one = _make_exec(mock, ctx_cap, deadline_s)
    main_prompt = "[M8 main] Under-load main measurement: summarize the bench task shape in one sentence."
    ep_by_prompt = {p: sub_eps[i % len(sub_eps)] for i, p in enumerate(prompts)}
    ep_by_prompt[main_prompt] = main_ep

    def exec_prompt(prompt: str):
        return exec_one(prompt, ep_by_prompt[prompt])

    combined = prompts + [main_prompt]
    out, _agg = bexec.run_concurrent(
        combined, max(1, (conc or max(1, len(prompts))) + 1), exec_prompt
    )
    sub_tasks = out[: len(prompts)]
    main_res = out[-1]
    m8_main_tps = (main_res.decode_tps or 0.0) if main_res.ok else 0.0
    # the baseline: this run's M6 control t/s, else a fresh unloaded
    # main-side measurement (a standalone M8 run, e.g. --scenario M8).
    m6_tps = m6_baseline
    if not isinstance(m6_tps, (int, float)) or m6_tps <= 0:
        ctrl = exec_one("[M6] Control: complete the task.", main_ep)
        m6_tps = (ctrl.decode_tps or 0.0) if ctrl.ok else 0.0
    verdict = bscore.m8.score(
        {
            "m6_tps": m6_tps,
            "m8_main_tps": m8_main_tps,
            "main_guardrail_pct": cfg.get("main_guardrail_pct", 5.0),
        }
    )
    row = report.build_row(
        "M8", sub_tasks, verdict, snap, cfg, None, box_root=_REPO_ROOT
    )
    row["m8_main_tps"] = m8_main_tps
    row["m6_baseline_tps"] = m6_tps
    row["n_tasks"] = len(sub_tasks)
    row["concurrency"] = conc
    row["ctx_cap"] = ctx_cap
    row["tool_execution"] = "off"
    return row


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="The qwen-tory38 bench runner (S5.4; MAP Phase 4)."
    )
    ap.add_argument(
        "--quick", action="store_true", help="the S5.5 quick bench (M1+M2+M7)"
    )
    ap.add_argument("--scenario", help="run ONE scenario id (M1..M10)")
    ap.add_argument(
        "--mock",
        metavar="MODULE:FN",
        help="the S5.8 mock-endpoint executor (hermetic; fn(prompt,endpoint)->TaskResult)",
    )
    ap.add_argument(
        "--mock-cap",
        metavar="MODULE:FN",
        help="the S5.8/4.9 canned capability result (hermetic M9/M10; fn(sid,tool_execution,root=None)->dict)",
    )
    ap.add_argument(
        "--config",
        default=None,
        help="bench.yaml path (default: the tracked template, or $BONSAI2_BENCH_CONFIG)",
    )
    ap.add_argument(
        "--max-tokens", type=int, default=None, help="the S5.2 completion cap override"
    )
    ap.add_argument(
        "--deadline-s",
        type=float,
        default=600.0,
        help="per-task deadline (default 600 s)",
    )
    ap.add_argument(
        "--spotcheck",
        type=float,
        default=None,
        help="the M3 spot-check value 0..1 (the S5.3 main/human-judgment seam)",
    )
    ap.add_argument(
        "--results-dir",
        default=None,
        help="override the results dir (default: the bench.yaml results_dir)",
    )
    ap.add_argument(
        "--release-after",
        action="store_true",
        help="H13: after the run writes all rows + the summary, release the "
        "pool tier-2 (bin/bonsai2-unload --all; manager-mediated, S3.2). "
        "Default OFF; a no-op recorded as such under --mock.",
    )
    ap.add_argument(
        "--release-between",
        action="store_true",
        help="H13: at each scenario boundary, release tier-2 (unload --all) "
        "then reload (bonsai2-reload) + warmup, before the next scenario. "
        "Default OFF; a no-op recorded as such under --mock.",
    )
    ap.add_argument(
        "--summary-dir",
        default=None,
        help="the bench-results/ summary dir (default: repo-root bench-results/)",
    )
    args = ap.parse_args(argv)
    try:
        cfg = config.load_bench_config(args.config)
    except config.ConfigError as exc:
        # the S5.8 "invalid scenario id -> exit 2" + the S5.4 "exit 2 =
        # bench config invalid" — the S3.2 code 2, in the closed set.
        print(f"[bench] {exc}", file=sys.stderr)
        return EXIT_CONFIG
    if args.results_dir:
        cfg["results_dir"] = args.results_dir
    cfg["_summary_dir"] = (
        args.summary_dir if args.summary_dir else str(_REPO_ROOT / "bench-results")
    )
    try:
        run_report = run(
            cfg,
            quick=args.quick,
            scenario=args.scenario,
            mock_spec=args.mock,
            mock_cap_spec=args.mock_cap,
            deadline_s=args.deadline_s,
            max_tokens=args.max_tokens,
            spotcheck=args.spotcheck,
            release_after=args.release_after,
            release_between=args.release_between,
        )
    except config.ConfigError as exc:
        print(f"[bench] {exc}", file=sys.stderr)
        return EXIT_CONFIG
    except Exception as exc:  # noqa: BLE001 — an unexpected runner crash
        # An unexpected crash is still exit 0 (the S5.4 closed set: 0 or 2;
        # S5.9 #3 asserts bonsai2-bench stays in the 0/2 subset, so no
        # exit 1). But it is NOT a successful bench run: the "one JSON
        # object on stdout" (S3.1) is written with status="crash" + the
        # named error, so a consumer sees the run did not complete instead
        # of a bare "done".
        print(f"[bench] runner error: {exc.__class__.__name__}: {exc}", file=sys.stderr)
        sys.stdout.write(
            json.dumps(
                {
                    "status": "crash",
                    "error": f"{exc.__class__.__name__}: {exc}",
                }
            )
            + "\n"
        )
        return EXIT_OK
    verdicts = {
        row["scenario"]: row.get("row_verdict", "fail") for row in run_report["rows"]
    }
    # the S3.1 "one JSON object on stdout" convention, adapted to the bench.
    out_doc = {
        "status": "done",
        "n_scenarios": run_report["n_scenarios"],
        "verdicts": verdicts,
        "summary": run_report["summary_path"],
    }
    # H13 (post-live-run): the pool-release records ride the stdout JSON
    # (the run's report dict carries them too; the S3.1 one-object-on-
    # stdout convention is the consumer-facing surface). Only present when
    # the flag was set (default OFF keeps the pre-hotfix shape byte-identical).
    for key in ("release_after", "release_between"):
        if key in run_report:
            out_doc[key] = run_report[key]
    sys.stdout.write(json.dumps(out_doc) + "\n")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
