#!/usr/bin/env python3
"""bonsai2-taskworker — the detached child that RUNS one harness task
(MAP 3.3; the surface the S3.2 `spawn` JSON implies).

Not a S3.2 command (the S3.2 command surface is locked and does not
include a worker) — `bonsai2-spawn` starts it detached for every record
it marks `running`. The task record (<results>/tasks/<task_id>.json) is
the source of truth: the worker ALWAYS exits 0 (a crashed worker leaves
its record `running`/`engine_retried` — the Phase-3 reaper (MAP 3.4)
turns a stale running record into failed/ERR_ENGINE_DOWN); the terminal
state + error name live on the record, and `collect` (MAP 3.6) maps them
to the S3.2 codes (20/21/22).

Wire behavior (the S1.5 + S3.7 rows the spawn JSON implies):
  * one streaming chat completion per attempt (the pinned engine's
    --reasoning-format deepseek splits content/reasoning, S1.3; usage is
    the stream-final chunk's `usage` when present, else an estimate —
    the result file's `usage` is data, not a verdict);
  * HTTP 400 -> the record is failed `ERR_CTX_TOO_LONG` (S1.5: request >
    ctx-size, the exit-5 condition's task-side state);
  * non-JSON / unparseable completion body -> failed `ERR_PARSE` (S3.7);
  * connection error / 429 / 5xx -> `engine_retried` ONCE on another
    instance (lowest-i healthy with ctx fit among the UNTRIED; when
    every healthy instance has already been tried the task fails — the
    pre-fix least-loaded ANY-instance fallback could re-pick a tried
    one, violating S1.5), second failure -> failed `ERR_ENGINE_DOWN`;
  * the task's own --deadline (S3.4) expiring mid-run -> `timeout`
    (the 22-path state; the manager-side enforcement half is MAP 3.4).

Capability layer (S3.4 tool loop; the 2026-09-25 spec amendment, MAP
3.14): when `rec["tools"] == "on"` the single-attempt body becomes the
bounded step-loop:
  * send the current message history (prompt + prior tool results) with
    the four-tool OpenAI `tools` array to the routed instance;
  * if the response carries a tool call — native `tool_calls` array OR
    the text-JSON convention (a fenced/bare JSON object in the content
    naming one of the four tools; the @gpu probe test decides which the
    pin's chat template honors, tests/gpu/test_tools_probe.py) —
    validate it against the four-tool schema (`bin/bonsai2_tools.py`);
    a bad call is fed back as an error result, up to `tool_invalid_max`
    (S3.6, default 3) consecutive bad calls, then the task fails
    `ERR_TOOL_INVALID_CALL` (S1.5, collect exit 21);
  * execute it: host-side via `bin/bonsai2_tools.py` (the default,
    `tool_execution: host`) or engine-side via the instance's `/tools`
    endpoint (`tool_execution: engine`, the fork's built-in toolset at
    the pin — S1.3; the 4 host-side names map onto the 7-tool engine
    set, S1.3 note); the result is appended to the history and the loop
    continues;
  * if the response is a final answer, the task is `done`;
  * loop bounds: the task's `--deadline` bounds the WHOLE loop; a tool
    step that overruns its per-tool `tool_timeout_s` fails
    `ERR_TOOL_TIMEOUT` (collect 21, S1.5); hitting `--max-steps`
    without a final answer -> `timeout` / `ERR_DEADLINE` (collect 22);
  * steer: `steer_messages` on a `--tools on` task are drained at the
    loop's next step boundary as the next user message (S4.3);
  * cancel: the SIGTERM handler SIGTERMs the in-flight tool
    subprocess's process group (via bonsai2_tools.kill_current), then
    marks the record failed ERR_CANCELLED as before (the 5 s SIGKILL
    backstop is the cancel command's, S3.2);
  * `--tools off` stays a single completion, byte-identical to the
    pre-loop path (the amendment's hard constraint; the Phases 0-3
    hermetic suite depends on it).

The result file gains the S3.1 capability fields `tool_log` (one entry
per tool call: {step, tool, params (redacted), exit_code, output
(truncated to tool_output_max_kb), duration_ms}), `steps` (total
completions issued — 1 for a text-only task) and `workdir`; a failed
task's result file still carries tool_log up to the failure.

The pool's wire identity (alias + api_key + port base) is re-read from
subagent-engine.yaml at worker start (load_engine_identity) — a config
re-render between spawn and worker start is picked up; a missing/invalid
engine yaml is a worker-start failure: the record goes failed
ERR_ENGINE_DOWN with the detail (the record is still collectable).
"""

from __future__ import annotations

import base64
import json
import re
import signal
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bonsai2_harness as H
import bonsai2_tools as T

REQUEST_TIMEOUT_S = 60.0  # per-SSE-chunk socket cap (idle engine = stuck)
POLL_S = 0.1

# S1.5 / S3.4 (the amendment): tool-step error names (task-level, not new
# exit codes — collect maps failed->21 / timeout->22 as before).
ERR_TOOL_INVALID_CALL = "ERR_TOOL_INVALID_CALL"
ERR_TOOL_TIMEOUT = "ERR_TOOL_TIMEOUT"

# Engine-side tool name mapping (S1.3 note, A8 pin-reality): the fork's
# built-in 7-tool set at pin 9a9394a8 — the 4 host-side names mapped onto
# it. bash ≈ exec_shell_command; read_file/write_file 1:1; python has no
# engine-side tool (driven through exec_shell_command + the config's
# python bin).
_ENGINE_TOOL_NAMES = {
    "bash": "exec_shell_command",
    "read_file": "read_file",
    "write_file": "write_file",
    "python": "exec_shell_command",
}
ENGINE_TOOLS_TIMEOUT_S = 20.0  # the /tools POST's socket cap (the per-tool
# process-group timeout is the HOST-side half; engine-side steps are
# bounded by this + the task --deadline, S3.4)

# Defensive cap for the step-loop deadline when the record somehow lacks
# deadline_s (the harness always sets it at spawn from
# task_default_deadline_s / --deadline, S3.4). Match the harness default
# (task_default_deadline_s: 600) rather than an ad-hoc 3600 so a missing
# deadline cannot silently widen the window 6x.
DEFAULT_DEADLINE_S = 600.0

# The text-JSON tool-call convention (the A2 probe fallback): a fenced
# ```json ...``` block, or a bare object at the start of the content,
# whose "tool" (or "name") field names one of the four tools.
# G3 PR#18 r5 P2 (noted): non-greedy `\{.*?\}` stops at the FIRST `}` it
# can close the object at — fine for the flat tool-call shape the spec
# expects ({tool, path/content/cmd/code}), but a nested object
# ({"a":{"b":1}}) would truncate at the inner `}` and fail json.loads,
# falling through to the next step (not a hard error). If the pin ever
# emits a nested params object in the text-JSON convention, switch to a
# JSON-aware scanner (e.g. json.JSONDecoder().raw_decode at each `{`).
_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)
_TOOL_CALL_KEYS = ("tool", "name")


def _log(msg: str) -> None:
    print(f"[bonsai2-taskworker] {msg}", file=sys.stderr, flush=True)


class _Cancelled(Exception):
    """The SIGTERM sentinel: the main loop turns it into a clean exit 0
    (the record is already terminal — the always-exits-0 contract)."""


# module-level handler state (signal handlers cannot rely on locals from
# the interrupted frame; the handler is installed with the record path
# set before the run starts)
_CANCEL_REC_PATH: Path | None = None
_CANCELLED_SEEN = False


def _on_cancel_signal(signum, frame):
    """bonsai2-cancel's SIGTERM (S3.2: 'cancel SIGTERM -> SIGKILL after
    5 s'). S3.4 (the amendment): for a mid-step tool task, FIRST kill the
    in-flight tool subprocess's process group (so the step's children die
    with the worker, not after it), THEN mark the record terminal NOW
    (the cancel command's belt-and-braces also writes it — the lock makes
    the second write a no-op) so the slot frees at signal time, not after
    the process dies. A second signal re-raises the default: the worker
    exits, and whatever state the record is in is what the
    reaper/cancel sees."""
    global _CANCELLED_SEEN
    if _CANCELLED_SEEN:
        signal.default_int_handler(signum)(signum, frame)
        return
    try:
        T.kill_current()
    except Exception:  # noqa: BLE001, S110 — a handler must never crash the run
        pass
    _CANCELLED_SEEN = True
    rec_path = _CANCEL_REC_PATH
    if isinstance(rec_path, Path) and rec_path.is_file():
        try:
            rec = json.loads(rec_path.read_text())
            if isinstance(rec, dict) and rec.get("task_id"):
                d = {
                    "base": rec_path.parent.parent,
                    "tasks": rec_path.parent,
                    "locks": rec_path.parent.parent / ".locks",
                }
                if rec.get("state") not in H.TERMINAL_STATES:
                    rec["state"] = "failed"
                    rec["error"] = "ERR_CANCELLED"
                    rec["detail"] = (
                        "cancelled: SIGTERM from bonsai2-cancel — slot freed (S3.4)"
                    )
                    rec["updated_ts"] = time.time()
                    with H.lock_wait(d["base"], f"task-{rec['task_id']}"):
                        fresh = H.read_task(d, rec["task_id"])
                        if fresh.get("state") not in H.TERMINAL_STATES:
                            fresh["state"] = rec["state"]
                            fresh["error"] = rec["error"]
                            fresh["detail"] = rec["detail"]
                            fresh["updated_ts"] = time.time()
                            H.write_task(d, fresh)
                    _log(
                        f"{rec['task_id']}: SIGTERM -> failed ERR_CANCELLED (slot freed)"
                    )
        except Exception as exc:  # noqa: BLE001 — a handler must never crash the run
            _log(f"cancel handler record write failed: {exc!r}")
    raise _Cancelled()


