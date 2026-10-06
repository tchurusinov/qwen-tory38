# qwen-tory38 code review and summary

This reviews https://github.com/tchurusinov/qwen-tory38 at commit `b17eed0` on branch `main`, as of 2026-10-05. The clone is in `~/UACP/qwen-tory38`. Seven reviewers read every production file in parallel, one area each, and I re-checked the most serious findings against the source. No repo code was run.

## Summary

qwen-tory38 runs a pool of helper model servers, called subagents, next to an existing coding agent on one NVIDIA DGX Spark class box. The main agent is Qwen3.8-27B in NVFP4 on SGLang. The subagents are copies of Ternary-Bonsai-2-27B, a 7.2 GB ternary version of the same model, served by a fork of llama.cpp. The main agent hands them work through ten command-line tools and follows a 953-line instruction file, the "harness skill". An AI coding agent running on the same local Qwen3.8-27B wrote 239 of the 264 commits in 17 days, from a 2,010-line spec. The same model also reviewed each pull request.

Parts of the pool-management code are well built. The code writes every state file atomically, one function enforces a closed set of exit codes, the installer checks model downloads against SHA-256 hashes, the Dockerfiles pin base images by digest, and the tests run the real worker process against fake engines.

The parts that show whether the idea works are weak. The bench that calibrates the skill's rules measures throughput wrongly and scores several tasks wrongly. In its own committed results, the subagents beat main on no comparable task. In two of the three live runs, only one of four tasks landed. The third run landed all four after the author switched reasoning off and after main had written 62 to 93 percent of each file itself.

The code also has defects that will appear under the fan-out the skill recommends. Retried tasks hold slots that nothing counts, the reaper can overwrite a finished task with a failure, queued tasks can wait forever, and the manager restarts a fully busy instance along with its tasks. On a real systemd boot, no instance starts until the manager's 180 s boot deadline has passed. The subagent's bash and Python tools run with the operator's full rights, although the spec says they are confined to a work directory. The root-run manager writes files at paths taken from a directory that users have to be able to write.

In its current state the project is a prototype that works with one instance under supervision. It is not ready to install and leave running. The first fixes should go to the bench measurements, the task state machine, the boot ordering, a sandbox for the tools, and a gate that runs the 790 tests it currently skips.

## What the project does

### The goal

The author's box has an NVIDIA GB10 Blackwell chip, an aarch64 CPU and about 120 GB of memory that the CPU and GPU share. Their main coding agent, Qwen3.8-27B in NVFP4 on SGLang at port 30001, takes about 70 to 76 GB of it. `idea.txt` asks for a one-command install that fills the remaining memory with Bonsai subagents and gives the main agent a way to use them. An earlier attempt with LM Studio and an agent framework's built-in delegation failed, because the author could not control concurrency or instances. This project drives the pool with plain commands instead.

### The models and the memory budget

The main stack is external. The project reads its endpoint and its memory use and never changes it.

The subagents run Ternary-Bonsai-2-27B in the `PQ2_0` packing, which is 7.21 GB, with `PTQ1_0` at 5.95 GB as a low-memory fallback. Upstream llama.cpp, SGLang and vLLM have no kernels for these ternary formats, so the engine is the PrismML fork of llama.cpp, pinned at commit `9a9394a8` as a git submodule. Each instance is one docker container running `llama-server` on port 30101 plus its index, under its own systemd unit `bonsai2-server@i`. It has a 262,144-token context shared by 8 parallel slots and a `q4_0` KV cache.

The spec's budget starts from 121.6 GiB, takes off 76.4 GB for the main stack and an 8 GB reserve, and leaves 37.2 GB. At 14.78 GB per instance, that is room for 2. The README's standing rule is to load only 1 until the end-to-end flow is proven.

### The command surface

The main agent uses ten commands under `/opt/bonsai2/bin`. All of them are thin Python wrappers around `bin/bonsai2_harness.py`.

| Command | What it does |
|---|---|
| `bonsai2-spawn` | Submits a prompt as a task, with tools on or off, a deadline, a step limit and a priority |
| `bonsai2-collect` | Returns the task record, answer, reasoning, token usage and tool log |
| `bonsai2-steer` | Adds a message that the worker feeds to the model at the next step |
| `bonsai2-cancel` | Stops a task's worker |
| `bonsai2-status`, `bonsai2-list-instances`, `bonsai2-health` | Report the pool, and with `status --tasks`, live task progress |
| `bonsai2-unload`, `bonsai2-reload` | Ask the manager to stop or start instances |
| `bonsai2-bench` | Runs the bench |

Each command prints one JSON object on stdout and exits with one of 12 codes: 0, 2, 3, 4, 5, 8, 20, 21, 22, 80, 82 and 84. For example, 8 means the queue wait expired, 20 means the task is done, 80 means no instances are loaded, and 84 means the manager is down.

### The life of a task

1. `bonsai2-spawn` reads `harness.yaml` and the manager's `/run/bonsai2/health.json`, checks that the main endpoint answers, and builds a dedup key from a hash of the prompt and the flags.
2. It picks the healthy instance with the fewest charged slots and writes a `queued` task record under a per-key lock. Under a global `slot-claim` lock, it then promotes the record to `running` if that instance has a free slot.
3. It starts `bin/bonsai2-taskworker.py` as a detached process for the task. If no slot is free, the spawn waits up to `queue_wait_max_s` and exits 8 when that time runs out.
4. The worker streams chat completions from the instance. With `--tools off` it makes one completion. With `--tools on` it runs a loop. The model asks for one of four tools, `bash`, `python`, `read_file` or `write_file`, and the worker runs it in the task's work directory on the host, appends the output and asks again. The loop ends at a final answer, the step limit or the deadline.
5. The worker writes the result and the final state. `bonsai2-collect` then returns 20, 21 or 22 for done, failed or timed out. A reaper marks tasks whose worker died or whose deadline passed.

### The manager

