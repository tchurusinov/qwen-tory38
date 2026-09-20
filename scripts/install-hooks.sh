#!/usr/bin/env bash
# install-hooks.sh — enable the repo-local pre-commit hook for this clone.
#
# Sets core.hooksPath=.githooks (no symlinks, survives re-clone),
# chmod +x the hook, and is idempotent (safe to re-run any time).
# Machine rule: per-clone enablement; a fresh clone runs this once.

set -euo pipefail

cd "$(git rev-parse --show-toplevel)"

if [ ! -f .githooks/pre-commit ]; then
  echo "ERROR: .githooks/pre-commit not found (repo layout regression?)" >&2
  exit 1
fi

chmod +x .githooks/pre-commit
git config core.hooksPath .githooks

cur="$(git config core.hooksPath)"
echo "ok: core.hooksPath=$cur"
if [ "$cur" != ".githooks" ]; then
  echo "ERROR: core.hooksPath is $cur, expected .githooks" >&2
  exit 1
fi
