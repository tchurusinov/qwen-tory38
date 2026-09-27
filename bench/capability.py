"""bench.capability — the MAP 4.9 M9/M10 capability-layer driver.

The S5.2 M9/M10 rows "measure the capability layer (S3.4) and run in both
``tool_execution`` modes (host and engine); the run records the mode in
the row". A capability-layer task is NOT a plain streaming completion:
it runs through the REAL subagent tool loop —
``bin/bonsai2-taskworker.py`` (rec["tools"] == "on" -> _run_tool_loop ->
_stream_chat -> tool-call parse (native array OR text-JSON) ->
``bonsai2_tools.execute`` for ``tool_execution: host``, or a POST to the
instance's ``/tools`` endpoint for ``tool_execution: engine``).

This module is that driver. One call to :func:`run_mode` = ONE capability
task in ONE tool_execution mode:

  * the S5.2 fixture goldens are staged into a per-task workdir
    (``bench/goldens/m9``: the spec-chunk fixture; ``bench/goldens/m10``:
    the 10-file test batch) — the subagent's prompt names the workdir
    paths (the S4.1 workdir-contract template shape);
  * the task is spawned through the harness seam (``cmd_spawn`` with
    ``-i`` / ``--tools on`` / ``--workdir`` / ``--deadline``; the
    taskworker is Popen'd by the spawn, exactly as
    ``tests/test_harness_tools_loop.py`` rigs it: a scripted
    ``tests/fixtures/tool_engine.ToolEngine`` subagent instance on its
    own port + a hand-written health document + a temp results dir +
    the S3.2 env overrides);
  * the driver waits for the terminal record, reads the task record +
    the result file, and returns the capability result
    (``{task_id, tool_execution, state, error, answer, reasoning,
    usage, tool_log, steps, workdir, timing}``).

``timing`` is a ``bench.exec.TaskResult``-shaped dict (the runner's row
contract): ``elapsed_ms`` from spawn to terminal; ``decode_tps`` from
the result's usage when the engine reports completion tokens, else a
bounded estimate from the step count — the capability row still carries
the S5.2 t/s + box-state columns (the box-state snapshot is the
runner's, not this module's).

The S5.8 hermetic seam: the runner's ``mock_cap`` callable (canned
capability results, ``tests/fixtures/bench_mock.py``) replaces the
harness seam for the mock-endpoint run — zero network/GPU, the same
"timings sane" convention as the S5.8 mock executor (the canned timing
is built here, not faked in the fixture).

Stdlib-only top level (the hermetic suite's import rule): the harness
module is loaded LOCALLY (importlib, the tests' convention for the
``bin/`` tooling); the ToolEngine fixture is imported at the package
edge only inside :func:`_spawn` (the live seam), so the module's
top-level import chain stays stdlib.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import io
import json
import os
import shutil
import tempfile
import time
from contextlib import redirect_stdout
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_BIN_DIR = _REPO_ROOT / "bin"

# The capability-row deadlines (S5.2 ctx column -> the task's --deadline;
# the tool-loop bound is the harness's task_max_steps, not the bench's):
# M9 (the 32k spec-chunk) and M10 (the 10-file batch) get the S5.2
# scenario budgets, floored to sane live values.
_DEFAULT_DEADLINE_S = {"M9": 300.0, "M10": 300.0}


class CapabilityError(Exception):
    """A capability task that never reached a terminal record (the S5.4
    "failure is data" rule still applies — the caller records a failed
    row, it does not raise)."""


def _load_harness():
    """Load ``bin/bonsai2_harness.py`` as a module (the tests'
    importlib convention: the harness is the repo's own tooling, not an
    importable package)."""
    path = _BIN_DIR / "bonsai2_harness.py"
    loader = importlib.machinery.SourceFileLoader("bonsai2_harness", str(path))
    spec = importlib.util.spec_from_loader("bonsai2_harness", loader)
    assert spec
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


# --------------------------------------------------------------------------
# fixture staging (the S5.2 workdir contract: the goldens land in the
# task's workdir BEFORE the task is submitted)
# --------------------------------------------------------------------------
def _stage_m9_workdir(root: str | None) -> Path:
    """Stage the M9 fixture into a fresh workdir: the spec-chunk file
    (m9spec.json, the subagent's task statement) + the golden solution
    module + the 3-case suite are NOT pre-written — the spec-chunk task
    is to IMPLEMENT the 3 functions + WRITE the suite + RUN it (the
    S5.2 shape). What lands up front is ONLY the spec (the fixture the
    subagent is asked to implement); the scorer's main-side re-run uses
    the goldens root directly, and the workdir function check reads
    what the subagent's tool loop actually wrote."""
    from bench.score import goldens  # local edge import (the repo package)

    wd = Path(tempfile.mkdtemp(prefix="bonsai2-m9-"))
    spec = goldens.m9_spec(root)
    (wd / "spec.json").write_text(json.dumps(spec, indent=2) + "\n")
    return wd


def _stage_m10_workdir(root: str | None) -> Path:
    """Stage the M10 fixture into a fresh workdir: the 10-file test
    batch (files_000.py .. files_009.py) + the batch instruction. The
    subagent's tool loop runs every file and emits the structured
    report (the S5.2 shape); the scorer's 2/10 main-side re-run reads
    the goldens root, not the workdir copy."""
    from bench.score import goldens  # local edge import (the repo package)

    wd = Path(tempfile.mkdtemp(prefix="bonsai2-m10-"))
    sources = goldens.m10_batch_sources(root)
    for name, src in sources.items():
        (wd / name).write_text(src)
    names = sorted(sources)
    (wd / "BATCH.md").write_text(
        "Run every files_*.py in this workdir with: "
        f"python -m unittest <module> (modules: {', '.join(n.removesuffix('.py') for n in names)}). "
        "Then emit ONE JSON object on a line by itself: "
        '{"files": [{"file": <name>, "status": "pass"|"fail", '
        '"exit_code": <int>, "duration_ms": <int>}, ...] for ALL 10 files, '
        '"summary": {"n_files": 10, "n_pass": <int>, "n_fail": <int>}}.\n'
    )
    return wd


def stage_workdir(sid: str, root: str | None = None) -> Path:
    """The S5.2 fixture workdir for one capability scenario (M9 or M10).
    The LIVE driver (``run_mode``) cleans it in its ``finally``; the
    hermetic mock path (``tests/fixtures/bench_mock.mock_cap``) stages it
    itself and the scorer's file check reads it while it exists."""
    if sid == "M9":
        return _stage_m9_workdir(root)
    if sid == "M10":
        return _stage_m10_workdir(root)
    raise CapabilityError(f"capability staging for unknown scenario {sid!r}")


def _prompt_for(sid: str, workdir: Path) -> str:
    """The capability task prompt (the S4.1 workdir-contract shape: the
    workdir paths are named, the definition of done is a test command +
    expected exit code, and the final-answer contract is stated)."""
    if sid == "M9":
        return (
            f"[M9] Spec-chunk task. Workdir: {workdir}.\n"
            "Read spec.json in the workdir. Implement its 3 functions in "
            f"workdir module textkit.py, then write the 3-case unittest "
            "suite test_textkit.py in the same workdir (one test case per "
            "spec function), then run the suite: "
            f"`python -m unittest test_textkit` with cwd {workdir} — "
            "definition of done: exit code 0.\n"
            "Final answer: state that the suite passed with exit 0."
        )
    if sid == "M10":
        return (
            f"[M10] Test-batch task. Workdir: {workdir}.\n"
            f"Read BATCH.md. Run all 10 files_* modules; definition of "
            "done: every file executed exactly once and its exit code "
            "observed.\n"
            "Final answer: emit the structured report JSON object (per "
            "BATCH.md) on a line by itself."
        )
    raise CapabilityError(f"capability prompt for unknown scenario {sid!r}")


# --------------------------------------------------------------------------
# the result shape
# --------------------------------------------------------------------------
def _timing_from_result(result: dict, elapsed_ms: float) -> dict:
    """The capability row's timing (the S5.2 t/s column): the engine's
    usage when it carried completion tokens (every stream chunk in the
    loop counts as a content-chunk step — the loop's completions are
    content), else None (a loop task with no usage reports no t/s —
    data, not a verdict)."""
    usage = result.get("usage")
    if isinstance(usage, dict):
        pt = usage.get("prompt_tokens")
        ct = usage.get("completion_tokens")
        if isinstance(ct, int) and ct > 0 and elapsed_ms > 0:
            return {
                "ok": True,
                "answer": result.get("answer", ""),
                "reasoning": result.get("reasoning", ""),
                "usage": usage,
                "prompt_tokens": pt if isinstance(pt, int) else None,
                "completion_tokens": ct,
                "time_to_first_token_ms": None,
                "elapsed_ms": elapsed_ms,
                "n_content_chunks": result.get("steps") or 1,
                "decode_tps": ct / (elapsed_ms / 1000.0),
                "error": None,
                "detail": None,
            }
    return {
        "ok": True,
        "answer": result.get("answer", ""),
        "reasoning": result.get("reasoning", ""),
        "usage": {},
        "prompt_tokens": None,
        "completion_tokens": None,
        "time_to_first_token_ms": None,
        "elapsed_ms": elapsed_ms,
        "n_content_chunks": result.get("steps") or 1,
        "decode_tps": None,
        "error": None,
        "detail": None,
    }


# --------------------------------------------------------------------------
# the live seam (the harness tool loop)
# --------------------------------------------------------------------------
def default_deadline_s(sid: str) -> float:
    """The S5.2 scenario budget for the capability task's ``--deadline``
    (the tool-loop bound is the harness's ``task_max_steps``, not the
    bench's — the deadline is the backstop)."""
    return _DEFAULT_DEADLINE_S.get(sid, 300.0)


def capability_loop_spec(sid: str, root: str | None = None) -> tuple[list, dict]:
    """The scripted instance response queue for one capability
    scenario: ``(responses, tools_response)`` — the S5.8 "canned
    M9/M10 responses" shape, shared by the live driver AND the runner's
    hermetic mock (one loop spec, both transports):

      * M9: write_file the module + write_file the suite + bash the
        suite run + the final claim (the S5.2 spec-chunk shape);
      * M10: bash the batch run + the final report JSON (the S5.2
        test-batch shape).

    In ``tool_execution: host`` the host tools EXECUTE these calls in
    the workdir (the workdir file check then verifies the artifacts the
    loop wrote); in ``tool_execution: engine`` every ``/tools`` POST
    answers the canned ``tools_response`` (the fixture's documented
    shape — the engine half is the wire, not the tool semantics: the
    instance container has no shared volume, so the host workdir is NOT
    touched in engine mode — the S3.4 documented v1 limitation,
    recorded in the host-vs-engine comparison row).
    """
    if sid == "M9":
        from bench.score import goldens  # local edge import (the repo package)

        return (
            [
                {
                    "kind": "tool",
                    "tool": "write_file",
                    "params": {
                        "path": "textkit.py",
                        "content": goldens.m9_module_source(root),
                    },
                },
                {
                    "kind": "tool",
                    "tool": "write_file",
                    "params": {
                        "path": "test_textkit.py",
                        "content": goldens.m9_suite_source(root),
                    },
                },
                {
                    "kind": "tool",
                    "tool": "bash",
                    "params": {"cmd": "python -m unittest test_textkit"},
                },
                {"kind": "final", "content": "Suite passed: Ran 3 tests, OK (exit 0)."},
            ],
            {
                "success": True,
                "plain_text_response": "Ran 3 tests in 0.001s\nOK",
                "exit_code": 0,
            },
        )
    if sid == "M10":
        return (
            [
                {
                    "kind": "tool",
                    "tool": "bash",
                    "params": {
                        "cmd": "python -m unittest "
                        + " ".join(f"files_{i:03d}" for i in range(10))
                    },
                },
                {"kind": "final", "content": _m10_canned_report()},
            ],
            {
                "success": True,
                "plain_text_response": "Ran 30 tests in 0.01s\nFAILED (failures=6)",
                "exit_code": 1,
            },
        )
    raise CapabilityError(f"capability loop spec for unknown scenario {sid!r}")


def run_mode(
    sid: str,
    tool_execution: str,
    *,
    root: str | None = None,
    deadline_s: float | None = None,
    max_steps: int = 16,
    wait_s: float | None = None,
    instance_port: int = 30101,
    main_endpoint: str | None = None,
    engine_yaml: str | Path | None = None,
) -> dict:
    """Run ONE capability task (M9 or M10) in ONE ``tool_execution``
    mode, through the REAL harness tool-loop seam (``cmd_spawn`` + the
    Popen'd ``bin/bonsai2-taskworker.py`` — the taskworker's
    ``rec["tools"] == "on"`` path: _run_tool_loop -> _stream_chat ->
    tool-call parse (native array OR text-JSON) ->
    ``bonsai2_tools.execute`` for ``tool_execution: host``, or a POST
    to the instance's ``/tools`` endpoint for ``tool_execution:
    engine``).

    The driver is transport-agnostic: ``instance_port`` is the port of
    the instance that serves the task (the health document carries it;
    the taskworker routes to it). A live caller points it at the box's
    real instance (30101+i); a hermetic caller points it at its
    scripted ``tests/fixtures/tool_engine.ToolEngine`` (the S5.8 rig —
    the same seam ``tests/test_harness_tools_loop.py`` uses: a
    hand-written health document + a temp results dir + the S3.2 env
    overrides, the taskworker Popen'd by cmd_spawn inheriting them).

    main_endpoint: the harness config's main_endpoint (the spawn's
    exit-82 probe target). None = the instance's own URL (the hermetic
    default: the scripted engine answers GET /v1/models with 200 — the
    probe passes with no box main); a live caller passes the box's
    real main (``http://127.0.0.1:30001`` — its 401 wall still
    satisfies the probe).

    engine_yaml: the subagent-engine.yaml the taskworker re-reads at
    start (its wire identity). None = the repo's tracked template
    (``etc/subagent-engine.yaml`` — its ``port_base: 30101`` matches
    the live port math); a hermetic caller passes its own temp YAML.

    Returns the capability result dict (see the module docstring). A
    non-zero spawn exit or a non-terminal task within ``wait_s`` raises
    :class:`CapabilityError` (the caller records the failed row — the
    S5.4 "failure is data" rule).

    wait_s: the terminal-poll window. None (the default) = the task's own
    ``deadline_s`` + a 60 s poll margin (the post-live-run amendment,
    S5.2/S5.9 #5: the live M9 crash was a hardcoded 120 s window racing
    a 300/600 s task deadline — the driver must cover the backstop, not
    a constant smaller than it). An explicit ``wait_s`` wins (the
    hermetic seam).
    """
    if tool_execution not in ("host", "engine"):
        raise CapabilityError(
            f"tool_execution must be host|engine, got {tool_execution!r}"
        )
    if main_endpoint is None:
        main_endpoint = f"http://127.0.0.1:{instance_port}"
    deadline_s = deadline_s or default_deadline_s(sid)
    if wait_s is None:
        wait_s = deadline_s + 60.0

    H = _load_harness()

    _tmp = tempfile.TemporaryDirectory()
    tmp = Path(_tmp.name)
    health_dir = tmp / "run"
    results = tmp / "results"
    cfg_path = tmp / "harness.yaml"
    workdir = stage_workdir(sid, root)

    data = {
        "main_endpoint": main_endpoint,
        "results_dir": str(results),
        "dedup_dir": str(health_dir / "dedup"),
        "dedup_ttl_h": 24,
        "queue_wait_max_s": 10,
        "workspace_dir": str(tmp / "workspaces"),
        "tool_execution": tool_execution,
        "tool_python_bin": "/usr/bin/python3",
        "tool_timeout_s": 60,
        "task_max_steps": max_steps,
    }
    cfg_path.write_text("\n".join(f"{k}: {v}" for k, v in data.items()) + "\n")

    env_over = {
        "BONSAI2_CONFIG": str(cfg_path),
        "BONSAI2_RESULTS_DIR": str(results),
        "BONSAI2_HEALTH_DIR": str(health_dir),
        "BONSAI2_ENGINE_YAML": str(
            engine_yaml or (_REPO_ROOT / "etc" / "subagent-engine.yaml")
        ),
    }
    saved_env = {k: os.environ.get(k) for k in env_over}
    health_dir.mkdir(parents=True, exist_ok=True)
    (health_dir / "health.json").write_text(
        json.dumps(
            {
                "stack": "healthy",
                "instances": [
                    {
                        "i": 0,
                        "port": instance_port,
                        "state": "healthy",
                        "ctx": 262144,
                        "ctx_reduced": False,
                        "slots_in_use": 0,
                        "slots_total": 8,
                    }
                ],
                "main_endpoint": main_endpoint,
                "main_healthy": True,
            }
        )
        + "\n"
    )

    prompt = _prompt_for(sid, workdir)
    spawn_args = [
        "-i",
        prompt,
        "--tools",
        "on",
        "--workdir",
        str(workdir),
        "--deadline",
        str(int(deadline_s)),
    ]
    task_id = None
    try:
        for k, v in env_over.items():
            os.environ[k] = v
        out_buf = io.StringIO()
        with redirect_stdout(out_buf):
            code = H.cmd_spawn(spawn_args, env_over)
        spawn_doc = json.loads(out_buf.getvalue())
        if code != 0:
            raise CapabilityError(
                f"{sid}/{tool_execution}: spawn exit {code}: "
                f"{spawn_doc.get('detail') or spawn_doc.get('reason') or spawn_doc}"
            )
        task_id = spawn_doc.get("task_id")
        if not task_id:
            raise CapabilityError(
                f"{sid}/{tool_execution}: spawn accepted without a task_id: {spawn_doc}"
            )
    finally:
        for k, v in saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    d = H.results_dirs(H.load_config(env_over))
    H.ensure_results_dirs(d)
    t0 = time.monotonic()
    deadline_wait = time.monotonic() + wait_s
    last = None
    while time.monotonic() < deadline_wait:
        last = H.read_task(d, task_id)
        if last.get("state") in H.TERMINAL_STATES:
            break
        time.sleep(0.1)
    if last is None or last.get("state") not in H.TERMINAL_STATES:
        raise CapabilityError(
            f"{sid}/{tool_execution}: task {task_id} not terminal within {wait_s}s "
            f"(last state: {last.get('state') if last else None})"
        )
    elapsed_ms = (time.monotonic() - t0) * 1000.0
    result_file = last.get("result_file")
    result = {}
    if result_file and Path(result_file).is_file():
        result = json.loads(Path(result_file).read_text())
    out = {
        "task_id": task_id,
        "tool_execution": tool_execution,
        "state": last.get("state"),
        "error": last.get("error"),
        "answer": result.get("answer") or "",
        "reasoning": result.get("reasoning") or "",
        "usage": result.get("usage") or {},
        "tool_log": result.get("tool_log") or [],
        "steps": result.get("steps"),
        "workdir": workdir,
        "timing": _timing_from_result(result, elapsed_ms),
    }
    try:
        return out
    finally:
        _tmp.cleanup()
        # H6 (post-live-run): the staged fixture workdir is the driver's
        # to clean — every live capability call leaked a
        # /tmp/bonsai2-m{9,10}-* dir (confirmed in the first live run,
        # the staged M9 workdir survived the crash).
        shutil.rmtree(workdir, ignore_errors=True)


def _m10_canned_report() -> str:
    """The M10 scripted final answer: the S5.2 structured report for the
    golden batch (8 pass, files_003/files_007 fail — the seeded fails,
    matching the M10 golden report the scorer's 2/10 re-run checks)."""
    files = []
    for i in range(10):
        status = "fail" if i in (3, 7) else "pass"
        files.append(
            {
                "file": f"files_{i:03d}.py",
                "status": status,
                "exit_code": 1 if status == "fail" else 0,
                "duration_ms": 240,
            }
        )
    n_pass = sum(1 for f in files if f["status"] == "pass")
    return json.dumps(
        {
            "files": files,
            "summary": {"n_files": 10, "n_pass": n_pass, "n_fail": 10 - n_pass},
        }
    )