`bin/bonsai2-manager` runs as root under `bonsai2-sglang.service`, a systemd `Type=notify` unit. At boot it estimates how many instances fit from the main container's cgroup memory. Before each start it checks that free memory covers the instance footprint plus 1.0 GB, then it starts the instances one at a time and waits for each to answer. If none fits, it retries with the smaller quant, then with half the context, and then falls back to main-only mode.

After boot it loops every 10 s. It probes each instance with `GET /v1/models` and a 16-token completion that must finish in under 10 s, marks an instance degraded after 2 failures, and restarts it after 5. It handles unload and reload request files in `/run/bonsai2/control` and writes an ack file for each. It runs the harness reaper. Every 5 minutes it compares each container's memory with its cap. If one stays over the cap for 30 s, it halves that instance's context and restarts it. It writes `health.json`, which every command reads.

### The installer

`install.sh` hands over to `bin/bonsai2_install.py`, which runs these steps in order:

1. Check prerequisites such as the CPU architecture, driver, docker, Python and disk space, writing nothing.
2. Download the model and check its SHA-256.
3. Build the runtime image, or verify it against its recorded digest.
4. Read the main stack's memory footprint from its SGLang startup log.
5. Compute N.
6. Render the four YAML configs and the unit files with atomic writes, and copy `bin/` and `bench/` to `/opt/bonsai2`.
7. Restart the manager.
8. Wait up to 180 s for the pool to report healthy.
9. Write a `state-<ts>.yaml` file for rollback.

`--rollback`, `--refresh-main` and `--instances N` reuse these steps.

### The skill and the bench

`skills/bonsai2-harness/SKILL.md` is the text the main model loads. It says which work to send to subagents and which to keep, gives prompt templates and seven worked recipes, sets rules for checking results, and has one paragraph per exit code. Every rule must cite a bench scenario, and `bench/doctrine_audit.py` checks the citations against `claims.json` during the gate.

`bench/run.py` runs ten scenarios. M1 is single-file code generation, M2 a test batch, M3 fan-out document analysis, M4 a diff review with 10 seeded bugs, and M5 a long-document question set. M6 runs the M1 to M5 prompts on main alone, as the control. M7 fills every slot, and M8 measures main's speed while the pool works. M9 and M10 are tool-loop tasks: implementing a spec chunk, and running a test batch and reporting on it.

```
 main agent: Qwen3.8-27B NVFP4 on SGLang :30001 (external, unchanged)
   | runs bonsai2-* commands, reads one JSON line and an exit code
   v
 bin/bonsai2_harness.py ---- task records, locks, dedup in /run/bonsai2
   | starts one detached worker per task
   v
 bin/bonsai2-taskworker.py --- streams completions ---> llama-server :30101+i
   | tool loop on the host: bash, python,               Bonsai-2-27B, one docker
   | read_file, write_file in the task workdir          container per systemd unit
   v                                                            ^
 result file, tool_log                                          | start, probe,
                                                                | restart, stop
 bin/bonsai2-manager (root, Type=notify) -----------------------+
   writes health.json, handles control/ requests, runs the reaper
```

## How it was built

- The history has 264 commits from 2026-09-19 to 2026-10-05. An AI agent committing as "Hermes Agent" wrote 239 of them. The human account "blckwell" made the other 25, all of them pull-request merges on a local Gitea server.
- The project went spec first. `SPEC.md` holds the design in 2,010 lines, and `MAP.md` holds the phased checklist and a session log in 3,893 lines. The repo has about 21,600 lines of product code and config, 25,100 lines of tests and 17,400 lines of Markdown, not counting the video transcripts.
- Every pull request went through `scripts/review.sh`, which sends the diff to the same Qwen3.8-27B model that wrote it and repeats until the reviewer reports clean. Phase 0 took 14 rounds, and PR #10 and PR #22 took 16 each.
- The word "locked" appears 120 times in the spec. Even so, 41 commits changed the spec, 27 of them after it was agreed, and it carries 49 delta notes. Locked values that moved include N, from 3 to 2, the KV-cache constant, and the reload budget, from 15 s to 30 s. The project's own rules allow this, because the spec is meant to follow the code.
- The code is full of process notes. In the harness, manager, worker and tools modules, 35 to 41 percent of lines are comments or docstrings, and more than 120 lines cite a review round, such as "G3 PR#18 r11 P1#4". Some functions are very long: `cmd_spawn` is 524 lines, `_run_tool_loop` is 662 and `install` is 490.

## What the measurements show

### The bench run of 2026-09-28

The committed full run used 2 instances. In its rows, the subagents beat main-only on no comparable task:

- Main produced an answer on all 21 of its M1 to M5 tasks. The subagents produced one on 7 of 21, and the missing answers stop mid-sentence at the scenario's token cap.
- Main ran its tasks one at a time and still finished each comparable scenario sooner than the two-instance pool running in parallel. It was about 3 times faster on M1, 1.2 times on M2 and M3, 6 times on M4 and 4 times on M5.
- In the saturation test M7, with 16 tasks on 2 instances, each task decoded at 3.7 to 19.3 t/s, with a median of 5.3.
- The contention test M8 recorded main's speed under load as 0.00 t/s. The author put this down to a timing artifact and never measured it again. The skill's planning figure, that main's speed halves under a loaded pool, comes from the author's observation of 30 t/s dropping to 15.
- 5 of the 10 rows passed. The doctrine audit reports a pass because `claims.json` lists M1, M9 and M10 as documented exceptions.

Because of the bench defects B1 to B14 below, none of these numbers is a fair comparison in either direction. The repo has no measurement that shows a subagent win.

### The three live runs

In each run, the main agent built small browser projects with the pool.

