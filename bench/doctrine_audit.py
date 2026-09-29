#!/usr/bin/env python3
"""bench.doctrine_audit — the S4.6 doctrine-vs-reality audit (MAP 5.6).

The S4.6 audit (locked — SPEC S4.6 "Testing", the cross-section invariant
named at the SPEC bottom): the skill's doctrine claims (the S4.2
work-allocation table, the recipes, the prompt templates) must stay in
sync with the bench. This module is the script that enforces it:

  1. **Citation existence** — every S4.2 row / recipe / prompt template
     that carries a bench citation ``[M#]`` cites a scenario that EXISTS
     in ``bench.yaml`` (the S5.7 config, the S5.2 scenario matrix).
     A citation to a scenario the bench does not define is a review
     defect (SPEC S4.1 "a doctrine number without a bench citation is a
     review defect", S4.7 #1 "every S4.2 row cites an existing S5.2
     scenario").
  2. **Win/lose recompute** — for each subagent-side S4.2 row that cites
     an ``[M#]``, recompute the bench verdict from the LATEST bench rows
     for that scenario and count CONSECUTIVE losses (newest-first). The
     S4.4 win rule is self-correcting: "two consecutive bench-confirmed
     losses on a task shape flip that row to main until re-benched."
     A subagent-side row with >= 2 consecutive losses that is NOT backed
     by a documented exception (``claims.json`` ``win_rule.exceptions``)
     is STALE — the table still allocates it to the subagent, but the
     bench says it should have flipped to main. S4.7 #3 is exactly this:
     "two synthetic 'loss' scenarios ... -> the audit flags the stale
     row."
  3. **Output** — a ``doctrine-audit.json`` (per-row status + the
     recomputed win/lose). A failing audit (exit 1) blocks the skill PR
     (S4.6); it is wired into ``scripts/gate.sh`` for that reason.

The machine-readable source of the S4.2 table is
``skills/bonsai2-harness/claims.json`` (the "single source of truth =
claims.json" convention from the Phase 5 implementation — SKILL.md prose
is reviewed against it). The bench scenario set comes from ``bench.yaml``
(``etc/bench.yaml`` the tracked template; install.sh renders the box
copy). The verdict data comes from the S5.2 run rows in the results dir
(``bench-results/`` the committed summary dir; the rows are
``<ts>_<sid>.json``, grouped by the row's INTERNAL ``scenario`` field —
the engine-mode capability rows are named ``_<sid>_engine.json`` but
carry ``scenario: <sid>`` internally, so filename suffixes are not a
reliable grouping key).

Split like the S5.5 gate (``bench/rebench.py``): a PURE core (hermetic —
the S5.8 "golden in/out pairs" convention drives the unit tests) and a
thin I/O edge (``bench.yaml`` / ``claims.json`` / results-dir reads;
stdlib ``json`` + ``pathlib``; ``yaml`` is the S5.4 edge dep, imported
locally at the file-read edge only).

Exit-code contract (the S4.6 "exit 0/1" verdict set, with a distinct
bad-input code the S5.5 gate already established):

  * 0 = audit clean (citations exist + no undocumented-stale row).
  * 1 = audit FAILED (a stale row without a documented exception, OR a
        citation to a scenario that is not in ``bench.yaml``) — the
        "failing audit blocks the skill PR" signal.
  * 2 = bad input (``bench.yaml`` / ``claims.json`` missing or
        unparseable). Absent bench.yaml or claims.json is a hard input
        error (the audit cannot run without its two config sides); an
        ABSENT or EMPTY results dir is NOT bad input — it is "no bench
        data yet" (every cited row is ``no-data``), the audit still runs
        and passes on the citation half (the S5.5 gate's "absent
        baseline -> empty shape, not an error" leniency for DATA, as
        opposed to CONFIG).

Usage:
  python3 bench/doctrine_audit.py check   # no write; gate.sh uses this
  python3 bench/doctrine_audit.py audit   # writes doctrine-audit.json
    [--bench-yaml P] [--claims P] [--results-dir D] [--output P]

The default paths are the tracked repo state (``etc/bench.yaml`` +
``skills/bonsai2-harness/claims.json`` + ``bench-results/``), so the gate
and the committed audit both run on the same inputs.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_CONFIG = 2

# The tracked repo defaults (the gate + the committed audit run on these).
DEFAULT_BENCH_YAML = "etc/bench.yaml"
DEFAULT_CLAIMS = "skills/bonsai2-harness/claims.json"
DEFAULT_RESULTS_DIR = "bench-results"
DEFAULT_OUTPUT = "bench-results/doctrine-audit.json"

# S4.4 win rule: "two consecutive bench-confirmed losses on a task shape
# flip that row to main until re-benched." This is the flip threshold.
FLIP_LOSS_THRESHOLD = 2


class AuditError(Exception):
    """A bad-input / config error -> the CLI's exit-2 path."""


