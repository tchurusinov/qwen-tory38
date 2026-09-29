"""MAP 6.1-6.6 / S6: the hermetic E2E suite for the one-command installer.

Drives `bin/bonsai2_install.py` through its seams (faked uname/driver/
docker/cgroup/meminfo/systemctl + the main container's SGLang log) against
a scratch opt root + health dir + unit dir — no GPU, no network, no
systemd. This is the S6.7-style "fake uname/driver/…" prereq set plus the
full S6.3 pipeline scenarios, covering the MAP 6.1-6.6 acceptance rows:

  1. first install N=2 (this box: pool 121.63 GiB, SGLang footprint
     69.52 GB from the startup log, N=2 per S1.4)
  2. unchanged re-run = zero systemctl calls, "unchanged (S6.4: no rewrite)"
  3. --instances 0 override (unit stop/remove, exit 0 main-only)
  4. --instances 3 override (unit install, per-instance memory budget in
     the docker flags)
  5. main-stack-dead fallback (S2.6 default 76.4 + WARN_MAIN_STACK_DEAD)
  6. live-cgroup fallback (alive, no WARN)
  7. live-margin OOM -> exit 4 (main_only_reason=ERR_VRAM_OOM, S2.4)
  8. settled partial -> exit 0 status "partial" (S6.3: exit 3 is arch
     mismatch, not partial)
  9. footprint drift 59.52 -> 69.52 -> rewrite with the drift named in
     the report warnings (S6.4)
  10. state rotation (last 5 kept, S6.5)
  11. rollback (n restored to 1, unit @1 removed, manager restarted, S6.5)
  12. --refresh-main (re-detect + N + quick bench via the BONSAI2_BENCH_CONFIG
     env, S6.5)
  + CLI wiring: --dry-run (no writes), --rollback

Run: `python3 -m unittest tests.test_install_e2e` from the repo root.
"""

import contextlib
import importlib.util
import io
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
_TOOL = REPO_ROOT / "bin" / "bonsai2_install.py"


def _load_tool():
    spec = importlib.util.spec_from_file_location("bonsai2_install_e2e", str(_TOOL))
    assert spec and spec.loader, f"could not load {_TOOL}"
    mod = importlib.util.module_from_spec(spec)
    # register before exec: the tool's `from __future__ import annotations`
    # + @dataclass resolve string annotations via sys.modules[cls.__module__].
    sys.modules["bonsai2_install_e2e"] = mod
    spec.loader.exec_module(mod)
    return mod


inst = _load_tool()
Seams = inst.Seams

# The live box numbers (probed 2026-09-29 against the real SGLang log):
# pool 124546 MiB = 121.63 GiB; available_gpu_mem=52.11 GB post fresh load
# -> footprint 69.52 GB; N = floor((121.63 - 69.52 - 8.0) / 14.78) = 2.
POOL_MIB = 124546
POOL_BYTES = POOL_MIB * 1024 * 1024
POOL_LINE = (
    "Failed to get GPU memory capacity from nvidia-smi. Falling back to "
    "torch.cuda.mem_get_info(). Reported total GPU memory per device "
    f"(MiB): [{POOL_MIB}], using min: {POOL_MIB} MiB."
)


def _avail_line(available_gb: float) -> str:
    return (
        "max_total_num_tokens=98304, context_len=262144, "
        f"available_gpu_mem={available_gb:.2f} GB"
    )


AVAIL_69_52 = 52.11  # -> footprint 69.52, N=2
AVAIL_59_52 = 62.11  # -> footprint 59.52, N=3 (the S6.7 +10 GB drift input)

IMAGE_TAG = "bonsai2-server:1.0"
IMAGE_SHA = (
    "sha256:" + "6b4cd79f492d12ed72661022d8803fe91cf2bc94fb0963b05a6bdafac969ea53"
)


class Clock:
    """A seam now() that advances so consecutive installs get distinct
    state-file timestamps (S6.5 rotation needs distinct ts tags)."""

    def __init__(self, start: float = 1_759_000_000.0):
        self._t = start

    def __call__(self) -> float:
        self._t += 120.0
        return self._t


class FakeSystemd:
    def __init__(self):
        self.calls: list[list[str]] = []

    def __call__(self, args: list[str]) -> int:
        self.calls.append(list(args))
        return 0


class FakeRunPython:
    def __init__(self, rc: int = 0):
        self.calls: list[dict] = []
        self.rc = rc

    def __call__(self, python: str, script: str, argv: list[str], env=None):
        self.calls.append(
            {"python": python, "script": script, "argv": argv, "env": env}
        )

        class _R:
            returncode = self.rc
            stdout = ""
            stderr = ""

        return _R()