- Run 1 on 2026-10-03 was a dashboard and three canvas pages. One of four tasks landed. The other three spent 9,000 to 21,000 tokens reasoning in their first completion and made no tool call before their deadline, because the worker sent no `max_tokens`. Main then wrote those pages itself. The run also exposed a boot deadlock, an interpreter without PyYAML, commands without the exec bit, and a root-owned `/run/bonsai2`. PR #37 fixed most of these.
- Run 2 on 2026-10-04 also landed one of four tasks. Six attempts made no tool call at all, and every first completion was a reasoning block of 15,800 to 18,600 tokens. The ledger's verdict is "UNSUCCESSFUL RUN". PR #38 switched reasoning off by default.
- Run 3 on 2026-10-05 landed four of four tasks in the first round, with reasoning off. Main had first written skeletons that were already 62 to 93 percent of the final file sizes. The ledger's verdict is "SUCCESSFUL RUN". The four follow-up fixes it calls for, I1 to I4, are written up in `fixes-third-practical-run.md` but not implemented.

No run timed main doing the same work alone, so the repo cannot show a wall-clock gain.

### Speeds the skill relies on

One subagent stream decodes at about 18 t/s with reasoning on and 26.5 t/s with it off, according to `fix-proposals-second-practical-run.md:533-545`. Main decodes at about 25 to 30 t/s when idle and about 15 t/s with the pool loaded. A single subagent stream is therefore about as fast as main at idle and faster than main under load. A fan-out of 8 to 16 tasks drops each stream to 3 to 8 t/s. The skill's speed table lists 44.7 to 61.9 t/s for "subagent decode t/s, single task, no contention" at `SKILL.md:344`. That figure is the total of 8 concurrent streams, as `MAP.md:276-282` records it.

## Findings

The IDs group findings by area: S for security, C for task bookkeeping, M for the manager, I for the installer, B for the bench, K for the skill, T for tests and gates, and D for the docs. Each area lists its items in severity order. "Conditional" means the defect needs a state that the repo's own run notes reach, such as a manual `chown`.

| ID | Severity | Problem |
|---|---|---|
| S1 | High | The bash and python tools run as the operator with no confinement |
| S2 | High, conditional | The root manager writes files at paths a user can choose |
| C1 | High | Retried tasks hold slots that nothing counts |
| C2 | High | The reaper can overwrite a finished task with a failure |
| C3 | High | Queued tasks wait forever after their spawn exits 8 |
| M1 | High | On a real boot, no instance starts until the 180 s deadline passes |
| M2 | High | A fully busy instance fails its probe, and the manager restarts it along with its tasks |
| M3 | High | Unit-file rewrites never reach systemd |
| I1 | High | Wrong ownership under `/run/bonsai2`, and a reboot breaks the commands |
| B1, B2 | High | The bench measures throughput wrongly, and the main-only control is not comparable |
| B3 to B10 | High | Rubrics pass empty or leaked answers and fail correct ones |
| K1 | High | The skill tells the model to do things the code does not support |
| T1 | High | The gate never runs the tests |
| C4 | Medium | Deadlines and cancel do not stop a running tool |
| C5 | Medium | A cancel can be undone |
| C6 | Medium | The file lock's recovery code is dead or wrong |
| C7 | Medium | One down instance turns a queueable spawn into a hard failure |
| C8 | Medium | A dead manager goes unnoticed |
| C9 | Medium | Tool output and files are read fully into memory |
| M4 | Medium | In main-only mode the manager exits |
| M5 | Medium | The memory meter reads the wrong source in mixed units |
| M6 | Medium | One bad response or hung subprocess stops the probe loop |
| M7 | Medium | Restart guards, adoption after a restart, and reload math are wrong |
| I2 | Medium | The pool does not start after a reboot |
| I3 | Medium | An unchanged re-run restarts the whole pool |
| I4 | Medium | A fresh box cannot install |
| I5 | Medium | Rollback leaves removed instances running |
| S3 | Medium | Engine-side tools run as root with host networking, off by default |
| B11 to B14 | Medium | No repetitions, a weak audit, a regression gate that passes unlike runs, unsafe rotation |
| K2 | Medium | The skill is long and contradicts itself |
| T2 | Medium | The secret scan misses most secrets |
| T3 | Medium | The AI reviewer is the author's own model and cannot block |
| T4 | Medium | Some tests cannot fail, and some mocks differ from production |
| D1 | Medium | The docs report more as done than the code does |
| S4, C10, I6 | Low | Smaller defects, listed in their sections |

### Security

**S1. Model-issued shell and Python commands run with the operator's full rights.** The `bash` tool runs `/bin/sh -c <cmd>` and the `python` tool runs the interpreter, and the only restriction is the working directory, set to the task's workdir (`bin/bonsai2_tools.py:324-331`, `:398`). Nothing scrubs the environment, blocks the network or switches user, and the worker itself inherits the caller's environment (`bin/bonsai2_harness.py:995-1001`). SPEC S0 says the four tools are "all confined to the task's workdir" and that the subagent "cannot see outside the workdir" (`SPEC.md:82-84`). The skill tells the main agent the same (`SKILL.md:87-88`, `:126-127`). Only SPEC S3.4 admits that host-side tools share the "same trust domain as the main agent's own bash" (`SPEC.md:1153-1154`). The skill's recipes send subagents to review diffs, read documents and drive a browser, so they read untrusted text, and injected instructions mislead a 27B ternary model easily. One injected line in a reviewed file can make it read `~/.ssh`, send the contents out with `curl`, or delete files outside the workdir. Run the tools under bubblewrap or nsjail with only the workdir mounted, no network and an empty environment. Until then, remove the confinement claim from SPEC S0 and the skill. The path checks on `read_file` and `write_file` do hold, including against symlinks.

**S2. The root manager writes files at paths that a user can choose.** The manager runs as root, since `units/bonsai2-sglang.service` sets no `User=`. It reads every `*.json` in `/run/bonsai2/control`, takes `request_id` from the file unchecked, and writes `control/ack-<request_id>.json` through a `.tmp` file with `Path.write_text`, which follows symlinks (`bin/bonsai2-manager:1919`, `:2036-2047`). It writes an error ack even for an invalid request. On a default install `control/` is root-owned, so user-run unload and reload fail, as I1 describes. Runs 1 and 2 hit this, and the operator worked around it with `sudo chown -R` on all of `/run/bonsai2` (`evidence-ledger-for-first-practical-run.md:137`, `evidence-ledger-second-practical-run.md:179`). With that ownership, any process running as the user can plant `control/ack-x.tmp` as a symlink to any file and then drop a request with `request_id` set to `x`. The root manager then overwrites the symlink's target with JSON. That process can be a subagent's bash tool from S1. Give the control directory to a dedicated group, open files with `O_NOFOLLOW`, and accept only request ids that match a strict pattern such as `^[0-9a-f]{32}$`.