def _set_state(
    rec: dict,
    state: str,
    error: str | None = None,
    detail: str | None = None,
    d: dict | None = None,
) -> None:
    rec["state"] = state
    rec["error"] = error
    rec["detail"] = detail if detail is not None else rec.get("detail")
    rec["updated_ts"] = time.time()
    if d is not None:
        H.write_task(d, rec)


def _pick_retry_instance(
    doc: dict, rec: dict, d: dict
) -> tuple[int | None, int | None]:
    """S1.5 one-retry target: the lowest-i healthy instance with ctx fit
    among the UNTRIED instances. (i, port) or (None, None) when no
    untried instance is healthy with ctx fit — the caller then fails the
    task (second failure -> ERR_ENGINE_DOWN).

    P0 #2 (G3 PR#12 r1): the pool is the UNTRIED-fit instances ONLY — a
    retry that re-picks an already-tried instance violates S1.5 "one
    retry, next instance". (The pre-fix fallback was all-fit, so a second
    failure could land back on the instance that just failed. The
    pre-fix "least-loaded ANY instance" branch is gone with it: when
    every healthy instance has been tried, the task fails instead.)"""
    tried = set(rec.get("instances_tried") or [])
    instances = [e for e in doc.get("instances", []) if e.get("state") == "healthy"]
    if not instances:
        return None, None
    ctx_need = rec.get("ctx_need")

    def fits(e: dict) -> bool:
        return ctx_need is None or (
            isinstance(e.get("ctx"), int) and e["ctx"] >= ctx_need
        )

    fresh = [e for e in instances if fits(e) and e.get("i") not in tried]
    if not fresh:
        return None, None
    target = min(fresh, key=lambda e: e.get("i", 0))
    port = target.get("port")
    if not isinstance(port, int):
        ident_port_base = 30101
        try:
            ident_port_base = H.load_engine_identity().get("port_base", 30101)
        except H.ConfigError:
            pass
        port = ident_port_base + int(target.get("i", 0))
    return target.get("i"), port


def _persist_rec_preserving(d: dict, rec: dict) -> None:
    """Persist the local `rec` while preserving the on-disk record's
    `steer_messages`. The step-loop's local `rec` is a worker-start
    snapshot; a steer posted between step-boundaries writes
    `steer_messages` to the DISK record, and an unconditional
    `H.write_task(d, rec)` would squash it. G3 PR#18 r4 P1: used at every
    step-loop write site that persists the local record (engine-retried
    and step-end), mirroring the S4.3 lock-safe re-read-merge at the
    step boundary. The on-disk `steer_messages` is the source of truth
    for the pending queue; the local `rec` owns every other field."""
    with H.lock_wait(d["base"], f"task-{rec['task_id']}"):
        fresh = H.read_task(d, rec["task_id"])
        # adopt the disk's pending-queue state (empty list if no steers);
        # do NOT clobber it with the stale local snapshot.
        rec["steer_messages"] = fresh.get("steer_messages") or []
        # G3 PR#18 r6 P1 (defense-in-depth, no behavior change today):
        # also re-adopt the loop-gate fields from the on-disk record.
        # In the current flow the local `rec` (json.loads of the spawn
        # record at worker start, _main) already carries tools/max_steps/
        # workdir — they are set once at spawn (harness record creation)
        # and never mutated mid-loop — so this is a no-op; but it makes
        # the invariant structural instead of accidental: the on-disk
        # record is the source of truth for the gate flags, and a future
        # code path that drops or mutates them on the local snapshot can
        # never propagate the loss to disk.
        for k in ("tools", "max_steps", "workdir"):
            if fresh.get(k) is not None:
                rec[k] = fresh[k]
        H.write_task(d, rec)


