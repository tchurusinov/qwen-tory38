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
**Your answer:**welp what i meant when saying main agent is how its setup and used and exposed in this repo: /home/blckwll/prod/dgx-spark-qwen38 via sglang a qwen38 in nvfp4 format that i use as main agent. i can use it within a hermes harness, within pi or within a separate project where we need a llm exposed adn working. its a full suite tahta handles all from installing to serving to system prompt to starting to load at system startup via a daemon if im not mistaken. what i had in mind since we already habe this for the main agent, since its specific to what is the most optimised to run on the gb10, to come up and create a similar service but for the most optimised manner of running the bonsai2 subagent

**C-02 — Subagent task shapes: what do you actually want to offload?**
idea.txt says "make main qwen agents more productive". Concretely, which of
these are in scope for v1 (pick/adjust): diff/code review (the
senior-git-1.0 pattern), long-context reading/summarization of repo docs,
bulk extraction/transform jobs (scrape-ETL style), test-writing/
test-running, planning sub-problems, simple mechanical edits, "everything
main finds below its quality bar". And which are OUT: creative
architecture, final merge decisions, anything needing your judgment?
Gates: S0, S4, S5.
**Your answer:** well i want the main agent to be able to delegate and offload to subagents tasks and if its a research tasks to depend on their outputs or if its a coding tasks to delegate teh already defined task for the subagent to perform - in short a standard subagent workflow , where the main agent uses subagents whenever benefitial in order to preserve main agent's context and allow it to achieve more in the same session delegating the grunt and repetitive work to subagents or spawn them out to scaffold based on predefined while its only checking and verifying - like i mentioned - whatever will allow the main agent to be more productive and achieve more in those same sessions as opposed to the main agent doign all the tasks in the session : from investigation to planning to actual implementations 

**C-03 — Instance routing policy default.**
Round-robin vs pin-to-instance vs least-loaded vs "one instance per running
task" (a task keeps its instance for its whole lifetime). Which default do
you want, and should it be overridable per-task via script flag?
Gates: S3.
**Your answer:**should be overwritable, i am not sure which strategy tho, whichever will be most efficient based on first how do we load and run a subagent instance , how many can we cram , what is their performance in concurrency 1 as opposed to greater than 1 
**Agent note (2026-09-19):** Spec implements the routing decision as a
config + per-task flag with the benchmark deciding the default:
`routing: pinned|least-loaded|round-robin` in `harness.yaml`,
`--route=<value>` on the spawn script overrides per task. Default set by
the R-08 concurrency sweep (S1/S5): for this stack, **pinned instance per
task** is the expected v1 default — each instance's prompt/KV prefix
cache stays warm for the task's follow-up calls (tool loops), which
llama-server's per-instance prefix cache rewards; least-loaded is the
fallback for short one-shot tasks. The bench proves which, then the
number is backported into the spec (the method's backport rule).

**C-04 — Concurrency model: how many in-flight subagent tasks at once?**
The idea says "how many instances + concurrency per instance, tuned later".
For the spec: fixed at boot (instances × concurrency = slot pool, static)
vs dynamic (scripts may request more, engine refuses on OOM)? And: do
subagent slots and the main model's own in-flight requests share the SGLang
engine's queue (they will, physically — the doctrine should say what that
means operationally, e.g. cap N so main keeps its t/s).
Gates: S1, S3, S4.
**Your answer:** number of instances of bonsai2 will be fixed at boot once we habe the code to load and run 1 instance we will test how many more can we load while still having resources in the system for system things (2-3 gbs left for system at least, otherwise will be unusable and ill have to decide whether to use browser or have agent+subagents) + the kv and context all the loaded models will require - in the sglang project this is clearly explained and configured, the model loads at certain vram and then there is a forecast how much the kv and context can grow and what is the max ceiling past which it cannot grow more

