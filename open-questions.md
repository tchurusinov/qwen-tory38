---
title: qwen-tory38 — open questions & clarifications
status: open
purpose: >
  Every clarification (C-nn) and research topic (R-nn) needed to lock down
  SPEC.md, in one place, answered in-place. When the SPEC is populated and
  this file's items are all resolved/absorbed, this file is deleted (its
  purpose is temporary). IDs are referenced from SPEC.md sections.
created: 2026-09-19
---

# How to use this file

- **C-nn** = clarification: I need YOU to decide. Write your answer under
  `**Your answer:**` for each item, in place.
- **R-nn** = research topic: I (the agent) investigate, report findings
  here under `**Findings:**`, and propose a resolution; you still confirm.
- Each item ends with the SPEC section(s) it gates. Items can be answered
  in any order; I won't start filling SPEC sections for a gated area until
  its blocking items are closed.
- Facts already verified at 2026-09-19 (from the transcripts + HF model
  card) are inlined as context, not questions:
  - bonsai2 = prism-ml/Ternary-Bonsai-2-27B, derived from Qwen3.8-27B,
    ternary g128 weights, **262K context** (hybrid ~75% linear attention).
  - Two GGUF packings: **PTQ1_0 1.75 bits/w = 5.95 GB**,
    **PQ2_0 2.13 bits/w = 7.21 GB**; claims 98.2% of FP16 avg across 14
    thinking-mode benchmarks (84.78 vs FP16 baseline; coding 89.42, agentic
    tool-calling 74.92).
  - Needs the **prism-ml llama.cpp fork** (custom ternary kernels, CUDA +
    Metal) — upstream llama.cpp does not support the packed formats yet.
  - Measured ~68 tok/s decode on a single 3090-class GPU, ~47 tok/s on M5
    Max laptop; MLX variant exists for Apple Silicon (not our platform).
  - Vision tower ships separately as Q8_0 mmproj (we are text-only agents —
    probably irrelevant, but noted: does our serving need it at all?).
  - Main model: qwen3.8-27b NVFP4 @ sglang :30001, ~70/120 GB, one shared
    engine => main-agent and subagent decodes queue on the same engine.

---

## A. Clarifications — you decide

### Scope & division of labor

**C-01 — What exactly is "the main agent" in this stack?**
Is the main agent (a) Hermes Agent on this box (its model = sglang qwen38,
pi or hermes as the harness CLI), (b) pi specifically, (c) any harness the
user points at the scripts? The scripts are harness-agnostic, but the skill
(doctrine) is written FOR a specific harness — which one? This determines
what "session", "turn", and "context growth" mean concretely in the spec.
Gates: S0, S3, S4.
**Your answer:**

**C-02 — Subagent task shapes: what do you actually want to offload?**
idea.txt says "make main qwen agents more productive". Concretely, which of
these are in scope for v1 (pick/adjust): diff/code review (the
senior-git-1.0 pattern), long-context reading/summarization of repo docs,
bulk extraction/transform jobs (scrape-ETL style), test-writing/
test-running, planning sub-problems, simple mechanical edits, "everything
main finds below its quality bar". And which are OUT: creative
architecture, final merge decisions, anything needing your judgment?
Gates: S0, S4, S5.
**Your answer:**

**C-03 — Instance routing policy default.**
Round-robin vs pin-to-instance vs least-loaded vs "one instance per running
task" (a task keeps its instance for its whole lifetime). Which default do
you want, and should it be overridable per-task via script flag?
Gates: S3.
**Your answer:**

**C-04 — Concurrency model: how many in-flight subagent tasks at once?**
The idea says "how many instances + concurrency per instance, tuned later".
For the spec: fixed at boot (instances × concurrency = slot pool, static)
vs dynamic (scripts may request more, engine refuses on OOM)? And: do
subagent slots and the main model's own in-flight requests share the SGLang
engine's queue (they will, physically — the doctrine should say what that
means operationally, e.g. cap N so main keeps its t/s).
Gates: S1, S3, S4.
**Your answer:**

