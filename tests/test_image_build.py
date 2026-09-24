"""MAP 1.4 / S1.6: hermetic tests for bin/bonsai2_build_image.py.

Covers the PURE part of the runtime-image pipeline (no docker, no
network, stdlib-only — the hermetic-suite convention in
tests/test_smoke.py):

  * pin8_of_commit: the 40-hex -> 8-hex extraction (the banner's
    `commit <8hex>` field)
  * normalize_digest: the docker content-digest normalization that
    lands in image.pin (S1.6 image_pin: {tag, sha256})
  * write_image_pin: the surgical YAML pin writer (comment-preserving,
    round-trip asserts)
  * check_cache_file: the S1.1 "hash-verified before baking" pre-check
  * the build() timeout path: a hung `docker buildx build` raises
    subprocess.TimeoutExpired inside build() -> main() exits 5 with
    ERR_TIMEOUT (subprocess.run is monkeypatched; no docker, no GPU)

The docker-side of build() (staging, buildx, digest read-back) is
exercised live in this session and by tests/gpu/test_runtime_image.py
(@gpu, against the built image).

The tool is loaded from bin/ via importlib.util (same convention as
tests/test_model_fetch.py: bin/ is the deploy target, not a test
package).

Run: `python3 -m unittest discover -s tests -t .` from the repo root.
"""

import hashlib
import importlib.util
import textwrap
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
_TOOL = REPO_ROOT / "bin" / "bonsai2_build_image.py"
REPO_TEMPLATE = REPO_ROOT / "etc" / "subagent-engine.yaml"


def _load_tool():
    spec = importlib.util.spec_from_file_location("bonsai2_build_image", str(_TOOL))
    assert spec and spec.loader, f"could not load {_TOOL}"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


build = _load_tool()

FAKE_DIGEST = "sha256:" + "ab" * 32
FAKE_TAG = "bonsai2-server:1.0"


def _minimal_yaml() -> str:
    """The template shape, reduced to the blocks write_image_pin needs
    (plus the model block, to prove it is untouched by the pin write)."""
    return textwrap.dedent(
        """\
        # top comment that must survive
        engine:
          binary: /opt/bonsai2/bin/llama-server
          pin:
            repo: https://github.com/PrismML-Eng/llama.cpp
            commit: "9a9394a895b96003ca842a6041cb28ac49a108f7"
        image:
          pin:
            tag: "bonsai2-server:<tag-at-install>"
            sha256: "<docker-content-digest-at-install>"
        model:
          repo: prism-ml/Ternary-Bonsai-2-27B-gguf
          revision: main
          file: bonsai-2-27b-gguf-PQ2_0.gguf
          sha256: "3907dc1658db1f78a9826bf8d5bcb8dc65db0d466388937af57f2294fae62ec1"
        """
    )


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class TestPin8(unittest.TestCase):
    def test_extracts_first_eight_hex(self):
        commit = "9a9394a895b96003ca842a6041cb28ac49a108f7"
        self.assertEqual(build.pin8_of_commit(commit), "9a9394a8")

    def test_rejects_short(self):
        with self.assertRaises(ValueError):
            build.pin8_of_commit("9a9394a8")

    def test_rejects_non_hex(self):
        with self.assertRaises(ValueError):
            build.pin8_of_commit("z" * 40)

    def test_case_insensitive_input(self):
        commit = "9A9394A8" + "95b96003ca842a6041cb28ac49a108f7"
        self.assertEqual(build.pin8_of_commit(commit), "9a9394a8")


class TestNormalizeDigest(unittest.TestCase):
    def test_accepts_prefixed(self):
        self.assertEqual(build.normalize_digest(FAKE_DIGEST), FAKE_DIGEST)

    def test_accepts_bare_hex(self):
        self.assertEqual(build.normalize_digest("ab" * 32), FAKE_DIGEST)

    def test_uppercase_normalized(self):
        self.assertEqual(
            build.normalize_digest(("AB" * 32).lower().upper()), FAKE_DIGEST
        )

    def test_rejects_wrong_length(self):
        with self.assertRaises(ValueError):
            build.normalize_digest("sha256:abc")

    def test_rejects_image_id_shape_with_bad_hex(self):
        with self.assertRaises(ValueError):
            build.normalize_digest("sha256:zz" * 32)


