# Review of qwen-tory38 on one DGX Spark

Review date: 2026-10-05. The target is your personal GB10 system and its existing main agent. Recommendations assume one shared GPU and unified memory pool.

## Assessment

The architecture fits your intent. Main plans and defines interfaces. Bonsai workers implement bounded tasks in separate workdirs, run checks, and return evidence. Main unloads the pool, verifies the artifacts, and integrates them. The third practical-run ledger records four delivered artifacts and main-side verification. That is useful evidence that this division of work can succeed on your machine.

The implementation does not yet enforce that workflow reliably. Context admission uses the whole instance context instead of one slot. Retry tasks disappear from capacity accounting. Queued work can remain queued after capacity becomes free. Deadline handling can report a freed slot while generation continues. Memory admission treats a cgroup reading as the main model's unified-memory footprint, although the main repository explicitly documents that this reading misses CUDA allocations. These are correctness issues before they are tuning issues.

My evaluation is a working personal prototype with a sensible division of work, substantial testing, and useful live-run records. I would keep this design and strengthen its deterministic controller. I would not spend the next iteration adding agents, increasing concurrency, or extending worker reasoning.

| Your question | Answer |
|---|---|
| Does it do what you intended? | Yes for the demonstrated bounded tasks. The skill expresses your phase order. The manager does not guarantee queue progress, actual cancellation, or unloading before main reasoning. |
| Does it manage and serve models like the main repository? | Similar deployment shape: ARM64 CUDA image, Docker under systemd, model identity, health probes, constrained memory settings. Different inference system: Prism llama.cpp with fixed slots and replicated weights, versus SGLang with a shared cache pool and DFlash. |
| Are context and concurrency efficient? | Sharing weights across slots is sensible. Two replicas and sixteen configured slots need measurement. The context gate, memory units, and sizing formula have defects. Small prompts do not necessarily cause small initial llama.cpp cache allocation. |
| Can the skill yield at least three times more work in the same main context? | Plausible for independent implementation with compact returns. The repository does not measure this. Full ledgers, generated files, repeated polling, and main verification can erase the gain. |
| What should you borrow from frontier systems? | Explicit task contracts, separate worker context, small evidence returns, persistent progress, deterministic completion handling, and evaluation of accepted work. Their many-agent results do not establish a speedup on one GB10. |
| What would improve this narrow use case most? | Fix admission, leases, deadlines, and success semantics. Add one batch command that waits and unloads. Shorten the skill. Then compare one worker process against two using your actual tasks. |

## Scope and evidence

The review uses fixed snapshots, rather than moving branch names:

| Repository | Reviewed commit | Local snapshot |
|---|---|---|
| `tchurushinov/qwen-tory38` | `b17eed0619860e5193fe3c8001bb0f2a436ab17` | Subagents source |
| `hassio5703/dgx-spark-qwen38` | `d62e739f1169179e908c934fdf399bb796624860` | Main source |
| `PrismML-Eng/llama.cpp` | `9a9394a895b96003ca842a6041cb28ac49a108f7` | Selected upstream files inspected at this exact pin, as configured by the subagents repository |

I reviewed the manager, task routing and persistence, streaming worker, tools, model and image packaging, installer and units, skill, benchmark execution and scoring, tests, and practical-run records. The main repository comparison concentrates on the SGLang service, memory guidance, and lifecycle controls. I did not audit every main-repository feature or the full CUDA kernel implementation.

The subagents snapshot has 191 tracked entries, counted with `git ls-files`, and the default test suite has 30 tracked `tests/test_*.py` modules and 790 test function or method definitions, counted through Python AST inspection. The runner executes 792 cases because inherited cases and discovered cases and discovery affect the total.

Validation used Python 3.14.7 on macOS, PyYAML 6.0.3 installed inside this review directory with your approval, and local mock servers. There was no Docker build, deployment, model download, GPU benchmark, or change to the reviewed implementation.

| Check | Result | Evidence |
|---|---|---|
| Full default suite | 792 cases, 787 passed and five failed because `/bin/sh` could not find `python` | Full-suite log |
| Recheck of those five cases | All five passed after adding a project-local `python` alias to the same interpreter | Host recheck |
| Targeted audit reproductions | 15 passed, confirming the defective behavior described below | Reproduction script, results |
| Source cleanliness | Both snapshots retained clean tracked working trees | Read-only Git status checks |

