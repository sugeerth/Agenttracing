"""At a glance: a whole run on one chart, the first thing its page shows.

One clock runs left to right. Each working stretch (a *session*,
:mod:`agentdiff.longrun`) keeps its true time; the idle between two of
them is a narrow break that says how long it was, so a run of days still
reads in one width. Top to bottom:

- **you asked**: each prompt typed (``turns``), numbered, at the moment it
  was typed. Hovering one shows what was asked, and clicking it opens the
  steps from there.
- **the agent**: what the main agent did, as one track. Each column takes
  the colour of the activity it spent most seconds on; idle inside a
  working stretch stays empty.
- **checks**: every test, lint or build the run ran, as a dot that passed
  or failed.
- **loops**: the stretches where it went round, as brackets. These are
  longrun's repeated bursts, or laps that repeated the one before three
  times or more.
- **each sub-agent** on a lane of its own, named by what it was asked.
  A faint line drops from the main track to the lane where the sub-agent
  began.

Hovering any mark names its steps; nothing on the chart needs a script.
Colours are the activity slots of :data:`agentdiff.hub.viz.ACTIVITIES`.
Pass and fail use the status colours, always beside a ✓ or ✗.
"""

from __future__ import annotations

import html
from typing import Dict, List, Optional, Tuple

from ..longrun import dur, when
from .longviz import Axis, _fit, _ticks
from .viz import ACTIVITIES

__all__ = ["glance_svg", "glance_html", "worked_html", "GLANCE_CSS"]

LEFT, RIGHT = 128, 14
COL = 2.0                 # px per column of the agent's track
LANES_MAX = 8
_INK = ' style="fill:var(--ink)"'
_K = ' class="k"'
_PRIORITY = ("verify", "edit", "run", "explore", "research", "plan", "delegate", "other", "think")

GLANCE_CSS = """
.glance{margin:2px 0 6px}
.glance .gsum{display:flex;flex-wrap:wrap;gap:4px 18px;margin:2px 0 8px;font-size:13px;color:var(--soft)}
.glance .gsum b{color:var(--ink);font-weight:600;font-size:15px;margin-right:4px}
svg.glance-svg{display:block;width:100%;height:auto;overflow:visible}
svg.glance-svg text{font:11px system-ui,-apple-system,"Segoe UI",sans-serif;fill:var(--soft)}
svg.glance-svg text.row{fill:var(--soft);font-size:11px}
svg.glance-svg text.tick{font-variant-numeric:tabular-nums}
svg.glance-svg .bed{fill:var(--line);opacity:.55}
svg.glance-svg .col{fill-opacity:.82}
svg.glance-svg .col:hover{fill-opacity:1}
svg.glance-svg .hair{stroke:var(--line);stroke-width:1}
svg.glance-svg .drop{stroke:var(--soft);stroke-width:1;stroke-opacity:.35}
svg.glance-svg .chk{stroke:var(--panel);stroke-width:1.5}
svg.glance-svg .chk.ok{fill:var(--sg)}svg.glance-svg .chk.bad{fill:var(--sc)}
svg.glance-svg .errt{fill:var(--sc);opacity:.75}
svg.glance-svg .loop{fill:none;stroke:#ec835a;stroke-width:2;stroke-linecap:round;stroke-linejoin:round}
svg.glance-svg text.loopl{fill:var(--ink);font-size:11px}
svg.glance-svg .brk{stroke:var(--soft);stroke-width:1.3;stroke-opacity:.6}
svg.glance-svg .here{fill:var(--ink)}
svg.glance-svg a.pin,svg.glance-svg a.pin text{text-decoration:none}
svg.glance-svg .pin circle{fill:var(--accent);stroke:var(--panel);stroke-width:2}
svg.glance-svg .pin text.n{fill:var(--panel);font:700 10px system-ui,sans-serif;text-anchor:middle;pointer-events:none}
svg.glance-svg .pin:hover circle,svg.glance-svg .pin:focus circle{stroke:var(--ink)}
svg.glance-svg .pin .rule{stroke:var(--accent);stroke-opacity:.18;stroke-width:1}
svg.glance-svg .tip{visibility:hidden;opacity:0;transition:opacity .12s;pointer-events:none}
svg.glance-svg .pin:hover .tip,svg.glance-svg .pin:focus .tip,svg.glance-svg .pin:focus-within .tip{visibility:visible;opacity:1}
svg.glance-svg .tip rect{fill:var(--panel);stroke:var(--line);filter:drop-shadow(0 4px 10px rgba(0,0,0,.12))}
svg.glance-svg .tip text{fill:var(--ink);font-size:12px}
svg.glance-svg .tip text.k{fill:var(--soft);font-size:11px}
.worked{margin:4px 0 14px;--h0:var(--line);--h1:#b7d3f6;--h2:#6da7ec;--h3:#2a78d6;--h4:#184f95}
@media (prefers-color-scheme:dark){.worked{--h1:#184f95;--h2:#256abf;--h3:#3987e5;--h4:#86b6ef}}
.worked svg{display:block;width:100%;height:auto}
.worked svg text{font:11px system-ui,-apple-system,"Segoe UI",sans-serif;fill:var(--soft);font-variant-numeric:tabular-nums}
.worked svg rect.h:hover{stroke:var(--ink);stroke-width:1.5}
.worked .wkey{display:flex;align-items:center;gap:4px;font-size:12px;color:var(--soft);margin-top:4px}
.worked .wkey i{display:inline-block;width:12px;height:12px;border-radius:2px}
.glance .gkey{display:flex;flex-wrap:wrap;gap:4px 14px;margin:6px 0 0;font-size:12px;color:var(--soft)}
.glance .gkey i{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:5px;vertical-align:-1px}
.glance .gkey i.dot{border-radius:50%}
.glance .gkey i.pin{border-radius:50%;background:var(--accent)}
.glance .gkey i.loop{height:5px;border:2px solid #ec835a;border-top:none;border-radius:0 0 2px 2px;background:none;vertical-align:1px}
"""