def make_seams(
    tmp: Path,
    *,
    available_gb: float = AVAIL_69_52,
    logs: bool = True,
    container_id: str | None = "deadbeef",
    cgroup_bytes: float | None = None,
    image_match: bool = True,
    docker_ok: bool = True,
    arch: str = "aarch64",
    driver: int | None = 580,
    python_version=(3, 12),
    disk_free_gb: float = 120.0,
    os_id: str = "24.04",
    mem_bytes: float = POOL_BYTES,
    scope: str = "system",
    clock: "Clock | None" = None,
) -> tuple[Seams, FakeSystemd, FakeRunPython, Clock]:
    s = Seams()
    s.repo_root = REPO_ROOT
    s.system_unit_dir = tmp / "units"
    s.user_unit_dir = tmp / "units-user"
    s.is_root = lambda: True
    s.uname_m = lambda: arch
    s.nvidia_smi_driver = lambda: driver
    s.docker_version_ok = lambda: docker_ok
    s.python_version = lambda: python_version
    s.df_free_gb = lambda: disk_free_gb
    s.os_release_id = lambda: os_id
    s.mem_total_bytes = lambda: mem_bytes

    if logs:

        def docker_logs(container: str, tail: int = 0) -> list[str]:
            return [POOL_LINE, _avail_line(available_gb)]

    else:

        def docker_logs(container: str, tail: int = 0) -> list[str]:
            return []

    s.docker_logs = docker_logs
    s.docker_container_id = (
        (lambda name: container_id) if container_id else (lambda name: None)
    )
    s.cgroup_memory_current = (
        (lambda flat: cgroup_bytes) if cgroup_bytes is not None else (lambda flat: None)
    )
    s.docker_image_id = lambda tag: IMAGE_SHA if image_match else None
    s.main_probe = lambda url, path, timeout_s=5.0: True
    sc = FakeSystemd()
    s.systemctl = sc
    s.sc = lambda scope_, *args: sc([str(a) for a in args])
    run = FakeRunPython()
    s.run_python = run
    s.sleep = lambda _s: None
    clock = clock if clock is not None else Clock()
    s.now = clock
    return s, sc, run, clock


def _write_health(health_dir: Path, doc: dict) -> None:
    health_dir.mkdir(parents=True, exist_ok=True)
    (health_dir / "health.json").write_text(json.dumps(doc))


def _full_doc(n: int) -> dict:
    return {
        "stack": "healthy",
        "instances": [
            {"i": i, "port": 30101 + i, "state": "healthy"} for i in range(n)
        ],
        "main_endpoint": "http://localhost:30001",
        "main_healthy": True,
    }


class _E2EBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="bonsai2-install-e2e-")
        self.tmp = Path(self._tmp.name)
        self.opt = self.tmp / "opt"
        self.run_dir = self.tmp / "run"

    def tearDown(self):
        self._tmp.cleanup()

    def install(self, s, **kw):
        return inst.install(
            s,
            opt_root=self.opt,
            health_dir=self.run_dir,
            scope="system",
            python="/usr/bin/python3",
            **kw,
        )

    def engine_on_disk(self) -> str:
        return (self.opt / "etc" / "subagent-engine.yaml").read_text()

    def serving_on_disk(self) -> str:
        return (self.opt / "etc" / "serving.yaml").read_text()

    def state_files(self) -> list[Path]:
        return sorted((self.opt / "etc").glob("state-*.yaml"))


class TestSglangLogParser(unittest.TestCase):
    """S6.3 step 4 primary: pool - available from the startup log."""

    def test_live_box_numbers(self):
        parsed = inst.parse_sglang_footprint([POOL_LINE, _avail_line(AVAIL_69_52)])
        self.assertIsNotNone(parsed)
        footprint, source = parsed
        self.assertAlmostEqual(footprint, 69.52, delta=0.01)
        self.assertIn("pool", source)

    def test_missing_pool_line(self):
        self.assertIsNone(inst.parse_sglang_footprint([_avail_line(AVAIL_69_52)]))

    def test_missing_available(self):
        self.assertIsNone(inst.parse_sglang_footprint([POOL_LINE]))

    def test_degenerate_pair_rejected(self):
        # available > pool: a stale pre-load pair must not parse.
        self.assertIsNone(inst.parse_sglang_footprint([POOL_LINE, _avail_line(130.0)]))


