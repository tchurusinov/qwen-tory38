"""bench.score._common — the shared S5.3 verdict shape.

Every scorer's ``score(...)`` returns a verdict dict of this fixed shape
(the S5.2 row verdict + the S5.3 rubric score):

  {
    "pass": bool,     # the S5.2 row verdict (a guardrail breach / rubric
                      #   miss flips the row to 'fail' while the run still
                      #   exits 0 — S5.4)
    "score": float,   # the S5.3 rubric score, [0, 1]
    "detail": str,    # the human reason (data, not a verdict)
    "extra": dict,    # rubric-specific measurements (e.g. n_found, n_pass)
  }

The helper ``verdict()`` builds it with a clamped score, so no scorer can
emit a score outside [0, 1] or a non-bool pass.
"""

from __future__ import annotations

from typing import Any


def verdict(
    ok: bool,
    score: float,
    detail: str,
    extra: dict[str, Any] | None = None,
) -> dict:
    """Build a fixed-shape S5.3 verdict. ``score`` is clamped to [0, 1];
    ``pass`` is the coerced bool; ``extra`` defaults to {}."""
    try:
        s = float(score)
    except (TypeError, ValueError):
        s = 0.0
    if s < 0.0:
        s = 0.0
    elif s > 1.0:
        s = 1.0
    return {
        "pass": bool(ok),
        "score": s,
        "detail": str(detail),
        "extra": dict(extra or {}),
    }