# --------------------------------------------------------------------------
# the pure core (hermetic — the S5.8 golden in/out pairs drive these)
# --------------------------------------------------------------------------
def row_verdict(row: dict) -> str:
    """A row's ``pass``/``fail`` verdict (the S5.2 row verdict)."""
    return "pass" if (row or {}).get("row_verdict") == "pass" else "fail"


def row_timestamp(row: dict) -> str:
    """A row's ``generated_ts`` (the sort key; missing -> empty string so
    it sorts oldest-first, a safe tie-break direction)."""
    ts = (row or {}).get("generated_ts")
    return ts if isinstance(ts, str) else ""


def consecutive_losses(rows: list[dict]) -> int:
    """The CONSECUTIVE-loss streak for one scenario's rows (S4.4 win
    rule). Rows are sorted newest-first by ``generated_ts``; the streak is
    the number of LEADING (newest) ``fail`` rows. A newest ``pass`` ->
    streak 0 (the shape recovered). No rows -> 0 (``no-data``).

    This is the "consecutive bench-confirmed losses" the S4.4 flip rule
    counts: two consecutive losses on a task shape flip it to main. The
    streak is per SCENARIO (the cited ``[M#]``), not per task — a shape
    is benched as one scenario, and the mode variant (``tool_execution``
    host vs engine, S5.2) is a measurement of the same shape, not a
    separate shape, so its rows count in the same streak."""
    if not rows:
        return 0
    ordered = sorted(rows, key=row_timestamp, reverse=True)
    n = 0
    for r in ordered:
        if row_verdict(r) == "fail":
            n += 1
        else:
            break
    return n


def is_subagent_side(allocation: str) -> bool:
    """Is an S4.2 allocation a SUBAGENT-side one (the flip rule's scope)?

    The S4.4 flip rule flips a row TO main after consecutive losses, so
    it only bites a row that is currently allocated subagent-side.
    ``main`` / ``main, always`` are main-side (a loss cannot flip them
    further to main); ``subagent`` / ``fan-out N`` / ``subagent draft ->
    main verifies`` / ``subagent, --tools on`` / ``main or subagent
    --tools on via the python tool`` are subagent-side."""
    a = (allocation or "").lower()
    return ("subagent" in a) or ("fan-out" in a)


def documented_exception(exceptions: list, scenario: str) -> dict | None:
    """The documented win-rule exception for a scenario (if any).

    ``claims.json`` ``win_rule.exceptions`` is a list of ``{scenario,
    shape?, reason}``; a row with >= ``FLIP_LOSS_THRESHOLD`` consecutive
    losses is NOT stale when a documented exception names its scenario
    (the loss is a known rubric/mechanism seam, not an allocation loss —
    the M1 ``m1_nuance`` precedent generalized). Returns the matching
    exception dict, or ``None``."""
    if not isinstance(exceptions, list) or not scenario:
        return None
    for ex in exceptions:
        if isinstance(ex, dict) and ex.get("scenario") == scenario:
            return ex
    return None