class TestPrereqs(_E2EBase):
    """MAP 6.1 / S6.2: each hard-fail row faked -> named ERR + exit code +
    NOTHING written under the opt root (atomic); the warn rows continue."""

    def _prereqs(self, **overrides):
        s, _sc, _run, _clock = make_seams(self.tmp, **overrides)
        return s

    def _run(self, s):
        return self.install(s)

    def test_all_rows_pass(self):
        code, report = self._run(self._prereqs())
        self.assertEqual(code, 0)
        self.assertNotIn("prereq-fail", report["status"])

    def test_arch_x86_64_is_err_arch_exit_3(self):
        code, report = self._run(self._prereqs(arch="x86_64"))
        self.assertEqual(code, 3)
        self.assertEqual(report["status"], "prereq-fail")
        self.assertIn("ERR_ARCH", json.dumps(report["errors"]))
        self.assertFalse(
            self.opt.exists(), "atomic: nothing written under the opt root"
        )

    def test_driver_low_is_err_driver_exit_2(self):
        code, report = self._run(self._prereqs(driver=570))
        self.assertEqual(code, 2)
        self.assertIn("ERR_DRIVER", json.dumps(report["errors"]))
        self.assertFalse(self.opt.exists())

    def test_no_driver_parse_is_err_driver(self):
        code, report = self._run(self._prereqs(driver=None))
        self.assertEqual(code, 2)
        self.assertIn("ERR_DRIVER", json.dumps(report["errors"]))

    def test_docker_missing_is_err_docker(self):
        code, report = self._run(self._prereqs(docker_ok=False))
        self.assertEqual(code, 2)
        self.assertIn("ERR_DOCKER", json.dumps(report["errors"]))

    def test_python_39_is_err_python(self):
        code, report = self._run(self._prereqs(python_version=(3, 9)))
        self.assertEqual(code, 2)
        self.assertIn("ERR_PYTHON", json.dumps(report["errors"]))

    def test_low_disk_is_err_disk(self):
        code, report = self._run(self._prereqs(disk_free_gb=10.0))
        self.assertEqual(code, 2)
        self.assertIn("ERR_DISK", json.dumps(report["errors"]))

    def test_warn_rows_continue_exit_0(self):
        # 90 GiB < 96 GiB matrix + a non-24.04 os: both warn rows, no fails.
        s = self._prereqs(mem_bytes=90 * 1024**3, os_id="22.04")
        # N recomputes to 0 at 90 GiB (budget 12.48 < 14.78): a settled
        # main-only document so check_health exits the wait instantly.
        _write_health(
            self.run_dir, {"instances": [], "main_only_reason": "budget_zero"}
        )
        code, report = self._run(s)
        self.assertEqual(code, 0)
        self.assertEqual(report["n_auto"], 0, "N recomputed down on the 90 GiB box")
        joined = " ".join(report["warnings"])
        self.assertIn("WARN_MEMORY_BELOW_MATRIX", joined)
        self.assertIn("WARN_OS_VERSION", joined)