**S3. Engine-side tool execution runs as root with host networking.** This mode is off by default. With `tool_execution: engine`, the worker sends tool calls to the fork's `/tools` endpoint inside the instance container. The container runs with `--network host --ipc=host` and no `--user`, and the image sets no `USER` (`bin/bonsai2_install.py:729`, `image/Dockerfile.runtime`). The worker applies no path checks in this mode (`bin/bonsai2-taskworker.py:737-742`). When the 20 s POST times out, the command keeps running. Drop privileges and the network for tool execution, or remove the mode.

**S4. The repo commits a static API key and leaves the bind address to the default.** Every instance runs with `--api-key bonsai2-local` from the tracked config (`etc/subagent-engine.yaml:59`) and no `--host` flag. Upstream llama-server listens on 127.0.0.1 by default, so with host networking the ports are probably reachable only from the box. I could not confirm that for the fork, because the submodule is not checked out. Pass `--host 127.0.0.1` explicitly and generate the key at install time.

### Task bookkeeping in the harness and worker

**C1. Tasks that moved to another instance hold a slot that nothing counts.** After an engine error, the worker moves a task to a second instance and sets its state to `engine_retried` (`bin/bonsai2-taskworker.py:1336`, `:1907`). It never sets `running` again. The slot counters count only `queued` and `running` (`bin/bonsai2_harness.py:250`, `:645-670`). From then on, routing and `bonsai2-status` treat that slot as free. The pool admits more completions than llama-server has slots, and the status can show 0 slots in use while the task still runs. The skill's idle check tells the main agent to run `bonsai2-unload --all` when every instance shows 0 slots in use (`SKILL.md:293-296`), and that kills the task. Count `engine_retried` in both counters, and check capacity when picking the retry instance.

**C2. The reaper can overwrite a finished task with a failure.** `reap_stale` reads every task record at the start of a pass, checks each worker's PID later, and writes its copy back without a lock or a re-read (`bin/bonsai2_harness.py:1145-1233`). Suppose a worker writes `done` and exits after the snapshot but before the PID check. The reaper then marks the task `failed` with `ERR_ENGINE_DOWN`, and `bonsai2-collect` returns 21 with the answer still in the payload. The manager runs this pass every cycle, and every collect and every queue-wait poll runs it too, so this race comes up often during a fan-out. The same path rewrites context-too-long errors, cancellations and timeouts as engine failures, which makes the main agent apply the wrong recovery rule. Take the per-task lock, re-read the record and re-check the condition before each write.

**C3. Queued tasks wait forever once their spawn gives up.** Only `claim_slot` promotes queued tasks, and its only caller is the wait loop inside `cmd_spawn` (`bin/bonsai2_harness.py:2281`). The manager runs the reaper but never claims. When a spawn exits 8, its task stays `queued` and charged, with nothing left to promote it. `bonsai2-collect` returns 0 for it indefinitely, and re-spawning the same prompt hits dedup and returns the stuck task with exit 0 (`:2184-2191`). The skill's advice for exit 8 is to re-spawn or raise the priority (`SKILL.md:431-436`). That either returns the stuck task or creates a duplicate. The fast path also lets a newcomer take a free slot ahead of older queued tasks, whatever their priority (`:899-926`). A queued task stays bound to the instance chosen at submission, even when another instance is idle. Run a claim pass in the manager loop and on the dedup path, route every promotion through one head-of-queue choice, and mark a task failed when its spawn's queue wait expires.

**C4. Deadlines and cancel do not stop a running tool.** The model sets each tool call's `timeout`, and `_param_timeout` accepts any positive number (`bin/bonsai2_tools.py:517-525`). The worker checks the task deadline only between steps and passes no remaining time to the tool (`bin/bonsai2-taskworker.py:1122`, `:1610`). When the deadline passes, the reaper marks the task `timeout` and frees its slot but sends no signal, and `bonsai2-cancel` refuses to touch a finished task (`bin/bonsai2_harness.py:2546-2547`). Tools also run in their own session (`bin/bonsai2_tools.py:330`), so cancel's process-group signals miss them. The worker's handler sends the tool one SIGTERM and never a SIGKILL (`:616`), and a child started with `setsid` escapes even that. A call such as `{"cmd": "sleep 100000", "timeout": 100000}` therefore outlives its task while a new task takes the slot. Clamp the tool timeout to the smaller of `tool_timeout_s` and the time left, record the tool's process group, and have cancel and the reaper kill it after a grace period.

**C5. A cancel can be undone.** Cancel takes the per-task lock, while `claim_slot` and `start_worker` work under the global `slot-claim` lock and write back their own copies of the record (`bin/bonsai2_harness.py:939-981`, `:2545-2565`). If a cancel of a queued task lands during a claim pass, the claim writes `running` over it, and the worker starts after cancel has already reported success. Make every state change a compare-and-set under the per-task lock.

**C6. The file lock's recovery code is dead or wrong.** `lock_wait` resets `first_empty = None` on every pass of its retry loop, so its grace-period branch for an empty lock file can never run (`bin/bonsai2_harness.py:703-721`). A lock file left empty, for example by a PID write that failed on a full tmpfs, blocks that lock for good. Every later spawn then waits 2 s and exits 1 with a traceback, because no caller catches the `TimeoutError` (`:683-684`). The code counts a permission error from `os.kill(pid, 0)` as a dead holder, so a waiter can delete a live lock that another user holds. Two waiters can also reclaim the same stale lock and both enter the critical section. Nothing ever deletes task records, and `claim_slot` re-reads all of them several times while it holds `slot-claim` (`:939-955`), so lock hold times grow with every task run since boot. Use `fcntl.flock`, which the kernel releases when the holder dies, map a lock timeout to a closed-set exit code, and prune old records.

