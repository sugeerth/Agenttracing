"""Long runs, drawn as one trail: hours and days at the scale they ran.

One ink, and the shape says it. The run is a trunk along its clock; a burst
is a branch whose length and leaf grow with its calls, filled when it moved
forward; failed checks hang below the trunk; a loop of bursts is one arc
over its stretch; the idle between sessions is a dotted gap that says how
long it was; checkpoints are dots on the trunk; where to look first is
ringed.

- :func:`long_overview` — the whole run, or one stretch of it, as that trail.
- :func:`burst_calls` — every call of a burst, or of a stretch: on its
  clock, then in order at an even pace, a faint line joining the two.
- :func:`chapters_table` — the story as rows: a loop is one row, filler is
  one row, idle is a divider.
- :func:`rhythm_grid` — a row per day, a column per hour; darker, more calls.
- :func:`pace_chart` — calls, seconds per call, tokens per step, each on its
  own axis.
- :func:`phase_treemap` — where the working time went: a box per phase, a
  tile per tool, area by seconds, darker where its calls failed.
- :func:`diff_timeline` — two long runs cut at the checkpoints they share,
  one above the line and one below.

Server-side SVG, no script needed. The hub's ``longview.js`` adds the lens:
the same trail as a tape of phases, a fisheye round the pointer, and
moving phase by phase (``agentdiff/hub/static/longview.js``).
"""

from __future__ import annotations

import bisect
import difflib
import math
from typing import Dict, List, Optional

from ..longrun import check_key, dur, when
from .viz import e

__all__ = ["long_overview", "rhythm_grid", "chapters_table", "burst_calls", "pace_chart", "phase_treemap",
           "diff_timeline", "phases", "STATUS", "Axis", "lens_payload"]

_TICKS = (60, 300, 900, 1800, 3600, 2 * 3600, 3 * 3600, 6 * 3600, 12 * 3600, 86400)


class Axis:
    """Run time to x. Sessions keep their true clock; the idle between them is
    a break of fixed width. ``t0``/``t1`` give one linear stretch instead."""

    def __init__(self, sessions: List[dict], x0: float, x1: float, *, t0: Optional[float] = None,
                 t1: Optional[float] = None, gap_w: float = 46.0, min_w: float = 26.0) -> None:
        self.x0, self.x1 = x0, x1
        if t0 is not None or not sessions:
            lo = t0 or 0.0
            hi = t1 if t1 is not None else max([s["to"] for s in sessions] or [1.0])
            self.parts = [(lo, max(hi, lo + 1e-3), x0, x1)]
            self.gaps: List[tuple] = []
        else:
            n = len(sessions)
            avail = (x1 - x0) - gap_w * (n - 1)
            secs = [max(1.0, s["to"] - s["from"]) for s in sessions]
            widths = [avail * v / sum(secs) for v in secs]
            # a short session still gets room to be seen, taken from the long ones
            for _ in range(3):
                small = [k for k, w in enumerate(widths) if w < min_w]
                if not small:
                    break
                need = sum(min_w - widths[k] for k in small)
                big = sum(w for k, w in enumerate(widths) if k not in small)
                widths = [min_w if k in small else w - need * w / big for k, w in enumerate(widths)]
            self.parts, self.gaps, x = [], [], x0
            for k, (s, w) in enumerate(zip(sessions, widths)):
                if k:
                    self.gaps.append((sessions[k - 1]["to"], s["from"], x, x + gap_w))
                    x += gap_w
                self.parts.append((s["from"], max(s["to"], s["from"] + 1e-3), x, x + w))
                x += w
        self._starts = [p[0] for p in self.parts]

    def __call__(self, t: float) -> float:
        k = bisect.bisect_right(self._starts, t) - 1
        if k < 0:
            return self.parts[0][2]
        a, b, xa, xb = self.parts[k]
        if t <= b:
            return xa + (xb - xa) * (t - a) / (b - a)
        if k < len(self.gaps):  # inside the break after this session
            g0, g1, ga, gb = self.gaps[k]
            return ga + (gb - ga) * min(1.0, (t - g0) / max(1e-3, g1 - g0))
        return xb

    def inverse(self, x: float) -> float:
        for a, b, xa, xb in self.parts:
            if x <= xb:
                return a + (b - a) * max(0.0, x - xa) / max(1e-6, xb - xa)
        return self.parts[-1][1]


def _ticks(axis: Axis, started_at, span: float) -> List[tuple]:
    """(x, label, major) at round moments of the clock, at least 48 px apart."""
    origin = float(started_at or 0.0)
    out, last = [], -1e9
    for a, b, xa, xb in axis.parts:
        px_per_s = (xb - xa) / max(1e-6, b - a)
        step = next((s for s in _TICKS if s * px_per_s >= 52), _TICKS[-1])
        lab0 = when(a, started_at, span)
        if xa >= last:
            out.append((xa, lab0, True))
            last = xa + 6.4 * len(lab0) + 6
        else:
            last = max(last, xa)
        k = math.ceil((origin + a) / step) * step
        while k - origin <= b:
            t = k - origin
            x = xa + (t - a) * px_per_s
            if x >= last and xb - x >= 20:
                if started_at is not None:
                    import datetime as _dt
                    w = _dt.datetime.fromtimestamp(k, tz=_dt.timezone.utc)
                    major = w.hour == 0 and w.minute == 0
                    lab = when(t, started_at, max(span, 12 * 3600)) if major else f"{w:%H:%M}"
                else:
                    major, lab = False, f"+{dur(t)}"
                out.append((x, lab, major))
                last = x + max(48, 6.4 * len(lab) + 8)
            k += step
    return out