The reproduction assertions deliberately expect current defects. Their passing result is evidence of a defect, not a health certificate. The mock suite passed across the full run and host recheck; there was no second complete 792-case run. Local mocks do not validate system ordering, CUDA release, memory accounting, or real model quality.

The September benchmark files precede the latest reasoning-off and practical-run fixes. I use them to assess what their measurements support, not to claim that every old task failure remains present at the reviewed commit. The third practical-run ledger records four first-round successes, with task wall times of roughly four to fifteen and a half minutes. It also records a six-minute loaded-idle gap and main bookkeeping before unload. Its remediation proposal is present, but the proposed terminal-task flag is absent from the reviewed implementation.

The code has useful foundations. Model download checks hashes and sizes. Task and result writes use temporary files and atomic replacement. Steering merges pending messages. Tool execution tracks exit codes and kills process groups on its enforced timeout path. Mock and GPU tests have separate entry points. The live ledgers record failed delegation and main fallback instead of counting any finished artifact as a worker success. Keep these properties during the scheduler repairs. The large test count does not compensate for mocks that accept impossible system ordering or tests that encode truncated output as success.

## Findings

P1 means fix before relying on unattended batches or resource guarantees. P2 means a material reliability, efficiency, or evaluation problem. Findings below name source files and one-based line locations from the snapshots.

### F01. P1: memory admission does not preserve the claimed shared-memory reserve

In `bin/bonsai2-manager`, `main_used_gb` at lines 499-534 prefers the main container's `memory.current`, `planning_tier` at lines 537-570 subtracts that value from total memory. The function calls the live reading sleep-accurate.

That assumption conflicts with the main repository's GB10 memory evidence. That document reports CUDA allocations outside cgroup accounting, including an active generation with only 7.15 GiB charged to the container. A small main cgroup reading cannot establish that the main model is asleep or that its GPU allocation is available to workers.

The second admission gate, `allocation_tier` at lines 722-786, checks available host memory against the instance need plus a small margin. It does not reserve the configured eight-unit OS reserve after allocation or reserve the main model's wake-up growth. Drift monitoring checks instance cgroups, not global available memory. The September report records `MemAvailable=251744` KB at M8, about 2.43 GiB, well below the intended reserve. This does not prove an OOM, but it disproves a guaranteed eight-unit floor during that run.

Use byte-based accounting and measured host headroom. Include main awake demand, wake-up margin, existing workloads, and worker peak allocation. Reserve the main awake requirement even when it sleeps, unless the controller explicitly prevents a wake during worker execution. Monitor a global low-water threshold and pause admission before the machine becomes tight. Docker's host memory cap alone cannot enforce this GPU budget on the documented GB10 stack.

### F02. P1: context admission compares against the whole instance

`bin/bonsai2_harness.py`, lines 769-984, compares `ctx_need` with `instance["ctx"]`. Worker retry selection at lines 226-265 in `bin/bonsai2-taskworker.py` makes the same comparison.

The configured instance has `--ctx-size 262144 --parallel 8`. The pinned engine divides non-unified context among sequences, giving 32,768 tokens per slot. The reproduction submission submits a 65,536-token requirement and the router accepts it. The skill already explains the 32,768-token limit, so code and instructions disagree. The engine calculation is visible in pinned `llama-context.cpp`.

Publish separate total and effective per-slot limits. Validate the rendered prompt, tool schemas, retained history, and output reserve against the effective limit. Repeat that check as a tool conversation grows. `--ctx` is currently an admission hint; it does not resize the engine cache or establish the actual token count. A 32,768-token output budget cannot fit alongside a nonempty prompt in a 32,768-token slot.

### F03. P1: retries bypass capacity accounting

`CHARGED_STATES` at line 250 of `bin/bonsai2_harness.py` includes `queued` and `running`, but omits `engine_retried`. `running_count` and `_instance_loads` at lines 645-671 also miss retry execution. The worker's `_pick_retry_instance` at lines 226-265 chooses a healthy untitled target without checking its occupied slots.

The reproductions show an active retry counted as zero occupied slots and a retry routed to an already full one-slot target. This permits oversubmission, misleading health data, and invisible waiting inside the engine. `engine_retried` can persist through subsequent steps, so the discrepancy can last for the rest of the task.

