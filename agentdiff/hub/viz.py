"""The hub's charts, drawn on the server as SVG: one renderer for every page.

The same function draws a chart on a static page and on the live page,
where a fragment is re-fetched as a run grows, so there is never a second
implementation to drift from the first.

- :func:`lap_chart` — the agent's loop, one row per lap
- :func:`flow_ring` — how the run moves between its tools; cycles are loops
- :func:`eval_river` — the self-evolving loop: every eval's life across generations
- :func:`hop_timeline` — in-band telemetry hops on one clock, a lane per process
- :func:`lap_strip` — a lap chart folded to one line, for a list of traces
- :func:`step_ribbon` — the last steps of a running trace, newest at the right
- :func:`run_timeline` — one run on its clock: a lane per thread, laps, folds, where to look
- :func:`trunk_svg` — a run as a trunk with its tool calls as branches and its sub-agents
  hanging off it; two runs facing each other, the first difference marked
- :func:`pair_timeline` — two runs on one axis, the first difference marked
- :func:`ribbons_svg` — many runs, one row each, on one clock
- :func:`harness_river` — the self-evolving harness: pass rate per generation,
  every change tried with its paired test, the evals born and retired

Colour follows the activity, never its rank, in the fixed slot order of a
validated palette (worst adjacent CVD ΔE 9.1 light / 8.4 dark). Every mark
also carries a glyph, a hover title and a legend, so identity is never
colour alone, and every chart has a table view beside it on the page.
Pass and fail use the reserved status colours, always with ✓ or ✗.
"""

from __future__ import annotations

import html
import math
from typing import Dict, List, Optional

__all__ = ["ACTIVITIES", "lap_chart", "lap_table", "flow_ring", "eval_river", "hop_timeline", "legend",
           "activity_of_hop", "lap_strip", "step_ribbon", "compact_laps", "harness_river", "run_timeline",
           "pair_timeline", "ribbons_svg", "trunk_svg", "VIZ_CSS"]


def e(v) -> str:
    return html.escape("" if v is None else str(v), quote=True)


#: activity -> (css class, glyph, label), in the palette's slot order
ACTIVITIES: Dict[str, tuple] = {
    "explore": ("a1", "◇", "explore"),
    "edit": ("a2", "✎", "edit"),
    "verify": ("a3", "✓", "check"),
    "run": ("a4", "▸", "run"),
    "plan": ("a5", "≡", "plan"),
    "research": ("a5", "⌕", "research"),
    "delegate": ("a7", "⇄", "delegate"),
    "other": ("an", "·", "other"),
    "think": ("at", "…", "think"),
}

VIZ_CSS = """
:root{--a1:#2a78d6;--a2:#eb6834;--a3:#1baf7a;--a4:#eda100;--a5:#e87ba4;--a7:#4a3aa7;--an:#9a9993;--at:#d8d7d2;
--sg:#0ca30c;--sc:#d03b3b;--grid:#e3e2dc;--ink2:#52514e}
@media (prefers-color-scheme:dark){:root{--a1:#3987e5;--a2:#d95926;--a3:#199e70;--a4:#c98500;--a5:#d55181;
--a7:#9085e9;--an:#77766f;--at:#3d3c39;--grid:#33322f;--ink2:#c3c2b7}}
svg.viz{display:block;max-width:100%;height:auto}
svg.viz text{fill:var(--ink2);font:11px ui-monospace,SFMono-Regular,Menlo,monospace}
svg.viz text.lab{font:12px system-ui,sans-serif;fill:var(--ink)}
svg.viz .a1{fill:var(--a1)}svg.viz .a2{fill:var(--a2)}svg.viz .a3{fill:var(--a3)}svg.viz .a4{fill:var(--a4)}
svg.viz .a5{fill:var(--a5)}svg.viz .a7{fill:var(--a7)}svg.viz .an{fill:var(--an)}svg.viz .at{fill:var(--at)}
svg.viz text.g{fill:#fff;font:600 11px system-ui,sans-serif;pointer-events:none}
svg.viz text.g.dk{fill:#1d1d1b}
svg.viz .err{stroke:var(--sc);stroke-width:2}
svg.viz .grid{stroke:var(--grid);stroke-width:1}
svg.viz .ok{fill:var(--sg)}svg.viz .bad{fill:var(--sc)}
svg.viz text.ok{fill:var(--sg)}svg.viz text.bad{fill:var(--sc)}
svg.viz .bar{fill:var(--a1)}
svg.viz .edge{fill:none;stroke:var(--ink2);stroke-opacity:.45}
svg.viz .edge.cyc{stroke:var(--a2);stroke-opacity:.9}
svg.viz .rep{fill:none;stroke:var(--a2);stroke-width:1.5;stroke-dasharray:4 3}
svg.viz .lane{stroke:var(--ink2);stroke-width:2}
svg.viz .lane.quiet{stroke-dasharray:3 4;stroke-opacity:.6}
svg.viz rect.hot:hover,svg.viz circle.hot:hover{stroke:var(--ink);stroke-width:2}
.legend{display:flex;flex-wrap:wrap;gap:6px 14px;margin:6px 0 2px;font-size:12px;color:var(--soft)}
.legend span{display:inline-flex;align-items:center;gap:5px}
.legend i{display:inline-block;width:12px;height:12px;border-radius:3px;font-style:normal;text-align:center;
font-size:9px;line-height:12px;color:#fff}
.legend .a1{background:var(--a1)}.legend .a2{background:var(--a2)}.legend .a3{background:var(--a3)}
.legend .a4{background:var(--a4)}.legend .a5{background:var(--a5)}.legend .a7{background:var(--a7)}
.legend .an{background:var(--an)}.legend .at{background:var(--at);color:var(--ink)}
"""

_DARK_GLYPH = {"a4", "at", "a5", "a3"}   # light fills: a dark glyph reads better on them


def activity_of_hop(row: dict) -> str:
    from ..laps import _activity
    return _activity({"type": row.get("kind"), "name": row.get("tool") or ""})


def legend(used: List[str], extra: Optional[List[tuple]] = None) -> str:
    seen, parts = set(), []
    for act in [a for a in ACTIVITIES if a in used]:
        cls, glyph, label = ACTIVITIES[act]
        if (cls, label) in seen:
            continue
        seen.add((cls, label))
        parts.append(f'<span><i class="{cls}">{e(glyph)}</i>{e(label)}</span>')
    for mark, label in extra or []:
        parts.append(f"<span>{mark}{e(label)}</span>")
    return f'<div class="legend" aria-label="legend">{"".join(parts)}</div>'


# ----------------------------------------------------------------- laps
def lap_chart(result: dict, width: int = 980) -> str:
    rounds = result.get("laps") or []
    if not rounds:
        return '<p class="muted">No steps to draw.</p>'
    left, right, row_h, gap = 92, 170, 30, 8
    widest = max(len(r["steps"]) for r in rounds)
    # only as wide as the longest lap needs, so a short run stays legible on a phone
    width = int(min(width, max(520, left + widest * 28 + 130 + right)))
    tile = max(8.0, min(26.0, (width - left - right - 8) / max(1, widest) - 2))
    top = 22
    height = top + len(rounds) * (row_h + gap) + 6
    max_sec = max((r["seconds"] for r in rounds), default=0) or 1.0
    out = [f'<svg class="viz" viewBox="0 0 {width} {height}" width="{width}" role="img" '
           f'aria-label="{e(result.get("summary"))}">',
           f'<text x="{left}" y="13">steps in order →</text>',
           f'<text x="{width - right + 12}" y="13">lap time</text>']
    for k, r in enumerate(rounds):
        y = top + k * (row_h + gap)
        closed = r.get("closed_by")
        passed = (closed or {}).get("passed")
        badge, cls = ("✓", "ok") if passed is True else ("✗", "bad") if passed is False else ("–", "")
        out.append(f'<text class="lab" x="4" y="{y + 19}">lap {r["n"]}</text>')
        out.append(f'<text class="{cls}" x="56" y="{y + 20}" style="font-size:15px">{badge}</text>')
        x = left
        for s in r["steps"]:
            act = s["activity"]
            ccls, glyph, label = ACTIVITIES.get(act, ACTIVITIES["other"])
            if act == "verify" and closed and closed.get("index") == s["index"]:
                glyph = "✓" if passed else ("✗" if passed is False else "?")
            tip = f'#{s["index"]} {s["name"]} · {label} · {s["latency_s"]:.2f}s' + (" · error" if s["error"] else "")
            if s.get("reward") is not None:
                tip += f' · reward {s["reward"]:+g}'
            stroke = ' class="err"' if s["error"] else ""
            out.append(f'<g><rect class="{ccls} hot" x="{x:.1f}" y="{y + 2}" width="{tile:.1f}" height="{row_h - 4}" '
                       f'rx="4"><title>{e(tip)}</title></rect>'
                       + (f'<rect x="{x:.1f}" y="{y + 2}" width="{tile:.1f}" height="{row_h - 4}" rx="4" fill="none"{stroke}/>'
                          if s["error"] else "")
                       + (f'<text class="g{" dk" if ccls in _DARK_GLYPH else ""}" x="{x + tile / 2:.1f}" y="{y + 19}" '
                          f'text-anchor="middle">{e(glyph)}</text>' if tile >= 15 else "") + "</g>")
            x += tile + 2
        if r.get("same_as_previous"):
            out.append(f'<rect class="rep" x="{left - 4}" y="{y}" width="{x - left + 6:.1f}" height="{row_h}" rx="6"/>')
            out.append(f'<text x="{x + 6:.1f}" y="{y + 19}" style="fill:var(--a2)">↻ same as lap {r["n"] - 1}</text>')
        bar_w = (right - 70) * (r["seconds"] / max_sec)
        bx = width - right + 12
        out.append(f'<rect class="bar" x="{bx}" y="{y + 10}" width="{max(2.0, bar_w):.1f}" height="10" rx="3">'
                   f'<title>{r["seconds"]:.2f}s, {r["tokens"]:,} tokens</title></rect>')
        out.append(f'<text x="{bx + max(2.0, bar_w) + 6:.1f}" y="{y + 19}">{r["seconds"]:.1f}s</text>')
    out.append("</svg>")
    used = sorted({s["activity"] for r in rounds for s in r["steps"]})
    extra = [('<b style="color:var(--sg)">✓</b>', "check passed"), ('<b style="color:var(--sc)">✗</b>', "check failed"),
             ('<b style="color:var(--a2)">↻</b>', "same calls as the lap before")]
    return "".join(out) + legend(used, extra)


