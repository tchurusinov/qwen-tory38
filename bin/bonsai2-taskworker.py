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

The pool's wire identity (alias + api_key + port base) is re-read from
subagent-engine.yaml at worker start (load_engine_identity) — a config
re-render between spawn and worker start is picked up; a missing/invalid
engine yaml is a worker-start failure: the record goes failed
ERR_ENGINE_DOWN with the detail (the record is still collectable).
"""

from __future__ import annotations

import json
import signal
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bonsai2_harness as H

REQUEST_TIMEOUT_S = 60.0  # per-SSE-chunk socket cap (idle engine = stuck)
POLL_S = 0.1


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
    5 s'). The handler marks the record terminal NOW (the cancel command's
    belt-and-braces also writes it — the lock makes the second write a
    no-op) so the slot frees at signal time, not after the process dies.
    A second signal re-raises the default: the worker exits, and whatever
    state the record is in is what the reaper/cancel sees."""
    global _CANCELLED_SEEN
    if _CANCELLED_SEEN:
        signal.default_int_handler(signum)(signum, frame)
        return
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


def _run_attempt(rec: dict, port: int, ident: dict) -> dict:
    """One streaming chat completion. Returns {"ok": True, ...} or
    {"ok": False, "kind": "ctx"|"parse"|"engine", "detail": ...}."""
    url = f"http://127.0.0.1:{port}/v1/chat/completions"
    body = json.dumps(
        {
            "model": ident["alias"],
            "messages": [{"role": "user", "content": rec.get("prompt", "")}],
            "stream": True,
        }
    ).encode()
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Authorization": f"Bearer {ident['api_key']}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    content_parts: list[str] = []
    reasoning_parts: list[str] = []
    usage: dict | None = None
    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_S) as resp:
            status = resp.status
            for raw in resp:
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]":
                    break
                try:
                    chunk = json.loads(payload)
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
                if chunk.get("usage"):
                    usage = chunk["usage"]
                choices = chunk.get("choices") or []
                if not choices:
                    continue
                delta = choices[0].get("delta") or {}
                if delta.get("content"):
                    content_parts.append(str(delta["content"]))
                if delta.get("reasoning_content"):
                    reasoning_parts.append(str(delta["reasoning_content"]))
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
    if not answer and not reasoning:
        return {
            "ok": False,
            "kind": "parse",
            "detail": f"stream ended with no content (HTTP {status}, port {port})",
        }
    if usage is None:
        usage = {
            "prompt_tokens": None,
            "completion_tokens": max(1, len(answer) // 4),
            "estimated": True,
        }
    return {"ok": True, "answer": answer, "reasoning": reasoning, "usage": usage}


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
    # 3600 s default". Use `is not None`, not truthiness.
    deadline_at = time.monotonic() + (
        float(deadline_s) if deadline_s is not None else float(3600)
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
