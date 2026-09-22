#!/usr/bin/env bash
# review.sh — ISOLATED, READ-ONLY code review of a PR branch.
#
# The reviewer is pi running the SAME local SGLang model as the builder,
# but in a completely separate context (--no-session, --no-tools). It can
# read the diff and the repo docs, but it CANNOT run code or edit files.
# It PROPOSES findings; it never APPLIES fixes. The builder verifies and fixes.
#
# Usage:
#   ./review.sh <pr-branch> [base] [plan-file] [intent-file]
#
# Env:
#   INTENT_FILE    path to a text file with the PR problem+approach (default /tmp/pr-intent.txt)
#   REVIEW_EXCLUDE colon-separated git pathspec patterns to keep OUT of the
#                  reviewed diff (the files still stay in the PR; they are
#                  just not fed to the reviewer — e.g. a docs-only file)
#
# Output:
#   /tmp/review-<branch>.txt   the reviewer's findings (strict format)
#   /tmp/review-err-<branch>.txt   pi stderr
#
# Exit codes: 0 = ran (read the output for findings), 2 = no diff, 1 = pi failed

set -euo pipefail
cd "$(git rev-parse --show-toplevel)"

BRANCH="${1:?usage: review.sh <pr-branch> [base] [plan-file] [intent-file]}"
BASE="${2:-main}"
PLAN="${3:-.git/plan.md}"
INTENT="${4:-${INTENT_FILE:-/tmp/pr-intent.txt}}"

EXT="$HOME/.pi/agent/sglang-extension.ts"
[ -f "$EXT" ] || { echo "review: pi SGLang extension missing at $EXT" >&2; exit 1; }

if ! git fetch origin "$BASE" "$BRANCH" --quiet 2>/tmp/review-fetch-err.txt; then
  echo "review: git fetch of origin/$BASE and origin/$BRANCH failed:" >&2
  cat /tmp/review-fetch-err.txt >&2
  echo "review: is the branch pushed to origin yet?" >&2
  exit 1
fi
if ! git rev-parse --verify "origin/$BASE" >/dev/null 2>&1; then
  echo "review: origin/$BASE does not exist locally — cannot compute a diff base" >&2
  exit 1
fi
# Optional: REVIEW_EXCLUDE = colon-separated git pathspec patterns to keep
# OUT of the reviewed diff (e.g. REVIEW_EXCLUDE="README.md" for a
# docs-only file that is part of the PR but not the review target).
# Each pattern becomes a ':!<pattern>' exclusion pathspec on the diff
# command (a single unquoted token, so it word-splits into one arg).
# Must be set BEFORE the diff below (set -u: an unset $EXCLUDE_SPECS
# would abort at the word-split).
EXCLUDE_SPECS=""
if [ -n "${REVIEW_EXCLUDE:-}" ]; then
  IFS=':' read -ra _excl <<< "${REVIEW_EXCLUDE}"
  for p in "${_excl[@]}"; do
    [ -n "$p" ] || continue
    EXCLUDE_SPECS="${EXCLUDE_SPECS} :!${p}"
  done
fi
if ! DIFF="$(git diff "origin/$BASE...$BRANCH" $EXCLUDE_SPECS 2>/tmp/review-diff-err.txt)"; then
  echo "review: git diff origin/$BASE...$BRANCH failed:" >&2
  cat /tmp/review-diff-err.txt >&2
  exit 1
fi
if [ -z "$DIFF" ]; then
  echo "review: no diff between origin/$BASE and $BRANCH — nothing to review" >&2
  exit 2
fi

# Sanitize the branch name for filenames: 'feature/foo' would create
# /tmp/review-feature/foo.txt (a path with a missing directory segment) —
# hit e2e on hla-plus PR #2 (2026-09-05).
SAFE_BRANCH="${BRANCH//\//_}"
OUT="/tmp/review-$SAFE_BRANCH.txt"
ERR="/tmp/review-err-$SAFE_BRANCH.txt"

# Gather whatever repo context files exist. Do NOT invent any.
# (list adapted at bootstrap 2026-09-19 to this repo's actual planning docs)
CTX=""
for f in AGENTS.md SPEC.md ROADMAP.md TASKS.md; do
  if [ -f "$f" ]; then
    CTX="${CTX}
