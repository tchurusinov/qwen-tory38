"""The S3.7 manager stub: an in-process stand-in for `bin/bonsai2-manager`
for the POOL-CONTROL half of the S3.2 protocol (the unload/reload
control/ queue) + the health.json it owns.

Why a stub, not the real manager: the real manager is a systemd
service that shells out to `systemctl` / `docker` and probes real
ports (S2.2/S2.5). For the hermetic suite we only need the manager's
S3.2 surface that the harness talks to:

  * health.json (the S3.1 document the harness routes on), and
  * the control/ queue: a request file `{type, instances, request_id}`
    gets an ack `ack-<request_id>.json` `{type, request_id, ts,
    state, unloaded/reloaded, detail}` and the request is renamed
    `.json.done` (the exact shape the real manager's
    `process_control` + `_write_ack` write — MAP 2.10 / S3.2).

The stub is a plain class (no unittest coupling), mirroring the
`MockEngine` convention in tests/fixtures/mock_engine.py: a test holds
it, points `BONSAI2_HEALTH_DIR` at its dir, runs a `bonsai2-unload` /
`bonsai2-reload` in-process, then calls `.service()` once (one health
cycle, the manager's `health_poll_s` cadence) and asserts the ack +
the health.json change.

Canned behavior:

  * `add_instance(i, port, state, ctx, total)` — grow the health doc.
  * `service()` — one pass over `control/`: process each request,
    write the ack, mark it done. Unload: a running/spawned index ->
    `bunloaded` + recorded in `unloaded`; a `bunloaded`/never-spawned
    index is a no-op for that index (the S3.2 idempotent case). Reload:
    a `bunloaded` index whose allocation tier grants -> `healthy` +
    `reloaded`; a tier reject -> stays `bunloaded` + `state: "partial"`.
  * `set_dead()` — stop processing (no acks) -> the harness's
    exit-84 path (the S3.2 "a dead manager -> exit 84" case).
  * `set_main_only(reason)` — zero running instances -> the S2.4
    main-only document (`main_only_reason` present).

The allocation tier is a pure hook (`grant_reload(i) -> bool`), so a
test can force the partial case deterministically (the S1.4
`WARN_VRAM_SHORTFALL` shape) without a live memory read.
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Self

# S3.2 state set the manager may put an instance in (the S3.1 instance
# state strings). `bunloaded` is the explicit-unload marker (S3.2).
VALID_STATES = ("healthy", "degraded", "bdown", "bfailed", "bunloaded")


class MockManager:
    """One canned manager: `MockManager(health_dir)` then
    `.add_instance(...)`, `.service()`. `.health_doc()` returns the
    current S3.1 document. Owns the `control/` subdir of
    `health_dir` (the same dir the harness's `BONSAI2_HEALTH_DIR`
    points at, so the request/ack + health.json co-locate like the
    real manager)."""

    def __init__(self, health_dir: str | Path):
        self.health_dir = Path(health_dir)
        self.control = self.health_dir / "control"
        self._instances: dict[int, dict] = {}
        self._spawned: set[int] = set()
        self._dead = False
        # i -> grant verdict for the reload allocation tier (default
        # grant; a test flips one False to force the partial case).
        self._grant_reload: dict[int, bool] = {}
        self._main_only_reason: str | None = None
        self._loop_thread = None
        self._stop = False
        self._write_health()  # the S3.1 document exists before the first request

    # ------------------------------------------------------------------
    # document + state
    # ------------------------------------------------------------------
    def add_instance(
        self,
        i: int,
        port: int,
        state: str = "healthy",
        ctx: int = 262144,
        total: int = 8,
        spawned: bool = True,
        bfailed_reason: str | None = None,
    ) -> None:
        if state not in VALID_STATES:
            raise ValueError(f"bad canned state {state!r}")
        self._instances[i] = {
            "i": i,
            "port": port,
            "state": state,
            "ctx": ctx,
            "ctx_reduced": False,
            "slots_in_use": 0,
            "slots_total": total,
        }
        if bfailed_reason:
            self._instances[i]["bfailed_reason"] = bfailed_reason
        if spawned and state != "bunloaded":
            self._spawned.add(i)
        self._write_health()

    def set_state(self, i: int, state: str) -> None:
        if i not in self._instances:
            raise KeyError(f"unknown instance {i}")
        if state not in VALID_STATES:
            raise ValueError(f"bad canned state {state!r}")
        self._instances[i]["state"] = state
        if state == "bunloaded":
            self._spawned.discard(i)
        else:
            self._spawned.add(i)
        self._write_health()

    def grant_reload(self, i: int, granted: bool = True) -> None:
        """Force the reload allocation-tier verdict for index `i`
        (the S1.4 gate the real manager runs live)."""
        self._grant_reload[i] = granted

    def set_dead(self, dead: bool = True) -> None:
        self._dead = dead

    # ------------------------------------------------------------------
    # the manager's steady-loop (process_control runs once per cycle)
    # ------------------------------------------------------------------
    def start_loop(self, interval_s: float = 0.02) -> None:
        """A daemon thread that services the control/ queue on a short
        cadence — the model of the manager's steady-loop probe cycle
        (the real manager's `process_control` runs once per
        `health_poll_s`). The harness command blocks on the ack-wait,
        so the service pass must run CONCURRENTLY for the command to
        observe the ack within its budget."""
        if getattr(self, "_loop_thread", None) is not None:
            return

        def _run() -> None:
            while not self._stop:
                self.service()
                time.sleep(interval_s)

        self._stop = False
        self._loop_thread = threading.Thread(target=_run, daemon=True)
        self._loop_thread.start()

    def stop_loop(self) -> None:
        if getattr(self, "_loop_thread", None) is None:
            return
        self._stop = True
        t = self._loop_thread
        self._loop_thread = None
        t.join(timeout=2.0)

    @property
    def dead(self) -> bool:
        return self._dead

    def health_doc(self) -> dict:
        return self._read_health()

    def _read_health(self) -> dict:
        return json.loads((self.health_dir / "health.json").read_text())

    def _write_health(self) -> None:
        self.health_dir.mkdir(parents=True, exist_ok=True)
        self.control.mkdir(parents=True, exist_ok=True)
        running = [i for i in sorted(self._instances) if i in self._spawned]
        stack = "healthy"
        if not running:
            stack = "main-only"
        elif any(self._instances[i]["state"] in ("bdown", "bfailed") for i in running):
            stack = "degraded"
        doc: dict = {
            "stack": stack,
            "instances": [self._instances[i] for i in sorted(self._instances)],
            "main_endpoint": "http://127.0.0.1:30001",
            "main_healthy": True,
        }
        if not running:
            doc["main_only_reason"] = self._main_only_reason or "user_unloaded"
        (self.health_dir / "health.json").write_text(json.dumps(doc, indent=2) + "\n")

    # ------------------------------------------------------------------
    # the S3.2 control/ queue (the manager's process_control shape)
    # ------------------------------------------------------------------
    def service(self) -> list[dict]:
        """One health-cycle pass over the control/ queue (the manager's
        `process_control` runs once per `health_poll_s`). Returns the
        list of acks written this pass. A dead manager writes no acks
        (the harness's exit-84 path)."""
        if self._dead:
            return []
        if not self.control.is_dir():
            return []
        acks = []
        for req_file in sorted(self.control.iterdir()):
            if req_file.suffix != ".json" or not req_file.is_file():
                continue
            if req_file.name.startswith("ack-"):
                continue  # an already-written ack — leave it for the harness
            try:
                req = json.loads(req_file.read_text())
            except (OSError, json.JSONDecodeError):
                continue
            rtype = req.get("type")
            targets = req.get("instances")
            req_id = str(req.get("request_id") or req_file.stem)
            ack: dict = {"type": rtype, "request_id": req_id, "ts": time.time()}
            if rtype not in ("unload", "reload") or not (
                targets == "all"
                or isinstance(targets, list)
                and all(type(t) is int for t in targets)
            ):
                ack.update(
                    state="error",
                    detail="bad request: type must be unload|reload; instances must be a list of int indexes or 'all'",
                )
                self._write_ack(req_file, ack)
                acks.append(ack)
                continue
            idxs = [i for i in sorted(self._instances) if 0 <= i < len(self._instances)]
            if targets != "all":
                idxs = [i for i in idxs if i in targets]
            if rtype == "unload":
                done = []
                for i in idxs:
                    e = self._instances[i]
                    if e["state"] == "bunloaded":
                        continue  # idempotent no-op (S3.2)
                    if i not in self._spawned:
                        continue  # not a managed index — no-op
                    e["state"] = "bunloaded"
                    self._spawned.discard(i)
                    done.append(i)
                if not self._spawned:
                    self._main_only_reason = "user_unloaded"
                self._write_health()
                ack.update(unloaded=sorted(done), state="ok")
            else:  # reload
                reloaded: list[int] = []
                partial = False
                for i in idxs:
                    e = self._instances[i]
                    if e["state"] != "bunloaded":
                        continue  # idempotent: only unloaded indexes reload
                    if not self._grant_reload.get(i, True):
                        partial = True
                        continue  # tier reject -> stays bunloaded (S1.4)
                    e["state"] = "healthy"
                    self._spawned.add(i)
                    reloaded.append(i)
                if self._spawned:
                    self._main_only_reason = None
                self._write_health()
                ack.update(
                    reloaded=sorted(reloaded),
                    state="partial" if partial else "ok",
                    detail="WARN_VRAM_SHORTFALL" if partial else None,
                )
            self._write_ack(req_file, ack)
            acks.append(ack)
        return acks

    def _write_ack(self, req_file: Path, ack: dict) -> None:
        req_id = ack.get("request_id") or req_file.stem
        ack_file = self.control / f"ack-{req_id}.json"
        tmp = ack_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(ack, indent=2) + "\n")
        os.replace(tmp, ack_file)
        try:
            os.replace(req_file, req_file.with_suffix(".json.done"))
        except OSError:
            pass

    def __enter__(self) -> Self:
        self._write_health()
        return self

    def __exit__(self, *exc) -> None:
        pass
