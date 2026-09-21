#!/usr/bin/env python3
"""bonsai2_build_image.py — MAP 1.4 / S1.6 / S6.1: runtime image builder.

Builds the `bonsai2-server:<tag>` runtime image (SPEC S6.1: binary +
model files + CUDA runtime, main-stack parity) and records its docker
content digest as `image.pin` in `etc/subagent-engine.yaml` (the S1.6
image_pin, "recorded at install").

Inputs (all from the YAML pins — S1.6 is the source of truth):
  * engine.pin.commit   -> PIN8 build-arg (the in-image banner check)
  * model block         -> SHA/size build-args + the host cache files
                           that get baked (MAP 1.3 cache dir)

Pipeline (fail fast, nothing is written on a bad input):
  1. read + validate the YAML pins (pure `load_build_inputs`)
  2. verify the MAP 1.2 build image exists locally AND its binary
     banner carries the YAML engine pin (a stale build image must not
     silently ship an old fork commit — cross-checked here, not just
     in-build, so the failure surfaces before a 13 GB layer is written)
  3. pre-verify the host model cache files against the YAML pins
     (sha256 + size; the S1.1 "hash-verified before baking" clause,
     enforced BEFORE the build so a bad cache file costs one hash pass,
     not a full image build; re-checked in-build by the Dockerfile)
  4. stage a minimal build context (models/ only — the binary comes
     from `COPY --from=<build image>`, never from the context) and run
     `docker buildx build` with the build args; the build log is kept
     in test-logs/build-runtime-<ts>.log (rotation: keep last 3, same
     convention as the MAP 1.2 build log)
  5. read the content digest back from `docker image inspect`
     (pure `read_image_digest`) and write `image.pin: {tag, sha256}`
     into the YAML (pure `write_image_pin`; the S1.6 "recorded at
     install" value)

Exit codes (S1.5 closed-set subset for this step):
  0 = image built + pin recorded
  2 = ERR_CONFIG (YAML missing/malformed, docker missing, bad digest
      read-back)
  4 = ERR_HASH_MISMATCH (cache file or build image vs the YAML pins)
  5 = ERR_TIMEOUT (docker buildx build exceeded --build-timeout)

Stdlib + PyYAML (config read/write only — the same convention as
bin/bonsai2_model_fetch.py; the hermetic test suite is stdlib-only and
drives the pure functions without docker or yaml).

Usage:
  python3 bin/bonsai2_build_image.py [--config PATH]
                                     [--build-image IMG]
                                     [--tag TAG]
                                     [--cache-dir DIR]
                                     [--build-timeout SECONDS]
                                     [--keep-context]
"""

from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = REPO_ROOT / "etc" / "subagent-engine.yaml"
DEFAULT_CACHE_DIR = "/opt/bonsai2/cache"
BUILD_IMAGE = "bonsai2-server-build:121a"  # MAP 1.2 build pipeline (fixed tag)
DEFAULT_TAG = "bonsai2-server:1.0"  # stable tag; the PIN is {tag, sha256}, S1.6
RUNTIME_DOCKERFILE = REPO_ROOT / "image" / "Dockerfile.runtime"
BIN_IN_BUILD_IMAGE = "/opt/bonsai2/bin/llama-server"
TEST_LOG_DIR = REPO_ROOT / "test-logs"

EXIT_CONFIG = 2
EXIT_HASH = 4
EXIT_TIMEOUT = 5

# A 13 GB layer build can legitimately take a long time — 2 h default;
# a hung buildx (dockerd wedge, stuck pull) must not hang the
# orchestrator forever like the other _docker() calls would.
DEFAULT_BUILD_TIMEOUT_S = 7200.0

ERR_CONFIG = "ERR_CONFIG"
ERR_HASH_MISMATCH = "ERR_HASH_MISMATCH"
ERR_TIMEOUT = "ERR_TIMEOUT"

# The S1.1 on-disk names (S1.3 serving command paths), from the YAML.
DEFAULT_MODEL_NAME = "bonsai-2-27b-gguf-PQ2_0.gguf"
FALLBACK_MODEL_NAME = "bonsai-2-27b-gguf-PTQ1_0.gguf"


