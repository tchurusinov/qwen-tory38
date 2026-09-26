# qwen-tory38 TEST SPEC (v1.0 — skeleton, normative)

> Authority: SPEC-grade — the layers, budgets, commands, and gate
> assignments in this document are locked values; changing one follows the
> spec-amendment rule (expected-vs-real, delta-note at the amended site,
> `## Spec amendments` section in the PR body, MAP session-log entry).
> Numbering is T1–T10 to avoid collision with SPEC.md's S1–S6.
>
> Provenance: extracted from `docs/testing-strategy-report.md` §9
> (v0.1 draft, 2026-09-25) and updated against the 2026-09-26 reality
> (PR #18 merged as `e44762a`; suite state verified: 427 hermetic cases
> green in ~130 s under `/usr/bin/python3.12`, 21 @gpu methods).
> The report remains the design record (measured root causes, strategy
> trade, Appendix A quiet-box eradication); this document is the policy.
> Where the two disagree, this document wins (it was written later,
> against the merged tree).
>
> Standing user decisions recorded 2026-09-26 (the "decision" tags below):
> authority = SPEC-grade; runner = hybrid; L1 = all five modules;
> G4 = L2-fresh + audit only (L3 release-only); post-commit non-blocking;
> open-defects section in scope; install.sh tests = MAP phase item, not a
> T-requirement.

---

## T1. INTERPRETER (fix, not feature — do this first)

- **T1.1** `scripts/pybox.sh`: resolves the project interpreter, in order:
  `PYTHON_BIN` env override → first of `python3.12`, `python3` that passes
  `import yaml` (hermetic + gate) → else FAIL with a one-line message
  (exit 2). No silent fallback to whatever bare `python3` is. (The
  2026-09-25 seven-phantom-failure class: box default `python3` = Hermes
  toolchain 3.14, no PyYAML.)
- **T1.2** `tests/run_gpu.sh`, `scripts/gate.sh`, and everything in
  `.githooks/` invoke python only via pybox. Acceptance: `grep -n 'python3'
  scripts/ tests/run_gpu.sh .githooks/` shows no bare interpreter
  invocation outside pybox itself.
- **T1.3** Gate baseline re-recorded once, under the pinned interpreter
  (new baseline entry in the MAP log; the "only new failures block" rule
  applies from that baseline).

## T2. LAYERS + BUDGETS (ceilings enforced in runners, not prose)

Budgets are wall-clock ceilings on THIS box; enforce in the runner, not in
prose. No layer may exceed its budget without a MAP-logged amendment (a
spec value, amended per the rule — not a hotfix).

