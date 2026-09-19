#!/usr/bin/env python3
"""drawio generator: dgx-spark-qwen38 serving stack, end to end (2026-09-19).

Re-runnable: edit the data below, run
    python3 diagram_qwen38_stack.py /home/blckwll/prod/qwen-tory38/docs/dgx-spark-qwen38-stack.drawio
"""
import sys

# helpers (from the drawio-diagrams skill template)
import html
import xml.etree.ElementTree as ET

_counter = [1]

def _id():
    _counter[0] += 1
    return f"n{_counter[0]}"

def esc(s):
    return html.escape(str(s), quote=True)

PALETTE = {
    "blue":   ("#dcebf8", "#083763"),
    "green":  ("#d9f0d3", "#004d00"),
    "orange": ("#fde8cd", "#7a3d00"),
    "red":    ("#f6d9d9", "#990000"),
    "gray":   ("#eeeeee", "#333333"),
    "none":   ("none",    "#555555"),
}

_cells = []

def box(x, y, w, h, label, color="gray", font_size=11, bold=False, dashed=False):
    fill, stroke = PALETTE[color]
    d = "dashed=1;" if dashed else ""
    b = "fontStyle=1;" if bold else ""
    _cells.append(
        f'<mxCell id="{_id()}" value="{esc(label)}" '
        f'style="rounded=1;whiteSpace=wrap;html=1;fillColor={fill};strokeColor={stroke};{d}{b}fontSize={font_size};" '
        f'vertex="1" parent="1">'
        f'<mxGeometry x="{x}" y="{y}" width="{w}" height="{h}" as="geometry"/></mxCell>'
    )

def lane(x, y, w, h, color="blue"):
    fill, stroke = PALETTE[color]
    _cells.append(
        f'<mxCell id="{_id()}" value="" '
        f'style="rounded=1;whiteSpace=wrap;html=1;fillColor=none;strokeColor={stroke};" '
        f'vertex="1" parent="1">'
        f'<mxGeometry x="{x}" y="{y}" width="{w}" height="{h}" as="geometry"/></mxCell>'
    )

def text(x, y, w, label, font_size=11, color="#333333", bold=False):
    b = "fontStyle=1;" if bold else ""
    _cells.append(
        f'<mxCell id="{_id()}" value="{esc(label)}" '
        f'style="text;html=1;align=left;{b}fontColor={color};fontSize={font_size};" '
        f'vertex="1" parent="1">'
        f'<mxGeometry x="{x}" y="{y}" width="{w}" height="30" as="geometry"/></mxCell>'
    )

def edge(x1, y1, x2, y2, label="", dashed=False, color="#555555"):
    d = "dashed=1;" if dashed else ""
    _cells.append(
        f'<mxCell id="{_id()}" value="{esc(label)}" '
        f'style="edgeStyle=orthogonal;rounded=1;html=1;strokeWidth=1.5;strokeColor={color};{d}endArrow=block;fontSize=10;" '
        f'edge="1" parent="1">'
        f'<mxGeometry relative="1" as="geometry">'
        f'<mxPoint x="{x1}" y="{y1}" as="sourcePoint"/>'
        f'<mxPoint x="{x2}" y="{y2}" as="targetPoint"/>'
        f'</mxGeometry></mxCell>'
    )

def edge_wp(points, label="", dashed=False, color="#555555"):
    """Edge with explicit corners: points[0] = source, points[-1] = target,
    anything in between = waypoints. Use when the straight orthogonal route
    would cross a box."""
    d = "dashed=1;" if dashed else ""
    src, tgt = points[0], points[-1]
    wps = "".join(f'<mxPoint x="{px}" y="{py}"/>' for (px, py) in points[1:-1])
    arr = f'<Array as="points">{wps}</Array>' if wps else ""
    _cells.append(
        f'<mxCell id="{_id()}" value="{esc(label)}" '
        f'style="edgeStyle=orthogonal;rounded=1;html=1;strokeWidth=1.5;strokeColor={color};{d}endArrow=block;fontSize=10;" '
        f'edge="1" parent="1">'
        f'<mxGeometry relative="1" as="geometry">'
        f'<mxPoint x="{src[0]}" y="{src[1]}" as="sourcePoint"/>'
        f'{arr}'
        f'<mxPoint x="{tgt[0]}" y="{tgt[1]}" as="targetPoint"/>'
        f'</mxGeometry></mxCell>'
    )