def _stream_chat(port: int, ident: dict, body: dict) -> dict:
    """One streaming chat completion against the instance at `port`.
    Returns {"ok": True, "answer", "reasoning", "tool_call", "usage",
    "finish_reason", "cmpl_id"} or {"ok": False, "kind": "ctx"|"parse"|"engine",
    "detail"}.

    `finish_reason` is the stream's finish_reason (e.g. "stop" /
    "length" / "tool_calls") — None when the engine omits it; `cmpl_id`
    is the completion id (F11 cancel lever) — None when omitted.
    (F1/F2 first-practical-run remediation, 2026-10-03.)

    `tool_call` is the parsed native `tool_calls` array entry when the
    stream carries one (the A2 native-array shape), else None — the
    caller also checks the answer for the text-JSON convention.
    """
    url = f"http://127.0.0.1:{port}/v1/chat/completions"
    payload = json.dumps(body).encode()
    req = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Authorization": f"Bearer {ident['api_key']}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    content_parts: list[str] = []
    reasoning_parts: list[str] = []
    # G3 PR#18 r9 P1 (fix): accumulate the native tool_calls deltas per
    # index. The OpenAI streaming contract delivers a tool call in
    # fragments: the first chunk carries id + function.name, LATER
    # chunks carry only function.arguments fragments that must be
    # APPENDED (OpenAI/llama.cpp both do this for multi-token arguments).
    # The pre-r9 code OVERWROTE `tool_calls` on every delta, so a
    # chunked call would end up with only the LAST fragment's
    # arguments — a partial, unparseable tool call. Accumulate by index;
    # finalize the FIRST call that is complete (has a function name) at
    # stream end. Multi-call streams still pick tcs[0] (the r5 P1
    # design: the single-step contract executes the first call).
    tc_acc: dict[int, dict] = {}
    usage: dict | None = None
    # F1/F2 (first-practical-run remediation): capture the SSE `id`
    # (the engine's completion id — the F11 control-endpoint lever) and
    # the stream's `finish_reason` (F2 truncation guard). Both are
    # top-level / choice fields on the OpenAI-compat chunks; the pin
    # emits them (the A2 probe saw finish_reason=tool_calls). Absent
    # -> None (a mock engine that omits them keeps the pre-F2 behavior).
    cmpl_id: str | None = None
    finish_reason: str | None = None
    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_S) as resp:
            status = resp.status
            for raw in resp:
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                chunk_payload = line[5:].strip()
                if chunk_payload == "[DONE]":
                    break
                try:
                    chunk = json.loads(chunk_payload)
                except json.JSONDecodeError:
                    return {
                        "ok": False,
                        "kind": "parse",
                        "detail": f"non-JSON SSE payload in stream (HTTP {status})",
                    }
                if not isinstance(chunk, dict):
                    return {
                        "ok": False,
                        "kind": "parse",
                        "detail": f"non-object SSE payload in stream (HTTP {status})",
                    }
                if chunk.get("id"):
                    cmpl_id = str(chunk["id"])
                if chunk.get("usage"):
                    usage = chunk["usage"]
                choices = chunk.get("choices") or []
                if not choices:
                    continue
                fr = choices[0].get("finish_reason")
                if isinstance(fr, str) and fr:
                    finish_reason = fr
                delta = choices[0].get("delta") or {}
                if delta.get("content"):
                    content_parts.append(str(delta["content"]))
                if delta.get("reasoning_content"):
                    reasoning_parts.append(str(delta["reasoning_content"]))
                tcs = delta.get("tool_calls")
                if isinstance(tcs, list) and tcs:
                    # G3 PR#18 r5 P1 (documented, design): capture ONLY the
                    # first function in the tool_calls array. The spec's
                    # step contract is "when the model emits A tool call the
                    # harness executes it and feeds the result back" (S3.4,
                    # singular) and the @gpu probe verdict at pin 9a9394a89
                    # recorded "the pin emits one" (finish_reason=tool_calls,
                    # one tool_call delta per completion). Parallel tool
                    # calls (multiple in one array) are out of scope for the
                    # host-side single-step contract: execute tcs[0], feed
                    # the result back, and let the model re-emit the rest on
                    # the NEXT step. (If the pin ever grows to emit N, this
                    # line is the site to extend.)
                    # G3 PR#18 r9 P1 (fix): the same tool call can arrive
                    # across MULTIPLE deltas (the OpenAI/llama.cpp
                    # streaming contract: chunk 1 = id + name, later
                    # chunks = arguments fragments). Per index, APPEND
                    # the arguments fragment instead of overwriting; the
                    # call is finalized from the accumulated record at
                    # stream end.
                    for tc_delta in tcs:
                        if not isinstance(tc_delta, dict):
                            continue
                        try:
                            idx = int(tc_delta.get("index", 0))
                        except (TypeError, ValueError):
                            # G3 PR#18 r11 P2 #8 (noted): a non-int `index`
                            # (e.g. the string "abc") is coerced to idx 0
                            # (the first slot) rather than rejected — the
                            # OpenAI/llama.cpp contract always emits a
                            # non-negative int index (or omits it), so the
                            # coercion is a safe default; if a future engine
                            # ever emits a malformed index that should fail,
                            # add a str.isdigit()/isinstance-int guard here.
                            idx = 0
                        acc = tc_acc.setdefault(
                            idx, {"id": None, "name": None, "arguments": ""}
                        )
                        if tc_delta.get("id") and acc["id"] is None:
                            # G3 PR#18 r11 P1 #3 (fix): capture the FIRST
                            # non-empty id and ignore later ones. The
                            # OpenAI/llama.cpp streaming contract re-emits
                            # the id on later chunks; a non-idempotent
                            # sequence (id A then id B) must not silently
                            # switch to the last id, or the appended `tool`
                            # result message's tool_call_id would no longer
                            # match the assistant message's
                            # tool_calls[0].id (breaking the tool-result
                            # pairing contract).
                            acc["id"] = str(tc_delta["id"])
                        fn = tc_delta.get("function")
                        if isinstance(fn, dict):
                            if fn.get("name"):
                                acc["name"] = str(fn["name"])
                            if fn.get("arguments") is not None:
                                acc["arguments"] += str(fn["arguments"])
    except urllib.error.HTTPError as e:
        if e.code == 400:
            return {
                "ok": False,
                "kind": "ctx",
                "detail": f"engine HTTP 400 (request > ctx, S1.5) on port {port}",
            }
        return {
            "ok": False,
            "kind": "engine",
            "detail": f"engine HTTP {e.code} on port {port}"
            + (" (429 = overloaded, retried once)" if e.code == 429 else ""),
        }
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        return {
            "ok": False,
            "kind": "engine",
            "detail": f"connection error on port {port}: {exc.__class__.__name__}: {exc}",
        }
    answer = "".join(content_parts)
    reasoning = "".join(reasoning_parts)
    # G3 PR#18 r9 P1: finalize the first complete tool call from the
    # per-index accumulator (the r5 P1 single-call contract: pick index
    # 0; the model's re-emit loop handles any additional calls on the
    # NEXT step). A stream that carried tool-call deltas but only
    # fragments with no name is still surfaced (arguments only) — the
    # _validate_tool_call path rejects it cleanly rather than the loop
    # silently treating a tool call as a final answer.
    tool_calls: dict | None = None
    if tc_acc:
        first_idx = (
            min(k for k in tc_acc if tc_acc[k].get("name"))
            if any(tc_acc[k].get("name") for k in tc_acc)
            else min(tc_acc)
        )
        acc = tc_acc[first_idx]
        tool_calls = {
            "id": str(acc["id"] or "call_0"),
            "name": str(acc["name"] or ""),
            "arguments_raw": str(acc["arguments"] or "{}"),
        }
    if not answer and not reasoning and tool_calls is None:
        return {
            "ok": False,
            "kind": "parse",
            "detail": f"stream ended with no content (HTTP {status}, port {port})",
        }
    if usage is None:
        # G3 PR#18 r2 P2: the estimate must account for the tool-call
        # payload too — a stream carrying ONLY a tool call (answer +
        # reasoning empty, the common native tool-call shape) would
        # otherwise estimate ~1 completion token.
        # G3 PR#18 r8 P2 (noted): this is a COMPLETION-side estimate only
        # (len/4 heuristic over the model's output). The PROMPT side —
        # which grows every step as the tool_log/history accumulates — is
        # not estimated (prompt_tokens: None); the S4.4 cost model sees a
        # completion-only figure. For a tool-heavy multi-step task the
        # real prompt_tokens dominate, so the estimate UNDER-states cost.
        # It is flagged estimated=True, so a cost model that respects the
        # flag treats it as a lower bound. No behavior change.
        # G3 PR#18 r11 P2#10 (noted): this whole block only runs when the
        # engine did NOT provide a real `usage` (usage is None). If the
        # engine later returns a real usage, the tool-call payload is NOT
        # folded into the completion count and the `estimated` flag is NOT
        # set — the real usage is trusted as-is. Fine for v1 (the engine's
        # completion_tokens already counts the tool-call tokens the model
        # actually emitted); note only, so a future cost-model change that
        # wants tool-call weighting can key off a single flag.
        tc_text = json.dumps(tool_calls) if tool_calls is not None else ""
        usage = {
            "prompt_tokens": None,
            "completion_tokens": max(1, (len(answer) + len(tc_text)) // 4),
            "estimated": True,
        }
    return {
        "ok": True,
        "answer": answer,
        "reasoning": reasoning,
        "tool_call": tool_calls,
        "usage": usage,
        # F1/F2 (first-practical-run remediation): the stream's
        # finish_reason (the F2 truncation guard's signal) and the
        # completion id (F11 — the engine control endpoint addresses a
        # completion by it). Both None when the engine omits them.
        "finish_reason": finish_reason,
        "cmpl_id": cmpl_id,
    }


def _run_attempt(rec: dict, port: int, ident: dict) -> dict:
    """The PRE-LOOP single-attempt body (MAP 3.3): one streaming chat
    completion, the prompt-only message, NO `tools` array. Byte-identical
    wire behavior to the pre-capability-layer worker — the amendment's
    hard constraint for `--tools off` tasks (the Phases 0-3 hermetic
    suite asserts the exact canned mock responses against this shape).
    F1 (first-practical-run remediation, 2026-10-03): the per-completion
    `max_tokens` cap is added ONLY when the record carries it (every
    post-remediation record does); a record without the key keeps the
    legacy byte-identical payload. Returns {"ok": True, ...} or
    {"ok": False, "kind": ...}."""
    body = {
        "model": ident["alias"],
        "messages": [{"role": "user", "content": rec.get("prompt", "")}],
        "stream": True,
    }
    if isinstance(rec.get("max_tokens"), int):
        # F1: cap the pre-loop completion too — a --tools off task that
        # bloats its reasoning still races the task deadline.
        body["max_tokens"] = rec["max_tokens"]
    if rec.get("arm_reasoning_control"):
        # F11: arm the engine's real-time reasoning control on the
        # pre-loop completion too.
        body["reasoning_control"] = True
    res = _stream_chat(port, ident, body)
    if res.get("ok"):
        res.pop("tool_call", None)  # the pre-loop path ignores it (never sent)
    return res


def _parse_text_tool_call(answer: str) -> dict | None:
    """The text-JSON tool-call convention (the A2 probe fallback): when
    the pin's chat template does not emit the native `tool_calls` array,
    the model is expected to emit a fenced (or bare) JSON object naming
    one of the four tools. Returns {"id", "name", "arguments_raw"} or
    None (the answer is a final answer, not a tool call)."""
    if not isinstance(answer, str) or not answer:
        return None
    m = _FENCE_RE.search(answer)
    candidates = [m.group(1)] if m else []
    if answer.lstrip().startswith("{"):
        candidates.append(answer.lstrip())
    for cand in candidates:
        try:
            obj = json.loads(cand)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict):
            continue
        name = None
        for k in _TOOL_CALL_KEYS:
            v = obj.get(k)
            if isinstance(v, str):
                name = v
                break
        if name in T._TOOL_NAMES:
            params = obj
            for pk in ("params", "arguments", "args", "parameters"):
                if isinstance(obj.get(pk), dict):
                    params = obj[pk]
                    break
            if isinstance(params, dict) and name in params:
                # {"tool": "bash", "cmd": "..."} shape — params under the
                # tool name's key.
                params = params[name]
            return {
                "id": str(obj.get("id") or "call_textjson"),
                "name": name,
                "arguments_raw": json.dumps(params if isinstance(params, dict) else {}),
            }
    return None