**C7. One down instance turns a queueable spawn into a hard failure.** When no healthy instance has a free slot, `route_target` picks its fallback from every instance whose context fits, including `bfailed` and `bdown` ones (`bin/bonsai2_harness.py:812-831`). Those carry no load, so the selection picks them, and the spawn gets exit 4 or 3 instead of queueing on the busy healthy instance. Pick the fallback from routable instances whenever there are any.

**C8. A dead manager goes unnoticed.** `health.json` carries no timestamp, and the manager never removes it (`bin/bonsai2-manager:2129-2195`). The harness treats the manager as down only when the file is missing (`bin/bonsai2_harness.py:450-467`). After a crash, or after systemd stops restarting the manager, spawn and status keep answering from the last good state, and no reaper runs. The repo has seen this already: "the entire steady loop dies and health.json goes stale" (`docs/two-red-gpu-tests-fix.md:218-219`). Write a timestamp every cycle and treat a stale file as exit 84.

**C9. Tool output and files are read fully into memory.** `_run_capped` collects all output with `communicate()` before it truncates (`bin/bonsai2_tools.py:335`), and `read_file` and `write_file` have no size limit (`:451`, `:503`). A command such as `yes | head -c 50G` or a large `write_file` can exhaust memory. The default workspace is on `/run`, which is usually a RAM-backed tmpfs on the same box the models need. Cap bytes while reading and cap file sizes.

**C10. Smaller harness and worker defects.**

- A final answer that happens to be a JSON object naming a tool runs as a tool call (`bin/bonsai2-taskworker.py:580-609`).
- `--deadline nan` and `--deadline inf` pass validation and turn off every deadline check (`bin/bonsai2_harness.py:1704-1715`).
- There is no top-level exception guard. A missing PyYAML, a prompt file that is not UTF-8, or `--ctx ²` exits 1 with no JSON, outside the closed exit-code set (`:2891-2916`).
- Workspace rotation deletes every subdirectory of `workspace_dir` that has gone untouched for 7 days, not only task directories (`:581-593`).
- A steer sent to a `--tools off` task returns success but never reaches the model.
- Concurrent writers of one record share the same `.tmp` file name (`:618`).

### The manager and the boot lifecycle

**M1. On a real systemd boot, no instance starts until the 180 s deadline has passed.** The installer renders each instance unit with `After=docker.service bonsai2-sglang.service` (`bin/bonsai2_install.py:684`). The manager is a `Type=notify` service that sends `READY=1` only after its spawn sequence returns (`bin/bonsai2-manager:2889-2893`). systemd holds every instance start job until the manager has finished starting. The manager therefore polls ports that nothing listens on until its deadline, marks the instances degraded and sends READY. Only then do both start jobs run, at the same time. The memory check for the second instance ran while the first had allocated nothing, so on a tight pool the check approves two instances and the kernel's OOM killer decides between them. The run ledgers show the symptom. In run 1 an instance start job sat "waiting" on the manager, and in run 2 the "measured boot" was 180.1 s, which is the deadline. The GPU tests start the manager as a plain subprocess, so they never see this. Drop `bonsai2-sglang.service` from the instance units' `After=` line, or send READY before spawning.

**M2. A fully busy instance fails its health probe, and the manager restarts it along with its tasks.** An instance counts as healthy only if a 16-token completion returns in under 10 s (`bin/bonsai2-manager:855-863`). The harness fills all 8 slots, and llama-server holds a new request until a slot frees, so under full load the probe times out. After five failed cycles, a minute or two under that load, the manager runs `systemctl restart` and kills every in-flight completion. Their retries then fill the other instance, which then fails the same way. The M7 saturation test and the skill's fan-out advice both create this load. Treat an instance that answers `/v1/models` with every slot busy as healthy, for example by reading `/slots`, or keep one slot free for the probe. The queueing is upstream llama-server behavior, which I could not check for the fork.

**M3. Unit-file rewrites never reach systemd.** When an instance stays over its memory cap, the manager rewrites `--ctx-size` in the unit file and restarts the unit (`bin/bonsai2-manager:1097-1135`, `:1802-1818`). Nothing in the manager runs `systemctl daemon-reload`, so systemd restarts the unit from its cached definition with the old context, while health.json advertises the new one. Each later recheck halves the context again, down to 8192, and the next reload or reboot brings the instance up at that size. The quant and context fallbacks rewrite unit 0 the same way. Run `daemon-reload` after each rewrite, or move the per-instance values into an `EnvironmentFile`.

**M4. In main-only mode, the manager exits.** When no instance fits, the manager sends READY and returns 0 (`bin/bonsai2-manager:2842-2843`), and `Restart=on-failure` does not restart a clean exit. health.json then stays frozen, `bonsai2-reload` waits about 215 s and returns 84, and nothing reaps tasks. The module docstring and SPEC S2.4 both say the manager stays up in main-only mode. Enter the probe loop instead of returning.

**M5. The memory meter reads the wrong source, in mixed units.** The planner and the drift watch read cgroup `memory.current`. The repo's own measurement says that number leaves out the GPU-side memory on GB10, in MAP 1.7: "do NOT meter via cgroup". An awake main stack holding about 70 GB shows 11 to 14 GB in its cgroup, so the planner almost always grants every instance, and the drift watch cannot see KV-cache growth. The manager also divides the KiB values in `/proc/meminfo` by 1e6 and treats the result as decimal GB (`bin/bonsai2-manager:421`), while the installer uses GiB for the same formula. Meter device memory per container, as the repo's VRAM probe does with `nvidia-smi`, and use bytes throughout.

