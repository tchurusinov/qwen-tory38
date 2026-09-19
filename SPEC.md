# qwen-tory38 — SPEC

Goal: a robust, reliable main-agent + subagent stack optimized for a GB10 box
(120 GB unified VRAM) running two dense 27B models — qwen3.8-27b (NVFP4,
sglang, existing) as the smart main agent, and Ternary-Bonsai-2-27B as the
subagent — packaged so any GB10/DGX box can install it with one command.

Source of the idea: `idea.txt` (2026-09-19). This SPEC records what that
file settled; everything else is research, not settled.

## Settled requirements

### 1. Inference engine (loads at startup, independent of the skill)
- Main: sglang + qwen3.8-27b NVFP4, already running on this box (keepalive
  endpoint :30001). Keep it as the main-agent model.
- Subagent: prism-ml Ternary-Bonsai-2-27B (HF:
  prism-ml/Ternary-Bonsai-2-27B-gguf — verify exact repo name).
  - Preliminary research required before choosing:
    - which quant/artifact actually runs on GB10/Blackwell (the HF listing is
      GGUF; check if something else is faster/more effective here),
    - which engine is fastest for it on GB10 (for qwen38-nvfp4, sglang beat
      vllm in t/s; for bonsai this is an open question),
    - how to serve it as a server that loads at startup with N instances,
      N = whatever the remaining VRAM allows after the main model (~40 GB
      free by the idea's estimate; tune later),
    - concurrency per instance (qwen38 runs satisfactorily at concurrency
      2–4 loaded once, in the sglang way — apply the same discipline).
- Reference the sglang project for main-model inference (pin to a specific
  release vs track latest: decide, don't leave floating).

### 2. Harness skill (main agent ↔ subagents)
- Universal skill that lets the main agent use its own session plus subagent
  instances **through modifiable bash scripts** — not Hermes internal
  delegation. Previous attempts (lmstudio ggufs + hermes delegation skill)
  did not produce something usable; precision control of concurrency and of
  the different loaded instances was "hellishly impossible" that way.
- The scripts get flags/conditions for using and shepherding subagents in
  all the different ways — across instances, across concurrencies.
- Model the pattern on senior-git-1.0's repo-local `review.sh`: a separate
  isolated pi process on the shared SGLang endpoint, diff + docs + intent in,
  findings out.
- Account for the intelligence gap: MAIN IS DEFINITELY SMARTER. The skill
  must chart how the main agent gets the most done per session — which
  subagent strategies work on these resources; preliminary research required
  on subagent strategies overall and on similar systems.

### 3. Bench framework
- Identify ALL usage scenarios, test each, and report the most efficient /
  fastest / "swarmiest" / best t-s throughput ways the main agent can use the
  subagents on this box.

### 4. Packaging
- A GB10/DGX-specific container (or equivalent) that runs on any GB10 system
  with one command, containing: the inference engine(s) + the skill + the
  bench framework.
- Start with 1 box; "1" should be structured as a batch of however many later.

## Non-goals (explicit, from the idea)
- Not a Hermes-internal-delegation skill for this job.
- Not LM Studio GGUF serving for the subagent (that attempt was retired).
- Not a general multi-box fleet tool yet (1 box first).

## Acceptance criteria
- [ ] Bonsai2 served on GB10 next to the running sglang qwen38, both up at
      startup; VRAM budget documented.
- [ ] Main agent can spawn/steer subagent work via the bash scripts alone,
      across instances and concurrency levels.
- [ ] Bench results: per-scenario t/s + task-completion comparison, in repo.
- [ ] One-command install on a GB10 box produces the working stack.

## Pinned versions / dependencies
No manifest exists yet (research phase). When the engine/skill code lands,
pin every external dependency (sglang version, model revisions, engine
builds) in this file — a gitleaks-style gate will run against the diff.

## Open questions (research tickets in TASKS.md)
1. Bonsai2 artifact + quant for GB10: GGUF as-listed, or something else?
2. Fastest serving engine for bonsai2 on GB10 (sglang vs vllm vs other).
3. Instance count / concurrency split given the VRAM budget.
4. sglang pinning strategy (release pin vs tracked).
5. Subagent strategy taxonomy: what work shapes to give the subagent vs keep
   main.
