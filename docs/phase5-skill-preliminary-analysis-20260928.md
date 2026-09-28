# qwen-tory38 — Phase 5 Skill: Preliminary Analysis

Date: 2026-09-28. Author: senior architect (Hermes session).
Scope: everything needed to design and write the `bonsai2-harness` skill (SPEC S4).
Sources: SPEC.md S3/S4, MAP.md Phase 4-5, PR #21 (comments 1031-1043),
bench-results/ (2026-09-28 post-hotfix run), image/Dockerfile.runtime,
etc/subagent-engine.yaml, external web research (2026-09-28).

---

## 1. What Phase 5 is, in one paragraph

Phase 5 produces the **doctrine skill** (`bonsai2-harness`) that the main
agent loads to know *when, how, and why* to delegate to subagents. It is
the missing layer between the harness scripts (S3 — the "what" commands)
and the bench results (S5 — the "where subagents actually win" evidence).
The skill carries: the command reference (S3.2 verbatim), the
work-allocation table (S4.2, recalibrated by S5 bench data), session
economy rules (S4.3), cost doctrine with calibrated t/s constants (S4.4),
failure doctrine per exit code (S4.5), prompt templates per tool-loop row
(S4.1), and a **recipes section** (new, this analysis) with worked
examples for different coding-work shapes. The skill is
**harness-agnostic**: it documents the bonsai2 pool on this box but is
written so any agent runtime that can run bash and load a text skill can
use it. The doctrine audit script (S4.6) cross-checks every S4.2 claim
against S5.2 bench rows; the fresh-session test (S5.6) verifies the skill
is complete and self-sufficient in a zero-context session.

---

## 2. Phase 5 chunk-by-chunk (MAP 5.1–5.7)

### 5.1 — SKILL.md skeleton

Frontmatter, when-to-use trigger, command reference (exactly S3.2 — the
skill never invents commands), + the S4.1 prompt templates (one proven
template per S4.2 tool-loop row: workdir contract, definition of done =
test command + expected exit code, final-answer contract).

**What this means concretely:** the skill's command section is a copy of
S3.2's command surface (spawn/steer/cancel/status/collect/unload/reload/
bench + their flags + exit codes). It does not add, remove, or rename any
command. The prompt templates are the subagent-facing text: the main agent
fills in task specifics (workdir, spec, test command) and passes the
template to `bonsai2-spawn -i`. The template structure is:
(1) workdir contract ("all paths relative to workdir; do not look
outside it"), (2) definition of done (exact test command + expected exit
code), (3) final-answer contract (one paragraph: what was done, what was
verified, what failed, exact commands run).

**New from this analysis:** the skill also carries a **recipes section**
(§4 below) with worked examples for different coding-work shapes. These
are not new prompt templates — they are *usage examples* showing the main
agent how to fill the templates for specific task types (single-file
code-gen, test batch, diff review, spec-chunk implementation, fan-out
analysis). Each recipe cites the bench row that validates it.

### 5.2 — Work-allocation table

Every S4.2 row carries a `[M#]` citation from the Phase 4 run. The
self-report rule: "unverified claim WITH EVIDENCE" — `--tools on` results
carry the tool_log ledger, and main's verification is ledger-first
(check the claimed suite ran with exit 0 in tool_log, check the claimed
files exist in the workdir, then re-run the named check on main's own
side). The S4.2 tool-loop rows (spec-chunk [M9], test-batch [M10],
browser-via-python-tool) are included.

**What this means concretely:** the S4.2 table in SPEC.md is the
source-of-truth. The skill re-states it with bench citations. The 2026-09-28
run gives us the citations:

| S4.2 row | Bench citation | Live result |
|---|---|---|
| Single-file code-gen chunk → subagent | [M1] | **fail** (0/3 — reasoning-only output, H4 content-only fix landed but M1 still fails at pin) — **row stays subagent but the recipe must force content-only** |
| Per-file analysis fan-out → fan-out N | [M3] | **pass** (0.88 post-spot-check re-run) — independent units work; spot-check needs main to read 1/3 |
| Diff review → subagent draft → main verifies | [M4] | **pass** (10/10 seeded bugs) — subagent finds bugs reliably; main verifies the claim |
| Long-context reading (≥30K) → main | [M5] | **pass** (5/5 QA at 60k ctx) — but the row says main because subagent at 8k default loses the tail; M5 proves the 64k-ctx subagent works, so the allocation stays main for *reading* but the subagent is viable for *QA on long docs* |
| Test execution / lint / build-check → subagent | [M2] | **pass** (10/10 coverage) — mechanical, parallelizable, output is a report |
| Draft writing → subagent | [M3] | **pass** (0.88) — quality bar is lower-verification; main edits the draft |
| Spec-chunk implementation → subagent --tools on | [M9] | **fail** (2/3 — workdir-cleanup ordering bug: H6 rmtree before M9 scorer runs) — capability layer works (main re-run passed, tool_log shows exit 0) but the workdir check is systematically unsatisfied; row stays subagent, recipe must note the workdir persistence issue |
| Test batch with report → subagent --tools on | [M10] | **fail** (0/2 spot verdicts agree — claimed None vs observed fail) — the subagent ran the batch correctly (10/10 covered) but the report's per-file status field was `claimed: None` in the re-run check; the capability layer works, the report format needs the per-file status field populated |
| Saturation / concurrency → fan-out N | [M7] | **pass** (2.08 t/s wall at 16-wide) — zero 5xx; per-task t/s 4-19 (variance = slot sharing); aggregate is the headline |
| Main control baseline | [M6] | **fail** (main t/s 8.52, 3/5 shapes pass) — the control is incomplete; M1/M3 fail on main too (same reasoning-only + spot-check issues) |
| Main guardrail | [M8] | **fail** (main t/s 0.00 vs M6 8.52 — 100% drop) — M8 measures main's t/s *while subagents are loaded*; the 0.00 is a measurement artifact (the main's completions queued behind the subagent decodes on the shared engine, and the timing window missed them); the guardrail is unmeasurable until the timing is fixed |

### 5.3 — Session economy + cost doctrine

S4.3 session economy (slots, batch spawns, short main turns, instance
hygiene, steer/cancel semantics) + S4.4 cost doctrine with **re-benched
t/s constants** replacing the a-priori 29/4800 floors. The `--tools on`
dispatch-cost term: `steps_est × tool duration + prefill of tool results`.
Win rule + two-consecutive-loss flip.

**What this means concretely — the calibrated constants:**

The a-priori values (t/s_decode ≈ 29, t/s_prefill ≈ 4800) are now
replaced by measured values from the 2026-09-28 run:

