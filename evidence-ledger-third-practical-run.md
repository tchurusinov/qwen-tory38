# Evidence ledger — third practical live run of the bonsai2 subagent pool

Date: 2026-10-05. Task: THIRD practical run AFTER the second-run
remediation (PR #38, merged main @ c0e008d: reasoning off by default,
bounded nudge, worker self-cancel, progress probe, install/reaper
hygiene): re-deploy the patched pool (install.sh re-run) and extend the
existing project at `/home/blckwll/prod/de-hex-er/subagentses` with
THREE new dashboard windows — all derivatives of the stickman arena
war (`stickman-war.html`), per the user:

  X1 `stickman-duel.html` — 1v1 stickman duel: one human stickman vs
      one AI stickman, mirror arena, the "garry" extra: both players
      have weapons + the AI fights back like a rival, not a horde.
      (user: "one to be extra gorry [garry], surprise me" — the
      surprise is that the AI has two guns, aims with lead, and taunts
      via on-canvas text between rounds.)
  X2 `stickman-siege.html` — stickman siege: the player is a WALL
      BUILDER — place neon barriers in a grid, then survive waves of
      stickmen that path around walls (simple flow-field steering),
      walls take damage from bullets.
  X3 `stickman-rally.html` — stickman rally: endless top-down
      survival rally, stickman on a scrolling neon highway, dodge AI
      stickman traffic + shoot stragglers, combo meter, speed ramps.

  Plus X0: `index.html` gains three new cards (the W0 shape that WON
  in run 2 — pre-seeded modification, small delta).

This file documents every turn and the exact behaviour of the
workflow: exit codes, timings, VRAM, what landed on the pool vs main,
what the subagent got right/wrong, what main re-verified. Companion to
`evidence-ledger-for-first-practical-run.md` and
`evidence-ledger-second-practical-run.md` (same repo). Read top-down;
newest entries at the bottom.

## Legend

- [MAIN] = the main agent (qwen3.8-27b via SGLang :30001)
- [POOL] = bonsai2 pool operations (spawn/collect/status/...)
- [VER] = main-side verification (the self-report rule)
- Times in local EEST.

## Acceptance bar for THIS run (from the second-run adjudication
§6 + the PR #38 fixes under test)

1. All three window files LAND ON THE POOL (collect exit 20, honest
   tool_log, artifact verified by main) — the run's load-bearing
   artifacts must be subagent-delivered, not main-fallback
   (guardrail 4: "artifact done" ≠ "run successful").
2. REASONING OFF BY DEFAULT works: spawn records carry
   `reasoning_off: true`, PONG probe returns a short/empty reasoning
   field, and the first completions contain the first tool call WELL
   INSIDE the deadline (the 15-17K-token reasoning block is gone).
3. No silent false-success; the bounded nudge (FIX 2) and worker
   self-cancel (FIX 3) paths are noted if exercised.
4. The progress probe (`bonsai2-status --tasks`, FIX 5) is main's
   mid-run eye: every ~2-3 min poll; a stuck task (steps==0 &&
   tool_calls==0 with <50% deadline left) gets a steer/cancel
   decision LOGGED, not a silent wait to the reaper (guardrail 7).
5. collect → unload → verify (F8 order, mandatory). No token-heavy
   main-side generation while the pool is loaded.
6. Install is clean first-try: re-run from PR #38 main, 10/10 wrappers
   +x, `--reasoning` flag live on spawn, `workspaces/` chowned on
   install (FIX 7 — no manual /run/bonsai2 chown needed this time).
7. Delegation-size rule (guardrail 6): every window task ships with a
   PRE-SEEDED SKELETON that already passes `node --check`; the prompt
   says "fill/edit this file", not "write this file". The expected
   outputs are ~8-11 KB, so this is not optional this run.

## Plan (main's architecture decision, pre-pool)

