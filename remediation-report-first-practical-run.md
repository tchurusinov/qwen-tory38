# Remediation report — first practical run of the bonsai2 subagent pool

Companion to `evidence-ledger-for-first-practical-run.md` (the per-step record).
Read that first; this is the diagnosis + the exact fixes + an ordered plan for
the next agent to implement. Every fix names the file + line it lands in.

---

## 0. What actually happened (one paragraph, evidence-based)

The pool infra did its job: routing, the reaper, the failure paths, unload, and
the command surface all behaved per design. What lost was **model-shape, not
harness-shape**: at this pin (Ternary-Bonsai-2, deepseek reasoning format), the
3 big code-gen tasks emitted **9–21K tokens of reasoning inside their FIRST
completion, before a single tool call**. Because the worker sends **no
`max_tokens`** and the task deadline was **smaller than the wall-clock of even
one completion**, all three lost the deadline race mid-reasoning and were reaped
with **zero tool calls and zero artifact**. Main then had to build 3 of the 4
pages itself. The ledger's "add `max_tokens: 4096`" is on the right axis but
missized and misnamed (see §1) — it would fix the small tasks and actively hurt
the big ones if applied as a blanket 4K.

The two critical problems, restated as engineering defects:

1. **Subagents should do the work (even big code-gen), not main.** The blocker
   is not "subagents are dumb" — it is (a) no output cap on a completion, (b) a
   deadline shorter than the whole tool loop's wall-clock, and (c) no partial
   artifact to salvage when the deadline fires. Fix all three and the same tasks
   land on the pool.
2. **Main should only hold the pool loaded while subagents are actually
   working.** The skill already prescribes the stateless branch (unload between
   delegations); the run didn't enforce it. Make it a hard rule, and make the
   "spawn only when the task is well-defined/bounded" gate explicit.

---

## 1. Critical issue #1 — make big code-gen land on the pool

### 1.1 Root cause, precise

Three compounding defects, all in `bin/bonsai2-taskworker.py`:

- **No `max_tokens` on the completion payload.** The tool-loop body is built at
  `bin/bonsai2-taskworker.py:1001-1010`:
  ```python
  res = _stream_chat(port, ident, {
      "model": ident["alias"],
      "messages": messages,
      "tools": T.TOOLS_JSON,
      "stream": True,
  })
  ```
  and the `--tools off` body at `:507-515` is the same shape minus `tools`.
  No `max_tokens` key anywhere → a completion runs as long as the model wants
  (observed 9–21K tokens = 1200–2600 s at 5–8 t/s under contention).

- **The deadline is a fixed number, not a function of the expected work.**
  `DEFAULT_DEADLINE_S = 600.0` (`:121`), and the run used 900 s / 1200 s.
  Round 2's data: the *tightened* completions were ~4.4K tokens ≈ **710 s each**,
  and the v2 plan needed **3 tool calls** (write → check → rm), i.e. ≥ 3
  completions ≈ **2100 s** — already past the 1200 s deadline before the last
  call. The deadline was never sized for the *whole loop*, only "a bit more
  than one completion."

- **No partial artifact on timeout.** When the deadline fires, the worker is
  *blocked inside the streaming read* (`_stream_chat`), so it only notices the
  deadline *after* the whole completion finishes. The partial reasoning/answer
  of the in-flight completion is never persisted — `reap_stale`
  (`bin/bonsai2_harness.py:1005`) marks the record `timeout` with
  `reasoning_len=0 / answer_len=0`. Main loses everything.

### 1.2 Why "set ctx to 4K" is the wrong lever (the user's question)

The ledger wrote `max_tokens (e.g. 4096)`. That is the **per-completion output
cap**, NOT `--ctx` (the context *window*). Two distinct things:

- **`--ctx N`** = the window (input + output + history + tool results). Per-slot
  cap on this box is `ctx_size / parallel = 262144 / 8 = 32,768`. Do **not**
  shrink ctx to 4K — that would lose the multi-step history and tool results
  the loop depends on.
- **`max_tokens`** = the output cap for ONE completion (reasoning + content +
  tool call). This is the cap that matters here.

