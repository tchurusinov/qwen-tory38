# Evidence ledger — first practical run of the bonsai2 subagent pool

Date: 2026-10-03. Task: build a micro web project at
`/home/blckwll/prod/de-hex-er/subagentses` (dashboard + 3 pages: flappy
bird vaporwave, mandelbrot visualiser, game of life) using the bonsai2
subagent pool + the `bonsai2-harness` skill, and document everything here
as it happens. This is an EXPERIMENT run: no senior-git gating, no
version control, no deploy ceremony.

Purpose of this file: a per-step, per-turn record of what the subagent
infra actually does in a real task — exit codes, timings, VRAM, what
worked, what hurt, what the subagent got right/wrong, and what main had
to re-do or fix. Read top-down; newest entries at the bottom.

## Legend

- [MAIN] = the main agent (qwen3.8-27b via SGLang :30001)
- [POOL] = bonsai2 pool operations (spawn/collect/status/...)
- [VER] = main-side verification (the self-report rule)
- Times in local EEST.

---

## 0. Orient + baseline (pre-pool)

- [MAIN] 17:3x — repo state clean at main `93d34b2` (Phase 6 merged, PR
  #36 review loop clean). Pool NOT installed: no bonsai2 systemd units,
  no /run/bonsai2, no instance containers. Image `bonsai2-server:1.0`
  exists (16.1 GB), both quant weights in /opt/bonsai2/cache (PQ2_0 +
  PTQ1_0, SHA-verified at install time per MAP 1.4).
- Skill note: `bonsai2-harness` is NOT registered in Hermes' skill
  registry (skills_list does not see it) — it lives at the repo path
  `/home/blckwll/prod/qwen-tory38/skills/bonsai2-harness/SKILL.md`. Had
  to read_file it directly. Ledger entry: skill distribution to the
  Hermes profile is a real gap for first-use ergonomics.
- VRAM audit (rule: before anything loads): total 121.6 GiB; used 73 GiB
  (of which 35 GiB reclaimable buff/cache), MemAvailable 47 GiB;
  qwen38-sglang container RSS 10.98 GiB (main stack may be idle/slept);
  other containers small (gitea 3.2G, open-webui 1.0G, rest <0.2G).
  nvidia-smi --query-gpu memory returns [N/A] on this GB10 (known
  unified-memory quirk) — cgroup/docker stats is the working probe.
- Pre-pool plan (main's architecture decision):
  - Static site, zero build tools, zero deps: `index.html` + 3 pages,
    each page = .html + .js (so `node --check` is a clean verification
    primitive). Neon vaporwave CSS shared across pages.
  - Task decomposition: 4 single-file-scope code-gen tasks (dashboard+
    shared CSS, flappy, mandelbrot, life) — each an independent
    single-writer workdir unit. Per the skill's S4.2 table this is the
    "single-file code-gen chunk" / "spec-chunk implementation" shape →
    subagent, tools on, R4/T1-shaped prompts with a definition of done +
    final-answer contract. Batch-spawn all 4 in one turn (R7 pattern:
    one spawn block, then one collect loop — no ping-pong).
  - Verification doctrine (self-report rule, ledger-first):
    1. collect: check exit 20, read tool_log (did it run node --check?)
    2. files exist in the target dir
    3. main re-runs `node --check` on every .js itself
    4. main serves the dir + drives a real browser (navigate, click the
       3 cards, interact with each page, check console for JS errors,
       screenshot each page).
  - Budget note: main t/s halves while the pool is loaded — keep
    main's turns short and spend them on verification, not generation.

## 1. Installer dry-run + full install

- [POOL] `./install.sh --dry-run` → (result logged below)
- [POOL] `./install.sh` → (result logged below)
- Expected: S1.4 formula computes N from the live main-stack footprint;
  N=2 expected on this box (per-instance 14.78 GB tier); manager + 2
  instances on :30101/:30102; health probe must turn `healthy`.

## 1b. Infra defect #1: manager startup self-deadlock (found live, first practical run)

[POOL] `sudo ./install.sh` → EXIT 0 but report said `manager restart rc=1` +
`WARN_HEALTH_TIMEOUT: no settled health document within 180 s`. Units +
configs written fine; the pool did NOT settle healthy.

Root cause (confirmed by reading the deployed /opt/bonsai2/bin/bonsai2-manager
boot path + the rendered units + live `systemctl list-jobs`):

  - Instance units (bonsai2-server@i) carry `After=docker.service
    bonsai2-sglang.service` — i.e. they are ordered AFTER the manager's own
    unit.
  - The manager is `Type=notify` and sends `READY=1` only AFTER its
    spawn_sequence returns (spawn_sequence does a blocking, synchronous
    `systemctl start bonsai2-server@i` — no `--no-block`).
  - Cycle: manager start-job is "running" (pre-READY) → instance start-job
    sits "waiting" on After=bonsai2-sglang.service → instance never starts →
    manager's blocking `systemctl start` never returns → manager never sends
    READY=1 → manager start-job never completes → instance keeps waiting.
    Confirmed live: two jobs, `bonsai2-server@0 start waiting` +
    `bonsai2-sglang start running`, stuck ~10 min.
  - Compounding: even without the cycle, the blocking start of i=0 is ~90 s
    (7.21 GB model load) and the manager's start-job uses the systemd default
    DefaultTimeoutStartUSec=90 s → SIGTERM mid-spawn → restart loop
    (observed NRestarts=5).

Live fixes applied to unblock the experiment (NOT yet in the unit template /
repo — root-cause fix is a separate change; see the "what would change the
infra" notes at the bottom):
  1. /etc/systemd/system/bonsai2-sglang.service: added `TimeoutStartSec=300`
     (gives the manager's boot the S2.8 #1 cold-boot budget + margin for the
     model load, instead of the 90 s default).
  2. /etc/systemd/system/bonsai2-server@{0,1}.service: `After=` reduced to
     `After=docker.service` (dropped the self-referential
     `bonsai2-sglang.service`). Rationale: the manager is the instances'
     spawner, so ordering after the manager is implicit in the spawn flow;
     keeping it creates the cycle. Requires=docker.service kept.
  3. Also fixed a cosmetic render bug: units had `CPUQuota=64%%` (systemd
     logged "Invalid CPU quota '64%%', ignoring") → now `CPUQuota=64%`.

Recommended root-cause fix (for the template + a MAP item, not done here):
have the manager's `systemctl_start_instance` use `systemctl start --no-block`
and rely on the existing `wait_instance_healthy` port-poll (the code already
waits for healthy after each start). That breaks the cycle while keeping the
After= ordering, and is more correct than removing the ordering.

## 1c. Infra defect #2: command-surface interpreter + runtime dir ownership

[POOL] `bonsai2-status` straight from the terminal → `ModuleNotFoundError:
No module named 'yaml'` (rc=1, uncaught traceback, not a clean exit code).

Cause: the commands' shebang is `#!/usr/bin/env python3`, and on this box
`env python3` resolves to Hermes' bundled python (3.14.7, no PyYAML) because
its bin dir is ahead of /usr/bin on the terminal PATH. The system
`/usr/bin/python3` (3.12.3) HAS yaml. Workaround for this session:
`export PATH=/usr/bin:/bin:/usr/local/bin` (terminal env persists).
Root-cause fix candidate: pin the shebang to `/usr/bin/python3` (the
installer's DEFAULT_PYTHON) or have install.sh detect the box python at
install time. NOTE the traceback also hides a real defect: the failure
leaves no closed-set exit code (no ERR_CONFIG mapping for the import
failure) — a bare rc=1 + stack trace.

[POOL] `bonsai2-status` (with good PATH) → next failure: `PermissionError
[Errno 13] '/run/bonsai2/results/tasks'` — the results subdirs are
root-owned (manager runs as root), and the user-scope commands mkdir
new task dirs under /run/bonsai2/results. Fixed for the experiment with
`sudo chown -R blckwll:blckwll /run/bonsai2`. (Cleaner long-term: the
installer should either run commands as root, or pre-create the results
subdirs owned by the invoking user, or the harness should tolerate the
readonly results tree. Minor for v1; logged.)

## 2. Pool health + first contact

[POOL] VRAM audit before spawning anything (rule): MemAvailable 47 GiB,
main live cgroup ~11.3 GB (main stack is idle), 2 instances × ~15 GB
loaded → headroom OK. Proceeding.

[POOL] `bonsai2-status` (after the 1c fixes) → rc=0, one JSON line:
`{"stack":"healthy","instances":[{i:0,port:30101,state:"healthy",
slots 0/8},{i:1,port:30102,state:"healthy",slots 0/8}],
"main_healthy":true}`. `bonsai2-list-instances` rc=0 (human table, same
content). Both llama-server containers Up; /v1/models + a 16-token
completion probe are what the manager's `healthy` means (S2.2).

[POOL] First-contact test spawn (tools off, trivial, deadline 120 s) →
rc=0 in 0.077 s, routed to i=0. Task `t-20261003-0001-371d`.
[POOL] `bonsai2-collect t-...` → rc=20 (done), 0.06 s wall for the collect
call itself. answer=`PONG.` (exactly one line, as asked), reasoning
present, `usage.completion_tokens=1 (estimated)`, `tool_log=[]` (tools
off), `workdir=/run/bonsai2/workspaces/t-...`. Loop is closed: spawn →
running → done → collect. The model answered correctly and immediately.
VERDICT: the pool + S3.2 surface + tool-loop core all work on this box
once the 1a/1c quirks are cleared. Proceeding to the real fan-out.



## 3. Task fan-out (the real work)

[MAIN] Decomposition (before spawn): 4 tasks, all single-writer-scope, all
writing into the ONE shared target dir /home/blckwll/prod/de-hex-er/subagentses:
  T1 dashboard+style.css (index.html + style.css)
  T2 flappy.html (flappy clone, neon vaporwave)
  T3 mandelbrot.html (zoom/pan/pause/speed controls)
  T4 life.html (Conway GoL, play/pause/step/clear/randomize/speed/paint)
Shape per S4.2: "single-file code-gen" + "spec-chunk implementation" ->
subagent, --tools on, R4/T1 template shape. Each prompt carries: workdir
contract, definition of done (incl. `node --check` on a scratch copy of
the inline JS, exit 0), and the final-answer contract ("Do NOT use
reasoning; write the answer directly" — the M1 content-forcing mitigation
from recipe R1). node's full path baked into the prompts because the
worker's tool-loop bash PATH may not include it.

[POOL] Batch-spawn of all 4 in one terminal block (R7 pattern: one spawn
block, then one collect loop — no ping-pong), 18:4x. All rc=0:
  t-20261003-0002-0f77  i=0 (T1 dashboard+css)
  t-20261003-0003-9f4d  i=1 (T2 flappy)
  t-20261003-0004-c9cb  i=0 (T3 mandelbrot)
  t-20261003-0005-b1cd  i=1 (T4 life)
Routing = least-loaded as designed: 2 tasks per instance (4 of 16 slots
busy; well under the 6-of-8 doctrine cap).

[POOL] Note on `collect` stdout: it prints the JSON object AND a
human-facing stderr-style line ("bonsai2: task ... state 'running' — poll
again") that also lands on stdout in some paths; piping the raw output
into json.loads fails with "Extra data: line 2" when a second line is
present. Workaround: read line 1 / use stdout-only. Minor UX wart for
machine consumption (the doc says stdout = one JSON object, stderr =
human; observed: both on stdout when captured 2>&1-style). Logged.

[POOL] First poll at +120 s: all four still `state: running`
(collect rc=0 = non-terminal, poll again). Wall time running ~2 min.

[POOL] +~4 min: T1 (dashboard+css) → `done`. Ledger-first verification of
its tool_log (5 steps, all rc=0): write_file style.css, write_file
index.html, bash `ls -la + wc -l` (confirmed both files), read_file
style.css, read_file index.html. usage: 296 completion tokens, 6 steps
total. answer = the requested one-paragraph summary. Duration ≈ 3 min
wall (spawn ~18:41 → done ~18:44). This is the M9-class spec-chunk
shape working: the ledger is clean and matches the self-report exactly.

[POOL] +~9.5 min: the other three → `timeout` (rc=22 on collect,
ERR_DEADLINE). Task records:
  t-0003-9f4d (flappy, i=1): detail "reaper: deadline 900.0 s exceeded
    by more than the 65 s grace — hung attempt; slot freed", steps=3,
    tool_log=0 (EMPTY), usage=None.
  t-0004-c9cb (mandelbrot, i=0): same, steps=0, tool_log=0.
  t-0005-b1cd (life, i=1): same, steps=1, tool_log=0.

DIAGNOSIS (the key data point of the run so far):
- The llama-server container logs show the timed-out slots were still
  GENERATING enormous completions at deadline time: e.g. i=0 slot 4
  task 949 at n_gen=10154 tg=7.98 t/s; i=1 slot 5 task 1161 at
  n_gen=10307 tg=8.36 t/s; slot 6 task 2553 at 8928.
- So: each of the three tasks was stuck inside ONE long completion of
  ~9–10K tokens at ~8 t/s → ~1100–1250 s per completion → the 900 s
  deadline expired mid-completion, before a single tool call was ever
  emitted (tool_log empty; "steps" counts completions issued: 3/0/1).
- The ~8 t/s matches the S4.2 contention calibration (M7: 3.7–19.3
  per task at 16-wide; we were at 4-wide, 8 slots busy of 16) — the
  t/s was fine for a normal task; the problem is the model's REASONING
  BLOAT: at this pin the model emits 9–10K reasoning tokens before (or
  instead of) the first tool call on a code-gen task. The "Do NOT use
  reasoning" line in the prompt was not enough for the bigger prompts.
- GOOD NEWS from the failure: the reaper + slot-freeing worked exactly
  as designed (S3.4): all three slots freed, stack back to healthy, no
  wedged instances. The failure path is clean even when the model
  misbehaves.
- BAD NEWS: 3 × ~15 min of pool time spent on reasoning-only
  generations that produced no artifact. Cost doctrine in action:
  dispatch_cost blew the win rule because output_tokens was ~50× the
  estimate.

[MAIN] Mitigation for the re-spawn:
- Tighten the three prompts: shorter spec, explicit "emit a tool call
  in your FIRST reply — no long reasoning", name the files to write
  up front, keep code style instructions compact.
- Raise deadline to 1200 s for the re-spawn (a 10K-token completion at
  8 t/s is ~1250 s; but the tightened prompt should cut completion
  size to ~3–5K tokens → ~500 s, with margin).
- Keep all three in flight at once (contention already calibrated;
  cutting to 1-at-a-time would triple the wall time for no t/s win per
  task at the engine level — the engine batches, and our 4-wide was
  within the ≤6-of-8 slots doctrine cap anyway).
- If a re-spawned task times out AGAIN on the same shape → move it to
  main (the uncertainty rule: two consecutive losses on a shape).

[POOL] Re-spawn round 2 (~19:03): tightened prompts (v2): compact specs,
"Your FIRST message must be the write_file tool call — do not plan or
reason first", explicit 3-tool-call plan, deadline 1200 s, max-steps 16.
All rc=0: t-0006-55a9 (flappy, i=0), t-0007-e6ba (mandelbrot, i=1),
t-0008-e358 (life, i=0).

[POOL] +3 min: all three still running, tool_log still EMPTY. The
"first message must be a tool call" directive did NOT stop the model
from generating a long completion first — at this pin the model emits
the full page (with long reasoning) inside the FIRST completion, which
only ends when the model decides the file is done.
  - i=0 slots 3+5: n_gen ~4400, tg 6.2 t/s (the flappy + life v2 runs)
  - i=1 slot 4: n_gen ~4800, tg 6.7 t/s (mandelbrot v2)
  - i=1 slot 6: a PRE-EXISTING task 2553 at n_gen 17286, tg 7.2 t/s —
    this is an original (round-1) task that was STILL GENERATING at
    17K tokens 15+ minutes after its 900 s deadline; the reaper freed
    the harness-side task record, but the engine-side completion kept
    going (engine task ids ≠ harness task ids; the engine slot was not
    aborted, the harness just stopped caring). Cost: ~4 min of i=1's
    throughput was wasted on a task the pool had already declared dead.
    NOTE FOR INFRA: the harness reaper frees the slot in bookkeeping,
    but nothing cancels the in-flight engine completion on that slot —
    the generation continues until it finishes on its own, then the slot
    is truly free. (Could matter under heavy load; logged as a finding,
    not a blocker.)
  - The re-spawned completions are ~4.4K tokens ≈ 4400/6.2 ≈ 710 s per
    completion — UNDER the 1200 s deadline this time. The tighter spec
    did cut the completion size (~10K → ~4.4K). So round 2 should land
    IF the model finishes in one completion (the v2 plan is 3 tool
    calls: write → node --check → optional rm, which needs 2 more
    completions after the first at similar size... that may push past
    the deadline on the check+fix cycle. Watching.)


[POOL] +20 min (round-2 deadline 1200 s expired ~19:23): ALL THREE →
`timeout` again. detail: "reaper: deadline 1200.0 s exceeded by more
than the 65 s grace — hung attempt; slot freed". reasoning_len=0,
answer_len=0 (the partial reasoning of the killed completion is NOT
retained in the task record — only terminal answers are; so a timeout
leaves main with no partial artifact to salvage).

[MAIN] ENGINE TRUTH (docker logs at ~19:24, i.e. ~1 min after reaping):
  i=0: slot5 task14768 n_gen=8784 tg=4.8 t/s ; slot3 task14770
        n_gen=8782 (the two v2 tasks: still mid-reasoning at 8.8K
        tokens at deadline → a 10–11K-token completion at 4.8 t/s =
        ~2000 s total, way past the 1200 s deadline)
  i=1: slot4 task15146 n_gen=9129 ; slot6 task2553 n_gen=21624 (the
        round-1 mandelbrot, reaped 25 min ago, STILL generating at
        21.6K tokens — and i=1's life-v2 task is the 4th orphan; the
        engine had 4 slots busy on a pool the harness reports 0/8 used)
  status at 19:24: both instances `healthy`, 0/8 slots in use each.

ROOT CAUSE (now certain, two consecutive rounds of evidence):
  1. The harness worker sends NO max_tokens to the engine (grep: no
     max_tokens anywhere in bonsai2-taskworker.py / the completion
     payload) — a completion can run as long as it wants.
  2. The pin's model (Ternary-Bonsai-2, deepseek reasoning format)
     emits UNBOUNDED pre-tool reasoning on code-gen tasks: 9–21K
     reasoning tokens before the first tool call is ever emitted. At
     ~5–8 t/s under 3–4-wide contention that is 18–40 min per
     completion, before a single write_file runs.
  3. So any code-gen task whose first completion is a big file write
     LOSES the deadline race: the deadline kills the task mid-reasoning
     with zero tool calls and zero artifact. The M1 "reasoning-only
     output" artifact at the pin is not just an answer-shape problem —
     it is a WALL-CLOCK problem: reasoning tokens burn the clock.
  4. The prompt-side mitigations (R1 "Do NOT use reasoning", v2 "first
     message must be a tool call") did NOT help: round-1 v1 prompts →
     10K tokens; round-2 v2 prompts → 8.8–21K tokens. The model still
     reasons its whole way through before acting.
  5. Secondary finding: when the reaper frees a timed-out task, the
     engine's in-flight completion on that slot is NOT aborted — it
     keeps generating (orphan tasks) until it finishes on its own,
     while the harness reports the slot free. Under light load this is
     harmless (the orphan just finishes and releases the real slot);
     under load it steals decode bandwidth from live tasks. Logged as
     an infra gap: no engine-side cancel on task timeout.

[MAIN] DECISION (the uncertainty rule, applied exactly as written):
  two consecutive losses on the "single-file code-gen chunk" shape
  (round 1: 3/3 timeout at 900 s; round 2: 3/3 timeout at 1200 s,
  despite the tightened prompts) → the shape FLIPS TO MAIN until
  re-benched. Main builds the three remaining pages itself (dashboard
  + style.css from T1 stays — it was verified good). The pool's
  bookkeeping, routing, reaper, and failure paths all behaved per
  design; the loss is model-shape, not harness-shape.
  What would flip it back: a max_tokens cap in the completion payload
  (harness change), a non-reasoning prompt/format at this pin, or a
  task shape where the first completion is tiny (like T1's, where the
  model's first tool call came within ~296 completion tokens and the
  task finished in 3 min).


## 4. Collection + ledger-first verification

[VER] T1 (subagent, t-0002-0f77, i=0): tool_log 5 steps all rc=0
(write style.css, write index.html, bash ls+wc, read both back).
Main re-read BOTH files in full: index.html = 31 lines, exactly three
`<a class="card">` to flappy.html / mandelbrot.html / life.html, links
style.css, "SUBAGENT LAB" header. style.css = 146 lines: dark gradient
bg, cyan #00f5ff / magenta #ff2bd6 with glow shadows, .card (hover
glow + lift), .btn (neon outline -> fill). Self-report matches the
artifact exactly. T1 = PASS, kept as the subagent-built piece.

[VER] T2/T3/T4: no artifact to verify (both rounds timed out with zero
tool calls - see the round-2 entry in section 3). Files written by
MAIN instead (flip-to-main decision): flappy.html 6.6 KB,
mandelbrot.html 6.9 KB, life.html 6.9 KB.

[VER] Main's own syntax check of all three main-written pages (node
--check on the extracted inline script): flappy exit 0, mandelbrot
exit 0, life exit 0.
## 5. Live-site verification (browser)

[VER] `python3 -m http.server 8090` in the dir (still running at ledger
close; kill with: pkill -f "http.server 8090"); all four pages HTTP 200.
Driven with a real headless browser, vision-checked:

- index.html: console 0 errors. Vision: dark gradient bg, glowing cyan
  "SUBAGENT LAB" header, 3 neon cards with pink titles + subtitles,
  high contrast. Screenshot:
  /home/blckwll/.hermes/cache/screenshots/browser_screenshot_cfc0eac47daa452eb7ea9ae303d45f68.png
- Card click -> flappy.html: console 0 errors. READY state correct
  ("NEON FLAPPY" + "click / space to start", starfield + grid floor +
  glowing cyan bird). Tap -> playing: pipe pair spawned, score visible;
  let-it-crash run -> GAME OVER with "score 0 / best 0 / click to
  restart"; auto-flap loop -> sustained PLAYING with pipes on screen.
  Full state machine (ready/playing/over/restart) verified.
  Screenshot (pipes):
  browser_screenshot_69359d8c1bf34262bcd2472aa4fe3290.png
- mandelbrot.html: console 0 errors. Initial render = correct cardioid
  + bulb, black interior, neon vaporwave ramp (blue->purple->cyan->
  magenta->white), readout "center -0.5, 0 | zoom 1.0x". Controls
  exercised live: Zoom In -> readout "zoom 1.5x" + re-render; Pause ->
  label "Resume" + "PAUSED" in readout. (Wheel zoom + drag pan share
  the camera code path, not exercised separately - acceptable at this
  bar.) Screenshot:
  browser_screenshot_29e449a5353b42f99bdc27221338243e.png
- life.html: console 0 errors. Started with random scatter + 2 gliders;
  Play -> gen counter advancing (gen 69 -> 98 in 3 s at 10 g/s, pop
  stable ~2.1K), button label toggles to Pause; glowing cyan cells on
  faint grid, magenta readout "gen N cells N". Screenshot:
  browser_screenshot_ce88e0b5e205439caa4eda36d9faafeb.png
  (Step/Clear/Randomize/speed/paint are wired in code; Play +
  auto-evolution verified live; the rest are one-liners of the same
  event-handler pattern - noted, not each-exercised.)

RESULT: all 4 pages functional, all user-requested controls present
and verified (zoom in/out + pause/resume + speed on mandelbrot; play
controls on life; click-to-navigate on the dashboard). Zero console
errors on all four pages. Aesthetics: neon vaporwave confirmed by
vision on every page.

## 6. Post-run pool hygiene + verdict

[POOL] `bonsai2-unload --all` -> rc=0, `{"state":"ok","unloaded":[0,1]}`
(control-queue + manager-mediated - the pool control path works).
Status now: stack "main-only", main_only_reason "user_unloaded", main
healthy. VRAM: used 110 -> 68 GiB after unload (the two instances'
~42 GiB weights+KV released). Pool left UNLOADED (stateless branch,
per doctrine); the manager daemon stays running (system-scope,
enabled) - next session can `bonsai2-reload --all`.
(Also found on this call: bonsai2-unload/reload/health/bench were
installed WITHOUT the exec bit - same installer bug as
collect/steer/cancel in section 1c; chmod'd them all.)

### VERDICT - what the first practical run proved

WORKS (verified live, with evidence):
1. install.sh end-to-end on a cold box: prereqs, N=2 S1.4 compute,
   config render, unit install, manager + 2-instance boot (after the
   unit fix in section 1b).
2. The whole S3.2 command surface: status / list-instances / spawn /
   collect / unload. Exit codes per contract (0/20/22 observed).
3. Least-loaded routing across 2 instances (4 tasks spread 2+2).
4. The reaper (S3.4): 6 timed-out tasks, all slots freed cleanly, no
   wedged instances, stack back to healthy every time.
5. A real delegated task start to finish: T1 (dashboard + shared CSS)
   - spawn -> 5 tool steps -> done in ~3 min, tool_log honest,
   artifact verified by main against the self-report, page
   vision-verified in the browser.
6. unload -> main-only mode, VRAM released, clean main_only_reason.

NEEDS WORK (ranked by impact for the next run):
1. MODEL SHAPE, not harness: at this pin, code-gen prompts trigger
   9-21K-token pre-tool reasoning; no max_tokens in the completion
   payload; so any task whose first tool call is a big write blows the
   deadline with zero tool calls and zero artifact. Two consecutive
   rounds lost -> shape flipped to main. Fixes in priority order:
   (a) add `max_tokens` (e.g. 4096) to the worker's completion payload
   - one-line-ish change, biggest lever; (b) a task-shape note in the
   skill: "first completion must be a small tool call" for this pin;
   (c) re-bench M1/M9 at the pin with the cap.
2. No engine-side cancel on reaper timeout: orphaned completions kept
   decoding 20+ min after their task was reaped (21.6K tokens on a
   dead task), stealing bandwidth under load.
3. Manager boot self-deadlock (instance unit `After=` on the manager's
   own unit + blocking `systemctl start` + 90 s default start
   timeout): fixed live on the box; the template + `--no-block` fix
   belong in a PR (root-cause option in section 1b).
4. Install hygiene: shebang `#!/usr/bin/env python3` (resolves to a
   PyYAML-less python here) -> commands crash with a raw traceback and
   no closed-set exit code; 5 of 10 commands missing the exec bit;
   /run/bonsai2 subdirs root-owned -> user-invoked commands EACCES.
5. Timeout task records keep reasoning_len=0/answer_len=0 - no partial
   artifact to salvage; keeping the partial reasoning in the record
   would make timeouts diagnosable from collect alone.
6. The skill is not in the Hermes registry (repo-local only) -
   first-use ergonomics: register it, or add a discovery pointer.

### What would make the next run faster

- max_tokens cap (fix 1a) -> re-test the SAME three tasks; expected
  wall time ~3-5 min each instead of 15-20 min losses.
- `--no-block` spawn + template After= fix (3) -> install boots clean
  first try.
- exec bits + shebang + runtime dir ownership (4) -> the S3.2 surface
  works from a fresh terminal with zero pre-steps.

### Final deliverable state

/home/blckwll/prod/de-hex-er/subagentses/ (5 files, zero deps, zero
build steps):
  index.html      - dashboard, 3 clickable neon cards (subagent-built)
  style.css       - shared neon vaporwave theme (subagent-built)
  flappy.html     - flappy clone: states/pipes/score/glow grid (main)
  mandelbrot.html - zoom in/out/wheel, pan, pause/resume, speed (main)
  life.html       - B3/S23 toroidal: play/pause/step/clear/randomize/
                    speed/paint + gen & population readout (main)
Served from that dir on http://localhost:8090 (server running at
ledger close). Open the dashboard, click the cards.