def e(v) -> str:
    return html.escape(str(v), quote=True)


def _wrap(text: str, width: int = 56, most: int = 4) -> List[str]:
    words, lines, cur = " ".join(str(text).split()).split(" "), [], ""
    for w in words:
        if len(cur) + len(w) + 1 > width and cur:
            lines.append(cur)
            cur = w
            if len(lines) == most:
                break
        else:
            cur = (cur + " " + w).strip()
    if len(lines) < most and cur:
        lines.append(cur)
    elif len(lines) == most:
        lines[-1] = lines[-1][: width - 1].rstrip() + "…"
    return lines


def _short(s: float) -> str:
    """A gap in one word: 17h, 45m, 2d."""
    s = float(s or 0)
    if s >= 86400:
        return f"{s / 86400:.0f}d" if s >= 3 * 86400 else f"{s / 86400:.1f}d"
    if s >= 3600:
        return f"{s / 3600:.0f}h"
    return f"{s / 60:.0f}m"


def _columns(items: List[dict], axis: Axis, x0: float, x1: float, col: float = COL) -> List[dict]:
    """The items as columns COL px wide, each with the activity it spent most seconds on;
    neighbours of one activity merge into one run."""
    n = int((x1 - x0) / col) + 1
    weight: List[Optional[Dict[str, float]]] = [None] * n
    steps: List[Optional[list]] = [None] * n
    for x in items:
        xa, xb = axis(x["start"]), axis(max(x["end"], x["start"]))
        a = max(0, min(n - 1, int((xa - x0) / col)))
        b = max(a, min(n - 1, int((xb - x0) / col)))
        secs = max(0.25, float(x["end"]) - float(x["start"])) / (b - a + 1)
        for k in range(a, b + 1):
            w = weight[k] if weight[k] is not None else {}
            w[x["activity"]] = w.get(x["activity"], 0.0) + secs
            weight[k] = w
            st = steps[k] if steps[k] is not None else []
            st.append(x)
            steps[k] = st
    runs: List[dict] = []
    for k in range(n):
        w = weight[k]
        if not w:
            continue
        doing = [a for a in w if a != "think"] or ["think"]
        act = max(doing, key=lambda a: (w[a], -_PRIORITY.index(a) if a in _PRIORITY else -99))
        if runs and runs[-1]["act"] == act and runs[-1]["k1"] == k - 1:
            runs[-1]["k1"] = k
            runs[-1]["steps"].extend(steps[k] or [])
        else:
            runs.append({"act": act, "k0": k, "k1": k, "steps": list(steps[k] or [])})
    for r in runs:
        r["x"] = x0 + r["k0"] * col
        r["w"] = (r["k1"] - r["k0"] + 1) * col
        seen = {}
        for x in r["steps"]:
            seen[x["index"]] = x
        r["steps"] = sorted(seen.values(), key=lambda x: x["index"])
    return runs


