"""bench.score.m8 — the M8 rubric: "main t/s guardrail: within
``main_guardrail_pct`` of M6 (auto)".

The S5.2 M8 row: "contention: main at working load + subagents at working
load ... main t/s guardrail: within ``main_tps_guardrail_pct`` of M6
(auto). The runner records the verdict per scenario row as pass/fail; a
guardrail breach flips the row to fail while the run still exits 0 (S5.4)".

The guardrail: the main endpoint's t/s measured AT FULL SUBAGENT LOAD
(M8) must stay within ``main_guardrail_pct`` of the main endpoint's t/s
measured UNLOADED (M6, the control). The S5.2/S0 guardrail semantics: the
main stack is external infrastructure the pool consumes but never modifies,
so the pool's load must not regress the main t/s by more than the guardrail
(default 5%, ``main_tps_guardrail_pct`` in harness.yaml / bench.yaml).

Input (the runner's contention payload for one M8 task):
  {
    "m6_tps": <float>,            # the M6 main-only control t/s
    "m8_main_tps": <float>,       # the main t/s measured under M8 load
    "main_guardrail_pct": <float>,# the guardrail threshold (default 5)
  }

Scoring (auto, binary — S5.3 "machine-scored"):
  * ``pct_drop`` = (m6_tps - m8_main_tps) / m6_tps * 100 (the main t/s
    regression under load).
  * ``pass`` = ``pct_drop <= main_guardrail_pct`` AND ``m8_main_tps >= 0``
    (a non-negative main t/s — the main endpoint is still serving).
  * ``score`` = 1.0 - min(pct_drop / guardrail, 1.0) (the S5.3 rubric
    score, [0,1]: a main t/s AT the M6 control scores 1.0; at the guardrail
    edge scores 0.0; beyond it stays 0.0).
  * a missing/zero M6 control (m6_tps <= 0) is a fail with a named detail
    (the guardrail has no baseline — the control did not run or stalled).

The hermetic S5.8 golden in/out pair: m8_main_tps == m6_tps -> pass
(0% drop, within 5%); m8_main_tps = 0.90 * m6_tps -> fail (10% drop,
beyond the 5% guardrail).

Stdlib-only: the shared verdict helper.
"""

from __future__ import annotations

from bench.score._common import verdict

DEFAULT_GUARDRAIL_PCT = 5.0


def score(payload: dict, root=None) -> dict:
    """Score one M8 contention payload. See the module docstring for the
    payload shape. The verdict is the S5.2 row verdict (pass/fail) the
    runner records in the run JSON."""
    m6_tps = payload.get("m6_tps")
    m8_main_tps = payload.get("m8_main_tps")
    guardrail = payload.get("main_guardrail_pct", DEFAULT_GUARDRAIL_PCT)
    if not isinstance(m6_tps, (int, float)) or m6_tps <= 0:
        return verdict(
            False,
            0.0,
            f"M8: no M6 control baseline (m6_tps={m6_tps!r}) — guardrail has no baseline",
            {"m6_tps": m6_tps, "m8_main_tps": m8_main_tps},
        )
    if not isinstance(m8_main_tps, (int, float)):
        return verdict(
            False,
            0.0,
            f"M8: m8_main_tps missing/not a number: {m8_main_tps!r}",
            {"m6_tps": m6_tps, "m8_main_tps": m8_main_tps},
        )
    try:
        guardrail = float(guardrail)
    except (TypeError, ValueError):
        guardrail = DEFAULT_GUARDRAIL_PCT
    if guardrail <= 0 or guardrail > 100:
        guardrail = DEFAULT_GUARDRAIL_PCT
    if m8_main_tps < 0:
        m8_main_tps = 0.0
    pct_drop = (m6_tps - m8_main_tps) / m6_tps * 100.0
    within = pct_drop <= guardrail
    # score: 1.0 at the control, linear to 0.0 at the guardrail edge, 0.0
    # beyond (the S5.3 rubric score shape; the pass/fail verdict is binary
    # at the edge, the score is a continuous proximity measure).
    score_val = 1.0 - min(pct_drop / guardrail, 1.0)
    return verdict(
        within,
        score_val,
        f"M8: main t/s {m8_main_tps:.2f} vs M6 control {m6_tps:.2f} "
        f"({pct_drop:.1f}% drop, guardrail {guardrail:.0f}%): "
        f"{'pass' if within else 'FAIL — guardrail breach'}",
        {
            "m6_tps": m6_tps,
            "m8_main_tps": m8_main_tps,
            "pct_drop": round(pct_drop, 4),
            "main_guardrail_pct": guardrail,
            "verdict": "pass" if within else "fail",
        },
    )
