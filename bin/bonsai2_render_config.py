#!/usr/bin/env python3
"""bonsai2_render_config.py — MAP 1.5 / S1.6 / S1.7: subagent-engine.yaml
writer + schema validation (the S1.7 "unit half": YAML schema
validation — port math, ctx/parallel bounds, sampling ranges).

Two responsibilities:

1. `validate_engine_config(data) -> (errors, warnings)` — the pure
   schema check against the S1.6 shape and the S1.7/S1.3/S1.4 bounds:

   presence:
     * all S1.6 top-level blocks: engine{binary,pin{repo,commit}},
       image{pin{tag,sha256}}, model{repo,revision,file,
       fallback_file,source_object,fallback_source_object,sha256,
       size_bytes,fallback_sha256,fallback_size_bytes},
       instances{count,port_base,ctx_size,max_num_seqs,
       per_instance_budget_gb}, sampling{temperature,top_p,top_k,
       min_p,repeat_penalty,repeat_last_n,stop_token_ids},
       reasoning_format, alias, api_key, kv_cache
   formats:
     * engine.pin.commit = 40 hex
     * model sha fields = 64 hex; size fields = int > 0; revision
       non-empty
     * image.pin.tag: non-empty, no `<` placeholder residue
     * image.pin.sha256: `sha256:` + 64 hex OR the template's
       documented placeholder `<docker-content-digest-at-install>`
       (placeholder residue is a warning until the MAP 1.4 build
       records the real digest; anything else is an error)
   math / bounds:
     * port math: port_base int in [1024, 65535]; every instance port
       (port_base + i, i in 0..count-1) <= 65535; optional
       per-instance `ports` map (i -> port) must be ints in range and
       all ports (base-derived or overridden) unique
     * ctx_size: int, 8192 <= ctx <= 262144, a multiple of 8192
       (S2.5 floor + multiple rule; S1.3 ceiling = the model's 262K)
     * max_num_seqs: int 1..64 (S1.3)
     * instances.count: int 0..8 (the S1.4 clamp range; 0 = main-only,
       S2.4 — a valid state)
     * per_instance_budget_gb: number > 0
     * sampling ranges (S1.7 "sampling ranges"): temperature [0, 2],
       top_p (0, 1], top_k int >= 1, min_p [0, 1], repeat_penalty
       [0.5, 2.0], repeat_last_n int >= 0, stop_token_ids a non-empty
       list of ints
     * reasoning_format / alias / api_key: non-empty strings
     * kv_cache: a pin-supported --cache-type-k/v dtype (S1.3/S1.4
       re-anchor; the spec pins q4_0)

2. rendering — `render_config(data, overrides)`: fill remaining
   `<placeholder>` values (the image pin from `--image-pin-json`
   `{"tag": ..., "sha256": ...}` or already-present pins; everything
   else must already be pinned in the template) and write the YAML
   (`--out`, default stdout). This is the writer install.sh (MAP 6.2)
   calls per S1.6/S6.3 to produce /opt/bonsai2/etc/subagent-engine.yaml.

Exit codes: 0 = valid / rendered; 2 = ERR_CONFIG.

Stdlib-only for the validation core (the S1.7 unit half is hermetic);
PyYAML is used only for YAML (de)serialization at the edges — the
hermetic test suite drives the pure validator with in-memory dicts and
loads the tracked template via a stdlib text parse where needed.

Usage:
  python3 bin/bonsai2_render_config.py --check            # validate the template
  python3 bin/bonsai2_render_config.py --out /path/out.yaml \
      --image-pin-json '{"tag":"bonsai2-server:1.0","sha256":"sha256:..." }'
"""

from __future__ import annotations

import argparse
import copy
import json
import re
import sys

EXIT_OK = 0
EXIT_CONFIG = 2

ERR_CONFIG = "ERR_CONFIG"