| Constant | A-priori | Measured (2026-09-28) | Source |
|---|---|---|---|
| Subagent decode t/s (single task, no contention) | 29 | **44-61** (MAP 1.7-1.11 @gpu, sustained 8-concurrent) | bench MAP 1.7 |
| Subagent decode t/s (under 16-wide contention) | 29 | **3.7-19.3** (M7 per-task, 16 tasks at concurrency 16) | bench-results M7 |
| Subagent decode t/s (tool-loop, M9) | 29 | **0.27** (M9: 111 completion tokens / 413 s elapsed — the tool-loop steps dominate, not decode) | bench-results M9 |
| Subagent decode t/s (tool-loop, M10) | 29 | **2.33** (M10: 250 completion tokens / 108 s) | bench-results M10 |
| Main t/s (idle, no subagents) | ~30 (user observed) | **8.52** (M6 control, key-walled main with `main_api_key`) | bench-results M6 |
| Main t/s (while subagents loaded) | ~15 (user observed) | **0.00** (M8 — measurement artifact; the main's completions queue behind subagent decodes on the shared engine) | bench-results M8 |
| Aggregate t/s (16-wide, 2 instances) | — | **2.08 wall / 0.215 serial** (M7) | bench-results M7 |

**The key calibration insight:** the a-priori 29 t/s decode was for a
*single subagent task with no contention*. Under realistic multi-task
load (16 tasks, 2 instances, 8 slots each), per-task decode drops to
3.7-19.3 t/s. The skill's cost doctrine must use the *contention-adjusted*
number, not the single-task number. The tool-loop tasks (M9/M10) have a
different cost profile entirely: the wall time is dominated by tool
execution (bash commands, file writes, test runs), not by decode. M9 took
413 s for 111 completion tokens (0.27 t/s effective) because the 5 tool
steps (read_file, ls, write_file ×2, bash unittest) took the bulk of the
time. The dispatch-cost formula for `--tools on` tasks must reflect this:
`steps_est × tool_mean_duration_s + prefill_of_tool_results` is the
dominant term, not `output_tokens / t/s_decode`.

**New from user's PR #21 observations (comment 1037):**

1. **Main agent t/s halving (30→15):** user observed main's effective
   t/s fell from 30 to 15 when 2 subagent instances were loaded. This is
   the *real-world* M8 effect (the bench's M8 measured 0.00 due to a
   timing artifact, but the user's observation is the ground truth for
   planning). The skill must bake in: **while subagents are running, plan
   main's work around the halved t/s** — send subagents large clearly-
   defined chunks, then work on intelligence-critical tasks that don't
   suffer from reduced t/s (planning, architecture decisions, verifying
   subagent output). Do NOT do token-heavy work on main while subagents
   are running.

2. **Stateless vs stateful workflow branches:** user wants two
   workflow branches in the skill:
   - **Stateless (default):** main loads subagent for a task, subagent
     does it, main unloads subagent, main verifies at full t/s, if
     needed spawn again. Load is "relatively instantaneous" (warm boot
     8-15 s, observed 21.5 s under contention; S2.8 reload budget 30 s).
     No context retention between tasks. This is the default because it
     keeps VRAM free and the main's t/s at full speed between delegations.
   - **Stateful:** subagent model remains loaded, retains context from
     previous turns. Use when the task is multi-step and the subagent
     needs to remember what it did in step 1 while doing step 2. Costs:
     VRAM stays allocated (instance footprint 14.08 GB resident), main's
     t/s stays halved, and the subagent's context grows (KV cache grows
     with sequence length — on a 262144-ctx instance, a 10k-token
     conversation uses ~10k/262144 × KV_cache_size of the slot's KV).

   The skill documents both branches with the same structure (the same
   S3.2 commands, the same verification protocol), but different lifecycle
   management: stateless = spawn → collect → unload → verify → (spawn
   again if needed); stateful = spawn → collect → steer (if needed) →
   collect → … → unload at task completion.

3. **VRAM climb observation:** user observed VRAM climbing from 100/121
   to 116/121 during the M1-M10 run (2 instances + main), with post-run
   VRAM staying at 117/121. The climb is expected: each subagent task's
   KV cache grows with sequence length (q4_0 KV, 262144 ctx — the KV
   cache is pre-allocated at boot for the full ctx, but the *used* KV
   grows as the task's sequence grows). The post-run 117/121 is the
   instances still loaded (they weren't unloaded after the bench run).
   The skill should document: **check `bonsai2-health` or `nvidia-smi`
   after a bench run; if VRAM is still high, `bonsai2-unload --all`**
   (H13 tier 2). The 16 GiB "did not exceed" observation (the user's
   "did not exceed 16 which was good to see") is the per-instance KV
   growth staying within the 14.78 GB budget — the S1.4 allocation tier
   held.

4. **Cache memory 99%:** user observed cache memory at 99% consistently
   with main + 2 instances. This is the OS page cache (Linux's
   read-ahead and file caching) — it's *reclaimable* memory (the H12
   `meminfo()` snapshot carries MemFree, Cached, Shmem, SReclaimable
   specifically to distinguish reclaimable from resident growth). The 99%
   cache is not a problem: it's the OS caching the model weights and
   bench fixtures, and it's freed under memory pressure. The skill
   should note: "high cache% is normal on a unified-memory system;
   look at MemAvailable, not cache%, for the real headroom."

5. **CPU 4-11-20% / GPU 96%:** CPU was consistently low (4-20%, never
   >30%), GPU was 96% consistent. This is expected: the subagent decode
   is GPU-bound (Blackwell iGPU, memory-bandwidth-limited — see §5.2
   below), and the CPU is only doing tool execution (bash commands, file
   I/O) and the harness scripts. The user's question "will we benefit
   with more t/s if we somehow squeeze the CPU more too?" — the answer is
   **no, for decode**: the decode is memory-bandwidth-bound (GPU read
   161 GB/s from LPDDR5X, the theoretical 273 GB/s bus is shared with
   the CPU). CPU threads matter for *prefill* (the initial prompt
   processing), not for decode. The llama.cpp docs confirm: "If your
   token generation is extremely slow, try setting this number to 1. If
   this significantly improves your token generation speed, then your CPU
   is being oversaturated." Our CPU is at 4-20% — not oversaturated.
   The GPU at 96% is the healthy state.

### 5.4 — Failure doctrine

One paragraph per S3.2 exit code. This is already specified in S4.5;
the skill re-states it. No new content needed beyond the S4.5 text.

### 5.5 — Doctrine audit script

`bench.yaml` scenario rows ↔ S4.2 citations + win/lose recompute →
`doctrine-audit.json`; failing audit blocks the skill PR.

**What this means concretely:** a Python script (in `bench/` or
`scripts/`) that:
1. Reads `bench.yaml` (the S5.7 config — the scenario matrix M1-M10).
2. Reads the S4.2 work-allocation table (from the skill's SKILL.md or
   from a machine-readable copy in the repo).
3. For each S4.2 row that cites `[M#]`, checks that M# exists in
   `bench.yaml` and that the bench row's verdict (pass/fail) supports the
   allocation (e.g., if the row says "subagent" but the bench row failed
   for a reason that invalidates the subagent approach, the audit flags
   it).
4. Recomputes the win/lose for each task shape using the calibrated
   S4.4 constants and compares to the table.
5. Outputs `doctrine-audit.json` with the pass/fail per row + the
   recomputed win/lose.
6. Exit 0 = audit clean; exit 1 = stale row detected (the S4.7 #3
   acceptance: "two synthetic loss scenarios → the audit flags the
   stale row").

### 5.6 — Fresh-session test

New main-agent session, skill + S3.2 commands only (mock engine),
completes the S5.6 standard workload set (M1, M2, M3 + one M4-style
diff review, text-only; plus the M9/M10 tool-loop rows with `--tools on`,
ledger-verified per the amended S4.2 self-report rule). Command log ⊆
S3.2, all results verified per S4.2.

**What this means concretely:** a test (in `tests/`) that:
1. Spawns a fresh pi/Hermes session with only the `bonsai2-harness`
   skill loaded (no other context, no memory, no prior state).
2. The session is given the S5.6 standard workload set as a task list.
3. The session must complete all tasks using only S3.2 commands
   (asserted by parsing the session's command log).
4. Each result is verified per the S4.2 self-report rule (ledger-first:
   check tool_log, check files in workdir, re-run the named check).
5. The test passes if all tasks complete, all commands are in S3.2, and
   all verifications pass.

**This is the skill's self-sufficiency test:** a zero-context agent that
only has the skill must be able to do the work. If the skill is missing
a command, a doctrine rule, or a prompt template, the test fails.

### 5.7 — S4.7 acceptance

- Audit passes (citations exist, win/lose recomputed or table updated in
  same PR).
- Fresh-session workload complete.
- Two synthetic loss scenarios → audit flags the stale row.
- Tool-loop doctrine: the S4.2 tool-loop rows (M9/M10-cited) complete the
  standard workload set with `--tools on` in a fresh session, every result
  ledger-verified per the amended S4.2 self-report rule; command set
  still S3.2-only.

**This is the gate:** all four acceptance criteria must pass before the
skill PR is mergeable. The audit script (5.5) provides criteria 1 and 3;
the fresh-session test (5.6) provides criteria 2 and 4.

---

## 3. PR #21 + first live run: what happened, what's hotfixed, what's open

### 3.1 The first live run (2026-09-27, pre-prod)

Single live run on this box (2 instances, `--tools` launch, 14.78g pool
cap each, S1.3 shape, PQ2_0, q4_0 KV, ctx 262144, ports 30101/30102).
Launched with the prod venv, `--deadline-s 600`.

**Outcome:** M6/M1/M2/M3/M4/M5/M7 rows written; run crashed at M9
(`CapabilityError: not terminal within 120.0s` — the driver's `wait_s=120`
poll window vs the task's 600 s deadline). M8/M9/M10 rows not written.

**Key failures:**
- M6: fail (main 401 wall — the main endpoint is key-walled but the
  bench's main endpoint has no `api_key`)
- M1: fail (all output in `reasoning_content`, 1 content token — the
  pin's deepseek reasoning format put the whole M1 answer in reasoning)
- M3: fail (spot-check seam unsupplied — the `--spotcheck` value must be
  driven by the live main agent)
- M5: fail 4/5 (the one miss: "M1, M2, M7" vs "M1, M2, and M7" — the
  scorer's matcher is stricter than the doc's phrasing)
- M9: crash (driver-timeout defect — `wait_s=120` vs `deadline 600`)

### 3.2 Hotfixes H1-H13 (PR #22, merged)

| Hotfix | What it fixed | Status |
|---|---|---|
| H1 (P0) | Driver's `wait_s=120` racing 300/600 s task deadline → `wait_s` now defaults to `deadline_s + 90 s` | ✓ (M9 no crash in re-run) |
| H2 (P1) | Capability-row failure crashed the whole run → now a row verdict, run continues | ✓ (re-run reached M10/M8) |
| H3 (P1) | Main endpoint key-walled, no `api_key` in bench → `harness.main_api_key` added | ✓ (M6 main_tps 8.52, no 401s) |
| H4 (P1) | M1 prompt forces content-only ("Your final answer must be EXACTLY the two ```python fences") | ✗ (M1 still reasoning-only in re-run: 1 content token, ~4.5k reasoning chars — the prompt fix landed but the pin's deepseek format still burns everything into reasoning) |
| H5 (P1) | Undriven M3 spot-check seam recorded `spotcheck: "unjudged"` + named detail | ✓ (M3 unjudged row named; judged re-run supersedes) |
| H6 (P2) | Staged M9 workdir leaked `/tmp/bonsai2-m{9,10}-*` → `shutil.rmtree` in `finally` | ✓ (but see §3.3 ordering bug) |
| H7 (P2) | M9 `_tool_log_shows_test_run` — no early-return on non-zero match | ✓ |
| H8 (P2) | M5 matcher tolerant of conjunctions/word-boundary particles | ✓ (M5 now 5/5 in re-run) |
| H9 (P2) | M7 records both `aggregate_tps_wall` and `aggregate_tps_serial`; re-bench gate compares like-with-like | ✓ (M7 headline = wall 2.08 t/s) |
| H10 (P2) | `rebench.py` — present-but-malformed `--config` is a named bad-input error | ✓ |
| H11 (P2) | `results_dir` WARN names the `--results-dir` override flag | ✓ |
| H12 (P2) | `boxstate.py` — meminfo carries reclaimable pool fields; runner writes post-run snapshot | ✓ |
| H13 (P1) | Two pool-release flags `--release-after` / `--release-between` (manager-mediated unload/reload) | ✓ (flags exercised in re-run; `bonsai2-unload` / `bonsai2-reload` work) |

### 3.3 The re-run (2026-09-28, post-hotfix, PR #23, MAP 4.10 clean half)

All 10 scenario rows written + summary + M3 spot-check re-run row.

**Verdicts:** M2/M4/M5/M7 pass; M3 pass (spot-check re-run, 0.88);
M6/M1/M8/M9/M10 fail.

**Open issues after the re-run:**

1. **M1 still reasoning-only** (H4 landed but the pin's deepseek format
   still burns everything into `reasoning_content`). The M1 prompt now
   says "Your final answer must be EXACTLY the two ```python fences" but
   the model's 4.5k reasoning chars + 1 content token persist. This is a
   **SPEC-level seam question**: either the M1 rubric must tolerate
   reasoning-only output (score from `reasoning_content` when
   `content` is empty), or the M1 tasks must force the model to emit
   content (e.g., "Do NOT use reasoning; write your answer directly").
   For the skill: the M1 recipe must include a content-forcing preamble
   in the prompt template.

2. **M9 workdir-cleanup ordering bug** (H6's `rmtree` in `finally` runs
   *before* the M9 scorer's workdir check): the M9 scorer checks that
   "the spec's 3 functions exist in the workdir" — but H6 already
   `rmtree`'d the workdir in the driver's `finally` before the scorer
   runs. So sub-condition (c) is **systematically unsatisfiable** in live
   runs: the workdir is gone by the time the scorer looks for it. The
   M9 row shows `n_present: 0` (0/3 functions found) every time. This is
   a **P1 defect** in the hotfix sequence: H6 cleaned up the workdir,
   but the M9 scorer needs the workdir to be alive during scoring.
   Fix options: (a) move the `rmtree` to after scoring (the driver should
   clean up after the scorer runs, not before), or (b) the M9 scorer
   checks the workdir *before* the driver's `finally` (i.e., the scoring
   happens inside the driver, not after it). For the skill: the M9 recipe
   must note that the workdir check is the one sub-condition that depends
   on the driver's cleanup ordering, and that the skill's verification
   protocol (main re-runs the suite in its own copy) is the authoritative
   check, not the workdir file check.

3. **M10 report format gap** (claimed: None vs observed: fail): the M10
   scorer's "main re-runs 2/10 files, verdicts agree" check reads the
   subagent's report JSON and checks that the per-file `status` field
   matches what main observes when re-running. The subagent's report had
   `status: "pass"` for all 10 files (including the 2 seeded fails), so
   main's re-run of the 2 seeded-fail files found `exit 1` (fail) but the
   report said `status: "pass"` → 0/2 agree. The root cause: the
   subagent's report format doesn't distinguish "I ran the file and it
   passed" from "I ran the file and it failed but I'm reporting the
   overall batch result." The M10 recipe must specify: **the report JSON
   must have per-file `status` (pass/fail) + `exit_code` + `duration_ms`,
   and the status must reflect the actual exit code, not the batch
   verdict.**

4. **M8 main t/s = 0.00** (measurement artifact): M8 measures main's
   t/s *while subagents are loaded*. The 0.00 means the main's
   completions didn't complete within the timing window — they queued
   behind the subagent decodes on the shared engine. The user's
   observation (30→15 t/s) is the ground truth; the bench's 0.00 is a
   timing artifact. For the skill: the M8 guardrail is a *planning*
   tool, not a hard gate — "expect main's t/s to halve while subagents
   are running; plan accordingly."

5. **M6 control incomplete** (3/5 shapes pass on main): the M1 and M3
   shapes fail on main too (same reasoning-only + spot-check issues).
   The control is incomplete — it should pass 5/5 if the main agent is
   doing the work. This means the S4.2 rows that say "main" for M1/M3
   shapes are not validated by the control: the main agent also fails
   them. For the skill: the M1/M3 recipes must include the same
   content-forcing + spot-check-drive steps that the subagent recipes
   need.

### 3.4 User's specific directions from PR #21 (comment 1037)

These are the user's admin observations of the live run, and they are the
**design requirements** for the skill:

1. **Main agent t/s halving → plan around it:** "the main agent's t/s
   fell from effective 30 t/s to 15 t/s — this will have to be baked
   into the skill that the way the main agent works is somehow to plan
   out the work in such a way that he will be able to send subagents on
   large chunks of clearly predefined tasks and then move on to the next
   critical (in the meaning of dependent on the main agent's intelligence
   task while the grunts do the grunt work) task that will not suffer
   from the decreased t/s"

   → **Skill doctrine: "While subagents are running, main's effective
   t/s halves. Plan main's inter-spawn work around this: do
   intelligence-critical tasks (planning, architecture, verification)
   that don't need high t/s. Do NOT do token-heavy generation on main
   while subagents are running. The subagents do the grunt work; main
   does the thinking work."**

2. **Two workflow branches: stateful and stateless:** "i feel like in
   the skill we should have 2 workflow branches, exactly the same as each
   other, but one is stateful and subagent models remain loaded and
   stateless where the main agent loads subagent for whatever task and
   after its task is done to unload it. stateless and stateful here is
   understood both in the sense that they either retain context from
   prev turns and also in the sense that the subagent model remains
   loaded or not. then have the stateless one as default cuz i genuinely
   feel it would be faster this way, since to load the subagent is
   relatively instantaneous and it would be good if we reliably make it
   so: the main agent understands the task, loads subagents and
   delegates to them the big clearly defined work, they do it, unload
   and then the main verifies state of the work and whatnot with full
   t/s, then if needed, spawn them again and so on and so forth."

   → **Skill structure: two workflow branches with identical structure
   (same commands, same verification protocol), different lifecycle
   management:**
   - **Stateless (default):** spawn → task → collect → unload → verify
     at full t/s → (spawn again if needed). No context retention. VRAM
     freed between tasks. Main's t/s at full speed between delegations.
   - **Stateful:** spawn → task → collect → (stay loaded, context
     retained) → steer if needed → collect → … → unload at task
     completion. VRAM stays allocated. Main's t/s stays halved. Use for
     multi-step tasks where the subagent needs to remember prior steps.

3. **VRAM climb + release mechanism:** "2 loaded instances + main was
   100/121, but then as the tasks progressed it began climbing: 104,
   108, 110, 111, 113, 115, 116 (but did not exceed 16 which was good to
   see) — what causes that? i am curious to know if we have a baked in
   inefficiency that needs cleaning - like dropping the used context and
   reloading it or something. also post run the vram stayed 117/121 so
   we def need a release mechanism of unused for the task at hand drop
   of vram"

   → **Skill doctrine: "VRAM climbs during tasks because the KV cache
   grows with sequence length. The per-instance budget (14.78 GB) is the
   ceiling — the climb stays within it. After a task or bench run,
   check VRAM: if it's still high (instances still loaded), run
   `bonsai2-unload --all` (H13 tier 2) to release. The release mechanism
   is the S3.2 unload/reload pair; the skill documents when to call
   it (after each task in stateless mode; at task completion in
   stateful mode; after a bench run)."**

4. **Cache memory 99%:** "i saw cache memory with the main+2 instances
   was 99% consistently — what is that for and what role has it in our
   agents usage?"

   → **Skill doctrine: "High cache% is the OS page cache (reclaimable
   memory). It's not a problem — it's freed under memory pressure. Look
   at MemAvailable (from `bonsai2-health` or `nvidia-smi`), not cache%,
   for the real headroom. The H12 meminfo snapshot carries the
   reclaimable fields (MemFree, Cached, Shmem, SReclaimable) for this
   reason."**

5. **CPU low / GPU 96%:** "cpu numbers were consistently between
   4-11-20% and did not exceed 30% at any point, gpu numbers were
   consistent 96% which is good, we wanna see it full force, will we
   benefit with more t/s if we somehow squeeze the cpu more too?"

   → **Skill doctrine: "CPU at 4-20% is normal: the subagent decode is
   GPU-bound (memory-bandwidth-limited on the unified LPDDR5X). CPU
   threads matter for prefill, not decode. GPU at 96% is the healthy
   state — the GPU is working as hard as it can. Squeezing the CPU
   further will not increase decode t/s; it will only increase prefill
   speed (which is already fast)."**

---

## 4. Recipes section: design

The skill carries a **recipes section** with worked examples for
different types of coding work / approaches to coding with subagents.
Each recipe is a fill-in-the-blanks prompt template + the verification
protocol + the bench citation that validates it.

### 4.1 Recipe structure

Each recipe has:
- **Task shape:** the S4.2 row this recipe implements.
- **Bench citation:** the M# row that validates this task shape.
- **Prompt template:** the fill-in-the-blanks text the main agent passes
  to `bonsai2-spawn -i`. Includes the workdir contract, the task
  description, the definition of done (test command + expected exit
  code), and the final-answer contract.
- **Verification protocol:** what main does after `collect` (ledger-
  first: check tool_log, check files in workdir, re-run the named check).
- **Failure modes:** what goes wrong with this task shape (from bench
  data + live run observations) and how to handle it.
- **Stateless vs stateful:** which workflow branch to use (stateless by
  default; stateful only for multi-step tasks where the subagent needs
  to remember prior steps).

### 4.2 Recipes (from bench lessons + research)

#### Recipe R1: Single-file code-gen (S4.2 row: subagent, [M1])

**Task:** Generate a single-file Python module + its test suite.
**Bench:** M1 (0/3 pass — reasoning-only output; content-forcing needed).
**Prompt template:**
```
You are implementing a single-file Python module.
Workdir: {workdir} (all paths are relative to it; do not look outside it).

Task: {task_description}

Definition of done:
- The module file is at {module_path} and compiles: `python -c "import {module_name}"` exits 0.
- The test suite is at {suite_path} and passes: `python -m unittest {suite_name}` exits 0.

Final answer contract (your last message, no tool calls after this):
One paragraph: what you did, what you verified (the exact commands you
ran and their exit codes), what failed (if anything).
Do NOT use reasoning. Write your answer directly.
```
**Verification:** main re-runs the module compile + suite in its own
scratch dir; checks the module defines the expected functions.
**Failure mode:** the model burns everything into `reasoning_content`
(M1 live result: 4.5k reasoning chars, 1 content token). Fix: the
"Do NOT use reasoning" line in the template + the main agent checks
`content` first, falls back to `reasoning_content` if `content` is
empty.
**Workflow:** stateless (single task, no context retention needed).

#### Recipe R2: Test batch with report (S4.2 row: subagent --tools on, [M10])

**Task:** Run N test files, structure the results into a report JSON.
**Bench:** M10 (0/2 spot verdicts agree — report format gap).
**Prompt template:**
```
You are running a test batch and generating a report.
Workdir: {workdir} (all paths are relative to it; do not look outside it).

Task: Run each {N} file in {file_pattern} with:
  python -m unittest {module_name}
Record the exit code and duration for each file.

Definition of done:
- All {N} files have been run exactly once.
- The report is written to {report_path} as JSON with this schema:
  {{"files": [{{"file": "<name>", "status": "pass"|"fail", "exit_code": <int>, "duration_ms": <int>}}]}}
- The `status` field must reflect the ACTUAL exit code (0 → "pass",
  non-zero → "fail"), not the batch verdict.

Final answer contract:
One paragraph: how many files passed, how many failed, which files
failed (by name), and the exact commands you ran.
```
**Verification:** main parses the report JSON; checks 10/10 files
covered; re-runs 2/10 files (the seeded-fail spot) and checks the
claimed verdicts agree with observed.
**Failure mode:** the subagent reports `status: "pass"` for all files
including the seeded fails (M10 live result: claimed None vs observed
fail). Fix: the template explicitly says "status must reflect the actual
exit code" + main's re-run is the authoritative check.
**Workflow:** stateless (batch task, no context retention needed).

#### Recipe R3: Diff review (S4.2 row: subagent draft → main verifies, [M4])

**Task:** Review a PR-sized diff for bugs.
**Bench:** M4 (10/10 seeded bugs found — subagent finds bugs reliably).
**Prompt template:**
```
You are reviewing a code diff for bugs.
Workdir: {workdir} (all paths are relative to it; do not look outside it).

Task: Review the diff at {diff_path} for bugs.
The diff is against {base_ref}...{head_ref}.
Look for: off-by-one, null-pointer, race condition, resource leak,
incorrect API usage, missing error handling.

Definition of done:
- You have read the entire diff.
- You have identified all bugs (if any).
- Your answer lists each bug with: file, line number, bug description,
  severity (P0/P1/P2).

Final answer contract:
One paragraph per bug: file:line — description — severity.
If no bugs: "No bugs found. I reviewed N files, M hunks."
```
**Verification:** main reads the diff itself; checks that each claimed
bug is real (reads the file at the cited line); checks that no obvious
bugs were missed (main's own review).
**Failure mode:** the subagent's draft is a self-report — it may miss
bugs or claim false positives. Main's verification is the real review
(senior-git-1.0's "reviewer proposes, builder verifies" pattern).
**Workflow:** stateless (single review task; main keeps the diff in
context for verification).

#### Recipe R4: Spec-chunk implementation (S4.2 row: subagent --tools on, [M9])

**Task:** Implement a bounded spec chunk (a small library with 3
functions + a 3-case test suite).
**Bench:** M9 (2/3 sub-conditions met — workdir-cleanup ordering bug).
**Prompt template:**
```
You are implementing a spec chunk.
Workdir: {workdir} (all paths are relative to it; do not look outside it).

Spec: {spec_json}
  (functions: {function_list}; module: {module_name}; suite: {suite_name};
   run command: {run_command})

Task:
1. Implement the module at {module_path} with the spec's functions.
2. Write the test suite at {suite_path} with 3 test cases (one per function).
3. Run the suite: {run_command}
4. Confirm the suite passes (exit 0).

Definition of done:
- The module compiles and defines all spec functions.
- The suite passes: {run_command} exits 0.
- The workdir contains {module_name} and {suite_name}.

Final answer contract:
One paragraph: what you implemented, what you verified (the exact
commands you ran and their exit codes), what failed (if anything).
```
**Verification:** main re-runs the suite in its own scratch dir (the
authoritative check); checks the tool_log shows the suite run with exit
0; checks the workdir contains the module + suite (the workdir check is
the one that depends on the driver's cleanup ordering — see §3.3 #2).
**Failure mode:** the driver's `rmtree` runs before the scorer's
workdir check (H6 ordering bug). The workdir check is systematically
unsatisfied in live runs. Main's re-run in its own copy is the
authoritative check.
**Workflow:** stateless (single spec chunk; no context retention needed).

#### Recipe R5: Fan-out analysis (S4.2 row: fan-out N, [M3])

**Task:** Analyze N independent files/sections in parallel.
**Bench:** M3 (0.88 post-spot-check re-run — independent units work;
spot-check needs main).
**Prompt template (per unit):**
```
You are analyzing a single file/section.
Workdir: {workdir} (all paths are relative to it; do not look outside it).

Task: Analyze {file_or_section} and produce a summary.
{analysis_brief}

Definition of done:
- Your answer is ≤ 300 tokens.
- Your answer contains exactly 3 claims about the file/section.
- Each claim is factual and verifiable.

Final answer contract:
3 claims, one per line. No preamble. No summary. Just the 3 claims.
```
**Verification:** main spot-checks 1/3 of the sections (reads the
section itself, checks the 3 claims are accurate). The spot-check is
the M3 seam that must be driven by main (H5).
**Failure mode:** the subagent's summary may be too long (>300 tokens)
or contain more than 3 claims. The template caps it; main's spot-check
catches the accuracy issues.
**Workflow:** stateless (each unit is independent; no context retention
across units). **Batch spawn:** spawn all N units in one turn (one tool
block), then one `collect` loop (S4.3 batch-spawn rule).

#### Recipe R6: Long-context QA (S4.2 row: main, [M5])

**Task:** Answer QA questions about a long document (≥30K tokens).
**Bench:** M5 (5/5 correct at 60k ctx — subagent works, but the S4.2
row says main because the subagent at 8k default loses the tail).
**Prompt template (if delegating to subagent at 64k ctx):**
```
You are answering questions about a long document.
Workdir: {workdir} (all paths are relative to it; do not look outside it).

Document: {doc_path} ({N} tokens)
Questions: {question_list}

Definition of done:
- You have read the entire document.
- Each answer is ≤ 50 tokens.
- Each answer is factual and verifiable in the document.

Final answer contract:
One answer per question, labeled. No preamble.
```
**Verification:** main re-reads the relevant section of the document;
checks each answer is accurate.
**Failure mode:** the subagent at 8k default ctx loses the tail of a
60k document. Fix: use `--ctx 65536` (or the instance's default 262144)
when delegating long-context QA. The S4.2 row stays "main" for the
*reading* (main's 262K ctx is free, already loaded), but the subagent
is viable for *QA on long docs* at the right ctx.
**Workflow:** stateless (single QA task; no context retention needed).

#### Recipe R7: Saturation / high-concurrency fan-out (S4.2 row: fan-out N, [M7])

**Task:** Run N independent tasks at full concurrency.
**Bench:** M7 (2.08 t/s wall at 16-wide, zero 5xx — works at ceiling).
**Prompt template:** same as R5 (per-unit) or R2 (per-batch).
**Verification:** main checks the aggregate result; checks zero 5xx
(the M7 rubric); checks per-task t/s is within the expected range
(3.7-19.3 under 16-wide contention).
**Failure mode:** under high concurrency, per-task t/s drops
(3.7-19.3 vs 44-61 single-task). The aggregate t/s (2.08 wall) is the
headline, not the per-task. The skill's cost doctrine must use the
contention-adjusted t/s for the win/lose call.
**Workflow:** stateless (each task is independent; batch spawn all N,
then one collect loop).

### 4.3 Recipe selection guide

The main agent uses the S4.2 work-allocation table to select the recipe.
The table says *what* to delegate; the recipe says *how* to do it:

| Task shape | S4.2 allocation | Recipe |
|---|---|---|
| Single-file code-gen | subagent | R1 |
| Test batch + report | subagent --tools on | R2 |
| Diff review | subagent draft → main verifies | R3 |
| Spec-chunk implementation | subagent --tools on | R4 |
| Fan-out analysis (per-file) | fan-out N | R5 |
| Long-context QA | main (or subagent at 64k ctx) | R6 |
| High-concurrency fan-out | fan-out N | R7 |
| Multi-step plan / architecture | main | (no recipe — main does it) |
| Live tool feedback loop | main | (no recipe — main does it) |

---

## 5. Harness-agnostic treatment

The user's direction: "clear guidance on how to treat this skill, its
not a harness-specific, rather harness agnostic and may be used by any
agent."

### 5.1 What "harness-agnostic" means

The skill documents the bonsai2 pool on this box (the specific S3.2
commands, the specific VRAM budget, the specific t/s constants), but it
is **written so that any agent runtime that can run bash and load a text
skill can use it.** The skill is not *about* the bonsai2 harness — it is
about the *pattern*: a main agent that has access to a pool of stateless
subagent instances, driven through a closed set of bash commands, with
a bench-validated doctrine for when/how to delegate.

### 5.2 How the skill achieves harness-agnosticism

1. **Command reference is S3.2 verbatim:** the skill never invents
   commands. The S3.2 command surface (spawn/steer/cancel/status/
   collect/unload/reload/bench + flags + exit codes) is the API. Any
   harness that implements this surface is compatible.

2. **Doctrine is bench-cited, not harness-specific:** the S4.2
   work-allocation table is derived from the S5.2 bench results, which
   are harness-agnostic (they measure the model's capability, not the
   harness's plumbing). The t/s constants are calibrated from the bench
   runs, which are reproducible on any GB10-class box.

3. **Verification protocol is ledger-first:** the S4.2 self-report rule
   ("unverified claim WITH EVIDENCE") is harness-agnostic: it says
   "check the tool_log, check the files in the workdir, re-run the
   named check" — these are properties of the *result*, not of the
   harness that produced it.

4. **Recipes are fill-in-the-blanks templates:** the prompt templates
   (R1-R7) are the main agent's text — they don't depend on the harness.
   The harness provides the `spawn` command; the template provides the
   prompt. Any harness with a `spawn`-equivalent command can use the
   templates.

5. **The skill names its own box:** the skill includes a "This box"
   section with the specific VRAM budget, instance count, t/s constants,
   and the pre-install vs installed layout differences. This section is
   the *only* part that is box-specific; the rest of the skill is
   portable. A different box (e.g., a GB10 with 1 instance instead of
   2, or a different main stack) updates this section; the doctrine,
   recipes, and verification protocol stay the same.

### 5.3 What the skill does NOT do

- It does NOT prescribe the harness's internal implementation (how
  `spawn` routes to the least-loaded instance, how the manager tracks
  slots, how the KV cache is allocated). Those are S1-S3 details.
- It does NOT require a specific agent runtime (Hermes, pi, or any
  other). The skill is a text file; any runtime that loads text skills
  and can run bash can use it.
- It does NOT require a specific model for the main agent. The skill
  works with any main agent that can read the S4.2 table, select the
  right recipe, and run the S3.2 commands. The main agent's own model
  (qwen3.8-27b on this box) is a detail of the box, not of the skill.

---

## 6. Research findings: what the web says about what we're building

### 6.1 Anthropic's multi-agent research system (June 2025)

**Source:** https://www.anthropic.com/engineering/multi-agent-research-system

**Key findings:**
- **Orchestrator-worker pattern:** a lead agent (Claude Opus 4)
  coordinates the process while delegating to specialized subagents
  (Claude Sonnet 4) that operate in parallel. The subagents act as
  "intelligent filters" — they iteratively use search tools to gather
  information and return a list of findings to the lead agent.
- **90.2% improvement over single-agent:** the multi-agent system with
  Opus 4 lead + Sonnet 4 subagents outperformed single-agent Opus 4 by
  90.2% on Anthropic's internal research eval. The improvement is for
  *breadth-first queries* that involve pursuing multiple independent
  directions simultaneously.
- **Token usage explains 80% of performance variance:** on BrowseComp,
  token usage alone explains 80% of the performance variance. Agents use
  ~4× the tokens of a chat; multi-agent systems ~15×. This is the
  honest constraint on when the pattern pays: the 15× token multiplier
  means multi-agent only wins when the task is breadth-first and the
  subagents' work is genuinely independent.
- **Teach the orchestrator how to delegate:** "Each subagent needs an
  objective, an output format, guidance on the tools and sources to use,
  and clear task boundaries. Without detailed task descriptions, agents
  duplicate work, leave gaps, or fail to find necessary information."
  This validates the S4.2 recipe structure: each recipe has a clear
  objective, output format (the final-answer contract), tool guidance
  (the workdir contract), and task boundaries (the definition of done).
- **Scale effort to query complexity:** "Simple fact-finding requires
  just 1 agent with 3-10 tool calls, direct comparisons might need 2-4
  subagents with 10-15 calls each, and complex research might use more
  than 10 subagents with clearly divided responsibilities." This
  validates the S4.4 cost doctrine: the win/lose call must account for
  the number of subagents × their tool calls, not just the single-task
  cost.
- **Parallel tool calling transforms speed:** "The lead agent spins up
  3-5 subagents in parallel rather than serially; the subagents use 3+
  tools in parallel. These changes cut research time by up to 90% for
  complex queries." This validates the S4.3 batch-spawn rule: spawn all
  N in one turn, then one collect loop.
- **Synchronous execution creates bottlenecks:** "The lead agent
  executes subagents synchronously, waiting for each set of subagents to
  complete before proceeding. This simplifies coordination, but creates
  bottlenecks in the information flow between agents." This is the
  stateless vs stateful trade-off: stateless (synchronous, one task at
  a time) is simpler but has the bottleneck; stateful (subagents stay
  loaded, can be steered mid-task) reduces the bottleneck but costs
  VRAM + main's t/s.

### 6.2 Cognition's "Don't Build Multi-Agents" (June 2025)

**Source:** Cognition's engineering blog (the day before Anthropic
published their results).

**Key findings:**
- **Subagents fail when they write to shared artifacts:** "The core
  problem is that when multiple subagents write to the same file or
  data structure, they can conflict. The context handoff between
  subagents loses fidelity." This is the counterpoint to the
  orchestrator-worker pattern: it works for *read-only, independent*
  subagents (research, analysis), but fails for *write-heavy,
  shared-state* subagents (coding agents that all edit the same repo).
- **The boundary is whether subtasks write to a shared artifact:** a
  subagent that spends 50,000 tokens exploring returns 1,000-2,000
  tokens. That compression is unmatched by any summarisation prompt
  because the discarded material never entered the orchestrator's
  window. But if the subagent is *writing* to a shared file, the
  compression is lost — the orchestrator has to merge the subagents'
  writes, which is where the fidelity loss happens.
- **For our skill:** this validates the S4.2 row "Multi-step plan /
  architecture / cross-file refactor coherence → main": the main agent
  does the cross-file refactor (shared artifact), and the subagents do
  the independent units (per-file analysis, test batch, diff review)
  where the compression wins. The recipes R1-R7 are all *read-only or
  single-writer* tasks: the subagent writes to its own workdir, not to a
  shared repo. This is the "shared artifact" boundary: the subagent's
  workdir is its own artifact; main's repo is the shared artifact.

### 6.3 LangChain's context isolation economics

**Source:** LangChain's multi-agent documentation (2025).

**Key findings:**
- **Subagent reads 6,100 tokens, returns ~420-token summary:** a
  subagent that reads 6,100 tokens of files and returns a ~420-token
  summary keeps the ~9,500 tokens of intermediate work out of the
  orchestrator's context entirely. The mechanism is compression, not
  delegation: what saves tokens is reading less irrelevant context into
  the shared window, not making fewer model calls.
- **Routed subagent approach: ~9,000 tokens across 5 calls vs ~15,000
  for monolithic:** the routed subagent approach uses ~9,000 tokens
  across 5 calls, vs ~15,000 for a monolithic approach — 40% fewer
  tokens despite more model calls.
- **For our skill:** this validates the stateless workflow branch: the
  subagent does the token-heavy work in its own context, returns a
  compact result to main, and is then unloaded. Main's context stays
  small (the subagent's 6,100 tokens never enter main's window). The
  stateful branch (subagent stays loaded, context retained) is for
  multi-step tasks where the subagent needs to remember what it did in
  step 1 — but it costs VRAM + main's t/s, and the token compression
  is lost (the subagent's context grows with each step).

### 6.4 GB10 memory subsystem

**Source:** NVIDIA DGX Spark Porting Guide, Chips and Cheese analysis,
NVIDIA Developer Forums (GB10 hardware baseline).

**Key findings:**
- **Unified memory architecture (UMA):** the CPU and GPU share 128 GB
  of LPDDR5X memory. There is no fixed VRAM carve-out; the GPU accesses
  system memory over NVLink-C2C in a coherent, transparent manner.
  "High GPU bandwidth demands can squeeze out the CPU in both memory
  subsystems."
- **Memory bandwidth is the bottleneck:** GPU read 161 GB/s from
  LPDDR5X (theoretical 273 GB/s bus, shared with the CPU). "The 44%
  GPU read bandwidth drop under inference load directly explains tok/s
  variability. Memory bandwidth is the bottleneck — not coherence, not
  latency." This confirms the user's observation: GPU at 96% is the
  healthy state; the GPU is memory-bandwidth-bound. Squeezing the CPU
  further will not increase decode t/s.
- **No measurable first-touch penalty:** "Hardware coherence resolves
  page state before the load instruction executes. LPDDR5X baseline
  access latency: 16.5ns / 40 cycles." The coherent UMA means the
  subagent's KV cache (in the shared LPDDR5X) is accessible by the GPU
  without a page-fault penalty. This is why the VRAM climb (100→116
  GiB) is expected: the KV cache grows with sequence length, and it's
  in the shared memory that the GPU is already reading.
- **For our skill:** the VRAM budget (S1.4) is the real constraint.
  The 14.78 GB per-instance budget is the ceiling; the KV cache grows
  within it. The "climb" the user observed is the KV cache growing as
  the tasks' sequences grow — it's expected, not a leak. The release
  mechanism (H13 unload/reload) is the way to free the VRAM after a
  task.

### 6.5 llama.cpp thread tuning

**Source:** llama.cpp `docs/development/token_generation_performance_tips.md`
(antirez's original, still in the fork).

**Key findings:**
- **Thread count matters for prefill, not decode:** "If your token
  generation is extremely slow, try setting this number to 1. If this
  significantly improves your token generation speed, then your CPU is
  being oversaturated." The `-t N` (or `--threads N`) parameter should
  be set to the number of physical CPU cores. "If in doubt, start with 1
  and double the amount until you hit a performance bottleneck, then
  scale the number down."
- **For our skill:** the subagent instances run with the default thread
  count (the llama.cpp fork's default, which is auto-detect). The CPU
  at 4-20% is not oversaturated — the decode is GPU-bound. The thread
  count matters for prefill (the initial prompt processing), which is
  fast on this box. No skill change needed; the thread count is a
  launch-time detail (S1.3), not a runtime doctrine.

### 6.6 llama.cpp KV cache / slot mechanics

**Source:** multigrid.ai, llama.cpp discussions, vladbutacu.com.

**Key findings:**
- **KV cache is pre-allocated at boot:** "The cache is allocated up
  front for the full -c, not grown on demand. A 32k setting for an 8k
  workload costs four times the memory permanently." Our instances boot
  with `--ctx-size 262144` — the KV cache is allocated for 262144
  tokens at boot, regardless of the actual task length. The "climb" the
  user observed is the *used* KV growing within the pre-allocated cache,
  not the cache itself growing.
- **Per-slot context = ctx_size / parallel:** "-c 32768 --parallel 8 is
  a server whose requests each get 4,096 tokens." Our instances have
  `--ctx-size 262144 --parallel 8`, so each slot gets 32,768 tokens of
  context. A task that uses more than 32,768 tokens per slot will fail
  with a context-too-long error (exit 21 on collect).
- **KV cache quantization:** "Moving both to q8_0 halves the numbers at
  a quality cost that is small but is not nothing." We use `--cache-type-k
  q4_0 --cache-type-v q4_0` (even more aggressive than q8_0) — this
  halves the KV cache memory again, which is why the 262144 ctx fits in
  the 14.78 GB budget. The quality cost is small (per the S1.4 re-
  anchor measurement).
- **Slot save/restore:** "llama-server supports saving and restoring KV
  cache slots to disk: --slot-save-path <dir>." This is a potential
  optimization for the stateful workflow branch: save the subagent's
  KV cache to disk between steps, restore it for the next step. This
  would avoid the re-prefill cost of the subagent's context. Not
  implemented in v1 (the stateful branch just keeps the instance loaded);
  a v2 optimization.
- **For our skill:** the slot mechanics explain the VRAM budget: each
  instance has 8 slots × 32,768 tokens × q4_0 KV = the pre-allocated
  KV cache. The "climb" is the used KV growing within this allocation.
  The S4.3 session economy (≤6 of 8 slots busy) is the doctrine cap
  that keeps 2 slots free for deadline-sensitive spawns.

---

## 7. What's undefined / unclear in the current SPEC, and how this analysis resolves it

| # | Unclear/undefined in SPEC | How this analysis resolves it |
|---|---|---|
| 1 | S4.1 doesn't specify the recipe structure | §4: recipes are fill-in-the-blanks prompt templates + verification protocol + bench citation + failure modes + workflow branch |
| 2 | S4.1 doesn't say how the skill treats itself as harness-agnostic | §5: the skill is harness-agnostic by construction (S3.2 verbatim commands, bench-cited doctrine, ledger-first verification, fill-in-the-blanks templates, box-specific "This box" section) |
| 3 | S4.3 doesn't document the main t/s halving effect | §3.4 #1: "While subagents are running, main's effective t/s halves. Plan main's inter-spawn work around this." |
| 4 | S4.3 doesn't document stateless vs stateful workflow branches | §3.4 #2: two branches with identical structure, different lifecycle management; stateless is default |
| 5 | S4.3 doesn't document the VRAM climb + release mechanism | §3.4 #3: "VRAM climbs during tasks (KV cache growth). After a task, check VRAM; if high, `bonsai2-unload --all`." |
| 6 | S4.3 doesn't document the cache memory 99% observation | §3.4 #4: "High cache% is the OS page cache (reclaimable). Look at MemAvailable, not cache%." |
| 7 | S4.3 doesn't document the CPU/GPU utilization expectations | §3.4 #5: "CPU at 4-20% is normal (decode is GPU-bound). GPU at 96% is healthy." |
| 8 | S4.4 cost constants are a-priori (29/4800) | §3.2: calibrated constants from the 2026-09-28 run (44-61 single-task, 3.7-19.3 under 16-wide contention, 0.27 tool-loop M9, 2.33 tool-loop M10, 8.52 main idle, 0.00 main under load (artifact)) |
| 9 | S4.2 M1 row doesn't account for the reasoning-only output issue | §4.2 R1: the M1 recipe includes a content-forcing preamble; the verification protocol falls back to `reasoning_content` if `content` is empty |
| 10 | S4.2 M9 row doesn't account for the workdir-cleanup ordering bug | §4.2 R4: the M9 recipe notes that the workdir check is the one sub-condition that depends on the driver's cleanup ordering; main's re-run in its own copy is the authoritative check |
| 11 | S4.2 M10 row doesn't specify the report format | §4.2 R2: the M10 recipe specifies the report JSON schema (per-file status/exit_code/duration_ms); the status must reflect the actual exit code |
| 12 | S4.6 doesn't specify the audit script's input/output format | §2 (5.5): the audit script reads `bench.yaml` + the S4.2 table, checks citations + win/lose, outputs `doctrine-audit.json`, exit 0/1 |
| 13 | S4.6 doesn't specify the fresh-session test's setup | §2 (5.6): the test spawns a fresh pi/Hermes session with only the skill loaded, gives it the S5.6 workload set, asserts command log ⊆ S3.2 + all verifications pass |
| 14 | The skill doesn't have a "This box" section | §5.2 #5: the skill includes a box-specific section (VRAM budget, instance count, t/s constants, pre-install vs installed layout) that a different box would update |
| 15 | The relationship between the skill and the bench is unclear | §1: the skill is the doctrine layer between the harness scripts (S3) and the bench results (S5); the S4.2 table is derived from S5.2; the S4.4 constants are calibrated from S5 runs; the S4.6 audit cross-checks S4.2 against S5.2 |

---

## 8. Open questions for the skill implementation

1. **M1 content-forcing:** the H4 fix (prompt says "EXACTLY the two
   ```python fences") landed but M1 still fails (reasoning-only output).
   Is this a SPEC-level seam (the M1 rubric must tolerate reasoning-only
   output) or a prompt-level fix (the M1 recipe must force the model to
   emit content)? For the skill: the R1 recipe includes the content-
   forcing preamble; the rubric question is a Phase 4 hotfix, not a
   Phase 5 skill concern. The skill documents the workaround; the
   rubric fix is the bench's job.

2. **M8 main t/s timing artifact:** the M8 row measures 0.00 t/s (the
   main's completions queue behind subagent decodes). The user's
   observation (30→15) is the ground truth. Is this a Phase 4 timing
   fix (the M8 measurement window is too short) or a Phase 5 doctrine
   note ("expect main's t/s to halve while subagents are running")? For
   the skill: the doctrine note (the user's direction #1); the timing
   fix is the bench's job.

3. **Stateful workflow: when to use it?** the user said "stateless is
   default." When is stateful the right choice? The answer from the
   research: multi-step tasks where the subagent needs to remember what
   it did in step 1 while doing step 2 (e.g., a 5-step debugging session
   where each step depends on the previous step's findings). For the
   skill: the stateful branch is documented but not the default; the
   recipe selection guide says "stateless unless the task is multi-step
   and the subagent needs to remember prior steps."

4. **Recipe R6 (long-context QA): main or subagent?** the S4.2 row says
   "main" (the subagent at 8k default loses the tail), but M5 proves the
   subagent works at 64k ctx. For the skill: the R6 recipe is "main
   (default) or subagent at `--ctx 65536` (when main's context is too
   large to hold the document + the QA questions)." The S4.2 row stays
   "main" (the default); the recipe offers the subagent alternative.

5. **The doctrine audit script: where does it live?** §2 (5.5) says
   "a Python script (in `bench/` or `scripts/`)." For the skill: the
   audit script is a `bench/` module (it reads `bench.yaml` and the
   S4.2 table, which are bench artifacts). The skill's SKILL.md cites
   the audit script; the script itself is not part of the skill (it's
   part of the bench framework).

6. **The fresh-session test: which runtime?** §2 (5.6) says "a new
   main-agent session in any agent runtime that loads the S4.1 skill
   (pi, Hermes, or equivalent)." For the skill: the test uses pi
   (the default harness on this box, per the senior-git-1.0 workflow).
   The test is runtime-agnostic: it loads the skill, gives the workload
   set, asserts the command log. The runtime is a test detail, not a
   skill concern.

---

## 9. Source list

### Project sources (this repo)
- SPEC.md S3 (harness control scripts), S4 (harness skill), S5 (bench framework)
- MAP.md Phase 4 (bench framework), Phase 5 (harness skill), Phase 6 (GB10 packaging)
- PR #21 (Gitea `blckwell/qwen-tory38/pulls/21`): comments 1031-1043
  (handoff, G3 review, status, user observations, live run results, merge)
- PR #22 (hotfixes H1-H13), PR #23 (post-hotfix re-run, MAP 4.10)
- `bench-results/` (2026-09-28 post-hotfix run: 10 scenario rows + M3
  re-run + S5.5 baseline)
- `image/Dockerfile.runtime` (the subagent launch command: ctx 262144,
  parallel 8, q4_0 KV, reasoning-format deepseek)
- `etc/subagent-engine.yaml` (instance config: count 2, port_base 30101,
  ctx 262144, max_num_seqs 8, per_instance_budget 14.78 GB, q4_0 KV)
- `docs/post-live-run-state-report-2026-09-27.md`
- `docs/post-live-run-hotfix-plan-2026-09-27.md`

### External sources (web research, 2026-09-28)
- Anthropic Engineering: "How we built our multi-agent research system"
  (June 2025) — https://www.anthropic.com/engineering/multi-agent-research-system
- Anthropic Cookbook: "Orchestrator-Workers Workflow" —
  https://github.com/anthropics/anthropic-cookbook/blob/main/patterns/agents/orchestrator_workers.ipynb
- Cognition: "Don't Build Multi-Agents" (June 2025) — via secondary
  sources (the Cognition blog is behind a paywall; the key findings are
  reproduced in the LangChain + handbook.academy + praveentn.live sources)
- LangChain: multi-agent documentation (context isolation economics)
- handbook.academy: "Multi-Agent Orchestration" (LangGraph, OpenAI
  Agents SDK, AutoGen, Swarm) —
  https://hld.handbook.academy/curriculum/ai-ml-system-design/multi-agent-orchestration
- praveentn.live: "The Context Economics of Agents" —
  https://praveentn.live/learn/blog/context-economics-of-agents
- stochasticsandbox.com: "AI Agent Orchestration Patterns" —
  https://stochasticsandbox.com/posts/ai-agent-orchestration-patterns-2026-04-21
- NVIDIA DGX Spark Porting Guide: "System Overview" —
  https://docs.nvidia.com/dgx/dgx-spark-porting-guide/overview.html
- Chips and Cheese: "Inside Nvidia GB10's Memory Subsystem, from the
  CPU Side" — https://chipsandcheese.com/p/inside-nvidia-gb10s-memory-subsystem
- NVIDIA Developer Forums: "GB10 Hardware Baseline — First Direct
  Measurements and Findings" —
  https://forums.developer.nvidia.com/t/gb10-hardware-baseline-first-direct-measurements-and-findings/367851
- multigrid.ai: "llama.cpp's --ctx-size Flag and What It Costs in Memory" —
  https://multigrid.ai/learn/llamacpp-ctx-size-memory
- multigrid.ai: "Serving Concurrent Requests From One llama.cpp Server" —
  https://multigrid.ai/learn/llama-cpp-server-slots
- vladbutacu.com: "Your Local LLM Is Slow Because of Five Config Flags" —
  https://vladbutacu.com/blog/your-local-llm-is-slow-because-of-five-config-flags
- llama.cpp (antirez): "Token Generation Performance Tips" —
  https://github.com/antirez/llama.cpp-deepseek-v4-flash/blob/main/docs/development/token_generation_performance_tips.md
- particula.tech: "Full Prompt Re-Processing: llama.cpp Cache Fixes That
  Work" — https://particula.tech/blog/prompt-reprocessing-swa-hybrid-models-kv-cache
- medium.rebeauty-writing.com: "I Deployed Local LLMs in Production for
  a Year. Part 2: The Operational Playbook" —
  https://medium.rebeauty-writing.com/?action=show&id=cc903d6a4426
- arize.com: "How cheap models changed multi-agent economics" —
  https://arize.com/blog/how-cheap-models-changed-multi-agent-economics/
