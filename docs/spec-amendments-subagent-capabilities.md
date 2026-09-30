# qwen-tory38 — Spec amendments: subagent capability layer (tool loop)

Status: APPLIED. These amendments are back-integrated into SPEC.md
(the S3.4 tool loop, the S3.6 `tool_execution`/`tool_*`/`task_max_steps`
keys, the S5.2 M9/M10 capability-layer scenarios, and the S0 capability
boundary) — confirmed present in the live SPEC.md as of the 2026-09-30
doc audit. This file is kept as the record of what was proposed and
the decision log (agreed 2026-09-25, owner: user); read SPEC.md for the
authoritative text. Companion to
`docs/state-of-implementation-report.md` (2026-09-25).

This document contains ONLY spec amendments (reworded / added /
removed text per section), not commentary. Each amendment names its
SPEC target, the replacement text, and the reintegration impact on
already-landed work (Phases 0-3) so back-reintegration is mechanical.

## Decision log (agreed 2026-09-25, owner: user)

1. Tool execution: DUAL MODE behind a config flag. Host-side
   execution by the taskworker (default); engine-side execution via
   the fork's built-in `--tools` server toolset (alternative). Both
   implemented, both tested, results reported in the implementing
   PR's `## Uncertainties & decisions`.
2. Wire protocol: PROBE BOTH at the pinned fork commit
   (`9a9394a8`). Native OpenAI `tools` array on
   `/v1/chat/completions` first (the engine's chat-template layer at
   the pin accepts `tools`/`tool_choice` and returns `tool_calls`;
   whether the Bonsai model's chat template actually emits tool calls
   is unverified). Text-JSON convention (model emits a fenced JSON
   tool call, the worker parses it) as fallback. Selected per probe
   result, recorded as a pin-reality delta per the spec-amendment
   rule.
3. MVP toolset (host-side): 4 tools — `bash(cmd, timeout)`,
   `read_file(path)`, `write_file(path, content)`,
   `python(code, timeout)`. Every path strictly contained in the
   task's workdir. Spawn gains `--workdir DIR`; when omitted, the
   harness creates a per-task scratch dir under
   `/run/bonsai2/workspaces/` (rotated with the 7-day results
   window).
4. `--tools` defaults ON. Every spawn gets the tool loop available;
   text-pure work (bench baselines M1-M8) passes `--tools off`
   explicitly.
5. Bench: M9/M10 are NEW tool-loop scenarios; M1-M8 remain
   text-only baselines for comparability. M9 = implement a spec
   chunk + self-verify by running its own test suite; M10 = run a
   test batch + structured report.

## Decisions made by the architect (reject any by re-issuing this doc)

- Loop bounds: `--max-steps` spawn flag, default 32, range 1-128.
  The task's existing `--deadline` bounds the whole loop. Per-tool
  timeout 60 s default (`tool_timeout_s` in harness.yaml). Tool
  output truncated at 32 KB (`tool_output_max_kb`).
- Browser via camoufox: NOT a dedicated v1 tool. The subagent can
  drive camoufox through the `python`/`bash` tools against the host
  venv at `~/prod/.venv` (harness.yaml gains `tool_python_bin`,
  default `/usr/bin/python3`; point it at
  `~/prod/.venv/bin/python` on this box). A dedicated `browser`
  tool is a v2 candidate, parked (bench v2 item, same status as
  LLM-judge mode).
- Statefulness: intra-task multi-turn is ADDED (the tool loop's
  message history lives in the taskworker + task record). Cross-task
  state remains a locked non-goal ("stateful subagents"). The model
  is stateless in both stacks (transformer = pure function of input
  tokens); SGLang/llama.cpp keep only prefix/KV caches, which are
  evicted (main via sleep_on_idle; subagent slots per-request). No
  session DB for subagents in v1. Main inspects subagent work via
  the `tool_log` ledger in the result file (see A3).