# S1.3/S1.4/S2.5 constants (the spec is the source; these are the
# validator's copies, asserted against worked examples in the tests).
PORT_MIN = 1024
PORT_MAX = 65535
CTX_MIN = 8192  # S2.5 drift floor
CTX_MAX = 262144  # S1.3: the model's 262K ceiling
CTX_MULTIPLE = 8192  # S2.5: ctx reductions keep a multiple of 8192
PARALLEL_MIN = 1
PARALLEL_MAX = 64  # S1.3: max_num_seqs bounds
COUNT_MIN = 0  # S2.4 main-only is a valid state
COUNT_MAX = 8  # S1.4 clamp
# S1.3/S1.4 re-anchor: the allowed --cache-type-k/v values at the pinned
# fork (the fork's kv_cache_types list, third_party/llama.cpp/common/
# arg.cpp:304). The spec pins q4_0 (the S1.4 re-anchor, MAP 1.7); the
# validator accepts the full pin-supported set so an S1.4 re-anchor to a
# different KV quant does not require a validator change.
KV_CACHE_TYPES = frozenset(
    {"f32", "f16", "bf16", "q8_0", "q4_0", "q4_1", "iq4_nl", "q5_0", "q5_1"}
)

_COMMIT_RE = re.compile(r"[0-9a-f]{40}")
_SHA256_RE = re.compile(r"sha256:[0-9a-f]{64}")
_SHA256_HEX_RE = re.compile(r"[0-9a-f]{64}")


def _is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _is_num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _num_range(errors: list[str], where: str, v, lo, hi):
    if not _is_num(v):
        errors.append(f"{ERR_CONFIG}: {where}: not a number: {v!r}")
        return
    if not (lo <= v <= hi):
        errors.append(f"{ERR_CONFIG}: {where}: {v} not in [{lo}, {hi}]")


def _instance_ports(inst: dict, count: int) -> dict[int, int]:
    """Effective port per instance index (base-derived, overrides applied).

    Collision-check map: includes EVERY override with an int port value,
    in-range or not — the shape check (the caller) reports out-of-range
    indices, the collision check must still see their port (a stray
    index 5 duplicating index 0's port is a collision the map would
    otherwise miss). When `port_base` is missing/invalid the base
    derives from 0: the shape check already reports the bad base, so
    the collision map just needs a stable base and is gated on
    `count > 0` by the caller.
    """
    base = inst.get("port_base", 0) if _is_int(inst.get("port_base")) else 0
    ports: dict[int, int] = {}
    for i in range(max(0, count)):
        ports[i] = base + i
    overrides = inst.get("ports")
    if isinstance(overrides, dict):
        for k, v in overrides.items():
            if not (_is_int(k) and _is_int(v)):
                continue  # shape errors are reported by the caller
            ports[k] = v
    return ports


