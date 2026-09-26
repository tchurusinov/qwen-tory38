"""bench.report — the S5.4 reporter: JSON row per scenario + the
``bench-results/<date>_<commit>.md`` summary.

The S5.2/S5.4 reporter:

  * ONE JSON row per scenario, written to ``<results_dir>/<ts>_<scenario>.json``
    (the S5.2 results dir: ``/run/bonsai2/bench/`` at install). The row
    carries: the scenario id + matrix fields, the per-task execution data
    (the ``bench.exec.TaskResult`` fields, serialized), the S5.3 rubric
    verdict (the ``pass``/``score``/``detail`` the scorer returned), the
    S5.2 box-state snapshot (``bench.boxstate.snapshot`` — "every row
    carries the box-state snapshot", S5.9 #1), the run metadata (commit,
    timestamp, config path), and the scenario's ``pass``/``fail`` row
    verdict (the S5.2 "the runner records the verdict per scenario row as
    pass/fail").
  * a ``bench-results/<date>_<commit>.md`` summary (the S5.2 "committed
    summary" — the doctrine's citation source, S4.2): a human table of
    every scenario's verdict + the measured t/s + the box-state snapshot
    fields, so a committed bench run is citable (the S5.6 standard
    workload set + the S4.2 work-allocation table cite these rows).

The reporter is I/O (the JSON write + the markdown write); the verdict
itself is DATA (the S5.4 "exit 0 even on scenario-level failures" rule —
a ``fail`` row is still a written row, not an error). The exit-2
(bad config) case is the loader's (``bench.config``), not the reporter's.

Stdlib-only: ``json`` + ``pathlib`` + ``datetime`` + ``subprocess``
(git commit) — the S5.4 reporter half.
"""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

# The S5.2 results layout.
DEFAULT_RESULTS_DIR = "/run/bonsai2/bench"
DEFAULT_SUMMARY_DIR = "bench-results"


def _git_commit(root: str | Path | None = None) -> str:
    """The repo commit short-hash (the S5.2 reproducible pin). 'unknown'
    when git is absent / not a repo (the snapshot is data, not a verdict)."""
    try:
        p = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
            cwd=str(root) if root else None,
        )
        if p.returncode == 0 and p.stdout.strip():
            return p.stdout.strip()
    except (subprocess.TimeoutExpired, OSError, ValueError):
        pass
    return "unknown"


def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def build_row(
    scenario_id: str,
    tasks: list,
    verdict: dict,
    snapshot: dict,
    cfg: dict,
    commit: str | None = None,
    box_root: str | Path | None = None,
) -> dict:
    """Build ONE scenario's JSON row (the S5.2 run row). Pure on inputs —
    no I/O (the caller writes it).

    tasks: the list of ``bench.exec.TaskResult`` for this scenario's task
    fan-out. verdict: the S5.3 rubric verdict dict. snapshot: the S5.2
    box-state snapshot. cfg: the loaded bench config (the S5.7 shape).
    """
    commit = commit or _git_commit(box_root)
    task_dicts = []
    for t in tasks or []:
        task_dicts.append(
            {
                "ok": t.ok,
                "answer": t.answer,
                "reasoning": t.reasoning,
                "usage": t.usage,
                "prompt_tokens": t.prompt_tokens,
                "completion_tokens": t.completion_tokens,
                "time_to_first_token_ms": t.time_to_first_token_ms,
                "elapsed_ms": t.elapsed_ms,
                "n_content_chunks": t.n_content_chunks,
                "decode_tps": t.decode_tps,
                "error": t.error,
                "detail": t.detail,
            }
        )
    return {
        "scenario": scenario_id,
        "generated_ts": _ts(),
        "commit": commit,
        "config_path": cfg.get("config_path"),
        "repetitions": cfg.get("repetitions"),
        "tasks": task_dicts,
        "verdict": verdict,
        "row_verdict": "pass" if verdict.get("pass") else "fail",
        "box_state": snapshot,
    }


