#!/usr/bin/env python3
"""bench.rebench — the S5.5 re-bench policy (MAP 4.11).

The S5.5 policy (locked — feeds the S6.5 upgrade procedure):

  * **Quick bench (M1+M2+M7, ``--quick``)** — run on every engine-pin bump,
    every model re-download with a changed SHA, and weekly (optional cron).
    A regression of ``> regression_tps_pct`` t/s OR any auto-rubric
    regression BLOCKS the PR that bumped the pin until it is re-resolved.
  * **Full bench (M1-M10)** — due on a model revision change, a quant
    fallback switch, or an S4.2 row flip; its output feeds the doctrine
    audit (S4.6) in the same PR.

This module owns the S5.5 policy, split into a PURE core (hermetic — the
S5.8 "rubric tests are golden in/out pairs" convention) and a thin I/O
edge (the baseline JSON read/write; stdlib ``json`` + ``pathlib``, no
yaml, no requests — the S5.4 runner's dep surface):

  * the **engine identity** (``engine_identity``): the engine pin commit +
    the model revision + the model SHA + the active quant file — the key a
    pin-bump / re-download / quant-switch CHANGES. Pure on the loaded
    ``subagent-engine.yaml`` dict.
  * the **baseline** (the last ACCEPTED quick-bench run): per-scenario
    verdict + t/s + the engine identity, read/written as JSON (the S5.7
    ``rebench_baseline`` path, default ``bench-results/rebench-baseline.json``).
  * the **quick-bench regression gate** (``quick_gate``): given the baseline
    + the current quick-bench rows -> blocked? (a ``> regression_tps_pct``
    M7 t/s drop, or a quick auto-rubric that passed in the baseline now
    failing). This is the "blocks the pin-bump PR" half.
  * the **full-bench due-detection** (``full_bench_due``): is a full bench
    due? (a model revision change, a quant fallback switch, or an S4.2 row
    flip — the cases the quick gate does NOT cover).

Exit-code contract (the S5.5 gate, a named tool distinct from the S5.4
bench runner's 0/2 closed set — the runner is the bench; the re-bench gate
is a gate that can BLOCK a PR, so a distinct "blocked/due" exit is the
signal the pin-bump PR flow asserts on):

  * ``gate``    — 0 = no regression (the PR is unblocked) OR not-comparable
                  (the named H9 status: the M7 t/s comparison is recorded
                  "not comparable", NOT silently skipped — a rubric
                  regression still blocks), 2 = bad config / input,
                  3 = regression found (the PR is BLOCKED).
  * ``due``     — 0 = full bench not due, 2 = bad config, 3 = full bench DUE.
  * ``save-baseline`` — 0 = baseline written, 2 = bad config / input.

Usage:
  python3 bench/rebench.py gate <results-dir> [--baseline PATH] [--config P]
  python3 bench/rebench.py due [--baseline PATH] [--config P] [--s42-flip]
  python3 bench/rebench.py save-baseline <results-dir> [--baseline PATH] [--config P]

The ``<results-dir>`` is the S5.2 results dir of a just-completed ``--quick``
run (the ``bench/run.py --quick`` output rows, ``<ts>_<sid>.json``); the gate
reads the M1+M2+M7 rows from it (the quick-bench scenario set, S5.5).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

EXIT_OK = 0
EXIT_CONFIG = 2
EXIT_BLOCKED = 3

# The S5.5 quick-bench scenario set (the S5.5 "quick bench (M1+M2+M7)").
QUICK_SCENARIOS = ("M1", "M2", "M7")
# The M7 throughput scenario's t/s lives in its rubric verdict extra (the
# bench/score/m7.py "aggregate_tps" — the M7 "t/s at the ceiling" metric).
_M7 = "M7"

# The default baseline path (repo-root bench-results/; the S5.7
# ``rebench_baseline`` config key overrides this — the MAP 4.11 "wired into
# bench/config.py defaults").
DEFAULT_BASELINE = "bench-results/rebench-baseline.json"


# --------------------------------------------------------------------------
# the engine identity (the key a pin-bump / re-download / quant-switch changes)
# --------------------------------------------------------------------------
def engine_identity(engine: dict) -> dict:
    """The S5.5 engine-identity key from a loaded ``subagent-engine.yaml``
    dict: the engine pin commit + the model revision + the model SHA + the
    ACTIVE quant file (``model.file``) + the fallback SHA. A pin-bump PR
    changes ``engine_commit``; a model re-download changes ``model_sha``;
    a quant fallback switch changes ``quant`` (the active ``model.file``)
    — the S5.5 "revision/quant change or re-download" cases. Pure on the
    dict (no I/O — the caller loads the yaml; the S5.4 resolver's
    convention). Missing fields default to ``None`` (the gate treats a
    missing field as "unknown", not a change)."""
    if not isinstance(engine, dict):
        engine = {}
    eng = engine.get("engine", {})
    model = engine.get("model", {})
    img = engine.get("image", {})

    def _commit(d: dict) -> str | None:
        v = d.get("commit")
        return str(v) if isinstance(v, str) and v else None

    def _sha(d: dict) -> str | None:
        v = d.get("sha256")
        return str(v) if isinstance(v, str) and v else None

    def _file(d: dict, key: str) -> str | None:
        v = d.get(key)
        return str(v) if isinstance(v, str) and v else None

    return {
        "engine_commit": _commit(eng.get("pin", {})) if isinstance(eng, dict) else None,
        "model_revision": _file(model, "revision"),
        "model_sha": _sha(model),
        "quant": _file(model, "file"),
        "quant_fallback": _file(model, "fallback_file"),
        "image_sha": _sha(img.get("pin", {})) if isinstance(img, dict) else None,
    }


# --------------------------------------------------------------------------
# row extraction (the S5.2 run rows -> the gate's (verdict, tps) pairs)
# --------------------------------------------------------------------------
def row_verdict(row: dict) -> str:
    """A row's ``pass``/``fail`` verdict (the S5.2 row verdict)."""
    return "pass" if row.get("row_verdict") == "pass" else "fail"