def _validate_tool_call(tc: dict) -> tuple[dict | None, str | None]:
    """Validate a parsed tool call against the four-tool schema (S3.4:
    "validate it against the four-tool schema"). Returns (params, None)
    or (None, error_text). The error text is fed back to the model as an
    error result (counted against tool_invalid_max, S3.6)."""
    if not isinstance(tc, dict):
        return None, "ERR_TOOL_INVALID_CALL: tool call is not a JSON object"
    name = tc.get("name")
    if name not in T._TOOL_NAMES:
        return None, (
            f"ERR_TOOL_INVALID_CALL: unknown tool {name!r} "
            f"(expected one of {sorted(T._TOOL_NAMES)})"
        )
    raw = tc.get("arguments_raw")
    if not isinstance(raw, str) or not raw.strip():
        return None, f"ERR_TOOL_INVALID_CALL: tool {name!r} has no arguments object"
    try:
        params = json.loads(raw)
    except json.JSONDecodeError as exc:
        return (
            None,
            f"ERR_TOOL_INVALID_CALL: tool {name!r} arguments are not JSON: {exc}",
        )
    if not isinstance(params, dict):
        return None, f"ERR_TOOL_INVALID_CALL: tool {name!r} arguments are not an object"
    # the four-tool schema (the host-side shapes; engine-side executes
    # through the same validation — the model's call is one schema in
    # both modes, S3.4):
    if name in ("bash", "python"):
        key = "cmd" if name == "bash" else "code"
        if not isinstance(params.get(key), str) or not params.get(key):
            return (
                None,
                f"ERR_TOOL_INVALID_CALL: tool {name!r} needs a non-empty string `{key}`",
            )
    if name == "read_file" and (
        not isinstance(params.get("path"), str) or not params.get("path")
    ):
        return (
            None,
            "ERR_TOOL_INVALID_CALL: tool read_file needs a non-empty string `path`",
        )
    if name == "write_file":
        if not isinstance(params.get("path"), str) or not params.get("path"):
            return (
                None,
                "ERR_TOOL_INVALID_CALL: tool write_file needs a non-empty string `path`",
            )
        if not isinstance(params.get("content"), str):
            return (
                None,
                "ERR_TOOL_INVALID_CALL: tool write_file needs a string `content`",
            )
    return params, None


def _execute_tool(
    name: str,
    params: dict,
    ctx: dict,
    port: int | None,
    ident: dict | None,
    tool_execution: str,
    step: int,
) -> dict:
    """Execute one tool call. `tool_execution` = the S3.6 flag
    (host: the bin/bonsai2_tools.py module in-process — the default;
    engine: POST the instance's /tools endpoint, the fork's built-in
    toolset at the pin, S1.3). Returns the S3.1-shaped result dict
    {ok, exit_code, output, detail, step} — host-side via
    bonsai2_tools.execute; engine-side via the /tools HTTP call with the
    host->engine name mapping (S1.3 A8 note)."""
    if tool_execution == "engine" and port is not None and ident is not None:
        return _execute_tool_engine(name, params, ctx, port, ident, step)
    t0 = time.monotonic()
    res = T.execute(name, params, ctx, step=step)
    res["duration_ms"] = int((time.monotonic() - t0) * 1000)
    return res


def _execute_tool_engine(
    name: str, params: dict, ctx: dict, port: int, ident: dict, step: int
) -> dict:
    """The engine-side half (S3.4 dual-mode): POST the instance's
    `/tools` endpoint (the fork's built-in toolset at pin 9a9394a8 —
    S1.3, tools/server/server-tools.cpp handle_post). Body shape per the
    pin: {"tool": <engine name>, "params": {...}}. The response (per the
    pin's invoke() results) is a JSON object; "error" key = failure,
    "plain_text_response" / "result" / other string fields = output.
    The host-side bash/python params are mapped onto
    exec_shell_command (the A8 note: bash ≈ exec_shell_command, no
    engine-side python tool — python runs through sh + the config's
    python bin)."""
    engine_name = _ENGINE_TOOL_NAMES.get(name, name)
    ep = dict(params)
    if name == "bash":
        cmd = str(ep.pop("cmd", ""))
        ep["command"] = cmd
    elif name == "python":
        code = str(ep.pop("code", ""))
        pybin = ctx.get("python_bin") or "/usr/bin/python3"
        # base64-encode the code so multi-line source survives the shell
        # round-trip: the base64 alphabet is shell-safe (no quotes, no
        # newlines, no metachars), so it needs no shell-escaping and the
        # -c body stays a single physical line. (The host-side python tool
        # passes code via stdin, which handles newlines natively; the
        # engine-side path must go through the shell, hence the encode.)
        # G3 PR#18 r3 P2: the quoting is safe BECAUSE pybin comes from the
        # config (tool_python_bin, an absolute path — no spaces/globs in the
        # default /usr/bin/python3) AND the b64 payload is wrapped in single
        # quotes with a shell-safe charset; if a future config ever allows a
        # pybin with shell metachars, re-check this line.
        # G3 PR#18 r10 P2 (noted, v1 limitation): the instance container
        # has no shared volume (units/bonsai2-server@.service: no -v), so a
        # HOST-venv python bin (e.g. ~/prod/.venv/bin/python, an S3.6
        # override) does NOT exist inside the container — only a path that
        # exists in the image (the default /usr/bin/python3) works in
        # engine mode. The key implies a host path that is only valid in
        # host mode; documented, no v1 fix.
        b64 = base64.b64encode(code.encode("utf-8")).decode("ascii")
        ep["command"] = f"{pybin} -c \"import base64;exec(base64.b64decode('{b64}'))\""
    # G3 PR#18 r2 P2: the instance container has no shared volume
    # (units/bonsai2-server@.service: docker run --network host --ipc=host,
    # no -v), so the host workdir (ctx["workdir"]) does NOT exist inside the
    # container. Do NOT force it onto the engine as `cwd` — the fork's chdir
    # into a non-existent path would fail with a confusing error. Leave cwd
    # to whatever the model supplied in `params` (ep == dict(params)); with
    # no cwd the engine uses its own working dir. A model-supplied explicit
    # cwd is honored; relative paths resolve against the engine's own dir —
    # a documented v1 limitation until a shared-workdir volume lands.
    t0 = time.monotonic()
    body = json.dumps({"tool": engine_name, "params": ep}).encode()
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/tools",
        data=body,
        headers={
            "Authorization": f"Bearer {ident['api_key']}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=ENGINE_TOOLS_TIMEOUT_S) as resp:
            raw = resp.read()
        try:
            data = json.loads(raw.decode("utf-8", "replace"))
        except json.JSONDecodeError:
            data = {"plain_text_response": raw.decode("utf-8", "replace")}
        duration_ms = int((time.monotonic() - t0) * 1000)
        if isinstance(data, dict) and data.get("error"):
            return {
                "ok": False,
                "exit_code": 1,
                "output": f"ERR_ENGINE_TOOL: {data['error']}",
                "detail": "ERR_ENGINE_TOOL",
                "step": step,
                "duration_ms": duration_ms,
            }
        if isinstance(data, dict):
            out = None
            for k in ("plain_text_response", "result", "output", "content", "text"):
                if isinstance(data.get(k), str):
                    out = data[k]
                    break
            if out is None:
                out = json.dumps(data)[:4096]
            code = data.get("exit_code")
            code = code if isinstance(code, int) else (0 if out else 1)
            # G3 PR#18 r11 P1#4: the engine /tools endpoint reports a
            # timeout as exit_code 124 (the fork's own convention); mirror
            # the host module's timed_out flag so the loop's
            # res_tool.get("timed_out") match works in BOTH modes. A
            # user-side exit 124 is rare and the engine does not set a
            # distinct "timed_out" key, so we derive it from exit_code.
            return {
                "ok": code == 0,
                "exit_code": code,
                "output": out,
                "detail": None,
                "step": step,
                "duration_ms": duration_ms,
                "timed_out": code == 124,
            }
        return {
            "ok": False,
            "exit_code": 1,
            "output": str(data)[:4096],
            "detail": "ERR_ENGINE_TOOL: unexpected /tools response shape",
            "step": step,
            "duration_ms": duration_ms,
        }
    except urllib.error.HTTPError as e:
        detail = f"ERR_ENGINE_TOOL: /tools HTTP {e.code} on port {port}"
        try:
            body_text = e.read(4096).decode("utf-8", "replace")
        except OSError:
            body_text = ""
        return {
            "ok": False,
            "exit_code": e.code,
            "output": body_text or detail,
            "detail": detail,
            "step": step,
            "duration_ms": int((time.monotonic() - t0) * 1000),
        }
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        return {
            "ok": False,
            "exit_code": 1,
            "output": f"ERR_ENGINE_TOOL: /tools connection error on port {port}: {exc}",
            "detail": "ERR_ENGINE_TOOL",
            "step": step,
            "duration_ms": int((time.monotonic() - t0) * 1000),
        }


def _tool_log_entry(step: int, tc: dict, res: dict) -> dict:
    """The S3.1 tool_log entry: {step, tool, params (redacted), exit_code,
    output (truncated to tool_output_max_kb — already truncated by
    execute()), duration_ms}."""
    return {
        "step": step,
        "tool": tc.get("name"),
        "params": T.redact_params(
            _safe_json(tc.get("arguments_raw"))
            if isinstance(tc.get("arguments_raw"), str)
            else None
        ),
        "exit_code": res.get("exit_code"),
        "output": res.get("output"),
        "duration_ms": res.get("duration_ms"),
    }


def _safe_json(raw: str):
    try:
        v = json.loads(raw)
        return v if isinstance(v, dict) else None
    except json.JSONDecodeError:
        return None


def _append_result_file(
    rec: dict, tool_log: list, steps: int, workdir: str | None
) -> None:
    """Best-effort ledger persist: rewrite the result file with the
    capability fields so a FAILED task's result file still carries
    tool_log up to the failure (S3.1: "the ledger is diagnostic data,
    not a verdict"). The answer/reasoning/usage (if written earlier) are
    preserved."""
    rf = rec.get("result_file")
    if not isinstance(rf, str) or not rf:
        return
    try:
        data: dict = {}
        p = Path(rf)
        if p.is_file():
            try:
                loaded = json.loads(p.read_text())
                if isinstance(loaded, dict):
                    data = loaded
            except (OSError, json.JSONDecodeError):
                data = {}
        data["tool_log"] = tool_log
        data["steps"] = steps
        if workdir is not None:
            data["workdir"] = workdir
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2) + "\n")
        tmp.replace(p)
    except OSError as exc:
        _log(f"ledger persist to {rf} failed: {exc}")