def write_row(
    row: dict,
    results_dir: str | Path,
    scenario_id: str | None = None,
) -> Path:
    """Write ONE scenario's JSON row to ``<results_dir>/<ts>_<scenario>.json``
    (atomic tmp+rename, the S3.1 write convention). Returns the path. The
    S5.9 #1 contract: "every row carries the box-state snapshot" — the row's
    ``box_state`` field is asserted by the S5.8 dogfood test."""
    results_dir = Path(results_dir)
    results_dir.mkdir(parents=True, exist_ok=True)
    sid = scenario_id or row.get("scenario", "scenario")
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    p = results_dir / f"{ts}_{sid}.json"
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(row, indent=2) + "\n")
    tmp.replace(p)
    return p


def write_summary(
    rows: list[dict],
    summary_dir: str | Path,
    cfg: dict,
    commit: str | None = None,
    box_root: str | Path | None = None,
) -> Path:
    """Write the ``bench-results/<date>_<commit>.md`` summary (the S5.2
    "committed summary — the doctrine's citation source"). A human table
    of every scenario's row verdict + measured t/s + box-state snapshot.
    Returns the path (in ``summary_dir``). The S5.9 #1 "committed
    bench-results/" deliverable."""
    summary_dir = Path(summary_dir)
    summary_dir.mkdir(parents=True, exist_ok=True)
    commit = commit or _git_commit(box_root)
    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    p = summary_dir / f"{date}_{commit}.md"
    lines = [
        "# qwen-tory38 bench summary",
        "",
        f"- commit: `{commit}`",
        f"- date: {date} (UTC)",
        f"- config: `{cfg.get('config_path')}`",
        f"- scenarios: {len(rows)}",
        "",
        "| scenario | verdict | score | detail | main/sub t/s | driver | mem-available |",
        "|---|---|---|---|---|---|---|",
    ]
    for row in rows:
        verdict = row.get("verdict", {})
        # the measured t/s: prefer the aggregate (M7/M8), else the max task
        # decode_tps (the single-task scenarios).
        tps = None
        agg_tps = _aggregate_tps(row)
        if agg_tps is not None:
            tps = f"{agg_tps:.2f} (agg)"
        else:
            task_tps = [
                t.get("decode_tps") for t in row.get("tasks", []) if t.get("decode_tps")
            ]
            if task_tps:
                tps = f"{max(task_tps):.2f}"
        snap = row.get("box_state", {})
        lines.append(
            f"| {row.get('scenario')} | {row.get('row_verdict')} | "
            f"{verdict.get('score', 0.0):.2f} | "
            f"{_cell(verdict.get('detail', ''))} | {tps or 'n/a'} | "
            f"{snap.get('driver', 'n/a')} | {_mem_avail(snap)} |"
        )
    lines.append("")
    lines.append("---")
    lines.append(
        "Source: `bench/run.py` (S5.4). Box-state snapshot per S5.2 "
        "(nvidia-smi + docker stats + /proc/meminfo + date). Rows: "
        "`/run/bonsai2/bench/<ts>_<scenario>.json`."
    )
    p.write_text("\n".join(lines) + "\n")
    return p


def _aggregate_tps(row: dict) -> float | None:
    """The M7/M8 aggregate t/s if the row carried one (else None). The
    reporter does not recompute — the runner records the aggregate in the
    row's verdict extra or a top-level field; this reads it if present."""
    v = row.get("verdict", {})
    extra = v.get("extra") if isinstance(v, dict) else None
    if isinstance(extra, dict) and isinstance(extra.get("aggregate_tps"), (int, float)):
        return float(extra["aggregate_tps"])
    if isinstance(row.get("aggregate_tps"), (int, float)):
        return float(row["aggregate_tps"])
    return None


def _mem_avail(snap: dict) -> str:
    """The MemAvailable field of the box-state snapshot (the S5.2 pool
    physics the T5 doctrine names). 'n/a' when the snapshot lacks it."""
    mi = snap.get("meminfo", "")
    if "MemAvailable=" in mi:
        return mi.split("MemAvailable=", 1)[1].strip()
    return "n/a"


def _cell(s: str) -> str:
    """Trim a detail string to a table cell (one line, <= 80 chars, no
    pipes)."""
    s = (s or "").replace("|", "\\|").replace("\n", " ")
    return s if len(s) <= 80 else s[:77] + "..."