def _col_title(r: dict, sa, span: float) -> str:
    st = r["steps"]
    names: Dict[str, int] = {}
    for x in st:
        names[str(x.get("name"))] = names.get(str(x.get("name")), 0) + 1
    top = ", ".join(f"{n} ×{c}" if c > 1 else n for n, c in sorted(names.items(), key=lambda kv: -kv[1])[:3])
    lo, hi = st[0], st[-1]
    rng = f"step {lo['index']}" if lo is hi else f"steps {lo['index']}–{hi['index']}"
    act = ACTIVITIES.get(r["act"], ACTIVITIES["other"])[2]
    return f"{rng} · mostly {act} · {top} · {when(lo['start'], sa, span)}"


def _loops(tl: dict, r: dict) -> List[Tuple[float, float, str]]:
    """(from, to, what) for each stretch the run went round."""
    out = []
    bursts = r.get("bursts") or []
    for lp in r.get("loops") or []:
        a, b = lp["bursts"]
        if 1 <= a <= len(bursts) and 1 <= b <= len(bursts):
            out.append((bursts[a - 1]["from"], bursts[b - 1]["to"],
                        f"went round {lp['count']}× · {dur(lp.get('wall_s') or 0)}"))
    laps = tl.get("laps") or []
    k = 0
    while k < len(laps):
        j = k
        while j + 1 < len(laps) and laps[j + 1].get("repeat"):
            j += 1
        if j - k + 1 >= 4:          # a lap and three repeats of it
            fr, to = laps[k]["from"], laps[j]["to"]
            if not any(a <= fr <= b or a <= to <= b for a, b, _ in out):
                out.append((fr, to, f"the same lap {j - k + 1}×"))
        k = j + 1
    return sorted(out)


def _plain_ticks(span: float, x0: float, x1: float) -> List[tuple]:
    """A short run with no clock of its own: seconds from its start, at round steps 80 px apart or more."""
    step = next((s for s in (0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300) if s * (x1 - x0) / span >= 80), 600)
    out, t = [], 0.0
    while t <= span + 1e-9:
        out.append((x0 + (x1 - x0) * t / span, f"{t:g}s" if t < 60 else dur(t), t == 0))
        t += step
    return out