def audit_row(
    task_shape: str,
    allocation: str,
    citation: str | None,
    bench_scenarios: set,
    rows_by_scenario: dict,
    exceptions: list,
) -> dict:
    """The win/lose recompute for ONE S4.2 work-allocation row (S4.7 #1
    "the win/lose recompute matches the table" + S4.7 #3 "two synthetic
    loss scenarios -> the audit flags the stale row"). Pure.

    Returns a per-row record:
      * ``citation_exists`` — is the cited ``[M#]`` in ``bench.yaml``?
      * ``latest_verdict`` / ``n_rows`` / ``consecutive_losses`` — the
        bench data for the cited scenario.
      * ``is_subagent_side`` — the flip rule's scope gate.
      * ``documented_exception`` — is a known-seam exception on record?
      * ``status`` — one of:
          - ``no-citation``      (no ``[M#]``; the row is main-by-
                                   construction or provisional; nothing to
                                   recompute — not a failure)
          - ``missing-citation`` (cites a ``[M#]`` not in bench.yaml — a
                                   review defect, FAILS the audit)
          - ``no-data``          (cites a valid ``[M#]`` but no bench row
                                   yet — cannot recompute; not a failure)
          - ``match``            (the bench supports the allocation: a
                                   subagent-side row with < FLIP losses, or
                                   a main-side row with a supporting
                                   citation)
          - ``stale``            (a subagent-side row with >= FLIP
                                   consecutive losses and NO documented
                                   exception — the S4.4 flip says it should
                                   be main now; FAILS the audit)
          - ``documented-exception`` (>= FLIP losses but a known-seam
                                   exception is on record — the loss is a
                                   rubric/mechanism seam, not an
                                   allocation loss; NOT a failure, the
                                   M1/M9/M10 precedent)"""
    rec: dict = {
        "task_shape": task_shape,
        "allocation": allocation,
        "bench": citation,
    }
    if citation is None:
        rec.update(
            {
                "citation_exists": None,
                "latest_verdict": None,
                "n_rows": 0,
                "consecutive_losses": 0,
                "is_subagent_side": is_subagent_side(allocation),
                "documented_exception": False,
                "status": "no-citation",
                "reason": "no [M#] citation (main by construction / provisional)",
            }
        )
        return rec

    exists = citation in bench_scenarios
    rows = rows_by_scenario.get(citation, []) if exists else []
    losses = consecutive_losses(rows)
    sub_side = is_subagent_side(allocation)
    ex = (
        documented_exception(exceptions, citation)
        if losses >= FLIP_LOSS_THRESHOLD
        else None
    )

    rec.update(
        {
            "citation_exists": exists,
            "latest_verdict": row_verdict(rows[-1]) if rows else None,
            "n_rows": len(rows),
            "consecutive_losses": losses,
            "is_subagent_side": sub_side,
            "documented_exception": ex is not None,
        }
    )

    if not exists:
        rec["status"] = "missing-citation"
        rec["reason"] = (
            f"cites [{citation}] which is not in the bench.yaml scenario set {sorted(bench_scenarios)}"
        )
        return rec
    if not rows:
        rec["status"] = "no-data"
        rec["reason"] = (
            f"[{citation}] is a valid scenario but has no bench row yet (cannot recompute)"
        )
        return rec
    if sub_side and losses >= FLIP_LOSS_THRESHOLD:
        if ex is not None:
            rec["status"] = "documented-exception"
            rec["reason"] = ex.get("reason", "(documented win-rule exception)")
        else:
            rec["status"] = "stale"
            rec["reason"] = (
                f"{losses} consecutive losses on [{citation}] (>= {FLIP_LOSS_THRESHOLD}); "
                "the S4.4 win rule says this shape should flip to main, but the "
                "table still allocates it subagent-side and no documented "
                "exception is on record"
            )
        return rec
    rec["status"] = "match"
    if sub_side:
        rec["reason"] = (
            f"[{citation}] supports the subagent-side allocation ({losses} consecutive loss(es), < {FLIP_LOSS_THRESHOLD})"
        )
    else:
        rec["reason"] = (
            f"main-side allocation; [{citation}] is supporting evidence ({losses} consecutive loss(es))"
        )
    return rec


