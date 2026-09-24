"""MAP 2.11 / S2.8 acceptance: the live S2.8 acceptance run on this box.

SPEC S2.8 "Acceptance (runnable)" — five criteria, all run live through
the manager (the Phase-3 harness commands are MAP P3; at this layer the
test drives the manager's surfaces directly, the same way the Phase-3
`bonsai2-unload` / `bonsai2-reload` commands will):

  #1 Cold boot -> main + N healthy <= boot_target_s (recorded T).
  #2 Single-instance crash -> auto-recovered (reload half < 15 s);
     main unaffected; health.json transitions
     healthy -> degraded -> healthy.
  #3 N=0 mode end-to-end (S2.7 test: override the main footprint to
     120 GB -> the S2.3 `max(0, ...)` clamp -> install exit 0,
     main-only reported) + the S2.4 sibling: the N'=0 PTQ1_0 fallback
     retry live at its fallback footprint.
  #4 5-minute drift simulation -> ctx-reduce event at the halved ctx,
     the instance healthy again < 15 s, zero restarts on the other
     instance, N unchanged.
  #5 Unload/reload cycle (S2.7 test): `unload --all` -> main-only
     `user_unloaded`, the drift cycle does NOT re-spawn; `reload --all`
     -> N healthy within `boot_target_s`.

Method (box-specific, per the @gpu conventions — see
test_manager_spawn_2_5.py's module doc for the user-scope unit deltas,
which this test uses verbatim):

  * user-scope S2.2-shaped units for `bonsai2-server@0/1` (the
    documented three deltas vs the system template); the manager runs
    as a `--settle N` subprocess with BONSAI2_USER_SYSTEMCTL=1 +
    BONSAI2_HEALTH_DIR + BONSAI2_ENGINE_YAML + BONSAI2_SERVING_YAML
    (the manager's documented test hooks).
  * S2.8 #1: the cold boot+spawn+settle wall time is measured from
    just before the manager starts to its exit 0; the S2.8 #1 budget
    is the serving.yaml `boot_target_s` read LIVE (the shared whole-
    boot deadline the manager enforces against it, G3 PR #10 r15).
  * S2.8 #2: the engine's in-container PID (llama-server is PID 1 via
    the nvidia entrypoint, probed 2026-09-22) is killed with kill -9
    while the manager's probe loop runs; recovery = the unit's own
    `Restart=on-failure` (RestartSec=5) + the manager's S2.2
    escalation backstop. The health.json state sequence
    healthy->degraded->healthy is witnessed by polling the document
    every 0.2 s. The S1.8 #4 15 s budget covers the RELOAD half (the
    MAP 1.11 convention: teardown/restart prefix measured, not
    charged) — here the prefix is the 10 s probe-cadence degraded lag
    + systemd's RestartSec=5, so the assert is on the reload half,
    with the total logged for the S2.7 reboot test.
  * S2.8 #3: a crafted serving.yaml overrides
    `main_stack.footprint_gb` to 120 GB AND points `container` at a
    name that does not exist, so the S2.3 live-cgroup read falls back
    to the recorded footprint (the spec's "override main footprint to
    120 GB" — the real main container's live cgroup is ~2 GB asleep
    and would otherwise grant the full pool). free = 121.6 - 120 -
    8.0 = 3.6 GB -> N'=0. Half A (no fallback size in the config):
    plain main-only `budget_zero`, exit 0, no containers. Half B:
    footprint 100.0 -> free 13.6 GB, still < the PQ2_0 budget 14.78
    (N'=0 at the PQ2_0 budget) but >= the S2.4 fallback budget (~13.46
    = PTQ1_0 footprint + 5% margin, computed from the YAML at run
    time) -> the retry rewrites instance 0's unit to the PTQ1_0 quant
    + the fallback budget and spawns it live (the image bakes both
    quants, S1.3/S6.1).
  * S2.8 #4: the cgroup pressure is SIMULATED on the instance's real
    cgroup v2 `memory.current` (written between the manager's drift
    rechecks — the drift watch READS the file, it never allocates, so
    the write IS the pressure signal the S2.5 rule consumes). The
    S2.5 sustained-30 s clock spans rechecks: the first over-cap
    recheck starts the clock, a recheck > 30 s later fires the halve
    + the in-place `systemctl restart`. The recheck cadence is shrunk
    via the manager's documented BONSAI2_DRIFT_RECHECK_S env hook (6 s)
    so the whole S2.5 window fits in the test. Assertions: the unit
    file carries the halved ctx, the health document reports
    `ctx_reduced: true` + the halved ctx, instance 0's host PID
    changed (a real in-place restart), instance 1's host PID did NOT
    change + its state stayed healthy (zero restarts on the other
    instance), N unchanged, healthy again < 15 s at the halved ctx.
  * S2.8 #5: the control-queue request files
    (`control/unload-all.json` / `control/reload-all.json` — the shape
    the Phase-3 commands will write) drive the manager's MAP 2.10
    path; the ack files (`control/ack-<id>.json`) are the completion
    signal. Unload: both units stopped, cgroups gone (S2.7: assert
    VRAM release), health.json main-only `user_unloaded`. The
    no-respawn half: two drift rechecks after the unload fire no
    restart (`bunloaded` indexes are not in `runtime.running`).
    Reload: both instances back through the S1.4 tier, healthy
    within `boot_target_s`, `main_only_reason` gone.

Pool preconditions (the 1.9 rule, asserted before each phase): the
pool must clear the phase's load, ports 30101/30102 free, no stale
bonsai2 containers, the template count is 2 (the units/ports this
test installs are exactly 2). Estimated wall time ~15-25 min on a
clear pool: five 27B cold loads (~10-15 s each, warm-cache side after
the first) + the kill/drift/reload windows. Every phase tears down in
`finally` (one box, ports 30101/30102 — never leave a listener
behind).
"""

import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
MANAGER = REPO_ROOT / "bin" / "bonsai2-manager"
ENGINE_YAML = REPO_ROOT / "etc" / "subagent-engine.yaml"
SERVING_YAML = REPO_ROOT / "etc" / "serving.yaml"
RUNTIME_IMAGE = "bonsai2-server:1.0"
PORT_BASE = 30101
SETTLE_S = 20  # post-boot probe-loop window (2 cycles at the 10 s cadence)
MANAGER_TIMEOUT_S = 300
POLL_S = 0.2  # health.json witness poll (tight enough for the 15 s budget)
RELOAD_BUDGET_S = 15.0  # S2.8 #2 reload half (S1.8 #4 / MAP 1.11 convention)
DRIFT_RECHECK_S = 6  # the manager's documented env hook (5-min cadence shrunk)


def _image_present() -> bool:
    if shutil.which("docker") is None:
        return False
    r = subprocess.run(
        ["docker", "image", "inspect", RUNTIME_IMAGE],
        capture_output=True,
        text=True,
        check=False,
    )
    return r.returncode == 0


def _user_systemctl_available() -> bool:
    """blckwll has Linger=yes on this box (probed 2026-09-22); the user
    manager being up is the liveness test (same as test 2.5)."""
    r = subprocess.run(
        ["systemctl", "--user", "status"],
        capture_output=True,
        text=True,
        check=False,
    )
    return r.returncode == 0


def _port_free(port: int) -> bool:
    # G3 PR#11 r1 (DOD #4): SO_REUSEADDR — a plain bind() reports EADDRINUSE
    # on a port whose listener is GONE but whose connections are still in
    # TIME_WAIT (the @gpu discovery runs test 1.11 / 1.10 on
    # --network host right before this file; the host-net engine's
    # closed connections leave TIME_WAIT entries on 30101/30102 for up
    # to 60 s). SO_REUSEADDR lets the probe bind through the TIME_WAIT
    # entries — the check then measures ACTUAL listeners, not the
    # kernel's socket table. (A real listener still refuses the
    # connect-style probe semantics: the unit's ExecStartPre
    # docker rm -f clears a same-name container, but a DIFFERENT
    # listener on the port is exactly what this precondition must
    # catch, and bind-vs-lister contention is unaffected by
    # SO_REUSEADDR when the holder has it set too — the bounded retry
    # in _preconditions covers that residual case.)
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        s.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def _ports_free_bounded(timeout_s: float = 15.0, poll_s: float = 1.0) -> bool:
    """G3 PR#11 r1 (DOD #4): the port precondition is RETRIABLE — a
    transient holder between tests (a unit mid-restart after the prior
    @gpu file's teardown, a slow docker rm) is waited out for a
    bounded window instead of hard-failing the precondition; the hard
    assert happens only AFTER the window. 15 s matches the documented
    MAP 2.11 fix (poll every ~1 s up to ~15 s)."""
    deadline = time.monotonic() + timeout_s
    while True:
        if _port_free(PORT_BASE) and _port_free(PORT_BASE + 1):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(poll_s)


