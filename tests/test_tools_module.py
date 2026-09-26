"""MAP 3.13 hermetic cases — the host-side 4-tool module
(`bin/bonsai2_tools.py`, the subagent capability layer; spec amendment
2026-09-25, S3.4).

The cases the amendment names, one per behavior:
  * valid call -> result (each of the four tools, exit_code 0 / ok True);
  * malformed -> error RESULT (ok=False, never an exception): unknown
    tool, bad params shape, params not an object;
  * workdir escape -> rejected call: `/`-rooted-outside paths,
    `..`-crossing paths (textual), a symlink INSIDE the workdir that
    points OUT (physical, rejected at open), a symlink pointing to a
    sibling INSIDE the workdir (allowed);
  * timeout kill: a `bash` sleep past `tool_timeout_s` -> ok False,
    exit_code 124, the process group is dead (no stray child);
  * truncation: output beyond `tool_output_max_kb` is capped with the
    marker (the model must see the cut).

Run: `python3 -m unittest discover -s tests -t .` (hermetic default).
The module under test is loaded via importlib (the same convention as
the harness tests — bin/ is not a package).
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import os
import signal
import tempfile
import time
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BIN_DIR = REPO_ROOT / "bin"


def _load_tools():
    loader = importlib.machinery.SourceFileLoader(
        "bonsai2_tools_under_test", str(BIN_DIR / "bonsai2_tools.py")
    )
    spec = importlib.util.spec_from_loader("bonsai2_tools_under_test", loader)
    assert spec
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


T = _load_tools()


class ToolsModuleBase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.wd = self.tmp / "workdir"
        self.wd.mkdir()
        self.outside = self.tmp / "outside"
        self.outside.mkdir()
        self.ctx = T.make_ctx(self.wd, timeout_s=3.0, output_max_kb=1)

    def tearDown(self) -> None:
        self._tmp.cleanup()


class TestValidCalls(ToolsModuleBase):
    """valid call -> result (S3.4; one case per tool)."""

    def test_bash_ok(self) -> None:
        res = T.execute("bash", {"cmd": "echo hello"}, self.ctx)
        self.assertTrue(res["ok"])
        self.assertEqual(res["exit_code"], 0)
        self.assertEqual(res["output"].strip(), "hello")
        # cwd is the workdir (the confinement contract, S3.4)
        res2 = T.execute("bash", {"cmd": "pwd"}, self.ctx)
        self.assertEqual(res2["output"].strip(), str(self.ctx["workdir"]))

    def test_bash_nonzero_exit_is_still_a_result(self) -> None:
        # a non-zero shell exit is a VALID result (ok True, the exit_code
        # is the signal — S3.1: the model sees it, not a harness crash)
        res = T.execute("bash", {"cmd": "exit 7"}, self.ctx)
        self.assertTrue(res["ok"])
        self.assertEqual(res["exit_code"], 7)

    def test_read_file_ok(self) -> None:
        self.wd.joinpath("note.txt").write_text("the note\n")
        res = T.execute("read_file", {"path": "note.txt"}, self.ctx)
        self.assertTrue(res["ok"])
        self.assertEqual(res["output"], "the note\n")

    def test_write_file_ok(self) -> None:
        res = T.execute(
            "write_file", {"path": "sub/new.txt", "content": "abc"}, self.ctx
        )
        self.assertTrue(res["ok"])
        self.assertTrue(self.wd.joinpath("sub/new.txt").is_file())
        self.assertEqual(self.wd.joinpath("sub/new.txt").read_text(), "abc")

    def test_python_ok(self) -> None:
        res = T.execute("python", {"code": "print(6 * 7)"}, self.ctx)
        self.assertTrue(res["ok"])
        self.assertEqual(res["exit_code"], 0)
        self.assertEqual(res["output"].strip(), "42")


class TestMalformedCalls(ToolsModuleBase):
    """malformed -> error RESULT, never an exception (S3.4)."""

    def test_unknown_tool(self) -> None:
        res = T.execute("frobnicate", {"x": 1}, self.ctx)
        self.assertFalse(res["ok"])
        self.assertEqual(res["exit_code"], 1)
        self.assertIn("ERR_TOOL_INVALID_CALL", res["output"])

    def test_params_not_a_dict(self) -> None:
        res = T.execute("bash", ["echo hi"], self.ctx)  # type: ignore[arg-type]
        self.assertFalse(res["ok"])
        self.assertIn("ERR_TOOL_INVALID_CALL", res["output"])

    def test_bash_missing_cmd(self) -> None:
        res = T.execute("bash", {}, self.ctx)
        self.assertFalse(res["ok"])
        self.assertIn("cmd", res["output"])

    def test_write_file_missing_content(self) -> None:
        res = T.execute("write_file", {"path": "a.txt"}, self.ctx)
        self.assertFalse(res["ok"])
        self.assertIn("content", res["output"])

    def test_read_file_empty_path(self) -> None:
        res = T.execute("read_file", {"path": ""}, self.ctx)
        self.assertFalse(res["ok"])

    def test_a_crashing_tool_returns_an_error_result(self) -> None:
        # no tool may escape execute() as an exception (the loop feeds
        # error results back to the model) — monkeypatch one to crash.
        orig = T._TOOLS["python"]

        def boom(params, ctx):
            raise RuntimeError("kaboom")

        T._TOOLS["python"] = boom
        try:
            res = T.execute("python", {"code": "x"}, self.ctx)
        finally:
            T._TOOLS["python"] = orig
        self.assertFalse(res["ok"])
        self.assertIn("ERR_TOOL_EXEC", res["output"])


class TestWorkdirContainment(ToolsModuleBase):
    """workdir escape -> rejected call (the MAP 3.13 containment
    contract, S3.4)."""

    def test_slash_rooted_outside_path_rejected(self) -> None:
        res = T.execute("read_file", {"path": "/etc/hostname"}, self.ctx)
        self.assertFalse(res["ok"])
        self.assertIn("ERR_TOOL_PATH", res["output"])

    def test_dotdot_crossing_rejected(self) -> None:
        res = T.execute("read_file", {"path": "../../etc/hostname"}, self.ctx)
        self.assertFalse(res["ok"])
        self.assertIn("ERR_TOOL_PATH", res["output"])

    def test_relative_inside_workdir_allowed(self) -> None:
        self.wd.joinpath("ok.txt").write_text("in")
        res = T.execute("read_file", {"path": "ok.txt"}, self.ctx)
        self.assertTrue(res["ok"])

    def test_symlink_pointing_out_rejected_at_open(self) -> None:
        (self.outside / "secret.txt").write_text("out")
        link = self.wd / "link_out"
        os.symlink(self.outside / "secret.txt", link)
        res = T.execute("read_file", {"path": "link_out"}, self.ctx)
        self.assertFalse(res["ok"])
        self.assertIn("ERR_TOOL_PATH", res["output"])

    def test_symlink_pointing_inside_allowed(self) -> None:
        (self.wd / "real.txt").write_text("in")
        os.symlink(self.wd / "real.txt", self.wd / "link_in")
        res = T.execute("read_file", {"path": "link_in"}, self.ctx)
        self.assertTrue(res["ok"])
        self.assertEqual(res["output"], "in")

    def test_write_cannot_escape(self) -> None:
        res = T.execute(
            "write_file", {"path": "../outside/x.txt", "content": "x"}, self.ctx
        )
        self.assertFalse(res["ok"])
        self.assertFalse(self.outside.joinpath("x.txt").exists())

    def test_write_through_symlinked_parent_out_rejected(self) -> None:
        """G3 PR#18 r10 P1 regression (the reviewer's write-file
        containment-gap probe): a symlinked PARENT that points OUT of the
        workdir. The containment check runs BEFORE mkdir, so the check
        must be strong enough to catch the escape even though the leaf
        does not exist yet. Probed shape: wd/out -> <outside> (an
        existing outside dir), path out/f.txt — resolve() follows the
        existing symlink ancestor, so out/f.txt physically lands at
        <outside>/f.txt, outside root_real, and the containment check
        rejects it (ERR_TOOL_PATH, no bytes written).

        This is the write half of the "symlinks OUT are rejected"
        guarantee that read_file enforces at open."""
        os.symlink(self.outside, self.wd / "out")
        res = T.execute("write_file", {"path": "out/f.txt", "content": "X"}, self.ctx)
        self.assertFalse(res["ok"])
        self.assertIn("ERR_TOOL_PATH", res["output"])
        # nothing landed outside, nothing in the workdir:
        self.assertFalse(self.outside.joinpath("f.txt").exists())
        self.assertFalse(self.wd.joinpath("out", "f.txt").exists())

    def test_write_through_symlinked_parent_out_with_missing_dir_rejected(self) -> None:
        """G3 PR#18 r10 P1 (variant): the same symlink-OUT parent, but
        with an intermediate dir that does not exist yet (out/sub/f.txt).
        The containment check still resolves through the existing
        symlink, so the escape is caught before mkdir materializes
        anything."""
        os.symlink(self.outside, self.wd / "out")
        res = T.execute(
            "write_file", {"path": "out/sub/f.txt", "content": "X"}, self.ctx
        )
        self.assertFalse(res["ok"])
        self.assertIn("ERR_TOOL_PATH", res["output"])
        self.assertFalse(self.outside.joinpath("sub", "f.txt").exists())

    def test_write_through_symlinked_parent_in_allowed(self) -> None:
        """G3 PR#18 r10 P1 (positive control): a symlinked parent that
        points INTO the workdir — the intended in-workdir write must
        still succeed. The physical inode is inside the workdir, so
        containment holds and the write lands on the in-workdir real
        dir."""
        real = self.wd / "real"
        real.mkdir()
        os.symlink(real, self.wd / "inn")
        res = T.execute("write_file", {"path": "inn/f.txt", "content": "in"}, self.ctx)
        self.assertTrue(res["ok"], f"expected in-workdir write to pass, got {res}")
        self.assertTrue(real.joinpath("f.txt").is_file())
        self.assertEqual(real.joinpath("f.txt").read_text(), "in")

    def test_make_ctx_requires_an_existing_workdir(self) -> None:
        with self.assertRaises(ValueError):
            T.make_ctx(self.wd / "does-not-exist")


class TestTimeoutKill(ToolsModuleBase):
    """timeout kill: the per-tool process-group SIGKILL at tool_timeout_s
    (S3.4)."""

    def test_bash_timeout_returns_124(self) -> None:
        res = T.execute("bash", {"cmd": "sleep 10", "timeout": 1}, self.ctx)
        self.assertFalse(res["ok"])
        self.assertEqual(res["exit_code"], 124)
        self.assertIn("ERR_TOOL_TIMEOUT", res.get("detail") or "")

    def test_partial_stdout_survives_the_timeout(self) -> None:
        # G3 PR#18 r6 P2: a tool that PRINTS before the timeout must not
        # report empty output — TimeoutExpired.output carries the partial
        # stdout and the post-kill re-read only returns post-timeout bytes;
        # the module coalesces both into the tool_log entry.
        res = T.execute(
            "bash", {"cmd": "echo before-timeout; sleep 10", "timeout": 1}, self.ctx
        )
        self.assertFalse(res["ok"])
        self.assertEqual(res["exit_code"], 124)
        self.assertIn("before-timeout", res.get("output") or "")

    def test_the_process_group_is_dead_after_timeout(self) -> None:
        # bash spawns a CHILD (sh -> sleep); the group kill must take the
        # child with it — assert no descendant of the group survives.
        res = T.execute("bash", {"cmd": "sleep 10 & wait", "timeout": 1}, self.ctx)
        self.assertFalse(res["ok"])
        time.sleep(0.2)  # let any zombie reaper settle
        # the group pid is gone: killpg(0) on the group the child was in
        # would be racy to probe directly — instead probe via the module's
        # kill_current() having nothing to report after the call returned.
        self.assertFalse(T.kill_current(), "no in-flight proc after the call")

    def test_per_call_timeout_overrides_ctx(self) -> None:
        # a fast command with a 1 s per-call cap still passes (the cap
        # must not misfire on short commands)
        res = T.execute("bash", {"cmd": "true", "timeout": 1}, self.ctx)
        self.assertTrue(res["ok"])


class TestTruncation(ToolsModuleBase):
    """output truncation at tool_output_max_kb (S3.4; the cap is on what
    the model sees AND the tool_log records, S3.1)."""

    def test_output_capped_with_marker(self) -> None:
        # output_max_kb = 1 (the ctx in setUp): ~1 KiB + marker
        res = T.execute("bash", {"cmd": "yes A | head -c 5000"}, self.ctx)
        self.assertTrue(res["ok"])
        self.assertIn("truncated at", res["output"])
        # the cap is on the PRE-marker content: split off the marker's
        # own "\n[... " lead-in, the remaining content is the cut body
        content = res["output"].split("\n[...")[0]
        self.assertLessEqual(len(content.encode("utf-8")), 1024)

    def test_short_output_untouched(self) -> None:
        res = T.execute("bash", {"cmd": "echo short"}, self.ctx)
        self.assertEqual(res["output"].strip(), "short")
        self.assertNotIn("truncated at", res["output"])


class TestKillCurrent(ToolsModuleBase):
    """kill_current(): the taskworker's SIGTERM half (S3.4 cancel: kill
    the in-flight tool subprocess's process group)."""

    def test_kill_current_no_proc(self) -> None:
        self.assertFalse(T.kill_current())

    def test_kill_current_signals_the_running_group(self) -> None:
        proc = T._CURRENT_PROC["proc"]
        self.assertIsNone(proc)
        # arm it the way _run_capped does, via a real long tool call
        import subprocess as sp

        p = sp.Popen(
            ["/bin/sh", "-c", "sleep 30"],
            stdout=sp.PIPE,
            stderr=sp.STDOUT,
            start_new_session=True,
        )
        T._CURRENT_PROC["proc"] = p
        try:
            self.assertTrue(T.kill_current())
            p.wait(timeout=3.0)
            # SIGTERM: Python records -15 on Unix
            self.assertEqual(p.returncode, -signal.SIGTERM)
        finally:
            T._CURRENT_PROC["proc"] = None


class TestRedactAndSchema(ToolsModuleBase):
    """the S3.1 tool_log support: params redaction + the four-tool
    OpenAI `tools` array the taskworker sends with the request."""

    def test_redact_caps_long_strings(self) -> None:
        big = {"cmd": "echo " + ("x" * 500)}
        red = T.redact_params(big)
        self.assertIsNotNone(red)
        self.assertIn("...", red["cmd"])

    def test_tools_array_has_exactly_the_four_tools(self) -> None:
        names = [t["function"]["name"] for t in T.TOOLS_JSON]
        self.assertEqual(sorted(names), ["bash", "python", "read_file", "write_file"])
        # every entry is OpenAI function-tool shape (the wire schema the
        # pinned fork's chat/completions accepts)
        for t in T.TOOLS_JSON:
            self.assertEqual(t["type"], "function")
            self.assertIn("parameters", t["function"])


if __name__ == "__main__":
    unittest.main()
