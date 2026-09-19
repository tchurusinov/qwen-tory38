# ROADMAP

Phases. Each phase ends with a G4-merged PR (senior-git-1.0 gate flow).
Nothing here is code yet — phases 0–2 are research, deliberately.

## Phase 0 — Repo bootstrap (this commit)
- gate.sh / review.sh installed, planning docs in place, gate baseline recorded.

## Phase 1 — Research: bonsai2 on GB10 (writes: SPEC.md + research notes)
- R1: inventory prism-ml artifacts; determine the quant/artifact that runs
      on Blackwell and whether GGUF-as-listed is actually the best fit.
- R2: engine comparison for that artifact on GB10 (sglang vs vllm vs llama.cpp
      server) — t/s at realistic agent context lengths, VRAM footprint.
- R3: VRAM budget math: qwen38-nvfp4 (~70/120 GB) + N × bonsai2 instance
      footprints + headroom; pick instance count + concurrency per instance.
- R4: sglang pinning strategy decision (release pin vs tracked).
- Exit: SPEC.md "open questions" all answered with numbers, not vibes.

## Phase 2 — Serve bonsai2 alongside the running sglang qwen38
- Startup service (systemd or equivalent) that loads main + subagent engine(s)
      independently of the skill.
- Exit: both endpoints up on boot, VRAM within budget, documented.

## Phase 3 — Harness skill + bash scripts
- Subagent control scripts (spawn/steer/collect, instance + concurrency
      flags), patterned on review.sh's isolated-process discipline.
- Skill charting main-vs-subagent work allocation (main is smarter).
- Exit: main agent drives subagent work through scripts alone.

## Phase 4 — Bench framework
- Scenario matrix, t/s + task-completion measurement, results in repo.
- Exit: SPEC acceptance criteria 3 checked.

## Phase 5 — GB10 container packaging
- One-command install on a fresh GB10 box; "1 box" structured as a batch of N.
- Exit: SPEC acceptance criteria 4 checked.
