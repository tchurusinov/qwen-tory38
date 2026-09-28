"""bench.endpoints — the S5.4 endpoint resolver.

The S5.4 runner names the endpoint resolver as a component: "subagent
ports from ``subagent-engine.yaml``, main from ``harness.yaml``". This
module resolves, for a given scenario, WHICH endpoint its tasks run
against:

  * subagent scenarios (M1-M5, M7-M10): one port per subagent instance,
    read from ``subagent-engine.yaml`` (``instances.port_base`` + i, with
    the optional per-instance ``ports`` override — the S1.6 convention,
    the same port-math the S1.7 validator checks). The pool size N is
    ``instances.count`` (the S1.4 clamp range 0..8).
  * the main-only control (M6, and M8's main half): the main endpoint,
    read from ``harness.yaml`` (``main_endpoint``) — the S3.6 convention.

The resolved endpoint is a small dict the executor consumes:
``{"kind": "subagent"|"main", "port": int, "url": str, "alias": str,
"api_key": str, "index": int}``. The subagent alias + api_key are the
S1.3 wire identity (the same values the taskworker's
``load_engine_identity`` reads); the main endpoint's api_key comes from
``harness.yaml`` ``main_api_key`` when present (the 2026-09-27
post-live-run amendment, H3 / S3.6: the live main stack is key-walled —
no-key completions 401; the S3.2 main-probe "401-walled is still UP"
convention applies to the HEALTH PROBE, not to completions). Empty or
absent = keyless (today's behavior, back-compatible).

Pure + hermetic: given the two YAML dicts (or the loaded engine/harness
identities) the resolver returns the endpoints with no I/O. The file
reading (``load_engine_identity`` / ``load_harness_main``) is a thin edge
wrapper over the S3.6/S1.6 conventions (PyYAML at the edge, the box dep).
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

DEFAULT_ENGINE_YAML = "/opt/bonsai2/etc/subagent-engine.yaml"
DEFAULT_HARNESS_YAML = "/opt/bonsai2/etc/harness.yaml"


def _load_yaml_map(path: Path) -> dict:
    import yaml  # PyYAML: box dep, at the edge only (repo convention)

    if not path.is_file():
        raise FileNotFoundError(f"config not found: {path}")
    try:
        data = yaml.safe_load(path.read_text())
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError(f"config unreadable: {path}: {exc!r}") from exc
    return data if isinstance(data, dict) else {}


def subagent_ports(engine: dict) -> tuple[int, list[dict]]:
    """The S1.6 pool: (N, [endpoint per instance]). N = instances.count
    (the S1.4 clamp 0..8); ports from instances.port_base + i with the
    optional per-instance ``ports`` override (same map the S1.7 validator
    checks). Each endpoint carries the S1.3 alias + api_key (the wire
    identity) + its index. An N=0 pool (main-only) returns ([], N)."""
    inst = engine.get("instances", {})
    count = inst.get("count", 0)
    if not isinstance(count, int) or isinstance(count, bool):
        count = 0
    count = max(0, min(8, count))
    base = inst.get("port_base", 30101)
    if not isinstance(base, int) or isinstance(base, bool):
        base = 30101
    overrides = inst.get("ports")
    if not isinstance(overrides, dict):
        overrides = {}
    alias = engine.get("alias", "qwen38-27b-subagent")
    api_key = engine.get("api_key", "bonsai2-local")
    endpoints: list[dict] = []
    for i in range(count):
        port = base + i
        ov = overrides.get(i)
        if isinstance(ov, int) and not isinstance(ov, bool):
            port = ov
        endpoints.append(
            {
                "kind": "subagent",
                "port": port,
                "url": f"http://127.0.0.1:{port}",
                "alias": alias,
                "api_key": api_key,
                "index": i,
            }
        )
    return count, endpoints


def main_endpoint(harness: dict) -> dict:
    """The main-only endpoint (M6 control, M8 main half): from
    ``harness.yaml`` main_endpoint (the S3.6 convention). The api_key
    comes from ``harness.yaml`` ``main_api_key`` when present (the
    2026-09-27 post-live-run amendment, H3: the live main stack is
    key-walled — completions need the key; the 401-walled-is-still-UP
    main-PROBE convention is for the health probe, not completions).
    Absent/empty key = keyless (back-compatible: no Authorization
    header, today's behavior)."""
    url = harness.get("main_endpoint", "http://localhost:30001")
    api_key = harness.get("main_api_key", "")
    if not isinstance(api_key, str):
        # G3c round-4 P2: a non-string key (a number, a list, …) is a
        # CONFIG ERROR, not a "keyless" signal. We coerce to "" (keyless)
        # rather than raise — main-PROBE tolerates a keyless main (the
        # health probe is 401-tolerant) and the completions endpoint may
        # genuinely be keyless — but a MIS-TYPED key (e.g. a bare number
        # instead of a string) is silently dropped here. Naming it in a
        # comment (rather than raising) keeps the hermetic / no-key path
        # working while flagging the case a reader should check.
        api_key = ""
    port = 30001
    # parse the port out of the url for the record (best-effort; the url is
    # authoritative for the executor). G3c round-6 P2: the 30001 default is
    # a FALLBACK only — when the url carries an explicit port (e.g. an
    # override or keepalive proxy on a non-standard port) the parsed value
    # wins. The S3.1 contract says the endpoint dict carries the real port;
    # this parser extracts it from the url string (the executor's source of
    # truth), so the recorded port matches what the executor actually
    # targets, not a hardcoded default.
    if ":" in url:
        _, _, port_s = url.rpartition(":")
        if port_s.isdigit():
            port = int(port_s)
    return {
        "kind": "main",
        "port": port,
        "url": url,
        "alias": "main",
        "api_key": api_key,
        "index": -1,
    }


def load_engine_identity(env: Mapping | None = None) -> dict:
    """Read subagent-engine.yaml (the S5.4 "subagent ports from
    subagent-engine.yaml"). Path override: the manager's documented
    ``BONSAI2_ENGINE_YAML`` hook (the S3.2/S3.6 convention)."""
    env = os.environ if env is None else env
    path = Path(env.get("BONSAI2_ENGINE_YAML", DEFAULT_ENGINE_YAML))
    return _load_yaml_map(path)


def load_harness_main(env: Mapping | None = None) -> dict:
    """Read harness.yaml (the S5.4 "main from harness.yaml"). Path
    override: the S3.2 ``BONSAI2_CONFIG`` hook."""
    env = os.environ if env is None else env
    path = Path(env.get("BONSAI2_CONFIG", DEFAULT_HARNESS_YAML))
    return _load_yaml_map(path)


def resolve_for_scenario(
    sid: str,
    engine: dict,
    harness: dict | None = None,
    pool_size: int | None = None,
) -> list[dict]:
    """The endpoints a scenario's tasks run against.

    M6 (main-only control) -> the main endpoint (one).
    M8 (contention) -> the subagent pool (the subagent half; the main half
    is resolved separately by the runner, which measures main t/s under
    the subagent load).
    All other scenarios -> the subagent pool, sized to `pool_size` (or the
    engine's N when None). A scenario that needs subagents on a 0-pool
    returns the main endpoint (the S2.4 main-only fallback: with no
    subagents the control still runs on main).
    """
    from bench import scenarios  # local import to avoid a module cycle at import time

    n, endpoints = subagent_ports(engine)
    if pool_size is not None:
        n = max(0, min(pool_size, len(endpoints)))
        endpoints = endpoints[:n] if pool_size < len(endpoints) else endpoints
    if sid == "M6" or (n == 0 and scenarios.get(sid).get("tools") != "on"):
        return [main_endpoint(harness or {})]
    if n == 0:
        # capability rows (M9/M10) need a subagent; with no pool the runner
        # reports them skipped, but resolve still returns the main endpoint
        # as the fallback target (the S2.4 main-only convention).
        return [main_endpoint(harness or {})]
    return endpoints