**C-05 — Packaging form.**
idea.txt wavers: "container or sth". Decide: (a) docker compose +
install.sh (matches this box's ~/prod/containers convention), (b) systemd
units + install.sh (native, no docker layer, matches how sglang qwen runs
today — the qwen spangl server here is not dockerized AFAIK, verify),
(c) docker image that CONTAINS both the bonsai instances AND a new sglang
qwen (replacing the current setup — riskier), (d) docker for bonsai only,
sglang qwen stays as-is on the host. Which?
Gates: S2, S6.
**Your answer:** i am confused , is there not a container for sglang? then
what is this when i docker ps: "3b30d023441f   qwen38-dflash2:v1.2.3
"/opt/nvidia/nvidia_…"   7 hours ago    Up 7 hours qwen38-sglang" - i am
pretty sure there is a systemd service but then what is this container .
i want the same config as is in the sglang repo
**Agent note (R-06/R-09 answered this, 2026-09-19):** Both, by design of
the dgx-spark-qwen38 repo: `qwen38-sglang.service` (systemd) is the
SUPERVISOR and it does `docker run --rm ... qwen38-dflash2:v1.2.3` as its
ExecStart — so `docker ps` shows the qwen38-sglang container while
`systemctl status qwen38-sglang` shows the service. The container IS the
process the unit manages; when the unit stops, the container stops
(SuccessExitStatus 137/143 encodes exactly that). "Same config as the
sglang repo" = the bonsai subagent service mirrors this shape: install.sh
writes systemd unit(s) + an image/binary + weights, supervised with
Restart=always, cgroup memory caps, After=qwen38-keepalive.service, with
its own bench.sh-style scripts. SPEC S2/S6 are written to that.

**C-06 — sglang pinning strategy for the main model.**
idea.txt: "pin to a specific release or point to up-to-date, I really
don't know". Decide: (a) pin exact sglang version now, upgrades are
deliberate events with mandatory re-bench; (b) track a release channel with
manual promotion + auto re-bench; (c) whatever is currently installed,
untouched unless you ask. Also: do bonsai-side engine versions get the same
treatment? (They're a fork — pinning is probably more important there;
confirm.)
Gates: S6 (also S1 for the fork version).
**Your answer:** no i did not mean pin sglang to a specific version, rather pin the sglang project to a specific version since i like how its working as of rn but what will happen say they update it and sth breaks upstream in my application. now that im thinking of it, lets have it as follows - track the sglang project for qwen to the latest release and incase uits been upgraded and sth breaks habe a fallback pointing to the sglang qwen project to the specific version we are at right now. 

**C-07 — bonsai2 quant: PTQ1_0 (5.95 GB) vs PQ2_0 (7.21 GB) vs both.**
Smaller = more instances + more headroom; PQ2_0 may be faster (wider
slots, friendlier to memory subsystem) and possibly better quality. Do you
want the bench to pick (default: R-03 measures both, doctrine picks the
winner per use case), or do you want to pre-commit to one for v1? Note the
per-instance delta: 5.95 vs 7.21 GB × N instances.
Gates: S1, S5.
**Your answer:**hmm , this will depend on what is the most optimal way to run this bonsai2 subagent model - is the quantization standartized and the same for all architectures or are there versions as is in larger models - aka we have ggufs and we have the nvfp4 format that is waaay faster and optimised on this machine compared t the ggufs? if there are diff quants ,  i want the most optimal , if its ggufs for all , then the smaller gguf
**Agent note (R-01/R-02 settled this, 2026-09-19):** bonsai2 is NOT a
dense model that got quantized — it is a TERNARY model (weights ∈
{-1,0,+1}, ~1.7-2.1 bits/weight by construction), and it ships ONLY as two
GGUF packings of that ternary format: PTQ1_0 (1.75 bpw, 5.95 GB) and
PQ2_0 (2.13 bpw, 7.21 GB). There is no NVFP4/F8 variant — the
NVFP4-vs-GGUF question from idea.txt does not apply here; the "quant
choice" IS the packing choice. The HF README throughput table + the GB10
community bench say: **PQ2_0 is the faster decode on Blackwell cards and
faster at prompt processing everywhere**; PTQ1_0 only wins on
Ada-generation/L4-class parts or when memory is the binding constraint.
So "most optimal on this machine" = **PQ2_0**, which is also what every
GB10 benchmark used. v1 spec pins PQ2_0; PTQ1_0 stays a config option
(the C-08 instance experiment may want the 1.26 GB/instance saving).

**C-08 — How many instances in v1, and static vs dynamic.**
VRAM math (rough, to be replaced by R-04): 120 − 70 (qwen) = 50 GB free.
PTQ1_0 @ ~6 GB + KV: plausibly 4-6 instances before KV eats the rest. Do
you want (a) the install to compute N from measured VRAM (formula in S1) —
recommended, matches idea.txt "load at startup ... whatever the remaining
vram allows"; (b) a fixed N you pick now; and static-only for v1 with
dynamic as a later phase?
Gates: S1, S2, S6.
**Your answer:** we will start with one loaded and if we can load one, we can load more - at this point we will make a special experiment what is the optimal number of instances we can cram into the remaining vram
**Agent note (R-04 supplies the experiment design, 2026-09-19):** The
"special experiment" is spec'd as S2's load experiment: boot with N=1,
verify health + t/s, then load instances one at a time (each its own
systemd unit / cgroup cap) until MemAvailable < system_tail (2-3 GiB,
your C-04 floor) + OS_reserve, recording per-instance t/s and the qwen
main-endpoint t/s at each step. The result pins N_default for this box
class into S1/S6 config (recomputed at install on any GB10 via the same
formula — C-09's "no hardcoded paths" requirement). v1 ships N=1 as the
safe minimum; the experiment output raises it.

**C-09 — Is the project "qwen-tory38" the name, and who's the audience
beyond you?**
idea.txt: "all the other people who have this box would benefit". Does the
spec need to target reusability (generic config, no hard-coded paths,
clean install) or is it a personal tool first with portability as a bonus?
This changes how much effort goes into S6 polish.
Gates: S0, S6.
**Your answer:**"generic config, no hard-coded paths,clean install" - yes . this is a personal tool but i want it to be proffessionally made with clean install on the gb10 box with no hardcoded paths so that i can eventually share it with the world and all the other people with the exact same machine that will benefit from installing and using if this is a tool that will bring me value - it will other ppl with the same machibne as well. just as the sglang qwen project was made specifically for people with gb10 and hence i found it and started using it and is bringing me immense value , i want ot do the same  

**C-10 — Context-length policy per subagent instance.**
Model supports 262K; agent contexts grow with work (idea.txt explicitly
calls this out). Options: (a) fixed large context (e.g. 32K/64K) for all
instances, sized by R-04 KV math; (b) per-task context budget the main
agent sets via script flag (costs more VRAM per slot, less headroom for
concurrency); (c) adaptive: start small, script grows context and
re-spawns the engine instance if the task outgrows it. Which philosophy?
Gates: S1, S3.
**Your answer:**hmm, lets have the orchestration scripts be able to set this up - main agent will decide what context to set as in code review script, but have them loaded by default at max ctx, adn have this as a setting we cna change with as is in sglang qwen repo 

**C-11 — What happens to a task when its instance dies mid-flight?**
Options: auto-requeue to another instance (needs the task's full prompt
persisted), fail with a clear exit code and let the main agent re-dispatch
(decider = main agent, matches the "main is smarter" doctrine), or
checkpoint/resume (heavier machinery). Which?
Gates: S3.
**Your answer:**fail with a clear exit 

**C-12 — Doctrine: when does the main agent NOT use subagents?**
Equally important as when to use them. Do you want an explicit
"subagents are NOT for" list in the skill (e.g. tasks under ~X tokens of
expected output, anything where the user's judgment is in the loop,
anything touching git history, the G4 merge decision itself)?
Gates: S4.
**Your answer:**sure, but this will be further tuned and adjusted once we get to this component of the project

**C-13 — Bench success rubric: who scores subagent output quality?**
Options: (a) fixed task types with objective checkers (does the test pass,
does the regex match, does the diff apply); (b) main-agent-as-judge on a
rubric (fast, but main grades subagents — acceptable since main is
smarter?); (c) human spot-check sampling. For which task types is (a)
possible and where must we fall back to (b)?
Gates: S5.
**Your answer:**hmm, how is it in sglang qwen repo - i think it tests primarily token outputs for different tasks and in different scenarios - lets first have that and in terms of quality, this will become apparent once we start using the subagents based on the fact if the tasks are successfull or not 

**C-14 — Does the project own the main sglang endpoint, or treat it as
external?**
i.e. after the one-command install, is :30001 qwen38 assumed present and
untouched (install just adds bonsai instances + scripts), or does the
install bring up/replace the qwen server too (so the whole stack is
self-contained on any GB10 box)? The idea text leans self-contained, but
your current box already has qwen running — confirm the v1 boundary.
Gates: S0, S2, S6.
**Your answer:** hmmm, interesting, maybe initially assumed present and develop from there and when we get to containerizing it, we will have to build a subservice referencing the sglang project so that the install or build command will first setup the sglang qwen repo main agent , then do the smae for the inference of the subagents we are yet to build and then all teh rest in terms of skill and benchmarking etc

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
**Your answer:** so the process as  i see it will be taht based on my answers here and research findings u will have done, u write the spec, then open a pr and request a code review for the spec, then adress findings and iterate until we have dialed in the spec (i will decide that) at which point we will merge the pr and have a solid spec based on which we will begin implementations 

**C-16 — When does this spec start generating code?**
Definition of "dialed in": all C items answered + all R items confirmed +
reviewer pass clean on each section? Or section-by-section (S0+S1 locked ->
start S1 code, spec the rest in parallel)? This is the go/no-go gate.
Gates: whole project.
**Your answer:**spec to be clear and detailed describing the vision i have for this project, then i will decide and merge the pr - meaning the spec is done 

---

## B. Research topics — I investigate, you confirm

**R-01 — prism-ml artifacts inventory (closes partially C-07, C-08).**
Pull the full HF repo (both packings, the mmproj, the whitepaper, the
demo/setup repo with pinned binaries + serving instructions). Extract:
exact model revisions/hashes, kernel requirements, recommended serving
commands, any GB10/Blackwell notes from prism-ml or the community.
Deliverable: `research/bonsai2-artifacts.md`.
Gates: S1.
**Findings:** (researched 2026-09-19, HF API + README + cloned PrismML-Eng/Bonsai-demo)
- Repo files: PTQ1_0 5.95 GB, PQ2_0 7.21 GB, + mmproj Q8_0 0.63 GB (optional,
  text-only serving skips it), + F16 reference 54 GB. License Apache-2.0.
- Source of truth for running: **PrismML-Eng/Bonsai-demo** (pinned binaries,
  serving, bench scripts; "where anything disagrees, it is right"). Kernels:
  **PrismML-Eng/llama.cpp fork** (branch `prism`; pinned release
  `prism-b10709-9a9394a`, build 10660+).
- Pinned demo release as of research: `prism-b10709-9a9394a`. Whitepaper:
  Bonsai-demo/bonsai-2-27b-whitepaper.pdf.
- Sampling params (base-model defaults, carried in GGUF metadata):
  thinking temp=1.0 top_p=0.95 top_k=20; reasoning effort `xhigh` default
  (`medium` = faster; `low` NOT supported — behaves like xhigh).
- **GB10 community benchmark exists in the demo repo**
  (community-benchmarks/ternary-bonsai/cuda-gb10-27b-linux.md): same box
  class, CUDA 13.0.88, driver 580.173.02, aarch64, fork built for
  **CUDA arch `121a`** (exactly our GB10 sm_121). Numbers: PQ2_0 tg128
  **29.16 t/s**, pp512 **~1005 t/s**; DSpark drafter raises 512-token code
  gen 28.6 -> **70 t/s (2.45x)**, math 2.62x, chat 1.97x.
- Our box (verified): driver 580.159.03, CUDA 13.0 (V13.0.88), GB10
  compute 12.1, 121.6 GiB unified — matches the benchmark env.
- Prebuilt CUDA release binaries are **x64-only** — on GB10 we build the
  fork from source (`build_cuda_linux.sh`), which is documented and
  supported (aarch64 builds work; the GB10 bench was a source build).
**Proposed resolution:** artifact = PQ2_0 (Blackwell-favorable packing, see
C-07/R-02), pinned demo release as of spec date; install builds fork
source with archs incl. `121a`. No mmproj for v1 (text agents).

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
**Findings:** (2026-09-19)
- (a) sm_121: **verified working**. The demo repo's GB10 community bench
  (same box, 121.6 GiB, aarch64, CUDA 13.0.88, driver 580.173.02) built the
  fork for CUDA arch **`121a`** and ran clean. Our box: driver 580.159.03,
  CUDA 13.0 (V13.0.88), GB10 CC 12.1 — within the same envelope. Build path:
  `./scripts/build_cuda_linux.sh` (source, aarch64 supported; prebuilt CUDA
  binaries are x64-only, so GB10 builds from source).
- (b) OpenAI-compatible server: **yes, native**. `llama-server` with
  `--jinja` exposes `/v1/chat/completions` with the standard OpenAI `tools`
  array and returns structured `tool_calls` (verified in TOOLS.md with a
  full round-trip). Sampling defaults ride the GGUF metadata. So the harness
  scripts can talk to bonsai instances with the SAME OpenAI chat shape as
  sglang qwen — no wrapper needed. Server also streams `reasoning_content`
  and accepts `--reasoning-budget N`.
- (c) sglang/vllm ternary support: **none yet** — the ternary g128 kernels
  live only in the PrismML-Eng/llama.cpp fork (CUDA + Metal); stock llama.cpp
  rejects PTQ1_0/PQ2_0 (or loads Q2_0 and emits garbage). So for now the
  llama.cpp fork is the only real serving option for bonsai2. (The
  sglang-beats-vllm observation for qwen38 does not transfer — that was an
  NVFP4 story; ternary is a different format.)
- (d) GB10 decode: **29.16 t/s** (PQ2_0 tg128, community bench, this box
  class). Prefill pp512 ~1005 t/s. This is a hard floor to beat, not a
  target — R-03 measures it on-box with our exact pinned build.
- **Speculative decoding (DSpark drafter)** is available and workload-
  dependent: +2.45x on code, +2.62x math, +1.97x chat on GB10 (acceptance
  48-74%). It forces `-np 1` (single slot) and re-prefills each request —
  so it trades concurrency for single-stream speed. Decision needed: default
  OFF for the multi-instance/concurrency use case (v1), exposed as a flag.
**Proposed resolution:** engine = PrismML-Eng/llama.cpp fork (branch `prism`,
pinned release), source-built for `121a`, served via `llama-server --jinja`
(OpenAI shape). DSpark optional, off by default.

**R-03 — Bench both packings on this box.**
PTQ1_0 vs PQ2_0: t/s at realistic agent context lengths (4K/16K/32K),
VRAM footprint, and a quick quality probe (same short agentic task, both).
Short, dirty, on-box.
Deliverable: `research/bonsai2-engine-bench.md` (contd).
Gates: S1, S5.
**Findings:** (measurement — on-box, run at S1 implementation, not at spec
time; reference numbers below so the plan is concrete)
- Reference (GB10 community bench, same box class): PQ2_0 tg128 29.16 t/s;
  PTQ1_0 is the slower packing on Blackwell per the HF README throughput
  table (Blackwell favors PQ2_0 decode; PQ2_0 wins prefill everywhere).
- To measure: both packings, t/s at 4K/16K/32K context, VRAM via
  /proc/meminfo MemAvailable delta while the instance is resident (unified
  memory — there is no discrete VRAM counter; nvidia-smi reports N/A),
  and a short agentic task for a quality smoke probe.
**Proposed resolution:** PQ2_0 is the v1 packing (Blackwell decode + faster
prefill + the measured bench packing). PTQ1_0 kept as a config option for
the memory-tightest multi-instance scenarios; final call comes from the
S1 bench row.

**R-04 — VRAM budget measurement (the load-bearing number).**
Measure on this box: exact KV cache growth per token at the chosen context
per instance, the OOM boundary for N instances × concurrency M, and the
main qwen38 t/s degradation curve as bonsai instances come online (answer
to C-04/C-05 X% baseline, S1/S5 acceptance #5). This is the formula S1/S2
/S6 install logic runs on.
Gates: S1, S2, S5, S6.
**Findings:** (2026-09-19 — formula inputs now known; final numbers at S1)
- Unified memory: total 121.6 GiB (/proc/meminfo MemTotal 127535152 kB).
  Measured at research time: MemAvailable 41.2 GiB WITH the qwen stack up.
- The qwen container is capped: `--memory 100g --memory-swap 100g`
  (systemd unit; cgroup accounting). So the bonsai instances' budget is:
  ~121.6 GiB − ~100 GiB qwen cgroup headroom is NOT the right read — the
  qwen process actually RESIDES at ~55-70 GiB (NVFP4 weights ~21-27 GB +
  DFlash draft + KV pool sized by --mem-fraction-static 0.50). The cgroup
  CAP is 100 GiB; the practical free pool is measured by MemAvailable
  while qwen is idle (≈41 GiB observed) minus OS/other-service overhead
  (this box also runs gitea/searxng/open-webui/etc, ~2-4 GiB).
- Per-instance footprint (HF README peak-memory methodology,
  weights + KV + activations + ~1.2 GiB overhead): PQ2_0 weights 7.21 GB;
  KV cache FP16 ≈ **64 KiB/token** (hybrid-attention backbone — cheap for
  this model class): 16K ctx ≈ 1.0 GB, 32K ≈ 2.0 GB, 100K ≈ 6.3 GB.
  So PQ2_0 @ 32K ctx ≈ ~10.4 GiB per resident instance.
- **Working budget formula (to be validated by the S1 OOM-boundary test):**
  N_max = floor((MemAvailable_idle − OS_reserve − system_tail(2-3 GiB,
  user's requirement)) / per_instance_footprint(at chosen ctx)).
  With 41 GiB idle-observed and 10.4 GiB @32K instances: N ≈ 3 (with
  system tail) to 4. At 16K ctx (~9.2 GiB/instance): N ≈ 3-4.
- Qwen degradation measurement: run the qwen bench battery (bench.sh
  single-stream) with 0, 1, 2, 3 idle bonsai instances resident; record
  the t/s delta. This defines the acceptance #5 X% number.
**Proposed resolution:** install computes N at boot from live
MemAvailable via the formula above (C-08 "experiment" = this measurement
at S1/S2, results pinned in SPEC S1 + comp-perf doc); system tail
floor 2-3 GiB per user's C-04 answer; N static for v1.

**R-05 — Ternary format quirks at serving time.**
Known failure modes / gotchas of the fork: kernel availability per CUDA
version, CUDA arch flags needed for build, any 120GB-unified-memory
specifics (does the fork assume discrete VRAM? unified GB10 should be fine
but verify), prefill performance (the video noted prefill speed was an
open question for agentic workflows — prefill on huge contexts matters for
our use case more than decode).
Gates: S1, S2.
**Findings:** (2026-09-19)
- Build arch flags: `build_cuda_linux.sh` defaults to `80;86;89;90;100;120a`
  on CUDA 13; GB10 needs `121a` (the community GB10 bench built with
  arch `121a`). Install must pass `--archs "121a"` (or add it to the list).
- Prebuilt CUDA release assets are x64-only → GB10 source-builds (verified
  path; ~10-15 min on GB10 with -j$(nproc) — estimate, confirm at S1).
- Unified-memory specifics: the fork is a plain CUDA build; it does NOT
  assume discrete VRAM sizing — `-ngl 99` offloads all layers to the GPU
  and KV lives in the unified pool. The GB10 community bench ran
  `-ngl 99 -fa on` clean on unified LPDDR5X. Nothing special needed;
  monitor via /proc/meminfo (nvidia-smi memory counters report N/A on GB10).
- Prefill: pp512 ≈ 1005 t/s on GB10 (community bench) — prefill is NOT the
  bottleneck for this model on this box (unlike M5 Max where prefill was
  the practical limit). Long-context agentic prefill (100K+) should be
  re-checked at S1, but the hybrid linear-attention backbone keeps it
  tractable.
- Gotchas from the demo docs: (1) stock llama.cpp loads Q2_0 and produces
  GARBAGE silently — always use the fork binary; (2) sampling defaults are
  in the GGUF (thinking temp 1.0); `low` reasoning effort is unsupported;
  (3) DSpark forces single slot `-np 1` + re-prefill per request;
  (4) KV4 (`BONSAI_KV4=1`) cuts KV to ~18 KiB/token for very long contexts
  (decode slightly slower) — relevant if a task outgrows default ctx;
  (5) `BONSAI_CTX` auto-mode is RAM-tiered (8K..131K); pass explicit
  numbers to control it.
**Proposed resolution:** pin fork release, source-build for 121a, -ngl 99
-fa on; KV4 off by default (config flag); prefill re-verified at S1 bench.

**R-06 — How does the existing qwen38 sglang setup actually load here?**
Reverse-engineer the current :30001 setup (service file, env, args, NVFP4
config, max context, keepalive) so the spec can state it precisely and the
install can coexist with it (or replace it per C-14). Facts, not
guesses — read the actual service/config on this box.
Gates: S0, S2, S6.
**Findings:** (2026-09-19, read from live box: /etc/systemd/system + docker inspect)
- The main agent stack is the **dgx-spark-qwen38 repo** (~/prod/dgx-spark-qwen38):
  full suite — install.sh (idempotent, re-derives target/context/port),
  bench.sh, concurrent-bench.sh, switch-model.sh, uninstall.sh, docs.
- **qwen38-sglang.service** (systemd, user blckwll): docker-run --rm with
  `--gpus all --memory 100g --memory-swap 100g --shm-size 16g --network
  host --ipc=host`, image `qwen38-dflash2:v1.2.3`, mounting the HF cache
  (root-owned dirs handled in-container) + ~/.config/qwen38 as /out.
  Flags (abridged): abliterated-NVFP4 target pinned by --revision,
  `--mem-fraction-static 0.50`, flashinfer, chunked-prefill 8192, DFlash
  speculative draft (maurienne-ai NVFP4 RTNcal, 16 draft tokens, modelopt_fp4),
  `--max-running-requests 8`, `--sleep-on-idle`, reasoning-parser qwen3,
  tool-call-parser qwen3_coder, api-key from ~/.config/qwen38/api-key,
  host 0.0.0.0 **port 30000**.
- **qwen38-keepalive.service** (systemd): keepalive-proxy.py (v6.x)
  fronting :30000 → clients (hermes, pi) use **:30001**. The proxy adds
  abort-on-client-vanish, dead-engine reporting, pool-cache invalidation.
- Client baseline (from qwen38-dgx-serving-ops skill, 2026-09-17 idle-box
  bench, this box): qwen38 single-stream greedy median ~37 t/s with the
  RTNcal D16 drafter; C=2 20.6, C=8 per-stream 11.3 (aggregate 90.4 t/s,
  knee at C=8 = max-running-requests). This is the "main agent speed"
  baseline the subagent stack must not crater (acceptance #5).
- Startup: both units WantedBy=multi-user.target; engine boots in ~5-7 min
  (CUDA graph capture); Restart=always, RestartSec=15, SuccessExitStatus
  137/143 (clean docker stops).
- Ports in use: 30000 (engine), 30001 (keepalive), 3000/8080 open-webui,
  8081 searxng, 3001 gitea. Subagent ports must avoid all of these
  (proposed: 30100-N range).
**Proposed resolution:** SPEC S0 states this stack as the "main agent
service" (assumed present per C-14), with the unit flags as the record;
coexistence contract: subagent units start after qwen38-keepalive is
healthy, never touch :30000/:30001, ports from 30100 up.

**R-07 — Subagent strategy survey (feeds C-02, C-12, doctrine).**
Research: what task shapes work well for a ~98%-quality-ternary model under
a smarter orchestrator, across similar local-agent systems (senior-git-1.0
reviewer pattern, hermes delegation patterns, pi sub-process patterns,
published multi-agent local-LLM experiments). Output: a task-shape table
with risk notes, to be turned into the C-02 answer + S4 allocation table.
Gates: S0, S4.
**Findings:** (2026-09-19 — synthesis of this machine's own proven patterns
+ the capability data; no new web research needed, the local evidence is
stronger)
- Proven local pattern #1 (senior-git-1.0, verified e2e on de-hex-er/hla+d):
  **isolated read-only reviewer** on the same model family, diff+docs+intent
  in, P0/P1/P2 findings out; builder verifies (probe before classifying),
  fixes on branch. Real P0s were caught (evdev table, SPC/TAB drop); ~2
  false positives/round is normal and is exactly what the verify step eats.
  Generalizes to: "subagent proposes, main verifies" for any bounded
  artifact (diff, extracted data, test suite, draft doc).
- Proven local pattern #2 (webscraping-suite ETL): bulk mechanical
  extract/transform with provenance metadata — pure grunt work, no judgment,
  main just checks counts + spot-samples. Maps to C-02 "bulk extraction/
  transform jobs".
- Proven local pattern #3 (idea.txt itself + this repo's flow): research
  offload — subagent returns a findings digest; main depends on it for
  decisions (this very session is that pattern: R-01..R-06 as delegated
  research with main consolidating).
- Capability anchor (from the bonsai2 numbers): 98.2% of FP16 avg; coding
  89.42 (level with baseline), math 96.57 (within 0.5), agentic tool
  calling 74.92 (vs 76.74 — 2.4 pts), knowledge/reasoning 79.86 (vs 85.55
  — the weakest area, 5.7 pts), vision 66.19 (n/a, text agents). Implication:
  **coding + math + tool-calling + bulk extraction = safe offloads;
  knowledge-heavy synthesis and long-reasoning planning = keep main, or
  subagent-draft + main-refine.**
- Task-shape allocation table (v1 draft, feeds S4; user tunes per C-12):
  | shape | dispatch | why |
  | diff/code review (bounded) | subagent propose + main verify | senior-git-1.0 proven; coding near-parity |
  | coding a well-specified ticket | subagent implements, main verifies + gates | coding near-parity; main keeps judgment at G4 |
  | bulk extract/transform (scrape/ETL) | subagent fully | mechanical, checker-verified (tests pass, counts match) |
  | research findings digest | subagent returns digest, main synthesizes | C-02 "research tasks depend on their outputs" |
  | test-writing for given spec | subagent writes, main runs the suite | mechanical + objective checker |
  | long-context repo reading/summary | subagent returns compressed digest | context preservation is the whole point (C-02) |
  | architecture decisions / planning | MAIN | 5.7-pt knowledge/reasoning gap; user judgment in loop |
  | merge decisions (G4) / anything user-facing | MAIN | judgment + authority stay with main+user |
  | tiny tasks (<~2k output tokens) | MAIN (no dispatch overhead) | cost model R-11 |
**Proposed resolution:** the allocation table above becomes S4's doctrine
body, marked "v1 draft — tuned when the S4 component is specced" per
C-12. All dispatch is verify-gated (subagent output = self-report, never
trusted unverified — senior-git-1.0's hard rule).

**R-08 — Bench scenario matrix draft (feeds C-13, S5).**
From R-07 + idea.txt, draft the full scenario matrix: task type × N
instances × concurrency × context, with per-task scoring method (objective
checker where possible). Include the "swarmiest" scenarios from the idea
text. This becomes `bench.yaml` content after your sign-off.
Gates: S5.
**Findings:** (2026-09-19 — matrix drafted; scoring per C-13 "t/s first,
quality becomes apparent from task success")
- Axes: task type × N instances × concurrency-per-instance × context tier.
- Task types (each with an OBJECTIVE checker first, per C-13):
  1. code-review of a seeded bug-set (checker: bug found / not-found /
     false-positive count) — mirrors senior-git-1.0
  2. coding a fixed ticket (checker: the ticket's test suite passes)
  3. bulk extraction from a fixed URL set (checker: item counts + field
     accuracy vs gold) — webscraping-suite style
  4. research digest (checker: rubric of must-contain facts, automated
     grep of the source transcript)
  5. long-context reading (fixed 16K-32K doc, needle facts) (checker:
     recall of planted facts)
  6. test-writing for a fixed spec (checker: suite runs + catches seeded
     bugs)
- Scenario ladder (v1): N=1 baseline; N=2,3,4 with C=1; then N=1 C=2..4
  (per-instance concurrency sweep — answers C-03 empirically); context
  tiers 8K/16K/32K. "Swarmiest" = highest N × C that stays above the
  qwen-degradation floor (R-04 acceptance #5).
- Per-run metrics: t/s (decode), prefill t/s, task wall-time, task success
  (binary, checker), per-stream vs aggregate (the qwen38 concurrent-bench
  convention: barrier start, C distinct prompts at even spacing, compare
  per-stream AVERAGE across concurrency levels, never medians).
- Control conditions: (a) main-only qwen38 baseline per task type
  (capability comparison the idea wants); (b) idle-box requirement (qwen38
  concurrent-bench lesson: contaminated boxes skew 8-30%); (c) recorded
  config block per run (pinned fork release, packing, ctx, N, C, box state).
**Proposed resolution:** this matrix = S5 bench.yaml content; objective
checkers for all 6 types exist or are trivial to write; quality = task
success rate (C-13), no judge-model needed for v1.

**R-09 — GB10 install prerequisites + coexistence test.**
Document the exact prerequisites for a one-command install on this box
class (DGXOS/Ubuntu 24.04.4, nvidia kernel, toolkit, docker vs systemd
state) and verify what a second engine stack alongside sglang actually
needs (ports, cgroups, memory accounting on unified memory — does the OS
slice the 120GB between engines? how is that controlled?).
Gates: S2, S6.
**Findings:** (2026-09-19, read from live box)
- Box class (verified): DGXOS/Ubuntu 24.04.4 LTS, kernel 6.17.0-1021-nvidia
  (custom NVIDIA kernel), aarch64, 121.6 GiB unified LPDDR5X, GB10 CC 12.1,
  driver 580.159.03, CUDA toolkit 13.0 (V13.0.88). 20 CPU (10 X925 / 10
  A725). This IS the reference box the community GB10 benches were cut on.
- Docker present and used by the qwen stack (qwen38-sglang runs as a
  docker-run from systemd; other boxes' services: gitea, searxng,
  open-webui, understory, senior-architect all in docker). So "container or
  sth" — the box already runs both systemd-managed docker-run services AND
  bare systemd. The cleanest coexistence: mirror the qwen pattern exactly
  (systemd unit doing a docker-run, cgroup-capped), OR native systemd
  (no docker) since the bonsai fork is a single static-ish binary set +
  weights. Either works; native avoids the docker layer and the 100g cap
  bookkeeping. Decision belongs in C-05/S6.
- Memory accounting on unified memory: NO discrete VRAM. The OS manages the
  single 121.6 GiB pool; the qwen container is cgroup-capped at 100g
  (--memory-swap 100g pins swap=memory so it's a hard cap). Bonsai
  instances (native or docker) contend for the SAME pool. There is no
  per-engine slice unless we add one; control is (a) cgroup caps per
  instance if docker, (b) the install-time N formula from R-04 + a runtime
  MemAvailable floor guard. "How is it controlled" = cgroup memory caps
  (docker) + the boot-time N sizing + a health-check that re-checks
  MemAvailable and can shed instances if a threshold is crossed.
- Ports: reserve 30100-3010N for bonsai instances (avoid 30000/30001/3000/
  8080/8081/3001/30090/30091/11000/11434 all confirmed in use).
- One-command install prerequisites: cmake, ninja-build, a C++ toolchain
  (build-essential), CUDA toolkit (nvcc, present), git, curl. All present
  on this box class; install.sh should preflight and report missing.
**Proposed resolution:** install mirrors the qwen stack's service style
(systemd-managed, cgroup-aware, docker optional); memory controlled by
cgroup caps + boot-time N + runtime MemAvailable floor; ports 30100+.

**R-10 — The two other videos: what did they actually prove?**
Digest: (a) 975ILFNTKfk — the bonsai2 numbers, the agentic game test,
where it beat/lost qwen38-fp16, any serving gotchas; (b) jZ9Mdit5TdI — the
qwen38 t/s baselines on the tested hardware, the vLLM settings, what to
carry over as "main agent is the strong one" calibration. These are the
capability-comparison baseline the idea text points to; extract the
usable numbers so S5 control conditions cite real baselines, not vibes.
Deliverable: short digest appended here under each item's Findings.
Gates: S0 (grounding), S5 (control conditions).
**Findings:** (2026-09-19, digested from docs/transcripts/)
- (a) 975ILFNTKfk "Is Bonsai 2 27B REALLY 98% of Qwen 3.8 27B fp16?" —
  Digital Spaceport. The bonsai2 model tested is the SAME family (Ternary
  Bonsai 2 27B). Their measured decode ~68-70 tok/s was on a DIFFERENT box
  (not GB10 — they use llama.cpp on their setup, single 3090-class GPU,
  and run the model via the prism fork since upstream llama.cpp lacks
  ternary support). They used Hermes Agent to generate a full arcade game
  suite and compared vs full fp16 Qwen 3.8 27B — the headline is that
  bonsai2 is close but not identical; the 98% claim holds on aggregate
  benchmarks but task-by-task there are gaps. Usable for us: (1) confirms
  the prism llama.cpp fork is the serving path, (2) confirms agentic/tool
  calling works end-to-end through Hermes with the bonsai model, (3) the
  68 tok/s is a consumer-GPU number; our GB10 community bench (29.16 t/s
  tg128) is the GB10 reference — NOTE the discrepancy: the 68 tok/s is
  likely a different context/quant/build or a different test; R-03
  measures on-box to settle it.
- (b) jZ9Mdit5TdI "QWEN 3.8 27B Local AI Review" — Digital Spaceport.
  Covers qwen38 27B on vLLM on a Threadripper WRX80 build (NOT GB10).
  Confirms: FP8 and NVFP4 are the two quant families for qwen38 on
  consumer/prosumer; NVFP4 is the faster path on Blackwell-class GPUs
  (consistent with our box running NVFP4 via sglang). The vLLM settings
  discussed are for their vLLM setup — NOT our stack (we use sglang for
  qwen38). Usable for us: (1) confirms "main agent = the strong one"
  calibration (qwen38 is the full-precision reference the subagent is
  measured against), (2) the FP8/NVFP4 tradeoff context, (3) their
  single-stream t/s numbers on their hardware are a SECOND control
  condition (but different box — cite as "consumer reference", not our
  baseline). Our baseline = the qwen38-dgx-serving-ops bench (37 t/s
  greedy median, this box, idle).
**Proposed resolution:** S5 control conditions cite TWO baselines:
(1) our-box qwen38 (37 t/s greedy, qwen38-dgx-serving-ops 2026-09-17),
(2) the consumer vLLM reference (jZ9Mdit5TdI) labeled as such. R-03
settles the bonsai2 on-box number against the 68 tok/s claim.

**R-11 — Cost model for the doctrine (feeds S4).**
From R-03/R-08 measurements: for each task shape, expected subagent cost
(tokens × t/s × N) vs main-agent time saved; find the break-even line where
"main does it itself" beats "dispatch to subagent". This is the single
most important doctrine number — the skill must not dispatch tasks that
are cheaper for main to do itself.
Gates: S4, S5.
**Findings:** (2026-09-19 — formula + v1 numbers; final calibration at S5)
- Cost model: dispatch_cost(task) = prefill_tokens/t/s_prefill +
  output_tokens/t/s_decode + dispatch_overhead (slot wait + collect +
  verify time on main). main_cost(task) = output_tokens/t/s_main +
  main-context-growth cost (the hidden cost: every token the main agent
  itself generates/reads burns its session context — the C-02 goal).
- Break-even: dispatch wins when output_tokens/t/s_bonsai +
  dispatch_overhead < output_tokens/t/s_main + context_saved_value, AND
  the task is verify-able cheaply (checker cost < verification of a
  subagent mistake on main). With v1 numbers (bonsai2 29.16 t/s decode
  vs qwen38 ~37 t/s single-stream — subagent is SLOWER per token!):
  the win is NOT raw speed, it's CONTEXT PRESERVATION + PARALLELISM.
  A 50k-token bulk job on main = 50k tokens of main context + 23 min;
  on a subagent = 0 main-context cost + 29 min but main is free to work.
  Fan-out N instances = wall-time / N for independent subtasks.
- v1 doctrine numbers (until R-08/R-03 calibrate): dispatch threshold =
  expected output > ~2k tokens (below that, dispatch overhead dominates
  — matches C-12's tiny-tasks row); fan-out threshold = task decomposes
  into >= 2 independent subtasks of >2k tokens each; single-slot
  (no fan-out) for tasks needing shared repo state.
- The "main is slower per token" fact is load-bearing for S4: the skill
  must frame subagents as context-parallelism workers, NOT speed
  workers. DSpark (2.45x on code, single-slot) flips that for
  single-stream coding jobs — flag per-task.
**Proposed resolution:** S4 doctrine cites this formula + the 2k-token
dispatch threshold + the context-preservation framing; S5 bench
calibrates the thresholds with real numbers (R-08 ladder), results
backported to S4.

---

## C. Parking lot (things that surfaced but may not be OQs)

- mmproj/vision tower: skip for v1? (text agents) — **confirmed skip**
  (R-01: text-only serving never loads it; 0.63 GB stays on disk).
- Multiple GB10 boxes: "treat 1 as a batch of however many" — defer to
  post-v1, confirm. - confirmed
- The idea's "bench framework that identifies ALL usage scenarios" — the
  scenario matrix is R-08; anything beyond task-type×N×concurrency×context
  (e.g. cross-box, multi-main-agent) is post-v1? — treated as post-v1 in
  the R-08 matrix draft; confirm at S5.
- License of the delivered package (if others get it): personal MIT?
  Confirm at S6. (Model weights are Apache-2.0; our install/skill scripts
  can be MIT; confirm.)
