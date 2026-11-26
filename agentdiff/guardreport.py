"""The guard replay as a page: what each guard would have caught and what it would have cost, drawn from
the runs themselves (``agentdiff guard --replay DIR --html out.html``).

Standard library only, no script, every string escaped. The picture is the check guard's own table of
outcomes, failed or passed against sent back or let finish, with each cell filled by its runs: one small
strip per run, a mark per tool call, and a red rule where the guard steps in. Below it, each rule an operator
could choose beside the one installed, with exact 95% intervals, and each guard's counts. Every number on
the page is a count from :func:`agentdiff.guard.replay` and :func:`agentdiff.guard.replay_variants`.
"""

from __future__ import annotations

from html import escape
from typing import List, Optional

__all__ = ["replay_html"]

_CSS = """
/* VOICE work: an audit of a rule, read by the people who would install it
   IDEA a timetable's matrix: each run a slip filed in the cell its outcome and the guard's call put it in
   PICTURE the check guard's 2x2 table of outcomes, every cell filled with its own runs as strips
   COLOUR white ground, black ink, one grey, signal red only where a guard steps in
   TYPE Archivo heavy for figures and the title, Archivo for text
   LAYOUT title and the key figures across the top; the matrix full width beneath; rules, counts, notes below */
:root{--bg:#fff;--ink:#111;--ink2:#5c5c5c;--rule:#d8d8d8;--soft:#f3f3f3;--red:#e4002b;--grey:#9a9a9a}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#0e0e0e;--ink:#f2f2f2;--ink2:#a9a9a9;
--rule:#2e2e2e;--soft:#1a1a1a;--red:#ff4060;--grey:#6d6d6d}}
:root[data-theme="dark"]{--bg:#0e0e0e;--ink:#f2f2f2;--ink2:#a9a9a9;--rule:#2e2e2e;--soft:#1a1a1a;--red:#ff4060;
--grey:#6d6d6d}
*{box-sizing:border-box;min-width:0}
body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.55 Archivo,system-ui,sans-serif;overflow-x:clip}
.band{padding-block:clamp(24px,4vw,52px);padding-left:max(5vw,16px,env(safe-area-inset-left,0px));
padding-right:max(5vw,16px,env(safe-area-inset-right,0px))}
.wrap{max-width:1240px;margin:0 auto}
.top{border-top:6px solid var(--ink)}
h1{font-size:clamp(28px,4.2vw,46px);line-height:1.06;letter-spacing:-.02em;font-weight:800;margin:0 0 10px;
max-width:22em}
h2{font-size:clamp(20px,2.4vw,26px);line-height:1.15;letter-spacing:-.01em;margin:0 0 6px;font-weight:800}
.lede{color:var(--ink2);margin:0 0 22px;max-width:62ch}
.figs{display:grid;grid-template-columns:repeat(auto-fit,minmax(17em,1fr));gap:0 28px;
border-top:1px solid var(--ink);margin-bottom:28px}
.g{padding:12px 0 14px;border-bottom:1px solid var(--rule)}
.gn{font-size:14px;font-weight:800;margin-bottom:6px}
.pair{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:12px}
.pair b{display:block;font-size:clamp(30px,3.6vw,42px);font-weight:800;line-height:1;letter-spacing:-.02em;
font-variant-numeric:tabular-nums;white-space:nowrap}
.pair b small{font-size:.45em;letter-spacing:0}
.pair b.red{color:var(--red)}
.pair span{display:block;font-size:13.5px;color:var(--ink2);margin-top:6px}
.matrix{display:grid;grid-template-columns:7.5em minmax(0,1fr) minmax(0,1fr);border-top:2px solid var(--ink)}
.mh{font-size:13.5px;font-weight:700;padding:10px 12px 8px;border-bottom:1px solid var(--rule)}
.mh.red{color:var(--red)}
.ml{font-size:14px;font-weight:700;padding:14px 12px 14px 0;border-bottom:1px solid var(--rule)}
.ml small{display:block;font-weight:400;color:var(--ink2);font-size:13px}
.cell{padding:12px;border-bottom:1px solid var(--rule);border-left:1px solid var(--rule)}
.cell.hit{background:var(--soft)}
.n{font-size:clamp(28px,3.4vw,40px);font-weight:800;line-height:1;letter-spacing:-.02em;margin-bottom:4px}
.cell.hit .n{color:var(--red)}
.say{font-size:13.5px;color:var(--ink2);margin-bottom:10px}
.slips{display:flex;flex-wrap:wrap;gap:7px 8px}
.slips svg{display:block;height:22px;width:auto}
.key{display:flex;flex-wrap:wrap;gap:6px 18px;font-size:13.5px;color:var(--ink2);margin-top:14px}
.key svg{vertical-align:-3px;margin-right:5px}
.rules{border-top:2px solid var(--ink);margin-top:14px}
.rule{display:grid;grid-template-columns:minmax(0,1.3fr) minmax(0,1fr) minmax(0,1fr);gap:16px;
padding:12px 0;border-bottom:1px solid var(--rule);align-items:center}
.rule .what{font-size:15px}
.rule .what em{font-style:normal;font-size:12.5px;font-weight:700;color:var(--bg);background:var(--ink);
padding:1px 7px;border-radius:3px;margin-left:6px;white-space:nowrap}
.rule .what small{display:block;color:var(--ink2);font-size:13px}
.bar{position:relative;height:12px;background:var(--soft);border-radius:2px}
.bar i{position:absolute;left:0;top:0;bottom:0;border-radius:2px}
.bar s{position:absolute;top:5px;height:2px;background:var(--ink)}
.c i{background:var(--red)}.k i{background:var(--grey)}
.val{font-size:13.5px;margin-top:4px;font-variant-numeric:tabular-nums;white-space:nowrap}
.val span{color:var(--ink2)}
.colh{display:grid;grid-template-columns:minmax(0,1.3fr) minmax(0,1fr) minmax(0,1fr);gap:16px;font-size:13px;
font-weight:700;color:var(--ink2);padding-top:8px}
.scroll{overflow-x:auto}
table{border-collapse:collapse;width:100%;font-size:14.5px}
th,td{text-align:left;padding:8px 10px 8px 0;border-bottom:1px solid var(--rule);vertical-align:top}
th{font-size:13px;color:var(--ink2)}
td.num{font-variant-numeric:tabular-nums;white-space:nowrap}
code{font-size:13.5px;overflow-wrap:anywhere}
.notes{columns:2 22em;column-gap:40px;color:var(--ink2);font-size:15px}
.notes p{margin:0 0 12px;break-inside:avoid}
footer{color:var(--ink2);font-size:13.5px;border-top:1px solid var(--rule)}
@media (max-width:640px){.matrix{grid-template-columns:minmax(0,1fr) minmax(0,1fr)}.ml{grid-column:1/-1;
padding-bottom:4px;border-bottom:0}.corner{display:none}.rule,.colh{grid-template-columns:minmax(0,1fr) minmax(0,1fr)}
.rule .what,.colh .w{grid-column:1/-1}table{font-size:13px}.slips svg{height:16px}}
"""