def audit_citation_record(record: dict, bench_scenarios: set) -> dict:
    """The citation-existence check for a RECIPE or PROMPT-TEMPLATE record
    (the S4.1 citation rule applies to recipes and templates, not just the
    S4.2 table — SPEC S4.1 "a recipe without a bench citation is a review
    defect", and the S4.2 tool-loop rows carry the prompt templates).
    Pure. A null citation is ``provisional`` (documented "no S5.2 row yet";
    the T3/browser case) — allowed, not a failure. A non-null citation not
    in bench.yaml is ``missing`` — a failure."""
    citation = record.get("bench")
    out = dict(record)
    out["citation_exists"] = (citation in bench_scenarios) if citation else None
    if citation is None:
        out["citation_status"] = "provisional"
        out["citation_reason"] = record.get(
            "note", "no S5.2 bench row yet (provisional)"
        )
    elif citation in bench_scenarios:
        out["citation_status"] = "ok"
        out["citation_reason"] = f"[{citation}] is a valid bench.yaml scenario"
    else:
        out["citation_status"] = "missing"
        out["citation_reason"] = f"[{citation}] is not in the bench.yaml scenario set"
    return out


def run_audit(
    claims: dict,
    bench_scenarios: set,
    rows_by_scenario: dict,
) -> dict:
    """The full S4.6 doctrine-vs-reality audit. Pure on (claims, bench
    scenarios, rows-by-scenario) — the S5.8 hermetic convention.

    Checks:
      1. every S4.2 work-allocation row / recipe / prompt-template citation
         exists in bench.yaml (citation half).
      2. the subagent-side S4.2 rows' win/lose recompute (the flip half).

    ``pass`` is True iff there is NO ``missing-citation`` record and NO
    ``stale`` row. ``no-data``, ``provisional``, ``match``,
    ``documented-exception``, and main-side rows do not fail the audit."""
    exceptions = (claims.get("win_rule") or {}).get("exceptions", [])
    if not isinstance(exceptions, list):
        exceptions = []

    work = claims.get("work_allocation") or []
    recipes = claims.get("recipes") or []
    templates = claims.get("prompt_templates") or []

    work_records = [
        audit_row(
            (r.get("task_shape") or ""),
            (r.get("allocation") or ""),
            r.get("bench"),
            bench_scenarios,
            rows_by_scenario,
            exceptions,
        )
        for r in work
        if isinstance(r, dict)
    ]
    recipe_records = [
        audit_citation_record(r, bench_scenarios)
        for r in recipes
        if isinstance(r, dict)
    ]
    template_records = [
        audit_citation_record(r, bench_scenarios)
        for r in templates
        if isinstance(r, dict)
    ]

    rows_checked = {}
    for sid in sorted(rows_by_scenario):
        rows = rows_by_scenario[sid]
        rows_checked[sid] = {
            "n_rows": len(rows),
            "latest_verdict": row_verdict(max(rows, key=row_timestamp))
            if rows
            else None,
            "consecutive_losses": consecutive_losses(rows),
        }

    missing_citations = [
        r
        for r in work_records + recipe_records + template_records
        if r.get("citation_exists") is False
    ]
    stale_rows = [r for r in work_records if r.get("status") == "stale"]

    reasons: list[str] = []
    for r in missing_citations:
        ident = r.get("id") or r.get("task_shape") or "(record)"
        reasons.append(f"missing citation: {ident} -> {r.get('citation_reason')}")
    for r in stale_rows:
        reasons.append(f"stale row: {r.get('task_shape')} -> {r.get('reason')}")

    audit_pass = not missing_citations and not stale_rows
    return {
        "spec_ref": "S4.6 (MAP 5.6)",
        "bench_yaml_scenarios": sorted(bench_scenarios),
        "flip_loss_threshold": FLIP_LOSS_THRESHOLD,
        "rows_checked": rows_checked,
        "work_allocation": work_records,
        "recipes": recipe_records,
        "prompt_templates": template_records,
        "missing_citations": missing_citations,
        "stale_rows": stale_rows,
        "pass": audit_pass,
        "reasons": reasons,
    }