def glance_svg(data: dict, tl: dict, r: dict, *, width: int = 980, href: str = "") -> str:
    items = [x for x in tl.get("steps") or [] if isinstance(x.get("start"), (int, float))]
    if not items:
        return ""
    span = max(float(tl.get("span_s") or 0.0), max(float(x["end"]) for x in items), 1e-3)
    sa = r.get("started_at")
    sessions = r.get("sessions") or []
    x0, x1 = LEFT, width - RIGHT
    axis = Axis(sessions, x0, x1, gap_w=34.0, min_w=18.0) if len(sessions) > 1 else \
        Axis([], x0, x1, t0=0.0, t1=span)
    turns = [t for t in data.get("turns") or [] if isinstance(t, dict) and isinstance(t.get("at_s"), (int, float))]
    root = [x for x in items if x["agent"] == "root"]
    subs: List[str] = []
    for x in items:
        if x["agent"] != "root" and x["agent"] not in subs:
            subs.append(x["agent"])
    loops = _loops(tl, r)
    checks = [x for x in items if x.get("check") is not None]

    y = 10
    y_pin = y + 12 if turns else None
    y = (y_pin + 18) if turns else y + 8
    y_track, h_track = y + 6, 26
    y = y_track + h_track + 8
    y_chk = y + 6 if checks else None
    y = (y_chk + 10) if checks else y
    y_loop = y + 4 if loops else None
    y = (y_loop + 20) if loops else y
    shown = subs[:LANES_MAX]
    y_lane0, lane_h = y + 6, 15
    y = y_lane0 + lane_h * len(shown) + (14 if len(subs) > len(shown) else 0)
    y_axis = y + 14
    height = y_axis + 8
    tip_room = 0
    out: List[str] = []

    # the working stretches, as a bed under the track and each lane; the breaks between them
    for a, b, xa, xb in axis.parts:
        out.append(f'<rect class="bed" x="{xa:.1f}" y="{y_track}" width="{max(1.0, xb - xa):.1f}" '
                   f'height="{h_track}" rx="4"/>')
    for g0, g1, ga, gb in axis.gaps:
        cx, cy = (ga + gb) / 2, y_track + h_track / 2
        out.append(f'<g><title>idle {e(dur(g1 - g0))}: from {e(when(g0, sa, span))} to {e(when(g1, sa, span))}</title>'
                   f'<line class="brk" x1="{cx - 5:.1f}" y1="{cy + 6}" x2="{cx - 1:.1f}" y2="{cy - 6}"/>'
                   f'<line class="brk" x1="{cx + 1:.1f}" y1="{cy + 6}" x2="{cx + 5:.1f}" y2="{cy - 6}"/>'
                   f'<text x="{cx:.1f}" y="{y_track + h_track + 11}" text-anchor="middle" class="tick" '
                   f'style="font-size:10px">{e(_short(g1 - g0))}</text></g>')
    # row labels
    if turns:
        out.append(f'<text class="row" x="0" y="{y_pin + 4}">you asked</text>')
    agent = str((data.get("agent") or {}).get("name") or "the agent")
    out.append(f'<text class="row" x="0" y="{y_track + h_track / 2 + 4}">{e(_fit(agent, LEFT - 10))}</text>')
    if checks:
        out.append(f'<text class="row" x="0" y="{y_chk + 4}">checks</text>')
    if loops:
        out.append(f'<text class="row" x="0" y="{y_loop + 8}">loops</text>')
    # the agent's track
    for c in _columns(root, axis, x0, x1):
        cls = ACTIVITIES.get(c["act"], ACTIVITIES["other"])[0]
        out.append(f'<rect class="col {cls}" x="{c["x"]:.1f}" y="{y_track}" width="{c["w"]:.1f}" '
                   f'height="{h_track}"><title>{e(_col_title(c, sa, span))}</title></rect>')
    errs: Dict[int, List[dict]] = {}
    for x in root:
        if x.get("error"):
            errs.setdefault(int((axis(x["start"]) - x0) / 4), []).append(x)
    for k in sorted(errs):
        got = errs[k]
        xe = sum(axis(x["start"]) for x in got) / len(got)
        what = (f'step {got[0]["index"]} ({got[0]["name"]}): an error' if len(got) == 1 else
                f'{len(got)} errors, steps {got[0]["index"]}–{got[-1]["index"]}')
        out.append(f'<rect class="errt" x="{xe - 1:.1f}" y="{y_track - 5}" width="2" height="3" rx="1">'
                   f'<title>{e(what)}</title></rect>')
    # checks: one dot per few pixels, coloured by how the last check there ended
    buckets: Dict[int, List[dict]] = {}
    for x in checks:
        buckets.setdefault(int((axis(x["start"]) - x0) / 7), []).append(x)
    for k in sorted(buckets):
        got = buckets[k]
        last = got[-1]
        ok = last["check"] is True
        cx = sum(axis(x["start"]) for x in got) / len(got)
        bad = sum(1 for x in got if x["check"] is False)
        what = (f'step {last["index"]} · {"✓ passed" if ok else "✗ failed"} · {last["name"]}' if len(got) == 1 else
                f'{len(got)} checks, steps {got[0]["index"]}–{last["index"]}: {bad} ✗ failed, {len(got) - bad} ✓ passed; '
                f'the last {"passed" if ok else "failed"}')
        out.append(f'<circle class="chk {"ok" if ok else "bad"}" cx="{cx:.1f}" cy="{y_chk}" r="{3.6 if len(got) == 1 else 4.6}">'
                   f'<title>{e(what)} · {e(when(last["start"], sa, span))}</title></circle>')
    # loops
    for fr, to, what in loops:
        xa, xb = axis(fr), max(axis(to), axis(fr) + 6)
        out.append(f'<g><title>{e(what)}: {e(when(fr, sa, span))} to {e(when(to, sa, span))}</title>'
                   f'<path class="loop" d="M{xa:.1f},{y_loop} v5 H{xb:.1f} v-5"/>'
                   + (f'<text class="loopl" x="{xa:.1f}" y="{y_loop + 17}">↻ {e(what)}</text>'
                      if xb - xa >= 6.2 * (len(what) + 2) or (xb + 6.2 * (len(what) + 2) < x1) else "")
                   + '</g>')
    # sub-agents, each on its lane
    for k, name in enumerate(shown):
        ly = y_lane0 + k * lane_h
        mine = [x for x in items if x["agent"] == name]
        xs = axis(mine[0]["start"])
        out.append(f'<line class="drop" x1="{xs:.1f}" y1="{y_track + h_track}" x2="{xs:.1f}" y2="{ly + 2}"/>')
        out.append(f'<text class="row" x="10" y="{ly + 8}">{e(_fit(name, LEFT - 18))}<title>{e(name)}</title></text>')
        out.append('<g class="sublane">')
        xe = axis(max(x["end"] for x in mine))
        out.append(f'<rect class="bed" x="{xs:.1f}" y="{ly + 1}" width="{max(2.0, xe - xs):.1f}" height="9" rx="2"/>')
        for c in _columns(mine, axis, x0, x1, col=4.0):
            cls = ACTIVITIES.get(c["act"], ACTIVITIES["other"])[0]
            rx = ' rx="2"' if c["w"] >= 8 else ""
            out.append(f'<rect class="col {cls}" x="{c["x"]:.1f}" y="{ly + 1}" width="{c["w"]:.1f}" '
                       f'height="9"{rx}><title>{e(name)}: {e(_col_title(c, sa, span))}</title></rect>')
        out.append('</g>')
    if len(subs) > len(shown):
        rest = len([x for x in items if x["agent"] in subs[LANES_MAX:]])
        out.append(f'<text class="row" x="10" y="{y_lane0 + lane_h * len(shown) + 9}">+{len(subs) - len(shown)} more '
                   f'sub-agent(s), {rest} step(s): every lane is in “Every thread on its own lane”</text>')
    # the clock: each working stretch's start, and round moments inside the long ones
    for tx, lab, major in (_ticks(axis, sa, span) if sa is not None or span >= 600 else _plain_ticks(span, x0, x1)):
        if tx > x1 - 20 or tx + 6.1 * len(lab) > width:
            continue
        out.append(f'<text class="tick" x="{tx:.1f}" y="{y_axis}"{_INK if major else ""}>'
                   f'{e(lab)}</text>')
    # where to look first
    here = (tl.get("look_here") or {})
    hx = next((axis(x["start"]) for x in items if x["index"] == here.get("index")), None)
    if hx is not None and here.get("kind") not in ("now",):
        out.append(f'<g><title>{e(here.get("sentence"))}</title>'
                   f'<path class="here" d="M{hx - 5:.1f},{y_track - 12} h10 l-5,7 z"/></g>')
    # your prompts, last so their cards lie over everything
    if turns:
        groups: List[List[dict]] = []
        for t in sorted(turns, key=lambda t: t["at_s"]):
            tx = axis(float(t["at_s"]))
            if groups and tx - groups[-1][-1]["_x"] < 22:
                groups[-1].append(dict(t, _x=tx))
            else:
                groups.append([dict(t, _x=tx)])
        n = 0
        for g in groups:
            first, last = n + 1, n + len(g)
            n = last
            gx = g[0]["_x"]
            label = str(first) if first == last else f"{first}–{last}"
            rr = 9 if len(label) <= 2 else 12
            lines: List[Tuple[str, str]] = []
            for k, t in enumerate(g[:3]):
                lines.append(("k", f"you asked · #{first + k} · {when(float(t['at_s']), sa, span)}"))
                lines += [("", ln) for ln in _wrap(t.get("prompt") or "", 58, 3 if len(g) == 1 else 2)]
            if len(g) > 3:
                lines.append(("k", f"and {len(g) - 3} more"))
            tw, th = 380, 16 + 16 * len(lines)
            tx0 = min(max(0.0, gx - 30), width - tw)
            ty0 = y_pin + 14
            tip_room = max(tip_room, ty0 + th + 4)
            tip = "".join(f'<text{_K if cls else ""} x="{tx0 + 12:.1f}" y="{ty0 + 20 + 16 * k}">{e(t)}</text>'
                          for k, (cls, t) in enumerate(lines))
            link = f'{e(href)}?at={int(g[0].get("step") or 0)}#s{int(g[0].get("step") or 0)}' if href else ""
            body = (f'<line class="rule" x1="{gx:.1f}" y1="{y_pin + rr}" x2="{gx:.1f}" y2="{y_axis - 12}"/>'
                    f'<circle cx="{gx:.1f}" cy="{y_pin}" r="{rr}"/><text class="n" x="{gx:.1f}" y="{y_pin + 3.5}">{e(label)}</text>'
                    f'<g class="tip"><rect x="{tx0:.1f}" y="{ty0}" width="{tw}" height="{th}" rx="8"/>{tip}</g>')
            if link:
                out.append(f'<a class="pin" href="{link}" aria-label="your prompt {e(label)}">{body}</a>')
            else:
                out.append(f'<g class="pin" tabindex="0">{body}</g>')
    height = max(height, tip_room)
    label = (f"{len(turns)} prompt(s), {len(items)} steps over {dur(span)}, {len(checks)} check(s) "
             f"of which {sum(1 for x in checks if x['check'] is False)} failed, {len(loops)} loop(s), "
             f"{len(subs)} sub-agent(s)")
    return (f'<svg class="viz glance-svg" viewBox="0 0 {width} {height:.0f}" role="img" aria-label="{e(label)}">'
            + "".join(out) + "</svg>")


