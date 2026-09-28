"""bench.exec — the S5.4 task executor: streaming chat completions with
token timing from stream timestamps.

The S5.1 "raw throughput" half (t/s decode, t/s prefill, task wall-time)
is measured here: each task is one streaming chat completion against the
pinned OpenAI-compatible endpoint (the S1.3 alias + api_key). The executor
records, per task:

  * the accumulated ``answer`` + ``reasoning`` (the S1.3 deepseek
    split, same wire shape the taskworker's ``_stream_chat`` speaks);
  * the final chunk's ``usage`` when present, else a completion-side
    estimate (flagged ``estimated: true`` — the S4.4 lower-bound
    convention, same as the taskworker);
  * TIMING from stream timestamps: time-to-first-token, total elapsed,
    the content-chunk count, the completion-token count, and the derived
    DECODE t/s (completion_tokens / decode-window). Prefill t/s is
    derived by the caller as prompt_tokens / time-to-first-token.

Design for hermetic testability (S5.8 mock-endpoint run): the timing core
(``score_stream``) is a PURE function over a list of
``(monotonic_ts, chunk_dict)`` pairs — the hermetic suite drives it with
canned chunks and asserts the computed t/s / elapsed / token counts
without a single network call. The real HTTP path (``stream_chat``) is a
thin wrapper that produces those pairs and calls ``score_stream``.

Concurrency: ``run_concurrent`` runs a batch of tasks with a bounded
thread pool (the S5.2 "concurrency" column: M2=8, M7=8*N, M8=4*N) and
aggregates the t/s + 5xx count the M7/M8 rubrics need.

Dependency surface: stdlib + ``requests`` (the S5.4 named non-stdlib dep,
the S6.6 pin). ``requests`` is imported LAZILY inside ``stream_chat`` —
the hermetic timing core (``score_stream`` / ``run_concurrent`` /
``TaskResult``) needs no third-party dep, so the S5.8 mock-endpoint run
stays zero-network/GPU/requests.
"""

from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field

_CHUNK_SOCKET_TIMEOUT_S = 60.0  # per-chunk cap (an idle engine = stuck)


@dataclass
class TaskResult:
    """One task's execution result + timing (the S5.4 executor output).

    ``ok`` False carries ``error`` (a named error: ctx/parse/engine) and
    still records the partial ``timing`` (the S5.2 box-state convention:
    data, not a verdict)."""

    ok: bool
    answer: str = ""
    reasoning: str = ""
    usage: dict = field(default_factory=dict)
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    time_to_first_token_ms: float | None = None
    elapsed_ms: float | None = None
    n_content_chunks: int = (
        0  # content-only chunk count (the decode-window basis; excludes reasoning)
    )
    decode_tps: float | None = None  # None when no content chunk was decoded
    error: str | None = None
    detail: str | None = None
    raw: dict = field(default_factory=dict)


def _chunk_content(chunk: dict) -> tuple[str, str, dict | None]:
    """Extract (content, reasoning, usage) from one SSE chunk. Mirrors the
    taskworker's ``_stream_chat`` delta handling (the S1.3 deepseek split):
    ``choices[0].delta.content`` / ``.reasoning_content`` / the chunk's
    top-level ``usage``."""
    if not isinstance(chunk, dict):
        return "", "", None
    usage = chunk.get("usage")
    choices = chunk.get("choices") or []
    content = ""
    reasoning = ""
    if choices:
        delta = choices[0].get("delta") or {}
        if isinstance(delta, dict):
            if delta.get("content"):
                content = str(delta["content"])
            if delta.get("reasoning_content"):
                reasoning = str(delta["reasoning_content"])
    return content, reasoning, usage if isinstance(usage, dict) else None


