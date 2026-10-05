# Fixes — third practical live run remediation (I1–I4)

Source of the issues: `evidence-ledger-third-practical-run.md` §8
(2026-10-05). Scope of this file: what to change, where, and how —
nothing else. Companion to `fix-proposals-second-practical-run.md`
(same repo, same pattern). One PR; no locked SPEC value moves.

The four issues, in one line each:
- **I1** — the progress probe (`bonsai2-status --tasks`) drops
  terminal tasks from its output, so a batch-completion poller built
  on it can never observe completion. CODE SURFACE FIX.
- **I4** — the main-side batch poller pattern was improvised and
  failed on I1. PATTERN FIX (skill + the I1 flag makes it robust).
- **I2** — the pool sat loaded-and-idle ~6 min after the last task
  finished (main had no completion signal; F7 idle check missed).
  PROCEDURE FIX + optional v2 feature.
- **I3** — main ran collect-parse/bookkeeping while the pool was
  still loaded and idle. PROCEDURE FIX (same turn as I2).

---

## FIX 1 (I1) — make terminal tasks visible to the probe

The root cause is one filter. `progress_report()` skips any record
whose state is terminal:

  file: `bin/bonsai2_harness.py`
  function: `progress_report(d, task_ids)` — def at line 1375
  the filter — lines 1402-1403:
      if rec.get("state") in TERMINAL_STATES:
          continue

  (`TERMINAL_STATES = ("done", "failed", "timeout")` — line 249.)
  The docstring says it on purpose ("For each LIVE task") — the probe
  is a mid-run steering tool. That stays; we add an explicit opt-in
  instead of changing the default (F1 discipline: default behavior
  stays byte-identical for existing consumers).

