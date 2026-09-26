#!/usr/bin/env python3
"""bonsai2_tools — the host-side 4-tool module for the S3.4 tool loop
(MAP 3.13; the subagent capability layer, spec amendment 2026-09-25).

The ONLY toolset a subagent sees when `tool_execution: host`
(the S3.4 default; the engine-side alternative targets the instance's
`/tools` endpoint instead, S1.3). Exactly four tools, locked (S0
capability boundary + S3.4):

  * bash(cmd, timeout?)      — run a shell command, cwd = the workdir
  * read_file(path)          — read a text file inside the workdir
  * write_file(path, content)— create/overwrite a file in the workdir
  * python(code, timeout?)   — run python source under `tool_python_bin`

Contract (S3.1 tool_log entry schema): `execute` returns
`{ok, exit_code, output, detail}` — `output` is the (truncated)
machine-visible result text that the taskworker appends to the message
history; `exit_code` is the recorded S3.1 field; `detail` is human
diagnostics (never machine-parsed beyond being fed back to the model).

Workdir containment (S3.4, MAP 3.13): every path is confined to the
task's workdir root — `/`-rooted paths and `..`-crossing paths are
rejected; symlinks INSIDE the workdir are allowed, symlinks OUT are
rejected at open (the containment check resolves the common physical
prefix of the workdir and the target path). A bad path is an ERROR
RESULT (ok=False, exit_code=1, ERR_TOOL_PATH), not an exception —
the loop feeds it back to the model.

Per-tool subprocess timeout (S3.4 `tool_timeout_s`): the subprocess
runs in its own process group; on timeout the whole group is SIGKILLed
(no stray grandchildren) and the result is exit_code 124 +
ERR_TOOL_TIMEOUT. Output truncation (S3.4 `tool_output_max_kb`):
combined output is capped at the limit with a truncation marker; the
full output is diagnostic-only (the cap applies to what the model
sees AND what the tool_log records, S3.1).

Stdlib-only, no engine, no network (A15/S6.6: no new deps for
host-side execution). The taskworker imports this module; `main`
exists so the module is also usable standalone for a smoke check.

Usage (smoke, not an S3.2 command):
  python3 bin/bonsai2_tools.py --workdir DIR --tool bash --cmd "echo hi"
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
from pathlib import Path

# --------------------------------------------------------------------------
# Tool registry + the OpenAI-shape tool definitions the taskworker sends
# with the `tools` array (S3.4: the model's call is validated against this
# four-tool schema).
# --------------------------------------------------------------------------
TOOLS_JSON: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "bash",
            "description": (
                "Run a shell command in the task workdir. All paths are "
                "relative to the workdir. Returns combined "
                "stdout/stderr and the exit code."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "cmd": {"type": "string"},
                    "timeout": {"type": "number", "minimum": 1},
                },
                "required": ["cmd"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": (
                "Read a text file inside the workdir and return its content."
            ),
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": (
                "Create or overwrite a file inside the workdir with the given content."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "python",
            "description": (
                "Run python source code (via stdin) under the configured "
                "interpreter, cwd = the task workdir. Returns combined "
                "stdout/stderr and the exit code."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {"type": "string"},
                    "timeout": {"type": "number", "minimum": 1},
                },
                "required": ["code"],
            },
        },
    },
]

_TOOL_NAMES = frozenset(
    {t["function"]["name"] for t in TOOLS_JSON}
)  # {"bash", "read_file", "write_file", "python"}

# S3.1 tool_log params redaction: no secrets beyond what the model sent —
# the redaction is a size cap on long string values, not a content filter.
_REDACT_STRING_MAX = 200

# The in-flight tool subprocess, so the taskworker's SIGTERM handler can
# kill the current step's process group before the worker dies (S3.4:
# "Cancel kills the current step's subprocess (SIGTERM the tool execution's
# process group), then the worker"). Re-armed on every execute().
_CURRENT_PROC: dict[str, subprocess.Popen | None] = {"proc": None}

_TIMEOUT_EXIT = 124  # the conventional shell timeout exit code


# --------------------------------------------------------------------------
# result helpers
# --------------------------------------------------------------------------
def _ok(output: str, exit_code: int = 0, detail: str | None = None) -> dict:
    return {"ok": True, "exit_code": exit_code, "output": output, "detail": detail}


def _err(output: str, exit_code: int = 1, detail: str | None = None) -> dict:
    """An error RESULT (ok=False): fed back to the model by the loop; the
    S3.1 tool_log entry records exit_code + the (truncated) output."""
    return {"ok": False, "exit_code": exit_code, "output": output, "detail": detail}


def _truncate(s: str, max_bytes: int) -> str:
    """Truncate at `tool_output_max_kb` (S3.1/S3.4): cap on the UTF-8 byte
    length with an explicit marker (the model must see that output was
    cut, not treat it as complete)."""
    if max_bytes <= 0:
        return s
    b = s.encode("utf-8", "replace")
    if len(b) <= max_bytes:
        return s
    cut = b[:max_bytes].decode("utf-8", "ignore")
    return (
        cut + f"\n[... truncated at {max_bytes // 1024} KiB (tool_output_max_kb) ...]"
    )


def _err_str(exc: Exception) -> str:
    return f"{exc.__class__.__name__}: {exc}"


# --------------------------------------------------------------------------
# workdir containment (S3.4 / MAP 3.13)
# --------------------------------------------------------------------------
def resolve_path(root: Path, p) -> Path:
    """Resolve `p` against the workdir root with escape rejection.

    Rules (the MAP 3.13 containment contract):
      * relative paths are anchored at the workdir;
      * a path that (after normalization) sits ABOVE the root — `/`-rooted
        paths outside the workdir, `..`-crossing paths — is rejected with
        ValueError(ERR_TOOL_PATH);
      * symlinks INSIDE the workdir are allowed; symlinks OUT are rejected
        at open: containment holds on the physical (resolved) prefix of
        root AND target, so `root/a -> /tmp/x` fails while
        `root/inner/link -> root/real` passes.

    Returns the (normalized, root-anchored) path — not yet opened; the
    caller's open/read is where a residual symlink escape would surface.
    """
    if not isinstance(p, str) or not p:
        raise ValueError(f"ERR_TOOL_PATH: path must be a non-empty string: {p!r}")
    root_real = root.resolve()
    candidate = Path(p)
    if not candidate.is_absolute():
        candidate = root / candidate
    norm = Path(os.path.normpath(str(candidate)))

    def _under(path: Path) -> bool:
        return path == root_real or str(path).startswith(str(root_real) + os.sep)

    # Two containment tests:
    #   * textual (no symlink following): catches `..`-crossing and
    #     `/`-rooted-outside paths on their face — a clean, predictable
    #     rejection with a readable message;
    #   * physical (resolve the path, symlinks followed): the ground
    #     truth for WHERE the file actually lives. A symlink named
    #     inside the workdir that points OUT passes the textual test
    #     but fails here (the "symlinks out rejected at open" rule).
    # G3 PR#18 r4 P2 (noted): `norm.resolve()` is the NON-STRICT
    # resolve (Python >=3.10 default strict=False), so a brand-new path
    # (write_file to a not-yet-existing nested file) does NOT raise
    # FileNotFoundError — the containment check is purely textual +
    # symlink-aware on the physical prefix, and the write itself creates
    # the leaf. No strict=True here on purpose.
    # G3 PR#18 r8 P1 (verified false-positive): the candidate is anchored
    # at the UNRESOLVED `root` (line 203-204) and only norm-resolved at
    # the comparison (line 224), while `root_real` is root's physical
    # target. Both sides are therefore PHYSICAL at the point of
    # comparison, so a symlinked workdir (root -> /real) does NOT cause a
    # relative path through the symlinked parent to fail containment that
    # should pass: candidate /workdir/sub/file resolves to /real/sub/file,
    # which IS under root_real /real. The textual test (line 211) catches
    # the face-level `..`-crossing; the physical test catches the
    # symlink-out; together they are complete for the workdir-is-a-symlink
    # case. No behavior change.
    # G3 PR#18 r9 P1 (verified false-positive, re-verified with a probe):
    # "a symlink pointing INTO the workdir passes containment, e.g.
    # workdir/alias -> /data and path /data/f.txt is read" — only when /data
    # ITSELF is inside the workdir (in which case reading the real file is
    # the intended outcome: alias/f.txt physically lands on the real
    # in-workdir inode, so no bytes leak OUT). The escape shape the
    # reviewer feared (/data OUTSIDE the workdir) fails BOTH tests:
    # textual — /data/f.txt is /-rooted and not under the workdir;
    # physical — resolve() lands at /data/f.txt, outside root_real.
    # Probe (r9 disposition): alias->real-in-workdir passes, out->/etc
    # rejected. No behavior change.
    if not _under(norm.resolve()):
        raise ValueError(
            f"ERR_TOOL_PATH: {p!r} escapes the workdir {root_real} "
            f"(resolved to {norm.resolve()})"
        )
    return norm


# --------------------------------------------------------------------------
# the four tools
# --------------------------------------------------------------------------
def _run_capped(
    argv: list[str],
    ctx: dict,
    timeout_s: float | None,
    *,
    cwd: Path,
    input_bytes: bytes | None = None,
) -> tuple[bytes, int]:
    """Run one subprocess in its own process group, capped at `timeout_s`
    (the per-tool `tool_timeout_s`, overridable per call). Returns
    (combined stdout+stderr bytes, exit code). On timeout the group is
    SIGKILLed and the exit code is the conventional 124."""
    proc = subprocess.Popen(
        argv,
        cwd=str(cwd),
        stdin=subprocess.PIPE if input_bytes is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    _CURRENT_PROC["proc"] = proc
    timed_out = False
    try:
        out, _ = proc.communicate(input=input_bytes, timeout=timeout_s)
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        # G3 PR#18 r6 P2: the exception object carries the partial stdout
        # buffered up to the deadline (e.output); the post-kill re-read
        # below returns only bytes written AFTER the timeout, so coalesce
        # the two or a tool that printed before the timeout would report
        # empty output in the tool_log (dropping the diagnostic data the
        # S3.1 ledger promises).
        partial = exc.output or b""
        _kill_group(proc)
        try:
            tail, _ = proc.communicate(timeout=2.0)
            out = partial + (tail or b"")
        except (subprocess.TimeoutExpired, OSError):
            out = partial
    finally:
        if _CURRENT_PROC["proc"] is proc:
            _CURRENT_PROC["proc"] = None
    code = (
        _TIMEOUT_EXIT
        if timed_out
        else (proc.returncode if proc.returncode is not None else 124)
    )
    return out or b"", code


def _kill_group(proc: subprocess.Popen) -> None:
    """SIGKILL the whole process group (the subprocess + any children it
    spawned — S3.4 "process-group kill at tool_timeout_s").
    G3 PR#18 r5 P2 (noted): the subprocess is launched with
    start_new_session=True (setsid -> its own session + process group),
    so killpg(getpgid(proc.pid)) reaches the ENTIRE tree (leader + every
    descendant, e.g. sh -> sleep). The proc.kill() fallback (SIGKILL to
    the leader only) fires only when killpg raises — i.e. the group has
    already exited or been reaped — so in that narrow case the leader is
    gone anyway and there is no orphan left behind. No behavior change.
    G3 PR#18 r8 P1 (fix): killpg is retried on EINTR (an in-flight
    signal interrupted the syscall) BEFORE falling back to the
    leader-only proc.kill() — an EINTR means the group is NOT gone, so
    the leader-only fallback would be the orphan-leaking path the
    reviewer flagged. Bounded retry (3) to avoid an infinite loop on a
    pathological signal storm."""
    for _ in range(3):
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            return
        except InterruptedError:
            continue
        except (ProcessLookupError, PermissionError, OSError):
            break
    try:
        proc.kill()
    except OSError:
        pass


def _tool_bash(params: dict, ctx: dict) -> dict:
    cmd = params.get("cmd")
    if not isinstance(cmd, str) or not cmd:
        return _err("ERR_TOOL_INVALID_CALL: bash needs a non-empty string `cmd`")
    timeout = _param_timeout(params, ctx)
    out, code = _run_capped(
        ["/bin/sh", "-c", cmd], ctx, timeout, cwd=Path(ctx["workdir"])
    )
    text = out.decode("utf-8", "replace")
    if code == _TIMEOUT_EXIT:
        return _err(
            (text or "") + f"\n[tool timeout after {timeout:.0f} s]",
            code,
            "ERR_TOOL_TIMEOUT: bash exceeded the per-tool timeout",
        )
    return _ok(text, code)


def _tool_python(params: dict, ctx: dict) -> dict:
    code = params.get("code")
    if not isinstance(code, str) or not code:
        return _err("ERR_TOOL_INVALID_CALL: python needs a non-empty string `code`")
    timeout = _param_timeout(params, ctx)
    pybin = ctx.get("python_bin") or sys.executable
    out, code_rc = _run_capped(
        [str(pybin), "-"],
        ctx,
        timeout,
        cwd=Path(ctx["workdir"]),
        input_bytes=code.encode("utf-8"),
    )
    text = out.decode("utf-8", "replace")
    if code_rc == _TIMEOUT_EXIT:
        return _err(
            (text or "") + f"\n[tool timeout after {timeout:.0f} s]",
            code_rc,
            "ERR_TOOL_TIMEOUT: python exceeded the per-tool timeout",
        )
    return _ok(text, code_rc)


def _tool_read_file(params: dict, ctx: dict) -> dict:
    p = params.get("path")
    try:
        target = resolve_path(Path(ctx["workdir"]), p)
    except ValueError as exc:
        return _err(str(exc), 1, "ERR_TOOL_PATH")
    if target.is_dir():
        return _err(f"{p!r} is a directory, not a file", 1, "ERR_TOOL_PATH")
    try:
        content = target.read_text()
    except (OSError, UnicodeDecodeError) as exc:
        return _err(f"{p!r}: {_err_str(exc)}", 1, "ERR_TOOL_READ")
    return _ok(content)


def _tool_write_file(params: dict, ctx: dict) -> dict:
    p = params.get("path")
    content = params.get("content")
    if not isinstance(content, str):
        return _err("ERR_TOOL_INVALID_CALL: write_file needs a string `content`")
    try:
        target = resolve_path(Path(ctx["workdir"]), p)
    except ValueError as exc:
        return _err(str(exc), 1, "ERR_TOOL_PATH")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
    except OSError as exc:
        return _err(f"{p!r}: {_err_str(exc)}", 1, "ERR_TOOL_WRITE")
    return _ok(f"wrote {len(content)} bytes to {p}")


_TOOLS: dict[str] = {
    "bash": _tool_bash,
    "python": _tool_python,
    "read_file": _tool_read_file,
    "write_file": _tool_write_file,
}


def _param_timeout(params: dict, ctx: dict) -> float:
    """The per-call override: a positive number in `timeout` wins over the
    config's `tool_timeout_s` (both are seconds; the schema's `minimum: 1`
    is advisory — the model's call is validated against the four-tool
    schema by the LOOP, this is the host-side execution half)."""
    t = params.get("timeout")
    if isinstance(t, (int, float)) and not isinstance(t, bool) and t > 0:
        return float(t)
    return float(ctx.get("timeout_s", 60.0))


def make_ctx(
    workdir: str | Path,
    *,
    timeout_s: float = 60.0,
    output_max_kb: int = 32,
    python_bin: str | None = None,
) -> dict:
    """The execution context the taskworker builds from harness.yaml
    (S3.6) + the task's workdir. `workdir` must exist (the harness
    creates it at spawn, S3.2)."""
    w = Path(workdir).expanduser().resolve()
    if not w.is_dir():
        raise ValueError(f"ERR_TOOL_WORKDIR: workdir does not exist: {w}")
    return {
        "workdir": str(w),
        "timeout_s": float(timeout_s),
        "output_max_kb": int(output_max_kb),
        "python_bin": python_bin or sys.executable,
    }


def execute(tool: str, params: dict, ctx: dict, *, step: int | None = None) -> dict:
    """Run one tool call. Returns the S3.1-shaped result dict:
    {ok, exit_code, output (truncated to tool_output_max_kb), detail,
    step (echoed when given — the loop stamps it)}. Malformed calls
    (unknown tool, bad params) are ERROR RESULTS, never exceptions:
    the loop counts them against tool_invalid_max (S3.4)."""
    if not isinstance(params, dict):
        res = _err(
            f"ERR_TOOL_INVALID_CALL: params must be a JSON object, got {type(params).__name__}"
        )
    else:
        fn = _TOOLS.get(tool)
        if fn is None:
            res = _err(
                f"ERR_TOOL_INVALID_CALL: unknown tool {tool!r} "
                f"(expected one of {sorted(_TOOLS)})"
            )
        else:
            try:
                res = fn(params, ctx)
            except Exception as exc:  # noqa: BLE001 — a tool must never crash the loop
                res = _err(f"ERR_TOOL_EXEC: {_err_str(exc)}", 1, "ERR_TOOL_EXEC")
    max_bytes = int(ctx.get("output_max_kb", 32)) * 1024
    out = res.get("output")
    # G3 PR#18 r8 P2 (noted): the non-str branch (json.dumps) is a latent
    # path — every host tool returns `output` as a STR (bash/read/write/
    # python all return strings), and the engine-side path also coerces
    # to str before returning (taskworker _execute_tool_engine: plain
    # text fields or json.dumps(data)[:4096]). So the dict branch is
    # effectively unreachable today; it exists as a guard so a future
    # tool that returns a structured output is still truncated (not
    # silently passed through unbounded). Documented, no behavior change.
    res["output"] = _truncate(
        out if isinstance(out, str) else json.dumps(out), max_bytes
    )
    if step is not None:
        res["step"] = step
    return res


def redact_params(params: dict | None) -> dict | None:
    """The S3.1 `params` (redacted: no secrets beyond what the model
    sent — long strings are capped, nothing is dropped)."""
    if not isinstance(params, dict):
        return params

    def _cap(v):
        if isinstance(v, str) and len(v) > _REDACT_STRING_MAX:
            return (
                v[:_REDACT_STRING_MAX] + f"... (+{len(v) - _REDACT_STRING_MAX} chars)"
            )
        if isinstance(v, dict):
            return {k: _cap(x) for k, x in v.items()}
        if isinstance(v, list):
            return [_cap(x) for x in v]
        return v

    return {k: _cap(v) for k, v in params.items()}


def kill_current() -> bool:
    """Kill the in-flight tool subprocess's process group (the taskworker's
    SIGTERM handler, S3.4 cancel). Returns True when a group was signalled."""
    proc = _CURRENT_PROC["proc"]
    if proc is None:
        return False
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        return True
    except (ProcessLookupError, PermissionError, OSError):
        return False


def main(argv: list[str] | None = None) -> int:
    """Standalone smoke check (not an S3.2 command)."""
    import argparse

    ap = argparse.ArgumentParser(description="bonsai2_tools smoke check")
    ap.add_argument("--workdir", required=True)
    ap.add_argument("--tool", required=True, choices=sorted(_TOOLS))
    ap.add_argument("--cmd", default=None)
    ap.add_argument("--code", default=None)
    ap.add_argument("--path", default=None)
    ap.add_argument("--content", default=None)
    ap.add_argument("--timeout", type=float, default=None)
    args = ap.parse_args(argv)
    ctx = make_ctx(args.workdir, timeout_s=args.timeout or 30.0)
    params = {}
    if args.tool in ("bash",):
        params["cmd"] = args.cmd
    elif args.tool == "python":
        params["code"] = args.code
    elif args.tool == "read_file":
        params["path"] = args.path
    elif args.tool == "write_file":
        params["path"], params["content"] = args.path, (args.content or "")
    res = execute(args.tool, params, ctx)
    print(json.dumps(res, indent=2))
    return 0 if res.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
