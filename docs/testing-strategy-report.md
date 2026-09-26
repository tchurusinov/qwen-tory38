# qwen-tory38 — Testing Strategy & Infrastructure Report

> Status: proposal, updated 2026-09-26 (branch `test-spec-live`). MAP
> 3.13–3.15 (capability layer / tool loop) is MERGED as PR #18
> (`e44762a`) — the "parallel session" premise of this draft is stale: its
> tests exist and are green, and the skeleton's §7 mapping is now verified
> fact, not plan. Original draft date: 2026-09-25. Everything below is
> grounded in the live tree: `tests/` (427 hermetic cases, verified
> 2026-09-26: `Ran 427 tests in 130.119s / OK` under `/usr/bin/python3.12`),
> `tests/gpu/` (21 methods incl. the new `test_tools_probe.py`),
> `scripts/gate.sh`, `.githooks/pre-commit`, `tests/run_gpu.sh`,
> `test-logs/` (the two 2026-09-25 @gpu logs + the 2026-09-26 probe
> logs), `docs/two-red-gpu-tests*.md`, SPEC §3.4/§3.7/§3.8/§5.8/Global
> testing discipline, MAP session log 2026-09-25/2026-09-26.

---

## 0. TL;DR (recommendations up front)

1. **The 427 hermetic tests are green and are the right shape** (table-driven,
   mock-engine, exit-code-closed-set). Keep them, stop touching their
   semantics. (366 at the original draft; PR #18 added `test_tools_module.py`
   + `test_harness_tools_loop.py`, +4 render-config + spawn cases — 427
   verified 2026-09-26, ~130 s wall.)
