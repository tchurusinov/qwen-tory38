"""MAP 1.3 / S1.1 / S1.5: hermetic tests for bin/bonsai2_model_fetch.py.

No GPU, no network, stdlib-only (the hermetic-suite convention in
tests/test_smoke.py). The fetch/verify core is exercised by monkeypatching
`_stream` — the only function that touches urllib — so every retry,
hash-mismatch, and idempotent path is driven with in-memory fake bytes.

The tool itself is loaded from bin/ via stdlib `importlib.util` (NOT a plain
top-level import): the hermetic convention in tests/test_smoke.py forbids
absolute imports of non-stdlib modules, and `bin/` is the deploy target, not
a test package. Loading a named .py by path uses only stdlib, so the suite
stays stdlib-only. All scratch files go into a per-test tempdir (cleaned in
tearDown), so the hermetic run never writes into the repo tree.

Run: `python3 -m unittest discover -s tests` from the repo root.
"""

import hashlib
import importlib.util
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
_TOOL = REPO_ROOT / "bin" / "bonsai2_model_fetch.py"


def _load_tool():
    spec = importlib.util.spec_from_file_location("bonsai2_model_fetch", str(_TOOL))
    assert spec and spec.loader, f"could not load {_TOOL}"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


fetch = _load_tool()


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _ok_stream_factory(data: bytes):
    """A `_stream` fake that writes `data` to dest_part; records call count."""
    state = {"calls": 0}

    def _fake(url, dest_part, expected_size, **_kw):
        state["calls"] += 1
        Path(dest_part).write_bytes(data)
        return len(data), 0

    return _fake, state


def _fail_stream_factory():
    def _fake(url, dest_part, expected_size, **_kw):
        raise OSError("connection reset (fake)")

    return _fake


class _Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="bonsai2-test-")
        self.tmp = Path(self._tmp.name)
        # A scratch dir for the fake download target (dest_part) too, so the
        # download_one tests write there instead of the CWD.
        self.dl_dir = self.tmp / "dl"
        self.dl_dir.mkdir()

    def tearDown(self):
        self._tmp.cleanup()


class TestVerify(_Base):
    def test_missing_file_is_err_download(self):
        ok, err = fetch.verify(str(self.tmp / "nope.gguf"), "abc", None)
        self.assertFalse(ok)
        self.assertEqual(err, fetch.ERR_DOWNLOAD)

    def test_correct_file_passes(self):
        data = b"weights"
        p = self.tmp / "w.bin"
        p.write_bytes(data)
        ok, err = fetch.verify(str(p), _sha(data), len(data))
        self.assertTrue(ok, err)
        self.assertIsNone(err)

    def test_size_mismatch_is_hash_mismatch(self):
        p = self.tmp / "w.bin"
        p.write_bytes(b"short")
        ok, err = fetch.verify(str(p), _sha(b"short"), 99)
        self.assertFalse(ok)
        self.assertEqual(err, fetch.ERR_HASH_MISMATCH)

    def test_sha_mismatch_is_hash_mismatch(self):
        p = self.tmp / "w.bin"
        p.write_bytes(b"contents")
        ok, err = fetch.verify(str(p), "0" * 64, len(b"contents"))
        self.assertFalse(ok)
        self.assertEqual(err, fetch.ERR_HASH_MISMATCH)

    def test_sha_compare_is_case_insensitive(self):
        p = self.tmp / "w.bin"
        p.write_bytes(b"contents")
        ok, _ = fetch.verify(str(p), _sha(b"contents").upper(), None)
        self.assertTrue(ok)


