"""The lag as a page: when each run's code first became correct, and what it did with the rest of its run
(``agentdiff lag RUNS/ --html out.html``).

Standard library only, no script, every string escaped. Two drawings, both from :func:`agentdiff.lag.lag_over`:
every passing run as a row of its steps, split where its code first passed the held-out check, the steps after
coloured by what they were; and, per task, each run's share of tests passing after every edit, from the
untouched workspace to its end.
"""

from __future__ import annotations

from html import escape
from typing import List, Sequence

__all__ = ["lag_html"]

_CSS = """
/* VOICE work: a measurement read by people who build and run coding agents
   IDEA a lab's time-and-motion study: each run a strip of its steps, the moment the work was done marked
   PICTURE every passing run as a row of steps, ink before the fix and coloured by kind after it
   COLOUR white ground, black ink, one grey, signal red for checks after the fix, the fix line in red
   TYPE Archivo heavy for figures and titles, Archivo for text
   LAYOUT title and five figures across the top; the rows full width; small multiples of progress below */
:root{--bg:#fff;--ink:#111;--ink2:#5c5c5c;--rule:#d8d8d8;--soft:#f3f3f3;--red:#e4002b;--grey:#9a9a9a;--pale:#dedede}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#0e0e0e;--ink:#f2f2f2;--ink2:#a9a9a9;
--rule:#2e2e2e;--soft:#1a1a1a;--red:#ff4060;--grey:#6d6d6d;--pale:#333}}
:root[data-theme="dark"]{--bg:#0e0e0e;--ink:#f2f2f2;--ink2:#a9a9a9;--rule:#2e2e2e;--soft:#1a1a1a;--red:#ff4060;
--grey:#6d6d6d;--pale:#333}
*{box-sizing:border-box;min-width:0}
body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.55 Archivo,system-ui,sans-serif;overflow-x:clip}
.band{padding-block:clamp(24px,4vw,52px);padding-left:max(5vw,16px,env(safe-area-inset-left,0px));
padding-right:max(5vw,16px,env(safe-area-inset-right,0px))}
.wrap{max-width:1180px;margin:0 auto}
.top{border-top:6px solid var(--ink)}
h1{font-size:clamp(30px,4.4vw,50px);line-height:1.05;letter-spacing:-.02em;font-weight:800;margin:0 0 12px;max-width:21em}
h2{font-size:clamp(20px,2.4vw,26px);line-height:1.15;margin:0 0 6px;font-weight:800}
.lede{color:var(--ink2);margin:0 0 22px;max-width:66ch;font-size:17px}
.figs{display:grid;grid-template-columns:repeat(auto-fit,minmax(10.5em,1fr));gap:0 24px;border-top:1px solid var(--ink);
margin-bottom:26px}
.fig1{padding:12px 0 14px;border-bottom:1px solid var(--rule)}
.fig1 b{display:block;font-size:clamp(28px,3.4vw,40px);font-weight:800;line-height:1;letter-spacing:-.02em;white-space:nowrap}
.fig1 b small{font-size:.45em}
.fig1 b.red{color:var(--red)}
.fig1 span{display:block;font-size:13.5px;color:var(--ink2);margin-top:6px}
.rows{border-top:2px solid var(--ink);padding-top:10px}
.row{display:grid;grid-template-columns:minmax(0,13em) minmax(0,1fr) 3.4em;gap:12px;align-items:center;height:13px}
.row .n{font-size:11.5px;color:var(--ink2);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;line-height:13px}
.row .bar{display:flex;gap:1px;height:9px}
.row .bar i{flex:1 1 0;min-width:0}
.row .s{font-size:11.5px;color:var(--ink2);text-align:right;font-variant-numeric:tabular-nums}
.k-before{background:var(--ink)}.k-check{background:var(--red)}.k-edit{background:var(--grey)}
.k-read,.k-other{background:var(--pale)}.k-text{background:var(--soft);outline:1px solid var(--pale);outline-offset:-1px}
.fixmark{box-shadow:-3px 0 0 var(--bg),-4px 0 0 var(--red)}
.key{display:flex;flex-wrap:wrap;gap:6px 18px;font-size:13.5px;color:var(--ink2);margin:14px 0 0}
.key i{display:inline-block;width:14px;height:10px;margin-right:6px;vertical-align:0}
.multi{display:grid;grid-template-columns:repeat(auto-fill,minmax(210px,1fr));gap:18px 22px;margin-top:14px}
.panel h3{font-size:14px;margin:0 0 2px;font-weight:800}
.panel p{font-size:12.5px;color:var(--ink2);margin:0 0 6px}
.panel svg{display:block;width:100%;height:auto;border-bottom:1px solid var(--ink)}
.panel svg text{font-family:Archivo,system-ui,sans-serif}
.notes{columns:2 22em;column-gap:40px;color:var(--ink2)}
.notes p{margin:0 0 12px;break-inside:avoid}
footer{color:var(--ink2);font-size:14px;border-top:1px solid var(--rule)}
code{font-size:14px;overflow-wrap:anywhere}
@media (max-width:640px){.row{grid-template-columns:minmax(0,1fr) 3em}.row .n{display:none}}
"""