# --------------------------------------------------------------------------
# pure helpers (hermetic: no docker, no yaml, no fs beyond the arguments)
# --------------------------------------------------------------------------


def pin8_of_commit(commit: str) -> str:
    """First 8 hex of a 40-hex commit (the banner's `commit <8hex>` field)."""
    c = commit.strip().lower()
    if not re.fullmatch(r"[0-9a-f]{40}", c):
        raise ValueError(f"ERR_CONFIG: engine pin commit is not 40 hex: {commit!r}")
    return c[:8]


def normalize_digest(value: str) -> str:
    """Normalize a docker digest to `sha256:<64-hex>`.

    Accepts the `sha256:<hex>` form (docker RepoDigests / .Id) and the
    bare-<hex> form; rejects anything else (a pin is a content digest,
    S1.6 — an image *ID* without the algorithm prefix is not one).
    """
    v = value.strip().lower()
    v = v.removeprefix("sha256:")
    if not re.fullmatch(r"[0-9a-f]{64}", v):
        raise ValueError(f"ERR_CONFIG: not a sha256 digest: {value!r}")
    return f"sha256:{v}"


def write_image_pin(
    config_path: str, tag: str, digest: str, out_path: str | None = None
) -> str:
    """Write `image.pin: {tag, sha256}` into the YAML (S1.6 image_pin).

    Surgical, comment-preserving update: replaces ONLY the two
    `image.pin` leaf lines (`tag:` / `sha256:`) inside the `image:`
    block — every other byte of the file (comments, the engine/model
    blocks) is untouched, so the tracked template does not get
    re-flowed into a flat safe_dump. The `image:` block must exist
    with both leaf lines (the template shape); anything else is an
    ERR_CONFIG (a pin written into a YAML that no longer has the pin
    shape would be a silent contract break).

    When PyYAML is importable the written file is re-parsed and the
    pin re-read (round-trip assert); without PyYAML the line-level
    assertions still hold (the hermetic suite is stdlib-only).
    Returns the written path.
    """
    digest = normalize_digest(digest)
    with open(config_path, "r", encoding="utf-8") as f:
        text = f.read()

    # isolate the image: block: from the `^image:` line to the next
    # top-level key (a non-indented line) or EOF.
    m_block = re.search(r"(?m)^image:\n(.*?)(?=^\S|\Z)", text, re.DOTALL)
    if m_block is None:
        raise ValueError(f"ERR_CONFIG: no `image:` block in {config_path}")
    block = m_block.group(1)
    # The two pin leaf lines are matched indent-agnostically (G3c
    # round-3 P1): the writer must not assume a specific YAML emitter's
    # indentation (4 spaces here, but any [ \t]* depth on the `tag:` /
    # `sha256:` leaves). group(1) captures the leading whitespace +
    # `tag:`/`sha256:` so the splice preserves whatever indent was there.
    m_tag = re.search(r"(?m)^([ \t]*tag:).*$", block)
    m_sha = re.search(r"(?m)^([ \t]*sha256:).*$", block)
    if not m_tag or not m_sha:
        raise ValueError(
            f"ERR_CONFIG: image block in {config_path} lacks the "
            f"`tag:`/`sha256:` pin lines (template shape changed?)"
        )

    # replace within the block, then splice the block back
    new_block = (
        block[: m_tag.start()]
        + f'{m_tag.group(1)} "{tag}"'
        + block[m_tag.end() : m_sha.start()]
        + f'{m_sha.group(1)} "{digest}"'
        + block[m_sha.end() :]
    )
    new_text = text[: m_block.start(1)] + new_block + text[m_block.end(1) :]

    out = config_path if out_path is None else out_path
    with open(out, "w", encoding="utf-8") as f:
        f.write(new_text)

    # line-level round-trip assert (always):
    with open(out, "r", encoding="utf-8") as f:
        back = f.read()
    if not re.search(
        rf'(?m)^image:\n.*?^[ \t]*tag: "{re.escape(tag)}"', back, re.DOTALL
    ):
        raise ValueError(f"ERR_CONFIG: image.pin tag not written to {out}")
    if not re.search(
        rf'(?m)^image:\n.*?^[ \t]*sha256: "{re.escape(digest)}"', back, re.DOTALL
    ):
        raise ValueError(f"ERR_CONFIG: image.pin sha256 not written to {out}")
    # YAML round-trip assert (when PyYAML is present): the file must
    # still parse and carry the pin under the right keys.
    try:
        import yaml  # PyYAML: box dep (same convention as MAP 1.3 tools)
    except ImportError:
        return out
    with open(out, "r", encoding="utf-8") as f:
        parsed = yaml.safe_load(f)
    got = ((parsed or {}).get("image") or {}).get("pin") or {}
    if got.get("tag") != tag or got.get("sha256") != digest:
        raise ValueError(f"ERR_CONFIG: image.pin round-trip failed: {got!r}")
    return out


