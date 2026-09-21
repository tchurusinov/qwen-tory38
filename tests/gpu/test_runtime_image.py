"""MAP 1.4 / S1.6 / S6.1: the runtime image carries the fork + models + CUDA.

Runs against the locally built `bonsai2-server:1.0` runtime image (the
MAP 1.4 artifact, image/Dockerfile.runtime + bin/bonsai2_build_image.py).
Skips when the image is not present locally (fresh clone / pre-build
box — the same skipUnless convention as test_build_banner.py).

Assertions (the S6.1 artifact contract, checked inside the image):
  * `--version` (with --gpus all: ggml_cuda_init touches the GPU stack)
    exits 0 and the banner carries the engine-pin prefix from
    etc/subagent-engine.yaml (the S1.8 #1 fork assertion, runtime half)
  * the binary is an aarch64 ELF (the S1.2 aarch64-only rule)
  * libggml-cuda is linked + resolves from /opt/bonsai2/bin (S1.2
    GGML_CUDA=ON; RUNPATH $ORIGIN baked by the MAP 1.2 pipeline)
  * both model files are present at the pinned byte sizes (S1.1/S1.3;
    the in-build sha check already passed — this is the size side of
    the same pin, read back post-build)
  * the S1.3 serving command is the image's default CMD (S1.3 verbatim
    flags; docker create + inspect, no GPU needed)
"""

import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
ENGINE_YAML = REPO_ROOT / "etc" / "subagent-engine.yaml"
RUNTIME_IMAGE = "bonsai2-server:1.0"
BIN_IN_IMAGE = "/opt/bonsai2/bin/llama-server"
DEFAULT_MODEL = "/opt/bonsai2/models/bonsai-2-27b-gguf-PQ2_0.gguf"
FALLBACK_MODEL = "/opt/bonsai2/models/bonsai-2-27b-gguf-PTQ1_0.gguf"


def _engine_pin_sha8() -> str:
    """First 8 hex of the pinned fork commit (same parse as test_build_banner)."""
    text = ENGINE_YAML.read_text()
    m_block = re.search(r"(?m)^engine:.*?(?=^image:)", text, re.DOTALL)
    if m_block is None:
        raise AssertionError(f"engine: block not found in {ENGINE_YAML}")
    m = re.search(r'commit:\s*"?([0-9a-f]{40})"?', m_block.group(0))
    assert m, f"engine_pin.commit not found in {ENGINE_YAML}"
    return m.group(1)[:8]


def _model_pins() -> tuple[int, int]:
    """(size_bytes, fallback_size_bytes) from the template's model block."""
    text = ENGINE_YAML.read_text()
    m = re.search(r"(?m)^model:.*?(?=^\S|\Z)", text, re.DOTALL)
    if m is None:
        raise AssertionError(f"model: block not found in {ENGINE_YAML}")
    block = m.group(0)
    size = re.search(r"(?m)^\s*size_bytes:\s*(\d+)", block)
    fsize = re.search(r"(?m)^\s*fallback_size_bytes:\s*(\d+)", block)
    assert size and fsize, f"model size pins not found in {ENGINE_YAML}"
    return int(size.group(1)), int(fsize.group(1))


def _docker_image_present(image: str) -> bool:
    if shutil.which("docker") is None:
        return False
    r = subprocess.run(
        ["docker", "image", "inspect", image],
        capture_output=True,
        text=True,
        check=False,
    )
    return r.returncode == 0