def lap_table(result: dict) -> str:
    rows = []
    for r in result.get("laps") or []:
        c = r.get("closed_by") or {}
        verdict = "passed" if c.get("passed") is True else "failed" if c.get("passed") is False else "—"
        rows.append(f'<tr><td>{r["n"]}</td><td>{e(" → ".join(s["name"] for s in r["steps"]))}</td>'
                    f'<td>{e(verdict)}</td><td class="n">{r["seconds"]:.1f}s</td><td class="n">{r["tokens"]:,}</td>'
                    f'<td>{"↻" if r.get("same_as_previous") else ""}</td></tr>')
    return ('<table><tr><th>lap</th><th>steps</th><th>closed by a check that</th><th class="n">time</th>'
            '<th class="n">tokens</th><th>repeat</th></tr>' + "".join(rows) + "</table>")


# ------------------------------------------------------------ flow ring
def flow_ring(result: dict, size: int = 420) -> str:
    edges = result.get("transitions") or []
    if not edges:
        return '<p class="muted">One tool or none: no moves between tools to draw.</p>'
    calls: Dict[str, int] = {}
    acts: Dict[str, str] = {}
    order: List[str] = []
    for r in result.get("laps") or []:
        for s in r["steps"]:
            if s["activity"] == "think":
                continue
            calls[s["name"]] = calls.get(s["name"], 0) + 1
            acts.setdefault(s["name"], s["activity"])
            if s["name"] not in order:
                order.append(s["name"])
    nodes = order[:12]
    folded = len(order) - len(nodes)
    cx = cy = size / 2
    R = size / 2 - 70
    pos = {n: (cx + R * math.cos(-math.pi / 2 + 2 * math.pi * i / len(nodes)),
               cy + R * math.sin(-math.pi / 2 + 2 * math.pi * i / len(nodes))) for i, n in enumerate(nodes)}
    pairs = {(x["from"], x["to"]) for x in edges}
    top = max(x["count"] for x in edges)
    out = [f'<svg class="viz" viewBox="0 0 {size} {size}" width="{size}" role="img" aria-label="moves between tools; '
           f'orange marks a cycle">',
           '<defs><marker id="arw" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="9" markerHeight="9" '
           'markerUnits="userSpaceOnUse" orient="auto-start-reverse"><path d="M0,0 L8,4 L0,8 z" style="fill:var(--ink2)"/></marker></defs>']
    for x in edges:
        a, b, n = x["from"], x["to"], x["count"]
        if a not in pos or b not in pos:
            continue
        w = 1.5 + 4.5 * (n / top)
        cyc = a == b or (b, a) in pairs
        tip = f"{a} → {b}: {n}×"
        if a == b:
            px, py = pos[a]
            dx, dy = px - cx, py - cy
            d = math.hypot(dx, dy) or 1
            ox, oy = px + dx / d * 22, py + dy / d * 22
            out.append(f'<circle class="edge{" cyc" if cyc else ""}" cx="{ox:.1f}" cy="{oy:.1f}" r="12" '
                       f'style="stroke-width:{w:.1f}"><title>{e(tip)}</title></circle>')
            out.append(f'<text x="{ox + dx / d * 18:.1f}" y="{oy + dy / d * 18 + 4:.1f}" text-anchor="middle">{n}×</text>')
            continue
        (x1, y1), (x2, y2) = pos[a], pos[b]
        mx, my = (x1 + x2) / 2, (y1 + y2) / 2
        # bend each direction to its own side, so A→B and B→A never overlap
        nx, ny = -(y2 - y1), x2 - x1
        nd = math.hypot(nx, ny) or 1
        qx, qy = mx + nx / nd * 28 + (cx - mx) * 0.25, my + ny / nd * 28 + (cy - my) * 0.25
        # stop the arrow at the node's edge
        tx, ty = x2 - qx, y2 - qy
        td = math.hypot(tx, ty) or 1
        r2 = _node_r(calls.get(b, 1), max(calls.values()))
        ex, ey = x2 - tx / td * (r2 + 3), y2 - ty / td * (r2 + 3)
        out.append(f'<path class="edge{" cyc" if cyc else ""}" d="M{x1:.1f},{y1:.1f} Q{qx:.1f},{qy:.1f} {ex:.1f},{ey:.1f}" '
                   f'style="stroke-width:{w:.1f}" marker-end="url(#arw)"><title>{e(tip)}</title></path>')
        if n >= max(2, top * 0.5):
            out.append(f'<text x="{qx:.1f}" y="{qy:.1f}" text-anchor="middle">{n}×</text>')
    biggest = max(calls.values())
    for name in nodes:
        px, py = pos[name]
        cls, glyph, label = ACTIVITIES.get(acts.get(name, "other"), ACTIVITIES["other"])
        r = _node_r(calls[name], biggest)
        out.append(f'<circle class="{cls} hot" cx="{px:.1f}" cy="{py:.1f}" r="{r:.1f}" '
                   f'style="stroke:var(--panel);stroke-width:2"><title>{e(name)} · {label} · {calls[name]} call(s)</title></circle>')
        out.append(f'<text class="g{" dk" if cls in _DARK_GLYPH else ""}" x="{px:.1f}" y="{py + 4:.1f}" '
                   f'text-anchor="middle">{e(glyph)}</text>')
        lx, ly = cx + (R + r + 16) * (px - cx) / R, cy + (R + r + 16) * (py - cy) / R
        anchor = "middle" if abs(lx - cx) < 20 else ("start" if lx > cx else "end")
        out.append(f'<text class="lab" x="{lx:.1f}" y="{ly + 4:.1f}" text-anchor="{anchor}">{e(name[:18])}</text>')
    if folded:
        out.append(f'<text x="8" y="{size - 8}">+{folded} more tool(s) not drawn</text>')
    out.append("</svg>")
    return "".join(out) + legend(sorted(set(acts[n] for n in nodes)),
                                 [('<b style="color:var(--a2)">⟲</b>', "a cycle: a tool repeats, or two call each other")])


def _node_r(n: int, biggest: int) -> float:
    return 9 + 11 * math.sqrt(n / max(1, biggest))


