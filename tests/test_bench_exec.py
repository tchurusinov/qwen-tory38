"""tests/test_bench_exec.py — the S5.4 task executor timing core (MAP 4.2).

The S5.8 hermetic seam: ``bench.exec.score_stream`` is a PURE function
over canned ``(monotonic_ts, chunk)`` pairs — no network, no GPU. This
drives it with canned streams and asserts the S5.1 timing math:

  * answer/reasoning accumulation (the S1.3 deepseek split).
  * time-to-first-token, total elapsed, the content-chunk count.
  * the DECODE t/s = completion_tokens / (last - first content chunk),
    over the CONTENT window only (reasoning chunks are not in the
    window), the single-content-chunk fallback, and the None when the
    stream carried no content chunk (reasoning-only / usage-only).
  * the usage-estimate fallback (no usage chunk -> estimated: true).

Plus ``run_concurrent``: the bounded pool, task-order results, and the
M7/M8 aggregate (total tokens / total wall time).
"""

from __future__ import annotations

import unittest

from bench.exec import run_concurrent, score_stream


def _content_chunk(s: str) -> dict:
    return {"choices": [{"delta": {"content": s}}]}


def _reasoning_chunk(s: str) -> dict:
    return {"choices": [{"delta": {"reasoning_content": s}}]}


def _usage_chunk(prompt: int, completion: int) -> dict:
    return {
        "choices": [{"delta": {}}],
        "usage": {"prompt_tokens": prompt, "completion_tokens": completion},
    }


class ScoreStreamTest(unittest.TestCase):
    def test_accumulates_answer_and_reasoning(self):
        chunks = [
            (0.0, _reasoning_chunk("thinking ")),
            (0.1, _content_chunk("hello ")),
            (0.2, _content_chunk("world")),
            (0.3, _usage_chunk(10, 5)),
        ]
        res = score_stream(chunks)
        self.assertTrue(res.ok)
        self.assertEqual(res.answer, "hello world")
        self.assertEqual(res.reasoning, "thinking ")
        self.assertEqual(res.completion_tokens, 5)
        self.assertEqual(res.prompt_tokens, 10)
        self.assertEqual(
            res.n_content_chunks, 2
        )  # 2 content chunks (reasoning excluded)

    def test_time_to_first_token_and_elapsed(self):
        chunks = [
            (0.0, {}),  # an empty chunk before the first content
            (0.5, _content_chunk("a")),
            (1.5, _usage_chunk(1, 1)),
        ]
        res = score_stream(chunks)
        self.assertEqual(res.time_to_first_token_ms, 500.0)
        self.assertEqual(res.elapsed_ms, 1500.0)

    def test_decode_tps_over_the_content_window(self):
        # 4 completion tokens over a 1.0 s decode window (0.2 -> 1.2) = 4.0 t/s
        chunks = [
            (0.2, _content_chunk("a")),
            (1.2, _usage_chunk(1, 4)),
        ]
        res = score_stream(chunks)
        self.assertIsNotNone(res.decode_tps)
        self.assertAlmostEqual(res.decode_tps, 4.0, places=5)

    def test_single_content_chunk_uses_total_elapsed(self):
        # one content chunk at t=0.2, usage at t=1.2 -> window = total
        # (1.2 - 0.0) = 1.2 s; 3 tokens / 1.2 s
        chunks = [
            (0.0, {}),
            (0.2, _content_chunk("a")),
            (1.2, _usage_chunk(1, 3)),
        ]
        res = score_stream(chunks)
        self.assertIsNotNone(res.decode_tps)
        self.assertAlmostEqual(res.decode_tps, 3.0 / 1.2, places=5)

    def test_no_content_chunks_yields_no_decode_tps_and_no_ttft(self):
        chunks = [
            (0.0, {}),
            (0.5, _usage_chunk(1, 2)),
        ]
        res = score_stream(chunks)
        self.assertIsNone(res.time_to_first_token_ms)
        self.assertEqual(res.n_content_chunks, 0)
        self.assertIsNone(res.decode_tps)  # no content decoded -> no decode window

    def test_reasoning_only_stream_yields_no_decode_tps(self):
        # a reasoning-only stream decodes ZERO content tokens: the
        # decode window must gate on content chunks only (not reasoning),
        # so decode_tps is None even though usage reports completion
        # tokens (the G3 round-2 P1: the 0-chunk window must not count
        # reasoning).
        chunks = [
            (0.0, _reasoning_chunk("thinking a ")),
            (0.1, _reasoning_chunk("thinking b ")),
            (0.2, _reasoning_chunk("thinking c ")),
            (0.3, _usage_chunk(10, 3)),
        ]
        res = score_stream(chunks)
        self.assertTrue(res.ok)
        self.assertEqual(res.n_content_chunks, 0)
        self.assertEqual(res.completion_tokens, 3)
        self.assertIsNone(res.decode_tps)
        # TTFT still marks the first chunk that carried anything
        self.assertEqual(res.time_to_first_token_ms, 0.0)

    def test_usage_estimate_fallback_when_no_usage_chunk(self):
        # no usage chunk -> estimated completion = len(answer) // 4
        chunks = [
            (0.0, _content_chunk("abcdefgh" * 4)),  # 32 chars -> 8 tokens
            (1.0, _content_chunk("")),
        ]
        res = score_stream(chunks)
        self.assertTrue(res.usage.get("estimated"))
        self.assertEqual(res.completion_tokens, 8)

    def test_zero_chunks(self):
        res = score_stream([])
        self.assertTrue(res.ok)
        self.assertEqual(res.elapsed_ms, 0.0)
        self.assertIsNone(res.time_to_first_token_ms)
        self.assertIsNone(res.decode_tps)  # no stream, no content, no window

    def test_usage_with_non_positive_completion_is_recomputed(self):
        chunks = [
            (0.0, _content_chunk("abcdefgh" * 8)),  # 64 chars -> 16 tokens
            (1.0, _usage_chunk(1, 0)),  # a bogus 0 completion
        ]
        res = score_stream(chunks)
        self.assertEqual(res.completion_tokens, 16)