def _fit(text: str, px: float, char: float = 6.3) -> str:
    n = int(px / char)
    return text if len(text) <= n else (text[: max(0, n - 1)] + "…" if n > 3 else "")


def phases(r: dict, *, fold_filler: bool = True) -> List[dict]:
    """The chapters with filler folded: a run of bursts where no check changed
    and nothing repeated becomes one phase. Each phase has its bursts and its time."""
    out: List[dict] = []
    bursts = r["bursts"]
    for c in r.get("chapters") or []:
        if c["kind"] == "idle":
            out.append({"kind": "idle", "seconds": c["seconds"], "t": c["t"]})
            continue
        if c["kind"] == "loop":
            mine = bursts[c["bursts"][0] - 1:c["bursts"][1]]
            out.append({"kind": "loop", "bursts": list(c["bursts"]), "from": c["from"], "to": c["to"],
                        "calls": c["calls"], "active_s": c["active_s"], "count": c["count"], "failing": c["failing"],
                        "sessions": c.get("sessions", 1),
                        "shared": c["shared"], "first": mine[0]["first"], "last": mine[-1]["last"]})
            continue
        b = bursts[c["burst"] - 1]
        filler = fold_filler and b["status"] in ("working", "exploring") and not b["events"]
        prev = out[-1] if out else None
        if filler and prev and prev["kind"] == "filler":
            prev.update(to=b["to"], calls=prev["calls"] + b["calls"], active_s=prev["active_s"] + b["seconds"],
                        last=b["last"])
            prev["bursts"][1] = b["n"]
            continue
        out.append({"kind": "filler" if filler else "burst", "bursts": [b["n"], b["n"]], "from": b["from"], "to": b["to"],
                    "calls": b["calls"], "active_s": b["seconds"], "status": b["status"], "first": b["first"],
                    "last": b["last"]})
    if not fold_filler:
        return out
    # filler that leads into an eventful burst is that burst's lead-in: one phase
    merged: List[dict] = []
    for p in out:
        prev = merged[-1] if merged else None
        if prev and prev["kind"] == "filler" and p["kind"] == "burst":
            p = dict(p, bursts=[prev["bursts"][0], p["bursts"][1]], **{"from": prev["from"]},
                     calls=prev["calls"] + p["calls"], active_s=prev["active_s"] + p["active_s"], first=prev["first"],
                     lead_in=prev["bursts"][1] - prev["bursts"][0] + 1)
            merged[-1] = p
            continue
        merged.append(p)
    return merged


def _squarify(values: List[float], x: float, y: float, w: float, h: float) -> List[tuple]:
    """Squarified rectangles for ``values`` (largest first), in order."""
    out: List[tuple] = []
    items = [(v, k) for k, v in enumerate(values) if v > 0]
    total = sum(v for v, _ in items) or 1.0
    scale = w * h / total
    items = [(v * scale, k) for v, k in items]
    rects = [None] * len(values)

    def worst(row, side):
        s = sum(v for v, _ in row)
        return max(max(side * side * v / (s * s), (s * s) / (side * side * v)) for v, _ in row)
    while items:
        side = min(w, h)
        row = [items.pop(0)]
        while items and worst(row + [items[0]], side) <= worst(row, side):
            row.append(items.pop(0))
        s = sum(v for v, _ in row)
        if w >= h:
            cw = s / h
            yy = y
            for v, k in row:
                rects[k] = (x, yy, cw, v / cw)
                yy += v / cw
            x, w = x + cw, w - cw
        else:
            rh = s / w
            xx = x
            for v, k in row:
                rects[k] = (xx, y, v / rh, rh)
                xx += v / rh
            y, h = y + rh, h - rh
    out = [rc for rc in rects]
    return out


def _checkpoints(data: dict, items: List[dict]) -> List[tuple]:
    """(key, time, index) of each check passing where its previous run did not."""
    steps = data.get("steps") or []
    last: Dict[str, Optional[bool]] = {}
    out = []
    for x in sorted(items, key=lambda x: (x["start"], x["index"])):
        if x["check"] is None:
            continue
        k = check_key(steps[x["index"]]) if x["index"] < len(steps) else x["name"]
        if x["check"] is True and last.get(k) is not True:
            out.append((k, x["start"], x["index"]))
        last[k] = x["check"]
    return out


#: how a burst went, said in words (the drawing says it by shape, never by colour)
STATUS = {
    "progress": ("●", "moved forward: a check started passing, or passed more"),
    "regressed": ("×", "regressed: a check that passed now fails"),
    "stuck": ("↻", "stuck: repeats an earlier burst, or the same failing call"),
    "failing": ("↓", "checks failing, nothing repeated"),
    "done": ("■", "answered"),
    "working": ("○", "working: edits and runs"),
    "exploring": ("○", "reading"),
}


def _in_loop(r: dict) -> Dict[int, int]:
    out = {}
    for k, lp in enumerate(r.get("loops") or []):
        for n in range(lp["bursts"][0], lp["bursts"][1] + 1):
            out[n] = k
    return out


def _stem(x: float, y: float, h: float, cls: str = "stem") -> str:
    return f'<line class="{cls}" x1="{x:.1f}" x2="{x:.1f}" y1="{y:.1f}" y2="{y - h:.1f}"/>'


def _key(items: List[tuple]) -> str:
    """A one-line key, glyphs and words, no colour."""
    return ('<div class="legend trail-key">' + "".join(f'<span><b>{g}</b>{e(t)}</span>' for g, t in items)
            + "</div>")


