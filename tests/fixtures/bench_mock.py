"""tests/fixtures/bench_mock.py — the S5.8 mock-endpoint executor (the
bench's canned task executor; S5.8 "mock-endpoint run: full M1+M2 against
the S3.7 mock, canned streaming responses, zero network/GPU, timings
sane").

``mock_exec(prompt, endpoint) -> bench.exec.TaskResult``: keys the prompt
off its stable S5.2 shape marker (``[M1 task 0]`` / ``[M2 task 3]`` /
``[M5]`` / ``[M8 main]`` ...) and returns a TaskResult built through
``bench.exec.score_stream`` over canned ``(monotonic_ts, chunk)`` pairs —
the S5.8 "timings sane" assertion is real: the timing core runs, t/s is
computed from the stream window, no network, no GPU.

Hermetic-import rule (tests/test_smoke.py): this module is imported by
the hermetic test suite, so its TOP-LEVEL imports are stdlib-only. The
``bench`` package (which carries the S6.6 runtime dep ``requests``) is
imported ONLY locally (inside ``_run``) — the same local-edge convention
the suite documents for ``yaml``. The goldens (the M1 module/suite, the
M4 seeded bug ids, the M5 QA answers) are read straight from disk at
module load (bench/goldens/ is repo fixture data, not an import).

The canned answers are RUBRIC-CORRECT by construction:

  * M1 — the golden module + the golden 3-case suite in ```python fences
    (the M1 rubric: compiles + passes its own 3-case suite).
  * M2 — one ``{"file", "status", "exit_code",
    "duration_ms"}`` JSON object per task (the M2 rubric: report JSON
    parseable + 10/10 files covered — the runner aggregates the task
    reports).
  * M9 / M10 — the canned CAPABILITY result lives in ``mock_cap``
    (the MAP 4.9 hermetic seam, the ``bench.capability.run_mode``
    output shape, both tool_execution modes): M9 writes the golden
    module + suite into a real tmp workdir and its tool_log carries the
    suite run with exit 0 (the M9 rubric: main re-run exit 0 + tool_log
    exit 0 + the 3 spec functions in the workdir); M10 emits the golden
    report JSON (the M10 rubric: parseable + 10/10 covered + 2/10
    re-run agreement). The canned timings are real (elapsed > 0, a
    positive decode t/s).
  * M3 — one ``{"summary", "claims"}`` object (3 claims, short summary)
    (the M3 auto-shape: <= 300 tokens + 3 named claims).
  * M4 — cites all 10 seeded BUG-NN ids (the M4 rubric: >= 80% found).
  * M5 — a JSON array of the 5 golden answers (the M5 rubric: all 5
    binary-scored correct).
  * M6 / M7 / M8 — short one-sentence completions (the t/s shapes; the
    canned streams carry multi-chunk content so a decode window exists).

Usage (S5.8): ``bench/run.py --mock tests.fixtures.bench_mock:mock_exec``.
"""

from __future__ import annotations

import json
import re
import tempfile
import time
from pathlib import Path

# The repo root (this file is tests/fixtures/bench_mock.py): the goldens
# live in <root>/bench/goldens/ (repo fixtures, the S5.3 rule).
_REPO_ROOT = Path(__file__).resolve().parents[2]
_GOLDENS = _REPO_ROOT / "bench" / "goldens"

_M1_MODULE = (_GOLDENS / "m1" / "module.py").read_text()
_M1_SUITE = (_GOLDENS / "m1" / "test_module.py").read_text()
# The MAP 4.9 capability fixtures (the M9 golden module/suite + the M10
# golden report) are read from disk at module load (repo fixture data, not
# an import — the hermetic top-level-stdlib rule).
_M9_MODULE = (_GOLDENS / "m9" / "textkit.py").read_text()
_M9_SUITE = (_GOLDENS / "m9" / "test_textkit.py").read_text()
_M10_REPORT = json.loads((_GOLDENS / "m10" / "expected_report.json").read_text())
_M4_BUG_IDS = [
    b["id"] for b in json.loads((_GOLDENS / "m4" / "bugs.json").read_text())["bugs"]
]
_M5_ANSWERS = [
    str(a) for a in json.loads((_GOLDENS / "m5" / "qa.json").read_text())["answers"]
]


