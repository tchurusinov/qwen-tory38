---
title: qwen-tory38 SPEC
status: proposed            # proposed -> active (when locked down, all OQs resolved) -> superseded
supersedes: null
last_reviewed: 2026-09-19
method: spec-driven vibe coding (see docs/transcripts/spec-driven-vibe-coding-in-action_1Fl94dPn2mU.md)
---

# qwen-tory38 — SPEC (skeleton)

This document is SKELETON. Every section below names what it must lock down
before the section is "active". No code is written from this doc until each
section has: exact behavior, exact error handling (exit codes / exceptions
named, not "or similar"), a concrete configuration example, a testing
section, and verifiable acceptance criteria. Open questions live in
`open-questions.md` (IDs referenced as C-nn / R-nn); the doc moves to
`active` only when all referenced OQs are answered.

**What this project is (one paragraph, per 00-overview discipline):**
A GB10/DGX-box agent stack: the main agent runs on qwen3.8-27b NVFP4 via the
existing sglang server (:30001, ~70/120 GB unified memory); subagents run on
Ternary-Bonsai-2-27B (ternary weights, ~6-7 GB, ~98% of FP16 quality) served
as one or more instances in the remaining VRAM. The main agent drives
subagents through deterministic bash scripts (flags, conditions, no LLM
orchestration), governed by a harness skill that encodes work-allocation
doctrine (main is smarter). A bench framework measures the efficient/fast
usage patterns. Everything ships as a one-command install for GB10 boxes.
Original idea: `idea.txt` (verbatim; this spec is its working translation).

## Document conventions (locked now, per the spec-driven method)

- Status lifecycle via YAML frontmatter: `proposed` / `active` /
  `superseded` (superseded docs name their replacement).
- Every component section carries: **Configuration** (concrete schema),
  **Testing** (what, how, hermetic where applicable), **Acceptance
  criteria** (verifiable, runnable).
- Open questions: allowed while `proposed`; must be resolved or explicitly
  waived before a section goes `active`.
- This SPEC.md is the master. If a section outgrows the doc, it is split
  into `docs/specs/NN-slug.md` and this file keeps a one-paragraph pointer +
  its acceptance criteria. Sections are specced one at a time (the method's
  "pump the brakes" rule — do not lock the whole doc at once).
- Implementation findings that contradict this spec get backported HERE
  (spec follows code when the code was right), never the other way around.

---

## Section 0 — Project overview (no direct code; grounding)

Must lock: box + VRAM budget table (main model footprint, free budget,
per-subagent-instance footprint, headroom policy); the two-model division of
labor (what main does, what subagents do, why — intelligence gap is a
DESIGN INPUT, not an afterthought); component inventory (engine / harness
scripts / skill / bench / package); what is explicitly NOT in scope
(non-goals from idea.txt); existing-stack contract (the running sglang qwen
endpoint stays alive and untouched by this project unless C-14 says
otherwise). OQs: C-01, C-02, C-08, C-09, R-04.

## Section 1 — Subagent inference engine (bonsai2 on GB10)

Must lock: artifact + quant choice (PTQ1_0 vs PQ2_0 — sizes 5.95 GB vs
7.21 GB — quality deltas per the whitepaper; C-07); serving engine + exact
version/fork + CUDA arch support for sm_121 Blackwell (R-02); the exact
server command(s), ports (one port per instance; scheme defined here),
context-length setting per instance, max-num-sequences / concurrency knobs;
VRAM accounting formula (weights + KV at chosen context × concurrency,
verified against R-04 measurement, not estimated); error/edge cases:
out-of-VRAM on instance load, model download failure, port collision,
engine crash mid-request (each with named exit code / behavior).
Configuration: `subagent-engine.yaml` (schema + worked example: engine,
artifact hash, port range, context, concurrency, instance count).
Testing: cold-load test, sustained-load test at concurrency ceiling, VRAM
ceiling test (load until OOM, record the number). Acceptance: N instances
loaded at startup, each serving at measured t/s >= baseline from R-03, with
zero impact on the main sglang endpoint's measured t/s within X% (X set
after R-04). OQs: C-07, C-08, C-10, R-02, R-03, R-04, R-05.

## Section 2 — Startup / serving lifecycle

Must lock: what starts what, in what order (main sglang first — it is
pre-existing; bonsai instances after, or in parallel?); systemd units vs
docker vs both (C-05); health checks per endpoint (exact curl probes,
retry/backoff, what "healthy" means — /v1/models returns the model id,
first-token latency probe); failure policy: instance N dies -> restart
policy + max restarts + what the harness scripts report to the main agent;
VRAM-drift policy: contexts grow during work — is instance count STATIC
(boot-time, C-08 default) or DYNAMIC (spawn/kill on demand)? Exact
reclaim/rebalance procedure if dynamic. Boot-time budget check: refuse to
start instances if free VRAM < required (exact formula, from R-04).
Configuration: `serving.yaml` (order, units, health checks, restarts).
Testing: kill -9 an instance, verify recovery + harness reports it; reboot
test (box boots, both stacks up within T seconds — T measured, not guessed).
Acceptance: cold boot -> both main + N subagent endpoints healthy within T;
single-instance crash -> auto-recovered, main agent unaffected, harness
surfaces the event. OQs: C-05, C-08, C-10, R-09.

## Section 3 — Harness control scripts (the main-agent interface)

Must lock: the FULL command surface (this is the API; spec every flag):
- spawn: submit a task (prompt file / inline, tool policy, context budget,
  deadline, output file); exit codes defined per outcome (queued / done /
  timeout / engine-down / malformed)
- steer / steer-and-wait, cancel, status, list-instances,
  collect-result, bench-runner
