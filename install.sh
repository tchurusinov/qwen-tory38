#!/usr/bin/env bash
# install.sh — the qwen-tory38 one-command installer (SPEC S6.3, MAP 6.2).
#
# Thin entry point: the S6.3 ordered pipeline lives in
# bin/bonsai2_install.py (S6.6: the runtime is bash + the Python
# installer, no new dependencies). This wrapper picks the interpreter
# (the box python with PyYAML; override with BONSAI2_PYTHON), forwards
# all flags verbatim, and propagates the installer's exit codes:
#
#   0 = full or main-only success (incl. the "unchanged" re-run)
#   4 = live-margin OOM (planning granted N >= 1, the manager's i=0
#       allocation OOM)
#   3 = arch mismatch (ERR_ARCH)
#   2 = config (a non-arch prereq hard-fail, a bad config, or an
#       install step the spec maps to ERR_CONFIG)
#
# Usage (the S6.3 command surface):
#   ./install.sh                      # full install (N computed via S1.4)
#   ./install.sh --instances 1        # phased rollout (S0 / MAP 6.3)
#   ./install.sh --dry-run            # checks + plan, no writes
#   ./install.sh --force-config       # rewrite the four YAMLs anyway
#   ./install.sh --rollback etc/state-<ts>.yaml
#   ./install.sh --refresh-main
#
# The one JSON object on stdout is the stack-ready report (the S3.1
# one-document contract); stderr carries the human log.

set -euo pipefail

# The script can be run from anywhere; the installer is in the repo's
# bin/ next to this file.
REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

# The interpreter for the installer + its helper tools (model fetch,
# image build). The box python with PyYAML is the S6.2 default.
PYTHON="${BONSAI2_PYTHON:-/usr/bin/python3}"

exec "$PYTHON" "$REPO_ROOT/bin/bonsai2_install.py" "$@"