class TestReviewRound1Fixes(_E2EBase):
    """Regression tests for the G3c round-1 disposition (PR #36, 2026-09-30).
    One test pins each fixed finding so a later re-review cannot re-flag it."""

    def test_rollback_on_bare_seams_instance(self):
        """P1 r1 (WAIVED-false-positive, pinned): rollback() calls
        seams.sc(...) / seams.unit_dir(...) as INSTANCE methods on a real
        Seams instance. make_seams masked the (non-)bug by assigning a
        bound s.sc lambda; the REAL CLI path is a bare Seams(). Drive
        rollback() with an un-patched instance: no AttributeError, and
        sc() reaches the (faked) systemctl."""
        s1, _sc, _run, _clock = make_seams(self.tmp, clock=Clock())
        _write_health(self.run_dir, _full_doc(1))
        code, _ = self.install(s1, instances=1)
        self.assertEqual(code, 0)
        state_a = self.state_files()[0]

        s_bare = inst.Seams()  # the un-patched real instance
        calls: list[list[str]] = []
        s_bare.systemctl = lambda args: (calls.append(list(args)), 0)[1]
        s_bare.system_unit_dir = self.tmp / "units-bare"
        code, report = inst.rollback(s_bare, state_a, self.opt, "system")
        self.assertEqual(code, 0)
        self.assertEqual(report["status"], "rolled-back")
        self.assertTrue(
            any("daemon-reload" in c for c in calls), "seams.sc reached systemctl"
        )
        self.assertTrue(any("restart" in c and "bonsai2-sglang" in c for c in calls))

    def test_as_sha256_normalizes_forms(self):
        """P1 r2 (FIXED): _as_sha256 guarantees the sha256: prefix the
        report slice + the pin write rely on. Empty stays empty; a bare
        64-hex digest gets the prefix; an already-prefixed digest is
        unchanged."""
        bare64 = "6b4cd79f492d12ed72661022d8803fe91cf2bc94fb0963b05a6bdafac969ea53"
        self.assertEqual(inst._as_sha256(""), "")
        self.assertEqual(inst._as_sha256(f"sha256:{bare64}"), f"sha256:{bare64}")
        self.assertEqual(inst._as_sha256(bare64), f"sha256:{bare64}")

    def test_report_renders_unpinned_digest_gracefully(self):
        """P1 r2 (FIXED): the human report render must not crash or show a
        bare '@ ...' when the image digest is empty (a no-local-image
        first install with an unpinned template)."""
        report = {
            "status": "stack-ready",
            "n_instances": 1,
            "n_auto": 1,
            "n_override": None,
            "budget_gb_remaining": 0.0,
            "instances": [{"i": 0, "port": 30101, "state": "planned"}],
            "main": {
                "endpoint": "http://localhost:30001",
                "footprint_gb": 69.52,
                "detected_by": "sglang log",
            },
            "image": {"tag": "bonsai2-server:1.0", "digest": "", "action": "skipped"},
            "model": {"action": "verified"},
            "config": {"actions": ["unchanged"]},
            "units": {"actions": []},
            "health": {},
            "warnings": [],
            "commands": {"status": "bonsai2-status"},
            "config_paths": ["/opt/bonsai2/etc/subagent-engine.yaml"],
            "scope": "system",
        }
        out = io.StringIO()
        with contextlib.redirect_stderr(out):
            inst._print_report_human(report)
        self.assertIn("unpinned (no digest recorded)", out.getvalue())

    def test_check_health_full_names_count_mismatch(self):
        """P2 r1 (FIXED): a settled HEALTHY document whose instance count
        differs from expected_n (a re-install moved N, the manager has not
        re-converged yet) still returns 'full', but the warning names both
        counts so the S6.3 report is explicit."""
        d = self.run_dir
        d.mkdir(parents=True, exist_ok=True)
        doc = {
            "stack": "healthy",
            "instances": [{"i": 0, "port": 30101, "state": "healthy"}],
            "main_healthy": True,
        }
        (d / "health.json").write_text(json.dumps(doc))
        outcome, _doc, warns = inst.check_health(
            health_dir=d,
            expected_n=3,
            main_url="http://localhost:30001",
            main_probe_path="/v1/models",
            main_probe=lambda *a, **k: True,
            sleep=lambda s: None,
        )
        self.assertEqual(outcome, "full")
        joined = " ".join(warns)
        self.assertIn("WARN_HEALTH_SETTLING", joined)
        self.assertIn("1", joined)  # the running count
        self.assertIn("3", joined)  # the expected count

    def test_df_free_gb_uses_gib_convention(self):
        """P2 r3 (FIXED): the disk floor uses the box-wide GiB (1024**3)
        convention (S1.4 pool, the S6.2 matrix), not 1e9 decimal GB —
        otherwise MIN_DISK_FREE_GB=20.0 would mean 18.63 GiB."""

        class _R:
            returncode = 0
            stdout = "Filesystem 1B-blocks Used Avail Use% Mounted\n /dev/x 100 100 200 10% /opt\n"

        orig = inst.subprocess.run
        inst.subprocess.run = lambda *a, **k: _R()
        try:
            val = inst._df_free_gb_real("/opt")  # fields[3] = 200 bytes
        finally:
            inst.subprocess.run = orig
        self.assertAlmostEqual(val, 200 / 1024**3, places=9)


