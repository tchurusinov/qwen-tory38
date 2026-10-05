# Fix proposals — second practical run (evidence-ledger-second-practical-run.md)

Source of truth: evidence-ledger-second-practical-run.md (verdict §6: UNSUCCESSFUL RUN —
infra exercised via subagents failed; 1/4 windows delivered by the pool, 3/3 written by
main after two rounds × 3 attempts, 6 losses, 0 tool calls each).
This file proposes fixes with exact code locations. All paths are repo-relative
(/home/blckwll/prod/qwen-tory38). bin/ and /opt/bonsai2/bin are byte-identical as of
2026-10-04, so every fix below lands in the repo first and is deployed by re-running
./install.sh.

Root cause per the ledger's own adjudication (§3 18:51, §6.3): on this pin the subagent's
FIRST completion for a full-file write is a 15–17K+ token reasoning block that does not
contain the first tool call; at 4–7 t/s that outruns any sane cap+deadline pairing. The
harness bookkeeping was sound; the gap is (1) reasoning is ON, (2) no progress signal,
(3) the shape was not split/scaffolded, (4) orphans are not cancelled by their own worker,
(5) one install ownership bug, (6) the manager reaper runs without cfg.

Fixes are ordered by the ledger's three user-named hindrances (§6.3), then the
discovered defects, then the skill guardrails (§6.4).

===========================================================================
FIX 1 (Hindrance 1, the biggest single lever) — reasoning OFF by default
===========================================================================

Problem.
The pinned prism fork already supports turning reasoning off client-side:
  third_party/llama.cpp/tools/server/server-common.cpp ~line 1312:
  `reasoning_effort: "none"` -> `enable_thinking = false` and the kwarg is erased from
  the chat template. A second knob exists at ~line 1302:
  `chat_template_kwargs: {"enable_thinking": "false"}`. No engine change is needed —
  only the client (the worker) must send the field.

Evidence (this run).
The PONG probe's result carried a non-empty `reasoning` field for a one-word answer.
Every lost completion was a 15–17K+ token reasoning block before any tool call.
F1's max_tokens caps the TOTAL (reasoning+content+toolcall); it does not prefer
"less reasoning, more action".

Changes.
1. bin/bonsai2_harness.py, CONFIG_DEFAULTS dict (~line 214-220, the
   "first-practical-run remediation" block; next to "task_default_max_tokens"
   line 215 and "arm_reasoning_control" line 218): add
       "task_reasoning_off": True,  # second-run remediation: default reasoning off
   Default ON (i.e. off-by-default) per §6.3(a)/§6.4(5); the explicit exception is a
   spawn flag, not the default.

2. bin/bonsai2_harness.py, load_config boolean gate (~line 397): add the key to the
   validated-bool tuple so a non-bool value is a ConfigError (exit 2) like its
   siblings:
       for key in ("arm_reasoning_control", "cancel_on_deadline", "task_reasoning_off"):

3. bin/bonsai2_harness.py, task record creation (~line 2022-2028, next to the
   "arm_reasoning_control" record field): add
       # second-run remediation: reasoning-off threaded into every completion body.
       # Gated by a spawn flag else the S3.6 task_reasoning_off default.
       "reasoning_off": (
           parsed["reasoning_off"]
           if parsed["reasoning_off"] is not None
           else bool(cfg.get("task_reasoning_off", True))
       ),

4. bin/bonsai2_harness.py, _parse_spawn_args (~line 1534-1545 args dict; flag
   parsing ~line 1570-1640): add "reasoning_off": None to the args dict and a
   `--reasoning on|off` flag (mirror the `--tools on|off` handler at line 1570-1575):
       if a == "--reasoning":
           if i + 1 >= len(argv) or argv[i + 1] not in ("on", "off"):
               return "ERR_ARGS: --reasoning must be on|off"
           args["reasoning_off"] = argv[i + 1] == "off"
           i += 2
           continue
   (`--reasoning on` = the logged exception; `--reasoning off` = the default.)

5. bin/bonsai2-taskworker.py, tool-loop body (lines 1144-1180; the dict starting at
   line 1144 with "model"/"messages"/"tools"/"stream" and the F1/F11 gated splices):
   add, gated exactly like F1 (legacy record without the key keeps the
   byte-identical payload — the test at tests/test_remediation_first_practical_run.py:473
   asserts that discipline for max_tokens and must keep passing):
       **(
           {"reasoning_effort": "none"}
           if rec.get("reasoning_off")
           else {}
       ),
   (Use `reasoning_effort: "none"`, not chat_template_kwargs: the fork's parser at
   server-common.cpp:1312 handles it and erases the kwarg so the deepseek template
   cannot re-arm thinking; chat_template_kwargs is the fallback if a future pin stops
   honoring the OAI field.)