The danger of a **blanket 4K** on the tool loop: `max_tokens` counts reasoning
tokens too. If the model reasons 10K tokens before its first `write_file`, a
4K cap cuts the completion **mid-reasoning** → the completion returns with a
partial `reasoning_content`, empty `content`, and **no tool call** → the loop
(`bin/bonsai2-taskworker.py:1106`, `tc = res.get("tool_call") or
_parse_text_tool_call(answer)`) sees `tc is None` → treats it as a **final
answer** → marks the task `done` (exit 20) with a **truncated/garbage answer**.
So a 4K default is *worse* than no cap for big tasks: it converts a visible
timeout into a silent false-success.

**Conclusion:** `max_tokens` must be (a) **finite** (so the deadline math is
sound — the real win over "no cap"), (b) **generous enough to reach the tool
call** for a big single-file write, and (c) **a per-task knob**, not a blanket.
Recommended default **16384**, overridable per task with a new `--max-tokens N`.
Big multi-file work is fixed by **splitting into per-file tasks** (each file is
one bounded write — the single-writer-per-workdir doctrine), not by raising one
task's ctx.

### 1.3 The fixes (in priority order)

**F1 — thread a finite `max_tokens` through every completion.**

1. `etc/harness.yaml` — add a default next to `task_default_deadline_s: 600`
   (line 22):
   ```yaml
   task_default_deadline_s: 600
   task_default_max_tokens: 16384   # F1: per-completion output cap (reasoning + content + tool call)
   ```
2. `bin/bonsai2_harness.py` `_parse_spawn_args` (`:1343`) — add a
   `--max-tokens N` flag parsed like `--max-steps` (see the `--ctx` parse at
   `:1407-1411`). Store in `args["max_tokens"]`.
3. `bin/bonsai2_harness.py` record creation (`:1808-1828`) — add the field:
   ```python
   rec = {
       ...
       "max_steps": max_steps,
       "max_tokens": (          # F1
           parsed["max_tokens"]
           if parsed["max_tokens"] is not None
           else int(cfg["task_default_max_tokens"])
       ),
       ...
   }
   ```
4. `bin/bonsai2-taskworker.py` `_run_tool_loop` (`:1001-1010`) — add the cap to
   the tool-loop body:
   ```python
   mt = rec.get("max_tokens")
   body = {
       "model": ident["alias"],
       "messages": messages,
       "tools": T.TOOLS_JSON,
       "stream": True,
   }
   if isinstance(mt, int) and mt > 0:
       body["max_tokens"] = mt
   res = _stream_chat(port, ident, body)
   ```
5. `bin/bonsai2-taskworker.py` `_run_attempt` (`:507-515`) — same, for the
   `--tools off` path:
   ```python
   body = {"model": ident["alias"],
           "messages": [{"role": "user", "content": rec.get("prompt", "")}],
           "stream": True}
   mt = rec.get("max_tokens")
   if isinstance(mt, int) and mt > 0:
       body["max_tokens"] = mt
   res = _stream_chat(port, ident, body)
   ```
   Gate on `isinstance(mt, int)` so old records without the field stay
   byte-identical to pre-F1 (the S3.1 constraint the tests rely on).

**F2 — guard against the "truncated = false-success" trap.**

Capture `finish_reason` in `_stream_chat` (`:291`) and surface it to the loop:
- Add `finish_reason: str | None = None` alongside `usage`; in the chunk loop,
  when a chunk has `choices[0].finish_reason`, record it.
- Return it in the `{"ok": True, ...}` dict.
- In `_run_tool_loop`, at the `tc is None` final-answer branch
  (`:1107-1109`), do **not** accept a "final answer" when the step's
  `finish_reason == "length"` (i.e. it was cut by `max_tokens`) AND the step
  carried a tool call expectation. Concretely:
  ```python
  if tc is None:
      # F2: a completion cut by max_tokens (finish_reason == 'length') that
      # emitted no tool call is a TRUNCATION, not a final answer. Feed a
      # nudge back instead of marking done (prevents the silent
      # false-success of a 4K cap cutting a big write mid-reasoning).
      if res.get("finish_reason") == "length" and not res.get("answer"):
          messages.append({
              "role": "user",
              "content": "Your last message was cut off before a tool call or "
                         "final answer. Continue: either emit the tool call now "
                         "or give the final answer.",
          })
          steps += 1
          continue
      # ... existing final-answer write ...
  ```
  This is what makes a *smaller* `--max-tokens` safe for big tasks: instead of
  a silent garbage answer, the loop keeps going.