def _write_cmpl_sidecar(d: dict, rec: dict, port: int, cmpl_id: str | None) -> None:
    """F11 (first-practical-run remediation, 2026-10-03): record the
    in-flight completion's id so the harness's reaper can target it with
    the engine's control POST after a deadline reaping.

    The sidecar is `<tasks>/<task_id>.cmpl.json` (a sibling of the task
    record, in `d["tasks"]`). The reaper's F11 cancel reads it via
    `_read_cmpl_sidecar` and POSTs
    `/v1/chat/completions/control {id: <cmpl_id>, action: reasoning_end}`
    to the task's port. The worker rewrites it at each step boundary with
    the LATEST completion's id (the most recently in-flight one is the
    one that would keep decoding after a reaping). A None/empty cmpl_id
    (a mock/legacy engine that omits the SSE id) leaves the previous
    sidecar in place; the reaper treats a missing/unknown id as a no-op.
    Best-effort: a write failure only logs (the sidecar is a cancel hint,
    never a verdict).
    """
    if not isinstance(cmpl_id, str) or not cmpl_id:
        return
    task_id = rec.get("task_id")
    if not isinstance(task_id, str) or not task_id:
        return
    tasks_dir = d.get("tasks")
    if not isinstance(tasks_dir, (str, Path)):
        return
    sidecar = Path(tasks_dir) / f"{task_id}.cmpl.json"
    try:
        sidecar.parent.mkdir(parents=True, exist_ok=True)
        sidecar.write_text(
            json.dumps({"cmpl_id": cmpl_id, "port": port, "ts": time.time()}) + "\n"
        )
    except OSError as exc:
        _log(f"{task_id}: F11 cmpl sidecar write failed: {exc}")


def _salvage_partial_result(
    rec: dict,
    tool_log: list,
    steps: int,
    workdir: str | None,
    answer: str,
    reasoning: str,
    usage: dict | None,
) -> None:
    """F4 (first-practical-run remediation, 2026-10-03): partial-artifact
    salvage on a deadline / late-result timeout.

    The evidence (2026-10-03 ledger, F4): at this pin a big code-gen
    task's first completion emitted 9-21K reasoning tokens before the
    deadline fired, so the record ended `timeout` with `answer: null` —
    the reasoning (which often CONTAINS the planned/started work) and
    any partial answer text died in the record's `detail` string and
    never reached the result file the main agent reads via collect.

    This writes the in-flight completion's accumulated answer/reasoning
    to the result file tagged `partial: true` (plus the tool_log / steps
    / workdir capability fields, the S3.1 ledger shape). The harness's
    `reap_stale` deadline path reads these fields back onto the record
    (`partial_answer` / `partial_reasoning`) so collect on a reaped
    timeout returns the salvageable partial instead of nulls.

    Best-effort: a write failure only logs (the timeout state is already
    set; the salvage is a bonus, never a new failure mode).

    No-op when BOTH answer and reasoning are empty (nothing to salvage).
    """
    if not answer and not reasoning:
        return
    rf = rec.get("result_file")
    if not isinstance(rf, str) or not rf:
        return
    try:
        data: dict = {}
        p = Path(rf)
        if p.is_file():
            try:
                loaded = json.loads(p.read_text())
                if isinstance(loaded, dict):
                    data = loaded
            except (OSError, json.JSONDecodeError):
                data = {}
        # partial: true is the salvage marker; the answer/reasoning keys
        # carry the accumulated text (may be one or the other).
        data["partial"] = True
        if answer:
            data["answer"] = answer
        if reasoning:
            data["reasoning"] = reasoning
        if usage is not None:
            data["usage"] = usage
        data["tool_log"] = tool_log
        data["steps"] = steps
        if workdir is not None:
            data["workdir"] = workdir
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2) + "\n")
        tmp.replace(p)
        _log(
            f"{rec.get('task_id', '?')}: F4 partial salvage written "
            f"(answer {len(answer)} chars, reasoning {len(reasoning)} chars)"
        )
    except OSError as exc:
        _log(f"{rec.get('task_id', '?')}: F4 partial salvage write failed: {exc}")