class TestInstallE2E(_E2EBase):
    """The S6.3 pipeline scenarios (the handoff's 12-scenario suite)."""

    def test_01_first_install_n2(self):
        """Scenario 1: first install N=2 on the live box numbers."""
        s, _sc, _run, _clock = make_seams(self.tmp)
        _write_health(self.run_dir, _full_doc(2))
        code, report = self.install(s)
        self.assertEqual(code, 0)
        self.assertEqual(report["status"], "stack-ready")
        self.assertEqual(report["n_instances"], 2)
        self.assertEqual(report["n_auto"], 2)
        self.assertAlmostEqual(report["main"]["footprint_gb"], 69.52, delta=0.01)
        self.assertTrue(report["main"]["alive"])
        self.assertIn("pool", report["main"]["detected_by"])
        self.assertEqual(report["main"]["endpoint"], "http://localhost:30001")
        self.assertEqual(report["main"]["container"], "qwen38-sglang")
        self.assertEqual(report["image"]["action"], "skipped (digest match)")
        # the four YAMLs + the state file landed
        for name in (
            "subagent-engine.yaml",
            "serving.yaml",
            "harness.yaml",
            "bench.yaml",
        ):
            self.assertTrue((self.opt / "etc" / name).is_file())
        self.assertTrue(re.search(r"footprint_gb: 69\.52", self.serving_on_disk()))
        # units installed (2 instances + the manager)
        for i in (0, 1):
            self.assertTrue(
                (self.tmp / "units" / f"bonsai2-server@{i}.service").is_file()
            )
        self.assertTrue((self.tmp / "units" / "bonsai2-sglang.service").is_file())
        # /run/bonsai2 subdirs created (S6.3 step 4 tail)
        for sub in ("results", "dedup", "bench", "workspaces", "control"):
            self.assertTrue((self.run_dir / sub).is_dir())
        # the S3.1 one-document contract: the command surface summary
        self.assertEqual(report["commands"]["status"], "bonsai2-status")
        # a state file was written
        self.assertEqual(len(self.state_files()), 1)

    def test_02_unchanged_rerun_zero_systemctl(self):
        """Scenario 2: re-run with no drift -> zero systemctl calls,
        'unchanged (S6.4: no rewrite)', no YAML rewrite, no restart."""
        s1, sc, _run, _clock = make_seams(self.tmp)
        _write_health(self.run_dir, _full_doc(2))
        code, report = self.install(s1)
        self.assertEqual(code, 0)
        srv_before = self.serving_on_disk()
        eng_before = self.engine_on_disk()
        calls_before = len(sc.calls)

        s2, _sc, _run2, _clock2 = make_seams(self.tmp)
        code, report = self.install(s2)
        self.assertEqual(code, 0)
        self.assertEqual(report["status"], "stack-ready")
        self.assertIn("unchanged (S6.4: no rewrite)", report["config"]["actions"])
        # zero NEW systemctl calls: no daemon-reload, no manager restart
        self.assertEqual(
            sc.calls[calls_before:],
            [],
            "zero systemctl calls on the unchanged re-run "
            "(the first install's daemon-reload/restart are expected)",
        )
        self.assertEqual(self.serving_on_disk(), srv_before, "no YAML rewrite")
        self.assertEqual(self.engine_on_disk(), eng_before, "no YAML rewrite")
        joined = " ".join(report["units"]["actions"])
        self.assertIn("bonsai2-sglang: unchanged", joined)

    def test_03_instances_0_override(self):
        """Scenario 3: --instances 0 -> stop + remove both units, exit 0
        main-only (the S0 phased-rollout flag, the programmatic int path)."""
        s1, _sc, _run, _clock = make_seams(self.tmp)
        _write_health(self.run_dir, _full_doc(2))
        code, _r = self.install(s1)
        self.assertEqual(code, 0)
        _write_health(
            self.run_dir, {"instances": [], "main_only_reason": "budget_zero"}
        )
        s2, sc2, _run2, _clock2 = make_seams(self.tmp)
        code, report = self.install(s2, instances=0)  # int, not str
        self.assertEqual(code, 0)
        self.assertEqual(report["status"], "main-only")
        self.assertEqual(report["n_instances"], 0)
        for i in (0, 1):
            self.assertFalse(
                (self.tmp / "units" / f"bonsai2-server@{i}.service").exists()
            )
        stopped = [c for c in sc2.calls if "stop" in c]
        self.assertEqual(len(stopped), 2, "both instance units stopped on the shrink")

    def test_04_instances_3_override(self):
        """Scenario 4: --instances 3 -> unit @2 installed, the per-instance
        memory budget in the docker flags, port math 30101+i."""
        s1, _sc, _run, _clock = make_seams(self.tmp)
        _write_health(self.run_dir, _full_doc(3))
        code, report = self.install(s1, instances=3)
        self.assertEqual(code, 0)
        self.assertEqual(report["n_instances"], 3)
        self.assertEqual(report["n_auto"], 2, "S1.4 still computed 2; the override won")
        for i, port in ((0, 30101), (1, 30102), (2, 30103)):
            text = (self.tmp / "units" / f"bonsai2-server@{i}.service").read_text()
            self.assertIn(f"--port {port}", text)
            self.assertIn("--memory 14.78g --memory-swap 14.78g", text)
            self.assertIn(
                "--model /opt/bonsai2/models/bonsai-2-27b-gguf-PQ2_0.gguf", text
            )
        self.assertEqual(len(self.state_files()), 1)

    def test_05_main_stack_dead_fallback(self):
        """Scenario 5: dead main stack -> S2.6 default 76.4 + WARN_MAIN_STACK_DEAD,
        install continues (the S6.4 main-only fallback path)."""
        s, _sc, _run, _clock = make_seams(self.tmp, logs=False, container_id=None)
        _write_health(self.run_dir, _full_doc(2))
        code, report = self.install(s)
        self.assertEqual(code, 0)
        self.assertAlmostEqual(report["main"]["footprint_gb"], 76.4, delta=0.01)
        self.assertFalse(report["main"]["alive"])
        self.assertTrue(any("WARN_MAIN_STACK_DEAD" in w for w in report["warnings"]))
        self.assertEqual(report["n_auto"], 2)

    def test_06_live_cgroup_fallback(self):
        """Scenario 6: no SGLang log but a live cgroup -> the cgroup read,
        alive, NO WARN_MAIN_STACK_DEAD."""
        cgroup = 69.52 * 1024**3
        s, _sc, _run, _clock = make_seams(self.tmp, logs=False, cgroup_bytes=cgroup)
        _write_health(self.run_dir, _full_doc(2))
        code, report = self.install(s)
        self.assertEqual(code, 0)
        self.assertAlmostEqual(report["main"]["footprint_gb"], 69.52, delta=0.01)
        self.assertTrue(report["main"]["alive"])
        self.assertFalse(any("WARN_MAIN_STACK_DEAD" in w for w in report["warnings"]))
        self.assertEqual(report["n_auto"], 2)

    def test_07_live_margin_oom_exit_4(self):
        """Scenario 7: planning granted N >= 1 but the manager's i=0
        allocation OOM (health.json main_only_reason=ERR_VRAM_OOM, S2.4)
        -> exit 4, the spec's live-margin OOM row."""
        s, _sc, _run, _clock = make_seams(self.tmp)
        _write_health(
            self.run_dir, {"instances": [], "main_only_reason": "ERR_VRAM_OOM"}
        )
        code, report = self.install(s)
        self.assertEqual(code, 4)
        self.assertEqual(report["status"], "oom")
        self.assertEqual(report["n_instances"], 2, "planning granted N >= 1")

    def test_08_settled_partial_exit_0(self):
        """Scenario 8: a stable non-all-healthy document -> exit 0 status
        'partial' (SPEC S6.3: exit 3 is arch mismatch, NOT partial)."""
        s, _sc, _run, _clock = make_seams(self.tmp)
        _write_health(
            self.run_dir,
            {
                "stack": "degraded",
                "instances": [
                    {"i": 0, "port": 30101, "state": "healthy"},
                    {"i": 1, "port": 30102, "state": "degraded"},
                ],
            },
        )
        code, report = self.install(s)
        self.assertEqual(code, 0, "settled partial is exit 0 (S6.3)")
        self.assertEqual(report["status"], "partial")

    def test_09_footprint_drift_rewrite(self):
        """Scenario 9: footprint drift 59.52 -> 69.52 (+10 GB, > the 5 GB
        S6.4 threshold) -> N recomputes 3 -> 2, the rewrite happens and
        the drift is NAMED in the report warnings."""
        s1, _sc, _run, _clock = make_seams(self.tmp, available_gb=AVAIL_59_52)
        _write_health(self.run_dir, _full_doc(3))
        code, report = self.install(s1)
        self.assertEqual(code, 0)
        self.assertEqual(report["n_instances"], 3)
        self.assertTrue(re.search(r"footprint_gb: 59\.52", self.serving_on_disk()))
        self.assertTrue((self.tmp / "units" / "bonsai2-server@2.service").exists())

        s2, _sc, _run2, _clock2 = make_seams(self.tmp)  # log now shows 69.52
        _write_health(self.run_dir, _full_doc(2))
        code, report = self.install(s2)
        self.assertEqual(code, 0)
        self.assertEqual(report["n_instances"], 2)
        joined = " ".join(report["warnings"])
        self.assertIn("instances.count 3 -> 2", joined, "the N change is named")
        self.assertIn(
            "main footprint drift 59.52 -> 69.52 GB", joined, "the drift is named"
        )
        self.assertTrue(re.search(r"footprint_gb: 69\.52", self.serving_on_disk()))
        self.assertFalse((self.tmp / "units" / "bonsai2-server@2.service").exists())
        self.assertTrue(re.search(r"count: 2", self.engine_on_disk()))

    def test_10_state_rotation_last_5_kept(self):
        """Scenario 10: S6.5 rotation — after 6 successful installs only
        the last 5 state files remain; the oldest is removed."""
        docs = _full_doc(2)
        _write_health(self.run_dir, docs)
        shared_clock = Clock()
        first_state = None
        for _ in range(6):
            s, _sc, _run, _clock = make_seams(self.tmp, clock=shared_clock)
            code, _report = self.install(s)
            self.assertEqual(code, 0)
            if first_state is None:
                first_state = self.state_files()[0].name
        files = [p.name for p in self.state_files()]
        self.assertEqual(len(files), 5, "last 5 kept (S6.5)")
        self.assertNotIn(first_state, files, "the oldest was rotated out")

    def test_11_rollback_restores_previous_n(self):
        """Scenario 11: --rollback <state> (S6.5) — configs restored,
        unit @1 removed when N shrinks back to 1, manager restarted."""
        shared_clock = Clock()
        s1, sc, _run, _clock = make_seams(self.tmp, clock=shared_clock)
        _write_health(self.run_dir, _full_doc(1))
        code, _report = self.install(s1, instances=1)
        self.assertEqual(code, 0)
        state_a = self.state_files()[0]

        s2, _sc, _run2, _clock2 = make_seams(self.tmp, clock=shared_clock)
        _write_health(self.run_dir, _full_doc(2))
        code, _report = self.install(s2)
        self.assertEqual(code, 0)
        self.assertTrue((self.tmp / "units" / "bonsai2-server@1.service").exists())

        s3, _sc, _run3, _clock3 = make_seams(self.tmp, clock=shared_clock)
        code, report = inst.rollback(s3, state_a, self.opt, "system")
        self.assertEqual(code, 0)
        self.assertEqual(report["status"], "rolled-back")
        self.assertEqual(report["n"], 1)
        self.assertTrue(
            re.search(r"count: 1", self.engine_on_disk()), "config restored"
        )
        self.assertTrue((self.tmp / "units" / "bonsai2-server@0.service").exists())
        self.assertFalse((self.tmp / "units" / "bonsai2-server@1.service").exists())
        self.assertTrue(re.search(r"footprint_gb: 69\.52", self.serving_on_disk()))
        reloaded = [c for c in sc.calls if "daemon-reload" in c]
        restarted = [c for c in sc.calls if "restart" in c and "bonsai2-sglang" in c]
        self.assertTrue(reloaded, "daemon-reload on the rollback path")
        self.assertTrue(restarted, "the manager restarts on the rollback path")

    def test_12_refresh_main(self):
        """Scenario 12: --refresh-main (S6.5) — re-detect the footprint,
        recompute N, rewrite the config, restart the manager, and run the
        quick bench (M1/M2/M7) via the BONSAI2_BENCH_CONFIG env."""
        s1, _sc, _run, _clock = make_seams(self.tmp)
        _write_health(self.run_dir, _full_doc(2))
        code, _report = self.install(s1)
        self.assertEqual(code, 0)

        # the main stack 'upgraded': its log now shows the 59.52 footprint
        s2, sc, run2, _clock2 = make_seams(self.tmp, available_gb=AVAIL_59_52)
        code, report = inst.refresh_main(
            s2, opt_root=self.opt, python="/usr/bin/python3"
        )
        self.assertEqual(code, 0)
        self.assertEqual(report["status"], "refreshed")
        self.assertAlmostEqual(report["main_footprint_gb"], 59.52, delta=0.01)
        self.assertEqual(report["n"], 3, "N recomputed up on the smaller footprint")
        self.assertEqual(report["quick_bench_exit"], 0)
        self.assertTrue(re.search(r"footprint_gb: 59\.52", self.serving_on_disk()))
        self.assertTrue(re.search(r"count: 3", self.engine_on_disk()))
        restarted = [c for c in sc.calls if "restart" in c and "bonsai2-sglang" in c]
        self.assertTrue(restarted, "the manager restarts so it re-plans")
        bench_calls = [c for c in run2.calls if c["script"].endswith("bonsai2-bench")]
        self.assertEqual(len(bench_calls), 1, "the quick bench ran exactly once")
        self.assertEqual(bench_calls[0]["argv"], ["--quick"])
        self.assertEqual(
            bench_calls[0]["env"]["BONSAI2_BENCH_CONFIG"],
            str(self.opt / "etc" / "bench.yaml"),
            "the quick bench pointed at the installed tree's bench.yaml",
        )


