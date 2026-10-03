"""Three coding-agent runs with three loop shapes. SYNTHETIC.

What the loop views are shown on. Every value is invented by this script,
and every trace says so:

- **converges**: three laps of edit then test, the third one green
- **stuck**: the same edit and the same failing test, five laps in a row
- **flails**: a different thing tried every lap, never green

    python demo/loops/generate.py        # writes traces/ beside this file
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _step(steps, type_, name, inp, out, *, error=None, latency=0.4, tokens=60):
    steps.append({"index": len(steps), "type": type_, "name": name, "input": inp, "output": out,
                  "tokens": tokens, "latency_s": latency, "error": error})


def run(shape: str) -> dict:
    s: list = []
    _step(s, "reason", "think", "Read the failing test first.", "", latency=1.2, tokens=180)
    _step(s, "tool_call", "Read", "tests/test_parser.py", "40 lines", latency=0.2)
    _step(s, "tool_call", "Grep", "def parse_duration", "src/parser.py:12", latency=0.1)
    if shape == "converges":
        fixes = [("src/parser.py: handle '1h30m'", "2 failed, 10 passed"),
                 ("src/parser.py: minutes default 0", "1 failed, 11 passed"),
                 ("src/units.py: seconds per minute", "12 passed")]
    elif shape == "stuck":
        fixes = [("src/parser.py: handle '1h30m'", "2 failed, 10 passed")] * 5
    else:
        fixes = [("src/parser.py: regex rewrite", "4 failed, 8 passed"),
                 ("src/units.py: rounding", "3 failed, 9 passed"),
                 ("src/parser.py: revert regex", "2 failed, 10 passed"),
                 ("src/cli.py: argument order", "5 failed, 7 passed")]
    for k, (edit, result) in enumerate(fixes):
        _step(s, "reason", "think", f"Attempt {k + 1}: {edit}", "", latency=1.0 + 0.2 * k, tokens=220)
        if shape == "flails" and k % 2:
            _step(s, "tool_call", "Read", "src/units.py", "60 lines", latency=0.2)
        _step(s, "tool_call", "Edit", edit, "applied", latency=0.3)
        _step(s, "tool_call", "Bash", '{"command": "pytest -q"}', result, latency=2.1)
    ok = shape == "converges"
    _step(s, "answer", "answer", "", "fixed; 12 passing" if ok else "could not make the tests pass", latency=0.5)
    return {"schema_version": 1, "trace_id": f"loops-{shape}", "run_id": "r1",
            "agent": {"name": f"agent-{shape}", "model": "synthetic", "version": "synthetic"},
            "task": {"id": "parse_duration", "prompt": "Make tests/test_parser.py pass.", "expected": None},
            "outcome": {"success": ok, "answer": s[-1]["output"], "score": None},
            "totals": {"input_tokens": 0, "output_tokens": sum(x["tokens"] for x in s), "cost_usd": 0.0,
                       "latency_s": round(sum(x["latency_s"] for x in s), 3)},
            "steps": s,
            "harness": {"adapter": "synthetic", "note": "SYNTHETIC: a scripted run showing one loop shape"}}


def generate(out: Path = HERE / "traces") -> list:
    out.mkdir(parents=True, exist_ok=True)
    names = []
    for shape in ("converges", "stuck", "flails"):
        path = out / f"parse_duration__agent-{shape}.json"
        path.write_text(json.dumps(run(shape), indent=1), encoding="utf-8")
        names.append(path.name)
    return names


if __name__ == "__main__":
    print("\n".join(generate(Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "traces")))
