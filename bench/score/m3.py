"""bench.score.m3 — the M3 rubric (S5.3 semi-auto): "each section: summary
<= 300 tokens + 3 named claims each; main verifies 1/3 by spot-check
(rubric: claim accuracy >= 2/3); score = auto*0.7 + spotcheck*0.3".

The M3 scenario is "fan-out doc analysis (N=6 sections)": a subagent reads
a document and produces a summary + 3 named claims per section. The S5.3
M3 rubric is SEMI-AUTO:

  * AUTO half (machine-scored, 0.7 weight): the SHAPE of each section —
    the summary is <= 300 tokens AND there are exactly 3 named claims. A
    section that violates the shape scores 0 for the auto half; a section
    that meets it scores 1. The auto score = the fraction of sections with
    the correct shape.
  * SPOT-CHECK half (the main/human-judgment seam, 0.3 weight): the main
    verifies 1/3 of the sections' claim accuracy. The runner supplies the
    spot-check result (the main reads the 1/3 sections and judges whether
    the claims are accurate against the source): a 0..1 accuracy value.
    The S5.2 rubric threshold is "claim accuracy >= 2/3" — the spot-check
    passes when the supplied accuracy >= 2/3.

  * ``score`` = ``auto * 0.7 + spotcheck * 0.3`` (the S5.3 blend).
  * ``pass`` = ``score >= 0.5`` (a composite rubric: the blend is the
    verdict; 0.5 is the pass threshold the S5.2 "main verifies" seam
    implies — a section that is shape-correct but has weak claims, or
    weak shape but strong claims, is below the bar).

The spot-check value is SUPPLIED by the runner (the main's judgment — the
S5.3 "M3's spot-check is the human/main-judgment seam"; an LLM-judge mode
is a bench v2 item, parked). The hermetic S5.8 golden in/out pair: all
6 sections shape-correct + spot-check 1.0 -> pass; a mix -> the blend.

Stdlib-only: the shared verdict helper.
"""

from __future__ import annotations

from bench.score._common import verdict

# The S5.2 M3 shape bounds.
MAX_SUMMARY_TOKENS = 300
N_CLAIMS = 3
# The S5.3 M3 blend weights.
AUTO_WEIGHT = 0.7
SPOTCHECK_WEIGHT = 0.3
# The S5.2 "claim accuracy >= 2/3" spot-check threshold.
SPOTCHECK_THRESHOLD = 2.0 / 3.0


def _estimate_tokens(text: str) -> int:
    """A token estimate (the S5.2 "summary <= 300 tokens" shape check).
    The taskworker's convention: len/4 (a token ~ 4 chars for the model's
    output). Good enough for the shape bound (the bound is a 300-token
    ceiling, not a precision measurement)."""
    return max(1, len(text or "") // 4)


def _section_shape_ok(section: dict) -> bool:
    """The S5.2 M3 auto-shape check for one section: summary <= 300 tokens
    AND exactly 3 named claims."""
    if not isinstance(section, dict):
        return False
    summary = section.get("summary")
    if not isinstance(summary, str):
        return False
    if _estimate_tokens(summary) > MAX_SUMMARY_TOKENS:
        return False
    claims = section.get("claims")
    if not isinstance(claims, list):
        return False
    if len(claims) != N_CLAIMS:
        return False
    # each claim must be a non-empty string (a "named claim")
    for c in claims:
        if not isinstance(c, str) or not c.strip():
            return False
    return True


def score(payload: dict, root=None) -> dict:
    """Score one M3 fan-out payload. See the module docstring for the
    payload shape.

    payload: {"sections": [<{summary, claims}>...], "spotcheck": <0..1>}
    """
    sections = payload.get("sections")
    if not isinstance(sections, list) or not sections:
        return verdict(False, 0.0, "M3: no sections in payload")
    n = len(sections)
    shape_ok = sum(1 for s in sections if _section_shape_ok(s))
    auto = shape_ok / n
    spot = payload.get("spotcheck")
    if not isinstance(spot, (int, float)):
        spot = 0.0
    spot = min(1.0, max(0.0, float(spot)))
    blend = auto * AUTO_WEIGHT + spot * SPOTCHECK_WEIGHT
    # pass: the blend is above 0.5 AND the spot-check clears the S5.2
    # 2/3 threshold (a weak spot-check can't be rescued by shape alone).
    ok = blend >= 0.5 and spot >= SPOTCHECK_THRESHOLD
    return verdict(
        ok,
        blend,
        f"M3: auto shape {shape_ok}/{n} ({auto:.2f}), spot-check {spot:.2f} "
        f"(threshold {SPOTCHECK_THRESHOLD:.2f}), blend {blend:.2f} "
        f"({'pass' if ok else 'fail'})",
        {
            "auto": round(auto, 4),
            "spotcheck": spot,
            "blend": round(blend, 4),
            "n_sections": n,
            "n_shape_ok": shape_ok,
            "auto_weight": AUTO_WEIGHT,
            "spotcheck_weight": SPOTCHECK_WEIGHT,
            "spotcheck_threshold": SPOTCHECK_THRESHOLD,
        },
    )
