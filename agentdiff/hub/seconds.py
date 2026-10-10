"""Where the seconds went, as bars: the time each step took, added up.

Four questions, each a set of bars on one scale per question, so two runs
compare at a glance:
- **by activity**: what kind of work the time went to, one stacked bar
  per run (part to whole);
- **by tool**: the tools that took the most time, with their calls and
  failures;
- **by agent**: the main agent and each sub-agent, when there are any;
- **the slowest steps**, each one a link to the step.

A step's seconds are its own (start to end on the run's clock). Idle time
between steps is in no bar, so the bars add up to the time spent working,
which is shown beside the time on the clock.
"""

from __future__ import annotations

import html
from typing import Dict, List, Optional

from ..longrun import dur as _dur
from .viz import ACTIVITIES

__all__ = ["seconds_html", "SECONDS_CSS"]

TOP_TOOLS, TOP_AGENTS, SLOWEST = 8, 8, 6

SECONDS_CSS = """
.secs{display:grid;gap:18px}
.secs h3{font:600 12px system-ui,sans-serif;letter-spacing:.04em;text-transform:uppercase;color:var(--soft);margin:0 0 8px}
.secs .stack{display:flex;gap:2px;height:22px;margin:4px 0 2px}
.secs .stack span{display:block;height:100%;min-width:2px;opacity:.85}
.secs .stack span:first-child{border-radius:4px 0 0 4px}.secs .stack span:last-child{border-radius:0 4px 4px 0}
.secs .stack span:hover{opacity:1}
.secs .who{font-size:12px;color:var(--soft);margin-top:6px}
.secs .parts{display:flex;flex-wrap:wrap;gap:2px 14px;font-size:12px;color:var(--soft);margin-top:4px}
.secs .parts i{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:5px;vertical-align:-1px}
.secs .parts b{color:var(--ink);font-weight:600}
.secs table.bars{width:100%;border-collapse:collapse;font-size:13px}
.secs table.bars td{padding:3px 8px 3px 0;vertical-align:middle;border:none}
.secs table.bars td.k{width:170px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:170px}
.secs table.bars td.v{white-space:nowrap;color:var(--soft);font-variant-numeric:tabular-nums;width:1%}
.secs table.bars td.v b{color:var(--ink);font-weight:600}
.secs .track{position:relative;height:12px}
.secs .bar{display:block;height:12px;border-radius:0 4px 4px 0;opacity:.85;min-width:2px}
.secs .bar.b{height:7px;margin-top:2px;opacity:.45}
.secs .bar:hover{opacity:1}
.secs .bad{color:var(--sc)}
"""


def dur(s: float) -> str:
    """A duration, with tenths below ten seconds: a short run's steps are not all 0s."""
    s = float(s or 0)
    return f"{s:.1f}s" if s < 10 else _dur(s)


def e(v) -> str:
    return html.escape(str(v), quote=True)


def _secs(x: dict) -> float:
    return max(0.0, float(x.get("end") or 0) - float(x.get("start") or 0))


def _tally(tl: dict) -> dict:
    items = tl.get("steps") or []
    act: Dict[str, float] = {}
    tools: Dict[str, dict] = {}
    agents: Dict[str, dict] = {}
    for x in items:
        s = _secs(x)
        act[x["activity"]] = act.get(x["activity"], 0.0) + s
        a = agents.setdefault(x["agent"], {"s": 0.0, "steps": 0, "acts": {}})
        a["s"] += s
        a["steps"] += 1
        a["acts"][x["activity"]] = a["acts"].get(x["activity"], 0.0) + s
        if x.get("type") != "tool_call":
            continue
        t = tools.setdefault(str(x["name"]), {"s": 0.0, "calls": 0, "failed": 0, "acts": {}})
        t["s"] += s
        t["calls"] += 1
        t["failed"] += 1 if (x.get("error") or x.get("check") is False) else 0
        t["acts"][x["activity"]] = t["acts"].get(x["activity"], 0.0) + s
    busy = sum(act.values())
    slow = sorted((x for x in items if _secs(x) > 0), key=lambda x: -_secs(x))[:SLOWEST]
    return {"act": act, "tools": tools, "agents": agents, "busy": busy, "span": float(tl.get("span_s") or 0),
            "slow": slow}


def _cls(acts: Dict[str, float]) -> str:
    top = max(acts.items(), key=lambda kv: kv[1])[0] if acts else "other"
    return ACTIVITIES.get(top, ACTIVITIES["other"])[0]


def _stack(t: dict, most: float, name: str, pair: bool) -> str:
    order = [k for k in ACTIVITIES if t["act"].get(k, 0) > 0]
    width = 100.0 * t["busy"] / most if most else 0
    spans = "".join(f'<span class="" style="flex:{t["act"][k]:.3f};background:var(--{ACTIVITIES[k][0]})" '
                    f'title="{e(ACTIVITIES[k][2])}: {e(dur(t["act"][k]))}, {100 * t["act"][k] / t["busy"]:.0f}%"></span>'
                    for k in order)
    parts = "".join(f'<span><i style="background:var(--{ACTIVITIES[k][0]})"></i>{e(ACTIVITIES[k][2])} '
                    f'<b>{100 * t["act"][k] / t["busy"]:.0f}%</b> {e(dur(t["act"][k]))}</span>'
                    for k in sorted(order, key=lambda k: -t["act"][k]))
    head = (f'<div class="who"><b>{e(name)}</b> · ' if pair else '<div class="who">') + \
           f'{e(dur(t["busy"]))} working, of {e(dur(t["span"]))} on the clock</div>'
    return (f'{head}<div class="stack" style="width:{max(width, 1):.2f}%">{spans}</div>'
            f'<div class="parts">{parts}</div>')