Move retries through the same atomic slot lease as initial admission. Track execution state separately from retry history. Keep one active lease until execution has actually stopped. Test two simultaneous failures trying to move into the same remaining slot.

### F04. P1: queued tasks can remain stranded after slots become free

`claim_slot` at lines 929-984 of `bin/bonsai2_harness.py` runs from the active spawn wait loop at lines 2268-2299. Worker completion, collection, and the manager reaper do not run a queue dispatcher. Spawn can return exit 8 after its wait budget while leaving the task queued. Repeating an identical spawn can take the de-dup path and return without entering the claim loop.

If all waiting spawn processes leave before the running tasks finish, queued tasks have no reliable owner to start them. The reproduction leaves a queued task unchanged through collection and reaping despite an available slot, then shows that an explicit claim can promote it. A later unrelated spawn can mask the problem. It is not a dependable scheduler.

Make the manager own queue progress. Spawn should persist a task and return its ID promptly. The manager should dispatch on submission, completion, recovery, and a periodic backstop. Add a queue deadline, fair ordering, and tests where every submitting process has already exited.

### F05. P1: deadlines and cancellation can release bookkeeping before generation stops

`_stream_chat` at lines 297-530 of `bin/bonsai2-taskworker.py` has a socket inactivity timeout, but does not check the whole task deadline while receiving an ongoing stream. The completion ID is kept locally; the sidecar is written after the stream returns.

During the first long generation, the reaper therefore lacks the current completion ID.

The cancellation helper at lines 1032-1088 of `bin/bonsai2_harness.py` sends `action="reasoning_end"` at the pinned engine, that requests transition from reasoning into an answer. It does not mean abort and release the slot. See the pinned control handler.

The reaper marks a live worker timed out after the deadline and 65-second grace without terminating that worker. `_persist_rec_preserving` at lines 266-294 preserves steering fields, but can overwrite the terminal state with a stale worker snapshot. A reproduction confirms `timeout` returning to `running`. These paths can advertise capacity while an old completion still uses bandwidth.

Persist the current completion ID on its first stream event. Check an absolute monotonic deadline during streaming and tool execution. Close the request and validate the engine's actual disconnect or abort behavior at this pin. Make terminal state transitions monotonic. Release the lease only when worker and engine execution have stopped. A live regression must prove that a cancelled stream stops advancing engine tokens and frees its real slot.

Reasoning off is a valid practical improvement and the latest ledger shows it working. It reduces the frequency of this failure; it does not replace cancellation.

### F06. P1: token-capped output can be reported as complete

In `bin/bonsai2-taskworker.py`, the tool loop near line 1446 treats `finish_reason=="length"` as a special failure only when the answer is blank. A nonempty partial answer can become `done` with `partial=false`. The tools-off completion path at lines 1840-1885 accepts an otherwise successful response without checking the finish reason. The reproductions show both a truncated answer and an empty answer with capped reasoning reported as done.

Require a valid completion boundary and distinguish output from capped or interrupted output. Preserve salvageable content as partial. Keep task execution success separate from artifact acceptance. Main must still run the task's acceptance checks, but collection should not label truncation as a successful finished answer.

### F07. P2: output limits do not bound worker memory use

`_run_capped` at lines 308-360 of `bin/bonsai2_tools.py` captures all subprocess output through `communicate`. The public output cap applies after capture. `read_file` at lines 442-454 reads the entire file before truncation.

A verbose command or large file can consume far more memory than the configured result limit. These host-side workers are outside the model container's memory cap. On a GB10, that competes with the same pool you are protecting for inference.

Drain output continuously into a bounded tail buffer or a disk file. Return a short tail and artifact path. Support bounded file ranges. Keep full logs on persistent disk when needed, and report truncation explicitly.

### F08. P2: unload can acknowledge success while an instance remains active

The unload branch near lines 1945-1988 of `bin/bonsai2-manager` retains the old state when `systemctl stop` fails, but still writes an acknowledgement with `state="ok"`. It also marks an instance unloaded even when `wait_down` reports false. The reproduction confirms an OK acknowledgement with the instance still in `runtime.spawned`.

This is especially harmful to your phase order. Main can believe unload completed and start expensive reasoning while workers remain resident or active.

Return partial or failed status for unresolved targets. Verify the service and container closed, the listener closed, and host memory recovered within a defined bound. The main repository already checks memory release before starting another engine through `engine-preflight.sh`. Adopt that principle for the worker-to-main transition.

