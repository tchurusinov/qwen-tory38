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
    """`download_one` is a single attempt (the retry budget lives in
    `fetch_entry`, which retries on both transport errors and a completed-but-
    mismatched download — S1.5). These cases pin that contract."""

    def test_single_attempt_success(self):
        fake, state = _ok_stream_factory(b"abc")
        orig = fetch._stream
        fetch._stream = fake
        try:
            ok, err = fetch.download_one("http://x", str(self.dl_dir / "part"), 3)
        finally:
            fetch._stream = orig
        self.assertTrue(ok, err)
        self.assertIsNone(err)
        self.assertEqual(state["calls"], 1)

    def test_single_attempt_transport_failure_no_retry(self):
        fake = _fail_stream_factory()
        orig = fetch._stream
        fetch._stream = fake
        try:
            ok, err = fetch.download_one("http://x", str(self.dl_dir / "part"), None)
        finally:
            fetch._stream = orig
        self.assertFalse(ok)
        self.assertEqual(err, fetch.ERR_DOWNLOAD)
        # A single attempt: no internal backoff/retry here (that is
        # fetch_entry's job).


class TestFetchEntryRetry(_Base):
    """The S1.5 retry budget lives in `fetch_entry`: retry x3 on BOTH a
    transport error AND a completed-but-mismatched (corrupt) download."""

    def test_transport_error_retries_x3_then_err_download(self):
        cache = self.tmp / "cache"
        calls = []

        def _fail(_url, _part, _size, **_kw):
            calls.append(1)
            raise OSError("reset")

        orig = fetch._stream
        fetch._stream = _fail
        sleeps = []
        try:
            ok, err = fetch.fetch_entry(
                url="http://x/w.gguf",
                disk_name="w.gguf",
                expected_sha=None,
                expected_size=None,
                cache_dir=str(cache),
                retries=3,
                backoff=10.0,
                sleep=lambda s: sleeps.append(s),
            )
        finally:
            fetch._stream = orig
        self.assertFalse(ok)
        self.assertEqual(err, fetch.ERR_DOWNLOAD)
        # S1.5 "retry x3": 1 initial + 3 retries = 4 attempts; backoff between
        # attempts only (3 intervals, not after the last).
        self.assertEqual(len(calls), 4)
        self.assertEqual(len(sleeps), 3)
        self.assertEqual(sleeps, [10.0, 10.0, 10.0])

    def test_mismatched_download_retries_x3_then_err_hash_mismatch(self):
        cache = self.tmp / "cache"
        wrong = b"X" * 100
        calls = []

        def _always_wrong(_url, part, _size, **_kw):
            calls.append(1)
            Path(part).write_bytes(wrong)
            return len(wrong), 0

        orig = fetch._stream
        fetch._stream = _always_wrong
        try:
            ok, err = fetch.fetch_entry(
                url="http://x/w.gguf",
                disk_name="w.gguf",
                expected_sha=_sha(b"correct-bytes"),
                expected_size=len(wrong),
                cache_dir=str(cache),
                retries=3,
                backoff=10.0,
                sleep=lambda s: None,
            )
        finally:
            fetch._stream = orig
        self.assertFalse(ok)
        self.assertEqual(err, fetch.ERR_HASH_MISMATCH)
        # The full-size corrupt .part is cleared each attempt, so every retry
        # re-downloads from scratch — assert the re-download actually happened
        # (4 attempts = initial + 3 retries, not "resumed" the corrupt file).
        self.assertEqual(len(calls), 4)
        self.assertFalse((cache / "w.gguf.part").exists())


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