_CLS = {"check": "k-check", "edit": "k-edit", "read": "k-read", "other tool": "k-other", "text": "k-text"}


def _row(r: dict) -> str:
    """One passing run: its steps in order, ink up to and including the edit that made the code pass, then
    each later step coloured by its kind."""
    path, first = r.get("path") or [], r["first_correct"]
    cells = []
    for i, kind in enumerate(path):
        cls = "k-before" if i <= first else _CLS.get(kind, "k-other")
        mark = " fixmark" if i == first + 1 else ""
        cells.append(f'<i class="{cls}{mark}"></i>')
    tip = (f"{r['trace']}: {len(path)} steps; the code first passed at step {first}; {r['lag']} step(s) after "
           f"({r['lag_share']:.0%})")
    return (f'<div class="row" title="{escape(tip)}"><span class="n">{escape(r["trace"])}</span>'
            f'<span class="bar">{"".join(cells)}</span><span class="s">{r["lag_share"]:.0%}</span></div>')


def _panel(task: str, runs: List[dict]) -> str:
    """One task: each run's share of tests passing, from the untouched workspace (left) after every edit, to
    the run's end (right), on the run's own step scale."""
    W, H, L, R, T, B = 240, 120, 6, 234, 8, 112
    lines, dots = [], []
    for r in runs:
        n = max(1, r["steps"] - 1)
        pts = [(0.0, r["start"])]
        for step, share in r["progress"]:
            pts.append((step / n, pts[-1][1]))
            pts.append((step / n, share))
        pts.append((1.0, pts[-1][1]))
        xy = " ".join(f"{L + (R - L) * x:.1f},{B - (B - T) * y:.1f}" for x, y in pts)
        colour = "var(--ink)" if r["final_correct"] else "var(--red)"
        lines.append(f'<polyline points="{xy}" fill="none" stroke="{colour}" stroke-width="1.6" stroke-opacity=".7">'
                     f'<title>{escape(r["trace"])}</title></polyline>')
        if r["first_correct"] is not None:
            x = L + (R - L) * r["first_correct"] / n
            dots.append(f'<circle cx="{x:.1f}" cy="{T}" r="3.2" fill="var(--red)" stroke="var(--bg)" stroke-width="1.5"/>')
    total = runs[0].get("tests_total")
    passed = sum(1 for r in runs if r["final_correct"])
    return (f'<div class="panel"><h3>{escape(task)}</h3><p>{len(runs)} run(s), {passed} passed; '
            f'{total or "?"} test(s)</p><svg viewBox="0 0 {W} {H}" role="img" aria-label="{escape(task)}: share of '
            f'tests passing over each run"><line x1="{L}" x2="{R}" y1="{T}" y2="{T}" stroke="var(--rule)"/>'
            + "".join(lines) + "".join(dots) + "</svg></div>")