6. bin/bonsai2-taskworker.py, _run_attempt (lines 536-549, the pre-loop body): add the
   same gated splice after the F1/F11 splices, so --tools off tasks also get
   reasoning-off (the PONG class of task is exactly this path).

7. etc/harness.yaml (template, after the F11 block ~line 35-41):
       # second-run remediation: default reasoning off (reasoning_effort "none"
       # per completion). The engine's deepseek reasoning template otherwise emits
       # a 15-17K+ token pre-tool reasoning block on a full-file write (second
       # practical run, 2026-10-04). ON is the explicit, logged exception
       # (--reasoning on / task_reasoning_off: false).
       task_reasoning_off: true

Acceptance (per §6.3).
- A PONG spawn returns an empty (or <256-char) `reasoning` field in the collect
  payload.
- One real small task completes inside its deadline with tool_log > 0 and the first
  completion's completion_tokens an order of magnitude below the 15-17K observed.
- New hermetic tests (mirror TestToolLoopWireBody in
  tests/test_remediation_first_practical_run.py:343):
  * body carries "reasoning_effort": "none" by default;
  * `--reasoning on` omits the key;
  * a record without the key (pre-fix record on disk) omits the key (legacy
    byte-identical wire, the line-473 test's discipline).

===========================================================================
FIX 2 (Hindrance 1, adequate loop) — the verify loop becomes a harness
contract, and the F2 nudge is bounded
===========================================================================

Problem.
The implement → test-until-working discipline is currently prompt-dependent (main's
DoD quality): W0 verified because its DoD was a grep and it ran it; the three
generation tasks never reached a test because they never emitted a tool call.
Second defect discovered while auditing the F2 nudge: the nudge loop is UNBOUNDED in
the "empty completion cut at the cap" case — a completion with finish_reason="length"
and empty answer is re-nudged every step up to max_steps (32), so a model that keeps
emitting pure reasoning re-spends up to 32 completions of cap-sized reasoning before
failing.

Changes.
1. bin/bonsai2-taskworker.py, the F2 truncation-guard site (lines 1320-1338: the
   `if finish_reason == "length" and not answer.strip():` block that appends the
   "cut off mid-reasoning" nudge and `continue`s): add a bounded counter
   `empty_length_streak` (initialize next to `consecutive_bad` at line 1035):
   - reset it to 0 on ANY completion that carries answer content, a tool call, or a
     non-"length" finish_reason;
   - increment it in the nudge branch and, when it reaches 2, fail the task instead
     of nudging a third time:
       _set_state(rec, "failed", "ERR_TRUNCATED_NO_PROGRESS",
                  "2 consecutive cap-cut empty completions — the model kept "
                  "reasoning without acting (second-run remediation)", d)
       _append_result_file(rec, tool_log, steps, workdir)
       _salvage_partial_result(rec, tool_log, steps, workdir,
                               answer, reasoning, usage)  # keep the text
       return 0
   (ERR_TRUNCATED_NO_PROGRESS is a NEW error name: add it to the worker's top-of-file
   error constants next to ERR_TOOL_INVALID_CALL / ERR_TOOL_TIMEOUT, and to
   bin/bonsai2_harness.py's collect detail text if it maintains a closed set —
   collect maps state "failed" to exit 21 regardless, so the closed-set surface is
   unaffected.)
   Rationale: one nudge is the F2 contract (resumable truncation); two empty
   cap-cuts is "the model is reasoning its way to death" — fail fast and salvage,
   don't burn 30 more cap-sized completions.

2. bin/bonsai2-taskworker.py, the F2 nudge message (lines 1330-1336): append one
   sentence to the nudge text making the verify step explicit:
   "If your task's definition of done includes a test command, run it with a bash
   tool call now and report its exit code; if it fails, fix the output and re-run
   (at most 2 more times) before giving a final answer."
   This bakes the "implement → test until working" loop into the loop itself instead
   of relying on the prompt's DoD prose.

3. Skill contract (skills/bonsai2-harness/SKILL.md, new subsection under the
   Prompt templates S4.1 area, ~line 468): "Every task prompt ships with an
   EXECUTABLE DoD (node --check / browser smoke / grep counts / a python -c assert).
   A prompt without an executable DoD is not delegated — the DoD is part of the
   delegation, not an annotation." Plus: "main re-runs the DoD itself on collect
   (ledger-first — existing rule, now mandatory)."

===========================================================================
FIX 3 (Hindrance 3a) — the worker cancels its own orphan on every
deadline exit (the F11 gap proven live this run)
===========================================================================

Problem.
F11's orphan cancel is issued ONLY by the manager reaper (bin/bonsai2_harness.py
reap_stale, lines 1200-1217) or a collect-time reap (bin/bonsai2_harness.py line
2218). This run's three round-2 timeouts were reaped by the WORKER's own deadline
check (task records say: `detail: deadline 3600.0 s expired mid-loop`) — the manager
never got a chance to cancel. Evidence on disk: only ONE of the three round-2 cmpl
sidecars exists (/run/bonsai2/results/tasks/t-20261004-0008-a676.cmpl.json); 0007 and
0009 have none, and the engines' orphan slots kept decoding (the ledger's 17:45/17:55
n_gen observations). The engine control endpoint has exactly ONE action
(`reasoning_end`, third_party/llama.cpp/tools/server/server-context.cpp:4832 and
2363) — it force-ends the reasoning budget but does NOT kill the slot, so even a
successful cancel leaves the slot decoding its answer half to the stop token / cap.
The cancel is still the cheap, correct first move; the worker just never makes it.

