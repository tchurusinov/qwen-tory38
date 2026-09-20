# bonsai2 fork build image (MAP 1.2, SPEC S1.2).
#
# Source build of the pinned PrismML-Eng/llama.cpp fork (`prism` branch,
# pin in etc/subagent-engine.yaml `engine_pin` + the in-repo submodule at
# third_party/llama.cpp) for aarch64 GB10, CUDA 13.0.
#
# Flags are S1.2 at pin `9a9394a8` (delta 2026-09-20): the fork's ternary
# kernels compile unconditionally at the pin (no LLAMA_BONSAI_* cmake
# toggles); the Bonsai-demo script's generic multi-arch list is replaced
# by this box's sm_121 (no fat binary). MAP 0.3 note: the base image
# ships WITHOUT cmake/ninja, so they are apt'd here (Ubuntu 24.04 repos,
# aarch64 builds; provenance checked at MAP 0.3: cmake 3.28.3, ninja
# 1.11.1 in-container).
#
# Base image digest pin (verified aarch64 manifest entry at MAP 0.3):
#   nvidia/cuda@sha256:5dc1bca23d05bd37b011be68ec470c03b403a5da07ec3a86e41af9470e9d0cc6
# The tag `13.0.2-devel-ubuntu24.04` resolves to exactly that digest in
# the docker hub; install.sh (MAP 6.2) re-verifies at build time and
# rebuilds on any change (the pin + model SHA are the build inputs, S6.1).
#
# Artifact layout: /opt/bonsai2/bin/ carries llama-server + its shared
# libs side by side, RUNPATH patched to $ORIGIN (the Bonsai-demo
# script's proven pattern — patchelf). MAP 1.4 lifts /opt/bonsai2/bin
# verbatim into the runtime image; the S1.3 serving command runs
# /opt/bonsai2/bin/llama-server, which finds its libs next to itself.
#
# Build (aarch64 host; from the repo root):
#   docker build --platform linux/arm64 -f image/Dockerfile \
#       --build-arg LLAMA_PIN_SHORT=9a9394a8 -t bonsai2-server-build:121a .
#
# Verification (S1.2 / S1.8 #1), against the build log + the image:
#   1. sm_121a: `grep -c sm_121a <build-log>` > 0 (the in-build check
#      against compile_commands.json already asserts this).
#   2. Banner: `docker run --rm --entrypoint /opt/bonsai2/bin/llama-server
#      bonsai2-server-build:121a --version` prints
#      "version: 0.2.0-dev (build <N>, commit <engine_pin sha prefix>)";
#      the in-build checks assert the pin prefix is in the banner, the
#      binary links libggml-cuda (CUDA backend, S1.2 GGML_CUDA=ON), and
#      the binary is an aarch64 ELF (file, in-build). This fork has no
#      separate ternary banner line; the commit-carrying build line IS
#      the fork assertion (the pin only exists on the fork's prism
#      branch). --version also runs ggml_cuda_init, which in the GPU-less
#      build container logs "no CUDA-capable device is detected" - the
#      expected no-GPU outcome, not a failure.
#
# This image is the BUILD pipeline. The runtime image `bonsai2-server:<tag>`
# (S1.6 image_pin) is what install.sh bakes in MAP 1.4 from this binary +
# the model files.

FROM nvidia/cuda@sha256:5dc1bca23d05bd37b011be68ec470c03b403a5da07ec3a86e41af9470e9d0cc6
ARG LLAMA_CUDA_ARCHS="121a"
# Build with: docker build --platform linux/arm64 -f image/Dockerfile \
#       -t bonsai2-server-build:121a .
# The engine pin is read from etc/subagent-engine.yaml (S1.6 source of
# truth) — the Dockerfile carries no hardcoded pin default. The optional
# LLAMA_PIN_SHORT build-arg (first 8 hex) cross-checks the YAML pin and
# fails the build on drift (ERR_PIN_DRIFT): install.sh (MAP 6.2) passes
# it from the rendered subagent-engine.yaml so a pin bump that missed
# one place cannot silently build the old commit.
ARG LLAMA_PIN_SHORT=""
# LD_LIBRARY_PATH: build-time scope (nvcc + the link steps). The runtime
# image is not this image — the binaries ship with RUNPATH $ORIGIN and
# the env -i assertions below prove they resolve without it. A debug
# `docker run --entrypoint bash` into this image inherits the env.
ENV DEBIAN_FRONTEND=noninteractive \
    PATH=/usr/local/cuda/bin:$PATH \
    LD_LIBRARY_PATH=/usr/local/cuda/lib64