### F09. P2: the runtime command does not enforce the recorded image identity

`instance_exec_start` at lines 706-738 of `bin/bonsai2_install.py` runs the mutable `image_tag`. The YAML records a SHA256 identity and installation checks it, but a later retag changes what a restart runs without changing the recorded identity. The reproduction confirms a tag-only launch command.

Render an immutable local image ID or a verified repository digest into the actual command. Validate identity when starting an instance. Distinguish Docker image IDs from registry manifest digests when choosing the command format.

### F10. P2: CPUQuota applies to the Docker launcher, not clearly to inference

The generated unit at lines 680-704 of `bin/bonsai2_install.py` sets `CPUQuota=64%`. `ExecStart` launches the Docker client. The manager itself looks for the container under a separate Docker cgroup. The Docker command supplies neither a container CPU limit nor a parent cgroup that would make the unit quota govern inference.

The corrected single percent sign fixes syntax. It does not establish the intended server CPU constraint. If applied, 64% means 0.64 CPU worth of time, not 64% of the machine.

First decide whether limiting CPU improves this workload. If it does, enforce and inspect a container limit through Docker. Its documented controls include `--cpus` and CPU quota settings. `Docker resource constraints`.

### F11. P2: benchmark measurements cannot support the current cost doctrine

Several errors affect allocation advice:

- `score_stream` near lines 99-199 of `bench/exec.py` divides all completion tokens by the content-only emission window. Reasoning tokens can contribute to the numerator while its time is excluded. A reproduction yields 100 tokens/s over a one-second content window for a ten-second stream. The scorer starts elapsed time at the first received chunk, losing request and initial wait time captured by its caller.
- When usage is absent, answer characters divided by four omits reasoning and tool-call generation. This is a rough estimate, not an inference-work measurement.
- The skill, lines 344-359, labels 44.7-61.9 tokens/s as a single-task rate while its citation identifies an eight-concurrent aggregate. That rate cannot be inserted directly into a single-worker cost estimate.
- The skill says tool execution dominates the 413-second M9 host task. Its matching host result records five tool calls totalling 61 milliseconds. Most wall time occurred outside those recorded tool bodies. The result estimates only the final answer tokens, so the claim about where time went is unsupported.
- `bench/capability.py`, lines 525-537, removes the workdir before later artifact scoring. The same M9 result fails because `text.txt` is missing despite a successful recorded run and main rerun. This is a benchmark lifetime bug, not evidence that the model failed to create the file.
- M8 at lines 1444-1507 of `bench/run.py` compares different main prompts and converts absent decode measurements to zero. Its fallback calls a control completion without unloading inside that function. A 100% reported drop does not establish a real zero-throughput or fixed twofold slowdown.

Measure submission-to-finish wall time, queue time, first token latency, actual prompt and completion usage, reasoning and answer generation, tool duration, and cache hits separately. Score artifacts before cleanup. Require useful-task success alongside throughput.

Your intended non-overlapping workflow also needs a different acceptance rule from M8's five-percent simultaneous-main guardrail. Keep overlap as a diagnostic. Test that main performance and memory recover after verified unload. Do not use the current table as a precise dispatch calculator.

### F12. P2: workdir and timeout guarantees exceed what the tools enforce

The direct file tools in `bin/bonsai2_tools.py` validate paths and include symlink defenses. The shell and Python tools at lines 392-433 run with a selected working directory and the host user's permissions. That does not confine access to the workdir. A reproduction reads a harmless sibling file through `cat ../audit-marker.txt`, entirely inside the review directory.

`_param_timeout` at lines 517-526 lets any positive model-provided timeout override the configured timeout. The reproduction changes a 60-second setting to 3,600 seconds. A long tool can outlive the task deadline, while the reaper releases bookkeeping without stopping it.

Either describe the real trusted-host boundary or isolate tool execution with task-scoped mounts and permissions. For your personal coding workflow, accidental writes to adjacent projects are a practical reason to isolate it. Enforce the minimum of requested timeout, operator maximum, and remaining task time. Preserve the existing process-group termination behavior.

### F13. P2: memory units and the sizing formula are inconsistent

`compute_n` at lines 519-544 of `bin/bonsai2_install.py` converts total bytes using `1024**3`. The manager converts totals and live memory using decimal divisors. Drift caps use binary units. A reproduction with the same 128-GiB input yields two instances during installation and three in the manager's calculation.

