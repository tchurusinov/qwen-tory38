# qwen-tory38 — Coherence Report, Phases 0-2

Date: 2026-09-24. Scope: everything merged to `main` through PR #11
(MAP 0.1-2.11). Question this answers: what did phases 0-2 actually build,
does it meet the spec, does it solve the problem the project set out to
solve, why was phase 2 a loop, and what to fix now for maximum payoff
going into phases 3-7.

Every claim below is grounded in the current tree, the Gitea PR record,
and the live @gpu test logs. Numbers are as of this commit (`1e40dc7`).

---

## 1. High-level architecture

The system is a two-model stack on one GB10 box. The main agent (qwen38
27b, SGLang, :30001) is external: consumed, never modified. This project
owns only the subagent side.

```
  MAIN AGENT (external, SGLang :30001)
  qwen38-27b NVFP4  ~76.4 GB fresh load
        |
        |  deterministic bash only (S3.2) + bonsai2-harness skill (S4)
        |  [Phase 3 commands, Phase 5 skill: NOT BUILT YET]
        v
  +---------------------------------------------+
  | bonsai2-sglang.service  (systemd, Type=notify)|
  |   bin/bonsai2-manager  (2532 lines, Python)   |  <- PHASE 2, BUILT
  +---------------------------------------------+
        |  reads subagent-engine.yaml + serving.yaml
        |  runs S2.3 planning tier + S1.4 allocation tier
        |  spawns in order, watches health, drift, control queue
        |
        |  systemctl start bonsai2-server@i
        v
  +-------------------+  +-------------------+   up to N
  | bonsai2-server@0  |  | bonsai2-server@i  |
  | docker, host-net   |  | (one container    |
  | --memory 14.78g    |  |  per instance)    |
  | :30101            |  | :30101+i          |
  | llama-server       |  +-------------------+
  | (bonsai2-server:1.0
  |  image, Phase 1)    |
  | Ternary-Bonsai-2-27B |
  +-------------------+
        |  writes /run/bonsai2/health.json (S3.1 schema)
        v
  PHASE 3: bonsai2-spawn / -status / -collect / -unload ...
  PHASE 4: bench/run.py (M1-M8)  <- the reason this exists
```

What exists and what is debt:

| phase | deliverable | state |
|---|---|---|
| 0 | layout, test runner, toolchain, pre-commit | done, green |
| 1 | engine pin, build pipeline, model fetch, runtime image, VRAM formula, bring-up + load + OOM + port + kill @gpu tests | done, green (17/17 of its @gpu) |
| 2 | manager: skeleton, boot tier, allocation tier, units, spawn, escalation, N=0/PTQ1_0 fallback, drift watch, measured-boot writeback, control queue, S2.8 acceptance @gpu test | code done, hermetic green; @gpu acceptance **17/19** (see 4) |
| 3 | S3 control scripts: the bash commands the main agent calls | not started |
| 4 | bench framework M1-M8 | not started |
| 5 | harness doctrine skill | not started |
| 6 | one-command install.sh | not started |
| 7 | global validation, 72 h soak | not started |

The manager is the brain's reflexes: it makes N subagent containers exist
and stay up. It is not the hands (S3) and it is not the reason (S4/S5).

---

## 2. What was built, and does it adhere to the spec

Phase 0. Repo layout mirroring `/opt/bonsai2/` 1:1, a hermetic unittest
runner, toolchain verified (aarch64, driver, CUDA image), and a pre-commit
tripwire. 14 review rounds. Clean. This is the boring part done right.

Phase 1. The pinned fork, the build pipeline, model download + SHA256, the
runtime image (digest-pinned, both quants baked), the `subagent-engine.yaml`
writer/validator, and the S1.4 VRAM formula as a pure module
(`bin/bonsai2_vram.py`). The pure-formula module is the cleanest code in the
repo: total, no I/O, testable against the spec worked examples. Then five
@gpu edge tests: bring-up, sustained load, OOM ceiling, port collision,
kill-9 restart. These pass.

Spec adherence for S1: high. The one live correction that mattered was the
S1.4 re-anchor (constant moved from the FP16-era 0.00000176 to the measured
q4_0 0.000003277; budget re-derived to 14.78 GB; N=2 instead of the
original N=3). That was recorded as a delta, not silently patched.

Phase 2. The manager. This is where 90% of the lines and all of the pain
are. The code implements the spec formulas verbatim:

- S2.3 planning tier: `N' = max(0, min(N, floor(free / budget)))`, with
  `free` read from the main container's live cgroup `memory.current`
  (sleep-accurate), else the recorded footprint.