# ------------------------------------------------------------ eval river
def eval_river(result: dict, width: int = 980) -> str:
    lineage = [g for g in result.get("lineage") or [] if not g.get("skipped")]
    evals = result.get("evals") or []
    if not lineage:
        return '<p class="muted">No generations read.</p>'
    gens = [g["generation"] for g in lineage]
    left, top_h, lane_h = 230, 92, 30
    col = (width - left - 20) / max(1, len(gens))
    height = top_h + 30 + lane_h * max(1, len(evals)) + 10
    x = lambda i: left + col * i + col / 2  # noqa: E731
    out = [f'<svg class="viz" viewBox="0 0 {width} {height}" width="{width}" role="img" '
           f'aria-label="every eval across generations, with the forward coverage of each">',
           f'<text x="{left - 10}" y="20" text-anchor="end">forward coverage</text>',
           f'<text x="{left - 10}" y="36" text-anchor="end">on runs never seen</text>']
    base = 76
    out.append(f'<line class="grid" x1="{left}" x2="{width - 20}" y1="{base}" y2="{base}"/>')
    for i, g in enumerate(lineage):
        cov = g["forward"]["coverage"] if g.get("arrived") else None
        bx = x(i) - 14
        if cov is not None:
            h = 52 * cov
            out.append(f'<rect class="bar" x="{bx:.1f}" y="{base - h:.1f}" width="28" height="{max(2.0, h):.1f}" rx="4">'
                       f'<title>{g["generation"]}: the suite carried in caught {g["forward"]["caught"]} of '
                       f'{g["wrong"]} ({cov:.0%}), {g["forward"]["false_alarms"]} false alarm(s)</title></rect>')
            out.append(f'<text x="{x(i):.1f}" y="{base - h - 5:.1f}" text-anchor="middle">{cov:.0%}</text>')
        else:
            out.append(f'<text x="{x(i):.1f}" y="{base - 6}" text-anchor="middle">—</text>')
        out.append(f'<text class="lab" x="{x(i):.1f}" y="{base + 18}" text-anchor="middle">{e(g["generation"])}</text>')
        out.append(f'<text x="{x(i):.1f}" y="{base + 32}" text-anchor="middle">{g["wrong"]}/{g["runs"]} to catch</text>')
    idx = {g: i for i, g in enumerate(gens)}
    for k, ev in enumerate(evals):
        y = top_h + 34 + k * lane_h
        hist = [h for h in ev.get("history") or [] if h["generation"] in idx]
        out.append(f'<text class="lab" x="{left - 10}" y="{y + 4}" text-anchor="end">{e(ev["id"][:34])}'
                   f'<title>{e(ev.get("says"))}</title></text>')
        if not hist:
            continue
        for a, b in zip(hist, hist[1:]):
            quiet = b["verdict"] in ("quiet",) or a["verdict"] == "retired"
            if a["verdict"] in ("noisy",):
                continue
            out.append(f'<line class="lane{" quiet" if quiet else ""}" x1="{x(idx[a["generation"]]):.1f}" '
                       f'x2="{x(idx[b["generation"]]):.1f}" y1="{y}" y2="{y}"/>')
        retired_at = ev.get("retired_at") if ev.get("status") == "retired" else None
        for h in hist:
            cx_ = x(idx[h["generation"]])
            tip = (f'{ev["id"]} at {h["generation"]}: {h["verdict"]}, caught {h["caught"]} of {h["wrong"]}, '
                   f'{h["false_alarms"]} false alarm(s)')
            v = h["verdict"]
            if v == "born":
                mark = f'<circle class="ok hot" cx="{cx_:.1f}" cy="{y}" r="7"><title>{e(tip)}</title></circle>'
            elif v == "reborn":
                mark = (f'<circle class="a7 hot" cx="{cx_:.1f}" cy="{y}" r="7"><title>{e(tip)}</title></circle>'
                        f'<text class="g" x="{cx_:.1f}" y="{y + 4}" text-anchor="middle">↺</text>')
            elif v == "holds":
                mark = f'<rect class="a1 hot" x="{cx_ - 6:.1f}" y="{y - 6}" width="12" height="12" rx="3"><title>{e(tip)}</title></rect>'
            elif v == "quiet":
                mark = (f'<circle class="hot" cx="{cx_:.1f}" cy="{y}" r="6" style="fill:var(--panel);stroke:var(--ink2);'
                        f'stroke-width:2"><title>{e(tip)}</title></circle>')
            elif v == "noisy":
                mark = f'<text class="bad" x="{cx_:.1f}" y="{y + 5}" text-anchor="middle" style="font-size:15px">✕<title>{e(tip)}</title></text>'
            else:
                mark = f'<circle class="an hot" cx="{cx_:.1f}" cy="{y}" r="5"><title>{e(tip)}</title></circle>'
            out.append(mark)
        if retired_at in idx:
            rx = x(idx[retired_at]) + 16
            out.append(f'<text class="bad" x="{rx:.1f}" y="{y + 5}" style="font-size:15px">✕<title>{e(ev.get("reason"))}</title></text>')
    out.append("</svg>")
    key = [('<b style="color:var(--sg)">●</b>', "born"), ('<b style="color:var(--a1)">■</b>', "holds on runs it never saw"),
           ('<b style="color:var(--ink2)">○</b>', "quiet: caught nothing"), ('<b style="color:var(--sc)">✕</b>', "retired"),
           ('<b style="color:var(--a7)">↺</b>', "reborn: its failure came back")]
    return "".join(out) + legend([], key)


# ------------------------------------------------------------ hop timeline
def _clock(t: float, span: float) -> str:
    if span < 1:
        return f"{t * 1000:.0f}ms" if span >= 0.01 else f"{t * 1000:.1f}ms"
    if span < 120:
        return f"{t:.1f}s" if span < 10 else f"{t:.0f}s"
    return f"{t / 60:.1f}m"


def hop_timeline(rows: List[dict], span: float, width: int = 980, live: bool = False) -> str:
    nodes = sorted({r.get("node") or "?" for r in rows})
    lane_h, left = 30, 170
    height = 26 + lane_h * max(1, len(nodes))
    span = span or 1.0
    x = lambda t: left + (width - left - 12) * (t / span)  # noqa: E731
    out = [f'<svg class="viz" viewBox="0 0 {width} {height}" width="{width}" role="img" '
           f'aria-label="hops on one clock, one lane per process">']
    for i in range(5):
        t = span * i / 4
        anchor = "start" if i == 0 else "end" if i == 4 else "middle"
        out.append(f'<line class="grid" x1="{x(t):.1f}" x2="{x(t):.1f}" y1="18" y2="{height}"/>')
        out.append(f'<text x="{x(t):.1f}" y="12" text-anchor="{anchor}">{e(_clock(t, span))}</text>')
    used = set()
    for li, node in enumerate(nodes):
        y = 22 + li * lane_h
        out.append(f'<text x="4" y="{y + 16}">{e(node[:24])}</text>')
        for r in rows:
            if (r.get("node") or "?") != node:
                continue
            act = activity_of_hop(r)
            used.add(act)
            cls = ACTIVITIES.get(act, ACTIVITIES["other"])[0]
            x0 = x(r.get("start") or 0.0)
            w = max(3.0, x((r.get("start") or 0.0) + (r.get("latency") or 0.0)) - x0)
            tip = f'{r.get("tool")} · {r.get("status")} · {(r.get("latency") or 0):.3f}s'
            if r.get("reward") is not None:
                tip += f' · reward {r["reward"]:+g}'
            bad = r.get("status") not in (None, "ok")
            out.append(f'<rect class="{cls} hot" x="{x0:.1f}" y="{y + 3}" width="{w:.1f}" height="{lane_h - 10}" rx="4">'
                       f'<title>{e(tip)}</title></rect>')
            if bad:
                out.append(f'<rect class="err" x="{x0:.1f}" y="{y + 3}" width="{w:.1f}" height="{lane_h - 10}" rx="4" fill="none"/>')
            label = str(r.get("tool") or "")
            if w > 7 * len(label) + 10:
                out.append(f'<text class="g{" dk" if cls in _DARK_GLYPH else ""}" x="{x0 + 5:.1f}" y="{y + 16}">{e(label)}</text>')
    if live:
        out.append(f'<line x1="{x(span):.1f}" x2="{x(span):.1f}" y1="18" y2="{height}" '
                   f'style="stroke:var(--sc);stroke-width:2"><title>now</title></line>')
    out.append("</svg>")
    return "".join(out) + legend(sorted(used), [('<b style="color:var(--sc)">▢</b>', "not ok")])


# --------------------------------------------------- small, for lists
def compact_laps(result: dict) -> dict:
    """What a list keeps of :func:`agentdiff.laps.laps`: a mark per lap."""
    marks = []
    for r in result.get("laps") or []:
        passed = (r.get("closed_by") or {}).get("passed")
        marks.append(["p" if passed is True else "f" if passed is False else "n", bool(r.get("same_as_previous"))])
    b = ((result.get("stuck") or {}).get("longest_repeated_block") or {})
    return {"marks": marks, "basis": result.get("basis"), "first_pass_lap": result.get("first_pass_lap"),
            "repeated": len(result.get("repeated_laps") or []), "stuck": "stuck:" in (result.get("summary") or ""),
            "block": [b.get("period"), b.get("repeats")] if b else None, "summary": result.get("summary")}


