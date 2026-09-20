#!/usr/bin/env python3
"""bonsai2_model_fetch.py — MAP 1.3 / S1.1 / S1.5: download + verify weights.

Downloads the bonsai2 model weights (default PQ2_0 and/or the PTQ1_0
fallback) from the pinned HuggingFace repo into the model cache dir,
verifies each file's SHA256 and byte size against the pin recorded in
`etc/subagent-engine.yaml` (the S1.6 `model` block), and exits with the
S1.5 closed-set code.

Retry / error contract (S1.5, "retry x3 / 10 s backoff"):
  * a download attempt that errors (network / non-200 / truncated body) is
    retried, up to `retries` attempts, `backoff` seconds between attempts;
  * a completed download whose SHA256 or size does not match the pin is a
    corrupt/partial artifact -> the file is re-fetched from scratch, same
    retry budget;
  * a partial `.part` file is resumed via an HTTP `Range` header
    (HF/CDN serve `Accept-Ranges: bytes`), so a flaky network does not
    re-transfer 7 GB from zero. A resume that the server answers with a full
    200 (Range ignored) restarts from byte 0.

Exit codes (S1.5 for this step):
  0 = every requested quant is present + verified (fetched now, or already
      cached and re-verified — the S6.4 "zero downloads" idempotent path);
  2 = ERR_CONFIG (config missing/malformed);
  4 = ERR_HASH_MISMATCH or ERR_DOWNLOAD after the retry budget is spent.

Stdlib only for the fetch/verify core (urllib, hashlib, os, time, argparse,
sys). PyYAML is used solely to read the config at main() (present on the box
and in the runtime image); the hermetic test suite is stdlib-only and drives
the pure core without a YAML load.

Usage:
  python3 bin/bonsai2_model_fetch.py [--cache-dir DIR]
                                     [--quant default|fallback|all]
                                     [--force] [--retries N] [--backoff SECS]
                                     [--config PATH]
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
import time
import urllib.error
import urllib.request

DEFAULT_CONFIG = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "etc",
    "subagent-engine.yaml",
)
DEFAULT_CACHE_DIR = "/opt/bonsai2/cache"
USER_AGENT = "bonsai2-model-fetch/1.0 (qwen-tory38; MAP 1.3)"
_CHUNK = 1 << 20  # 1 MiB
_TIMEOUT = 30.0

EXIT_CONFIG = 2
EXIT_ERR = 4  # S1.5 closed-set code for ERR_HASH_MISMATCH / ERR_DOWNLOAD

ERR_DOWNLOAD = "ERR_DOWNLOAD"
ERR_HASH_MISMATCH = "ERR_HASH_MISMATCH"


def sha256_file(path: str, chunk: int = _CHUNK) -> str:
    """Streaming SHA256 of a file (memory-bounded for multi-GB weights)."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


def verify(
    path: str, expected_sha: str | None, expected_size: int | None
) -> tuple[bool, str | None]:
    """Pure verification core: byte size + SHA256 of `path` against the pin.

    Returns (ok, err): err is ERR_HASH_MISMATCH on any content mismatch
    (wrong size means the bytes are not the pinned object, so it is reported
    under the same S1.5 "corrupt artifact" condition -> caller re-downloads),
    ERR_DOWNLOAD when the file is absent, None on success.
    """
    if not os.path.exists(path):
        return False, ERR_DOWNLOAD
    size = os.path.getsize(path)
    if expected_size is not None and size != expected_size:
        return False, ERR_HASH_MISMATCH
    if expected_sha and sha256_file(path).lower() != expected_sha.lower():
        return False, ERR_HASH_MISMATCH
    return True, None