- S1.4 allocation tier: the stricter `free >= max(footprint + 1.0, cap)`
  gate, run before every spawn.
- S2.5 ordered spawn: start instance i, wait healthy, then i+1. One shared
  whole-boot deadline caps the entire sequence at `boot_target_s` (not
  N x it).
- S2.2 escalation: two consecutive probe fails -> `degraded`; five -> a
  `systemctl restart` backstop that defers to a unit systemd already
  restarted.
- S2.4 N=0 / main-only: PTQ1_0 fallback retry, then ctx-halve retry;
  `main_only_reason` of `budget_zero` / `user_unloaded` / `ERR_VRAM_OOM`.
- S2.5 drift watch: 5-minute recheck, cgroup pressure sustained > 30 s over
  the cap -> halve ctx + in-place restart.
- S2.6: measured boot written back to `serving.yaml`.
- S3.2 control queue: unload/reload request files -> ack files.

Spec adherence for S2: high in the code, not yet high in the acceptance run.
The ledger has 2.1-2.11 all checked, but the S2.8 acceptance is the phase's
exit criterion and the @gpu run is 17/19. The two red are #2 (crash recovery
witnessing `degraded`) and #4 (drift). The checkbox is flipped in the ledger;
the green run is a debt the MAP session log explicitly carries forward.

Does it solve the problem it set out to solve? The problem (SPEC S0) is: use
the VRAM the main stack leaves behind to run N cheap ternary subagents, driven
deterministically by the main agent through bash, and measured so we know where
they actually win. Phases 0-2 built the engine, the image, and the lifecycle
brain. So: subagents can now exist and stay up under a real VRAM budget. That
is the hard infrastructure half. But the two things that make it a *system*
rather than a *daemon* are not built:

- S3 (the commands the main agent calls to dispatch and collect) is not there.
- S5 (the bench that proves the subagents beat main on some tasks) is not there.

You cannot yet claim "the subagents help" because there is no dispatch surface
and no measurement. The problem is half-solved: the *can we run them* question
is answered; the *should we / does it win* question is untouched.

---

## 3. What is going swimmingly

- The docs are real. SPEC is locked, MAP is a resumable ledger with a
  session log a fresh agent can pick up cold, ARTIFACTS tracks everything
  outside the repo. The spec-follows-code rule worked: every box pin-reality
  (docker g = GiB, MemAvailable as the pool, 121.6 vs 127.54 GB, the 401
  auth wall on the main endpoint, the cgroup path shape) got a recorded
  delta, not a silent patch.
- The pure VRAM formula module. Total, no I/O, pinned to the spec examples.
  This is the right shape and it is the cleanest file in the project.
- The hermetic suite. 216/216 green, 5.4 s, no GPU, no network. The split
  between hermetic (`tests/`) and @gpu (`tests/gpu/`) is enforced and holds.
- The two-tier VRAM gate (planning tier at boot, allocation tier per spawn)
  is the correct design and it is implemented as the spec says.
- The manager is structurally sound. The escalation, the shared boot
  deadline, the N=0 fallback, the drift watch, and the control queue are all
  the right mechanisms. A new agent can read it and understand the intent.

---

## 4. What is going wrong

This is the section that matters.

4.1 The @gpu acceptance is not green, and it is the phase's exit criterion.
Latest run (`test-logs/gpu-20260923-230632.log`, 472 s): `Ran 19 tests,
FAILED (failures=1, errors=1)`. The progression across the session was
6/19 -> 13/19 -> 15/19 -> 17/19. Two tests remain red, both in
`test_lifecycle_2_11.py`:

- #2 crash recovery: after in-container `kill -9`, the sequence stayed
  `['healthy']` for 120 s. The unit's own `Restart=on-failure` + `RestartSec=5`
  recovers the engine in ~10-15 s, but the manager's degraded rule needs
  two consecutive fails at the 10 s cadence (~20 s of down time). The restart
  finishes between probe cycles, so `degraded` is never witnessed. Fix shape
  identified in the session log: hold the port with a listener from before the
  kill until the degraded witness. Three iterations in, not yet settled live.
- #4 drift: simulating sustained cgroup pressure. `memory.current` is not
  writable as uid 1000 on this box, so the test writes a ~13.7 GB pin file to
  the container rootfs with a detached `dd`, then polls the cgroup while the
  pin is held. Three failure modes hit in sequence: shell-copy amplification
  (OOM-kill), the target exceeding the kernel's binary-GiB cap (OOM-kill),
  and a blocking `docker exec` that only returned after the `rm`. The author
  records "I DON'T KNOW" whether the dd write time + 6 s recheck cadence +
  30 s sustained clock fit the 90 s poll window with margin.