def lap_strip(compact: Optional[dict], most: int = 24) -> str:
    """One square per lap: ✓ passed, ✗ failed, – no check; an orange bar
    under a lap that repeated the one before. The summary is the hover title."""
    marks = (compact or {}).get("marks") or []
    if not marks:
        return '<span class="muted">—</span>'
    shown = marks[:most]
    w = 16 * len(shown) + (26 if len(marks) > most else 2)
    out = [f'<svg class="viz strip" viewBox="0 0 {w} 19" width="{w}" height="19" role="img" '
           f'aria-label="{e(compact.get("summary"))}"><title>{e(compact.get("summary"))}</title>']
    for i, (state, rep) in enumerate(shown):
        x = 1 + i * 16
        cls, glyph = {"p": ("ok", "✓"), "f": ("bad", "✗")}.get(state, ("an", "–"))
        out.append(f'<rect class="{cls}" x="{x}" y="1" width="13" height="13" rx="3"/>'
                   f'<text class="g" x="{x + 6.5}" y="11.5" text-anchor="middle" style="font-size:9px">{glyph}</text>')
        if rep:
            # a repeat: an orange bar under it, joined to the lap it repeated
            out.append(f'<rect class="a2" x="{x - 3}" y="16" width="16" height="3" rx="1.5"/>')
    if len(marks) > most:
        out.append(f'<text x="{1 + most * 16 + 2}" y="12">+{len(marks) - most}</text>')
    out.append("</svg>")
    return "".join(out)


def step_ribbon(steps: List[dict], most: int = 48, width: int = 560) -> str:
    """The run's last ``most`` steps as tiles, newest at the right, so a
    running agent's rhythm (and a loop forming) is visible at a glance."""
    from ..laps import _activity
    tail = steps[-most:]
    if not tail:
        return '<span class="muted">no steps yet</span>'
    tile = min(16.0, (width - 4) / max(1, len(tail)) - 2)
    w = 2 + len(tail) * (tile + 2)
    out = [f'<svg class="viz" viewBox="0 0 {w:.0f} 22" width="{w:.0f}" height="22" role="img" '
           f'aria-label="the last {len(tail)} steps, newest at the right">']
    for i, s in enumerate(tail):
        act = _activity(s)
        cls, glyph, label = ACTIVITIES.get(act, ACTIVITIES["other"])
        x = 2 + i * (tile + 2)
        name = str(s.get("name") or s.get("type") or "step")
        out.append(f'<rect class="{cls} hot" x="{x:.1f}" y="3" width="{tile:.1f}" height="16" rx="3">'
                   f'<title>{e(name)} · {e(label)}{" · error" if s.get("error") else ""}</title></rect>')
        if s.get("error"):
            out.append(f'<rect class="err" x="{x:.1f}" y="3" width="{tile:.1f}" height="16" rx="3" fill="none"/>')
    out.append("</svg>")
    return "".join(out)


# ------------------------------------------------------ harness river
def _rate(passed_n) -> Optional[float]:
    if not passed_n or not passed_n[1]:
        return None
    return passed_n[0] / passed_n[1]


def harness_river(result: dict, width: int = 980) -> str:
    """One column per generation. Bars: the share of runs that passed under the
    harness as it was (blue), and under the change tried that round (green
    when kept, outlined when reverted). Under them, the change, its verdict
    and the evals that entered and left; a version lane joins the columns."""
    gens = result.get("lineage") or []
    if not gens:
        return '<p class="muted">No generation has run yet.</p>'
    left, col_min = 150, 150
    width = int(max(width, left + col_min * len(gens) + 20)) if len(gens) > 5 else width
    col = (width - left - 20) / len(gens)
    top, bar_h = 44, 86
    base = top + bar_h
    height = base + 132
    x = lambda i: left + col * i + col / 2  # noqa: E731
    out = [f'<svg class="viz" viewBox="0 0 {width} {height}" width="{width}" role="img" '
           f'aria-label="{e(result.get("narrative"))}">',
           f'<text x="{left - 10}" y="14" text-anchor="end">runs that passed</text>',
           f'<text class="lab" x="{left - 10}" y="{base + 22}" text-anchor="end">harness</text>',
           f'<text class="lab" x="{left - 10}" y="{base + 58}" text-anchor="end">change tried</text>',
           f'<text class="lab" x="{left - 10}" y="{base + 104}" text-anchor="end">evals</text>']
    for frac in (0.5, 1.0):
        y = base - bar_h * frac
        out.append(f'<line class="grid" x1="{left}" x2="{width - 20}" y1="{y:.1f}" y2="{y:.1f}"/>'
                   f'<text x="{left - 10}" y="{y + 4:.1f}" text-anchor="end">{frac:.0%}</text>')
    out.append(f'<line class="grid" x1="{left}" x2="{width - 20}" y1="{base}" y2="{base}"/>')
    prev = None
    for i, g in enumerate(gens):
        cx = x(i)
        runs, failed = g.get("runs") or 0, g.get("failed") or 0
        cur = (runs - failed) / runs if runs else None
        a = g.get("action") or {}
        t = a.get("test") or {}
        chg = _rate((t.get("passed") or {}).get("changed"))
        kept = t.get("verdict") == "kept"
        bw = 36
        if cur is not None:
            h = max(2.0, bar_h * cur)
            out.append(f'<rect class="a1 hot" x="{cx - bw - 3:.1f}" y="{base - h:.1f}" width="{bw}" height="{h:.1f}" rx="4">'
                       f'<title>{e(g["generation"])}: {runs - failed} of {runs} run(s) passed under '
                       f'v{e(g["harness"]["version"])}</title></rect>'
                       f'<text x="{cx - bw / 2 - 3:.1f}" y="{base - h - 5:.1f}" text-anchor="middle">{runs - failed}/{runs}</text>')
        if chg is not None:
            pc = t["passed"]["changed"]
            h = max(2.0, bar_h * chg)
            style = "" if kept else ' style="fill:none;stroke:var(--a3);stroke-width:2;stroke-dasharray:4 3"'
            out.append(f'<rect class="a3 hot" x="{cx + 3:.1f}" y="{base - h:.1f}" width="{bw}" height="{h:.1f}" rx="4"{style}>'
                       f'<title>with {e(a["remedy"]["id"])}: {pc[0]} of {pc[1]} passed ({e(t.get("verdict"))})</title></rect>'
                       f'<text x="{cx + bw / 2 + 3:.1f}" y="{base - h - 5:.1f}" text-anchor="middle">{pc[0]}/{pc[1]}</text>')
        # the version lane
        v = g["harness"]["version"]
        vy = base + 18
        if prev is not None:
            out.append(f'<line class="lane" x1="{prev[0] + 16:.1f}" x2="{cx - 16:.1f}" y1="{vy}" y2="{vy}"/>')
        out.append(f'<rect class="hot" x="{cx - 15:.1f}" y="{vy - 10}" width="30" height="20" rx="10" '
                   f'style="fill:var(--panel);stroke:var(--a1);stroke-width:2"><title>{e(g["generation"])} ran '
                   f'harness v{v}: {e(", ".join(g["harness"].get("remedies") or []) or "nothing added")}</title></rect>'
                   f'<text class="lab" x="{cx:.1f}" y="{vy + 4}" text-anchor="middle" style="font-size:11px">v{v}</text>')
        out.append(f'<text class="lab" x="{cx:.1f}" y="14" text-anchor="middle" style="font-weight:600">'
                   f'{e(g["generation"])}</text>')
        prev = (cx, vy)
        # the change and its paired test
        if a:
            badge, cls = ("✓ kept", "ok") if kept else ("✗ reverted", "bad")
            rid = str(a["remedy"]["id"]).split(":", 1)[-1]
            out.append(f'<text class="{cls}" x="{cx:.1f}" y="{base + 52}" text-anchor="middle" '
                       f'style="font:600 12px system-ui,sans-serif">{badge}<title>{e(t.get("why"))}</title></text>'
                       f'<text x="{cx:.1f}" y="{base + 68}" text-anchor="middle">{e(rid[:24])}'
                       f'<title>{e(a["remedy"]["value"])} (because: {e(a["because"].get("says"))}, '
                       f'{e(a["because"].get("caught"))} of {e(a["because"].get("wrong"))} failure(s))</title></text>')
        elif failed == 0 and runs:
            out.append(f'<text class="ok" x="{cx:.1f}" y="{base + 52}" text-anchor="middle" '
                       f'style="font:600 12px system-ui,sans-serif">✓ all passed</text>')
        else:
            out.append(f'<text x="{cx:.1f}" y="{base + 52}" text-anchor="middle">nothing to try</text>')
        ev = g.get("evals") or {}
        born, retired = ev.get("born") or [], ev.get("retired") or []
        tip = "; ".join(filter(None, [("born " + ", ".join(born)) if born else "",
                                      ("retired " + ", ".join(retired)) if retired else ""])) or "no change to the suite"
        out.append(f'<text x="{cx:.1f}" y="{base + 104}" text-anchor="middle">'
                   f'<tspan style="fill:var(--sg)">+{len(born)}</tspan>  <tspan style="fill:var(--sc)">−{len(retired)}</tspan>'
                   f'<title>{e(tip)}</title></text>')
    out.append("</svg>")
    key = [('<i class="a1"></i>', "passed under the harness as it was"),
           ('<i class="a3"></i>', "passed with the change (kept)"),
           ('<i style="background:none;border:2px dashed var(--a3);width:10px;height:10px"></i>', "with the change (reverted)"),
           ('<b style="color:var(--sg)">+</b><b style="color:var(--sc)">−</b>', "evals born, retired")]
    return "".join(out) + legend([], key)


