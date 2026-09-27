"""tests/test_bench_boxstate.py — the S5.2 box-state snapshot (MAP 4.2).

Hermetic: the snapshot is stdlib subprocess + /proc reads; this asserts
the SHAPE (every S5.2 field present, all string values except the int
epoch) and the driver-extraction helper's named-string behavior (no
crash on an unavailable nvidia-smi). No assertion on the live driver
version (box state, not a verdict).
"""

from __future__ import annotations

import unittest

from bench import boxstate


class SnapshotShapeTest(unittest.TestCase):
    def test_snapshot_has_all_s52_fields(self):
        snap = boxstate.snapshot()
        for key in ("nvidia_smi", "docker_stats", "meminfo", "driver", "date", "epoch"):
            self.assertIn(key, snap, f"missing S5.2 snapshot field: {key}")
        # the S5.2 "stored as strings" rule (epoch is the one int)
        for key in ("nvidia_smi", "docker_stats", "meminfo", "driver", "date"):
            self.assertIsInstance(snap[key], str, key)
        self.assertIsInstance(snap["epoch"], int)

    def test_driver_is_a_string_even_when_unavailable(self):
        snap = boxstate.snapshot()
        self.assertIsInstance(snap["driver"], str)
        self.assertTrue(len(snap["driver"]) > 0)


class DriverFromLineTest(unittest.TestCase):
    def test_parses_driver_from_a_wellformed_line(self):
        # the nvidia-smi csv,noheader shape: "<driver_version>, <mem-used>"
        self.assertEqual(boxstate._driver_from("580.126.09, 1024 MiB"), "580.126.09")

    def test_unavailable_marker_is_unknown(self):
        self.assertEqual(
            boxstate._driver_from("nvidia-smi-unavailable: no device"), "unknown"
        )

    def test_empty_line_is_unknown(self):
        self.assertEqual(boxstate._driver_from(""), "unknown")

    def test_whitespace_only_first_field_is_unknown(self):
        self.assertEqual(boxstate._driver_from(" , 1024 MiB"), "unknown")


class MeminfoTest(unittest.TestCase):
    def test_returns_a_string_with_the_fields(self):
        s = boxstate.meminfo()
        self.assertIsInstance(s, str)
        # on this box /proc/meminfo is present: the two fields are named
        self.assertIn("MemTotal=", s)
        self.assertIn("MemAvailable=", s)
        # H12 (post-live-run): the reclaimable-pool fields are present too
        # (the live run's VRAM climb 100->116 GiB with cache ~99% could not
        # be distinguished as resident vs reclaimable from a 2-field snapshot).
        self.assertIn("MemFree=", s)
        self.assertIn("Cached=", s)
        self.assertIn("Shmem=", s)
        self.assertIn("SReclaimable=", s)


if __name__ == "__main__":
    unittest.main()