Changes.
1. bin/bonsai2-taskworker.py: add a helper next to _write_cmpl_sidecar (line 873):
   def _cancel_inflight_completion(rec: dict, port: int, cmpl_id: str | None) -> None:
       """Second-run remediation: the worker's OWN F11 cancel. On every
       deadline exit the in-flight completion keeps decoding on the engine
       (the ledger's 'orphans steal bandwidth for ~20-25 min'). The manager
       reaper only cancels when IT is the one reaping — when the worker's own
       deadline fires first (the common case: the worker outlives its deadline
       by the socket cap), no cancel is ever issued. POST
       /v1/chat/completions/control {id, action: reasoning_end} best-effort:
       any failure logs, never changes the verdict."""
       if not cmpl_id or not rec.get("arm_reasoning_control"):
           return
       try:
           ident = H.load_engine_identity()
           api_key = ident.get("api_key", "")
       except H.ConfigError:
           api_key = ""
       url = f"http://127.0.0.1:{int(port)}/v1/chat/completions/control"
       body = json.dumps({"id": cmpl_id, "action": "reasoning_end"}).encode()
       req = urllib.request.Request(url, data=body, headers={
           "Authorization": f"Bearer {api_key}",
           "Content-Type": "application/json"}, method="POST")
       try:
           with urllib.request.urlopen(req, timeout=5.0) as resp:
               data = json.loads(resp.read())
           ok = bool(data.get("success"))
       except Exception as exc:  # noqa: BLE001 — best-effort cancel
           ok = False
       _log(f"{rec.get('task_id', '?')}: worker self-cancel "
            f"({'ok' if ok else 'no-op/failed'}: cmpl {cmpl_id[:24]!r})")
   (Duplicating the 8-line POST rather than importing
   _cancel_completion_on_instance from the harness keeps the worker's single-module
   deploy shape; both call sites stay in sync because the wire contract is one
   JSON object — the engine's endpoint is the source of truth.)

2. bin/bonsai2-taskworker.py, the last live cmpl_id is already known inside the loop:
   `res.get("cmpl_id")` from the step's _stream_chat (line 1260 writes it to the
   sidecar). Track it in a loop-local `last_cmpl_id` (set next to line 1260:
   `last_cmpl_id = res.get("cmpl_id") if res.get("ok") else last_cmpl_id`, init
   `None` near line 1041). Then call the helper at the three in-loop deadline exits:
   - line 1043-1055 (step-boundary deadline check, before `return 0`):
       _cancel_inflight_completion(rec, port, last_cmpl_id)
   - line 1182-1211 (deadline expired after step — the F4 late-result site; call it
     AFTER _salvage_partial_result so the salvage's log lands first):
       _cancel_inflight_completion(rec, port, last_cmpl_id)
   - line 1262-1275 (deadline expired before the step attempt):
       _cancel_inflight_completion(rec, port, last_cmpl_id)
   And in _main's pre-loop path (lines 1655-1680, the two deadline-exit sites of the
   --tools off attempt loop): capture `res.get("cmpl_id")` after line 1666 and call
   the helper at both timeout sites.

Acceptance.
- Unit test: run _run_tool_loop against a ToolEngine that returns one
  length-cut empty completion then a final; assert a control POST was issued for the
  step's cmpl_id on the deadline path (the rig already has ToolEngine.request_log in
  tests/fixtures/tool_engine.py — add a control-route or capture the POST via the
  same mock pattern tests/test_remediation_first_practical_run.py:787
  test_reaper_issues_control_post_on_deadline_reaping uses for the manager path).