def long_overview(r: dict, items: List[dict], *, width: int = 980, base: str = "", t0: Optional[float] = None,
                  t1: Optional[float] = None, live: bool = False) -> str:
    """The whole run (or ``t0``..``t1`` of it) as one trunk along its clock.

    Each burst is a branch: its length and its leaf grow with its calls, and
    the leaf is filled when the burst moved forward. Failed checks hang below
    the trunk. A loop of bursts is one arc over its stretch. The idle between
    sessions is a dotted gap that says how long it was. Checkpoints are dots on
    the trunk; where to look first is ringed. Shape and position carry it, so
    it reads in one ink."""
    if not r.get("bursts"):
        return '<p class="muted">No steps to draw.</p>'
    left, right = 16, 16
    x0, x1 = left, width - right
    zoom = t0 is not None
    axis = Axis(r["sessions"], x0, x1, t0=t0, t1=t1, gap_w=44)
    lo, hi = (t0, t1) if zoom else (0.0, r["span_s"])
    sa, span = r.get("started_at"), r["span_s"]
    yt = 104
    height = 186
    loop_of = _in_loop(r)
    out = [f'<svg class="viz trail" viewBox="0 0 {width} {height}" width="{width}" role="img" '
           f'aria-label="{e(r.get("sentence"))}">']
    # the clock: hairline ticks on the trunk, the moment above
    for x, lab, major in _ticks(axis, sa, span):
        out.append(f'<line class="tick" x1="{x:.1f}" x2="{x:.1f}" y1="{yt - 3}" y2="{yt + 3}"/>'
                   f'<text class="{"lab2" if major else "mu"}" x="{x:.1f}" y="14">{e(lab)}</text>')
    # the trunk, and the idle between sessions
    for a, b, xa, xb in axis.parts:
        out.append(f'<line class="trunk" x1="{xa:.1f}" x2="{xb:.1f}" y1="{yt}" y2="{yt}"/>')
    for g0, g1, ga, gb in axis.gaps:
        out.append(f'<line class="gap" x1="{ga:.1f}" x2="{gb:.1f}" y1="{yt}" y2="{yt}"><title>{e(dur(g1 - g0))} '
                   f'idle: no step at all, {e(when(g0, sa, span))} to {e(when(g1, sa, span))}</title></line>')
        if gb - ga >= 30:
            out.append(f'<text class="mu" x="{(ga + gb) / 2:.1f}" y="{yt + 16}" text-anchor="middle">'
                       f'{e(dur(g1 - g0))}</text>')
    # bursts as branches; the bursts of a loop as one arc
    for b in r["bursts"]:
        if b["to"] < lo or b["from"] > hi:
            continue
        x = axis(max(lo, min(hi, (b["from"] + b["to"]) / 2)))
        fails = b["checks"]["failed"] + (b["errors"] if not b["checks"]["failed"] else 0)
        if fails:
            out.append(f'<line class="stem down" x1="{x:.1f}" x2="{x:.1f}" y1="{yt + 2}" '
                       f'y2="{yt + min(26, 4 + 5 * math.log2(1 + fails)):.1f}"/>')
        if b["n"] in loop_of:
            out.append(_stem(x, yt - 1, 5, "stem faint"))
            continue
        h = min(54, 10 + 9 * math.log2(1 + b["calls"]))
        rr = min(5.5, 1.8 + math.sqrt(b["calls"]) / 2.2)
        moved = any(ev["kind"] == "progress" for ev in b["events"])
        cls = "leaf on" if moved else "leaf" + (" dim" if b["status"] in ("working", "exploring") else "")
        glyph, what = STATUS.get(b["status"], STATUS["working"])
        out.append(_stem(x, yt - 1, h - rr) +
                   f'<a href="{e(base)}&amp;burst={b["n"]}#p-long"><circle class="{cls}" cx="{x:.1f}" cy="{yt - h:.1f}" '
                   f'r="{rr:.1f}"><title>burst {b["n"]} · {e(when(b["from"], sa, span))} · {e(dur(b["seconds"]))} · '
                   f'{b["calls"]:,} calls · {e(what)}' + (f' · {e(b["note"])}' if b["note"] else "")
                   + ' — open its calls</title></circle></a>')
    for lp in r.get("loops") or []:
        if lp["to"] < lo or lp["from"] > hi:
            continue
        xa, xb = axis(max(lo, lp["from"])), axis(min(hi, lp["to"]))
        peak = yt - min(70, 26 + (xb - xa) / 9)
        mid = (xa + xb) / 2
        lab = f"↻ the same calls {lp['count']}× · {dur(lp['wall_s'])}"
        out.append(f'<a href="{e(base)}&amp;burst={lp["bursts"][0]}#p-long"><path class="arc" '
                   f'd="M{xa:.1f},{yt - 1} Q{mid:.1f},{2 * peak - yt:.1f} {xb:.1f},{yt - 1}"><title>a loop: bursts '
                   f'{lp["bursts"][0]}–{lp["bursts"][1]} did the same calls {lp["count"]} times over {e(dur(lp["wall_s"]))}, '
                   f'{lp["calls"]:,} calls' + (f', {e(lp["failing"])} failing' if lp["failing"] else "")
                   + ' — open it</title></path></a>'
                   f'<text class="lab2" x="{min(max(mid, x0 + 90), x1 - 90):.1f}" y="{peak - 6:.1f}" '
                   f'text-anchor="middle">{e(lab)}</text>')
    # checkpoints on the trunk, regressions below it
    for ev in r.get("events") or []:
        if not lo <= ev["t"] <= hi:
            continue
        xe = axis(ev["t"])
        if ev["kind"] == "progress":
            out.append(f'<circle class="mile" cx="{xe:.1f}" cy="{yt}" r="2.4"><title>step {ev["index"]}: {e(ev["check"])} '
                       f'passed ({e(ev["how"])}), {e(when(ev["t"], sa, span))}</title></circle>')
        else:
            out.append(f'<text class="lab2" x="{xe:.1f}" y="{yt + 30}" text-anchor="middle">×<title>step {ev["index"]}: '
                       f'{e(ev["check"])} failed after passing</title></text>')
    # the stall, as a bracket under the trunk
    st = r.get("stall")
    if st and st["active_s"] >= 600 and st["to"] >= lo and st["from"] <= hi:
        xa, xb = axis(max(lo, st["from"])), axis(min(hi, st["to"]))
        y = yt + 44
        lab = (f"no progress for {dur(st['wall_s'])} · {dur(st['active_s'])} of it working · {st['calls']:,} calls"
               + (" · still going" if st.get("open") and r.get("in_progress") else ""))
        anchor, lx = ("start", xa) if xa < width - 360 else ("end", xb)
        out.append(f'<path class="bracket" d="M{xa:.1f},{y - 4} v4 H{max(xb, xa + 2):.1f} v-4"/>'
                   f'<text class="lab2" x="{lx:.1f}" y="{y + 14}" text-anchor="{anchor}">'
                   f'{e(_fit(lab, (width - 14 - lx) if anchor == "start" else lx - 14, 6.0))}<title>{e(st["sentence"])}</title></text>')
    # where to look first
    here = r.get("look_here")
    if here:
        ht = next((x["start"] for x in items if x["index"] == here["index"]), None)
        if ht is not None and lo <= ht <= hi:
            hx = axis(ht)
            out.append(f'<circle class="ring" cx="{hx:.1f}" cy="{yt}" r="8"><title>{e(here["sentence"])}</title></circle>'
                       f'<line class="tick" x1="{hx:.1f}" x2="{hx:.1f}" y1="{yt + 9}" y2="{yt + 32}"/>'
                       f'<text class="lab2" x="{hx + 4:.1f}" y="{yt + 32}" style="font-weight:700">look here'
                       f'<title>{e(here["sentence"])}</title></text>')
    if live:
        out.append(f'<circle class="leaf" cx="{x1 - 2:.1f}" cy="{yt}" r="3.5"><title>now: still running</title></circle>')
    out.append("</svg>")
    return "".join(out) + _key([("○", "a burst: the longer the branch, the more calls"),
                                ("●", "it moved forward"), ("·", "a checkpoint"), ("╷", "failed checks"),
                                ("⌒", "a loop of bursts"), ("┄", "idle, shortened")])