#: how each tool call is drawn: an edit, a check that passed, a check that failed, anything else
_MARK = {"e": ("edit", '<rect x="{x}" y="3" width="6" height="8" fill="var(--ink)"/>'),
         "c": ("check passed", '<rect x="{x}.5" y="0.5" width="5" height="13" fill="none" stroke="var(--ink)"/>'),
         "x": ("check failed", '<rect x="{x}" y="0" width="6" height="14" fill="var(--grey)"/>'),
         "r": ("other call", '<rect x="{x}" y="5" width="6" height="4" fill="var(--rule)"/>')}


def _pct(k: int, n: int) -> str:
    return f"{100 * k / n:.0f}%" if n else "–"


def _slip(run: dict, guard: str = "check") -> str:
    """One run as a strip: a mark per tool call, and a red rule where ``guard`` steps in."""
    path = run.get("path") or ""
    w = max(1, len(path)) * 8 + 6
    marks = "".join(_MARK.get(ch, _MARK["r"])[1].format(x=i * 8) for i, ch in enumerate(path))
    hit = (run.get("refused") or {}).get(guard)
    red = ""
    if hit:
        at = min(int(hit.get("at") or 0), len(path))
        x = at * 8 - 1 if at else 0
        red = f'<rect x="{x}" y="0" width="3" height="14" fill="var(--red)"/>'
        if hit.get("step") == "finish":
            red = f'<rect x="{len(path) * 8}" y="0" width="5" height="14" fill="var(--red)"/>'
    outcome = {True: "passed", False: "failed", None: "ungraded"}[run.get("success")]
    said = ""
    if hit:
        said = (" · the guard sends its finish back" + (f" ({hit.get('why')})" if hit.get("why") else "")
                if hit.get("step") == "finish" else f" · the guard refuses step {hit.get('step')}")
    tip = f"{run.get('trace')}: {outcome}, {len(path)} tool call(s){said}"
    return (f'<svg viewBox="0 0 {w} 14" width="{w}" height="14" role="img" aria-label="{escape(tip)}">'
            f'<title>{escape(tip)}</title>{marks}{red}</svg>')