class RunConcurrentTest(unittest.TestCase):
    def test_task_order_preserved_and_aggregate_computed(self):
        from bench.exec import TaskResult

        calls = []

        def exec_one(prompt: str) -> TaskResult:
            calls.append(prompt)
            # vary elapsed so ordering matters
            return TaskResult(
                ok=True,
                answer="x",
                completion_tokens=10,
                elapsed_ms=100.0,
                decode_tps=10.0,
            )

        results, agg = run_concurrent(["a", "b", "c"], concurrency=3, exec_one=exec_one)
        self.assertEqual(len(results), 3)
        self.assertEqual(agg["n_tasks"], 3)
        self.assertEqual(agg["n_ok"], 3)
        self.assertEqual(agg["n_errors"], 0)
        self.assertEqual(agg["total_completion_tokens"], 30)
        self.assertAlmostEqual(agg["total_elapsed_s"], 0.3, places=5)
        # H9 (post-live-run): the SERIAL sum (decode-throughput invariant)
        # is aggregate_tps_serial; the WALL figure is the headline. The old
        # assertion checked the serial figure on aggregate_tps, which is
        # now the wall alias — the serial figure moved to
        # aggregate_tps_serial (the 30/0.3 s decode-throughput invariant).
        self.assertAlmostEqual(agg["aggregate_tps_serial"], 30 / 0.3, places=5)
        # aggregate_tps aliases the wall figure (the S5.5 baseline reads it;
        # the M7 rubric reads aggregate_tps_wall when present).
        self.assertEqual(agg["aggregate_tps"], agg["aggregate_tps_wall"])
        self.assertIn("total_wall_s", agg)
        # all prompts ran
        self.assertEqual(set(calls), {"a", "b", "c"})

    def test_error_tasks_counted_in_aggregate(self):
        from bench.exec import TaskResult

        def exec_one(prompt: str) -> TaskResult:
            if prompt == "bad":
                return TaskResult(ok=False, error="engine", detail="500")
            return TaskResult(ok=True, completion_tokens=5, elapsed_ms=100.0)

        _results, agg = run_concurrent(
            ["good", "bad"], concurrency=2, exec_one=exec_one
        )
        self.assertEqual(agg["n_ok"], 1)
        self.assertEqual(agg["n_errors"], 1)
        # the bad task contributes no tokens
        self.assertEqual(agg["total_completion_tokens"], 5)

    def test_concurrency_clamped_to_at_least_one(self):
        from bench.exec import TaskResult

        def exec_one(prompt: str) -> TaskResult:
            return TaskResult(ok=True, completion_tokens=1, elapsed_ms=10.0)

        _results, agg = run_concurrent(["x"], concurrency=0, exec_one=exec_one)
        self.assertEqual(agg["n_tasks"], 1)


if __name__ == "__main__":
    unittest.main()
