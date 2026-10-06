---
name: bonsai2-harness-v2
description: "Delegation doctrine for the bonsai2 subagent pool (v2, 2026-10-06). Use when offloading bounded coding tasks to the Bonsai-2-27B worker pool: one deterministic batch workflow, task contracts, exit-code handling, and verified release. Replaces v1 SKILL.md (frozen, not deleted)."
status: v2 — replaces SKILL.md (v1) from 2026-10-06; v1 is frozen, not deleted.
---

# bonsai2-harness v2 — delegation doctrine for the subagent pool

One GB10 box: main = Qwen3.8-27B NVFP4 on SGLang at :30001 (external —
consume, never modify). Pool = N instances of Ternary-Bonsai-2-27B
(PQ2_0, Prism llama.cpp fork) under `bonsai2-server@i` systemd units.
You drive the pool ONLY through the `bonsai2-*` commands — each prints
one JSON line and exits with a closed-set code.

**MVP rule:** until the end-to-end integration check passes, load and
test with exactly 1 instance (`install.sh --instances 1`). N=2 is the
final branch test, not an intermediate one.

## What the pool actually does (verified against the code)

- Each instance: one container, `--ctx-size 262144 --parallel 8` → the
  engine divides the context across the 8 slots: **32,768 tokens per
  task** (effective per-slot limit, not the whole 262,144). `--ctx` on
  spawn is validated against this effective limit; `--ctx 65536` exits 5.
- Slots are charged for a task's WHOLE lifetime, including `engine_retried`
  (a retried task still occupies its new instance).
- `queued` tasks are dispatched by the MANAGER when a slot frees — a
  spawn that exits 8 does NOT strand the task. Re-spawning the same
  prompt is dedup-safe: it returns the existing task.
- Tools run with the OPERATOR's host rights in the task workdir:
  non-interactive `sh -c` / the Python interpreter, scrubbed env, cwd =
  workdir, process-group killed at timeout. Tool timeout is clamped to
  `min(requested, tool_timeout_s, remaining task deadline)` — a tool can
  never outlive its task. Tool output is bounded: the model sees the
  last `tool_output_max_kb` of output (full log is spooled to the
  workdir, path in the tool result's detail). `read_file` returns at
  most 256 KB (use `offset`/`limit` to page); `write_file` rejects
  content over 512 KB (split the write). `read_file`/`write_file` have
  path + symlink checks; `bash`/`python` do NOT (a `cat ../x` in bash
  escapes the workdir — isolation is a follow-up, not a guarantee).
- A task whose answer hit the output cap is collected `done` WITH
  `partial: true` — treat it as a partial artifact, never a complete one.
- Unload acks carry `state: "ok"` ONLY when the stopped instances' ports
  are actually down; `state: "partial"` lists `unresolved` targets. Do
  not start heavy main work on a `partial` ack.
- `health.json` is timestamped; a dead manager surfaces as exit 84 after
  ~3 probe cycles (not forever).

## Commands (exactly what exists — no invented flags)

| command | purpose | exit codes |
|---|---|---|
| `bonsai2-spawn` | submit a task (`--tools on\|off`, `--ctx`, `--deadline`, `--priority`, `--workdir`) | 0 accepted/running, 2 args/config, 3 bfailed(port)/bdown, 4 bfailed(engine OOM/hash), 5 ctx too long, 8 queue wait expired, 80 main-only, 82 main down, 84 manager down |
| `bonsai2-collect <ID>` | task record + `answer`, `reasoning`, `usage`, `tool_log`, `steps`, `workdir`, `partial` | 0 non-terminal poll, 2 unknown task, 20 done, 21 failed, 22 timeout |
| `bonsai2-status --tasks --include-terminal` | progress for all tasks, terminal rows included | 0, 2, 82, 84 |
| `bonsai2-steer <ID> <msg>` | message landed at the task's next step boundary (queued tasks: at first step) | 0, 2 (incl. steer to a running `--tools off` task), 8 |
| `bonsai2-cancel <ID>` | SIGTERM/SIGKILL the worker + its tool group; frees the slot | 0, 2 |
| `bonsai2-unload --all` / `bonsai2-reload` | pool control via the manager | 0, 2, 84 |
| `bonsai2-health` / `bonsai2-list-instances` | raw health doc / instance table | 0, 2, 84 / 0, 2, 82, 84 |

There is no `--graceful` flag. `collect` never returns 84. The payload
fields are `answer`/`reasoning` (not `content`/`reasoning_content`).

## The one workflow (batch)

1. **Plan, pool unloaded.** Write the plan, task contracts, dependency
   order, and integration checks to persistent project files. No token
   heavy main generation needed here, but the pool being unloaded means
   main runs at full speed.
2. **Submit.** `bonsai2-spawn` each ready task (fan-out ≤ instances ×
   slots; with 1 instance that is 8, but 2–4 is the realistic ceiling
   until the saturation re-bench). Each spawn is independent; exit 8
   (queue wait expired) is FINE — the manager dispatches it when a slot
   frees.
3. **Poll, don't babysit.** `bonsai2-status --tasks --include-terminal`
   every few minutes. Stop when every tracked id is terminal. Do NOT
   re-spawn to "wake" a queued task.
4. **Collect.** One `bonsai2-collect` per terminal task (exit 20/21/22).
   Read `answer` + `partial`; open files in the workdir for real
   acceptance — a worker saying "tests passed" is not evidence.
5. **Release, verified.** `bonsai2-unload --all`; require
   `state: "ok"` in the ack (ports actually down). On `partial`,
   re-issue for the unresolved targets before continuing.
6. **Verify + integrate, pool down.** Rerun the acceptance commands
   yourself, review the diffs, integrate. Only now do heavy main work.
   Checkpoint accepted changes + the next frontier before the next batch.

## Task contract (one block per task, written to the project file at step 1)

```
task: <short-id>            objective: <one sentence>
fits: <how this serves the overall plan, one line>
files: <allowed paths, explicit>   exclude: <paths the task must not touch>
interfaces: <signatures / formats the task must expose or consume>
context: <relevant excerpts or paths + base revision or file hashes>
accept: <concrete commands + expected behavior, incl. one edge case>
budget: steps ≤ N, wall ≤ M s (compute --deadline from real measurements)
result: <fixed schema: what the final answer must contain>
```

Workers get enough local context to act without reconstructing the
architecture — not the whole repo, not the main conversation. When a
task's tool history approaches its slot limit, finish + checkpoint, or
start a successor task from a bounded summary (subagents are stateless
across tasks — there is no "stateful" mode).

## Failure doctrine (one line per code)

- **2** — args/config or unknown task. Fix the invocation; for an
  unknown task id, re-list with `status --tasks`.
- **3** — target instance bfailed(port)/bdown: check
  `list-instances`; requeue on another instance or run on main.
- **4** — engine-level bfailed (OOM/hash/download): check status,
  requeue elsewhere or run on main.
- **5** — `--ctx` exceeds the instance's effective per-slot ctx: shrink
  the task's context, don't raise the flag.
- **8** — queue wait expired. The task is still queued and charged; the
  manager will start it on a free slot. Poll; re-spawn is safe (dedup
  returns the same task). (Exit 8 can also mean the global slot-claim
  lock was busy past the budget — same action: poll.)
- **20 done** — read `partial`: true → artifact is truncated at the cap;
  rerun with more budget or a smaller slice. false → accept only after
  your own acceptance run.
- **21 failed** — the `error` field names the cause (ERR_ENGINE_DOWN,
  ERR_CTX_TOO_LONG, ERR_TOOL_TIMEOUT, ERR_CANCELLED, ERR_DUPLICATE,
  ERR_QUEUE_EXPIRED, ERR_TRUNCATED_NO_PROGRESS …). Re-spawn with a fixed
  contract, or run on main.
- **22 timeout** — the deadline bound held; a `partial_answer` may be
  salvaged from the record. Split the task.
- **80 main-only** — pool not loaded (0 instances): `bonsai2-reload`, or
  run on main. **82** — main stack down: run on main is OUT; wait.
  **84** — manager down or health stale: `systemctl status
  bonsai2-sglang` (it restarts; stale health surfaces within ~3 probe
  cycles).
- **cancel** — frees the slot and kills the worker + its tool group;
  cancelling a finished task is a no-op (exit 0).

## Cost (PROVISIONAL — bench is unproven after the 2026-10-05 reviews;
re-anchor after the next live re-bench)