# ------------------------------------------------------------------- rhythm
def rhythm_grid(r: dict, *, width: int = 980, base: str = "") -> str:
    """A row per day, a column per hour: the ink is how much it worked."""
    g = r.get("rhythm")
    if not g or not g.get("cells"):
        return ""
    left, top = 76, 18
    cols, rows = g["cols"], g["rows"]
    cw = (width - left - 8) / cols
    ch = 20 if rows <= 12 else 15
    height = top + rows * ch + 4
    most = max(c["calls"] for c in g["cells"]) or 1
    sa = r.get("started_at")
    out = [f'<svg class="viz trail" viewBox="0 0 {width} {height}" width="{width}" role="img" '
           f'aria-label="when it worked: {e(g["unit"])}, darker is more calls">']
    every = 3 if cols == 24 else 2
    for c in range(0, cols, every):
        lab = f"{c:02d}:00" if cols == 24 else f":{c * 5:02d}"
        out.append(f'<text class="mu" x="{left + c * cw + 2:.1f}" y="12">{lab}</text>')
    for k in range(rows):
        t_row = g["row0_s"] + k * g["row_s"]
        if g["row_s"] >= 86400:
            lab = f"day {k + 1}"
            if sa is not None:
                import datetime as _dt
                lab += f" {_dt.datetime.fromtimestamp(sa + max(0.0, t_row), tz=_dt.timezone.utc):%a}"
        else:
            lab = when(max(0.0, t_row), sa, 7200) if sa is not None else f"+{dur(max(0.0, t_row))}"
        out.append(f'<text class="lab2" x="4" y="{top + k * ch + ch - 6}">{e(lab)}</text>'
                   f'<line class="tick" x1="{left}" x2="{width - 8}" y1="{top + (k + 1) * ch - .5}" '
                   f'y2="{top + (k + 1) * ch - .5}" style="stroke-opacity:.35"/>')
    for c in g["cells"]:
        x, y = left + c["col"] * cw, top + c["row"] * ch
        op = 0.08 + 0.72 * math.sqrt(c["calls"] / most) if c["calls"] else 0.04
        tip = (f'{when(max(0.0, c["t0"]), sa, max(r["span_s"], 13 * 3600 if g["row_s"] >= 86400 else 3600))}: '
               f'{c["calls"]} call(s)' + (f'; {c["passed"]} check(s) passed' if c["passed"] else "")
               + (f'; {c["failed"]} failed' if c["failed"] else "") + (f'; {c["progress"]} step(s) forward' if c["progress"] else "")
               + ("; a repeating burst began here" if c["stuck"] else "") + " — open this stretch")
        out.append(f'<a href="{e(base)}&amp;t0={max(0.0, c["t0"]):.0f}&amp;t1={c["t1"]:.0f}#p-long">'
                   f'<rect class="cell" x="{x + 1:.1f}" y="{y + 1}" width="{cw - 2:.1f}" height="{ch - 3}" rx="2" '
                   f'style="fill-opacity:{op:.2f}"><title>{e(tip)}</title></rect></a>')
        if c["progress"]:
            out.append(f'<circle class="{"mile inv" if op > .5 else "mile"}" cx="{x + cw - 5:.1f}" cy="{y + 6}" r="2"/>')
        if c["stuck"]:
            out.append(f'<text class="{"inv" if op > .5 else "lab2"}" x="{x + 3:.1f}" y="{y + 10}" '
                       f'style="font-size:9px">↻</text>')
    out.append("</svg>")
    return "".join(out) + _key([("▢", "darker: more calls"), ("·", "a check started passing"),
                                ("↻", "a repeating burst began")])