`bin/bonsai2_vram.py`, lines 1-112, models cache as `slots * ctx_size * coefficient`. However, this engine's configured context is already the total shared across fixed sequences. The measured coefficient embeds the eight-slot calibration. It is a useful fitted estimate at that operating point, not a sound general formula when changing parallelism.

Use integer bytes internally and explicit GB or GiB labels at presentation boundaries. Separate measured resident weights, cache per total context token, recurrent state per sequence, and peak scratch memory. Recalibrate against several context and parallel settings. Qwen's hybrid attention means a plain dense-transformer KV formula is also insufficient.

### F14. P2: rewritten systemd commands may not reach restarted engines

`rewrite_unit_ctx` at lines 1097-1137 of `bin/bonsai2-manager` changes a unit file. The drift restart path does not call `systemctl daemon-reload`. The model rewrite used for fallback has the same reload concern. Changing the file on disk does not update systemd's loaded unit definition.

The manager can report a reduced context while the restarted server still receives the old context. The reported value does not verify the restarted server's actual context.

Reload unit definitions before restart and confirm effective arguments or engine-reported context before updating health. A simpler alternative is a stable launcher reading validated runtime settings, so tuning does not require rewriting units. Keep restart failures visible.

### F15. P1: cold-start readiness still conflicts with instance ordering

The instance template and installer contain `After=bonsai2-sglang.service`. That manager uses `type=notify` and sends `READY=1` only after `spawn_sequence` and its health waits. `systemctl_start_instance` at lines 941-967 of `bin/bonsai2-manager` now uses `--no-block`, but this only returns after submitting the job. It does not bypass the instance's ordering dependency.

On a cold manager start with instances stopped, the instance waits for manager readiness while the manager waits for instance health. Timeouts can break the wait and let loading begin late, producing degraded initial health and a misleading boot measurement. Warm reload after the manager is ready will not exercise this case.

This is a static finding supported by systemd's documented semantics. I could not reproduce a systemd boot on this Mac. `type=notify` delays follow-up units until the readiness notification, and `After` waits for startup completion. `systemd service documentation, unit ordering documentation`.

Remove the reverse dependency on manager readiness, leaving Docker ordering and manager-controlled starts. Alternatively, signal control-service readiness before asynchronous pool loading and publish pool readiness separately. Add a cold-start integration check with all instance units stopped. The existing mocked start calls cannot detect this ordering cycle.

### F16. P2: batch completion and unload remain fragile main-agent responsibilities

`progress_report` at lines 1375-1443 of `bin/bonsai2_harness.py` omits terminal tasks. The third-run poller waited for terminal rows that disappeared instead. The proposed `--include-terminal` repair has not landed at this snapshot.

The skill also contains conflicting instructions. Lines 240-245 require unloading before token-heavy main work. Lines 300-305 describe a verification-only turn at halved speed before unloading, then moving expensive verification after unload. The latest guardrails disambiguate correctly: require collect, unload, then verify. Repeated rules leave the model to reconcile the contradiction during the exact phase where you want it doing very little.

Land the terminal visibility fix. More importantly, add a deterministic batch wait-and-release operation that owns the IDs, detects all terminal outcomes, unloads the batch's instances, verifies release, and emits one compact result. This directly addresses the observed idle gap and protects main context from polling transcripts. A conservative idle-unload timer can be a backstop, with guards for queued and active work.

## Serving comparison and memory efficiency

| Aspect | Main SGLang lane | Bonsai pool | Consequence |
|---|---|---|---|
| Packaging | Configured CUDA Docker image under systemd | ARM64 CUDA image with binary and hash-checked GGUFs under systemd | Good deployment resemblance; runtime immutable identity still needs E2G |
| Model execution | NVFP4, FlashInfer, DFlash draft model, compilation and graph settings | Prism llama.cpp ternary/GGUF execution | Engine and quantization behavior differ; copying flags cannot establish parity |
| Process layout | One serving process accepts multiple requests | Two default instances, eight sequences each | Weights share within an instance; replicas duplicate GPU model and runtime allocations |
| Cache and state | State memory fraction, shared KV pool, Mamba cache policy | Total configured context divided across slots, q4_0 KV | Similar goal, different allocation and request-admission semantics |
| Prefill | Chunked prefill size 8192 | Batch size 2048 | These settings have different meanings and need workload-specific tuning |
| Lifecycle | Sleep on idle, engine conflict ordering, memory-release preflight | Explicit reload/unload, manager health, fallback and drift logic | Worker lifecycle fits phases, but needs verified release and cold-start repair |
| Observability | Metrics enabled in service | Task records, tool logs, health probes | Add actual engine slot occupancy and streaming progress to detect bookkeeping drift |
| Client interface | Main tooling and proxy around an OpenAI-compatible service | Deterministic CLI and host-side tool conversation | Compact structured returns can save main context if collection is bounded |