def _stream(url: str, dest_part: str, expected_size: int | None) -> tuple[int, int]:
    """Stream `url` into `dest_part`, resuming from any existing partial.

    Returns (final_size, resumed_from). Raises URLError/HTTPError/OSError on
    transport failure; a truncated body (final size != pinned size when a
    pinned size is known) raises URLError so the caller retries. Resume is
    byte-accurate: a Range-ignored 200 restarts from byte 0. If the partial
    already holds exactly the pinned byte count, the fetch is short-circuited
    (nothing left to download) and the caller verifies the bytes it has.

    On ANY mid-transfer failure the `.part` on disk is a clean, byte-accurate
    prefix (the open-for-write context manager flushes closed bytes before
    the exception propagates). We re-measure it and fold that confirmed size
    into the raised error so the retry can audit and reuse it instead of
    blindly re-opening a partial it never inspected (G3c round 2 P1).
    """
    start = os.path.getsize(dest_part) if os.path.exists(dest_part) else 0
    if expected_size is not None and start > expected_size:
        start = 0  # partial longer than the pin: restart
    if expected_size is not None and start == expected_size:
        # The partial already holds exactly the pinned byte count (e.g. a run
        # that crashed after the download completed but before promotion).
        # Nothing left to fetch: short-circuit so the caller can verify-and-
        # promote it (or clear-and-retry on a hash mismatch) instead of
        # issuing `Range: bytes=size-`, getting HTTP 416, and burning the
        # retry budget (G3c round 4 P1@186).
        return start, start
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    if start > 0:
        req.add_header("Range", f"bytes={start}-")
    got = start
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            code = getattr(resp, "status", resp.getcode())
            resume = start > 0 and code == 206
            got = start if resume else 0
            with open(dest_part, "ab" if resume else "wb") as out:
                for block in iter(lambda: resp.read(_CHUNK), b""):
                    out.write(block)
                    got += len(block)
    except Exception:
        # Mid-transfer reset / non-200 / early close. `dest_part` holds the
        # bytes actually written so far; surface that confirmed size so the
        # caller's resume/clear decision is on inspected data, not a guess.
        on_disk = os.path.getsize(dest_part) if os.path.exists(dest_part) else 0
        if expected_size is not None and on_disk != expected_size:
            raise urllib.error.URLError(
                f"transfer interrupted at {on_disk}B of {expected_size}B "
                f"(resuming from {on_disk}B)"
            )
        raise
    if expected_size is not None and got != expected_size:
        raise urllib.error.URLError(
            f"truncated body: expected {expected_size}B, got {got}B"
        )
    return got, start


def download_one(
    url: str,
    dest_part: str,
    expected_size: int | None,
    *,
    log=print,
    sleep=time.sleep,
) -> tuple[bool, str | None]:
    """One download attempt: `url` -> `dest_part`. Digest is the caller's.

    Returns (ok, err) with err in {None, ERR_DOWNLOAD}. The RETRY budget is
    owned by `fetch_entry`, which retries on BOTH a transport error AND a
    completed-but-mismatched (corrupt) download — S1.5 "retry download x3
    (10 s backoff); then fail ... ERR_HASH_MISMATCH". `log`/`sleep` are
    injectable so hermetic tests drive the attempt without a real network.
    """
    try:
        _stream(url, dest_part, expected_size)
        return True, None
    except (urllib.error.URLError, urllib.error.HTTPError, OSError) as exc:
        log(f"    download attempt failed: {exc!r}")
        return False, ERR_DOWNLOAD