class TestRuntimeImage(unittest.TestCase):
    """The MAP 1.4 artifact: binary + models + CUDA runtime in one image."""

    @unittest.skipUnless(
        _docker_image_present(RUNTIME_IMAGE),
        f"runtime image {RUNTIME_IMAGE} not present (run "
        f"python3 bin/bonsai2_build_image.py, MAP 1.4)",
    )
    def test_banner_carries_engine_pin(self):
        r = subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "--gpus",
                "all",
                "--entrypoint",
                BIN_IN_IMAGE,
                RUNTIME_IMAGE,
                "--version",
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
        )
        self.assertEqual(r.returncode, 0, r.stderr[-500:])
        out = r.stdout + r.stderr
        self.assertIn(
            _engine_pin_sha8(),
            out,
            f"engine pin prefix not in --version output:\n{out}",
        )

    @unittest.skipUnless(
        _docker_image_present(RUNTIME_IMAGE), "runtime image not present"
    )
    def test_binary_is_aarch64_elf(self):
        # Byte-exact check (no `file` in the runtime base): e_magic
        # 7f454c46 (\x7f 'E' 'L' 'F') + e_machine 0xB7 (AArch64) at
        # offset 18 (bytes 18-19, little-endian: b7 00).
        r = subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "--entrypoint",
                "sh",
                RUNTIME_IMAGE,
                "-c",
                f'head -c 20 {BIN_IN_IMAGE} | od -An -tx1 | tr -d " \\n"',
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
        self.assertEqual(r.returncode, 0, r.stderr[-500:])
        head = r.stdout.strip()
        self.assertEqual(head[:8], "7f454c46", f"e_magic {head[:8]}")
        self.assertEqual(
            head[16:20], "b700", f"e_machine {head[16:20]} (expected b700 = AArch64)"
        )

    @unittest.skipUnless(
        _docker_image_present(RUNTIME_IMAGE), "runtime image not present"
    )
    def test_cuda_backend_resolves_from_own_dir(self):
        # env -i: no inherited env can mask a resolution failure.
        r = subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "--entrypoint",
                "sh",
                RUNTIME_IMAGE,
                "-c",
                f"env -i ldd {BIN_IN_IMAGE}",
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
        self.assertEqual(r.returncode, 0, r.stderr[-500:])
        self.assertNotIn("not found", r.stdout)
        self.assertIn("/opt/bonsai2/bin/libggml-cuda", r.stdout, r.stdout)

    @unittest.skipUnless(
        _docker_image_present(RUNTIME_IMAGE), "runtime image not present"
    )
    def test_models_baked_at_pinned_sizes(self):
        size_default, size_fallback = _model_pins()
        r = subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "--entrypoint",
                "sh",
                RUNTIME_IMAGE,
                "-c",
                f"stat -c %s {DEFAULT_MODEL}; stat -c %s {FALLBACK_MODEL}",
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
        self.assertEqual(r.returncode, 0, r.stderr[-500:])
        got = [int(x) for x in r.stdout.split()]
        self.assertEqual(
            got[0], size_default, f"default model size {got[0]} != pin {size_default}"
        )
        self.assertEqual(
            got[1],
            size_fallback,
            f"fallback model size {got[1]} != pin {size_fallback}",
        )

    @unittest.skipUnless(
        _docker_image_present(RUNTIME_IMAGE), "runtime image not present"
    )
    def test_default_cmd_is_s1_3_verbatim(self):
        r = subprocess.run(
            ["docker", "create", RUNTIME_IMAGE],
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
        cid = r.stdout.strip()
        self.assertEqual(r.returncode, 0, r.stderr[-500:])
        try:
            insp = subprocess.run(
                ["docker", "inspect", "--format", "{{json .Config.Cmd}}", cid],
                capture_output=True,
                text=True,
                check=False,
            )
            cmd = json.loads(insp.stdout)
            # S1.3: the full flag set must be the image default (the S2.2
            # unit supplies port_base+i at docker-run time; the default
            # here is instance 0's port, 30101).
            expected_flags = [
                "--model",
                DEFAULT_MODEL,
                "--ctx-size",
                "262144",
                "--parallel",
                "8",
                "--batch-size",
                "2048",
                "--no-prompt",
                "--port",
                "30101",
                "--api-key",
                "bonsai2-local",
                "--reasoning-format",
                "deepseek_r1",
                "--alias",
                "qwen38-27b-subagent",
            ]
            self.assertEqual(cmd[1:], expected_flags, f"S1.3 CMD drifted: {cmd}")
        finally:
            subprocess.run(
                ["docker", "rm", "-f", cid], capture_output=True, check=False
            )


if __name__ == "__main__":
    unittest.main()
