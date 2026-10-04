#!/usr/bin/env python3
"""bonsai2_install.py — MAP 6.1–6.6 / S6: the one-command installer core.

`install.sh` (repo root) is a thin `exec python3 bin/bonsai2_install.py "$@"`
(S6.6: the runtime is bash + the Python installer, no new dependencies).
This module is the S6.3 ordered pipeline:

  (1) prereq checks (S6.2; hard-fail -> named ERR + exit, warn -> continue;
      ATOMIC: check-all before touch-any — a failed prereq writes nothing
      under the opt root, S6.7)
  (2) download + verify the model quants into the cache (S1.1; skip on
      SHA match — bin/bonsai2_model_fetch's idempotent path)
  (3) build-or-verify the runtime image + record its digest (S6.1/S6.4:
      skip when the local image matches the recorded pin; the builder's
      cache pre-verification passes because step 2 ran first)
  (4) detect the main-stack fresh-load footprint from the SGLang startup
      log (pool − available_gpu_mem, read-only `docker logs`); fallbacks:
      live cgroup memory.current, then the S2.6 76.4 default +
      WARN_MAIN_STACK_DEAD (the S6.4 main-only fallback)
  (5) compute N via S1.4 (bonsai2_vram is the formula source);
      `--instances N` overrides (the S0 phased-rollout flag)
  (6) write the four YAMLs (S1.6/S2.6/S3.6/S5.7) surgically — unchanged
      values are not rewritten (S6.4); deploy the bin/ + bench/ runtime
      tree to the opt root; create the /run/bonsai2 subdirs
  (7) install the S2.2 units (manager + one bonsai2-server@i per
      granted instance; system or user scope, auto-detected),
      daemon-reload only on diff, start/restart the manager only when
      something changed
  (8) health checks against the S2.7 probe budget (180 s): full /
      main-only / partial, exit 4 on the manager's i=0 ERR_VRAM_OOM
  (9) the stack-ready report: ONE JSON object on stdout (the S3.1 one-
      document contract; stderr carries the human log), plus the state
      file (S6.5, last 5 kept) and `--rollback` / `--refresh-main`

Exit codes (S6.3, locked): 0 = full or main-only success (incl. the
"unchanged" re-run, incl. "settling" after an unchanged pool re-read);
4 = live-margin OOM (planning granted N ≥ 1, the manager's i=0
allocation OOM); 3 = arch mismatch; 2 = config (a non-arch prereq
hard-fail, a bad config, or an install step the spec maps to ERR_CONFIG).

Memory convention (worked out against the live SGLang log, 2026-09-29):
the SGLang pool line is MiB and converts to GiB (`MiB / 1024` = 121.63
on this box = the S0 "121.6" pool; the spec table's 76.4/8.0/14.78/37.2
are all the same binary-GB convention, so pool GiB − available GiB is
the footprint, and N = floor((pool − footprint − reserved)/budget)
reproduces the S0 worked example N = 2 with the live numbers).
`/proc/meminfo` kB -> GiB likewise. The S6.2 matrix row is 96 GiB.

Hermetic by seam: every external dependency (uname, nvidia-smi, docker,
/proc/meminfo, /etc/os-release, df, the cgroup files, systemctl, the
main endpoint) is a function in `Seams`; the defaults are the real
reads, and the test suite (MAP 6.7) drives the pipeline with faked
seams — S6.7's "fake uname/driver/…" prereq cases.

PyYAML is used at the config edge only (the same box-dep convention as
bin/bonsai2_build_image.py / bin/bonsai2_model_fetch.py / the harness);
the rest is stdlib.
"""

from __future__ import annotations

import argparse
import datetime
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OPT_ROOT = "/opt/bonsai2"
DEFAULT_HEALTH_DIR = "/run/bonsai2"
DEFAULT_PYTHON = "/usr/bin/python3"  # box python with PyYAML (probed 2026-09-29)

HEALTH_BUDGET_S = 180.0  # S2.7 / S6.3 step 8: the probe budget
DRIFT_GB_THRESHOLD = 5.0  # S6.4 policy constant (below the 14.78 GB instance budget)
STATE_KEEP = 5  # S6.5: last 5 state files kept
DEFAULT_MAIN_FOOTPRINT_GB = 76.4  # S2.6 default (the WARN_MAIN_STACK_DEAD fallback)
MEMORY_MATRIX_BYTES = 96 * 1024**2 * 1024  # S6.2: 96 GiB (the S0 pool convention)
MIN_DRIVER = 580
MIN_DISK_FREE_GB = 20.0
MIN_PYTHON = (3, 10)
OS_RELEASE_WARN_ID = "24.04"

EXIT_OK = 0
EXIT_CONFIG = 2
EXIT_ARCH = 3
EXIT_VRAM_OOM = 4

# The S3.2 closed-set summary (S6.3 "the command surface summary").
COMMAND_SURFACE = {
    "status": "bonsai2-status",
    "spawn": "bonsai2-spawn",
    "collect": "bonsai2-collect",
    "steer": "bonsai2-steer",
    "cancel": "bonsai2-cancel",
    "unload": "bonsai2-unload",
    "reload": "bonsai2-reload",
    "health": "bonsai2-health",
    "list-instances": "bonsai2-list-instances",
    "bench": "bonsai2-bench",
}
# The bin/ deploy set (repo bin/ -> <opt>/bin/, one tree; S6.1 "the
# repo mirrors the host /opt/bonsai2/ layout 1:1").
BIN_DEPLOY = [
    "bonsai2-manager",
    "bonsai2_harness.py",
    "bonsai2_vram.py",
    "bonsai2_tools.py",
    "bonsai2_model_fetch.py",
    "bonsai2_build_image.py",
    "bonsai2_render_config.py",
    "bonsai2-status",
    "bonsai2-list-instances",
    "bonsai2-health",
    "bonsai2-spawn",
    "bonsai2-taskworker.py",
    "bonsai2-collect",
    "bonsai2-steer",
    "bonsai2-cancel",
    "bonsai2-unload",
    "bonsai2-reload",
    "bonsai2-bench",
]


def _log(msg: str) -> None:
    print(f"[install] {msg}", file=sys.stderr)