class TestWriteImagePin(unittest.TestCase):
    def setUp(self):
        self._tmp = _mkdtemp()
        self.tmp = Path(self._tmp.name)
        self.cfg = self.tmp / "subagent-engine.yaml"
        self.cfg.write_text(_minimal_yaml())

    def tearDown(self):
        _rmtree(self.tmp)

    def test_writes_tag_and_digest(self):
        out = build.write_image_pin(str(self.cfg), FAKE_TAG, FAKE_DIGEST)
        text = Path(out).read_text()
        self.assertIn(f'tag: "{FAKE_TAG}"', text)
        self.assertIn(f'sha256: "{FAKE_DIGEST}"', text)

    def test_preserves_comments_and_other_blocks(self):
        out = build.write_image_pin(str(self.cfg), FAKE_TAG, FAKE_DIGEST)
        text = Path(out).read_text()
        self.assertIn("# top comment that must survive", text)
        self.assertIn('commit: "9a9394a895b96003ca842a6041cb28ac49a108f7"', text)
        self.assertIn(
            'sha256: "3907dc1658db1f78a9826bf8d5bcb8dc65db0d466388937af57f2294fae62ec1"',
            text,
        )

    def test_round_trips_through_yaml(self):
        import yaml  # box dep (the tool itself uses it for the same assert)

        out = build.write_image_pin(str(self.cfg), FAKE_TAG, FAKE_DIGEST)
        data = yaml.safe_load(Path(out).read_text())
        self.assertEqual(data["image"]["pin"]["tag"], FAKE_TAG)
        self.assertEqual(data["image"]["pin"]["sha256"], FAKE_DIGEST)
        # the model block is intact (the writer touched only image.pin)
        self.assertEqual(data["model"]["file"], "bonsai-2-27b-gguf-PQ2_0.gguf")

    def test_normalizes_a_bare_hex_digest(self):
        out = build.write_image_pin(str(self.cfg), FAKE_TAG, "ab" * 32)
        self.assertIn(f'sha256: "{FAKE_DIGEST}"', Path(out).read_text())

    def test_out_path_writes_elsewhere(self):
        dest = self.tmp / "rendered.yaml"
        out = build.write_image_pin(
            str(self.cfg), FAKE_TAG, FAKE_DIGEST, out_path=str(dest)
        )
        self.assertEqual(out, str(dest))
        self.assertIn(f'sha256: "{FAKE_DIGEST}"', dest.read_text())
        # the source is untouched
        self.assertIn("<docker-content-digest-at-install>", self.cfg.read_text())

    def test_repo_template_round_trips(self):
        # The tracked template (the real pin target) must round-trip
        # through the writer on this box.
        import yaml  # box dep

        scratch = self.tmp / "template-copy.yaml"
        scratch.write_text(REPO_TEMPLATE.read_text())
        out = build.write_image_pin(str(scratch), FAKE_TAG, FAKE_DIGEST)
        data = yaml.safe_load(Path(out).read_text())
        self.assertEqual(data["image"]["pin"]["sha256"], FAKE_DIGEST)
        # every top-level block survived
        for block in ("engine", "image", "model", "instances", "sampling"):
            self.assertIn(block, data)

    def test_missing_image_block_is_err_config(self):
        self.cfg.write_text("engine:\n  binary: /x\n")
        with self.assertRaises(ValueError) as cm:
            build.write_image_pin(str(self.cfg), FAKE_TAG, FAKE_DIGEST)
        self.assertIn("ERR_CONFIG", str(cm.exception))

    def test_image_block_without_pin_lines_is_err_config(self):
        self.cfg.write_text(
            "engine:\n  binary: /x\nimage:\n  other: true\nmodel:\n  file: y\n"
        )
        with self.assertRaises(ValueError) as cm:
            build.write_image_pin(str(self.cfg), FAKE_TAG, FAKE_DIGEST)
        self.assertIn("ERR_CONFIG", str(cm.exception))