def validate_engine_config(data) -> tuple[list[str], list[str]]:
    """S1.6 shape + S1.7 bounds. Returns (errors, warnings).

    `errors` are ERR_CONFIG conditions (the validator's exit-2 set);
    `warnings` are non-blocking notes (e.g. the image digest still in
    template-placeholder form before MAP 1.4 records it).
    """
    errors: list[str] = []
    warnings: list[str] = []
    if not isinstance(data, dict):
        return [
            f"{ERR_CONFIG}: top level is not a mapping: {type(data).__name__}"
        ], warnings

    # --- engine ---
    eng = data.get("engine")
    if not isinstance(eng, dict):
        errors.append(f"{ERR_CONFIG}: engine: block missing or not a mapping")
    else:
        if not (isinstance(eng.get("binary"), str) and eng.get("binary").strip()):
            errors.append(f"{ERR_CONFIG}: engine.binary: missing/empty")
        pin = eng.get("pin")
        if not isinstance(pin, dict):
            errors.append(f"{ERR_CONFIG}: engine.pin: missing/not a mapping")
        else:
            if not (isinstance(pin.get("repo"), str) and pin.get("repo").strip()):
                errors.append(f"{ERR_CONFIG}: engine.pin.repo: missing/empty")
            commit = pin.get("commit")
            if not (
                isinstance(commit, str) and _COMMIT_RE.fullmatch(commit.strip().lower())
            ):
                errors.append(
                    f"{ERR_CONFIG}: engine.pin.commit: not a 40-hex sha: {commit!r}"
                )

    # --- image ---
    img = data.get("image")
    if not isinstance(img, dict):
        errors.append(f"{ERR_CONFIG}: image: block missing or not a mapping")
    else:
        pin = img.get("pin")
        if not isinstance(pin, dict):
            errors.append(f"{ERR_CONFIG}: image.pin: missing/not a mapping")
        else:
            tag = pin.get("tag")
            if not (isinstance(tag, str) and tag.strip()):
                errors.append(f"{ERR_CONFIG}: image.pin.tag: missing/empty")
            elif "<" in tag or ">" in tag:
                warnings.append(
                    f"{ERR_CONFIG}: image.pin.tag: still a template placeholder "
                    f"{tag!r} (the MAP 1.4 build records the real tag)"
                )
            digest = pin.get("sha256")
            if not (isinstance(digest, str) and digest.strip()):
                errors.append(f"{ERR_CONFIG}: image.pin.sha256: missing/empty")
            elif _SHA256_RE.fullmatch(digest.strip().lower()):
                pass  # pinned (MAP 1.4 has recorded the content digest)
            elif "<" in digest and ">" in digest:
                warnings.append(
                    f"{ERR_CONFIG}: image.pin.sha256: still a template placeholder "
                    f"(the MAP 1.4 build records the docker content digest)"
                )
            else:
                errors.append(
                    f"{ERR_CONFIG}: image.pin.sha256: not a `sha256:<64-hex>` "
                    f"digest and not the documented placeholder: {digest!r}"
                )

    # --- model ---
    model = data.get("model")
    if not isinstance(model, dict):
        errors.append(f"{ERR_CONFIG}: model: block missing or not a mapping")
    else:
        for k in (
            "repo",
            "revision",
            "file",
            "fallback_file",
            "source_object",
            "fallback_source_object",
        ):
            if not (isinstance(model.get(k), str) and model.get(k).strip()):
                errors.append(f"{ERR_CONFIG}: model.{k}: missing/empty")
        for k in ("sha256", "fallback_sha256"):
            v = model.get(k)
            if not (isinstance(v, str) and _SHA256_HEX_RE.fullmatch(v.strip().lower())):
                errors.append(f"{ERR_CONFIG}: model.{k}: not a 64-hex sha256: {v!r}")
        for k in ("size_bytes", "fallback_size_bytes"):
            v = model.get(k)
            if not (_is_int(v) and v > 0):
                errors.append(f"{ERR_CONFIG}: model.{k}: not an int > 0: {v!r}")

    # --- instances ---
    inst = data.get("instances")
    if not isinstance(inst, dict):
        errors.append(f"{ERR_CONFIG}: instances: block missing or not a mapping")
    else:
        # Port math runs on the validated int values (isinstance
        # narrowing; bool is an int subclass and is excluded). Invalid
        # values fall back to 0; the range checks above carry their own
        # errors, so the math below cannot double-report them.
        count_raw = inst.get("count")
        if isinstance(count_raw, int) and not isinstance(count_raw, bool):
            if COUNT_MIN <= count_raw <= COUNT_MAX:
                count: int = count_raw
            else:
                errors.append(
                    f"{ERR_CONFIG}: instances.count: not an int in "
                    f"[{COUNT_MIN}, {COUNT_MAX}]: {count_raw!r}"
                )
                count = 0
        else:
            errors.append(
                f"{ERR_CONFIG}: instances.count: not an int in "
                f"[{COUNT_MIN}, {COUNT_MAX}]: {count_raw!r}"
            )
            count = 0
        base_raw = inst.get("port_base")
        if isinstance(base_raw, int) and not isinstance(base_raw, bool):
            if PORT_MIN <= base_raw <= PORT_MAX:
                base: int = base_raw
            else:
                errors.append(
                    f"{ERR_CONFIG}: instances.port_base: not an int in "
                    f"[{PORT_MIN}, {PORT_MAX}]: {base_raw!r}"
                )
                base = 0
        else:
            errors.append(
                f"{ERR_CONFIG}: instances.port_base: not an int in "
                f"[{PORT_MIN}, {PORT_MAX}]: {base_raw!r}"
            )
            base = 0
        if count > 0 and base + count - 1 > PORT_MAX:
            errors.append(
                f"{ERR_CONFIG}: instances: port_base {base} + count {count} "
                f"overflows the port range (last port {base + count - 1} > {PORT_MAX})"
            )
        overrides = inst.get("ports")
        if overrides is not None:
            if not isinstance(overrides, dict):
                errors.append(f"{ERR_CONFIG}: instances.ports: not a mapping i->port")
            else:
                for k, v in overrides.items():
                    if not (
                        isinstance(k, int)
                        and 0 <= k < count
                        and isinstance(v, int)
                        and not isinstance(v, bool)
                        and PORT_MIN <= v <= PORT_MAX
                    ):
                        errors.append(
                            f"{ERR_CONFIG}: instances.ports[{k!r}]: not an int "
                            f"port in [{PORT_MIN}, {PORT_MAX}]: {v!r}"
                        )
        if count > 0:
            eff = _instance_ports(inst, count)
            seen: dict[int, list[int]] = {}
            for i, p in sorted(eff.items()):
                seen.setdefault(p, []).append(i)
            for p, idxs in seen.items():
                if len(idxs) > 1:
                    errors.append(
                        f"{ERR_CONFIG}: instances: port {p} assigned to "
                        f"multiple instances {idxs}"
                    )
        ctx = inst.get("ctx_size")
        if not _is_int(ctx):
            errors.append(f"{ERR_CONFIG}: instances.ctx_size: not an int: {ctx!r}")
        elif not (CTX_MIN <= ctx <= CTX_MAX):
            errors.append(
                f"{ERR_CONFIG}: instances.ctx_size: {ctx} not in [{CTX_MIN}, {CTX_MAX}]"
            )
        elif ctx % CTX_MULTIPLE != 0:
            errors.append(
                f"{ERR_CONFIG}: instances.ctx_size: {ctx} is not a multiple of "
                f"{CTX_MULTIPLE} (S2.5 drift-reduction rule)"
            )
        par = inst.get("max_num_seqs")
        if not (_is_int(par) and PARALLEL_MIN <= par <= PARALLEL_MAX):
            errors.append(
                f"{ERR_CONFIG}: instances.max_num_seqs: not an int in "
                f"[{PARALLEL_MIN}, {PARALLEL_MAX}]: {par!r}"
            )
        budget = inst.get("per_instance_budget_gb")
        if not (_is_num(budget) and budget > 0):
            errors.append(
                f"{ERR_CONFIG}: instances.per_instance_budget_gb: not a number > 0: "
                f"{budget!r}"
            )

    # --- sampling (S1.7 "sampling ranges"; S1.6 model-card lock) ---
    samp = data.get("sampling")
    if not isinstance(samp, dict):
        errors.append(f"{ERR_CONFIG}: sampling: block missing or not a mapping")
    else:
        _num_range(errors, "sampling.temperature", samp.get("temperature"), 0.0, 2.0)
        # top_p is (0, 1]: 0.0 selects nothing (degenerate sampling) and
        # is rejected; the S1.6 model-card value 0.9 sits inside.
        tp = samp.get("top_p")
        if not (_is_num(tp) and 0.0 < tp <= 1.0):
            errors.append(
                f"{ERR_CONFIG}: sampling.top_p: not a number in (0, 1]: {tp!r}"
            )
        tk = samp.get("top_k")
        if not (_is_int(tk) and tk >= 1):
            errors.append(f"{ERR_CONFIG}: sampling.top_k: not an int >= 1: {tk!r}")
        _num_range(errors, "sampling.min_p", samp.get("min_p"), 0.0, 1.0)
        _num_range(
            errors, "sampling.repeat_penalty", samp.get("repeat_penalty"), 0.5, 2.0
        )
        rln = samp.get("repeat_last_n")
        if not (_is_int(rln) and rln >= 0):
            errors.append(
                f"{ERR_CONFIG}: sampling.repeat_last_n: not an int >= 0: {rln!r}"
            )
        stops = samp.get("stop_token_ids")
        if not (
            isinstance(stops, list)
            and len(stops) > 0
            and all(_is_int(x) and x >= 0 for x in stops)
        ):
            errors.append(
                f"{ERR_CONFIG}: sampling.stop_token_ids: not a non-empty list of ints: "
                f"{stops!r}"
            )

    # --- scalar leaves ---
    for k in ("reasoning_format", "alias", "api_key"):
        if not (isinstance(data.get(k), str) and data.get(k).strip()):
            errors.append(f"{ERR_CONFIG}: {k}: missing/empty")
    # --- kv_cache (S1.3/S1.4 re-anchor): must be a pin-supported dtype ---
    kvc = data.get("kv_cache")
    if not (isinstance(kvc, str) and kvc.strip()):
        errors.append(f"{ERR_CONFIG}: kv_cache: missing/empty")
    elif kvc.strip().lower() not in KV_CACHE_TYPES:
        errors.append(
            f"{ERR_CONFIG}: kv_cache: {kvc!r} not a supported --cache-type-k/v "
            f"value ({', '.join(sorted(KV_CACHE_TYPES))})"
        )
    return errors, warnings