def fetch_entry(
    *,
    url: str,
    disk_name: str,
    expected_sha: str | None,
    expected_size: int | None,
    cache_dir: str,
    force: bool = False,
    retries: int = 3,
    backoff: float = 10.0,
    log=print,
    sleep=time.sleep,
) -> tuple[bool, str | None]:
    """Fetch + verify one quant into `cache_dir/disk_name`.

    Idempotent (S6.4): if the target already verifies and `force` is unset,
    it is re-verified and skipped (zero downloads). The download is retried
    up to `retries` times (S1.5 "retry x3 / 10 s backoff") on BOTH a transport
    error AND a completed-but-mismatched (corrupt) download, then fails with
    `err` in {None, ERR_HASH_MISMATCH, ERR_DOWNLOAD}. A stale/corrupt final
    file is reused as a resume base; a verified-but-forced file is re-fetched.
    """
    target = os.path.join(cache_dir, disk_name)
    part = target + ".part"

    if not force:
        ok, _ = verify(target, expected_sha, expected_size)
        if ok:
            log(f"    {disk_name}: present + verified (skip)")
            return True, None

    os.makedirs(cache_dir, exist_ok=True)
    if os.path.exists(target):
        if force:
            # --force = "re-download even if cached + verified": drop the old
            # target and re-fetch from zero. No re-verify needed — force
            # already decided, so we do not pay a full-SHA pass on the
            # (potentially 7 GB) target before removing it (G3c round 1 P1).
            os.remove(target)
        else:
            # Non-force: we reached here because the idempotent check above
            # did NOT pass, so the target is known-unverified (a corrupt or
            # partial file). Reuse its bytes as a resume base for the next
            # download attempt.
            os.replace(target, part)  # corrupt/partial: resume from these bytes

    # S1.5 "retry download x3 (10 s backoff)": 1 initial download + 3 retries
    # = 4 total attempts (the spec is master — G3c round 4 P2@286). `retries`
    # is the retry budget; the total attempt count is retries + 1.
    total_attempts = max(1, retries) + 1
    last_err = ERR_DOWNLOAD
    for attempt in range(1, total_attempts + 1):
        dl_ok, dl_err = download_one(url, part, expected_size, log=log, sleep=sleep)
        if not dl_ok:
            last_err = dl_err or ERR_DOWNLOAD
            # keep .part so a later attempt (or a later run) can resume
            if attempt < total_attempts and backoff > 0:
                log(
                    f"    {disk_name}: backoff {backoff}s, retrying ({attempt}/{total_attempts})"
                )
                sleep(backoff)
            continue
        # Download completed: verify before trusting the bytes.
        ok, verr = verify(part, expected_sha, expected_size)
        if ok:
            os.replace(part, target)
            log(f"    {disk_name}: verified sha256={sha256_file(target)[:12]}...")
            return True, None
        last_err = verr or ERR_HASH_MISMATCH
        # Wrong content after a completed download (S1.5 "corrupt artifact"):
        # keep the .part only if it is a plausible partial (smaller than the
        # pin) so the next attempt resumes it; otherwise clear it so the next
        # attempt re-downloads from scratch (a full-size corrupt file would
        # otherwise keep being "resumed" forever).
        #
        # A full-size mismatch is always re-fetched from byte 0, NOT resumed
        # from the existing prefix: with only a whole-file SHA (HF provides
        # no per-chunk hash) we cannot verify the leading bytes in isolation,
        # so the "valid" prefix might itself be corrupt. Re-downloading from 0
        # is the conservative, convergent choice — at most one extra full
        # re-transfer per corrupt event (G3c round 7 P1@139, waived as
        # design/efficiency note, not a correctness defect).
        part_size = os.path.getsize(part) if os.path.exists(part) else 0
        if expected_size is None or part_size < expected_size:
            log(
                f"    {disk_name}: {last_err} — keeping partial ({part_size}B) for resume"
            )
        else:
            log(
                f"    {disk_name}: {last_err} — clearing {part_size}B, re-download from scratch"
            )
            try:
                os.remove(part)
            except OSError:
                pass
        if attempt < total_attempts and backoff > 0:
            log(
                f"    {disk_name}: backoff {backoff}s, retrying ({attempt}/{total_attempts})"
            )
            sleep(backoff)
    return False, last_err