class TestCheckCacheFile(unittest.TestCase):
    def setUp(self):
        self._tmp = _mkdtemp()
        self.tmp = Path(self._tmp.name)
        self.w = self.tmp / "model.gguf"
        self.data = b"weights" * 1000
        self.w.write_bytes(self.data)

    def tearDown(self):
        _rmtree(self.tmp)

    def test_matching_file_passes(self):
        ok, err = build.check_cache_file(str(self.w), _sha(self.data), len(self.data))
        self.assertTrue(ok, err)
        self.assertEqual(err, "")

    def test_missing_file_is_err_config(self):
        ok, err = build.check_cache_file(
            str(self.tmp / "nope.gguf"), _sha(self.data), 5
        )
        self.assertFalse(ok)
        self.assertIn("ERR_CONFIG", err)

    def test_size_mismatch_is_err_hash_mismatch(self):
        ok, err = build.check_cache_file(
            str(self.w), _sha(self.data), len(self.data) + 1
        )
        self.assertFalse(ok)
        self.assertIn("ERR_HASH_MISMATCH", err)

    def test_sha_mismatch_is_err_hash_mismatch(self):
        ok, err = build.check_cache_file(str(self.w), "0" * 64, len(self.data))
        self.assertFalse(ok)
        self.assertIn("ERR_HASH_MISMATCH", err)

    def test_none_size_skips_size_check(self):
        ok, err = build.check_cache_file(str(self.w), _sha(self.data), None)
        self.assertTrue(ok, err)

    def test_sha_case_insensitive(self):
        ok, err = build.check_cache_file(
            str(self.w), _sha(self.data).upper(), len(self.data)
        )
        self.assertTrue(ok, err)


# small fs helpers kept local so the suite stays stdlib-only
def _mkdtemp():
    import tempfile

    return tempfile.TemporaryDirectory(prefix="bonsai2-img-test-")


def _rmtree(p: Path):
    import shutil

    shutil.rmtree(p, ignore_errors=True)