### 1.1 `progress_report()` gains an `include_terminal` parameter

  location: `bin/bonsai2_harness.py`, line 1375
  change:
      def progress_report(
          d: dict,
          task_ids: list[str] | None = None,
          include_terminal: bool = False,
      ) -> list[dict]:
  and the filter at line 1402 becomes:
      if not include_terminal and rec.get("state") in TERMINAL_STATES:
          continue
  Docstring addition (after the "For each LIVE task" paragraph):
      `include_terminal=True` also carries done/failed/timeout rows
      (third-run I1: a batch-completion poller built on the probe
      could never observe completion — terminal tasks vanished from
      the output; with this flag the probe doubles as a "batch
      finished" confirmation). For terminal rows
      `deadline_remaining_s` is the same arithmetic
      (running_ts + deadline_s - now; negative = past deadline) and
      `cmpl_id` is None when the sidecar has been cleaned.

### 1.2 `cmd_status` gains `--include-terminal`

  location: `bin/bonsai2_harness.py`, `cmd_status` — def at line 1445
  - Flag parse: add `include_terminal = False` next to
    `tasks_flag` (line 1456). In the `while` loop, accept
    `--include-terminal` as a standalone flag:
      if argv[i] == "--include-terminal":
          include_terminal = True
          i += 1
          continue
    placed BEFORE the catch-all `return emit(...)` at line 1481, so
    `status --tasks ... --include-terminal` and
    `status --include-terminal --tasks ...` both parse. It is only
    meaningful with `--tasks`; when used without it, it is a no-op
    (document that; do NOT error — status without --tasks is the
    byte-identical pool-status path and must stay so).
  - The probe call at line 1505 becomes:
        rows = progress_report(d, task_ids, include_terminal)
  - `cmd_status` docstring: add the flag to the `--tasks` paragraph
    (lines 1451-1454).

### 1.3 Wrapper docstrings

  - `bin/bonsai2-status`, module docstring lines 24-32:
        bonsai2-status --tasks [TASK_ID ...] [--include-terminal]
    Document: `--include-terminal` keeps done/failed/timeout rows in
    the probe output (third-run I1); default omits them
    (byte-identical to today). The wrapper is a thin dispatcher —
    no parser lives in it, only the doc.
  - `bin/bonsai2-spawn` docstring: no change needed (spawn is
    unaffected) — listed here so the reviewer knows it was checked.

### 1.4 SPEC amendment

  - `SPEC.md`, S3.2 command-surface paragraph for `bonsai2-status`
    (the `--tasks` probe note added by PR #38): append
    `[--include-terminal]` + one line: terminal rows carried when
    the flag is given; default omits them. Record as an S3.2 delta
    in the PR body per the spec-amendment rule (expected behavior
    vs real: the probe's LIVE-only default made batch completion
    unobservable; no locked value changed).
  - `SPEC.md`, S3.4 task-lifecycle note: one sentence — a task's
    terminal state is authoritative from `bonsai2-collect`
    (rc 20/21/22); the progress probe is the mid-run steering
    signal, not the completion source (third-run I1/I4).

### 1.5 Tests (hermetic, new module `tests/test_remediation_third_practical_run.py`)

  Cases against `progress_report` (build a fake results dir the way
  `tests/test_remediation_second_practical_run.py` does):
  1. default: a done/failed/timeout record is EXCLUDED from the
     output (regression guard for the F1 byte-identical default).
  2. `include_terminal=True`: the same three records are INCLUDED,
     with correct `state`, `steps`, `tool_calls`.
  3. `include_terminal=True` + `task_ids` filter: only the named
     ids, terminal included.
  4. terminal row with `running_ts` set: `deadline_remaining_s` is
     `running_ts + deadline_s - now` (negative after expiry) — no
     crash path.
  5. terminal row whose sidecar file is absent: `cmpl_id` is None.
  6. `cmd_status` arg parsing: `--tasks t-... --include-terminal`
     and `--include-terminal --tasks t-...` both reach the probe
     with `include_terminal=True`; `--tasks` alone stays
     byte-identical (the emit dict for a live task set is
     unchanged); `--include-terminal` without `--tasks` does not
     change the pool-status emit.
  - Run: `python3 -m unittest discover -s tests` must stay green
    (the suite grows from 791 to 791+6).

---

## FIX 2 (I4) — the batch poller pattern becomes the documented rule

No code change beyond FIX 1. The rule is written into the skill so
the next session inherits it instead of improvising a poller script.

  location: `skills/bonsai2-harness/SKILL.md`, "Workflow branches +
  session economy (S4.3)" section — append a new subsection after
  "Batch spawns" (the bullet list around "Spawn gate"):

  ### Batch completion loop (third-run adjudication, 2026-10-05)

  The completion signal for a batch is `bonsai2-collect <id>` per
  task (rc 0 = running; 20/21/22 = terminal, payload carries the
  result) — polled every 30-60 s over the WHOLE batch. Never build
  the completion detector on the progress probe: the probe reports
  running/queued only (terminal tasks drop out by default; use
  `--include-terminal` as a "batch finished" confirmation, third-run
  I1). Loop shape:
  1. tick: collect every non-terminal id; any rc 20/21/22 flips that
     id to terminal (record the tick window: "finished between
     tick N-1 and tick N" in the ledger).
  2. if an id DISAPPEARS from a probe tick between collect ticks,
     collect it that tick to get the exact terminal state
     (catch-the-miss fallback).
  3. termination = "collect returned terminal rc for ALL ids of the
     batch" — never "the probe shows N terminal rows" (cannot fire
     without --include-terminal) and never "the probe list is empty"
     (ambiguous; a collect per id disambiguates — unknown id = exit
     2).
  4. the tick that completes the LAST id of the batch runs
     `bonsai2-unload --all` in the SAME turn, before any
     collect-parse/ledger/analysis work (the F8 order, made
     mechanical — third-run I2/I3).
  5. the progress probe (`--tasks`) stays the mid-run steering
     signal only (guardrail 7: stuck-detection between ticks).

  Also: the "Idle-while-loaded check (F7)" paragraph gets one added
  sentence: it only fires when main actually sees completion — see
  the batch completion loop above for why the trigger was invisible
  in the third run (I1) and for the unload-in-the-completing-tick
  rule (I2/I3).

---

## FIX 3 (I2) — procedure now; v2 feature proposed, not built

  (a) Procedure: covered by the FIX 2 loop rule #4 (unload in the
      completing tick). Nothing else to change today.
  (b) v2 candidate (PROP-ONLY, per the post-MVP rule — record it in
      the PR body under `## Uncertainties & decisions`, do not
      implement this round):
      - `etc/harness.yaml`: new key `idle_unload_s: 0`
        (0 = off, current behavior; N = after the LAST task of a
        loaded pool goes terminal, unload the pool after N seconds
        if no new task arrives).
      - Manager: in the health-cycle loop (`bin/bonsai2-manager`,
        the cycle that already runs the S3.4 reaper pass, line ~1312
        `reap_stale`), after a reaper pass that flipped the last
        running task to terminal: record `last_terminal_ts`; if
        `now - last_terminal_ts > idle_unload_s` and slots_in_use ==
        0 and no queued task: issue the same unload the S3.2
        `unload --all` command issues (manager-mediated, the same
        control channel). Guard: `user_unloaded`/`bunloaded` states
        stay untouched (drift watch already skips them).
      - This is lifecycle hygiene, NOT the out-of-scope
        kill-for-drift instance retirement (SPEC non-goal).
      - Decider: does the fourth run still show an idle-loaded gap
        when main follows the FIX 2 loop? If yes, build (b); if no,
        leave it off.

---

## FIX 4 (I3) — procedure only

No code. Covered by FIX 2 loop rule #4: the unload precedes every
post-collect main-side action in the same turn. The skill wording
(FIX 2) already names the violated shape: "collect-parse +
ledger-while-idle-loaded" is the anti-pattern; "unload first, then
parse at full t/s" is the pattern.

---

## Out of scope for this PR (record so it is not absorbed silently)

- The siege wall-break nit (`Object.keys(walls).find(k => walls[k]
  <= 0)` deletes the first hp<=0 key, not the hit one) — it lives in
  the de-hex-er artifact, not the pool; correct today because wall
  damage is exactly 1. Not a harness fix.
- The browser-verification rAF lesson (inject the shim
  pre-document-load via CDP, not post-load) — belongs to the
  verification harness skill, not this remediation. Noted in the
  ledger §6 for that pass.
- I2(b) the idle-unload timer — proposed, not built (see FIX 3).
- Any `--reasoning`-side or cap/deadline changes — the third run
  proved the pin works with the current defaults; nothing moves.

## Verification checklist (DOD input for the PR)

- [ ] FIX 1 code lands: `progress_report(include_terminal=...)`,
      `cmd_status --include-terminal`, both docstrings updated.
- [ ] 6 new hermetic cases pass; full suite green
      (`python3 -m unittest discover -s tests`).
- [ ] `./scripts/gate.sh` PASS on the branch tip.
- [ ] SPEC S3.2 + S3.4 deltas amended; PR body carries
      `## Spec amendments` + `## Uncertainties & decisions`
      (I2(b) proposal + disposition of I1–I4).
- [ ] Skill subsection + F7 sentence land in
      `skills/bonsai2-harness/SKILL.md`.
- [ ] Ledger dispositions: I1–I4 marked taken/dropped in
      `evidence-ledger-third-practical-run.md` §8 (follow-up entry,
      same file, per the uncertainty rule).
- [ ] Deploy check after merge: `sudo ./install.sh` (the
      byte-identical re-run path) → `bonsai2-status --tasks
      --include-terminal` returns the terminal rows for the run-3
      task ids (they still sit in /run/bonsai2/results).