**M6. One bad response or hung subprocess stops the probe loop.** Only three of the loop's passes catch exceptions (`bin/bonsai2-manager:2563-2617`). A non-HTTP listener on an instance port, a truncated response body, a JSON list where the code expects an object, or a full `/run` raises out of `run_probe_loop` and exits the manager. No `subprocess.run` call has a timeout, so a hung `docker inspect` or `systemctl` blocks the loop forever. Because health.json has no timestamp, as C8 describes, nothing notices. Catch exceptions per pass and per instance, add timeouts, and use `WatchdogSec`.

**M7. Restart guards, adoption after a restart, and reload math are wrong.**

- The double-restart guard takes its baseline once per process. After one automatic restart, the manager never restarts a later wedged engine (`:1703-1729`).
- The instance units have no `PartOf=` on the manager. After a manager restart, the manager treats the running instances as cold starts and checks them against free memory they already use. On a tight pool the manager exits 4 and loops until systemd stops restarting it.
- A unit that systemd has stopped after repeated failures stays "degraded" and keeps receiving tasks, because the loop never reads `SubState` (`:1632-1640`).
- Reload charges each started instance twice, so it reloads fewer instances than a boot would (`:1995-2031`).
- Each boot writes its measured duration into `boot_target_s`, which becomes the next boot's deadline, with a 10 s floor and no ceiling (`:2198-2234`).
- `CPUQuota=64%` limits only the `docker run` client process, not the container.

### The installer and operations

**I1. Users cannot run pool control on a default install, and a reboot breaks the commands.** The installer creates `results`, `dedup`, `bench`, `workspaces` and `control` under `/run/bonsai2` as root, and gives only `results` and `workspaces` to the user who ran sudo (`bin/bonsai2_install.py:1446-1466`). User-run `bonsai2-unload` and `bonsai2-reload` then fail with an uncaught PermissionError (`bin/bonsai2_harness.py:2610-2623`). Dedup writes fail silently, so dedup never works. `/run` is a tmpfs. After a reboot only the manager recreates `/run/bonsai2`, as root and without the subdirectories (`bin/bonsai2-manager:2191`), so spawn and status fail until someone runs the installer again. The run notes work around this with `sudo chown -R` on the whole tree, which makes S2 exploitable. Create the tree at boot with `tmpfiles.d` or `RuntimeDirectory=`, and give the shared directories to a group.

**I2. The pool does not start after a reboot.** The installer runs `daemon-reload` and restarts `bonsai2-sglang`, but it never runs `systemctl enable`, so `WantedBy=multi-user.target` has no effect. SPEC S2.5 and acceptance item S2.8 #1 both expect the pool to come up on boot. Enable the unit in step 7.

**I3. An unchanged re-run restarts the whole pool.** The installer compares the full text of the deployed `serving.yaml` with its template render (`bin/bonsai2_install.py:1779`). The manager rewrites `boot_target_s` in that file after every boot, with a different comment (`bin/bonsai2-manager:2198-2211`, `:2882`). Every re-run therefore sees drift, rewrites the file and restarts the manager, which reloads both 27B instances. The third run's ledger records about 11 minutes to healthy after a re-run with no real change. SPEC S6.4 promises that an unchanged re-run restarts nothing. The hermetic test misses this because it never runs the real manager. Compare only the keys the installer owns.

**I4. A fresh box cannot install.** When the runtime image is missing, the installer calls `bin/bonsai2_build_image.py`. That script needs a local `bonsai2-server-build:121a` image and stops with ERR_CONFIG without it (`bin/bonsai2_build_image.py:373-378`). Nothing builds that image, and nothing initialises the `third_party/llama.cpp` submodule it is built from. Re-runs on the author's box pass only because the image digest already matches. Build `image/Dockerfile` after `git submodule update --init` when the build image is missing, or list both as manual prerequisites.

**I5. Rollback leaves removed instances running.** `install()` stops a unit before it deletes the unit file, but `rollback()` only deletes the file (`bin/bonsai2_install.py:1211-1216`, compared with `:1839-1841`). A rollback from 2 instances to 1 leaves `bonsai2-server@1` running and holding about 14.8 GB with no unit file. The rollback test checks only that the file is gone. Stop the unit first.

**I6. Smaller supply-chain and prerequisite gaps.**

- Both Dockerfiles install apt packages without version pins. The Dockerfiles pin the base images by digest, but the image digest still drifts for a fixed engine pin.
- The installer does not check for the nvidia-container-toolkit, although SPEC S6.2 lists it as a prerequisite.
- The model `revision` is `main`, not the commit hash that sits in a comment next to it. The SHA-256 check still fails closed when the file changes.
- The free-disk check always reads `/opt`, even with `--opt-root`.
- The installer builds unit `ExecStart` lines by unquoted interpolation of config values.

### The bench

These defects affect every number the skill's cost rules use.

