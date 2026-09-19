# TASKS

Work items. Statuses: open / doing / done. Each ticket = one reviewable unit
for the senior-git-1.0 gate flow.

## Phase 1 — Research
- [ ] T1: Inventory prism-ml/Ternary-Bonsai-2-27B artifacts on HF — formats,
      quant sizes, VRAM estimates. Write to research/bonsai2-artifacts.md.
- [ ] T2: Benchmark candidate serving engines for the chosen bonsai2
      artifact on GB10 (t/s at 8k/32k/128k context, VRAM).
      research/bonsai2-engine-bench.md
- [ ] T3: VRAM budget + instance/concurrency plan.
      research/vram-budget.md
- [ ] T4: sglang pinning decision (pin release vs track). Note in SPEC.md.
- [ ] T5: Subagent strategy research: what work shapes suit a weaker dense
      27B under a smarter main agent; similar-systems survey.
      research/subagent-strategies.md

## Phase 2 — Serving
- [ ] T6: Startup service for main + subagent engine(s).

## Phase 3 — Harness
- [ ] T7: Subagent control bash scripts (spawn/steer/collect).
- [ ] T8: Harness skill (main-agent usage doctrine).

## Phase 4 — Bench
- [ ] T9: Scenario bench framework + results.

## Phase 5 — Packaging
- [ ] T10: GB10 one-command container.

## Bootstrap (done)
- [x] B1: scripts/gate.sh + scripts/review.sh installed, planning docs in
      place, gate baseline recorded (see commit message / G0 record).