# ------------------------------------------------------------------ chapters
def chapters_table(r: dict, *, base: str = "", most: int = 80) -> str:
    """The run's story as rows: a loop is one row, filler is one row, idle a divider."""
    sa, span = r.get("started_at"), r["span_s"]
    rows = []
    allp = phases(r)
    for p in allp[:most]:
        if p["kind"] == "idle":
            rows.append(f'<tr class="idle"><td colspan="6" class="muted" style="text-align:center">'
                        f'{e(dur(p["seconds"]))} idle</td></tr>')
            continue
        a, b = p["bursts"]
        mine = r["bursts"][a - 1:b]
        passed = sum(x["checks"]["passed"] for x in mine)
        failed = sum(x["checks"]["failed"] for x in mine)
        if p["kind"] == "loop":
            glyph = "↻"
            what = (f'<strong>the same calls {p["count"]}× in a row</strong>'
                    + (f', across {p["sessions"]} sessions and the idle between' if p.get("sessions", 1) > 1 else "")
                    + (f'; <code>{e(p["failing"][:70])}</code> kept failing' if p["failing"] else ""))
        elif p["kind"] == "filler":
            glyph, what = "○", f'<span class="muted">{b - a + 1} burst(s) of reading and editing; no check changed</span>'
        else:
            glyph, desc = STATUS.get(p["status"], STATUS["working"])
            ev = next((x for x in reversed(mine) if x["note"]), mine[-1])
            what = e(ev["note"] or desc)
        label = f"burst {a}" if a == b else f"bursts {a}–{b}"
        checks = " ".join(x for x in (f"✓{passed}" if passed else "", f"✗{failed}" if failed else "") if x)
        rows.append(f'<tr><td class="glyph">{glyph}</td><td><a href="{e(base)}&amp;burst={a}#p-long">{e(label)}</a>'
                    f'<div class="muted">{e(when(p["from"], sa, span))} · {e(dur(p["to"] - p["from"]))}</div></td>'
                    f'<td class="n">{p["calls"]:,}</td><td class="n muted">{e(checks)}</td><td>{what}</td></tr>')
    more = len(allp) - most
    return ('<table class="chapters"><tr><th></th><th>phase</th><th class="n">calls</th><th class="n">checks</th>'
            '<th>what happened</th></tr>' + "".join(rows) + "</table>"
            + (f'<p class="muted">{more} more phase(s).</p>' if more > 0 else ""))