| ID | Scenario | Problem | Location |
|---|---|---|---|
| B1 | All | Timing starts at the first streamed chunk, not at the request, so time to first token and prefill are never measured. The request never asks for usage, so the bench counts tokens as `len(answer) // 4`. It leaves reasoning tokens out of every t/s figure. Counting them puts main's 8.52 t/s at about 25.9 | `bench/exec.py:125`, `:153`, `:223-227` |
| B2 | M6 compared with M1 to M5 | The main-only control runs with no output cap and one task at a time. The subagent rows run under a cap of one eighth of the context, 1,024 to 8,192 tokens, and up to 8 at once. Most subagent misses are truncation at that cap | `bench/run.py:718-732` |
| B3 | M2 | The report builder inserts a "fail" entry for every empty or failed task, and the rubric scores coverage only. Ten empty answers scored "10/10 files covered" and passed. The tasks run with tools off, so nothing executes | `bench/run.py:303-304`, `bench/score/m2.py:72` |
| B4 | M4 | The seeded diff names each bug in a comment, such as `# BUG-01 off-by-one`, and the scorer falls back to the reasoning text. The committed 10 of 10 came from a reply with an empty answer whose reasoning read the comments | `bench/goldens/m4/diff.patch:6`, `bench/run.py:258-262` |
| B5 | M5 | The "60K-token" document is 1,900 characters long, with a placeholder line where the padding should be | `bench/goldens/m5/qa.json` |
| B6 | M3 | The prompt contains no document, and both runs answered "No document section N was provided". The 1.00 spot-check score is a number typed on the command line for the re-run | `bench/run.py:132-135` |
| B7 | M9 | The driver deletes the workdir before scoring, and the "main re-run" executes the golden module, not the subagent's code. A timed-out run that made up its inputs scored the same 0.67 as a run that passed its tests | `bench/capability.py:525-533`, `bench/score/m9.py:89-90` |
| B8 | M10 | The scorer reads only the final answer and compares it with the golden files. A correct report failed because it wrote "files_003" where the scorer wanted "files_003.py" | `bench/score/m10.py:64`, `:123` |
| B9 | M1 | The prompt does not name the module file, but the scorer saves the code as `module.py`. Main's correct suites import `utils` or `mylib` and fail | `bench/score/m1.py:76-77` |
| B10 | M8 | The guardrail compares one short capped prompt's decode rate with M6's whole-run rate. Nobody measured main under load, and the committed value is 0.00 | `bench/run.py:1466-1497` |
| B11 | All | The config validates and records `repetitions: 3`, but nothing reads it, so every scenario ran once. Run-to-run noise on M7 was 10 to 14 percent, against a 10 percent regression gate | `bench/config.py:158-161`, `bench/report.py:106` |
| B12 | Audit | `doctrine_audit.py` checks that each cited scenario exists and counts recent "fail" rows. It never compares subagent rows with M6 or checks any number in `claims.json` | `bench/doctrine_audit.py:124-136` |
| B13 | Rebench | The comparability key leaves out the quant, the caps and the prompts, and "not comparable" exits 0, so the regression gate passes unlike runs | `bench/run.py:1081-1085`, `bench/rebench.py:302-303` |
| B14 | Runner | Rotation runs before the lock and deletes every `*.json` older than 7 days in whatever results directory it gets. The lock's path also depends on that directory | `bench/run.py:534-542`, `bench/rotate.py:44` |

The bench keeps every task's answer, reasoning, timings and tool log in its JSON rows. That is why the reviewers could confirm each defect above from the committed results alone.

### The skill

**K1. The skill tells the main model to do things the code does not support.** The tests reviewer compared every command, flag and exit code in `SKILL.md` with the parsers and found 22 mismatches. These have the most effect:

- It says `--workdir` confines the tools (`SKILL.md:87-88`). S1 shows that it does not.
- For exit 8 it says to re-spawn or raise the priority (`:431-436`). That returns the stuck task or duplicates it, as C3 describes.
- For exit 22 it says to cancel the task if the reaper has not already taken it (`:449-451`). Cancel does nothing on a finished task, and nothing kills the tool, as C4 describes.
- It says `collect` answers 84 when the manager is down (`:463-466`). `collect` never reads health.json and its allowed codes are 0, 2, 20, 21 and 22 (`bin/bonsai2_harness.py:172`).
- It tells the model to check `content` first and fall back to `reasoning_content` (`:439-441`). The collect payload uses `answer` and `reasoning` (`bin/bonsai2_harness.py:1341-1364`).
- It documents `bonsai2-cancel <TASK_ID> [--graceful]` (`:92`). The code ignores the flag.
- Its command reference leaves out `--reasoning` and `status --tasks`, which later sections require (`:501-505`, `:526-531`).
- The R3 and R6 templates point a `--tools off` task at `{diff_path}` and `{doc_path}` (`:748-757`, `:874-884`). A tools-off task has no tools, so the subagent cannot open either file.
- The "stateful" branch says a subagent keeps its context between chunks (`:230-236`). Steering a finished task returns exit 2, and SPEC lists stateful subagents as out of scope (`SPEC.md:2007`).
- The speed table labels the 8-stream total as single-task speed (`:344`).

**K2. The skill is long and contradicts itself.** It is 953 lines and 8,355 words, about 14,000 to 17,000 tokens each time the main model loads it. About a quarter of it is dated incident narrative. Several rules conflict:

- The fan-out size is "min(files, instances × slots)" in one place (`:185`) and at most 6 of 8 slots in another (`:253`).
- The default 1800 s deadline "covers the whole loop" (`:591-593`), yet "a spawn without an explicitly computed --deadline is a guess" (`:542-544`).
- One rule allows prebuilding index and CSS files while the pool is loaded (`:479-480`), and the next forbids "token-heavy main-side generation while the pool is loaded" (`:484-485`).

The skill itself records that the second live run "violated the pipeline it should have codified" (`:470-474`). The "fresh session" acceptance test drives the commands from a scripted Python driver, not a model (`tests/test_fresh_session_5_7.py:54-61`), so no test checks whether a model can follow the skill.

### Tests and gates

**T1. The gate never runs the tests.** `scripts/gate.sh` and the pre-commit hook run `bash -n`, shellcheck, ruff, a secret scan, a YAML parse and the doctrine audit. Neither one runs `unittest`. A missing shellcheck or ruff counts as a skip, YAML errors only print a warning, and `uvx ruff` pulls an unpinned ruff version. A commit that breaks `cmd_spawn` still gets "[gate] PASS", and PR bodies record that as the gate result. Run the hermetic suite in the gate with a pinned interpreter, and fail when a tool is missing.

**T2. The secret scan misses most secrets.** The scan ends in `grep -Eiq` inside a pipeline, under `set -uo pipefail` (`scripts/gate.sh:26`, `:93-100`). On a large diff, `grep -q` exits at the first match and the upstream stages die with SIGPIPE. `pipefail` then makes the pipeline's status 141, so the `if` treats a match as no match. The tests reviewer reproduced this with a synthetic copy of the pipeline. The pattern also needs a quoted value of 16 alphanumeric characters. It never matches unquoted YAML values like the repo's own `main_api_key:`, JSON keys, or `hf_` and `ghp_` tokens. Capture the matches into a variable before testing them, or use gitleaks.

