"""bench.rotate — the S5.2 run-JSON rotation + 1 GB cap, and the S3.5
flock.

The S5.2 rotation rule (bench run JSONs): "the runner prunes
``/run/bonsai2/bench/*.json`` older than 7 days on each start, and
additionally caps the bench dir at 1 GB (keep newest, delete oldest), so
the global #5 disk budget (2 GB) covers them."

Two operations, both run at the start of a bench run (``bench.run``):

  * ``rotate_7day(dir)`` — delete ``*.json`` whose mtime is older than the
    7-day window (the S3.1 results window, the same convention the
    workspace rotation uses, MAP 3.15 / S3.2).
  * ``cap_1gb(dir)`` — while the dir's total size exceeds the 1 GB cap,
    delete the OLDEST ``*.json`` (keep newest, delete oldest).

``acquire_lock(path)`` is the S3.5 flock (``fcntl.flock`` LOCK_EX |
LOCK_NB): the bench run is exclusive on the box (one heavy thing at a
time — the machine rule). It is held for the whole run; a second bench
invocation while the lock is held exits 2 with a named "bench already
running" detail (S3.2 code 2, bad-config/args family — a concurrent bench
is a usage error, the S5.4 runner surfaces it).

Stdlib-only: ``pathlib`` + ``os`` + ``fcntl`` + ``time``.
"""

from __future__ import annotations

import fcntl
import os
import time
from pathlib import Path

# The S5.2 7-day window (the S3.1 results window, same constant as the
# MAP 3.15 workspace rotation).
ROTATION_AGE_S = 7 * 24 * 3600
# The S5.2 1 GB cap on the bench dir.
CAP_BYTES = 1 * 1024 * 1024 * 1024


def _json_files(dir: Path) -> list[Path]:
    if not dir.is_dir():
        return []
    return [p for p in dir.glob("*.json") if p.is_file()]


def rotate_7day(dir: Path, now: float | None = None) -> list[str]:
    """Prune ``dir/*.json`` older than the 7-day window. Returns the list
    of removed filenames (best-effort: an OSError on one file is skipped).
    A missing/empty dir is a no-op."""
    now = time.time() if now is None else now
    removed: list[str] = []
    for p in _json_files(dir):
        try:
            mtime = p.stat().st_mtime
        except OSError:
            continue
        if now - mtime > ROTATION_AGE_S:
            try:
                p.unlink()
                removed.append(p.name)
            except OSError:
                continue
    return removed


def _dir_bytes(dir: Path) -> int:
    total = 0
    for p in _json_files(dir):
        try:
            total += p.stat().st_size
        except OSError:
            continue
    return total


def cap_1gb(dir: Path, cap_bytes: int = CAP_BYTES) -> list[str]:
    """While ``dir`` exceeds ``cap_bytes`` total, delete the OLDEST ``*.json``
    (keep newest, delete oldest — the S5.2 "keep newest" rule). Returns the
    list of removed filenames. Stops as soon as the total is under the cap
    (or there are no more files to delete)."""
    removed: list[str] = []
    while True:
        total = _dir_bytes(dir)
        if total <= cap_bytes:
            break
        files = _json_files(dir)
        if not files:
            break
        # oldest first (by mtime; a stat failure sorts oldest so it is
        # tried first — deleting a stat-unknown file is the safe direction
        # when we are over the cap).
        files.sort(key=lambda p: _safe_mtime(p))
        oldest = files[0]
        try:
            oldest.unlink()
            removed.append(oldest.name)
        except OSError:
            # cannot delete the oldest; stop (do not loop forever on a
            # wedged file)
            break
    return removed


def _safe_mtime(p: Path) -> float:
    try:
        return p.stat().st_mtime
    except OSError:
        return 0.0


def rotate(dir: Path, now: float | None = None) -> dict:
    """The full S5.2 rotation: 7-day prune + 1 GB cap. Returns a small
    report (the runner logs it)."""
    now = time.time() if now is None else now
    pruned = rotate_7day(dir, now=now)
    capped = cap_1gb(dir)
    return {"pruned_7day": pruned, "capped_1gb": capped}


class BenchLock:
    """The S3.5 flock on ``path`` (a file the caller creates in the bench
    dir or a ``/run/bonsai2`` scratch). Exclusive, non-blocking on
    acquire: a second bench while the lock is held is a usage error (the
    S5.4 runner's exit-2 "bench already running" case).

    Usage::

        lock = BenchLock("/run/bonsai2/.bench.lock")
        if not lock.acquire():
            ... # exit 2, bench already running
        try:
            ...  # the whole bench run
        finally:
            lock.release()
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._fd: int | None = None

    def acquire(self) -> bool:
        """Try to take the lock (LOCK_EX | LOCK_NB). True when acquired.
        Creates the parent dir on demand; the lock file itself is left in
        place on release (a lock file's absence is not a signal — the
        flock is the signal)."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fd = os.open(str(self.path), os.O_RDWR | os.O_CREAT, 0o644)
        try:
            fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (BlockingIOError, OSError):
            os.close(self._fd)
            self._fd = None
            return False
        return True

    def release(self) -> None:
        if self._fd is not None:
            try:
                fcntl.flock(self._fd, fcntl.LOCK_UN)
            except OSError:
                pass
            os.close(self._fd)
            self._fd = None

    @property
    def held(self) -> bool:
        return self._fd is not None