# --------------------------------------------------------- run timeline
class _Clock:
    """Seconds to x, with folded stretches drawn short (``timeline.fold_width``)."""

    def __init__(self, segments: list, x0: float, x1: float, span: float) -> None:
        from ..timeline import fold_width
        self.segs = segments or [{"kind": "open", "from": 0.0, "to": span or 1.0}]
        folds = sum(fold_width(s["to"] - s["from"]) for s in self.segs if s["kind"] == "fold")
        opens = sum(s["to"] - s["from"] for s in self.segs if s["kind"] == "open") or 1.0
        width = max(40.0, (x1 - x0) - folds)
        self.parts, x = [], x0
        for s in self.segs:
            w = fold_width(s["to"] - s["from"]) if s["kind"] == "fold" else width * (s["to"] - s["from"]) / opens
            self.parts.append((s, x, x + w))
            x += w
        self.x0, self.x1 = x0, x

    def __call__(self, t: float) -> float:
        for s, a, b in self.parts:
            if t <= s["to"] or s is self.parts[-1][0]:
                if s["to"] <= s["from"]:
                    return a
                f = min(1.0, max(0.0, (t - s["from"]) / (s["to"] - s["from"])))
                return a + (b - a) * f
        return self.x1


def _secs(t: float, span: float) -> str:
    return _clock(t, span)


def run_timeline(t: dict, width: int = 980, live: bool = False, link: bool = True) -> str:
    """One run on its clock: a lane per thread, the laps as bands, quiet
    stretches folded, the tokens spent as a track, and where to look first."""
    steps, lanes = t.get("steps") or [], t.get("lanes") or []
    if not steps:
        return '<p class="muted">No steps to draw.</p>'
    left, top, lane_h = 150, 54, 16 if len(lanes) > 10 else 22
    track_h = 34
    height = top + lane_h * len(lanes) + 14 + track_h + 22
    clock = _Clock(t.get("segments") or [], left, width - 14, t.get("span_s") or 1.0)
    span = t.get("span_s") or 1.0
    here = t.get("look_here") or {}
    by_index = {s["index"]: s for s in steps}
    out = [f'<svg class="viz" viewBox="0 0 {width} {height}" width="{width}" role="img" '
           f'aria-label="{e(here.get("sentence") or t.get("lap_summary"))}">']
    # the laps, as bands across every lane
    lap_label_end = -1e9
    for b in t.get("laps") or []:
        xa, xb = clock(b["from"]), max(clock(b["to"]), clock(b["from"]) + 2)
        shade = "var(--grid)" if b["n"] % 2 else "none"
        out.append(f'<rect x="{xa:.1f}" y="{top - 22}" width="{xb - xa:.1f}" height="{lane_h * len(lanes) + 24}" '
                   f'style="fill:{shade};opacity:.45"/>')
        badge, cls = ("✓", "ok") if b["passed"] is True else ("✗", "bad") if b["passed"] is False else ("", "")
        lab = f"lap {b['n']}" + (f" {badge}" if badge else "") + (" ↻" if b["repeat"] else "")
        if xa >= lap_label_end + 4 and (xb - xa > 6 * len(lab) or len(t["laps"]) <= 12):
            lap_label_end = xa + 6.2 * len(lab)
            out.append(f'<text class="{cls}" x="{xa + 3:.1f}" y="{top - 10}" style="font-size:10px">{e(lab)}'
                       f'<title>lap {b["n"]}: {b["steps"]} step(s)'
                       f'{", closed by a check that " + ("passed" if b["passed"] else "failed") if b["passed"] is not None else ""}'
                       f'{", the same calls as the lap before" if b["repeat"] else ""}</title></text>')
        if b["repeat"]:
            out.append(f'<rect class="rep" x="{xa:.1f}" y="{top - 22}" width="{xb - xa:.1f}" height="{lane_h * len(lanes) + 24}" rx="3"/>')
    # folds, across every lane
    for s, a, b2 in clock.parts:
        if s["kind"] != "fold":
            continue
        n = s.get("steps", 0)
        tip = (f"{n} quiet step(s), {s['to'] - s['from']:.1f}s: no edit, check, error, answer or new thread"
               if n else f"{s['to'] - s['from']:.1f}s with no step at all")
        out.append(f'<rect x="{a:.1f}" y="{top - 4}" width="{b2 - a:.1f}" height="{lane_h * len(lanes) + 6}" '
                   f'style="fill:var(--panel);stroke:var(--ink2);stroke-dasharray:2 3;stroke-opacity:.6"><title>{e(tip)}</title></rect>'
                   f'<text x="{(a + b2) / 2:.1f}" y="{top + lane_h * len(lanes) + 12}" text-anchor="middle" '
                   f'style="font-size:9px">⋯{n if n else ""}</text>')
    # the threads
    row = {l["id"]: k for k, l in enumerate(lanes)}
    for k, l in enumerate(lanes):
        y = top + k * lane_h
        indent = 4 + 10 * l.get("depth", 0)
        out.append(f'<text x="{indent}" y="{y + lane_h - 5}" style="font-size:{10 if lane_h < 20 else 11}px">'
                   f'{e(l["label"][:20])}<title>{e(l["label"])}: {l["steps"]} step(s)</title></text>')
        out.append(f'<line class="grid" x1="{left}" x2="{clock.x1:.1f}" y1="{y + lane_h - 1}" y2="{y + lane_h - 1}"/>')
    for s in steps:
        k = row.get(s.get("lane"), 0)
        y = top + k * lane_h + 2
        x0 = clock(s["start"])
        w = max(2.5, clock(s["end"]) - x0)
        cls = ACTIVITIES.get(s["activity"], ACTIVITIES["other"])[0]
        tip = (f'#{s["index"]} {s["name"]} · {ACTIVITIES.get(s["activity"], ACTIVITIES["other"])[2]} · '
               f'{s["start"]:.1f}s +{s["latency_s"]:.2f}s · {s["tokens"]:,} tokens'
               + (" · check " + ("passed" if s["check"] else "failed") if s["check"] is not None else "")
               + (" · error" if s["error"] else "") + (f" · lap {s['lap']}" if s.get("lap") else ""))
        bar = (f'<rect class="{cls} hot" x="{x0:.1f}" y="{y}" width="{w:.1f}" height="{lane_h - 5}" rx="2">'
               f'<title>{e(tip)}</title></rect>')
        if link:
            bar = f'<a href="#s{s["index"]}">{bar}</a>'
        out.append(bar)
        if s["error"] or s["check"] is False:
            out.append(f'<rect class="err" x="{x0:.1f}" y="{y}" width="{w:.1f}" height="{lane_h - 5}" rx="2" fill="none"/>')
        elif s["check"] is True:
            out.append(f'<rect x="{x0:.1f}" y="{y + lane_h - 6}" width="{w:.1f}" height="2" style="fill:var(--sg)"/>')
    # where to look first
    if here and here.get("index") in by_index:
        s = by_index[here["index"]]
        hx = clock(s["start"])
        col = "var(--sg)" if here.get("kind") == "pass" else "var(--a1)" if here.get("kind") == "now" else "var(--sc)"
        k = row.get(s.get("lane"), 0)
        out.append(f'<line x1="{hx:.1f}" x2="{hx:.1f}" y1="{top - 30}" y2="{top + lane_h * len(lanes)}" '
                   f'style="stroke:{col};stroke-width:2"><title>{e(here.get("sentence"))}</title></line>'
                   f'<circle cx="{hx + 1:.1f}" cy="{top + k * lane_h + lane_h / 2:.1f}" r="{lane_h / 2 + 3:.1f}" '
                   f'style="fill:none;stroke:{col};stroke-width:2"/>'
                   f'<text x="{hx + 5 if hx < width - 170 else hx - 5:.1f}" y="{top - 32}" '
                   f'text-anchor="{"start" if hx < width - 170 else "end"}" style="fill:{col};font:600 11px system-ui,sans-serif">'
                   f'◆ look here: step {here["index"]}<title>{e(here.get("sentence"))}</title></text>')
    # tokens, as they were spent
    ty = top + lane_h * len(lanes) + 18
    cum = t.get("tokens") or []
    if cum and cum[-1][1]:
        most = cum[-1][1]
        pts = [f"{clock(0):.1f},{ty + track_h:.1f}"]
        for at, n in cum:
            pts.append(f"{clock(at):.1f},{ty + track_h - track_h * n / most:.1f}")
        pts.append(f"{clock(cum[-1][0]):.1f},{ty + track_h:.1f}")
        out.append(f'<polygon points="{" ".join(pts)}" style="fill:var(--a1);opacity:.25"/>'
                   f'<text x="{left - 8}" y="{ty + track_h - 4}" text-anchor="end" style="font-size:10px">tokens</text>'
                   f'<text x="{clock(cum[-1][0]) - 4:.1f}" y="{ty + 10}" text-anchor="end" style="font-size:10px">{most:,}</text>')
    # the clock: real seconds at each open stretch's edges
    seen = []
    for s, a, b2 in clock.parts:
        for tt, xx in ((s["from"], a), (s["to"], b2)):
            if all(abs(xx - p) > 46 for p in seen):
                seen.append(xx)
                out.append(f'<text x="{xx:.1f}" y="12" text-anchor="middle" style="font-size:10px">{e(_secs(tt, span))}</text>')
    out.append(f'<text x="4" y="{top - 32}" style="font-size:10px">{e(t.get("basis"))} clock</text>')
    if live:
        out.append(f'<line x1="{clock.x1:.1f}" x2="{clock.x1:.1f}" y1="18" y2="{top + lane_h * len(lanes)}" '
                   f'style="stroke:var(--sc);stroke-width:2"><title>now</title></line>')
    out.append("</svg>")
    used = sorted({s["activity"] for s in steps})
    key = [('<b style="color:var(--sc)">◆</b>', "look here"), ('<b style="color:var(--sc)">▢</b>', "error or failed check"),
           ('<b style="color:var(--sg)">▁</b>', "passed check"), ('<b style="color:var(--a2)">⬚</b>', "repeated lap"),
           ('<b>⋯</b>', "folded quiet stretch")]
    return "".join(out) + legend(used, key)