4.2 The @gpu suite is a slow, global-state machine. It takes ~8 minutes for
19 tests and every file in `tests/gpu/` shares the same two instance ports
(30101/30102) and the same tracked `etc/*.yaml`. TIME_WAIT left by file N is
file N+1's precondition failure. The S2.6/S2.4 write-backs mutated the tracked
templates and leaked `boot_target_s: 11` into the next run. You cannot isolate
one @gpu failure because the suite is not hermetic and its teardown is shared
state. This is the single biggest reason phase 2 felt hellish: every code fix
needed a ~12-minute round trip, and a fix in one file could break another.

4.3 The manager is a 2532-line extensionless Python file that the gate does
not lint. It is Python (shebang, `from __future__ import annotations`,
an `_InstanceState` dataclass), but named `bonsai2-manager` with no `.py`
suffix, so it falls outside the gate's `*.py` glob. The MAP admits it:
"the manager is extensionless, outside the gate's `*.py` glob, linted
manually." 2532 lines of unlinted Python means the LLM reviewer is the only
lint. That is how the `run_probe_loop` bug got through: the signature has a
keyword-only `log` after `settle_s`, but three call sites passed `_log`
positionally. Hermetic tests mocked `run_probe_loop`, so the `TypeError`
only fired live in @gpu. One real bug, and it killed the manager after every
boot, causing four of the five @gpu failures in the first full run.

4.4 Six regex "surgical rewrite" functions that the code owns but did not
author. `record_quant_in_use`, `record_ctx_in_use`, `rewrite_unit_ctx`,
`rewrite_unit_model`, `rewrite_unit_memory`, and `write_boot_target_s` each:
read a file, `splitlines`, regex-match a key, mutate one line, `os.replace`.
They duplicate the same shape six times, and they parse YAML and systemd unit
files by regex. The smell is the `ctx_size:\s?` special case added in G3 PR#11
r1: it had to match `ctx_size:262144` with no space after the colon because
that is valid YAML and the replace path would otherwise fall through to a
duplicate-key insert. That edge exists only because the code parses YAML by
regex. The root cause is that install.sh (phase 6, not built) owns unit
rendering, so the manager must hand-patch files it did not write.

4.5 The review loop has no P2 stopping condition. `review.sh` runs pi on the
same SGLang model, read-only, it can read the diff and docs but cannot run
code or edit files. It proposes findings; the builder dispositions each. The
DOD stop condition is "0 P0, no open P1." P2s (docstring precision, a log
marker printing the wrong inequality, a naming nit, a vestigial param) have no
ceiling, so a rigorous reviewer finds a new one every round. PR #10 ran 16
rounds. PR #2 (phase 0) ran 14. The DOD + spec-amendment + uncertainty
rules were added mid-phase and they helped (clear stop, coherent spec), but
they did not bound P2s. Each round also now writes DOD, spec amendments,
uncertainties, checks, and residual risk into the PR body, which raises the
per-round cost.

4.6 The process velocity. The code did not deteriorate; 216/216 hermetic holds
and the manager is structurally sound. What deteriorated was the @gpu harness
around it: a real code bug (`run_probe_loop`), a missing module constant
(`KV_PER_SLOT_PER_TOKEN_GB`), shared-port TIME_WAIT, and template mutation.
The loop felt endless because (a) the reviewer is adversarial and unbounded on
P2s, (b) @gpu is slow and stateful so every fix is a 12-minute gamble, and
(c) the spec kept meeting the box's pin-reality, each forcing a recorded delta
and a test pin.

---

## 5. Why phase 2 was the hellish one (the direct answer)

Not new guidance, not the agent's logic. Three compounding causes:

1. The target of the loop was the @gpu acceptance, not the code. The hermetic
   surface (216 tests) was green early. The @gpu surface is a live, slow,
   shared-state machine where each test's teardown bleeds into the next and
   each fix costs ~12 minutes. Phase 1's @gpu tests were mostly single-shot
   (bring-up, one load, one OOM). Phase 2's `test_lifecycle_2_11` is the
   acceptance test: it must drive the manager through cold boot, a crash, an
   N=0 clamp, a drift cycle, and an unload/reload cycle, in sequence, against
   the same two ports and the same two tracked YAMLs. That is where the
   global-state pain concentrates.

2. The code had one real bug that only @gpu could see. `run_probe_loop` was
   called with a positional `log` against a keyword-only parameter. Hermetic
   tests mocked the loop, so the bug was invisible until the manager booted
   live and died. When it died, four of the five @gpu tests failed for the
   same underlying reason. That single bug plus the missing `KV_PER_SLOT_PER_TOKEN_GB`
   constant drove the 6/19 -> 13/19 jump.