These comparisons come from the main SGLang service, subagent settings, and rendered worker command. The main repository has several engine lanes; this comparison addresses the SGLang NVFP4 lane you described.

Your observed two main sessions adding only two or three GB initially is consistent with one server sharing weights and using an already allocated cache pool. It is not evidence that a second model process costs only two or three GB. Likewise, your 68-80 GB main footprint should be treated as total serving memory, not purely model weights. Cache, recurrent state, graphs, activations, and runtime buffers also matter. SGLang documents these allocation categories and the role of `--fraction-static`. Its general tuning advice must remain subordinate to the main repository's E2G-specific measurements. `SGLang memory tuning`.

The server prepares capacity for its configured context and sequence layout. Reducing per-task `--CTX` from 32K to 8K currently changes the routing hint, not server allocation. Increasing a worker's `--CTX` has the opposite problem.

Two replicas may improve batching or isolation, but they spend memory on duplicated model state and share the same memory bandwidth. Resident idle weights occupy memory; they do not continuously consume the bandwidth of active decoding. The ledgers' halved-main-speed observation needs a controlled comparison of unloaded, resident-idle, and actively decoding workers. Do not convert it into a universal law that a loaded idle pool halves main speed.

I would benchmark these candidates before changing the default:

| Candidate | Total context per process | Slots per process | Purpose |
|---|---|---|---|
| One process | 65,536 | 2 | Two 32K workers with the smallest replica count |
| One process | 131,072 | 4 | Four 32K workers sharing one weight copy |
| Two processes | 65,536 each | 2 each | Same four-worker capacity with separate engines |
| Current configuration | 262,144 each | 8 each | Reference point for memory and accepted-work throughput |

These are test candidates, not proven safe allocations. Confirm the engine's effective per-slot context and model quality in each case. Hold task inputs and acceptance checks constant. Measure available-memory minimum through main wake-up, long prefill, worker decode, tools, cancellation, unload, and main verification. Choose accepted tasks per hour and safe headroom, not configured slot count. Start with two active workers; expand only when the next level improves accepted throughput.

Keep q4_0 as the measured cache baseline until a quality and memory comparison justifies changing it. Check whether prompt caching benefits repeated tool steps and whether host-side cache memory is bounded. Stable prefixes and routing the same task to the same actual engine slot can help, but the current harness lease does not identify a native slot. Validate reuse before building a more elaborate slot-affinity layer.

## Skill changes for more work per main context

The strongest existing rules should stay: main owns architecture and interfaces, each task has one writer, workers receive bounded scope and executable acceptance checks, reasoning is off by default, and main verifies after unload. The live run supports pre-seeded files with specific modifications as a useful task shape. It does not establish that every coding task should be converted into a skeleton first. Count skeleton creation and verification as main work when measuring savings.

The skill is 953 lines and 53,811 characters. Dividing characters by four gives roughly 13.5K tokens as a rough sizing heuristic, not a measurement with your tokenizer. Loading this history-rich file into main consumes context before any useful delegation. Move historical runs, calibration tables, and uncommon recipes to referenced files. Keep the active skill roughly 100-150 lines, with one unambiguous workflow and one task contract. Measure the real tokenizer cost before setting a strict limit.

A worker packet should include:

- Task ID, batch ID, objective, and a short explanation of how the task fits the plan.
- Allowed files, interface signatures, named dependencies, and explicit exclusions.
- Relevant source paths or excerpts, with a base revision or file hashes.
- Concrete acceptance commands and expected behavior, including meaningful edge cases.
- Input, output, step, and wall-time budgets derived from actual measurements.
- A fixed result schema and a rule to stop with a blocker when required assumptions fail.

