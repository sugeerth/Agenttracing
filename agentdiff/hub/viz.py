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
           "activity_of_hop", "lap_strip", "step_ribbon", "compact_laps", "harness_river", "VIZ_CSS"]


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
    base, bar_h = 118, 86
    height = base + 132
    x = lambda i: left + col * i + col / 2  # noqa: E731
    out = [f'<svg class="viz" viewBox="0 0 {width} {height}" width="{width}" role="img" '
           f'aria-label="{e(result.get("narrative"))}">',
           f'<text x="{left - 10}" y="22" text-anchor="end">runs that passed</text>',
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
        bw = 26
        if cur is not None:
            h = max(2.0, bar_h * cur)
            out.append(f'<rect class="a1 hot" x="{cx - bw - 2:.1f}" y="{base - h:.1f}" width="{bw}" height="{h:.1f}" rx="4">'
                       f'<title>{e(g["generation"])}: {runs - failed} of {runs} run(s) passed under '
                       f'v{e(g["harness"]["version"])}</title></rect>'
                       f'<text x="{cx - bw / 2 - 2:.1f}" y="{base - h - 5:.1f}" text-anchor="middle">{runs - failed}/{runs}</text>')
        if chg is not None:
            pc = t["passed"]["changed"]
            h = max(2.0, bar_h * chg)
            style = "" if kept else ' style="fill:none;stroke:var(--a3);stroke-width:2;stroke-dasharray:4 3"'
            out.append(f'<rect class="a3 hot" x="{cx + 2:.1f}" y="{base - h:.1f}" width="{bw}" height="{h:.1f}" rx="4"{style}>'
                       f'<title>with {e(a["remedy"]["id"])}: {pc[0]} of {pc[1]} passed ({e(t.get("verdict"))})</title></rect>'
                       f'<text x="{cx + bw / 2 + 2:.1f}" y="{base - h - 5:.1f}" text-anchor="middle">{pc[0]}/{pc[1]}</text>')
        # the version lane
        v = g["harness"]["version"]
        vy = base + 18
        if prev is not None:
            out.append(f'<line class="lane" x1="{prev[0] + 16:.1f}" x2="{cx - 16:.1f}" y1="{vy}" y2="{vy}"/>')
        out.append(f'<rect class="hot" x="{cx - 15:.1f}" y="{vy - 10}" width="30" height="20" rx="10" '
                   f'style="fill:var(--panel);stroke:var(--a1);stroke-width:2"><title>{e(g["generation"])} ran '
                   f'harness v{v}: {e(", ".join(g["harness"].get("remedies") or []) or "nothing added")}</title></rect>'
                   f'<text class="lab" x="{cx:.1f}" y="{vy + 4}" text-anchor="middle" style="font-size:11px">v{v}</text>')
        out.append(f'<text class="lab" x="{cx:.1f}" y="{base - bar_h - 14}" text-anchor="middle">{e(g["generation"])}</text>')
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
