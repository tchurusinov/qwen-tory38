#!/usr/bin/env python3
"""bench.config — MAP 4.1 / S5.7 / S5.8: bench.yaml loader + schema
validation.

The S5.7 bench.yaml is the bench's config: the scenario list, the quick
list, repetitions, the regression t/s threshold, the results + goldens
dirs, and the M8 main-guardrail percentage. This module owns its schema
(the "config test" of S5.8): every scenario id is one of the S5.2 matrix
ids (M1..M10), the quick list is a non-empty subset of the scenario list,
repetitions is a positive int, the two dirs resolve to real (or
resolvable) paths, and the two percentages are in (0, 100].

Exit-code contract (S5.4 / S3.5): 0 = valid / loaded; 2 = ERR_CONFIG
(invalid bench.yaml) — the S3.2 code 2 (malformed args / bad config), in
the closed set. A scenario id that is not in the S5.2 matrix is the
S5.8 "invalid scenario id -> exit 2" case.

Pure on dicts for the validation core (the hermetic suite drives it with
in-memory configs, no yaml dep needed); PyYAML is used only at the
file-read edge (the box dep the S3.6/S1.6 tooling shares — the same
convention as ``bonsai2_render_config.load_yaml``).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

EXIT_OK = 0
EXIT_CONFIG = 2

ERR_CONFIG = "ERR_CONFIG"

# The S5.2 scenario matrix ids (locked, M1-M10). A bench.yaml scenario id
# outside this set is the S5.8 invalid-id -> exit 2 case.
VALID_SCENARIO_IDS = frozenset(
    {"M1", "M2", "M3", "M4", "M5", "M6", "M7", "M8", "M9", "M10"}
)

# S5.7 defaults (a missing key gets the default; a malformed VALUE is an
# ERR_CONFIG -> exit 2; S5.7 is the locked key set). The template's
# S5.7 literal is the source.
BENCH_CONFIG_DEFAULTS: dict = {
    "scenarios": ["M1", "M2", "M3", "M4", "M5", "M6", "M7", "M8", "M9", "M10"],
    "quick": ["M1", "M2", "M7"],  # S5.5: the text-only regression gate
    "repetitions": 3,
    "regression_tps_pct": 10,
    "results_dir": "/run/bonsai2/bench",
    "goldens_dir": "bench/goldens",
    "main_guardrail_pct": 5,  # mirrors harness.yaml main_tps_guardrail_pct
    "rebench_baseline": "bench-results/rebench-baseline.json",  # S5.5/4.11: the last-accepted quick-bench baseline
    # S3.6 / H3 (post-live-run, 2026-09-27): a MIRROR of harness.yaml
    # main_api_key (the M6/M8 main-side Bearer key; empty = keyless).
    # The AUTHORITATIVE source is harness.yaml (bench.endpoints.main_endpoint
    # reads it from there); this key is in the schema so the documented
    # bench.yaml shape validates without an "unknown key" warning.
    "main_api_key": "",
}

_DEFAULT_TEMPLATE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "etc",
    "bench.yaml",
)


def _is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _is_num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _is_str(v) -> bool:
    return isinstance(v, str) and v.strip() != ""


def validate_bench_config(data) -> tuple[list[str], list[str]]:
    """S5.7 shape + S5.8 config-test bounds. Returns (errors, warnings).

    ``errors`` are ERR_CONFIG conditions (the exit-2 set); ``warnings``
    are non-blocking notes. A config with a valid ``scenarios`` list is
    checked for: each id in the S5.2 matrix, ``quick`` a non-empty subset
    of ``scenarios``, ``repetitions`` a positive int, both pct fields in
    (0, 100], and both dirs resolving to resolvable path strings (an
    absolute path that does not exist yet is a warning — it is created at
    run time; a relative path is resolved against CWD and checked).
    """
    errors: list[str] = []
    warnings: list[str] = []
    if not isinstance(data, dict):
        return [
            f"{ERR_CONFIG}: top level is not a mapping: {type(data).__name__}"
        ], warnings

    # --- scenarios (S5.2 matrix, S5.8 invalid-id -> exit 2) ---
    scenarios = data.get("scenarios")
    if not isinstance(scenarios, list) or len(scenarios) == 0:
        errors.append(
            f"{ERR_CONFIG}: scenarios: must be a non-empty list: {scenarios!r}"
        )
        seen: list[str] = []
    else:
        seen = []
        for sid in scenarios:
            if not _is_str(sid):
                errors.append(f"{ERR_CONFIG}: scenarios: not a string id: {sid!r}")
                continue
            if sid not in VALID_SCENARIO_IDS:
                # the S5.8 "invalid scenario id -> exit 2" case, named.
                errors.append(
                    f"{ERR_CONFIG}: scenarios: unknown scenario id {sid!r} "
                    f"(expected one of {sorted(VALID_SCENARIO_IDS)})"
                )
            seen.append(sid)
        # no duplicate ids
        if len(set(seen)) != len(seen):
            dupes = sorted({s for s in seen if seen.count(s) > 1})
            errors.append(f"{ERR_CONFIG}: scenarios: duplicate ids: {dupes}")

    # --- quick (S5.5): non-empty subset of scenarios ---
    # The S5.7 subset invariant applies to a DECLARED quick list: when
    # `quick` is present it must be a non-empty list and a subset of the
    # effective scenarios (the explicit list when valid, else the S5.7
    # default list). An omitted quick inherits the S5.7 default, which is
    # consistent with the default scenario list by construction.
    if "quick" in data:
        quick = data["quick"]
        if not isinstance(quick, list) or len(quick) == 0:
            errors.append(f"{ERR_CONFIG}: quick: must be a non-empty list: {quick!r}")
        else:
            if (
                isinstance(scenarios, list)
                and scenarios
                and all(_is_str(s) and s in VALID_SCENARIO_IDS for s in scenarios)
            ):
                scenario_set = set(scenarios)
            else:
                scenario_set = set(BENCH_CONFIG_DEFAULTS["scenarios"])
            for sid in quick:
                if not _is_str(sid):
                    errors.append(f"{ERR_CONFIG}: quick: not a string id: {sid!r}")
                    continue
                if sid not in scenario_set:
                    errors.append(
                        f"{ERR_CONFIG}: quick: {sid!r} not in scenarios {sorted(scenario_set)}"
                    )

    # --- repetitions: positive int ---
    reps = data.get("repetitions", BENCH_CONFIG_DEFAULTS["repetitions"])
    if not _is_int(reps) or reps <= 0:
        errors.append(f"{ERR_CONFIG}: repetitions: not a positive int: {reps!r}")

    # --- the two pct fields: (0, 100] ---
    for key in ("regression_tps_pct", "main_guardrail_pct"):
        v = data.get(key, BENCH_CONFIG_DEFAULTS[key])
        if not _is_num(v) or not (0 < v <= 100):
            errors.append(f"{ERR_CONFIG}: {key}: not a number in (0, 100]: {v!r}")

    # --- main_api_key (S3.6 / H3): any string ("" = keyless) ---
    mkey = data.get("main_api_key", BENCH_CONFIG_DEFAULTS["main_api_key"])
    if not isinstance(mkey, str):
        errors.append(f"{ERR_CONFIG}: main_api_key: not a string: {mkey!r}")

    # --- the two dirs: resolvable path strings (S5.8 "dir resolvability") ---
    cwd = Path.cwd()
    for key in ("results_dir", "goldens_dir"):
        v = data.get(key, BENCH_CONFIG_DEFAULTS[key])
        if not _is_str(v):
            errors.append(f"{ERR_CONFIG}: {key}: not a path string: {v!r}")
            continue
        p = Path(v)
        # H11 (post-live-run): a pre-install run's default results_dir
        # (/run/bonsai2/bench) does not exist yet — the WARN NAMES THE
        # FLAG that overrides it (the S5.2/S5.7 pre-install convention:
        # --results-dir), so a first-time reader knows the knob instead
        # of guessing.
        flag_hint = (
            " (override with --results-dir; see the pre-install convention "
            "in bench-results/README.md)"
            if key == "results_dir"
            else ""
        )
        if p.is_absolute():
            # an absolute path that does not exist yet is a warning (created
            # at run time); one that is not a dir but exists is an error.
            if p.exists() and not p.is_dir():
                errors.append(
                    f"{ERR_CONFIG}: {key}: exists but is not a directory: {v!r}"
                )
            elif not p.exists():
                warnings.append(
                    f"{ERR_CONFIG}: {key}: {v!r} does not exist yet "
                    f"(will be created at run time){flag_hint}"
                )
        else:
            # a relative path: resolve against CWD and check.
            rp = cwd / p
            if rp.exists() and not rp.is_dir():
                errors.append(
                    f"{ERR_CONFIG}: {key}: exists but is not a directory: {v!r}"
                )
            elif not rp.exists():
                warnings.append(
                    f"{ERR_CONFIG}: {key}: {v!r} (relative to CWD {cwd}) does "
                    f"not exist yet (will be created at run time){flag_hint}"
                )

    # --- unknown top-level keys: a warning, not an error (the S5.7 set is
    # the documented shape; extra keys are forward-compat, not a failure) ---
    known = set(BENCH_CONFIG_DEFAULTS)
    unknown = sorted(set(data) - known)
    if unknown:
        warnings.append(
            f"{ERR_CONFIG}: unknown top-level key(s) {unknown} "
            f"(S5.7 documented set: {sorted(known)})"
        )

    return errors, warnings


def load_bench_config(path: str | None = None, cwd: str | None = None) -> dict:
    """Load + validate a bench.yaml. Returns a dict with every S5.7 key
    filled (defaults for missing) + ``config_path``. Raises ``ConfigError``
    (the exit-2 path) when the file is missing/unreadable or the schema
    reports errors. The caller (``bench.run``) maps the raise to exit 2."""
    import yaml  # PyYAML: box dep, at the edge only (repo convention)

    cfg_path = Path(path or os.environ.get("BONSAI2_BENCH_CONFIG", _DEFAULT_TEMPLATE))
    if not cfg_path.is_file():
        raise ConfigError(f"bench config not found: {cfg_path}")
    try:
        text = cfg_path.read_text()
        data = yaml.safe_load(text)
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigError(f"bench config unreadable: {cfg_path}: {exc}") from exc
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise ConfigError(f"{ERR_CONFIG}: {cfg_path}: top level is not a mapping")

    errors, warnings = validate_bench_config(data)
    for w in warnings:
        print(f"[bench] WARN {w}", file=sys.stderr)
    for e in errors:
        print(f"[bench] {e}", file=sys.stderr)
    if errors:
        raise ConfigError(f"invalid bench config {cfg_path} ({len(errors)} error(s))")

    cfg: dict = dict(BENCH_CONFIG_DEFAULTS)
    for key in BENCH_CONFIG_DEFAULTS:
        if key in data:
            cfg[key] = data[key]
    # NOTE: an omitted quick inherits the S5.7 default (the template
    # literal, M1+M2+M7) — it is the engine-regression gate, independent
    # of which scenarios a bench.yaml declares for a full run. A DECLARED
    # quick is subset-checked by validate_bench_config against the
    # declared scenarios (the S5.7 invariant); an omitted one keeps the
    # default (the loop above fills it from BENCH_CONFIG_DEFAULTS).
    cfg["config_path"] = str(cfg_path)
    return cfg


class ConfigError(Exception):
    """Malformed/missing bench config -> the runner's exit-2 path."""


def main(argv: list[str] | None = None) -> int:
    """--check: validate a bench.yaml, print the result, exit 0/2."""
    ap = _argparse()
    args = ap.parse_args(argv)
    try:
        cfg = load_bench_config(args.config)
    except ConfigError as exc:
        print(f"[bench] {exc}", file=sys.stderr)
        return EXIT_CONFIG
    print(f"[bench] valid: {cfg['config_path']}")
    return EXIT_OK


def _argparse():
    import argparse

    ap = argparse.ArgumentParser(
        description="bench.yaml loader + schema validation (MAP 4.1)."
    )
    ap.add_argument(
        "--config",
        default=os.environ.get("BONSAI2_BENCH_CONFIG", _DEFAULT_TEMPLATE),
        help="bench.yaml path (default: the tracked template, or "
        "$BONSAI2_BENCH_CONFIG)",
    )
    return ap


if __name__ == "__main__":
    sys.exit(main())
