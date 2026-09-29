#!/usr/bin/env bash
# gate.sh — repo-local required check for qwen-tory38.
#
# The "required check" that stands in for CI (Gitea Actions runner is not
# enabled on this machine). Run at G2 after implementing and re-run after
# every G3 fix. Only NEW failures block the gate; pre-existing failures
# recorded in the gate baseline do not.
#
# Adapted from senior-git-1.0 templates/gate.sh for this repo (bash serving
# scripts + skill + bench harness; no Python test suite yet):
#   1) bash -n every tracked .sh file
#   2) shellcheck every tracked .sh file — HARD when installed, skipped
#      when absent (gate baseline: "not installed"; the pre-commit tripwire
#      and the gate now agree on severity — G3c round 14 P1)
#   3) ruff format --check + ruff check every tracked .py — HARD when
#      ruff is available (PATH `ruff`, or `uvx ruff` via uv when ruff is
#      not on the bare PATH), skipped when neither is present
#      (G3c round 14 P1: the hook checked .py at all; the gate now does
#      too, so the tripwire and the formal gate run the same lint contract)
#   4) secret scan on the PR diff (gitleaks substitute)
#   5) yaml parse every tracked .yaml/.yml
#   6) S4.6 doctrine audit (bench/doctrine_audit.py check — MAP 5.6; the
#      skill's claims.json must stay in sync with the bench; a failing
#      audit blocks the skill PR)

set -uo pipefail
cd "$(git rev-parse --show-toplevel)"
BASE="${BASE:-main}"
FAIL=0

# 1) syntax-check every tracked bash file
echo "[gate] bash -n tracked .sh files"
mapfile -t SHFILES < <(git ls-files '*.sh' 2>/dev/null)
for f in "${SHFILES[@]}"; do
  [ -f "$f" ] || continue
  if bash -n "$f" 2> /tmp/gate-sh-err.txt; then
    echo "  ok    $f"
  else
    echo "  FAIL  $f"; cat /tmp/gate-sh-err.txt; FAIL=1
  fi
done

# 2) shellcheck — HARD when installed (a failing lint is a gate failure,
#    same as the pre-commit tripwire; skipped = not installed, which is
#    the recorded baseline). G3c round 14 P1: this used to be WARN-only,
#    so the tripwire could block what the formal gate would allow.
if command -v shellcheck >/dev/null 2>&1; then
  echo "[gate] shellcheck tracked .sh files"
  for f in "${SHFILES[@]}"; do
    [ -f "$f" ] || continue
    if shellcheck "$f" > /tmp/gate-shellcheck.txt 2>&1; then
      echo "  ok    $f"
    else
      echo "  FAIL  $f (shellcheck):"; sed 's/^/    /' /tmp/gate-shellcheck.txt; FAIL=1
    fi
  done
else
  echo "[gate] shellcheck not installed — skipping (baseline: soft)"
fi

# 3) ruff — HARD when available. Detection: `ruff` on the bare PATH, or
#    `uvx ruff` (uv present) — on this box ruff is uv-managed and not on
#    the bare PATH, so uvx is the availability axis. Neither present:
#    skipped (baseline: soft). Same contract as the pre-commit tripwire
#    (G3c round 14 P1).
RUFF_CMD=""
if command -v ruff >/dev/null 2>&1; then
  RUFF_CMD="ruff"
elif command -v uvx >/dev/null 2>&1; then
  RUFF_CMD="uvx ruff"
fi
if [ -n "$RUFF_CMD" ]; then
  mapfile -t PYFILES < <(git ls-files '*.py' 2>/dev/null)
  if [ "${#PYFILES[@]}" -gt 0 ]; then
    echo "[gate] ruff tracked .py files"
    if $RUFF_CMD format --check "${PYFILES[@]}" > /tmp/gate-ruff-fmt.txt 2>&1; then
      echo "  ok    ruff format --check"
    else
      echo "  FAIL  ruff format --check:"; sed 's/^/    /' /tmp/gate-ruff-fmt.txt; FAIL=1
    fi
    if $RUFF_CMD check "${PYFILES[@]}" > /tmp/gate-ruff.txt 2>&1; then
      echo "  ok    ruff check"
    else
      echo "  FAIL  ruff check:"; sed 's/^/    /' /tmp/gate-ruff.txt; FAIL=1
    fi
  fi
else
  echo "[gate] ruff not available (no ruff, no uvx) — skipping (baseline: soft)"
fi

# 4) secret scan on the PR diff (heuristic — this machine's gitleaks fallback)
echo "[gate] secret scan (diff)"
DIFF="$(git diff "origin/$BASE...HEAD" 2>/dev/null || git diff HEAD~1 2>/dev/null || true)"
if echo "$DIFF" | grep -nE '^\+' | grep -vE '^\+\+\+' \
   | grep -Eiq '(api[_-]?key|secret|password|token|bearer)\s*[:=]\s*["'\''][A-Za-z0-9]{16,}'; then
  echo "  WARN  possible secret literal in diff:"
  echo "$DIFF" | grep -nE '^\+' | grep -vE '^\+\+\+' \
    | grep -Ei '(api[_-]?key|secret|password|token|bearer)\s*[:=]\s*["'\''][A-Za-z0-9]{16,}' | sed 's/^/    /'
  FAIL=1
fi

# 5) YAML sanity for tracked .yaml/.yml (config files land here over time)
echo "[gate] yaml parse tracked .yaml/.yml"
mapfile -t YAMLFILES < <(git ls-files '*.yaml' '*.yml' 2>/dev/null)
for f in "${YAMLFILES[@]}"; do
  [ -f "$f" ] || continue
  if python3 -c "import yaml,sys; yaml.safe_load(open(sys.argv[1]))" "$f" 2>/tmp/gate-yaml-err.txt; then
    echo "  ok    $f"
  else
    echo "  WARN  $f (yaml parse):"; sed 's/^/    /' /tmp/gate-yaml-err.txt
  fi
done

# 6) S4.6 doctrine audit (MAP 5.6) — the skill's doctrine claims
#    (skills/bonsai2-harness/claims.json: the S4.2 table + recipes + prompt
#    templates) must stay in sync with the bench: every [M#] citation names
#    a bench.yaml scenario, and no subagent-side row has >= 2 consecutive
#    bench losses without a documented win_rule exception. A failing audit
#    blocks the skill PR (S4.6 "a failing audit blocks the skill PR").
#    Runs on the bare box python3 (no PyYAML needed: the module has a
#    stdlib fallback for the template's flow-sequence scenarios line).
echo "[gate] S4.6 doctrine audit (bench/doctrine_audit.py check)"
if python3 bench/doctrine_audit.py check > /tmp/gate-doctrine-audit.txt 2>&1; then
  echo "  ok    $(cat /tmp/gate-doctrine-audit.txt)"
else
  rc=$?
  echo "  FAIL  doctrine audit (exit $rc — S4.6: a failing audit blocks the skill PR):"
  sed 's/^/    /' /tmp/gate-doctrine-audit.txt
  FAIL=1
fi

if [ $FAIL -eq 0 ]; then
  echo "[gate] PASS"
else
  echo "[gate] FAIL"
fi
exit $FAIL