def lag_html(result: dict, sources: Sequence[str] = (), title: str = "Termination lag") -> str:
    """The page for one :func:`agentdiff.lag.lag_over` result."""
    e = escape
    ok = [r for r in result["per_run"] if r.get("rebuilt") and r.get("edits")]
    passed = sorted((r for r in ok if r["final_correct"] and r["first_correct"] is not None),
                    key=lambda r: (-r["lag_share"], r["trace"]))
    never = sum(1 for r in passed if not (r.get("after") or {}).get("check"))
    figs = [
        (f'{result["first_edit_correct"]}<small> of {result["passed"]}</small>', "passing runs right after their "
         "first edit to the project", True),
        (f'{result["median_lag"]:g}<small> steps</small>' if result["median_lag"] is not None else "–",
         "after the code first passed, median", False),
        (f'{result["median_lag_share"]:.0%}' if result["median_lag_share"] is not None else "–",
         "of a passing run came after its fix, median", True),
        (f'{result["median_tokens_after_share"]:.0%}' if result["median_tokens_after_share"] is not None else "–",
         "of its tokens, median", False),
        (f'{never}<small> of {len(passed)}</small>', "never checked after the code was right", False),
        (f'{result["undone"]}<small> of {result["graded"]}</small>', "had a passing fix and lost it", False)]
    tiles = "".join(f'<div class="fig1"><b{" class=red" if red else ""}>{v}</b><span>{e(t)}</span></div>'
                    for v, t, red in figs)
    head = (f"{result['first_edit_correct']} of {result['passed']} passing runs were right after their first edit, "
            f"then spent half the run going on" if result["median_lag_share"] and 0.4 <= result["median_lag_share"]
            <= 0.6 else f"{result['first_edit_correct']} of {result['passed']} passing runs were right after their "
            f"first edit")
    lede = (f"{result['graded']} real runs, rebuilt from their tasks' workspaces edit by edit and graded by the "
            f"held-out check after every edit. A rebuild counts only if its final files hash to what the run "
            f"left; {result['runs'] - result['rebuilt']} did not and are set aside.")
    rows = "".join(_row(r) for r in passed)
    key = ('<div class="key"><span><i class="k-before"></i>up to the edit that made the code pass</span>'
           '<span><i class="k-check"></i>a check after it</span><span><i class="k-edit"></i>an edit</span>'
           '<span><i class="k-read"></i>a read or other tool</span><span><i class="k-text"></i>text: '
           'reasoning, the answer</span></div>')
    by: dict = {}
    for r in ok:
        by.setdefault(r["task"], []).append(r)
    panels = "".join(_panel(t, rs) for t, rs in sorted(by.items()))
    fails = [r for r in ok if not r["final_correct"]]
    fail_line = ""
    if fails:
        parts = [f"{r['trace']} ({r['tests'][-1][1]} of {r['tests_total']} tests at the end)" for r in fails
                 if r["tests"] and r["tests"][-1][1] is not None]
        fail_line = (f"<p>The {len(fails)} failing run(s) never passed at any point: "
                     + e("; ".join(parts)) + ". They fell short; none lost a fix.</p>")
    src = ", ".join(f"<code>{e(str(s))}</code>" for s in sources)
    return (
        f'<!doctype html><html lang="en"><head><meta charset="utf-8"><title>{e(title)}</title>'
        '<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">'
        '<meta name="claude:viewport-fit-sides" content="cover">'
        '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Archivo:wdth,wght@100..125,400..800'
        f'&display=swap"><style>{_CSS}</style></head><body>'
        f'<header class="band top"><div class="wrap"><h1>{e(head)}</h1><p class="lede">{e(lede)}</p>'
        f'<div class="figs">{tiles}</div>'
        f'<h2>Every passing run, before and after its fix</h2><p class="lede">One row a run, one cell a step. Ink '
        f'is the run up to the edit after which the held-out check first passed; what follows is coloured by what '
        f'it was. Sorted by the share of the run after the fix.</p><div class="rows">{rows}</div>{key}'
        f'</div></header>'
        f'<section class="band"><div class="wrap"><h2>How far each run had got, edit by edit</h2><p class="lede">'
        f'The share of the task\'s held-out tests passing, from the untouched workspace (left) after every edit to '
        f'the run\'s end (right), each run on its own step scale. Black: the run passed. Red: it failed. A red dot '
        f'on the top line: where a run first passed every test.</p><div class="multi">{panels}</div>'
        f'{fail_line}</div></section>'
        '<section class="band"><div class="wrap"><h2>How it was measured</h2><div class="notes">'
        "<p>Each run starts from its task's own workspace. Its recorded <code>Write</code>, <code>Edit</code> and "
        "<code>MultiEdit</code> calls are applied in order, exactly as the tools did; edits outside the workspace "
        "(an agent's own scratch tests) are not.</p>"
        "<p>After every edit the task's held-out check runs on a copy, and its own summary gives the tests passed. "
        "Every task's untouched workspace fails its check, so a pass is the edit's doing.</p>"
        "<p>A rebuild counts only if its final files hash to the SHA-256 the run's record kept, and its final grade "
        "matches the run's own. A file changed from the shell is not replayed, so such a run is set aside.</p>"
        "<p>No agent was run and no model judged anything. Steps after the fix are not all waste: a check after it "
        "is how a run learns it is right, and the agent cannot see the held-out tests.</p>"
        "</div></div></section>"
        '<footer class="band"><div class="wrap">Made by <code>agentdiff lag</code>' + (f" from {src}" if src else "")
        + ". No script.</div></footer></body></html>")