def row_tps(row: dict) -> float | None:
    """A row's measured t/s: the M7 rubric's ``aggregate_tps`` (the S5.2
    t/s column). ``None`` when the row carries no t/s (non-M7 rows, or an
    M7 that produced no aggregate — data, not a verdict)."""
    extra = (row.get("verdict") or {}).get("extra")
    if isinstance(extra, dict) and isinstance(extra.get("aggregate_tps"), (int, float)):
        return float(extra["aggregate_tps"])
    return None


def row_comparability(row: dict) -> dict | None:
    """The M7 row's COMPARABILITY triple (the 2026-09-27 post-live-run
    amendment, H9 / decision §0.2): ``{n_tasks, concurrency, N}`` —
    the load the bench applied. The S5.5 baseline row carries the same
    triple; the gate compares LIKE-WITH-LIKE (a mismatch -> "not
    comparable", named, not a silent compare: the engine's ``--parallel
    8`` cap is constant, but the load varies per M — M2 = 8-wide,
    M7 = 16, M8 = 8 — so comparability is a RECORDED property). Pre-
    amendment rows carry no triple -> None (the gate compares on t/s
    alone, the legacy behavior)."""
    c = row.get("comparability")
    if isinstance(c, dict):
        return {
            "n_tasks": c.get("n_tasks"),
            "concurrency": c.get("concurrency"),
            "N": c.get("N"),
        }
    return None


def rows_summary(rows: list[dict]) -> dict:
    """The gate's per-scenario summary from a run's rows (a list of S5.2
    run-row dicts): ``{sid: {"verdict": str, "tps": float|None, "score":
    float}}``. Duplicates keep the LAST row (a re-run's row is the newest).
    Pure on the rows (no I/O)."""
    out: dict = {}
    for row in rows:
        sid = row.get("scenario")
        if not isinstance(sid, str) or not sid:
            continue
        verdict = row.get("verdict") or {}
        out[sid] = {
            "verdict": row_verdict(row),
            "score": float(verdict.get("score") or 0.0),
            "tps": row_tps(row),
            "comparability": row_comparability(row),
        }
    return out


# --------------------------------------------------------------------------
# the baseline (the last accepted quick-bench run)
# --------------------------------------------------------------------------
def baseline_shape() -> dict:
    """An empty baseline (the S5.5 shape): the engine identity + the
    per-scenario summary. ``load_baseline`` returns this (empty) when the
    file is absent — the first quick run has no baseline to regress from."""
    return {
        "engine": None,
        "generated_ts": None,
        "commit": None,
        "scenarios": {},
    }