def _no_bonsai_containers() -> bool:
    r = subprocess.run(
        [
            "docker",
            "ps",
            "-a",
            "--filter",
            "name=bonsai2-server-",
            "--format",
            "{{.Names}}",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    return not r.stdout.strip()


def _mem_available_gb() -> float:
    for line in Path("/proc/meminfo").read_text().splitlines():
        if line.startswith("MemAvailable:"):
            return int(line.split()[1]) / 1e6
    raise AssertionError("MemAvailable not in /proc/meminfo")


def _mem_total_gb() -> float:
    """Live /proc/meminfo MemTotal in decimal GB (the S2.3 planning tier's
    `total` — the manager reads the SAME value in read_total_gb). G3 PR#11
    r1 (DOD #4, test #3 root cause): box-size constants baked into the test
    (the SPEC's 121.6 GB) rot on a different MemTotal — this box's is
    127.5 GB. Any test that sizes a pool scenario must read this live."""
    for line in Path("/proc/meminfo").read_text().splitlines():
        if line.startswith("MemTotal:"):
            return int(line.split()[1]) / 1e6
    raise AssertionError("MemTotal not in /proc/meminfo")


def _instance_budget_gb() -> float:
    text = ENGINE_YAML.read_text()
    m = re.search(r"(?m)^[ \t]*per_instance_budget_gb:[ \t]*([0-9.]+)", text)
    assert m, "per_instance_budget_gb not found in the template"
    return float(m.group(1))


def _fallback_budget_gb() -> float:
    """The S2.4 PTQ1_0 fallback budget, computed from the YAML the same
    way the manager does (S1.4 formula at the PTQ1_0 weights + 5%
    margin) — not a hardcoded copy that can drift from the template
    (the same guard as test 2.5's r12 P2 for the unit flags)."""
    sys.path.insert(0, str(REPO_ROOT / "bin"))
    import bonsai2_vram  # the S1.4 formula module (bin/, no deps)

    text = ENGINE_YAML.read_text()
    m = re.search(r"(?m)^[ \t]*fallback_size_bytes:[ \t]*(\d+)", text)
    assert m, "model.fallback_size_bytes not found in the template"
    ctx = re.search(r"(?m)^[ \t]*ctx_size:[ \t]*(\d+)", text)
    par = re.search(r"(?m)^[ \t]*max_num_seqs:[ \t]*(\d+)", text)
    kv = re.search(r"(?m)^kv_cache:[ \t]*(\S+)", text)
    assert ctx and par and kv, "ctx_size/max_num_seqs/kv_cache missing"
    return (
        bonsai2_vram.instance_footprint_gb(
            int(m.group(1)) / 1e9,
            int(par.group(1)),
            int(ctx.group(1)),
            bonsai2_vram.KV_PER_SLOT_PER_TOKEN_GB[kv.group(1)],
        )
        * 1.05
    )


def _boot_target_s_from(path: Path) -> float:
    text = path.read_text()
    m = re.search(r"(?m)^boot_target_s:[ \t]*([0-9.]+)", text)
    assert m, f"boot_target_s not found in {path}"
    return float(m.group(1))


def _count_from_template() -> int:
    text = ENGINE_YAML.read_text()
    m = re.search(r"(?m)^[ \t]*count:[ \t]*(\d+)", text)
    assert m, "instances.count not found in the template"
    return int(m.group(1))


def _ctx_kv_par_from_template() -> tuple[int, str, int]:
    text = ENGINE_YAML.read_text()

    def _grab(pattern: str) -> str:
        m = re.search(pattern, text)
        assert m, f"pattern {pattern!r} not found in the template"
        return m.group(1)

    ctx = int(_grab(r"(?m)^[ \t]*ctx_size:[ \t]*(\d+)"))
    kv = _grab(r"(?m)^[ \t]*kv_cache:[ \t]*(\S+)")
    par = int(_grab(r"(?m)^[ \t]*max_num_seqs:[ \t]*(\d+)"))
    return ctx, kv, par


def _model_alias_key_reasoning_from_template() -> tuple[str, str, str, str, str]:
    """model.file, model.fallback_file, alias, api_key, reasoning_format
    from the engine YAML (the same drift guard as test 2.5's G3 r12
    P2: the units the test installs must not hardcode values that can
    drift from the S1.3/S1.6 template)."""
    text = ENGINE_YAML.read_text()

    def _grab(pattern: str) -> str:
        m = re.search(pattern, text)
        assert m, f"pattern {pattern!r} not found in the template"
        return m.group(1)

    model_file = _grab(r"(?m)^[ \t]*file:[ \t]*(\S+)")
    fallback_file = _grab(r"(?m)^[ \t]*fallback_file:[ \t]*(\S+)")
    alias = _grab(r"(?m)^alias:[ \t]*(\S+)")
    api_key = _grab(r"(?m)^api_key:[ \t]*(\S+)")
    reasoning = _grab(r"(?m)^reasoning_format:[ \t]*(\S+)")
    return model_file, fallback_file, alias, api_key, reasoning


def _instance_unit_text(
    i: int,
    image: str,
    budget_gb: float,
    ctx: int,
    parallel: int,
    kv: str,
    port: int,
    model_file: str,
    alias: str,
    api_key: str,
    reasoning_format: str,
) -> str:
    """The S2.2 unit shape for instance i (user-scope deltas exactly as
    test 2.5 documents them)."""
    exec_start = (
        "ExecStart=/usr/bin/docker run --rm --name bonsai2-server-%i "
        "--gpus all "
        f"--memory {budget_gb}g --memory-swap {budget_gb}g "
        "--shm-size 4g --network host --ipc=host "
        f"{image} "
        f"/opt/bonsai2/bin/llama-server "
        f"--model /opt/bonsai2/models/{model_file} "
        f"--ctx-size {ctx} --parallel {parallel} --batch-size 2048 "
        f"--cache-type-k {kv} --cache-type-v {kv} "
        f"--port {port} --api-key {api_key} "
        f"--reasoning-format {reasoning_format} --alias {alias}"
    )
    return (
        "[Unit]\n"
        "Description=bonsai2 subagent instance %i (llama-server container, @gpu test)\n"
        "After=docker.service\n"
        "StartLimitIntervalSec=300\n"
        "StartLimitBurst=3\n"
        "\n"
        "[Service]\n"
        "ExecStartPre=/usr/bin/docker rm -f bonsai2-server-%i\n"
        f"{exec_start}\n"
        "Restart=on-failure\n"
        "RestartSec=5\n"
        "CPUQuota=64%\n"
        "\n"
        "[Install]\n"
        "WantedBy=multi-user.target\n"
    )


def _gpu_compute_apps_mib() -> dict:
    """pid -> used MiB from nvidia-smi (the box's process-level meter).
    The main-stack PIDs are the S2.8 #2 'main unaffected' baseline."""
    r = subprocess.run(
        [
            "nvidia-smi",
            "--query-compute-apps=pid,used_memory",
            "--format=csv",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    out = {}
    for line in r.stdout.splitlines():
        line = line.strip()
        if not line.endswith(" MiB") or "," not in line:
            continue
        pid_s, mib_s = line.split(",", 1)
        if pid_s.strip().isdigit():
            out[int(pid_s.strip())] = int(mib_s.split()[0])
    return out


def _container_host_pid(name: str) -> int:
    """The container's init PID in the host namespace (0 when not
    running). llama-server IS the container's init (the nvidia
    entrypoint execs it), so the host pid is the restart witness —
    the in-container PID is 1 in every container."""
    r = subprocess.run(
        ["docker", "inspect", name, "--format", "{{.State.Pid}}"],
        capture_output=True,
        text=True,
        check=False,
    )
    if r.returncode != 0:
        return 0
    return int(r.stdout.strip())


def _engine_pid_in_container(name: str) -> int:
    r = subprocess.run(
        ["docker", "exec", name, "pgrep", "-x", "llama-server"],
        capture_output=True,
        text=True,
        check=False,
    )
    pids = [p for p in r.stdout.split() if p.isdigit()]
    assert pids, f"llama-server PID not found in {name}: {r.stdout!r} {r.stderr!r}"
    return int(pids[0])


def _port_holder(port: int, wait_s: float = 15.0):
    """G3 PR#11 r1 (DOD #4, #2 root cause): a bounded HOST-SIDE port
    holder — binds 127.0.0.1:`port` in a daemon thread and refuses the
    port to whoever else wants it (the restarted engine's
    `--port` bind fails -> the unit's Restart=on-failure loop keeps
    the instance DOWN across probe cycles, so the manager's S2.2
    two-consecutive-fails -> degraded rule is actually reachable).

    The bind is RETRIED within a bounded window: right after the
    kill the old engine's port may still be closing (or the unit's
    RestartSec=5 restart may have rebound it for one cycle); EADDRINUSE
    is retryable, not fatal (the 2026-09-23 re-run proved that).

    Returns a handle with `.release()`: closes the socket, the thread
    exits, the port is free for the restarted engine. The holder is
    ALWAYS released in the test's `finally` (even on a timeout) so no
    listener leaks into the next phase (the one-GPU/one-ports rule).
    """
    import threading

    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    deadline = time.monotonic() + wait_s
    while True:
        try:
            s.bind(("127.0.0.1", port))
            break
        except OSError:
            if time.monotonic() >= deadline:
                s.close()
                raise
            time.sleep(1.0)
    s.listen(1)

    state = {"stopped": False}

    def _accept_loop() -> None:
        while not state["stopped"]:
            try:
                conn, _ = s.accept()
                conn.close()  # refuse: probe gets a clean close, not a 200
            except OSError:
                break

    t = threading.Thread(target=_accept_loop, daemon=True)
    t.start()

    class _Holder:
        def release(self) -> None:
            state["stopped"] = True
            try:
                s.close()
            except OSError:
                pass
            t.join(timeout=5.0)

    return _Holder()


def _cgroup_dir_for(name: str) -> Path | None:
    """The container's cgroup v2 dir (same resolution the manager's
    instance_cgroup_path does: docker id -> system.slice/docker-<id>.
    scope, then the raw docker/<id> shape)."""
    r = subprocess.run(
        ["docker", "inspect", "-f", "{{.Id}}", name],
        capture_output=True,
        text=True,
        check=False,
    )
    if r.returncode != 0:
        return None
    cid = r.stdout.strip()
    if not cid:
        return None
    flat = cid.replace("/", "")
    for cand in (
        Path(f"/sys/fs/cgroup/system.slice/docker-{flat}.scope"),
        Path(f"/sys/fs/cgroup/docker/{flat}"),
    ):
        if cand.is_dir():
            return cand
    return None


def _inject_cgroup_pressure(
    container: str,
    cg: Path | None,
    target_bytes: int,
    cap_bytes: int,
    hold_s: float = 120.0,
) -> bool:
    """G3 PR#11 r1 (DOD #4, test #4 root cause): the cgroup v2
    `memory.current` on this box is NOT writable by the test user — the
    instance containers run in `system.slice/docker-<id>.scope`
    (root-owned; the 2026-09-23 drift probe got `Permission denied`,
    rc=1, both as uid 1000 and via a privileged root helper). The old
    simulated write therefore never landed and the S2.5 clock never
    started. The pressure is now injected the way the kernel actually
    charges it: `docker exec` allocates REAL anonymous memory inside
    the container, which the kernel charges to that container's cgroup
    (`memory.current` rises by the allocation).

    The holder is a detached in-container `python3` process holding a
    `bytearray(n)` (docs/two-red-gpu-tests-fix.md, the PR #11 r1
    technical-debt follow-up): anonymous pages held by a live process
    are NOT reclaimable, so they charge `memory.current` durably for the
    whole `hold_s`. The earlier iterations used a `dd` file on the
    container rootfs — wrong, because a file's pages are RECLAIMABLE
    page cache: the crash-state experiments measured a 3 GB synced dd
    moving `memory.current` only ~0.5 MB (never near cap). The runtime
    image bakes python3 for this (Dockerfile.runtime; verified in-build).

    `hold_s` defaults to 120 s: the S2.5 sustained window is 30 s and the
    drift recheck cadence is shrunken to 6 s, so the over-cap charge must
    outlive 30 s of clock PLUS the ~6 s of recheck latency that starts the
    clock, with margin. With a 13.7 GB allocation the cap can be crossed
    ~30-45 s after the holder starts; the 120 s hold keeps the charge in
    force well past any drift-clock start inside the 90 s halve-poll
    window, so the sustained-30 s condition is met and the halve fires.

    Returns True only when the charge actually LANDED: `memory.current`
    is re-read and must EXCEED the manager's S2.2 `--memory` cap
    (`cap_bytes`, the same `cur > cap_bytes` condition `check_drift`
    reacts to — G3 PR#11 r1 disposition / technical debt, docs/pr11-
    two-red-gpu-tests.md Fix A). The old `0.5 * target` threshold was
    well below the cap the drift watch actually checks, so it could
    report "landed" at a level the watch never saw. The poll deadline
    is `hold_s + 60` — past the allocation time, so the detection never
    gives up before the charge is in force.
    """
    if cg is None:
        return False
    if cap_bytes <= 0:
        # G3 PR#11 r1 disposition / technical debt (Fix A): the landed
        # condition is `memory.current > cap_bytes` (the cap check_drift
        # reacts to) — the caller MUST pass the manager's decimal cap;
        # the old 0.5*target baseline heuristic retired with it.
        raise ValueError(
            "_inject_cgroup_pressure: cap_bytes must be > 0 (the manager's "
            "S2.2 --memory cap in bytes, the condition check_drift reacts to)"
        )
    try:
        before = int((cg / "memory.current").read_text().strip())
    except (OSError, ValueError):
        return False
    n = int(target_bytes)
    # The holder allocates anonymous memory INSIDE the container via a
    # detached `python3` process (docs/two-red-gpu-tests-fix.md, the PR
    # #11 r1 technical-debt follow-up). Anonymous pages held by a live
    # process are NOT reclaimable, so they charge the container's cgroup
    # memory.current durably for the whole hold. The earlier iterations
    # (shell-variable copy, then a `dd` file on the rootfs) failed: the
    # shell variable copy-amp'd to ~2x and OOM-killed (rc=137); the `dd`
    # file's pages are RECLAIMABLE page cache, so the crash-state
    # experiments saw memory.current move only ~0.5 MB (a 3 GB synced dd
    # held ~3.5 MB, not ~3 GB). A live python process holding a
    # bytearray is the mechanism that actually lands the charge.
    #
    # The exec must run DETACHED (docker exec -d): the holder holds the
    # charge for `hold_s`, so a blocking exec would return only after the
    # release and the re-read would see the post-release value. The holder
    # script writes a bytearray(n) (n bytes of anonymous, resident pages),
    # sleeps hold_s (keeping the pages charged for the S2.5 window), then
    # exits; the pages are released on exit. The runtime image bakes
    # python3 for this (Dockerfile.runtime, verified in-build).
    holder_script = (
        f"import time\n"
        f"n = {n}\n"
        f"b = bytearray(n)\n"
        f"for i in range(0, n, 1 << 20):\n"
        f"    b[i] = 1\n"
        f"time.sleep({int(hold_s)})\n"
    )
    holder = subprocess.Popen(
        [
            "docker",
            "exec",
            "-d",
            container,
            "sh",
            "-c",
            f"python3 -c '{holder_script}'",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        # G3 PR#11 r1 disposition / technical debt (Fix A,
        # docs/pr11-two-red-gpu-tests.md + docs/two-red-gpu-tests-fix.md):
        # the detection must (1) check the condition the drift watch
        # reacts to — `memory.current > cap_bytes` (the manager's decimal
        # `--memory` cap), not `0.5 * target` — and (2) wait PAST the
        # allocation time (`hold_s + 60`), not give up before the ~13.7 GB
        # allocation has charged enough resident pages to cross the cap.
        # The holder keeps the charge in force for the full `hold_s`, so
        # once the cap is crossed the charge is sustained > 30 s and
        # check_drift fires.
        deadline = time.monotonic() + hold_s + 60.0
        after = before
        while time.monotonic() < deadline:
            try:
                cur = int((cg / "memory.current").read_text().strip())
            except (OSError, ValueError):
                cur = before
            if cur > cap_bytes:
                after = cur
                break
            time.sleep(2.0)
        else:
            try:
                after = int((cg / "memory.current").read_text().strip())
            except (OSError, ValueError):
                after = before
    finally:
        try:
            holder.wait(timeout=hold_s + 120.0)
        except subprocess.TimeoutExpired:
            # The holder may outlive the window if the engine restarts
            # mid-hold; the detached python exec is reaped when the
            # container is torn down in the test's finally, so no leftover
            # charge leaks into the next phase (one-GPU/one-ports rule).
            holder.kill()
            holder.wait(timeout=30.0)
    landed = after > cap_bytes
    print(
        f"[2.11 #4] cgroup charge: {before} B -> {after} B "
        f"(manager cap {cap_bytes} B; target +{n} B; landed={landed})"
    )
    return landed


def _read_health(health_dir: Path) -> dict | None:
    p = health_dir / "health.json"
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def _tail_lines(path: Path, n: int = 40) -> str:
    """The last `n` lines of a text file (the manager-log diagnostic
    dump on a red degraded-witness); '' when the file is absent or
    unreadable."""
    try:
        return "\n".join(path.read_text().splitlines()[-n:])
    except OSError:
        return ""


def _health_states(health: dict) -> dict:
    """i -> (state, ctx, ctx_reduced) from a health document."""
    out = {}
    for inst in health.get("instances", []):
        out[int(inst["i"])] = (
            inst["state"],
            inst.get("ctx"),
            inst.get("ctx_reduced", False),
        )
    return out


def _poll_health(health_dir: Path, timeout_s: float) -> dict:
    """Poll health.json until it appears + parses; the manager writes
    it on every probe cycle (10 s) so this is fast once the loop runs."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        h = _read_health(health_dir)
        if h is not None:
            return h
        time.sleep(0.5)
    raise AssertionError(f"health.json never appeared in {health_dir}")


def _poll_until(health_dir: Path, timeout_s: float, pred, what: str) -> dict:
    """Poll health.json until `pred(health)` is True (the 'event
    landed' half of the S2.8 assertions — the manager writes the
    document on every probe cycle, so a 0.2 s poll resolves within
    one cycle)."""
    deadline = time.monotonic() + timeout_s
    last = None
    while time.monotonic() < deadline:
        last = _read_health(health_dir)
        if last is not None and pred(last):
            return last
        time.sleep(POLL_S)
    raise AssertionError(
        f"{what} did not land within {timeout_s:.0f} s "
        f"(last doc: {json.dumps(last)[:400] if last else None})"
    )


def _write_control_request(control_dir: Path, req: dict) -> None:
    control_dir.mkdir(parents=True, exist_ok=True)
    (control_dir / f"{req['request_id']}.json").write_text(
        json.dumps(req, indent=2) + "\n"
    )


def _wait_ack(control_dir: Path, req_id: str, timeout_s: float) -> dict:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        p = control_dir / f"ack-{req_id}.json"
        if p.is_file():
            return json.loads(p.read_text())
        time.sleep(0.5)
    raise AssertionError(f"ack-{req_id}.json never appeared in {control_dir}")


class TestS28Acceptance(unittest.TestCase):
    """S2.8 acceptance, all five, live (MAP 2.11). Sequential phases
    (the one-GPU rule); each phase owns its manager subprocess +
    teardown in `finally`."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.ctx, cls.kv, cls.par = _ctx_kv_par_from_template()
        (
            cls.model_file,
            cls.fallback_file,
            cls.alias,
            cls.api_key,
            cls.reasoning,
        ) = _model_alias_key_reasoning_from_template()
        cls.budget = _instance_budget_gb()
        cls.user_unit_dir = Path.home() / ".config" / "systemd" / "user"
        cls.user_unit_dir.mkdir(parents=True, exist_ok=True)
        cls.unit_files = []
        # G3 PR#11 r1 (DOD #4): the manager's S2.6 measured-boot and S2.4
        # quant/ctx write-backs mutate the LIVE templates under the @gpu
        # harness (BONSAI2_*_YAML point at the tracked etc/ files).
        # Snapshot both so the tracked tree is clean after the suite —
        # otherwise the next hermetic run reads the leaked values.
        cls._tpl_backups = {p: p.read_bytes() for p in (ENGINE_YAML, SERVING_YAML)}

    @classmethod
    def tearDownClass(cls) -> None:
        for uf in cls.unit_files:
            uf.unlink(missing_ok=True)
        stray = Path.home() / ".config/systemd/user/bonsai2-sglang.service"
        stray.unlink(missing_ok=True)
        subprocess.run(
            ["systemctl", "--user", "daemon-reload"],
            capture_output=True,
            check=False,
        )
        for p, blob in cls._tpl_backups.items():
            try:
                if p.read_bytes() != blob:
                    p.write_bytes(blob)
            except OSError:
                pass

    def _install_units(self) -> None:
        for i in (0, 1):
            uf = self.user_unit_dir / f"bonsai2-server@{i}.service"
            uf.write_text(
                _instance_unit_text(
                    i,
                    RUNTIME_IMAGE,
                    self.budget,
                    self.ctx,
                    self.par,
                    self.kv,
                    PORT_BASE + i,
                    self.model_file,
                    self.alias,
                    self.api_key,
                    self.reasoning,
                )
            )
            self.unit_files.append(uf)
        subprocess.run(
            ["systemctl", "--user", "daemon-reload"],
            capture_output=True,
            check=True,
        )

    def _teardown_instances(self) -> None:
        for i in (0, 1):
            subprocess.run(
                ["systemctl", "--user", "stop", f"bonsai2-server@{i}"],
                capture_output=True,
                check=False,
            )
            subprocess.run(
                ["docker", "rm", "-f", f"bonsai2-server-{i}"],
                capture_output=True,
                check=False,
            )
        for uf in self.unit_files:
            uf.unlink(missing_ok=True)
        self.unit_files = []
        stray = Path.home() / ".config/systemd/user/bonsai2-sglang.service"
        stray.unlink(missing_ok=True)
        subprocess.run(
            ["systemctl", "--user", "daemon-reload"],
            capture_output=True,
            check=False,
        )

    def _manager_env(
        self, health_dir: Path, engine_yaml: Path, serving_yaml: Path
    ) -> dict:
        env = dict(os.environ)
        env["BONSAI2_USER_SYSTEMCTL"] = "1"
        env["BONSAI2_HEALTH_DIR"] = str(health_dir)
        env["BONSAI2_ENGINE_YAML"] = str(engine_yaml)
        env["BONSAI2_SERVING_YAML"] = str(serving_yaml)
        return env

    def _run_manager(self, env: dict, settle_s: float) -> tuple[int, str]:
        """Run the manager to exit; return (rc, stderr). The manager's
        stderr carries its _log lines (the S2.3/S2.4/S2.5 markers the
        assertions grep)."""
        r = subprocess.run(
            [sys.executable, str(MANAGER), "--settle", str(settle_s)],
            capture_output=True,
            text=True,
            env=env,
            check=False,
            timeout=MANAGER_TIMEOUT_S,
        )
        return r.returncode, r.stderr

    def _preconditions(self, pool_need_gb: float) -> set:
        free_gb = _mem_available_gb()
        self.assertGreaterEqual(
            free_gb,
            pool_need_gb,
            f"pool not clear: free {free_gb:.1f} GB < {pool_need_gb:.1f} GB "
            f"— run the VRAM audit and clear",
        )
        self.assertTrue(
            _ports_free_bounded(),
            "ports 30101/30102 busy after 15 s — leftover container?",
        )
        self.assertTrue(_no_bonsai_containers(), "stale bonsai2-server containers")
        self.assertEqual(
            _count_from_template(),
            2,
            "engine YAML instances.count != 2 — this @gpu test installs "
            "exactly 2 units/ports; update it if the template count changes",
        )
        stray_manager_unit = Path.home() / ".config/systemd/user/bonsai2-sglang.service"
        self.assertFalse(
            stray_manager_unit.exists(),
            "stray user-scope bonsai2-sglang.service — stop/disable it and re-run",
        )
        # The S2.8 #2 'main unaffected' baseline (same convention as
        # test 2.5: the manager's activity must not lose a main-stack
        # PID from the compute-apps listing).
        return set(_gpu_compute_apps_mib())

    def _assert_main_pids_intact(self, baseline_pids: set) -> None:
        after_pids = set(_gpu_compute_apps_mib())
        lost = baseline_pids - after_pids
        self.assertFalse(
            lost,
            f"main-stack PIDs lost during the phase: {lost}",
        )

    # ------------------------------------------------------------------
    # S2.8 #1 — cold boot -> main + N healthy <= boot_target_s (T)
    # ------------------------------------------------------------------
    @unittest.skipUnless(_image_present(), f"runtime image {RUNTIME_IMAGE} not present")
    @unittest.skipUnless(_user_systemctl_available(), "user-scope systemd unavailable")
    def test_1_cold_boot_under_boot_target(self):
        self._preconditions(2 * self.budget + 1.0)
        try:
            self._install_units()
            with tempfile.TemporaryDirectory(prefix="bonsai2-health-211-1-") as td:
                health_dir = Path(td)
                env = self._manager_env(health_dir, ENGINE_YAML, SERVING_YAML)
                boot_target = _boot_target_s_from(SERVING_YAML)
                t0 = time.monotonic()
                rc, err = self._run_manager(env, SETTLE_S)
                boot_s = time.monotonic() - t0
                self.assertEqual(
                    rc,
                    0,
                    f"manager exit {rc} (want 0 = full load / partial / "
                    f"main-only).\nSTDERR:\n{err[-2000:]}",
                )
                self.assertLessEqual(
                    boot_s,
                    boot_target,
                    f"S2.8 #1: cold boot+spawn+settle {boot_s:.1f} s > "
                    f"boot_target_s {boot_target:.0f} s (recorded T={boot_s:.1f} s)",
                )
                self.assertIn("full load: 2 instance(s) spawned", err)
                # S2.6 (MAP 2.9): the measured boot written back to the
                # serving.yaml path the manager was fed. Under the @gpu
                # harness that path IS the tracked template
                # (BONSAI2_SERVING_YAML); the write is asserted via the
                # manager's log line, and the tracked templates are
                # restored by tearDownClass (G3 PR#11 r1 DOD #4).
                self.assertIn("S2.6: measured boot", err)
                health = _poll_health(health_dir, 5.0)
                self.assertEqual(health["stack"], "healthy")
                self.assertIs(health["main_healthy"], True)
                self.assertEqual(len(health["instances"]), 2)
                for i in (0, 1):
                    self.assertEqual(
                        _health_states(health)[i][0],
                        "healthy",
                        f"instance {i} not healthy: "
                        f"{json.dumps(health['instances'][i])}",
                    )
                print(
                    f"\n[2.11 #1] cold boot T={boot_s:.1f} s "
                    f"(<= boot_target_s {boot_target:.0f} s; "
                    f"2 instances healthy)"
                )
        finally:
            self._teardown_instances()

    # ------------------------------------------------------------------
    # S2.8 #2 — single-instance crash -> auto-recovered, main
    #          unaffected; health.json healthy->degraded->healthy
    # ------------------------------------------------------------------
    @unittest.skipUnless(_image_present(), f"runtime image {RUNTIME_IMAGE} not present")
    @unittest.skipUnless(_user_systemctl_available(), "user-scope systemd unavailable")
    def test_2_crash_recovery(self):
        baseline_pids = self._preconditions(2 * self.budget + 1.0)
        try:
            self._install_units()
            with tempfile.TemporaryDirectory(prefix="bonsai2-health-211-2-") as td:
                health_dir = Path(td)
                env = self._manager_env(health_dir, ENGINE_YAML, SERVING_YAML)
                # G3 PR#11 r1 disposition, technical-debt fix (Fix A,
                # docs/pr11-two-red-gpu-tests.md): the old port-holder
                # machinery raced the engine's own port rebind (EADDRINUSE,
                # Errno 98) because it tried to hold 30101 down while the
                # still-live engine owned it. The root cause was the 20 s
                # degraded window (2 fails x 10 s cadence) vs the unit's
                # ~8-13 s self-recovery — the holder existed to stretch the
                # down time past the restart.
                #
                # The 2 s probe cadence alone is NOT enough on this box:
                # each probe round-trips a 16-token completion with a ~15 s
                # timeout (COMPLETION_RT_MAX_S=10 + 5 s socket cap), so a
                # FAILED probe costs up to ~15 s regardless of cadence —
                # degraded = 2 x ~15 s = ~30 s, longer than the unit's
                # self-recovery. The BONSAI2_PROBE_TIMEOUT_S=3 hook
                # (docs/two-red-gpu-tests-fix.md) caps the probe's socket
                # timeout so a dead engine's failed probe fails in ~3 s
                # instead of ~15 s. At the 2 s cadence, 2 consecutive
                # fails then register in ~6-8 s, inside the ~8-20 s down
                # window the unit's RestartSec=5 + warm reload leaves, so
                # degraded is witnessed naturally, no port holder.
                env["BONSAI2_HEALTH_POLL_S"] = "2"
                env["BONSAI2_PROBE_TIMEOUT_S"] = "3"
                # G3 PR#11 r1 disposition / technical debt: capture the
                # manager's stderr (its _log probe-cycle trace) to a file
                # so a red degraded-witness can be diagnosed from the
                # actual probe timings instead of re-running blind.
                # (No context manager: the handle stays open for the
                # manager's lifetime and is closed in the finally below.)
                mgr_log = health_dir / "manager-2.log"
                mgr_log_fh = open(mgr_log, "w")  # noqa: SIM115
                proc = subprocess.Popen(
                    [sys.executable, str(MANAGER), "--settle", str(SETTLE_S + 90)],
                    stdout=subprocess.PIPE,
                    stderr=mgr_log_fh,
                    text=True,
                    env=env,
                )
                try:
                    health = _poll_health(health_dir, 150.0)
                    self.assertEqual(
                        health["stack"],
                        "healthy",
                        f"boot not healthy before the kill: {json.dumps(health)[:400]}",
                    )
                    pid_before = _engine_pid_in_container("bonsai2-server-0")
                    print(
                        f"\n[2.11 #2] healthy; killing engine pid {pid_before} "
                        f"inside bonsai2-server-0"
                    )

                    kill_t0 = time.monotonic()
                    subprocess.run(
                        [
                            "docker",
                            "exec",
                            "bonsai2-server-0",
                            "kill",
                            "-9",
                            str(pid_before),
                        ],
                        capture_output=True,
                        text=True,
                        check=True,
                    )
                    # Witness the degraded state first (2 consecutive
                    # fails at the BONSAI2_HEALTH_POLL_S=2 s cadence with
                    # the BONSAI2_PROBE_TIMEOUT_S=3 s cap -> ~6-8 s; the
                    # unit's RestartSec=5 + warm reload outlasts that, so
                    # no port holder is needed — the holder's EADDRINUSE
                    # race is gone).
                    # Observe the state over the window (don't assume a
                    # fixed down time — docs/pr11-two-red-gpu-tests.md):
                    # log the instance-0 state timeline to the manager
                    # log file so a failure is diagnosable.
                    degraded_at = None
                    timeline = []
                    t_deadline = kill_t0 + 60.0
                    while time.monotonic() < t_deadline:
                        h = _read_health(health_dir)
                        st0 = (
                            _health_states(h).get(0, ("?", None, False))[0]
                            if h
                            else "?"
                        )
                        off = time.monotonic() - kill_t0
                        if not timeline or timeline[-1][1] != st0:
                            timeline.append((round(off, 1), st0))
                        if st0 == "degraded" and degraded_at is None:
                            degraded_at = time.monotonic()
                            break
                        time.sleep(POLL_S)
                    if degraded_at is None:
                        for off, st in timeline:
                            print(f"[2.11 #2] t={off:6.1f}s state0={st}", flush=True)
                        print(
                            f"[2.11 #2] manager log tail:\n{_tail_lines(mgr_log, 40)}",
                            flush=True,
                        )
                        self.fail(
                            "S2.8 #2 degraded witness did not land within 60 s "
                            f"(timeline: {timeline[-12:]}; manager log: {mgr_log})"
                        )
                    print(
                        f"[2.11 #2] degraded witnessed at {degraded_at - kill_t0:.1f} s"
                    )
                    # The S1.8 #4 15 s budget is the RELOAD half,
                    # measured from the degraded witness (the engine
                    # may already be reloading under the 5 s
                    # RestartSec floor; the prefix is measured, not
                    # charged — the MAP 1.11 convention).
                    reload_t0 = time.monotonic()
                    health = _poll_until(
                        health_dir,
                        90.0,
                        lambda h: (
                            _health_states(h).get(0, ("?", None, False))[0] == "healthy"
                        ),
                        "S2.8 #2 instance 0 healthy after the crash",
                    )
                    recovered_at = time.monotonic()
                    total_s = recovered_at - kill_t0
                    reload_s = recovered_at - reload_t0
                    self.assertLess(
                        reload_s,
                        RELOAD_BUDGET_S,
                        f"S2.8 #2: reload half {reload_s:.1f} s > "
                        f"{RELOAD_BUDGET_S:.0f} s (total {total_s:.1f} s "
                        f"= degraded-witness {degraded_at - kill_t0:.1f} s "
                        f"prefix + reload; sequence witness complete)",
                    )
                    host_pid_after = _container_host_pid("bonsai2-server-0")
                    self.assertGreater(
                        host_pid_after,
                        0,
                        "post-restart unit not running (instance 0)",
                    )
                    # Zero restarts on the OTHER instance: instance 1
                    # stayed healthy and its host PID never changed.
                    health = _poll_until(
                        health_dir,
                        30.0,
                        lambda h: (
                            _health_states(h).get(1, ("?", None, False))[0] == "healthy"
                        ),
                        "instance 1 healthy after the crash",
                    )
                    self._assert_main_pids_intact(baseline_pids)
                    print(
                        f"[2.11 #2] kill -> healthy in {total_s:.1f} s "
                        f"(reload half {reload_s:.1f} s < {RELOAD_BUDGET_S:.0f} s; "
                        f"degraded witnessed; instance 0 host pid now "
                        f"{host_pid_after}; main PIDs intact)"
                    )
                finally:
                    proc.terminate()
                    try:
                        proc.wait(timeout=15.0)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait(timeout=10.0)
                    # The manager's stderr streams to mgr_log (a
                    # diagnostic for the degraded-witness window); close
                    # it after the manager exits so the fd does not leak
                    # into the health-dir teardown.
                    try:
                        mgr_log_fh.close()
                    except (OSError, ValueError):
                        pass
        finally:
            self._teardown_instances()

    # ------------------------------------------------------------------
    # S2.8 #3 — N=0 end-to-end (S2.7: footprint override -> the S2.3
    #          max(0, ...) clamp) + the S2.4 PTQ1_0 fallback retry
    # ------------------------------------------------------------------
    @unittest.skipUnless(_image_present(), f"runtime image {RUNTIME_IMAGE} not present")
    @unittest.skipUnless(_user_systemctl_available(), "user-scope systemd unavailable")
    def test_3_n0_main_only_and_ptq1_fallback(self):
        self._preconditions(self.budget + 1.0)
        fb_budget = _fallback_budget_gb()  # computed live (no hardcoded copy)
        # G3 PR#11 r1 (DOD #4, #3 root cause): the footprints are computed
        # from the LIVE MemTotal (the manager's read_total_gb reads the
        # same value) instead of baked box-size constants — this box's
        # MemTotal is 127.5 GB, not the SPEC's 121.6 worked example, and
        # a baked 100.0 GB footprint left free 19.54 GB -> N'=1, so the
        # S2.4 retry (which only fires at N'=0) never ran. Half A: free
        # ~1.0 GB (deep in N'=0). Half B: free = the MIDPOINT of
        # [fallback budget, PQ2_0 budget) — strictly below the 14.78 GB
        # PQ2_0 budget (planning tier N'=0) but above the 13.46 GB
        # fallback budget (the S2.4 retry grants instance 0).
        total = _mem_total_gb()
        fp_a = total - 8.0 - 1.0
        free_b = (round(fb_budget, 2) + 14.78) / 2.0
        fp_b = total - 8.0 - free_b
        print(
            f"[2.11 #3] live MemTotal {total:.2f} GB: half A footprint "
            f"{fp_a:.2f} (free {total - fp_a - 8.0:.2f} GB -> N'=0 clamp); "
            f"half B footprint {fp_b:.2f} (free {free_b:.2f} GB -> "
            f"N'=0 at PQ2_0, S2.4 fallback grants)"
        )
        with tempfile.TemporaryDirectory(prefix="bonsai2-211-3-") as td:
            tdir = Path(td)
            # The S2.7 N=0 case: override the main footprint (live
            # MemTotal-based, see above) AND point `container` at a
            # name that does not exist, so the S2.3 live-cgroup read
            # falls back to the recorded footprint (the real main
            # container's live cgroup is ~2 GB asleep and would
            # otherwise grant the full pool).
            srv = tdir / "serving-n0.yaml"
            srv.write_text(
                SERVING_YAML.read_text()
                .replace(
                    "container: qwen38-sglang", "container: qwen38-sglang-missing-211"
                )
                .replace("footprint_gb: 76.4", f"footprint_gb: {fp_a:.2f}")
            )
            self.assertIn(f"footprint_gb: {fp_a:.2f}", srv.read_text())
            # --- half A: no fallback size in the config -> plain
            #     main-only (the S2.3 clamp, no S2.4 retry possible).
            eng = tdir / "engine-n0.yaml"
            eng.write_text(
                re.sub(
                    r"(?m)^  fallback_size_bytes: \d+\n?",
                    "",
                    ENGINE_YAML.read_text(),
                )
            )
            self.assertNotIn("fallback_size_bytes", eng.read_text())
            with tempfile.TemporaryDirectory(prefix="bonsai2-health-211-3a-") as td_a:
                env = self._manager_env(Path(td_a), eng, srv)
                rc, err = self._run_manager(env, SETTLE_S)
                self.assertEqual(
                    rc,
                    0,
                    f"manager exit {rc} (want 0 main-only).\n{err[-1500:]}",
                )
                self.assertRegex(err, r"N'=0(\s|$)", "S2.3 must clamp to N'=0")
                self.assertIn("main-only", err)
                health = _poll_health(Path(td_a), 5.0)
                self.assertEqual(health["stack"], "main-only")
                self.assertEqual(health["instances"], [])
                self.assertEqual(health.get("main_only_reason"), "budget_zero")
                self.assertIs(health["main_healthy"], True)
                self.assertTrue(
                    _no_bonsai_containers(),
                    "main-only boot must not have spawned a container",
                )
                print(
                    "[2.11 #3] N=0 clamp: install exit 0, main-only "
                    "budget_zero, zero instances"
                )
            # --- half B: the S2.4 PTQ1_0 fallback retry (the S2.8 #3
            #     sibling): live-footprint override -> free = free_b
            #     (midpoint of [fallback budget, PQ2_0 budget)) —
            #     still < the PQ2_0 budget 14.78 (N'=0 at the PQ2_0
            #     budget) but >= the S2.4 fallback budget (~13.46) ->
            #     the retry rewrites instance 0's unit to the PTQ1_0
            #     quant + the fallback budget and spawns it live (the
            #     image bakes both quants, S1.3/S6.1).
            srv2 = tdir / "serving-fb.yaml"
            srv2.write_text(
                SERVING_YAML.read_text()
                .replace(
                    "container: qwen38-sglang", "container: qwen38-sglang-missing-211"
                )
                .replace("footprint_gb: 76.4", f"footprint_gb: {fp_b:.2f}")
            )
            self._install_units()
            try:
                with tempfile.TemporaryDirectory(
                    prefix="bonsai2-health-211-3b-"
                ) as td_b:
                    env = self._manager_env(Path(td_b), ENGINE_YAML, srv2)
                    rc, err = self._run_manager(env, SETTLE_S)
                    self.assertEqual(
                        rc,
                        0,
                        f"fallback manager exit {rc} (want 0).\n{err[-2000:]}",
                    )
                    self.assertIn("PTQ1_0 fallback", err)
                    health = _poll_health(Path(td_b), 5.0)
                    self.assertEqual(health["stack"], "healthy")
                    self.assertEqual(len(health["instances"]), 1)
                    inst0 = health["instances"][0]
                    self.assertEqual(inst0["i"], 0)
                    self.assertEqual(inst0["state"], "healthy")
                    # The fallback unit ran the PTQ1_0 model at the
                    # fallback budget (the live unit rewrites): the
                    # unit file carries the PTQ1_0 --model + the
                    # fallback --memory flags (the manager's rewrite
                    # prints the budget at 2 decimals — the same
                    # rounding docker gets), and the container's
                    # docker-parsed cap matches.
                    unit0 = self.user_unit_dir / "bonsai2-server@0.service"
                    unit_text = unit0.read_text()
                    self.assertIn(
                        f"--model /opt/bonsai2/models/{self.fallback_file} ",
                        unit_text,
                        "unit 0 not rewritten to the PTQ1_0 model",
                    )
                    fb_rounded = round(fb_budget, 2)
                    self.assertIn(
                        f"--memory {fb_rounded}g ",
                        unit_text,
                        f"unit 0 not rewritten to the S2.4 fallback "
                        f"budget ({fb_rounded}g)",
                    )
                    ins = json.loads(
                        subprocess.run(
                            ["docker", "inspect", "bonsai2-server-0"],
                            capture_output=True,
                            text=True,
                            check=True,
                        ).stdout
                    )[0]
                    self.assertEqual(
                        ins["HostConfig"]["Memory"],
                        int(fb_rounded * 2**30),
                        "fallback cap != the fallback budget as docker parses it",
                    )
                    self.assertIn(
                        f"/opt/bonsai2/models/{self.fallback_file}",
                        json.dumps(ins["Config"]["Cmd"]),
                        "container not running the PTQ1_0 model",
                    )
                    # The quant record landed in the engine YAML the
                    # manager was fed (the tracked template under the
                    # @gpu harness — restored in finally).
                    self.assertIn("quant_in_use: PTQ1_0", ENGINE_YAML.read_text())
                    print(
                        f"[2.11 #3] S2.4 fallback: PTQ1_0 instance 0 "
                        f"live at the {fb_rounded} g cap, quant_in_use "
                        f"recorded"
                    )
            finally:
                self._teardown_instances()
                subprocess.run(
                    ["git", "checkout", "--", "etc/subagent-engine.yaml"],
                    cwd=REPO_ROOT,
                    capture_output=True,
                    check=False,
                )

    # ------------------------------------------------------------------
    # S2.8 #4 — 5-minute drift simulation -> ctx-reduce at the halved
    #          ctx, healthy < 15 s, zero restarts on the other
    #          instance, N unchanged
    # ------------------------------------------------------------------
    @unittest.skipUnless(_image_present(), f"runtime image {RUNTIME_IMAGE} not present")
    @unittest.skipUnless(_user_systemctl_available(), "user-scope systemd unavailable")
    def test_4_drift_ctx_halve(self):
        self._preconditions(2 * self.budget + 1.0)
        cap_bytes = int(self.budget * 1e9)  # the S2.2 --memory cap, decimal GB
        halved_ctx = self.ctx // 2  # the S2.5 halve at the template ctx
        try:
            self._install_units()
            with tempfile.TemporaryDirectory(prefix="bonsai2-health-211-4-") as td:
                health_dir = Path(td)
                env = self._manager_env(health_dir, ENGINE_YAML, SERVING_YAML)
                # The S2.5 5-minute recheck cadence shrunk via the
                # manager's documented env hook: the S2.5 sustained-30 s
                # clock spans rechecks, so ~7 rechecks at the 6 s
                # cadence fit the halve inside the test window.
                env["BONSAI2_DRIFT_RECHECK_S"] = str(DRIFT_RECHECK_S)
                proc = subprocess.Popen(
                    [sys.executable, str(MANAGER), "--settle", str(SETTLE_S + 120)],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    env=env,
                )
                try:
                    health = _poll_health(health_dir, 150.0)
                    self.assertEqual(
                        health["stack"],
                        "healthy",
                        f"boot not healthy before the drift: {json.dumps(health)[:400]}",
                    )
                    pid0_before = _container_host_pid("bonsai2-server-0")
                    pid1_before = _container_host_pid("bonsai2-server-1")
                    self.assertGreater(pid0_before, 0, "instance 0 not running")
                    self.assertGreater(pid1_before, 0, "instance 1 not running")
                    cg0 = _cgroup_dir_for("bonsai2-server-0")
                    self.assertIsNotNone(cg0, "instance 0 cgroup not found")
                    # G3 PR#11 r1 (DOD #4, #4 root cause, 2nd iteration):
                    # the allocation must land memory.current STRICTLY
                    # BETWEEN the manager's decimal cap (budget_gb * 1e9,
                    # what the drift watch compares against) and the
                    # kernel's BINARY cgroup cap (memory.max — the
                    # docker `g` suffix parses GiB, 14.78g = 15.87e9
                    # bytes). The first re-run's target of
                    # cap+1 GiB = 15.85e9 already exceeded the kernel
                    # cap once base usage (~1.35 GB) was added -> the
                    # holder got OOM-killed (rc=137) and the charge
                    # never sustained. So: read the live current,
                    # target 95% of the kernel cap, allocate the delta.
                    kernel_cap = int((cg0 / "memory.max").read_text().strip())
                    base_now = int((cg0 / "memory.current").read_text().strip())
                    target_now = int(0.95 * kernel_cap)
                    alloc_bytes = target_now - base_now
                    self.assertGreater(
                        alloc_bytes,
                        cap_bytes - base_now,
                        f"allocation {alloc_bytes} B would not clear the "
                        f"manager cap {cap_bytes} B (base {base_now} B)",
                    )
                    print(
                        f"\n[2.11 #4] healthy; charging {alloc_bytes} B "
                        f"(base {base_now} B -> target {target_now} B; "
                        f"kernel cap {kernel_cap} B > manager cap "
                        f"{cap_bytes} B) into instance 0's cgroup "
                        f"(real in-container allocation)"
                    )
                    self.assertTrue(
                        _inject_cgroup_pressure(
                            "bonsai2-server-0",
                            cg0,
                            alloc_bytes,
                            cap_bytes,
                        ),
                        "S2.8 #4: the cgroup charge did not land — "
                        "memory.current did not rise over the manager "
                        "cap; the drift watch cannot see it",
                    )
                    # The halve event: the S2.5 clock starts at the
                    # first over-cap recheck and fires at the first
                    # recheck > 30 s later. Poll the document for
                    # ctx_reduced.
                    health = _poll_until(
                        health_dir,
                        90.0,
                        lambda h: (
                            _health_states(h).get(0, ("?", None, False))[2] is True
                        ),
                        "S2.8 #4 ctx_reduced event",
                    )
                    st0 = _health_states(health)[0]
                    self.assertEqual(
                        st0[1],
                        halved_ctx,
                        f"halved ctx wrong: {st0[1]} (want {halved_ctx})",
                    )
                    self.assertTrue(st0[2], "ctx_reduced must be true")
                    # The unit file carries the halved ctx (the S2.5
                    # in-place rewrite; the manager restarted the unit
                    # with it).
                    unit0 = self.user_unit_dir / "bonsai2-server@0.service"
                    self.assertIn(
                        f"--ctx-size {halved_ctx} ",
                        unit0.read_text(),
                        "unit 0 not rewritten to the halved ctx",
                    )
                    # A real in-place restart: instance 0's host PID
                    # changed; instance 1's did NOT (zero restarts on
                    # the other instance) + instance 1 stayed healthy.
                    pid0_after = _container_host_pid("bonsai2-server-0")
                    pid1_after = _container_host_pid("bonsai2-server-1")
                    self.assertNotEqual(
                        pid0_after,
                        pid0_before,
                        f"instance 0 host pid unchanged "
                        f"({pid0_before}) — no in-place restart witnessed",
                    )
                    self.assertEqual(
                        pid1_after,
                        pid1_before,
                        "instance 1's host pid changed — the drift restart "
                        "touched the other instance",
                    )
                    health = _poll_until(
                        health_dir,
                        30.0,
                        lambda h: (
                            _health_states(h).get(1, ("?", None, False))[0] == "healthy"
                        ),
                        "instance 1 healthy after the drift",
                    )
                    # N unchanged: both instances still in the document.
                    self.assertEqual(len(health["instances"]), 2)
                    # Healthy again < 15 s at the halved ctx (the fresh
                    # load is the warm-cache reload, the MAP 1.11 band).
                    _poll_until(
                        health_dir,
                        RELOAD_BUDGET_S,
                        lambda h: (
                            _health_states(h).get(0, ("?", None, False))[0] == "healthy"
                        ),
                        "S2.8 #4 instance 0 healthy at the halved ctx",
                    )
                    print(
                        f"[2.11 #4] drift: ctx {self.ctx} -> {halved_ctx} "
                        f"(ctx_reduced=true, unit rewritten, in-place "
                        f"restart {pid0_before} -> {pid0_after}), "
                        f"instance 1 untouched (pid {pid1_before} stable, "
                        f"healthy), N=2 unchanged"
                    )
                finally:
                    proc.terminate()
                    try:
                        proc.wait(timeout=15.0)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait(timeout=10.0)
        finally:
            self._teardown_instances()

    # ------------------------------------------------------------------
    # S2.8 #5 — unload/reload cycle (S2.7 test): unload --all ->
    #          main-only user_unloaded, no auto-reload on the drift
    #          cycle; reload --all -> N healthy within boot_target_s
    # ------------------------------------------------------------------
    @unittest.skipUnless(_image_present(), f"runtime image {RUNTIME_IMAGE} not present")
    @unittest.skipUnless(_user_systemctl_available(), "user-scope systemd unavailable")
    def test_5_unload_reload_cycle(self):
        baseline_pids = self._preconditions(2 * self.budget + 1.0)
        try:
            self._install_units()
            with tempfile.TemporaryDirectory(prefix="bonsai2-health-211-5-") as td:
                health_dir = Path(td)
                control_dir = health_dir / "control"
                env = self._manager_env(health_dir, ENGINE_YAML, SERVING_YAML)
                # The drift recheck is shrunk (the no-respawn half needs
                # a couple of rechecks after the unload to be
                # witnessed): the S3.2 path itself is cadence-agnostic.
                env["BONSAI2_DRIFT_RECHECK_S"] = str(DRIFT_RECHECK_S)
                proc = subprocess.Popen(
                    [sys.executable, str(MANAGER), "--settle", str(SETTLE_S + 180)],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    env=env,
                )
                try:
                    health = _poll_health(health_dir, 150.0)
                    self.assertEqual(
                        health["stack"],
                        "healthy",
                        f"boot not healthy before the unload: {json.dumps(health)[:400]}",
                    )
                    cg0 = _cgroup_dir_for("bonsai2-server-0")
                    self.assertIsNotNone(
                        cg0, "instance 0 cgroup not found (pre-unload)"
                    )

                    # --- UNLOAD --all ---
                    _write_control_request(
                        control_dir,
                        {"type": "unload", "instances": "all", "request_id": "u1"},
                    )
                    ack = _wait_ack(control_dir, "u1", 90.0)
                    self.assertEqual(ack.get("state"), "ok", f"unload ack: {ack}")
                    self.assertEqual(
                        sorted(ack.get("unloaded", [])),
                        [0, 1],
                        f"unload ack: {ack}",
                    )
                    health = _poll_until(
                        health_dir,
                        30.0,
                        lambda h: h.get("main_only_reason") == "user_unloaded",
                        "S2.8 #5 main-only user_unloaded after unload",
                    )
                    self.assertEqual(health["stack"], "main-only")
                    self.assertEqual(health["instances"], [])
                    for i in (0, 1):
                        cg = _cgroup_dir_for(f"bonsai2-server-{i}")
                        self.assertIsNone(
                            cg,
                            f"instance {i} cgroup still present after unload "
                            "(S2.7: assert VRAM release — instance cgroups gone)",
                        )
                    print(
                        "[2.11 #5] unload --all -> main-only user_unloaded, cgroups gone"
                    )

                    # --- No auto-reload on the drift cycle: a couple of
                    #     rechecks with nothing running must NOT respawn
                    #     (`bunloaded` indexes are not in
                    #     runtime.running, so the drift watch's loop
                    #     body never reaches them).
                    time.sleep(2 * DRIFT_RECHECK_S + 5.0)
                    health = _read_health(health_dir) or _poll_health(health_dir, 10.0)
                    self.assertEqual(
                        health.get("main_only_reason"),
                        "user_unloaded",
                        f"drift cycle re-spawned after unload: "
                        f"{json.dumps(health)[:400]}",
                    )
                    self.assertEqual(
                        health["instances"], [], "drift cycle re-spawned instances"
                    )
                    print("[2.11 #5] drift rechecks after unload: no re-spawn")

                    # --- RELOAD --all: healthy again within boot_target_s ---
                    boot_target = _boot_target_s_from(SERVING_YAML)
                    t_reload = time.monotonic()
                    _write_control_request(
                        control_dir,
                        {"type": "reload", "instances": "all", "request_id": "r1"},
                    )
                    ack = _wait_ack(control_dir, "r1", 300.0)
                    self.assertEqual(ack.get("state"), "ok", f"reload ack: {ack}")
                    self.assertEqual(
                        sorted(ack.get("reloaded", [])),
                        [0, 1],
                        f"reload ack: {ack}",
                    )
                    reload_s = time.monotonic() - t_reload
                    self.assertLessEqual(
                        reload_s,
                        boot_target,
                        f"S2.8 #5: reload took {reload_s:.1f} s > "
                        f"boot_target_s {boot_target:.0f} s",
                    )
                    health = _poll_until(
                        health_dir,
                        30.0,
                        lambda h: (
                            h.get("stack") == "healthy"
                            and len(h.get("instances", [])) == 2
                            and all(
                                s[0] == "healthy" for s in _health_states(h).values()
                            )
                        ),
                        "S2.8 #5 both instances healthy after reload",
                    )
                    self.assertNotIn(
                        "main_only_reason",
                        health,
                        "main_only_reason must be absent with >=1 instance",
                    )
                    self._assert_main_pids_intact(baseline_pids)
                    print(
                        f"[2.11 #5] reload --all in {reload_s:.1f} s "
                        f"-> 2 healthy (<= boot_target_s {boot_target:.0f} s)"
                    )
                finally:
                    proc.terminate()
                    try:
                        proc.wait(timeout=15.0)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait(timeout=10.0)
        finally:
            self._teardown_instances()


if __name__ == "__main__":
    unittest.main()