def _cell(runs: List[dict], hit: bool, say: str) -> str:
    slips = "".join(_slip(r) for r in sorted(runs, key=lambda r: (-len(r.get("path") or ""), r.get("trace") or "")))
    return (f'<div class="cell{" hit" if hit else ""}"><div class="n">{len(runs)}</div>'
            f'<div class="say">{escape(say)}</div><div class="slips">{slips}</div></div>')


def _bar(kind: str, k: int, n: int, ci: Optional[list]) -> str:
    if not n:
        return '<div class="val"><span>no runs</span></div>'
    whisker = ""
    if ci:
        whisker = f'<s style="left:{100 * ci[0]:.1f}%;width:{max(0.6, 100 * (ci[1] - ci[0])):.1f}%"></s>'
    span = f" ({100 * ci[0]:.0f}–{100 * ci[1]:.0f}%)" if ci else ""
    return (f'<div class="bar {kind}"><i style="width:{100 * k / n:.1f}%"></i>{whisker}</div>'
            f'<div class="val"><b>{k} of {n}</b> <span>{_pct(k, n)}{span}</span></div>')


def _key() -> str:
    items = []
    for ch in "ecxr":
        name, mark = _MARK[ch]
        items.append(f'<span><svg viewBox="0 0 7 14" width="7" height="14">{mark.format(x=0)}</svg>{name}</span>')
    items.append('<span><svg viewBox="0 0 4 14" width="4" height="14"><rect width="3" height="14" '
                 'fill="var(--red)"/></svg>where the guard steps in (at the end: it sends the finish back)</span>')
    return f'<div class="key">{"".join(items)}</div>'


