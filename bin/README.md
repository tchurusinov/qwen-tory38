# bin/ — source tree for /opt/bonsai2/bin/ (SPEC S3.2, S1.6, S2.2)

Everything in this directory is deployed by install.sh to
/opt/bonsai2/bin/ on the host (layout MAP 0.1). The repo tree mirrors
the host layout 1:1 so source and deploy paths never diverge in naming.

Lands here (later MAP phases):

- `bonsai2-manager` — the instance manager (MAP 2.1, S2.1/S2.2); runs
  from the `bonsai2-sglang.service` unit.
- `bonsai2-spawn`, `bonsai2-steer`, `bonsai2-cancel`,
  `bonsai2-status`, `bonsai2-list-instances`, `bonsai2-collect`,
  `bonsai2-bench`, `bonsai2-unload`, `bonsai2-reload`,
  `bonsai2-health` — the S3.2 control scripts, the main-agent API
  (MAP 3.1–3.12).
- `llama-server` — NOT in the repo: it is baked into the
  `bonsai2-server:<tag>` image (S1.4/S6.1); a host copy lives at
  /opt/bonsai2/bin/llama-server for ops (S1.2, MAP 1.2).

Empty until MAP 2.1/Phase 3 add the first script; this README keeps the
directory tracked in git so the layout is visible from day one.