def save_baseline(
    path: str | Path, engine: dict, rows: list[dict], commit: str | None = None
) -> Path:
    """Write the baseline (the last ACCEPTED quick-bench run): the engine
    identity + the per-scenario summary + the capture metadata. Atomic
    tmp+rename (the S3.1 write convention). The caller is responsible for
    the "accepted" precondition (the gate passed, or the user chose to
    baseline a known-good run)."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "engine": engine_identity(engine),
        "generated_ts": _ts(),
        "commit": commit,
        "scenarios": rows_summary(rows),
    }
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    tmp.replace(p)
    return p


def load_baseline(path: str | Path) -> dict:
    """Load the baseline. An absent file returns the empty shape (the first
    run has nothing to regress from — the gate then gates on the t/s drop
    alone, with no baseline to compare a rubric flip against). A present
    but malformed file raises ``RebenchError`` (the caller maps to exit 2)."""
    p = Path(path)
    if not p.is_file():
        return baseline_shape()
    try:
        data = json.loads(p.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise RebenchError(f"baseline unreadable: {p}: {exc!r}") from exc
    if not isinstance(data, dict):
        raise RebenchError(f"baseline is not an object: {p}")
    return data


# --------------------------------------------------------------------------
# the S5.5 quick-bench regression gate (the "blocks the pin-bump PR" half)
# --------------------------------------------------------------------------
def quick_gate(
    baseline: dict,
    current: dict,
    regression_tps_pct: float = 10.0,
) -> dict:
    """The S5.5 quick-bench regression gate: does the current quick-bench
    run regress against the baseline?

    Two independent halves (the S5.5 "> 10 % t/s OR any auto-rubric
    regression"):

      1. **t/s regression** — the M7 ``aggregate_tps`` (the quick bench's
         throughput scenario). A drop ``> regression_tps_pct`` over the
         baseline M7 t/s is a regression. No baseline M7 t/s (the first
         run) -> no t/s gate (nothing to drop from). H9 (post-live-run,
         decision §0.2): when BOTH sides carry a COMPARABILITY triple
         (the {n_tasks, concurrency, N} load), a mismatched triple makes
         the t/s comparison "not comparable" — it is RECORDED as such
         (``tps["comparable"] = False`` + a reason), NOT silently compared
         and NOT silently skipped (the rubric half still runs, and a
         rubric regression still blocks).
      2. **auto-rubric regression** — a quick auto-rubric scenario (M1,
         M2, M7) that PASSED in the baseline now FAILS in the current run
         (a rubric flip; the S5.5 "any auto-rubric regression"). A scenario
         absent from the baseline cannot regress (no baseline verdict).

    Returns ``{"blocked": bool, "tps": {...}, "rubrics": {...}, "reasons":
    [str]}``. Pure on (baseline, current) dicts (the S5.8 hermetic
    convention — the S5.8 rubric-test golden in/out pairs drive this)."""
    base_scen = baseline.get("scenarios") or {}
    reasons: list[str] = []

    # 1) the M7 t/s regression (the S5.5 "> 10 % t/s" half).
    base_tps = base_scen.get(_M7, {}).get("tps")
    cur_tps = current.get(_M7, {}).get("tps")
    tps_regression = False
    tps_info: dict = {"baseline_tps": base_tps, "current_tps": cur_tps}
    base_c = base_scen.get(_M7, {}).get("comparability")
    cur_c = current.get(_M7, {}).get("comparability")
    comparable = True
    # G3c round-6 P2: the triple is a dict comparison (base_c != cur_c).
    # A MISSING key in one triple makes the dicts unequal -> "not
    # comparable", which is the correct (conservative) reading: a partial
    # row (one of {n_tasks, concurrency, N} absent) is NOT known to be the
    # same load, so the t/s half is recorded, not silently compared. A
    # reader wanting to know WHY can look at the recorded
    # baseline_comparability / current_comparability above.
    #
    # G3c round-8 P2 (asymmetric case, named): when ONLY ONE side carries
    # a triple (base_c is None but cur_c is not, or vice versa — e.g. a
    # legacy baseline with no M7 row, or a pre-amendment baseline vs a
    # post-amendment run), `comparable` stays True and the t/s half is
    # compared on t/s ALONE (the legacy pre-triple behavior). This is the
    # intended "pre-amendment" reading, NOT a silent skip: the triple is
    # only a comparability GATE when BOTH sides have it; one side missing
    # it means the baseline predates the triple, so we fall back to the
    # t/s-only comparison that always worked. The gate-level
    # `status: "not_comparable"` marker is set ONLY in the BOTH-sides-
    # differ case above (the `comparable = False` branch); the asymmetric
    # case is the legacy "compare on t/s" path and is not not-comparable.
    if base_c is not None and cur_c is not None and base_c != cur_c:
        comparable = False
        tps_info["comparable"] = False
        # G3c round-3 P2: name that the t/s half was SKIPPED, not
        # "checked and passed". A reader seeing regression: false with
        # comparable: false could misread it as "t/s checked, no
        # regression"; the explicit skipped marker removes that ambiguity
        # (regression is only meaningful when comparable — it is left
        # absent here so its .get(..., False) default is the read).
        tps_info["skipped"] = True
        tps_info["baseline_comparability"] = base_c
        tps_info["current_comparability"] = cur_c
        reasons.append(
            "M7 t/s not comparable (load triple differs: "
            f"baseline {base_c} vs current {cur_c}) — t/s half SKIPPED, "
            "recorded, not silently compared"
        )
    if (
        comparable
        and isinstance(base_tps, (int, float))
        and base_tps > 0
        and isinstance(cur_tps, (int, float))
    ):
        drop_pct = (base_tps - cur_tps) / base_tps * 100.0
        tps_info["drop_pct"] = round(drop_pct, 4)
        tps_regression = drop_pct > regression_tps_pct
        tps_info["regression"] = tps_regression
        tps_info["threshold_pct"] = regression_tps_pct
        if tps_regression:
            reasons.append(
                f"M7 t/s dropped {drop_pct:.1f}% (> {regression_tps_pct:g}% "
                f"regression threshold): {base_tps:.2f} -> {cur_tps:.2f}"
            )

    # 2) the auto-rubric regression (the S5.5 "any auto-rubric regression"
    #    half): a quick rubric that passed in the baseline now fails.
    rubric_info: dict = {}
    rubric_regressions: list[str] = []
    for sid in QUICK_SCENARIOS:
        base_v = base_scen.get(sid, {}).get("verdict")
        cur_v = current.get(sid, {}).get("verdict")
        rubric_info[sid] = {"baseline_verdict": base_v, "current_verdict": cur_v}
        if base_v == "pass" and cur_v is not None and cur_v != "pass":
            rubric_regressions.append(sid)
            reasons.append(f"{sid} auto-rubric regressed: pass -> {cur_v}")

    blocked = tps_regression or bool(rubric_regressions)
    # G3c round-5 P1: name the "unblocked-but-tps-skipped" case distinctly
    # at the GATE-OUTPUT level (not just inside tps_info). The S5.5 gate
    # is "blocked = t/s regression OR rubric regression" — when the t/s
    # half is not comparable (the load triple differs) and no rubric
    # regressed, the gate unblocks on the rubric half alone with the t/s
    # half SKIPPED. A reader seeing only `blocked: false` could not tell
    # "t/s checked and passed" from "t/s skipped, rubric passed". The
    # explicit gate-level `status: "not_comparable"` (alongside
    # `blocked: false`) names it. The S5.5 spec requires RECORDING the
    # not-comparable case, not unblocking on it; the current behavior
    # (unblock on the rubric half, record the skip) is the spec's weaker
    # reading, and the status marker makes that weaker reading VISIBLE
    # rather than silent.
    # G3c round-7 P2 (make the `blocked`-vs-`status` split airtight): this
    # holds in BOTH directions of the t/s baseline — (a) base_tps present
    # but the triple differs -> t/s skipped, and (b) base_tps ABSENT
    # (a first-run / no-aggregate baseline) AND the triple differs -> the
    # t/s half is skipped for the same not-comparable reason even though
    # there was nothing to drop from. In both, `blocked` reflects ONLY the
    # rubric half; it is the gate DECISION (rubric regression OR a
    # comparable t/s regression), NOT an "every half was checked" flag.
    # A reader who keys on `blocked` alone for "did we verify t/s?" must
    # also read `status` (== "not_comparable" -> the t/s half did not
    # run) and `tps.skipped` / `tps.comparable`. The S5.5 contract is
    # satisfied by the recorded `status` + `skipped` fields, and this
    # comment is the authoritative note that `blocked` is not the whole
    # story in that case.
    gate_status = "not_comparable" if not comparable else "comparable"
    return {
        "blocked": blocked,
        "status": gate_status,
        "tps": tps_info,
        "rubrics": rubric_info,
        "regression_tps_pct": regression_tps_pct,
        "rubric_regressions": rubric_regressions,
        "reasons": reasons,
    }


# --------------------------------------------------------------------------
# the S5.5 full-bench due-detection (the "revision/quant/S4.2 flip" half)
# --------------------------------------------------------------------------
def full_bench_due(
    baseline: dict,
    current_identity: dict,
    s42_row_flipped: bool = False,
) -> dict:
    """The S5.5 full-bench due-detection: is a full M1-M10 bench due?

    The S5.5 full-bench triggers (the cases the quick gate does NOT cover):

      * a **model revision change** (``model_revision`` differs from the
        baseline's) — a new model revision is a capability change, not just
        a throughput change.
      * a **quant fallback switch** (the active ``quant`` file differs from
        the baseline's) — the S5.5 "quant fallback switches".
      * an **S4.2 row flip** (``s42_row_flipped``) — the doctrine
        work-allocation table changed, which the bench input feeds.

    Note: an engine PIN change (``engine_commit``) is the QUICK-gate's case
    (the S6.5 pin-bump PR's quick bench); it does not, on its own, trigger
    the full bench here. A missing baseline (the first run) is NOT "due"
    (there is no prior state to have changed) — the first run establishes
    the baseline.

    Returns ``{"due": bool, "reasons": [str]}``. Pure on the two identity
    dicts + the flag (hermetic)."""
    base_eng = baseline.get("engine")
    if not isinstance(base_eng, dict):
        return {"due": False, "reasons": [], "note": "no baseline identity (first run)"}
    reasons: list[str] = []
    base_rev = base_eng.get("model_revision")
    cur_rev = current_identity.get("model_revision")
    if cur_rev is not None and base_rev is not None and cur_rev != base_rev:
        reasons.append(f"model revision change: {base_rev} -> {cur_rev}")
    base_quant = base_eng.get("quant")
    cur_quant = current_identity.get("quant")
    if cur_quant is not None and base_quant is not None and cur_quant != base_quant:
        reasons.append(f"quant fallback switch: {base_quant} -> {cur_quant}")
    if s42_row_flipped:
        reasons.append("S4.2 work-allocation row flip")
    return {"due": bool(reasons), "reasons": reasons}


# --------------------------------------------------------------------------
# I/O edge: load the current quick-bench rows from a results dir
# --------------------------------------------------------------------------
def load_rows(
    results_dir: str | Path, scenarios: tuple = QUICK_SCENARIOS
) -> list[dict]:
    """Load the S5.2 run rows for ``scenarios`` from a results dir (the
    ``bench/run.py`` output: ``<ts>_<sid>.json``). The latest row per
    scenario id is kept (a re-run's row is the newest). A missing dir is a
    named error (the gate's exit-2 path)."""
    d = Path(results_dir)
    if not d.is_dir():
        raise RebenchError(f"results dir not found: {d}")
    rows: list[dict] = []
    for sid in scenarios:
        # the latest row for this scenario (the newest by mtime). The row
        # filename is ``<ts>_<sid>.json``; match the EXACT ``_<sid>.json``
        # suffix (so an M1 glob never also matches the M10 rows, which
        # share the ``M1`` prefix).
        candidates = sorted(
            (
                p
                for p in d.glob("*_<sid>.json".replace("<sid>", sid))
                if p.name.endswith(f"_{sid}.json")
            ),
            key=lambda p: p.stat().st_mtime,
        )
        if not candidates:
            raise RebenchError(f"no {sid} row in {d}")
        try:
            rows.append(json.loads(candidates[-1].read_text()))
        except (OSError, json.JSONDecodeError) as exc:
            raise RebenchError(f"row unreadable {candidates[-1]}: {exc!r}") from exc
    return rows


def load_engine_yaml(path: str | None = None, env: os._Environ | None = None) -> dict:
    """Read ``subagent-engine.yaml`` (the S5.4 endpoint resolver's
    convention: the ``BONSAI2_ENGINE_YAML`` hook, default the box path)."""
    import yaml  # PyYAML: box dep, at the edge only (repo convention)

    env = os.environ if env is None else env
    p = Path(
        path or env.get("BONSAI2_ENGINE_YAML", "/opt/bonsai2/etc/subagent-engine.yaml")
    )
    if not p.is_file():
        raise RebenchError(f"engine yaml not found: {p}")
    try:
        data = yaml.safe_load(p.read_text())
    except (OSError, yaml.YAMLError) as exc:
        raise RebenchError(f"engine yaml unreadable: {p}: {exc!r}") from exc
    return data if isinstance(data, dict) else {}


def _ts() -> str:
    import time

    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class RebenchError(Exception):
    """A re-bench policy I/O / config error -> the CLI's exit-2 path."""


# --------------------------------------------------------------------------
# the CLI (the S5.5 gate: a named tool distinct from the S5.4 bench runner)
# --------------------------------------------------------------------------
def _load_bench_yaml(cfg_path: str | None) -> tuple[bool, dict | None, str | None]:
    """Single load-parse-validate pass for the CLI's bench.yaml config.

    Returns ``(present, data, error)``:
      - ``present=False``: no config (falsy path OR file not there) — the
        caller uses its defaults; no error.
      - ``present=True, data=None, error=<msg>``: a present file that
        FAILED to parse into a dict (the H10 "bad input" cases:
        unparseable, non-mapping top level, or an empty/whitespace file
        which parses to None — G3c round-13 P1#1 extended the strict
        path to name this). The caller raises ``RebenchError`` (exit 2
        "bad input").
      - ``present=True, data=<dict>, error=None``: a present mapping; the
        caller pulls its keys out of it.

    The G3c round-13 P2 (double-parse) fix: this is the single pass the
    CLI uses for BOTH S5.7 keys it reads (``rebench_baseline`` /
    ``regression_tps_pct``), so the file is parsed ONCE, not once per
    key. ``_config_value`` (the documented H10 interface) delegates its
    load-parse-validate to this helper, so the CLI path and the per-key
    helper share one source of parse truth and cannot diverge on what
    counts as a bad input.

    G3c round-9 P2 (empty-string convention, named) still applies: a
    FALSY ``cfg_path`` — None (the argparse default) OR "" (an
    explicitly-passed ``--config ""``) — is treated as "no config
    given" (present=False), consistent with the documented "empty =
    absent" escape hatch.
    """
    if not cfg_path:
        return False, None, None
    p = Path(cfg_path)
    if not p.is_file():
        return False, None, None
    try:
        import yaml
    except ImportError:
        # No PyYAML in the environment (hermetic / minimal box): the
        # config file is NOT readable, so behave exactly as the legacy
        # lenient behavior did — "no usable config data" -> the caller
        # takes its defaults. Reported as present=False (not a parse
        # error) so the strict path does not invent a bad-input error
        # out of an environment condition (missing module) rather than
        # a data condition (malformed file).
        return False, None, None
    try:
        data = yaml.safe_load(p.read_text())
    except (OSError, yaml.YAMLError) as exc:
        return True, None, f"--config {cfg_path} unparseable: {exc!r}"
    if data is None:
        # G3c round-13 P1#1: an empty / whitespace file parses to None.
        # Previously hidden by ``or {}`` (so it was a silent default);
        # now normalized to the empty mapping so the caller's key
        # lookup runs — in the strict path that becomes the named
        # "has no '<key>' key" bad input, the SAME message as a mapping
        # that lacks the key (the two are indistinguishable from the
        # caller's point of view: a present file that yields no value).
        return True, {}, None
    if not isinstance(data, dict):
        # G3c round-13 P1#1: an empty/whitespace file parses to None —
        # previously hidden by ``or {}`` (so it was a silent default),
        # now named as the same bad input as any other non-mapping.
        return (
            True,
            None,
            (f"--config {cfg_path} top level is not a mapping: {type(data).__name__}"),
        )
    return True, data, None


def _config_value(
    cfg_path: str | None, key: str, default, strict_malformed: bool = False
):
    """Read one S5.7 bench.yaml key (``regression_tps_pct`` /
    ``rebench_baseline``). File ABSENT -> the default (the gate still
    runs — the config is not the source of the regression data).

    H10 (post-live-run): a file PRESENT but unparseable (or whose top
    level is not a mapping) is NOT a silent default — it is a named bad
    input. ``strict_malformed=True`` (the documented CLI ``--config``
    path) makes that a ``RebenchError`` (the caller maps it to exit 2
    "bad input"); without strictness the legacy lenient behavior is
    kept for internal callers.

    G3c round-9 P2 (empty-string convention, named): a FALSY ``cfg_path``
    — None (the argparse default) OR "" (an explicitly-passed
    ``--config ""``) — is treated as "no config given", NOT as a bad
    input: the ``if not cfg_path: return default`` short-circuit above is
    the documented "empty = absent" convention, so ``--config ""`` is a
    no-config escape hatch, consistent with the H10 "absent = default"
    half of the contract (the bad-input half applies to a NON-EMPTY path
    naming a present-but-malformed file). A caller wanting "" to be an
    error must check it at the CLI layer; the config layer treats it as
    absent by design (an empty path names nothing to parse).

    G3c round-13 P1, re-amended at round-14 P1 (runner alignment, H10
    letter): the strict path fires on a PRESENT file that is MALFORMED —
    unparseable, or a non-mapping top level that is NOT empty. A present
    file that PARSES to a mapping but LACKS the key (including an
    empty/whitespace file, which the runner normalizes to {} at
    bench/config.py:253-254) is INCOMPLETE, not malformed: the runner
    fills that key from BENCH_CONFIG_DEFAULTS (bench/config.py:266-269),
    so the gate does the same and returns the default here (named in the
    CLI's config_note by main; this accessor returns the bare default).
    The round-13 "hard-fail a present-but-incomplete file" over-tightened
    the contract relative to the runner; round-14 restores runner parity
    while keeping the malformed half (H10) intact. The lenient (internal
    caller) path is UNCHANGED: it returns the default for every
    present-but-valueless file, strict or not.

    G3c round-13 P2 (single source of parse truth): this now delegates
    the load-parse-validate to ``_load_bench_yaml`` (the same single
    pass ``main`` uses for BOTH S5.7 keys), so the CLI path and this
    helper can never diverge on what counts as a bad input.
    """
    present, data, err = _load_bench_yaml(cfg_path)
    if err:
        if strict_malformed:
            raise RebenchError(err)
        return default
    if not present:
        return default
    data = data or {}
    if key in data:
        return data[key]
    # G3c round-14 P1: a present mapping that lacks the key is INCOMPLETE
    # (the runner fills it from the S5.7 default), not MALFORMED — so it
    # is the default here, whether strict or lenient. Only a genuinely
    # malformed file (the err case above) is the strict bad input.
    return default


def _print(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj) + "\n")


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(
        description="The S5.5 re-bench policy gate (MAP 4.11; the quick-bench "
        "regression gate + the full-bench due-detection)."
    )
    ap.add_argument(
        "command",
        choices=["gate", "due", "save-baseline"],
        help="gate = the S5.5 quick-bench regression gate; due = full-bench "
        "due-detection; save-baseline = record the current quick run as the "
        "baseline.",
    )
    ap.add_argument(
        "results_dir",
        nargs="?",
        default=None,
        help="the S5.2 results dir of a just-completed --quick run (gate / "
        "save-baseline).",
    )
    ap.add_argument("--baseline", default=None, help="the baseline JSON path")
    ap.add_argument(
        "--config",
        default=None,
        help="bench.yaml (regression_tps_pct / rebench_baseline)",
    )
    ap.add_argument(
        "--engine",
        default=None,
        help="subagent-engine.yaml (due / save-baseline; default the box path "
        "or the BONSAI2_ENGINE_YAML env)",
    )
    ap.add_argument(
        "--s42-flip",
        action="store_true",
        help="an S4.2 work-allocation row flipped (the full-bench due trigger)",
    )
    args = ap.parse_args(argv)

    # H10 (post-live-run): a PRESENT-but-malformed --config is a named
    # bad-input error (exit 2), not a silent default fallback; an ABSENT
    # config keeps the legacy default (the gate still runs).
    #
    # G3c round-13 P2 (single pass): the file is parsed ONCE via
    # _load_bench_yaml and both S5.7 keys are read from that single
    # result — no double I/O / double parse / divergent error surface for
    # the two keys.
    #
    # G3c round-14 P1 (runner alignment, H10 letter): a present bench.yaml
    # that PARSES to a mapping (including an empty/whitespace file, which
    # the runner normalizes to {} at bench/config.py:253-254) but LACKS
    # one of the two S5.7 keys is NOT a bad input — the runner fills that
    # key from BENCH_CONFIG_DEFAULTS (bench/config.py:266-269), so the
    # gate must do the same (same file, same semantics; H10 only required
    # "present-but-malformed -> exit 2"). The gate now fills a missing
    # key from the SAME S5.7 defaults the runner uses, and records the
    # fill (config_note) in the gate output so a reader can see the value
    # came from the default, not the file — the round-13 concern ("silent
    # default, indistinguishable from absent") is answered by the named
    # fill, not by hard-failing a file the runner accepts. Only a
    # genuinely malformed file (unparseable, or a non-mapping top level
    # that is NOT empty) is the exit-2 bad input.
    try:
        present, data, err = _load_bench_yaml(args.config)
        if err:
            raise RebenchError(err)
        data = data or {}
        config_note = None
        if not present:
            # no --config (or it names a missing file): H10 default, gate
            # still runs.
            baseline_path = args.baseline or DEFAULT_BASELINE
            regression_tps_pct = 10.0
        else:
            # a present mapping: take each S5.7 key from the file, fill a
            # missing one from the S5.7 default (the runner's behavior,
            # bench/config.py BENCH_CONFIG_DEFAULTS), and name the fill.
            fills = []
            if "rebench_baseline" in data:
                baseline_path = args.baseline or data["rebench_baseline"]
            else:
                baseline_path = args.baseline or DEFAULT_BASELINE
                fills.append(f"rebench_baseline <- S5.7 default ({DEFAULT_BASELINE})")
            if "regression_tps_pct" in data:
                regression_tps_pct = data["regression_tps_pct"]
            else:
                regression_tps_pct = 10.0
                fills.append("regression_tps_pct <- S5.7 default (10)")
            if fills:
                config_note = "; ".join(fills)
    except RebenchError as exc:
        _print({"status": "error", "error": str(exc)})
        return EXIT_CONFIG

    try:
        if args.command == "gate":
            if not args.results_dir:
                _print({"status": "error", "error": "gate needs a <results-dir>"})
                return EXIT_CONFIG
            rows = load_rows(args.results_dir)
            current = rows_summary(rows)
            baseline = load_baseline(baseline_path)
            gate = quick_gate(baseline, current, regression_tps_pct)
            out_gate = {
                "status": "blocked" if gate["blocked"] else "unblocked",
                "gate": gate,
            }
            if config_note:
                out_gate["config_note"] = config_note
            _print(out_gate)
            return EXIT_BLOCKED if gate["blocked"] else EXIT_OK

        if args.command == "due":
            engine = load_engine_yaml(args.engine)
            baseline = load_baseline(baseline_path)
            due = full_bench_due(baseline, engine_identity(engine), args.s42_flip)
            out_due = {"status": "due" if due["due"] else "not-due", "due": due}
            if config_note:
                out_due["config_note"] = config_note
            _print(out_due)
            return EXIT_BLOCKED if due["due"] else EXIT_OK

        # save-baseline
        if not args.results_dir:
            _print({"status": "error", "error": "save-baseline needs a <results-dir>"})
            return EXIT_CONFIG
        rows = load_rows(args.results_dir)
        engine = load_engine_yaml(args.engine)
        commit = _git_commit()
        p = save_baseline(baseline_path, engine, rows, commit)
        out_save = {
            "status": "saved",
            "baseline": str(p),
            "scenarios": sorted(rows_summary(rows)),
        }
        if config_note:
            out_save["config_note"] = config_note
        _print(out_save)
        return EXIT_OK
    except RebenchError as exc:
        _print({"status": "error", "error": str(exc)})
        return EXIT_CONFIG


def _git_commit(root: str | None = None) -> str | None:
    import subprocess

    try:
        p = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
            cwd=root if root else None,
        )
        if p.returncode == 0 and p.stdout.strip():
            return p.stdout.strip()
    except (subprocess.TimeoutExpired, OSError, ValueError):
        pass
    return None


if __name__ == "__main__":
    sys.exit(main())