**F3 — size the deadline to the whole loop, not one completion.**

The deadline should cover `expected_steps × per_completion_time`. Two
implementations, pick one (cheapest first):

- **F3a (do this): raise the tool-loop default deadline.** In
  `bin/bonsai2-taskworker.py:121`, the `DEFAULT_DEADLINE_S = 600.0` is only a
  fallback (records always carry `deadline_s`). The real knob is the *default
  the spawn applies*. In `bin/bonsai2_harness.py` record creation (`:1729-1733`),
  when `--deadline` is not given, use a tool-loop-aware default:
  ```python
  deadline_s = (
      parsed["deadline"]
      if parsed["deadline"] is not None
      else (float(cfg["task_default_deadline_s_tools_on"])
            if parsed["tools"] == "on"
            else float(cfg["task_default_deadline_s"]))
  )
  ```
  and add to `etc/harness.yaml`:
  ```yaml
  task_default_deadline_s: 600
  task_default_deadline_s_tools_on: 1800   # F3a: ~3 completions x ~600 s at contention t/s
  ```
  This alone turns round-2's ~2100 s 3-call plan from a loss into a near-miss,
  and combined with F1 (finite caps → completions stop at 16K, not 21K) into a
  clean win.

- **F3b (optional, if F3a + F1 still miss on the biggest tasks): estimate the
  deadline from `max_tokens` + a contention t/s floor.** Add to
  `etc/harness.yaml` a `contention_tps_floor: 5.0` and compute
  `deadline_s >= ceil(max_tokens / contention_tps_floor) × expected_steps × 1.5`
  when the caller didn't pass `--deadline`. This makes the deadline track the
  cap automatically. Mark it `UNRESOLVED: deadline-estimate` if implemented,
  since the t/s floor is a planning number, not a measured constant.

**F4 — salvage a partial artifact on timeout (the "no partial to salvage" fix).**

This is the least critical of the three but directly serves "main looks around,
tests, verifies with the partial." Implement in the worker:

- Add a soft-deadline watchdog to `_stream_chat`: pass `deadline_at` in, and in
  the chunk-read loop, if `time.monotonic() >= deadline_at` AND we have
  accumulated `content_parts`/`reasoning_parts`, **persist a partial** to the
  result file (the worker already knows `rec["result_file"]` and can reuse
  `_append_result_file`-style atomic write) tagged
  `{"partial": true, "answer": ..., "reasoning": ...}`, then return with a
  `partial=True` flag.
- In `reap_stale` (`bin/bonsai2_harness.py:1005`), when reaping a deadline
  timeout, also read any `partial` field from the result file and carry it onto
  the record as `rec["partial_reasoning"]` / `rec["partial_answer"]` so `collect`
  surfaces it.

This is a bounded change; the reaper already touches every reaped record.

**F5 — prompt discipline for the big shapes (skill-side, not code).**

In `skills/bonsai2-harness/SKILL.md`, R1 (single-file code-gen) and the T1
template: the round-2 data showed the tightened prompt cut 10K → 4.4K
reasoning. Keep that discipline and add the **first-reply-is-a-tool-call**
directive + **name every file up front**. This reduces reasoning size so even
the default deadline is safe. It is a mitigation, not a fix — F1–F3 are the
structural fixes; F5 just makes the default cap more likely to reach the tool
call.

### 1.4 What "the subagent does the big task" now means (operable rule)

After F1–F3 land:
- Small/medium single-file code-gen: default `--max-tokens 16384`, default
  tool-loop deadline 1800 s → lands in one round, like T1 did.
