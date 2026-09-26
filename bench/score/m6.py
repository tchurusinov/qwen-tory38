"""bench.score.m6 — the M6 rubric: "main-only control of M1-M5, same
rubrics (the S5.2 control)".

M6 is NOT a standalone task — it is the CONTROL row: the runner runs the
M1-M5 task shapes against the MAIN endpoint (the S3.6 ``main_endpoint``)
instead of the subagent pool, and scores them with the SAME rubric
modules (``m1``/``m2``/``m3``/``m4``/``m5``). The S5.2 matrix: "M6 |
main-only control of M1-M5 | — | 1 | — | same rubrics; the control
(idea.txt capability comparison)".

This module owns the CONTROL AGGREGATE: given the per-shape verdicts the
runner collected on the main endpoint (a verdict dict per M1-M5), it
produces the M6 control row:

  * ``main_tps`` — the main endpoint's t/s measured on the control load
    (the S5.2 M8 guardrail baseline: M8's main t/s is checked WITHIN
    ``main_guardrail_pct`` of THIS control's t/s). The runner supplies the
    measured value (the S5.4 "main from harness.yaml" half — the control
    measures the main endpoint's t/s directly, not the subagent pool's).
  * ``verdicts`` — the M1-M5 rubric verdicts on the main endpoint (the
    "same rubrics" half — the control proves the main agent clears the
    same task bar the subagents are scored against).
  * ``pass`` — the control row passes when every M1-M5 rubric verdict
    passes AND a positive ``main_tps`` was measured (the control is a
    VALID control when it produced a t/s baseline the M8 guardrail can
    reference).

This module is the control's dispatch (the runner routes M6's M1-M5
sub-tasks through the SAME rubric modules with the main endpoint's
TaskResults, then aggregates here). It does not score a task directly —
the rubric modules do; this aggregates their verdicts + the main t/s.

Stdlib-only: the shared verdict helper.
"""

from __future__ import annotations

from bench.score._common import verdict

# The M1-M5 shapes the control runs (the S5.2 "main-only control of M1-M5").
CONTROL_SHAPES = ("M1", "M2", "M3", "M4", "M5")


def score(payload: dict, root=None) -> dict:
    """Aggregate the M6 control row. See the module docstring for the
    payload shape.

    payload: {
      "main_tps": <float>,          # the main endpoint t/s (M8 baseline)
      "verdicts": {<M1..M5>: <verdict dict>},  # the same-rubric verdicts
    }
    """
    main_tps = payload.get("main_tps")
    if not isinstance(main_tps, (int, float)) or main_tps <= 0:
        return verdict(
            False,
            0.0,
            f"M6: no main endpoint t/s measured (main_tps={main_tps!r}) "
            f"— control has no M8 guardrail baseline",
            {"main_tps": main_tps},
        )
    verdicts = payload.get("verdicts")
    if not isinstance(verdicts, dict):
        return verdict(
            False,
            0.0,
            "M6: no M1-M5 verdicts in control payload",
            {"main_tps": main_tps},
        )
    # "same rubrics" — the control passes when every M1-M5 rubric verdict
    # on the main endpoint passes (the S5.2 "same rubrics; the control").
    all_pass = True
    per_shape = {}
    for shape in CONTROL_SHAPES:
        v = verdicts.get(shape)
        ok = bool(v.get("pass")) if isinstance(v, dict) else False
        per_shape[shape] = ok
        if not ok:
            all_pass = False
    ok = all_pass
    return verdict(
        ok,
        1.0 if ok else 0.0,
        f"M6: main t/s {main_tps:.2f} (M8 guardrail baseline); "
        f"M1-M5 on main {sum(per_shape.values())}/{len(CONTROL_SHAPES)} pass "
        f"({'control valid' if ok else 'control incomplete'})",
        {"main_tps": main_tps, "per_shape": per_shape, "all_pass": all_pass},
    )