class TestBuildTimeoutPath(unittest.TestCase):
    """The hung-buildx path: subprocess.run times out -> main() exits 5
    with ERR_TIMEOUT. subprocess.run is monkeypatched (no docker, no
    GPU); the pipeline is short-circuited up to the buildx call with
    patched pure helpers + a minimal valid YAML."""

    def setUp(self):
        self._tmp = _mkdtemp()
        self.tmp = Path(self._tmp.name)
        self.cfg = self.tmp / "subagent-engine.yaml"
        self.cfg.write_text(
            textwrap.dedent(
                """\
                engine:
                  binary: /opt/bonsai2/bin/llama-server
                  pin:
                    repo: https://github.com/PrismML-Eng/llama.cpp
                    commit: "9a9394a895b96003ca842a6041cb28ac49a108f7"
                image:
                  pin:
                    tag: "bonsai2-server:<tag-at-install>"
                    sha256: "<docker-content-digest-at-install>"
                model:
                  repo: prism-ml/Ternary-Bonsai-2-27B-gguf
                  revision: main
                  file: bonsai-2-27b-gguf-PQ2_0.gguf
                  sha256: "3907dc1658db1f78a9826bf8d5bcb8dc65db0d466388937af57f2294fae62ec1"
                  size_bytes: 7206168928
                  fallback_file: bonsai-2-27b-gguf-PTQ1_0.gguf
                  fallback_sha256: "0007dc1658db1f78a9826bf8d5bcb8dc65db0d466388937af57f2294fae62ec"
                  fallback_size_bytes: 5946648928
                """
            )
        )
        self._orig = {}
        for name in (
            "docker_image_exists",
            "banner_check_build_image",
            "check_cache_file",
            "stage_context",
            "rotate_build_logs",
            "TEST_LOG_DIR",
        ):
            self._orig[name] = getattr(build, name)

    def tearDown(self):
        for name, val in self._orig.items():
            setattr(build, name, val)
        _rmtree(self.tmp)

    def test_hung_buildx_is_exit_5_err_timeout(self):
        import io
        import subprocess
        from contextlib import redirect_stderr

        build.docker_image_exists = lambda image: True
        build.banner_check_build_image = lambda image, pin8: pin8
        build.check_cache_file = lambda path, sha, size: (True, "")
        build.stage_context = lambda cache_dir, default_file, fallback_file, dest: None
        build.rotate_build_logs = lambda keep=3: None
        build.TEST_LOG_DIR = self.tmp

        def _run_timeout(*args, **kwargs):
            raise subprocess.TimeoutExpired(args[0] if args else kwargs.get("cmd"), 5.0)

        orig_run = build.subprocess.run
        build.subprocess.run = _run_timeout
        try:
            err = io.StringIO()
            with redirect_stderr(err):
                rc = build.main(
                    [
                        "--config",
                        str(self.cfg),
                        "--build-image",
                        "bonsai2-server-build:121a",
                        "--tag",
                        "bonsai2-server:1.0",
                        "--cache-dir",
                        str(self.tmp),
                        "--build-timeout",
                        "5",
                    ]
                )
        finally:
            build.subprocess.run = orig_run
        self.assertEqual(rc, build.EXIT_TIMEOUT, err.getvalue())
        self.assertEqual(rc, 5, err.getvalue())
        self.assertIn("ERR_TIMEOUT", err.getvalue())
        self.assertIn("timed out", err.getvalue())

    def test_default_build_timeout_is_two_hours(self):
        import inspect

        sig = inspect.signature(build.build)
        self.assertEqual(sig.parameters["build_timeout_s"].default, 7200.0)
        self.assertEqual(build.DEFAULT_BUILD_TIMEOUT_S, 7200.0)

    def _run_main_with_raised_build(self, msg: str) -> int:
        # Drive main()'s exit-code dispatch directly: patch the whole
        # build() to raise a ValueError carrying `msg`, return rc.
        import io
        from contextlib import redirect_stderr

        orig = build.build

        def _raise(*a, **k):
            raise ValueError(msg)

        build.build = _raise
        try:
            err = io.StringIO()
            with redirect_stderr(err):
                rc = build.main(
                    [
                        "--config",
                        str(self.cfg),
                        "--build-image",
                        "bonsai2-server-build:121a",
                        "--tag",
                        "bonsai2-server:1.0",
                        "--cache-dir",
                        str(self.tmp),
                    ]
                )
        finally:
            build.build = orig
        return rc

    def test_exit_routing_leads_with_token(self):
        # G3c round-3 P1 regression: routing is on the LEADING token, not a
        # substring anywhere. Each named error must land on its own exit
        # code, and a message that merely MENTIONS another token (e.g. a
        # path containing 'ERR_CONFIG') must NOT be misrouted to it.
        self.assertEqual(
            self._run_main_with_raised_build("ERR_CONFIG: config bad"),
            build.EXIT_CONFIG,
        )
        self.assertEqual(
            self._run_main_with_raised_build("ERR_HASH_MISMATCH: sha bad"),
            build.EXIT_HASH,
        )
        self.assertEqual(
            self._run_main_with_raised_build("ERR_TIMEOUT: buildx hung"),
            build.EXIT_TIMEOUT,
        )
        # The misroute case: the leading token is ERR_HASH_MISMATCH but the
        # message body contains "ERR_CONFIG" (a path). Old `in str(exc)`
        # logic returned EXIT_CONFIG; leading-token routing must return
        # EXIT_HASH.
        self.assertEqual(
            self._run_main_with_raised_build(
                "ERR_HASH_MISMATCH: cache file /tmp/ERR_CONFIG_x.sha mismatch"
            ),
            build.EXIT_HASH,
        )

    def test_write_image_pin_is_indent_agnostic(self):
        # G3c round-3 P1 regression: the pin writer must not assume a
        # specific YAML emitter's indentation. image.pin leaves indented
        # by 4 or 6 spaces (hand-edited / a different emitter) must both
        # round-trip — the old `^(    tag:)` (hard 4-space) regex raised
        # ERR_CONFIG on anything but exactly 4 spaces.
        import hashlib

        digest = "sha256:" + hashlib.sha256(b"x").hexdigest()
        for indent in ("    ", "      "):
            src = (
                "engine:\n"
                "  binary: /x\n"
                "image:\n"
                "  pin:\n"
                + indent
                + 'tag: "bonsai2-server:<tag-at-install>"\n'
                + indent
                + 'sha256: "<docker-content-digest-at-install>"\n'
                "model:\n"
                "  file: y\n"
            )
            cfg = self.tmp / f"indent{len(indent)}.yaml"
            cfg.write_text(src)
            out = build.write_image_pin(str(cfg), "bonsai2-server:9.9", digest)
            text = Path(out).read_text()
            self.assertIn('tag: "bonsai2-server:9.9"', text, f"indent {indent!r}")
            self.assertIn(f'sha256: "{digest}"', text, f"indent {indent!r}")
            # The indentation the file had is preserved.
            self.assertIn(indent + "tag:", text, f"indent {indent!r} preserved")


if __name__ == "__main__":
    unittest.main()