3. The spec-vs-box surface was large in phase 2 and not in 0-1. The manager
   touches the main container's cgroup, the host's MemAvailable/MemTotal,
   systemd user-scope semantics, docker's GiB parse, and the main endpoint's
   401 auth wall. Each of those is a pin-reality delta that needs a recorded
   spec amendment and a test pin, and each one is a place the isolated
   reviewer can find a P2. Phase 0 and 1 were "straightforward" because they
   touched the build, the fetch, and the image: few external surfaces. Phase
   2 is where the code meets the OS, and that is where the loop bites.

The new guidance (DOD section, spec-amendment rule, the fixed five-subsection
uncertainty section, the VRAM audit rule) did not cause the loop. It reduced
the cost of *stopping* and kept the spec coherent. The loop was driven by
the adversarial P2 reviewer plus the @gpu harness fragility.

---

## 6. How to improve the remaining phases qualitatively

Phases 3-7 are where the real complexity lives: S3 is routing + slot model +
deadline enforcement + idempotency + a 12-code closed set + a mock engine; S5
is the runner + scorers + goldens + a live M1-M8; S6 is the installer that
glues it together. To not repeat the phase 2 pain:

- Build the S3 mock engine fixture first (MAP 3.10), before the commands.
  Phase 2 learned that a manager with no test double forces live @gpu. The
  S3 commands need a canned `/v1/models` + `/v1/chat/completions` server and
  a manager stub so the exit-code closed set is testable hermetically, not
  only @gpu. Get the 12-code mapping green hermetically before touching a
  live instance.
- Make @gpu a base class before writing S3 @gpu tests. A shared `@gpu`
  unittest.TestCase that does port TIME_WAIT cleanup (SO_REUSEADDR + bounded
  wait) and snapshots/restores the tracked `etc/*.yaml` in
  `setUp`/`tearDown` kills the cross-file bleed. Phase 2 did this ad hoc in
  `test_lifecycle_2_11`; promote it to the base so every @gpu test gets it.
- Re-anchor tests to live box values, not baked constants. The N=0 test
  baked `121.6 GB`; the box is `127.54 GB`. Compute the footprint from live
  `/proc/meminfo` at run time. Any test that bakes a box-size constant will
  rot on a re-image.
- Keep the spec-first discipline but batch the pin-reality deltas. Phase 2
  recorded each delta in its own round. For S3, probe the main endpoint and
  the cgroup shapes up front, record them in one spec-amendment pass, and
  pin them with hermetic tests so the reviewer is not re-discovering box
  reality for the first time in round 4.

---

## 7. Overly-complex, nonsensical, or plain-stupid code decisions

Ranked by payoff of the fix.

1. Six regex surgical rewrites that parse YAML and unit files by hand
   (manager lines 624-707, 1026-1147, 1889). They duplicate a shape six
   times and each carries a regex edge case (the `ctx_size:\s?` no-space
   special case). Fix: extract one `_patch_first_line(path, pattern,
   new_line, label)` helper and delete five of the six. Bigger fix, phase 6:
   have install.sh own unit rendering so the manager re-renders a unit it
   can freely rewrite instead of regex-patching a template it did not write.
   This is the biggest structural smell in the codebase.

2. The @gpu #4 drift test simulates memory pressure by `dd`ing a ~13.7 GB
   pin file onto the container rootfs, detached, then polling. It has three
   known failure modes and the author records uncertainty about whether the
   timing window even works. The spec rule is "cgroup `memory.current` > cap
   sustained > 30 s." The clean simulation is a `docker exec` into the
   instance that pins >cap of RAM and holds it for 40 s; the cgroup charges
   that real allocation. That removes the file, the detached process, the
   blocking-exec race, and the OOM-kill risk. Cheaper to write than the dd
   version and far more faithful to what S2.5 actually watches.

3. `bonsai2-manager` is a 2532-line extensionless Python file escaping the
   gate's lint. Fix: rename to `bonsai2-manager.py` (or move the core to
   `bin/bonsai2/` as a package with a thin launcher) and add it to the
   gate's `*.py` glob. The cost is a one-line path update; the payoff is that
   the next `run_probe_loop` positional-arg class of bug is caught by ruff
   and the structure pressure (fewer cross-cutting positional params) is real.