def _bars(rows: List[tuple], tallies: List[dict], key: str, most: float, pair: bool, unit) -> str:
    """rows: (label, title, [entry per run or None]). One bar per run, B thinner below A."""
    out = []
    for label, title, entries in rows:
        bars, vals = [], []
        for k, ent in enumerate(entries):
            if ent is None:
                bars.append(f'<span class="bar{" b" if k else ""}" style="width:0"></span>')
                continue
            w = 100.0 * ent["s"] / most if most else 0
            bars.append(f'<span class="bar{" b" if k else ""} {_cls(ent["acts"])}" '
                        f'style="width:{max(w, 0.3):.2f}%;background:var(--{_cls(ent["acts"])})" '
                        f'title="{e(("A · " if k == 0 else "B · ") if pair else "")}{e(label)}: {e(dur(ent["s"]))}"></span>')
            vals.append(unit(ent, k))
        out.append(f'<tr><td class="k" title="{e(title)}">{e(label)}</td><td><div class="track">{"".join(bars)}</div></td>'
                   f'<td class="v">{" · ".join(vals)}</td></tr>')
    return '<table class="bars">' + "".join(out) + "</table>"


def seconds_html(tls: List[dict], names: List[str], *, href: str = "") -> str:
    tls = [t for t in tls if t and t.get("steps")]
    if not tls:
        return '<p class="muted">No steps to add up.</p>'
    pair = len(tls) > 1
    tal = [_tally(t) for t in tls]
    most = max(t["busy"] for t in tal) or 1.0
    out = ['<div class="secs">']
    out.append("<section><h3>By activity</h3>" + "".join(_stack(t, most, n, pair) for t, n in zip(tal, names))
               + "</section>")

    def tool_val(ent, k):
        fail = f' · <span class="bad">{ent["failed"]} ✗</span>' if ent["failed"] else ""
        return f'{"A " if pair and k == 0 else "B " if pair else ""}<b>{e(dur(ent["s"]))}</b> · {ent["calls"]} call(s){fail}'
    names_all: Dict[str, float] = {}
    for t in tal:
        for n, v in t["tools"].items():
            names_all[n] = names_all.get(n, 0.0) + v["s"]
    top = sorted(names_all, key=lambda n: -names_all[n])[:TOP_TOOLS]
    if top:
        most_t = max(v["s"] for t in tal for v in t["tools"].values()) or 1.0
        rest = len(names_all) - len(top)
        out.append("<section><h3>By tool</h3>"
                   + _bars([(n, n, [t["tools"].get(n) for t in tal]) for n in top], tal, "tools", most_t, pair, tool_val)
                   + (f'<p class="muted" style="font-size:12px;margin:4px 0 0">and {rest} more tool(s)</p>' if rest else "")
                   + "</section>")
    agents = {a for t in tal for a in t["agents"]}
    if agents - {"root"}:
        def agent_val(ent, k):
            return f'{"A " if pair and k == 0 else "B " if pair else ""}<b>{e(dur(ent["s"]))}</b> · {ent["steps"]} step(s)'
        order = ["root"] + sorted(agents - {"root"}, key=lambda a: -sum(t["agents"].get(a, {"s": 0})["s"] for t in tal))
        shown = order[:TOP_AGENTS]
        most_a = max(v["s"] for t in tal for v in t["agents"].values()) or 1.0
        out.append("<section><h3>By agent</h3>"
                   + _bars([("main agent" if a == "root" else a, a, [t["agents"].get(a) for t in tal]) for a in shown],
                           tal, "agents", most_a, pair, agent_val)
                   + (f'<p class="muted" style="font-size:12px;margin:4px 0 0">and {len(order) - len(shown)} more '
                      f'sub-agent(s)</p>' if len(order) > len(shown) else "")
                   + "</section>")
    slow = tal[0]["slow"]
    if slow:
        most_s = _secs(slow[0]) or 1.0
        rows = []
        for x in slow:
            link = f'{e(href)}?at={x["index"]}#s{x["index"]}' if href else f'#s{x["index"]}'
            cls = ACTIVITIES.get(x["activity"], ACTIVITIES["other"])[0]
            flag = ' · <span class="bad">✗</span>' if (x.get("error") or x.get("check") is False) else ""
            rows.append(f'<tr><td class="k"><a href="{link}">step {x["index"]}</a> · {e(x["name"])}</td>'
                        f'<td><div class="track"><span class="bar {cls}" style="width:{100 * _secs(x) / most_s:.2f}%;'
                        f'background:var(--{cls})"></span></div></td><td class="v"><b>{e(dur(_secs(x)))}</b>{flag}</td></tr>')
        out.append(f'<section><h3>The slowest steps{" of A" if pair else ""}</h3><table class="bars">{"".join(rows)}'
                   f'</table></section>')
    out.append("</div>")
    return "".join(out)