2. **The @gpu suite (21 methods: 20 + the new wire-protocol probe) is where
   all the pain lives.** The 2026-09-25 reds have three measured root
   causes, none of which are "the code is broken": (a) the runner invokes
   bare `python3` → Hermes toolchain 3.14 without PyYAML; (b) wall-clock
   timing assumptions vs. a box running at 96% main-stack utilization;
   (c) one test asserting on a stack the test itself only installed half
   of (N=2 template vs N=1 MVP units). **All three remain unfixed as of
   2026-09-26 (PR #18 merge)** — neither `test_lifecycle_2_11.py` nor
   `test_s38_acceptance.py` changed in PR #18; see §2 status note. Fix
   the three, add the flake policy in §6, and the hotfix treadmill ends.
3. **We are NOT testing the wrong things — we are testing the right things
   at the wrong altitudes, in one undifferentiated pile.** No tiering, no
   fast path, no interpreter pin, no quarantine mechanism, no release
   artifact (changelog/tags/install verification). That is the actual debt.
4. **Layer model (the core of this report):** L0 lint → L1 hermetic-fast →
   L2 hermetic-full → L3 @gpu-smoke → L4 @gpu-full → L5 bench/acceptance.
   Every gate is mapped to a layer with a wall-clock budget. Nothing slower
   than L2 may block a commit; nothing slower than L4 may block a release.
5. **Pipeline additions, lean and in order of value:** interpreter pin (fix,
   not feature); `gate.sh` becomes L0+L2 (required check); pre-commit keeps
   L0 + a ~10 s L1 subset; post-merge G4 gate adds L3 (VRAM-audited); release
   = tag + L4 + changelog + install-verification checklist. Gitea Actions
   stays off until post-MVP (one-box rule), as AGENTS.md already says.
6. **Decision you should make:** pytest vs unittest. I recommend **keep
   unittest** for the hermetic core (zero migration cost, the table-driven
   pattern is mature there) and adopt **pytest only for new @gpu/flake
   tooling** if at all — but if you want the `flaky` plugin, `-k`
   filtering, and JSON reports for free, a one-time migration is ~half a
   day and the skeleton below works either way (§1 flags the seam).

---

## 1. What we actually have (inventory, verified 2026-09-25)

### 1.1 Hermetic suite (`tests/`, no GPU, no network)

427 tests (verified 2026-09-26: `Ran 427 tests in 130.119s / OK`), exit 0,
run under `/usr/bin/python3.12 -m unittest discover -s tests -t .` (the
`-t .` is the documented target; plain `discover -s tests` breaks on the
relative-import fixture modules — see the MAP 2026-09-25 entry). Per-file
case counts:

| file | cases | what it proves |
|---|---|---|
| test_manager.py | 86 | S2.2 escalation, S2.5 drift watch, probe loop, health.json atomicity, boot-deadline ordering, env hooks (BONSAI2_PROBE_TIMEOUT_S etc.) |
| test_render_config.py | 71 | serving/engine YAML → unit-file render round-trip, key rewrites (boot_target_s write-back), schema shape (PR #18 added the capability-layer keys: tool_execution, tool_timeout_s, tool_output_max_kb, tool_invalid_max, workdir surface) |
| test_harness_34_38.py | 38 | reaper + deadline, spawn dedup, collect, steer/cancel, unload/reload (MAP 3.4–3.8) |
| test_harness_spawn.py | 35 | spawn surface vs mock manager (PR #18: + the workdir/tools spawn-surface cases) |
| test_harness_core.py | 33 | harness core, mock-engine surface |
| test_tools_module.py | 33 | **NEW (PR #18, MAP 3.13)** the host 4-tool module: valid-call result per tool, malformed → error result (never exception), workdir escape rejection (`/`-rooted, `..`-crossing, symlink-in-out physical/inside), timeout kill → exit 124 + no stray child, output truncation marker |
| test_harness_tools_loop.py | 24 | **NEW (PR #18, MAP 3.14/3.15)** the taskworker tool loop: step + final answer → done (result carries tool_log/steps/workdir), native `tool_calls` AND text-JSON both drive the loop (the A2 duality), tools-array wire shape on/off, host + engine execution modes, message-history growth, loop bounds (tool_invalid_max → ERR_TOOL_INVALID_CALL, step overrun → ERR_TOOL_TIMEOUT/124, no-final-answer → ERR_DEADLINE), steer at step boundary, cancel mid-step; 3.15 workdir/flag validation (`--workdir` must exist+writable, `--max-steps` regex) |
| test_image_build.py | 27 | Dockerfile render/build-banner/image-pin logic (no docker run) |
| test_model_fetch.py | 18 | PQ2_0 fetch: SHA verify, backoff/retry, hash-mismatch re-download (the "download attempt failed: reset" lines in the log are FAKE resets the tests inject — normal noise, not a failure) |
| test_vram.py | 20 | S1.4 allocation-tier math, budget planning |
| test_harness_closed_set.py | 25 | the S3.2 exit-code closed set: every code table-driven, exact JSON + code asserted (SPEC §3.7/§3.8 #3; PR #18 added the tool-loop codes 21/22 rows) |
| test_harness_status.py | 15 | status/health reads, N=0 main_only_reason |
| test_smoke.py | 2 | package sanity |
| fixtures/ | — | mock_engine.py (254 L: canned /v1/* incl. 200/400/429/500, streaming), mock_manager.py (285 L: stateful pool stub), tool_engine.py (**NEW**, 274 L: canned tool-call /v1/chat/completions incl. both wire shapes, streaming tool-call argument accumulation) |

**Verdict: this is a good suite.** Table-driven, mock-backed, every test
asserts a named SPEC criterion, exit codes asserted not implied. The shape
is correct; the debt is around it (tiers, interpreter, CI hooks), not in it.

### 1.2 @gpu suite (`tests/gpu/`, live box, live 27B engines)

21 test methods (20 at the original draft + the PR #18 probe), run via
`tests/run_gpu.sh` (unittest discover, nvidia-smi pre/post snapshots into
`test-logs/gpu-<ts>.log`). Observed wall time 2026-09-25: 776 s and 892 s
for the full dir. Inventory:

| file | cases | SPEC anchor | weight |
|---|---|---|---|
| test_lifecycle_2_11.py | 5 | S2.8 #1–#5: cold boot under target, crash recovery <30 s, N=0 + PTQ1_0 fallback, drift ctx-halve, unload/reload | HEAVY (the whole file is ~1600 L of live orchestration) |
| test_runtime_image.py | 5 | S1.6 image pin, in-image python3 | light |
| test_build_banner.py | 3 | S1.6 build banner | light |
| test_bringup_1_7.py | 1 | S1.8 #1 single-instance bring-up | heavy (27B load) |
| test_load_1_8.py | 1 | S1.8 #2 sustained load | heavy |
| test_oom_1_9.py | 1 | S1.4 allocation-tier OOM ceiling (holder-sizing loop) | HEAVIEST (~5–8 min alone) |
| test_port_collision_1_10.py | 1 | S1.8 #5 port collision | heavy |
| test_kill_restart_1_11.py | 1 | S1.8 #4 kill -9 restart | heavy |
| test_manager_spawn_2_5.py | 1 | S2.2/S2.5 live spawn, 2 real instances | heavy |
| test_s38_acceptance.py | 1 | S3.8 fan-out/kill/N=0/unload live acceptance | heavy |
| test_tools_probe.py | 1 | **NEW (PR #18, MAP 3.14)** S3.4 A2 wire-protocol probe: one `tools`-array chat completion against the pinned image; records native-vs-text-JSON verdict to `test-logs/tools-probe-<ts>.json` | light (reduced ctx 8192, ~16 s at 2026-09-26 run; verdict: **native** at pin `9a9394a8`, backfilled to SPEC S1.3/S3.4 in the 2026-09-26 G4 entry) |

Note the history you recalled: 17–18 tests green → broke at 19–20. That was
the S2.8 reactive pair (#2 crash / #4 drift) — documented in
`docs/two-red-gpu-tests.md` and `docs/two-red-gpu-tests-fix.md` — both
test-harness problems (a socket race + a detection window), hotfixed in PR #13.
The same class recurred on 2026-09-25: see §2.

### 1.3 Enforcement today

- **`.githooks/pre-commit`** — tripwire: bash -n + shellcheck (staged .sh),
  ruff format+check (staged .py), whitespace/CRLF/50 MB, secret scan, then
  full `gate.sh`.
- **`scripts/gate.sh`** — the G2 "required check" standing in for CI:
  bash -n all tracked .sh, shellcheck (HARD when installed), ruff (HARD when
  available), secret scan on PR diff, yaml parse (WARN-only). Baseline:
  PASS, exit 0 (2026-09-20).
- **No post-commit hook, no CI, no release artifacts, no changelog, no
  quarantine mechanism.** `test-logs/` is gitignored (audit trail only, not
  tracked).

---

## 2. The failing ones: what broke, measured

Latest @gpu logs (`test-logs/gpu-20260925-*.log`): run 1 under the default
`python3` (Hermes toolchain 3.14.7, no PyYAML): **13 ok / 7 FAIL — all 7
`ModuleNotFoundError: No module named 'yaml'`** inside the manager (an
environment artifact, not code). Run 2 under `/usr/bin/python3.12`:
**17 ok / 3 FAIL**:

1. `test_4_drift_ctx_halve` (S2.8 #4): the "pool healthy before we inject
   drift" precondition saw `degraded` — instance 1's probes were still
   catching up while the main SGLang engine ran at ~96% utilization.
   **Busy main stack is the default state of this project (§5.5)** — the
   box being busy is not the defect; the defect is the test's quiet-box
   settling assumption (no "healthy on all installed instances" settle
   before injecting drift). VRAM was ample at every audit point: the
   scenario was 96%-busy with headroom, which per §5.5 must pass on spec
   budgets. **Fix:** add the settle (poll health.json for all installed
   instances `healthy`, generous deadline) as the precondition of the
   drift phase — a condition-poll, never a sleep or a utilization check.
2. `test_5_unload_reload_cycle` (S2.8 #5): reload took 21.5 s vs
   `boot_target_s` = 11 s. **The test is spec-compliant, not buggy:** S2.8
   #5's own criterion is "reload --all → N healthy within `boot_target_s`",
   so asserting against the write-back target is the letter of the spec —
   unlike #2/#4, which carry the 30 s reload budget (S1.8 #4 / S2.8 #2/#4,
   amended 2026-09-24, delta-noted). The real defect is upstream of the
   test: `boot_target_s` is a S2.6 **warm** single-boot measurement
   (11.0 s on a quiet box) that S2.8 #5 silently inherits as the reload
   budget, while a cold 2×27B reload under busy-main contention measures
   21.5 s.
   **Fix (a spec fork, decision needed):** (a) amend S2.8 #5 to the 30 s
   reload budget (align #5 with #2/#4 — one reload number in the spec,
   delta-noted per the amendment rule), and/or (b) have the S2.6
   `boot_target_s` write-back measure under the box's default busy state
   so the written value is contention-robust. Option (a) is the small
   move; (b) is the doctrine move (§5.5: timing values sized for the
   contested box). Until decided, this test is the 30 s re-measurement's
   canary (Appendix A.2 #4).
3. `test_s38_fanout_kill_n0_unload` (S3.8 live acceptance): the manager
   honors the template `instances.count: 2` while the MVP test installs only
   the instance-0 user unit. Instance 1's probes lag → escalation →
   `systemctl restart bonsai2-server@1` → "Unit not found" ×5 → stack stuck
   `degraded` → the boot precondition (STACK healthy) fails. The test
   asserts on a stack it only installed half of. Everything past the
   precondition (the fan-out / kill / N=0 / unload halves) never ran.
   **Fix:** assert on the installed subset — instance 0 healthy — not the
   stack (the MAP 2026-09-25 entry already records this as the follow-up;
   consistent with §5.5: precondition on what you installed, not the
   template's N).

**The pattern (this is the actual lesson, and it matches the 19/20 breakage
you saw during the prompt-template era):** @gpu tests model the box's timing
in the author's head and assert against that model; the real box drifts under
contention. The fix doctrine, already proven twice: **assert the condition
the manager checks, with a generous deadline; never a fixed wall-clock
number; and precondition against the subset of the system the test
actually installed** (MVP = N=1 subset, not the template's N).

### Status as of 2026-09-26 (PR #18 merged as `e44762a`)

- **Red #1 (drift settle, `test_4_drift_ctx_halve`): still open.** The merged
  tree still has no "all instances healthy" settle before the drift phase;
  `test_lifecycle_2_11.py` was last touched by the PR #11 hotfix chain, not
  PR #18. The §5.5 condition-poll settle remains the fix.
- **Red #2 (S2.8 #5 reload budget, `test_5_unload_reload_cycle`): still open.**
  SPEC S2.8 #5 still asserts `reload --all → N healthy within
  `boot_target_s`` (a warm single-boot write-back value); the §2 spec fork
  (amend #5 to the 30 s budget vs measure the write-back under a busy main)
  was not taken in PR #18. §8.6 remains live.
- **Red #3 (S3.8 precondition, `test_s38_fanout_kill_n0_unload`): STILL AS
  WRITTEN in the merged tree.** The precondition is `health.get("stack") ==
  "healthy" and any(instance healthy)` plus a NEW explicit
  `len(health["instances"]) == 1` assertion (MVP: exactly one instance) —
  i.e. the assertion now names the installed subset explicitly, but the
  `stack == "healthy"` gate is unchanged: if the manager honors the
  template `instances.count: 2` (its S2.3 boot path reads the live YAML the
  test snapshots/restores) while only the instance-0 user unit is
  installed, `stack` still goes `degraded` and the precondition can still
  fail. The "assert on the installed subset, not the stack" fix from this
  report is the remaining half; verify against the live manager's current
  N behavior when it is next run (the 2026-09-25 failure mode: escalation →
  `systemctl restart bonsai2-server@1` → "Unit not found" ×5).
- **Interpreter bug (red class (a)): still open.** `tests/run_gpu.sh` still
  invokes bare `python3` (PR #18 only added the extra-args passthrough).
  The 2026-09-26 probe runs were therefore done under the Hermes toolchain
  python 3.14 — the probe passed (it needs no yaml), but the 7-phantom-
  failure class would still bite any yaml-importing @gpu test. §4 is
  untouched by PR #18 and remains "fix, do this first."
- **New since the draft: the wire-protocol probe.** Two early probe runs
  (2026-09-26 01:32 / 01:44) errored in `setUpClass` (the argv-shape fix:
  `flag value` jammed into one argv token → llama-server rejected the args),
  then the post-fix run passed in 16.2 s with verdict **native** (recorded
  to `test-logs/tools-probe-20260926-015610.json`). The A2 question is
  answered: the pin emits the native `tool_calls` array; SPEC S1.3/S3.4
  backfilled at G4 (spec-follows-code, no new locked value).

---

## 3. Are we testing the correct things? (strategy + result)

### 3.1 What the current strategy buys us (positives)

- **Hermetic-first is the right call for this codebase.** The manager's
  logic (escalation, drift, dedup, slot accounting, exit codes) is
  deterministic given injected fakes — and that is exactly how S3.2's closed
  exit set is proven: 100% of the codes, table-driven, asserted not implied.
  This is the strongest part of the project's quality story.
- **Every test asserts a named SPEC acceptance item** (Global testing
  discipline: "no vibes"). Tracing a failure to a spec line is cheap.
- **Mock fixtures are real and load-bearing** (mock_engine with 200/400/429/
  500 + streaming variants; mock_manager as a stateful pool). Not throwaway
  stubs.
- **@gpu tests are acceptance-grade, not demos**: they prove S2.8 #1–#5, the
  S1.4 OOM tier, S2.2/S2.5 live behavior. That is what "the stack actually
  degrades correctly on a real GB10" means, and no hermetic test can prove
  it.

### 3.2 What is missing or wrong (negatives)

1. **No tiers.** 427 hermetic cases run in one undifferentiated ~130 s pile.
   There is no "did I break the package" subset, so pre-commit runs the
   whole gate (correct, but slow to iterate on), and there is no cheap
   signal at all between commits.
2. **No interpreter pin.** `run_gpu.sh` calls bare `python3`; the box's
   default is the Hermes toolchain 3.14 (no PyYAML). This single ambiguity
   produced 7 phantom failures on 2026-09-25. The gate has the same latent
   issue (its yaml-parse step degrades to WARN under a yaml-less python).
3. **Wall-clock assumptions in @gpu** (documented in §2; the doctrine exists
   in the two red-tests docs but is not codified anywhere enforced — it is
   prose, not policy).
4. **Precondition-vs-reality mismatch**: tests assert on the template's N=2
   stack when the test (MVP mode) installed N=1 units. Same class: the test
   models the system instead of observing it.
5. **No flake detection or quarantine.** A red @gpu test blocks the next
   thing; the response is "hotfix the test," and the hotfix itself can carry
   the next assumption. There is no "re-run 3×, then root-cause, then
   quarantine with a tracking item" loop.
6. **Coverage gaps vs. the project's own acceptance criteria:**
   - **Global acceptance #1** (cold boot ≤180 s) — covered by 1.7/2.11, OK.
   - **Global acceptance #2** (a fresh main-agent session completes the
     S5.6 workload set using only S3.2 commands) — NOT covered by any
     automated test; it is a manual dogfood. This is the user story and it
     has no test.
   - **Global acceptance #3** (M1–M10 bench, S4.2 rows cite bench rows) —
     SPEC §5.8 specifies scorer rubric tests (hermetic goldens); the bench
     runner itself (S5.4) is not built yet (frontier 4.1).
   - **Global acceptance #4** (one-command install on a fresh box) —
     install.sh has zero tests (not even dry-run). Highest-risk untested
     code in the repo.
   - **Global acceptance #5** (72 h soak) — manual; no soak harness.
   - **Capability layer (3.13–3.15)**: MERGED (PR #18, 2026-09-26).
     Hermetic side: `test_tools_module.py` (33 cases — the exit-contract
     table + path-containment, the latter as explicit adversarial cases
     rather than a property framework) + `test_harness_tools_loop.py`
     (24 cases — both wire shapes, both execution modes, loop bounds,
     flags) + `fixtures/tool_engine.py`. @gpu side: `test_tools_probe.py`
     (the A2 wire-protocol probe, verdict native, recorded). All green at
     merge; the layer they live in is §5 (L1/L2 hermetic, L3 probe).
     What remains from this report's original ask: nothing structural —
     the 4-tool module got the containment cases as table entries, and a
     dedicated property-style module is optional polish, not a gap.
7. **No release surface.** No changelog, no tags, no "what changed since
   last release + what was verified" artifact. For a project whose
   install path is one command and whose spec is locked values, a release
   without a verification checklist is a spec-claim without evidence.
8. **No contract test against the engine wire protocol.** The subagent talks
   to an OpenAI-compatible endpoint; the hermetic side uses a homemade mock
   (correct for logic), but "does a REAL llama-server endpoint speak the
   same dialect as our mock" is only spot-checked inside @gpu tests. A
   small golden-response contract test (hermetic, recorded payloads) would
   pin the mock-to-real gap.

### 3.3 Strategy question: are we using the best strategies?

For THIS project (one box, one GPU pool, deterministic control plane +
stochastic LLM edge, locked spec values) the right mix is:

| strategy | verdict |
|---|---|
| Table-driven exit-code tests | keep — best practice here, already done (incl. the tool-loop codes 21/22, PR #18) |
| Mock-engine integration (hermetic) | keep — done well; `fixtures/tool_engine.py` extended it to both tool-call wire shapes |
| Live acceptance (@gpu, SPEC-anchored) | keep, add the observe-don't-predict doctrine + tiers |
| **Contract tests (wire protocol goldens)** | PARTIAL → the A2 probe answered the native-vs-text-JSON fork with a live verdict (PR #18); the hermetic golden-response goldens pinning the mock-to-real gap remain to add (§3.2.8) |
| **Property tests (workdir containment)** | DONE-AS-TABLE — PR #18 landed the adversarial containment cases as table entries; a property framework around them is optional, not a gap |
| **Golden-file round-trips (config render)** | PARTIAL → formalize under `tests/golden/` |
| Fast/smoke tier | ADD — biggest leverage per minute (§5) |
| **Acceptance-as-code for Global #2 (fresh-session dogfood script)** | ADD, post-MVP — turns the user story into a re-runnable test |
| install.sh dry-run tests | ADD, post-MVP (Global #4) |
| Soak harness | ADD, post-MVP (Global #5; 72 h = release-gate material) |
| Mutation testing | OPTIONAL, post-MVP — validates that the hermetic suite actually bites |
| pytest-flake/rerun tooling | decide with the unittest/pytest call (§5.4) |
| CI (Gitea Actions) | post-MVP per AGENTS.md — one-box constraint makes a second runner expensive; the post-MVP proposal already scopes this |

What is NOT worth it here: heavy E2E matrix (multi-box is out of scope v1),
LLM-judge bench (locked out of scope), coverage-percentage chasing (the
spec-anchoring is stronger than % coverage as a signal).

---

## 4. The interpreter bug (fix, not feature — do this first)

Measured: `python3` → Hermes toolchain 3.14.7 (no PyYAML); `/usr/bin/
python3.12` has PyYAML 6.0.1 and is the box python; `pytest` is not
installed for either (uvx gives pytest 9.1.1 on demand).

Fix: one helper, one rule.

- `scripts/pybox.sh` (or `tests/python`): resolves the project interpreter —
  `PYTHON_BIN` env override, else the first of `python3.12`/`python3` that
  passes `import yaml` (hermetic + gate) / passes nothing (L1 subset may
  tolerate stdlib-only), else fail with a one-line message. No silent
  fallback to whatever `python3` is.
- `tests/run_gpu.sh`: use it (kills the 7-failure class forever).
- `scripts/gate.sh` + `.githooks/pre-commit`: use it for every python
  invocation (kills the latent yaml-parse-degrades-to-WARN class).
- Rule for all @gpu tests: assert the condition the manager checks, with a
  generous deadline; precondition on the installed subset (MVP N=1), not the
  template N.

---

## 5. The layer model (the proposal, core)

Budgets are wall-clock ceilings on THIS box; enforce in the runner, not in
prose.

| layer | what | command (target) | budget | blocks what |
|---|---|---|---|---|
| **L0** | lint + syntax + secrets | `gate.sh` steps 1–4 (bash -n, shellcheck, ruff, secret scan) | ~10 s | commit (pre-commit), G2 gate |
| **L1** | hermetic-fast subset: closed-set + manager + harness_core + the tool layer (test_tools_module + test_harness_tools_loop — the newest code regresses most until it has scar tissue) | `/usr/bin/python3.12 -m unittest tests.test_harness_closed_set tests.test_manager tests.test_harness_core tests.test_tools_module tests.test_harness_tools_loop` (explicit module list — note: `discover -p` takes ONE glob, so the multi-pattern pipe form in this draft's v0.1 was never a working command; module names are the correct form) | ~80 s (measured 2026-09-26: closed-set 44 s is the floor — 25 cases each round-trip-serialize JSON docs; manager 6 s; core 2 s; tools 4 s; tools_loop 23 s) | pre-commit (add), G2 gate |
| **L2** | hermetic full | `python -m unittest discover -s tests -t .` (py3.12) | ≤150 s | G2 gate, G4 merge |
| **L3** | @gpu-smoke: runtime-image + build-banner + ONE 27B bring-up (1.7) | `tests/run_gpu.sh -k smoke` (subset flag) | ≤10 min | G4 merge (VRAM-audited), release |
| **L4** | @gpu full | `tests/run_gpu.sh` | ≤20 min | release, S5.5 re-bench |
| **L5** | bench M-rows, S5.6 workload set, fresh-session acceptance, 72 h soak | bench runner (S5.4, unbuilt) + dogfood script | hours–days | release (M9/M10 rows per 4.9) |

Consequences, deliberately:

- **Commits wait on at most ~95 s of tests.** Pre-commit = L0 + L1 (L1
  measured 79.8 s, closed-set dominates; §8.1 re-raises the L1-membership
  question: the original draft guessed ~30 s — the guess was wrong, the
  closed-set round-trip serialization is the floor). The current
  pre-commit already runs full `gate.sh`; add L1, keep L0. Nothing changes
  about the "only new failures block" baseline.
- **G2 gate = L0 + L2.** Hermetic-full becomes the required check (it is
  what the MAP log already records every PR; this just makes the gate
  actually run it instead of lint-only).
- **G4 (human merge) gate = L2 green (fresh run) + VRAM audit + L3.**
  Today nothing between the branch and the merge re-runs tests; this adds a
  10-minute smoke on a clear pool. The @gpu-full run stays a release/
  re-bench obligation, not a per-PR one (it is 15 min+ and contention-
  sensitive; running it per PR is how the 19/20 hotfix treadmill happened).
- **Release = tag + L4 + changelog + install-verification checklist** (§6.3).

### 5.1 Why not "run everything everywhere"

One box, one GPU pool, main stack sharing the pool at up to 96% util:
running L4 at G2 or per-PR is (a) slow, (b) contention-prone (the measured
failures were exactly this), and (c) it trains the team to expect green only
when the box is quiet — the wrong invariant. The layer model makes the
invariant "L2 green at commit, L3 green at merge on an audited pool, L4
green at release," which matches the one-box physics.

### 5.2 Post-commit hook

Value: cheap confirmation that the just-landed commit is L2-green in the
tree it actually lives in (catches the "gate ran on a dirty worktree" class
the pre-commit doc itself flags as a known trade-off). Cost: ~100 s after
every commit. Verdict: **adopt it, non-blocking first** (echo + log to
`test-logs/postcommit-<ts>.log`; a red post-commit is a signal to re-run
the gate, not a block) — make it blocking only if a red one is ever
observed to escape to G3. Keep it as a hook, not a daemon.

### 5.3 Post-merge / G4

Not a hook (G4 is the human merge gate per senior-git): the merge checklist
line becomes "L2 re-run + L3 (audited) green, recorded in the PR body."
No new machinery — the checklist + the runner flags are the mechanism.

### 5.4 The pytest decision (you asked; here is the honest trade)

- **Keep unittest (my default):** zero migration; the table-driven pattern
  is mature; `unittest` supports `-p` patterns and `-v`; the fixtures are
  unittest-style and stay as-is. What we lose: `-k` expression filtering,
  `--reruns`/flaky plugins, JSON reports, parametrization ergonomics.
- **Migrate to pytest (~half a day, mechanical):** TestCase classes run
  unchanged under pytest (no rewrite needed); we gain `-k`, `flaky`,
  `rerunfailures`, JSON/JUnit reports, and the de-facto ecosystem
  (the post-MVP CI/CD proposal already names a pytest suite for the bench
  runner, S5.8). Cost: one more tool on the box (uvx pytest already works),
  and the migration commit itself needs gate discipline.
- **Hybrid:** unittest for hermetic core, pytest for the @gpu runner's
  subset selection. Works, but two runners is the kind of sprawl this
  project has been avoiding.

Recommendation: **if the flake/quarantine policy in §6.1 gets adopted,
migrate to pytest in the same session** (reruns + JSON reports are what make
that policy cheap); otherwise keep unittest and the skeleton below is
runner-agnostic. Either way the layer model and budgets are unchanged.

### 5.5 The box is busy by design: utilization is a red herring, VRAM is the invariant

The whole point of this project is to load subagents ALONGSIDE the main
agent (SGLang at :30001, ~76 GB resident) and shepherd them. So the main
stack being loaded and actively used — `utilization.gpu` at ~96% — is the
**default operating state, not an anomaly**. Two runs can both read "96%
utilization" and be completely different worlds:

- 96% util, VRAM ~72/121 GB used → ample headroom → contention is
  latency-only; subagent tests must pass on their spec budgets.
- 96% util, VRAM ~119/121 GB used → pool nearly exhausted → the test is
  running in a world it cannot afford; failing here is the pool talking,
  not the code.

The meaningful check is VRAM headroom, not utilization — and it is already
the spec: the S1.4 allocation tier (`need = instance_footprint + 1.0 GB`
margin) and the AGENTS.md VRAM audit rule compute the free pool from live
cgroup footprints and `/proc/meminfo`, never from a utilization percentage.
§5.5 enforces that in the test harness instead of letting utilization
creep back in as a root-cause story:

- **Gate on VRAM.** Every @gpu precondition (and `run_gpu.sh`'s pre-block)
  asserts the S1.4 headroom number: `MemAvailable` / free pool − other
  containers − 8.0 GB reserved ≥ `instance_footprint + 1.0 GB` per instance
  the test will load. The 1.9/2.5 tests already fail loudly on a tight pool;
  that pattern becomes the universal precondition.
- **Record utilization, never gate on it.** `nvidia-smi utilization.gpu`
  goes into the log for correlation (useful when a timing margin is
  borderline). It is never a start/gate condition and never a red-test root
  cause. Banned root-cause phrasing: "main was at 96% so the test timed
  out." Correct phrasing: "free VRAM was X GB" (pool problem) or "the test
  assumed a quiet-box timing" (test problem — the usual one).
- **Timings are sized for the contested box.** Budgets in §5 (L3/L4
  ceilings, the S2.8 reload check, probe-cadence windows) are written for
  the box in its default busy state. Where a test needs a settling window
  (e.g. "all installed instances healthy before I inject a fault"), the
  settle is a **condition-poll on health.json with a generous deadline** —
  not a sleep, and not a utilization check. A spec budget that passes on a
  quiet box but not on the busy default is a spec value to amend + measure
  (spec-amendment rule, delta-note in the PR), not a test flake to hotfix.

---

## 6. The pipeline, lean (what to actually build, in order)

### 6.1 Flake + quarantine policy (the missing loop — highest value per line)

1. **Observe, don't predict** (doctrine, now enforced in every @gpu test
   review): assert the condition the manager checks, generous deadline,
   precondition on the installed subset. Reviewer checklist line.
2. **Re-run before root-cause**: any L3/L4 red is re-run up to 3× on an
   audited pool before anyone opens a hotfix. (pytest `--reruns 3` makes
   this one flag; unittest gets a runner loop in `run_gpu.sh`.)
3. **Quarantine**: a test that fails 2 of 3 re-runs moves to
   `tests/quarantine/` (discovered separately, never in L3/L4 default) with
   a MAP tracking item naming the failure class + the condition to re-admit.
   A quarantined test can stay no more than one release cycle. No silent
   `skipUnless`, no commented-out tests.
4. **Precondition audit at every @gpu run**: run_gpu.sh records (already
   does: nvidia-smi + /proc/meminfo + free) — keep `nvidia-smi
   utilization.gpu` in the pre-block for correlation (§5.5), but the gate
   condition is the S1.4 VRAM headroom number, not the percentage.

### 6.2 Gate + hooks, final state

- `scripts/gate.sh`: L0 + L2 (interpreter-pinned). Baseline re-recorded once
  (L2 adds the suite run to the recorded baseline).
- `.githooks/pre-commit`: L0 + L1 subset (interpreter-pinned); keeps the
  full gate.sh at the end (L2 then runs at commit too — that is today's
  behavior, keep it; L1 just gives the *fast* failing signal before the
  slower L2).
- `scripts/post-commit` (new): L2 non-blocking, logged.
- `tests/run_gpu.sh`: interpreter-pinned, `-k <tag|file|case>` subset flag,
  3× re-run-on-red option, main-stack utilization in the pre-block.
- G4 merge checklist: + "L3 green (audited), log path recorded."

### 6.3 Releases + changelog (the "professional" surface)

- **Changelog**: `CHANGELOG.md`, one section per merged phase/PR cluster
  (date, items, spec deltas, verification logs with `test-logs/` paths).
  Hand-written from the MAP session log at G4 merge — the session log is
  already the raw material; the changelog is its compressed public face.
  Rule: a section exists only when a release tag exists; no "unreleased"
  drift beyond one release.
- **Tags**: `bonsai2-vX.Y` at release merges (G4). Tag = "L4 green +
  changelog section + install checklist run on this box."
- **Release checklist (script or doc, your call — doc first, script later)**:
  L4 full green (log path); L3 fresh; install.sh dry-run / fresh-dir
  verification (S6.8 all four prereq cases + S1.1 SHA-verified fetch);
  image pin re-verified against `etc/subagent-engine.yaml`; M-rows relevant
  to the release re-run (M9/M10 once 4.9 lands); changelog section added;
  tag pushed; ARTIFACTS.md updated (pins, units, images — the no-orphaned-
  artifacts rule).
- **Gitea Actions**: stays off until post-MVP (AGENTS.md), and even then
  only the hermetic L0–L2 on the single self-hosted runner — the @gpu
  layers remain one-box-human-gated (they need the VRAM audit, which is a
  judgment call, not a CI step).

### 6.4 What I would NOT add (lean-keeping)

No coverage-% gates. No mutation CI. No second runner. No testcontainers-
style isolation (we DO have docker, but the @gpu tests need the real pool
physics — cgroup caps, unified memory — that a container-in-a-container
would fake). No CI email/webhook. No parallel @gpu (one pool, one heavy
thing at a time — machine rule). No L5 in the G4 loop (soak is a release
obligation, not a merge gate, until the 72 h harness exists).

---

## 7. How the new spec items (3.13–3.15, 4.9) plug in — VERIFIED against the merged tree

PR #18 (merged 2026-09-26 as `e44762a`) owned the 3.13–3.15 implementation
and its tests; the original "parallel session" framing of this section is
retired. What actually landed, and where it sits in §5:

- **3.13 (4-tool module)** → L1/L2, landed as
  `tests/test_tools_module.py` (33 cases): the tool-exit contract
  (ok/exit_code/output/detail shape per tool; malformed → error result,
  never exception; timeout → 124 + dead process group; truncation marker)
  + workdir path-containment (adversarial `/`-rooted, `..`-crossing,
  symlink-in-out physical cases — table entries, not a property
  framework). Pure stdlib: it is in the L1 fast subset (§5).
- **3.14 (taskworker tool loop)** → L2, landed as
  `tests/test_harness_tools_loop.py` (24 cases): both wire shapes
  (native `tool_calls` + text-JSON), both execution modes (host /
  engine-side `tool_execution: engine` incl. name mapping + error shape),
  loop bounds (21/22/124 in the closed set — the `test_harness_closed_set.py`
  rows were extended to match), steer/cancel semantics, message-history
  growth. The @gpu wire-protocol probe landed as
  `tests/gpu/test_tools_probe.py` → L3 (smoke-eligible: one spawn, one
  tool call, verdict recorded; ~16 s at its 2026-09-26 run). The A2
  verdict (native at pin `9a9394a8`) is backfilled into SPEC S1.3/S3.4.
- **3.15 (workdir + spawn flags + config keys)** → L2, landed in
  `test_harness_tools_loop.py` (flag validation: `--workdir`
  exist+writable, `--max-steps` regex) + `test_render_config.py`
  (+4 cases: the capability-layer keys' render round-trip).
- **4.9 (M9/M10 goldens + scorers)** → still the frontier (MAP 4.9 is
  next after 4.1–4.8 bench framework). L5 for M-row execution; scorer
  goldens hermetic/L2-eligible per the original plan — now with one
  fewer unknown: the goldens are tuned against the recorded probe
  verdict (native), not guesswork.
- All four follow §6.1 (observe-don't-predict, re-run, quarantine) from
  day one — requirement met: they landed in the general harness, and the
  @gpu probe followed the @gpu conventions (always-stop instance,
  recorded log, port-margined above the pool range).

---

## 8. Open decisions (need your call, in priority order)

> Decisions recorded 2026-09-26 (branch `test-spec-live`, user-confirmed):
> #1 → hybrid (unittest hermetic core, pytest @gpu subset selection);
> #2 → L3 release-only (G4 = L2-fresh + VRAM audit only);
> #3 → hand-written changelog section (as leant);
> #4 → install.sh hermetic tests deferred to a MAP phase item (they land
> with that MAP item; the release checklist's S6.8 row is the compensating
> control until then); #5 → post-commit non-blocking + log;
> #6 → deferred to test-spec T9/OD-2 as a standing open defect (the spec
> fork itself is untouched — no SPEC move without the re-measurement).
> All of these are now locked in `docs/test-spec.md` (T2/T3/T4/T6/T8/T9).

1. **unittest vs pytest** (§5.4): keep unittest (default) or migrate ~half
   day to unlock flake/reruns/JSON. My lean: migrate IF we adopt the
   quarantine policy (§6.1) in the same session — the two pay for each
   other.
2. **G4 merge gate scope**: L3-smoke mandatory at merge (adds ~10 min + a
   VRAM audit before every human merge) vs L3 release-only (cheaper merges,
   more late discoveries at release). My lean: mandatory — the 2026-09-25
   reds were exactly merge-time discoveries.
3. **Changelog form**: hand-written compressed section (my lean) vs auto-
   generated from MAP log (mechanical but noisy).
4. **install.sh tests**: dry-run hermetic tests now (cheap, catches the
   prereq-matrix regressions) vs post-MVP with a real fresh-dir run. My
   lean: hermetic prereq tests now, fresh-dir run at the first release.
5. **post-commit blocking**: non-blocking with log (my lean) vs blocking.
6. **S2.8 #5 reload budget** (§2 red #2): the test asserts against
   `boot_target_s` (spec-compliant), but that value is a warm single-boot
   number (11 s) that a cold 2×27B reload under busy-main exceeds (21.5 s
   observed). Options: (a) amend S2.8 #5 to the 30 s reload budget used by
   #2/#4 — one reload number in the spec; (b) make the S2.6 `boot_target_s`
   write-back measure under the box's default busy state so the written
   value is contention-robust. My lean: (a) now (small, delta-noted, kills
   the red), and (b) into the §5.5 eradication (Appendix A) as the
   doctrine-level follow-up.

---

## 9. Skeleton: TESTING-SPEC (extracted to `docs/test-spec.md`)

The skeleton below was the v0.1 draft; it is now a standalone document,
**`docs/test-spec.md`**, updated against the 2026-09-26 reality (PR #18
merged, §2 status note, L1 command + budget corrected from the v0.1 guess —
`discover -p 'a|b|c'` was never a working unittest invocation and the
measured L1 is ~80 s, not ~30 s). This section is kept as the historical
draft; `docs/test-spec.md` is the living text.

```text
# qwen-tory38 TESTING SPEC v0.1 (skeleton — see report §5/§6/§8)

S1. INTERPRETER (fix)
- S1.1 scripts/pybox.sh: PYTHON_BIN override, else first python3.12|python3
  passing `import yaml`; else FAIL (one-line msg). No bare `python3` anywhere.
- S1.2 run_gpu.sh + gate.sh + .githooks/* all invoke via pybox.
- S1.3 gate baseline re-recorded with the pinned interpreter.

S2. LAYERS + BUDGETS (ceiling enforced in runners, not prose)
- L0 lint/syntax/secrets (gate.sh 1-4)        ~10 s   commit, G2
- L1 hermetic-fast: closed-set+manager+core   ~30 s   commit, G2
- L2 hermetic-full (discover -s tests -t .)  <=150 s  G2, G4
- L3 @gpu-smoke: runtime-image+banner+bringup  <=10 m G4 (audited), release
- L4 @gpu full (run_gpu.sh)                    <=20 m release, S5.5
- L5 bench M-rows / S5.6 / fresh-session / soak  hours  release
- no layer may exceed budget without a MAP-logged amendment (spec value)

S3. HOOKS + GATES
- pre-commit: L0 + L1 (pinned), then full gate (L2 at commit — keep)
- post-commit: L2 non-blocking, log test-logs/postcommit-<ts>.log
- G2 gate = L0+L2; G4 merge = L2 fresh + L3 (VRAM audit) + log path in PR
- release = tag bonsai2-vX.Y + L4 + changelog + install checklist (S5)

S4. FLAKE + QUARANTINE
- @gpu red: 3 re-runs on audited pool before root-cause (runner loop /
  pytest --reruns 3)
- 2/3 red -> tests/quarantine/ + MAP item (failure class + re-admit cond)
- quarantine <= 1 release cycle; no silent skipUnless; doctrine:
  assert-the-manager's-condition, generous deadline, precondition on the
  INSTALLED subset (MVP N=1), never template N
- run_gpu.sh pre-block: record util for correlation; GATE on S1.4 VRAM
  headroom (free pool - 8.0 reserved >= footprint + 1.0 GB/instance)
- DOCTRINE (S5.5): main stack busy (~96% util) = default state, not
  anomaly; 96%+headroom must pass on spec budgets; 96%+tight pool = pool
  problem; never root-cause a red on utilization; settles =
  condition-poll on health.json, generous deadline, never sleep/util

S5. RELEASE + CHANGELOG
- CHANGELOG.md: one section per release (date, items, spec deltas,
  verification log paths); hand-compressed from MAP session log at G4
- tag = "L4 green + changelog + install checklist run" (checklist:
  L4 log, L3 fresh, install dry-run/S6.8, image pin vs engine.yaml,
  M-rows incl. M9/M10 post-4.9, ARTIFACTS.md updated)
- Gitea Actions: post-MVP, self-hosted, hermetic L0-L2 only

S6. GOLDEN FILES + CONTRACT
- tests/golden/: config render round-trips; OpenAI-dialect golden responses
  (mock-to-real contract; @gpu probe cross-checks the live engine against
  the goldens)

S7. NEW-SPEC-ITEM COVERAGE (3.13-3.15, 4.9) — lands in the layers, no
  parallel test world:
- 3.13 tool module: L1/L2 tests/test_tools_3_13.py (exit-contract
  table + property path-containment: adversarial paths, symlink in/out)
- 3.14 wire-protocol probe: L3 smoke (tools=on spawn + 1 tool call +
  tool_log shape); dual-mode hermetic halves L2
- 3.15 workdir/flags/config: L2 (validation + render round-trip pattern)
- 4.9 M9/M10: scorer goldens L2, M-row execution L5
- all four follow S4 from day one

S8. OUT OF SCOPE (locked until post-MVP): coverage-% gates, mutation CI,
second runner, parallel @gpu, testcontainers isolation, LLM-judge,
multi-box e2e (SPEC S0)
```

---

## Appendix A — Extermination list: the quiet-box assumption (tracked, NOT yet changed)

> Per user direction 2026-09-25: "where in the project does 'the box must
> be quiet' come from? It's probably in the spec. Check, don't change yet —
> mark it to be tracked down and eradicated." Findings below are the audit
> result: **the assumption is not in the decision logic — the gate code is
> already VRAM-based (good); it lives in prose, conventions, and one locked
> value measured on a quiet box.** That is why it keeps leaking into red-test
> root-cause stories. Hunt the prose, not the code.

### A.1 What the code gets RIGHT (keep — the doctrine already lives here)

- `tests/gpu/test_lifecycle_2_11.py:838` `_preconditions(pool_need_gb)`:
  `free_gb >= pool_need_gb` from `/proc/meminfo`, fail-loud with "pool not
  clear" — a pure VRAM headroom gate.
- `tests/gpu/test_manager_spawn_2_5.py:337`: `free_gb >= 2*budget + 1.0` —
  VRAM.
- `tests/gpu/test_s38_acceptance.py:239`: `free_gb >= budget + 1.0` (MVP N=1)
  — VRAM.
- `tests/gpu/test_oom_1_9.py`: "free memory asserted >= ~37 GB before the
  two full-cap loads (the test is invalid — and fails loudly — if someone
  else ate the pool mid-run)" — VRAM, and explicitly NOT a quietness check.
- AGENTS.md VRAM audit rule: `free pool = total − main-stack live cgroup −
  other containers − 8.0 reserved`; utilization is recorded in the snapshot
  but the *decision* is the headroom number. Already doctrine-shaped.

### A.2 What carries the quiet-box assumption (eradication targets)

1. **Prose: "clear pool" / "clear box"** — the words encode quietness even
   though the numbers are VRAM.
   - `tests/gpu/README.md:18` — "~15-20 min on a clear pool".
   - `tests/gpu/test_lifecycle_2_11.py:95` — "on a clear pool" (docstring).
   - Assertion messages: "run the VRAM audit and clear" (the three
     `_preconditions` sites above, `lifecycle:844`, `spawn:337`, `s38:239`) —
     "clear" is an instruction to quiet the box; should read "raise the free
     pool to ≥ N (main stack may stay loaded)".
   - Action: rewrite to headroom language ("pool ≥ N GB free; main stack may
     be loaded and busy — see report §5.5"). No logic change.
2. **The seed rule: "one heavy thing at a time."**
   - `AGENTS.md:269` — "One GPU, one heavy thing at a time: `@gpu` tests
     never run parallel with a live session or a review (SPEC global testing
     discipline)."
   - `SPEC.md:1571` — same sentence in Global testing discipline.
   - These govern *our own* agents (review sessions, subagent sessions,
     bench runs) — they are correct as written, but in practice they've been
     read as "the box must be quiet." The main SGLang stack (the product's
     own workload) is never mentioned as exempt.
   - Action: append one clause to both: "the main stack at :30001 being
     loaded and busy is the project's default state and is NOT a 'heavy
     thing' that must yield; it is the co-tenant this project exists to
     coexist with. Gate on VRAM headroom (S1.4), record utilization for
     correlation, never gate on it (report §5.5)."
3. **SPEC.md:119-122 "Fresh-load vs idle (locked)."** Frames the main stack
   as a binary: fresh-loaded (~76 GB) or asleep (weights released to RAM).
   The third state — **loaded and busy** (the default at @gpu test time:
   main session in use, util ~96%, full footprint resident) — is unnamed.
   Action: add the busy state to that paragraph: "a loaded main stack in
   active use keeps its full footprint at any utilization; utilization is
   never a signal about memory state." Delta-note it (the "Fresh-load vs
   idle" line is locked — the amendment rule applies, record expected vs
   real).
4. **The 30 s reload budget was measured on a quiet box.** S1.8 #4 / S2.8
   #2/#4 amended 15 s → 30 s from the 2026-09-24 run (observed reload half
   25.5 s) — that box had the main stack not in a live session. Under the
   §5.5 doctrine, a locked timing value needs re-validation with the main
   stack busy: run S2.8 #2 with a live main-stack workload in flight and
   record the reload half; if it exceeds 30 s, amend + delta-note (that is a
   *spec* move, not a test hotfix). Track as a release-checklist item
   (§6.3), not a blocker.
5. **The precedent that proves the point (keep as the citation):**
   `SPEC.md:1258` M8 row — "contention: main at working load + subagents at
   working load." The spec already knows busy-main is a first-class
   scenario; the testing discipline just doesn't inherit it. The §5.5
   amendment makes M8's assumption the default assumption, not an exception
   row.

### A.3 Definition of done for the eradication (when it becomes a PR)

- No `clear pool` / `clear box` / "run the VRAM audit and clear" in
  `tests/`, `tests/gpu/README.md`, or `SPEC.md` testing sections — replaced
  with headroom phrasing (grep-able: the hunt is a `grep -rn 'clear pool'`).
- AGENTS.md + SPEC.md one-box rule carries the busy-main clause.
- SPEC.md:119 names the busy state (delta-noted, locked line).
- 30 s budget re-measured under busy main (or delta-noted with the number
  that stuck).
- Report §5.5 is cited at each site as the governing doctrine.
- No logic changes: the `_preconditions` gates stay exactly as they are
  (they were already right).