def _warn(msg: str) -> None:
    print(f"WARN {msg}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Seams — every external dependency behind a named function (S6.7 fakes)
# ---------------------------------------------------------------------------


def _uname_m_real() -> str:
    return subprocess.run(
        ["uname", "-m"], capture_output=True, text=True, check=True
    ).stdout.strip()


def _python_version_real() -> tuple[int, ...]:
    return (sys.version_info.major, sys.version_info.minor)


def _docker_version_ok_real() -> bool:
    return (
        subprocess.run(
            ["docker", "version", "--format", "{{.Server.Version}}"],
            capture_output=True,
            text=True,
            check=False,
        ).returncode
        == 0
    )


def _driver_version_real() -> int | None:
    """The NVIDIA driver major version (S6.2: nvidia-smi parses, >= 580)."""
    r = subprocess.run(
        ["nvidia-smi", "--query-gpu=driver_version", "--format=csv,noheader"],
        capture_output=True,
        text=True,
        check=False,
    )
    if r.returncode != 0:
        return None
    m = re.match(r"(\d+)", r.stdout.strip())
    return int(m.group(1)) if m else None


def _df_free_gb_real(path: str = "/opt") -> float:
    r = subprocess.run(["df", "-B1", path], capture_output=True, text=True, check=False)
    if r.returncode != 0:
        return 0.0
    fields = r.stdout.splitlines()[-1].split()
    # 1024**3 = GiB, the box-wide memory convention (S1.4 pool, the S6.2
    # matrix) — so MIN_DISK_FREE_GB=20.0 means 20 GiB of free space, not
    # 20 decimal GB. (df -B1 reports 1-byte blocks; fields[3] = available.)
    return int(fields[3]) / 1024**3 if len(fields) >= 4 else 0.0


def _mem_total_bytes_real() -> float:
    for line in Path("/proc/meminfo").read_text().splitlines():
        if line.startswith("MemTotal:"):
            return float(line.split()[1]) * 1024.0
    raise OSError("MemTotal not in /proc/meminfo")


def _os_release_id_real() -> str:
    try:
        text = Path("/etc/os-release").read_text()
    except OSError:
        return ""
    m = re.search(r'^(?:ID_LIKE|ID)="?([^"\n]+)', text, re.MULTILINE)
    return m.group(1).strip() if m else ""


def _docker_logs_lines_real(container: str, tail: int = 0) -> list[str]:
    """Read-only `docker logs` (the S6.4 main-stack contract: logs /
    stats / inspect / cgroup reads ONLY). `tail=0` = the full log: the
    SGLang pool line is emitted ONCE at engine startup, so a small tail
    window can miss it on a long-lived container (the box log is a few
    MB — a cheap read-only cost)."""
    args = ["docker", "logs"]
    if tail:
        args += ["--tail", str(tail)]
    args.append(container)
    r = subprocess.run(args, capture_output=True, text=True, check=False)
    if r.returncode != 0:
        return []
    return (r.stdout + r.stderr).splitlines()


def _docker_image_id_real(tag: str) -> str | None:
    r = subprocess.run(
        ["docker", "image", "inspect", "--format", "{{.Id}}", tag],
        capture_output=True,
        text=True,
        check=False,
    )
    if r.returncode != 0:
        return None
    return r.stdout.strip() or None


def _docker_container_id_real(name: str) -> str | None:
    r = subprocess.run(
        ["docker", "inspect", "-f", "{{.Id}}", name],
        capture_output=True,
        text=True,
        check=False,
    )
    if r.returncode != 0:
        return None
    out = r.stdout.strip()
    return out.replace("/", "") or None


def _cgroup_memory_current_bytes_real(flat_id: str) -> float | None:
    """The container cgroup's memory.current (the S2.3 main_used_gb
    convention: system.slice shape first, then the raw docker/ shape)."""
    for cand in (
        Path(f"/sys/fs/cgroup/system.slice/docker-{flat_id}.scope/memory.current"),
        Path(f"/sys/fs/cgroup/docker/{flat_id}/memory.current"),
    ):
        if cand.is_file():
            try:
                return float(cand.read_text().strip())
            except (OSError, ValueError):
                return None
    return None


def _http_probe_real(url: str, path: str, timeout_s: float = 5.0) -> bool:
    """The S3.6 liveness probe convention: any HTTP response (incl. 401,
    the key-walled main stack) means UP; connection failure means down."""
    try:
        with urllib.request.urlopen(url.rstrip("/") + path, timeout=timeout_s):
            return True
    except urllib.error.HTTPError:
        return True
    except (TimeoutError, urllib.error.URLError, OSError):
        return False


def _systemctl_real(args: list[str]) -> int:
    return subprocess.run(
        ["systemctl", *args], capture_output=True, text=True, check=False
    ).returncode


def _run_python_real(
    python: str, script: str, argv: list[str], env: dict | None = None
) -> subprocess.CompletedProcess:
    return subprocess.run(
        [python, script, *argv],
        capture_output=True,
        text=True,
        check=False,
        env=dict(os.environ) if env is None else env,
    )


@dataclass
class Seams:
    """The external world. The defaults are the real box reads; tests
    swap any of them for fakes (a faked uname, a faked driver, a faked
    meminfo — the S6.7 prereq-fail rows). All return values are plain
    types (no subprocess results leak past the seam)."""

    uname_m = staticmethod(_uname_m_real)
    nvidia_smi_driver = staticmethod(_driver_version_real)
    docker_version_ok = staticmethod(_docker_version_ok_real)
    python_version = staticmethod(_python_version_real)
    df_free_gb = staticmethod(_df_free_gb_real)
    mem_total_bytes = staticmethod(_mem_total_bytes_real)
    os_release_id = staticmethod(_os_release_id_real)
    docker_logs = staticmethod(_docker_logs_lines_real)
    docker_image_id = staticmethod(_docker_image_id_real)
    docker_container_id = staticmethod(_docker_container_id_real)
    cgroup_memory_current = staticmethod(_cgroup_memory_current_bytes_real)
    main_probe = staticmethod(_http_probe_real)
    systemctl = staticmethod(_systemctl_real)
    run_python = staticmethod(_run_python_real)
    sleep = staticmethod(time.sleep)
    now = staticmethod(time.time)
    is_root = staticmethod(lambda: os.geteuid() == 0)
    repo_root: Path = REPO_ROOT
    user_unit_dir: Path = Path.home() / ".config" / "systemd" / "user"
    system_unit_dir: Path = Path("/etc/systemd/system")
    # the S2.7/S6.3 step 8 health document location (the manager writes
    # it; --health-dir is the test seam for a scratch dir)
    health_dir_override: str | None = None

    def unit_dir(self, scope: str) -> Path:
        return self.system_unit_dir if scope == "system" else self.user_unit_dir

    def sc(self, scope: str, *args: str) -> int:
        """systemctl with the scope prefix (user scope = `--user`, the
        S2.2 unit shape this box runs; the documented @gpu convention)."""
        prefix = [] if scope == "system" else ["--user"]
        return self.systemctl(prefix + list(args))


def _as_sha256(value: str) -> str:
    """Guarantee the `sha256:<64-hex>` form the S1.6 image pin uses, for the
    stack-ready report slice and the re-rendered engine YAML. Every reachable
    source already carries the prefix (the tracked template pin, the local
    `docker inspect` id, the builder's normalize_digest); this is the explicit
    guarantee the report slice + the pin write rely on. A bare 64-hex digest
    gets the prefix; an empty value stays empty (never fabricate a digest)."""
    v = (value or "").strip()
    if not v:
        return ""
    if v.lower().startswith("sha256:"):
        return v
    if re.fullmatch(r"[0-9a-fA-F]{64}", v):
        return f"sha256:{v.lower()}"
    return v


# ---------------------------------------------------------------------------
# Prereq checks (6.1 / S6.2) — check-all before touch-any
# ---------------------------------------------------------------------------


def check_prereqs(seams: Seams) -> tuple[list[tuple[str, str, int, str]], list[str]]:
    """Run every S6.2 row. Returns (failures, warnings): failures are
    (row_name, err, exit_code, detail) for each HARD row that failed;
    warnings are the WARN_* strings. ATOMIC: nothing here touches the
    filesystem (S6.7: a faked hard-fail must leave the opt root empty)."""
    failures: list[tuple[str, str, int, str]] = []
    warnings: list[str] = []

    arch = seams.uname_m()
    if arch != "aarch64":
        failures.append(
            ("arch", "ERR_ARCH", EXIT_ARCH, f"uname -m = {arch!r}, want aarch64")
        )
    else:
        _log(f"arch: {arch} ok")

    driver = seams.nvidia_smi_driver()
    if driver is None:
        failures.append(
            ("driver", "ERR_DRIVER", EXIT_CONFIG, "nvidia-smi did not parse (S6.2)")
        )
    elif driver < MIN_DRIVER:
        failures.append(
            (
                "driver",
                "ERR_DRIVER",
                EXIT_CONFIG,
                f"driver {driver} < {MIN_DRIVER} (S6.2)",
            )
        )
    else:
        _log(f"driver: {driver} ok (>= {MIN_DRIVER})")

    if not seams.docker_version_ok():
        failures.append(
            ("docker", "ERR_DOCKER", EXIT_CONFIG, "docker version failed (S6.2)")
        )
    else:
        _log("docker: runtime present")

    pv = seams.python_version()
    if (pv[0], pv[1]) < MIN_PYTHON:
        failures.append(
            ("python", "ERR_PYTHON", EXIT_CONFIG, f"python {pv[0]}.{pv[1]} < 3.10")
        )
    else:
        _log(f"python: {pv[0]}.{pv[1]} ok (>= 3.10)")

    free = seams.df_free_gb()
    if free < MIN_DISK_FREE_GB:
        failures.append(
            (
                "disk",
                "ERR_DISK",
                EXIT_CONFIG,
                f"/opt free {free:.1f} GB < {MIN_DISK_FREE_GB} GB",
            )
        )
    else:
        _log(f"/opt free: {free:.1f} GB ok (>= {MIN_DISK_FREE_GB} GB)")

    os_id = seams.os_release_id()
    if OS_RELEASE_WARN_ID not in os_id:
        w = f"WARN_OS_VERSION: /etc/os-release id={os_id!r} (expected {OS_RELEASE_WARN_ID}; S6.2 warn row)"
        warnings.append(w)
        _warn(w)
    else:
        _log(f"os: {os_id} ok")

    mem_bytes = seams.mem_total_bytes()
    if mem_bytes < MEMORY_MATRIX_BYTES:
        w = (
            "WARN_MEMORY_BELOW_MATRIX: unified memory < 96 GiB (S6.2 warn row) — "
            "proceeding, N recomputed per S1.4/S2.3"
        )
        warnings.append(w)
        _warn(w)
    else:
        _log(f"unified memory: {mem_bytes / 1024**3:.1f} GiB ok (>= 96 GiB matrix)")

    return failures, warnings


# ---------------------------------------------------------------------------
# S6.3 step 4 — main-stack fresh-load footprint detection
# ---------------------------------------------------------------------------

# SGLang startup-log lines (probed on this box 2026-09-29):
#   "Failed to get GPU memory capacity from nvidia-smi. Falling back to
#    torch.cuda.mem_get_info(). Reported total GPU memory per device
#    (MiB): [124546], using min: 124546 MiB."
#   "... max_total_num_tokens=..., context_len=..., available_gpu_mem=52.11 GB"
_POOL_RE = re.compile(r"total GPU memory per device \(MiB\): \[(\d+)\]")
_AVAIL_RE = re.compile(r"available_gpu_mem=([0-9]+(?:\.[0-9]+)?)\s*GB")


def parse_sglang_footprint(log_lines: list[str]) -> tuple[float, str] | None:
    """S6.3 step 4 primary: pool − available from the LAST startup
    record. Returns (footprint_gb, source) or None when the log does not
    carry both values. The pool line is the box's unified memory in MiB
    (MiB/1024 = GiB = the S0 "121.6" pool convention); `available_gpu_mem`
    is the post-load remainder in GB; their difference is the fresh-load
    footprint (the spec's "compute pool − available")."""
    pool_mib = None
    avail_gb = None
    for line in log_lines:
        m = _POOL_RE.search(line)
        if m:
            pool_mib = int(m.group(1))
        a = _AVAIL_RE.search(line)
        if a:
            avail_gb = float(a.group(1))
    if pool_mib is None or avail_gb is None:
        return None
    pool_gb = pool_mib / 1024.0  # MiB -> GiB (the S1.4/S0 pool convention)
    footprint = pool_gb - avail_gb
    if footprint <= 0 or footprint >= pool_gb:
        return None  # a degenerate parse (e.g. a stale pre-load line pair)
    return footprint, "sglang log (pool - available_gpu_mem)"


def detect_main_footprint(
    seams: Seams,
    container: str,
    recorded_fallback_gb: float,
) -> tuple[float, str, bool]:
    """(4) — (footprint_gb, source, main_alive). Falls through the S6.3
    chain: SGLang startup log -> live cgroup memory.current (the S2.3
    main_used_gb shape, while the main stack is awake) -> the S2.6
    76.4 default + WARN_MAIN_STACK_DEAD (S6.4: install continues, the
    report names it)."""
    lines = seams.docker_logs(container)
    parsed = parse_sglang_footprint(lines) if lines else None
    if parsed is not None:
        footprint, source = parsed
        _log(f"main footprint: {footprint:.2f} GB ({source})")
        return footprint, source, True

    cid = seams.docker_container_id(container)
    if cid:
        cur = seams.cgroup_memory_current(cid)
        if cur is not None and cur > 0:
            footprint = cur / 1024**3  # bytes -> GiB (the S0 pool convention)
            _log(
                f"main footprint: {footprint:.2f} GB (live cgroup - SGLang log unavailable)"
            )
            return footprint, "live cgroup", True

    _warn(
        f"WARN_MAIN_STACK_DEAD: {container} unreadable - using the S2.6 default "
        f"{recorded_fallback_gb} GB (S6.4: install continues)"
    )
    return recorded_fallback_gb, "S2.6 default (main stack dead)", False


# ---------------------------------------------------------------------------
# S6.3 step 5 — N computation (S1.4; bonsai2_vram is the formula source)
# ---------------------------------------------------------------------------


def compute_n(
    mem_total_bytes: float,
    main_footprint_gb: float,
    os_reserved_gb: float,
    per_instance_budget_gb: float,
) -> tuple[int, float]:
    """S1.4 via bonsai2_vram: budget = pool − main − reserved; N =
    floor(budget / per_instance_budget_gb) clamped [0, 8]. Returns
    (n, budget_gb) — the budget is surfaced in the report (the S6.3
    "N correct per S1.4" audit trail)."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import bonsai2_vram

    pool_gb = mem_total_bytes / 1024**3
    budget = bonsai2_vram.subagent_budget_gb(pool_gb, main_footprint_gb, os_reserved_gb)
    return bonsai2_vram.instance_count(budget, per_instance_budget_gb), budget


# ---------------------------------------------------------------------------
# YAML surgery — surgical, comment-preserving (S6.4: unchanged values
# are NOT rewritten; only the detected-state keys move). Block-scoped:
# a key matches only inside its named top-level block, so `count:` /
# `tag:` / `sha256:` never collide with a sibling block's keys.
# ---------------------------------------------------------------------------


def _block_text(text: str, key: str) -> str | None:
    """The lines under a top-level `key:` at deeper indent, up to the
    next top-level key or EOF."""
    lines = text.splitlines()
    out: list[str] = []
    in_block = False
    for line in lines:
        m = re.match(r"^([ \t]*)([\w.-]+):", line)
        if not m:
            if in_block:
                out.append(line)
            continue
        indent, k = m.group(1), m.group(2)
        if not indent:
            if in_block:
                break
            in_block = k == key
            continue
        if in_block:
            out.append(line)
    if not in_block:
        return None
    return "\n".join(out)


def _set_yaml_scalar_block(
    text: str, block: str, key: str, value: str
) -> tuple[str, bool]:
    """Set `key:` to `value` inside the top-level `block:` (indentation-
    preserving; trailing comments kept). Returns (new_text, changed).
    `changed` is VALUE-sensitive: an already-equal value (modulo
    quoting/whitespace) reports changed=False — the S6.4 "unchanged
    re-run rewrites nothing" row (a re-rendered line with the same
    value must not count as a drift action)."""
    block_txt = _block_text(text, block)
    if block_txt is None:
        return text, True  # missing block: the caller's append-last fallback
    want = value.strip('"').strip("'")
    pat = re.compile(
        rf"^(?P<indent>[ \t]*){re.escape(key)}:[ \t]*(?P<val>\S+?)(?P<tail>(?:[ \t]+#.*)?)[ \t]*$"
    )
    lines = block_txt.splitlines()
    for i, line in enumerate(lines):
        m = pat.match(line)
        if not m:
            continue
        cur = m.group("val").strip('"').strip("'")
        if cur == want:
            return text, False  # the value already matches (comment kept)
        new_line = f"{m.group('indent')}{key}: {value}"
        if m.group("tail"):
            new_line += "  " + m.group("tail").strip()
        # splice back into the full text: the block starts at the first
        # top-level `block:` line; its body runs until the next top-level
        # key (a non-indented, non-blank line) or EOF
        full = text.splitlines()
        bstart = next(
            (j for j, l in enumerate(full) if re.match(rf"^{re.escape(block)}:", l)),
            None,
        )
        if bstart is None:
            return text, True
        bend = bstart + 1
        while bend < len(full) and (not full[bend].strip() or full[bend][0] in " \t"):
            bend += 1
        head = full[: bstart + 1]
        tail = full[bend:]
        lines[i] = new_line
        return "\n".join(head + lines + tail) + (
            "\n" if text.endswith("\n") else ""
        ), True
    return text, True


def _set_yaml_scalar_top(text: str, key: str, value: str) -> tuple[str, bool]:
    """Set a TOP-LEVEL `key:` scalar (the os_reserved_gb shape)."""
    lines = text.splitlines()
    pat = re.compile(rf"^(?P<indent>[ \t]*){re.escape(key)}:[ \t]*")
    for i, line in enumerate(lines):
        m = pat.match(line)
        if m and not m.group("indent"):
            new_line = f"{key}: {value}"
            cm = re.search(r"[ \t]+(#.*)$", line)
            if cm:
                new_line += "  " + cm.group(1).strip()
            if new_line == line:
                return text, False
            lines[i] = new_line
            return "\n".join(lines) + ("\n" if text.endswith("\n") else ""), True
    out = lines + [f"{key}: {value}"]
    return "\n".join(out) + ("\n" if text.endswith("\n") else ""), True


def _block_scalar(block_txt: str, key: str) -> str | None:
    m = re.search(rf"(?m)^[ \t]*{re.escape(key)}:[ \t]*(\S+)", block_txt)
    if not m:
        return None
    return m.group(1).strip('"').strip("'")


def render_engine_yaml(
    text: str, *, count: int, image_tag: str, image_sha: str
) -> tuple[str, list[str]]:
    """S1.6 rendered engine YAML: instances.count + image.pin.{tag,sha256}.
    Returns (new_text, actions) — actions name only the KEYS that moved
    (S6.4: an unchanged re-run rewrites nothing)."""
    actions: list[str] = []
    for block, key, val, label in (
        ("instances", "count", str(count), f"instances.count={count}"),
        ("image", "tag", f'"{image_tag}"', f"image.pin.tag={image_tag}"),
        ("image", "sha256", f'"{image_sha}"', "image.pin.sha256=<digest>"),
    ):
        text, changed = _set_yaml_scalar_block(text, block, key, val)
        if changed:
            actions.append(label)
    return text, actions


def render_serving_yaml(text: str, *, footprint_gb: float) -> tuple[str, list[str]]:
    """S2.6: main_stack.footprint_gb (the S6.4 drift-watch baseline).
    boot_target_s is NOT touched here — the manager writes it back from
    the measured boot (S2.6/MAP 2.9); install only moves the footprint."""
    out, changed = _set_yaml_scalar_block(
        text, "main_stack", "footprint_gb", f"{footprint_gb:.2f}"
    )
    actions = [f"main_stack.footprint_gb={footprint_gb:.2f}"] if changed else []
    return out, actions


# ---------------------------------------------------------------------------
# Units (S2.2 shape — the @gpu-tested ExecStart verbatim)
# ---------------------------------------------------------------------------

# S1.3 flags verbatim (image Dockerfile.runtime CMD + the @gpu unit
# shape; values from the rendered engine YAML).
_UNIT_SERVICE_TMPL = (
    "[Unit]\n"
    "Description=bonsai2 subagent instance %i (llama-server container)\n"
    "Requires=docker.service\n"
    "After=docker.service bonsai2-sglang.service\n"
    "StartLimitIntervalSec=300\n"
    "StartLimitBurst=3\n"
    "\n"
    "[Service]\n"
    "ExecStartPre=/usr/bin/docker rm -f bonsai2-server-%i\n"
    "%EXECSTART%\n"
    "Restart=on-failure\n"
    "RestartSec=5\n"
    # F10 (first-practical-run remediation, 2026-10-03): the render bug —
    # systemd logged "Invalid CPU quota '64%%', ignoring" because this
    # template reaches systemd as a PLAIN string (only the %EXECSTART%
    # token is substituted via .replace, no % / .format pass), so the
    # doubled %% survived to the unit. A single % is the valid systemd
    # form (units/bonsai2-server@.service carries 64% too).
    "CPUQuota=64%\n"
    "\n"
    "[Install]\n"
    "WantedBy=multi-user.target\n"
)


def instance_exec_start(
    *,
    budget_gb: float,
    ctx: int,
    parallel: int,
    kv: str,
    port: int,
    image_tag: str,
    model_file: str,
    api_key: str,
    reasoning: str,
    alias: str,
    tools: list[str] | None = None,
) -> str:
    """The S2.2 ExecStart (the full-command docker run, probed 2026-09-22:
    bare `docker run img --flag` dies under the nvidia entrypoint).
    `--tools {set}` is appended when the S1.6 tools block is enabled
    (the S1.6 tools amendment, A8: `tools` = the set list; None = off)."""
    tools_flags = f" --tools {' '.join(tools)}" if tools else ""
    return (
        "ExecStart=/usr/bin/docker run --rm --name bonsai2-server-%i "
        "--gpus all "
        f"--memory {budget_gb:g}g --memory-swap {budget_gb:g}g "
        "--shm-size 4g --network host --ipc=host "
        f"{image_tag} "
        "/opt/bonsai2/bin/llama-server "
        f"--model /opt/bonsai2/models/{model_file} "
        f"--ctx-size {ctx} --parallel {parallel} --batch-size 2048 "
        f"--cache-type-k {kv} --cache-type-v {kv} "
        f"--port {port} --api-key {api_key} "
        f"--reasoning-format {reasoning} --alias {alias}"
        f"{tools_flags}"
    )


def render_instance_unit(
    i: int,
    *,
    budget_gb: float,
    ctx: int,
    parallel: int,
    kv: str,
    port_base: int,
    image_tag: str,
    model_file: str,
    api_key: str,
    reasoning: str,
    alias: str,
    tools: bool = False,
    tools_set: list[str] | None = None,
) -> str:
    return _UNIT_SERVICE_TMPL.replace(
        "%EXECSTART%",
        instance_exec_start(
            budget_gb=budget_gb,
            ctx=ctx,
            parallel=parallel,
            kv=kv,
            port=port_base + i,
            image_tag=image_tag,
            model_file=model_file,
            api_key=api_key,
            reasoning=reasoning,
            alias=alias,
            tools=tools_set if tools else None,
        ),
    )


# ---------------------------------------------------------------------------
# Health check (S6.3 step 8 — the S2.7 probe budget against health.json)
# ---------------------------------------------------------------------------


def read_health(health_dir: Path) -> dict | None:
    p = health_dir / "health.json"
    if not p.is_file():
        return None
    try:
        doc = json.loads(p.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    return (
        doc
        if isinstance(doc, dict) and isinstance(doc.get("instances"), list)
        else None
    )


def check_health(
    *,
    health_dir: Path,
    expected_n: int,
    main_url: str,
    main_probe_path: str,
    main_probe,
    budget_s: float = HEALTH_BUDGET_S,
    poll_s: float = 2.0,
    sleep=time.sleep,
    now=time.monotonic,
) -> tuple[str, dict, list[str]]:
    """Wait for the S2.7 probe outcome on health.json.

    Returns (outcome, last_doc, warnings) with outcome in:
      "full"      — a settled document with the RUNNING set all healthy
      "partial"   — the granted prefix loaded, rest ungranted (the
                    manager's WARN_VRAM_SHORTFALL at i > 0): a stable
                    (unchanged across two consecutive polls) document
                    whose stack is not all-healthy
      "main-only" — a settled 0-instance document: instant on
                    main_only_reason=ERR_VRAM_OOM (the exit-4 path),
                    otherwise accepted once STABLE across two polls
                    (guards the transient 0-instance window the manager
                    writes right after its restart, before the first
                    instance's health probe lands)
      "oom"       — the manager's i=0 ERR_VRAM_OOM (main_only_reason)
      "timeout"   — budget exhausted, doc never settled

    A document whose `stack` is "healthy" (the manager's own derivation:
    every running instance healthy) is settled by definition — it may
    carry a DIFFERENT instance count than expected_n when a re-install
    changed N and the manager's next rewrite has not yet landed; the
    running set is what the box actually serves, and the S6.3 report
    states both. (The manager rewrites health.json within one
    health_poll_s of the config change anyway.)
    """
    # P1 (G3c r5): the deadline loop must use the injectable `now` seam,
    # NOT raw time.monotonic() — a faked `sleep` that advances nothing
    # would otherwise burn the REAL 180 s budget in hermetic tests.
    deadline = now() + budget_s
    last: dict = {}
    prev: dict | None = None
    while now() < deadline:
        doc = read_health(health_dir)
        if doc is not None:
            last = doc
            stable = prev is not None and _doc_eq(prev, doc)
            states = [e.get("state") for e in doc.get("instances", [])]
            n_running = len(states)
            stack = doc.get("stack")
            main_only_reason = doc.get("main_only_reason")
            if main_only_reason == "ERR_VRAM_OOM" and expected_n >= 1:
                _log("health: i=0 ERR_VRAM_OOM (exit 4 path)")
                return "oom", doc, []
            if n_running == 0:
                if main_only_reason is not None and stable:
                    # a settled, STABLE main-only document (budget_zero /
                    # the post-fallback zero / user_unloaded)
                    _log("health: main-only settled")
                    return "main-only", doc, []
                # 0 running without a reason (or a transient one): the
                # manager may still be planning/falling back or the
                # document is the pre-spawn window of a restart - keep
                # polling until the budget
            elif stack == "healthy" and all(s == "healthy" for s in states):
                _log(f"health: {n_running} instances healthy, stack settled")
                hwarn: list[str] = []
                if n_running != expected_n:
                    # the healthy document carries a DIFFERENT count than
                    # expected_n: a re-install moved N (grew 2->3, or shrank)
                    # and the manager's next rewrite has not landed yet. The
                    # running set is what the box actually serves; name both
                    # counts so the S6.3 report is explicit (S6.4/S6.3 step 8).
                    hwarn.append(
                        f"WARN_HEALTH_SETTLING: the settled document runs "
                        f"{n_running} instance(s) but this install expects "
                        f"{expected_n} - the manager will re-converge the pool"
                    )
                return "full", doc, hwarn
            elif stable:
                # unchanged across two polls and not all-healthy: a
                # settled partial (the S1.4 allocation-tier shortfall)
                _log(
                    f"health: {sum(1 for s in states if s == 'healthy')}/{expected_n} "
                    f"healthy - settled partial (stack={stack})"
                )
                return "partial", doc, []
        prev = doc if doc is not None else None
        sleep(poll_s)
    states = [e.get("state") for e in last.get("instances", [])] if last else []
    if last and last.get("main_only_reason") == "ERR_VRAM_OOM" and expected_n >= 1:
        return "oom", last, []
    if last:
        n = len(states)
        if n == 0:
            return (
                "main-only",
                last,
                [f"WARN_HEALTH_TIMEOUT: settled main-only after {budget_s:.0f} s"],
            )
        if any(s == "healthy" for s in states):
            return (
                "partial",
                last,
                [
                    (
                        f"WARN_HEALTH_TIMEOUT: {sum(1 for s in states if s == 'healthy')}/{expected_n} "
                        f"healthy after the {budget_s:.0f} s S2.7 budget"
                    )
                ],
            )
    return (
        "timeout",
        last,
        [f"WARN_HEALTH_TIMEOUT: no settled health document within {budget_s:.0f} s"],
    )


def _doc_eq(a: dict, b: dict) -> bool:
    """Full-document structural equality: `json.dumps(..., sort_keys=True)`
    on BOTH documents. This is a STRICT equality over every field — the
    docstring's old "ignores timestamps" claim was wrong (there is no
    field-exclusion), so the "stable across two polls" check in
    `check_health` depends on the manager NOT emitting a per-poll
    timestamp/monotonic counter into health.json (the current S2.7 schema
    does not; a future one would defeat stability detection — G3c r2 P2
    note, deferred)."""
    return json.dumps(a, sort_keys=True, default=str) == json.dumps(
        b, sort_keys=True, default=str
    )


# ---------------------------------------------------------------------------
# State files (6.5 / S6.5) + rollback
# ---------------------------------------------------------------------------


def _ts_tag(now: float) -> str:
    # local time, explicit tz (DTZ006): the state-file ts tag is a local
    # wall-clock label, not a UTC epoch.
    return (
        datetime.datetime.fromtimestamp(now, tz=datetime.timezone.utc)
        .astimezone()
        .strftime("%Y%m%d-%H%M%S")
    )


def _dump_yaml(d: dict, indent: int = 0) -> str:
    """A minimal YAML dumper (state files are read by humans + the
    rollback path; PyYAML at the edge is fine but this keeps the writer
    dependency-free)."""

    def _scalar(v) -> str:
        if isinstance(v, bool):
            return "true" if v else "false"
        if v is None:
            return "null"
        if isinstance(v, (int, float)):
            return str(v)
        return json.dumps(str(v))  # safe quoting

    out: list[str] = []
    for k, v in d.items():
        pad = "  " * indent
        if isinstance(v, dict):
            out.append(f"{pad}{k}:")
            out.append(_dump_yaml(v, indent + 1))
        elif isinstance(v, list):
            if not v:
                out.append(f"{pad}{k}: []")
            else:
                out.append(f"{pad}{k}:")
                for item in v:
                    if isinstance(item, dict):
                        out.append(f"{pad}  -")
                        out.append(_dump_yaml(item, indent + 2))
                    else:
                        out.append(f"{pad}  - {_scalar(item)}")
        else:
            out.append(f"{pad}{k}: {_scalar(v)}")
    return "\n".join(out)


def write_state(opt_root: Path, state: dict, now: float) -> Path:
    etc = opt_root / "etc"
    etc.mkdir(parents=True, exist_ok=True)
    p = etc / f"state-{_ts_tag(now)}.yaml"
    text = _dump_yaml(state) + "\n"
    tmp = p.with_suffix(".yaml.tmp")
    tmp.write_text(text)
    os.replace(tmp, p)
    # rotation: last STATE_KEEP kept (S6.5)
    states = sorted(glob.glob(str(etc / "state-*.yaml")))
    for old in states[:-STATE_KEEP]:
        os.remove(old)
    _log(f"state: wrote {p.name} ({len(states)} kept)")
    return p


def _load_yaml_file(path: Path) -> dict:
    """Read a YAML file. PyYAML at the edge when available; otherwise a
    stdlib fallback that covers the state-file subset we WRITe with
    _dump_yaml (two-space indented mappings, JSON-quoted scalars, lists
    of scalars) — the S6.7 bare-box-python3 convention, same shape as
    bench/doctrine_audit.py's stdlib fallback. The full YAML reader is
    preferred when PyYAML is importable (the box python has it)."""
    try:
        import yaml  # PyYAML at the edge (the box-dep convention)

        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except ImportError:
        return _parse_state_yaml(path.read_text())


def _parse_state_yaml(text: str) -> dict | list:
    """The stdlib fallback for _dump_yaml output (see _load_yaml_file)."""
    lines = [
        ln for ln in text.splitlines() if ln.strip() and not ln.lstrip().startswith("#")
    ]

    def _scalar(v: str):
        v = v.strip()
        if v.startswith('"'):
            # the writer is json.dumps: a double-quoted JSON string is the
            # primary form — parse it as-is (apostrophes inside are legal
            # JSON, so NO quote-replacement: replacing ' with " would
            # break "the pin's value" into an unescaped quote).
            try:
                return json.loads(v)
            except json.JSONDecodeError:
                pass
            try:
                return json.loads(v.replace("'", '"'))
            except json.JSONDecodeError:
                return v.strip("\"'")
        if v in ("true", "false"):
            return v == "true"
        if v in ("null", "~"):
            return None
        try:
            return int(v)
        except ValueError:
            pass
        try:
            return float(v)
        except ValueError:
            return v

    pos = 0

    def parse_block(indent: int):
        nonlocal pos
        # decide mapping vs list from the first line
        if pos >= len(lines):
            return {}
        first = lines[pos]
        if first.lstrip().startswith("- "):
            out_list = []
            while pos < len(lines):
                ln = lines[pos]
                ind = len(ln) - len(ln.lstrip())
                if ind != indent or not ln.lstrip().startswith("- "):
                    break
                out_list.append(_scalar(ln.lstrip()[2:]))
                pos += 1
            return out_list
        out = {}
        while pos < len(lines):
            ln = lines[pos]
            ind = len(ln) - len(ln.lstrip())
            if ind != indent:
                break
            m = re.match(r"^([ \t]*)([\w.-]+):\s*(.*)$", ln)
            if not m:
                pos += 1
                continue
            key, val = m.group(2), m.group(3)
            pos += 1
            if val == "":
                # nested block: deeper-indented lines (or none -> empty dict)
                if pos < len(lines):
                    nind = len(lines[pos]) - len(lines[pos].lstrip())
                    if nind > indent:
                        out[key] = parse_block(nind)
                        continue
                out[key] = {}
            else:
                out[key] = _scalar(val)
        return out

    return parse_block(0)


def rollback(
    seams: Seams,
    state_file: Path,
    opt_root: Path,
    scope: str,
) -> tuple[int, dict]:
    """`--rollback <state-<ts>.yaml>` (S6.5): restore the state's four
    YAMLs + the units, restart the manager; binary/model rollback =
    re-point the pin (the cache is kept). Returns (exit, report)."""
    if not state_file.is_file():
        _log(f"rollback: state file missing: {state_file}")
        return EXIT_CONFIG, {"status": "error", "error": "state file missing"}
    try:
        state = _load_yaml_file(state_file)
    except Exception as exc:  # noqa: BLE001 - deliberately broad: a corrupt
        # state file can surface as a PyYAML YAMLError OR a fallback-parse
        # failure; both must exit EXIT_CONFIG, not crash with exit 1
        # (outside the S6.3 closed set {0, 2, 3, 4} - G3c r2 P1).
        _log(f"rollback: state file unreadable: {exc}")
        return EXIT_CONFIG, {
            "status": "error",
            "error": f"state file unreadable: {exc}",
        }
    if not isinstance(state, dict):
        _log("rollback: state file is empty or not a mapping - nothing to restore")
        return EXIT_CONFIG, {
            "status": "error",
            "error": "state file is empty or not a mapping",
        }
    cfg = state.get("config") or {}
    etc = opt_root / "etc"
    etc.mkdir(parents=True, exist_ok=True)
    for name in ("subagent-engine.yaml", "serving.yaml", "harness.yaml", "bench.yaml"):
        blob = cfg.get(name)
        if blob is None:
            _log(f"rollback: {name} not in the state file - keeping the current one")
            continue
        dst = etc / name
        tmp = dst.with_suffix(dst.suffix + ".tmp")
        tmp.write_text(blob if isinstance(blob, str) else _dump_yaml(blob))
        os.replace(tmp, dst)
        _log(f"rollback: restored {name}")
    # P0 (G3c r3): the unit/manager re-render below is driven by the RESTORED
    # engine yaml. If the state's config block lacks subagent-engine.yaml,
    # NOTHING was restored and (etc/subagent-engine.yaml) is the STALE on-disk
    # file - falling through would re-render units from the current N and
    # report "rolled-back" with an inconsistent N. Treat a missing engine
    # blob as a clean EXIT_CONFIG error.
    if "subagent-engine.yaml" not in cfg:
        _log(
            "rollback: state file has no subagent-engine.yaml config - nothing to restore"
        )
        return EXIT_CONFIG, {
            "status": "error",
            "error": "state file has no subagent-engine.yaml config block - cannot restore",
        }
    # units: re-render from the restored engine yaml + re-install the
    # manager unit (the S6.4 unit-diff path). P2 (G3c r4): the restored
    # blob was written by THIS function's tmp+os.replace path a moment ago,
    # but a corrupt blob in the state file can still carry an unreadable or
    # malformed instances block — an unguarded read_text()/int()/float()
    # would raise OSError/ValueError to main() -> exit 1, outside the S6.3
    # closed set {0,2,3,4}. Guard the whole parse section with a clean
    # EXIT_CONFIG.
    try:
        engine_text = (etc / "subagent-engine.yaml").read_text()
        inst_vals = _block_values(_block_text(engine_text, "instances") or "")
        img_tag = (
            _block_scalar(_block_text(engine_text, "image") or "", "tag")
            or "bonsai2-server:1.0"
        )
        model_file = (
            _block_scalar(_block_text(engine_text, "model") or "", "file")
            or "bonsai-2-27b-gguf-PQ2_0.gguf"
        )
        port_base = int(inst_vals.get("port_base", 30101))
        # P1 (G3c r5): if the restored blob was stored as a DICT (a
        # partial/flat mapping), _dump_yaml writes it without a top-level
        # `instances:` key and inst_vals comes back empty — N must fall
        # back to the state's own S6.5-recorded `n` (the install-time
        # count), NOT to 0 (a silent zero-instance rollback).
        n = int(inst_vals.get("count", state.get("n") or 0))
        ctx = int(inst_vals.get("ctx_size", 262144))
        par = int(inst_vals.get("max_num_seqs", 8))
        budget = float(inst_vals.get("per_instance_budget_gb", 14.78))
        # P2 (G3c r5): the top-level scalar / tools reads operate on the
        # SAME already-in-memory engine_text — keep them inside the guard
        # so the EXIT_CONFIG boundary is consistent (the r4 guard wrapped
        # read_text + the instances parse but not these lines).
        kv = _top_scalar_str(engine_text, "kv_cache") or "q4_0"
        alias = _top_scalar_str(engine_text, "alias") or "qwen38-27b-subagent"
        api_key = _top_scalar_str(engine_text, "api_key") or "bonsai2-local"
        reasoning = _top_scalar_str(engine_text, "reasoning_format") or "deepseek"
        tools_block = _block_text(engine_text, "tools") or ""
        tools = (_block_scalar(tools_block, "enabled") or "false").lower() == "true"
        tools_set = _block_list(tools_block, "set") if tools else None
    except (OSError, ValueError) as exc:
        _log(f"rollback: restored engine yaml unreadable: {exc}")
        return EXIT_CONFIG, {
            "status": "error",
            "error": f"restored engine yaml unreadable: {exc}",
        }
    unit_dir = seams.unit_dir(scope)
    unit_dir.mkdir(parents=True, exist_ok=True)
    for i in range(n):
        u = render_instance_unit(
            i,
            budget_gb=budget,
            ctx=ctx,
            parallel=par,
            kv=kv,
            port_base=port_base,
            image_tag=img_tag,
            model_file=model_file,
            api_key=api_key,
            reasoning=reasoning,
            alias=alias,
            tools=tools,
            tools_set=tools_set,
        )
        (unit_dir / f"bonsai2-server@{i}.service").write_text(u)
    # remove units beyond the restored N
    for u in sorted(unit_dir.glob("bonsai2-server@*.service")):
        m = re.match(r"bonsai2-server@(\d+)\.service$", u.name)
        if m and int(m.group(1)) >= n:
            u.unlink()
            _log(f"rollback: removed {u.name}")
    manager_src = seams.repo_root / "units" / "bonsai2-sglang.service"
    (unit_dir / "bonsai2-sglang.service").write_text(manager_src.read_text())
    warnings: list[str] = []
    rc = seams.sc(scope, "daemon-reload")
    _log(f"rollback: daemon-reload rc={rc}")
    if rc != 0:
        # P1 (G3c r3/r4): a failed daemon-reload was previously only logged,
        # so a failed reload followed by a successful restart reported
        # "rolled-back" with no warning. The S6.5 contract is "restores
        # configs + restarts the manager" — if systemd did not accept the
        # reloaded unit table, the contract is not met: exit EXIT_CONFIG
        # (2) with the failure NAMED (the configs stay restored on disk;
        # re-running the restart after `systemctl daemon-reload` is
        # idempotent — S6.3 exit table: an install step's ERR_CONFIG maps
        # to exit 2, same as install()).
        _log("rollback: daemon-reload failed - configs restored, restart not accepted")
        return EXIT_CONFIG, {
            "status": "error",
            "state": str(state_file),
            "n": n,
            "error": f"daemon-reload failed rc={rc} (configs restored, manager not restarted)",
            "warnings": warnings,
        }
    rc = seams.sc(scope, "restart", "bonsai2-sglang")
    _log(f"rollback: manager restart rc={rc}")
    if rc != 0:
        # P1 (G3c r4): a failed restart was previously exit 0 + warning
        # only, asymmetric with install() (whose failed restart rc lands in
        # the report and exits 2). The manager did not restart -> the S6.5
        # contract is not met -> EXIT_CONFIG with the failure named.
        _log(
            "rollback: manager restart failed - configs restored, manager not restarted"
        )
        return EXIT_CONFIG, {
            "status": "error",
            "state": str(state_file),
            "n": n,
            "error": f"manager restart failed rc={rc} (configs restored, manager not restarted)",
            "warnings": warnings,
        }
    # P2 (G3c r3): the state records the scope it was written under; rolling
    # back under a DIFFERENT CLI scope rewrites the units into the other
    # systemd tree. Name the mismatch so the operator sees it.
    state_scope = state.get("scope")
    if isinstance(state_scope, str) and state_scope != scope:
        warnings.append(
            f"WARN_ROLLBACK_SCOPE_MISMATCH: state was written under "
            f"scope={state_scope!r}, this rollback ran under scope={scope!r} "
            "- units land in the current scope's tree"
        )
    _log("rollback: manager restarted")
    return EXIT_OK, {
        "status": "rolled-back",
        "state": str(state_file),
        "n": n,
        "warnings": warnings,
    }


def _block_values(block: str) -> dict[str, str]:
    vals: dict[str, str] = {}
    for line in block.splitlines():
        m = re.match(r"^[ \t]*([\w.-]+):\s*(.+?)(\s+#.*)?$", line)
        if m:
            vals[m.group(1)] = m.group(2).strip().strip('"').strip("'")
    return vals


def _top_scalar_str(text: str, key: str) -> str | None:
    m = re.search(rf"(?m)^{re.escape(key)}:[ \t]*(\S+)", text)
    return m.group(1).strip('"').strip("'") if m else None


def _block_list(block: str, key: str) -> list[str] | None:
    """A YAML list value (flow or block style) under `key:` in the block."""
    m = re.search(rf"(?m)^[ \t]*{re.escape(key)}:[ \t]*(\[[^\]]*\])", block)
    if m:
        inner = m.group(1)[1:-1]
        return [x.strip().strip('"').strip("'") for x in inner.split(",") if x.strip()]
    # block style:
    m = re.search(
        rf"(?m)^[ \t]*{re.escape(key)}:[ \t]*\n((?:[ \t]+-[ \t]*.+\n?)+)", block
    )
    if m:
        return [
            ln.strip().lstrip("- ").strip().strip('"').strip("'")
            for ln in m.group(1).strip().splitlines()
        ]
    return None


# ---------------------------------------------------------------------------
# The install pipeline
# ---------------------------------------------------------------------------


@dataclass
class InstallPlan:
    """Everything the pipeline computed, before any write (atomic)."""

    opt_root: Path
    health_dir: Path
    scope: str
    dry_run: bool
    python: str
    engine_yaml_text: str  # the RENDERED engine yaml (count + image pin)
    serving_yaml_text: str  # the RENDERED serving yaml (footprint)
    harness_yaml_text: str
    bench_yaml_text: str
    n: int
    n_auto: int
    n_override: int | None
    budget_gb_remaining: float
    main_footprint_gb: float
    footprint_source: str
    main_alive: bool
    image_tag: str
    image_digest: str
    image_action: str  # "skipped (digest match)" | "built (digest re-recorded)"
    model_action: str
    unit_texts: list[str]
    warnings: list[str] = field(default_factory=list)


def _read_templates(seams: Seams) -> dict[str, str]:
    etc = seams.repo_root / "etc"
    return {
        name: (etc / name).read_text()
        for name in (
            "subagent-engine.yaml",
            "serving.yaml",
            "harness.yaml",
            "bench.yaml",
        )
    }


def _deploy_bin_tree(
    seams: Seams, opt_root: Path, python: str = DEFAULT_PYTHON
) -> None:
    """Deploy the bin/ + bench/ runtime tree to the opt root (the repo
    mirrors the host layout 1:1; S6.1: the four runtime YAMLs + the bin
    tree + the bench package are the install surface). Idempotent.

    `python` is the box-python shebang the F10 deploy-time fix rewrites
    the command wrappers to (the installer's resolved interpreter;
    DEFAULT_PYTHON = /usr/bin/python3)."""
    bin_dst = opt_root / "bin"
    bin_dst.mkdir(parents=True, exist_ok=True)
    for name in BIN_DEPLOY:
        src = seams.repo_root / "bin" / name
        if not src.is_file():
            _log(f"deploy: WARN {name} missing from the repo bin/ - skipping")
            continue
        shutil.copy2(src, bin_dst / name)
    # F10 (first-practical-run remediation, 2026-10-03 — ledger section
    # 1c): the S3.2 command wrappers land NOT +x on some clones (the
    # repo copies lost their exec bit; copy2 preserves it) and carry a
    # `#!/usr/bin/env python3` shebang that resolves to the Hermes
    # bundled python (no PyYAML) when its bin dir is ahead of /usr/bin
    # on the PATH -> raw traceback, no closed-set exit code. Both fixed
    # AT DEPLOY, so every install lands a clean command surface:
    #  * chmod 0o755 the executable set (the manager + all commands; the
    #    _harness/_tools/_vram/_fetch/_image/_render/_taskworker modules
    #    stay 0644 — they are imported, not executed),
    #  * rewrite the first line of each executable to the box python
    #    (the installer's `python` argument, /usr/bin/python3 on this
    #    box — the interpreter that HAS PyYAML). The repo source files
    #    are NOT rewritten (deploy-time only; a re-install is
    #    idempotent because the rewrite is a no-op on an already
    #    pinned shebang).
    exec_set = {
        "bonsai2-manager",
        "bonsai2-status",
        "bonsai2-list-instances",
        "bonsai2-health",
        "bonsai2-spawn",
        "bonsai2-collect",
        "bonsai2-steer",
        "bonsai2-cancel",
        "bonsai2-unload",
        "bonsai2-reload",
        "bonsai2-bench",
    }
    for name in exec_set:
        p = bin_dst / name
        if not p.is_file():
            continue
        try:
            txt = p.read_text()
            if txt.startswith("#!"):
                first_nl = txt.index("\n", 2)
                txt = f"#!{python}\n" + txt[first_nl + 1 :]
            p.write_text(txt)
            p.chmod(0o755)
        except (OSError, ValueError) as exc:
            _log(f"deploy: WARN {name} exec-bit/shebang fix failed: {exc}")
    # the bench package (run.py resolves $BONSAI2_HOME/share/bench at
    # install time; repo-relative in the tree):
    share = opt_root / "share"
    share.mkdir(parents=True, exist_ok=True)
    bench_dst = share / "bench"
    if bench_dst.exists():
        shutil.rmtree(str(bench_dst), ignore_errors=True)
    shutil.copytree(
        str(seams.repo_root / "bench"),
        str(bench_dst),
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    (share / "bench-results").mkdir(parents=True, exist_ok=True)
    _log("deploy: bin/ + bench/ tree in place")


def _invoke_uid() -> int | None:
    """F10 (first-practical-run remediation, 2026-10-03 — ledger section
    1c): the uid of the USER who invoked the installer. The installer
    normally runs as root (sudo ./install.sh) and creates the
    /run/bonsai2 subtrees root-owned; the user-scope S3.2 commands
    (spawn/collect/...) then `mkdir` task dirs under results/ as that
    user -> EACCES. SUDO_UID (set by sudo for the invoking user) is the
    source of truth; None when not running under sudo (a non-root
    install: the dirs are already owned by the caller — no chown
    needed)."""
    v = os.environ.get("SUDO_UID")
    if v is not None and v.isdigit() and int(v) != 0:
        return int(v)
    return None


def _make_dirs(health_dir: Path, invoke_uid: int | None = None) -> None:
    for sub in ("results", "dedup", "bench", "workspaces", "control"):
        (health_dir / sub).mkdir(parents=True, exist_ok=True)
    # F10: chown the results subtree to the invoking user so user-scope
    # commands can create task dirs under results/ without EACCES. Only
    # `results/` (its immediate subdirs are created at runtime, and they
    # inherit the parent's owner): dedup/bench/workspaces/control are
    # manager- (root-) scope and stay root-owned. Best-effort: a chown
    # failure (not running as root, an immutable subtree) logs, never
    # fails the install (a user-scope install already has the caller's
    # uid on the dirs).
    if invoke_uid is not None:
        results = health_dir / "results"
        try:
            for p in [results, *results.iterdir()]:
                try:
                    os.chown(p, invoke_uid, -1)
                except OSError:
                    pass  # root-only op; keep going (best-effort)
            _log(f"run: {results} chowned to uid={invoke_uid} (user-scope commands)")
        except OSError as exc:
            _log(
                f"run: WARN results chown failed ({exc}) — user-scope commands may hit EACCES"
            )
    _log(f"run: {health_dir} dirs in place (results/dedup/bench/workspaces/control)")


def install(
    seams: Seams,
    *,
    opt_root: Path = Path(DEFAULT_OPT_ROOT),
    health_dir: Path = Path(DEFAULT_HEALTH_DIR),
    scope: str | None = None,
    python: str = DEFAULT_PYTHON,
    instances: int | str = "auto",
    force_config: bool = False,
    dry_run: bool = False,
) -> tuple[int, dict]:
    """The S6.3 pipeline. Returns (exit_code, report_dict).

    The report is the S6.3 stack-ready document (JSON on stdout by
    main(); here it is the dict the caller serializes)."""
    t_start = seams.now()
    # ONE clock tick for the whole install: the state-file name and its
    # document must share a timestamp (S6.5 rotation keys off the ts
    # tag — a file written from tick N but named from tick N+1 collides
    # with the NEXT install's tick-N file and silently overwrites it).
    state_now = t_start
    if scope is None:
        scope = "system" if seams.is_root() else "user"
        _log(f"scope: {scope} (auto-detected)")

    n_override: int | None = None
    if instances != "auto":
        inst_s = str(instances)
        if inst_s.isdigit():
            n_override = int(inst_s)
            if not 0 <= n_override <= 8:
                _log(f"ERR_CONFIG: --instances {instances} out of range [0,8]")
                return EXIT_CONFIG, {
                    "status": "error",
                    "error": "--instances out of range",
                }
        else:
            _log("ERR_CONFIG: --instances expects a non-negative int or 'auto'")
            return EXIT_CONFIG, {"status": "error", "error": "--instances bad value"}

    # ---- (1) prereqs (6.1): check-all before touch-any ------------------
    failures, warnings = check_prereqs(seams)
    if failures:
        for name, err, code, detail in failures:
            _log(f"{err}: {detail}")
        worst = max(failures, key=lambda f: f[2])
        _log(f"prereq: FAILED - nothing written under {opt_root} (atomic)")
        return worst[2], {
            "status": "prereq-fail",
            "errors": [{"row": n, "err": e, "detail": d} for n, e, _c, d in failures],
            "warnings": warnings,
        }
    _log("prereq: all S6.2 rows pass (hard rows ok, warns recorded)")

    templates = _read_templates(seams)
    eng_text, srv_text = templates["subagent-engine.yaml"], templates["serving.yaml"]

    # template values the pipeline needs
    inst_vals = _block_values(_block_text(eng_text, "instances") or "")
    srv_main = _block_values(_block_text(srv_text, "main_stack") or "")
    os_reserved = float(_top_scalar_str(srv_text, "os_reserved_gb") or 8.0)
    model_file = (
        _block_scalar(_block_text(eng_text, "model") or "", "file")
        or "bonsai-2-27b-gguf-PQ2_0.gguf"
    )
    alias = _top_scalar_str(eng_text, "alias") or "qwen38-27b-subagent"
    api_key = _top_scalar_str(eng_text, "api_key") or "bonsai2-local"
    reasoning = _top_scalar_str(eng_text, "reasoning_format") or "deepseek"
    kv = _top_scalar_str(eng_text, "kv_cache") or "q4_0"
    port_base = int(inst_vals.get("port_base", 30101))
    ctx = int(inst_vals.get("ctx_size", 262144))
    par = int(inst_vals.get("max_num_seqs", 8))
    budget_gb = float(inst_vals.get("per_instance_budget_gb", 14.78))
    main_container = srv_main.get("container", "qwen38-sglang")
    # recorded_footprint: the LAST recorded value (the on-disk serving.yaml,
    # if a previous install landed it), else the template default. It is the
    # WARN_MAIN_STACK_DEAD fallback, not a drift input — the drift check
    # below compares the RENDERED yaml to the on-disk copy directly.
    _disk_srv = opt_root / "etc" / "serving.yaml"
    if _disk_srv.is_file():
        _disk_foot = _block_scalar(
            _block_text(_disk_srv.read_text(), "main_stack") or "", "footprint_gb"
        )
        recorded_footprint = (
            float(_disk_foot) if _disk_foot is not None else DEFAULT_MAIN_FOOTPRINT_GB
        )
    else:
        recorded_footprint = float(
            srv_main.get("footprint_gb", DEFAULT_MAIN_FOOTPRINT_GB)
        )
    img_tag = (
        _block_scalar(_block_text(eng_text, "image") or "", "tag")
        or "bonsai2-server:1.0"
    )
    img_sha_rec = _block_scalar(_block_text(eng_text, "image") or "", "sha256") or ""
    tools_block = _block_text(eng_text, "tools") or ""
    tools = (_block_scalar(tools_block, "enabled") or "false").lower() == "true"
    tools_set = _block_list(tools_block, "set") if tools else None

    # ---- (2) download + verify model (S1.1; skip on SHA match) ----------
    # Runs BEFORE the image build: the builder's cache pre-verification
    # (S1.1 "hash-verified before baking") passes by construction.
    model_action = "skipped (dry-run)" if dry_run else "fetched/verified (S1.1)"
    if not dry_run:
        fetch_rc = seams.run_python(
            python,
            str(Path(__file__).resolve().parent / "bonsai2_model_fetch.py"),
            ["--cache-dir", str(opt_root / "cache")],
        ).returncode
        if fetch_rc != 0:
            # the fetch tool's closed set: 2 = ERR_CONFIG, 4 =
            # ERR_HASH_MISMATCH / ERR_DOWNLOAD (S1.5)
            _log(f"model fetch exit {fetch_rc} (S1.1)")
            return fetch_rc if fetch_rc in (
                EXIT_CONFIG,
                EXIT_VRAM_OOM,
            ) else EXIT_CONFIG, {
                "status": "error",
                "error": f"model fetch exit {fetch_rc}",
                "warnings": warnings,
            }
        _log("model: cache verified (default + fallback quants)")

    # ---- (3) build-or-verify image (S6.4 step 3) -------------------------
    local_id = seams.docker_image_id(img_tag)
    pinned = (img_sha_rec or "").lower().replace("sha256:", "")
    local_norm = (local_id or "").lower().replace("sha256:", "")
    image_action = "skipped (digest match)"
    image_digest = _as_sha256(f"sha256:{local_norm}" if local_norm else img_sha_rec)
    if local_norm and pinned and local_norm == pinned:
        _log(f"image: {img_tag} present, digest match - skip (S6.4)")
    else:
        if dry_run:
            image_action = "would build-or-verify (dry-run)"
            _log(
                f"image: digest {'unknown (no local image)' if not local_norm else 'MISMATCH'} "
                "- would build-or-verify"
            )
        else:
            _log(
                f"image: building or re-verifying {img_tag} (pin {pinned[:16] or '-'}..., "
                f"local {local_norm[:16] or 'absent'}...)"
            )
            sys.path.insert(0, str(Path(__file__).resolve().parent))

            # the builder verifies the cache + writes the pin into the
            # YAML it is given: give it a scratch copy under the opt
            # root so the recorded pin lands where the manager reads it
            # (the S1.6 "recorded at install" value) and the tracked
            # template is never mutated.
            opt_etc = opt_root / "etc"
            opt_etc.mkdir(parents=True, exist_ok=True)
            cfg_path = opt_etc / "subagent-engine.yaml"
            cfg_path.write_text(eng_text)
            build_rc = seams.run_python(
                python,
                str(Path(__file__).resolve().parent / "bonsai2_build_image.py"),
                [
                    "--config",
                    str(cfg_path),
                    "--cache-dir",
                    str(opt_root / "cache"),
                    "--tag",
                    img_tag,
                ],
            ).returncode
            if build_rc != 0:
                _log(f"ERR_CONFIG: image build/verify exit {build_rc} (S6.3 step 3)")
                return EXIT_CONFIG, {
                    "status": "error",
                    "error": f"image build/verify exit {build_rc}",
                    "warnings": warnings,
                }
            eng_text = cfg_path.read_text()  # the pin write landed here
            img_sha_new = (
                _block_scalar(_block_text(eng_text, "image") or "", "sha256") or ""
            )
            image_digest = _as_sha256(img_sha_new or image_digest)
            image_action = "built (digest re-recorded)"
            _log(f"image: {image_action} at {image_digest[:24]}...")

    # ---- (4) detect the main-stack fresh-load footprint ------------------
    footprint, footprint_source, main_alive = detect_main_footprint(
        seams, main_container, recorded_footprint
    )
    if not main_alive:
        warnings.append(
            f"WARN_MAIN_STACK_DEAD: {main_container} unreadable - using the S2.6 "
            f"default {recorded_footprint} GB (S6.4: install continues)"
        )

    # ---- (5) compute N (S1.4) --------------------------------------------
    mem_bytes = seams.mem_total_bytes()
    n_auto, budget_remaining = compute_n(mem_bytes, footprint, os_reserved, budget_gb)
    n = n_override if n_override is not None else n_auto
    _log(
        f"N: S1.4 -> {n_auto} (pool {mem_bytes / 1024**3:.2f} GiB, main {footprint:.2f} GB, "
        f"reserved {os_reserved} GB -> budget {budget_remaining:.2f} GB / {budget_gb} GB "
        f"per instance)"
        + (f"; --instances override -> {n}" if n_override is not None else "")
    )
    warnings.append(
        f"main footprint {footprint:.2f} GB ({footprint_source}); N={n} "
        f"(S1.4 computed {n_auto})"
    )

    # ---- (6) render the four YAMLs (S1.6/S2.6/S3.6/S5.7) -----------------
    engine_rendered, eng_actions = render_engine_yaml(
        eng_text, count=n, image_tag=img_tag, image_sha=image_digest
    )
    serving_rendered, srv_actions = render_serving_yaml(
        srv_text, footprint_gb=footprint
    )

    unit_texts = [
        render_instance_unit(
            i,
            budget_gb=budget_gb,
            ctx=ctx,
            parallel=par,
            kv=kv,
            port_base=port_base,
            image_tag=img_tag,
            model_file=model_file,
            api_key=api_key,
            reasoning=reasoning,
            alias=alias,
            tools=tools,
            tools_set=tools_set,
        )
        for i in range(n)
    ]

    plan = InstallPlan(
        opt_root=opt_root,
        health_dir=health_dir,
        scope=scope,
        dry_run=dry_run,
        python=python,
        engine_yaml_text=engine_rendered,
        serving_yaml_text=serving_rendered,
        harness_yaml_text=templates["harness.yaml"],
        bench_yaml_text=templates["bench.yaml"],
        n=n,
        n_auto=n_auto,
        n_override=n_override,
        budget_gb_remaining=budget_remaining,
        main_footprint_gb=footprint,
        footprint_source=footprint_source,
        main_alive=main_alive,
        image_tag=img_tag,
        image_digest=image_digest,
        image_action=image_action,
        model_action=model_action,
        unit_texts=unit_texts,
        warnings=warnings,
    )

    if dry_run:
        _log("dry-run: no writes (the plan above is the report)")
        return EXIT_OK, _report(
            plan,
            status="dry-run",
            config_actions=eng_actions + srv_actions,
            unit_actions=[],
        )

    # ---- write the configs (S6.4 idempotency) -----------------------------
    etc = opt_root / "etc"
    etc.mkdir(parents=True, exist_ok=True)
    config_actions: list[str] = []
    # S6.4 drift detection: compare each RENDERED yaml to its on-disk copy.
    # install re-renders exactly two files from the tracked template —
    # subagent-engine.yaml (instances.count + image.pin) and serving.yaml
    # (main_stack.footprint_gb) — so a byte-diff in either means a value
    # moved (or the on-disk file drifted by hand). harness/bench are copied
    # verbatim from the template, so they cannot drift. The drift list names
    # the specific keys so the S6.3 report explains WHY the rewrite happened.
    eng_on_disk = etc / "subagent-engine.yaml"
    srv_on_disk = etc / "serving.yaml"
    drift: list[str] = []
    if not eng_on_disk.is_file() or not srv_on_disk.is_file():
        drift.append("first install (no on-disk config)")
    else:
        if eng_on_disk.read_text() != engine_rendered:
            cur_n = _block_scalar(
                _block_text(eng_on_disk.read_text(), "instances") or "", "count"
            )
            if cur_n is not None and int(cur_n) != n:
                drift.append(f"instances.count {cur_n} -> {n}")
            cur_sha = _block_scalar(
                _block_text(eng_on_disk.read_text(), "image") or "", "sha256"
            )
            if (cur_sha or "") != image_digest:
                drift.append(f"image.pin.sha256 -> {image_digest[:24]}...")
            if not any(d.startswith("instances.count") for d in drift):
                drift.append("subagent-engine.yaml differs from rendered")
        if srv_on_disk.read_text() != serving_rendered:
            cur_foot = _block_scalar(
                _block_text(srv_on_disk.read_text(), "main_stack") or "", "footprint_gb"
            )
            if (
                cur_foot is not None
                and abs(float(cur_foot) - footprint) > DRIFT_GB_THRESHOLD
            ):
                drift.append(
                    f"main footprint drift {float(cur_foot):.2f} -> {footprint:.2f} GB (> {DRIFT_GB_THRESHOLD} GB)"
                )
            elif cur_foot is not None:
                drift.append(f"main_stack.footprint_gb {cur_foot} -> {footprint:.2f}")
            else:
                drift.append("serving.yaml differs from rendered")

    # P2 (G3c r5): the `or not eng_on_disk.is_file()` alone was the
    # asymmetric gate the reviewer flagged — mirror the srv side too so
    # the "first install" drift message and the rewrite gate read the
    # same way (the drift list already covers both, so this is clarity,
    # not behavior).
    do_rewrite = (
        force_config
        or bool(drift)
        or not eng_on_disk.is_file()
        or not srv_on_disk.is_file()
    )
    if do_rewrite:
        for name, text in (
            ("subagent-engine.yaml", engine_rendered),
            ("serving.yaml", serving_rendered),
            ("harness.yaml", plan.harness_yaml_text),
            ("bench.yaml", plan.bench_yaml_text),
        ):
            p = etc / name
            tmp = p.with_suffix(p.suffix + ".tmp")
            tmp.write_text(text)
            os.replace(tmp, p)
            config_actions.append(f"{name}: written")
        _log(f"config: written ({'; '.join(drift) if drift else 'forced'})")
    else:
        config_actions.append("unchanged (S6.4: no rewrite)")
        _log("config: unchanged - no YAML rewrite (S6.4)")
    if drift:
        warnings.append(f"config rewrite: {'; '.join(drift)} (S6.4 drift detection)")
    # deploy the runtime tree (bin + bench) - cheap, idempotent copies
    _deploy_bin_tree(seams, opt_root, python=python)
    _make_dirs(health_dir, invoke_uid=_invoke_uid())

    # ---- (7) units + manager (S6.3 step 7) --------------------------------
    unit_dir = seams.unit_dir(plan.scope)
    unit_dir.mkdir(parents=True, exist_ok=True)
    unit_actions: list[str] = []
    unit_changed = False
    # stop instances beyond the new N (shrink)
    old_ns: set[int] = set()
    for u in sorted(unit_dir.glob("bonsai2-server@*.service")):
        m = re.match(r"bonsai2-server@(\d+)\.service$", u.name)
        if m:
            old_ns.add(int(m.group(1)))
    for i in sorted(old_ns - set(range(n))):
        seams.sc(plan.scope, "stop", f"bonsai2-server@{i}")
        (unit_dir / f"bonsai2-server@{i}.service").unlink(missing_ok=True)
        unit_actions.append(f"bonsai2-server@{i}: removed")
        unit_changed = True
    for i in range(n):
        p = unit_dir / f"bonsai2-server@{i}.service"
        new_text = unit_texts[i]
        if p.is_file() and p.read_text() == new_text:
            unit_actions.append(f"bonsai2-server@{i}: unchanged")
        else:
            p.write_text(new_text)
            unit_actions.append(f"bonsai2-server@{i}: installed")
            unit_changed = True
    manager_unit = unit_dir / "bonsai2-sglang.service"
    manager_src = (seams.repo_root / "units" / "bonsai2-sglang.service").read_text()
    if manager_unit.is_file() and manager_unit.read_text() == manager_src:
        unit_actions.append("bonsai2-sglang: unchanged")
    else:
        manager_unit.write_text(manager_src)
        unit_actions.append("bonsai2-sglang: installed")
        unit_changed = True

    # S6.4: daemon-reload ONLY on diff; the manager restarts only when
    # units or config changed (an unchanged re-run = zero restarts).
    something_changed = unit_changed or bool(drift) or force_config
    if unit_changed:
        rc = seams.sc(plan.scope, "daemon-reload")
        _log(f"units: daemon-reload rc={rc}")
    if something_changed:
        rc = seams.sc(plan.scope, "restart", "bonsai2-sglang")
        _log(f"manager: restart rc={rc}")
        if rc != 0:
            unit_actions.append(f"manager restart rc={rc}")
    else:
        _log("manager: unchanged re-run - no restart (S6.4)")

    # ---- (8) health checks (S2.7 probes, 180 s budget) ---------------------
    main_url = (
        _top_scalar_str(templates["harness.yaml"], "main_endpoint")
        or "http://localhost:30001"
    )
    main_probe_path = (
        _top_scalar_str(templates["harness.yaml"], "main_endpoint_healthy_probe")
        or "/v1/models"
    )
    if something_changed:
        # a (re)started manager is settling: run the full S2.7 wait
        outcome, doc, hwarns = check_health(
            health_dir=health_dir,
            expected_n=n,
            main_url=main_url,
            main_probe_path=main_probe_path,
            main_probe=seams.main_probe,
            sleep=seams.sleep,
            now=seams.now,
        )
        plan.warnings.extend(hwarns)
    else:
        # unchanged re-run: the pool is already running - read the
        # CURRENT document (no wait: S6.4 "zero restarts")
        doc = read_health(health_dir) or {}
        states = [e.get("state") for e in doc.get("instances", [])]
        if not states:
            outcome = "main-only" if n == 0 else "timeout"
        elif all(s == "healthy" for s in states) and len(states) == n:
            outcome = "full"
        elif any(s == "healthy" for s in states):
            outcome = "partial"
        else:
            outcome = "timeout"
        if outcome == "timeout" and n > 0:
            plan.warnings.append(
                "WARN_HEALTH_TIMEOUT: unchanged re-run found no healthy document "
                "(the manager may still be settling - re-run the health check)"
            )

    if outcome == "oom" and n >= 1:
        _log(
            "install: live-margin OOM - planning granted N >= 1, instance 0 could not boot (exit 4, S6.3)"
        )
        return EXIT_VRAM_OOM, _report(
            plan,
            status="oom",
            health=doc,
            config_actions=config_actions,
            unit_actions=unit_actions,
        )

    # ---- (9) state file + report (S6.5 / S6.3 step 9) ----------------------
    # the timestamp taken at the pipeline's start (see state_now above).
    # local time, explicit tz (DTZ006): a tz-aware label, not a naive one.
    _state_ts = (
        datetime.datetime.fromtimestamp(state_now, tz=datetime.timezone.utc)
        .astimezone()
        .isoformat(timespec="seconds")
    )
    state = {
        "timestamp": _state_ts,
        "n": n,
        "n_auto": n_auto,
        "n_override": n_override,
        "main_footprint_gb": round(footprint, 3),
        "footprint_source": footprint_source,
        "image": {"tag": img_tag, "digest": image_digest, "action": image_action},
        "model": {"action": model_action},
        "config": {
            "subagent-engine.yaml": engine_rendered,
            "serving.yaml": serving_rendered,
            "harness.yaml": plan.harness_yaml_text,
            "bench.yaml": plan.bench_yaml_text,
        },
        "units": unit_texts,
        "warnings": plan.warnings,
        "scope": plan.scope,
    }
    write_state(opt_root, state, state_now)

    status = {
        "full": "stack-ready",
        "partial": "partial",
        "main-only": "main-only",
        "timeout": "settling",
    }[outcome]
    report = _report(
        plan,
        status=status,
        health=doc,
        config_actions=config_actions,
        unit_actions=unit_actions,
    )
    _print_report_human(report)
    return EXIT_OK, report


def _report(
    plan: InstallPlan,
    *,
    status: str,
    health: dict | None = None,
    config_actions: list[str] | None = None,
    unit_actions: list[str] | None = None,
) -> dict:
    """The S6.3 stack-ready report (the one JSON object on stdout)."""
    return {
        "status": status,
        "n_instances": plan.n,
        "n_auto": plan.n_auto,
        "n_override": plan.n_override,
        "budget_gb_remaining": round(plan.budget_gb_remaining, 2),
        "instances": [
            {"i": i, "port": _port_base(plan) + i, "state": "planned"}
            for i in range(plan.n)
        ],
        "main": {
            "container": str(
                _block_scalar(
                    _block_text(plan.serving_yaml_text, "main_stack") or "", "container"
                )
                or ""
            ),
            "endpoint": str(
                _block_scalar(
                    _block_text(plan.serving_yaml_text, "main_stack") or "", "endpoint"
                )
                or ""
            ),
            "footprint_gb": round(plan.main_footprint_gb, 3),
            "detected_by": plan.footprint_source,
            "alive": plan.main_alive,
        },
        "image": {
            "tag": plan.image_tag,
            "digest": plan.image_digest,
            "action": plan.image_action,
        },
        "model": {"action": plan.model_action},
        "config": {"actions": config_actions or []},
        "units": {"actions": unit_actions or []},
        "health": health or {},
        "warnings": plan.warnings,
        "commands": COMMAND_SURFACE,
        "config_paths": [
            str(plan.opt_root / "etc" / n)
            for n in (
                "subagent-engine.yaml",
                "serving.yaml",
                "harness.yaml",
                "bench.yaml",
            )
        ],
        "scope": plan.scope,
    }


def _port_base(plan: InstallPlan) -> int:
    v = _block_scalar(
        _block_text(plan.engine_yaml_text, "instances") or "", "port_base"
    )
    return int(v) if v is not None else 30101


def _print_report_human(report: dict) -> None:
    lines = [
        "",
        "=== bonsai2 stack-ready report ===",
        f"  status        : {report['status']}",
        f"  instances     : {report['n_instances']} "
        + " ".join(
            f"i={e['i']}:{e['port']}({e['state']})" for e in report["instances"]
        ),
        f"  main          : {report['main']['endpoint']} (footprint {report['main']['footprint_gb']} GB via {report['main']['detected_by']})",
        f"  image         : {report['image']['tag']} @ "
        + (
            f"{report['image']['digest'][:24]}..."
            if report["image"]["digest"]
            else "unpinned (no digest recorded)"
        )
        + f" [{report['image']['action']}]",
        f"  config        : {'; '.join(report['config']['actions']) or '-'}",
        f"  units         : {'; '.join(report['units']['actions']) or '-'}",
        f"  scope         : {report['scope']}",
        "  commands      : "
        + " ".join(f"{v}({k})" for k, v in COMMAND_SURFACE.items()),
    ]
    for w in report["warnings"]:
        lines.append(f"  WARN          : {w}")
    lines.append("")
    for line in lines:
        _log(line)


# ---------------------------------------------------------------------------
# --refresh-main (6.6 / S6.5)
# ---------------------------------------------------------------------------


def refresh_main(
    seams: Seams,
    *,
    opt_root: Path = Path(DEFAULT_OPT_ROOT),
    python: str = DEFAULT_PYTHON,
    scope: str | None = None,
) -> tuple[int, dict]:
    """S6.5: after a main-stack upgrade - re-detect the footprint,
    recompute N, re-run the quick bench (M1/M2/M7). The quick bench is
    run via bin/bonsai2-bench --quick against the installed tree (the
    bench resolves its config from /opt/bonsai2/etc via the BONSAI2_*
    env vars).

    P2 (G3c r2, deferred): a refresh run rewrites the config but does
    NOT write a new state file (one state per install, S6.5). The
    deferral is recorded here, not just in the PR: `--rollback` always
    targets an install-time state, and a refresh-main that moves the
    footprint/N is a config-change event whose next full install will
    record the new state. (Review r5 P2 #6: the deferral note belongs
    in the code, not only in the disposition.)"""
    scope = scope or ("system" if seams.is_root() else "user")
    srv_text = (opt_root / "etc" / "serving.yaml").read_text()
    eng_text = (opt_root / "etc" / "subagent-engine.yaml").read_text()
    container = (
        _block_scalar(_block_text(srv_text, "main_stack") or "", "container")
        or "qwen38-sglang"
    )
    recorded = float(
        _block_scalar(_block_text(srv_text, "main_stack") or "", "footprint_gb")
        or DEFAULT_MAIN_FOOTPRINT_GB
    )
    footprint, source, _alive = detect_main_footprint(seams, container, recorded)
    # re-render serving.yaml with the new footprint (the S6.4 rewrite)
    new_srv, actions = render_serving_yaml(srv_text, footprint_gb=footprint)
    p = opt_root / "etc" / "serving.yaml"
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(new_srv)
    os.replace(tmp, p)
    # recompute N + rewrite the engine yaml + units + manager restart
    os_reserved = float(_top_scalar_str(srv_text, "os_reserved_gb") or 8.0)
    budget_gb = float(
        _block_values(_block_text(eng_text, "instances") or "").get(
            "per_instance_budget_gb", 14.78
        )
    )
    n_new, _budget = compute_n(
        seams.mem_total_bytes(), footprint, os_reserved, budget_gb
    )
    new_eng, eng_actions = render_engine_yaml(
        eng_text,
        count=n_new,
        image_tag=_block_scalar(_block_text(eng_text, "image") or "", "tag") or "",
        image_sha=_block_scalar(_block_text(eng_text, "image") or "", "sha256") or "",
    )
    p = opt_root / "etc" / "subagent-engine.yaml"
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(new_eng)
    os.replace(tmp, p)
    _log(
        f"refresh-main: footprint {footprint:.2f} GB ({source}) -> N={n_new}; config rewritten"
    )
    # restart the manager so it re-plans (the S2.3 boot tier re-runs)
    rc = seams.sc(scope, "restart", "bonsai2-sglang")
    _log(f"refresh-main: manager restart rc={rc} (re-plans at the new footprint)")
    # the S5.5 quick bench gate (M1/M2/M7)
    env = dict(os.environ)
    env["BONSAI2_BENCH_CONFIG"] = str(opt_root / "etc" / "bench.yaml")
    env["BONSAI2_ENGINE_YAML"] = str(opt_root / "etc" / "subagent-engine.yaml")
    rc = seams.run_python(
        python,
        str(opt_root / "bin" / "bonsai2-bench"),
        ["--quick"],
        env=env,
    ).returncode
    _log(
        f"refresh-main: quick bench exit {rc} (S5.5 gate: > 10% t/s regression blocks)"
    )
    return EXIT_OK, {
        "status": "refreshed",
        "main_footprint_gb": round(footprint, 3),
        "footprint_source": source,
        "n": n_new,
        "quick_bench_exit": rc,
        "config_actions": actions + eng_actions,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="qwen-tory38 one-command installer (MAP 6.1-6.6, SPEC S6)."
    )
    ap.add_argument(
        "--instances",
        default="auto",
        help="instance count override (0..8) or 'auto' = compute via S1.4 "
        "(the S0 phased-rollout flag: --instances 1 to build/verify, then re-run)",
    )
    ap.add_argument(
        "--force-config",
        action="store_true",
        help="rewrite the four YAMLs even when the on-disk state matches (S6.4)",
    )
    ap.add_argument(
        "--rollback",
        metavar="STATE_FILE",
        help="restore a state file (S6.5): configs + units + manager restart",
    )
    ap.add_argument(
        "--refresh-main",
        action="store_true",
        help="re-detect the main-stack footprint, recompute N, re-run the quick bench (S6.5)",
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="run the checks + plan; write nothing",
    )
    ap.add_argument(
        "--opt-root",
        default=DEFAULT_OPT_ROOT,
        help=f"the install root (default {DEFAULT_OPT_ROOT}; S6.7 scratch namespaces)",
    )
    ap.add_argument(
        "--health-dir",
        default=None,
        help="the health/results root (default /run/bonsai2; test seam)",
    )
    ap.add_argument(
        "--scope",
        choices=("system", "user"),
        default=None,
        help="systemd scope (default: auto - root=system, non-root=user)",
    )
    ap.add_argument(
        "--python",
        default=None,
        help="the interpreter for the helper tools (default: the "
        "interpreter running the installer — sys.executable)",
    )
    args = ap.parse_args(argv)

    seams = Seams()
    opt_root = Path(args.opt_root)
    health_dir = Path(args.health_dir or DEFAULT_HEALTH_DIR)
    python = args.python or sys.executable

    if args.rollback:
        try:
            code, report = rollback(
                seams,
                Path(args.rollback),
                opt_root,
                args.scope or ("system" if seams.is_root() else "user"),
            )
        except (OSError, ValueError) as exc:
            # P2 (G3c r5): an unexpected OSError/ValueError in a
            # non-guarded section must exit the S6.3 closed set {0,2,3,4},
            # never a traceback + exit 1.
            _log(f"ERR_CONFIG (unhandled {type(exc).__name__}): {exc}")
            code, report = (
                EXIT_CONFIG,
                {
                    "status": "error",
                    "error": f"unhandled {type(exc).__name__}: {exc}",
                },
            )
    elif args.refresh_main:
        try:
            code, report = refresh_main(
                seams,
                opt_root=opt_root,
                python=python,
                scope=args.scope,
            )
        except (OSError, ValueError) as exc:
            _log(f"ERR_CONFIG (unhandled {type(exc).__name__}): {exc}")
            code, report = (
                EXIT_CONFIG,
                {
                    "status": "error",
                    "error": f"unhandled {type(exc).__name__}: {exc}",
                },
            )
    else:
        try:
            code, report = install(
                seams,
                opt_root=opt_root,
                health_dir=health_dir,
                scope=args.scope,
                python=python,
                instances=args.instances,
                force_config=args.force_config,
                dry_run=args.dry_run,
            )
        except (OSError, ValueError) as exc:
            _log(f"ERR_CONFIG (unhandled {type(exc).__name__}): {exc}")
            code, report = (
                EXIT_CONFIG,
                {
                    "status": "error",
                    "error": f"unhandled {type(exc).__name__}: {exc}",
                },
            )
    # S3.1 style: the one JSON object on stdout is the data; stderr is
    # the human log (already emitted).
    json.dump(report, sys.stdout, indent=2)
    print()
    return code


if __name__ == "__main__":
    sys.exit(main())