# --------------------------------------------------------------------------
# I/O edge (the bench.yaml / claims.json / results-dir reads)
# --------------------------------------------------------------------------
def _parse_bench_yaml_scenarios(text: str) -> set | None:
    """The NARROW stdlib fallback for ``load_bench_scenarios`` when PyYAML
    is unavailable (the S4.6 gate runs on the bare box ``python3``, which
    has no PyYAML — the hermetic suite's interpreter, S6.6 "no extra
    deps"). It is a LINE parser for the ONE shape the tracked
    ``etc/bench.yaml`` template + the install.sh render use for the
    scenario set: a ``scenarios:`` key with a FLOW sequence on the same
    line (``scenarios: [M1, M2, M3]``). It returns the set of scenario
    ids, ``None`` when a ``scenarios:`` line exists but is NOT the
    flow-sequence shape (a parse error the caller names), or ``set()``
    when there is no ``scenarios:`` line at all (an empty scenario set,
    not a parse error). This is deliberately NOT a YAML parser: it
    exists so the S4.6 gate is a one-line ``python3`` call, not a venv
    dance; anything beyond the flow-sequence shape goes back to PyYAML
    when it is present."""
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("scenarios"):
            continue
        # the key + value on one line: "scenarios: [M1, M2, M3]" — a
        # trailing comment after the ] is allowed (as in the tracked
        # template: `scenarios: [M1, ...]   # S5.2 matrix`). A flow
        # sequence MUST start with [ and contain a ]; take the text
        # between the [ and the FIRST ] (the scenario ids; any trailing
        # comment after the ] is dropped with the split).
        _, _, rest = stripped.partition(":")
        inner = rest.strip()
        if not (inner.startswith("[") and "]" in inner):
            return None
        body = inner[1:].split("]", 1)[0]
        items = [tok.strip().strip("\"'") for tok in body.split(",")]
        return {s for s in items if s}
    # no `scenarios:` line at all -> empty scenario set (not a parse error;
    # the audit then flags every citation as missing, the exit-1 case)
    return set()


def load_bench_scenarios(bench_yaml: str | Path) -> set:
    """Read the S5.7 ``bench.yaml`` and return its ``scenarios`` set (the
    S5.2 scenario matrix). A missing / unparseable / non-mapping file is a
    named ``AuditError`` (the CLI's exit-2 path); a present file with no
    ``scenarios`` key -> empty set (the audit then flags every citation as
    missing, the exit-1 "citation to a non-existent scenario" case).

    PyYAML is used when importable (the box / prod-venv interpreter, the
    S5.4 edge dep). When it is absent (the hermetic ``python3`` the gate
    runs on), the narrow stdlib fallback above parses the tracked
    template's flow-sequence ``scenarios:`` line — so the S4.6 gate needs
    no PyYAML install on the box."""
    p = Path(bench_yaml)
    if not p.is_file():
        raise AuditError(f"bench.yaml not found: {p}")
    try:
        text = p.read_text()
    except OSError as exc:
        raise AuditError(f"bench.yaml unreadable: {p}: {exc!r}") from exc
    try:
        import yaml  # PyYAML: the S5.4 edge dep, at the file-read edge only

        data = yaml.safe_load(text)
    except ImportError:
        scen = _parse_bench_yaml_scenarios(text)
        if scen is None:
            raise AuditError(
                f"bench.yaml unparseable without PyYAML and not the "
                f"flow-sequence scenario shape: {p} (install PyYAML or use "
                "the `scenarios: [M1, ...]` template shape)"
            )
        return scen
    except Exception as exc:  # yaml.YAMLError and friends
        raise AuditError(f"bench.yaml unparseable: {p}: {exc!r}") from exc
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise AuditError(f"bench.yaml top level is not a mapping: {p}")
    scen = data.get("scenarios")
    if not isinstance(scen, list):
        return set()
    return {s for s in scen if isinstance(s, str) and s}