- Big single file: raise `--max-tokens` to 32768 (one slot's ctx headroom) and
  `--deadline` to ~2400 s; the loop's F2 guard keeps it from false-succeeding if
  the cap is hit.
- Multi-file "gigantic" task: **split into one task per file** (single-writer,
  one bounded write each), batch-spawn, one collect loop. This is the doctrine's
  existing "single-file code-gen chunk" row — a big job is N small chunks, not
  one giant prompt.
- If a task still loses **two consecutive rounds** at the right cap + deadline:
  it is a main task (the S4.2 self-correcting rule) — move it, don't keep
  re-spawning.

---

## 2. Critical issue #2 — load only while working, unload between rounds

### 2.1 What the skill already says (and why the run missed it)

`skills/bonsai2-harness/SKILL.md` lines 213–220, the **Stateless (DEFAULT)**
branch:
> understand → load (reload if needed) → delegate a bounded, clearly-defined
> task → `collect` → **`bonsai2-unload --all`** → verify at full main t/s →
> re-load only if more work is needed.

That is exactly the model the user wants. The run deviated: the pool stayed
loaded (main at halved t/s) while main did the browser verification **and**
wrote 3 pages. The doctrine is a *suggestion*; nothing enforced the unload.

### 2.2 The fixes

**F6 — make "unload between batches" a hard rule (skill, mandatory).**

Edit `skills/bonsai2-harness/SKILL.md` "Two workflow branches" → rename the
stateless description to a **required** sequence and add an explicit hard rule
immediately after the branch definitions:

> **Hard rule (stateless batch):** the pool is loaded ONLY for the span
> `reload → batch-spawn → collect-all`. The moment `collect` returns 20/21/22
> for **every** task in the batch, main MUST `bonsai2-unload --all` BEFORE any
> token-heavy main-side work (writing files, generating, driving the browser,
> long-context reads). Reload only at the top of the *next* batch. Never hold
> the pool loaded while main is generating — that is the halved-t/s tax, and it
> is the single most expensive mistake on this box.

And add a one-line "spawn gate" rule under "Slot economy" (lines 231–250):

> **Spawn gate (well-defined/bounded):** spawn a subagent task ONLY when each
> task fits ONE bounded completion — the files are named, the spec is compact,
> and a single `write_file` (+ one check) fits the deadline + `max_tokens`. If a
> task would need >3 completions or >`max_tokens` of reasoning to finish its
> first write, split it into per-file tasks or keep it on main. A task main
> cannot bound to a deadline is not delegable yet.

This directly encodes "main spawns and loads subagents only when their tasks are
clear and well-defined."

**F7 — give main a cheap way to see "the pool is loaded but idle" so it knows
to unload.**

`bonsai2-status` already reports `slots_in_use`/`slots_total` per instance.
The skill should tell main to check it at the end of a batch:

> After `collect`-all: run `bonsai2-status`. If `slots_in_use == 0` on every
> running instance but the stack is not `main-only`, the pool is loaded-and-idle
> → `bonsai2-unload --all` now (you are paying the t/s tax for nothing).

This is a doctrine addition (one paragraph), no code. It makes the "idle while
loaded" state visible and actionable from the meters main already reads.

**F8 — verify the main-t/s-halving assumption is being applied (skill).**

The run never once checked main's t/s while the pool was loaded. Add to "Main
t/s halving under a loaded pool" (lines 252–262): after every `collect`-all,
main should treat its own next turn as *verification-only* (short, ledger-
first) because it is still halved, and the *unload* is what restores full t/s —
so the ordering is **collect → unload → verify**, never **verify-while-loaded**.
This is the "main looks around, tests, verifies with full bandwidth" guarantee
made concrete.

---

## 3. Secondary infra fixes (lower priority, same run)

These are "NEEDS WORK" items 2–6 from the ledger, mapped to code. Do them in the
same PR set as §1–§2 but they are not what blocked the run.

**F9 — manager boot self-deadlock (the §1b defect).** Two coordinated changes:

1. `bin/bonsai2-manager` `systemctl_start_instance` (`:941-949`) — make the start
   non-blocking so the manager's start-job doesn't sit "waiting" on its own
   instances' `After=` while it is still pre-`READY=1`:
   ```python
   def systemctl_start_instance(i: int, base: list[str] | None = None) -> int:
       """`systemctl start --no-block bonsai2-server@i` (S2.5). The manager is
       Type=notify and only sends READY=1 AFTER spawn_sequence returns, so a
       blocking `start` deadlocks against the instance units'
       After=bonsai2-sglang.service. --no-block returns immediately; the
       existing wait_instance_healthy port-poll is the real gate."""
       cmd = (base or systemctl_base()) + ["start", "--no-block", f"bonsai2-server@{i}"]
       r = subprocess.run(cmd, capture_output=True, text=True, check=False)
       ...
   ```
   The `wait_healthy(i, cfg, boot_deadline_s)` at `:2278` already gates each
   start, so `--no-block` is safe. Keep the `After=` ordering (more correct
   than removing it, per the ledger's root-cause note).
2. `units/bonsai2-sglang.service` (`:23-27`) — add `TimeoutStartSec=300` so the
   manager's own start-job survives the ~90 s × N model loads instead of hitting
   the 90 s `DefaultTimeoutStartUSec` and restarting (the observed `NRestarts=5`).

**F10 — install hygiene (the §1c defects).**

1. **Exec bits.** `bin/bonsai2_install.py` `_deploy_bin_tree` (`:1348-1373`)
   copies via `shutil.copy2` (preserves source mode). The 5 command wrappers
   (`bonsai2-status/-collect/-steer/-cancel/-unload`/`-reload`/`-health`) are
   not `+x` in the repo, so they land not-`+x`. Add, after the `shutil.copy2`
   loop in `_deploy_bin_tree`:
   ```python
   COMMAND_EXEC = {
       "bonsai2-manager", "bonsai2-status", "bonsai2-list-instances",
       "bonsai2-health", "bonsai2-spawn", "bonsai2-collect",
       "bonsai2-steer", "bonsai2-cancel", "bonsai2-unload", "bonsai2-reload",
   }
   for name in COMMAND_EXEC:
       p = bin_dst / name
       if p.is_file():
           p.chmod(0o755)
   ```
   (Also `chmod +x` the repo copies so pre-install `bin/` runs work.)
2. **Shebang / interpreter.** The commands' `#!/usr/bin/env python3` resolves to
   the Hermes bundled python (no PyYAML) on this box → raw traceback, no
   closed-set exit code. Two options; prefer (a):
   - (a) In `_deploy_bin_tree`, **rewrite the deployed commands' first line**
     to the box python (`DEFAULT_PYTHON`, `:80`, `/usr/bin/python3`):
     ```python
     import re
     for name in COMMAND_EXEC:
         p = bin_dst / name
         if not p.is_file():
             continue
         txt = p.read_text()
         txt = re.sub(r"^#!.*python3.*$", f"#!{python}", txt, count=1)
         p.write_text(txt); p.chmod(0o755)
     ```
     (The `python` arg is the installer's `BONSAI2_PYTHON` / `DEFAULT_PYTHON`,
     which install.sh already pins to `/usr/bin/python3`.)
   - (b) Alternatively add a closed-set `ERR_CONFIG` mapping in the worker's
     uncaught-import path so the failure at least exits 2 with a clean message
     instead of a bare rc=1 traceback. (Do this *in addition* — a PyYAML-less
     interpreter should never escape as rc=1.)
3. **`/run/bonsai2` ownership.** `bin/bonsai2_install.py` `_make_dirs`
   (`:1376-1379`) creates `results/` etc. as root (the installer runs as root);
   the user-scope commands then `mkdir` task dirs under `results/` as `blckwll`
   → `EACCES`. Add, in `_make_dirs`, a chown of the results subtree to the
   invoking user when running system-scope:
   ```python
   def _make_dirs(health_dir: Path, invoke_uid: int | None = None) -> None:
       for sub in ("results", "dedup", "bench", "workspaces", "control"):
           (health_dir / sub).mkdir(parents=True, exist_ok=True)
       if invoke_uid is not None:
           results = health_dir / "results"
           # chown -R results to the invoking user so user-scope commands
           # can mkdir task dirs without EACCES (the ledger §1c defect).
           ...  # os.chown walk, or a single chown on results/ + results/tasks/
   ```
   Thread `invoke_uid` (the `geteuid()` of the caller, or `SUDO_UID`) into
   `_make_dirs` from `install()`. This is the cleanest long-term fix over the
   live `sudo chown -R` the run used.

**F11 — orphan completions (the §3 secondary finding).** When the reaper frees a
timed-out slot, the engine's in-flight completion on that slot keeps decoding
(observed 21.6K tokens on a dead task). The engine exposes
`POST /slots/:id_slot` (`third_party/llama.cpp/tools/server/server.cpp:274`) to
stop a slot's computation. Fix approach (deferred — logged, not a blocker):

- In `bin/bonsai2_harness.py` `reap_stale` (`:1005`) or the manager's
  `reap_tasks_once` (`bin/bonsai2-manager:1294`), on a deadline reaping, issue
  `POST http://127.0.0.1:{port}/slots/{slot_id}` for the task's slot to abort
  the in-flight generation.
- The non-trivial part is the **harness task-id → engine slot-id mapping**
  (engine task ids ≠ harness task ids, per the ledger). The engine's
  `GET /slots` lists slots with their `task_id`/state; a `GET /slots` match on
  the task's `port` + the recorded start time can recover the slot id. Mark this
  `UNRESOLVED: engine-slot-cancel` and implement behind the reaper's fault-
  tolerance (a failed slot-cancel must never kill the reaper pass).
- Impact: under light load an orphan is harmless (it finishes and frees the slot
  for real); under load it steals decode bandwidth. Low priority relative to
  §1–§2.

**F12 — `collect` stdout purity (minor).** The contract (`emit`,
`bin/bonsai2_harness.py:267`) is already correct: JSON on stdout, the human
"poll again" line on stderr. The ledger's "Extra data: line 2" was from
capturing `2>&1`. No code change needed; document in the skill's command
reference: "machine consumers read stdout only; do not merge stderr." One-line
doc, not a bug.

---

## 4. Exact step-by-step plan for the next agent

Implement in this order. Each step is one reviewable unit; run
`./scripts/gate.sh` after each and add a focused hermetic test where noted.

### Step 1 — F1 + F2 + F3a (the core: caps + deadline + no-false-success)
1. Edit `etc/harness.yaml`: add `task_default_max_tokens: 16384` and
   `task_default_deadline_s_tools_on: 1800`.
2. `bin/bonsai2_harness.py`:
   - `_parse_spawn_args` (`:1343`): add `--max-tokens N` parse (mirror the
     `--max-steps` handling; range check 1–32768).
   - Record creation (`:1808-1828`): add `rec["max_tokens"]`.
   - Deadline selection (`:1729-1733`): tool-loop-aware default (F3a).
   - Add `task_default_max_tokens` + `task_default_deadline_s_tools_on` to the
     config-load validation + `CONFIG_DEFAULTS` so a missing key is a clean
     default, not a KeyError.
3. `bin/bonsai2-taskworker.py`:
   - `_stream_chat` (`:291`): capture `finish_reason` (add to the return dict).
   - `_run_tool_loop` (`:1001-1010`): add `max_tokens` to the body; add the F2
     truncation guard at the `tc is None` branch (`:1107`).
   - `_run_attempt` (`:507-515`): add `max_tokens` to the `--tools off` body.
4. **Test:** a hermetic test that (a) a `--tools on` spawn record carries
   `max_tokens` (default + `--max-tokens` override), (b) the tool-loop body sent
   to `_stream_chat` includes `max_tokens`, (c) a completion with
   `finish_reason="length"` + no tool call + empty answer does NOT mark done
   (F2) but feeds the nudge back. Reuse the existing mock-engine fixtures
   (`tests/fixtures/mock_engine.py`).
5. `./scripts/gate.sh` + the hermetic suite.

### Step 2 — F4 (partial-artifact salvage)
1. `bin/bonsai2-taskworker.py`: add a `deadline_at` param to `_stream_chat`; in
   the chunk loop, on soft-deadline with accumulated parts, persist a
   `{"partial": true, ...}` result file + return `partial=True`.
2. `bin/bonsai2_harness.py` `reap_stale` (`:1005`): on a deadline reaping, read
   the result file's `partial` field and carry it onto the record.
3. **Test:** a task that times out mid-completion has
   `rec["partial_reasoning"]`/`partial_answer` populated and `collect` surfaces
   them.

### Step 3 — F6/F7/F8 (the skill doctrine: load-only-while-working)
1. `skills/bonsai2-harness/SKILL.md`:
   - Make the stateless branch a **required** sequence + add the "Hard rule
     (stateless batch)" paragraph (F6).
   - Add the "Spawn gate (well-defined/bounded)" rule under Slot economy (F6).
   - Add the "idle-while-loaded → status → unload" meter rule (F7).
   - Add the "collect → unload → verify" ordering to the t/s-halving section
     (F8).
   - In R1 + the T1 template: add the first-reply-tool-call + name-files-up-
     front + `--max-tokens` note (F5).
2. No code gate (docs), but run the skill's self-consistency check if one
   exists (the S4.1 citation rule).

### Step 4 — F9 (boot deadlock)
1. `bin/bonsai2-manager` `systemctl_start_instance` (`:941`): add `--no-block`.
2. `units/bonsai2-sglang.service`: add `TimeoutStartSec=300`.
3. **Test:** the @gpu boot test (`tests/gpu/test_lifecycle_2_11.py`) boots clean
   first try (manager start-job does not sit "waiting"). If no @gpu time, a
   hermetic test that `systemctl_start_instance` emits `--no-block` in its argv.

### Step 5 — F10 (install hygiene)
1. `bin/bonsai2_install.py` `_deploy_bin_tree` (`:1348`): add `COMMAND_EXEC`
   chmod + the shebang rewrite (F10.1 + F10.2a).
2. `bin/bonsai2_install.py` `_make_dirs` (`:1376`): thread `invoke_uid`, chown
   the results subtree (F10.3).
3. Also fix the **`CPUQuota=64%%`** render bug: `bin/bonsai2_install.py:693`
   (`_UNIT_SERVICE_TMPL`) renders the literal `CPUQuota=64%%` into the unit
   (systemd logged "Invalid CPU quota '64%%'"). Change to `CPUQuota=64%` (the
   template is a plain string, not `.format`/`%`-formatted at that site —
   verify by grepping how `%EXECSTART%` is the only substitution, then a single
   `%` is correct). Also keep `units/bonsai2-server@.service:47` consistent
   (already `64%` there — the install template is the one that's wrong).
4. **Test:** `install.sh --dry-run` → the rendered instance unit has
   `CPUQuota=64%` (single), and a real install lands 10/10 command wrappers
   `+x` with a `/usr/bin/python3` shebang.

### Step 6 — F11 (orphan cancel) + F12 (collect doc)
1. F11: reaper slot-cancel via `POST /slots/:id` + `GET /slots` id-recovery,
   behind the reaper's fault tolerance; mark `UNRESOLVED: engine-slot-cancel`
   until @gpu-verified.
2. F12: one-line skill doc ("machine consumers read stdout only").

### Step 7 — Spec amendments + MAP + re-bench
- Amend `SPEC.md` S3.6 (new `task_default_max_tokens` +
  `task_default_deadline_s_tools_on` config keys), S3.4 (F2 truncation guard
  semantics; F4 partial-artifact on the record), S2.5 (`--no-block` spawn;
  `TimeoutStartSec`), S1.4/S2.2 (the `CPUQuota` render). Record the before/after
  in each PR body under `## Spec amendments`.
- Log the run + the fixes in `MAP.md` session log.
- **Re-bench the SAME four tasks** that failed (flappy / mandelbrot / life +
  dashboard) against the patched pool (the ledger's "what would make the next
  run faster" list). Acceptance: **all 4 land on the pool (exit 20)** in a
  single round, no main-side re-do, and the pool was unloaded between the
  fan-out and main's verification.

---

## 5. Acceptance criteria for "the run is fixed"

1. **The 3 failed tasks land on the pool** (exit 20, honest `tool_log`,
   artifact verified by main against the self-report) — no main-side re-do.
2. **No silent false-success:** a task whose first completion is cut by
   `max_tokens` does not return `done` with a truncated answer (F2 guard).
3. **A timed-out task carries a partial artifact** (F4) so main can salvage /
   steer instead of losing everything.
4. **The pool is unloaded between the fan-out and main's verification** — main
   does its browser-verify + file checks at full t/s (F6/F7/F8), and
   `bonsai2-status` at batch end shows `main-only` (or 0 slots in use) before
   main does token-heavy work.
5. **Install is clean first-try:** 10/10 command wrappers `+x` with a
   `/usr/bin/python3` shebang, `CPUQuota=64%` (single) in the rendered unit,
   manager boots without the start-job deadlock (F9/F10).
6. **Orphans no longer steal bandwidth under load** (F11) — verified by
   `docker logs` showing the reaped slot's completion stopped shortly after the
   reaping.

---

## 6. What is deliberately NOT in this plan

- No `if N == 1` hacks (the MVP safety rule) — all changes are full-capability.
- No change to the main stack (`:30001`) — it is external infrastructure.
- No ctx shrink to 4K — that's the misnamed lever; the real lever is
  `max_tokens` + the deadline + the split-into-per-file-tasks rule.
- No new engine pin / fork bump — the fixes are all harness-side (worker,
  install, skill, unit template) except the deferred F11 slot-cancel, which
  reuses the pin's existing `POST /slots/:id` endpoint (no pin move).
- No stateful-branch changes — the stateless branch is the one being enforced
  harder, not reworked.