def replay_html(result: dict, variants: Optional[List[dict]] = None, sources: Optional[List[str]] = None,
                title: str = "Guard replay", label: str = "recorded") -> str:
    """The page for one replay (:func:`agentdiff.guard.replay`), with its rule variants when given.
    ``label`` says what the runs are: recorded, synthetic, or a mix, and is printed beside every count."""
    e = escape
    runs = result.get("per_run") or []
    graded = [r for r in runs if r.get("success") is not None]
    guards = result.get("guards") or {}
    check = guards.get("check")
    f, p = result.get("failed", 0), result.get("passed", 0)

    if check:
        head = (f"The check guard steps into {check['in_failed']} of {check['failed']} failing runs, "
                f"and {check['in_passed']} of {check['passed']} that passed")
    else:
        head = f"{len(runs)} recorded runs, put through the guards"
    lede = (f"Every one of {len(runs)} {label} runs ({f} failed, {p} passed"
            + (f", {result.get('ungraded')} ungraded" if result.get("ungraded") else "")
            + ") was put through the live guards' own rules, call by call. This is where each would have stepped "
              "in, not what the agent would have done after it.")

    figs = []
    for name, row in guards.items():
        figs.append(f'<div class="g"><div class="gn">{e(name)}</div><div class="pair">'
                    f'<div><b class="red">{row["in_failed"]}<small> of {row["failed"]}</small></b>'
                    f'<span>failing runs it steps into</span></div>'
                    f'<div><b>{row["in_passed"]}<small> of {row["passed"]}</small></b>'
                    f'<span>passing runs it sends back</span></div></div></div>')

    matrix = ""
    if check:
        def pick(ok, hit):
            return [r for r in graded if r["success"] is ok and (("check" in r["refused"]) == hit)]
        cells = {(ok, hit): pick(ok, hit) for ok in (False, True) for hit in (True, False)}
        why = {k: sum(1 for r in cells[(False, True)] if r["refused"]["check"].get("why") == k)
               for k in ("unchecked", "failing")}
        no_edit = sum(1 for r in cells[(False, False)] if "e" not in (r.get("path") or ""))
        matrix = (
            '<div class="matrix"><div class="mh corner"></div>'
            '<div class="mh red">Sent back to check</div><div class="mh">Let finish</div>'
            f'<div class="ml">Failed<small>{f} runs</small></div>'
            + _cell(cells[(False, True)], True, f"caught: {why['unchecked']} with edits no check followed, "
                                                f"{why['failing']} with their own last check failing")
            + _cell(cells[(False, False)], False, f"missed: {no_edit} of these edited nothing")
            + f'<div class="ml">Passed<small>{p} runs</small></div>'
            + _cell(cells[(True, True)], True, "its cost: each sent back once to run a check")
            + _cell(cells[(True, False)], False, "left alone")
            + "</div>" + _key())

    rules = ""
    if variants:
        rows = []
        for v in variants:
            tag = "<em>installed</em>" if v["installed"] else ""
            rows.append(f'<div class="rule"><div class="what"><b>{e(v["guard"])}</b>{tag}<small>{e(v["rule"])}'
                        f'</small></div><div>{_bar("c", v["in_failed"], v["failed"], v.get("catch_ci"))}</div>'
                        f'<div>{_bar("k", v["in_passed"], v["passed"], v.get("cost_ci"))}</div></div>')
        rules = ('<section class="band"><div class="wrap"><h2>Each rule an operator could choose, against the '
                 'same runs</h2><p class="lede">Red is what a rule catches among the failing runs, grey what it costs '
                 'among the passing. The black line is the exact 95% interval: with this many runs, the rate '
                 'could be anywhere along it.</p><div class="colh"><span class="w">rule</span><span>failing runs it '
                 'steps into</span><span>passing runs it sends back</span></div><div class="rules">'
                 + "".join(rows) + "</div></div></section>")

    trs = []
    for name, row in guards.items():
        scope = f"{row['runs']} of {result.get('runs')}" if row.get("runs", result.get("runs")) != result.get("runs") \
            else str(result.get("runs"))
        ex = ", ".join(f"<code>{e(str(x.get('trace')))}</code>" for x in row.get("examples", [])[:3]) or "–"
        trs.append(f'<tr><td><b>{e(name)}</b></td><td class="num">{scope}</td>'
                   f'<td class="num">{row["in_failed"]} of {row["failed"]}</td>'
                   f'<td class="num">{row["in_passed"]} of {row["passed"]}</td>'
                   f'<td class="num">{row.get("in_ungraded", 0)}</td><td>{ex}</td></tr>')
    table = ('<section class="band"><div class="wrap"><h2>Each guard, counted</h2><div class="scroll"><table>'
             '<tr><th>guard</th><th>runs read</th><th>failing it steps into</th><th>passing it sends back</th>'
             '<th>ungraded</th><th>first runs it steps into</th></tr>' + "".join(trs) + "</table></div></div></section>")

    src = ", ".join(f"<code>{e(s)}</code>" for s in (sources or []))
    notes = (
        '<section class="band"><div class="wrap"><h2>How to read it</h2><div class="notes">'
        "<p>A run is put through the guards as it was recorded: each tool call in order, the same rules the hook "
        "runs, the state kept in memory, then its finish. Nothing is written and no agent is run.</p>"
        "<p>It shows where a guard would have stepped in. It cannot show what the agent would have done next: "
        "a run sent back might have gone on to pass, or fail the same way. A run the guard sends back costs one "
        "more check, not the run.</p>"
        "<p>Failed and passed are the runs' own grades. Ungraded runs are counted apart, never as failures. The "
        "tests guard is read only on runs whose trace says where the project was.</p>"
        "<p>The intervals are exact (Clopper–Pearson), so they hold at 0 of n and n of n, where small counts "
        "land. They are wide because the runs are few, and they say so.</p>"
        "</div></div></section>")
    return (
        f'<!doctype html><html lang="en"><head><meta charset="utf-8"><title>{e(title)}</title>'
        '<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">'
        '<meta name="claude:viewport-fit-sides" content="cover">'
        '<link rel="preconnect" href="https://fonts.googleapis.com">'
        '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Archivo:wdth,wght@100..125,400..800'
        f'&display=swap"><style>{_CSS}</style></head><body>'
        f'<header class="band top"><div class="wrap"><h1>{e(head)}</h1><p class="lede">{e(lede)}</p>'
        f'<div class="figs">{"".join(figs)}</div>{matrix}</div></header>'
        f"{rules}{table}{notes}"
        f'<footer class="band"><div class="wrap">Made by <code>agentdiff guard --replay</code>'
        + (f" from {src}" if src else "") + ". No script; every number is a count of the runs above."
        "</div></footer></body></html>")