- Single subagent stream, reasoning off: ~26.5 t/s (live-run measured).
  Main idle: ~25–30 t/s; main under a loaded pool was observed at ~15
  t/s — the halving claim is one observation, not a law.
- Fan-out drops per-stream rate sharply (M7 history: 3–8 t/s at
  saturation). A 32,768-token output budget CANNOT fit alongside a
  nonempty prompt in one slot — size `--ctx` from the actual prompt +
  expected answer, not the slot maximum.
- The win is CONTEXT, not speed: a delegated task costs main
  plan+dispatch+compact collect+verify; a solo task costs main the whole
  implementation. Count skeleton writing and verification as main work
  when measuring.

## Recipes (the three shapes the live runs actually proved)

- **R1 — pre-seeded skeleton + specific modification** (run 3, 4/4
  first-round): main writes the file skeleton (62–93 % of final size)
  while the pool is unloaded; the worker gets "here is the file at
  <path>; make ONLY these modifications: …; run <accept>; report exit
  codes." Tools on. This is the default shape.
- **R2 — test batch with structured report**: tools on; the worker runs
  the suite, collects results, returns a fixed-schema report (pass/fail
  counts + failing cases). Main verifies the report by rerunning the
  failing cases itself.
- **R3 — single-file code-gen**: tools on, `--ctx` sized to prompt +
  expected file; the worker writes the file in its workdir and runs the
  accept command; main pulls the file and re-runs accept.

## Rules that used to be argued about, now settled

- No token-heavy main generation while the pool is loaded. (If you must:
  it's allowed for <500 tokens of index/CSS; anything more waits for
  release.)
- A spawn without a computed `--deadline` is a guess — compute it from
  measured rates, default 1800 s, and say why.
- One task = one writer. Two tasks never touch the same file.
- Mechanical work (run a test suite, hash files, merge JSON reports)
  does NOT need a model: if the whole task is mechanical, run it on main
  or in bash. Delegate interpretation and implementation.
