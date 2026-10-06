# SPEC.md (retired 2026-10-06)

The design spec has been **retired, not deleted**: it now lives at
`archive/SPEC.md` (git-mv — full history preserved).

Why: the implementation is stable enough that the spec was describing
reality rather than guiding it, and its 2,010 lines + 49 delta notes were
adding confusion instead of direction. The working documents until the
spec is resurrected are:

- `CONSOLIDATED-REVIEW-FIXES.md` — the fix list driving the current
  integration branch (from the two 2026-10-05 frontier-model reviews),
- `skills/bonsai2-harness/SKILL-v2.md` — the operator-facing doctrine,
- `MAP.md` — the ledger of what is done / what is left.

Conventions while retired:

- **Spec amendments still happen, but they land in the PR body** under
  `## Spec amendments` (AGENTS.md spec-amendment rule: what was
  expected, what was real, before/after for any locked value) instead of
  editing `archive/SPEC.md`.
- **Resurrection** is one step: `git mv archive/SPEC.md SPEC.md` + a
  consistency pass against the v2 skill and the fixes file (the archive
  is not expected to be byte-current after this branch merges).

Locked values that matter day-to-day (VRAM budget, per-instance
footprint formula, closed exit-code set) are also stated in the code
(`bin/bonsai2_vram.py`, `bin/bonsai2_harness.py`) and the v2 skill —
those are the live sources of truth, and they match the archive as of
2026-10-06.
