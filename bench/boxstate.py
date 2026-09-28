"""bench.boxstate — the S5.2 box-state snapshot.

The S5.2 "Reproducible" paragraph: a run row carries a box-state snapshot
(driver, nvidia-smi, docker stats, /proc/meminfo, date) recorded per run.
The runner captures it via stdlib ``subprocess`` (no extra dep) plus
``/proc/meminfo`` reads:

  * ``nvidia-smi --query-gpu=driver_version,memory.used --format=csv,noheader``
  * ``docker stats --no-stream --format '{{.Name}} {{.MemUsage}}'``
    (the main stack container, read-only, per S6.4)
  * a ``MemTotal``/``MemAvailable`` read of ``/proc/meminfo``
  * the date (``date -u`` + a machine-readable epoch)

The results are stored as STRINGS in the run row (the S5.2 "stored as
strings" rule). Every source is read-only (``nvidia-smi`` / ``docker
stats`` / a ``/proc`` read / ``date`` — no state change), and a missing
tool (no ``nvidia-smi``, no ``docker`` on the box) degrades to a named
string rather than failing the run (the snapshot is data, not a verdict).

Stdlib-only: ``subprocess`` + ``pathlib`` + ``time`` — the S5.4 runner's
snapshot half (S6.6-safe: no aarch64-incompatible dep).
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

_NVIDIA_SMI_QUERY = "driver_version,memory.used"


def _run(cmd: list[str], timeout_s: float = 10.0) -> tuple[int, str]:
    """Run `cmd`, return (returncode, combined stdout+stderr as a string).
    A missing binary (FileNotFoundError) or a timeout returns a non-zero
    code + a named detail — the caller stores it as a string."""
    try:
        p = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
    except FileNotFoundError:
        return 127, f"command not found: {cmd[0]}"
    except subprocess.TimeoutExpired:
        return 124, f"timeout after {timeout_s}s: {' '.join(cmd)}"
    except OSError as exc:
        return 1, f"OSError running {' '.join(cmd)}: {exc!r}"
    out = (p.stdout or "") + (p.stderr or "")
    return p.returncode, out.strip()


def _meminfo_field(name: str) -> str | None:
    """One field of /proc/meminfo (e.g. 'MemTotal', 'MemAvailable').
    Returns the raw value string (with units) or None when absent."""
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith(name + ":"):
                return " ".join(line.split()[1:])
    except OSError:
        return None
    return None


def nvidia_smi() -> str:
    """The S5.2 nvidia-smi query (driver + memory used). A named string on
    any failure (no GPU / no nvidia-smi / timeout)."""
    rc, out = _run(
        ["nvidia-smi", "--query-gpu=" + _NVIDIA_SMI_QUERY, "--format=csv,noheader"]
    )
    if rc != 0:
        return f"nvidia-smi-unavailable: {out}"
    return out


def _driver_from(gpu_line: str) -> str:
    """Extract the driver_version from a ``nvidia-smi`` csv,noheader line
    (``<driver>, <mem-used>``). 'unknown' for anything that is not a
    well-formed first field (the unavailable-marker or an empty line)."""
    if not gpu_line or gpu_line.startswith("nvidia-smi-unavailable"):
        return "unknown"
    first = gpu_line.split(",")[0].strip()
    return first if first else "unknown"


def docker_stats() -> str:
    """The S5.2 docker stats read (read-only, per S6.4). One line per
    container, name + MemUsage. A named string when docker is absent."""
    rc, out = _run(
        [
            "docker",
            "stats",
            "--no-stream",
            "--format",
            "{{.Name}} {{.MemUsage}}",
        ]
    )
    if rc != 0:
        return f"docker-stats-unavailable: {out}"
    return out


def meminfo() -> str:
    """The /proc/meminfo memory fields, one string. H12 (post-live-run):
    beyond ``MemTotal``/``MemAvailable`` the snapshot carries the
    RECLAIMABLE pool (``MemFree``, ``Cached``, ``Shmem``,
    ``SReclaimable``) — the live run's VRAM climb (100 -> 116 GiB) with
    cache at ~99% could not be distinguished as resident vs reclaimable
    from a 2-field snapshot; the committed per-row snapshots form the
    run's VRAM time series, so a reader can tell page-cache /
    shared-memory (reclaimable) from resident growth."""
    fields = (
        "MemTotal",
        "MemAvailable",
        "MemFree",
        "Cached",
        "Shmem",
        "SReclaimable",
    )
    parts = []
    for name in fields:
        val = _meminfo_field(name)
        # G3c round-4 P2: 'n/a' is a deliberate STRING sentinel (the S5.2
        # "stored as strings" rule). A missing /proc/meminfo field (e.g. a
        # minimal container) records n/a, not a numeric 0 — the snapshot is
        # diagnostic data, not a machine-parsed metric; a reader parsing
        # the value should handle the n/a string (it's a string by contract).
        parts.append(f"{name}={val if val is not None else 'n/a'}")
    return " ".join(parts)


def snapshot() -> dict:
    """The full S5.2 box-state snapshot for a run row. All values are
    STRINGS (the S5.2 'stored as strings' rule) except ``epoch`` (the
    capture time, unix seconds):

      {
        "nvidia_smi": str,     # driver_version + memory.used
        "docker_stats": str,   # name + MemUsage per container
        "meminfo": str,        # MemTotal + MemAvailable + the reclaimable
                               # pool (MemFree/Cached/Shmem/SReclaimable,
                               # H12 post-live-run)
        "driver": str,        # extracted from the nvidia-smi line
        "date": str,          # UTC date string
        "epoch": int,         # the capture time (unix seconds)
      }
    The runner already snapshots PER ROW (one per M row) — the committed
    rows form the run's VRAM time series; the runner adds ONE extra
    post-scenarios snapshot (the run JSON's ``box_state_post_scenarios``)
    so the end-of-scenarios state is explicit (H12).

    G3c round-12 P2 (the failure contract, named): ``snapshot`` never
    RAISES on a box-state failure — a hung/missing nvidia-smi or docker
    is a NAMED STRING in the corresponding field (the
    "nvidia-smi-unavailable: ..." / "docker-stats-unavailable: ..." /
    "timeout after N s" markers from _run), not an exception, so a
    degraded box (no GPU, no docker daemon) cannot crash the bench's
    per-row snapshot path. The regression test
    test_snapshot_never_raises_on_subprocess_timeout pins this by
    forcing a subprocess.TimeoutExpired through the real code paths."""
    gpu = nvidia_smi()
    return {
        "nvidia_smi": gpu,
        "docker_stats": docker_stats(),
        "meminfo": meminfo(),
        "driver": _driver_from(gpu),
        "date": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "epoch": int(time.time()),
    }