# ------------------------------------------------------------- every call
def burst_calls(win: dict, r: dict, *, width: int = 980, page: int = 0, per_page: int = 600, base: str = "") -> str:
    """Every call in a window, as two trunks: on its clock, then in order at an
    even pace, with a faint line from each call to its moment. A call is a
    branch up; a failed one hangs down; a passing check ends in a dot."""
    steps = win["steps"]
    if not steps:
        return '<p class="muted">No steps in this stretch.</p>'
    pages = max(1, math.ceil(len(steps) / per_page))
    page = min(max(0, page), pages - 1)
    shown = steps[page * per_page:(page + 1) * per_page]
    t0, t1 = min(x["start"] for x in shown), max(x["end"] for x in shown)
    t1 = max(t1, t0 + 1.0)
    left, right = 70, 16
    x0, x1 = left, width - right
    ya, yb = 70, 190
    height = 262
    sa, span = r.get("started_at"), r["span_s"]

    def tx(t: float) -> float:
        return x0 + (x1 - x0) * (t - t0) / (t1 - t0)
    cw = (x1 - x0) / len(shown)
    out = [f'<svg class="viz trail" viewBox="0 0 {width} {height}" width="{width}" role="img" '
           f'aria-label="{len(shown)} calls, on their clock and in order">']
    step = next((s for s in (1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600, 7200, 21600)
                 if s * (x1 - x0) / (t1 - t0) >= 70), 21600)
    k = math.ceil(t0 / step) * step
    while k <= t1:
        x = tx(k)
        out.append(f'<line class="tick" x1="{x:.1f}" x2="{x:.1f}" y1="{ya - 3}" y2="{ya + 3}"/>'
                   f'<text class="mu" x="{x:.1f}" y="14" text-anchor="middle">'
                   f'{e(when(k, sa, max(span, 3601)) if step >= 60 else "+" + dur(k - t0))}</text>')
        k += step
    out.append(f'<text class="mu" x="4" y="{ya + 4}">its clock</text><text class="mu" x="4" y="{yb + 4}">in order</text>'
               f'<line class="trunk" x1="{x0}" x2="{x1}" y1="{ya}" y2="{ya}"/>'
               f'<line class="trunk" x1="{x0}" x2="{x1}" y1="{yb}" y2="{yb}"/>')
    connectors = len(shown) <= 400
    streak_from, last_sig, brackets = None, None, []
    for k2, x in enumerate(shown + [None]):
        sig = (x["name"], x.get("call")) if x else None
        bad = bool(x and (x["error"] or x["check"] is False))
        if x is not None and sig == last_sig and bad:
            continue
        if streak_from is not None and k2 - streak_from >= 3:
            brackets.append((streak_from, k2 - 1))
        streak_from, last_sig = (k2, sig) if bad else (None, None)
    last_label = -1e9
    for k2, x in enumerate(shown):
        bad = x["error"] or x["check"] is False
        think = x["type"] in ("reason", "plan", "answer")
        h = 0 if think else min(40, 8 + 7 * math.log2(1 + x["latency_s"]))
        tip = (f'#{x["index"]} {x["name"]} · {when(x["start"], sa, max(span, 3601))} · {x["latency_s"]:.1f}s · '
               f'{x["tokens"]:,} tokens' + (f' · {x["call"]}' if x.get("call") else "")
               + (" · check passed" if x["check"] is True else " · check failed" if x["check"] is False else "")
               + (" · error" if x["error"] else "") + (f' → {x["said"]}' if x.get("said") else ""))
        xc, xo = tx(x["start"]), x0 + (k2 + 0.5) * cw
        if connectors:
            out.append(f'<line class="wire" x1="{xc:.1f}" y1="{ya + 4}" x2="{xo:.1f}" y2="{yb - 46}"/>')
        for xx, y in ((xc, ya), (xo, yb)):
            if think:
                out.append(f'<circle class="mile faint" cx="{xx:.1f}" cy="{y}" r="1.6"/>')
                continue
            sign = 1 if bad else -1
            out.append(f'<line class="stem{" down" if bad else ""}" x1="{xx:.1f}" x2="{xx:.1f}" y1="{y + sign * 1:.1f}" '
                       f'y2="{y + sign * h:.1f}"/>')
            if x["check"] is True:
                out.append(f'<circle class="mile" cx="{xx:.1f}" cy="{y - h:.1f}" r="2.2"/>')
            elif x["check"] is False:
                out.append(f'<circle class="leaf" cx="{xx:.1f}" cy="{y + h:.1f}" r="2.2"/>')
        out.append(f'<a href="#s{x["index"]}"><rect class="hit" x="{xo - max(cw, 3) / 2:.1f}" y="{yb - 44}" '
                   f'width="{max(cw, 3):.1f}" height="88"><title>{e(tip)}</title></rect></a>')
        if cw >= 34 and not think and xo - last_label > 40:
            ly = yb + h + 12 if bad else yb - h - 6
            out.append(f'<text class="mu" x="{xo:.1f}" y="{ly:.1f}" text-anchor="middle">'
                       f'{e(_fit(x["name"], cw * 1.6, 5.6))}</text>')
            last_label = xo
    for a, b in brackets:
        xa, xb = x0 + a * cw, x0 + (b + 1) * cw
        out.append(f'<path class="bracket" d="M{xa:.1f},{yb + 52} v4 H{xb:.1f} v-4"/>'
                   f'<text class="lab2" x="{(xa + xb) / 2:.1f}" y="{yb + 68}" text-anchor="middle">'
                   f'↻ {b - a + 1}× the same failing call</text>')
    out.append("</svg>")
    nav = ""
    if pages > 1:
        links = []
        for p in range(pages):
            a = p * per_page
            links.append(f'<a href="{e(base)}&amp;page={p}#p-long"{" class=on" if p == page else ""}>'
                         f'{a + 1}–{min(len(steps), a + per_page)}</a>')
        nav = f'<div class="filters"><span class="muted">calls</span>{"".join(links)}</div>'
    return nav + "".join(out) + _key([("│", "a call: taller took longer"), ("╷", "it failed"),
                                      ("·", "a check that passed"), ("⋰", "where in time each call happened")])


# ------------------------------------------------------------------- pace
def pace_chart(r: dict, *, width: int = 980) -> str:
    rows = (r.get("pace") or {}).get("rows") or []
    if len(rows) < 3:
        return ""
    bucket = r["pace"]["bucket_s"]
    left, right, h, gap = 120, 16, 34, 14
    x0, x1 = left, width - right
    span = max(r["span_s"], rows[-1]["to"])

    def tx(t):
        return x0 + (x1 - x0) * t / span
    charts = (("calls", f"calls per {dur(bucket)}", lambda p: p["calls"], "bar"),
              ("sec", "seconds per call", lambda p: p["sec_per_call"], "line"),
              ("tok", "tokens per step", lambda p: p["tokens_per_step"], "line"))
    height = len(charts) * (h + gap) + 4
    out = [f'<svg class="viz trail" viewBox="0 0 {width} {height}" width="{width}" role="img" '
           f'aria-label="pace over the run: calls, seconds per call, tokens per step">']
    for k, (key, label, get, kind) in enumerate(charts):
        y0 = 4 + k * (h + gap)
        vals = [get(p) for p in rows if get(p) is not None]
        top = max(vals) if vals else 1
        out.append(f'<text class="lab2" x="4" y="{y0 + h - 12}">{e(label)}</text>'
                   f'<text class="mu" x="4" y="{y0 + h}">up to {top:,.1f}</text>'
                   f'<line class="tick" x1="{x0}" x2="{x1}" y1="{y0 + h}" y2="{y0 + h}" style="stroke-opacity:.4"/>')
        pts = []
        for p in rows:
            v = get(p)
            if v is None:
                continue
            xa, xb = tx(p["from"]), tx(p["to"])
            hh = h * v / (top or 1)
            tip = f'{when(p["from"], r.get("started_at"), r["span_s"])}: {label} {v:,.1f}'
            if kind == "bar":
                out.append(f'<rect class="cell" x="{xa:.1f}" y="{y0 + h - hh:.1f}" width="{max(1.0, xb - xa - 1):.1f}" '
                           f'height="{hh:.1f}" style="fill-opacity:.35"><title>{e(tip)}'
                           + (f'; {p["failed"]} failed check(s)' if p["failed"] else "") + '</title></rect>')
            else:
                pts.append(f"{(xa + xb) / 2:.1f},{y0 + h - hh:.1f}")
        if len(pts) > 1:
            out.append(f'<polyline class="line" points="{" ".join(pts)}"/>')
    out.append("</svg>")
    trend = (r["pace"].get("trend") or {}).get("sentence")
    return (f'<p class="muted">{e(trend)}</p>' if trend else "") + "".join(out)