| layer | what | command (target) | budget | blocks what |
|---|---|---|---|---|
| **L0** | lint + syntax + secrets | `gate.sh` steps 1–4 (bash -n, shellcheck, ruff, secret scan) | ~10 s | commit (pre-commit), G2 |
| **L1** | hermetic-fast subset: closed-set + manager + harness_core + tools_module + tools_loop (all five — closed-set is the highest-blast-radius invariant; **decision 2026-09-26: all 5 in**). L1 = an explicit unittest module list — `discover -p` takes ONE glob, so the v0.1 pipe form was never a working command (verified 2026-09-26: it ran 0 tests). | `/usr/bin/python3.12 -m unittest tests.test_harness_closed_set tests.test_manager tests.test_harness_core tests.test_tools_module tests.test_harness_tools_loop` | ~80 s (measured 2026-09-26: 201 cases in 79.8 s; closed-set 44 s is the floor — 25 cases each round-trip-serialize JSON docs; manager 6 s; core 2 s; tools_module 4 s; tools_loop 23 s) | commit (pre-commit), G2 |
| **L2** | hermetic full | `/usr/bin/python3.12 -m unittest discover -s tests -t .` | ≤150 s (verified 2026-09-26: 427 in 130 s) | G2, G4 |
| **L3** | @gpu-smoke: runtime-image + build-banner + one 27B bring-up (1.7) + the wire-protocol probe (2026-09-26 run: ~16 s) | `tests/run_gpu.sh --l3` | ≤10 min | **release only** (decision 2026-09-26: NOT per-merge — G4 = L2-fresh + VRAM audit; late discoveries are the release checklist's job), S5.5 re-bench |
| **L4** | @gpu full | `tests/run_gpu.sh --l4` | ≤20 min | release, S5.5 re-bench |
| **L5** | bench M-rows, S5.6 workload set, fresh-session acceptance, 72 h soak | bench runner (S5.4) + dogfood script | hours–days | release (M9/M10 rows once MAP 4.9 lands) |

Consequences, deliberately:

- Commits wait on at most ~220 s of tests: pre-commit runs the ~80 s L1
  fast-signal first, then the full `gate.sh` (L0 + L2 ≈ 140 s; see T3). L1
  gives the fast failing signal before the slower L2 — today's gate-at-
  commit behavior, kept.
- G2 gate = L0 + L2: hermetic-full is the required check (what the MAP log
  already records every PR — this makes the gate actually run it instead of
  lint-only).
- G4 (human merge) = L2 green on a fresh run + VRAM audit + L2 log path
  recorded in the PR body. No @gpu per merge (decision 2026-09-26).
- Release = tag + L3 + L4 + changelog + install checklist (T6).

Why not "run everything everywhere" (one box, one GPU pool, main stack at
up to 96% util): per-PR L4 is slow, contention-prone (the 2026-09-25
failures were exactly this), and trains the team to expect green only when
the box is quiet. The invariant is "L1/L2 green at commit, L2 green at
merge on an audited box, L3/L4 green at release" — one-box physics.

## T3. HOOKS + GATES

- **pre-commit** (`.githooks/pre-commit`): L0 + L1 (interpreter-pinned),
  then the full `gate.sh` (L2 at commit too — today's behavior, kept; L1
  just gives the fast failing signal before the slower L2).
- **post-commit** (new `scripts/post-commit`): L2 non-blocking, logged to
  `test-logs/postcommit-<ts>.log` (**decision 2026-09-26: non-blocking**).
  A red post-commit is a signal to re-run the gate, not a block; make it
  blocking only if a red one is ever observed to escape to G3. Keep it a
  hook, not a daemon.
- **G2 gate** (`scripts/gate.sh`): L0 + L2, interpreter-pinned (T1).
  Baseline re-recorded per T1.3.
- **G4 (human merge gate)**: L2 fresh + VRAM audit + log path in the PR
  body. L3 release-only (decision 2026-09-26).
- **release**: tag `bonsai2-vX.Y` + L3 + L4 green + changelog section +
  install checklist (T6).

## T4. RUNNERS (hybrid — decision 2026-09-26)

- **Hermetic core (L1/L2): unittest.** Zero migration — the
  table-driven pattern is mature, the fixtures are unittest-style and stay
  as-is. Explicit module lists / `discover -s tests -t .`; no `discover -p`
  multi-pattern forms (T2 note).
- **@gpu (L3/L4): pytest, for subset selection and flake tooling only.**
  The @gpu test files stay unittest `TestCase` classes and run unchanged
  under pytest — no @gpu test rewrite. pytest (uvx; no system install)
  provides `-k` selection, `--reruns 3` (T5), and JSON reports. It is the
  SELECTION layer, not a second test framework.
- **`tests/run_gpu.sh` contract**: interpreter-pinned (T1); `--l3` /
  `--l4` subset flags; 3× re-run-on-red option (T5); pre-block records
  nvidia-smi + /proc/meminfo + free, with `utilization.gpu` for
  correlation only (T5 gate condition).
- No third runner. The post-MVP bench-runner proposal (SPEC S5.8 area)
  assumes pytest — consistent.

## T5. FLAKE + QUARANTINE + DOCTRINE (locked)

**Doctrine (observe, don't predict)** — enforced in every @gpu test
review; reviewer checklist line:

- Assert the condition the manager checks, with a generous deadline; never
  a fixed wall-clock number.
- Precondition on the INSTALLED subset (MVP = N=1), never the template's N.
- The main stack busy (~96% utilization) is the default operating state,
  not an anomaly: 96% + VRAM headroom MUST pass on spec budgets; 96% +
  tight pool = the pool talking, not the code.
- Never root-cause a red on utilization (banned phrasing: "main was at 96%
  so the test timed out"; correct: "free VRAM was X GB" — pool problem —
  or "the test assumed a quiet-box timing" — test problem, the usual one).
- Settles are condition-polls on `health.json` with a generous deadline —
  never a sleep, never a utilization check. A spec budget that passes on a
  quiet box but not on the busy default is a spec value to amend + measure
  (spec-amendment rule, delta-note in the PR), not a test flake to hotfix.

**Flake + quarantine loop** (the missing loop — highest value per line):

- Any L3/L4 red is re-run up to 3× on an audited pool before anyone opens
  a root-cause (pytest `--reruns 3`; the `run_gpu.sh` equivalent for the
  unittest-invocation path).
- A test that fails 2 of 3 re-runs moves to `tests/quarantine/`
  (discovered separately, never in L3/L4 default) with a MAP tracking item
  naming the failure class + the re-admit condition. Quarantine ≤ one
  release cycle. No silent `skipUnless`, no commented-out tests.
- Precondition audit at every @gpu run: `run_gpu.sh` records the
  pre/post snapshots (already does). The GATE condition is the S1.4
  VRAM headroom number — free pool − 8.0 GB reserved ≥
  `instance_footprint + 1.0 GB` per instance the test will load — not a
  utilization percentage (the S1.4 allocation tier + the AGENTS.md VRAM
  audit rule already compute this from live cgroup footprints; the tests
  inherit it as a universal precondition).

## T6. RELEASE + CHANGELOG

- **Changelog**: `CHANGELOG.md`, one section per release (date, items,
  spec deltas, verification log paths under `test-logs/`).
  Hand-written, compressed from the MAP session log at G4 — the session
  log is the raw material; the changelog is its public face. A section
  exists only when a release tag exists; no "unreleased" drift beyond one
  release.
- **Tags**: `bonsai2-vX.Y` at release. Tag = "L3 + L4 green + changelog
  section + install checklist run on this box."
- **Release checklist** (doc first, script later): L4 full green (log
  path); L3 fresh; install.sh prereq matrix (S6.8 all four prereq cases) +
  S1.1 SHA-verified fetch; image pin re-verified against
  `etc/subagent-engine.yaml`; M-rows relevant to the release re-run
  (M9/M10 once 4.9 lands); changelog section added; tag pushed;
  ARTIFACTS.md updated (pins, units, images — the no-orphaned-artifacts
  rule).
- **Gitea Actions**: stays off until post-MVP (AGENTS.md one-box rule);
  even then, only the hermetic L0–L2 on the single self-hosted runner —
  the @gpu layers remain one-box-human-gated (the VRAM audit is a
  judgment call, not a CI step).

## T7. GOLDEN FILES + WIRE CONTRACT

- `tests/golden/`: config render round-trips (formalized from the
  `test_render_config.py` pattern) + OpenAI-dialect golden responses
  pinning the mock-to-real gap: recorded payloads from the pinned engine,
  hermetic replay against the same request shape the harness sends.
- **The goldens are tuned against observed wire shape, not guesswork**:
  the 2026-09-26 A2 wire-protocol probe verdict is **NATIVE** at pin
  `9a9394a8` (native `tool_calls` array, finish_reason=tool_calls,
  arguments as a JSON string; recorded in
  `test-logs/tools-probe-20260926-015610.json`; SPEC S1.3/S3.4
  backfilled at G4). The text-JSON half of the dual parsing stays
  hermetically covered (design answer: both halves work) but the golden
  set anchors on the native shape.
- The @gpu probe cross-checks the live engine against the goldens at every
  pin move (SPEC S5.5 pin-move rule: the pin moves only via a PR + S5
  quick bench — the probe is part of that evidence).

## T8. SPEC-ITEM COVERAGE (3.13–3.15 landed; 4.9 forward; install.sh a MAP item)

- **3.13 (4-tool module)** → L1/L2, landed in PR #18:
  `tests/test_tools_module.py` (33 cases): tool-exit contract
  (ok/exit_code/output/detail shape; malformed → error result, never
  exception; timeout → 124 + dead process group; truncation marker) +
  workdir path-containment (adversarial `/`-rooted, `..`-crossing,
  symlink-in-out physical cases — table entries; a property framework is
  optional polish, not a requirement).
- **3.14 (taskworker tool loop)** → L2:
  `tests/test_harness_tools_loop.py` (24 cases): both wire shapes
  (native + text-JSON), both execution modes (host / `tool_execution:
  engine` incl. name mapping + error shape), loop bounds (closed-set
  codes 21/22, 124 in the log), steer at the step boundary, cancel
  mid-step, message-history growth. L3: `tests/gpu/test_tools_probe.py`
  (the A2 probe, verdict recorded 2026-09-26).
- **3.15 (workdir + spawn flags + config keys)** → L2: flag validation in
  `test_harness_tools_loop.py` (`--workdir` exist+writable, `--max-steps`
  regex) + `test_render_config.py` (+4 cases: capability-layer keys'
  render round-trip).
- **4.9 (M9/M10 goldens + scorers)** → frontier (MAP, after 4.1–4.8 bench
  framework): scorer goldens hermetic (L2-eligible, T7 pattern, tuned
  against the recorded probe verdict); M-row execution L5.
- **install.sh**: hermetic prereq-matrix tests are a **MAP phase item**
  (decision 2026-09-26: owned by the MAP plan, not a T-requirement — they
  land when that MAP item lands, L2-eligible); the fresh-dir run stays at
  the first release (T6 checklist). Until then: "highest-risk untested
  code in the repo" remains true, and the release checklist's S6.8 row is
  the compensating control.
- All new-spec items follow T4/T5 from day one — the harness discipline
  without the hotfix treadmill.

## T9. OPEN DEFECTS (standing; re-admit condition per item)

This section is a tracking surface, not a spec change: each entry names
the failure class and the condition that re-admits it. Entries move to the
MAP session log (or out) when re-admitted; they do NOT stay here as prose
only. (Open items as of 2026-09-26, all verified against the merged tree
at `e44762a`.)

- **OD-1 @gpu drift settle (S2.8 #4, `test_4_drift_ctx_halve`)** — the
  "pool healthy before we inject drift" precondition must poll
  `health.json` for ALL installed instances healthy (generous deadline)
  before the drift phase; no quiet-box settling assumption (report §2
  red #1; §5.5 doctrine). Re-admit: green 2/3 consecutive audited L3
  runs, busy main stack present at audit time.
- **OD-2 S2.8 #5 reload budget spec fork (report §2 red #2)** — the test
  asserts against `boot_target_s` (spec-compliant letter), but that value
  is a warm single-boot write-back (11 s) exceeded by a cold 2×27B reload
  under busy main (21.5 s observed). Fork: (a) amend S2.8 #5 to the 30 s
  reload budget used by #2/#4 (one reload number in the spec, delta-noted)
  and/or (b) the S2.6 `boot_target_s` write-back measures under the
  box's default busy state so the written value is contention-robust.
  Until decided, `test_5_unload_reload_cycle` is the re-measurement's
  canary. Re-admit: the spec delta lands + the test green under busy main.
- **OD-3 S3.8 precondition on the installed subset (report §2 red #3,
  `test_s38_fanout_kill_n0_unload`)** — the precondition currently gates
  on `stack == "healthy"`; the merged tree also carries a
  `len(instances) == 1` assertion (explicit subset), but if the manager honors the
  template `instances.count: 2` while only the instance-0 user unit is
  installed, `stack` still degrades and the precondition can still fail
  (09-25 failure mode: escalation → `systemctl restart
  bonsai2-server@1` → "Unit not found" ×5). Fix direction: gate on
  instance-0 healthy + the count assertion, not on the stack (or verify
  the live manager's current N behavior first — the test snapshots/restores
  the live YAML it runs against). Re-admit: green 2/3 audited runs with
  the fan-out / kill / N=0 / unload halves actually executing.
- **OD-4 interpreter pin (T1)** — pybox landed + `run_gpu.sh` /
  `gate.sh` / `.githooks/*` pinned + gate baseline re-recorded. Re-admit:
  T1.1–T1.3 all true (grep-able acceptance in T1.2).
- **OD-5 quiet-box eradication (report Appendix A)** — five targets:
  "clear pool" prose (`tests/gpu/README.md`, `test_lifecycle_2_11.py`
  docstring), the "run the VRAM audit and clear" assertion messages, the
  AGENTS.md/SPEC one-box rule (append the busy-main clause), the
  SPEC "Fresh-load vs idle (locked)" line (name the busy state,
  delta-noted), and the 30 s reload budget re-measured under a busy main.
  Re-admit: Appendix A.3 DOD, all of it, in one PR.

## T10. OUT OF SCOPE (locked until post-MVP)

Coverage-% gates · mutation CI · second runner · parallel @gpu (one pool,
one heavy thing at a time — machine rule) · testcontainers-style
isolation (the @gpu tests need the real pool physics — cgroup caps,
unified memory) · LLM-judge bench mode (SPEC locked out) · multi-box e2e
(SPEC S0) · property-framework test tooling (T8: table entries are
sufficient) · the 72 h soak harness (until it exists, the soak is a manual
release obligation, not a gate) · install.sh hermetic tests (T8: MAP
phase item, lands with its MAP item).

---

## Change log (this document)

- **v1.0 skeleton, 2026-09-26** (branch `test-spec-live`): extracted from
  the strategy report §9 v0.1; updated against PR #18 (`e44762a`): suite
  state 427 hermetic / 21 @gpu, probe verdict native at pin `9a9394a8`,
  L1 command + budget corrected from the v0.1 guess (explicit module
  list; ~95 s measured), G4 = L2-fresh + audit only, L3 release-only,
  hybrid runner, T9 open-defects standing section. User decisions
  recorded in the header.