class TestDownloadOne(_Base):
    def test_success_first_attempt(self):
        fake, state = _ok_stream_factory(b"abc")
        orig = fetch._stream
        fetch._stream = fake
        try:
            ok, err = fetch.download_one(
                "http://x",
                str(self.dl_dir / "part"),
                3,
                retries=3,
                backoff=0.0,
                sleep=lambda s: None,
            )
        finally:
            fetch._stream = orig
        self.assertTrue(ok, err)
        self.assertIsNone(err)
        self.assertEqual(state["calls"], 1)

    def test_retries_then_fails(self):
        fake = _fail_stream_factory()
        sleeps = []
        orig = fetch._stream
        fetch._stream = fake
        try:
            ok, err = fetch.download_one(
                "http://x",
                str(self.dl_dir / "part"),
                None,
                retries=3,
                backoff=10.0,
                sleep=lambda s: sleeps.append(s),
            )
        finally:
            fetch._stream = orig
        self.assertFalse(ok)
        self.assertEqual(err, fetch.ERR_DOWNLOAD)
        # 3 attempts, backoff logged between attempts (not after the last),
        # at the S1.5 10 s interval.
        self.assertEqual(len(sleeps), 2)
        self.assertEqual(sleeps, [10.0, 10.0])


class TestFetchEntry(_Base):
    def test_downloads_verifies_and_lands(self):
        data = b"gguf-bytes"
        fake, _state = _ok_stream_factory(data)
        cache = self.tmp / "cache"
        target = cache / "w.gguf"
        orig = fetch._stream
        fetch._stream = fake
        try:
            ok, err = fetch.fetch_entry(
                url="http://x/w.gguf",
                disk_name="w.gguf",
                expected_sha=_sha(data),
                expected_size=len(data),
                cache_dir=str(cache),
                backoff=0.0,
            )
        finally:
            fetch._stream = orig
        self.assertTrue(ok, err)
        self.assertTrue(target.exists())
        self.assertEqual(target.read_bytes(), data)
        self.assertFalse((cache / "w.gguf.part").exists())

    def test_idempotent_skip_when_verified(self):
        data = b"already-here"
        cache = self.tmp / "cache"
        cache.mkdir()
        target = cache / "w.gguf"
        target.write_bytes(data)
        calls = []

        def _counting(_url, _part, _size, **_kw):
            calls.append(1)
            raise AssertionError("should not download when cached + verified")

        orig = fetch._stream
        fetch._stream = _counting
        try:
            ok, err = fetch.fetch_entry(
                url="http://x/w.gguf",
                disk_name="w.gguf",
                expected_sha=_sha(data),
                expected_size=len(data),
                cache_dir=str(cache),
            )
        finally:
            fetch._stream = orig
        self.assertTrue(ok, err)
        self.assertEqual(calls, [])  # zero downloads (S6.4 idempotent path)

    def test_force_redownloads_even_when_verified(self):
        data = b"fresh-bytes"
        cache = self.tmp / "cache"
        cache.mkdir()
        target = cache / "w.gguf"
        target.write_bytes(b"stale")  # wrong content on disk
        fake, state = _ok_stream_factory(data)
        orig = fetch._stream
        fetch._stream = fake
        try:
            ok, err = fetch.fetch_entry(
                url="http://x/w.gguf",
                disk_name="w.gguf",
                expected_sha=_sha(data),
                expected_size=len(data),
                cache_dir=str(cache),
                force=True,
                backoff=0.0,
            )
        finally:
            fetch._stream = orig
        self.assertTrue(ok, err)
        self.assertEqual(target.read_bytes(), data)
        self.assertGreaterEqual(state["calls"], 1)

    def test_corrupt_download_clears_and_reports_mismatch(self):
        # The fake writes the wrong content at the pinned size: the fetch
        # completes, verification fails, and the mismatch is surfaced. The
        # .part is cleared because it is not a plausible partial.
        data = b"wrong-content"
        cache = self.tmp / "cache"
        fake, _state = _ok_stream_factory(data)
        orig = fetch._stream
        fetch._stream = fake
        try:
            ok, err = fetch.fetch_entry(
                url="http://x/w.gguf",
                disk_name="w.gguf",
                expected_sha=_sha(b"expected-content"),
                expected_size=len(data),
                cache_dir=str(cache),
                backoff=0.0,
            )
        finally:
            fetch._stream = orig
        self.assertFalse(ok)
        self.assertEqual(err, fetch.ERR_HASH_MISMATCH)
        self.assertFalse((cache / "w.gguf").exists())
        self.assertFalse((cache / "w.gguf.part").exists())


if __name__ == "__main__":
    unittest.main()