Give workers enough local context to act without reconstructing the architecture. Do not send the main conversation or entire repository documentation by default. For a task whose tool history approaches its slot limit, either finish and checkpoint or start a successor task from a bounded summary and artifact references. A long-running project can use many short worker tasks; one endlessly growing worker conversation is unnecessary.

Return artifacts by path and hash. Main's default collection should contain a compact summary, changed-file list, acceptance outcomes, blockers, and references to detailed logs. A reasonable starting target is 300-600 tokens per task, with a larger return only when main requests a specific diagnostic. Do not inject every tool call's output or reasoning into main context.

The controller should execute acceptance commands from the task contract and record their outcomes directly. A model-written statement that tests passed is weaker evidence. Preserve the distinction between checks that run successfully and behavior that those checks actually cover. The third run's browser verification added evidence that syntax and identifier checks alone could not provide.

Use one active workflow:

1. While workers are unloaded, main writes the plan, task contracts, dependency order, and integration checks to persistent project files.
2. A deterministic batch command reloads only needed instances and submits ready tasks.
3. The controller schedules, monitors, applies deadlines, and records progress. Main remains idle unless a decision is required.
4. The controller observes every task's terminal state, stops execution, unloads the batch's instances, and verifies release.
5. Main reads one compact batch summary, reruns required checks, reviews selected diffs, and integrates accepted work.
6. Main checkpoints accepted changes and the next frontier before another batch.

This command is a proposed addition. It is not available in the reviewed CLI. Until it exists, poll known IDs through `collect`, preserve their exit codes, unload in the tick where the last task becomes terminal, and require successful release before verification.

`status --tasks` alone is not a completion signal at this commit.

Mechanical operations should stay mechanical. Starting a test suite, formatting files, hashing artifacts, merging a machine-readable report, or waiting for completion does not need a model call. Delegate interpretation or implementation when that work benefits from model context. This also corrects the current skill's tendency to route pure test execution through a worker model.

## Measuring the threefold target

Three times more accepted work in a 262K main context is a context-efficiency claim. It is separate from three times faster completion or three times lower total token use.

For comparable accepted units of work, measure:

```
main context per delegated unit =
  planning + dispatch + compact collection + verification + retries

context gain =
  main context per accepted solo unit /
  main context per accepted delegated unit
```

Track actual retained main-history tokens, including tool responses, loaded skill text, and summaries. Summing API input usage over calls counts repeated history many times and is not the same measurement. Worker tokens matter for compute cost, but do not automatically occupy the main window.

For illustration only: a solo unit consuming 10K main tokens versus a delegated unit consuming 1K for planning and dispatch, 0.5K for collection, 1.5K for verification, and 0.3K for other overhead yields about 3.03 times the accepted work per variable context budget. That advantage disappears if main rereads the full generated file and debugging transcript or regularly rewrites failed output. Fixed skill and system context reduce available capacity in both cases and must be included in the actual session experiment.

Run paired trials on representative bounded tasks. Count only accepted artifacts. Include main-created skeletons, review fixes, failed batches, retry prompts, and unload overhead. Report main tokens per accepted task, accepted tasks before the context limit, total worker tokens, wall time, and success rate. The repository currently has no evidence for a measured threefold gain.

For elapsed time, account for the entire phase sequence:

```
batch wall time = plan + load + worker waves + stop/unload + verify + rework
```

On one GB10, worker waves share bandwidth. Do not estimate their duration by dividing every task by an aggregate multi-request token rate. Amortize model loading across an already planned wave. Keep related worker waves loaded only when main needs no substantial reasoning between them and global headroom remains safe.

## What frontier teams do, and what transfers here

Anthropic's research system uses a lead agent that plans and workers with separate context that return condensed findings. It emphasizes explicit objectives, boundaries, output formats, effort budgets, and outcome evaluation. It also reports substantially greater total token use and warns that coding with many dependencies offers less parallel work than broad research. The transferable idea is context separation and clear delegation. Its research benchmark result does not predict your coding throughput. Anthropic multi-agent research system.

Anthropic's context guidance discusses compaction, structured notes, and worker summaries for work that exceeds a single conversation. For your project, put task state and detailed evidence on disk and return the smallest summary that lets main decide what to accept or inspect. Do not reload historical ledgers into every batch. Anthropic context engineering.