class TestCliWiring(_E2EBase):
    """The install.sh-facing CLI: the one JSON on stdout + exit codes.

    `main()` builds its own `Seams()` (real box reads) — to drive the CLI
    path hermetically the module-level `Seams` is monkeypatched to return
    the fakes (the CLI arg surface is what is under test here)."""

    def _main_with(self, seams, argv):
        @contextlib.contextmanager
        def _swap():
            real = inst.Seams
            inst.Seams = lambda: seams
            try:
                yield
            finally:
                inst.Seams = real

        with _swap():
            return inst.main(argv)

    def test_dry_run_writes_nothing(self):
        s, sc, _run, _clock = make_seams(self.tmp)
        code = self._main_with(
            s,
            [
                "--dry-run",
                "--opt-root",
                str(self.opt),
                "--health-dir",
                str(self.run_dir),
                "--scope",
                "system",
            ],
        )
        self.assertEqual(code, 0)
        self.assertFalse(
            (self.opt / "etc").exists(), "dry-run: no writes under the opt root"
        )
        self.assertEqual(sc.calls, [], "dry-run: zero systemctl calls")

    def test_rollback_via_cli(self):
        shared_clock = Clock()
        s1, _sc, _run, _clock = make_seams(self.tmp, clock=shared_clock)
        _write_health(self.run_dir, _full_doc(1))
        code = self._main_with(
            s1,
            [
                "--instances",
                "1",
                "--opt-root",
                str(self.opt),
                "--health-dir",
                str(self.run_dir),
                "--scope",
                "system",
            ],
        )
        self.assertEqual(code, 0)
        state_a = self.state_files()[0]
        s2, _sc, _run2, _clock2 = make_seams(self.tmp, clock=shared_clock)
        _write_health(self.run_dir, _full_doc(2))
        code = self._main_with(
            s2,
            [
                "--opt-root",
                str(self.opt),
                "--health-dir",
                str(self.run_dir),
                "--scope",
                "system",
            ],
        )
        self.assertEqual(code, 0)
        s3, _sc, _run3, _clock3 = make_seams(self.tmp, clock=shared_clock)
        code = self._main_with(
            s3,
            [
                "--rollback",
                str(state_a),
                "--opt-root",
                str(self.opt),
                "--health-dir",
                str(self.run_dir),
                "--scope",
                "system",
            ],
        )
        self.assertEqual(code, 0)
        self.assertTrue(re.search(r"count: 1", self.engine_on_disk()))