def sha256_file(path: str, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


def check_cache_file(
    path: str, expected_sha: str, expected_size: int | None
) -> tuple[bool, str]:
    """Pre-build host cache check (S1.1 "hash-verified before baking").

    Returns (ok, err) with err in {None, ERR_CONFIG, ERR_HASH_MISMATCH}.
    """
    if not os.path.exists(path):
        return False, f"{ERR_CONFIG}: cache file missing: {path}"
    size = os.path.getsize(path)
    if expected_size is not None and size != expected_size:
        return False, f"{ERR_HASH_MISMATCH}: {path}: size {size} != pin {expected_size}"
    if expected_sha:
        got = sha256_file(path)
        if got.lower() != expected_sha.lower():
            return False, (
                f"{ERR_HASH_MISMATCH}: {path}: sha256 {got[:16]}... != pin "
                f"{expected_sha[:16]}..."
            )
    return True, ""


# --------------------------------------------------------------------------
# docker-side (not exercised by the hermetic suite)
# --------------------------------------------------------------------------


def _docker(*args: str, timeout: float = 600.0) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["docker", *args], capture_output=True, text=True, timeout=timeout, check=False
    )


def docker_image_exists(image: str) -> bool:
    return _docker("image", "inspect", image).returncode == 0


def read_image_digest(tag: str) -> str:
    """The image's content digest (the S1.6 image_pin `sha256`).

    A locally built image has empty RepoDigests, so the digest is read
    from `docker image inspect .Id` (the config digest — the
    `sha256:...` that `docker run` resolves to; for locally built
    images this IS the content digest, probed 2026-09-21).
    """
    r = _docker("image", "inspect", tag)
    if r.returncode != 0:
        raise ValueError(
            f"{ERR_CONFIG}: docker image inspect {tag} failed: {r.stderr.strip()}"
        )
    try:
        meta = json.loads(r.stdout)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"{ERR_CONFIG}: docker image inspect {tag}: bad JSON: {exc}"
        ) from exc
    img = meta[0] if isinstance(meta, list) and meta else {}
    # prefer a RepoDigest (remote images); fall back to the local .Id
    digests = img.get("RepoDigests") or []
    raw = (
        digests[0].split("@", 1)[1]
        if digests and "@" in digests[0]
        else img.get("Id", "")
    )
    return normalize_digest(raw)


def banner_check_build_image(build_image: str, expected_pin8: str) -> str:
    """Verify the MAP 1.2 build image carries the YAML engine pin.

    Runs `--version` WITHOUT GPUs (the banner needs no CUDA context;
    the ggml_cuda_init failure line is the expected no-GPU outcome,
    same as the MAP 1.2 build assertions). Returns the banner text;
    raises on any mismatch (a stale build image = the runtime image
    would ship an old fork commit).
    """
    r = _docker(
        "run",
        "--rm",
        "--entrypoint",
        BIN_IN_BUILD_IMAGE,
        build_image,
        "--version",
        timeout=120,
    )
    if r.returncode != 0:
        raise ValueError(
            f"{ERR_CONFIG}: build image {build_image} --version failed: {r.stderr[-300:]}"
        )
    out = r.stdout + r.stderr
    if f"commit {expected_pin8}" not in out:
        raise ValueError(
            f"{ERR_HASH_MISMATCH}: build image {build_image} banner does not carry "
            f"engine pin {expected_pin8} (stale build image — rebuild it via "
            f"image/Dockerfile, MAP 1.2):\n{out.strip()}"
        )
    return out