Its long-running-agent work uses explicit progress records, incremental features, and verification so a fresh session can resume. Your equivalent is a persistent batch manifest, accepted-artifact ledger, and next-task list with recorded checks. Default state under `/run` is useful for live control but will not survive reboot. Keep durable project progress and accepted artifacts outside that transient state. Anthropic long-running agents.

Anthropic's parallel compiler experiment stresses independent task selection and tests that give agents useful feedback. The lesson for your smaller worker is a bounded change with strong local checks and minimal overlapping file ownership. Reproducing the experiment's agent count is unnecessary. Parallel compiler experiment.

Google's agent-scaling study compares architectures across tasks and finds that the value of additional agents depends on task structure. Parallel work can benefit; sequential dependencies and coordination overhead can negate the gain. For your setup, benchmark independently reviewable edits and keep architectural decisions on main. Google agent-scaling study.

My inference from these sources and your logs is that deterministic coordination will buy more than a richer orchestration prompt. You already have the lead-worker pattern. The missing cheap wins are reliable handoff, bounded returns, persistent progress, and measurements of accepted work.

## Recommended order of work

| Order | Change | Effort | Expected benefit and acceptance check |
|---|---|---|---|
| 1 | Correct slot context, retries, queue ownership, terminal states, and cancellation | Medium across several files | No oversubscription, stranded tasks, false complete answers, or orphan generation. Prove with failure-path tests and a live cancellation check. |
| 2 | Fix byte units, main awake accounting, global reserve, unload acknowledgement, and cold boot | Medium | Prevent admission on misleading headroom. Verify a stopped-pool cold start and measured memory recovery. |
| 3 | Add batch wait-and-release with compact collection | Small to medium after scheduler fixes | Remove polling context and the observed idle-loaded gap. Main receives a result only after verified release. |
| 4 | Shorten the active skill and add a fixed task/result contract | Small | Lower repeated context cost and conflicting instructions. Measure token size and run the same live task shapes. |
| 5 | Repair benchmark timing and artifact lifetime | Small to medium | Obtain trustworthy accepted-work and contention measurements before tuning. |
| 6 | Compare one process with two at matched per-slot context | Small experiment after safety fixes | Reduce duplicated residency if one process gives equal or better accepted throughput. |
| 7 | Bound tool buffers, clamp timeouts, and persist project checkpoints | Small to medium | Protect unified memory and recover useful work after interruptions. |

These effort labels describe relative scope, not delivery estimates. The first two rows establish correctness. For the largest usability gain with modest implementation work, prioritize the batch command, compact result mode, and shorter skill once those invariants hold.

I would also make these narrow changes:

- Attach batch ownership to tasks and instance leases. One main session's `unload --all` must not silently terminate another session's active work. Queue whole worker phases when two main sessions share the same pool.
- Separate historical evidence from current capability. Maintain a short table of task shapes, current p95 sample size, accepted success rate, main context cost, and verification cost. Remove single-task rates derived from aggregate runs.
- Add an operator command that reports installed code identity, engine through effective context, real artifacts, and global headroom. The third run begins with stale installed files despite a newer repository.
- Keep diagnostics out of ordinary success return. Offer full tool logs and reasoning only through explicit detail retrieval. Save failures to disk and return the error, partial artifact references, and the next useful action.
- Use persistent workdirs for variable generated artifacts. Apply retention limits to finished task records and detailed logs.
- Prefer a schema-validated task manifest over more command-line options and prompt prose. A stable JSON schema can feed routing, deadlines, checks, summary limits, and batch ownership from one source.
- Refactor scheduling and resource policy into explicit state transitions. The current command, worker, and installer contain long historical comments and duplicated policy. Preserve the useful regression tests while making lifecycle rules readable in one place.

The next live acceptance run should include a cold boot, more tasks than available slots, a retry into a busy instance, a continuously streaming task past its deadline, token-capped output, a failed unload, and a main wake-up under worker residency. Then compare accepted work and main context against solo execution. Your successful four-task run is a good foundation for that test; it does not cover these failure modes.

## Final evaluation

Keep the project specialized. A single GB10 favors planned worker phases, compact evidence, and main-side decisions after release. The current design already supports that shape and the latest live record demonstrates useful output.

The next version would be stricter in code and shorter in instructions. Make the controller responsible for scheduling, capacity, deadlines, and unloading. Let the skill describe task selection and acceptance. With those changes, a threefold main-context gain becomes a testable target instead of an expectation resting on successful individual runs.
