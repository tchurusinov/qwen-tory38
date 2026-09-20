"""MAP 1.2 / S1.8 #1: the built bonsai2 fork binary prints the build banner.

Runs against the locally built `bonsai2-server-build:121a` image (the
MAP 1.2 build pipeline, image/Dockerfile). Skips when the image is not
present locally (fresh clone before the first build). `--version` is
run with `--gpus all`: the binary calls ggml_cuda_init, which touches
the GPU stack, so the @gpu label + VRAM audit rule apply (Global
testing discipline: @gpu tests live in tests/gpu/ only). It is the
only test here that allocates a CUDA context — and only a transient
one: no model is loaded, init + exit measures <1s (probe 2026-09-20).
The ldd/file tests run without `--gpus` because they only read ELF
metadata.

Assertions (the S1.8 #1 "fork ternary banner", adapted to this pin):
  * `llama-server --version` exits 0
  * the banner line carries the engine pin: the `commit` field contains
    the first 8 hex chars of the `engine_pin.commit` recorded in
    etc/subagent-engine.yaml (the pin only exists on the fork's prism
    branch, so the commit is the fork assertion; this fork has no
    separate ternary banner line, and --version does not print the
    llama_print_system_info "CUDA1 = 1" line, which is a model-load
    banner)
  * the CUDA backend is built in: the binary links libggml-cuda
  * the binary is an aarch64 ELF (the S1.2 aarch64-only rule)
"""

import re
import shutil
import subprocess
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
ENGINE_YAML = REPO_ROOT / "etc" / "subagent-engine.yaml"
BUILD_IMAGE = "bonsai2-server-build:121a"
BIN_IN_IMAGE = "/opt/bonsai2/bin/llama-server"


def _engine_pin_sha8() -> str:
    """First 8 hex chars of the pinned fork commit from the template YAML.

    stdlib-only parse (hermetic-suite rule): the value lives on the line
    `    commit: "<40-hex>"` under engine.pin (S1.6). Boundary-based:
    the engine block runs from the `engine:` top-level key to the next
    top-level key (`image:`), so a future `commit:` under another block
    cannot re-anchor this assertion.
    """
    text = ENGINE_YAML.read_text()
    m_block = re.search(r"(?m)^engine:.*?(?=^image:)", text, re.DOTALL)
    if m_block is None:
        raise AssertionError(f"engine: block not found in {ENGINE_YAML}")
    m = re.search(r'commit:\s*"([0-9a-f]{40})"', m_block.group(0))
    assert m, f"engine_pin.commit not found in {ENGINE_YAML}"
    return m.group(1)[:8]


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


class TestForkBuildBanner(unittest.TestCase):
    @unittest.skipUnless(
        _docker_image_present(BUILD_IMAGE),
        f"build image {BUILD_IMAGE} not present (run the MAP 1.2 pipeline: "
        f"docker build --platform linux/arm64 -f image/Dockerfile -t "
        f"{BUILD_IMAGE} .)",
    )
    def test_version_banner_carries_engine_pin(self):
        r = subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "--gpus",
                "all",
                "--entrypoint",
                BIN_IN_IMAGE,
                BUILD_IMAGE,
                "--version",
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
        )
        self.assertEqual(r.returncode, 0, r.stderr[-500:])
        out = r.stdout + r.stderr
        # S1.8 #1: the commit-carrying build line is the fork assertion.
        self.assertIn(
            _engine_pin_sha8(),
            out,
            f"engine pin prefix not in --version output:\n{out}",
        )

    @unittest.skipUnless(
        _docker_image_present(BUILD_IMAGE),
        "build image not present",
    )
    def test_binary_links_cuda_backend(self):
        r = subprocess.run(
            ["docker", "run", "--rm", "--entrypoint", "ldd", BUILD_IMAGE, BIN_IN_IMAGE],
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
        self.assertEqual(r.returncode, 0, r.stderr[-500:])
        # CUDA backend built in (S1.2: GGML_CUDA=ON): the server links
        # libggml-cuda. (The "CUDA1 = 1" line only appears in the
        # model-load system-info banner, not in --version output.)
        self.assertIn("libggml-cuda", r.stdout, r.stdout)

    @unittest.skipUnless(
        _docker_image_present(BUILD_IMAGE),
        "build image not present",
    )
    def test_binary_is_aarch64_elf(self):
        r = subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "--entrypoint",
                "file",
                BUILD_IMAGE,
                BIN_IN_IMAGE,
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
        self.assertEqual(r.returncode, 0, r.stderr[-500:])
        self.assertIn("aarch64", r.stdout, r.stdout)


if __name__ == "__main__":
    unittest.main()