def render_config(data: dict, image_pin: dict | None = None) -> tuple[dict, list[str]]:
    """Produce the rendered config (a NEW dict; the input is untouched).

    `image_pin` = {"tag": str, "sha256": str} overrides the image pin
    (install.sh passes the MAP 1.4 digest). Missing/empty pin values in
    the template that are NOT overridden remain errors (the rendered
    config must be fully pinned — S1.6).
    Returns (rendered, errors); `errors` is empty only when the result
    is fully pinned + valid.
    """
    # G3c round-3 P2: `copy.deepcopy` instead of a `json.loads(json.dumps())`
    # round-trip — the json path silently drops non-JSON YAML values (a date
    # in `revision`, a binary-safe string, a nested mapping key) and coerces
    # `None` -> `null`. `deepcopy` is stdlib and preserves the YAML value set
    # exactly, so the rendered dict is a faithful copy of the input.
    out = copy.deepcopy(data)  # new dict; the caller's input is untouched
    if image_pin:
        tag, digest = image_pin.get("tag"), image_pin.get("sha256")
        if tag and digest:
            out.setdefault("image", {}).setdefault("pin", {})
            out["image"]["pin"]["tag"] = tag
            out["image"]["pin"]["sha256"] = digest
    errors, _ = validate_engine_config(out)
    return out, errors