- Capability layer location: HARNESS layer. Execution lives in the
  taskworker; the toolset is a single isolated module
  `bin/bonsai2_tools.py` — the only toolset a subagent sees. The
  skill (S4) carries doctrine + prompt templates only; the manager
  stays lifecycle-only (a looping task simply holds its slot longer;
  slot model, reaper, deadline enforcement all work unchanged); the
  engine optionally carries the built-in toolset for engine-side
  mode (A5).
- The closed-set exit codes {0,2,3,4,5,8,20,21,22,80,82,84} are
  UNCHANGED. Loop failure is a task-level terminal state
  (failed/timeout), not a new exit code.

---

## A1. S0 — replace the "Division of labor" paragraph's last sentence
and add a capability-boundary paragraph after it

**Remove:**
> Subagent output is a **self-report**: main never trusts it
> unverified.

(That rule survives — it is re-asserted in S4.2, amended in A4.)

**Add after the "Division of labor" paragraph:**

> **Subagent capability boundary (v1, locked).** A subagent task is a
> bounded AGENTIC LOOP: the main agent submits a prompt (plus an
> optional workdir), and the task runs up to `max_steps` steps where
> each step is one streaming chat completion; when the model emits a
> tool call the harness executes it and feeds the result back as the
> next message, when it emits a final answer the task is done. The
> v1 host-side toolset is exactly four tools (S3.4, locked):
> `bash(cmd, timeout)`, `read_file(path)`, `write_file(path,
> content)`, `python(code, timeout)`, all confined to the task's
> workdir. The subagent can read and write files, run Linux
> commands, and run Python inside its workdir; it cannot see outside
> the workdir, cannot spawn processes the loop cannot see, and cannot
> talk to other subagents (S0 non-goal 1). Browser access is via the
> host venv through `python`/`bash` (S3.6 `tool_python_bin`), not a
> dedicated tool (v2 candidate). Intra-task conversation state
> (the loop's message history) belongs to the task; cross-task state
> remains out of scope. Subagent output is a **self-report with
> evidence**: the result carries a `tool_log` ledger (every tool
> call, its exit code, its output, its duration) that main verifies
> per S4.2 — main still never trusts a subagent's claim of success
> without the named verification; the ledger makes that
> verification cheap.

## A2. S0 — amend the "Non-goals" list, item 6

**Replace:**
> (6) No replacement of the senior-git-1.0 or existing Hermes
> workflows — the skill sits alongside them.

**With:**
> (6) No replacement of the senior-git-1.0 or existing Hermes
> workflows — the skill sits alongside them. (7) No dedicated
> subagent browser tool (camoufox is driven through the `python` /
> `bash` tools against the host venv; a first-class `browser` tool
> is a v2 candidate, parked as a bench v2 item).

## A3. S3.1 — extend the result-file schema

**Replace the `collect` line in the S3.1 JSON block:**
> `bonsai2-collect  <TASK_ID>  # -> JSON; reads result file, returns task JSON + {"answer": ..., "reasoning": ..., "usage": {...}}`

**With:**
> `bonsai2-collect  <TASK_ID>  # -> JSON; reads result file, returns task JSON + {"answer": ..., "reasoning": ..., "usage": {...}, "tool_log": [...], "steps": N, "workdir": "..."} — tool_log is [] for --tools off tasks`

**Add to S3.1 (after the schema block):**

> **Result-file shape (locked).** The taskworker writes
> `{answer, reasoning, usage, tool_log, steps, workdir}` to the
> result file. `tool_log` is an ordered array, one entry per tool
> call: `{step, tool, params (redacted: no secrets beyond what the
> model sent), exit_code, output (truncated to
> tool_output_max_kb), duration_ms}`. `steps` = total completions
> issued (1 for a text-only task). `workdir` = the resolved workdir
> the task ran in (main's verification entry point). A failed task's
> result file still carries `tool_log` up to the failure (the
> ledger is diagnostic data, not a verdict).

## A4. S3.2 — amend the spawn line and the exit-code table notes

**Replace the spawn line:**
> `bonsai2-spawn  [-p PROMPT_FILE | -i INLINE_PROMPT] [--tools on|off] [--ctx N] [--deadline SECS] [--out FILE] [--priority low|normal|high] -> JSON (3.1). Submits to the least-loaded healthy instance.`

**With:**
> `bonsai2-spawn  [-p PROMPT_FILE | -i INLINE_PROMPT] [--tools on|off] [--ctx N] [--deadline SECS] [--max-steps N] [--workdir DIR] [--out FILE] [--priority low|normal|high] -> JSON (3.1). Submits to the least-loaded healthy instance. --tools defaults ON (the agentic loop, S3.4); --tools off = single completion, byte-identical to the pre-loop behavior. --max-steps bounds the tool loop (default task_max_steps, range 1-128); --workdir DIR confines the toolset to that directory (must exist, writable; when omitted the harness creates <workspace_dir>/<task_id> under /run/bonsai2/workspaces/, rotated with the 7-day results window, S3.1).`

**Amend S3.4 dedup key:**
> The dedup key is the prompt hash (of the `-p` file content or the `-i` inline text) plus the `--tools`, `--ctx`, `--priority`, and `--workdir` flags

**Add to S3.2 exit-code notes (per-command mapping, after the spawn bullet):**
> - `spawn` with `--tools on` against an instance whose live ctx
>   cannot fit the tool definitions + prompt + expected loop growth
>   still submits normally: the `--ctx` filter is a pre-submission
>   gate on the REQUESTED ctx only; the loop's own growth is bounded
>   by the engine's live ctx (a step that exceeds it is the S1.5
>   HTTP 400 → `ERR_CTX_TOO_LONG` task failure, exit 21 on collect).

## A5. S3.4 — add the slot-model / loop section after the existing S3.4 text

> **Tool loop (S3.4, locked).** A `--tools on` task runs a loop
> inside its taskworker: send the current message history (prompt +
> prior tool results) to the routed instance → if the response
> carries a tool call, validate it against the four-tool schema
> (a bad call is fed back to the model as an error result, up to
> `tool_invalid_max` = 3 consecutive bad calls, then the task fails
> `ERR_TOOL_INVALID_CALL`), execute it (host-side: the
> `bin/bonsai2_tools.py` module; engine-side: the instance's
> `/tools` endpoint, A8), append the result, and continue; if the
> response is a final answer, the task is `done`. The loop holds its
> single slot for the task's whole lifetime (the existing S3.4 slot
> model, unchanged). The task's `--deadline` bounds the whole loop;
> a step that overruns is killed at its per-tool timeout
> (`tool_timeout_s`, default 60 s) and the task fails
> `ERR_TOOL_TIMEOUT`. `--max-steps` (default `task_max_steps` = 32)
> bounds the number of completions; hitting it without a final
> answer → task `timeout` / `ERR_DEADLINE` (the loop exhausted its
> step budget — collect exit 22). The task record gains
> `tool_log` (mirrored into the result file, S3.1) and `steps`;
> `steer_messages` on a `--tools on` task are injected as the next
> user message at the loop's next step boundary (the steer becomes
> effective mid-loop for tool tasks — S4.3, A10). Cancel
> (S3.2/S3.4) kills the current step's subprocess (SIGTERM the tool
> execution's process group), then the worker, same 5 s SIGKILL
> backstop. The reaper (S3.4 deadline backstop) is unchanged: it
> reaps the WORKER pid; a mid-step tool subprocess is a child of the
> worker and dies with it.
>
> **Host-side execution (default):** tools run in the taskworker
> process on the host, same trust domain as the main agent's own
> bash. The toolset is the `bin/bonsai2_tools.py` module — the
> ONLY toolset a subagent sees; capability growth happens there and
> nowhere else (A12).
>
> **Engine-side execution (alternative, config flag):** the taskworker
> executes tool calls by POSTing to the routed instance's `/tools`
> endpoint (the fork's built-in toolset at the pin, A8). Selected by
> `harness.yaml` `tool_execution: host|engine` (default `host`).
> Both modes are implemented and tested; the bench (M9/M10, A13)
> carries the comparison.

## A6. S3.6 — extend the harness.yaml schema

**Replace the S3.6 YAML block with:**
```yaml
main_endpoint: http://localhost:30001
main_endpoint_healthy_probe: /v1/models
results_dir: /run/bonsai2/results
dedup_dir: /run/bonsai2/dedup
dedup_ttl_h: 24
queue_wait_max_s: 60
instance_healthy_timeout_s: 30
task_default_deadline_s: 600
main_tps_guardrail_pct: 5      # S1.8 #5 / S0 guardrail
# --- subagent capability layer (S3.4 tool loop, A5) ---
tool_execution: host           # host | engine (A5/A8; dual-mode, both tested)
tool_python_bin: /usr/bin/python3   # interpreter for the python tool (host venv for camoufox: ~/prod/.venv/bin/python)
tool_timeout_s: 60             # per-tool subprocess timeout
tool_output_max_kb: 32         # tool output truncation in tool_log
task_max_steps: 32             # default --max-steps (range 1-128)
tool_invalid_max: 3            # consecutive malformed tool calls -> ERR_TOOL_INVALID_CALL
workspace_dir: /run/bonsai2/workspaces   # per-task scratch workdirs (7-day rotation)
# boot_target_s lives in serving.yaml (S2.6), not here: it is a
# boot-lifecycle measurement owned by install/manager
```

## A7. S1.5 — extend the error/edge-case table

**Add rows:**
> | task emits a malformed tool call (not JSON, unknown tool, bad params) > `tool_invalid_max` consecutive times | model is fed the error back each time; after the limit the task fails | `ERR_TOOL_INVALID_CALL` (task-level, collect exit 21) |
> | a tool step exceeds `tool_timeout_s` | the step's subprocess is killed, task fails | `ERR_TOOL_TIMEOUT` (task-level, collect exit 21) |
> | loop hits `--max-steps` without a final answer | task times out, slot freed | `ERR_DEADLINE` (existing; collect exit 22) |

## A8. S1.3 / S1.6 — engine-side tool mode (config-gated; default OFF at the engine level)

**Add to S1.3 after the `--alias` line:**
> `--tools {toolset}` (engine-side tool mode, S3.4 A5; only present
> in the unit's docker run when subagent-engine.yaml
> `tools.enabled: true`): enables the fork's built-in server toolset
> at the pin — `read_file, write_file, edit_file,
> exec_shell_command, file_glob_search, grep_search, get_info` —
> served at the instance's `/tools` HTTP endpoint. The host-side
> default path (S3.4) does NOT require this flag: the chat-template
> layer at the pin accepts the OpenAI `tools` array on
> /v1/chat/completions regardless, so `--tools` is only needed when
> `tool_execution: engine`. Note (pin-reality, recorded at the A8
> probe): the built-in toolset is 7 tools and differs from the
> 4-tool host-side MVP set (bash ≈ exec_shell_command,
> read_file/write_file 1:1, no python tool); engine-side mode runs
> the 7-tool set, and the tool_log's `tool` field names the
> engine-side names.

**Add to S1.6 the YAML block (after `api_key:`):**
```yaml
tools:
  enabled: false            # S1.3 --tools flag on the instance's docker run
  set: [read_file, write_file, edit_file, exec_shell_command,
        file_glob_search, grep_search, get_info]   # the fork's built-in set at pin 9a9394a8
```
> Manager validation (S1.6 convention): `tools.enabled: true` with
> an unknown tool name = `ERR_CONFIG` exit 2 at config load (the
> footprint must never silently gate on a set the config didn't ask
> for — same discipline as `kv_cache`, S1.6 delta 2026-09-23).

## A9. S2.2 — amend the per-instance unit rendering

**Add to S2.2 (after the docker-run block):**
> When `tools.enabled: true` (S1.6), install.sh appends
> `--tools {set}` to the rendered unit's docker run command (and the
> runtime image's CMD remains the no-tools default; the unit's full
> command is authoritative, S2.2 pin-reality 2026-09-22). Engine-side
> tool execution (S3.4) runs inside the instance's cgroup and counts
> against its `--memory` cap; a long `exec_shell_command` step is
> bounded by `tool_timeout_s` on the taskworker side regardless of
> mode.