def _run_tool_loop(
    rec: dict,
    port: int,
    ident: dict,
    d: dict,
    tool_cfg: dict,
) -> int:
    """The S3.4 bounded step-loop (MAP 3.14): send the message history
    -> parse the tool call (native array OR text-JSON) -> execute (host
    module or the instance /tools endpoint per S3.6 tool_execution) ->
    append -> repeat; final answer -> done. Loop bounds: --deadline
    bounds the whole loop; a tool step overrun fails ERR_TOOL_TIMEOUT
    (collect 21); --max-steps without a final answer -> timeout /
    ERR_DEADLINE (collect 22); tool_invalid_max consecutive bad calls ->
    failed ERR_TOOL_INVALID_CALL (collect 21).

    The record is re-read under the lock at every step boundary (steer
    messages are drained there — S4.3: "injected as the next user
    message at the loop's next step boundary"). The engine-down one-retry
    (S1.5) applies PER ATTEMPT of a step, not per task — the same
    untried-instance rule as the pre-loop path. Returns the worker exit
    code (always 0 — the record is the source of truth)."""
    workdir = rec.get("workdir")
    max_steps = rec.get("max_steps")
    if not isinstance(max_steps, int) or max_steps < 1:
        max_steps = int(tool_cfg.get("task_max_steps", 32))
    tool_invalid_max = int(tool_cfg.get("tool_invalid_max", 3))
    tool_execution = tool_cfg.get("tool_execution", "host")
    if isinstance(workdir, str) and workdir:
        try:
            tool_ctx = T.make_ctx(
                workdir,
                timeout_s=float(tool_cfg.get("tool_timeout_s", 60.0)),
                output_max_kb=int(tool_cfg.get("tool_output_max_kb", 32)),
                python_bin=tool_cfg.get("tool_python_bin") or None,
            )
        except ValueError as exc:
            _set_state(
                rec,
                "failed",
                "ERR_TOOL_INVALID_CALL",
                f"workdir setup: {exc}",
                d,
            )
            _log(f"{rec['task_id']}: {exc} -> failed ERR_TOOL_INVALID_CALL")
            return 0
    else:
        # Defensive: pre-capability records have no workdir — fall back to
        # the record's results base's parent (the scratch space is the
        # results dir's sibling); the tool loop is --tools on only, and
        # --tools on records always carry a workdir from spawn.
        tool_ctx = None

    messages: list[dict] = [{"role": "user", "content": rec.get("prompt", "")}]
    tool_log: list[dict] = []
    steps = 0
    consecutive_bad = 0
    rec.setdefault("instances_tried", [])
    deadline_s = rec.get("deadline_s")
    deadline_at = time.monotonic() + (
        float(deadline_s) if deadline_s is not None else DEFAULT_DEADLINE_S
    )
    attempt_detail: list[str] = []

    while True:
        # ---- step boundary: deadline + steer drain (S4.3) + persist ----
        if time.monotonic() >= deadline_at:
            _set_state(
                rec,
                "timeout",
                "ERR_DEADLINE",
                f"deadline {deadline_s} s expired mid-loop",
                d,
            )
            _append_result_file(rec, tool_log, steps, workdir)
            _log(f"{rec['task_id']}: deadline expired -> timeout")
            return 0
        with H.lock_wait(d["base"], f"task-{rec['task_id']}"):
            fresh = H.read_task(d, rec["task_id"])
            if fresh.get("state") in H.TERMINAL_STATES:
                # cancel/steer raced us terminal (cancel marks failed
                # ERR_CANCELLED in the signal handler) — the record is
                # the verdict; stop looping.
                rec = fresh
                _log(
                    f"{rec['task_id']}: already {fresh.get('state')} at step boundary — loop ends"
                )
                return 0
            steer_msgs = fresh.get("steer_messages") or []
            # drain the steer queue (S4.3): each pending steer becomes the
            # next user message. (The pre-loop path kept them advisory —
            # consumed at the next ATTEMPT; the loop consumes at the next
            # STEP boundary, which is the amendment's steer semantics.)
            # G3 PR#18 r7 P1 (invariant check, no behavior change): the
            # loop-local tool_ctx (the execution root the tools actually
            # run in) was built at loop entry from the initial workdir.
            # NOTE: `rec` here is usually `fresh` itself (the step-end
            # site does `rec = fresh`), so the check compares the
            # LOOP-ENTRY `workdir` local — not `rec["workdir"]` — against
            # the on-disk record: workdir is invariant across the loop
            # (set once at spawn, never mutated), and the r6 P1
            # re-adoption in _persist_rec_preserving means a hypothetical
            # mid-loop record change would be visible at the boundary.
            # Log loudly instead of letting the tools silently run in a
            # stale root.
            if workdir is not None and fresh.get("workdir") != workdir:
                _log(
                    f"{rec['task_id']}: workdir invariant broken mid-loop "
                    f"(loop entry {workdir!r} vs record "
                    f"{fresh.get('workdir')!r}); tools keep the loop-entry "
                    f"root, record now reports the new value"
                )
            for sm in steer_msgs:
                msg = sm.get("message") if isinstance(sm, dict) else None
                if isinstance(msg, str) and msg:
                    messages.append({"role": "user", "content": msg})
            fresh["steer_messages"] = []
            fresh["steps"] = steps
            fresh["tool_log"] = tool_log
            fresh["updated_ts"] = time.time()
            H.write_task(d, fresh)
        # G3 PR#18 r8 P0: re-sync the loop-local `port` from the on-disk
        # record at the step boundary. The local `port` is a loop variable
        # (the engine-retry branch updates it: rec["port"]=nport AND
        # port=int(nport), line ~931), so it tracks the record in the
        # common case; but the DISK record is the source of truth for the
        # gate fields (r6 P1 re-adoption), and the next step's
        # _stream_chat(port, ...) reads the LOCAL — so re-sync here to
        # keep them equal if any other process (a steer/cancel write, or a
        # future port-migration path) changed the record's port mid-loop.
        # In the current design nothing external mutates port mid-loop, so
        # this is a no-op; it makes the invariant structural.
        if isinstance(fresh.get("port"), int):
            port = int(fresh["port"])
        if steps >= max_steps:
            # S3.4: "hitting it without a final answer -> task timeout /
            # ERR_DEADLINE (the loop exhausted its step budget)".
            # G3 PR#18 r8 P1 (documented, contract): the check fires when
            # steps == max_steps, i.e. AFTER max_steps tool steps have
            # run without a final answer — so `--max-steps N` allows N
            # tool steps and then times out (codified by
            # test_max_steps_exhausted_is_timeout: `--max-steps 3` ->
            # exactly 3 completions). This is NOT an off-by-one: a task
            # with `--max-steps 1` that emits a tool call on step 1 has
            # exhausted its single allowed step and correctly times out;
            # the "1 step" was the tool step, not a final answer.
            _set_state(
                rec,
                "timeout",
                "ERR_DEADLINE",
                f"--max-steps {max_steps} exhausted without a final answer (S3.4)",
                d,
            )
            _append_result_file(rec, tool_log, steps, workdir)
            _log(f"{rec['task_id']}: max-steps exhausted -> timeout ERR_DEADLINE")
            return 0

        # ---- the step's completion attempt (S1.5 one-retry per attempt) ----
        res: dict | None = None
        for _ in range(2):
            if time.monotonic() >= deadline_at:
                break
            res = _stream_chat(
                port,
                ident,
                {
                    "model": ident["alias"],
                    "messages": messages,
                    "tools": T.TOOLS_JSON,
                    "stream": True,
                    # F1 (first-practical-run remediation, 2026-10-03):
                    # the per-completion output cap. Without this the
                    # engine runs unbounded; at this pin (Ternary-Bonsai-2,
                    # deepseek reasoning) a big code-gen task emits 9–21K
                    # reasoning tokens before the first tool call and
                    # loses the deadline race. The cap is the harness
                    # record's max_tokens (spawn flag else S3.6 default).
                    # Gated on an int exactly like the pre-loop path
                    # (S3.4 F1 delta: "a record without the key keeps the
                    # legacy byte-identical payload" — an unconditional
                    # `rec.get(...)` would emit "max_tokens": null for a
                    # pre-remediation record and change the wire shape).
                    **(
                        {"max_tokens": rec["max_tokens"]}
                        if isinstance(rec.get("max_tokens"), int)
                        else {}
                    ),
                    # F11 (first-practical-run remediation): arm the
                    # engine's real-time reasoning control on this
                    # completion so a deadline reaping can end an
                    # orphaned in-flight one via
                    # /v1/chat/completions/control {id, reasoning_end}.
                    # Only when the record arms it (arm_reasoning_control,
                    # default on); the engine defaults the field to false
                    # when absent, so omitting it on a disarmed record
                    # keeps the pre-F11 wire shape.
                    **(
                        {"reasoning_control": True}
                        if rec.get("arm_reasoning_control")
                        else {}
                    ),
                },
            )
            if time.monotonic() >= deadline_at:
                # a result landing after the deadline is a late result —
                # S3.4 deadline-expiry-mid-run -> timeout even if the
                # step technically returned.
                # F4 (first-practical-run remediation, 2026-10-03): a late
                # result is EXACTLY the evidence case — the completion's
                # accumulated reasoning/answer (9-21K reasoning at this
                # pin) landed after the deadline and would otherwise die
                # in `detail`. Salvage it to the result file tagged
                # partial:true before the reaper marks the record.
                if res is not None and res.get("ok"):
                    _salvage_partial_result(
                        rec,
                        tool_log,
                        steps,
                        workdir,
                        res.get("answer") or "",
                        res.get("reasoning") or "",
                        res.get("usage"),
                    )
                _set_state(
                    rec,
                    "timeout",
                    "ERR_DEADLINE",
                    f"deadline {deadline_s} s expired mid-loop",
                    d,
                )
                _append_result_file(rec, tool_log, steps, workdir)
                _log(f"{rec['task_id']}: deadline expired after step -> timeout")
                return 0
            if res.get("ok"):
                break
            if res.get("kind") in ("ctx", "parse"):
                break  # task-level terminal, no retry
            # kind == engine: the S1.5 one-retry path (per ATTEMPT of a
            # step, same untried-instance rule as the pre-loop path).
            attempt_detail.append(res.get("detail") or "")
            try:
                doc = H.load_health_doc()
            except H.HealthMissing:
                doc = {"instances": []}
            ni, nport = _pick_retry_instance(doc, rec, d)
            if ni is None:
                res = {
                    "ok": False,
                    "kind": "engine",
                    "detail": "second failure -> failed ERR_ENGINE_DOWN (S1.5): "
                    + "; ".join(attempt_detail),
                }
                break
            rec["instance"] = ni
            rec["port"] = nport
            if ni not in rec["instances_tried"]:
                rec["instances_tried"].append(ni)
            port = int(nport)
            rec["state"] = "engine_retried"
            rec["error"] = "ERR_ENGINE_DOWN"
            rec["detail"] = "engine_retried once on i={} (S1.5, tool loop): {}".format(
                ni, "; ".join(attempt_detail)
            )
            rec["updated_ts"] = time.time()
            _persist_rec_preserving(d, rec)
            _log(
                f"{rec['task_id']}: {res.get('detail')} -> engine_retried on i={ni} (port {port})"
            )
            time.sleep(POLL_S)
        steps += 1
        rec["steps"] = steps
        rec["updated_ts"] = time.time()
        _persist_rec_preserving(d, rec)
        # F11 (first-practical-run remediation, 2026-10-03): record this
        # step's completion id (the one the engine may still be decoding
        # when the reaper's deadline backstop fires) so the reaper can
        # target it with the control POST. The sidecar is rewritten each
        # step — the latest in-flight id is the cancel target. A mock /
        # legacy engine that omits the SSE id returns None -> the sidecar
        # is left as-is (the reaper treats a missing id as a no-op).
        if res is not None and res.get("ok"):
            _write_cmpl_sidecar(d, rec, port, res.get("cmpl_id"))

        if res is None:
            # the deadline expired before the first attempt of this step
            # ran (the retry loop broke at its own deadline check) -> the
            # task's --deadline bounds the whole loop (S3.4).
            _set_state(
                rec,
                "timeout",
                "ERR_DEADLINE",
                f"deadline {deadline_s} s expired mid-loop",
                d,
            )
            _append_result_file(rec, tool_log, steps, workdir)
            _log(f"{rec['task_id']}: deadline expired before step attempt -> timeout")
            return 0

        if not res.get("ok"):
            kind = res.get("kind")
            if kind == "ctx":
                _set_state(rec, "failed", "ERR_CTX_TOO_LONG", res.get("detail"), d)
                _append_result_file(rec, tool_log, steps, workdir)
                _log(
                    f"{rec['task_id']}: {res.get('detail')} -> failed ERR_CTX_TOO_LONG"
                )
                return 0
            if kind == "parse":
                _set_state(rec, "failed", "ERR_PARSE", res.get("detail"), d)
                _append_result_file(rec, tool_log, steps, workdir)
                _log(f"{rec['task_id']}: {res.get('detail')} -> failed ERR_PARSE")
                return 0
            # kind == engine (second failure, no untried target)
            _set_state(rec, "failed", "ERR_ENGINE_DOWN", res.get("detail"), d)
            _append_result_file(rec, tool_log, steps, workdir)
            _log(f"{rec['task_id']}: second failure -> failed ERR_ENGINE_DOWN")
            return 0

        answer = res.get("answer") or ""
        reasoning = res.get("reasoning") or ""
        usage = res.get("usage")

        # ---- parse the tool call: native array FIRST, then text-JSON ----
        tc = res.get("tool_call") or _parse_text_tool_call(answer)
        if tc is None:
            # F2 (first-practical-run remediation, 2026-10-03): a
            # truncation guard against the exact evidence failure. With
            # F1's max_tokens cap the engine stops mid-completion at
            # finish_reason="length" — and at this pin a completion cut
            # inside its reasoning bloat carries EMPTY answer + reasoning
            # deltas (reasoning arrives as one terminal chunk on the pin,
            # so a cut leaves nothing accumulated). Pre-F2 that empty
            # completion marked the task `done` with an empty answer —
            # a silent false success (worse than the timeout it replaced).
            # Guard: finish_reason=="length" AND no answer content AND no
            # tool call => NOT a final answer; feed a nudge back so the
            # next step resumes. Conservative by design (the round data:
            # content stayed empty through the bloat) — a completion that
            # DID emit answer content is trusted as a final answer, which
            # bounds the nudge loop (max_steps still caps it).
            # finish_reason absent (a mock/legacy engine) -> pre-F2 path.
            finish_reason = res.get("finish_reason")
            if finish_reason == "length" and not answer.strip():
                _log(
                    f"{rec['task_id']}: step {steps} hit finish_reason=length "
                    f"with no answer content (F2 truncation) — nudging, not done"
                )
                messages.append({"role": "assistant", "content": answer})
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "Your last reply was cut off mid-reasoning by the "
                            "output cap and carried no visible answer. Resume "
                            "directly: skip any preamble and continue with the "
                            "next tool call or your final answer."
                        ),
                    }
                )
                continue
            # final answer -> done
            try:
                Path(rec["result_file"]).parent.mkdir(parents=True, exist_ok=True)
                tmp = Path(rec["result_file"]).with_suffix(".tmp")
                # G3 PR#18 r3 P1 (waived false-positive): `workdir` here is
                # the worker-start snapshot (rec.get("workdir"), line 710).
                # It is set ONCE at spawn and never mutated mid-loop (only
                # steps / tool_log / steer_messages / state change), so the
                # local value is always == the on-disk record's workdir — no
                # lock-safe re-read needed, unlike steps / tool_log which are
                # accumulated across steps.
                # G3 PR#18 r5 P1 (documented, design): the write is
                # tmp + atomic rename (tmp.write_text -> tmp.replace), so a
                # mid-write failure leaves the PREVIOUS result file intact
                # (never a half-written file the collector would read as
                # truncated JSON). The tmp file is a SIBLING of the target
                # (.json -> .json.tmp) on the same filesystem, so
                # Path.replace is an atomic rename(2). The loop path writes
                # the full result dict directly (answer + reasoning + usage
                # + tool_log + steps + workdir); the pre-loop path writes
                # only {answer, reasoning, usage} — both land atomically.
                tmp.write_text(
                    json.dumps(
                        {
                            "answer": answer,
                            "reasoning": reasoning,
                            "usage": usage,
                            "tool_log": tool_log,
                            "steps": steps,
                            "workdir": workdir,
                        },
                        indent=2,
                    )
                    + "\n"
                )
                tmp.replace(Path(rec["result_file"]))
            except OSError as exc:
                _set_state(
                    rec,
                    "failed",
                    "ERR_ENGINE_DOWN",
                    f"could not write {rec['result_file']}: {exc}",
                    d,
                )
                _log(str(exc))
                return 0
            _set_state(rec, "done", None, None, d)
            _log(f"{rec['task_id']}: done after {steps} step(s) (port {port})")
            return 0

        params, verr = _validate_tool_call(tc)
        if verr is not None:
            consecutive_bad += 1
            if consecutive_bad >= tool_invalid_max:
                # S3.4: "up to tool_invalid_max consecutive bad calls,
                # then the task fails ERR_TOOL_INVALID_CALL" (S1.5 row).
                err_res = {
                    "ok": False,
                    "exit_code": 1,
                    "output": verr,
                    "detail": "ERR_TOOL_INVALID_CALL",
                    "step": steps,
                    "duration_ms": 0,
                }
                tool_log.append(_tool_log_entry(steps, tc, err_res))
                _set_state(
                    rec,
                    "failed",
                    ERR_TOOL_INVALID_CALL,
                    f"{consecutive_bad} consecutive malformed tool calls (S3.4 "
                    f"tool_invalid_max={tool_invalid_max}): last error: {verr}",
                    d,
                )
                _append_result_file(rec, tool_log, steps, workdir)
                _log(f"{rec['task_id']}: -> failed ERR_TOOL_INVALID_CALL")
                return 0
            # feed the error back to the model (the loop continues)
            messages.append(
                {
                    "role": "assistant",
                    "content": answer or None,
                    "reasoning_content": reasoning or None,
                    "tool_calls": [
                        {
                            "id": tc.get("id"),
                            "type": "function",
                            "function": {
                                "name": str(tc.get("name")),
                                "arguments": str(tc.get("arguments_raw") or "{}"),
                            },
                        }
                    ],
                }
            )
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": str(tc.get("id")),
                    "content": json.dumps({"error": verr}),
                }
            )
            tool_log.append(
                _tool_log_entry(
                    steps,
                    tc,
                    {
                        "ok": False,
                        "exit_code": 1,
                        "output": verr,
                        "duration_ms": 0,
                        "step": steps,
                    },
                )
            )
            _log(
                f"{rec['task_id']}: malformed tool call ({consecutive_bad}/"
                f"{tool_invalid_max}) — error fed back to the model"
            )
            continue
        consecutive_bad = 0

        # ---- execute the tool call (host module or the /tools endpoint) ----
        _log(f"{rec['task_id']}: step {steps}: tool {tc.get('name')}")
        res_tool = _execute_tool(
            tc.get("name"),
            params,
            tool_ctx if tool_ctx is not None else {},
            port,
            ident,
            tool_execution,
            steps,
        )
        # S3.4: "a step that overruns is killed at its per-tool timeout
        # (tool_timeout_s) and the task fails ERR_TOOL_TIMEOUT" — the
        # host-side module enforces the process-group kill and reports a
        # result with timed_out=True; the task-level failure is recorded
        # here. G3 PR#18 r11 P1#4: match on the explicit `timed_out` flag,
        # NOT on exit_code == 124 — a user `bash "exit 124"` (or a
        # returncode-None post-communicate race) that is NOT a real timeout
        # must not fail the task; and a genuine timeout whose command also
        # happened to print 124 must still fail it.
        if res_tool.get("timed_out") is True:
            tool_log.append(_tool_log_entry(steps, tc, res_tool))
            _set_state(
                rec,
                "failed",
                ERR_TOOL_TIMEOUT,
                f"tool {tc.get('name')!r} exceeded tool_timeout_s "
                f"({int(tool_cfg.get('tool_timeout_s', 60))} s) at step {steps} (S3.4)",
                d,
            )
            _append_result_file(rec, tool_log, steps, workdir)
            _log(f"{rec['task_id']}: tool step timeout -> failed ERR_TOOL_TIMEOUT")
            return 0
        tool_log.append(_tool_log_entry(steps, tc, res_tool))

        # append the tool result to the history (the loop continues)
        if isinstance(res_tool.get("output"), str):
            out_text = res_tool["output"]
        else:
            out_text = json.dumps(res_tool.get("output"))
        messages.append(
            {
                "role": "assistant",
                "content": answer or None,
                "reasoning_content": reasoning or None,
                "tool_calls": [
                    {
                        "id": tc.get("id"),
                        "type": "function",
                        "function": {
                            "name": str(tc.get("name")),
                            "arguments": str(tc.get("arguments_raw") or "{}"),
                        },
                    }
                ],
            }
        )
        messages.append(
            {
                "role": "tool",
                "tool_call_id": str(tc.get("id")),
                "content": out_text,
            }
        )
        # S4.3 (the amendment's steer semantics): the step-end persist is
        # LOCK-SAFE — re-sync from the record and merge the step fields.
        # The steer/cancel commands write the SAME record under this very
        # task lock; a plain write_task(rec) would squash a steer that
        # landed mid-step (the local rec predates it: loaded at worker
        # start, before the steer command ran). If the record went
        # terminal mid-step (cancel's SIGTERM handler), keep its verdict
        # and let the next step boundary end the loop.
        with H.lock_wait(d["base"], f"task-{rec['task_id']}"):
            fresh = H.read_task(d, rec["task_id"])
            if fresh.get("state") not in H.TERMINAL_STATES:
                fresh["steps"] = steps
                fresh["tool_log"] = tool_log
                fresh["updated_ts"] = time.time()
                H.write_task(d, fresh)
            # G3 PR#18 r7 P0 (verified, no behavior change): adopting the
            # DISK record as the loop-local `rec` is safe — every field
            # the next iteration reads from `rec` is persistable on the
            # record: result_file/instances_tried/deadline_s/instance/
            # port/tools/max_steps/workdir are set at SPAWN (harness
            # record creation) and persisted by every write path here;
            # the only loop-local state is messages/steps/tool_log/
            # consecutive_bad/deadline_at (loop variables, not record
            # fields). So `rec = fresh` cannot lose a field the engine-
            # retry branch or the final write needs — the reviewer's
            # KeyError scenario required a spawn-time field to be absent
            # from the record, but the spawn record is the single
            # writer of those fields and it persists them atomically
            # (write_task = tmp + rename).
            rec = fresh


