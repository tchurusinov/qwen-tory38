"""bench.score.m7 — the M7 rubric: "t/s at the ceiling + zero 5xx (auto)".

Input (the runner's saturation payload for one M7 task, the
``bench.exec.run_concurrent`` aggregate):
  {"agg": <dict from run_concurrent>, "results": [<TaskResult>, ...]}
  * ``agg`` — the concurrency aggregate: ``n_ok``, ``n_errors``,
    ``total_completion_tokens``, ``total_elapsed_s`` (the SERIAL sum —
    the decode-throughput invariant), ``total_wall_s`` (the batch
    wall-clock), and the t/s figures. The S5.1 "t/s at the ceiling" is
    the WALL figure (the 2026-09-27 post-live-run amendment, H9 / S5.5:
    the live first run's 0.25 was the serial sum 370/1484 s, not the
    ~2.3 batch wall 370/161 s — the ceiling is a wall-clock saturation
    figure). ``aggregate_tps_wall`` is the headline; ``aggregate_tps``
    aliases it; ``aggregate_tps_serial`` is recorded for the
    decode-throughput comparison.
  * ``results`` — the per-task results (the 5xx/429 count the "zero 5xx"
    half checks: any task whose error is an engine/5xx-class failure
    counts; a ctx/parse task failure is NOT a 5xx — it is the request
    shape, not the engine).

Scoring (auto, binary — S5.3 "machine-scored"):
  * ``zero_5xx`` = no task failed with an engine/5xx-class error
    (``results`` all ok, or the failures are ctx/parse only).
  * ``pass`` = ``zero_5xx`` AND the aggregate produced a positive t/s
    (the run measured a ceiling, not a stall). The t/s VALUE is recorded
    in ``extra`` (data, not a verdict — the S5.2 "t/s at the ceiling" is
    measured, not thresholded, in v1; the regression threshold is the
    S5.5 re-bench policy, not this rubric).
  * ``score`` = 1.0 when pass, 0.0 otherwise.

The hermetic S5.8 golden in/out pair: an aggregate with 0 errors + a
positive t/s -> pass; an aggregate with one 5xx task -> fail.

Stdlib-only: the shared verdict helper.
"""

from __future__ import annotations

from bench.score._common import verdict

# The error classes that count as a 5xx for the M7 "zero 5xx" half. The
# executor maps 5xx/429/timeout/connection to error="engine"; a 400 is
# error="ctx"; a non-JSON stream is error="parse".
_5XX_ERRORS = frozenset({"engine"})


def _n_5xx(results) -> int:
    n = 0
    for r in results or []:
        err = getattr(r, "error", None)
        if not getattr(r, "ok", True) and err in _5XX_ERRORS:
            n += 1
    return n


def score(payload: dict, root=None) -> dict:
    """Score one M7 saturation payload. See the module docstring for the
    payload shape."""
    agg = payload.get("agg")
    results = payload.get("results") or []
    if not isinstance(agg, dict):
        return verdict(False, 0.0, "M7: no concurrency aggregate in payload")
    # H9 (post-live-run): the headline "t/s at the ceiling" is the BATCH
    # WALL figure. Prefer the explicit wall key; fall back to the legacy
    # ``aggregate_tps`` (which pre-amendment was the serial sum — recorded
    # in the run JSON that predates the amendment).
    tps = agg.get("aggregate_tps_wall")
    if not isinstance(tps, (int, float)):
        tps = agg.get("aggregate_tps")
    tps = tps if isinstance(tps, (int, float)) else 0.0
    tps_serial = agg.get("aggregate_tps_serial")
    tps_serial = tps_serial if isinstance(tps_serial, (int, float)) else None
    n_errors = agg.get("n_errors", 0)
    # cross-check the 5xx count two ways: the agg's n_errors and the
    # results' engine-class failures — they should agree; use the results
    # (the ground truth the per-task error names carry).
    n_5xx = _n_5xx(results)
    zero_5xx = n_5xx == 0
    produced = tps > 0
    ok = zero_5xx and produced
    return verdict(
        ok,
        1.0 if ok else 0.0,
        f"M7: t/s at ceiling {tps:.2f}, {'zero' if zero_5xx else f'{n_5xx}'} 5xx "
        f"({'pass' if ok else 'fail'})",
        {
            "aggregate_tps": tps,
            "aggregate_tps_wall": tps,
            "aggregate_tps_serial": tps_serial,
            "n_5xx": n_5xx,
            "n_errors_agg": n_errors,
            "n_tasks": agg.get("n_tasks", 0),
            "n_ok": agg.get("n_ok", 0),
            "total_completion_tokens": agg.get("total_completion_tokens", 0),
            "total_wall_s": agg.get("total_wall_s"),
        },
    )
