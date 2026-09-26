"""bench — the qwen-tory38 bench framework (SPEC S5, MAP Phase 4).

Repo-source only (MAP 0.1): runtime results go to ``/run/bonsai2/bench/``
and the committed summary to ``bench-results/``. The runner entry point is
``bench/run.py`` (SPEC S5.4); the thin S3.2 wrapper is ``bin/bonsai2-bench``
(MAP 4.7). Submodules are the S5.4 runner components:

  * ``config``    — bench.yaml loader + schema validation (MAP 4.1, S5.7/S5.8)
  * ``scenarios`` — the S5.2 scenario matrix (task shapes + rubric refs)
  * ``endpoints`` — endpoint resolver (subagent ports / main) (S5.4)
  * ``exec``      — the task executor (streaming chat + token timing) (S5.4)
  * ``boxstate``  — the S5.2 box-state snapshot (stdlib subprocess + /proc)
  * ``rotate``    — the S5.2 7-day rotation + 1 GB cap + flock (S3.5)
  * ``report``    — the reporter (JSON row + markdown summary) (S5.4/S5.2)
  * ``score``     — the per-rubric scorer plugins (S5.3/S5.4) + golden loader

The dependency surface is stdlib + ``requests`` (the S6.6 pin; the S5.4
runner names ``requests`` as its sole non-stdlib dep) plus PyYAML at the
config-read edge (the box dep the S3.6/S1.6 tooling shares).
"""

__all__ = [
    "boxstate",
    "config",
    "endpoints",
    "exec",
    "report",
    "rotate",
    "scenarios",
    "score",
]