class _ImportBlocked:
    """A sys.modules placeholder that makes `import yaml` raise
    ImportError, forcing `_load_yaml_file` onto its stdlib fallback even
    under the box python (where PyYAML is present)."""

    def __init__(self):
        self.name = "yaml"

    def __getattr__(self, item):
        raise ImportError("blocked for the S6.7 bare-box-python3 path")


class TestStateRoundTrip(unittest.TestCase):
    """S6.5 state files must round-trip through BOTH readers: PyYAML when
    present and the stdlib fallback when it is not (the rollback path
    reads state files with _load_yaml_file; a bare-box python3 has no
    PyYAML — the S6.7 convention)."""

    def _state_like(self) -> dict:
        return {
            "timestamp": "2026-09-29T20:30:00",
            "n": 2,
            "n_auto": 2,
            "n_override": None,
            "main_footprint_gb": 69.52,
            "footprint_source": "sglang log (pool - available_gpu_mem)",
            "image": {
                "tag": IMAGE_TAG,
                "digest": IMAGE_SHA,
                "action": "skipped (digest match)",
            },
            "model": {"action": "fetched/verified (S1.1)"},
            "config": {
                "subagent-engine.yaml": "count: 2\n",
                "serving.yaml": "footprint: 69.52",
            },
            "units": ["[Unit]\nDescription=x %i\n", "[Unit]\nDescription=y %i\n"],
            "warnings": ["main footprint 69.52 GB (sglang log); N=2 (S1.4 computed 2)"],
            "scope": "system",
        }

    def test_fallback_parser_matches_pyyaml(self):
        state = self._state_like()
        text = inst._dump_yaml(state) + "\n"
        parsed_fallback = inst._parse_state_yaml(text)
        try:
            import yaml
        except ImportError:
            self.skipTest("PyYAML not available (cross-check skipped)")
        parsed_yaml = yaml.safe_load(text)
        self.assertEqual(
            parsed_fallback,
            parsed_yaml,
            "the stdlib fallback must agree with PyYAML on _dump_yaml output",
        )

    def test_load_yaml_file_prefers_pyyaml_falls_back(self):
        import tempfile

        state = self._state_like()
        text = inst._dump_yaml(state) + "\n"
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "state-x.yaml"
            p.write_text(text)
            loaded = inst._load_yaml_file(p)
            self.assertEqual(loaded["n"], 2)
            self.assertEqual(loaded["config"]["serving.yaml"], "footprint: 69.52")
            self.assertEqual(loaded["units"], state["units"])

    def test_load_yaml_file_fallback_path_covers_units(self):
        """P2 (review r1): the `_load_yaml_file` fallback path is only
        reached when PyYAML is absent. Force the ImportError branch under
        a PyYAML-present box python so the multi-line block-scalar `units`
        list — the case most likely to diverge between the two readers —
        is exercised through the ACTUAL file-load path that `rollback`
        uses, not just the direct `_parse_state_yaml` call."""
        import tempfile

        state = self._state_like()
        text = inst._dump_yaml(state) + "\n"
        real_yaml = sys.modules.get("yaml")
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "state-yaml-absent.yaml"
            p.write_text(text)
            sys.modules["yaml"] = _ImportBlocked()
            try:
                loaded = inst._load_yaml_file(p)  # hits the stdlib fallback
            finally:
                if real_yaml is not None:
                    sys.modules["yaml"] = real_yaml
                else:
                    sys.modules.pop("yaml", None)
            self.assertEqual(
                loaded["units"], state["units"], "fallback read the units list"
            )
            self.assertIsNone(loaded["n_override"], "null round-trips to None")
            self.assertEqual(loaded["config"]["serving.yaml"], "footprint: 69.52")


if __name__ == "__main__":
    unittest.main()