**C-05 — Packaging form.**
idea.txt wavers: "container or sth". Decide: (a) docker compose +
install.sh (matches this box's ~/prod/containers convention), (b) systemd
units + install.sh (native, no docker layer, matches how sglang qwen runs
today — the qwen spangl server here is not dockerized AFAIK, verify),
(c) docker image that CONTAINS both the bonsai instances AND a new sglang
qwen (replacing the current setup — riskier), (d) docker for bonsai only,
sglang qwen stays as-is on the host. Which?
Gates: S2, S6.
**Your answer:**

**C-06 — sglang pinning strategy for the main model.**
idea.txt: "pin to a specific release or point to up-to-date, I really
don't know". Decide: (a) pin exact sglang version now, upgrades are
deliberate events with mandatory re-bench; (b) track a release channel with
manual promotion + auto re-bench; (c) whatever is currently installed,
untouched unless you ask. Also: do bonsai-side engine versions get the same
treatment? (They're a fork — pinning is probably more important there;
confirm.)
Gates: S6 (also S1 for the fork version).
**Your answer:**

**C-07 — bonsai2 quant: PTQ1_0 (5.95 GB) vs PQ2_0 (7.21 GB) vs both.**
Smaller = more instances + more headroom; PQ2_0 may be faster (wider
slots, friendlier to memory subsystem) and possibly better quality. Do you
want the bench to pick (default: R-03 measures both, doctrine picks the
winner per use case), or do you want to pre-commit to one for v1? Note the
per-instance delta: 5.95 vs 7.21 GB × N instances.
Gates: S1, S5.
**Your answer:**

**C-08 — How many instances in v1, and static vs dynamic.**
VRAM math (rough, to be replaced by R-04): 120 − 70 (qwen) = 50 GB free.
PTQ1_0 @ ~6 GB + KV: plausibly 4-6 instances before KV eats the rest. Do
you want (a) the install to compute N from measured VRAM (formula in S1) —
recommended, matches idea.txt "load at startup ... whatever the remaining
vram allows"; (b) a fixed N you pick now; and static-only for v1 with
dynamic as a later phase?
Gates: S1, S2, S6.
**Your answer:**

**C-09 — Is the project "qwen-tory38" the name, and who's the audience
beyond you?**
idea.txt: "all the other people who have this box would benefit". Does the
spec need to target reusability (generic config, no hard-coded paths,
clean install) or is it a personal tool first with portability as a bonus?
This changes how much effort goes into S6 polish.
Gates: S0, S6.
**Your answer:**

**C-10 — Context-length policy per subagent instance.**
Model supports 262K; agent contexts grow with work (idea.txt explicitly
calls this out). Options: (a) fixed large context (e.g. 32K/64K) for all
instances, sized by R-04 KV math; (b) per-task context budget the main
agent sets via script flag (costs more VRAM per slot, less headroom for
concurrency); (c) adaptive: start small, script grows context and
re-spawns the engine instance if the task outgrows it. Which philosophy?
Gates: S1, S3.
**Your answer:**

**C-11 — What happens to a task when its instance dies mid-flight?**
Options: auto-requeue to another instance (needs the task's full prompt
persisted), fail with a clear exit code and let the main agent re-dispatch
(decider = main agent, matches the "main is smarter" doctrine), or
checkpoint/resume (heavier machinery). Which?
Gates: S3.
**Your answer:**

**C-12 — Doctrine: when does the main agent NOT use subagents?**
Equally important as when to use them. Do you want an explicit
"subagents are NOT for" list in the skill (e.g. tasks under ~X tokens of
expected output, anything where the user's judgment is in the loop,
anything touching git history, the G4 merge decision itself)?
Gates: S4.
**Your answer:**

**C-13 — Bench success rubric: who scores subagent output quality?**
Options: (a) fixed task types with objective checkers (does the test pass,
does the regex match, does the diff apply); (b) main-agent-as-judge on a
rubric (fast, but main grades subagents — acceptable since main is
smarter?); (c) human spot-check sampling. For which task types is (a)
possible and where must we fall back to (b)?
Gates: S5.
**Your answer:**

**C-14 — Does the project own the main sglang endpoint, or treat it as
external?**
i.e. after the one-command install, is :30001 qwen38 assumed present and
untouched (install just adds bonsai instances + scripts), or does the
install bring up/replace the qwen server too (so the whole stack is
self-contained on any GB10 box)? The idea text leans self-contained, but
your current box already has qwen running — confirm the v1 boundary.
Gates: S0, S2, S6.
**Your answer:**

### Process

**C-15 — Spec workflow cadence.**
The spec-driven video's loop: I fill a section -> you answer its OQs -> I
have the isolated reviewer critique it (senior-git-1.0 G3 machinery) ->
we iterate until locked -> next section. Confirm: (a) one section at a time
in the order S0->S6, or do you want to jump around; (b) should each
completed+locked section be its own PR/merge (senior-git-1.0) or one big
spec PR at the end; (c) checkpoint commits per refinement pass like the
video, or commit only the locked result.
Gates: the whole spec process.
**Your answer:**

**C-16 — When does this spec start generating code?**
Definition of "dialed in": all C items answered + all R items confirmed +
reviewer pass clean on each section? Or section-by-section (S0+S1 locked ->
start S1 code, spec the rest in parallel)? This is the go/no-go gate.
Gates: whole project.
**Your answer:**

---

## B. Research topics — I investigate, you confirm

**R-01 — prism-ml artifacts inventory (closes partially C-07, C-08).**
Pull the full HF repo (both packings, the mmproj, the whitepaper, the
demo/setup repo with pinned binaries + serving instructions). Extract:
exact model revisions/hashes, kernel requirements, recommended serving
commands, any GB10/Blackwell notes from prism-ml or the community.
Deliverable: `research/bonsai2-artifacts.md`.
Gates: S1.
**Findings:**

**R-02 — Serving engine for bonsai2 on GB10 (Blackwell sm_121).**
The fork is CUDA + Metal. Verify: (a) does the fork build/work on sm_121
(aarch64 CUDA)? (b) is there an OpenAI-compatible server mode (llama.cpp
`server` binary or an OpenAI shim) so the harness scripts can talk to
bonsai instances with the SAME OpenAI chat API shape as sglang qwen — or do
we need a thin wrapper? (c) any sglang/vllm ternary support that would make
them viable (for the "fastest engine" question in idea.txt — for qwen38,
sglang beat vllm; does anything change for ternary formats, or is
llama.cpp-fork the only real option right now)? (d) what's the decode t/s
to expect on GB10 vs the 68 tok/s measured on 3090 — any data points, or
do we have to measure?
Deliverable: `research/bonsai2-engine-bench.md`.
Gates: S1.
**Findings:**

**R-03 — Bench both packings on this box.**
PTQ1_0 vs PQ2_0: t/s at realistic agent context lengths (4K/16K/32K),
VRAM footprint, and a quick quality probe (same short agentic task, both).
Short, dirty, on-box.
Gates: S1, S5.
**Findings:**

**R-04 — VRAM budget measurement (the load-bearing number).**
Measure on this box: exact KV cache growth per token at the chosen context
per instance, the OOM boundary for N instances × concurrency M, and the
main qwen38 t/s degradation curve as bonsai instances come online (answer
to C-04/C-05 X% baseline, S1/S5 acceptance #5). This is the formula S1/S2
/S6 install logic runs on.
Gates: S1, S2, S5, S6.
**Findings:**

**R-05 — Ternary format quirks at serving time.**
Known failure modes / gotchas of the fork: kernel availability per CUDA
version, CUDA arch flags needed for build, any 120GB-unified-memory
specifics (does the fork assume discrete VRAM? unified GB10 should be fine
but verify), prefill performance (the video noted prefill speed was an
open question for agentic workflows — prefill on huge contexts matters for
our use case more than decode).
Gates: S1, S2.
**Findings:**

**R-06 — How does the existing qwen38 sglang setup actually load here?**
Reverse-engineer the current :30001 setup (service file, env, args, NVFP4
config, max context, keepalive) so the spec can state it precisely and the
install can coexist with it (or replace it per C-14). Facts, not
guesses — read the actual service/config on this box.
Gates: S0, S2, S6.
**Findings:**

**R-07 — Subagent strategy survey (feeds C-02, C-12, doctrine).**
Research: what task shapes work well for a ~98%-quality-ternary model under
a smarter orchestrator, across similar local-agent systems (senior-git-1.0
reviewer pattern, hermes delegation patterns, pi sub-process patterns,
published multi-agent local-LLM experiments). Output: a task-shape table
with risk notes, to be turned into the C-02 answer + S4 allocation table.
Gates: S0, S4.
**Findings:**

**R-08 — Bench scenario matrix draft (feeds C-13, S5).**
From R-07 + idea.txt, draft the full scenario matrix: task type × N
instances × concurrency × context, with per-task scoring method (objective
checker where possible). Include the "swarmiest" scenarios from the idea
text. This becomes `bench.yaml` content after your sign-off.
Gates: S5.
**Findings:**

**R-09 — GB10 install prerequisites + coexistence test.**
Document the exact prerequisites for a one-command install on this box
class (DGXOS/Ubuntu 24.04.4, nvidia kernel, toolkit, docker vs systemd
state) and verify what a second engine stack alongside sglang actually
needs (ports, cgroups, memory accounting on unified memory — does the OS
slice the 120GB between engines? how is that controlled?).
Gates: S2, S6.
**Findings:**

**R-10 — The two other videos: what did they actually prove?**
Digest: (a) 975ILFNTKfk — the bonsai2 numbers, the agentic game test,
where it beat/lost qwen38-fp16, any serving gotchas; (b) jZ9Mdit5TdI — the
qwen38 t/s baselines on the tested hardware, the vLLM settings, what to
carry over as "main agent is the strong one" calibration. These are the
capability-comparison baseline the idea text points to; extract the
usable numbers so S5 control conditions cite real baselines, not vibes.
Deliverable: short digest appended here under each item's Findings.
Gates: S0 (grounding), S5 (control conditions).
**Findings:**

**R-11 — Cost model for the doctrine (feeds S4).**
From R-03/R-08 measurements: for each task shape, expected subagent cost
(tokens × t/s × N) vs main-agent time saved; find the break-even line where
"main does it itself" beats "dispatch to subagent". This is the single
most important doctrine number — the skill must not dispatch tasks that
are cheaper for main to do itself.
Gates: S4, S5.
**Findings:**

---

## C. Parking lot (things that surfaced but may not be OQs)

- mmproj/vision tower: skip for v1? (text agents) — confirm with R-01.
- Multiple GB10 boxes: "treat 1 as a batch of however many" — defer to
  post-v1, confirm.
- The idea's "bench framework that identifies ALL usage scenarios" — the
  scenario matrix is R-08; anything beyond task-type×N×concurrency×context
  (e.g. cross-box, multi-main-agent) is post-v1? Confirm.
- License of the delivered package (if others get it): personal MIT?
  Confirm at S6.