# ---------------------------------------------------------------- treemap
def phase_treemap(r: dict, items: List[dict], *, width: int = 980, height: int = 220, base: str = "") -> str:
    """Where the working time went: a box per phase, a tile per tool, area by
    seconds; the darker a tile, the more of its calls failed."""
    allp = phases(r)
    keys = [k for k, p in enumerate(allp) if p["kind"] != "idle"]  # the lens numbers phases with the idle among them
    ph = [allp[k] for k in keys]
    if not ph:
        return ""
    secs, tools_of = [], []
    by_index = {x["index"]: x for x in items}
    for p in ph:
        tc: Dict[str, list] = {}
        for i in range(p["first"], p["last"] + 1):
            x = by_index.get(i)
            if x is None or x["latency_s"] <= 0:
                continue
            t = tc.setdefault(x["name"], [0.0, 0, 0])
            t[0] += x["latency_s"]
            t[1] += 1
            t[2] += int(x["error"] or x["check"] is False)
        tools_of.append(sorted(tc.items(), key=lambda kv: -kv[1][0]))
        secs.append(sum(v[0] for _, v in tc.items()))
    order = sorted(range(len(ph)), key=lambda k: -secs[k])
    rects = _squarify([secs[k] for k in order], 0, 16, width, height)
    sa, span = r.get("started_at"), r["span_s"]
    total = sum(secs) or 1
    out = [f'<svg class="viz trail treemap" viewBox="0 0 {width} {height + 18}" width="{width}" role="img" '
           f'aria-label="where the working time went: a box per phase, a tile per tool, area by seconds">'
           f'<text class="mu" x="0" y="11">area is the seconds a tool ran ({dur(total)} in all); darker, more of its '
           f'calls failed</text>']
    for k, rc in zip(order, rects):
        if rc is None:
            continue
        x, y, w, h = rc
        p = ph[k]
        a, b = p["bursts"]
        name = (f"loop {a}–{b}" if p["kind"] == "loop" else f"filler {a}–{b}" if p["kind"] == "filler"
                else f"burst {a}" if a == b else f"bursts {a}–{b}")
        trs = tools_of[k]
        room = h > 34 and w > 50
        inner = _squarify([v[0] for _, v in trs], x + 2, y + (16 if room else 2), max(1, w - 4),
                          max(1, h - (18 if room else 4)))
        out.append(f'<g data-phase="{keys[k]}">')
        for (tool, (ts, n, bad)), irc in zip(trs, inner):
            if irc is None:
                continue
            ix, iy, iw, ih = irc
            op = 0.10 + 0.32 * (bad / n if n else 0)
            out.append(f'<rect class="cell" x="{ix + .5:.1f}" y="{iy + .5:.1f}" width="{max(.5, iw - 1):.1f}" '
                       f'height="{max(.5, ih - 1):.1f}" rx="2" style="fill-opacity:{op:.2f}"><title>{e(name)} · {e(tool)}: '
                       f'{n} call(s), {e(dur(ts))}' + (f', {bad} failed' if bad else "") + '</title></rect>')
            if iw > 50 and ih > 14:
                out.append(f'<text class="lab2" x="{ix + 5:.1f}" y="{iy + 12:.1f}">'
                           f'{e(_fit(f"{tool} · {n}", iw - 8, 6.0))}</text>')
        out.append(f'<a href="{e(base)}&amp;burst={a}#p-long"><rect class="box{" strong" if p["kind"] == "loop" else ""}" '
                   f'x="{x + .5:.1f}" y="{y + .5:.1f}" width="{max(1, w - 1):.1f}" height="{max(1, h - 1):.1f}" rx="3">'
                   f'<title>{e(name)}: {e(when(p["from"], sa, span))}, {e(dur(secs[k]))} of tool time '
                   f'({secs[k] / total:.0%}), {p["calls"]:,} calls — open it</title></rect></a>')
        if room:
            out.append(f'<text class="lab2" x="{x + 5:.1f}" y="{y + 12:.1f}" style="font-weight:700">'
                       f'{e(_fit(f"{name} · {secs[k] / total:.0%}", w - 10, 6.2))}</text>')
        out.append("</g>")
    out.append("</svg>")
    return "".join(out)


