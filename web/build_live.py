#!/usr/bin/env python3
"""Build web/live.html — AgentDiff Live — from the demo reports.

The live page is the deployable demo: two real Claude agents solve the
same task in the viewer's browser (through the claude.ai artifact
runtime's `sample` capability, with the tools defined in the page), every
thought and tool call streaming into a step line, then a browser-side
diff. What it cannot do is run the engine, so the engine's findings for
each task's *recorded* pair — the verdict card, the decisive step, the
causal account, the prompt suggestions — are precomputed here and cached
inside the page. Opened anywhere else, the page replays the recorded pair
and never calls a model.

    python web/build_live.py            # runs the demo, writes web/live.html
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "web" / "live_template.html"
OUTPUT = ROOT / "web" / "live.html"
TASKS = ("t05_flight_duration", "t01_acme_revenue")
MARKER = "__PRE__"


def precompute(reports_dir: Path) -> dict:
    out = {}
    for task in TASKS:
        path = reports_dir / f"report_{task}.json"
        r = json.loads(path.read_text(encoding="utf-8"))
        dec = r["diagnosis"]["decisive_step"]
        side = r["diagnosis"]["subject"]
        out[task] = {
            "task": r["task"],
            "agents": {s: {
                "name": r[s]["agent"]["name"], "success": r[s]["outcome"]["success"],
                "answer": r[s]["outcome"]["answer"],
                "steps": [{"index": st["index"], "type": st["type"], "name": st["name"],
                           "input": st["input"][:400], "output": st["output"][:400],
                           "latency_s": st.get("latency_s"), "tokens": st.get("tokens"),
                           "quality": st.get("quality")} for st in r[s]["steps"]]} for s in "ab"},
            "verdict": [line["text"] for line in r["verdict_card"]["lines"]],
            "decisive": {"side": side, "step": dec["step"], "verification": dec["verification"],
                         "criterion": dec["criterion"]},
            "take_forward": [{"at_step": x["at_step"], "instead": x["instead"]}
                             for x in r["reading"][side]["take_forward"]][:3],
            "prompts": [p["text"] for p in r["feedback"]["prompt_suggestions"]],
            "causal": [link["step"] for link in r["diagnosis"]["causal_account"]],
        }
    return out


def build(reports_dir: Path | None = None) -> Path:
    template = TEMPLATE.read_text(encoding="utf-8")
    if MARKER not in template:
        raise SystemExit(f"{TEMPLATE} has no {MARKER} marker")
    if reports_dir is None:
        tmp = Path(tempfile.mkdtemp(prefix="agentdiff-live-"))
        subprocess.run([sys.executable, "-m", "deepcompare", "demo", "-o", str(tmp)],
                       cwd=str(ROOT), check=True, capture_output=True)
        reports_dir = tmp
    payload = json.dumps(precompute(reports_dir), ensure_ascii=False).replace("</", "<\\/")
    OUTPUT.write_text(template.replace(MARKER, payload, 1), encoding="utf-8")
    return OUTPUT


if __name__ == "__main__":
    out = build(Path(sys.argv[1]) if len(sys.argv) > 1 else None)
    print(f"wrote {out.relative_to(ROOT)} — {out.stat().st_size // 1024} KB")