def _key(items: List[dict], turns: bool, checks: bool, loops: bool) -> str:
    used = {x["activity"] for x in items}
    parts = []
    if turns:
        parts.append('<span><i class="pin"></i>your prompt (hover: what you asked)</span>')
    for k, (cls, _glyph, name) in ACTIVITIES.items():
        if k in used:
            parts.append(f'<span><i style="background:var(--{cls})"></i>{e(name)}</span>')
    if checks:
        parts.append('<span><i class="dot" style="background:var(--sg)"></i>✓ check passed</span>'
                     '<span><i class="dot" style="background:var(--sc)"></i>✗ check failed</span>')
    if loops:
        parts.append('<span><i class="loop"></i>↻ went round</span>')
    parts.append('<span>▼ where to look first</span><span>⁄⁄ idle, folded</span>')
    return '<div class="gkey">' + "".join(parts) + "</div>"


def glance_html(data: dict, tl: dict, r: dict, *, href: str = "") -> str:
    """The chart with the numbers it is about above it and its key below."""
    svg = glance_svg(data, tl, r, href=href)
    if not svg:
        return ""
    items = tl.get("steps") or []
    checks = [x for x in items if x.get("check") is not None]
    failed = sum(1 for x in checks if x["check"] is False)
    subs = {x["agent"] for x in items if x["agent"] != "root"}
    turns = data.get("turns") or []
    loops = _loops(tl, r)
    by_subs = sum(1 for x in items if x["agent"] != "root")
    bits = []
    if turns:
        bits.append(f"<span><b>{len(turns)}</b>prompt{'s' if len(turns) != 1 else ''}</span>")
    bits.append(f"<span><b>{len(items):,}</b>steps" + (f" · {by_subs:,} by {len(subs)} sub-agent(s)" if subs else "")
                + "</span>")
    bits.append(f"<span><b>{e(dur(r.get('active_s') or 0))}</b>working, of {e(dur(r.get('span_s') or 0))} on the clock</span>")
    if checks:
        bits.append(f"<span><b>{len(checks)}</b>checks · {failed} failed</span>")
    if loops:
        bits.append(f"<span><b>{len(loops)}</b>loop{'s' if len(loops) != 1 else ''}</span>")
    return (f'<div class="glance"><div class="gsum">{"".join(bits)}</div>{svg}'
            f'{_key(items, bool(turns), bool(checks), bool(loops))}</div>')