def stage_context(
    cache_dir: str, default_file: str, fallback_file: str, dest: str
) -> None:
    """Stage the minimal build context: <dest>/models/<the two quants>."""
    models_dir = Path(dest) / "models"
    models_dir.mkdir(parents=True)
    for name in (default_file, fallback_file):
        src = Path(cache_dir) / name
        if not src.is_file():
            raise ValueError(f"{ERR_CONFIG}: staging source missing: {src}")
        shutil.copy2(src, models_dir / name)


def rotate_build_logs(keep: int = 3) -> None:
    TEST_LOG_DIR.mkdir(parents=True, exist_ok=True)
    logs = sorted(glob.glob(str(TEST_LOG_DIR / "build-runtime-*.log")))
    for old in logs[:-keep]:
        os.remove(old)


def build(
    *,
    config: str,
    build_image: str,
    tag: str,
    cache_dir: str,
    keep_context: bool = False,
    build_timeout_s: float = DEFAULT_BUILD_TIMEOUT_S,
    log=print,
) -> str:
    """Full MAP 1.4 pipeline. Returns the written content digest.

    The pin itself is written into the YAML by the caller (`main` ->
    `write_image_pin`) AFTER the build returns: an interrupted build
    (SIGKILL between build success and pin write) leaves a built but
    unpinned image — install.sh's rebuild-skip check (S6.1 step 2,
    `image.pin` vs local image) rebuilds when the pin is missing, so
    the interruption is recoverable, not silent.
    """
    import yaml  # PyYAML: box dep (config read), same convention as MAP 1.3

    with open(config, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    commit = ((data.get("engine") or {}).get("pin") or {}).get("commit", "")
    model = data.get("model") or {}
    default_sha = (model.get("sha256") or "").strip().lower()
    fallback_sha = (model.get("fallback_sha256") or "").strip().lower()
    try:
        default_size = int(model["size_bytes"])
        fallback_size = int(model["fallback_size_bytes"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(
            f"{ERR_CONFIG}: model size pins missing/bad in {config}: {exc}"
        ) from exc
    default_file = model.get("file") or DEFAULT_MODEL_NAME
    fallback_file = model.get("fallback_file") or FALLBACK_MODEL_NAME
    if not default_sha or not fallback_sha:
        raise ValueError(f"{ERR_CONFIG}: model sha pins missing in {config}")
    pin8 = pin8_of_commit(commit)
    log(f"[build] engine pin {pin8}, tag {tag}, build image {build_image}")

    # 1) the binary source image exists and carries the pin
    if not docker_image_exists(build_image):
        raise ValueError(
            f"{ERR_CONFIG}: build image {build_image} not present — run the MAP 1.2 "
            f"pipeline first (docker build --platform linux/arm64 -f image/Dockerfile "
            f"-t {build_image} .)"
        )
    banner_check_build_image(build_image, pin8)

    # 2) host cache pre-verification (S1.1 "hash-verified before baking")
    for path, sha, size in (
        (os.path.join(cache_dir, default_file), default_sha, default_size),
        (os.path.join(cache_dir, fallback_file), fallback_sha, fallback_size),
    ):
        ok, err = check_cache_file(path, sha, size)
        if not ok:
            raise ValueError(err)
        log(f"[build] cache ok: {os.path.basename(path)}")

    # 3) stage + build
    if not RUNTIME_DOCKERFILE.is_file():
        raise ValueError(
            f"{ERR_CONFIG}: runtime Dockerfile missing: {RUNTIME_DOCKERFILE}"
        )
    ctx = tempfile.mkdtemp(prefix="bonsai2-ctx-")
    try:
        stage_context(cache_dir, default_file, fallback_file, ctx)
        rotate_build_logs()
        ts = time.strftime("%Y%m%d-%H%M%S")
        log_path = TEST_LOG_DIR / f"build-runtime-{ts}.log"
        log(f"[build] build log: {log_path}")
        cmd = [
            "docker",
            "buildx",
            "build",
            "--platform",
            "linux/arm64",
            "-f",
            str(RUNTIME_DOCKERFILE),
            "--build-arg",
            f"PIN8={pin8}",
            "--build-arg",
            f"MODEL_SHA_DEFAULT={default_sha}",
            "--build-arg",
            f"MODEL_SHA_FALLBACK={fallback_sha}",
            "--build-arg",
            f"MODEL_SIZE_DEFAULT={default_size}",
            "--build-arg",
            f"MODEL_SIZE_FALLBACK={fallback_size}",
            "-t",
            tag,
        ]
        # mirror the build log to the console (tail) and the log file
        with open(log_path, "wb") as lf:
            try:
                p = subprocess.run(
                    [*cmd, "--progress", "plain", ctx],
                    stdout=lf,
                    stderr=subprocess.STDOUT,
                    check=False,
                    timeout=build_timeout_s,
                )
            except subprocess.TimeoutExpired:
                raise ValueError(
                    f"{ERR_TIMEOUT}: docker buildx build timed out after "
                    f"{build_timeout_s:.0f}s (log {log_path})"
                )
        tail = "\n".join(log_path.read_text(errors="replace").splitlines()[-15:])
        if p.returncode != 0:
            log(f"[build] FAILED (exit {p.returncode}), last lines:\n{tail}")
            raise subprocess.CalledProcessError(p.returncode, cmd)
        log("[build] docker build ok")
    finally:
        if not keep_context:
            shutil.rmtree(ctx, ignore_errors=True)

    # 4) read the digest back + record the pin
    digest = read_image_digest(tag)
    log(f"[build] content digest: {digest[:24]}...")
    return digest


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Build the bonsai2-server runtime image + record image_pin (MAP 1.4)."
    )
    ap.add_argument(
        "--config", default=str(DEFAULT_CONFIG), help="subagent-engine.yaml path"
    )
    ap.add_argument(
        "--build-image",
        default=BUILD_IMAGE,
        help=f"MAP 1.2 build-pipeline image (default {BUILD_IMAGE})",
    )
    ap.add_argument(
        "--tag", default=DEFAULT_TAG, help=f"runtime image tag (default {DEFAULT_TAG})"
    )
    ap.add_argument(
        "--cache-dir",
        default=DEFAULT_CACHE_DIR,
        help=f"model cache dir (default {DEFAULT_CACHE_DIR})",
    )
    ap.add_argument(
        "--build-timeout",
        type=float,
        default=DEFAULT_BUILD_TIMEOUT_S,
        help=f"docker buildx wall-clock budget in seconds (default {DEFAULT_BUILD_TIMEOUT_S:.0f})",
    )
    ap.add_argument(
        "--keep-context",
        action="store_true",
        help="keep the staged build context (debug)",
    )
    args = ap.parse_args(argv)

    try:
        digest = build(
            config=args.config,
            build_image=args.build_image,
            tag=args.tag,
            cache_dir=args.cache_dir,
            keep_context=args.keep_context,
            build_timeout_s=args.build_timeout,
        )
    except ValueError as exc:
        msg = str(exc)
        print(f"[build] {exc}", file=sys.stderr)
        # G3c round-3 P1: route on the LEADING token, not a substring
        # anywhere — every named error starts with its token (verified:
        # the token + ':' is the message head), so leading-token matching
        # is exact and does not misroute a message that merely mentions
        # another token (e.g. a path containing 'ERR_CONFIG').
        if msg.startswith(f"{ERR_TIMEOUT}:"):
            return EXIT_TIMEOUT
        if msg.startswith(f"{ERR_CONFIG}:"):
            return EXIT_CONFIG
        if msg.startswith(f"{ERR_HASH_MISMATCH}:"):
            return EXIT_HASH
        return EXIT_CONFIG
    except subprocess.CalledProcessError as exc:
        print(f"[build] docker build failed: {exc}", file=sys.stderr)
        return EXIT_CONFIG

    # record the pin (S1.6 image_pin) — only after a successful build
    try:
        out = write_image_pin(args.config, args.tag, digest)
    except Exception as exc:  # noqa: BLE001 - config write failure -> named config error
        print(f"[build] {ERR_CONFIG}: pin write failed: {exc!r}", file=sys.stderr)
        return EXIT_CONFIG
    print(f"[build] done: {args.tag} pinned at {digest} ({out})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