## A10. S4.3 — qualify the steer guidance (supersedes report 7.2)

**Replace:**
> **Instance hygiene:** prefer reusing a running task over re-spawning when the task is >50% done (steer instead of cancel+spawn); cancel is for wrong-shape tasks, not impatience.

**With:**
> **Instance hygiene:** prefer reusing a running task over
> re-spawning when the task is >50% done. Steer a `--tools on` task:
> the message is injected at the loop's next step boundary
> (effective mid-loop). Steer a `--tools off` task: advisory only —
> consumed at the next attempt (a retry), not the in-flight single
> completion; for a single-attempt task mid-stream, cancel + spawn
> with the corrected prompt is the effective path. Cancel is for
> wrong-shape tasks, not impatience.

## A11. S4.2 — work-allocation table: add tool-loop rows + amend the self-report rule

**Add rows to the S4.2 table:**
> | Spec-chunk implementation (bounded, self-contained, with a test suite to run) | **subagent, --tools on** | the loop runs its own compile/tests; the tool_log is the evidence; main verifies the ledger + re-runs the suite on its own copy [M9] |
> | Test execution batch with a report (run N tests, structure the results) | **subagent, --tools on** | mechanical, parallelizable, self-reported with exit codes in the ledger; main spot-checks 1/3 [M10] |
> | Anything that must see a rendered page / browser state (camoufox) | **main, or subagent --tools on via the python tool** | the venv is host-side; a subagent drives it through python/bash; a multi-step interactive session with state the main agent must keep = main (the v2 browser tool, parked) |