# --------------------------------------------------------- two runs
def pair_timeline(c: dict, axis: str = "step", width: int = 980) -> str:
    """Two runs on one axis: by step (aligned columns, the first difference
    marked) or by time (both from 0 on one linear clock)."""
    rows = [("A", c["a"]), ("B", c["b"])]
    left, top, row_h = 150, 28, 30
    height = top + row_h * 2 + 30
    out = [f'<svg class="viz" viewBox="0 0 {width} {height}" width="{width}" role="img" aria-label="{e(c.get("sentence"))}">']
    if axis == "time":
        span = max(c["a"]["span_s"], c["b"]["span_s"]) or 1.0
        X = lambda tt: left + (width - left - 14) * tt / span  # noqa: E731
        for i in range(5):
            tt = span * i / 4
            out.append(f'<text x="{X(tt):.1f}" y="12" text-anchor="middle" style="font-size:10px">{e(_secs(tt, span))}</text>')
    else:
        n = max(len(c["a"]["steps"]), len(c["b"]["steps"])) or 1
        cw = (width - left - 14) / n
        for i in range(0, n, max(1, n // 10)):
            out.append(f'<text x="{left + cw * i + cw / 2:.1f}" y="12" text-anchor="middle" style="font-size:10px">{i}</text>')
    for k, (name, t) in enumerate(rows):
        y = top + k * row_h
        glyph, cls = (("✓", "ok") if t["success"] is True else ("✗", "bad") if t["success"] is False else ("–", ""))
        out.append(f'<text class="lab" x="4" y="{y + 17}">{name} · {e(str(t["agent"])[:14])}</text>'
                   f'<text class="{cls}" x="{left - 16}" y="{y + 18}" style="font-size:14px">{glyph}</text>')
        here = (t.get("look_here") or {}).get("index")
        for s in sorted(t["steps"], key=lambda s: s["index"]):
            if axis == "time":
                x0, w = X(s["start"]), max(2.5, X(s["end"]) - X(s["start"]))
            else:
                x0, w = left + cw * s["index"], max(1.5, cw - 1)
            cls2 = ACTIVITIES.get(s["activity"], ACTIVITIES["other"])[0]
            out.append(f'<rect class="{cls2} hot" x="{x0:.1f}" y="{y + 4}" width="{w:.1f}" height="{row_h - 10}" rx="2">'
                       f'<title>{name} #{s["index"]} {e(s["name"])}{" · error" if s["error"] else ""}'
                       f'{" · check " + ("passed" if s["check"] else "failed") if s["check"] is not None else ""}</title></rect>')
            if s["error"] or s["check"] is False:
                out.append(f'<rect class="err" x="{x0:.1f}" y="{y + 4}" width="{w:.1f}" height="{row_h - 10}" rx="2" fill="none"/>')
            if s["index"] == here:
                out.append(f'<circle cx="{x0 + w / 2:.1f}" cy="{y + row_h / 2 - 1:.1f}" r="{row_h / 2:.1f}" '
                           f'style="fill:none;stroke:var(--sc);stroke-width:2"><title>{e(t["look_here"]["sentence"])}</title></circle>')
    d = c.get("diverged_at")
    if d is not None and axis != "time":
        x = left + cw * d
        out.append(f'<line x1="{x:.1f}" x2="{x:.1f}" y1="{top - 6}" y2="{top + row_h * 2}" style="stroke:var(--a2);stroke-width:2;'
                   f'stroke-dasharray:4 3"><title>{e(c.get("sentence"))}</title></line>'
                   f'<text x="{x + 4:.1f}" y="{top + row_h * 2 + 16}" style="fill:var(--a2);font:600 11px system-ui,sans-serif">'
                   f'⟂ first difference: step {d}</text>')
    out.append("</svg>")
    return "".join(out)


# --------------------------------------------------------- many runs
def ribbons_svg(rows: List[dict], width: int = 980, shared: bool = True, links: Optional[list] = None,
                labels: Optional[list] = None) -> str:
    """Many runs, one row each: its steps compacted into cells along its clock
    (one clock for all of them, or each its own), its laps as ticks, how it
    ended, and where to look first ringed."""
    if not rows:
        return '<p class="muted">No runs to draw.</p>'
    left, right, row_h, top = 230, 78, 18, 20
    height = top + row_h * len(rows) + 8
    span = max(r["span_s"] for r in rows) or 1.0
    out = [f'<svg class="viz" viewBox="0 0 {width} {height}" width="{width}" role="img" '
           f'aria-label="{len(rows)} runs, one row each, on {"one clock" if shared else "their own clocks"}">']
    if shared:
        for i in range(5):
            tt = span * i / 4
            x = left + (width - left - right) * tt / span
            out.append(f'<line class="grid" x1="{x:.1f}" x2="{x:.1f}" y1="{top - 4}" y2="{height}"/>'
                       f'<text x="{x:.1f}" y="12" text-anchor="middle" style="font-size:10px">{e(_secs(tt, span))}</text>')
    for k, r in enumerate(rows):
        y = top + k * row_h
        own = r["span_s"] or 1.0
        X = (lambda tt, own=own: left + (width - left - right) * tt / (span if shared else own))
        glyph, cls = (("●", "") if r["in_progress"] else ("✓", "ok") if r["success"] is True
                      else ("✗", "bad") if r["success"] is False else ("–", ""))
        label = (labels[k] if labels else f'{r["agent"]} · {r["task"]}')
        short = label if len(label) <= 29 else label[:15] + "…" + label[-13:]
        txt = (f'<text x="{left - 22}" y="{y + 13}" text-anchor="end" style="font-size:11px">{e(short)}'
               f'<title>{e(label)}</title></text>')
        if links and links[k]:
            txt = f'<a href="{e(links[k])}">{txt}</a>'
        out.append(txt + f'<text class="{cls}" x="{left - 14}" y="{y + 13}" style="font-size:12px'
                   f'{";fill:var(--a1)" if r["in_progress"] else ""}">{glyph}</text>')
        here = (r.get("look_here") or {}).get("index")
        for c in r["cells"]:
            x0 = X(c["from"])
            w = max(1.5, X(c["to"]) - x0)
            cls2 = ACTIVITIES.get(c["activity"], ACTIVITIES["other"])[0]
            span_lbl = f'#{c["first"]}' + (f'–{c["last"]}' if c["last"] != c["first"] else "")
            out.append(f'<rect class="{cls2}" x="{x0:.1f}" y="{y + 3}" width="{w:.1f}" height="{row_h - 7}">'
                       f'<title>{span_lbl} · mostly {ACTIVITIES.get(c["activity"], ACTIVITIES["other"])[2]}'
                       f'{" · error" if c["error"] else ""}</title></rect>')
            if c["error"] or c["check"] is False:
                out.append(f'<rect x="{x0:.1f}" y="{y + row_h - 4}" width="{w:.1f}" height="2" style="fill:var(--sc)"/>')
            if here is not None and c["first"] <= here <= c["last"]:
                ring = {"pass": "var(--sg)", "now": "var(--a1)"}.get(r["look_here"].get("kind"), "var(--sc)")
                out.append(f'<circle cx="{x0 + w / 2:.1f}" cy="{y + row_h / 2 - 1:.1f}" r="7" '
                           f'style="fill:none;stroke:{ring};stroke-width:2"><title>{e(r["look_here"]["sentence"])}</title></circle>')
        for lp in r.get("laps") or []:
            x = X(lp["at"])
            col = "var(--sg)" if lp["passed"] is True else "var(--sc)" if lp["passed"] is False else "var(--ink2)"
            out.append(f'<line x1="{x:.1f}" x2="{x:.1f}" y1="{y + 1}" y2="{y + row_h - 3}" style="stroke:{col};stroke-width:1.5">'
                       f'<title>end of lap {lp["n"]}{" (repeat)" if lp["repeat"] else ""}</title></line>')
        end = X(r["span_s"])
        if r["in_progress"]:
            out.append(f'<circle class="a1" cx="{end + 4:.1f}" cy="{y + row_h / 2 - 1:.1f}" r="3"><title>running</title></circle>')
        out.append(f'<text x="{width - right + 16}" y="{y + 13}" style="font-size:10px">{e(_secs(r["span_s"], span))}'
                   f' · {r["steps"]}</text>')
    out.append("</svg>")
    used = sorted({c["activity"] for r in rows for c in r["cells"]})
    key = [('<b class="ok">✓</b><b class="bad">✗</b>', "passed, failed"), ('<b style="color:var(--a1)">●</b>', "running"),
           ('<b style="color:var(--sc)">○</b>', "look here (a failed run)"), ('<b style="color:var(--sg)">○</b>', "its first passing check"),
           ('<b style="color:var(--sg)">|</b><b style="color:var(--sc)">|</b>', "end of a lap: check passed, failed")]
    return "".join(out) + legend(used, key)


# --------------------------------------------------------- the trunk
_BRANCHING = ("explore", "edit", "verify", "run", "research", "delegate", "other")


def trunk_svg(runs: list, *, cmp: Optional[dict] = None, axis: str = "time", width: int = 980,
              link: bool = True) -> str:
    """Each run as a trunk along its clock: its thinking on the trunk, its tool
    calls as branches ending in leaves, its sub-agents as branches hanging off
    it, its laps as ticks on it, crowded stretches gathered into ×N bubbles,
    where to look ringed. Two runs face each other, A's branches up and B's
    down, with the steps they share joined and the first difference marked."""
    if not runs or not any(r.get("steps") for r in runs):
        return '<p class="muted">No steps to draw.</p>'
    pair = len(runs) == 2
    left, right = 120, 20
    reach = 58
    subs = [[l for l in r["lanes"] if l["agent"] != "root" and l["parallel"] == 0] for r in runs]
    sub_h = 16
    if pair:
        top_pad = 46 + reach
        trunk_y = [top_pad, top_pad + 96]
        height = trunk_y[1] + reach + 50
    else:
        trunk_y = [46 + reach + 10]
        height = trunk_y[0] + 30 + sub_h * len(subs[0]) + 30
    x0, x1 = left, width - right
    if axis == "step":
        n = max(len(r["steps"]) for r in runs) or 1

        def by_step(s):
            return x0 + (x1 - x0) * (s["index"] + 0.5) / n
        pos = [by_step] * len(runs)
    else:
        if pair:
            span = max(r["span_s"] for r in runs) or 1.0
            clocks = [(lambda t, span=span: x0 + (x1 - x0) * t / span)] * 2
        else:
            c = _Clock(runs[0].get("segments") or [], x0, x1, runs[0]["span_s"] or 1.0)
            clocks = [c]
        pos = [(lambda s, c=c: c(s["start"])) for c in clocks]
    out = [f'<svg class="viz trunk" viewBox="0 0 {width} {height:.0f}" width="{width}" role="img" '
           f'aria-label="{e((cmp or {}).get("sentence") or (runs[0].get("look_here") or {}).get("sentence"))}">']
    # folds on a single run's clock
    if not pair and axis != "step":
        for s, a, b2 in clocks[0].parts:
            if s["kind"] == "fold":
                out.append(f'<line x1="{a:.1f}" x2="{b2:.1f}" y1="{trunk_y[0]}" y2="{trunk_y[0]}" '
                           f'style="stroke:var(--panel);stroke-width:6"/>'
                           f'<line x1="{a:.1f}" x2="{b2:.1f}" y1="{trunk_y[0]}" y2="{trunk_y[0]}" '
                           f'style="stroke:var(--ink2);stroke-width:2;stroke-dasharray:2 3"><title>{s.get("steps", 0)} quiet '
                           f'step(s), {s["to"] - s["from"]:.1f}s folded</title></line>')
    where: List[dict] = [{}, {}]
    for k, r in enumerate(runs):
        ty = trunk_y[k]
        up = -1 if (k == 0) else 1
        steps = sorted(r["steps"], key=lambda s: (s["start"], s["index"]))
        here = (r.get("look_here") or {}).get("index")
        for s in steps:
            s["_here"] = s["index"] == here
        P = pos[k]
        for s in steps:
            where[k][s["index"]] = P(s)
        # two runs facing each other carry every thread on their trunks; one run hangs its sub-agents below
        root_steps = steps if pair else [s for s in steps if s["agent"] == "root"]
        last_x = max(P(s) for s in steps)
        glyph, gcls = ("✓", "ok") if r["success"] is True else ("✗", "bad") if r["success"] is False else ("●", "")
        name = ("A · " if pair and k == 0 else "B · " if pair else "") + str(r.get("agent") or "run")
        out.append(f'<text class="{gcls}" x="4" y="{ty + 5}" style="font-size:14px">{glyph}</text>'
                   f'<text class="lab" x="20" y="{ty + 4}" style="font-weight:600">{e(name[:15])}'
                   f'<title>{e(name)}</title></text>')
        out.append(f'<line x1="{x0}" x2="{last_x:.1f}" y1="{ty}" y2="{ty}" '
                   f'style="stroke:var(--{"sc" if r["success"] is False else "ink2"});stroke-width:3;stroke-linecap:round;'
                   f'stroke-opacity:.8"/>')
        # laps as ticks across the trunk
        for b in r.get("laps") or []:
            last = [s for s in steps if s.get("lap") == b["n"]]
            if not last:
                continue
            lx = max(P(s) for s in last)
            col = "var(--sg)" if b["passed"] is True else "var(--sc)" if b["passed"] is False else "var(--ink2)"
            out.append(f'<line x1="{lx:.1f}" x2="{lx:.1f}" y1="{ty - 7}" y2="{ty + 7}" style="stroke:{col};stroke-width:2">'
                       f'<title>end of lap {b["n"]}{": its check " + ("passed" if b["passed"] else "failed") if b["passed"] is not None else ""}'
                       f'{" (the same calls as the lap before)" if b["repeat"] else ""}</title></line>')
        # the root agent's steps: thinking on the trunk, tools as branches
        leaf = 0
        last_leaf_x = -1e9
        clusters = []
        cur: list = []
        for s in root_steps:
            alone = s["_here"] or s["error"] or s["check"] is False
            if alone:
                if cur:
                    clusters.append(cur)
                    cur = []
                clusters.append([s])
                continue
            if cur and P(s) - P(cur[0]) > 14:
                clusters.append(cur)
                cur = []
            cur.append(s)
        if cur:
            clusters.append(cur)
        leaf_xs = [P(g[0]) for g in clusters if len(g) == 1 and (g[0]["activity"] in _BRANCHING or g[0]["error"])]

        def roomy(xv: float) -> bool:
            return all(abs(xv - o) >= 30 or o == xv for o in leaf_xs)
        for g in clusters:
            if len(g) > 1:
                ga, gb = P(g[0]), P(g[-1])
                bw = max(14.0, gb - ga + 10)
                tools: Dict[str, int] = {}
                for s in g:
                    tools[s["name"]] = tools.get(s["name"], 0) + 1
                top = max(tools.items(), key=lambda kv: kv[1])[0]
                bad = any(s["error"] or s["check"] is False for s in g)
                okc = any(s["check"] is True for s in g)
                tip = (f'steps {g[0]["index"]}–{g[-1]["index"]}: ' + ", ".join(f"{v}× {kk}" for kk, v in
                                                                                 sorted(tools.items(), key=lambda kv: -kv[1])[:6]))
                href = f'#s{g[0]["index"]}'
                body = (f'<rect x="{ga - 5:.1f}" y="{ty - 8}" width="{bw:.1f}" height="16" rx="8" '
                        f'style="fill:var(--panel);stroke:var(--{"sc" if bad else "sg" if okc else "a1"});stroke-width:1.6">'
                        f'<title>{e(tip)}</title></rect>')
                if bw >= 24:
                    body += (f'<text x="{ga - 5 + bw / 2:.1f}" y="{ty + 4}" text-anchor="middle" style="font-size:9.5px">'
                             f'×{len(g)}{" " + e(top[:max(0, int((bw - 26) / 6))]) if bw > 46 else ""}</text>')
                out.append(f'<a href="{href}">{body}</a>' if link else body)
                continue
            s = g[0]
            sx = P(s)
            cls, glyph2, label = ACTIVITIES.get(s["activity"], ACTIVITIES["other"])
            tip = (f'#{s["index"]} {s["name"]} · {label} · {s["start"]:.1f}s'
                   + (" · check " + ("passed" if s["check"] else "failed") if s["check"] is not None else "")
                   + (" · error" if s["error"] else ""))
            if s["activity"] in _BRANCHING or s["error"]:
                h = reach * (1 if leaf % 2 else 0.62)
                leaf += 1
                ly = ty + up * h
                cy = ty + up * h * 0.55
                stroke = "var(--sc)" if s["error"] or s["check"] is False else "var(--ink2)"
                node = (f'<path d="M{sx:.1f},{ty} C{sx:.1f},{cy:.1f} {sx:.1f},{cy:.1f} {sx:.1f},{ly:.1f}" '
                        f'style="fill:none;stroke:{stroke};stroke-width:1.4;stroke-opacity:.7"/>'
                        f'<circle class="{cls} hot" cx="{sx:.1f}" cy="{ly:.1f}" r="6"><title>{e(tip)}</title></circle>')
                if s["error"] or s["check"] is False:
                    node += f'<circle cx="{sx:.1f}" cy="{ly:.1f}" r="8.5" style="fill:none;stroke:var(--sc);stroke-width:2"/>'
                elif s["check"] is True:
                    node += (f'<text class="g dk" x="{sx:.1f}" y="{ly + 3.5:.1f}" text-anchor="middle" '
                             f'style="font-size:9px">✓</text>')
                if sx - last_leaf_x > 40 and (roomy(sx) or (leaf % 2 == 0)):
                    ty_lab = ly + up * 12 + (8 if up > 0 else 0)
                    node += (f'<text x="{sx:.1f}" y="{ty_lab:.1f}" text-anchor="middle" style="font-size:9.5px">'
                             f'{e(s["name"][:10])}</text>')
                    last_leaf_x = sx
                out.append(f'<a href="#s{s["index"]}">{node}</a>' if link else node)
            else:
                node = (f'<circle class="{cls} hot" cx="{sx:.1f}" cy="{ty}" r="4.5" '
                        f'style="stroke:var(--panel);stroke-width:1.5"><title>{e(tip)}</title></circle>')
                out.append(f'<a href="#s{s["index"]}">{node}</a>' if link else node)
        # sub-agents, as branches hanging off the trunk (single run only)
        if not pair:
            for j, l in enumerate(subs[0]):
                mine = [s for s in steps if s["agent"] == l["agent"]]
                if not mine:
                    continue
                by = ty + 30 + j * sub_h
                bx0, bx1 = P(mine[0]), max(P(s) for s in mine)
                indent = 4 + 8 * l.get("depth", 1)
                out.append(f'<path d="M{bx0:.1f},{ty} C{bx0:.1f},{by - 6:.1f} {bx0:.1f},{by:.1f} {bx0 + 6:.1f},{by:.1f}" '
                           f'style="fill:none;stroke:var(--ink2);stroke-width:1.2;stroke-opacity:.6"/>'
                           f'<line x1="{bx0 + 6:.1f}" x2="{max(bx1, bx0 + 8):.1f}" y1="{by}" y2="{by}" '
                           f'style="stroke:var(--ink2);stroke-width:2;stroke-opacity:.55"/>'
                           f'<text x="{indent}" y="{by + 4}" style="font-size:10px">{e(l["agent"][:18])}'
                           f'<title>{e(l["agent"])}: {len(mine)} step(s)</title></text>')
                last_x2 = -1e9
                for s in mine:
                    sx = P(s)
                    if sx - last_x2 < 3 and not (s["error"] or s["check"] is False or s["_here"]):
                        continue
                    last_x2 = sx
                    cls = ACTIVITIES.get(s["activity"], ACTIVITIES["other"])[0]
                    bad = s["error"] or s["check"] is False
                    node = (f'<circle class="{cls} hot" cx="{sx:.1f}" cy="{by}" r="{4.5 if bad else 3.5}" '
                            f'style="stroke:{"var(--sc)" if bad else "var(--panel)"};stroke-width:{2 if bad else 1}">'
                            f'<title>#{s["index"]} {e(s["name"])} · {e(l["agent"])}{" · error" if s["error"] else ""}'
                            f'{" · check " + ("passed" if s["check"] else "failed") if s["check"] is not None else ""}</title></circle>')
                    out.append(f'<a href="#s{s["index"]}">{node}</a>' if link else node)
        # where to look first
        if here is not None and here in where[k]:
            hs = next(s for s in steps if s["index"] == here)
            hx = where[k][here]
            hy = ty if hs["agent"] == "root" else ty + 30 + sub_h * next(
                (j for j, l in enumerate(subs[0]) if l["agent"] == hs["agent"]), 0) if not pair else ty
            col = "var(--sg)" if r["look_here"].get("kind") == "pass" else "var(--a1)" if r["look_here"].get("kind") == "now" else "var(--sc)"
            out.append(f'<circle cx="{hx:.1f}" cy="{hy:.1f}" r="11" style="fill:none;stroke:{col};stroke-width:2.5">'
                       f'<title>{e(r["look_here"]["sentence"])}</title></circle>')
            ly2 = (ty - reach - 30) if (k == 0) else (ty + reach + 40)
            anchor, lx = ("start", hx + 4) if hx < width - 170 else ("end", hx - 4)
            out.append(f'<text x="{lx:.1f}" y="{ly2:.1f}" text-anchor="{anchor}" style="fill:{col};font:600 11px system-ui,sans-serif">'
                       f'◆ look here: step {here}<title>{e(r["look_here"]["sentence"])}</title></text>')
    # the steps two runs share, joined; the first difference marked
    if pair and cmp is not None:
        d = cmp.get("diverged_at")
        upto = d if d is not None else min(len(runs[0]["steps"]), len(runs[1]["steps"]))
        for i in range(0, upto, max(1, upto // 40)):
            if i in where[0] and i in where[1]:
                out.append(f'<path d="M{where[0][i]:.1f},{trunk_y[0] + 4} C{where[0][i]:.1f},{(trunk_y[0] + trunk_y[1]) / 2:.1f} '
                           f'{where[1][i]:.1f},{(trunk_y[0] + trunk_y[1]) / 2:.1f} {where[1][i]:.1f},{trunk_y[1] - 4}" '
                           f'style="fill:none;stroke:var(--sg);stroke-width:1;stroke-opacity:.45"><title>step {i}: the same call in both</title></path>')
        if d is not None and d in where[0] and d in where[1]:
            out.append(f'<path d="M{where[0][d]:.1f},{trunk_y[0] + 4} C{where[0][d]:.1f},{(trunk_y[0] + trunk_y[1]) / 2:.1f} '
                       f'{where[1][d]:.1f},{(trunk_y[0] + trunk_y[1]) / 2:.1f} {where[1][d]:.1f},{trunk_y[1] - 4}" '
                       f'style="fill:none;stroke:var(--a2);stroke-width:2.5;stroke-dasharray:5 3"><title>{e(cmp["sentence"])}</title></path>'
                       f'<text x="{where[0][d] + 6:.1f}" y="{(trunk_y[0] + trunk_y[1]) / 2 + 4:.1f}" '
                       f'style="fill:var(--a2);font:600 11px system-ui,sans-serif">⟂ first difference: step {d}</text>')
    for r in runs:
        for s in r["steps"]:
            s.pop("_here", None)
    out.append("</svg>")
    used = sorted({s["activity"] for r in runs for s in r["steps"]})
    key = [('<b style="color:var(--ink2)">━</b>', "the trunk: thinking on it"), ('<b style="color:var(--ink2)">╭</b>', "a tool call branching off"),
           ('<b style="color:var(--a1)">⬭</b>', "×N steps gathered (opens the first)"), ('<b style="color:var(--sc)">◯</b>', "error or failed check"),
           ('<b style="color:var(--sg)">|</b><b style="color:var(--sc)">|</b>', "end of a lap")]
    if not pair and subs[0]:
        key.append(('<b style="color:var(--ink2)">└─</b>', "a sub-agent, hanging off the trunk"))
    return "".join(out) + legend(used, key)
