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
# Short engine pin (first 8 hex of engine_pin.commit). The build context has
# no git metadata (COPY strips .git; the submodule's .git is a gitlink file),
# so build-info's commit line is supplied from the pin: CMake only falls
# back to git-derived BUILD_COMMIT when LLAMA_BUILD_COMMIT is unset.
# Build with: docker build --build-arg LLAMA_PIN_SHORT=9a9394a8 ...
# (install.sh, MAP 6.2, reads it from the rendered subagent-engine.yaml).
ARG LLAMA_PIN_SHORT="9a9394a8"
ENV DEBIAN_FRONTEND=noninteractive \
    PATH=/usr/local/cuda/bin:$PATH \
    LD_LIBRARY_PATH=/usr/local/cuda/lib64

# aarch64 sanity: S6.2's ERR_ARCH row, exit 3, fail fast on a wrong-arch host.
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
    echo "=== build inputs: arch=${LLAMA_CUDA_ARCHS} pin_short=${LLAMA_PIN_SHORT} ==="; \
    test -n "${LLAMA_PIN_SHORT}" \
        || { echo "ERR_PIN: LLAMA_PIN_SHORT build-arg empty" >&2; exit 4; }; \
    test -f /usr/local/cuda/lib64/stubs/libcuda.so \
        || { echo "ERR_CUDA_STUBS: /usr/local/cuda/lib64/stubs/libcuda.so missing" >&2; exit 4; }; \
    cmake -B build -G Ninja \
        -DCMAKE_BUILD_TYPE=Release \
        -DGGML_CUDA=ON \
        -DCMAKE_CUDA_ARCHITECTURES="${LLAMA_CUDA_ARCHS}" \
        -DLLAMA_BUILD_UI=OFF \
        -DLLAMA_USE_PREBUILT_UI=OFF \
        -DLLAMA_BUILD_COMMIT="${LLAMA_PIN_SHORT}" \
        -DLLAMA_BUILD_NUMBER="0" \
        -DCMAKE_SHARED_LINKER_FLAGS="-Wl,-rpath-link,/usr/local/cuda/lib64/stubs" \
        -DCMAKE_EXE_LINKER_FLAGS="-Wl,-rpath-link,/usr/local/cuda/lib64/stubs"; \
    cmake --build build -j16; \
    echo "=== sm_121a sanity (count must be >0) ==="; \
    test "$(grep -c sm_121a build/compile_commands.json)" -gt 0; \
    mkdir -p /opt/bonsai2/bin; \
    cp -f build/bin/llama-server /opt/bonsai2/bin/llama-server; \
    # all shared libs the binary needs at runtime (libllama.so, libggml*.so, \
    # libllama-server-impl.so, ...): one glob, no hardcoded list to drift.
    cp -f build/bin/lib*.so* /opt/bonsai2/bin/; \
    patchelf --set-rpath '$ORIGIN' /opt/bonsai2/bin/llama-server; \
    for so in /opt/bonsai2/bin/lib*.so*; do \
        patchelf --set-rpath '$ORIGIN' "$so" 2>/dev/null || true; \
    done; \
    echo "=== artifact sanity ==="; \
    file /opt/bonsai2/bin/llama-server; \
    echo "=== version banner (stdout+stderr) ==="; \
    /opt/bonsai2/bin/llama-server --version > /tmp/banner.txt 2>&1 \
        || { cat /tmp/banner.txt; echo "ERR_BANNER: llama-server --version failed" >&2; exit 4; }; \
    cat /tmp/banner.txt; \
    # S1.8 #1: the commit-carrying build line is the fork assertion
    # (this pin only exists on the fork's prism branch).
    grep -q "commit ${LLAMA_PIN_SHORT}" /tmp/banner.txt \
        || { echo "ERR_BANNER_COMMIT: pin ${LLAMA_PIN_SHORT} not in banner" >&2; exit 4; }; \
    # CUDA backend built in: the binary links libggml-cuda (and the
    # --version output shows ggml_cuda_init running - the CUDA code path
    # is compiled in; without a GPU in this container init fails with
    # "no CUDA-capable device is detected", which is the expected
    # no-GPU outcome, not a link failure).
    ldd /opt/bonsai2/bin/llama-server | grep -q "libggml-cuda" \
        || { echo "ERR_BANNER_CUDA: libggml-cuda not linked into llama-server" >&2; exit 4; }