# ------------------------------------------------------------- two long runs
def diff_timeline(da: dict, db: dict, ia: List[dict], ib: List[dict], *, names=("A", "B"), width: int = 980) -> str:
    """Two long runs cut at the checkpoints they share (the same check starting
    to pass, in the same order): A's steps rise above the line, B's hang below,
    and the darker part of each is what failed."""
    ca, cb = _checkpoints(da, ia), _checkpoints(db, ib)
    sm = difflib.SequenceMatcher(a=[k for k, _, _ in ca], b=[k for k, _, _ in cb], autojunk=False)
    pairs = [(ca[m.a + j], cb[m.b + j]) for m in sm.get_matching_blocks() for j in range(m.size)]
    end_a = max([x["end"] for x in ia] or [0.0])
    end_b = max([x["end"] for x in ib] or [0.0])
    cuts = [((None, 0.0, None), (None, 0.0, None))] + pairs + [(("end", end_a + 1, None), ("end", end_b + 1, None))]
    segs = []
    for (pa, pb), (qa, qb) in zip(cuts, cuts[1:]):
        def take(its, t0, t1):
            got = [x for x in its if t0 <= x["start"] < t1]
            return {"n": len(got), "fail": sum(1 for x in got if x["error"] or x["check"] is False),
                    "wall": max(0.0, min(t1, max([x["end"] for x in got] or [t0])) - t0)}
        segs.append({"a": take(ia, pa[1], qa[1]), "b": take(ib, pb[1], qb[1]),
                     "label": (qa[0] or "end").split(":", 1)[-1] if qa[0] != "end" else "the end"})
    if not segs:
        return ""
    left, right = 92, 16
    x0, x1 = left, width - right
    wts = [math.sqrt(max(s["a"]["n"], s["b"]["n"], 1)) for s in segs]
    tot = sum(wts)
    half = 62
    mid = 30 + half
    height = mid + half + 28
    most = max(max(s["a"]["n"], s["b"]["n"]) for s in segs) or 1
    out = [f'<svg class="viz trail" viewBox="0 0 {width} {height}" width="{width}" role="img" '
           f'aria-label="two runs cut at {len(pairs)} shared checkpoint(s); {e(names[0])} above, {e(names[1])} below">'
           f'<text class="lab2" x="4" y="{mid - 10}">{e(_fit(names[0], 84))}</text>'
           f'<text class="lab2" x="4" y="{mid + 18}">{e(_fit(names[1], 84))}</text>'
           f'<line class="trunk" x1="{x0}" x2="{x1}" y1="{mid}" y2="{mid}"/>']
    x = float(x0)
    worst, worst_k = 0, None
    for k, (s, wt) in enumerate(zip(segs, wts)):
        w = (x1 - x0) * wt / tot
        for side, sign in (("a", -1), ("b", 1)):
            d = s[side]
            if not d["n"]:
                continue
            hh = half * math.sqrt(d["n"] / most)
            fh = hh * d["fail"] / d["n"]
            y = mid - hh if sign < 0 else mid
            out.append(f'<rect class="cell" x="{x + 1.5:.1f}" y="{y:.1f}" width="{max(1.0, w - 3):.1f}" height="{hh:.1f}" '
                       f'style="fill-opacity:.18"><title>{e(names[0 if side == "a" else 1])}: {d["n"]:,} step(s) over '
                       f'{e(dur(d["wall"]))} before {e(s["label"])}' + (f', {d["fail"]} failed' if d["fail"] else "")
                       + '</title></rect>')
            if fh >= 0.5:
                fy = (mid - fh) if sign < 0 else (mid + hh - fh)
                out.append(f'<rect class="cell" x="{x + 1.5:.1f}" y="{fy:.1f}" width="{max(1.0, w - 3):.1f}" '
                           f'height="{fh:.1f}" style="fill-opacity:.6"/>')
        delta = s["a"]["n"] - s["b"]["n"]
        if abs(delta) > worst:
            worst, worst_k = abs(delta), k
        if w > 60 and abs(delta) >= max(10, 0.3 * max(s["a"]["n"], s["b"]["n"])):
            y = (mid - half * math.sqrt(s["a"]["n"] / most) - 6) if delta > 0 else (mid + half * math.sqrt(s["b"]["n"] / most) + 13)
            out.append(f'<text class="lab2" x="{x + w / 2:.1f}" y="{y:.1f}" text-anchor="middle" style="font-weight:700">'
                       f'+{abs(delta):,} step(s)</text>')
        if k < len(segs) - 1:
            out.append(f'<circle class="mile" cx="{x + w:.1f}" cy="{mid}" r="2.4"><title>checkpoint {k + 1}: '
                       f'{e(s["label"])} starts passing in both</title></circle>')
        x += w
    out.append(f'<text class="mu" x="{x0}" y="14">cut at {len(pairs)} checkpoint(s) both reached, in the same order; '
               f'size grows with the square root of the steps between them</text></svg>')
    sentence = ""
    if worst_k is not None:
        s = segs[worst_k]
        before = "the first checkpoint" if worst_k == 0 else f"checkpoint {worst_k}"
        sentence = (f'<p class="note">They part most between {e(before)} and <code>{e(s["label"][:70])}</code>: '
                    f'{e(names[0])} made {s["a"]["n"]:,} step(s) over {e(dur(s["a"]["wall"]))}, '
                    f'{e(names[1])} {s["b"]["n"]:,} over {e(dur(s["b"]["wall"]))}.</p>')
    return sentence + "".join(out) + _key([("▯", "steps between two checkpoints"), ("▮", "the part that failed"),
                                           ("·", "a checkpoint both reached")])


def lens_payload(data: dict, r: dict, *, ident: str, live: bool = False) -> dict:
    """What the lens reads: the reading (without its heaviest parts), the phases,
    and every step as compact columns."""
    from ..timeline import _items
    items, _ = _items(data)
    steps = data.get("steps") or []
    names: dict = {}
    acts: dict = {}
    cols: Dict[str, list] = {k: [] for k in ("i", "s", "d", "a", "n", "e", "c", "k", "t")}
    for x in sorted(items, key=lambda x: (x["start"], x["index"])):
        s = steps[x["index"]] if x["index"] < len(steps) else {}
        cols["i"].append(x["index"])
        cols["s"].append(round(x["start"], 2))
        cols["d"].append(round(x["latency_s"], 2))
        cols["a"].append(acts.setdefault(x["activity"], len(acts)))
        cols["n"].append(names.setdefault(x["name"], len(names)))
        cols["e"].append(int(x["error"]))
        cols["c"].append(-1 if x["check"] is None else int(x["check"]))
        cols["k"].append(x["tokens"])
        cols["t"].append(" ".join(str(s.get("input") or "").split())[:90])
    light = {k: v for k, v in r.items() if k not in ("pace", "rhythm")}
    return {"id": ident, "reading": light, "phases": phases(r), "activities": list(acts), "names": list(names),
            "calls": cols, "live": live}
