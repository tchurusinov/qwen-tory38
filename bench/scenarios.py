"""bench.scenarios — the S5.2 scenario matrix (locked, v1 = M1-M10).

Data-driven: one entry per scenario id (M1..M10) with the S5.2 matrix
fields — the shape (a human string), the instance/concurrency/ctx columns,
whether the capability layer is on (`--tools on` for M9/M10 only), the
success rubric (the S5.3 scorer name), and how many TASKS the scenario
generates (the "instances"/"concurrency" column read as a task fan-out:
M1=3 tasks, M2=10, M3=6 sections, M4=1 diff, M5=5 QA, M7=8*N, M8=4*N,
M9=1, M10=10).

The S5.2 matrix note: M1-M8 are TEXT-ONLY baselines (`--tools off`) —
they measure the raw inference stack and stay comparable across
engine-pin bumps; M9/M10 measure the capability layer (S3.4) and run in
both `tool_execution` modes. The scenario's `ctx` column is a
RUNNER-ENFORCED token budget (S5.2 "Reproducible"), not a server setting:
the runner keeps each task's input + output within the column's tokens
(prompt shaping + a completion cap) and records the actual tokens used.
"""

from __future__ import annotations

# The S5.2 matrix. `instances` / `concurrency` are either a literal int or
# the string "N" / "8*N" / "4*N" (resolved against the pool size N at run
# time). `tools` is 'on' for the M9/M10 capability rows, 'off' otherwise
# (the S5.2 baseline note). `rubric` is the S5.3 scorer name (see
# bench.score). `tasks` is the task-count column read as a fan-out count
# (M7 "8*N" -> 8*N concurrent completions; M8 "4*N" -> 4*N).
SCENARIOS: dict[str, dict] = {
    "M1": {
        "shape": "single-file code-gen (3 tasks)",
        "instances": 1,
        "concurrency": 1,
        "ctx": 8192,
        "tools": "off",
        "rubric": "M1",
        "tasks": 3,
    },
    "M2": {
        "shape": "test execution batch (10 tasks)",
        "instances": 1,
        "concurrency": 8,
        "ctx": 8192,
        "tools": "off",
        "rubric": "M2",
        "tasks": 10,
    },
    "M3": {
        "shape": "fan-out doc analysis (N=6 sections)",
        "instances": "min(6,avail)",
        "concurrency": "parallel",
        "ctx": 8192,
        "tools": "off",
        "rubric": "M3",
        # a FIXED 6-section fan-out (the S5.2 shape): the document has 6
        # sections, so the task count is always 6; the instances column
        # (min(6,avail)) caps how many run in PARALLEL (one section per
        # instance), not how many sections exist.
        "tasks": 6,
    },
    "M4": {
        "shape": "diff review (1 PR-sized diff)",
        "instances": 1,
        "concurrency": 1,
        "ctx": 32768,
        "tools": "off",
        "rubric": "M4",
        "tasks": 1,
    },
    "M5": {
        "shape": "long-context doc read (60K tokens)",
        "instances": 1,
        "concurrency": 1,
        "ctx": 65536,
        "tools": "off",
        "rubric": "M5",
        "tasks": 1,
    },
    "M6": {
        "shape": "main-only control of M1-M5",
        "instances": 0,
        "concurrency": 1,
        "ctx": 0,
        "tools": "off",
        "rubric": "M6",
        "tasks": 1,
    },
    "M7": {
        "shape": "saturation: all instances, all slots",
        "instances": "N",
        "concurrency": "8*N",
        "ctx": 8192,
        "tools": "off",
        "rubric": "M7",
        "tasks": "8*N",
    },
    "M8": {
        "shape": "contention: main at working load + subagents at working load",
        "instances": "N",
        "concurrency": "4*N",
        "ctx": 8192,
        "tools": "off",
        "rubric": "M8",
        "tasks": "4*N",
    },
    "M9": {
        "shape": "tool-loop spec-chunk (3 functions + a 3-case suite)",
        "instances": 1,
        "concurrency": 1,
        "ctx": 32768,
        "tools": "on",
        "rubric": "M9",
        "tasks": 1,
    },
    "M10": {
        "shape": "tool-loop test batch (10 files, structured report)",
        "instances": 1,
        "concurrency": 8,
        "ctx": 8192,
        "tools": "on",
        "rubric": "M10",
        "tasks": 1,
    },
}

# The text-only baselines (the S5.2 "M1-M8 are TEXT-ONLY baselines" note).
TEXT_ONLY = frozenset({"M1", "M2", "M3", "M4", "M5", "M6", "M7", "M8"})
# The capability-layer rows (the S5.2 "M9/M10 measure the capability layer"
# note; run in both tool_execution modes, mode recorded in the row).
CAPABILITY = frozenset({"M9", "M10"})

VALID_IDS = frozenset(SCENARIOS)


def scenario_ids() -> list[str]:
    """All S5.2 scenario ids in matrix order (M1..M10)."""
    return list(SCENARIOS)


def get(sid: str) -> dict:
    """One scenario row. KeyError when the id is not in the S5.2 matrix
    (the config validator's S5.8 invalid-id case is upstream of this)."""
    if sid not in SCENARIOS:
        raise KeyError(
            f"unknown scenario id: {sid!r} (S5.2 matrix: {sorted(VALID_IDS)})"
        )
    return SCENARIOS[sid]


def resolve_tasks(sid: str, pool_size: int) -> int:
    """The task fan-out for `sid` at a pool size of `pool_size` instances.

    Resolves the "N"-style columns against the live pool: "8*N" -> 8*N,
    "4*N" -> 4*N, "N" -> N. A pool of 0 (M6 main-only) resolves N-style to
    1 (the control runs one task on the main endpoint). A literal int is
    returned as-is. Minimum 1 (a scenario always produces at least one
    task — a 0-task row has nothing to score).
    """
    t = get(sid)["tasks"]
    if isinstance(t, int):
        return max(1, t)
    # the N-style forms
    import re

    # "min(6,avail)" (M3) — checked before the bare-N form: a literal int
    # is returned as-is, everything else is an N-style string.
    m2 = re.fullmatch(r"min\((\d+),\s*avail\)", t)
    if m2:
        return max(1, min(int(m2.group(1)), max(pool_size, 1)))
    m = re.fullmatch(r"(\d*)\**\s*N", t)
    if m is None:
        return 1
    mult = m.group(1)
    mult = int(mult) if mult else 1
    n = max(1, pool_size)  # a 0-pool control still runs one task
    return max(1, mult * n)
