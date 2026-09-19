# AGENTS.md — qwen-tory38

Repo rules for any agent working here.

## What this repo is
The qwen-tory38 project: main-agent (qwen3.8-27b NVFP4 via sglang, existing on
this box at :30001) + subagent (Ternary-Bonsai-2-27B) stack for GB10 boxes,
delivered as inference engine(s) + bash-script harness skill + bench framework,
packaged as a one-command install. See SPEC.md for settled requirements.

## Workflow
- senior-git-1.0 gated flow: G0 orient → G1 plan → G2 implement +
  `./scripts/gate.sh` → G3 PR + `./scripts/review.sh` isolated review +
  reconcile → G4 human merge gate.
- Gate baseline recorded in the bootstrap commit; only NEW gate failures
  block.
- The isolated reviewer (scripts/review.sh) reads AGENTS.md, SPEC.md,
  ROADMAP.md, TASKS.md as context — keep them true or the review is blind.

## Machine facts (this box)
- GB10, 120 GB unified VRAM. sglang qwen38-nvfp4 up at :30001; one SGLang
  engine → reviews/subagent work run sequentially, never two at once.
- Gitea at :3001 (SSH: gitea-local :2222). PRs are the merge gate.
- ARM64 only: any new dependency needs an aarch64 build.

## Layout
- idea.txt — original idea, kept verbatim (do not rewrite; SPEC.md is the
  working translation of it).
- SPEC.md / ROADMAP.md / TASKS.md — planning docs, updated at G4 after merges.
- scripts/gate.sh — the required check (run at G2 and after every G3 fix).
- scripts/review.sh — isolated read-only reviewer (G3b).
- research/ — phase-1 research notes (one file per task, see TASKS.md).