def _chunks(text: str, n: int = 4, gap: float = 0.05) -> list:
    """Split ``text`` into ``n`` SSE content chunks with a final usage
    chunk; timestamps are spaced ``gap`` seconds apart (a non-zero decode
    window -> a real decode t/s, the S5.8 "timings sane" property)."""
    base = time.monotonic()
    parts = [
        text[max(0, i * len(text) // n) : (i + 1) * len(text) // n] for i in range(n)
    ]
    parts = [p for p in parts if p] or [text]
    out: list = []
    for i, p in enumerate(parts):
        out.append((base + i * gap, {"choices": [{"delta": {"content": p}}]}))
    out.append(
        (
            base + len(parts) * gap,
            {
                "choices": [{"delta": {}}],
                "usage": {
                    "prompt_tokens": 32,
                    "completion_tokens": max(1, len(text) // 4),
                },
            },
        )
    )
    return out


def _run(text: str):
    """One canned TaskResult over a canned stream (the S5.8 seam: the
    timing core is real, the responses are canned — zero network/GPU).
    The bench import is LOCAL (the hermetic top-level-stdlib rule,
    tests/test_smoke.py)."""
    from bench.exec import score_stream  # local edge import (S6.6)

    return score_stream(_chunks(text))


def _m1_answer() -> str:
    return (
        "Module:\n```python\n"
        + _M1_MODULE
        + "\n```\n\nSuite:\n```python\n"
        + _M1_SUITE
        + "\n```"
    )


def mock_exec(prompt: str, endpoint: dict):
    """One canned task result for ``prompt`` against ``endpoint``. The
    endpoint is accepted but not used (the mock is endpoint-agnostic —
    every resolved port answers identically; the S5.8 hermetic property:
    zero network/GPU regardless of the S1.6 pool size)."""
    if prompt.startswith("[M10"):  # before "[M1" (the shared prefix)
        # M10's canned TEXT shape (the t/s column; the capability result
        # itself is ``mock_cap`` — the MAP 4.9 hermetic seam).
        obj = {
            "file": "batch_file_00",
            "status": "pass",
            "exit_code": 0,
            "duration_ms": 120,
        }
        return _run("Ran the 10-file batch; report:\n" + json.dumps(obj))
    if prompt.startswith("[M1"):
        return _run(_m1_answer())
    if prompt.startswith("[M2"):
        m = re.search(r"file (\d+)", prompt)
        idx = int(m.group(1)) if m else 0
        obj = {
            "file": f"batch_file_{idx:02d}",
            "status": "pass",
            "exit_code": 0,
            "duration_ms": 120 + idx,
        }
        return _run("Ran the batch file; report:\n" + json.dumps(obj))
    if prompt.startswith("[M3"):
        m = re.search(r"section (\d+)", prompt)
        idx = int(m.group(1)) if m else 0
        obj = {
            "summary": f"Section {idx} summarizes the source material in a bounded summary.",
            "claims": [
                f"claim {idx}-a: the source states a bounded, verifiable fact.",
                f"claim {idx}-b: the source names a second checkable assertion.",
                f"claim {idx}-c: the source gives a third citable datum.",
            ],
        }
        return _run(json.dumps(obj))
    if prompt.startswith("[M4"):
        return _run("Bugs found in the diff: " + ", ".join(_M4_BUG_IDS))
    if prompt.startswith("[M5"):
        return _run(json.dumps(_M5_ANSWERS))
    # M6 control / M7 saturation / M8 (subagent + main) shapes: short
    # one-sentence completions (the t/s shapes — the canned multi-chunk
    # stream gives a real decode window).
    return _run("One-sentence completion of the bench task shape.")


# ----------------------------------------------------------------------
# the MAP 4.9 canned capability result (the bench's M9/M10 hermetic seam)
# ----------------------------------------------------------------------
def mock_cap(sid: str, tool_execution: str, root: str | None = None) -> dict:
    """One canned M9/M10 capability result for ``sid`` in ``tool_execution``
    mode — the ``bench.capability.run_mode`` output shape (the S5.8/4.9
    hermetic seam: the runner's capability row consumes it identically to
    the live tool-loop result; zero network/GPU).

    M9: the tool loop WROTE the golden module + suite into a real tmp
    workdir and ran the suite (the tool_log carries the suite run with
    exit 0 — the S3.1 tool_log entry shape, the taskworker's
    ``_tool_log_entry`` ledger, both host AND engine mode: the marker
    appears in ``params.cmd`` in host mode and in ``output`` in engine
    mode; the M9 rubric matches command-OR-output).

    M10: the tool loop ran the batch and the final answer is the golden
    report JSON (the 10/10 coverage the M10 rubric checks + the 2/10
    main re-run agreement).

    The canned timing is built here (the S5.8 "timings sane" convention —
    the capability row still carries the S5.2 t/s column): elapsed > 0,
    a positive decode t/s.
    """
    if tool_execution not in ("host", "engine"):
        raise ValueError(f"tool_execution must be host|engine, got {tool_execution!r}")
    elapsed_ms = 500.0  # the canned task wall time (the S5.1 t/s column)
    completion_tokens = 120  # the canned loop's completion usage
    usage = {"prompt_tokens": 32, "completion_tokens": completion_tokens}
    timing = {
        "ok": True,
        "answer": "",
        "reasoning": "",
        "usage": usage,
        "prompt_tokens": 32,
        "completion_tokens": completion_tokens,
        "time_to_first_token_ms": None,
        "elapsed_ms": elapsed_ms,
        "n_content_chunks": 4,
        "decode_tps": completion_tokens / (elapsed_ms / 1000.0),
        "error": None,
        "detail": None,
    }
    if sid == "M9":
        wd = Path(tempfile.mkdtemp(prefix="bonsai2-m9-mock-"))
        # the tool loop's artifacts: the golden module + the 3-case suite
        # (the M9 rubric's workdir function check reads these).
        (wd / "textkit.py").write_text(_M9_MODULE)
        (wd / "test_textkit.py").write_text(_M9_SUITE)
        # the S3.1 tool_log: the loop's write_file + the suite run (bash,
        # exit 0). The entry shape is the taskworker's _tool_log_entry —
        # the S5.9 #5 "host-vs-engine comparison": the S3.4 documented v1
        # limitation is that engine mode's /tools answers the suite's
        # output (the plain_text_response lands in "output") while host
        # mode runs the command in the shared workdir; the entry ledger
        # shape is the SAME in both (params.cmd carries the marker +
        # output carries the suite's OK), so the M9 rubric's
        # command-OR-output match holds for the host row AND the engine
        # row (both record the same verdict — the comparison's "agree").
        run_entry = {
            "step": 3,
            "tool": "bash",
            "params": {"cmd": "python -m unittest test_textkit"},
            "exit_code": 0,
            "output": "Ran 3 tests in 0.001s\nOK",
            "duration_ms": 40,
        }
        tool_log = [
            {
                "step": 1,
                "tool": "write_file",
                "params": {"path": "textkit.py", "content": "<module source>"},
                "exit_code": 0,
                "output": "wrote textkit.py",
                "duration_ms": 5,
            },
            {
                "step": 2,
                "tool": "write_file",
                "params": {"path": "test_textkit.py", "content": "<suite source>"},
                "exit_code": 0,
                "output": "wrote test_textkit.py",
                "duration_ms": 5,
            },
            run_entry,
        ]
        return {
            "task_id": f"mock-m9-{tool_execution}",
            "tool_execution": tool_execution,
            "state": "done",
            "error": None,
            "answer": "Suite passed: Ran 3 tests, OK (exit 0).",
            "reasoning": "",
            "usage": usage,
            "tool_log": tool_log,
            "steps": 3,
            "workdir": wd,
            "timing": timing,
        }
    if sid == "M10":
        tool_log = [
            {
                "step": 1,
                "tool": "bash",
                "params": {
                    "cmd": "python -m unittest files_000 files_001 files_002 "
                    "files_003 files_004 files_005 files_006 files_007 "
                    "files_008 files_009"
                },
                "exit_code": 1,
                "output": "Ran 30 tests in 0.01s\nFAILED (failures=6)",
                "duration_ms": 240,
            }
        ]
        return {
            "task_id": f"mock-m10-{tool_execution}",
            "tool_execution": tool_execution,
            "state": "done",
            "error": None,
            "answer": json.dumps(_M10_REPORT),
            "reasoning": "",
            "usage": usage,
            "tool_log": tool_log,
            "steps": 1,
            "workdir": Path(tempfile.mkdtemp(prefix="bonsai2-m10-mock-")),
            "timing": timing,
        }
    raise ValueError(f"mock_cap for unknown scenario {sid!r}")