**Amend the self-report rule paragraph:**
> **The self-report rule (locked):** every subagent result is treated
> as an unverified claim, WITH EVIDENCE: a `--tools on` result
> carries the tool_log ledger (S3.1), and main's named verification
> is ledger-first: check the claimed suite ran (tool_log entry, exit
> 0), check the claimed files exist (read_file the workdir path on
> main), then re-run the named check on main's own side. The
> ledger lowers the verification cost term in S4.4 — it does not
> remove it. Verification cost is part of the task's true cost in
> S4.4 — a task that costs main 60 s to verify but saves 90 s on
> main is not a win; it goes to main.

## A12. S4.1 / S4.4 — prompt templates + cost constants

**Add to S4.1 (the skill's shape):**
> - **Prompt templates:** the skill carries one proven prompt
>   template per S4.2 tool-loop row (spec-chunk implementation; test
>   batch + report; browser task via the python tool) — the
>   subagent's quality is bounded by the prompt (it is a text model
>   with a toolset, not a second main agent); the main agent
>   substitutes task specifics and does not improvise the structure.
>   A template: state the workdir contract ("all paths are relative
>   to the workdir; do not look outside it"), the definition of done
>   (the test command to run, the expected exit code), and the final
>   answer contract (one paragraph: what was done, what was
>   verified, what failed, the exact commands run).

**Amend S4.4's constants line:**
> `t/s_decode ≈ 29`, `t/s_prefill ≈ 4800` — a-priori floor from the
> community GB10 bench, UNCALIBRATED until the S5 run replaces them
> with measured values. Until then the win rule
> (`dispatch_cost < est_main_time_s`) is applied with a 20% margin
> on the dispatch side. For `--tools on` tasks, `dispatch_cost`
> additionally carries `steps_est × (tool_mean_duration_s +
> prefill_of_tool_results)` — the v1 estimate is 6 steps ×
> (10 s + 0.3 s), replaced by the M9/M10 measured per-step cost at
> the first full bench.

## A13. S5.2 — add M9/M10; amend the M1/M2 notes

**Add rows to the S5.2 matrix:**
> | M9 | tool-loop spec-chunk: implement a bounded spec chunk (fixture: a small library spec with 3 functions + a 3-case suite) | 1 | 1 | 32k, --tools on | the suite the subagent claims to have passed actually passes when MAIN re-runs it (auto: main-side re-run, exit 0) + tool_log shows the test run with exit 0 + the spec's 3 functions exist in the workdir (file check) |
> | M10 | tool-loop test batch: run a 10-file test batch, produce a structured report (per-file pass/fail, durations) | 1 | 8 | 8k, --tools on | report JSON parseable + 10/10 files covered + main re-runs 2/10 files to confirm the pass/fail verdicts agree (auto, 2/10 spot) |

**Add a note after the matrix:**
> M1-M8 are TEXT-ONLY baselines (`--tools off`): they measure the
> raw inference stack and stay comparable across engine-pin bumps
> and re-anchors. M9/M10 measure the capability layer (S3.4) and
> run in both `tool_execution` modes (host and engine, A5/A8); the
> run records the mode in the row. M9/M10 use the same
> box-state-snapshot + rotation rules as M1-M8 (S5.2).

**Amend S5.7 bench.yaml:**
```yaml
scenarios: [M1, M2, M3, M4, M5, M6, M7, M8, M9, M10]
quick: [M1, M2, M7]          # unchanged: the quick bench is the text-only regression gate
```
> S5.5 unchanged: quick = M1+M2+M7 (the engine-regression gate);
> full = M1-M10 (capability + doctrine input). M9/M10 are
> full-bench-only in v1 (their rubrics need main-side re-runs, which
> the quick bench's time budget does not carry).

**Add goldens (S5.3):** M9's spec-chunk fixture + M10's 10-file test
batch are versioned in `bench/goldens/` (repo fixtures, like M4's
seeded-bug diff).

## A14. S4.7 / S5.9 — acceptance: add the capability layer

**Add to S4.7 acceptance:**
> 4. Tool-loop doctrine: the S4.2 tool-loop rows (M9/M10-cited)
> complete the standard workload set with `--tools on` in a fresh
> session, every result ledger-verified per the amended S4.2
> self-report rule; the command set is still S3.2-only.

**Add to S5.9 acceptance:**
> 5. M9 and M10 pass (main-side re-run verdicts agree with the
> subagents' claims in ≥ 8/10 files for M10; M9's suite passes on
> main's re-run); the host-vs-engine tool_execution comparison is
> recorded in the run JSON and the doctrine cites it.

## A15. S6 — packaging amendments (minimal)

**S6.3 step 4 (YAMLs):** the four YAMLs gain the A6/A8 keys
(`tool_execution`, `tool_python_bin`, `tool_timeout_s`,
`tool_output_max_kb`, `task_max_steps`, `tool_invalid_max`,
`workspace_dir`, `tools.enabled/set`). `install.sh` renders them
from the templates; `--workdir`/`--max-steps` are task-time flags,
not install-time state.

**S6.6 pin list:** no new dependencies for host-side execution
(stdlib-only `bin/bonsai2_tools.py`; the python tool invokes
`tool_python_bin` as a subprocess). Camoufox is the host's venv
package, not a project dependency (S6.6 unchanged).

**ARTIFACTS.md (companion, not SPEC):** new rows for
`bin/bonsai2_tools.py` (the isolated toolset module) and
`/run/bonsai2/workspaces/` (per-task scratch workdirs, 7-day
rotation).

---

## Reintegration impact on landed work (Phases 0-3)

What the implementing session touches, and why it is safe:

- **`bin/bonsai2_harness.py`:** `cmd_spawn` gains `--max-steps` /
  `--workdir` parse + record fields; dedup key gains the workdir
  term (A4); a new `task_max_steps`/`tool_*` default block in
  `CONFIG_DEFAULTS` (A6). `cmd_collect`'s payload gains
  `tool_log`/`steps`/`workdir` (A3). `--tools off` spawns go through
  the EXACT current code path (no loop) — the 341-test hermetic
  suite stays green; the new cases are additive.
- **`bin/bonsai2-taskworker.py`:** gains the loop: when
  `rec["tools"] == "on"`, the single-attempt body becomes the
  step-loop (send history → parse tool call (native tools-array or
  text convention, per the A2 probe) → execute (host module or
  instance /tools endpoint) → append → repeat; final answer →
  done). `--tools off` = today's single completion, byte-identical.
  The always-exits-0 contract, the record-as-truth model, the SIGTERM
  handler, the one-retry engine-down path (S1.5, applies per ATTEMPT
  of a step, not per task), and the deadline check are all preserved.
- **NEW `bin/bonsai2_tools.py`:** the isolated 4-tool module
  (bash/read_file/write_file/python), workdir containment (resolve
  the path, reject escape above the workdir root —
  `/`-rooted and `..`-crossing paths rejected; symlinks inside the
  workdir allowed, symlinks out rejected at open), timeout via
  subprocess process-group kill, output truncation. No engine, no
  network, stdlib-only.
- **`bin/bonsai2-manager`:** no functional change for host-side
  mode (the reaper reaps worker pids as today; a looping worker is a
  longer-lived pid). Engine-side mode: when `tools.enabled`, the
  rendered unit's docker run gains `--tools {set}` (install.sh
  change, A9) and the S2.2 probe is unchanged (the /tools endpoint
  does not affect /v1/models health).
- **Mock engine fixture (`tests/fixtures/mock_engine.py`):** gains a
  tools mode — canned `tool_calls` in the SSE stream + canned
  `/tools` responses — so the loop is testable hermetically per S3.7
  (table-driven cases: valid call → execute → final answer;
  malformed ×3 → ERR_TOOL_INVALID_CALL; step over tool_timeout_s →
  ERR_TOOL_TIMEOUT; max-steps → timeout/ERR_DEADLINE; workdir escape
  → rejected call; steer mid-loop injection; cancel mid-step).
- **`etc/*.yaml` templates:** gain the A6/A8 keys (template +
  validator updates in `bin/bonsai2_render_config.py`: the A8
  `tools.set` validation row).
- **`units/bonsai2-server@.service` template:** comment-level note
  (A9); the rendered ExecStart change happens in install.sh
  (Phase 6, not yet built — no rework).
- **@gpu tests:** one new probe test (the A2 protocol probe: does
  the pinned model emit tool_calls via the native array at the pin?
  log the result, pin the decision) + M9/M10 @gpu exercises when
  Phase 4 lands. The probe is a SPEC-DELTA input, not an acceptance
  gate of its own.
- **MAP (companion, not SPEC):** new Phase-3 items 3.13 (tools
  module + hermetic cases), 3.14 (taskworker loop + @gpu probe +
  dual-mode), 3.15 (workdir + rotation + spawn flags); Phase 4
  gains 4.11 (M9/M10 goldens + scorers + both tool_execution modes);
  the parallel session's 3.9-3.12 land FIRST (they are the closed-set
  + acceptance floor this builds on).

## Open uncertainties for the implementing PR (report in `## Uncertainties & decisions`, fixed 5-subsection shape)

1. Native tools-array vs text-JSON convention at the pin (A2 probe
   result decides; both are implemented behind the worker's
   protocol choice, the probe measures which the Bonsai chat
   template actually honors at `9a9394a8`).
2. Host vs engine tool execution (A5/A8: the M9/M10 dual-mode
   numbers decide the default for S6; both stay).
3. Tool-definition prefill overhead: measure the added prompt
   tokens for the 4-tool schema (expected ~200-400 tokens; the A12
   cost estimate uses the measured value).
4. M9/M10 rubric thresholds (8/10 agreement for M10; the M9
   main-side re-run is strict) are first-pass — the first full
   bench's failure modes may warrant a S5.2 delta (amend with the
   usual delta note, not silently).
5. Whether `--tools on` + `--ctx` should auto-raise the ctx filter
   (the tool definitions + loop growth need headroom): v1 says NO
   (A4 note, the engine's 400 is the arbiter); the first bench's
   ERR_CTX_TOO_LONG rate on M9 decides.