def load_claims(claims: str | Path) -> dict:
    """Read ``claims.json`` (the machine-readable S4.2 table + win rule).
    A missing / unparseable / non-object file is a named ``AuditError``
    (the exit-2 path)."""
    p = Path(claims)
    if not p.is_file():
        raise AuditError(f"claims.json not found: {p}")
    try:
        data = json.loads(p.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise AuditError(f"claims.json unreadable: {p}: {exc!r}") from exc
    if not isinstance(data, dict):
        raise AuditError(f"claims.json is not an object: {p}")
    return data


def load_rows_by_scenario(results_dir: str | Path) -> dict:
    """Group the S5.2 run rows by their INTERNAL ``scenario`` field.

    Reads every ``*.json`` in the results dir and groups by
    ``row["scenario"]`` (the authoritative key). The engine-mode
    capability rows are named ``_<sid>_engine.json`` but carry
    ``scenario: <sid>`` internally, so the filename suffix is NOT a
    reliable grouping key (a ``*_<sid>.json`` glob would miss the
    ``_engine`` rows). Files that are not row JSON (the
    ``rebench-baseline.json``, the audit output itself, or a partial
    write — no ``scenario`` field) are skipped. An absent / empty dir ->
    ``{}`` ("no bench data yet"), NOT an error."""
    out: dict = {}
    d = Path(results_dir)
    if not d.is_dir():
        return out
    for p in sorted(d.glob("*.json")):
        if not p.is_file():
            continue
        try:
            row = json.loads(p.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(row, dict):
            continue
        sid = row.get("scenario")
        if not isinstance(sid, str) or not sid:
            continue
        out.setdefault(sid, []).append(row)
    return out


def _ts() -> str:
    import time

    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def write_audit(result: dict, output: str | Path, meta: dict) -> Path:
    """Write ``doctrine-audit.json`` (atomic tmp+rename, the S3.1 write
    convention). ``meta`` carries the input paths (for the record)."""
    p = Path(output)
    p.parent.mkdir(parents=True, exist_ok=True)
    out = {"generated_ts": _ts(), **meta, **result}
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(out, indent=2) + "\n")
    tmp.replace(p)
    return p


# --------------------------------------------------------------------------
# the CLI (the S4.6 audit: a named tool, exit 0/1/2)
# --------------------------------------------------------------------------
def _print(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj) + "\n")


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(
        description="The S4.6 doctrine-vs-reality audit (MAP 5.6; the "
        "S4.2 citation check + the S4.4 win/lose recompute)."
    )
    ap.add_argument(
        "command",
        choices=["check", "audit"],
        help="check = run the audit, no write (gate.sh uses this); "
        "audit = run the audit + write doctrine-audit.json.",
    )
    ap.add_argument("--bench-yaml", default=DEFAULT_BENCH_YAML)
    ap.add_argument("--claims", default=DEFAULT_CLAIMS)
    ap.add_argument("--results-dir", default=DEFAULT_RESULTS_DIR)
    ap.add_argument("--output", default=DEFAULT_OUTPUT)
    args = ap.parse_args(argv)

    try:
        bench_scenarios = load_bench_scenarios(args.bench_yaml)
        claims = load_claims(args.claims)
        rows_by_scenario = load_rows_by_scenario(args.results_dir)
    except AuditError as exc:
        _print({"status": "error", "error": str(exc)})
        return EXIT_CONFIG

    result = run_audit(claims, bench_scenarios, rows_by_scenario)
    meta = {
        "bench_yaml": str(args.bench_yaml),
        "claims": str(args.claims),
        "results_dir": str(args.results_dir),
    }

    if args.command == "audit":
        p = write_audit(result, args.output, meta)
        _print(
            {
                "status": "pass" if result["pass"] else "fail",
                "output": str(p),
                "pass": result["pass"],
                "missing_citations": [
                    (r.get("id") or r.get("task_shape"))
                    for r in result["missing_citations"]
                ],
                "stale_rows": [r.get("task_shape") for r in result["stale_rows"]],
                "reasons": result["reasons"],
            }
        )
    else:  # check
        _print(
            {
                "status": "pass" if result["pass"] else "fail",
                "pass": result["pass"],
                "missing_citations": [
                    (r.get("id") or r.get("task_shape"))
                    for r in result["missing_citations"]
                ],
                "stale_rows": [r.get("task_shape") for r in result["stale_rows"]],
                "reasons": result["reasons"],
            }
        )
    return EXIT_OK if result["pass"] else EXIT_FAILED


if __name__ == "__main__":
    sys.exit(main())
