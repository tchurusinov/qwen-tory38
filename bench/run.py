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

M9/M10 in this phase (MAP 4.1-4.8): their S5.3 rubrics (main-side re-runs)
land in MAP 4.9. Until then a selected M9/M10 row still RUNS its task(s) on
the subagent pool (the capability-layer t/s + box state are recorded) and
carries a named pending verdict (row ``fail``, detail "scorer lands in MAP
4.9") — exit 0 even on scenario-level failures (S5.4).
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import re
import sys
from pathlib import Path

# Make the bench package importable when run as a script (python3 bench/
# run.py) — the bin/bonsai2-bench wrapper does the same path insert for
# the repo-root import.
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from bench import boxstate, config, endpoints, report, scenarios
from bench import exec as bexec
from bench import score as bscore
from bench.rotate import BenchLock
from bench.rotate import rotate as rotate_dir
from bench.score import goldens

EXIT_OK = 0
EXIT_CONFIG = 2

# M9/M10 rows are run-but-pending until MAP 4.9 lands the capability
# scorers (the S5.7 note: M9/M10 are full-bench-only, their rubrics need
# main-side re-runs).
_PENDING_CAPABILITY = ("M9", "M10")
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
            "and pass its own 3-case suite."
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
    if sid in _PENDING_CAPABILITY:
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
            endpoint["port"],
            {"alias": endpoint["alias"], "api_key": endpoint["api_key"]},
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


def _aggregate_tps(tasks) -> dict:
    """The M7/M8 aggregate over task results (the run_concurrent agg
    shape, computed over pre-collected results)."""
    ok = [t for t in tasks if t.ok]
    total_tokens = sum(t.completion_tokens or 0 for t in ok)
    total_elapsed = sum(t.elapsed_ms or 0.0 for t in ok) / 1000.0
    return {
        "n_tasks": len(tasks),
        "n_ok": len(ok),
        "n_errors": len(tasks) - len(ok),
        "total_completion_tokens": total_tokens,
        "total_elapsed_s": total_elapsed,
        "aggregate_tps": total_tokens / total_elapsed if total_elapsed > 0 else 0.0,
    }


# --------------------------------------------------------------------------
# shape scoring (the S5.3 rubric routing, shared by the subagent rows and
# the M6 control — the S5.2 "same rubrics")
# --------------------------------------------------------------------------
def _score_shape(
    sid: str,
    tasks: list,
    cfg: dict,
    spotcheck: float | None = None,
) -> dict:
    """Score ONE scenario's task fan-out with its S5.3 rubric. A shape
    failure is a verdict (pass/score/detail), not an exception (the S5.4
    "the failure is data" rule). M7's zero-5xx half reads the per-task
    error names (the exec's ctx/parse/engine mapping)."""
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
        spot = (
            cfg.get("_spotcheck")
            if cfg.get("_spotcheck") is not None
            else (spotcheck or 0.0)
        )
        return bscore.m3.score({"sections": _m3_sections(tasks), "spotcheck": spot})
    if sid == "M4":
        return bscore.m4.score({"draft": _first_answer(tasks)}, root=root)
    if sid == "M5":
        return bscore.m5.score({"answers": _m5_answers(tasks)}, root=root)
    if sid == "M7":
        return bscore.m7.score({"agg": _aggregate_tps(tasks), "results": tasks})
    return bscore._common.verdict(False, 0.0, f"no rubric for shape {sid!r}")


# --------------------------------------------------------------------------
# the run
# --------------------------------------------------------------------------
def run(
    cfg: dict,
    quick: bool = False,
    scenario: str | None = None,
    mock_spec: str | None = None,
    deadline_s: float = 600.0,
    max_tokens: int | None = None,
    spotcheck: float | None = None,
) -> dict:
    """Run the bench (the S5.4 runner). Returns the run report (dict: the
    per-scenario rows + the summary path + the rotation report). The exit
    code is decided by ``main`` (0 on completion even with row failures,
    2 on bad config / a concurrent bench).

    quick: the S5.5 quick bench (M1+M2+M7).
    scenario: run ONE scenario id (a single-row re-run / the S5.8 dogfood).
    mock_spec: the S5.8 ``module:fn`` canned executor (hermetic).
    spotcheck: the M3 spot-check value (the S5.3 main/human-judgment seam;
    the live main agent reads 1/3 of the sections and supplies this).
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
        engine = endpoints.load_engine_identity()
        harness = endpoints.load_harness_main()
        n_pool, _ = endpoints.subagent_ports(engine)
        rows: list[dict] = []
        m6_baseline: float | None = None
        for sid in _order_for_m6(sids):
            row = _run_scenario(
                sid,
                cfg,
                engine,
                harness,
                n_pool,
                mock,
                deadline_s,
                max_tokens,
                m6_baseline,
            )
            rows.append(row)
            report.write_row(row, results_dir, sid)
            if sid == "M6":
                m6_baseline = row.get("main_tps")
        summary_path = report.write_summary(
            rows,
            cfg.get("_summary_dir", "bench-results"),
            cfg,
            None,
            box_root=_REPO_ROOT,
        )
        return {
            "n_scenarios": len(rows),
            "rows": rows,
            "summary_path": str(summary_path),
            "rotation": rotation_report,
        }
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


def _run_scenario(
    sid: str,
    cfg: dict,
    engine: dict,
    harness: dict,
    n_pool: int,
    mock,
    deadline_s: float,
    max_tokens: int | None,
    m6_baseline: float | None,
) -> dict:
    """Run ONE scenario: resolve endpoints + tasks, execute (the S5.2
    concurrency column), score (the S5.3 rubric), build the row (with the
    S5.2 box-state snapshot). A scenario-level failure is a row verdict
    (fail), not an error (the S5.4 "exit 0 even on scenario-level
    failures" rule)."""
    snap = boxstate.snapshot()
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

    tasks, _agg = bexec.run_concurrent(prompts, conc, exec_prompt)
    if sid in _PENDING_CAPABILITY:
        # MAP 4.9 lands the capability scorers; until then the row runs
        # its task(s) (t/s + box state recorded) with a named pending
        # verdict (the S5.4 "failure is data" rule).
        verdict = bscore._common.verdict(
            False,
            0.0,
            f"{sid}: task ran; rubric scorer lands in MAP 4.9 "
            f"(main-side re-run); row recorded pending",
            {
                "pending": "4.9",
                "n_tasks": len(tasks),
                "n_ok": sum(1 for t in tasks if t.ok),
            },
        )
    else:
        verdict = _score_shape(sid, tasks, cfg)
    row = report.build_row(sid, tasks, verdict, snap, cfg, None, box_root=_REPO_ROOT)
    row["concurrency"] = conc
    row["n_tasks"] = len(tasks)
    row["ctx_cap"] = ctx_cap
    row["tool_execution"] = "on" if sid in scenarios.CAPABILITY else "off"
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
            deadline_s=args.deadline_s,
            max_tokens=args.max_tokens,
            spotcheck=args.spotcheck,
        )
    except config.ConfigError as exc:
        print(f"[bench] {exc}", file=sys.stderr)
        return EXIT_CONFIG
    except Exception as exc:  # noqa: BLE001 — an unexpected runner crash
        # is still exit 0 (the S5.4 closed set: 0 or 2; a crash is not a
        # scenario-level failure but the S3.2 closed set has no generic
        # error code, so 0 + the named detail is the documented behavior).
        print(f"[bench] runner error: {exc.__class__.__name__}: {exc}", file=sys.stderr)
        return EXIT_OK
    verdicts = {
        row["scenario"]: row.get("row_verdict", "fail") for row in run_report["rows"]
    }
    # the S3.1 "one JSON object on stdout" convention, adapted to the bench.
    sys.stdout.write(
        json.dumps(
            {
                "status": "done",
                "n_scenarios": run_report["n_scenarios"],
                "verdicts": verdicts,
                "summary": run_report["summary_path"],
            }
        )
        + "\n"
    )
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