- Shape: single-file code-gen chunk, the shape that LOST both prior
  runs — now re-tested with the two levers that were missing:
  reasoning off at the wire level + pre-seeded compiling skeletons
  (the second-run adjudication §6.2 rule: "delegate MODIFICATIONS;
  for GENERATION, pre-seed a known-good skeleton and make the
  subagent fill/edit/verify it").
- Four tasks, each a single-writer unit, each with its OWN workdir
  (main-created, pre-seeded):
  - X0: index.html cards (workdir seeded with the current index.html;
    the W0 winning shape from run 2, expected output ~1.3 KB delta)
  - X1/X2/X3: each workdir seeded with a COMPILING SKELETON (head +
    canvas + style link + a game shell that draws grid + start
    screen + HUD frame, node --check exit 0) + a spec block in the
    prompt naming the exact functions to fill in.
- Prompts: T1-style (file named up front, "FIRST reply must be the
  write_file tool call"), compact spec, EXECUTABLE DoD baked in
  (node --check + a grep on required identifiers; the subagent runs
  it and reports the exit code; main re-runs it on collect).
- Spawn params (pinned settings, computed per guardrail 8, NOT
  guessed): --tools on, --reasoning off (the default; recorded, not
  a per-spawn exception), --max-tokens 16384 (the default covers a
  skeleton-fill completion; the first completion is now expected to
  be ~1-3K tokens with reasoning off, so 16384 is ample),
  --deadline 1800 s (the tools-on default; with reasoning off the
  second-run's 15-17K-token first completion collapses to ~1-3K →
  well inside; if the probe shows a task stuck at steps==0 past 50%
  of deadline, that is the logged steer/cancel trigger).
- Verification doctrine (ledger-first, per skill):
  1. collect: exit 20, read tool_log (write_file + node --check ran,
     rc 0)
  2. file exists in the task workdir; main copies it to the target
     dir
  3. main re-runs `node --check` on the extracted inline script
     ITSELF
  4. main serves the dir + drives a real browser: each page loads
     clean, no console errors, the game actually runs (movement,
     waves/combat per spec, restart path), dashboard cards link
- Budget: main t/s halves while the pool is loaded — main's turns
  stay short; verification happens AFTER unload (F8).

---

## 0. Orient + baseline

- [MAIN] 16:1x — qwen-tory38 repo clean at main c0e008d (PR #38
  merged; the second-run remediation is the tip). de-hex-er repo:
  subagentses/ untracked (8 files from runs 1+2: flappy, life,
  mandelbrot, stickman-war, mandelbrot_2, synthwave-machine, index,
  style.css). Node v26.7.0 at
  /home/blckwll/.hermes/tools/node-26.7.0-linux-arm64/bin/node (full
  path baked into the prompts, host PATH may not include it).
- [MAIN] Skill: `bonsai2-harness` repo-local at
  /home/blckwll/prod/qwen-tory38/skills/bonsai2-harness/SKILL.md,
  NOW CARRIES the second-run guardrails (pipeline, bounded retry,
  run-success criterion, reasoning-off default, delegation-size
  rule, progress probe, deadline formula) — loaded in full this
  session; the run follows them.
- [MAIN] Live /opt/bonsai2 is STALE relative to the merged repo
  (verified by diff): 12 bin files differ + bonsai2_install.py
  missing on the box (the install path writes it); etc/harness.yaml
  differs (the box copy lacks task_reasoning_off / the PR #38 keys),
  etc/serving.yaml differs. Manager service active but its code is
  pre-PR#38; the pool is user_unloaded (main-only) since run 2's
  19:01. → install.sh re-run is required before the run (per the
  user's note + the stale-tree probe).
- [MAIN] VRAM audit (before anything loads): Mem total 121 GiB, used
  70 GiB (35 GiB free + 17 buff/cache, 51 available), qwen38-sglang
  RSS 2.93 GiB (the main stack is ASLEEP via sleep_on_idle — far
  below the 76.4 GB fresh footprint; cgroup/docker-stats probe used,
  nvidia-smi memory query returns [N/A] on this GB10, known quirk).
  Other containers: gitea 1.26G, open-webui 0.31G, rest <0.5G.
  2 instances × 14.78 GB budget → headroom OK.
- [MAIN] de-hex-er git: subagentses/ still untracked (run-1 policy:
  do not overthink this dir's version control — the user re-confirmed
  it this run). No commit expected from this run; the ledger is the
  record.
- [MAIN] Workdir decision: /run/bonsai2/workspaces is the harness's
  home (health_dir), but main pre-seeds each task's workdir ITSELF
  under /home/blckwll/.hermes/cache/scratch/bonsai2-run3/ (x0..x3)
  and passes it via --workdir (existing + writable — the spawn
  validation path, same as run 2's W0). The task files are copied
  out to the target dir after collect; the scratch workdirs can die
  with the cache prune.
- [MAIN] install.sh scope decision: system-scope via sudo (the box's
  /opt/bonsai2 + /run/bonsai2 + units are root-owned from run 1's
  system-scope install; run 2 confirmed user-scope fights the
  root-owned tree with EOPNOTPERM). Working sudo pattern on this
  box (run 2): PTY background process → poll for the password prompt
  → process submit (types the password). No `sudo -S` pipe (the
  consent gate blocks it).

## 1. Delegation plan detail (pre-delegation gate, guardrail 2 —
written BEFORE spawn, pool unloaded)

Window specs (the subagents see the spec block in their prompt; main
keeps this table as its verification reference):

- X1 stickman-duel.html (workdir x1, skeleton stickman-duel.html):
  1v1 arena. Player stickman (cyan, WASD/arrows + mouse aim +
  click/space shoot) vs one AI stickman (pink, two parallel guns,
  leads its aim at the player's velocity, fire-rate 700 ms, hp 30,
  speed 90). Rounds: first to 5 hits takes the round; 3 rounds
  takes the match (best-of-3 rounds). Between rounds: AI taunt text
  (rotating: "gg ez", "my turn", "you missed", "is that all?")
  drawn on canvas for 3 s. Player hp 100 (8 per enemy hit, as in
  war); AI hp 60 (6 per friendly hit). Score = hits landed ×100.
  R restarts the match. HUD: round + score + both HP bars
  (player top-left cyan, AI top-right pink).
- X2 stickman-siege.html (workdir x2, skeleton stickman-siege.html):
  grid battlefield (canvas 960×540, grid 48 px cells, same as war).
  Phase A (20 s): place walls — click empty cells to toggle a wall
  (max 12 walls, neon cyan, hp 5 each); player stickman moves with
  WASD, no shooting yet. Phase B (30 s, ×6 waves): stickmen
  (pink, hp 2) spawn from the edges and steer AROUND walls
  (candidate directions: toward player ± spread; a wall on the next
  cell = try the two perpendicular options; walls block movement,
  bullets pass over walls; a bullet that hits a wall deals 1 wall
  damage; walls at hp 0 break with a particle burst). Player can
  shoot (mouse aim, click/space) during phase B. Wave clear →
  phase A again (3 s, walls persist, player can place more).
  6 waves, counts 3→12. Player hp 100. HUD: phase + timer + wave +
  score + HP bar + remaining wall count. Death → GAME OVER + R
  restart; wave 6 cleared → VICTORY.
- X3 stickman-rally.html (workdir x3, skeleton stickman-rally.html):
  endless vertical rally: the road scrolls DOWN past the player
  stickman at the bottom (speed ramps 300 → 700 px/s over 90 s),
  lane-based: 4 lanes, AI traffic stickmen spawn at the top of
  random lanes moving down with the scroll (some slower, some
  faster = overtakes), player steers between lanes with
  left/right (snappy lane lerp) and shoots up (space/click) along
  the current lane. Dodge = survive; hit a straggler = +50 combo
  (combo ×2 per hit up to ×8, resets on getting hit); player hp
  100 (10 per collision, 1 s invulnerability + blink). Score =
  distance (px/10) + kills ×200 × combo multiplier. Speed lines +
  particle trail on the player. HUD: speed, score, combo, hp bar.
  Death → GAME OVER + score + R restart. No waves — endless.
- X0 index.html (workdir x0, seeded with the CURRENT index.html):
  add three cards (STICKMAN DUEL / STICKMAN SIEGE / STICKMAN RALLY)
  with the hrefs above, same card shape as the existing ones.

Skeletons: each = the war-page's head/canvas/footer shell + a
compiling IIFE that sets up ctx + the grid + a "click to start"
ready-state render + an empty update(dt) + a requestAnimationFrame
loop + a HUD that prints the title (each passes node --check at
THINK time; the subagent fills update()/the entities/HUD detail).
The skeleton is the compile-green floor: the subagent edits a
COMPILE-ING file, it does not author one (guardrail 6).

DoD (identical shape, per task; the exact commands in the prompt):
- `node --check` on the extracted script (the prompt gives the
  exact bash: the script is between `<script>` and `</script>`; the
  skeleton ships a `check.sh` in the workdir that does the
  extraction + node --check — one command, rc 0 is the DoD) AND
- `grep -c` presence check for the spec's required identifiers
  (per-window named list, e.g. X1: `aiAim`, `rounds`, `taunt`).
- The subagent runs check.sh, reports its exit code; on non-zero:
  fix + re-run (≤ 3 times, then report the failure in the final
  answer — main decides).
- Final-answer contract: one paragraph (what was filled, the exact
  commands + exit codes, what failed).

## 2. Redeploy the patched pool (PR #38 live)

- [MAIN] 16:2x — VRAM re-audit right before deploy: Mem total 121 GiB,
  used 70 GiB, 51 available; qwen38-sglang 2.93 GiB (asleep); free
  pool (121.6 − main-cgroup − others − 8.0 reserved) ≫ 2 × 14.78 +
  1.0 margin → load OK. Repo clean @ c0e008d; no uncommitted changes
  to stress.
- [MAIN] 16:2x–16:3x — PTY-sudo `./install.sh` (system scope
  auto-detected). Install EXIT 0. Boot: image digest match (skipped),
  models SHA-verified, N=2 (S1.4: pool 121.63 − main 70.04 −
  reserved 8.0 → 43.59 / 14.78 per instance), config rewrite (no
  drift: 70.04 → 70.04), 3 units installed, WARN_OS_VERSION
  (cosmetic, same as run 2). Boot-to-healthy ≈ 11 min total incl. the
  password prompt + two sequential 27B loads (run 2 measured 180.1 s
  after the image was warm; today's number includes the cold-ish
  container start — recorded, not a regression).
- [MAIN] F-verification on the deployed tree (PR #38 acceptance
  #6 + FIX checks):
  - 12/12 command wrappers `#!/usr/bin/python3` +x (the deploy-time
    shebang rewrite, F10 discipline — the ONLY repo-vs-box diff on
    every bin file; the modules are identical).
  - `--reasoning` flag live: spawn docstring carries it; harness has
    16 reasoning_off references; the spawn parser maps
    `--reasoning on|off` → record reasoning_off (verified in code).
  - FIX 5 progress probe: progress_report present in status +
    harness (3 refs).
  - FIX 7: install log shows "run: /run/bonsai2/workspaces chowned to
    uid=1000" + "results chowned to uid=1000" — BOTH subtrees; no
    manual chown needed this run (the run-2 INFRA DEFECT #2 is
    closed at install).
  - CPUQuota=64% single-percent in both instance units (the 64%%
    bug stays gone); units in /etc/systemd/system (system scope,
    one canonical scope as in run 2).
  - stack: healthy, i=0 + i=1, 0/8 slots, main_healthy true.
- [MAIN] PONG probe (acceptance #2, reasoning-off sanity):
  spawn rc=0 → t-20261005-0010-fc8c on i=0, --tools off, deadline
  120. Collect rc=20 in ~20 s: answer "PONG" (exact word as asked),
  reasoning field EMPTY STRING, completion_tokens 1, tool_log [].
  The run-2 PONG returned a non-empty narration; today's is empty —
  reasoning_effort "none" is working at the wire level. FIX 1
  acceptance met.

## 3. Pre-seed workdirs + prompts

- [MAIN] 16:4x — workdirs under
  /home/blckwll/.hermes/cache/scratch/bonsai2-run3/:
  x0/ index.html (the current dashboard, 1166 B — the W0 winning
  shape from run 2); x1/x2/x3/ each carry a COMPILING SKELETON
  (main-written at THINK time, pool still unloaded): the
  war-page's head/canvas/footer shell + input + game loop + grid +
  start screen + HUD frame + the entity rendering + a STUB
  update(dt) marked with a "TODO: fill in the … gameplay" comment
  block naming the exact spec. All three pass `node --check` via
  their own check.sh (rc 0, printed CHECK_OK) BEFORE the pool ever
  sees them. style.css copied into each window workdir so the
  subagent never looks outside its workdir.
  - X1 stickman-duel.html skeleton: 8,545 B (the fill target is
    update() + optional helpers; expected output ≈ 11 KB)
  - X2 stickman-siege.html skeleton: 9,843 B (≈ 12 KB)
  - X3 stickman-rally.html skeleton: 8,999 B after stubbing the
    combat (≈ 10 KB)
- [MAIN] prompts prompt-x0..x3.txt written in the scratch root:
  T1 discipline (file named up front, "FIRST reply must be the
  read_file/write_file tool call — no planning paragraph"), compact
  spec block, EXECUTABLE DoD baked in (check.sh rc 0 + a grep for a
  marker the skeleton does NOT contain — X1 aiAimLead, X2
  wallSteer, X3 rallyShots — so the grep proves the subagent
  actually filled the TODO, not just resaved the skeleton). X0's
  DoD: two grep counts (9 cards / 3 new hrefs), the run-2 winner
  shape.
- [MAIN] Spawn params (pinned, computed per guardrail 8 — the
  reasoning-off first completion is expected ~1-4K tokens, so the
  default 1800 s deadline covers first completion + read/write/
  check/answer at 4 t/s worst case): --tools on, --reasoning off
  (the cfg default, recorded not per-spawn), --max-tokens 16384
  (default), --deadline 1800 s (default), --workdir <pre-seeded
  dir>, --max-steps 32 (default). No guessed numbers: 4K first
  completion @ 4 t/s = 1000 s + ~4 tool steps ≈ 4×60 s + margin
  = ~1400 s < 1800 s. ✓

## 4. PONG probe

(done in §2 — the probe ran immediately after the install
verification, before any real task.)

## 5. Task fan-out (the real work)

- [MAIN] 16:27:30 — batch-spawn of all four in ONE block (R7
  pattern), all EXIT=0:
  - X0 index cards:   t-20261005-0011-2f6c  i=0  workdir x0
  - X1 stickman duel: t-20261005-0012-ecce  i=1  workdir x1
  - X2 stickman siege: t-20261005-0013-7a66  i=0  workdir x2
  - X3 stickman rally: t-20261005-0014-1023  i=1  workdir x3
  Routing: 2 per instance (4 of 16 slots; under the 6-of-8
  doctrine cap). All default pins: tools on, reasoning off (cfg
  default), max-tokens 16384, deadline 1800 s (computed, §3),
  max-steps 32.
- [MAIN] 16:28 progress probe (FIX 5 working live): X0 already at
  steps=1, tool_calls=1 within ~60 s of spawn — the first tool call
  LANDED INSIDE THE FIRST MINUTE. Run 2's same moment: steps=0,
  tool_calls=0 (stuck in the 15-17K-token reasoning block). The
  shape difference is visible immediately. Poller running
  (/tmp/poll3.sh, 90 s cadence, progress-probe output to
  /tmp/poll3.log, notifies on all-terminal).

### 5.1 Completions (from the poller log + collect, 16:48)

- Timeline (spawn 16:27:30; the poller log shows each task's last
  running row, then its absence):
  - X0 t-0011: last seen 16:31:29 (steps=5) → gone by 16:32:59
    (~3-5 min wall)
  - X3 t-0014: last seen 16:38:59 (steps=4) → gone by 16:40:29
    (~13 min)
  - X1 t-0012: last seen 16:40:29 (steps=5) → gone by 16:41:59
    (~14 min)
  - X2 t-0013: last seen 16:41:59 (steps=8) → gone by 16:43:29
    (~15.5 min)
- collect: ALL FOUR rc=20, state done, error null. tool_log all
  rc=0 (no failed tool call anywhere in the run):
  - X0: 5 calls (read_file index → write_file → 3× grep DoD)
  - X1: 6 calls (read_file → inspect check.sh → write_file →
    check+grep → re-verify ×2)
  - X2: 12 calls — the subagent found a REAL BUG IN MAIN'S
    SKELETON (the grid's horizontal-loop line used ctx.lineTo(x,
    H) drawing vertical lines instead of horizontal) and fixed it
    itself with sed (tool 5), then ran the DoD 5× to confirm.
    The edit-verify loop on a pre-seeded file worked exactly as the
    adjudication intended: it edited, it re-tested, it re-ran.
  - X3: 4 calls (read_file → write_file → check+grep → grep -n)
- Wall-clock shape check vs the cost doctrine: X2 ≈ 15.5 min / 12
  tool steps ≈ 78 s/step — right on the M9-class calibrated
  per-step wall cost (82.6 s; tool execution + step-boundary
  completion + re-prefill of the growing history dominates, not
  decode). The reasoning-off first completion + the tool-loop
  economy both match the S4.4 numbers. No deadline anywhere came
  close to firing (min remaining at last seen: ~930 s).
- Deliverable files present in the workdirs: x0/index.html 1,587 B
  (was 1,166), x1/stickman-duel.html 11,909 B (skeleton 8,545),
  x2/stickman-siege.html 15,826 B (skeleton 9,843),
  x3/stickman-rally.html 9,640 B (skeleton ~9,000).

## 6. Collection + ledger-first verification (after unload)

- [MAIN] 16:49:0x — collect-all (4× rc=20, §5.1) → unload --all
  rc=0, stack main-only → verification NOW at full t/s (F8 order;
  the §8 issues below record the 6-min idle-loaded gap before this
  unload landed).
- [VER] 16:5x–17:0x — ledger-first verification, at full t/s:
  1. DoD re-run by MAIN on the workdir files (before copy-out):
     x1/check.sh, x2/check.sh, x3/check.sh → all rc 0 (CHECK_OK);
     x0 grep DoD → 9 cards, 3 new hrefs (expected 9/3). PASS.
  2. Files copied to
     /home/blckwll/prod/de-hex-er/subagentses/ (stickman-duel.html
     11,909 B; stickman-siege.html 15,826 B; stickman-rally.html
     9,640 B; index.html 1,587 B). Target dir now 9 cards
     (grep -c class="card" = 9).
  3. Browser pass (main-driven, real HTTP server on 127.0.0.1:8090
     serving the target dir; CDP rAF shim injected
     pre-document-load — this run's lesson: a post-load rAF patch
     does NOT catch a loop that already started on native rAF in a
     visibility:hidden tab):
     - index: 9 cards, all 3 new hrefs present + resolve. PASS.
     - stickman-duel: zero console errors. Start screen renders
       (37K px left half). Click-start works. AI opponent verified
       MOVING on the right half (frame-to-frame region px changes
       while the player idles — it strafes/orbits per the spec).
       Player shooting verified: left-half px jumps while the mouse
       is held down (bullets in flight). Player movement verified:
       S-hold shifts the left-half px. The subagent also wired the
       full spec per source audit: aiAimLead (0.3 s lead), dual-gun
       fire at 0.7 s, hits/rounds (first to 5), hp-depletion
       endRound, 3-round match, matchover + R-rematch. PASS.
     - stickman-siege: zero console errors. Placement phase
       verified LIVE: 3 same-tick cell clicks → wall px appear
       (66611 → 66671 canvas px). NOTE for the record: a click
       dispatched in a SEPARATE js() tick from the mousemove loses
       to the mouse.clicked reset that runs after every update() —
       verification must dispatch move+down in the same tick; this
       is a verification-harness artifact, not a page bug (a real
       mouse delivers both in one frame). Source audit: WASD
       movement + wall-blocking with axis-sliding, placing→wave
       flow (startWave after phaseTimer), wallSteer path-avoidance
       (the DoD marker), friendly-bullet wall damage + amber burst
       on wall break, enemy fire, wave-clear → startPhaseA,
       wave-6 clear → victory. PASS.
     - stickman-rally: zero console errors. Scrolling road verified
       DYNAMIC (frame hashes differ with no input). Traffic
       spawning verified (pink stickmen present, 299 pink px vs 588
       cyan player px in the bottom half — both alive in the same
       frame). Combo/kill/invuln/collision/over-state present per
       source audit (rallyShots helper = the DoD marker). Lane
       steering could not be proven by pixel-center shift under
       synthetic keydown in a hidden tab (the stickman is thin +
       the scroll repaints the band each frame) — source-verified
       only (lane lerp + laneX target). PASS-with-note.
  4. Console errors across all four pages: ZERO. No context loss,
     no exceptions.
- [VER] Minor source finding (NOT a DoD failure, logged for the
  record): stickman-siege.html's wall-break deletes via
  `Object.keys(walls).find(k => walls[k] <= 0)` — the FIRST
  key with hp≤0, not the specific wall that just took the hit.
  With all walls at integer hp and bullets dealing exactly 1, only
  the just-damaged wall is at hp 0 at that instant, so the
  behavior is correct today; it is fragile if wall damage ever
  exceeds 1 or two walls can die in one frame. Cosmetic-grade
  nit; the game plays correctly as built.

## 7. Outcome + verdict

- VERDICT: SUCCESSFUL RUN (guardrail 4: the subagents delivered
  the load-bearing artifacts — all three window files came out of
  the pool with collect rc=20, honest tool_logs, and passed
  main's re-verification; main's only artifacts were the skeletons
  + prompts + the ledger, i.e. THINK-time glue, not fallback
  implementation).
- The second-run's failed shape ("single-file code-gen chunk,
  reasoning ON, empty workdir") is now a PASSING shape with the two
  adjudicated levers: reasoning off at the wire (FIX 1) +
  pre-seeded compiling skeletons to edit (guardrail 6). 4/4
  tasks won on the FIRST round, no re-spawn, no cap firing, no
  deadline near-miss, 4–15.5 min wall each.
- What the run proved (acceptance bar §top):
  1. 4/4 windows landed on the pool. ✓
  2. Reasoning off works: PONG reasoning field empty; first tool
     calls within the first minute (run 2: never, inside any
     deadline). ✓
  3. No silent false-success: every tool_log entry rc=0, answers
     match the logs, main re-ran every DoD. ✓
  4. Progress probe is main's mid-run eye — but it has a surface
     bug (I1): it reports running/queued only, terminal tasks
     disappear. ✓-with-I1.
  5. collect → unload → verify held from 16:48 onward; the
     6-min idle-loaded gap before it is logged (I2/I3). ✓-with-I2/I3.
  6. Install clean first-try: EXIT 0, N=2, workspaces+results
     chowned at install (FIX 7 live-verified: no manual chown this
     run), 12/12 wrappers, --reasoning live, CPUQuota single-%. ✓
  7. Delegation-size rule applied: all three expected outputs
     9.6–15.8 KB shipped against compiling pre-seeds. ✓
- Issues carried out (all in §8): I1 (probe drops terminal tasks —
  code surface), I2 (idle-loaded gap; v2 idle-unload candidate),
  I3 (main did bookkeeping while idle-loaded), I4 (robust poller
  pattern, user-adjudicated — the collect-poll loop is now the
  documented batch-loop pattern). None block the artifact; I1 +
  I4 belong in the next remediation round.
- Ledger: this file (evidence-ledger-third-practical-run.md), in
  the qwen-tory38 repo root alongside the first two ledgers.
  subagentses/ stays untracked (the user's standing instruction).

## 8. ISSUES (need fix) — user-flagged 16:5x

User question: "why are you doing verifications and thinking while
the subagents are loaded, why did they not unload after
finishing?" Three distinct issues, all real:

- I1 (HARNESS SURFACE, needs a code/doc fix): the progress probe
  `bonsai2-status --tasks` DROPS terminal tasks from its output —
  it only reports running/queued. A "poll until all terminal" loop
  built on --tasks can therefore never observe the terminal state
  it is polling for: the moment a task finishes it vanishes from
  the list. This run's poller (/tmp/poll3.sh) waited for "4
  terminal rows", never saw one (they disappeared instead), and
  spun on an empty `{"tasks":[]}` for ~5 min after the last task
  finished at 16:43. That is why main did not notice completion
  until the user asked for a status update. Fix options (for the
  next remediation round): (a) `--tasks --include-terminal` (or a
  `--terminal` flag) that carries done/failed/timeout rows for N
  hours; (b) document the drop-out behavior in the S3.2 surface +
  the skill, and make the poller pattern "list shrank to 0 →
  collect the ids for terminal state"; (c) a manager-side
  "batch finished" event in health.json. (a) is the smallest real
  fix; the worker already writes the terminal result files.
- I2 (DOCTRINE MISSED BY MAIN + candidate pool feature): the pool
  sat loaded-and-idle from ~16:43 (X2 done) to 16:49:0x (main's
  unload) — 6 min of halved main t/s with 0/8 slots in use,
  caused by I1 (no completion signal reached main) + main missing
  the F7 idle-while-loaded check (after collect-all:
  `bonsai2-status` → slots_in_use==0 → unload NOW). Nothing in v1
  auto-unloads — the stateless branch makes the unload main's
  action, and F7 is main's meter-based backstop; both failed
  because the trigger (completion) was invisible. Fix: (a) main's
  batch loop codified as "collect-all rc-terminal for every id →
  unload in the SAME turn, before any other main work" (the skill's
  hard rule already says this; this run violated it under I1's
  fog — add an explicit "poller must report completion, not just
  progress" line to the skill); (b) v2 candidate: optional
  manager-side idle-unload timer (`idle_unload_s: 300` in
  harness.yaml — after the last task goes terminal, unload the
  pool after N s unless a new task arrives; NOT the out-of-scope
  kill-for-drift, just lifecycle hygiene). Propose, don't build,
  per the post-MVP rule.
- I3 (DOCTRINE MISSED BY MAIN, minor): main ran the collect
  parse + workdir `ls` + the analysis turn while the pool was
  still loaded and idle (16:48-16:49). collect itself is allowed
  while loaded (it is the batch loop's end), but the parse +
  bookkeeping + the ledger thinking should have happened AFTER the
  unload in that same turn (F8: collect → unload → verify). Cost
  paid: one halved-t/s main turn, ~2 min. Fix: same as I2(a) —
  the unload is the FIRST main action after the last terminal
  collect, not an afterthought.
- I4 (POLLER PATTERN — user adjudication 16:5x: "the poller needs
  to be more robust and needs to catch them when they finish
  EXACTLY somehow"): the main-side batch poller is a required part
  of the stateless batch loop and must be spec'd, not improvised.
  This run's improvised poller (90 s cadence, "wait for N terminal
  rows from --tasks") failed on I1. The robust design (the one to
  codify in the skill's workflow section, and to use from the next
  run on):
  1. The PRIMARY completion signal is `bonsai2-collect <id>` itself
     — it is the authoritative terminal-state source (rc 0 = still
     running; rc 20/21/22 = terminal, payload carries the full
     result). Poll EVERY task id by collect, not by the progress
     probe: a poller that watches the probe can never see
     completion (I1), a poller that collects sees it exactly.
  2. Cadence 30-60 s: when a poll tick flips a task to terminal,
     that tick's timestamp is the completion upper bound — record
     "finished between tick N-1 and tick N" per task in the
     ledger (this run's poller log gives exactly that shape: last
     running row → first absent row).
  3. The progress probe (`status --tasks`) is the SECONDARY signal
     — mid-run steering only (steps/tool_calls/deadline_remaining,
     the guardrail-7 stuck-detection). If an id DISAPPEARS from
     the probe between ticks, treat that tick as the completion
     window and collect the id for the exact terminal state
     (catch-the-miss fallback, covers probe edge cases).
  4. Batch loop termination = "collect returned terminal rc for
     ALL ids of the batch" — never "the probe shows N terminal
     rows" (which cannot fire, I1) and never "the list is empty"
     (ambiguous: could be a probe glitch; a collect per id
     disambiguates — unknown id = exit 2, terminal = 20/21/22,
     running = 0).
  5. Same-turn rule (I2/I3 glue): the tick that completes the last
     id of the batch is the tick that runs `bonsai2-unload --all`
     BEFORE any main-side parse/ledger/analysis work.
  6. Manager-side candidate (v2, propose don't build): a batch
     completion event in health.json (e.g. `batch_finished_ts` per
     manager-observed task cohort) so a poller can wait on a file
     instead of polling — kills I1 and I4 at the source. Until
     then the collect-poll loop above is the documented pattern.

All four issues: logged now, not blocking this run's artifact
verification (which ran at full t/s since the 16:49 unload). I1 and
I4 are the code-surface + pattern items for the next remediation
round; I2/I3 are procedure (+ a v2 candidate).