def emit(path, width, height):
    body = "\n".join(_cells)
    doc = f'''<mxfile host="app.diagrams.net" agent="hermes-agent">
  <diagram id="diagram" name="page">
    <mxGraphModel dx="1400" dy="800" grid="0" gridSize="10" guides="1" tooltips="1" connect="1" arrows="1" fold="1" page="0" pageScale="1" pageWidth="{width}" pageHeight="{height}" math="0" shadow="0">
      <root>
        <mxCell id="0"/>
        <mxCell id="1" parent="0"/>
{body}
      </root>
    </mxGraphModel>
  </diagram>
</mxfile>'''
    with open(path, "w") as f:
        f.write(doc)
    ET.parse(path)
    print(f"ok {path} ({len(_cells)} cells)")

# ---------------------------------------------------------------- content

def build():
    # Title
    text(40, 12, 1300,
         "dgx-spark-qwen38 serving stack - end to end (live state 2026-09-19)",
         font_size=20, color="#083763", bold=True)
    text(40, 36, 1360,
         "One GB10 box. Agent traffic: client -> keepalive proxy :30001 -> SGLang engine :30000. "
         "Control: browser -> Spark Cockpit :30090 -> sudo exact-argv -> systemd. Lane order below = request path.",
         font_size=11, color="#555555")

    # ============ LANE 1: clients ============
    lane(40, 70, 1360, 140, "blue")
    text(60, 80, 1320, "1. Clients (agent CLIs must use the proxy port 30001, not the engine port)",
         font_size=14, color="#083763", bold=True)
    box(60, 112, 380, 84,
        "Agent CLIs - opencode (oc), Claude Code, Hermes\n"
        "provider config -> :30001 (opencode.json)\n"
        "chat template patched for agentic clients, limits from oc-limits.sh",
        "blue")
    box(460, 112, 380, 84,
        "Any OpenAI / Anthropic client (SDKs, Open WebUI, curl)\n"
        "OpenAI :30000/v1/chat/completions - Anthropic :30000/v1/messages\n"
        "Bearer ~/.config/qwen38/api-key (Authorization header only)",
        "blue")
    box(860, 112, 520, 84,
        "Browser (laptop / phone, tailnet or loopback)\n"
        "Spark Cockpit UI + Agent tab (framed opencode web)\n"
        "login = the api-key; CSRF token on every mutating POST",
        "blue")

    # ============ LANE 2: keepalive proxy ============
    lane(40, 240, 1360, 140, "gray")
    text(60, 250, 1320, "2. Keepalive proxy - the only component that reads bytes it does not control",
         font_size=14, color="#7a3d00", bold=True)
    box(60, 282, 1320, 84,
        "keepalive-proxy.py - systemd qwen38-keepalive (enabled, running) - listens :30001, upstream 127.0.0.1:30000\n"
        "SSE keepalive at event boundaries (KEEPALIVE_S=10, MAX_SILENCE_S=3600)  |  zombie abort ordering: x-override-rid, abort BEFORE close, else drain\n"
        "oversize guard: counts via /tokenize, prices images from headers  |  corruption tripwire  |  one log line per request (bytes, first/last event, outcome)",
        "orange")

    # ============ LANE 3: engine ============
    lane(40, 410, 1360, 300, "green")
    text(60, 420, 1320,
         "3. SGLang engine :30000 - docker container, --gpus all, host net, --memory 100g. Exactly one lane enabled at a time.",
         font_size=14, color="#004d00", bold=True)
    box(60, 452, 860, 88,
        "27B lane - qwen38-sglang.service   [ENABLED, running now]\n"
        "edp1096 abliterated Qwen3.8-27B NVFP4 (pin rev 21565d3) + DFlash2 speculative draft, depth 16 (lossless: target verifies every draft)\n"
        "262K ctx | --mem-fraction-static 0.50 | autotuner disabled | --sleep-on-idle | 71.4 tok/s greedy median class",
        "green")
    box(60, 552, 860, 88,
        "flash lane - qwen38-flash.service   [installed, currently disabled]\n"
        "Qwen3.8-Flash-Next 176B hybrid MoE NVFP4 + NEXTN/MTP draft | 262K window on ONE box\n"
        "47.7 GiB PLE N-gram table served from sparse file on NVMe (rewritten each boot, ~10 min) | targets: flash / flash-uncensored / flash-nvda",
        "green", dashed=True)
    box(940, 452, 440, 88,
        "Checkpoint storage (pinned twice: image digest + HF revision, same --revision passed to the server)\n"
        "~/.cache/huggingface  -  weights\n"
        "~/flashnext-ple - sparse PLE table (flash lane only)",
        "gray")
    box(940, 552, 440, 88,
        "GB10 unified-memory guard (the trap that bricks this box)\n"
        "SGLang accounting misses 25-40 GB of transient allocations\n"
        "--memory 100g bounds host RSS only; the fraction is the real guard:\n"
        "0.50 native / 0.76 for the 1M preset",
        "red")

    # ============ LANE 4: control plane ============
    lane(40, 740, 1360, 300, "red")
    text(60, 750, 1320, "4. Control plane - Spark Cockpit (the box is meant to be driven from this page)",
         font_size=14, color="#990000", bold=True)
    box(60, 782, 700, 108,
        "Spark Cockpit - dashboard/cockpit.py - :30090 - qwen38-dashboard.service (enabled, running)\n"
        "single Python process, stdlib only: ThreadingHTTPServer, SSE /api/stream, canvas sparklines\n"
        "state from a REAL generation canary, not /health (a wedged SGLang answers /health fine): ready / loading / wedged / stopped\n"
        "8 tabs: Overview, Agent, Engines, Requests, Machine, Models, Logs, Setup",
        "blue")
    box(60, 906, 700, 120,
        "Action surface - everything privileged is exact-argv, audited, confirm-gated\n"
        "sudoers /etc/sudoers.d/qwen38-cockpit: NOPASSWD, no wildcards, fixed argv lines only\n"
        "from the browser: unit start / stop / restart / enable-disable (one serving unit at a time, enforced)\n"
        "lane switch (stages switch-model.sh, takes effect at restart) | flush prefix cache | abort all | smoke probe\n"
        "COCKPIT_AUTOHEAL=0: restart of a wedged engine is opt-in",
        "red")
    box(780, 782, 560, 108,
        "Agent relay - dashboard/agent_relay.py - :30091\n"
        "browser boundary for the Agent tab: frames opencode's own web UI behind the cockpit session\n"
        "run a session on the box from a laptop or phone, no terminal needed",
        "blue")
    box(780, 906, 560, 120,
        "opencode-web.service - opencode serve 127.0.0.1:4096 (enabled, running)\n"
        "loopback-only; the relay is the only browser-facing path to it\n"
        "default model follows every lane switch",
        "gray")

    # ============ LANE 5: systemd / boot ============
    lane(40, 1070, 1360, 170, "blue")
    text(60, 1080, 1320, "5. systemd - live at startup: every unit is enabled; Restart=always; the engine's long boot does not hold the others",
         font_size=14, color="#083763", bold=True)
    box(60, 1112, 1280, 116,
        "multi-user.target units (all in /etc/systemd/system, rendered by install.sh from the repo's pins)\n"
        "qwen38-sglang (ENABLED, running - boot ~5-7 min, cached kernels)  |  qwen38-flash (installed, disabled - boot ~10-15 min, PLE rewrite)\n"
        "qwen38-keepalive (enabled, running)  |  qwen38-dashboard (enabled, running, Restart=always RestartSec=3)  |  opencode-web (enabled, running)\n"
        "hardening: ProtectSystem=full + ReadWritePaths on the cockpit unit; cockpit has NO dependency on the engine on purpose -\n"
        "it starts at boot even if the engine is down, because reporting that IS its job. Cockpit shows the engine 'loading' until its canaries pass.",
        "blue")

    # ============ LANE 6: repo + state ============
    lane(40, 1270, 1360, 220, "gray")
    text(60, 1280, 1320, "6. Repo and state - what renders the units, and where everything the stack writes lives",
         font_size=14, color="#333333", bold=True)
    box(60, 1312, 620, 160,
        "Repo /home/blckwll/prod/dgx-spark-qwen38 (pinned v1.13)\n"
        "install.sh - converging installer: renders units/configs/templates from pins (never edits); idempotent; step 10/10 = cockpit; ends on the cockpit URL\n"
        "switch-model.sh - surgical: rewrites ONLY the --model-path line, flips boot enablement, stages the switch; never restarts a service itself\n"
        "bench.sh / bench-matrix.sh / tools-check.py / conc-check.py / bench-agent.py - the instruments\n"
        "ci-local.sh - the invariants (target-list spellings agree, pin blocks greppable, sudoers allowlist complete, limits chain, stdlib only)",
        "gray")
    box(720, 1312, 660, 160,
        "State dir ~/.config/qwen38 (the stack's single config home)\n"
        "api-key (0600; Bearer for the engine AND the cockpit login)\n"
        "patched chat templates | deployed keepalive-proxy.py | opencode.json + oc launcher (lifts the 32K output cap)\n"
        "cockpit-secret (0600) | cockpit-events.jsonl | cockpit-history.json | wedge-*.txt forensic dumps | unit *.bak-preupdate\n"
        "cockpit's recipes.py derives 'what this lane should run' from the repo's own text - that is the drift panel",
        "gray")

    # ============ footer: live state ============
    box(40, 1510, 1360, 96,
        "LIVE STATE 2026-09-19 - all four active units running (qwen38-sglang up 10 h).\n"
        "Serving lane: 27B UNCENSORED (edp1096 abliterated NVFP4, pin rev 21565d3, 262K ctx, mem-fraction 0.50, DFlash2 D16).\n"
        "Flash lane installed but disabled. A switch changes which unit is enabled at boot; the :30000 API surface and the :30001 proxy stay put.\n"
        "Engine and cockpit are independent units: the cockpit comes up at startup with or without the engine - same contract as the inference server, plus Restart=always.",
        "gray", font_size=11)

    # ============ edges (gutter-routed; labels placed in verified-empty bands) ============
    # clients -> proxy
    edge(250, 196, 300, 282, "agent traffic :30001")
    edge(650, 196, 700, 282, "agent traffic :30001 (direct :30000 fine for plain OpenAI/Anthropic clients)")
    # browser -> cockpit, down the left gutter (x=45, left of all boxes)
    edge_wp([(1120, 196), (45, 196), (45, 830), (60, 830)], "", color="#083763")
    text(150, 198, 320, ":30090 - login = api-key", font_size=10, color="#083763")
    # proxy -> engine
    edge(400, 366, 400, 452, "forwards SSE to :30000")
    # proxy -> cockpit request feed (annotation in the L4 top margin)
    text(950, 742, 440, "feed: one line per proxied request -> cockpit Requests tab + zombie guard",
         font_size=10, color="#7a3d00")
    edge(950, 776, 700, 782, "", dashed=True, color="#7a3d00")
    # cockpit canary -> engine, down the left gutter (x=55)
    edge_wp([(60, 840), (55, 840), (55, 496), (60, 496)], "", dashed=True, color="#004d00")
    text(64, 652, 320, "canary: a real generation probe - a wedged engine still answers /health",
         font_size=10, color="#004d00")
    # relay chain
    edge(1060, 890, 1060, 906, ":30091 -> :4096")
    # action surface -> systemd
    edge(410, 1026, 410, 1112, "sudo exact argv")
    # systemd -> keepalive (up the x=770 / x=930 gaps)
    edge_wp([(770, 1112), (770, 760), (930, 760), (930, 366)], "", color="#083763")
    text(785, 766, 250, "boots keepalive proxy (enabled)", font_size=10, color="#083763")
    # systemd -> engine (right gutter x=1410, into the lane bottom edge)
    edge_wp([(1340, 1160), (1410, 1160), (1410, 700), (920, 700), (920, 710)], "", color="#083763")
    text(1000, 684, 320, "boots the enabled lane: 27B ~5-7 min, flash ~10-15 min",
         font_size=10, color="#083763")
    # systemd -> opencode-web
    edge(1160, 1112, 1160, 1026, "starts opencode-web (enabled)")
    # repo -> systemd
    edge(380, 1312, 380, 1242, "")
    text(300, 1244, 400, "install.sh renders the units; switch-model.sh flips enablement", font_size=10)
    # repo -> state dir
    edge(700, 1392, 720, 1392, "")
    text(740, 1294, 300, "installer artifacts land here", font_size=10)
    # state -> cockpit (right gutter x=1425)
    edge_wp([(1380, 1312), (1425, 1312), (1425, 966), (1340, 966)], "", dashed=True)
    text(1000, 1050, 360, "state: cockpit-secret / events / history; recipes derived from repo text",
         font_size=10)

if __name__ == "__main__":
    build()
    out = sys.argv[1] if len(sys.argv) > 1 else "dgx-spark-qwen38-stack.drawio"
    emit(out, 1440, 1640)