- instance routing: round-robin vs pinned vs least-loaded (C-03);
  concurrency accounting: a task occupies a slot until completion — exact
  slot model, what happens when all slots are busy (queue? reject?
  deadline?)
- output contract: every script writes machine-parseable output
  (JSON on stdout or a typed file in a fixed dir — pick ONE and lock the
  schema); stderr is human-only
- error handling: every expected failure class named with its exit code and
  the exact script the main agent must run next (the "what do I do" table)
- idempotency: re-running a failed task does not double-charge slots or
  re-download models
- hermetic-testability: scripts accept env overrides for ports/paths so the
  test suite can run against fake endpoints (mock server that returns
  canned JSON) — no real GPU needed for the script-logic tests
Configuration: `harness.yaml` (ports, dirs, slot counts, timeouts, output
dir). Testing: full script surface against a mock engine (no GPU); slot
contention test (spawn K+1 tasks at concurrency K); failure-injection tests
(engine down, timeout, malformed response) each producing the specified
exit code. Acceptance: the main agent can complete a 3-task fan-out,
collect all results, and recover from a mid-task engine kill, using ONLY
the documented commands — verified by a scripted end-to-end run. OQs: C-03,
C-04, C-11, R-08.

## Section 4 — Harness skill (the doctrine)

Must lock: work-allocation rules — task-shape -> (main | subagent |
fan-out-N) table with the WHY per row (from R-07 research; e.g. "diff
review -> subagent + main verifies" mirrors senior-git-1.0's
reviewer/builder split; "long-context reading of repo docs -> main";
"independent subtasks -> fan-out"); quality bar: subagent output is a
SELF-REPORT — main verifies before trusting (the senior-git-1.0 lesson);
when to re-run vs accept; session-economy rules (one SGLang engine is
shared — main-agent turns and subagent decodes queue on the same engine;
keep main turns short while N subagents run, batch spawns); cost model
(t/s × expected tokens × N vs main-agent time saved — the doctrine must be
NUMBERS, not vibes, from R-08 bench results); failure doctrine (what the
main agent does on each harness exit code). Configuration: none (skill is
docs) — but it must reference `harness.yaml` paths so it is never stale.
Testing: doctrine-vs-reality audit — run the top-5 recommended patterns
from the bench, confirm measured outcomes match the doctrine's claims.
Acceptance: a fresh main-agent session, given only the skill + scripts,
completes the standard workloads in Section 5 with no undocumented commands.
OQs: C-04, C-12, R-07, R-08, R-11.

## Section 5 — Bench framework

Must lock: the scenario matrix (task type × concurrency × instances ×
context length — the full list, from R-08); metrics (t/s, prefill, task
wall-time, task success rate with a NAMED scoring rubric per task type —
how "quality of a weaker model's answer" is judged without hand-waving);
control conditions (main-only baseline for every task type — the idea's
capability comparison, video jZ9Mdit5TdI); reproducibility (pinned model
revisions, recorded box state: driver, VRAM, what else is loaded, dates);
output (results dir with per-run JSON + a comparable summary table; how to
re-bench after any model/engine update); what a bench result CHANGES
(feedback path: results -> update doctrine numbers in Section 4 -> PR).
Configuration: `bench.yaml` (scenarios, budgets, repetitions, scoring
weights). Testing: the framework must run its OWN dogfood scenario and
produce a parseable result row (bench tests the bench). Acceptance: one
full bench run over the matrix, results table committed, every doctrine
number in Section 4 cites a row from it. OQs: C-13, R-08.

## Section 6 — GB10 packaging (one-command install)

Must lock: artifact form (docker image + compose file + `install.sh`
orchestrator — C-05); exact prerequisites (DGXOS 24.04.4, nvidia kernel,
nvidia container toolkit — verified on this box, R-09); what the one
command does and in what order (detects box VRAM -> computes N instances
from the Section 1 formula -> writes `serving.yaml`/`harness.yaml` ->
starts units -> runs the Section 2 health checks -> prints a
"stack ready" report); idempotent re-runs (reinstall without double
loading, without touching the existing main sglang setup unless C-14
says); upgrade procedure for the pinned sglang (C-06): how a new sglang
release is evaluated + pinned, and what re-benching is triggered; rollback
procedure. ARM64-only rule: every dependency must have an aarch64 build —
list pinned here. Configuration: the generated per-box config files
(locked schemas = Sections 1-5 configs composed). Testing: install on a
clean VM/box matching the GB10 profile (or this box with a scratch
namespace), run the Section 2 acceptance end-to-end, then reinstall over
it (idempotency). Acceptance: one command on a fresh GB10 -> healthy stack
+ passing Section 2 checks, with the box's VRAM budget respected
automatically. OQs: C-05, C-06, C-14, R-09.

---

## Global testing discipline (applies to every section)

Hermetic where possible: script-logic and config-validation tests run
against mock endpoints, no GPU, no network (the method's "sealed
environment" rule: deterministic, injectable clock where timing matters,
seeded randomness). GPU-touching tests are explicit, labeled, and never run
in parallel with a live review/subagent session (one engine, one at a time
— the machine constraint from senior-git-1.0). Every test asserts against
an acceptance criterion above, not against vibes.

## Global acceptance criteria (project-level)

1. Cold boot -> main + N bonsai instances healthy within measured T.
2. Main agent completes the standard workloads (Section 5 matrix) using
   ONLY documented commands, with every task outcome verified against the
   named rubric.
3. Full bench matrix run; every doctrine number in Section 4 cites a bench
   row.
4. One-command install on a fresh GB10 box passes Section 6 acceptance.
5. Main endpoint t/s within X% of its pre-project baseline while all N
   instances are at working load (X measured in R-04, not guessed).