def main() -> int:
    """The ALWAYS-exits-0 contract (the docstring's source-of-truth note,
    G3 PR#12 r2 P2: it must be ENFORCED, not just documented) — a worker
    crash leaves its record `running`, and the Phase-3 reaper (MAP 3.4)
    turns a stale running record into failed/ERR_ENGINE_DOWN. Any
    uncaught exception (a KeyError in _run_attempt, a bad record port,
    ...) would otherwise escape as Python's exit 1; log + return 0 so
    the record is the single source of truth either way. `_Cancelled`
    (the SIGTERM sentinel, MAP 3.7 cancel) is a CLEAN exit: the handler
    already marked the record terminal, so 0 is the contract."""
    try:
        return _main()
    except _Cancelled:
        return 0
    except Exception as exc:  # noqa: BLE001 — the always-exits-0 contract
        _log(
            f"uncaught worker error: {exc.__class__.__name__}: {exc} (record stays as-is; the reaper owns it)"
        )
        return 0


def _main() -> int:
    if len(sys.argv) != 2:
        _log("usage: bonsai2-taskworker.py <task-record.json>")
        return (
            0  # the record (if any) carries the state; a bad argv is the spawner's bug
        )
    rec_path = Path(sys.argv[1])
    try:
        rec = json.loads(rec_path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        _log(f"cannot read task record {rec_path}: {exc}")
        return 0
    if not isinstance(rec, dict) or not rec.get("task_id"):
        _log(f"task record malformed: {rec_path}")
        return 0
    # MAP 3.7: the cancel path (SIGTERM) — the handler marks the record
    # failed ERR_CANCELLED and raises the sentinel; main() turns it into
    # the contract's clean 0. Installed BEFORE the first state check so
    # even an immediate cancel lands.
    global _CANCEL_REC_PATH
    _CANCEL_REC_PATH = rec_path
    signal.signal(signal.SIGTERM, _on_cancel_signal)
    # The record's own location IS the results layout (the spawner wrote
    # it there): <results>/tasks/<task_id>.json. Deriving d from the
    # record path (not from a config re-load) keeps the worker correct
    # no matter what env it runs under — the record is the contract.
    d = {
        "base": rec_path.parent.parent,
        "tasks": rec_path.parent,
        "locks": rec_path.parent.parent / ".locks",
    }
    if rec.get("state") in H.TERMINAL_STATES:
        _log(f"{rec['task_id']}: already {rec['state']} — nothing to do")
        return 0
    deadline_s = rec.get("deadline_s")
    # P2 #8 (G3 PR#12 r1): `0.0` is falsy — an explicit zero deadline must
    # mean "already expired" (timeout immediately), not "fall back to the
    # default". Use `is not None`, not truthiness.
    deadline_at = time.monotonic() + (
        float(deadline_s) if deadline_s is not None else DEFAULT_DEADLINE_S
    )
    try:
        ident = H.load_engine_identity()
    except H.ConfigError as exc:
        rec["error"] = "ERR_ENGINE_DOWN"
        rec["detail"] = f"worker-start: {exc}"
        rec["state"] = "failed"
        rec["updated_ts"] = time.time()
        H.write_task(d, rec)
        _log(str(exc))
        return 0
    port = rec.get("port")
    if not isinstance(port, int):
        port = int(ident.get("port_base", 30101)) + int(rec.get("instance", 0) or 0)
    port = int(port)
    rec.setdefault("instances_tried", [])
    if rec.get("instance") not in rec["instances_tried"]:
        rec["instances_tried"].append(rec.get("instance"))

    # S3.4 (the amendment, MAP 3.14): the tool-loop path is selected by
    # the record's `tools` field (spawn sets it; default ON, S3.2).
    if rec.get("tools") == "on":
        # The S3.6 capability-layer config (tool_execution, timeouts,
        # max-steps defaults). A malformed harness.yaml is NOT fatal to
        # the loop (the record's own fields — workdir, max_steps,
        # deadline_s — are authoritative; the defaults are sane): fall
        # back to the S3.6 literal defaults.
        try:
            tool_cfg = H.load_config()
        except H.ConfigError as exc:
            _log(f"capability config load failed ({exc}); using S3.6 defaults")
            tool_cfg = dict(H.CONFIG_DEFAULTS)
        return _run_tool_loop(rec, port, ident, d, tool_cfg)

    # ---- the pre-loop single-attempt path (--tools off; byte-identical
    # to the pre-capability-layer worker, the amendment's hard
    # constraint) ----
    attempt_detail: list[str] = []
    for _ in range(2):  # one run + the S1.5 single retry
        if time.monotonic() >= deadline_at:
            _set_state(
                rec,
                "timeout",
                "ERR_DEADLINE",
                f"deadline {deadline_s} s expired mid-run",
                d,
            )
            _log(f"{rec['task_id']}: deadline expired -> timeout")
            return 0
        res = _run_attempt(rec, port, ident)
        if time.monotonic() >= deadline_at:
            # S3.4: the deadline expiring MID-RUN -> timeout even if the
            # attempt technically returned (the manager-side abort is the
            # enforcement half, MAP 3.4; this is the worker's own check —
            # a result that lands after the deadline is a late result).
            _set_state(
                rec,
                "timeout",
                "ERR_DEADLINE",
                f"deadline {deadline_s} s expired mid-run",
                d,
            )
            _log(f"{rec['task_id']}: deadline expired after attempt -> timeout")
            return 0
        if res["ok"]:
            try:
                Path(rec["result_file"]).parent.mkdir(parents=True, exist_ok=True)
                tmp = Path(rec["result_file"]).with_suffix(".tmp")
                tmp.write_text(
                    json.dumps(
                        {
                            "answer": res["answer"],
                            "reasoning": res["reasoning"],
                            "usage": res["usage"],
                        },
                        indent=2,
                    )
                    + "\n"
                )
                os_replace = Path(rec["result_file"])
                tmp.replace(os_replace)
            except OSError as exc:
                _set_state(
                    rec,
                    "failed",
                    "ERR_ENGINE_DOWN",
                    f"could not write {rec['result_file']}: {exc}",
                    d,
                )
                _log(str(exc))
                return 0
            _set_state(rec, "done", None, None, d)
            _log(f"{rec['task_id']}: done (port {port})")
            return 0
        if res["kind"] == "ctx":
            _set_state(rec, "failed", "ERR_CTX_TOO_LONG", res["detail"], d)
            _log(f"{rec['task_id']}: {res['detail']} -> failed ERR_CTX_TOO_LONG")
            return 0
        if res["kind"] == "parse":
            _set_state(rec, "failed", "ERR_PARSE", res["detail"], d)
            _log(f"{rec['task_id']}: {res['detail']} -> failed ERR_PARSE")
            return 0
        # kind == "engine": the S1.5 one-retry path
        attempt_detail.append(res["detail"])
        try:
            doc = H.load_health_doc()
        except H.HealthMissing:
            doc = {"instances": []}
        ni, nport = _pick_retry_instance(doc, rec, d)
        if ni is None:
            _set_state(
                rec,
                "failed",
                "ERR_ENGINE_DOWN",
                "second failure -> failed ERR_ENGINE_DOWN (S1.5): "
                + "; ".join(attempt_detail),
                d,
            )
            _log(f"{rec['task_id']}: no healthy retry target -> failed ERR_ENGINE_DOWN")
            return 0
        rec["instance"] = ni
        rec["port"] = nport
        if ni not in rec["instances_tried"]:
            rec["instances_tried"].append(ni)
        port = nport
        rec["state"] = "engine_retried"
        rec["error"] = "ERR_ENGINE_DOWN"
        rec["detail"] = "engine_retried once on i={} (S1.5): {}".format(
            ni, "; ".join(attempt_detail)
        )
        rec["updated_ts"] = time.time()
        H.write_task(d, rec)
        _log(
            f"{rec['task_id']}: {res['detail']} -> engine_retried on i={ni} (port {nport})"
        )
        time.sleep(POLL_S)
    _set_state(
        rec,
        "failed",
        "ERR_ENGINE_DOWN",
        "second failure -> failed ERR_ENGINE_DOWN (S1.5): " + "; ".join(attempt_detail),
        d,
    )
    _log(f"{rec['task_id']}: second failure -> failed ERR_ENGINE_DOWN")
    return 0


if __name__ == "__main__":
    sys.exit(main())