def worked_html(hours_by_run: List[dict], *, days: int = 14) -> str:
    """When you worked: each day a row, each hour of this machine's local time a cell, darker the more
    the agents were working in it, added up over every run given (``summary["hours"]``)."""
    import datetime as _dt
    total: Dict[str, float] = {}
    for h in hours_by_run:
        for k, v in (h or {}).items():
            total[k] = total.get(k, 0.0) + float(v)
    if not total:
        return ""
    last = max(_dt.datetime.strptime(k, "%Y-%m-%dT%H") for k in total).date()
    first = max(min(_dt.datetime.strptime(k, "%Y-%m-%dT%H") for k in total).date(), last - _dt.timedelta(days=days - 1))
    rows = [first + _dt.timedelta(days=k) for k in range((last - first).days + 1)]
    vals = sorted(v for k, v in total.items() if _dt.datetime.strptime(k, "%Y-%m-%dT%H").date() >= first)
    cuts = [vals[min(len(vals) - 1, int(len(vals) * q))] for q in (0.25, 0.5, 0.75)]

    def level(v: float) -> int:
        return 0 if v <= 0 else 1 + sum(1 for c in cuts if v > c)
    left, cw, ch, gap, right = 92, 32, 14, 2, 74
    width = left + 24 * cw + right
    height = 18 + len(rows) * (ch + gap) + 4
    out = [f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="when the agents worked, by hour of day">']
    for hcol in range(0, 24, 3):
        out.append(f'<text x="{left + hcol * cw:.0f}" y="11">{hcol:02d}:00</text>')
    busiest = max(total.items(), key=lambda kv: kv[1])
    for r, day in enumerate(rows):
        y = 18 + r * (ch + gap)
        out.append(f'<text x="0" y="{y + 11}">{day:%a %d %b}</text>')
        day_total = 0.0
        for hcol in range(24):
            key = f"{day:%Y-%m-%d}T{hcol:02d}"
            v = total.get(key, 0.0)
            day_total += v
            out.append(f'<rect class="h" x="{left + hcol * cw:.0f}" y="{y}" width="{cw - gap}" height="{ch}" rx="2" '
                       f'style="fill:var(--h{level(v)})"><title>{day:%a %d %b}, {hcol:02d}:00–{(hcol + 1) % 24:02d}:00: '
                       f'{e(dur(v)) + " working" if v else "nothing"}</title></rect>')
        if day_total:
            out.append(f'<text x="{left + 24 * cw + 8}" y="{y + 11}">{e(dur(day_total))}</text>')
    out.append("</svg>")
    bday = _dt.datetime.strptime(busiest[0], "%Y-%m-%dT%H")
    key = ('<div class="wkey">less <i style="background:var(--h0)"></i><i style="background:var(--h1)"></i>'
           '<i style="background:var(--h2)"></i><i style="background:var(--h3)"></i><i style="background:var(--h4)"></i> more'
           f' · busiest hour {bday:%a %d %b %H}:00, {e(dur(busiest[1]))} working · this machine\'s local time</div>')
    return '<div class="worked">' + "".join(out) + key + "</div>"