def score_stream(chunks: list[tuple[float, dict]]) -> TaskResult:
    """The PURE timing core (S5.8 hermetic): over a list of
    ``(monotonic_ts, chunk)`` pairs, accumulate the answer/reasoning/usage
    and compute the S5.1 timing. ``chunks`` must be in stream order; the
    timestamps are monotonic (seconds).

    Timing definitions (the S5.1 throughput axes):
      * ``time_to_first_token_ms`` — the wall time to the first chunk that
        carries content or reasoning (the prefill boundary; the caller
        derives prefill t/s as prompt_tokens / this).
      * ``elapsed_ms`` — total wall time over the whole stream (the task
        wall-time, S5.1).
      * ``decode_tps`` — completion_tokens / (last - first CONTENT chunk),
        over the content-decode window only (reasoning chunks precede the
        answer and are not in the window). Falls back to
        completion_tokens / total when there is only one content chunk
        (no decode window). 0.0 when completion_tokens is 0. None when the
        stream carried no content chunk at all (reasoning-only /
        usage-only — no content was decoded; the caller derives prefill
        from prompt_tokens / time-to-first-token separately).
    """
    content_parts: list[str] = []
    reasoning_parts: list[str] = []
    usage: dict | None = None
    first_ts: float | None = (
        None  # first content-OR-reasoning chunk (the TTFT boundary)
    )
    content_first_ts: float | None = (
        None  # first CONTENT chunk (the decode-window start)
    )
    content_last_ts: float | None = None  # last CONTENT chunk (the decode-window end)
    n_content_chunks = 0  # content-only chunk count (excludes reasoning)
    start_ts = chunks[0][0] if chunks else 0.0

    for ts, chunk in chunks:
        content, reasoning, chunk_usage = _chunk_content(chunk)
        if chunk_usage:
            usage = chunk_usage
        if (content or reasoning) and first_ts is None:
            first_ts = ts
        if content:
            if content_first_ts is None:
                content_first_ts = ts
            content_last_ts = ts
            n_content_chunks += 1
            content_parts.append(content)
        if reasoning:
            reasoning_parts.append(reasoning)

    answer = "".join(content_parts)
    reasoning = "".join(reasoning_parts)
    end_ts = chunks[-1][0] if chunks else start_ts
    elapsed_ms = (end_ts - start_ts) * 1000.0 if chunks else 0.0
    ttft_ms = (
        (first_ts - start_ts) * 1000.0 if (first_ts is not None and chunks) else None
    )

    if usage is None:
        usage = {
            "prompt_tokens": None,
            "completion_tokens": max(1, len(answer) // 4),
            "estimated": True,
        }
    completion_tokens = usage.get("completion_tokens")
    if not isinstance(completion_tokens, int) or completion_tokens <= 0:
        completion_tokens = max(1, len(answer) // 4)
        usage = dict(usage)
        usage["completion_tokens"] = completion_tokens

    # decode t/s over the CONTENT-decode window (first -> last content
    # chunk): reasoning chunks precede the answer and are NOT part of the
    # content decode window, so the window gates on content chunks only.
    # A single content chunk has no window -> use total elapsed. A stream
    # with NO content chunk at all (reasoning-only / usage-only — 0
    # completion-side tokens of content) has no decode window either ->
    # decode_tps = None (the S5.1 "no content decoded" case; the caller
    # derives prefill from prompt_tokens / time-to-first-token separately,
    # and this task's completion_tokens are the usage/estimate fallback).
    if (
        n_content_chunks >= 2
        and content_last_ts is not None
        and content_first_ts is not None
    ):
        window_s = content_last_ts - content_first_ts
        decode_tps = completion_tokens / window_s if window_s > 0 else 0.0
    elif n_content_chunks == 1 and chunks and content_first_ts is not None:
        window_s = end_ts - start_ts
        decode_tps = completion_tokens / window_s if window_s > 0 else 0.0
    else:
        decode_tps = None

    return TaskResult(
        ok=True,
        answer=answer,
        reasoning=reasoning,
        usage=usage,
        prompt_tokens=usage.get("prompt_tokens"),
        completion_tokens=completion_tokens,
        time_to_first_token_ms=ttft_ms,
        elapsed_ms=elapsed_ms,
        n_content_chunks=n_content_chunks,
        decode_tps=decode_tps,
    )


def stream_chat(
    endpoint: dict,
    prompt: str,
    max_tokens: int | None = None,
    deadline_s: float = 600.0,
) -> TaskResult:
    """One real streaming chat completion against ``endpoint`` (the S1.3
    wire: OpenAI-compatible ``/v1/chat/completions``, the deepseek
    reasoning split). ``endpoint`` is a resolved endpoint dict from
    ``bench.endpoints`` (``{"url", "port", "alias", "api_key", ...}``).
    ``max_tokens`` is the S5.2 runner-enforced completion cap (the ctx
    column). Returns a TaskResult; the HTTP-error paths map to the
    taskworker's S1.5/S3.7 error names (ctx/parse/engine).

    The request URL is ``endpoint["url"]`` (authoritative: the main
    endpoint's url may point at a non-localhost host, the subagent
    pool's is http://127.0.0.1:<port>). The Bearer header carries the
    ``api_key`` when the endpoint has one (the subagent pool does);
    when the api_key is empty (the main endpoint — the S3.2 main-probe
    convention: 401-walled is still UP) no Authorization header is sent.
    """
    import requests  # lazy (the S5.4 named non-stdlib dep; the hermetic timing core needs no requests)

    port = endpoint.get("port")
    url = f"{str(endpoint['url']).rstrip('/')}/v1/chat/completions"
    body: dict = {
        "model": endpoint["alias"],
        "messages": [{"role": "user", "content": prompt}],
        "stream": True,
    }
    if max_tokens is not None:
        body["max_tokens"] = max_tokens
    deadline_at = time.monotonic() + deadline_s
    chunks: list[tuple[float, dict]] = []
    start_ts = time.monotonic()
    headers: dict = {}
    if endpoint.get("api_key"):
        headers["Authorization"] = f"Bearer {endpoint['api_key']}"
    try:
        with requests.post(
            url,
            json=body,
            headers=headers,
            stream=True,
            timeout=_CHUNK_SOCKET_TIMEOUT_S,
        ) as resp:
            if resp.status_code == 400:
                return TaskResult(
                    ok=False,
                    error="ctx",
                    detail=f"engine HTTP 400 (request > ctx, S1.5) on port {port}",
                    elapsed_ms=(time.monotonic() - start_ts) * 1000.0,
                )
            if resp.status_code >= 500:
                return TaskResult(
                    ok=False,
                    error="engine",
                    detail=f"engine HTTP {resp.status_code} on port {port}",
                    elapsed_ms=(time.monotonic() - start_ts) * 1000.0,
                )
            if resp.status_code == 429:
                return TaskResult(
                    ok=False,
                    error="engine",
                    detail=f"engine HTTP 429 (overloaded) on port {port}",
                    elapsed_ms=(time.monotonic() - start_ts) * 1000.0,
                )
            if not (200 <= resp.status_code < 300):
                return TaskResult(
                    ok=False,
                    error="engine",
                    detail=f"engine HTTP {resp.status_code} on port {port}",
                    elapsed_ms=(time.monotonic() - start_ts) * 1000.0,
                )
            for raw in resp.iter_lines():
                if time.monotonic() >= deadline_at:
                    break
                line = (
                    raw.decode("utf-8", "replace").strip()
                    if isinstance(raw, bytes)
                    else str(raw).strip()
                )
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]":
                    break
                try:
                    chunk = json.loads(payload)
                except json.JSONDecodeError:
                    chunks.append((time.monotonic(), {}))
                    return TaskResult(
                        ok=False,
                        error="parse",
                        detail=f"non-JSON SSE payload in stream on port {port}",
                        elapsed_ms=(time.monotonic() - start_ts) * 1000.0,
                    )
                chunks.append((time.monotonic(), chunk))
    except requests.exceptions.Timeout:
        return TaskResult(
            ok=False,
            error="engine",
            detail=f"timeout on port {port} (deadline {deadline_s}s or per-chunk cap)",
            elapsed_ms=(time.monotonic() - start_ts) * 1000.0,
        )
    except (
        requests.exceptions.ConnectionError,
        requests.exceptions.RequestException,
    ) as exc:
        return TaskResult(
            ok=False,
            error="engine",
            detail=f"connection error on port {port}: {exc.__class__.__name__}: {exc}",
            elapsed_ms=(time.monotonic() - start_ts) * 1000.0,
        )

    res = score_stream(chunks)
    if not res.answer and not res.reasoning and res.n_content_chunks == 0:
        res.ok = False
        res.error = "parse"
        res.detail = f"stream ended with no content on port {port}"
    return res


def run_concurrent(
    tasks: list[str],
    concurrency: int,
    exec_one,
) -> tuple[list[TaskResult], dict]:
    """Run a batch of task prompts with a bounded thread pool (the S5.2
    "concurrency" column). ``exec_one(prompt) -> TaskResult`` is the
    per-task callable (the runner wires it to ``stream_chat`` against the
    chosen endpoint). Returns ``(results, agg)`` where ``results`` is in
    TASK order and ``agg`` carries the M7/M8 metrics: total completions,
    total 5xx/error count, total completion_tokens, the SERIAL elapsed
    sum (decode-throughput invariant, ``total_elapsed_s`` /
    ``aggregate_tps_serial``), and the BATCH WALL-CLOCK (``total_wall_s``
    / ``aggregate_tps_wall``) — the M7 "t/s at the ceiling" saturation
    figure, which ``aggregate_tps`` aliases for the S5.5 baseline."""
    concurrency = max(1, int(concurrency))
    results: list[TaskResult | None] = [None] * len(tasks)

    def _wrapped(idx_prompt):
        idx, prompt = idx_prompt
        return idx, exec_one(prompt)

    batch_start = time.monotonic()
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = [pool.submit(_wrapped, (i, p)) for i, p in enumerate(tasks)]
        for fut in as_completed(futures):
            idx, res = fut.result()
            results[idx] = res
    batch_wall_s = time.monotonic() - batch_start

    out: list[TaskResult] = [r for r in results if r is not None]
    n_errors = sum(1 for r in out if not r.ok)
    total_completion_tokens = sum(r.completion_tokens or 0 for r in out if r.ok)
    # NUMERATOR CHOICE (S5.1 H9, G3c round-4 P1): the t/s numerator for BOTH
    # figures below is ``total_completion_tokens`` — the OUTPUT tokens only
    # (completion), NOT prompt+completion. This is settled by the spec's own
    # H9 worked example (SPEC S5.1/S5.5): the live first run's 0.25 was the
    # serial sum "370/1484 s" and the wall figure is "370/161 s" — 370 is the
    # completion-token count (the pre-amendment ``aggregate_tps`` numerator) in
    # BOTH, so serial and wall are LIKE-FOR-LIKE (identical numerator, only
    # the denominator differs: per-task elapsed sum vs batch wall-clock). It
    # is a DECODE-throughput ceiling (output t/s at the concurrency ceiling),
    # not a "total generated tokens incl. prefill" figure — the prefill cost
    # is the separate S5.1 prefill-t/s half, not mixed into this ceiling.
    # aggregate_tps, two denominators (the post-live-run amendment, H9 /
    # S5.1 + S5.5): the M7 "t/s at the ceiling" is a BATCH WALL-CLOCK
    # saturation figure (completion tokens / batch wall) — the live first run's
    # 0.25 was the serial sum (370 tokens / 1484 s of *per-task* elapsed),
    # not the ~161 s wall. The serial sum stays recorded as the
    # decode-throughput invariant (aggregate_tps_serial).
    total_serial_s = sum((r.elapsed_ms or 0.0) for r in out if r.ok) / 1000.0
    aggregate_tps_serial = (
        total_completion_tokens / total_serial_s if total_serial_s > 0 else 0.0
    )
    total_wall_s = batch_wall_s
    aggregate_tps_wall = (
        total_completion_tokens / total_wall_s if total_wall_s > 0 else 0.0
    )
    # `aggregate_tps` stays as the WALL alias (the S5.5 baseline reads it;
    # the M7 rubric reads aggregate_tps_wall when present, falls back to
    # this for pre-amendment aggregates).
    aggregate_tps = aggregate_tps_wall
    agg = {
        "n_tasks": len(out),
        "n_ok": sum(1 for r in out if r.ok),
        "n_errors": n_errors,
        "total_completion_tokens": total_completion_tokens,
        "total_elapsed_s": total_serial_s,
        "total_wall_s": total_wall_s,
        "aggregate_tps": aggregate_tps,
        "aggregate_tps_wall": aggregate_tps_wall,
        "aggregate_tps_serial": aggregate_tps_serial,
    }
    return out, agg