# aarch64 sanity: S6.2's ERR_ARCH row, exit 3. Checks the build *target*
# arch (uname -m in the container): with --platform linux/arm64 this is
# the native arch on an aarch64 host, the QEMU-emulated target on an
# x86_64 host — either way it fails fast when the target isn't aarch64.
RUN set -eu; \
    UARCH=$(uname -m); \
    if [ "$UARCH" != "aarch64" ]; then \
        echo "ERR_ARCH: build image requires an aarch64 host (got $UARCH)" >&2; \
        exit 3; \
    fi

# MAP 0.3: base image has no cmake/ninja (Ubuntu 24.04 repos, aarch64).
# file + patchelf: in-build artifact sanity + RUNPATH portability.
# libnvidia-compute-580: the CUDA driver library libcuda.so.1 (ggml-cuda
# links the driver API for VMM: cuMemCreate & co). The image only ships
# the toolkit STUB libcuda.so (SONAME libcuda.so.1, but the linker records
# the SONAME and fails the exe link without the real file). Library-only
# package, no kernel/GL deps; pinned to the box's driver series 580
# (provenance: NVIDIA apt repo, machine rule). The box's own driver
# provides the real libcuda.so.1 at runtime; the build only needs it to
# resolve symbols.
RUN apt-get update \
    && apt-get install -y --no-install-recommends cmake ninja-build file patchelf \
        libnvidia-compute-580 \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /src
# Pinned fork source (in-repo submodule: project-wide, never system-wide).
COPY third_party/llama.cpp/ /src/
# S1.6 source of truth for the engine pin: the build reads the commit
# from this template (COPY'd in, not ARG-hardcoded).
COPY etc/subagent-engine.yaml /tmp/engine.yaml