**T3. The AI reviewer is the author's own model and cannot block.** `scripts/review.sh` sends the plan, the docs and the raw diff to the same local Qwen3.8-27B that wrote the code (`scripts/review.sh:3-7`). The diff comes last, with nothing marking it as untrusted data. The script exits 0 whatever the reviewer finds. The code embeds reviewer dispositions such as "G3 PR#18 r9 P1 (verified false-positive, re-verified with a probe)" (`bin/bonsai2_tools.py:256`), and the next review reads them as part of the diff. Mark the diff as data, fail on P0 findings, use a different model or a person for merge reviews, and keep dispositions out of the source.

**T4. Some tests cannot fail, and some mocks differ from production.**

- The double-claim test accepts two successful spawns and checks for distinct PIDs across distinct records, so a second worker on one record still passes (`tests/test_harness_spawn.py:726-852`).
- The process-group kill test only checks that the code cleared a variable, so it passes even if `_kill_group` does nothing (`tests/test_tools_module.py:268-277`).
- The workspace ownership test asserts ownership only when it runs as root (`tests/test_remediation_second_practical_run.py:1108-1131`).
- `MockManager` writes `bunloaded` entries into health.json, which the real manager never does, so the pool-control tests take a path production never takes (`tests/fixtures/mock_manager.py:172-186`).
- The manager loop tests patch out every function that can raise, so they cannot catch M6.
- No test covers the riskiest paths: bash leaving the workdir, two processes racing for the last slot, an empty lock file, a reaped task whose worker is still alive, or a manager pass that raises.

The suite has strong parts too. The harness tests call the real `cmd_*` functions, start the real worker process and stream from loopback fixture servers. The installer tests fake every effect on the host through one `Seams` object, so the real rendering, validation and rollback code runs.

### Documentation

**D1. The docs report more as done than the code does.**

- `fixes-third-practical-run.md` describes the I1 fix, an `include_terminal` flag, down to line numbers. No code implements it, and its checklist is entirely unchecked.
- The README status is dated 2026-09-30 and cites 730 tests. The suite now has 790 test methods, and the README does not mention the three live runs or PRs #37 and #38.
- The README calls SPEC "locked". SPEC's own header says `status: proposed`, last reviewed on 2026-09-25.
- MAP marks Phase 4 done although M8, M9 and M10 fail their acceptance criteria. It ticks items 6.7 and 6.8 on hermetic tests alone, though the first real install then deadlocked and shipped commands without the exec bit. It has no entries for the live runs.

## What is done well

- The code writes every record, result, ack, YAML file and unit file through a temporary file and `os.replace`, so readers never see half-written JSON.
- One function, `emit`, writes all command output and raises on any exit code outside the 12, and each command declares its allowed subset (`bin/bonsai2_harness.py:156-176`, `:285-298`). A table test produces all 12 codes through live cases.
- The model fetch checks SHA-256 and byte size before it promotes a `.part` file, and resumes with HTTP range requests (`bin/bonsai2_model_fetch.py:239-244`). The engine builds from the submodule's commit, not the moving branch, and the image build checks the engine banner and the baked model hashes (`image/Dockerfile.runtime:180-230`).
- Prerequisite checks write nothing, and a hard failure returns before the first write (`bin/bonsai2_install.py:1524-1533`).
- `read_file` and `write_file` resolve symlinks and check the path again after `mkdir`, which closes the usual symlink escapes.
- The streaming parser assembles fragmented tool-call arguments by index, which avoids the common bug of keeping only the last fragment.

## Recommendations

1. Treat the current bench numbers and the skill's cost rules as unproven. Fix B1 and B2 first. Use the server's usage counts, time from the request, and give M6 the same caps and concurrency as the subagent rows. Then fix the rubrics B3 to B10, and run every scenario at least three times.
2. Fix the task state machine as one change. Make every state change a compare-and-set under the per-task lock, count `engine_retried` in the slot charge, run a claim pass in the manager loop, switch to `flock`, and prune old records. That covers C1, C2, C3, C5 and C6.
3. Sandbox the bash and python tools, or state plainly in SPEC S0 and the skill that they run with the operator's full rights. Clamp tool timeouts to the time left, and kill tool process groups on cancel and reap. That covers S1 and C4.
4. Fix the boot and ownership path before loading a second instance. Drop `After=bonsai2-sglang.service` from the instance units, enable the manager unit, create `/run/bonsai2` with `tmpfiles.d` and group ownership, validate request ids, and run `daemon-reload` after unit rewrites. That covers M1, M3, I1, I2 and S2.
5. Make the gate run the 790 hermetic tests, fail on missing tools, and replace the secret scan with gitleaks.
6. Rewrite the skill against the code as it is. Fix the 22 mismatches, cut the incident narrative, and test the skill with a real Qwen session instead of a scripted driver.

## Method and limits

- This is a read-only static review of commit `b17eed0`. Seven reviewers worked in parallel, one per area: the manager, the harness, the worker and tools, the installer, the bench, the tests with the gates and the skill, and the docs with the history. Each read its files in full. I re-checked the main findings in each area against the source.
- No repo code was run. The local sandbox blocked the hermetic test suite because the code comes from an external repo. To run it yourself, use `cd ~/UACP/qwen-tory38 && HOME=$(mktemp -d) /opt/homebrew/bin/python3 -m unittest discover -s tests -t .`. Expect a few failures that come from macOS rather than the code. `tests/test_harness_spawn.py:958` compares `/var` with `/private/var`, and some bench tests need the `requests` package, which is not installed here.
- The llama.cpp fork submodule is not checked out. I inferred engine-side behavior, such as the default bind address, the `/tools` endpoint and request queueing when all slots are busy, from upstream llama-server.
- There is no live box. The systemd ordering effects in M1 and M3, the memory metering in M5 and the reboot path in I1 and I2 come from standard systemd semantics and the repo's own run ledgers, not from a live test.
