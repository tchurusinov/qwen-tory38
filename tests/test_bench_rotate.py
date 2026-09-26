"""tests/test_bench_rotate.py — the S5.2 rotation + S3.5 flock (MAP 4.3).

Hermetic: tmp dirs + mtime manipulation; the flock is a real fcntl flock
on a tmp file (a second BenchLock on the same path must fail to
acquire while the first holds it).

Asserts:
  * the 7-day prune removes only the stale *.json (newer ones survive).
  * the 1 GB cap deletes the OLDEST first (keep newest).
  * the combined rotate() reports both removal lists.
  * BenchLock: acquire -> held; a second lock on the same path fails;
    release lets the second one through.
"""

from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path

from bench.rotate import (
    CAP_BYTES,
    ROTATION_AGE_S,
    BenchLock,
    cap_1gb,
    rotate,
    rotate_7day,
)


class RotateTest(unittest.TestCase):
    def _mk(self, d: Path, name: str, age_s: float, size: int = 100) -> Path:
        p = d / name
        p.write_bytes(b"x" * size)
        past = time.time() - age_s
        os.utime(p, (past, past))
        return p

    def test_7day_prune_removes_only_stale(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            self._mk(d, "old.json", ROTATION_AGE_S + 10)
            new = self._mk(d, "new.json", ROTATION_AGE_S - 10)
            notjson = self._mk(d, "old.txt", ROTATION_AGE_S + 10)
            removed = rotate_7day(d)
            self.assertEqual(removed, ["old.json"])
            self.assertTrue(new.is_file())
            self.assertTrue(notjson.is_file())

    def test_missing_dir_is_noop(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(rotate_7day(Path(td) / "absent"), [])

    def test_cap_deletes_oldest_first(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            # over the cap: 3 files of 0.4 GB each (1.2 GB total). The
            # cap loop stops as soon as the total is under the cap:
            # delete oldest (a) -> 0.8 GB -> under the 1 GB cap, stop.
            self._mk(d, "a.json", 300, size=int(0.4 * CAP_BYTES))
            self._mk(d, "b.json", 200, size=int(0.4 * CAP_BYTES))
            self._mk(d, "c.json", 100, size=int(0.4 * CAP_BYTES))
            removed = cap_1gb(d)
            # the OLDEST is deleted first (keep newest); the loop stops
            # at the first under-cap state
            self.assertEqual(removed, ["a.json"])
            self.assertTrue((d / "b.json").is_file())
            self.assertTrue((d / "c.json").is_file())
            self.assertFalse((d / "a.json").exists())

    def test_cap_deletes_until_under(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            # 4 files of 0.4 GB (1.6 GB): delete a (1.2), b (0.8) -> stop
            self._mk(d, "a.json", 400, size=int(0.4 * CAP_BYTES))
            self._mk(d, "b.json", 300, size=int(0.4 * CAP_BYTES))
            self._mk(d, "c.json", 200, size=int(0.4 * CAP_BYTES))
            self._mk(d, "d.json", 100, size=int(0.4 * CAP_BYTES))
            removed = cap_1gb(d)
            self.assertEqual(removed, ["a.json", "b.json"])
            self.assertTrue((d / "c.json").is_file())
            self.assertTrue((d / "d.json").is_file())

    def test_cap_under_limit_is_noop(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            self._mk(d, "only.json", 10, size=1024)
            self.assertEqual(cap_1gb(d), [])

    def test_combined_rotate_reports_both(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            self._mk(d, "stale.json", ROTATION_AGE_S + 5)
            rep = rotate(d)
            self.assertIn("pruned_7day", rep)
            self.assertIn("capped_1gb", rep)
            self.assertEqual(rep["pruned_7day"], ["stale.json"])


class BenchLockTest(unittest.TestCase):
    def test_acquired_held_then_second_fails(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / ".bench.lock"
            l1 = BenchLock(p)
            l2 = BenchLock(p)
            self.assertTrue(l1.acquire())
            self.assertTrue(l1.held)
            self.assertFalse(l2.acquire())
            self.assertFalse(l2.held)
            l1.release()
            self.assertFalse(l1.held)
            self.assertTrue(l2.acquire())
            l2.release()

    def test_release_allows_reacquire_in_place(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / ".bench.lock"
            l1 = BenchLock(p)
            self.assertTrue(l1.acquire())
            l1.release()
            self.assertTrue(l1.acquire())  # same fd holder can retake
            l1.release()

    def test_release_without_acquire_is_noop(self):
        with tempfile.TemporaryDirectory() as td:
            l1 = BenchLock(Path(td) / "x.lock")
            l1.release()  # must not raise


if __name__ == "__main__":
    unittest.main()