# S1.2 at pin 9a9394a8:
#   - ternary kernels: unconditional at the pin (no LLAMA_BONSAI_* toggles)
#   - arch: this box's sm_121 only (GB10 Blackwell)
#   - UI off: skips the prebuilt-UI fetch from an HF bucket at build time
#     (server works without it; hermetic build, no network dependency)
# ggml-cuda links the CUDA driver API (VMM: cuMemCreate & co) to the
# toolkit's driver STUB /usr/local/cuda/lib64/stubs/libcuda.so; the build
# container has no NVIDIA driver, so the link step needs the stubs dir on
# the -rpath-link search path or the executable link fails with undefined
# cuMem* references. The real libcuda.so.1 is provided by the box's driver
# at runtime (S1.2: driver >= 580).
RUN set -eu; \
    # the engine pin comes from the YAML template (S1.6 source of truth):
    # first 8 hex of engine.pin.commit. The build-arg, when provided,
    # cross-checks it — a pin bump that updated only one place fails
    # loudly instead of silently building the old commit.
    # first 8 hex of engine.pin.commit, anchored after the opening quote
    # (a bare `[0-9a-f]{8}` would match all five 8-hex chunks of the
    # 40-hex SHA and yield a 5-line PIN8).
    PIN8=$(awk '/^engine:/{f=1}/^image:/{f=0}f' /tmp/engine.yaml | grep -oE 'commit: "[0-9a-f]{40}"' | head -1 | grep -oE '^commit: "[0-9a-f]{8}' | grep -oE '[0-9a-f]{8}$'); \
    test -n "${PIN8}" \
        || { echo "ERR_PIN: engine pin commit not found in /tmp/engine.yaml" >&2; exit 4; }; \
    if [ -n "${LLAMA_PIN_SHORT}" ] && [ "${LLAMA_PIN_SHORT}" != "${PIN8}" ]; then \
        echo "ERR_PIN_DRIFT: build-arg ${LLAMA_PIN_SHORT} != YAML pin ${PIN8}" >&2; exit 4; \
    fi; \
    echo "=== build inputs: arch=${LLAMA_CUDA_ARCHS} pin_short=${PIN8}${LLAMA_PIN_SHORT:+ (arg cross-check: ${LLAMA_PIN_SHORT})} ==="; \
    test -f /usr/local/cuda/lib64/stubs/libcuda.so \
        || { echo "ERR_CUDA_STUBS: /usr/local/cuda/lib64/stubs/libcuda.so missing" >&2; exit 4; }; \
    cmake -B build -G Ninja \
        -DCMAKE_BUILD_TYPE=Release \
        -DGGML_CUDA=ON \
        -DCMAKE_CUDA_ARCHITECTURES="${LLAMA_CUDA_ARCHS}" \
        -DLLAMA_BUILD_UI=OFF \
        -DLLAMA_USE_PREBUILT_UI=OFF \
        -DLLAMA_BUILD_COMMIT="${PIN8}" \
        -DLLAMA_BUILD_NUMBER="0" \
        -DCMAKE_SHARED_LINKER_FLAGS="-Wl,-rpath-link,/usr/local/cuda/lib64/stubs" \
        -DCMAKE_EXE_LINKER_FLAGS="-Wl,-rpath-link,/usr/local/cuda/lib64/stubs"; \
    cmake --build build -j"$(nproc | awk '{print ($1>16)?16:$1}')"; \
    echo "=== sm_121a sanity (every CUDA TU must target sm_121a only) ==="; \
    # -j cap: 16 on this 20-core GB10 box (host headroom); nproc-based so
    # a smaller build host (e.g. 8-core) cannot overcommit. \
    # positive: the exact arch token appears (one per CUDA nvcc line).
    test "$(grep -c 'arch=compute_121a' build/compile_commands.json)" -gt 0; \
    # exhaustive: every arch-flagged line is 121a (a fat multi-arch build
    # would carry other arch=compute_1XX tokens; none may remain).
    test "$(grep -o 'arch=compute_[0-9a-z]*' build/compile_commands.json | grep -cv 'arch=compute_121a$')" -eq 0; \
    mkdir -p /opt/bonsai2/bin; \
    cp -f build/bin/llama-server /opt/bonsai2/bin/llama-server; \
    # all shared libs the binary needs at runtime (libllama.so, libggml*.so, \
    # libllama-server-impl.so, ...): one glob, no hardcoded list to drift.
    cp -f build/bin/lib*.so* /opt/bonsai2/bin/; \
    patchelf --set-rpath '$ORIGIN' /opt/bonsai2/bin/llama-server; \
    for so in /opt/bonsai2/bin/lib*.so*; do \
        # only set RUNPATH on real files; a versioned symlink
        # (libggml.so -> .so.0 -> .so.0.21.0) inherits the target's
        # RUNPATH, and patchelf may exit non-zero on a symlink (harmless).
        # A real .so that still can't get RUNPATH set is a mis-resolution
        # the env -i checks below are meant to catch, so that must be loud.
        if [ -L "$so" ]; then continue; fi; \
        patchelf --set-rpath '$ORIGIN' "$so" \
            || { echo "ERR_RPATH: patchelf failed on $so" >&2; exit 5; }; \
    done; \
    echo "=== artifact sanity ==="; \
    file /opt/bonsai2/bin/llama-server; \
    echo "=== version banner (stdout+stderr) ==="; \
    /opt/bonsai2/bin/llama-server --version > /tmp/banner.txt 2>&1 \
        || { cat /tmp/banner.txt; echo "ERR_BANNER: llama-server --version failed" >&2; exit 4; }; \
    cat /tmp/banner.txt; \
    # S1.8 #1: the commit-carrying build line is the fork assertion
    # (this pin only exists on the fork's prism branch). ${PIN8} is the
    # YAML-sourced pin actually baked in; the banner must carry it.
    grep -q "commit ${PIN8}" /tmp/banner.txt \
        || { echo "ERR_BANNER_COMMIT: pin ${PIN8} not in banner" >&2; exit 4; }; \
    # CUDA backend built in: the binary links libggml-cuda (and the
    # --version output shows ggml_cuda_init running - the CUDA code path
    # is compiled in; without a GPU in this container init fails with
    # "no CUDA-capable device is detected", which is the expected
    # no-GPU outcome, not a link failure).
    ldd /opt/bonsai2/bin/llama-server | grep -q "libggml-cuda" \
        || { echo "ERR_BANNER_CUDA: libggml-cuda not linked into llama-server" >&2; exit 4; }; \
    # Transitive-lib resolution without any environment (the runtime
    # container will have no LD_LIBRARY_PATH): every lib in
    # /opt/bonsai2/bin was patchelf'd to RUNPATH $ORIGIN above, so the
    # binary + all nested libs must resolve via $ORIGIN alone. The
    # in-build ldd above can be masked by LD_LIBRARY_PATH; this cannot.
    env -i /opt/bonsai2/bin/llama-server --version > /tmp/banner2.txt 2>&1 \
        || { cat /tmp/banner2.txt; echo "ERR_ENVI: --version failed under env -i" >&2; exit 4; }; \
    grep -q "commit ${PIN8}" /tmp/banner2.txt \
        || { echo "ERR_ENVI_COMMIT: pin ${PIN8} not in env -i banner" >&2; exit 4; }; \
    # the CUDA backend must resolve from /opt/bonsai2/bin (its own dir),
    # not from a system location: unmasked ldd of the server binary.
    env -i ldd /opt/bonsai2/bin/llama-server 2>/dev/null \
        | grep -q '/opt/bonsai2/bin/libggml-cuda' \
        || { echo "ERR_ENVI_CUDA: libggml-cuda not resolved from /opt/bonsai2/bin under env -i" >&2; exit 4; }
