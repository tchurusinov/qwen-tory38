# qwen-tory38 — AGENTS.md

Guidance for AI coding agents working in this repo. Machine-level rules:
`/home/blckwll/prod/AGENTS.md` (wins on conflicts outside this directory).

## What this project is

A two-model agent stack for GB10-class boxes: a **main agent** on
qwen3.8-27b NVFP4 served by the pre-existing SGLang stack at :30001, plus
**subagents** on Ternary-Bonsai-2-27B (GGUF, prism-ml llama.cpp fork)
served as N instances in the remaining VRAM. The main agent drives
subagents only through deterministic bash control scripts + a harness
skill. Everything installs by one command (`install.sh`).

**Doc map:** `SPEC.md` = master design (locked; if code and spec
disagree, fix the spec and record the delta). `MAP.md` = the
implementation ledger — resume protocol, phased checkboxes, session log;
the first unchecked item is the frontier. `ARTIFACTS.md` = where every
external thing lives (weights, binaries, images, units, tools) — keep it
current, full trackability. `README.md` = the public-facing overview
(skeleton as of 2026-09-22; sections marked `PENDING` fill in as MAP
phases land). `idea.txt` = original intent (history, not requirements).

## The target box (what we are building for)

Single GB10 system, any form factor (1TB-4 ASUS/MSI etc.): Blackwell
arch, ~120 GB unified LPDDR5X, aarch64. Design at the architecture level,
never the SKU. VRAM budget (this box, 121.6 GiB — recompute at install):

| component | footprint |
|---|---|
| main stack (qwen3.8-27b NVFP4, SGLang, fresh load) | ~76.4 GB |
| OS + other containers reserved | 8.0 GB |
| subagent pool | ~37.2 GB → N = floor(37.2 / 11.5) = 3 default |

The main agent eats 75–80/120 GB in the preferred configuration; the
35–40 GB remainder is what this project exists to use. Per-instance
budget is 11.5 GB (weights + KV + 5 % margin, SPEC S1.4).

**MVP safety rule (standing):** write the code with the full project in
mind — full capability, N instances, all paths — but **load, test, and
orchestrate with exactly 1 subagent instance** (`install.sh
--instances 1`, SPEC S0) until the end-to-end MVP is proven. The 1 is a
load-time condition, not a code limit: no `if N == 1` hacks, no disabled
code paths. After MVP e2e, load up to N via re-run (MAP P6/P7).

## Workflow: senior-git-1.0, PR-driven

All code changes follow `senior-git-1.0` end to end (G0–G4):

- **G0** orient: read this file, `SPEC.md`, `MAP.md` (newest session log
  entry first), probe live state.
- **G1** plan: phase-sized work gets a plan; record gate self-pass for
  small changes.
- **G2** implement one reviewable unit, then `./scripts/gate.sh`.
- **G3** commit/push/open Gitea PR → `./scripts/review.sh` (isolated pi
  reviewer on :30001) → raw-output comment, then disposition comment,
  loop until clean. One reviewer, builder applies fixes.
- **G4** human merge gate, always. Merge via `git-merge-push-sync`.

Repo state: Gitea remote `blckwell/qwen-tory38` (ssh `gitea-local:2222`),
`scripts/gate.sh` + `scripts/review.sh` installed (bootstrap done).
**Gate baseline (2026-09-20, untouched repo): PASS, exit 0** — bash -n
ok on both scripts, shellcheck soft-skipped (not installed), secret scan
clean. Only NEW failures block gates. PR body must record exact checks
run + skipped + residual risk (the full PR-body contract: the
"PR-body contract" section below).

One SGLang engine on this box: the builder's own model, the pi reviewer,
and any subagent experiments share it or its VRAM — reviews run
sequentially, and see the VRAM audit rule below.

## Spec-amendment rule (spec follows code, expected — not a failure)

SPEC.md is written by the agent and implemented by the agent — so it is
**expected** that a spec section stops working against the real world at
some point: a flag that doesn't exist at the pin, a measurement that
under-predicts, a path that is infeasible. When that happens:

- Find the working approach, implement it, **amend SPEC.md in the same
  change** (the document conventions already require it: "implementation
  findings that contradict this spec get backported HERE, never the
  other way around"). This rule just names the norm explicitly:
  spec-break-then-repair is a *normal part of the flow*, not a
  one-shot-failure.
- Do it by the design itself: complex work is broken into small,
  independently-testable chunks, each with robust tests; the chunk that
  breaks gets the spec amendment, the rest stay untouched. Never let a
  broken chunk accumulate silently.
- The amendment records **what was expected, what was real, and what
  the spec now says** (a short delta note at the amended site — the
  same pattern as the S1.3/S1.4 pin-reality and re-anchor notes in
  SPEC.md).
- Record the delta in the MAP session log too (spec deltas field).
- If the amendment changes a **locked** value (an acceptance number, a
  formula constant, a default), say so explicitly in the PR body under
  `## Spec amendments` with before/after — the reviewer and the human
  merge gate must see spec moves the way they see code moves.

## VRAM audit rule (before ANY agent/model loads)

Before spawning, loading, or running anything that touches the GPU —
G3b reviewer, a subagent instance, a bench run, a `@gpu` test, a bench of
the main endpoint — audit resources **at this moment**:

```bash
nvidia-smi --query-gpu=memory.used,memory.total,utilization.gpu --format=csv,noheader
free -h | sed -n '1,2p'
docker stats --no-stream --format 'table {{.Name}}\t{{.MemUsage}}'
```

Decision: free pool = 121.6 GiB − main-stack live usage (the main
container may be asleep via sleep_on_idle — read its cgroup, don't assume
76.4) − other containers − 8.0 reserved. A load needs
`instance_footprint + 1.0 GB` margin (SPEC S1.4 allocation tier).
Short on margin → don't load, don't start the agent; note it in the MAP
session log. And: **no uncommitted changes on the box you are about to
stress** — commit or stash first, so a crash never eats work.

## Uncertainty rule (implement → test → report; don't stall)

When a design fork comes up during implementation:

- **Implement, don't stall.** The loop is: implement the option(s) →
  test them → report in the PR. It is better to produce working options
  with the information that led to them than to stop and not do anything
  while burning compute. Where a choice is cheap, implement the top 2
  possibilities behind a config flag or a clearly-marked branch
  (`UNRESOLVED: <id>` comment at the site), with a test per option, so
  the user decides against working code + observed results, not
  descriptions.
- **Report the PR section in this exact order** — a fixed-shape
  `## Uncertainties & decisions` section (supersedes the old
  `## Open decisions (needs user)`; that header is gone):
  1. **Uncertainties** — what was unknown, one line each.
  2. **Possible decisions** — the options considered, and the
     information that led to each (measurement, pin behavior, doc
     finding, cost).
  3. **Decisions taken** — which option was implemented and why
     (default = the recommended one; "take" is the top pick, not a
     coin flip).
  4. **Results** — what the tests/measurements on the taken option
     returned (numbers, exit codes, observed behavior).
  5. **Further options** — what remains open, what would change the
     decision, and what the next step is.
- Never silently pick a 3rd option not named under "possible
  decisions". Never leave an `UNRESOLVED` marker past G4 without a
  disposition (taken / merged / dropped, and which path won) —
  disposition lands in the same PR section, in a follow-up entry.

## PR-body contract (written when the PR opens — DOD, amendments, uncertainties)

Every PR body carries these sections, written **when the PR opens**, not
retrofitted at merge time:

- **`## Definition of Done (acceptance for THIS PR)`** — the concrete,
  checkable conditions that, when all true, mean this PR is *done and
  no more review rounds are needed* — i.e. it can go straight to the
  human merge gate (G4). Scope it to the task at hand, in the scope of
  this PR only: name the spec acceptance items it claims (S1.8 #3,
  S3.8 #5 …), the tests that prove them (suite + case names), the
  observed exit codes / numbers, and what is explicitly NOT claimed
  (left to a later PR / phase). The reviewer uses this section to know
  when to stop finding nits: the DOD met = the loop is done. If a
  review round surfaces work outside this DOD, it is split into a new
  PR, not silently absorbed. No DOD = the PR is not openable.
- **`## Spec amendments`** (when SPEC.md changed in this PR) — per the
  spec-amendment rule above: what was expected vs real, before/after
  for any locked value, the MAP session-log delta entry.
- **`## Uncertainties & decisions`** (when a fork was hit) — the
  fixed 5-subsection shape from the uncertainty rule (uncertainties →
  possible decisions → decisions taken → results → further options).
- **Checks + residual risk** — exact gate/`review.sh` checks run, any
  soft-skipped ones, and what that leaves open (the gate-baseline
  paragraph in the Workflow section above).

The G3 reviewer loop ends when the DOD section is fully met and the
reviewer's disposition says so; G4 (human merge) then reviews against
the DOD, not against open-ended "is this good enough".

## Code quality + pre-commit

Full formatting, security, and quality practices apply. Enforced
automatically by a git pre-commit hook (Phase 0 installs it):

- Hook lives in `.githooks/pre-commit`; enabled per-clone via
  `scripts/install-hooks.sh` (sets `core.hooksPath=.githooks`,
  idempotent). No symlinks, survives re-clone.
- Pre-commit runs, on staged changes: `bash -n` + shellcheck (if
  installed) on `.sh`; `ruff format --check` + `ruff check` (if
  installed) on `.py`; secret scan of the staged diff (same logic as
  gate.sh); trailing-whitespace / CRLF / no files > 50 MB; then the full
  `./scripts/gate.sh`.
- `git commit --no-verify` is allowed only with a one-line reason in the
  PR body. The G2 gate is still the formal check — pre-commit is the
  tripwire, not the gate.
- Tooling installs (aarch64-safe): shellcheck from the dgx/spark apt
  repos (verify provenance first, machine rule), ruff via uvx or in a
  project venv (ruff has aarch64 wheels). Until installed, gate.sh
  treats them as soft (skip, don't fail) — the baseline above says so.

## llama.cpp fork + external deps (project-wide, not system-wide)

- Engine: prism-ml llama.cpp **fork**, branch `prism`, pinned commit
  recorded in `subagent-engine.yaml` at install (SPEC S1.2). Upstream
  llama.cpp has no ternary Bonsai kernels — the fork is the v1 path.
  **Merge status (checked 2026-09-20):** 1-bit Q1_0 is merged upstream;
  generic ternary Q2_0 (g64 packing) runs on mainline; **Ternary Bonsai
  2's PQ2_0/PTQ1_0 packing still needs the fork's `prism` branch** (a
  stock build refuses the files). Re-verify at first build (MAP 1.1):
  `git ls-remote https://github.com/PrismML-Eng/llama.cpp prism` +
  attempt a stock-build load; if merged, the pin still points at a
  fork commit until the S5 quick bench is re-run on upstream.
- Upstream is update-happy: **never track upstream main.** The pin
  moves only via a PR + S5 quick bench (SPEC S5.5).
- Install scope: the fork source lives **in the repo** at
  `third_party/llama.cpp` (git submodule pinned at the fork commit,
  `UNRESOLVED: fork-vendoring` — alternative: plain clone + pin file in
  `ARTIFACTS.md`; decide at MAP 1.1). Nothing is installed system-wide
  (`/usr/local` stays clean). The runtime binary is baked into the
  `bonsai2-server` docker image (SPEC S6.1); host-side only
  `/opt/bonsai2/{bin,etc,cache}` is created, all of it documented in
  `ARTIFACTS.md`.
- Model weights on this box:
  `/home/blckwll/.lmstudio/models/prism-ai/Ternary-Bonsai-2-27B-PTQ1_0-GGUF/`
  — `PTQ1_0` (5.95 GB, the SPEC fallback quant) + `mmproj-Q8_0`
  (0.63 GB, NOT loaded in v1 — text-only, non-goal). The SPEC default
  quant is `PQ2_0` (7.21 GB); install.sh downloads it to
  `/opt/bonsai2/cache` with SHA verification (SPEC S1.1). Don't load
  mmproj, don't move the LM Studio copy.
- Anything that must live outside the repo (systemd units, docker images,
  host tools) **must be listed in `ARTIFACTS.md`** with path, pin, and
  the MAP item that created it. No orphaned artifacts.

## After MVP e2e: CI/CD proposal (do not build before then)

Once the MVP runs end to end, propose — don't enable — additional git
CI/CD, tuned to what actually hurt during the run:

- Candidates, in order of likely value: post-commit hook (run
  gate.sh + the hermetic test subset), the pytest suite for the Python
  bench runner (SPEC S5.8), a Gitea Actions workflow (self-hosted, free
  — runner is off on this Gitea 1.27.1; enabling one runner is the
  cost), and wiring the senior-git reviewer into the PR as a required
  status.
- Gitea reality check (verify, don't assume): Gitea self-hosts Issues /
  PRs / labels / Actions / mirror — the paid cloud adds scale +
  support, not features we need; this box runs its own tracker.
- Rule: every enabled check must earn its place from an observed
  failure point in the senior-git e2e run — no default-on everything,
  no jibberish the reviewer has to read past. Goal: fastest possible
  runtimes for the most qualitative outputs; the review loop (G3) is
  the expensive part, so CI investment should cut review rounds, not add
  ceremony.
- Track the proposal + decisions as a MAP item (P7 area), not inline
  tooling sprawl.

## Machine rules (this box — inherited)

- aarch64 only: every new dependency needs an aarch64 build (machine
  rule + SPEC S6.6).
- No bare `sudo apt update && upgrade` (custom NVIDIA kernel); verify
  package provenance (dgx/spark repos).
- Python: venv inside the project dir, never `--break-system-packages`.
- One GPU, one heavy thing at a time: `@gpu` tests never run parallel
  with a live session or a review (SPEC global testing discipline).
- Main stack at :30001 is external infrastructure: consume, never
  modify; a dead :30001 is exit 82 territory (SPEC S3.2), not ours to fix.

## Out of scope (SPEC, locked)

LLM-to-LLM subagent orchestration · main-stack changes · vision/mmproj ·
multi-box v1 · fine-tuning · LLM-judge bench mode · kill-for-drift ·
stateful subagents.

## POST-IMPLEMENTATION (reminder — do this AFTER the corresponding work lands)

Standing reminder section: tasks below are deliberately NOT in the
senior-git G2 path because their payoff only becomes real and
measurable once the work they depend on has shipped. When the listed
trigger happens, pick the item up (as its own PR with its own DOD, not
slipped into an unrelated one).

- **senior-git-1.0 & senior-git-2 from the e2e runs (in the works).**
  Trigger: the qwen-tory38 MVP e2e run finishes (global acceptance #1–#5
  pass, MAP P7). Work up both flows from what the run actually hit
  (failure points, review rounds, slow spots) — the post-MVP CI/CD
  proposal in this file is their input, not their output. senior-git-2
  is the next revision; don't design it before the e2e evidence exists.
- **Uniformity of approaches + cleanup of autoskills and memories.**
  Trigger: after the `bonsai2-harness` subagent skill lands (MAP P5) —
  that skill adds enough new context that the current sprawl is visible.
  Problem: directions currently arrive from ~15 different places
  (system prompt, per-project AGENTS.md, skills, memories, cron,
  machine docs). Some system tells you to do X and another Y; they
  stack and somehow work, at the cost of a lot of context + tokens.
  Deliverable: (a) a single **authoritative on/off + direction source**
  for skills — the way authoritative inference / authoritative skill /
  authoritative capabilities already exist, one place decides what is
  active; (b) convergence of the ~15 direction points into fewer, with
  the hard rules that actually matter surviving verbatim and the rest
  demoted or deleted.
- **Universal docs log/ledger + auto-curation of .md docs.**
  Trigger: with the above — once one authoritative direction source
  exists, docs become a medium, not a source. Build the universal
  ledger (one index of which doc is authoritative for what, when,
  why) + an auto-curation mechanism that prunes/consolidates .md files
  against it. Rule for the curation: only hard rules that actually
  matter survive as text; everything else is re-derived, linked, or
  deleted.