def load_model_config(config_path: str, want: str = "all") -> dict:
    """Read the S1.6 `model` block; returns {default, fallback} fetch specs.

    Each spec is {url, disk_name, expected_sha, expected_size}. A missing pin
    raises SystemExit(EXIT_CONFIG) with a named message so a bad config is a
    config error, not a silent wrong download. `want` scopes which quant's pin
    must be complete: the default keys are always required; the fallback keys
    are required when `want` is "fallback" or "all" (the common case), and
    only then — a config with a complete default block and no fallback block
    is still valid for `--quant default` (G3c round 2 P2).
    """
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover - PyYAML is a box dep
        print(
            f"ERR_CONFIG: PyYAML required to read {config_path}: {exc}", file=sys.stderr
        )
        raise SystemExit(EXIT_CONFIG)
    with open(config_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    model = data.get("model") or {}
    required = ["repo", "revision", "file", "source_object", "sha256", "size_bytes"]
    if want in ("fallback", "all"):
        required += [
            "fallback_file",
            "fallback_source_object",
            "fallback_sha256",
            "fallback_size_bytes",
        ]
    missing = [k for k in required if not model.get(k)]
    if missing:
        print(
            f"ERR_CONFIG: model block missing {missing} in {config_path}",
            file=sys.stderr,
        )
        raise SystemExit(EXIT_CONFIG)

    def _entry(disk_key, obj_key, sha_key, size_key):
        obj = model.get(obj_key) or model.get(disk_key)
        url = (
            f"https://huggingface.co/{model['repo']}/resolve/{model['revision']}/{obj}"
        )
        try:
            size = int(model[size_key]) if model.get(size_key) is not None else None
        except (TypeError, ValueError):
            size = None
        return {
            "url": url,
            "disk_name": model.get(disk_key),
            "expected_sha": (model.get(sha_key) or "").strip().lower() or None,
            "expected_size": size,
        }

    return {
        "default": _entry("file", "source_object", "sha256", "size_bytes"),
        "fallback": _entry(
            "fallback_file",
            "fallback_source_object",
            "fallback_sha256",
            "fallback_size_bytes",
        ),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Download + verify bonsai2 model weights (MAP 1.3)."
    )
    ap.add_argument(
        "--cache-dir",
        default=DEFAULT_CACHE_DIR,
        help=f"model cache dir (default {DEFAULT_CACHE_DIR})",
    )
    ap.add_argument(
        "--quant",
        choices=("default", "fallback", "all"),
        default="all",
        help="which quant(s) (default: all)",
    )
    ap.add_argument(
        "--force", action="store_true", help="re-download even if cached + verified"
    )
    ap.add_argument(
        "--retries",
        type=int,
        default=3,
        help="download RETRIES after the initial attempt (S1.5 'retry x3': "
        "1 initial + 3 retries = 4 total attempts)",
    )
    ap.add_argument(
        "--backoff", type=float, default=10.0, help="seconds between retries (S1.5: 10)"
    )
    ap.add_argument(
        "--config", default=DEFAULT_CONFIG, help="path to subagent-engine.yaml"
    )
    args = ap.parse_args(argv)

    try:
        cfg = load_model_config(args.config, want=args.quant)
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 - config read failure -> named config error
        print(f"ERR_CONFIG: {exc!r}", file=sys.stderr)
        return EXIT_CONFIG

    wanted = ("default", "fallback") if args.quant == "all" else (args.quant,)
    rc = 0
    for name in wanted:
        e = cfg[name]
        print(f"[fetch] {name}: {e['disk_name']} <- {e['url']}")
        ok, err = fetch_entry(
            url=e["url"],
            disk_name=e["disk_name"],
            expected_sha=e["expected_sha"],
            expected_size=e["expected_size"],
            cache_dir=args.cache_dir,
            force=args.force,
            retries=args.retries,
            backoff=args.backoff,
        )
        if not ok:
            print(f"[fetch] {name}: {err} — exit {EXIT_ERR}", file=sys.stderr)
            rc = EXIT_ERR
    # G3c round 7 P2@380: a single stderr line per failed quant above is easy
    # to miss when `--quant all` fails on more than one; add a final summary
    # so the exit reason is unambiguous when multiple quants fail.
    if rc:
        print(f"[fetch] done with errors (exit {EXIT_ERR})", file=sys.stderr)
    return rc


if __name__ == "__main__":
    sys.exit(main())