===== ${f} =====
$(cat "$f")"
  fi
done

{
  echo "You are an INDEPENDENT code reviewer. You have NO context about how these"
  echo "changes were made and NO memory of any earlier review. You are reviewing a"
  echo "pull request diff on this repository. Be precise and terse. Every finding"
  echo "must cite file:line. Do not propose full rewrites — name the defect and the"
  echo "smallest fix."
  echo "You are running WITHOUT tools: no shell, no file access beyond the diff and"
  echo "context files provided below. Do not call functions or tools, do not emit"
  echo "tool-call markup, do not write code blocks, and do not write a preamble."
  echo "Your entire output is: the findings list, then the SUMMARY line."
  echo
  echo "PR INTENT (problem + approach, from the builder):"
  if [ -f "$INTENT" ]; then cat "$INTENT"; else echo "(none provided)"; fi
  echo
  echo "REPO CONTEXT FILES (authoritative for conventions, acceptance criteria, non-goals):"
  if [ -n "$CTX" ]; then echo "$CTX"; else echo "(no AGENTS/SPEC/ROADMAP/MANIFEST present)"; fi
  echo
  if [ -f "$PLAN" ]; then echo "PLAN:"; cat "$PLAN"; fi
  echo
  echo "FINDINGS TO CHECK:"
  echo "1. Logic errors: wrong conditionals, missing error handling, off-by-one, races, unhandled exceptions"
  echo "2. Security: hardcoded secrets/keys/paths, injection, unsafe deserialization, shell=True, path traversal, sensitive data written to logs"
  echo "3. Contract/spec violations vs the context files above (acceptance criteria, pinned versions, non-goals)"
  echo "4. Robustness: leaked resources (unclosed files/threads/devices), blocking on headless, missing cleanup on exit"
  echo "5. Scope creep: changes not implied by the PR INTENT"
  echo
  echo "OUTPUT FORMAT — strict, one line per finding, most severe first:"
  echo "P0 <file>:<line> - <one-sentence finding> (P0 = blocks merge)"
  echo "P1 <file>:<line> - <one-sentence finding> (P1 = fix before merge)"
  echo "P2 <file>:<line> - <one-sentence finding> (P2 = note for later)"
  echo "If NOTHING actionable, print exactly this line on its own: REVIEW CLEAN"
  echo "Then ALWAYS end with exactly one line:"
  echo "SUMMARY: <n> P0, <n> P1, <n> P2"
  echo
  echo "DIFF (origin/$BASE...$BRANCH):"
  echo "$DIFF"
} > /tmp/review-prompt-full.txt

# Run the isolated reviewer. `set +e` must be in the CALLER's shell right
# before the pipeline (not inside a `{ }` block): under `set -euo pipefail`
# a non-zero pipeline would otherwise abort the script before `rc=$?` runs.
set +e
cat /tmp/review-prompt-full.txt \
  | timeout 600 pi -e "$EXT" -p --no-tools --no-session --thinking off \
      --model 'sglang/qwen3.8-27b' \
      --system-prompt "You review code. Be precise and terse. Every finding cites file:line. Flag the defect; do not dump whole-file rewrites." > "$OUT" 2> "$ERR"
rc=$?
set -e
if [ $rc -ne 0 ]; then
  echo "review: pi exited $rc — see $ERR" >&2
  tail -5 "$ERR" >&2 || true
  exit 1
fi
# Guard against pi's empty-output failure mode: a thinking-only response
# produces zero text blocks (print mode drops thinking). A valid review
# always ends with SUMMARY: or says REVIEW CLEAN.
if ! grep -qE '^(SUMMARY:|REVIEW CLEAN)' "$OUT"; then
  echo "review: reviewer output lacks SUMMARY/REVIEW CLEAN marker — thinking-only response, rerun" >&2
  cat "$OUT" >&2 || true
  echo "review: last lines of $ERR:" >&2
  tail -5 "$ERR" >&2 || true
  exit 3
fi

echo "=== REVIEW OUTPUT ($OUT) ==="
cat "$OUT"