def load_yaml(path: str):
    import yaml  # PyYAML: box dep (edge-only, same convention as the MAP 1.3 tools)

    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def main(argv: list[str] | None = None) -> int:
    import os

    default_config = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "etc",
        "subagent-engine.yaml",
    )
    ap = argparse.ArgumentParser(
        description="subagent-engine.yaml writer + schema validation (MAP 1.5)."
    )
    ap.add_argument(
        "--config", default=default_config, help="source YAML (the template)"
    )
    ap.add_argument(
        "--check", action="store_true", help="validate only, print result, exit"
    )
    ap.add_argument("--out", help="rendered output path (default: stdout)")
    ap.add_argument(
        "--image-pin-json",
        help='image pin override, e.g. \'{"tag":"bonsai2-server:1.0","sha256":"sha256:..."}\'',
    )
    args = ap.parse_args(argv)

    try:
        data = load_yaml(args.config)
    except Exception as exc:  # noqa: BLE001 - any read/parse failure -> named config error
        print(f"{ERR_CONFIG}: {args.config}: {exc!r}", file=sys.stderr)
        return EXIT_CONFIG

    errors, warnings = validate_engine_config(data)
    for w in warnings:
        print(f"[render] WARN {w}", file=sys.stderr)
    for e in errors:
        print(f"[render] {e}", file=sys.stderr)

    if args.check:
        if errors:
            print(f"[render] INVALID ({len(errors)} error(s))", file=sys.stderr)
            return EXIT_CONFIG
        print(
            f"[render] valid: {args.config}"
            + (f" ({len(warnings)} warning(s))" if warnings else "")
        )
        return EXIT_OK

    pin = None
    if args.image_pin_json:
        try:
            pin = json.loads(args.image_pin_json)
            if not (isinstance(pin, dict) and pin.get("tag") and pin.get("sha256")):
                raise ValueError('need {"tag": ..., "sha256": ...}')
        except (json.JSONDecodeError, ValueError) as exc:
            print(f"{ERR_CONFIG}: --image-pin-json: {exc}", file=sys.stderr)
            return EXIT_CONFIG
    rendered, rerr = render_config(data, pin)
    for e in rerr:
        print(f"[render] {e}", file=sys.stderr)
    if rerr:
        return EXIT_CONFIG

    import yaml  # PyYAML: box dep (edge-only)

    text = yaml.safe_dump(rendered, sort_keys=False, default_flow_style=False)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"[render] wrote {args.out}")
    else:
        sys.stdout.write(text)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