- Live acceptance (next run): on any deadline reaping, the engine log for the task's
  slot shows the reasoning block ending promptly; the ledger's "5 min after reap the
  task still generating" observation no longer holds for worker-self-reaped tasks.

===========================================================================
FIX 4 (Hindrance 3b) — split-the-write / scaffold shapes: a harness
capability, not just prompt prose
===========================================================================

Problem.
Six attempts at the 9–12 KB generation shape died in the first completion. The
ledger's §6.2 finding is the design input: prompt size was nearly identical across
the winner and the losers — expected OUTPUT size is the differentiator. The winning
task MODIFIED ~1.2 KB of a pre-seeded file; the losers each generated 9–12 KB from
an empty workdir.

Changes.
1. Skill (skills/bonsai2-harness/SKILL.md, new section "Delegation-size rule
   (second-run adjudication, 2026-10-04)" next to the Recipe selection guide,
   ~line 838):
   - Expected output < ~2 KB, or a pure modification/verification: plain prompt,
     no seed.
   - Expected output > ~4 KB: pre-seed a known-good SKELETON in the task workdir
     before spawn (main writes it at THINK time; it must already pass the DoD's
     executable test, e.g. node --check of an empty-shell script), and the prompt
     says "fill/edit this file", not "write this file".
   - For large files, SPLIT THE WRITE into several small tool calls in the prompt
     itself: "step 1 write <head>+CSS, step 2 the script skeleton, step 3 the game
     loop / render code, step 4 run the DoD test and fix until exit 0" — many small
     bounded completions instead of one 15–25K-token bet.
   This is prompt/workdir discipline, not new worker code — the tool loop already
   executes exactly this shape (W0's read → write → verify loop is the proof).

2. bin/bonsai2-spawn docstring (lines 8-12, the usage block): add a one-line note:
   "Large single-file generation (>~4 KB expected output): pre-seed a skeleton in
   --workdir and prompt for edits, not authorship (second-run rule)."

===========================================================================
FIX 5 (Hindrance 3c) — a progress probe: main sees progress between poll ticks
===========================================================================

Problem.
Fire-and-collect gives main no mid-run visibility: it learns a task is stuck only
when the deadline fires. The ledger's Q2: "no cheap 'is it making progress' signal
short of reading engine n_gen by hand."

Changes.
1. bin/bonsai2_harness.py, new function next to cmd_status (~line 1800s, the
   status-family block; reuse iter_tasks + results_dirs + the cmpl sidecar reader at
   line 1078):
   def progress_report(d: dict, task_ids: list[str] | None = None) -> list[dict]:
       """Second-run remediation: the progress probe. For each live task:
       state, steps, tool_log length, created_ts / running_ts, deadline_s,
       and deadline_remaining_s = running_ts + deadline_s - now (negative =
       past deadline, about to be reaped). When the task's cmpl sidecar
       exists (<tasks>/<task_id>.cmpl.json), the sidecar's cmpl_id is
       included so the caller can read the engine's own /slots n_decoded for
       that completion (the engine's GET /slots JSON carries
       next_token[0].n_decoded per processing slot —
       third_party/llama.cpp/tools/server/server-context.cpp:663) without
       parsing engine logs. Read-only: the probe never mutates records."""
   Fields per task:
       {"task_id", "state", "steps", "tool_calls": len(tool_log),
        "deadline_s", "deadline_remaining_s", "cmpl_id": str | None}

2. bin/bonsai2-status: accept a `--tasks` flag (new; the command's docstring lines
   8-22 describe the status surface — add the flag contract: `bonsai2-status
   --tasks [TASK_ID ...]` prints the progress JSON list and exits 0; no flag =
   current behavior, byte-identical) and wire it to progress_report in dispatch /
   the status command body (bin/bonsai2_harness.py cmd_status).

3. Skill (skills/bonsai2-harness/SKILL.md): add "PROGRESS PROBE" to the main-loop
   guardrails (see FIX 9): while the pool is loaded, main polls
   `bonsai2-status --tasks` every ~2-3 min; steps==0 && tool_calls==0 &&
   deadline_remaining_s < 0.5×deadline_s = "stuck in reasoning" → steer a nudge or
   cancel+reshape, instead of waiting for the reaper.

===========================================================================
FIX 6 (Hindrance 3d) — deadlines computed from measured t/s, never guessed
===========================================================================

Problem.
Round 1 paired 32768 with the 1800 s default; round 2 paired 16384 with 3600 s —
both still under for a 17K-token first completion at ~4 t/s under contention. The
ledger's rule: "deadlines computed from measured t/s × expected token size + margin,
never guessed."

Changes.
1. Skill (skills/bonsai2-harness/SKILL.md, new subsection in the Cost doctrine area,
   ~line 334): "Deadline formula (second-run rule):
   deadline_s = (first_completion_budget_tokens / tps_min) + steps_budget ×
   per_step_budget_tokens / tps_min + 300 s margin, where tps_min is the MINIMUM
   per-task t/s observed on this box at the expected contention level (this run:
   3.96–7.5 t/s; use 4.0 for a loaded pool). A spawn without an explicitly computed
   --deadline is a guess and must say so in the ledger entry."
   The numbers that anchor it (from this run's task records + engine telemetry):
   first completion 15–17K tokens @ ~4 t/s under contention ≈ 1800–2400 s for the
   FIRST completion ALONE; with reasoning off (FIX 1) the first completion should
   drop to ~1–3K tokens, so the default 1800 s (task_default_deadline_s_tools_on,
   bin/bonsai2_harness.py line 217) becomes adequate again — re-bench after FIX 1
   lands, don't pre-change the constant.

2. No code change required (the default stays; the formula lives in the skill). If
   FIX 1's re-bench shows first completions land at ~2K tokens, consider lowering
   task_default_deadline_s_tools_on 1800 → 1200 so a stuck task reaps 10 min sooner
   — record that as its own spec delta.

===========================================================================
FIX 7 (infra defect, one-line class) — install chowns workspaces/ like results/
===========================================================================

Problem.
§2 INFRA DEFECT #2: F10's chown covers results/ only; a user-scope bonsai2-spawn with
no --workdir creates the harness task workdir under /run/bonsai2/workspaces/ as the
invoking user → EACCES (spawn EXIT=2 ERR_ARGS "cannot create workdir"). Same bug one
directory over; re-confirmed §4.3. Live workaround used: `sudo chown -R blckwll
/run/bonsai2`.

Change.
bin/bonsai2_install.py, _make_dirs (lines 1446-1470): chown BOTH user-scope
subtrees. Replace the `results = health_dir / "results"` + single-dir loop
(lines 1457-1468) with:
    if invoke_uid is not None:
        # F10 + second-run remediation: chown BOTH user-scope subtrees —
        # results/ (task result files) AND workspaces/ (the auto-created
        # per-task workdirs, S3.2: spawn without --workdir creates
        # <workspace_dir>/<task_id> here as the invoking user). The 2026-10-04
        # run's PONG probe hit EACCES on workspaces/ with the results-only
        # chown (INFRA DEFECT #2).
        for sub in ("results", "workspaces"):
            tree = health_dir / sub
            try:
                for p in [tree, *tree.iterdir()]:
                    try:
                        os.chown(p, invoke_uid, -1)
                    except OSError:
                        pass
                _log(f"run: {tree} chowned to uid={invoke_uid} (user-scope commands)")
            except OSError as exc:
                _log(f"run: WARN {sub} chown failed ({exc}) — user-scope "
                     "commands may hit EACCES")
(dedup/bench/control stay manager-root-scope, as the existing comment says.)

Test.
tests/test_remediation_first_practical_run.py:1077
test_make_dirs_chowns_results_subtree: extend the euid==0 assertion block to also
assert `os.stat(health_dir / "workspaces").st_uid == 1000` (the dedup-stays-root
assertion at line 1091-1095 stays as the counter-example).

===========================================================================
FIX 8 (discovered defect) — the manager reaper runs without cfg
===========================================================================

Problem.
bin/bonsai2-manager, reap_tasks_once (lines 1311-1340): `return h.reap_stale(d,
log=log)` — no cfg. Two consequences:
(a) F4 partial salvage does NOT run on manager-side reaps (the salvage-read block in
reap_stale is gated on `deadline_gone`, which it is, but the manager path still
works — the real gap is (b));
(b) F11 cancel_on_deadline: the gate at bin/bonsai2_harness.py line 1200 is
`(cfg or {}).get("cancel_on_deadline", True)` — with cfg=None a config that sets
`cancel_on_deadline: false` is silently IGNORED by the manager (the default-True
wins). And if a future reap_stale change adds a cfg-gated branch, the manager path
misses it too.
This run: the manager reaped nothing (workers self-reaped), so it stayed latent —
but the collect-time reap (bin/bonsai2_harness.py line 2218, which DOES pass cfg)
and the manager path should behave identically.

Change.
bin/bonsai2-manager, reap_tasks_once (~line 1338): load the harness config the way
_results_dirs_from_harness_cfg already reads it and pass it:
    h = _load_harness_module()
    cfg = None
    try:
        cfg = h.load_config()
    except Exception:  # noqa: BLE001 — a bad cfg must not kill the reaper pass
        cfg = None
    return h.reap_stale(d, log=log, cfg=cfg)
(The manager already reads harness.yaml in _results_dirs_from_harness_cfg; reusing
load_config is one call per health cycle — negligible. The except-swallow matches
the pass's existing "fault-tolerant at the caller" contract, line 1325-1330.)

Test.
tests/test_harness_34_38.py:909 (the manager reap_tasks_once rig): add a case that
sets cancel_on_deadline: false in the cfg the manager loads and asserts the control
POST is NOT issued (mirrors
tests/test_remediation_first_practical_run.py:856
test_reaper_cancel_is_gated_on_cancel_on_deadline, which today only covers the
collect-side cfg path).

===========================================================================
FIX 9 (guardrails, §6.4) — the main-loop enforcements in the skill
===========================================================================

Problem.
The run violated the pipeline it should have codified: token-heavy main-side ledger
generation while the pool was loaded (§3 18:16 self-check), open-ended re-spawns of
the same shape at changing caps (§3 decision log), and "artifact done" conflated
with "run successful" (§6 verdict wording).

Changes.
skills/bonsai2-harness/SKILL.md: add ONE new section, "Main-loop guardrails
(second-run adjudication, 2026-10-04)", placed after the Failure doctrine (~line
406) and before the Prompt templates. Exact content, each item a hard rule with its
evidence anchor:

1. PIPELINE (mandatory order):
   THINK (task + how to delegate + DoDs + workdir prep, pool UNLOADED)
   → DELEGATE (spawn the round; while the pool is loaded main does ONLY:
     short ledger entries, glue work — index/css/landing prebuild, wiring —
     and next-round prompt prep)
   → subagents FINISH → UNLOAD the pool
   → main TEST/RUN/VERIFY at full bandwidth (the F8 order, made mandatory:
     collect → unload → verify; no verification pass while the pool is loaded)
   → spawn the NEXT round.
   No token-heavy main-side generation while the pool is loaded (violated this run
   at 18:16 — ledger generation on three loaded-pool turns halved main's t/s
   exactly as the skill's t/s-halving doctrine warns).

2. PRE-DELEGATION GATE: the delegation plan (task, DoD, workdir contents incl.
   any seeded skeleton) is written BEFORE the spawn. While the pool is loaded,
   allowed main work is the set in rule 1 — nothing else.

3. BOUNDED RETRY: max 2 rounds per task shape. Two losses at the SAME settings →
   re-shape (scaffold/split, FIX 4) or take it to main. No round 3 on an unchanged
   shape. (This run re-litigated the cap+deadline every round, so "two losses at
   the right settings" was never cleanly counted; with reasoning off as the new
   default, the settings are pinned.)

4. RUN SUCCESS CRITERION: a run is "successful" only when subagents (not main)
   deliver the load-bearing artifacts. Every main-side fallback is logged as an
   infra loss, not a run win. "Artifact done" and "run successful" are two
   separate claims (the §6 verdict wording, adopted).

5. REASONING OFF BY DEFAULT (once FIX 1 lands): ON is the explicit, logged
   exception (`--reasoning on` in the spawn + the ledger entry).

6. DELEGATION-SIZE RULE: expected output > ~4 KB → pre-seed a skeleton in the
   workdir; expected output < ~2 KB or a pure modification → plain prompt, no seed
   (FIX 4).

7. PROGRESS PROBE: while the pool is loaded, main polls `bonsai2-status --tasks`
   every ~2–3 min (FIX 5); steps==0 && tool_calls==0 with <50% of the deadline
   remaining → the task is stuck in reasoning → steer/cancel/reshape, don't wait
   for the reaper.

8. DEADLINE DISCIPLINE: deadlines are computed from measured t/s × expected token
   size + margin (FIX 6), never guessed; a guessed deadline must be labeled a guess
   in the ledger entry.

===========================================================================
OUT OF SCOPE FOR THIS FIX LIST (noted, not fixed)
===========================================================================
- The engine has no "kill the slot" control action (only reasoning_end,
  server-context.cpp:4832): after the cancel the slot still decodes its answer
  half to the stop token / cap. Bounded by F1's max_tokens (witnessed live this
  run: orphan 802 cut at 32767). A real slot-kill would be an engine-side
  (fork) change — a pin-move PR + S5 quick bench per the repo's fork policy, not
  a client-side fix. Track as a candidate for the next pin evaluation.
- The 16-token health probes on freed slots (manager side) are expected behavior,
  not a defect (ledger §3 F1 observation).
- Routing blind to engine-internal orphan slots (ledger §3 17:48 ROUTING NOTE:
  orphan slots are engine-internal, invisible to the harness slot model): with
  FIX 3 the orphans die faster, which shrinks the blind window; a true fix would
  be route_target (bin/bonsai2_harness.py:763) querying the engine's GET /slots
  for live processing slots — candidate, not this list.

DEPLOY ORDER (one PR, per the workflow):
FIX 7 + FIX 8 first (pure infra hygiene, no wire-shape change) → FIX 1 + FIX 2
(reasoning off + bounded nudge; the wire-shape changes) → FIX 3 + FIX 5 (worker
self-cancel + progress probe) → FIX 4 + FIX 6 + FIX 9 (skill-side doctrine, no
code). Acceptance for the whole list = the next practical run's acceptance bar:
a subagent delivers a >4 KB generation window inside its deadline with tool_log > 0,
reasoning field empty/short, no worker-self-reaped orphan running 5+ min past its
reap, and the run verdict judged on the subagent-delivered artifacts.

===========================================================================
OBSERVED BEHAVIOUR (live, post-implementation, 2026-10-05)
===========================================================================

Live smoke against the pinned engine (image bonsai2-server:1.0 @
6b4cd79f, PQ2_0, engine pin 9a9394a8 — the S1.2 pin the PR is tested
against), same 9-12 KB full-file single-file HTML game prompt as the
second run's lost windows, VRAM-audited before each launch. Four arms,
recorded here as the current observed behaviour, in PR #38 comments
1273 + 1275. This section supersedes "deferred to your live retest" in
the PR body's Checks section.

ARM A — tools off, reasoning ON (pre-remediation wire, no
reasoning_effort in the body):
  907.4 s wall, finish_reason "length" (hit the 16384 cap),
  16384 completion tokens, answer_chars 0, reasoning_chars 47058.
  = the second run's lost-task shape reproduced live: a cap-sized
    pure-reasoning first completion, zero visible answer, still no
    write had happened when the cap cut it.

ARM B — tools off, reasoning OFF (reasoning_effort "none", the new
default wire post-FIX 1):
  248.1 s wall, finish_reason "stop", 6580 completion tokens,
  answer_chars 16949, reasoning_chars 0.
  = the whole game in ONE completion: complete, well-formed file
    (starts <!DOCTYPE html>, ends </html>), 3.7x faster wall, ~2.5x
    fewer completion tokens, ZERO reasoning bloat.

ARM C — tools ON + reasoning OFF (the normal work payload: the
agentic tool loop, driven exactly as bin/bonsai2-taskworker.py
_run_tool_loop drives it — the 4-tool schema executed host-side via
the repo's own bin/bonsai2_tools.py, stream:true, max_tokens 16384,
reasoning_control: true armed, reasoning_effort: "none"):
  SUCCESS — the full loop, 3 steps, state "done", ~317 s total wall:
    step 0: 312.5 s, finish_reason "tool_calls", 0 reasoning chars —
      the FIRST completion was the write_file tool call itself
      (engine telemetry: n_gen 6161 for the step, ~25-28 t/s, idle
      pool). No pre-tool reasoning block: it is gone.
    step 1: 3.7 s, finish "tool_calls" — the bash DoD command
      (extract the inline <script> + node --check).
    step 2: 0.6 s, finish "stop" — final answer
      "Exit code 0 — DOD check passed."
  Artifact: game.html, 13450 bytes; main-side re-verification
  (ledger-first): node --check on the extracted script -> exit 0;
  present: complete file structure, inline <style> (neon vaporwave),
  inline <script> with a requestAnimationFrame game loop, the
  5-wave mechanic, restart/game-over.
  = the acceptance bar the run list names ("a subagent delivers a
    >4 KB generation window inside its deadline with tool_log > 0,
    reasoning field empty/short") MET at the default caps + the new
    default wire, on the exact shape that lost 3 of 4 windows in
    the second run — with an idle pool and the default 1800 s
    F3a deadline (~317 s total vs 1800 s = 17% of budget).

CONCLUSION (live): FIX 1 works end-to-end at this pin — no engine-
side (fork) change was needed; the client-side reasoning_effort
field is sufficient. The first completion on the lost shape drops
from a cap-sized 15-17K reasoning block to ~6-7K content/tool-call
tokens, and the tools-on loop reaches its first tool call on step 0
and the DoD on step 1.

NEW LIVE OBSERVATION (surfaced by ARM C, 2026-10-05) — pin rejects
NULL reasoning_content in tool-loop history messages:
  The pin's chat-template message parser returns HTTP 500
  ("Failed to parse messages: [json.exception.type_error.302] type
   must be string, but is null") when a tool-loop ASSISTANT message
  carries reasoning_content: null. Reproduced in isolation on this
  exact engine:
    assistant {content: null, reasoning_content: null, tool_calls} -> 500
    assistant {content: "",  reasoning_content: "",    tool_calls} -> 200
    (content: null alone is TOLERATED; the null reasoning_content
     is the trigger.)
  Why it matters: with reasoning OFF the default, the tool loop's
  accumulated reasoning is EMPTY, and the taskworker's tool-result
  feedback appends `reasoning_content: reasoning or None`
  (bin/bonsai2-taskworker.py ~1646) — i.e. null — from step 1
  onward. A real tools-on + reasoning-off task (the new DEFAULT)
  will 500 at step 2 on this pin unless the worker sends "" (or
  omits the key) instead of None. ARM C's run used "" as the
  feedback shape (byte-equivalent for the model) and completed;
  the worker itself still sends None.
  Candidate follow-up (NOT in this PR's DOD): a one-line worker fix
  (send reasoning or "" — or omit the key when empty — in the
  tool-loop assistant feedback message) + a hermetic case asserting
  the tool-loop assistant message never carries a null
  reasoning_content. The pre-loop (tools off) path is unaffected —
  it never puts reasoning_content into the history.
  NOTE the first-run's R1 prompt discipline already nudged the
  shape toward "no planning preamble", but the null-feedback path
  is orthogonal: it fires on ANY tool-call step of a reasoning-off
  task, prompt aside.

EXACT FIX (for the round-2 commit — write only, NOT applied in
this PR's head; verified against the live 500 above):

  Both tool-loop assistant feedback messages in
  bin/bonsai2-taskworker.py carry `content: answer or None` and
  `reasoning_content: reasoning or None`. With reasoning OFF the
  default, `reasoning` is "" -> None -> JSON null -> the pin's 500.
  `content` can ALSO be None in exactly this shape (the write_file
  step in ARM C had 0 content chars -> None), and the isolated
  probes did not prove the pin tolerates content: null in the
  same message that carries tool_calls — so fix BOTH fields to ""
  (matches ARM C's successful shape exactly; byte-equivalent for
  the model: an empty string and an absent/null text field render
  the same in the chat template).

  SITE 1 — the bad-tool-call error feedback (the S3.4
  tool_invalid_max path, ~lines 1559-1562):
    BEFORE:
        {
            "role": "assistant",
            "content": answer or None,
            "reasoning_content": reasoning or None,
            "tool_calls": [...]
        }
    AFTER:
        {
            "role": "assistant",
            "content": answer or "",
            "reasoning_content": reasoning or "",
            "tool_calls": [...]
        }

  SITE 2 — the tool-result feedback (the loop-continues path,
  ~lines 1643-1646, the site ARM C proved 500s at step 2):
    BEFORE:
        {
            "role": "assistant",
            "content": answer or None,
            "reasoning_content": reasoning or None,
            "tool_calls": [...]
        }
    AFTER:
        {
            "role": "assistant",
            "content": answer or "",
            "reasoning_content": reasoning or "",
            "tool_calls": [...]
        }

  Nothing else changes: the `out_text` tool message (the raw tool
  output string) is already a str; the stream-side accumulation
  (line 383-384) is read-only; the pre-loop path never emits these
  messages.

  Hermetic case (tests/test_remediation_second_practical_run.py,
  next to the existing _stream_chat-mock tool-loop tests): a record
  with reasoning_off: True whose step returns a tool call with an
  EMPTY answer + EMPTY reasoning; assert the tool-result feedback
  assistant message appended to the history carries
  content == "" AND reasoning_content == "" (never None) — i.e.
  the wire can never contain null in either field when the
  reasoning-off default leaves them empty. The existing
  TestReasoningOffWire / TestBoundedNudge cases stay untouched
  (they assert the request body, not the feedback history).

SCOPE of this observation: single instance, idle pool (no
contention), no manager (the loop was driven host-side, not via
bonsai2-spawn). The post-merge full acceptance (the four lost
windows through the real spawn/collect pipeline, loaded pool, N
instances) remains the MAP 7.2 leg — this section proves the wire
+ the pin's behaviour at the first-completion and tool-loop level.