class TestStreamRealPath(_Base):
    """Drive the REAL `_stream` (the only urllib-touching function) via a
    fake `urllib.request.urlopen`. Closes the P2@56 gap: the resume /
    mid-transfer-reset / truncation paths in _stream are exercised hermetically,
    not just the _stream-monkeypatched retry loop.

    NOTE: a fake response serves the FULL object every time; Range-resume
    byte-offsets are therefore approximated (the fake writes from byte 0).
    The truncation / reset / size checks — the paths under test — do not
    depend on the exact resume offset, so the approximation is safe here.
    """

    def _patch_urlopen(self, resp_factory):
        import urllib.request

        orig = urllib.request.urlopen
        urllib.request.urlopen = lambda req, timeout=None: resp_factory()
        return orig

    def _resp(self, chunks):
        class R:
            status = 200

            def __init__(self):
                self._c = iter(chunks)

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def getcode(self):
                return self.status

            def read(self, n):
                try:
                    return next(self._c)
                except StopIteration:
                    return b""

        return R

    def _reset_resp(self, prefix, total_len):
        class R:
            status = 200

            def __init__(self):
                self._served = False

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def getcode(self):
                return self.status

            def read(self, n):
                if self._served:
                    raise ConnectionResetError("mid-transfer reset")
                self._served = True
                return prefix

        return R

    def test_success_writes_full_file(self):
        import urllib.request

        data = b"z" * 50
        resp_factory = self._resp([data])
        orig = self._patch_urlopen(resp_factory)
        try:
            got, _start = fetch._stream("http://x", str(self.dl_dir / "p"), 50)
        finally:
            urllib.request.urlopen = orig
        self.assertEqual(got, 50)
        self.assertEqual((self.dl_dir / "p").read_bytes(), data)

    def test_complete_partial_short_circuits_no_fetch(self):
        # A `.part` that already holds exactly the pinned byte count (e.g. a
        # crash after download, before promotion) must NOT trigger a Range
        # request (which would 416) — it short-circuits and the caller verifies
        # the bytes it has (G3c round 4 P1@186).
        import urllib.request

        data = b"q" * 60
        (self.dl_dir / "p").write_bytes(data)
        called = {"n": 0}

        def _factory():
            called["n"] += 1

            class R:
                status = 200

                def __enter__(self):
                    return self

                def __exit__(self, *a):
                    return False

                def getcode(self):
                    return self.status

                def read(self, n):
                    return b""

            return R()

        orig = self._patch_urlopen(_factory)
        try:
            got, resumed_from = fetch._stream("http://x", str(self.dl_dir / "p"), 60)
        finally:
            urllib.request.urlopen = orig
        # Short-circuit: the fake response was never read (no fetch happened).
        self.assertEqual(got, 60)
        self.assertEqual(resumed_from, 60)
        # The bytes on disk are untouched.
        self.assertEqual((self.dl_dir / "p").read_bytes(), data)

    def test_mid_transfer_reset_leaves_confirmed_prefix(self):
        import urllib.error
        import urllib.request

        prefix = b"A" * 40
        total = 100
        resp_factory = self._reset_resp(prefix, total)
        orig = self._patch_urlopen(resp_factory)
        try:
            with self.assertRaises(urllib.error.URLError) as ctx:
                fetch._stream("http://x", str(self.dl_dir / "p"), total)
        finally:
            urllib.request.urlopen = orig
        # The confirmed on-disk prefix (40 B of 100) is in the raised error so
        # the retry can audit and resume from it — not a silent blind reopen.
        self.assertIn("40B", str(ctx.exception))
        self.assertIn("100B", str(ctx.exception))
        self.assertEqual((self.dl_dir / "p").read_bytes(), prefix)

    def test_truncated_body_rejects_short_completion(self):
        import urllib.error
        import urllib.request

        data = b"B" * 30
        total = 100
        resp_factory = self._resp([data])
        orig = self._patch_urlopen(resp_factory)
        try:
            with self.assertRaises(urllib.error.URLError) as ctx:
                fetch._stream("http://x", str(self.dl_dir / "p"), total)
        finally:
            urllib.request.urlopen = orig
        self.assertIn("truncated", str(ctx.exception).lower())


if __name__ == "__main__":
    unittest.main()