4. Two poll cadences with one constant name: `HEALTH_PROBE_POLL_S = 2.0`
   (the boot wait in `wait_instance_healthy`) vs `health_poll_s = 10` (the
   steady loop, from `serving.yaml`). The S2.2 escalation is measured in the
   10 s cadence, the boot wait uses 2 s. A reader has to trace to
   `run_probe_loop` vs `wait_instance_healthy` to know which "probe cadence"
   the degraded window is measured against. This ambiguity is what made @gpu
   #2 hard to reason about. Fix: one named constant per cadence, and add a
   `BONSAI2_HEALTH_POLL_S` env hook so @gpu can test escalation at a faster
   cadence instead of needing a 20 s port-holder window.

5. The in-pass VRAM charge is threaded by hand in two places: `pre_charge_gb`
   in `allocation_tier` and `in_pass_charge_gb` in `process_control`'s reload
   loop. It is a real correctness need (do not over-grant on a tight pool),
   but it is state maintained by hand instead of a single "available pool"
   object that decrements on each grant. It will be a bug in phase 3 reload
   with N > 1. Fix: one pool object the tier and the reload loop both draw
   from.

None of these are wrong today; they are each a place the next phase will hit
harder if left.

---

## 8. Most critical follow-up steps, ranked

Do these before starting phase 3. They are the highest-leverage work now.

1. Green the two red @gpu tests so S2.8 acceptance is actually met. This is
   owed by the DOD #4 in the open session log.
   - #2: make the port holder own 30101 from before the `kill -9` until the
     `degraded` witness is recorded on `health.json` at the 0.2 s poll. Then
     release and measure the reload half. (Already iterated to this shape;
     needs the live run to settle it.)
   - #4: replace the dd-rootfs-pin with a `docker exec` real-memory pin that
     holds >cap of RAM for 40 s and asserts the cgroup `memory.current`
     sustains over the cap between the manager's 6 s rechecks.
   - Re-run the full `tests/run_gpu.sh` to 19/19, then close DOD #4.
2. Add `BONSAI2_HEALTH_POLL_S` and use it in `run_probe_loop`. This makes the
   escalation testable at a fast cadence and removes the 20 s port-holder
   fragility in #2 for good.
3. Extract the six regex rewrites to a shared helper (or re-render). Cut the
   `ctx_size:\s?` class of edge at the source.
4. Rename the manager to a `.py`-globbed file (or package) so the gate lints
   it. Add `run_probe_loop` to a keyword-arg discipline or shrink its
   positional surface.
5. Promote the @gpu base class (port TIME_WAIT cleanup + YAML snapshot/
   restore) out of `test_lifecycle_2_11` so the next @gpu suite does not
   re-learn the bleed.

After those, phase 3 starts on a clean @gpu base and a linted core, which is
the difference between a phase 3 that takes 8 rounds and one that takes 16.

---

## 9. Verdict and grade

The foundations are strong; the top of the stack is not yet proven.

What is going swimmingly: spec discipline (the spec-follows-code deltas are
recorded, not silently patched), the pure VRAM formula module, the 216-test
hermetic suite, the two-tier VRAM gate, and the docs. A fresh agent can read
SPEC + MAP + ARTIFACTS and resume cold. That is the part of this project that
is genuinely good and that will carry through every remaining phase.

What is going wrong: the @gpu harness (slow, stateful, not hermetic), the
unlinted 2532-line extensionless core, the six regex rewrites, and the
adversarial reviewer with no P2 ceiling. Phase 2 is functionally done but its
exit criterion (S2.8 acceptance) is 17/19, not 19/19.

Does it solve the problem? Half. The hard infrastructure half is built: N
ternary subagents can exist and stay up under a real VRAM budget. The two
halves that make it a system rather than a daemon (S3 commands, S5 bench) are
not built, so the "does it win" question is still open. You cannot claim the
project is useful until S5 produces a number.

Grades (A- = strong, B+ = solid with known gaps, B- = workable but costly):

| dimension | grade | note |
|---|---|---|
| spec adherence (S1, S2) | A- | formulas verbatim, deltas recorded; S2.8 not fully green |
| code quality | B+ | correct + tested; unlinted core + regex rewrites + harness fragility |
| process efficiency | B- | rigorous but adversarial P2 reviewer + slow @gpu = 16 rounds on the skeleton |
| does it solve the problem | B | lifecycle brain in; hands + reason not built; "does it win" unmeasured |

Overall: B+, about 82%. It is going swimmingly on coherence and spec
discipline and going poorly on @gpu robustness and review-loop velocity. The
single biggest risk going forward is that phase 3 (S3: routing, slots,
deadlines, idempotency, 12-code closed set, mock engine) hits the same @gpu
pain at roughly 3x the surface. Fixing the five items in section 8 now is what
keeps phase 3 from becoming a longer, uglier version of phase 2.
